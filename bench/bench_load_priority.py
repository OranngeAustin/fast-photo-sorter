"""Time until the *current* preview arrives when the preload window is cold.

Cold windows happen on resume (load_progress), undo, failed-delete rewind and
directory switch. Run as a script so spawn children re-import ``main`` like
the real app does.
"""
import os
import tempfile
import time

from _common import measure

import main
from PIL import Image
from PyQt6.QtCore import Qt


class Window:
    def __init__(self, paths, current_index, loader):
        self.image_paths = paths
        self.current_index = current_index
        self.session_id = 1
        self.cache = {}
        self.loader = loader

    def save_progress(self):
        pass


def time_to_current(paths, current_index):
    loader = main.ImageLoader()
    loader.begin_session(1)
    arrived = {}
    start = [0.0]

    def on_loaded(_session, index, _path, _qimage, _metadata):
        if index == current_index:
            arrived["at"] = time.perf_counter() - start[0]
            loader.stop()

    loader.image_loaded.connect(on_loaded, Qt.ConnectionType.DirectConnection)
    start[0] = time.perf_counter()
    main.FastPhotoSorter.request_cache(Window(paths, current_index, loader))
    loader.run()
    return arrived["at"]


def main_bench():
    runs = int(os.environ.get("BENCH_RUNS", "5"))
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for index in range(10):
            path = os.path.join(directory, f"IMG_{index}.jpg")
            Image.effect_noise((2000, 1500), 30).convert("RGB").save(path, quality=90)
            paths.append(path)
        results = []
        measure(
            "resume at index 5: time to current preview",
            lambda: results.append(time_to_current(paths, 5)),
            runs,
        )


if __name__ == "__main__":
    main_bench()
