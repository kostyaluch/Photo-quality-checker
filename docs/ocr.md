# OCR Setup Guide — Portable Tesseract

This document explains how to set up a **portable** (no-install) Tesseract OCR
engine for Photo Quality Checker, compile the project with PyInstaller, and
verify that everything works correctly on a machine without a system-wide
Tesseract installation.

---

## 1. Overview

The application uses [pytesseract](https://github.com/madmaze/pytesseract) to
detect watermark text, Russian-language content, and Rozetka logos in product
images.  `pytesseract` is a thin wrapper that calls the external `tesseract`
binary; both the binary and the language data (`.traineddata` files) must be
available at runtime.

Rather than requiring users to install Tesseract system-wide, the binary and
language data are kept in `vendor/tesseract/` **alongside** the compiled
application. The code automatically detects this location via
`resource_path()` in `image_metrics.py`.

---

## 2. Directory structure

After setup, `vendor/tesseract/` should look like this:

```
vendor/
└── tesseract/
    ├── tesseract.exe          ← Tesseract binary (Windows x64)
    ├── *.dll                  ← all DLLs shipped with Tesseract
    ├── (any other files from the portable installation)
    └── tessdata/
        ├── eng.traineddata    ← English
        ├── rus.traineddata    ← Russian
        └── ukr.traineddata    ← Ukrainian
```

> **Note:** The `vendor/tesseract/` directory is listed in `.gitignore` to
> avoid committing large binary files.  The directory structure is preserved in
> the repository via `.gitkeep` placeholders.

---

## 3. Step-by-step setup

### Step 1 — Download tessdata automatically

Run the helper script from the repository root:

```bash
python scripts/fetch_tesseract.py
```

This downloads `eng.traineddata`, `rus.traineddata`, and `ukr.traineddata`
from the official [tesseract-ocr/tessdata](https://github.com/tesseract-ocr/tessdata)
repository into `vendor/tesseract/tessdata/`.

### Step 2 — Obtain the Tesseract Windows binary

The Tesseract binary cannot be downloaded automatically due to its size and
licensing requirements.  Choose one of the following methods:

**Option A — winget (Windows 10/11, recommended):**

```powershell
winget install UB-Mannheim.TesseractOCR
```

After installation, copy all files from the install directory (typically
`C:\Program Files\Tesseract-OCR\`) into `vendor\tesseract\`:

```powershell
Copy-Item "C:\Program Files\Tesseract-OCR\*" -Destination "vendor\tesseract\" -Recurse -Force
```

**Option B — Manual download:**

1. Visit <https://github.com/UB-Mannheim/tesseract/releases>
2. Download the latest `tesseract-ocr-w64-setup-*.exe` installer.
3. Install to a temporary location and copy the resulting files to
   `vendor\tesseract\`.

**Option C — Chocolatey:**

```powershell
choco install tesseract
Copy-Item "C:\Program Files\Tesseract-OCR\*" -Destination "vendor\tesseract\" -Recurse -Force
```

### Step 3 — Verify the setup

```bash
python scripts/check_tesseract.py
```

Expected output (all checks green):

```
============================================================
  Tesseract OCR — portable installation check
============================================================
[ OK ] tesseract.exe found:  ...\vendor\tesseract\tesseract.exe
[ OK ] eng.traineddata
[ OK ] rus.traineddata
[ OK ] ukr.traineddata

  Tesseract version output:
    tesseract 5.x.x ...

============================================================
  Result: ALL CHECKS PASSED — Tesseract is ready.
============================================================
```

---

## 4. Adding extra languages

Place any additional `.traineddata` file into `vendor/tesseract/tessdata/`.
Files are available from:

- **Best quality:** <https://github.com/tesseract-ocr/tessdata>
- **Fast (smaller):** <https://github.com/tesseract-ocr/tessdata_fast>

Then update the `lang` parameter in `image_metrics.py → analyze_text_content()`
if necessary (currently `lang="rus+ukr+eng"`).

---

## 5. Building the executable with PyInstaller

### Prerequisites

```bash
pip install pyinstaller
```

### Build (onedir — recommended)

```bash
pyinstaller photo_quality_checker.spec
```

The finished application is placed in `dist/PhotoQualityChecker/`. The
`vendor/tesseract/` folder is automatically bundled inside the distribution
directory.

### Distribute

Copy the **entire** `dist/PhotoQualityChecker/` folder to the target PC.
No Python, no Tesseract, no additional installation needed.

### Build verification on the target machine

```
dist\PhotoQualityChecker\PhotoQualityChecker.exe
```

The application should start normally and perform OCR without errors.

---

## 6. How `resource_path()` works

`image_metrics.py` contains a helper function:

```python
def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)
```

| Runtime environment | `base_path` value |
|---------------------|-------------------|
| Plain Python script | directory of `image_metrics.py` |
| PyInstaller onefile | temporary extraction directory (`sys._MEIPASS`) |
| PyInstaller onedir  | distribution directory (`sys._MEIPASS`) |

The Tesseract binary and tessdata directory are then located at:

```python
_TESSERACT_EXE = resource_path(os.path.join("vendor", "tesseract", "tesseract.exe"))
_TESSDATA_DIR  = resource_path(os.path.join("vendor", "tesseract", "tessdata"))
```

If `tesseract.exe` is found, `pytesseract` is configured automatically:

```python
pytesseract.pytesseract.tesseract_cmd = _TESSERACT_EXE
os.environ["TESSDATA_PREFIX"] = _TESSDATA_DIR
```

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `TesseractNotFoundError` | `tesseract.exe` missing from `vendor/tesseract/` | Follow Section 3, Step 2 |
| `cannot find traineddata` | `tessdata/` missing or wrong `TESSDATA_PREFIX` | Run `scripts/fetch_tesseract.py` |
| `DLL load failed` | Required DLLs not copied | Copy **all** files from the Tesseract install directory |
| OCR returns empty text | Wrong language codes | Verify `.traineddata` files exist; check `lang=` parameter |
