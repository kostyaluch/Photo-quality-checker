# image_metrics.py
import os
import re
import sys
import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps


def resource_path(relative_path: str) -> str:
    """Return the absolute path to a bundled resource.

    Works both when running as a plain Python script and when packaged with
    PyInstaller (``--onefile`` or ``--onedir``).  PyInstaller sets the
    ``sys._MEIPASS`` attribute to the temporary extraction directory, so we
    prefer that when available; otherwise we fall back to the directory that
    contains this source file.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)


# Шлях до папки з шаблонами
BASE_DIR = os.path.dirname(__file__) if '__file__' in globals() else os.getcwd()
TEMPLATES_DIR = os.path.join(BASE_DIR, "watermark_templates")

# ---------------------------------------------------------------------------
# Portable Tesseract OCR paths
# The binary and language data are expected under vendor/tesseract/ relative
# to this file (or to the PyInstaller extraction root).
# ---------------------------------------------------------------------------
_TESSERACT_EXE = resource_path(os.path.join("vendor", "tesseract", "tesseract.exe"))
_TESSDATA_DIR = resource_path(os.path.join("vendor", "tesseract", "tessdata"))

if os.path.isfile(_TESSERACT_EXE):
    pytesseract.pytesseract.tesseract_cmd = _TESSERACT_EXE
    os.environ["TESSDATA_PREFIX"] = _TESSDATA_DIR

# 1. Текстові маркери водяних знаків
WATERMARK_KEYWORDS = [
    "shutterstock", "depositphotos", "istock", "dreamstime", "alamy", 
    "adobe stock", "copyright", "gettyimages", "123rf", "pond5", "fotolia"
]

# 2. Варіанти написання "Rozetka"
ROZETKA_VARIANTS = [
    "rozetka", "rozelka", "rozorka", "rosetka", "rossetka",
    "pozetka", "bozetka", "ozetka", "rozetk", "razetka",
    "rozerka", "roze1ka", "ro2etka", "r0zetka",
    "розетка", "восетка", "бозетка", "позетка",
    "pозетка", "rозетка", "rozетка", "rozeтка",
    "cynepmapket", "supermarket", "супермаркет",
    "internet", "interne", "nternet", "интернет", "інтернет"
]

CACHED_TEMPLATES = []

def load_templates():
    global CACHED_TEMPLATES
    if CACHED_TEMPLATES: return CACHED_TEMPLATES
    
    if not os.path.exists(TEMPLATES_DIR):
        try: os.makedirs(TEMPLATES_DIR)
        except: pass
        return []

    templates = []
    valid_ext = ('.jpg', '.jpeg', '.png', '.bmp')
    
    try:
        for f in os.listdir(TEMPLATES_DIR):
            if f.lower().endswith(valid_ext):
                path = os.path.join(TEMPLATES_DIR, f)
                try:
                    stream = np.fromfile(path, dtype=np.uint8)
                    templ = cv2.imdecode(stream, cv2.IMREAD_GRAYSCALE)
                    if templ is not None:
                        templates.append((f, templ))
                except: pass
    except: pass
    
    CACHED_TEMPLATES = templates
    return CACHED_TEMPLATES

# ----------------------------- Обробка PIL + Прозорість -----------------------------

def detect_transparency_in_bytes(data):
    """
    Перевіряє байти на наявність прозорості ДО конвертації в RGB.
    Використовує контекстний менеджер для коректного закриття Image.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            # 1. RGBA (Alpha channel)
            if img.mode == 'RGBA':
                extrema = img.getextrema()
                if extrema[3][0] < 255:
                    return True, "Прозорість (Alpha канал)"
            # 2. Paletted (GIF/PNG-8)
            elif img.mode == 'P':
                if 'transparency' in img.info:
                    return True, "Прозорість (Index)"
        return False, None
    except Exception:
        return False, None

