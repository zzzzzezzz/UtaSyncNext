# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


NEXT_ROOT = Path(SPECPATH).parents[0]
PROJECT_ROOT = NEXT_ROOT

# The legacy V3 workspace also has top-level packages named ui/utils/etc.
# Keep PyInstaller analysis pinned to UtaSyncNext so the launcher does not
# accidentally collect old workspace modules with the same package names.
project_root_text = str(PROJECT_ROOT)
legacy_parent_text = str(PROJECT_ROOT.parent)
sys.path = [project_root_text] + [
    item
    for item in sys.path
    if item and str(Path(item).resolve()) not in {project_root_text, legacy_parent_text}
]
for cached_name in list(sys.modules):
    if cached_name in {"controllers", "core", "engine", "ui", "utils"} or cached_name.startswith(
        ("controllers.", "core.", "engine.", "ui.", "utils.")
    ):
        del sys.modules[cached_name]

block_cipher = None

excluded_heavy_modules = [
    "ctranslate2",
    "demucs",
    "faster_whisper",
    "nvidia",
    "torch",
    "torchaudio",
    "torchvision",
    "whisper",
]

datas = []
datas += collect_data_files("customtkinter")
datas += [(str(PROJECT_ROOT / "engine" / "demucs_runner.py"), "engine")]

local_hiddenimports = []
for package_name in ("controllers", "core", "engine", "ui", "utils"):
    local_hiddenimports += collect_submodules(package_name)

a = Analysis(
    [str(PROJECT_ROOT / "app.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=local_hiddenimports
    + [
        "tkinterdnd2",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_heavy_modules,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UtaSyncNext",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="UtaSyncNext",
)
