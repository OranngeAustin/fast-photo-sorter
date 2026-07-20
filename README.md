# Fast Photo Sorter

A keyboard-first Windows desktop app for reviewing large photo and screenshot collections one image at a time. Keep a photo, schedule it for deletion, undo within three seconds, and continue without losing your place.

## Download

A packaged Windows build is prepared for GitHub Releases. Download the executable from the repository's **Releases** page when available.

## Highlights

- Recursively scans a chosen folder and its subfolders, then orders images from oldest to newest modification time.
- Uses a short, reversible deletion window: files move to the Windows Recycle Bin only after the three-second undo period expires.
- Keeps progress across sessions with a stable path-and-timestamp checkpoint.
- Loads previews in a cancellable background workflow so old tasks cannot leak into a newly selected folder.
- Bounds preview and full-resolution image work to avoid unbounded memory usage on very large photos.
- Skips symlinks and Windows junctions during scanning to avoid directory cycles.

## Controls

| Action | Shortcut or mouse | Result |
| --- | --- | --- |
| Keep and continue | `D` or `Right Arrow` | Keeps the current photo and opens the next one. |
| Delete | `W` or `Up Arrow` | Queues the current photo for deletion and opens the next one. |
| Undo pending deletion | `A` or `Left Arrow` | Cancels the most recent pending deletion within three seconds. |
| Inspect the original | Hold `Space` or the left mouse button | Temporarily zooms into the original image; drag while zoomed to inspect details. |
| Choose another folder | `O` | Cancels pending deletions from the old folder and starts a new session. |
| Toggle fullscreen | `F11` | Switches between fullscreen and windowed mode. |
| Exit | `Esc` or close the window | Safely cancels unexpired deletions and waits for background read-only work to stop. |

## Safety Notes

- Deletions are sent to the Windows Recycle Bin through `Send2Trash`; they are not permanently erased by the app.
- Changing folders or exiting cancels every pending deletion that is still within its undo window.
- If Windows rejects a Recycle Bin operation, the photo remains in place and the app returns to it.
- Only image formats registered by the installed Pillow build are considered. Support for HEIC depends on the Pillow plugins installed on the machine.

## Run from Source

Requirements: Python 3.12 or later on Windows.

```powershell
python -m pip install -r requirements.txt
python main.py
```

When run from source, the app stores its last selected folder in a local `config.json` file. The packaged EXE instead stores it in `%LOCALAPPDATA%\FastPhotoSorter\config.json`. Photo-review progress is stored as `.sorter_progress.json` inside the selected photo folder. These files are intentionally ignored by Git.

## Build a Windows Executable

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --onefile --name FastPhotoSorter main.py
```

## Tests

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -X utf8 -B -m unittest -v test.py
```

The test suite covers scanning, session isolation, corrupted and oversized images, deletion and undo behavior, progress recovery, zooming, subprocess decoding, and safe shutdown.
