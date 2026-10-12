# UtaSyncNext Launcher Packaging

UtaSyncNext is split into a light launcher and an external engine.

## Target Layout

```text
UtaSyncNext/
  UtaSyncNext.exe          # light launcher
  _internal/               # PyInstaller runtime files
  .engine/                 # external heavy AI engine, not bundled
  models_faster/           # external Faster-Whisper models, not bundled
  .cache/                  # local HF/cache data, not bundled
  fonts/                   # user-imported subtitle fonts, not bundled
  output/                  # user outputs, not bundled
```

## Rules

- Launcher must not bundle Torch, Demucs, Faster-Whisper, CUDA DLLs, model files, `.engine`, or `models_faster`.
- Launcher startup must not import `torch`, `faster_whisper`, `demucs`, `whisper`, or `ctranslate2`.
- Heavy engine packages live under `UtaSyncNext\.engine`.
- Models live under `UtaSyncNext\models_faster`.
- HuggingFace cache is redirected to `UtaSyncNext\.cache\huggingface`.
- User-imported `.ttf/.otf/.ttc/.otc` files live in `UtaSyncNext\fonts` and are
  preserved across launcher rebuilds; they are loaded only for subtitle burn.
- The settings panel supports either one-file import or first-level folder
  import; it never scans or installs the full Windows font registry.
- The application mark is stored in `packaging\assets\utasync.ico` and
  `packaging\assets\utasync.png`; the ICO is embedded in the Windows launcher
  and the PNG is used by the Tk window and child dialogs.
- The lightweight package includes the subtitle reviewer module used by the
  “听轴校对” action, but it does not include the external media, engine, or
  model payloads.

## Build Dependencies

Build dependencies are separate from normal UI runtime dependencies:

```powershell
cd /d E:\traecode\UtaSync\UtaSyncNext
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

## Preflight

```powershell
.venv\Scripts\python.exe tools\check_launcher_boundary.py
```

## Build

```powershell
powershell -ExecutionPolicy Bypass -File tools\package_launcher.ps1 -Clean
```

The build runs from an isolated source copy under `build\launcher_source` so
legacy top-level packages in the parent workspace cannot be collected by
PyInstaller.

## Post-Build Audit

```powershell
.venv\Scripts\python.exe tools\check_packaging_size.py
```

The audit should report no heavy AI packages and no model payloads inside `dist\UtaSyncNext`.

Icon/resource smoke check:

```powershell
.venv\Scripts\python.exe tools\smoke_icon_assets.py
```

## Local Runtime Links

For local smoke tests, link the external engine and model directories into the
packaged launcher without copying large files:

```powershell
powershell -ExecutionPolicy Bypass -File tools\link_dist_runtime.ps1
```

This creates NTFS junctions from `dist\UtaSyncNext\.engine` and
`dist\UtaSyncNext\models_faster` back to the Next-owned runtime folders. It does
not read or link any V3 directory.
