# main_app.py
import asyncio
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from io import BytesIO
import pandas as pd
from PIL import Image, ImageTk
from utils import load_config, save_config, clear_cache_dir, ensure_cache_dir, DEFAULT_CONFIG, format_duration
from processing_engine import process_file
from image_metrics import resource_path

# ── Design tokens ──────────────────────────────────────────────────────────────
# Light theme palette – change here to restyle the whole app.
_C_BG         = "#F1F5F9"   # Window background (light blue-grey)
_C_SURFACE    = "#FFFFFF"   # Card / panel surface
_C_BORDER     = "#CBD5E1"   # Separator and border colour
_C_PRIMARY    = "#2563EB"   # Primary action (Run button)
_C_PRIMARY_FG = "#FFFFFF"   # Text on primary button
_C_TEXT       = "#1E293B"   # Main body text
_C_MUTED      = "#64748B"   # Secondary / hint text
_C_SUCCESS    = "#16A34A"   # Good / OK colour
_C_ERROR      = "#DC2626"   # Bad / Problem colour
_C_LOG_BG     = "#F8FAFC"   # Log widget background
_C_LOG_FG     = "#334155"   # Log widget foreground
_C_TOOLTIP_BG = "#FFF9E6"   # Tooltip background
_C_SEARCH_HL  = "#FEF08A"   # Search-match highlight in the log

_FONT_BODY    = ("Segoe UI", 10)
_FONT_BOLD    = ("Segoe UI", 10, "bold")
_FONT_HEADING = ("Segoe UI", 11, "bold")
_FONT_SMALL   = ("Segoe UI", 9)
_FONT_LOG     = ("Consolas", 9)

# Fallback canvas dimensions used before the preview widget is realized
_PREVIEW_FALLBACK_W = 320
_PREVIEW_FALLBACK_H = 185
_PREVIEW_MIN_RESCHEDULE_MS = 20
# ───────────────────────────────────────────────────────────────────────────────


class ToolTip(object):
    """Lightweight tooltip shown on mouse-enter after a short delay."""

    def __init__(self, widget, text='widget info'):
        self.waittime = 500
        self.wraplength = 320
        self.widget = widget
        self.text = text
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.id = None
        self.tw = None

    def enter(self, event=None): self.schedule()
    def leave(self, event=None): self.unschedule(); self.hidetip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(self.waittime, self.showtip)

    def unschedule(self):
        id = self.id
        self.id = None
        if id: self.widget.after_cancel(id)

    def showtip(self, event=None):
        widget = self.widget
        try:
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() + 4
        except Exception:
            x = widget.winfo_rootx() + 25
            y = widget.winfo_rooty() + 20
        self.tw = tk.Toplevel(widget)
        self.tw.wm_overrideredirect(True)
        label = tk.Label(self.tw, text=self.text, justify='left',
                         background=_C_TOOLTIP_BG, foreground="#333333",
                         relief='solid', borderwidth=1,
                         font=('Segoe UI', 9),
                         wraplength=self.wraplength,
                         padx=6, pady=4)
        label.pack()
        # Adjust so the tooltip stays inside the screen
        self.tw.update_idletasks()
        sw = widget.winfo_screenwidth()
        tw_w = self.tw.winfo_width()
        if x + tw_w > sw:
            x = sw - tw_w - 5
        self.tw.wm_geometry("+%d+%d" % (x, y))

    def hidetip(self):
        tw = self.tw
        self.tw = None
        if tw: tw.destroy()


class PhotoQualityGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Photo Quality Checker")
        self.iconbitmap(resource_path("PQC_logo.ico"))
        # Wider window to accommodate the two-column layout
        self.geometry("1140x720")
        self.minsize(900, 600)
        self.resizable(True, True)
        self.conf = load_config()
        ensure_cache_dir()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.processing_thread = None
        # Processing state: 'idle' | 'running' | 'paused'
        self._proc_state = "idle"
        # References to asyncio loop and asyncio.Event (set in thread_target)
        self._async_loop = None
        self._async_pause_event = None
        # Cross-thread preview handoff:
        # keep only the latest preview buffer and schedule at most one GUI update.
        self._preview_lock = threading.Lock()
        self._latest_preview_data = None
        self._preview_update_scheduled = False
        self._last_preview_update_ts = 0.0
        self._preview_min_interval = 0.12
        self.create_widgets()

    # ── Widget construction ────────────────────────────────────────────────────

    def create_widgets(self):
        """Entry-point: configure styles, then build the two-column layout."""
        self._setup_styles()
        self.configure(bg=_C_BG)

        # Two-column grid: left column (controls), right column (preview + log)
        self.columnconfigure(0, weight=3, minsize=510)
        self.columnconfigure(1, weight=2, minsize=380)
        self.rowconfigure(0, weight=1)

        left = self._build_left_panel(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)

        right = self._build_right_panel(self)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)

    def _setup_styles(self):
        """Configure ttk styles for the modern light theme."""
        s = ttk.Style()
        # "clam" allows background/foreground customisation on most platforms
        s.theme_use("clam")

        # ── Base defaults ──
        s.configure(".", font=_FONT_BODY, background=_C_BG, foreground=_C_TEXT,
                    bordercolor=_C_BORDER, troughcolor=_C_BORDER)
        s.configure("TFrame",    background=_C_BG)
        s.configure("TLabel",    background=_C_BG, foreground=_C_TEXT)
        s.configure("TSeparator", background=_C_BORDER)

        # ── Card surface (white panels) ──
        s.configure("Card.TFrame",  background=_C_SURFACE)
        s.configure("Card.TLabel",  background=_C_SURFACE, foreground=_C_TEXT)
        s.configure("Muted.TLabel", background=_C_SURFACE, foreground=_C_MUTED,
                    font=_FONT_SMALL)

        # Column headers for the metrics table
        s.configure("ColHead.TLabel", background=_C_SURFACE, foreground=_C_MUTED,
                    font=("Segoe UI", 9, "bold"))
        s.configure("ColGood.TLabel", background=_C_SURFACE, foreground=_C_SUCCESS,
                    font=("Segoe UI", 9, "bold"))
        s.configure("ColBad.TLabel",  background=_C_SURFACE, foreground=_C_ERROR,
                    font=("Segoe UI", 9, "bold"))

        # ── Card-style LabelFrame ──
        s.configure("Card.TLabelframe",
                    background=_C_SURFACE, relief="flat",
                    bordercolor=_C_BORDER, borderwidth=1)
        s.configure("Card.TLabelframe.Label",
                    background=_C_SURFACE, foreground=_C_TEXT, font=_FONT_BOLD)

        # ── Checkbutton on white card surface ──
        s.configure("Card.TCheckbutton",
                    background=_C_SURFACE, foreground=_C_TEXT, font=_FONT_BODY)
        s.map("Card.TCheckbutton",
              background=[("active", _C_SURFACE)])
        s.configure("CardSelected.TCheckbutton", indicatorcolor="#16A34A")

        # ── Primary action button (Run) ──
        s.configure("Primary.TButton",
                    background=_C_PRIMARY, foreground=_C_PRIMARY_FG,
                    font=("Segoe UI", 11, "bold"), padding=(14, 9),
                    relief="flat", borderwidth=0, anchor="center")
        s.map("Primary.TButton",
              background=[("active", "#1D4ED8"), ("disabled", "#93C5FD")],
              foreground=[("disabled", "#DBEAFE"), ("active", _C_PRIMARY_FG)])

        # ── Secondary buttons (Stop, utilities) ──
        s.configure("Secondary.TButton",
                    background=_C_SURFACE, foreground=_C_TEXT,
                    font=_FONT_BODY, padding=(8, 5),
                    relief="flat", borderwidth=1, bordercolor=_C_BORDER)
        s.map("Secondary.TButton",
              background=[("active", "#F1F5F9"), ("disabled", _C_SURFACE)],
              foreground=[("disabled", _C_MUTED)])

        # ── Progress bar ──
        s.configure("Accent.Horizontal.TProgressbar",
                    troughcolor=_C_BORDER, background=_C_PRIMARY,
                    bordercolor=_C_BORDER, thickness=8)

        # ── Entry and Combobox ──
        s.configure("TEntry",    fieldbackground=_C_SURFACE,
                    foreground=_C_TEXT, insertcolor=_C_TEXT)
        s.configure("TCombobox", fieldbackground=_C_SURFACE,
                    foreground=_C_TEXT, background=_C_SURFACE)

    # ── Left panel ─────────────────────────────────────────────────────────────

    def _build_left_panel(self, parent):
        """Left column: data source → metrics → additional checks → launch."""
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        self._build_source_card(frame).pack(fill="x", pady=(0, 8))
        self._build_metrics_card(frame).pack(fill="x", pady=(0, 8))
        self._build_options_card(frame).pack(fill="x", pady=(0, 8))
        self._build_launch_card(frame).pack(fill="x")
        return frame

    def _build_source_card(self, parent):
        """Card ① – data source (file path + column selector)."""
        card = ttk.LabelFrame(parent, text="①  Джерело даних",
                              style="Card.TLabelframe", padding=10)

        # File row
        row1 = ttk.Frame(card, style="Card.TFrame")
        row1.pack(fill="x", pady=(0, 6))
        ttk.Label(row1, text="Файл:", style="Card.TLabel").pack(side="left")
        self.file_path_var = tk.StringVar()
        fe = ttk.Entry(row1, textvariable=self.file_path_var)
        fe.pack(side="left", fill="x", expand=True, padx=(6, 4))
        ToolTip(fe, "Шлях до Excel (.xlsx/.xls) або CSV-файлу з даними про товари. "
                    "Можна ввести вручну або натиснути «Огляд…».")
        browse_btn = ttk.Button(row1, text="Огляд…", command=self.browse_file,
                                style="Secondary.TButton")
        browse_btn.pack(side="left")
        ToolTip(browse_btn, "Відкрити діалог вибору файлу Excel або CSV.")

        # Column row
        row2 = ttk.Frame(card, style="Card.TFrame")
        row2.pack(fill="x")
        ttk.Label(row2, text="Колонка:", style="Card.TLabel").pack(side="left")
        self.col_combo = ttk.Combobox(row2, values=[], width=36, state="readonly")
        self.col_combo.pack(side="left", padx=(6, 0))
        self.col_combo.bind("<<ComboboxSelected>>", self.on_column_selected)
        ToolTip(self.col_combo,
                "Оберіть колонку, яка містить URL-адреси або шляхи до зображень. "
                "Програма автоматично визначає найбільш відповідну колонку після завантаження файлу.")

        return card

    def _build_metrics_card(self, parent):
        """Card ② – quality thresholds (good/bad values + logic operators)."""
        card = ttk.LabelFrame(parent, text="②  Вимоги до якості",
                              style="Card.TLabelframe", padding=10)
        g = ttk.Frame(card, style="Card.TFrame")
        g.pack(fill="x")
        g.columnconfigure(0, weight=1)

        # Header row
        ttk.Label(g, text="Параметр",  style="ColHead.TLabel").grid(
            row=0, column=0, sticky="w")
        good_lbl = ttk.Label(g, text="✔  Хороше", style="ColGood.TLabel")
        good_lbl.grid(row=0, column=1, padx=10)
        ToolTip(good_lbl, "Мінімальне значення, яке вважається прийнятним. "
                          "Зображення з показниками ≥ цього порогу отримають позначку «Добре».")
        ttk.Label(g, text="Логіка", style="ColHead.TLabel").grid(
            row=0, column=2, padx=6)
        bad_lbl = ttk.Label(g, text="✘  Погане", style="ColBad.TLabel")
        bad_lbl.grid(row=0, column=3, padx=10)
        ToolTip(bad_lbl, "Максимальне значення, нижче якого зображення вважається поганим. "
                         "Зображення з показниками ≤ цього порогу отримають позначку «Погано».")

        ttk.Separator(g, orient="horizontal").grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(4, 6))

        # Width
        wl = ttk.Label(g, text="Ширина (px):", style="Card.TLabel")
        wl.grid(row=2, column=0, sticky="w", pady=2)
        ToolTip(wl, "Горизонтальний розмір зображення у пікселях.")
        self.good_w = tk.IntVar(value=self.conf["good"]["width"])
        gwe = ttk.Entry(g, textvariable=self.good_w, width=8)
        gwe.grid(row=2, column=1, padx=4)
        ToolTip(gwe, "Мінімальна ширина (px) для «хорошого» зображення. Наприклад: 800.")
        self.bad_w = tk.IntVar(value=self.conf["bad"]["width"])
        bwe = ttk.Entry(g, textvariable=self.bad_w, width=8)
        bwe.grid(row=2, column=3, padx=4)
        ToolTip(bwe, "Максимальна ширина (px) для «поганого» зображення. Наприклад: 400.")

        # Logic operators (sit between Width and Height rows)
        self.good_logic_op = tk.StringVar(value=self.conf.get("good_logic_operator", "АБО"))
        og = ttk.Combobox(g, textvariable=self.good_logic_op,
                          values=["І", "АБО"], width=5, state="readonly")
        og.grid(row=3, column=1, pady=2, padx=4)
        ToolTip(og, "«І» — зображення хороше, якщо ширина І висота відповідають порогу.\n"
                    "«АБО» — достатньо, щоб хоча б один параметр відповідав.")
        ttk.Label(g, text="← Ш / В →", style="Muted.TLabel").grid(
            row=3, column=2, padx=4)
        self.bad_logic_op = tk.StringVar(value=self.conf.get("bad_logic_operator", "І"))
        ob = ttk.Combobox(g, textvariable=self.bad_logic_op,
                          values=["І", "АБО"], width=5, state="readonly")
        ob.grid(row=3, column=3, pady=2, padx=4)
        ToolTip(ob, "«І» — зображення погане, якщо ширина І висота нижче порогу.\n"
                    "«АБО» — достатньо, щоб хоча б один параметр був нижче порогу.")

        # Height
        hl = ttk.Label(g, text="Висота (px):", style="Card.TLabel")
        hl.grid(row=4, column=0, sticky="w", pady=2)
        ToolTip(hl, "Вертикальний розмір зображення у пікселях.")
        self.good_h = tk.IntVar(value=self.conf["good"]["height"])
        ghe = ttk.Entry(g, textvariable=self.good_h, width=8)
        ghe.grid(row=4, column=1, padx=4)
        ToolTip(ghe, "Мінімальна висота (px) для «хорошого» зображення. Наприклад: 800.")
        self.bad_h = tk.IntVar(value=self.conf["bad"]["height"])
        bhe = ttk.Entry(g, textvariable=self.bad_h, width=8)
        bhe.grid(row=4, column=3, padx=4)
        ToolTip(bhe, "Максимальна висота (px) для «поганого» зображення. Наприклад: 400.")

        # Sharpness
        sl = ttk.Label(g, text="Різкість (Laplacian):", style="Card.TLabel")
        sl.grid(row=5, column=0, sticky="w", pady=(2, 0))
        ToolTip(sl, "Оцінка різкості за методом Лапласіана. "
                    "Чим вище значення — тим чіткіше зображення. "
                    "Розмиті фото мають низький показник (< 50–100).")
        self.good_s = tk.DoubleVar(value=self.conf["good"]["sharpness"])
        gse = ttk.Entry(g, textvariable=self.good_s, width=8)
        gse.grid(row=5, column=1, padx=4)
        ToolTip(gse, "Мінімальний показник різкості для «хорошого» фото. Наприклад: 80.")
        self.bad_s = tk.DoubleVar(value=self.conf["bad"]["sharpness"])
        bse = ttk.Entry(g, textvariable=self.bad_s, width=8)
        bse.grid(row=5, column=3, padx=4)
        ToolTip(bse, "Максимальний показник різкості для «поганого» фото. Наприклад: 30.")

        return card

    def _build_options_card(self, parent):
        """Card ③ – additional checks: sliders with value labels + 2-col checkboxes."""
        opts_cfg = self.conf.get("options", DEFAULT_CONFIG["options"])
        card = ttk.LabelFrame(parent, text="③  Додаткові перевірки",
                              style="Card.TLabelframe", padding=10)

        def make_cb(parent_widget, text, var):
            # Використовуємо tk.Checkbutton, щоб мати стандартну "галочку" замість хрестика.
            return tk.Checkbutton(
                parent_widget,
                text=text,
                variable=var,
                bg=_C_SURFACE,
                fg=_C_TEXT,
                activebackground=_C_SURFACE,
                activeforeground=_C_TEXT,
                selectcolor="#E2E8F0",
                font=_FONT_BODY,
                anchor="w",
                relief="flat",
                bd=0,
                highlightthickness=0,
                cursor="hand2",
            )

        # ── Slider rows ──────────────────────────────────────────────────────
        sliders = ttk.Frame(card, style="Card.TFrame")
        sliders.pack(fill="x", pady=(0, 6))
        sliders.columnconfigure(2, weight=1)  # slider column stretches

        # Shadows
        self.opt_shadows = tk.BooleanVar(value=opts_cfg.get("check_shadows", False))
        cb_s = make_cb(sliders, "Тіні / Брудний фон", self.opt_shadows)
        cb_s.grid(row=0, column=0, sticky="w")
        ToolTip(cb_s, "Перевіряє перше фото товару: фон має бути білим, "
                      "а тіні — мінімальними. Аналізує весь периметр (верх/низ/ліво/право).")
        ttk.Label(sliders, text="Режим:", style="Muted.TLabel").grid(
            row=0, column=1, sticky="e", padx=(8, 4))
        self.shadow_mode_profiles = self.conf.get(
            "shadow_mode_profiles", DEFAULT_CONFIG.get("shadow_mode_profiles", {})
        )
        self.shadow_mode = tk.IntVar(value=int(self.conf.get("shadow_mode", 2)))
        self.shadow_mode_label = tk.StringVar()
        self._shadow_mode_titles = {
            1: "1 — дуже вибаглива",
            2: "2 — збалансована",
            3: "3 — м'якша",
            4: "4 — найм'якша",
        }
        self._update_shadow_mode_label()
        sc_s = tk.Scale(sliders, from_=1, to=4, resolution=1, orient="horizontal",
                        variable=self.shadow_mode, length=130, showvalue=0,
                        command=lambda *_: self._update_shadow_mode_label(),
                        bg=_C_SURFACE, fg=_C_TEXT, highlightthickness=0,
                        troughcolor=_C_BORDER, sliderrelief="flat", bd=0)
        sc_s.grid(row=0, column=2, sticky="ew", padx=(0, 4))
        ToolTip(sc_s, "Режими перевірки тіней/фону (1–4).\n"
                      "1 = дуже вибаглива перевірка.\n"
                      "4 = найм'якіша перевірка.\n"
                      "Детальні пороги змінюються кнопкою ⚙.")
        ttk.Label(sliders, textvariable=self.shadow_mode_label,
                  style="Muted.TLabel", width=20, anchor="w").grid(
            row=0, column=3, sticky="w")
        shadow_settings_btn = ttk.Button(
            sliders,
            text="⚙",
            width=2,
            style="Secondary.TButton",
            command=self.open_shadow_modes_dialog,
        )
        shadow_settings_btn.grid(row=0, column=4, sticky="w", padx=(2, 0))
        ToolTip(shadow_settings_btn, "Детальні налаштування порогів білизни фону та тіней для режимів 1–4.")

        # Borders
        self.opt_borders = tk.BooleanVar(value=opts_cfg.get("check_borders", True))
        cb_b = make_cb(sliders, "Некадровані (білі поля)", self.opt_borders)
        cb_b.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ToolTip(cb_b, "Виявляє зображення, де товар не займає весь кадр і навколо є надмірні білі поля.")
        ttk.Label(sliders, text="Макс %:", style="Muted.TLabel").grid(
            row=1, column=1, sticky="e", padx=(8, 4), pady=(6, 0))
        self.border_r = tk.DoubleVar(value=self.conf.get("border_ratio", 0.1))
        self.border_percent = tk.DoubleVar(value=round(self.border_r.get() * 100))
        def _upd_border_r(val): self.border_r.set(float(val) / 100.0)
        sc_b = tk.Scale(sliders, from_=1, to=50, orient="horizontal",
                        variable=self.border_percent, command=_upd_border_r, length=130,
                        showvalue=0,
                        bg=_C_SURFACE, fg=_C_TEXT, highlightthickness=0,
                        troughcolor=_C_BORDER, sliderrelief="flat", bd=0)
        sc_b.grid(row=1, column=2, sticky="ew", padx=(0, 4), pady=(6, 0))
        ToolTip(sc_b, "Максимально допустима частка білих полів від розміру зображення (1–50%). "
                      "Наприклад, 10% означає, що поля не повинні перевищувати 10% ширини/висоти.")
        # Display as integer to avoid "10.0" formatting
        # border_percent is a DoubleVar (e.g. 10.0); display as integer to avoid
        # the ugly ".0" suffix that would appear in a plain textvariable label.
        self._bord_disp = tk.StringVar(value=str(int(self.border_percent.get())))
        def _upd_bord_disp(*_): self._bord_disp.set(str(int(self.border_percent.get())))
        self.border_percent.trace_add("write", _upd_bord_disp)
        ttk.Label(sliders, textvariable=self._bord_disp,
                  style="Muted.TLabel", width=3, anchor="w").grid(
            row=1, column=3, sticky="w", pady=(6, 0))

        # ── 2-column checkbox grid ────────────────────────────────────────────
        ttk.Separator(card, orient="horizontal").pack(fill="x", pady=(0, 6))
        cbox = ttk.Frame(card, style="Card.TFrame")
        cbox.pack(fill="x")
        cbox.columnconfigure(0, weight=1)
        cbox.columnconfigure(1, weight=1)

        self.opt_logos = tk.BooleanVar(value=opts_cfg.get("check_logos", False))
        cb_l = make_cb(cbox, "Логотипи Rozetka", self.opt_logos)
        cb_l.grid(row=0, column=0, sticky="w", pady=2)
        ToolTip(cb_l, "Виявляє логотипи або фірмові елементи Rozetka на зображенні.")

        self.opt_watermark = tk.BooleanVar(value=opts_cfg.get("check_watermarks", False))
        cb_w = make_cb(cbox, "Водяні знаки, значки", self.opt_watermark)
        cb_w.grid(row=0, column=1, sticky="w", pady=2)
        ToolTip(cb_w, "Виявляє водяні знаки на фото за допомогою шаблонів із папки watermark_templates.")

        self.opt_rus_text = tk.BooleanVar(value=opts_cfg.get("check_rus_text", False))
        cb_r = make_cb(cbox, "Російський текст", self.opt_rus_text)
        cb_r.grid(row=1, column=0, sticky="w", pady=2)
        ToolTip(cb_r, "За допомогою OCR виявляє текст російською мовою. "
                      "Увага: ця перевірка може уповільнити обробку.")

        self.opt_qr_url = tk.BooleanVar(value=opts_cfg.get("check_qr_url", False))
        cb_q = make_cb(cbox, "Наявність URL або QR-коду", self.opt_qr_url)
        cb_q.grid(row=1, column=1, sticky="w", pady=2)
        ToolTip(cb_q, "Виявляє QR-коди або текстові URL-адреси на зображенні.")

        self.opt_phone_numbers = tk.BooleanVar(value=opts_cfg.get("check_phone_numbers", False))
        cb_ph = make_cb(cbox, "Номери телефонів", self.opt_phone_numbers)
        cb_ph.grid(row=2, column=1, sticky="w", pady=2)
        ToolTip(cb_ph, "Виявляє телефонні номери у тексті на фото (OCR).")

        self.opt_1px = tk.BooleanVar(value=opts_cfg.get("check_1px_border", False))
        cb_p = make_cb(cbox, "Тонка рамка", self.opt_1px)
        cb_p.grid(row=2, column=0, sticky="w", pady=2)
        ToolTip(cb_p, "Виявляє чорні/темні рамки товщиною 1-2 пікселі по самому краю фото.")

        return card

    def _update_shadow_mode_label(self):
        mode = int(self.shadow_mode.get())
        mode = 1 if mode < 1 else 4 if mode > 4 else mode
        self.shadow_mode.set(mode)
        self.shadow_mode_label.set(self._shadow_mode_titles.get(mode, f"{mode}"))

    def open_shadow_modes_dialog(self):
        win = tk.Toplevel(self)
        win.title("Налаштування режимів тіней")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Пороги для режимів 1–4 (білизна фону й тіні):",
            style="Card.TLabel",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(10, 6))

        ttk.Label(win, text="Режим", style="Muted.TLabel").grid(row=1, column=0, padx=8, sticky="w")
        ttk.Label(win, text="Мін V", style="Muted.TLabel").grid(row=1, column=1, padx=8)
        ttk.Label(win, text="Макс S", style="Muted.TLabel").grid(row=1, column=2, padx=8)
        ttk.Label(win, text="Тіні 0–100", style="Muted.TLabel").grid(row=1, column=3, padx=8)

        vars_by_mode = {}
        for mode in range(1, 5):
            prof = self.shadow_mode_profiles.get(str(mode), {})
            v_var = tk.IntVar(value=int(prof.get("white_v_min", 205)))
            s_var = tk.IntVar(value=int(prof.get("white_s_max", 25)))
            t_var = tk.IntVar(value=int(prof.get("shadow_tolerance", 50)))
            vars_by_mode[str(mode)] = {"white_v_min": v_var, "white_s_max": s_var, "shadow_tolerance": t_var}

            ttk.Label(win, text=f"{mode}").grid(row=1 + mode, column=0, sticky="w", padx=10, pady=3)
            ttk.Entry(win, textvariable=v_var, width=7).grid(row=1 + mode, column=1, padx=8, pady=3)
            ttk.Entry(win, textvariable=s_var, width=7).grid(row=1 + mode, column=2, padx=8, pady=3)
            ttk.Entry(win, textvariable=t_var, width=7).grid(row=1 + mode, column=3, padx=8, pady=3)

        def _save_shadow_profiles():
            new_profiles = {}
            try:
                for mode in ("1", "2", "3", "4"):
                    raw = vars_by_mode[mode]
                    white_v_min = max(0, min(255, int(raw["white_v_min"].get())))
                    white_s_max = max(0, min(255, int(raw["white_s_max"].get())))
                    shadow_tol = max(0, min(100, int(raw["shadow_tolerance"].get())))
                    new_profiles[mode] = {
                        "white_v_min": white_v_min,
                        "white_s_max": white_s_max,
                        "shadow_tolerance": shadow_tol,
                    }
            except Exception:
                messagebox.showerror("Помилка", "Вкажіть коректні числові значення.")
                return

            self.shadow_mode_profiles = new_profiles
            self.conf["shadow_mode_profiles"] = new_profiles
            save_config(self.conf)
            win.destroy()

        btns = ttk.Frame(win, style="Card.TFrame")
        btns.grid(row=6, column=0, columnspan=4, sticky="e", padx=10, pady=(8, 10))
        ttk.Button(btns, text="Скасувати", style="Secondary.TButton", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Зберегти", style="Primary.TButton", command=_save_shadow_profiles).pack(side="right", padx=(0, 6))

    def _build_launch_card(self, parent):
        """Card ④ – launch controls: threads, primary Run button, secondary actions."""
        card = ttk.LabelFrame(parent, text="④  Запуск",
                              style="Card.TLabelframe", padding=(10, 8))

        # Top row: threads selector + PRIMARY RUN/PAUSE button
        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Потоків:", style="Card.TLabel").pack(side="left")
        self.conc_var = tk.IntVar(value=self.conf.get("concurrency", 4))
        self.conc_combo = ttk.Combobox(top, textvariable=self.conc_var,
                                       values=[1, 2, 4, 8, 12, 16], width=4, state="readonly")
        self.conc_combo.pack(side="left", padx=(4, 12))
        ToolTip(self.conc_combo,
                "Кількість паралельних потоків для завантаження та обробки зображень. "
                "Більше потоків — швидша обробка, але вища навантаженість мережі та CPU. "
                "Рекомендовано: 4–8 для стандартних ПК.")

        # Dynamic button: «▶ Run» → «⏸ Pause» → «▶ Resume»
        self.run_pause_btn = ttk.Button(top, text="▶  ЗАПУСТИТИ ОБРОБКУ",
                                        command=self._dynamic_btn_click,
                                        style="Primary.TButton")
        self.run_pause_btn.pack(side="left", fill="x", expand=True)
        ToolTip(self.run_pause_btn,
                "Запустити обробку файлу. Під час роботи кнопка переключається "
                "між «Пауза» та «Продовжити».")

        # Bottom row: Stop + separator + utility buttons (secondary style)
        bot = ttk.Frame(card, style="Card.TFrame")
        bot.pack(fill="x")

        self.stop_btn = ttk.Button(bot, text="⬛ Стоп", command=self.stop_process,
                                   state="disabled", style="Secondary.TButton")
        self.stop_btn.pack(side="left", padx=(0, 4))
        ToolTip(self.stop_btn,
                "Зупинити поточну обробку. Вже оброблені результати будуть збережені.")

        ttk.Separator(bot, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)

        folder_btn = ttk.Button(bot, text="📁 Папка",
                                command=self.open_output_folder, style="Secondary.TButton")
        folder_btn.pack(side="left", padx=(0, 4))
        ToolTip(folder_btn,
                "Відкрити папку з результатами останньої обробки у провіднику файлів.")

        cache_btn = ttk.Button(bot, text="🗑 Очистити кеш",
                               command=self.clear_cache_clicked, style="Secondary.TButton")
        cache_btn.pack(side="left", padx=(0, 4))
        ToolTip(cache_btn,
                "Видалити локальний кеш завантажених зображень. "
                "Використовуйте, якщо зображення оновились і потрібно завантажити їх заново.")

        reagg_btn = ttk.Button(bot, text="🔄 Реагрегація",
                               command=self.update_status_clicked, style="Secondary.TButton")
        reagg_btn.pack(side="left")
        ToolTip(reagg_btn,
                "Перерахувати підсумковий файл статусів на основі відредагованого файлу деталей. "
                "Корисно, якщо ви вручну виправили статуси у файлі деталей і хочете оновити зведений звіт.")

        return card

    # ── Right panel ────────────────────────────────────────────────────────────

    def _build_right_panel(self, parent):
        """Right column: photo preview placeholder + progress bar + searchable log."""
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)   # log section expands vertically

        self._build_preview_card(frame).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._build_log_card(frame).grid(row=1, column=0, sticky="nsew")
        return frame

    def _build_preview_card(self, parent):
        """Photo preview card – shows the first photo of each product during processing."""
        card = ttk.LabelFrame(parent, text="Превʼю фото",
                              style="Card.TLabelframe", padding=(8, 6))

        self.preview_canvas = tk.Canvas(card, bg="#E8EFF5", height=185,
                                        highlightthickness=1,
                                        highlightbackground=_C_BORDER)
        self.preview_canvas.pack(fill="x")
        # Keep a reference to prevent garbage collection of the PhotoImage
        self._preview_photo = None

        def _draw_placeholder(event=None):
            if self._preview_photo is not None:
                # A real image is shown; re-centre it on resize
                self._redraw_preview_image()
                return
            c = self.preview_canvas
            c.delete("ph")
            w = c.winfo_width() or _PREVIEW_FALLBACK_W
            h = c.winfo_height() or _PREVIEW_FALLBACK_H
            c.create_text(w // 2, h // 2 - 14,
                          text="🖼", font=("Segoe UI", 26), fill="#B0BEC5", tags="ph")
            c.create_text(w // 2, h // 2 + 20,
                          text="Превʼю першого фото товару",
                          font=_FONT_SMALL, fill="#90A4AE", tags="ph")

        self.preview_canvas.bind("<Configure>", _draw_placeholder)
        self.after(100, _draw_placeholder)   # draw once layout has settled

        return card

    def _redraw_preview_image(self):
        """Re-centre the current preview image after a canvas resize."""
        c = self.preview_canvas
        c.delete("all")
        if self._preview_photo is None:
            return
        w = c.winfo_width() or _PREVIEW_FALLBACK_W
        h = c.winfo_height() or _PREVIEW_FALLBACK_H
        c.create_image(w // 2, h // 2, anchor="center",
                       image=self._preview_photo, tags="img")

    def update_preview(self, image_data: bytes):
        """Display image_data (raw bytes) in the preview canvas, scaled to fit."""
        try:
            with Image.open(BytesIO(image_data)) as raw:
                img = raw.convert("RGB")

            c = self.preview_canvas
            cw = c.winfo_width() or _PREVIEW_FALLBACK_W
            ch = c.winfo_height() or _PREVIEW_FALLBACK_H

            # Scale to fit canvas while preserving aspect ratio
            img.thumbnail((cw, ch), Image.LANCZOS)

            self._preview_photo = ImageTk.PhotoImage(img)
            self._redraw_preview_image()
        except Exception:
            pass  # Ignore any error (corrupt data, unusual format, memory, …)

    def _build_log_card(self, parent):
        """Progress bar + status label + live-searchable log text area."""
        card = ttk.LabelFrame(parent, text="Прогрес та лог",
                              style="Card.TLabelframe", padding=(8, 6))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)   # log text area expands

        # ── Progress row ──────────────────────────────────────────────────────
        prog = ttk.Frame(card, style="Card.TFrame")
        prog.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog, orient="horizontal", mode="determinate",
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label_var = tk.StringVar(value="Очікування…")
        ttk.Label(prog, textvariable=self.progress_label_var,
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))

        # ── Log search row ────────────────────────────────────────────────────
        srow = ttk.Frame(card, style="Card.TFrame")
        srow.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        ttk.Label(srow, text="🔍", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self._log_search_var = tk.StringVar()
        se = ttk.Entry(srow, textvariable=self._log_search_var)
        se.pack(side="left", fill="x", expand=True)
        ToolTip(se, "Пошук у лозі — підсвічує всі збіги в реальному часі.")

        # Explicit paste binding so Ctrl+V and right-click → Paste always work
        def _paste_to_search(event=None):
            try:
                text = se.clipboard_get()
                se.insert("insert", text)
            except tk.TclError:
                pass
            return "break"

        def _show_search_context_menu(event):
            menu = tk.Menu(se, tearoff=0)
            menu.add_command(label="Вставити", command=_paste_to_search)
            menu.add_command(label="Копіювати", command=lambda: se.event_generate("<<Copy>>"))
            menu.add_command(label="Вирізати", command=lambda: se.event_generate("<<Cut>>"))
            menu.add_separator()
            menu.add_command(label="Виділити все",
                             command=lambda: se.select_range(0, "end"))
            menu.tk_popup(event.x_root, event.y_root)

        se.bind("<Control-v>", _paste_to_search)
        se.bind("<Control-V>", _paste_to_search)
        se.bind("<Button-3>", _show_search_context_menu)
        clr_btn = ttk.Button(srow, text="✕", width=2,
                             command=lambda: self._log_search_var.set(""),
                             style="Secondary.TButton")
        clr_btn.pack(side="left", padx=(4, 0))
        ToolTip(clr_btn, "Очистити пошуковий рядок.")

        # ── Log text ──────────────────────────────────────────────────────────
        logf = ttk.Frame(card, style="Card.TFrame")
        logf.grid(row=2, column=0, sticky="nsew")
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log_text = tk.Text(logf, wrap="word", state="disabled",
                                bg=_C_LOG_BG, fg=_C_LOG_FG, font=_FONT_LOG,
                                relief="flat", padx=6, pady=4,
                                selectbackground=_C_PRIMARY,
                                selectforeground=_C_PRIMARY_FG)
        self.log_text.tag_configure("search_hl", background=_C_SEARCH_HL, foreground=_C_TEXT)
        scy = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scy.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scy.grid(row=0, column=1, sticky="ns")

        # Live search: re-highlight on every keystroke
        def _on_search(*_):
            self.log_text.tag_remove("search_hl", "1.0", "end")
            q = self._log_search_var.get().strip()
            if not q:
                return
            idx = "1.0"
            while True:
                pos = self.log_text.search(q, idx, stopindex="end", nocase=True)
                if not pos:
                    break
                end = f"{pos}+{len(q)}c"
                self.log_text.tag_add("search_hl", pos, end)
                idx = end

        self._log_search_var.trace_add("write", _on_search)
        return card
    
    def on_column_selected(self, event):
        val = self.col_combo.get()
        self.conf["last_manual_column"] = val
        save_config(self.conf)

    def browse_file(self):
        p = filedialog.askopenfilename(
            filetypes=[
                ("Підтримувані файли", "*.xlsx *.xls *.csv"),
                ("Excel Files", "*.xlsx *.xls"),
                ("CSV Files", "*.csv"),
            ]
        )
        if not p:
            return
        self.file_path_var.set(p)
        try:
            ext = os.path.splitext(p)[1].lower()
            if ext == ".csv":
                df = pd.read_csv(p, nrows=5, dtype=str)
            else:
                df = pd.read_excel(p, engine="openpyxl", nrows=5)
            cols = list(df.columns)
            self.col_combo['values'] = cols
            best_col = self.conf.get("last_manual_column", "")
            if best_col not in cols:
                best_col = self._detect_url_column(df, cols)
            if best_col:
                self.col_combo.set(best_col)
                self.on_column_selected(None)
            else:
                self.col_combo.set(cols[0])
        except Exception as e:
            messagebox.showerror("Помилка", f"Не вдалося прочитати файл: {e}")

    @staticmethod
    def _detect_url_column(df, cols):
        """Return the column with the most URL/image-link values."""
        import re
        url_re = re.compile(r'https?://\S+\.(?:jpg|jpeg|png|webp|gif|bmp|tiff?)(\?[^\s]*)?', re.IGNORECASE)
        best_col = ""
        best_count = 0
        for col in cols:
            count = df[col].astype(str).apply(lambda v: bool(url_re.search(v))).sum()
            if count > best_count:
                best_count = count
                best_col = col
        return best_col

    def collect_settings(self):
        current_mode = int(self.shadow_mode.get())
        current_profile = self.shadow_mode_profiles.get(str(current_mode), {})
        fallback_shadow = int(current_profile.get("shadow_tolerance", 50))
        return {
            "good": {"width": self.good_w.get(), "height": self.good_h.get(), "sharpness": self.good_s.get()},
            "bad": {"width": self.bad_w.get(), "height": self.bad_h.get(), "sharpness": self.bad_s.get()},
            "bad_logic_operator": self.bad_logic_op.get(),
            "good_logic_operator": self.good_logic_op.get(),
            "concurrency": self.conc_var.get(),
            "last_manual_column": self.col_combo.get(),
            "border_ratio": self.border_r.get(),
            "shadow_threshold": fallback_shadow,
            "shadow_mode": current_mode,
            "shadow_mode_profiles": self.shadow_mode_profiles,
            "options": {
                "check_logos": self.opt_logos.get(),
                "check_rus_text": self.opt_rus_text.get(),
                "check_shadows": self.opt_shadows.get(),
                "check_qr_url": self.opt_qr_url.get(),
                "check_phone_numbers": self.opt_phone_numbers.get(),
                "check_watermarks": self.opt_watermark.get(),
                "check_borders": self.opt_borders.get(),
                "check_1px_border": self.opt_1px.get(),
            }
        }

    def _dynamic_btn_click(self):
        """Обробник єдиної динамічної кнопки.

        IDLE → запускає обробку.
        RUNNING → ставить на паузу.
        PAUSED → відновлює обробку.
        """
        if self._proc_state == "idle":
            self.run_process_thread()
        else:
            self.toggle_pause()

    def run_process_thread(self):
        input_path = self.file_path_var.get().strip()
        if not input_path or not os.path.exists(input_path):
            messagebox.showerror("Error", "Файл не знайдено.")
            return
        current_conf = self.collect_settings()
        self.conf.update(current_conf)
        save_config(self.conf)
        self.stop_event.clear()
        self.set_controls_running(True)
        self.append_log("=== ЗАПУСК ОБРОБКИ ===")
        manual_col = self.col_combo.get()
        self.processing_thread = threading.Thread(
            target=self.thread_target,
            args=(input_path, current_conf, manual_col),
            daemon=True,
        )
        self.processing_thread.start()

    def thread_target(self, path, conf, col):
        """Запускає asyncio event loop у фоновому потоці."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # asyncio.Event для паузи/відновлення без блокування GUI
        pause_event = asyncio.Event()
        pause_event.set()  # Встановлено = не на паузі

        self._async_loop = loop
        self._async_pause_event = pause_event

        try:
            res = loop.run_until_complete(
                process_file(path, conf, self.gui_callback, col, pause_event, self.stop_event)
            )
            self.after(0, self.on_finished, res)
        except Exception as e:
            self.after(0, self.on_finished, {"error": f"Crash: {str(e)}"})
        finally:
            loop.close()
            self._async_loop = None
            self._async_pause_event = None

    def gui_callback(self, msg):
        if isinstance(msg, tuple) and msg[0] == "preview_image":
            # Coalesce preview updates: never flood tkinter queue with image blobs.
            with self._preview_lock:
                self._latest_preview_data = msg[1]
                if self._preview_update_scheduled:
                    return
                self._preview_update_scheduled = True
            self.after(0, self._flush_preview_update)
            return
        self.after(0, self.append_log, msg)

    def _flush_preview_update(self):
        now = time.monotonic()
        elapsed = now - self._last_preview_update_ts
        if elapsed < self._preview_min_interval:
            self.after(max(1, int((self._preview_min_interval - elapsed) * 1000)), self._flush_preview_update)
            return

        with self._preview_lock:
            data = self._latest_preview_data
            self._latest_preview_data = None

        if data is not None:
            self.update_preview(data)
            self._last_preview_update_ts = now

        with self._preview_lock:
            if self._latest_preview_data is None:
                self._preview_update_scheduled = False
                return

        self.after(max(_PREVIEW_MIN_RESCHEDULE_MS, int(self._preview_min_interval * 1000 // 2)), self._flush_preview_update)

    def append_log(self, msg):
        if isinstance(msg, tuple):
            cmd, val = msg
            if cmd == "progress_max":
                self.progress["maximum"] = val
                self.progress["value"] = 0
                self.progress_label_var.set(f"0 / {val} | Очікування...")
            elif cmd == "progress_update":
                # val = (processed_count, total_count, eta_seconds)
                done, total, eta_sec = val
                self.progress["value"] = done
                eta_str = format_duration(eta_sec)
                self.progress_label_var.set(f"{done} / {total} | ETA: {eta_str}")
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(msg) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_finished(self, result):
        self.set_controls_running(False)
        if "error" in result:
            messagebox.showerror("Помилка", result["error"])
        elif "stopped" in result:
            messagebox.showwarning("Стоп", f"Зупинено.\n{result.get('with_status')}")
            self.last_out = result.get('with_status')
        else:
            messagebox.showinfo("Успіх", f"Готово!\n{result.get('with_status')}")
            self.last_out = result.get('with_status')

    def set_controls_running(self, is_run):
        """Оновлює стан кнопок відповідно до поточного режиму."""
        if is_run:
            self._proc_state = "running"
            self.run_pause_btn.config(text="⏸  ПАУЗА")
            self.stop_btn.config(state="normal")
        else:
            self._proc_state = "idle"
            self.run_pause_btn.config(text="▶  ЗАПУСТИТИ ОБРОБКУ")
            self.stop_btn.config(state="disabled")

    def toggle_pause(self):
        """Перемикає між паузою та відновленням через asyncio.Event."""
        loop = self._async_loop
        pause_ev = self._async_pause_event

        if loop is None or pause_ev is None:
            return

        if self._proc_state == "running":
            # Ставимо на паузу — знімаємо asyncio.Event
            loop.call_soon_threadsafe(pause_ev.clear)
            self._proc_state = "paused"
            self.run_pause_btn.config(text="▶  ПРОДОВЖИТИ")
            self.append_log("⏸ PAUSED")
        elif self._proc_state == "paused":
            # Відновлюємо — встановлюємо asyncio.Event
            loop.call_soon_threadsafe(pause_ev.set)
            self._proc_state = "running"
            self.run_pause_btn.config(text="⏸  ПАУЗА")
            self.append_log("▶ RESUMED")

    def stop_process(self):
        if messagebox.askyesno("Stop", "Зупинити?"):
            self.stop_event.set()
            # Відновлюємо asyncio.Event, щоб завдання не зависли на паузі
            loop = self._async_loop
            pause_ev = self._async_pause_event
            if loop and pause_ev:
                loop.call_soon_threadsafe(pause_ev.set)

    def open_output_folder(self):
        if hasattr(self, 'last_out') and self.last_out: os.startfile(os.path.dirname(self.last_out))
        else:
            f = self.file_path_var.get()
            if f and os.path.exists(os.path.dirname(f)): os.startfile(os.path.dirname(f))

    def clear_cache_clicked(self):
        if clear_cache_dir()[0]: messagebox.showinfo("Info", "Кеш очищено")
        else: messagebox.showerror("Error", "Помилка очистки")

    def update_status_clicked(self):
        # 1. Просимо користувача вказати файл деталей
        file_path = filedialog.askopenfilename(
            title="Оберіть відредагований файл ДЕТАЛЕЙ",
            filetypes=[("Excel Files", "*_Деталі*.xlsx")]
        )
        if not file_path: return

        if messagebox.askyesno("Підтвердження", f"Це оновить файл 'Статус' на основі:\n{os.path.basename(file_path)}\n\nПродовжити?"):
            self.append_log(f"--- Оновлення статусів з: {os.path.basename(file_path)} ---")
            
            # Запускаємо в окремому потоці, щоб не вішати GUI (хоча це швидко)
            threading.Thread(target=self.run_update_logic, args=(file_path,), daemon=True).start()

    def run_update_logic(self, file_path):
        from processing_engine import regenerate_status_from_details # Імпорт тут або зверху
        
        res = regenerate_status_from_details(file_path)
        
        if "error" in res:
            self.gui_callback(f"❌ Помилка: {res['error']}")
            messagebox.showerror("Помилка", res['error'])
        else:
            warning = f"\n⚠️ {res['warning']}" if res.get("warning") else ""
            msg = f"✅ Готово! Оновлено записів: {res['count']}.\nФайл: {res['path']}{warning}"
            self.gui_callback(msg)
            messagebox.showinfo("Успіх", msg)

if __name__ == "__main__":
    app = PhotoQualityGUI()
    app.mainloop()