def pil_from_bytes(data):
    """Конвертує байти в PIL-зображення у форматі RGB.

    Повертає зображення або None при помилці.
    Відповідальність за закриття зображення лежить на викликачі.
    """
    try:
        with Image.open(BytesIO(data)) as raw:
            if raw.mode in ('RGBA', 'LA') or (raw.mode == 'P' and 'transparency' in raw.info):
                # Компонуємо прозоре зображення на білому тлі
                rgba = raw.convert('RGBA')
                white_bg = Image.new("RGB", raw.size, (255, 255, 255))
                white_bg.paste(rgba, (0, 0), rgba)
                rgba.close()
                return white_bg
            else:
                # convert() повертає нову незалежну копію
                return raw.convert("RGB")
    except Exception:
        return None

# ----------------------------- Метрики (UPDATED) -----------------------------

def is_low_contrast_image(pil_image, brightness_threshold=235, std_threshold=30):
    """Повертає True, якщо зображення дуже рівномірне і яскраве.

    Такі зображення типові для прозорих/напівпрозорих товарів (наприклад, прозорі
    чохли на білому тлі): весь кадр майже білий, текстура майже відсутня.
    Метрика різкості Лапласіана для них природно близька до нуля — не через розмитість,
    а через відсутність контрасту — тому таким фото не слід виставляти оцінку
    «Дуже розмите».
    """
    try:
        arr = np.array(pil_image)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        mean_b = float(np.mean(gray))
        std_b  = float(np.std(gray))
        return mean_b >= brightness_threshold and std_b <= std_threshold
    except Exception:
        return False


def compute_sharpness_pil(pil_image):
    """
    Grid Strategy (Зональна перевірка).
    """
    try:
        arr = np.array(pil_image)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        
        global_lap = cv2.Laplacian(gray, cv2.CV_64F)
        global_var = float(global_lap.var())
        
        if global_var > 100.0:
            return global_var
            
        h, w = gray.shape
        h_step = h // 3
        w_step = w // 3
        
        if h_step < 50 or w_step < 50:
            return global_var

        max_sector_sharpness = 0.0
        
        for i in range(3):
            for j in range(3):
                y1, y2 = i*h_step, (i+1)*h_step
                x1, x2 = j*w_step, (j+1)*w_step
                
                sector = gray[y1:y2, x1:x2]
                sector_lap = cv2.Laplacian(sector, cv2.CV_64F)
                val = float(sector_lap.var())
                if val > max_sector_sharpness:
                    max_sector_sharpness = val
        
        return max(global_var, max_sector_sharpness * 0.85)

    except Exception:
        return 0.0

def detect_white_borders(pil_image, border_ratio=0.1):
    """
    [PHOTOSHOP RAW MODE] Виявляє поля за методом "Top Left Pixel".
    Максимальна чутливість до слабких візерунків.
    Мінімальне розмиття, щоб не стерти тонкі лінії.
    """
    try:
        # Конвертуємо в numpy array (OpenCV uses BGR)
        img = np.array(pil_image)
        # Обробка каналів
        if len(img.shape) == 2:  # Grayscale
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:  # RGBA
            # Якщо є прозорість, перевіряємо альфа-канал
            alpha = img[:, :, 3]
            # Якщо є хоч один непрозорий піксель не по краях - це контент
            # Але для універсальності конвертуємо в білий фон
            bg = np.full_like(img[:, :, :3], 255) # Білий фон
            alpha_factor = alpha[:, :, np.newaxis] / 255.0
            img = (img[:, :, :3] * alpha_factor + bg * (1 - alpha_factor)).astype(np.uint8)
        elif len(img.shape) == 3 and img.shape[2] == 3:
             img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        h, w = img.shape[:2]
        total_area = h * w
        
        # 1. Беремо зразок фону (Верхній Лівий піксель)
        bg_color = img[0, 0]
        
        # 2. Обчислюємо різницю (Raw Difference)
        # Не робимо сильного розмиття (blur), бо воно знищує слабкий патерн!
        diff = cv2.absdiff(img, bg_color)
        
        # 3. Поріг чутливості (Tolerance)
        # 4 - це дуже чутливо. (Різниця між 255 і 251 вже буде контентом)
        # Це дозволяє зловити найсвітліший сірий візерунок.
        TOLERANCE = 4
        mask = np.max(diff, axis=2) > TOLERANCE
        mask_uint8 = mask.astype(np.uint8) * 255

        # 4. Обережна чистка шуму JPEG
        # Використовуємо дуже маленький кернел (2x2), щоб не вбити тонкі лінії візерунка
        kernel = np.ones((2, 2), np.uint8)
        mask_cleaned = cv2.morphologyEx(mask_uint8, cv2.MORPH_OPEN, kernel)

        # 5. Знаходимо контент
        coords = cv2.findNonZero(mask_cleaned)

        # Якщо абсолютно все збігається з лівим верхнім кутом
        if coords is None:
            return True, "Пусте/Суцільний фон"

        # 6. Рахуємо Bounding Box
        x, y, w_rect, h_rect = cv2.boundingRect(coords)
        content_area = w_rect * h_rect
        
        # Ігноруємо мікро-сміття (менше 0.1% площі)
        if content_area < (total_area * 0.001):
             return True, "Лише шум"

        # real_ratio = частка полів
        real_ratio = (total_area - content_area) / total_area
        
        debug_str = f"Поля {real_ratio * 100:.1f}%"
        
        if real_ratio > border_ratio:
            return True, debug_str
            
        return False, "OK"

    except Exception as e:
        return False, f"Err borders: {str(e)}"

