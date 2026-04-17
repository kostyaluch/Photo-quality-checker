# utils.py
import os
import re
import json
import shutil
import hashlib
import copy
import asyncio
from datetime import datetime
import aiohttp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ----------------------------- Константи / Конфіг -----------------------------
BASE_DIR = os.path.dirname(__file__) if '__file__' in globals() else os.getcwd()
CONFIG_FILE = os.path.join(BASE_DIR, "config_photo_quality.json")
CACHE_DIR = os.path.join(BASE_DIR, ".photo_cache")

HTTP_TIMEOUT = 15
HTTP_DOWNLOAD_RETRIES = 3
HTTP_RETRY_BASE_DELAY = 0.7  # base delay between retries (seconds)
HTTP_TIMEOUT_MULTIPLIER = 1.5  # extra read headroom for slow/unstable CDNs
CONNECT_TIMEOUT_CAP = 10  # upper bound for TCP connect timeout (seconds)
DOWNLOAD_CHUNK_SIZE_BYTES = 64 * 1024
MAX_IMAGE_SIZE_BYTES = 50 * 1024 * 1024
# Виключаємо лапки, крапки та коми з URL вже на рівні регексу
URL_REGEX = re.compile(r"(https?://[^\s,;\)\]\}\'\"]+)", re.IGNORECASE)

DEFAULT_CONFIG = {
    "good": {"width": 800, "height": 800, "sharpness": 80.0},
    "bad": {"width": 600, "height": 600, "sharpness": 50.0},
    "bad_logic_operator": "І",
    "good_logic_operator": "АБО",
    "concurrency": 4,
    "last_manual_column": "",
    "border_ratio": 0.1,
    "shadow_threshold": 50,
    "shadow_mode": 2,
    "shadow_mode_profiles": {
        "1": {"white_v_min": 240, "white_s_max": 15, "shadow_tolerance": 10},
        "2": {"white_v_min": 225, "white_s_max": 20, "shadow_tolerance": 35},
        "3": {"white_v_min": 210, "white_s_max": 26, "shadow_tolerance": 60},
        "4": {"white_v_min": 195, "white_s_max": 34, "shadow_tolerance": 85}
    },
    "options": {
        "check_rus_text": False,
        "check_shadows": False,
        "check_qr_url": False,
        "check_phone_numbers": False,
        "check_logos": True,
        "check_watermarks": False,
        "check_borders": True,
        "check_1px_border": False
    }
}

# ----------------------------- Форматування часу (NEW) -----------------------------
def format_duration(seconds):
    """Конвертує секунди у читабельний формат: 1h 20m 5s"""
    if seconds is None: return "--"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"

# ----------------------------- Конфіг -----------------------------
def load_config():
    conf = copy.deepcopy(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_conf = json.load(f)

            def update_dict(d, u):
                for k, v in u.items():
                    if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                        d[k] = update_dict(d.get(k, {}), v)
                    else:
                        d[k] = v
                return d

            conf = update_dict(conf, user_conf)
    except Exception:
        pass

    if "options" not in conf:
        conf["options"] = DEFAULT_CONFIG["options"].copy()
    else:
        for k, v in DEFAULT_CONFIG["options"].items():
            conf["options"].setdefault(k, v)

    conf["shadow_mode"] = int(conf.get("shadow_mode", DEFAULT_CONFIG["shadow_mode"]))
    if conf["shadow_mode"] < 1 or conf["shadow_mode"] > 4:
        conf["shadow_mode"] = DEFAULT_CONFIG["shadow_mode"]

    profiles = conf.get("shadow_mode_profiles", {})
    default_profiles = DEFAULT_CONFIG["shadow_mode_profiles"]
    if not isinstance(profiles, dict):
        profiles = {}
    normalized = {}
    for mode in ("1", "2", "3", "4"):
        src = profiles.get(mode, {})
        if not isinstance(src, dict):
            src = {}
        base = default_profiles[mode]
        normalized[mode] = {
            "white_v_min": int(src.get("white_v_min", base["white_v_min"])),
            "white_s_max": int(src.get("white_s_max", base["white_s_max"])),
            "shadow_tolerance": int(src.get("shadow_tolerance", base["shadow_tolerance"])),
        }
    conf["shadow_mode_profiles"] = normalized
    return conf


def save_config(conf):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Не вдалося зберегти конфіг:", e)


# ----------------------------- Утиліти кешу -----------------------------
def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def clear_cache_dir():
    if os.path.exists(CACHE_DIR):
        try:
            shutil.rmtree(CACHE_DIR)
            return True, None
        except Exception as e:
            return False, str(e)
    return True, None


def url_to_cache_path(url):
    h = hashlib.sha256(url.encode('utf-8', errors='ignore')).hexdigest()
    return os.path.join(CACHE_DIR, h)


def is_cached(url):
    base = url_to_cache_path(url)
    return os.path.exists(base + ".img")


def save_to_cache(url, content, content_type=None):
    base = url_to_cache_path(url)
    try:
        with open(base + ".img", "wb") as f:
            f.write(content)
        meta = {"url": url, "saved_at": datetime.now().isoformat()}
        if content_type:
            meta["content_type"] = content_type
        with open(base + ".meta", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:
        pass


def load_from_cache(url):
    base = url_to_cache_path(url)
    try:
        with open(base + ".img", "rb") as f:
            return f.read()
    except Exception:
        return None


# ----------------------------- Парсер URL -----------------------------
def extract_urls(text):
    if not isinstance(text, str) or not text.strip():
        return []

    raw_text = text.strip()
    clean_raw = raw_text.strip('"\'')

    if os.path.exists(clean_raw):
        if os.path.isfile(clean_raw):
            return [clean_raw]
        elif os.path.isdir(clean_raw):
            out = []
            valid_exts = ('.jpg', '.jpeg', '.png', '.bmp', '.webp')
            try:
                for fname in os.listdir(clean_raw):
                    if fname.lower().endswith(valid_exts):
                        out.append(os.path.join(clean_raw, fname))
            except:
                pass
            return out

    http_links = URL_REGEX.findall(raw_text)
    if http_links:
        out = []
        for u in http_links:
            # Відрізаємо зайві символи в кінці посилання
            u_clean = u.rstrip(").,;'\"")
            out.append(u_clean)
        return out

    parts = re.split(r'[,\n]+', raw_text)
    candidates = []
    seen = set()
    for p in parts:
        p_clean = p.strip().strip('"\'')
        if p_clean and os.path.isfile(p_clean) and p_clean not in seen:
            seen.add(p_clean)
            candidates.append(p_clean)
    
    return candidates


# ----------------------------- Мережа -----------------------------
def create_session_with_retries():
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        backoff_factor=1
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "PhotoQualityChecker/11.0"})
    return session


