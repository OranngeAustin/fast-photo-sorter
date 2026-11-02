"""Shared helpers for the micro-benchmarks (not part of the test suite)."""
import os
import statistics
import sys
import time
import tracemalloc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def measure(label, func, runs=5, setup=None, track_memory=False):
    """Run ``func`` ``runs`` times and print/return the median wall time (s)."""
    timings = []
    peaks = []
    for _ in range(runs):
        if setup is not None:
            setup()
        if track_memory:
            tracemalloc.start()
        start = time.perf_counter()
        func()
        timings.append(time.perf_counter() - start)
        if track_memory:
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
    median = statistics.median(timings)
    extra = ""
    if peaks:
        extra = f"  peak_py_mem={statistics.median(peaks) / 1e6:.1f} MB"
    print(
        f"{label:<48} median={median * 1000:9.2f} ms  "
        f"min={min(timings) * 1000:9.2f}  max={max(timings) * 1000:9.2f}{extra}"
    )
    return median