def detect_1px_border(pil_image, black_threshold=30, std_threshold=8.0, min_contrast_difference=30):
    try:
        img = np.array(pil_image)
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            gray = img

        h, w = gray.shape
        if h < 10 or w < 10: return False, None

        def check_edge(edge_strip, inner_strip_1, inner_strip_2):
            mean_edge = np.mean(edge_strip)
            if mean_edge > black_threshold: return False
            
            std_edge = np.std(edge_strip)
            if std_edge > std_threshold: return False 
            
            mean_inner_1 = np.mean(inner_strip_1)
            mean_inner_2 = np.mean(inner_strip_2)
            
            if (mean_inner_1 - mean_edge) > min_contrast_difference and (mean_inner_2 - mean_edge) > min_contrast_difference:
                return True
            return False

        top = check_edge(gray[0:1, :], gray[2:3, :], gray[5:10, :])
        bot = check_edge(gray[h-1:h, :], gray[h-3:h-2, :], gray[h-10:h-5, :])
        left = check_edge(gray[:, 0:1], gray[:, 2:3], gray[:, 5:10])
        right = check_edge(gray[:, w-1:w], gray[:, w-3:w-2], gray[:, w-10:w-5])

        detected = []
        if top: detected.append("Верх")
        if bot: detected.append("Низ")
        if left: detected.append("Ліво")
        if right: detected.append("Право")

        if detected:
            return True, f"Тонка рамка: {', '.join(detected)}"
        return False, None

    except Exception as e:
        return False, f"Err border: {e}"

