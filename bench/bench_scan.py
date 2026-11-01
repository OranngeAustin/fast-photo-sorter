"""Directory scan benchmark: synthetic tree, in-process _scan_directory_records."""
import os
import tempfile

from _common import measure

import main


def build_tree(root, depth=4, fanout=5, files_per_dir=8):
    """fanout**depth leaf directories; every directory holds a few photos."""
    count = [0, 0]

    def make(directory, level):
        os.makedirs(directory, exist_ok=True)
        count[0] += 1
        for index in range(files_per_dir):
            with open(os.path.join(directory, f"IMG_{index:04d}.jpg"), "wb") as handle:
                handle.write(b"x")
            with open(os.path.join(directory, f"note_{index}.txt"), "wb") as handle:
                handle.write(b"x")
            count[1] += 1
        if level < depth:
            for child in range(fanout):
                make(os.path.join(directory, f"album_{child}"), level + 1)

    make(root, 0)
    return count


def scan(root):
    records = []
    main._scan_directory_records(root, lambda: False, records.extend, 256)
    return records


def main_bench():
    runs = int(os.environ.get("BENCH_RUNS", "5"))
    with tempfile.TemporaryDirectory() as directory:
        root = os.path.join(directory, "a", "deep", "base", "path", "photos")
        directories, files = build_tree(root)
        print(f"tree: {directories} directories, {files} photos (+{files} non-photos), root depth 7")
        measure("_scan_directory_records", lambda: scan(root), runs)
        records = scan(root)
        measure(
            "supervisor sort (mtime, normcase)",
            lambda: sorted(records, key=lambda item: (item[0], os.path.normcase(item[1]))),
            runs,
        )


if __name__ == "__main__":
    main_bench()
