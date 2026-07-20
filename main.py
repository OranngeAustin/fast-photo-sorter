import json
import multiprocessing
import os
import sys
import threading
import time
from queue import Empty, Queue

from PIL import ExifTags, Image, ImageOps
from PIL.ImageQt import ImageQt
from PyQt6.QtCore import QFileInfo, QSize, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QImage, QPalette, QPixmap, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)
from send2trash import send2trash


def _bounded_preview_size(width, height, max_pixels):
    """Bound preview pixels so a pipe transfer cannot grow without limit."""
    width = max(1, int(width))
    height = max(1, int(height))
    if width * height <= max_pixels:
        return width, height

    scale = (max_pixels / (width * height)) ** 0.5
    return max(1, int(width * scale)), max(1, int(height * scale))


def _decode_preview_in_subprocess(
    result_connection,
    path,
    target_width,
    target_height,
    exif_datetime_tag,
    max_source_pixels,
    draft_formats,
    max_transfer_pixels,
):
    """Decode a read-only preview in a child process that may be safely stopped."""
    try:
        metadata = {"resolution": "Unknown", "date": "Unknown"}
        with Image.open(path) as pil_img:
            exif = pil_img.getexif()
            if exif and exif_datetime_tag is not None and exif_datetime_tag in exif:
                metadata["date"] = str(exif[exif_datetime_tag])

            source_pixels = pil_img.width * pil_img.height
            metadata["resolution"] = f"{pil_img.width} x {pil_img.height}"
            preview_allowed = True
            if source_pixels > max_source_pixels:
                if (pil_img.format or "").upper() in draft_formats:
                    pil_img.draft(
                        "RGB",
                        (max(1, target_width), max(1, target_height)),
                    )
                    if pil_img.width * pil_img.height > max_source_pixels:
                        preview_allowed = False
                else:
                    preview_allowed = False

            if preview_allowed:
                preview_image = ImageOps.exif_transpose(pil_img)
                preview_image.thumbnail(
                    _bounded_preview_size(
                        target_width,
                        target_height,
                        max_transfer_pixels,
                    ),
                    Image.Resampling.LANCZOS,
                )
                rgba_image = preview_image.convert("RGBA")
                result = (
                    "ok",
                    metadata,
                    rgba_image.width,
                    rgba_image.height,
                    rgba_image.tobytes(),
                )
            else:
                metadata["error"] = "The image is too large to preview safely in this format."
                result = ("ok", metadata, 0, 0, b"")
    except Exception as exc:
        result = ("error", str(exc))

    try:
        result_connection.send(result)
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        result_connection.close()


