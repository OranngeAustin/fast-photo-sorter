"""Progress persistence benchmark: per-keypress writes and resume lookup."""
import os
import tempfile
from unittest.mock import patch

from _common import measure

import main


class Window:
    """Just enough FastPhotoSorter state to drive the real progress methods."""

    def __init__(self, directory, count):
        self.current_directory = directory
        self.image_paths = [os.path.join(directory, f"IMG_{index:06d}.jpg") for index in range(count)]
        self.path_mtimes = {path: float(index) for index, path in enumerate(self.image_paths)}
        self.path_sort_keys = {
            path: (self.path_mtimes[path], os.path.normcase(path)) for path in self.image_paths
        }
        self.current_index = 0
        self.deleted_indices = set()
        self.pending_deletions = {}
        self.session_id = 1
        self.last_action = None

    def _progress_path(self):
        return os.path.join(self.current_directory, ".sorter_progress.json")

    def _has_current_image(self):
        return 0 <= self.current_index < len(self.image_paths)

    def save_progress(self):
        main.FastPhotoSorter.save_progress(self)

    def update_view(self):
        # The real update_view -> request_cache also calls save_progress.
        if self._has_current_image():
            self.save_progress()


def navigate(window, steps):
    window.current_index = 0
    for _ in range(steps):
        main.FastPhotoSorter.action_keep(window)


def main_bench():
    runs = int(os.environ.get("BENCH_RUNS", "5"))
    with tempfile.TemporaryDirectory() as directory:
        window = Window(directory, 100_000)
        writes = [0]
        real_write = main._write_json_atomically

        def counting_write(path, data):
            writes[0] += 1
            return real_write(path, data)

        with patch.object(main, "_write_json_atomically", counting_write):
            navigate(window, 10)
        print(f"progress-file writes for 10 keypresses (D): {writes[0]}")
        measure("1000 keypresses (D) incl. progress writes", lambda: navigate(window, 1000), runs)

        window.current_index = 99_000
        window.save_progress()
        target = window.image_paths[99_000]

        def resume_by_path():
            window.current_index = -1
            main.FastPhotoSorter.load_progress(window)
            assert window.current_index == 99_000

        measure("load_progress, last_path present (n=100k)", resume_by_path, runs)
        os.utime  # keep linters quiet
        window.image_paths.remove(target)

        def resume_by_sort_key():
            window.current_index = -1
            main.FastPhotoSorter.load_progress(window)
            assert window.current_index == 99_000

        measure("load_progress, last_path deleted (n=100k)", resume_by_sort_key, runs)


if __name__ == "__main__":
    main_bench()
