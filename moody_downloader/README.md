# Moody Downloader — Phase 1 (Core Engine + Desktop GUI)

A multi-threaded download accelerator for Windows, built with Python + PyQt6.

## Architecture

```
moody_downloader/
├── main.py                    Application entry point
├── requirements.txt
├── core/                      Engine layer — no UI imports, fully testable standalone
│   ├── models.py              DownloadTask / ChunkState dataclasses, DownloadStatus enum
│   ├── state_manager.py       SQLite persistence (resume support)
│   └── engine.py              HTTP probing, multi-threaded chunk downloader,
│                              pause/resume/cancel, chunk assembly
└── ui/                        View layer — only talks to core.engine's public API
    ├── styles.py               Dark theme QSS
    ├── main_window.py          Dashboard: download table, toolbar, refresh loop
    ├── add_download_dialog.py  "+ Add URL" modal with server probing
    └── thread_monitor.py       Live per-chunk thread progress panel
```

**Model-View separation:** `core/` has zero PyQt imports in its logic except the
`QObject`/`pyqtSignal` used purely as a thread-safe event bus (`EngineSignals`).
The engine can be driven and unit-tested headlessly with no GUI at all.

### How a download works

1. **Probe** (`DownloadEngine.probe_url`) — sends a `HEAD` request, and if that's
   inconclusive, a 1-byte ranged `GET` (`Range: bytes=0-0`) to confirm
   `Accept-Ranges: bytes` support and get the real `Content-Length` /
   `Content-Disposition` filename even from servers that mishandle `HEAD`.
2. **Chunking** — if ranges are supported and the size is known, the file is
   split into N equal byte ranges (1 per worker thread, configurable 1–16).
   Otherwise it falls back to a single full-stream connection automatically.
3. **Download** — each chunk runs on its own `ThreadPoolExecutor` worker,
   streaming `requests.get(..., stream=True)` in 64 KB blocks straight to its
   own `partN.tmp` file on disk (never buffered fully in RAM). Progress is
   persisted to SQLite periodically (throttled) so it survives restarts.
4. **Pause / Resume** — pausing clears a `threading.Event`; workers block on
   `event.wait()` mid-stream and resume the instant it's set again, with **no
   re-download of already-fetched bytes**. On resume (including after an app
   restart), the engine always re-checks the *actual on-disk size* of each
   part-file before computing the `Range` header offset — this is the
   authoritative source of truth, since a file write always happens before its
   DB checkpoint, which prevents any misaligned/corrupted resume even if the
   process was killed mid-write.
5. **Assemble** — once every chunk reports complete, the part-files are
   concatenated in order into the destination file, then the temp folder is
   removed.

## Requirements

- Windows 10/11 (also runs on macOS/Linux for development)
- Python 3.10 or newer

## Setup (Windows)

```powershell
# 1. Clone / copy the moody_downloader folder, then open PowerShell inside it
cd moody_downloader

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
python main.py
```

On macOS/Linux, replace step 2's activation with `source venv/bin/activate`.

## Using the app

1. Click **+ Add URL**, paste a direct download link, click **Fetch Info** to
   probe the server (this fills in file size and detects Range support),
   choose a destination folder and thread count, then **Start Download**.
2. The dashboard table shows File Name, Size, a live Progress bar, Speed,
   ETA, and Status for every download.
3. Select a row to see the **Thread Monitor** panel below the table, showing
   each worker thread's byte range and live progress bar.
4. Use the toolbar's **Start / Pause / Resume / Cancel / Remove** buttons on
   the selected row. Closing the app pauses active downloads safely — their
   progress is preserved and they can be resumed next launch (a "Resume"
   button click on a task loaded from a previous session automatically
   restarts its worker threads at the correct byte offsets).

State (including in-progress downloads) is stored in
`moody_downloader.db` (SQLite) next to `main.py`.

## Building a standalone Windows .exe (PyInstaller)

```powershell
# From inside the activated venv, in the moody_downloader folder:
pip install pyinstaller

pyinstaller --noconfirm --onefile --windowed ^
    --name "MoodyDownloader" ^
    --collect-all PyQt6 ^
    main.py
```

- `--onefile` bundles everything into a single `MoodyDownloader.exe`.
- `--windowed` suppresses the console window (GUI app).
- `--collect-all PyQt6` ensures PyQt6's Qt plugins (platform DLLs, styles)
  are included — omitting this is the most common cause of a PyInstaller
  PyQt6 app failing to launch with a "could not find or load the Qt
  platform plugin" error.

The finished executable will be in `dist\MoodyDownloader.exe`. It carries no
external Python dependency — it can be copied to any Windows machine.

## Building the .exe automatically via GitHub Actions (no Windows machine needed)

This repo includes `.github/workflows/build-windows.yml`, which builds
`MoodyDownloader.exe` on a real Windows GitHub-hosted runner every time you
push, or on demand.

**One-time setup:**
1. Create a new (private or public) GitHub repository.
2. From this project's root folder (the one containing both `moody_downloader/`
   and `.github/`), push everything:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```

**Getting the .exe after that:**
1. Go to your repository on GitHub → the **Actions** tab.
2. You'll see a "Build Windows Executable" run (it starts automatically on
   push, and re-runs on every future push to `main`). Click it.
   - To trigger it manually without pushing new code, open the workflow on
     the Actions tab and click **Run workflow**.
3. Once it finishes (usually 2–4 minutes), scroll to the **Artifacts**
   section at the bottom of that run's summary page and download
   **MoodyDownloader-Windows** — it's a zip containing `MoodyDownloader.exe`.

**Optional — attach the .exe to a GitHub Release:** push a version tag
(e.g. `git tag v1.0.0 && git push origin v1.0.0`) and the workflow will also
upload the `.exe` directly onto a GitHub Release for that tag, so anyone can
download it straight from the repo's **Releases** page without digging into
Actions artifacts.

**Optional — smaller executable:** add `--exclude-module PyQt6.QtNetwork
--exclude-module PyQt6.QtQml --exclude-module PyQt6.QtQuick` etc. to strip
unused Qt submodules if you want to trim file size further, though `--onefile`
still needs testing after excluding modules to confirm nothing broke.

## Known Phase 1 limitations (by design, to be extended in later phases)

- No bandwidth throttling / scheduler / browser-extension capture yet.
- No video-specific grabbing (HLS/DASH stream detection) yet — this phase
  covers the generic direct-URL HTTP(S) engine only.
- A hard `kill -9` / power-loss mid-write of the *very last* in-flight 64 KB
  block for a chunk is the only scenario that can lose that one block (a
  clean Pause, Cancel, or window-close is always safe); on relaunch that
  chunk simply resumes from its correct on-disk byte length, no corruption.
- Single global SQLite file; no per-user profiles yet.