class ImageLoader(QThread):
    """Loads only images belonging to the active directory session."""

    image_loaded = pyqtSignal(int, int, str, QImage, dict)
    MAX_PREVIEW_SOURCE_PIXELS = 24_000_000
    MAX_PREVIEW_TRANSFER_PIXELS = 6_000_000
    _DRAFT_FORMATS = {"JPEG", "MPO"}
    DECODE_POLL_SECONDS = 0.05
    DECODE_STOP_GRACE_SECONDS = 0.5
    decode_worker_target = staticmethod(_decode_preview_in_subprocess)

    def __init__(self):
        super().__init__()
        self._queue = Queue()
        self._pending_requests = set()
        self._wanted_requests = set()
        self._lock = threading.Lock()
        self._active_session = 0
        self._stopping = threading.Event()
        self.is_running = True
        self._target_size = QSize(1920, 1080)

        self.exif_datetime_tag = next(
            (key for key, value in ExifTags.TAGS.items() if value == "DateTimeOriginal"),
            None,
        )

    def begin_session(self, session_id):
        """Discard queued work from a prior directory without blocking the UI."""
        with self._lock:
            old_queue = self._queue
            self._queue = Queue()
            self._pending_requests.clear()
            self._wanted_requests.clear()
            self._active_session = session_id
        old_queue.put(None)

    def set_target_size(self, size):
        with self._lock:
            self._target_size = QSize(size)

    def update_window(self, session_id, requests):
        """Keep queued work limited to the current sliding window."""
        with self._lock:
            if not self.is_running or session_id != self._active_session:
                return

            ordered_requests = [(session_id, index, path) for index, path in requests]
            self._wanted_requests = set(ordered_requests)
            for request in ordered_requests:
                if request not in self._pending_requests:
                    self._pending_requests.add(request)
                    self._queue.put(request)

    def stop(self):
        with self._lock:
            self.is_running = False
            self._stopping.set()
            queue = self._queue
        self.requestInterruption()
        queue.put(None)

    def _should_stop(self):
        return self._stopping.is_set() or self.isInterruptionRequested()

    def _is_current_request(self, queue, request):
        with self._lock:
            return (
                self.is_running
                and not self._should_stop()
                and queue is self._queue
                and request in self._wanted_requests
                and request[0] == self._active_session
            )

    def _should_stop_request(self, queue, request):
        return self._should_stop() or not self._is_current_request(queue, request)

    def _end_decode_process(self, process):
        """End a child decoder from the loader thread, never from the GUI thread."""
        try:
            if process.is_alive():
                process.terminate()
            process.join(self.DECODE_STOP_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(self.DECODE_STOP_GRACE_SECONDS)
        finally:
            try:
                if not process.is_alive():
                    process.close()
            except (OSError, ValueError):
                pass

    def _decode_preview(self, queue, request, target_size):
        receive_connection = None
        send_connection = None
        process = None
        try:
            receive_connection, send_connection = multiprocessing.Pipe(duplex=False)
            process = multiprocessing.get_context("spawn").Process(
                target=self.decode_worker_target,
                args=(
                    send_connection,
                    request[2],
                    target_size.width(),
                    target_size.height(),
                    self.exif_datetime_tag,
                    self.MAX_PREVIEW_SOURCE_PIXELS,
                    tuple(self._DRAFT_FORMATS),
                    self.MAX_PREVIEW_TRANSFER_PIXELS,
                ),
            )
            process.daemon = True
            process.start()
            send_connection.close()
            send_connection = None

            while True:
                if self._should_stop_request(queue, request):
                    return None
                if receive_connection.poll(self.DECODE_POLL_SECONDS):
                    try:
                        return receive_connection.recv()
                    except EOFError:
                        return ("error", "Preview decoder returned no result")
                if not process.is_alive():
                    if receive_connection.poll(self.DECODE_POLL_SECONDS):
                        try:
                            return receive_connection.recv()
                        except EOFError:
                            pass
                    return ("error", "Preview decoder ended unexpectedly")
        except Exception as exc:
            return ("error", str(exc))
        finally:
            if process is not None:
                self._end_decode_process(process)
            if send_connection is not None:
                send_connection.close()
            if receive_connection is not None:
                receive_connection.close()

    @staticmethod
    def _qimage_from_rgba(width, height, pixels):
        if width <= 0 or height <= 0:
            return QImage()
        expected_bytes = width * height * 4
        if len(pixels) != expected_bytes:
            raise ValueError("Preview image data length does not match")
        return QImage(
            pixels,
            width,
            height,
            width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()

    def run(self):
        while not self._should_stop():
            with self._lock:
                queue = self._queue

            try:
                request = queue.get(timeout=0.05)
            except Empty:
                continue

            if request is None:
                continue

            session_id, index, path = request
            with self._lock:
                self._pending_requests.discard(request)
                target_size = QSize(self._target_size)

            if self._should_stop_request(queue, request):
                continue

            qimage = QImage()
            metadata = {"size_mb": 0.0, "resolution": "Unknown", "date": "Unknown"}
            try:
                file_info = QFileInfo(path)
                metadata["size_mb"] = file_info.size() / (1024 * 1024)
                result = self._decode_preview(queue, request, target_size)
                if result is None:
                    continue
                if not isinstance(result, tuple) or not result:
                    raise ValueError("Preview decoder returned an invalid result")
                if result[0] == "error":
                    raise RuntimeError(result[1])
                if result[0] != "ok" or len(result) != 5:
                    raise ValueError("Preview decoder returned an unknown result")

                _, preview_metadata, width, height, pixels = result
                if not isinstance(preview_metadata, dict):
                    raise ValueError("Preview metadata is invalid")
                metadata.update(preview_metadata)
                qimage = self._qimage_from_rgba(width, height, pixels)
                if metadata["date"] == "Unknown":
                    metadata["date"] = (
                        file_info.birthTime().toString("yyyy-MM-dd HH:mm:ss")
                        or "Unknown"
                    )
            except Exception as exc:
                print(f"Failed to load {path}: {exc}")
                metadata["error"] = "Failed to load image"

            if not self._should_stop_request(queue, request):
                self.image_loaded.emit(session_id, index, path, qimage, metadata)


def _is_linked_directory(entry):
    if entry.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(entry.path))


def _scan_directory_records(directory, is_cancelled, emit_batch, batch_size):
    """Enumerate a directory tree without writing to the filesystem."""
    Image.init()
    valid_exts = {extension.lower() for extension in Image.registered_extensions()}
    warnings = []
    visited = set()
    stack = [directory]
    batch = []
    batch_size = max(1, int(batch_size))

    def flush_batch():
        nonlocal batch
        if batch:
            emit_batch(batch)
            batch = []

    while stack and not is_cancelled():
        current_directory = stack.pop()
        try:
            directory_key = os.path.normcase(os.path.realpath(current_directory))
        except OSError:
            directory_key = os.path.normcase(os.path.abspath(current_directory))

        if directory_key in visited:
            continue
        visited.add(directory_key)

        try:
            with os.scandir(current_directory) as entries:
                for entry in entries:
                    if is_cancelled():
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not _is_linked_directory(entry):
                                stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            extension = os.path.splitext(entry.name)[1].lower()
                            if extension in valid_exts:
                                batch.append(
                                    (entry.stat(follow_symlinks=False).st_mtime, entry.path)
                                )
                                if len(batch) >= batch_size:
                                    flush_batch()
                    except OSError as exc:
                        if len(warnings) < 3:
                            warnings.append(f"Skipped inaccessible item: {entry.path} ({exc})")
        except OSError as exc:
            if len(warnings) < 3:
                warnings.append(f"Skipped inaccessible directory: {current_directory} ({exc})")

    if is_cancelled():
        return True, warnings
    flush_batch()
    return False, warnings


def _scan_directory_in_subprocess(
    result_connection,
    cancel_event,
    directory,
    batch_size,
):
    """Run the potentially blocking filesystem scan in a read-only child process."""
    try:
        cancelled, warnings = _scan_directory_records(
            directory,
            cancel_event.is_set,
            lambda batch: result_connection.send(("batch", batch)),
            batch_size,
        )
        if not cancelled:
            result_connection.send(("finished", warnings))
    except Exception as exc:
        try:
            result_connection.send(("error", f"Scan process failed: {exc}"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        result_connection.close()


class DirectoryScanner(QThread):
    """Supervise a read-only scan process without ever force-stopping this QThread."""

    scan_finished = pyqtSignal(int, list, str)
    SCAN_BATCH_SIZE = 256
    POLL_SECONDS = 0.05
    CANCEL_GRACE_SECONDS = 0.5
    PROCESS_STOP_GRACE_SECONDS = 0.5
    scan_worker_target = staticmethod(_scan_directory_in_subprocess)

    def __init__(self, directory, session_id):
        super().__init__()
        self.directory = directory
        self.session_id = session_id
        self._cancel_event = threading.Event()
        self._process_context = multiprocessing.get_context("spawn")
        self._child_cancel_event = self._process_context.Event()
        self._cancel_deadline = None

    def _request_child_cancellation(self):
        self._child_cancel_event.set()
        if self._cancel_deadline is None:
            self._cancel_deadline = time.monotonic() + self.CANCEL_GRACE_SECONDS

    def cancel(self):
        self._cancel_event.set()
        self._request_child_cancellation()
        self.requestInterruption()

    def _cancelled(self):
        return self._cancel_event.is_set() or self.isInterruptionRequested()

    @staticmethod
    def _is_linked_directory(entry):
        return _is_linked_directory(entry)

    def _end_scan_process(self, process):
        """Finish the child from this supervisor thread, with bounded waits only."""
        if process is None or process.pid is None:
            return
        try:
            process.join(self.PROCESS_STOP_GRACE_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join(self.PROCESS_STOP_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(self.PROCESS_STOP_GRACE_SECONDS)
        finally:
            try:
                if not process.is_alive():
                    process.close()
            except (OSError, ValueError):
                pass

    def _emit_scan_result(self, image_data, warnings):
        image_data.sort(key=lambda item: (item[0], os.path.normcase(item[1])))
        self.scan_finished.emit(self.session_id, image_data, "\n".join(warnings))

    def run(self):
        if self._cancelled():
            return

        receive_connection = None
        send_connection = None
        process = None
        image_data = []
        warnings = []
        completed = False
        try:
            receive_connection, send_connection = self._process_context.Pipe(duplex=False)
            process = self._process_context.Process(
                target=self.scan_worker_target,
                args=(
                    send_connection,
                    self._child_cancel_event,
                    self.directory,
                    self.SCAN_BATCH_SIZE,
                ),
            )
            process.daemon = True
            process.start()
            send_connection.close()
            send_connection = None

            while True:
                if self._cancelled():
                    self._request_child_cancellation()

                if receive_connection.poll(self.POLL_SECONDS):
                    try:
                        message = receive_connection.recv()
                    except EOFError:
                        message = None

                    if not isinstance(message, tuple) or not message:
                        if not self._cancelled():
                            warnings.append("Scan process returned no valid result.")
                        break

                    message_type = message[0]
                    if message_type == "batch" and len(message) == 2:
                        image_data.extend(message[1])
                    elif message_type == "finished" and len(message) == 2:
                        warnings.extend(message[1])
                        completed = True
                        break
                    elif message_type == "error" and len(message) == 2:
                        warnings.append(message[1])
                        break

                if self._cancelled():
                    if time.monotonic() >= self._cancel_deadline:
                        return
                elif not process.is_alive():
                    if receive_connection.poll(self.POLL_SECONDS):
                        continue
                    warnings.append("Scan process ended unexpectedly.")
                    break

            if not self._cancelled():
                if not completed and not warnings:
                    warnings.append("Scan did not complete normally.")
                self._emit_scan_result(image_data, warnings)
        except Exception as exc:
            if not self._cancelled():
                warnings.append(f"Scan supervisor failed: {exc}")
                self._emit_scan_result(image_data, warnings)
        finally:
            if process is not None:
                self._end_scan_process(process)
            if send_connection is not None:
                send_connection.close()
            if receive_connection is not None:
                receive_connection.close()


class ZoomableView(QGraphicsView):
    """Image view with a bounded full-resolution zoom path."""

    zoom_warning = pyqtSignal(str)
    MAX_FULL_ZOOM_PIXELS = 24_000_000

    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("border: 0px;")
        self.setBackgroundBrush(QColor("black"))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

        self.is_zoomed = False
        self.original_pixmap = None
        self.full_pixmap = None
        self.image_path = None

    def _update_scene_rect(self):
        self.scene.setSceneRect(self.pixmap_item.boundingRect())

    def setPixmap(self, pixmap, path=None):
        self.full_pixmap = None
        self.original_pixmap = pixmap
        self.image_path = path
        self.pixmap_item.setPixmap(pixmap)
        self._update_scene_rect()
        self.fit_to_screen()

    def fit_to_screen(self):
        if self.original_pixmap and not self.original_pixmap.isNull():
            self.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self.is_zoomed = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

    def set_zoom(self, zoomed):
        if not self.original_pixmap or self.original_pixmap.isNull():
            return

        if zoomed and not self.is_zoomed:
            if not self.image_path or not os.path.exists(self.image_path):
                return

            try:
                with Image.open(self.image_path) as pil_img:
                    source_pixels = pil_img.width * pil_img.height
                    if source_pixels > self.MAX_FULL_ZOOM_PIXELS:
                        self.zoom_warning.emit(
                            "The original image is too large; the fit-to-screen preview remains to avoid exhausting memory."
                        )
                        return

                    pil_img = ImageOps.exif_transpose(pil_img)
                    self.full_pixmap = QPixmap.fromImage(ImageQt(pil_img).copy())
                    self.pixmap_item.setPixmap(self.full_pixmap)
                    self._update_scene_rect()
            except Exception as exc:
                print(f"Failed to load full res: {exc}")
                self.zoom_warning.emit("Failed to load the original image; the fit-to-screen preview remains available.")
                return

            mouse_pos = self.mapToScene(self.mapFromGlobal(self.cursor().pos()))
            self.resetTransform()
            self.is_zoomed = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.centerOn(mouse_pos)

        elif not zoomed and self.is_zoomed:
            self.pixmap_item.setPixmap(self.original_pixmap)
            self.full_pixmap = None
            self._update_scene_rect()
            self.fit_to_screen()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_zoom(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_zoom(False)
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.is_zoomed:
            self.fit_to_screen()


class FastPhotoSorter(QMainWindow):
    UNDO_TIMEOUT_MS = 3000
    SHUTDOWN_GRACE_PERIOD_MS = 5000

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fast Photo Sorter")
        self.resize(1280, 720)
        self.showMaximized()
        self.setStyleSheet("background-color: black;")

        self.image_paths = []
        self.path_mtimes = {}
        self.path_sort_keys = {}
        self.current_index = -1
        self.current_directory = None
        self.cache = {}
        self.deleted_indices = set()
        self.pending_deletions = {}
        self.last_action = None
        self.session_id = 0
        self._scanners = set()
        self._closing = False
        self._shutdown_deadline = None
        self._shutdown_waiting_notice_shown = False

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.image_view = ZoomableView()
        self.image_view.zoom_warning.connect(self.show_zoom_warning)
        self.layout.addWidget(self.image_view)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.image_label)
        self.image_label.hide()

        self.osd_label = QLabel(self.central_widget)
        self.osd_label.setFont(QFont("Arial", 14))
        self.osd_label.hide()

        self.loader = ImageLoader()
        self.loader.image_loaded.connect(self.on_image_loaded)
        self.loader.start()

        QTimer.singleShot(100, self.startup_check)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "loader"):
            self.loader.set_target_size(self.size())
        if hasattr(self, "osd_label"):
            self.update_osd_position()

    def update_osd_position(self):
        self.osd_label.adjustSize()
        self.osd_label.move(30, self.height() - self.osd_label.height() - 30)

    @staticmethod
    def _config_path():
        if getattr(sys, "frozen", False):
            config_dir = os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "FastPhotoSorter",
            )
            try:
                os.makedirs(config_dir, exist_ok=True)
                return os.path.join(config_dir, "config.json")
            except OSError:
                pass
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

    def startup_check(self):
        config_path = self._config_path()
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as config_file:
                    last_directory = json.load(config_file).get("last_directory")
                if last_directory and os.path.isdir(last_directory):
                    self.load_directory(last_directory)
                    return
            except Exception as exc:
                print(f"Error reading config.json: {exc}")
        self.select_directory()

    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select Photo Folder (all subfolders will be scanned)"
        )
        if not directory:
            if not self.image_paths:
                self.close()
            return
        self.load_directory(directory)

    def _cancel_pending_deletions(self):
        cancelled_indices = []
        for timer, _, _, _ in self.pending_deletions.values():
            timer.stop()
        for _, _, index, session_id in self.pending_deletions.values():
            if session_id == self.session_id:
                cancelled_indices.append(index)
        self.pending_deletions.clear()
        self.last_action = None
        if cancelled_indices and self.current_directory:
            resume_index = min(cancelled_indices)
            self.current_index = (
                min(self.current_index, resume_index)
                if self.current_index >= 0
                else resume_index
            )
            self.save_progress()

    def _cancel_scanners(self):
        for scanner in tuple(self._scanners):
            if scanner.isRunning():
                scanner.cancel()

    def _on_scanner_finished(self, scanner):
        self._scanners.discard(scanner)
        scanner.deleteLater()

    def _begin_new_session(self, directory):
        self._cancel_pending_deletions()
        self._cancel_scanners()
        self.session_id += 1
        self.current_directory = directory
        self.current_index = -1
        self.image_paths = []
        self.path_mtimes = {}
        self.path_sort_keys = {}
        self.cache.clear()
        self.deleted_indices.clear()
        self.loader.begin_session(self.session_id)

    def load_directory(self, directory):
        directory = os.path.abspath(directory)
        self._begin_new_session(directory)

        try:
            with open(self._config_path(), "w", encoding="utf-8") as config_file:
                json.dump({"last_directory": directory}, config_file, ensure_ascii=False)
        except Exception as exc:
            print(f"Error saving config.json: {exc}")

        self.image_view.hide()
        self.image_label.setText("Scanning photos quickly, please wait...")
        self.image_label.setStyleSheet("color: white; font-size: 24px;")
        self.image_label.show()
        self.osd_label.hide()

        scanner = DirectoryScanner(directory, self.session_id)
        scanner.scan_finished.connect(self.on_scan_finished)
        scanner.finished.connect(lambda scanner=scanner: self._on_scanner_finished(scanner))
        self._scanners.add(scanner)
        self.scanner = scanner
        scanner.start()

    @pyqtSlot(int, list, str)
    def on_scan_finished(self, session_id, image_data, warning):
        if self._closing or session_id != self.session_id:
            return

        if warning:
            print(warning)

        self.image_paths = [path for _, path in image_data]
        self.path_mtimes = {path: mtime for mtime, path in image_data}
        self.path_sort_keys = {
            path: (mtime, os.path.normcase(path)) for mtime, path in image_data
        }
        if not self.image_paths:
            self.current_index = -1
            self._clear_progress()
            self.image_label.setText("No image files found")
            self.image_label.setStyleSheet("color: white; font-size: 24px;")
            self.image_label.show()
            self.image_view.hide()
            return

        self.load_progress()
        self.update_view()

    def _progress_path(self):
        return os.path.join(self.current_directory, ".sorter_progress.json")

    def _clear_progress(self):
        progress_file = self._progress_path()
        try:
            if os.path.isfile(progress_file):
                os.remove(progress_file)
        except OSError as exc:
            print(f"Failed to clear progress: {exc}")

    def load_progress(self):
        self.current_index = 0
        progress_file = self._progress_path()
        if not os.path.exists(progress_file):
            return

        try:
            with open(progress_file, "r", encoding="utf-8") as progress_handle:
                data = json.load(progress_handle)

            if data.get("status") == "completed":
                self.current_index = len(self.image_paths)
                return

            last_path = data.get("last_path")
            last_mtime = data.get("last_mtime")
            last_sort_key = data.get("last_sort_key")
            if last_path in self.image_paths:
                self.current_index = self.image_paths.index(last_path)
            elif (
                isinstance(last_sort_key, list)
                and len(last_sort_key) == 2
                and last_sort_key[0] is not None
            ):
                marker = (last_sort_key[0], str(last_sort_key[1]))
                for index, path in enumerate(self.image_paths):
                    if self.path_sort_keys.get(path, (0, "")) > marker:
                        self.current_index = index
                        break
                else:
                    self.current_index = len(self.image_paths)
            elif last_mtime is not None:
                for index, path in enumerate(self.image_paths):
                    if self.path_mtimes.get(path, 0) >= last_mtime:
                        self.current_index = index
                        break
                else:
                    self.current_index = len(self.image_paths)
        except Exception as exc:
            print(f"Failed to load progress: {exc}")

    def save_progress(self):
        if not self.current_directory or not self.image_paths:
            return

        pending_indices = [
            index
            for _, _, index, pending_session in getattr(
                self, "pending_deletions", {}
            ).values()
            if (
                pending_session == getattr(self, "session_id", None)
                and 0 <= index < len(self.image_paths)
            )
        ]
        checkpoint_index = (
            min(pending_indices) if pending_indices else self.current_index
        )

        if checkpoint_index >= len(self.image_paths):
            data = {
                "status": "completed",
                "last_path": None,
                "last_mtime": None,
                "last_sort_key": None,
            }
        elif checkpoint_index < 0:
            return
        else:
            valid_path = None
            valid_mtime = None
            for index in range(checkpoint_index, -1, -1):
                if index not in self.deleted_indices:
                    valid_path = self.image_paths[index]
                    valid_mtime = self.path_mtimes.get(valid_path)
                    break

            if valid_path is None:
                data = {
                    "status": "completed",
                    "last_path": None,
                    "last_mtime": None,
                    "last_sort_key": None,
                }
            else:
                data = {
                    "status": "in_progress",
                    "last_path": valid_path,
                    "last_mtime": valid_mtime,
                    "last_sort_key": list(
                        self.path_sort_keys.get(
                            valid_path,
                            (valid_mtime, os.path.normcase(valid_path)),
                        )
                    ),
                }

        try:
            with open(self._progress_path(), "w", encoding="utf-8") as progress_handle:
                json.dump(data, progress_handle, ensure_ascii=False)
        except Exception as exc:
            print(f"Failed to save progress: {exc}")

    def _has_current_image(self):
        return 0 <= self.current_index < len(self.image_paths)

    def update_view(self):
        if not self._has_current_image():
            self.image_view.hide()
            self.image_label.show()
            self.image_label.setText(
                "All photos have been sorted!"
                if self.current_index >= len(self.image_paths) and self.image_paths
                else ""
            )
            self.image_label.setStyleSheet("color: white; font-size: 32px;")
            self.osd_label.hide()
            return

        self.request_cache()
        cache_entry = self.cache.get(self.current_index)
        if cache_entry and cache_entry[2] == self.image_paths[self.current_index]:
            pixmap, metadata, _ = cache_entry
            if pixmap.isNull():
                self.image_view.hide()
                self.image_label.show()
                self.image_label.setText(metadata.get("error", "Failed to load image"))
                self.osd_label.hide()
                return

            self.image_label.hide()
            self.image_view.show()
            self.image_view.setPixmap(pixmap, self.image_paths[self.current_index])

            filename = os.path.basename(self.image_paths[self.current_index])
            progress = f"{self.current_index + 1} / {len(self.image_paths)}"
            osd_text = (
                f"File name: {filename}\n"
                f"Progress: {progress}\n"
                f"Date taken: {metadata['date']}\n"
                f"Resolution: {metadata['resolution']}\n"
                f"Size: {metadata['size_mb']:.2f} MB\n\n"
                f" ──────────────\n"
                f" Space/Left click: Hold to zoom original\n"
                f" D: Keep / Next photo\n"
                f" W: Delete\n"
                f" A: Undo\n"
                f" O: Open another folder\n"
                f" F11: Toggle fullscreen"
            )

            if (
                self.last_action
                and self.last_action["action"] == "delete"
                and self.last_action["session_id"] == self.session_id
            ):
                osd_text += "\n\n[Previous photo deleted — press A to undo]"
                self.osd_label.setStyleSheet(
                    "color: #ffaa00; background-color: rgba(0, 0, 0, 200);"
                    "padding: 15px; border-radius: 8px; border: 1px solid #ffaa00;"
                )
            else:
                self.osd_label.setStyleSheet(
                    "color: white; background-color: rgba(0, 0, 0, 150);"
                    "padding: 15px; border-radius: 8px; border: none;"
                )

            self.osd_label.setText(osd_text)
            self.osd_label.show()
            self.update_osd_position()
            return

        self.image_view.hide()
        self.image_label.show()
        self.image_label.setText("Loading...")
        self.image_label.setStyleSheet("color: white; font-size: 24px;")
        self.osd_label.hide()

    def request_cache(self):
        needed_indices = list(
            range(
                max(0, self.current_index - 1),
                min(len(self.image_paths), self.current_index + 4),
            )
        )

        for index in list(self.cache):
            if index not in needed_indices:
                del self.cache[index]

        requests = []
        for index in needed_indices:
            path = self.image_paths[index]
            cache_entry = self.cache.get(index)
            if cache_entry is None or cache_entry[2] != path:
                requests.append((index, path))
        self.loader.update_window(self.session_id, requests)
        self.save_progress()

    @pyqtSlot(int, int, str, QImage, dict)
    def on_image_loaded(self, session_id, index, path, qimage, metadata):
        if self._closing or session_id != self.session_id:
            return
        if not self._has_current_image():
            return
        if not (max(0, self.current_index - 1) <= index <= self.current_index + 3):
            return
        if index >= len(self.image_paths) or self.image_paths[index] != path:
            return

        pixmap = QPixmap.fromImage(qimage) if not qimage.isNull() else QPixmap()
        self.cache[index] = (pixmap, metadata, path)
        if index == self.current_index:
            self.update_view()

    def show_zoom_warning(self, message):
        if not message:
            return
        existing_text = self.osd_label.text()
        if message not in existing_text:
            self.osd_label.setText(f"{existing_text}\n\n[Zoom notice] {message}")
        self.osd_label.show()
        self.update_osd_position()

    def show_operation_error(self, message):
        existing_text = self.osd_label.text()
        self.osd_label.setText(f"{existing_text}\n\n[Operation failed] {message}")
        self.osd_label.show()
        self.update_osd_position()

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
            return
        if key == Qt.Key.Key_Space:
            if self._has_current_image():
                self.image_view.set_zoom(True)
            return
        if key in (Qt.Key.Key_A, Qt.Key.Key_Left):
            self.action_undo()
            return
        if key == Qt.Key.Key_O:
            self.select_directory()
            return
        if key == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showMaximized()
            else:
                self.showFullScreen()
            return
        if not self._has_current_image():
            return

        if key in (Qt.Key.Key_D, Qt.Key.Key_Right):
            self.action_keep()
        elif key in (Qt.Key.Key_W, Qt.Key.Key_Up):
            self.action_delete()

    def keyReleaseEvent(self, event):
        if not event.isAutoRepeat() and event.key() == Qt.Key.Key_Space:
            self.image_view.set_zoom(False)

    def action_keep(self):
        if not self._has_current_image():
            return
        self.last_action = None
        self.current_index += 1
        self.save_progress()
        self.update_view()

    def action_delete(self):
        if not self._has_current_image():
            return

        path = self.image_paths[self.current_index]
        index_to_delete = self.current_index
        session_id = self.session_id
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer_id = id(timer)
        timer.timeout.connect(
            lambda path=path, timer_id=timer_id, index=index_to_delete, session_id=session_id:
            self.execute_delete(path, timer_id, index, session_id)
        )
        timer.start(self.UNDO_TIMEOUT_MS)

        self.pending_deletions[timer_id] = (timer, path, index_to_delete, session_id)
        self.last_action = {
            "action": "delete",
            "index": index_to_delete,
            "timer_id": timer_id,
            "session_id": session_id,
        }
        self.current_index += 1
        self.save_progress()
        self.update_view()

    def execute_delete(self, path, timer_id, index, session_id):
        pending = self.pending_deletions.pop(timer_id, None)
        if pending is None:
            return

        delete_succeeded = not os.path.exists(path)
        try:
            if not delete_succeeded:
                send2trash(os.path.normpath(path))
                print(f"Moved to Recycle Bin: {path}")
                delete_succeeded = True
        except Exception as exc:
            print(f"Delete failed for {path}: {exc}")

        if delete_succeeded and session_id == self.session_id:
            self.deleted_indices.add(index)

        if not delete_succeeded:
            if session_id == self.session_id:
                if self.last_action and self.last_action.get("timer_id") == timer_id:
                    self.last_action = None
                self.current_index = (
                    min(self.current_index, index)
                    if self.current_index >= 0
                    else index
                )
                self.save_progress()
                self.update_view()
                self.show_operation_error(
                    "Could not move the photo to the Recycle Bin. Returned to it; please try again."
                )
            return

        if self.last_action and self.last_action.get("timer_id") == timer_id:
            self.last_action = None
            if session_id == self.session_id:
                self.save_progress()
                self.update_view()

    def action_undo(self):
        if not self.last_action:
            return

        action = self.last_action
        self.last_action = None
        if action["action"] != "delete" or action["session_id"] != self.session_id:
            return

        pending = self.pending_deletions.pop(action["timer_id"], None)
        if pending is None:
            print(f"Unable to undo deletion: the {self.UNDO_TIMEOUT_MS // 1000}-second window has expired and the file is already in the Recycle Bin")
            self.update_view()
            return

        timer, _, _, _ = pending
        timer.stop()
        self.current_index = action["index"]
        self.save_progress()
        self.update_view()

    def _active_workers(self):
        workers = []
        if self.loader.isRunning():
            workers.append(self.loader)
        workers.extend(scanner for scanner in self._scanners if scanner.isRunning())
        return workers

    def _workers_are_running(self):
        return bool(self._active_workers())

    def _request_worker_interruption(self):
        workers = self._active_workers()
        for worker in workers:
            worker.requestInterruption()

    def _begin_shutdown(self):
        self._closing = True
        self._shutdown_waiting_notice_shown = False
        self._shutdown_deadline = (
            time.monotonic() + self.SHUTDOWN_GRACE_PERIOD_MS / 1000
        )
        self._cancel_pending_deletions()
        self.loader.stop()
        self._cancel_scanners()
        self.image_view.hide()
        self.osd_label.hide()
        self.image_label.setText("Stopping background tasks, please wait...")
        self.image_label.setStyleSheet("color: white; font-size: 24px;")
        self.image_label.show()
        QTimer.singleShot(50, self._finish_shutdown)

    def _finish_shutdown(self):
        if not self._workers_are_running():
            self.close()
            return

        if (
            not self._shutdown_waiting_notice_shown
            and self._shutdown_deadline is not None
            and time.monotonic() >= self._shutdown_deadline
        ):
            self._shutdown_waiting_notice_shown = True
            self.image_label.setText("Background tasks are stopping safely; waiting for the current read-only operation to return...")
            self._request_worker_interruption()

        if self._workers_are_running():
            QTimer.singleShot(50, self._finish_shutdown)
        else:
            self.close()

    def closeEvent(self, event):
        if not self._closing:
            self._begin_shutdown()
            event.ignore()
            return
        if self._workers_are_running():
            event.ignore()
            return
        super().closeEvent(event)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    app.setPalette(palette)

    window = FastPhotoSorter()
    sys.exit(app.exec())
