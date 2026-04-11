"""
Модульні тести для check_first_photo_bg() з image_metrics.py.

Запуск:
    python -m pytest tests/test_image_metrics.py -v
    # або без pytest:
    python tests/test_image_metrics.py
"""

import sys
import os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image
from image_metrics import check_first_photo_bg


def _make_pil(rgb_array: np.ndarray) -> Image.Image:
    """Конвертує numpy-масив (H, W, 3) uint8 у PIL Image."""
    return Image.fromarray(rgb_array.astype(np.uint8), mode="RGB")


def _solid(color_rgb, size=(200, 200)) -> Image.Image:
    """Створює суцільне зображення заданого кольору."""
    arr = np.full((*size, 3), color_rgb, dtype=np.uint8)
    return _make_pil(arr)


def _white_with_bottom_shadow(shade: int = 210, size=(200, 200)) -> Image.Image:
    """Біле зображення з темною нижньою смугою (імітація тіні)."""
    arr = np.full((*size, 3), 255, dtype=np.uint8)
    shadow_h = max(int(size[0] * 0.12), 5)
    arr[-shadow_h:, :] = shade          # нижня смуга потемнена
    return _make_pil(arr)


# ---------------------------------------------------------------------------
# Тест 1: суцільно білий фон — повинен проходити при будь-якому допуску
# ---------------------------------------------------------------------------
def test_pure_white_passes():
    img = _solid((255, 255, 255))
    problem, reason = check_first_photo_bg(img, shadow_tolerance=0)
    assert not problem, f"Чисто білий фон хибно провалився: {reason}"
    problem, reason = check_first_photo_bg(img, shadow_tolerance=100)
    assert not problem, f"Чисто білий фон провалився при tolerance=100: {reason}"


# ---------------------------------------------------------------------------
# Тест 2: кольоровий (lifestyle) фон — повинен провалюватися як «Фон не білий»
# ---------------------------------------------------------------------------
def test_colored_bg_fails():
    img = _solid((120, 80, 50))          # коричневий / інтер'єр
    problem, reason = check_first_photo_bg(img, shadow_tolerance=100)
    assert problem, "Кольоровий фон має бути відхилений"
    assert "Фон не білий" in reason, f"Очікувалось 'Фон не білий', отримано: {reason}"


# ---------------------------------------------------------------------------
# Тест 3: сірий фон (#808080) — повинен провалюватися як «Фон не білий»
# ---------------------------------------------------------------------------
def test_gray_bg_fails():
    img = _solid((128, 128, 128))
    problem, reason = check_first_photo_bg(img, shadow_tolerance=100)
    assert problem, "Сірий фон має бути відхилений"
    assert "Фон не білий" in reason, f"Очікувалось 'Фон не білий', отримано: {reason}"


# ---------------------------------------------------------------------------
# Тест 4: тінь знизу при суворому допуску (tolerance=0) — має провалитися
# ---------------------------------------------------------------------------
def test_bottom_shadow_strict_fails():
    img = _white_with_bottom_shadow(shade=190)
    problem, reason = check_first_photo_bg(img, shadow_tolerance=0)
    assert problem, f"Тінь знизу при tolerance=0 має бути виявлена, але: {reason}"


# ---------------------------------------------------------------------------
# Тест 5: легка тінь при м'якому допуску (tolerance=100) — може проходити
# ---------------------------------------------------------------------------
def test_light_shadow_lenient_passes():
    img = _white_with_bottom_shadow(shade=245)   # ледь помітна тінь
    problem, reason = check_first_photo_bg(img, shadow_tolerance=100)
    assert not problem, f"Легка тінь при tolerance=100 не має відхилятись: {reason}"


# ---------------------------------------------------------------------------
# Тест 6: зображення з прозорим каналом (RGBA) обробляється без помилок
# ---------------------------------------------------------------------------
def test_rgba_image_no_crash():
    arr = np.full((100, 100, 4), 255, dtype=np.uint8)
    img = Image.fromarray(arr, mode="RGBA")
    problem, reason = check_first_photo_bg(img, shadow_tolerance=50)
    # Не повинно кидати виняток; результат — коректний bool
    assert isinstance(problem, bool)


# ---------------------------------------------------------------------------
# Запуск без pytest
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        test_pure_white_passes,
        test_colored_bg_fails,
        test_gray_bg_fails,
        test_bottom_shadow_strict_fails,
        test_light_shadow_lenient_passes,
        test_rgba_image_no_crash,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} тестів пройшли.")