def check_first_photo_bg(pil_image, shadow_tolerance=50):
    """
    Перевіряє перше фото товару на відповідність стандартам Rozetka:
      1. Фон має бути майже білим (#FFFFFF) по всьому периметру (верх/низ/ліво/право).
      2. Тіні оцінюються по нижній смузі і периметру; допустимий рівень
         контролюється параметром shadow_tolerance (0 = суворо, 100 = м'яко).

    Алгоритм:
      - Аналізує 4 периметральні смуги (по ~8% з кожного боку).
      - "Білий фон": середня яскравість HSV-V >= 205 і середня насиченість HSV-S <= 25.
        Якщо ≥ 2 смуги не проходять цей критерій → "Фон не білий".
      - "Тіні": std_dev яскравості в нижній смузі та периметрі порівнюється з
        порогом, що лінійно залежить від shadow_tolerance:
          max_std = 2.0 + tolerance * 0.7  →  2.0 при 0,  72.0 при 100.
        Якщо нижня смуга або середнє по периметру перевищує поріг → "Тінь".

    Returns: (has_problem: bool, reason: str)
    """
    try:
        img = np.array(pil_image)
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        h, w = img.shape[:2]

        strip_h = max(int(h * 0.08), 5)
        strip_w = max(int(w * 0.08), 5)

        strips = {
            "top":    img[0:strip_h, :],
            "bottom": img[h - strip_h:, :],
            "left":   img[:, 0:strip_w],
            "right":  img[:, w - strip_w:],
        }

        def _strip_stats(strip_bgr):
            hsv = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
            s_ch = hsv[:, :, 1]
            v_ch = hsv[:, :, 2]
            return {
                "mean_s": float(np.mean(s_ch)),
                "mean_v": float(np.mean(v_ch)),
                "std_v":  float(np.std(v_ch)),
                "v_ch":   v_ch,
            }

        stats = {name: _strip_stats(strip) for name, strip in strips.items()}

        # --- КРОК 1: Перевірка білого фону ---
        # Білий піксель: V >= 205, S <= 25 (HSV-шкала 0-255 для OpenCV)
        WHITE_V_MIN = 205
        WHITE_S_MAX = 25

        non_white = [
            f"{name}(V={st['mean_v']:.0f},S={st['mean_s']:.0f})"
            for name, st in stats.items()
            if st["mean_v"] < WHITE_V_MIN or st["mean_s"] > WHITE_S_MAX
        ]

        if len(non_white) >= 2:
            # Before flagging, verify there is actually a visible white background.
            # When a product is maximally cropped it fills the entire frame, so the
            # perimeter strips contain product edges rather than background.  In that
            # case the CENTER of the image is also non-white (same product colour),
            # meaning there is no background to evaluate → skip the flag.
            cy, cx = h // 2, w // 2
            center_h = max(int(h * 0.15), 10)
            center_w = max(int(w * 0.15), 10)
            center_crop = img[cy - center_h:cy + center_h, cx - center_w:cx + center_w]
            center_stats = _strip_stats(center_crop)
            center_is_non_white = (
                center_stats["mean_v"] < WHITE_V_MIN
                or center_stats["mean_s"] > WHITE_S_MAX
            )
            if not center_is_non_white:
                if len(non_white) == 4:
                    # All 4 perimeter strips non-white + white centre: this is a
                    # transparent or maximally-cropped product whose own border fills
                    # the frame edge-to-edge.  There is no background to evaluate.
                    pass
                else:
                    # Some edges non-white while centre is white → real non-white bg
                    return True, f"Фон не білий ({', '.join(non_white)})"
            # Centre is also non-white → product fills the entire frame, skip check

        # --- КРОК 2: Перевірка тіней ---
        # Переводимо shadow_tolerance (0..100) у максимально допустимий std_dev:
        #   0   → 2.0  (майже ідеально рівний білий, дуже суворо)
        #   50  → 37.0 (помірна допустимість)
        #   100 → 72.0 (дозволяє помітні тіні)
        shadow_tolerance = max(0, min(100, int(shadow_tolerance)))
        max_shadow_std = 2.0 + shadow_tolerance * 0.7

        # Thresholds used to distinguish a hard product edge from a soft shadow:
        # if a strip contains BOTH very dark pixels (product) and very bright pixels
        # (white background), the high std_v is caused by the product boundary, not a
        # shadow gradient.
        PRODUCT_EDGE_DARK_THRESHOLD = 100
        PRODUCT_EDGE_BRIGHT_THRESHOLD = 220

        def _strip_has_product_edge(strip_v_ch):
            """True if the pre-computed V channel contains both very dark (product)
            and very bright (background) pixels — indicating a hard edge, not a shadow."""
            return bool(
                np.any(strip_v_ch < PRODUCT_EDGE_DARK_THRESHOLD)
                and np.any(strip_v_ch > PRODUCT_EDGE_BRIGHT_THRESHOLD)
            )

        bottom_std = stats["bottom"]["std_v"]
        avg_perimeter_std = float(np.mean([st["std_v"] for st in stats.values()]))

        if bottom_std > max_shadow_std and not _strip_has_product_edge(stats["bottom"]["v_ch"]):
            return True, (
                f"Тінь внизу (std={bottom_std:.1f}, поріг={max_shadow_std:.1f})"
            )

        # Для перевірки по всьому периметру використовується поріг на 20% вищий,
        # ніж для нижньої смуги: нижня зона є найпріоритетнішою (тіні під товаром),
        # тому вимагає суворішого контролю; загальний периметр може мати трохи
        # більшу природну варіацію (наприклад, краї рамки з обох боків).
        PERIMETER_MULTIPLIER = 1.2
        strips_with_product_edge = sum(
            1 for st in stats.values() if _strip_has_product_edge(st["v_ch"])
        )
        if avg_perimeter_std > max_shadow_std * PERIMETER_MULTIPLIER and strips_with_product_edge < 2:
            return True, (
                f"Тінь на периметрі (avg_std={avg_perimeter_std:.1f}, "
                f"поріг={max_shadow_std * PERIMETER_MULTIPLIER:.1f})"
            )

        return False, "OK"

    except Exception as e:
        return False, str(e)


