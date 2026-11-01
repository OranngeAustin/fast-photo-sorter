import ctypes
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest

import main


def _sleeping_preview_worker(result_connection, *_args):
    """Test-only child target: it deliberately never returns a preview quickly."""
    try:
        time.sleep(10)
    finally:
        result_connection.close()


def _sleeping_scan_worker(result_connection, _cancel_event, *_args):
    """Test-only scan target that simulates an uninterruptible system call."""
    try:
        time.sleep(10)
    finally:
        result_connection.close()


class Event:
    def __init__(self, key):
        self._key = key

    def isAutoRepeat(self):
        return False

    def key(self):
        return self._key


class FakeTimer:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FastPhotoSorterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_directory_scanner_filters_and_sorts_by_mtime(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nested = root / "nested"
            nested.mkdir()
            older = nested / "older.jpg"
            newer = root / "newer.png"
            ignored = root / "ignored.txt"

            Image.new("RGB", (20, 10), (1, 2, 3)).save(older)
            Image.new("RGB", (20, 10), (4, 5, 6)).save(newer)
            ignored.write_text("not an image", encoding="utf-8")
            os.utime(older, (1_000_000_000, 1_000_000_000))
            os.utime(newer, (1_000_000_100, 1_000_000_100))

            received = []
            scanner = main.DirectoryScanner(str(root), 7)
            scanner.scan_finished.connect(
                lambda session_id, records, warning: received.append(
                    (session_id, records, warning)
                )
            )
            scanner.run()

            self.assertEqual(len(received), 1)
            session_id, records, warning = received[0]
            self.assertEqual(session_id, 7)
            self.assertEqual([path for _, path in records], [str(older), str(newer)])
            self.assertEqual(warning, "")

    def test_loader_reports_corrupt_image_without_dying(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            corrupt = Path(temporary_directory) / "corrupt.jpg"
            corrupt.write_bytes(b"not an image")
            received = []
            loader = main.ImageLoader()
            loader.begin_session(1)

            def capture(session_id, index, path, qimage, metadata):
                received.append((session_id, index, path, qimage.isNull(), metadata))
                loader.stop()

            loader.image_loaded.connect(capture)
            loader.update_window(1, [(0, str(corrupt))])
            loader.run()

            self.assertEqual(len(received), 1)
            self.assertEqual(received[0][:2], (1, 0))
            self.assertTrue(received[0][3])

    def test_loader_reports_corrupt_image_from_worker_thread(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            corrupt = Path(temporary_directory) / "corrupt.jpg"
            corrupt.write_bytes(b"not an image")
            received = []
            loader = main.ImageLoader()
            loader.begin_session(1)

            def capture(session_id, index, path, qimage, metadata):
                received.append((session_id, index, path, qimage.isNull(), metadata))
                loader.stop()

            loader.image_loaded.connect(capture)
            loader.start()
            loader.update_window(1, [(0, str(corrupt))])
            deadline = time.monotonic() + 2
            while loader.isRunning() and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)

            self.assertFalse(loader.isRunning())
            self.assertEqual(len(received), 1)
            self.assertTrue(received[0][3])

    def test_loader_decodes_preview_in_read_only_child_process(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "preview.jpg"
            Image.new("RGB", (20, 10), (1, 2, 3)).save(image_path)
            received = []
            loader = main.ImageLoader()
            loader.begin_session(1)

            def capture(session_id, index, path, qimage, metadata):
                received.append((session_id, index, path, qimage, metadata))
                loader.stop()

            loader.image_loaded.connect(capture)
            loader.start()
            loader.update_window(1, [(0, str(image_path))])
            deadline = time.monotonic() + 5
            while loader.isRunning() and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)

            self.assertFalse(loader.isRunning())
            self.assertEqual(len(received), 1)
            self.assertFalse(received[0][3].isNull())
            self.assertEqual(received[0][4]["resolution"], "20 x 10")

    def test_loader_stops_slow_decode_child_without_terminating_qthread(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "preview.jpg"
            Image.new("RGB", (20, 10), (1, 2, 3)).save(image_path)
            loader = main.ImageLoader()
            loader.decode_worker_target = _sleeping_preview_worker
            loader.begin_session(1)
            loader.start()
            loader.update_window(1, [(0, str(image_path))])
            QTest.qWait(250)

            start = time.monotonic()
            loader.stop()
            deadline = start + 3
            while loader.isRunning() and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)

            self.assertFalse(loader.isRunning())
            self.assertLess(time.monotonic() - start, 3)

    def test_loader_replaces_queue_for_new_session(self):
        loader = main.ImageLoader()
        loader.begin_session(1)
        loader.update_window(1, [(0, "old-directory.jpg")])
        old_queue = loader._queue

        loader.begin_session(2)
        loader.update_window(2, [(0, "new-directory.jpg")])

        self.assertIsNot(old_queue, loader._queue)
        self.assertEqual(list(loader._queue.queue), [(2, 0, "new-directory.jpg")])
        self.assertEqual(loader._wanted_requests, {(2, 0, "new-directory.jpg")})

    def test_scanner_handles_deep_logical_tree_without_recursion(self):
        original_scandir = main.os.scandir
        calls = [0]

        class FakeEntry:
            def __init__(self, depth):
                self.path = f"deep/{depth}"
                self.name = "child"

            def is_symlink(self):
                return False

            def is_dir(self, follow_symlinks=False):
                return True

            def is_file(self, follow_symlinks=False):
                return False

            def stat(self, follow_symlinks=False):
                return SimpleNamespace(st_file_attributes=0)

        class FakeEntries:
            def __init__(self, entries):
                self.entries = entries

            def __enter__(self):
                return iter(self.entries)

            def __exit__(self, *_args):
                return False

        def fake_scandir(_directory):
            calls[0] += 1
            if calls[0] <= 1_500:
                return FakeEntries([FakeEntry(calls[0])])
            return FakeEntries([])

        records = []
        try:
            main.os.scandir = fake_scandir
            cancelled, warnings = main._scan_directory_records(
                "deep",
                lambda: False,
                records.extend,
                64,
            )
        finally:
            main.os.scandir = original_scandir

        self.assertGreater(calls[0], 1_500)
        self.assertFalse(cancelled)
        self.assertEqual(records, [])
        self.assertEqual(warnings, [])

    def test_scanner_delivers_result_from_worker_thread(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            image = root / "sample.jpg"
            Image.new("RGB", (20, 10), (1, 2, 3)).save(image)
            received = []
            scanner = main.DirectoryScanner(str(root), 2)
            scanner.scan_finished.connect(
                lambda session_id, records, warning: received.append(
                    (session_id, records, warning)
                )
            )
            scanner.start()
            deadline = time.monotonic() + 2
            while scanner.isRunning() and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)
            self.app.processEvents()

            self.assertFalse(scanner.isRunning())
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0][0], 2)
            self.assertEqual(received[0][1][0][1], str(image))

    def test_scanner_reports_supervisor_startup_failure(self):
        class Connection:
            def close(self):
                pass

        class FailingProcess:
            pid = None
            daemon = False

            def start(self):
                raise RuntimeError("spawn failed")

        class FailingContext:
            def Pipe(self, duplex=False):
                return Connection(), Connection()

            def Process(self, **_kwargs):
                return FailingProcess()

        received = []
        scanner = main.DirectoryScanner("unavailable", 3)
        scanner._process_context = FailingContext()
        scanner.scan_finished.connect(
            lambda session_id, records, warning: received.append(
                (session_id, records, warning)
            )
        )
        scanner.run()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], 3)
        self.assertEqual(received[0][1], [])
        self.assertIn("扫描监督器失败：spawn failed", received[0][2])

    def test_scanner_stops_uninterruptible_child_after_cancel_deadline(self):
        scanner = main.DirectoryScanner("blocked-network-share", 4)
        scanner.scan_worker_target = _sleeping_scan_worker
        scanner.CANCEL_GRACE_SECONDS = 0.1
        scanner.PROCESS_STOP_GRACE_SECONDS = 0.2
        scanner.start()
        QTest.qWait(150)

        start = time.monotonic()
        scanner.cancel()
        deadline = start + 3
        while scanner.isRunning() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)

        self.assertFalse(scanner.isRunning())
        self.assertLess(time.monotonic() - start, 3)

    def test_shutdown_completes_with_uninterruptible_scan_child(self):
        class NoStartupSorter(main.FastPhotoSorter):
            def startup_check(self):
                pass

            def close(self):
                self.close_calls += 1

        window = NoStartupSorter()
        window.close_calls = 0
        scanner = main.DirectoryScanner("blocked-network-share", 5)
        scanner.scan_worker_target = _sleeping_scan_worker
        scanner.CANCEL_GRACE_SECONDS = 0.1
        scanner.PROCESS_STOP_GRACE_SECONDS = 0.2
        window._scanners.add(scanner)
        scanner.start()
        QTest.qWait(150)

        start = time.monotonic()
        window._begin_shutdown()
        deadline = start + 4
        while window.close_calls == 0 and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)

        self.assertGreater(window.close_calls, 0)
        self.assertFalse(scanner.isRunning())
        self.assertFalse(window._workers_are_running())
        self.assertLess(time.monotonic() - start, 4)

    def test_end_of_list_undo_is_not_blocked_by_keyboard_guard(self):
        calls = []

        class Dummy:
            current_index = 1
            image_paths = ["only.jpg"]

            def _has_current_image(self):
                return False

            def action_undo(self):
                calls.append("undo")

        main.FastPhotoSorter.keyPressEvent(Dummy(), Event(Qt.Key.Key_A))
        self.assertEqual(calls, ["undo"])

    def test_empty_state_w_does_not_call_delete(self):
        calls = []

        class Dummy:
            current_index = -1
            image_paths = []

            def _has_current_image(self):
                return False

            def action_delete(self):
                calls.append("delete")

        main.FastPhotoSorter.keyPressEvent(Dummy(), Event(Qt.Key.Key_W))
        self.assertEqual(calls, [])

    def test_initial_directory_cancel_uses_controlled_close(self):
        calls = []

        class Dummy:
            image_paths = []

            def close(self):
                calls.append("close")

        fake_dialog = type(
            "FakeDialog",
            (),
            {"getExistingDirectory": staticmethod(lambda *_args: "")},
        )
        with patch.object(main, "QFileDialog", fake_dialog):
            main.FastPhotoSorter.select_directory(Dummy())
        self.assertEqual(calls, ["close"])

    def test_close_event_starts_controlled_shutdown(self):
        calls = []

        class Dummy:
            _closing = False

            def _begin_shutdown(self):
                calls.append("shutdown")

        class CloseEvent:
            def ignore(self):
                calls.append("ignored")

        main.FastPhotoSorter.closeEvent(Dummy(), CloseEvent())
        self.assertEqual(calls, ["shutdown", "ignored"])

    def test_real_loader_stops_before_window_closes(self):
        class NoStartupSorter(main.FastPhotoSorter):
            def startup_check(self):
                pass

        window = NoStartupSorter()
        window._begin_shutdown()
        deadline = time.monotonic() + 2
        while window._workers_are_running() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)

        self.assertFalse(window._workers_are_running())
        self.app.processEvents()
        window.close()

    def test_new_session_resets_cross_directory_state(self):
        calls = []
        timer = FakeTimer()

        class FakeLoader:
            def begin_session(self, session_id):
                calls.append(("loader", session_id))

        class Dummy:
            session_id = 2
            current_directory = "old"
            current_index = 4
            image_paths = ["old.jpg"]
            path_mtimes = {"old.jpg": 1}
            cache = {0: object()}
            deleted_indices = {0}
            pending_deletions = {3: (timer, "old.jpg", 0, 2)}
            last_action = {"action": "delete"}
            loader = FakeLoader()

            def _cancel_pending_deletions(self):
                main.FastPhotoSorter._cancel_pending_deletions(self)
                calls.append("pending")

            def _cancel_scanners(self):
                calls.append("scanners")

            def save_progress(self):
                calls.append("progress")

        dummy = Dummy()
        main.FastPhotoSorter._begin_new_session(dummy, "new")
        self.assertEqual(dummy.session_id, 3)
        self.assertEqual(dummy.current_directory, "new")
        self.assertEqual(dummy.current_index, -1)
        self.assertEqual(dummy.image_paths, [])
        self.assertEqual(dummy.path_mtimes, {})
        self.assertEqual(dummy.cache, {})
        self.assertEqual(dummy.deleted_indices, set())
        self.assertEqual(dummy.pending_deletions, {})
        self.assertIsNone(dummy.last_action)
        self.assertTrue(timer.stopped)
        self.assertIn(("loader", 3), calls)

    def test_cancelled_pending_delete_rewinds_saved_progress(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = [str(root / "first.jpg"), str(root / "second.jpg")]
            timer = FakeTimer()

            class Dummy:
                current_directory = str(root)
                image_paths = paths
                path_mtimes = {paths[0]: 10.0, paths[1]: 20.0}
                path_sort_keys = {
                    paths[0]: (10.0, os.path.normcase(paths[0])),
                    paths[1]: (20.0, os.path.normcase(paths[1])),
                }
                current_index = 2
                deleted_indices = set()
                session_id = 4
                pending_deletions = {9: (timer, paths[1], 1, 4)}
                last_action = {"action": "delete"}

                def _progress_path(self):
                    return str(root / ".sorter_progress.json")

                def save_progress(self):
                    main.FastPhotoSorter.save_progress(self)

            dummy = Dummy()
            main.FastPhotoSorter._cancel_pending_deletions(dummy)
            self.assertTrue(timer.stopped)
            self.assertEqual(dummy.current_index, 1)
            with open(root / ".sorter_progress.json", "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["last_path"], paths[1])

    def test_delete_failure_rewinds_progress_and_reports_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            path = root / "failed.jpg"
            path.write_bytes(b"still here")
            updates = []
            errors = []

            class Dummy:
                current_directory = str(root)
                image_paths = [str(path)]
                path_mtimes = {str(path): 10.0}
                path_sort_keys = {str(path): (10.0, os.path.normcase(str(path)))}
                current_index = 1
                deleted_indices = set()
                session_id = 1
                pending_deletions = {8: (FakeTimer(), str(path), 0, 1)}
                last_action = {
                    "action": "delete",
                    "index": 0,
                    "timer_id": 8,
                    "session_id": 1,
                }

                def _progress_path(self):
                    return str(root / ".sorter_progress.json")

                def save_progress(self):
                    main.FastPhotoSorter.save_progress(self)

                def update_view(self):
                    updates.append(self.current_index)

                def show_operation_error(self, message):
                    errors.append(message)

            dummy = Dummy()
            with patch.object(main, "send2trash", side_effect=OSError("denied")):
                main.FastPhotoSorter.execute_delete(dummy, str(path), 8, 0, 1)

            self.assertTrue(path.exists())
            self.assertEqual(dummy.current_index, 0)
            self.assertEqual(dummy.deleted_indices, set())
            self.assertEqual(updates, [0])
            self.assertTrue(errors)
            with open(root / ".sorter_progress.json", "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["status"], "in_progress")

    def test_missing_progress_path_uses_next_stable_sort_key(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            earlier = str(root / "earlier.jpg")
            missing = str(root / "middle.jpg")
            next_path = str(root / "next.jpg")
            for path in (earlier, next_path):
                Path(path).write_bytes(b"x")
            sort_keys = {
                earlier: (100.0, os.path.normcase(earlier)),
                next_path: (100.0, os.path.normcase(next_path)),
            }
            with open(root / ".sorter_progress.json", "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "status": "in_progress",
                        "last_path": missing,
                        "last_mtime": 100.0,
                        "last_sort_key": [100.0, os.path.normcase(missing)],
                    },
                    handle,
                )

            class Dummy:
                current_directory = str(root)
                image_paths = [earlier, next_path]
                path_mtimes = {earlier: 100.0, next_path: 100.0}
                path_sort_keys = sort_keys
                current_index = -1

                def _progress_path(self):
                    return str(root / ".sorter_progress.json")

            dummy = Dummy()
            main.FastPhotoSorter.load_progress(dummy)
            self.assertEqual(dummy.current_index, 1)

    def test_oversized_unsupported_preview_reports_clear_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "large.png"
            Image.new("RGB", (20, 20), (1, 2, 3)).save(image_path)
            loader = main.ImageLoader()
            loader.MAX_PREVIEW_SOURCE_PIXELS = 1
            loader.begin_session(1)
            received = []

            def capture(session_id, index, path, qimage, metadata):
                received.append((qimage.isNull(), metadata))
                loader.stop()

            loader.image_loaded.connect(capture)
            loader.update_window(1, [(0, str(image_path))])
            loader.run()

            self.assertEqual(len(received), 1)
            self.assertTrue(received[0][0])
            self.assertIn("像素过大", received[0][1]["error"])

    def test_shutdown_requests_cooperative_worker_interruption(self):
        calls = []

        class Worker:
            def __init__(self):
                self.running = True

            def isRunning(self):
                return self.running

            def requestInterruption(self):
                calls.append("interrupt")

        worker = Worker()

        class Dummy:
            def _active_workers(self):
                return [worker]

        main.FastPhotoSorter._request_worker_interruption(Dummy())
        self.assertEqual(calls, ["interrupt"])

    def test_completion_is_saved_and_restored(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            image_path = str(root / "only.jpg")

            class SaveDummy:
                current_directory = str(root)
                image_paths = [image_path]
                path_mtimes = {image_path: 123.0}
                current_index = 1
                deleted_indices = set()

                def _progress_path(self):
                    return str(root / ".sorter_progress.json")

            main.FastPhotoSorter.save_progress(SaveDummy())
            with open(root / ".sorter_progress.json", "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["status"], "completed")

            class LoadDummy:
                current_directory = str(root)
                image_paths = [image_path]
                path_mtimes = {image_path: 123.0}
                current_index = -1

                def _progress_path(self):
                    return str(root / ".sorter_progress.json")

            dummy = LoadDummy()
            main.FastPhotoSorter.load_progress(dummy)
            self.assertEqual(dummy.current_index, 1)

    def test_zoom_updates_scene_rect_and_limits_large_sources(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            normal_image = root / "normal.jpg"
            Image.new("RGB", (3000, 2000), (1, 2, 3)).save(normal_image)

            thumbnail = QImage(800, 533, QImage.Format.Format_ARGB32)
            thumbnail.fill(0)
            view = main.ZoomableView()
            view.resize(800, 600)
            view.show()
            self.app.processEvents()

            view.setPixmap(QPixmap.fromImage(thumbnail), str(normal_image))
            view.set_zoom(True)
            self.assertTrue(view.is_zoomed)
            self.assertEqual(view.pixmap_item.pixmap().size().width(), 3000)
            self.assertEqual(int(view.scene.sceneRect().width()), 3000)
            self.assertEqual(int(view.scene.sceneRect().height()), 2000)

            view.set_zoom(False)
            view.MAX_FULL_ZOOM_PIXELS = 1
            warnings = []
            view.zoom_warning.connect(warnings.append)
            view.set_zoom(True)
            self.assertFalse(view.is_zoomed)
            self.assertEqual(view.pixmap_item.pixmap().size().width(), 800)
            self.assertTrue(warnings)
            view.close()

    def test_zoom_keeps_pressed_image_point_under_cursor(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            image_path = Path(temporary_directory) / "wide.jpg"
            Image.new("RGB", (3000, 2000), (1, 2, 3)).save(image_path)
            thumbnail = QImage(750, 500, QImage.Format.Format_ARGB32)
            thumbnail.fill(0)
            view = main.ZoomableView()
            view.resize(800, 600)
            view.show()
            self.app.processEvents()
            try:
                view.setPixmap(QPixmap.fromImage(thumbnail), str(image_path))
                for press in (QPoint(600, 200), QPoint(200, 420), QPoint(400, 300)):
                    on_preview = view.mapToScene(press)
                    expected_x = on_preview.x() * 3000 / 750
                    expected_y = on_preview.y() * 2000 / 500

                    QTest.mousePress(
                        view.viewport(), Qt.MouseButton.LeftButton, pos=press
                    )
                    self.assertTrue(view.is_zoomed)
                    on_full = view.mapToScene(press)
                    self.assertAlmostEqual(on_full.x(), expected_x, delta=2)
                    self.assertAlmostEqual(on_full.y(), expected_y, delta=2)

                    QTest.mouseRelease(
                        view.viewport(), Qt.MouseButton.LeftButton, pos=press
                    )
                    self.assertFalse(view.is_zoomed)

                # A cursor outside the viewport (keyboard zoom) anchors on its center.
                viewport_center = view.viewport().rect().center()
                on_preview = view.mapToScene(viewport_center)
                view.set_zoom(True, QPoint(-50, -50))
                on_full = view.mapToScene(viewport_center)
                self.assertAlmostEqual(on_full.x(), on_preview.x() * 3000 / 750, delta=2)
                self.assertAlmostEqual(on_full.y(), on_preview.y() * 2000 / 500, delta=2)
            finally:
                view.close()

    def test_stale_loader_signal_is_ignored(self):
        class Dummy:
            _closing = False
            session_id = 2
            current_index = 0
            image_paths = ["new.jpg"]
            cache = {}

            def _has_current_image(self):
                return True

            def update_view(self):
                raise AssertionError("stale signal should not refresh the UI")

        qimage = QImage(1, 1, QImage.Format.Format_ARGB32)
        main.FastPhotoSorter.on_image_loaded(
            Dummy(),
            1,
            0,
            "old.jpg",
            qimage,
            {"size_mb": 0.0, "resolution": "1 x 1", "date": "未知"},
        )
        self.assertEqual(Dummy.cache, {})

    def test_empty_scan_clears_stale_progress(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            progress_file = root / ".sorter_progress.json"
            progress_file.write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )

            class Label:
                def __init__(self):
                    self.text = ""

                def setText(self, text):
                    self.text = text

                def setStyleSheet(self, _style):
                    pass

                def show(self):
                    pass

            class View:
                def __init__(self):
                    self.hidden = False

                def hide(self):
                    self.hidden = True

            label = Label()
            view = View()

            class Dummy:
                _closing = False
                session_id = 7
                current_directory = str(root)
                image_paths = ["stale.jpg"]
                path_mtimes = {}
                path_sort_keys = {}
                current_index = 5
                image_label = label
                image_view = view

                def _progress_path(self):
                    return str(progress_file)

                def _clear_progress(self):
                    main.FastPhotoSorter._clear_progress(self)

            dummy = Dummy()
            main.FastPhotoSorter.on_scan_finished(dummy, 7, [], "")

            self.assertFalse(progress_file.exists())
            self.assertEqual(dummy.current_index, -1)
            self.assertEqual(label.text, "未找到任何图片文件")
            self.assertTrue(view.hidden)

            new_path = str(root / "new.jpg")
            dummy.image_paths = [new_path]
            dummy.path_mtimes = {new_path: 1.0}
            dummy.path_sort_keys = {new_path: (1.0, os.path.normcase(new_path))}
            main.FastPhotoSorter.load_progress(dummy)
            self.assertEqual(dummy.current_index, 0)

    def test_pending_last_delete_does_not_save_completed_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            image_path = str(root / "last.jpg")

            class Dummy:
                current_directory = str(root)
                image_paths = [image_path]
                path_mtimes = {image_path: 1.0}
                path_sort_keys = {
                    image_path: (1.0, os.path.normcase(image_path))
                }
                current_index = 1
                deleted_indices = set()
                session_id = 8
                pending_deletions = {
                    9: (FakeTimer(), image_path, 0, 8)
                }

                def _progress_path(self):
                    return str(root / ".sorter_progress.json")

            main.FastPhotoSorter.save_progress(Dummy())
            with open(root / ".sorter_progress.json", "r", encoding="utf-8") as handle:
                data = json.load(handle)

            self.assertEqual(data["status"], "in_progress")
            self.assertEqual(data["last_path"], image_path)

    def test_scanner_keeps_only_photo_formats(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for name in (
                "photo.jpg",
                "shot.png",
                "phone.webp",
                "report.pdf",
                "art.eps",
                "page.ps",
                "layers.psd",
                "icon.ico",
                "export.palm",
            ):
                (root / name).write_bytes(b"x")

            records = []
            main._scan_directory_records(str(root), lambda: False, records.extend, 64)

            self.assertEqual(
                sorted(os.path.basename(path) for _, path in records),
                ["phone.webp", "photo.jpg", "shot.png"],
            )

    def test_scanner_skips_recycle_bins_thumbnail_caches_and_dot_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".chosen-root"
            for relative_path in (
                "keep.jpg",
                "trip/day1.jpg",
                "trip/._day1.jpg",
                "$RECYCLE.BIN/S-1-5-21/$R0001.jpg",
                "System Volume Information/restore.jpg",
                "#recycle/old.jpg",
                "#snapshot/GMT+08-2026.09.01/keep.jpg",
                "@eaDir/keep.jpg/SYNOPHOTO_THUMB_XL.jpg",
                "@Recycle/old.jpg",
                ".thumbnails/123.jpg",
            ):
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")

            records = []
            main._scan_directory_records(str(root), lambda: False, records.extend, 64)

            self.assertEqual(
                sorted(os.path.relpath(path, root) for _, path in records),
                ["keep.jpg", os.path.join("trip", "day1.jpg")],
            )

    @unittest.skipUnless(os.name == "nt", "Windows file attributes")
    def test_scanner_skips_windows_hidden_and_system_entries(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            hidden_directory = root / "Private"
            hidden_directory.mkdir()
            (hidden_directory / "inside.jpg").write_bytes(b"x")
            system_file = root / "system.jpg"
            system_file.write_bytes(b"x")
            (root / "visible.jpg").write_bytes(b"x")
            set_attributes = ctypes.windll.kernel32.SetFileAttributesW
            self.assertTrue(
                set_attributes(str(hidden_directory), stat.FILE_ATTRIBUTE_HIDDEN)
            )
            self.assertTrue(set_attributes(str(system_file), stat.FILE_ATTRIBUTE_SYSTEM))

            records = []
            main._scan_directory_records(str(root), lambda: False, records.extend, 64)

            self.assertEqual(
                [os.path.basename(path) for _, path in records], ["visible.jpg"]
            )

    def test_delete_ignored_until_current_image_is_shown(self):
        class NoStartupSorter(main.FastPhotoSorter):
            def startup_check(self):
                pass

        window = NoStartupSorter()
        try:
            path = os.path.join("photos", "unseen.jpg")
            window.image_paths = [path, os.path.join("photos", "next.jpg")]
            window.current_index = 0
            window.loader.update_window = lambda *_args: None
            window.update_view()
            self.assertEqual(window.image_label.text(), "加载中...")

            window.keyPressEvent(Event(Qt.Key.Key_W))
            self.assertEqual(window.pending_deletions, {})
            self.assertEqual(window.current_index, 0)

            window.cache[0] = (
                QPixmap(),
                {
                    "size_mb": 0.1,
                    "resolution": "未知",
                    "date": "未知",
                    "error": "图片加载失败",
                },
                path,
            )
            window.update_view()
            window.keyPressEvent(Event(Qt.Key.Key_W))
            self.assertEqual(
                [pending[1] for pending in window.pending_deletions.values()], [path]
            )
            self.assertEqual(window.current_index, 1)
        finally:
            window._begin_shutdown()
            deadline = time.monotonic() + 2
            while window._workers_are_running() and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)
            window.close()

    def test_failed_preview_keeps_file_name_visible(self):
        class NoStartupSorter(main.FastPhotoSorter):
            def startup_check(self):
                pass

        window = NoStartupSorter()
        try:
            path = os.path.join("photos", "broken.jpg")
            window.image_paths = [path]
            window.current_index = 0
            window.loader.update_window = lambda *_args: None
            window.cache[0] = (
                QPixmap(),
                {
                    "size_mb": 0.1,
                    "resolution": "未知",
                    "date": "未知",
                    "error": "图片加载失败",
                },
                path,
            )
            window.update_view()

            self.assertEqual(window.image_label.text(), "图片加载失败")
            self.assertFalse(window.osd_label.isHidden())
            self.assertIn("broken.jpg", window.osd_label.text())
            self.assertIn("1 / 1", window.osd_label.text())
        finally:
            window._begin_shutdown()
            deadline = time.monotonic() + 2
            while window._workers_are_running() and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)
            window.close()


class ProgressPersistenceCharacterizationTests(unittest.TestCase):
    """Pin what .sorter_progress.json holds after each user-visible step."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, directory, count=3):
        class NoStartupSorter(main.FastPhotoSorter):
            def startup_check(self):
                pass

        window = NoStartupSorter()
        window.loader.update_window = lambda *_args: None
        window.current_directory = directory
        window.image_paths = [
            os.path.join(directory, f"IMG_{index}.jpg") for index in range(count)
        ]
        window.path_mtimes = {
            path: float(index) for index, path in enumerate(window.image_paths)
        }
        window.path_sort_keys = {
            path: (mtime, os.path.normcase(path))
            for path, mtime in window.path_mtimes.items()
        }
        window.current_index = 0
        return window

    def close_window(self, window):
        window._begin_shutdown()
        deadline = time.monotonic() + 2
        while window._workers_are_running() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        window.close()

    @staticmethod
    def read(directory):
        with open(os.path.join(directory, ".sorter_progress.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def test_keep_sequence_persists_each_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self.make_window(directory)
            try:
                window.update_view()
                self.assertEqual(self.read(directory)["last_path"], window.image_paths[0])
                for index in (1, 2):
                    window.action_keep()
                    data = self.read(directory)
                    self.assertEqual(data["status"], "in_progress")
                    self.assertEqual(data["last_path"], window.image_paths[index])
                    self.assertEqual(data["last_mtime"], float(index))
                    self.assertEqual(
                        data["last_sort_key"],
                        [float(index), os.path.normcase(window.image_paths[index])],
                    )
                window.action_keep()
                self.assertEqual(
                    self.read(directory),
                    {
                        "status": "completed",
                        "last_path": None,
                        "last_mtime": None,
                        "last_sort_key": None,
                    },
                )
            finally:
                self.close_window(window)

    def test_progress_is_rewritten_after_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self.make_window(directory)
            try:
                window.save_progress()
                window._clear_progress()
                self.assertFalse(os.path.exists(os.path.join(directory, ".sorter_progress.json")))
                window.save_progress()
                self.assertEqual(self.read(directory)["last_path"], window.image_paths[0])
            finally:
                self.close_window(window)

    def test_failed_write_is_retried_with_same_content(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "later")
            window = self.make_window(target)
            try:
                window.save_progress()  # directory missing: write fails
                os.mkdir(target)
                window.save_progress()
                self.assertEqual(self.read(target)["last_path"], window.image_paths[0])
            finally:
                self.close_window(window)

    def test_new_session_on_same_directory_rewrites_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self.make_window(directory)
            try:
                window.save_progress()
                os.remove(os.path.join(directory, ".sorter_progress.json"))
                window._begin_new_session(directory)
                window.image_paths = [os.path.join(directory, "IMG_0.jpg")]
                window.path_mtimes = {window.image_paths[0]: 0.0}
                window.path_sort_keys = {
                    window.image_paths[0]: (0.0, os.path.normcase(window.image_paths[0]))
                }
                window.current_index = 0
                window.save_progress()
                self.assertEqual(self.read(directory)["last_path"], window.image_paths[0])
            finally:
                self.close_window(window)


if __name__ == "__main__":
    unittest.main()
