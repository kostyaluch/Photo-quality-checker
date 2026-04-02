# image_metrics.py
import os
import re
import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps

# Шлях до папки з шаблонами
BASE_DIR = os.path.dirname(__file__) if '__file__' in globals() else os.getcwd()
TEMPLATES_DIR = os.path.join(BASE_DIR, "watermark_templates")

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
    """
    try:
        img = Image.open(BytesIO(data))
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
    try:
        img = Image.open(BytesIO(data))
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            img = img.convert('RGBA')
            white_bg = Image.new("RGB", img.size, (255, 255, 255))
            white_bg.paste(img, (0, 0), img)
            img = white_bg
        else:
            img = img.convert("RGB")
        return img
    except Exception:
        return None

# ----------------------------- Метрики (UPDATED) -----------------------------

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

def detect_1px_border(pil_image, black_threshold=80, std_threshold=15.0):
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
            
            if (mean_inner_1 - mean_edge) > 20 or (mean_inner_2 - mean_edge) > 20:
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

def detect_ai_generated(pil_image):
    reasons = []
    try:
        info = pil_image.info
        s_info = str(info).lower()
        ai_markers = ["midjourney", "dall-e", "stable diffusion", "adobe firefly", "generative"]
        for m in ai_markers:
            if m in s_info:
                reasons.append(f"Metadata: {m}")
    except: pass

    if reasons:
        return True, ", ".join(reasons)
    return False, None

def detect_shadows_on_bg(pil_image, shadow_std_dev_threshold=15.0):
    """
    [SMART V3] Виявляє брудний фон, враховуючи вирізи камер та різкий контраст.
    
    Логіка:
    1. Перевірка кольору/темряви/центру (як у V2).
    2. NEW: Перевірка кутів. Якщо кути білі (>245) -> OK.
    3. NEW: Перевірка "Hard Edges". Якщо шум (std_dev) дуже великий (>50), 
       це означає різкий контраст (чорний товар + білий фон), а не м'які тіні. -> OK.
    """
    try:
        # BGR
        img = np.array(pil_image)
        if len(img.shape) == 2: img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4: img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif img.shape[2] == 3: img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        h, w = img.shape[:2]
        
        # Аналізуємо верхні 10%
        strip_h = int(h * 0.10) 
        if strip_h < 5: strip_h = 5
        top_strip = img[0:strip_h, :]
        
        # Зразок центру
        cy, cx = h // 2, w // 2
        sample_h, sample_w = int(h * 0.1), int(w * 0.1)
        if sample_h < 5: sample_h = 5
        if sample_w < 5: sample_w = 5
        center_sample = img[cy-sample_h:cy+sample_h, cx-sample_w:cx+sample_w]

        # --- ЕТАП 1: Базові перевірки (Колір, Темрява) ---
        hsv_strip = cv2.cvtColor(top_strip, cv2.COLOR_BGR2HSV)
        mean_s = np.mean(hsv_strip[:, :, 1])
        mean_v = np.mean(hsv_strip[:, :, 2])
        
        # Кольоровий товар
        if mean_s > 15: return False, f"OK (Кольоровий: S={mean_s:.1f})"
        # Дуже темний верх (суцільний чорний чохол без дірок)
        if mean_v < 60: return False, f"OK (Темний: V={mean_v:.1f})"

        # --- ЕТАП 2: Порівняння з центром ---
        mean_top_bgr = np.mean(top_strip, axis=(0, 1))
        mean_cen_bgr = np.mean(center_sample, axis=(0, 1))
        color_diff = np.linalg.norm(mean_top_bgr - mean_cen_bgr)
        
        # Якщо верх схожий на центр - це товар на весь кадр
        if color_diff < 25: return False, f"OK (Товар на весь екран, diff={color_diff:.1f})"

        # --- ЕТАП 3: Статистика шуму та яскравості ---
        gray_strip = cv2.cvtColor(top_strip, cv2.COLOR_BGR2GRAY)
        std_dev = np.std(gray_strip)
        mean_brightness = np.mean(gray_strip)

        # 3.1: Ідеально білий фон
        if mean_brightness > 250: return False, "OK"

        # --- ЕТАП 4: Спец-перевірки для складних випадків (NEW) ---
        
        # A. Перевірка кутів (Corner Check)
        # Якщо в самих куточках чисто біле - значить фон вибитий в біле (High Key),
        # а все що між кутами (дірки, камери) - це товар.
        tl_corner = gray_strip[0:10, 0:10] # Top-Left
        tr_corner = gray_strip[0:10, -10:] # Top-Right
        avg_corners = (np.mean(tl_corner) + np.mean(tr_corner)) / 2
        
        if avg_corners > 245:
             return False, "OK (Білі кути)"

        # B. Перевірка на різкий контраст (High Contrast Exception)
        # Тіні/Бруд дають std_dev ~ 15-30.
        # Чорний чохол на білому фоні дає std_dev > 50 (через різку різницю 0 vs 255).
        if std_dev > 45.0:
             return False, f"OK (Контрастний об'єкт: std={std_dev:.1f})"

        # --- ЕТАП 5: Фінальний вердикт ---
        
        # Чистий сірий фон (плашка)
        if mean_brightness < 240 and std_dev < 5.0:
             # Можна повернути True, якщо сірий фон заборонений
             return False, f"OK (Рівний сірий: {mean_brightness:.1f})" 

        # Бруд/Тіні
        if std_dev > shadow_std_dev_threshold:
            return True, f"Тіні/Шум (std={std_dev:.1f})"
        
        # Просто темно
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
        ("is_ai_generated", "ШІ/Generative", metrics_results.get("ai_reason"))
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

    if sharpness < bad_s:
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
    
    is_good_sharp = (sharpness >= good_s)

    if is_good_size and is_good_sharp:
        return "Хороше", "", ""

    if not is_good_size: reasons.append("Розмір < ідеалу")
    if not is_good_sharp: reasons.append("Недостатня різкість")

    return "Середнє", "; ".join(reasons), "; ".join(filter(None, debug_info))