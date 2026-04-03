"""scripts/check_tesseract.py

Self-test that verifies the portable Tesseract OCR binary is in place and
reachable by the application.

Usage:
    python scripts/check_tesseract.py

Exit codes:
    0 — Tesseract found and operational
    1 — Tesseract binary or language data missing / error
"""

import os
import sys
import subprocess

# Resolve paths the same way image_metrics.py does so this script exercises
# exactly the same logic.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_DIR = os.path.join(REPO_ROOT, "vendor", "tesseract")
TESSERACT_EXE = os.path.join(VENDOR_DIR, "tesseract.exe")
TESSDATA_DIR = os.path.join(VENDOR_DIR, "tessdata")

REQUIRED_TRAINEDDATA = ["eng.traineddata", "rus.traineddata", "ukr.traineddata"]


def _check_binary() -> bool:
    if not os.path.isfile(TESSERACT_EXE):
        print(f"[FAIL] tesseract.exe not found at:\n       {TESSERACT_EXE}")
        print(
            "       Run  python scripts/fetch_tesseract.py  and follow the"
            " instructions to place the binary."
        )
        return False
    print(f"[ OK ] tesseract.exe found:  {TESSERACT_EXE}")
    return True


def _check_tessdata() -> bool:
    if not os.path.isdir(TESSDATA_DIR):
        print(f"[FAIL] tessdata directory not found at:\n       {TESSDATA_DIR}")
        print("       Run  python scripts/fetch_tesseract.py  to download language data.")
        return False

    all_ok = True
    for name in REQUIRED_TRAINEDDATA:
        path = os.path.join(TESSDATA_DIR, name)
        if os.path.isfile(path):
            print(f"[ OK ] {name}")
        else:
            print(f"[FAIL] {name} missing from {TESSDATA_DIR}")
            all_ok = False

    if not all_ok:
        print("       Run  python scripts/fetch_tesseract.py  to download missing language data.")

    return all_ok


def _check_version() -> bool:
    env = os.environ.copy()
    env["TESSDATA_PREFIX"] = TESSDATA_DIR
    try:
        result = subprocess.run(
            [TESSERACT_EXE, "--version"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        version_output = (result.stdout or result.stderr).strip()
        first_line = version_output.splitlines()[0] if version_output else "(no output)"
        print(f"\n  Tesseract version output:\n    {first_line}")
        return True
    except FileNotFoundError:
        print(f"[FAIL] Could not execute:  {TESSERACT_EXE}")
        return False
    except subprocess.TimeoutExpired:
        print("[FAIL] Tesseract timed out while querying version.")
        return False
    except Exception as exc:
        print(f"[FAIL] Unexpected error running Tesseract: {exc}")
        return False


def main() -> int:
    print("=" * 60)
    print("  Tesseract OCR — portable installation check")
    print("=" * 60)

    binary_ok = _check_binary()
    tessdata_ok = _check_tessdata()

    version_ok = False
    if binary_ok:
        version_ok = _check_version()

    print("\n" + "=" * 60)
    if binary_ok and tessdata_ok and version_ok:
        print("  Result: ALL CHECKS PASSED — Tesseract is ready.")
        print("=" * 60)
        return 0
    else:
        print("  Result: SOME CHECKS FAILED — see messages above.")
        print("  See docs/ocr.md for setup instructions.")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
