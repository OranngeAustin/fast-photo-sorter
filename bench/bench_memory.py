"""Peak RSS of one preview decode, measured in a fresh process per variant."""
import os
import resource
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

CHILD = r"""
import os, resource, sys
sys.path.insert(0, {here!r})
import bench_preview as bp
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
bp.decode_in_process({path!r})
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"{{(peak - base) / 1024:.1f}}")
"""


def main_bench():
    sys.path.insert(0, HERE)
    import bench_preview as bp

    with tempfile.TemporaryDirectory() as directory:
        path = bp.make_photo(directory)
        values = []
        for _ in range(int(os.environ.get("BENCH_RUNS", "5"))):
            output = subprocess.run(
                [sys.executable, "-c", CHILD.format(here=HERE, path=path)],
                check=True,
                capture_output=True,
                text=True,
                env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
            ).stdout.strip().splitlines()[-1]
            values.append(float(output))
        values.sort()
        print(f"decode peak RSS growth (MB): median={values[len(values) // 2]:.1f} all={values}")


if __name__ == "__main__":
    main_bench()