# Зворотна сумісність: стара функція залишається доступною
def detect_shadows_on_bg(pil_image, shadow_std_dev_threshold=15.0):
    """
    [ЗАСТАРІЛО] Виявляє брудний фон, аналізуючи верхні 10% кадру.
    Використовуйте check_first_photo_bg() для нових проектів.
    """
    try:
        img = np.array(pil_image)
        if len(img.shape) == 2: img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4: img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif img.shape[2] == 3: img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        h, w = img.shape[:2]

        strip_h = int(h * 0.10)
        if strip_h < 5: strip_h = 5
        top_strip = img[0:strip_h, :]

        cy, cx = h // 2, w // 2
        sample_h, sample_w = int(h * 0.1), int(w * 0.1)
        if sample_h < 5: sample_h = 5
        if sample_w < 5: sample_w = 5
        center_sample = img[cy-sample_h:cy+sample_h, cx-sample_w:cx+sample_w]

        hsv_strip = cv2.cvtColor(top_strip, cv2.COLOR_BGR2HSV)
        mean_s = np.mean(hsv_strip[:, :, 1])
        mean_v = np.mean(hsv_strip[:, :, 2])

        if mean_s > 15: return False, f"OK (Кольоровий: S={mean_s:.1f})"
        if mean_v < 60: return False, f"OK (Темний: V={mean_v:.1f})"

        mean_top_bgr = np.mean(top_strip, axis=(0, 1))
        mean_cen_bgr = np.mean(center_sample, axis=(0, 1))
        color_diff = np.linalg.norm(mean_top_bgr - mean_cen_bgr)

        if color_diff < 25: return False, f"OK (Товар на весь екран, diff={color_diff:.1f})"

        gray_strip = cv2.cvtColor(top_strip, cv2.COLOR_BGR2GRAY)
        std_dev = np.std(gray_strip)
        mean_brightness = np.mean(gray_strip)

        if mean_brightness > 250: return False, "OK"

        tl_corner = gray_strip[0:10, 0:10]
        tr_corner = gray_strip[0:10, -10:]
        avg_corners = (np.mean(tl_corner) + np.mean(tr_corner)) / 2

        if avg_corners > 245:
            return False, "OK (Білі кути)"

        if std_dev > 45.0:
            return False, f"OK (Контрастний об'єкт: std={std_dev:.1f})"

        if mean_brightness < 240 and std_dev < 5.0:
            return False, f"OK (Рівний сірий: {mean_brightness:.1f})"

        if std_dev > shadow_std_dev_threshold:
            return True, f"Тіні/Шум (std={std_dev:.1f})"

        if mean_brightness < 200:
            return True, f"Сірий фон ({mean_brightness:.1f})"

        return False, "OK"

    except Exception as e:
        return False, str(e)

