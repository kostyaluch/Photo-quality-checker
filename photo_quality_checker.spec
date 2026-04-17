# -*- mode: python ; coding: utf-8 -*-
"""photo_quality_checker.spec

PyInstaller build specification for Photo Quality Checker.

Build command (run from the repository root):
    pyinstaller photo_quality_checker.spec

The resulting application is placed in  dist/PhotoQualityChecker/ .
The layout of that folder is intentionally clean:

    dist/PhotoQualityChecker/
    ├── PhotoQualityChecker.exe   ← the application
    └── _internal/                ← Python runtime, DLLs, bundled resources
        ├── config_photo_quality.json
        ├── PQC_logo.ico
        ├── watermark_templates/
        └── vendor/
            └── tesseract/
                ├── tesseract.exe
                ├── *.dll
                └── tessdata/
                    ├── eng.traineddata
                    ├── rus.traineddata
                    └── ukr.traineddata

After the first run the application also creates next to the exe:
    ├── config_photo_quality.json   ← user-saved settings (overrides bundled default)
    └── .photo_cache/               ← downloaded-image cache

Copy or move the entire  dist/PhotoQualityChecker/  folder to any Windows PC —
no system-wide Python or Tesseract installation is required.

Prerequisites:
    1. pip install pyinstaller
    2. python scripts/fetch_tesseract.py   (downloads tessdata)
    3. Place tesseract.exe + DLLs in vendor/tesseract/  (see docs/ocr.md)
"""

import glob as _glob
import os
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

block_cipher = None

REPO_ROOT = os.path.abspath(os.path.dirname(SPEC))  # noqa: F821  # SPEC is injected by PyInstaller at build time — see PyInstaller docs §spec-files
VENDOR_TESSERACT = os.path.join(REPO_ROOT, "vendor", "tesseract")

# ---------------------------------------------------------------------------
# Collect Tesseract files selectively to keep the bundle lean:
#   • tesseract.exe + all DLLs from the root of vendor/tesseract/
#   • only the three language models actually used by the application
#
# A recursive Tree() would pull in every file found in vendor/tesseract/
# (including unused language packs that can easily weigh several GB).
# ---------------------------------------------------------------------------
_DEST_TESS_ROOT = os.path.join("vendor", "tesseract")
_DEST_TESSDATA = os.path.join("vendor", "tesseract", "tessdata")
_NEEDED_LANGS = {"eng.traineddata", "rus.traineddata", "ukr.traineddata"}
_SKIP_EXTENSIONS = {".gitignore", ".gitkeep", ".md", ".txt"}

# tesseract.exe and required DLLs from the root of vendor/tesseract/
tesseract_root_datas = [
    (src, _DEST_TESS_ROOT)
    for src in _glob.glob(os.path.join(VENDOR_TESSERACT, "*"))
    if os.path.isfile(src)
    and os.path.splitext(src)[1].lower() not in _SKIP_EXTENSIONS
]

# Only the three language models needed at runtime
tesseract_lang_datas = [
    (src, _DEST_TESSDATA)
    for name in _NEEDED_LANGS
    for src in [os.path.join(VENDOR_TESSERACT, "tessdata", name)]
    if os.path.isfile(src)
]

tesseract_datas = tesseract_root_datas + tesseract_lang_datas

a = Analysis(
    [os.path.join(REPO_ROOT, "main_app.py")],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[
        # Watermark template images
        (os.path.join(REPO_ROOT, "watermark_templates"), "watermark_templates"),
        # Default configuration (read-only bundled copy; user-editable copy lives next to the exe)
        (os.path.join(REPO_ROOT, "config_photo_quality.json"), "."),
        # Application icon
        (os.path.join(REPO_ROOT, "PQC_logo.ico"), "."),
        # Portable Tesseract OCR engine (filtered — only required files)
        *tesseract_datas,
    ],
    hiddenimports=[
        "PIL._tkinter_finder",
        "pytesseract",
        "cv2",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoQualityChecker",
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
    icon=os.path.join(REPO_ROOT, "PQC_logo.ico"),
    # Put all Python internals and bundled resources into _internal/ so that
    # only PhotoQualityChecker.exe is visible at the top of the dist folder.
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PhotoQualityChecker",
)
