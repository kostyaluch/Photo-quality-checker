# -*- mode: python ; coding: utf-8 -*-
"""photo_quality_checker.spec

PyInstaller build specification for Photo Quality Checker.

Build command (run from the repository root):
    pyinstaller photo_quality_checker.spec

The resulting application is placed in  dist/PhotoQualityChecker/ .
Copy or move the entire  dist/PhotoQualityChecker/  folder to any Windows PC —
no system-wide Python or Tesseract installation is required.

Prerequisites:
    1. pip install pyinstaller
    2. python scripts/fetch_tesseract.py   (downloads tessdata)
    3. Place tesseract.exe + DLLs in vendor/tesseract/  (see docs/ocr.md)
"""

import os
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT, Tree

block_cipher = None

REPO_ROOT = os.path.abspath(os.path.dirname(SPEC))  # noqa: F821  # SPEC is injected by PyInstaller at build time — see PyInstaller docs §spec-files
VENDOR_TESSERACT = os.path.join(REPO_ROOT, "vendor", "tesseract")

# ---------------------------------------------------------------------------
# Collect all files from vendor/tesseract/ recursively.
# This includes tesseract.exe, all required DLLs and the tessdata/ directory.
# ---------------------------------------------------------------------------
tesseract_tree = Tree(
    VENDOR_TESSERACT,
    prefix=os.path.join("vendor", "tesseract"),
    excludes=[".gitkeep", ".gitignore"],
)

a = Analysis(
    [os.path.join(REPO_ROOT, "main_app.py")],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[
        # Watermark template images
        (os.path.join(REPO_ROOT, "watermark_templates"), "watermark_templates"),
        # Default configuration
        (os.path.join(REPO_ROOT, "config_photo_quality.json"), "."),
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

# Append the Tesseract tree to the collected data
a.datas += tesseract_tree

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
    name="PhotoQualityChecker",
)