# ----------------------------- OCR / Text Analysis -----------------------------
def analyze_text_content(pil_image):
    try:
        proc_img = pil_image.copy()
        w, h = proc_img.size
        max_dim = 2000
        if w > max_dim or h > max_dim:
            ratio = min(max_dim/w, max_dim/h)
            new_size = (int(w*ratio), int(h*ratio))
            proc_img = proc_img.resize(new_size, Image.LANCZOS)

        img_rgb = proc_img.convert('RGB')
        
        data = pytesseract.image_to_data(img_rgb, lang="rus+ukr+eng", config=r'--oem 3 --psm 6', output_type=Output.DICT)
        
        valid_words = []
        full_text_raw = ""
        
        n_boxes = len(data['text'])
        for i in range(n_boxes):
            word = data['text'][i].strip()
            try: conf = int(data['conf'][i])
            except: conf = 0
            
            if not word: continue
            full_text_raw += word + " "
            
            if conf < 60: continue
            if len(word) < 3: continue

            word_lower = word.lower()
            vowels = set("аеєиіїоуюяыэёaeiouy")
            has_vowel = any(char in vowels for char in word_lower)
            if not has_vowel: continue
                
            valid_words.append(word_lower)

        text_clean = " ".join(valid_words)
        text_clean_no_spaces = re.sub(r'[^a-zа-я0-9]', '', text_clean)
        
        is_rozetka = False
        for variant in ROZETKA_VARIANTS:
            if variant in text_clean_no_spaces:
                is_rozetka = True
                break
        
        rus_specific = set('ыэъё')
        ukr_specific = set('іїєґ')
        
        rus_markers_count = 0
        ukr_markers_count = 0
        
        for w in valid_words:
            if any(c in rus_specific for c in w):
                rus_markers_count += 1
            if any(c in ukr_specific for c in w):
                ukr_markers_count += 1

        has_rus = False
        if rus_markers_count >= 2 and rus_markers_count > ukr_markers_count:
            has_rus = True
            
        found_wm_text = None
        for kw in WATERMARK_KEYWORDS:
            if kw in text_clean:
                found_wm_text = kw
                break

        word_count = len(valid_words)
        
        del proc_img
        del img_rgb

        return full_text_raw, has_rus, is_rozetka, found_wm_text, word_count

    except Exception as e:
        return "", False, False, None, 0

def detect_urls_from_text(text):
    return re.findall(r'[a-zA-Z0-9-]+\.(com|net|org|ua|ru|top)[^\s]*', text)


def detect_phone_numbers_from_text(text):
    """Повертає знайдені телефонні номери у тексті (OCR), нормалізовані до +380XXXXXXXXX."""
    if not text:
        return []

    # Залишаємо тільки цифри/службові розділювачі для стабільного regex-пошуку.
    cleaned = re.sub(r"[^\d\+\-\(\)\s]", " ", str(text))
    pattern = re.compile(
        r"(?<!\d)(?:\+?38)?0\d{2}[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{2}[\s\-\(\)]*\d{2}(?!\d)"
    )

    found = []
    seen = set()
    for m in pattern.findall(cleaned):
        digits = re.sub(r"\D", "", m)
        if len(digits) == 10 and digits.startswith("0"):
            normalized = "+38" + digits
        elif len(digits) == 12 and digits.startswith("380"):
            normalized = "+" + digits
        else:
            continue
        if normalized not in seen:
            seen.add(normalized)
            found.append(normalized)
    return found

def detect_qr_codes(pil_image):
    try:
        img_arr = np.array(pil_image)
        detector = cv2.QRCodeDetector()
        data, _, _ = detector.detectAndDecode(img_arr)
        if data: return True, data
        return False, None
    except Exception:
        return False, None

