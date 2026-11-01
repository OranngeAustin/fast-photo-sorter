"""Preview pipeline benchmark.

Run as a script (``python bench/bench_preview.py``) so that, like the real
app started with ``pythonw main.py``, every spawn child re-executes this
module, which imports ``main`` (PyQt6 + Pillow) at top level.
"""
import os
import sys
import tempfile

from _common import measure

import main  # noqa: E402  (imported for its spawn side effects as well)
from PIL import Image  # noqa: E402
from PyQt6.QtCore import QSize  # noqa: E402


class _Sink:
    def __init__(self):
        self.value = None

    def send(self, value):
        self.value = value

    def close(self):
        pass


def make_photo(directory, size=(4032, 3024)):
    path = os.path.join(directory, "photo.jpg")
    # A noisy gradient so the JPEG has realistic entropy.
    base = Image.linear_gradient("L").resize(size)
    noise = Image.effect_noise(size, 40)
    Image.merge("RGB", (base, noise, base.transpose(Image.Transpose.FLIP_LEFT_RIGHT))).save(
        path, quality=90
    )
    return path


def decode_in_process(path, target=(1920, 1080)):
    sink = _Sink()
    main._decode_preview_in_subprocess(
        sink,
        path,
        target[0],
        target[1],
        36867,
        main.ImageLoader.MAX_PREVIEW_SOURCE_PIXELS,
        tuple(main.ImageLoader._DRAFT_FORMATS),
        main.ImageLoader.MAX_PREVIEW_TRANSFER_PIXELS,
    )
    assert sink.value[0] == "ok", sink.value
    return sink.value


def loader_round_trip(loader, path, request_ids):
    """One request through ImageLoader._decode_preview (spawn + decode + pipe)."""
    request_ids[0] += 1
    request = (1, request_ids[0], path)
    with loader._lock:
        loader._wanted_requests = {request}
    result = loader._decode_preview(loader._queue, request, QSize(1920, 1080))
    assert result and result[0] == "ok", result


def main_bench():
    runs = int(os.environ.get("BENCH_RUNS", "5"))
    with tempfile.TemporaryDirectory() as directory:
        path = make_photo(directory)
        print(f"source: 4032x3024 JPEG, {os.path.getsize(path) / 1e6:.1f} MB; target 1920x1080")
        measure("decode_in_process (child work only)", lambda: decode_in_process(path), runs, track_memory=False)
        loader = main.ImageLoader()
        loader.begin_session(1)
        ids = [0]
        measure("loader_round_trip (spawn+decode+pipe)", lambda: loader_round_trip(loader, path, ids), runs)
        # Five-image sliding window, as request_cache asks for on a fresh start.
        measure(
            "window_of_5 (sequential, as the loader does)",
            lambda: [loader_round_trip(loader, path, ids) for _ in range(5)],
            max(1, runs // 2 + 1),
        )


if __name__ == "__main__":
    main_bench()
