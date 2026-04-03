# Photo-quality-checker
Photo Quality Checker — це потужний Python-інструмент для комплексної автоматизованої оцінки якості великих масивів фотографій. Програма розроблена спеціально для e-commerce та контент-менеджерів, яким потрібно швидко перевіряти тисячі зображень на відповідність стандартам маркетплейсів.

## Встановлення залежностей

```bash
pip install -r requirements.txt
```

## Налаштування Tesseract OCR (portable)

Для роботи функцій розпізнавання тексту (водяні знаки, кирилиця, логотипи)
потрібен **portable** Tesseract, розміщений у `vendor/tesseract/`.

**Швидкий старт:**

```bash
# 1. Завантажити мовні дані автоматично
python scripts/fetch_tesseract.py

# 2. Перевірити встановлення
python scripts/check_tesseract.py
```

Докладні інструкції (включно зі скачуванням бінарника та компіляцією у .exe)
дивись у [docs/ocr.md](docs/ocr.md).

## Збірка у .exe (PyInstaller)

```bash
pip install pyinstaller
pyinstaller photo_quality_checker.spec
```

Готова програма з'явиться у `dist/PhotoQualityChecker/`. Весь вміст цієї
папки (включно з `vendor/tesseract/`) можна скопіювати на інший комп'ютер
без встановлення Python або Tesseract.