def detect_watermark_advanced(pil_image, ocr_wm_text=None, word_count=0):
    if ocr_wm_text: return True, f"Текст: '{ocr_wm_text}'"
    if word_count > 50: return False, "Текст/Етикетка"

    templates = load_templates()
    if not templates: return False, None 

    try:
        img_gray = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2GRAY)
        main_h, main_w = img_gray.shape[:2]

        for filename, templ in templates:
            t_h, t_w = templ.shape[:2]
            if t_h > main_h or t_w > main_w: continue

            for scale in np.linspace(0.5, 1.5, 15): 
                resized_t_w = int(t_w * scale)
                resized_t_h = int(t_h * scale)
                if resized_t_h > main_h or resized_t_w > main_w or resized_t_h < 10 or resized_t_w < 10:
                    continue
                
                resized_templ = cv2.resize(templ, (resized_t_w, resized_t_h))
                res = cv2.matchTemplate(img_gray, resized_templ, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                if max_val > 0.75: 
                    return True, f"Шаблон '{filename}'"
        return False, None
    except Exception as e:
        return False, str(e)

# ----------------------------- Класифікатор -----------------------------
def analyze_and_classify_photo(width, height, sharpness, conf, metrics_results):
    reasons = []
    debug_info = []
    status = "Середнє"

    bad_w = conf["bad"]["width"]
    bad_h = conf["bad"]["height"]
    bad_s = conf["bad"]["sharpness"]
    good_w = conf["good"]["width"]
    good_h = conf["good"]["height"]
    good_s = conf["good"]["sharpness"]
    
    bad_op = conf.get("bad_logic_operator", "І")
    good_op = conf.get("good_logic_operator", "АБО")

    critical_flags = [
        ("is_transparent", "Прозорий фон", metrics_results.get("transparency_reason")),
        ("has_shadows", "Тіні/Брудний фон", metrics_results.get("shadows_reason")),
        ("has_white_borders", "Некадроване", metrics_results.get("borders_reason")),
        ("has_1px_border", "Рамка по краю", metrics_results.get("1px_border_reason")),
        ("has_logo", "Логотип Rozetka", "Знайдено текст 'Rozetka'"),
        ("has_watermark", "Водяний знак", metrics_results.get("watermark_reason")),
        ("has_rus_text", "Рос. текст", "Знайдено кирилицю"),
        ("has_qr_url", "URL/QR на фото", metrics_results.get("qr_url_data")),
        ("has_phone_numbers", "Номер телефону на фото", metrics_results.get("phone_numbers_data")),
    ]

    for key, reason_txt, debug_txt in critical_flags:
        if metrics_results.get(key):
            reasons.append(reason_txt)
            if debug_txt:
                debug_info.append(debug_txt)
            status = "Погане"

    is_bad_size = False
    size_debug = ""
    if bad_op == "І":
        if width < bad_w and height < bad_h: is_bad_size = True; size_debug = f"{width}x{height} < {bad_w}x{bad_h} (AND)"
    else:
        if width < bad_w or height < bad_h: is_bad_size = True; size_debug = f"{width}x{height} (one < {bad_w}/{bad_h})"

    if is_bad_size:
        reasons.append("Малий розмір")
        debug_info.append(size_debug)
        status = "Погане"

    if sharpness < bad_s and not metrics_results.get("is_low_contrast_image"):
        reasons.append("Дуже розмите")
        debug_info.append(f"Blur:{sharpness:.1f}<{bad_s}")
        status = "Погане"

    if status == "Погане":
        return status, "; ".join(reasons), "; ".join(filter(None, debug_info))

    is_good_size = False
    if good_op == "І":
        if width >= good_w and height >= good_h: is_good_size = True
    else:
        if width >= good_w or height >= good_h: is_good_size = True
    
    is_good_sharp = (sharpness >= good_s) or bool(metrics_results.get("is_low_contrast_image"))

    if is_good_size and is_good_sharp:
        return "Хороше", "", ""

    if not is_good_size: reasons.append("Розмір < ідеалу")
    if not is_good_sharp: reasons.append("Недостатня різкість")

    return "Середнє", "; ".join(reasons), "; ".join(filter(None, debug_info))