def download_image_bytes(path_or_url, session):
    ensure_cache_dir()
    is_http = path_or_url.lower().startswith(('http://', 'https://'))

    if not is_http:
        if os.path.isfile(path_or_url):
            try:
                with open(path_or_url, 'rb') as f:
                    return f.read(), None
            except Exception as e:
                return None, f"Read Error: {e}"
        else:
            return None, "File not found"

    try:
        if is_cached(path_or_url):
            return load_from_cache(path_or_url), None

        resp = session.get(path_or_url, timeout=HTTP_TIMEOUT, stream=True)
        resp.raise_for_status()
        
        content = resp.content
        if len(content) > MAX_IMAGE_SIZE_BYTES:
            return None, "File > 50MB"

        save_to_cache(path_or_url, content, resp.headers.get("Content-Type"))
        return content, None
    except Exception as e:
        return None, str(e)


async def async_download_image_bytes(path_or_url, session, semaphore):
    """Асинхронне завантаження зображення через aiohttp.

    Для локальних файлів використовує синхронне читання (швидко).
    Для HTTP/HTTPS — асинхронний запит з обмеженням паралельності (semaphore).
    Результат кешується на диск для повторного використання.
    """
    ensure_cache_dir()
    is_http = path_or_url.lower().startswith(('http://', 'https://'))

    if not is_http:
        # Локальний файл — читаємо синхронно
        if os.path.isfile(path_or_url):
            try:
                with open(path_or_url, 'rb') as f:
                    return f.read(), None
            except Exception as e:
                return None, f"Read Error: {e}"
        return None, "File not found"

    # Кеш перевіряємо поза семафором — швидка операція
    if is_cached(path_or_url):
        return load_from_cache(path_or_url), None

    last_error = "Unknown download error"

    async with semaphore:
        for attempt in range(1, HTTP_DOWNLOAD_RETRIES + 1):
            try:
                timeout = aiohttp.ClientTimeout(
                    total=HTTP_TIMEOUT * HTTP_TIMEOUT_MULTIPLIER,
                    connect=min(HTTP_TIMEOUT, CONNECT_TIMEOUT_CAP),
                    sock_read=HTTP_TIMEOUT * HTTP_TIMEOUT_MULTIPLIER,
                )
                async with session.get(path_or_url, timeout=timeout) as resp:
                    resp.raise_for_status()

                    chunks = []
                    total_size = 0
                    async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_SIZE_BYTES):
                        next_size = total_size + len(chunk)
                        if next_size > MAX_IMAGE_SIZE_BYTES:
                            return None, "File > 50MB"
                        total_size = next_size
                        chunks.append(chunk)
                    content = b"".join(chunks)

                    content_type = resp.headers.get("Content-Type")
                    save_to_cache(path_or_url, content, content_type)
                    return content, None
            except (
                aiohttp.ClientPayloadError,
                aiohttp.ClientConnectionError,
                aiohttp.ServerDisconnectedError,
                aiohttp.ClientOSError,
                asyncio.TimeoutError,
            ) as e:
                last_error = str(e)
                if attempt < HTTP_DOWNLOAD_RETRIES:
                    await asyncio.sleep(HTTP_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                    continue
                break
            except Exception as e:
                return None, str(e)

    return None, last_error
