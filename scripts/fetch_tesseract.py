"""scripts/fetch_tesseract.py

Download the portable Tesseract OCR binary (Windows x64) and the required
language data files (eng, rus, ukr) into the vendor/tesseract/ directory so
that the application can be compiled and run without a system-wide Tesseract
installation.

Usage:
    python scripts/fetch_tesseract.py

The script will:
1. Download tessdata files (eng, rus, ukr) from the official tessdata repository.
2. Print instructions for obtaining the Tesseract Windows binary and DLLs.

After running this script, follow the on-screen instructions to place
tesseract.exe and any required DLLs in vendor/tesseract/.
"""

import os
import sys
import urllib.request
import hashlib

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_DIR = os.path.join(REPO_ROOT, "vendor", "tesseract")
TESSDATA_DIR = os.path.join(VENDOR_DIR, "tessdata")

# Tessdata files from the official tessdata repository (pinned to release tag
# 4.0.0 for reproducibility — update the tag if newer trained data is needed).
TESSDATA_BASE_URL = (
    "https://github.com/tesseract-ocr/tessdata/raw/4.0.0"
)

REQUIRED_TRAINEDDATA = {
    "eng.traineddata": f"{TESSDATA_BASE_URL}/eng.traineddata",
    "rus.traineddata": f"{TESSDATA_BASE_URL}/rus.traineddata",
    "ukr.traineddata": f"{TESSDATA_BASE_URL}/ukr.traineddata",
}

# UB Mannheim Tesseract installer page (Windows x64, latest stable)
UB_MANNHEIM_RELEASES = (
    "https://github.com/UB-Mannheim/tesseract/releases"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _download(url: str, dest: str) -> None:
    """Download *url* to *dest*, showing a simple progress indicator."""
    filename = os.path.basename(dest)
    print(f"  Downloading {filename} …", end=" ", flush=True)
    try:
        urllib.request.urlretrieve(url, dest)
        size_kb = os.path.getsize(dest) // 1024
        print(f"OK ({size_kb} KB)")
    except Exception as exc:
        print(f"FAILED\n  Error: {exc}")
        raise


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_tessdata() -> None:
    """Download missing tessdata language files."""
    _ensure_dir(TESSDATA_DIR)
    print("\n[1/2] Downloading Tesseract language data files …")
    all_ok = True
    for name, url in REQUIRED_TRAINEDDATA.items():
        dest = os.path.join(TESSDATA_DIR, name)
        if os.path.isfile(dest):
            print(f"  {name} already present — skipping.")
            continue
        try:
            _download(url, dest)
        except Exception:
            all_ok = False

    if all_ok:
        print("  All language data files are ready.\n")
    else:
        print(
            "\n  Some downloads failed. Check your internet connection and retry.\n"
        )


def print_binary_instructions() -> None:
    """Print step-by-step instructions for placing the Tesseract binary."""
    exe_path = os.path.join(VENDOR_DIR, "tesseract.exe")
    exe_exists = os.path.isfile(exe_path)

    print("[2/2] Tesseract Windows binary")
    if exe_exists:
        print(f"  tesseract.exe found at: {exe_path}")
        print("  Binary already in place — nothing to do.\n")
        return

    print(
        f"""
  tesseract.exe was NOT found at:
    {exe_path}

  Please follow these steps to obtain the portable binary:

  A) Automated install via winget (Windows 10/11):
       winget install UB-Mannheim.TesseractOCR
     Then copy from the install directory (usually
       C:\\Program Files\\Tesseract-OCR\\
     ) the following files / directories into
       {VENDOR_DIR}\\
       • tesseract.exe
       • *.dll  (all DLL files in the Tesseract install directory)
       • any additional subdirectories required by tesseract.exe

  B) Manual download:
     1. Open: {UB_MANNHEIM_RELEASES}
     2. Download the latest  tesseract-ocr-w64-setup-*.exe  installer.
     3. Run the installer (choose a temporary location).
     4. Copy the installed files into  {VENDOR_DIR}\\

  C) Chocolatey:
       choco install tesseract
     Then copy as described in option A.

  After placing the binary you can verify the setup with:
    python scripts/check_tesseract.py
"""
    )


def main() -> None:
    print("=" * 60)
    print("  Tesseract OCR — portable setup helper")
    print("=" * 60)
    fetch_tessdata()
    print_binary_instructions()
    print("Done.  See docs/ocr.md for full documentation.")
    print("=" * 60)


if __name__ == "__main__":
    main()
