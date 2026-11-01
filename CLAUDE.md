# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Fast Photo Sorter is a single-window PyQt6 desktop app for Windows. It shows photos one at a time so the user can keep or delete each one quickly. All application code is in `main.py` and all tests are in `test.py`. UI strings, log messages and the README are in Chinese, so keep new user-facing text in Chinese. There is no CI, no linter config and no packaging.

## Commands

Dependencies are listed in `requirements.txt`: PyQt6, Pillow and Send2Trash. The project is developed on Python 3.12. The tests also pass on Python 3.13 on Linux.

```bash
pip install -r requirements.txt
```

On a headless Linux machine, PyQt6's QtGui also needs `libEGL.so.1`. On Debian or Ubuntu, install it with `apt-get install -y libegl1`. The tests use the `offscreen` platform, so the xcb/X11 libraries are not needed.

Run the full suite (32 tests; the Windows-attribute test is skipped on other platforms):

```bash
QT_QPA_PLATFORM=offscreen python -X utf8 -B -m unittest -v test.py
```

Run a single test:

```bash
QT_QPA_PLATFORM=offscreen python -X utf8 -B -m unittest test.FastPhotoSorterTests.test_completion_is_saved_and_restored
```

The test module is named `test`, which shadows the stdlib `test` package. Run these commands from the repo root so the local `test.py` is imported.

To run the app, use `python main.py`. `启动照片清理.bat` hardcodes `D:\anaconda3\envs\py312-clean\pythonw.exe`.

## Architecture

Three kinds of concurrency work together. Most bugs and tests revolve around keeping them consistent.

- **Session IDs isolate directory switches.** `FastPhotoSorter.session_id` goes up in `_begin_new_session()`. Every scanner result, loader request/signal and pending deletion carries the session ID it was created under. Handlers drop anything whose ID is stale. Keep this pattern for any new async path.
- **Blocking work runs in `spawn` child processes that a QThread supervises.** `DirectoryScanner` and `ImageLoader` are QThreads that start `multiprocessing.get_context("spawn")` children. The children run the module-level functions `_scan_directory_in_subprocess` and `_decode_preview_in_subprocess`, which must stay top-level so they can be pickled. Results come back over a one-way `Pipe`. On cancel or shutdown, the QThread terminates the *child process*; it never terminates the QThread itself. Tests swap in fake child targets through the `scan_worker_target` / `decode_worker_target` class attributes.
- **Deletion is delayed and undoable.** `action_delete` advances the view right away and starts a 3 s `QTimer` (`UNDO_TIMEOUT_MS`). `execute_delete` then calls `send2trash`. Pending entries live in `pending_deletions[timer_id] = (timer, path, index, session_id)`. If a deletion fails or is cancelled, `current_index` rewinds to that image. Switching directories or closing cancels every pending deletion.
- **Progress checkpointing.** `save_progress` writes `.sorter_progress.json` in the photo directory. It records `status` (in progress or completed) and the last path, mtime and sort key. The checkpoint is the earliest pending deletion, not `current_index`, so a crash never skips an undeleted file. When resuming, `load_progress` uses the stable `(mtime, normcase(path))` sort key. That way it can find the next item even if the saved file was deleted outside the app. The last opened directory is stored in `config.json` next to `main.py`. The file is gitignored.
- **Shutdown is cooperative.** `closeEvent` → `_begin_shutdown` cancels timers and scanners and asks the workers to interrupt. It then polls until they stop, giving up after `SHUTDOWN_GRACE_PERIOD_MS`. Python/Qt threads are never force-killed.
- **Memory limits.** Previews refuse to decode sources larger than 24 MP (`MAX_PREVIEW_SOURCE_PIXELS`). The exception is JPEG/MPO, which can use Pillow `draft` downscaling. The RGBA image sent back over the pipe is capped at 6 MP. `ZoomableView` applies the same 24 MP limit to the 1:1 zoom.
- **The scanner** walks the tree with an explicit stack instead of recursion. It skips symlinks and junctions, dot-prefixed and `$`-prefixed directories, NAS and recycle-bin folders (`_SKIPPED_DIRECTORY_NAMES`), and entries with the Windows hidden or system attribute. It only accepts the extensions in `PHOTO_FORMATS` that Pillow can actually open, so PDF, PSD and similar files are left out on purpose. The root directory itself is never filtered.
