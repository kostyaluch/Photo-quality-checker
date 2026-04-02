# main_app.py
import asyncio
import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
from utils import load_config, save_config, clear_cache_dir, ensure_cache_dir, DEFAULT_CONFIG, format_duration
from processing_engine import process_file

class ToolTip(object):
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
                         background="#fffbe6", foreground="#333333",
                         relief='solid', borderwidth=1,
                         font=('Helvetica', 9),
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
        self.geometry("800x850")
        self.resizable(True, True)
        self.conf = load_config()
        ensure_cache_dir()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.processing_thread = None
        # Стан обробки: 'idle' | 'running' | 'paused'
        self._proc_state = "idle"
        # Посилання на asyncio-цикл та asyncio.Event (задаються у thread_target)
        self._async_loop = None
        self._async_pause_event = None
        self.create_widgets()

    def create_widgets(self):
        style = ttk.Style()
        style.configure("Bold.TCheckbutton", font=('Helvetica', 10, 'bold'))
        style.configure("Header.TLabel", font=('Helvetica', 9, 'bold'))

        # --- 1. Джерело ---
        top_frame = ttk.LabelFrame(self, text="1. Джерело даних")
        top_frame.pack(fill="x", padx=10, pady=5)
        f_inner = ttk.Frame(top_frame)
        f_inner.pack(fill="x", padx=5, pady=5)
        ttk.Label(f_inner, text="Excel файл:").pack(side="left")
        self.file_path_var = tk.StringVar()
        file_entry = ttk.Entry(f_inner, textvariable=self.file_path_var)
        file_entry.pack(side="left", fill="x", expand=True, padx=5)
        ToolTip(file_entry, "Шлях до Excel (.xlsx/.xls) або CSV-файлу з даними про товари. "
                            "Можна ввести вручну або натиснути «Огляд…».")
        browse_btn = ttk.Button(f_inner, text="Огляд...", command=self.browse_file)
        browse_btn.pack(side="left")
        ToolTip(browse_btn, "Відкрити діалог вибору файлу Excel або CSV.")
        c_inner = ttk.Frame(top_frame)
        c_inner.pack(fill="x", padx=5, pady=5)
        ttk.Label(c_inner, text="Колонка з посиланнями:").pack(side="left")
        self.col_combo = ttk.Combobox(c_inner, values=[], width=40, state="readonly")
        self.col_combo.pack(side="left", padx=5)
        self.col_combo.bind("<<ComboboxSelected>>", self.on_column_selected)
        ToolTip(self.col_combo, "Оберіть колонку, яка містить URL-адреси або шляхи до зображень. "
                                "Програма автоматично визначає найбільш відповідну колонку після завантаження файлу.")
        self.col_status_var = tk.StringVar(value="(Оберіть файл)")
        ttk.Label(c_inner, textvariable=self.col_status_var, foreground="gray").pack(side="left", padx=10)

        # --- 2. Метрики ---
        metrics_frame = ttk.LabelFrame(self, text="2. Вимоги до якості")
        metrics_frame.pack(fill="x", padx=10, pady=5)
        grid_f = ttk.Frame(metrics_frame)
        grid_f.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(grid_f, text="Параметр", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        good_lbl = ttk.Label(grid_f, text="Хороше ✔", style="Header.TLabel", foreground="green")
        good_lbl.grid(row=0, column=1, padx=10)
        ToolTip(good_lbl, "Мінімальне значення, яке вважається прийнятним. "
                          "Зображення з показниками ≥ цього порогу отримають позначку «Добре».")
        ttk.Label(grid_f, text="Логіка з'єднання:", font=("Arial", 8, "italic")).grid(row=0, column=2)
        bad_lbl = ttk.Label(grid_f, text="Погане ✘", style="Header.TLabel", foreground="red")
        bad_lbl.grid(row=0, column=3, padx=10)
        ToolTip(bad_lbl, "Максимальне значення, нижче якого зображення вважається поганим. "
                         "Зображення з показниками ≤ цього порогу отримають позначку «Погано».")

        width_lbl = ttk.Label(grid_f, text="Ширина (px):")
        width_lbl.grid(row=1, column=0, sticky="w", pady=2)
        ToolTip(width_lbl, "Горизонтальний розмір зображення у пікселях.")
        self.good_w = tk.IntVar(value=self.conf["good"]["width"])
        good_w_entry = ttk.Entry(grid_f, textvariable=self.good_w, width=8)
        good_w_entry.grid(row=1, column=1)
        ToolTip(good_w_entry, "Мінімальна ширина (px) для «хорошого» зображення. Наприклад: 800.")
        self.bad_w = tk.IntVar(value=self.conf["bad"]["width"])
        bad_w_entry = ttk.Entry(grid_f, textvariable=self.bad_w, width=8)
        bad_w_entry.grid(row=1, column=3)
        ToolTip(bad_w_entry, "Максимальна ширина (px) для «поганого» зображення. Наприклад: 400.")

        self.good_logic_op = tk.StringVar(value=self.conf.get("good_logic_operator", "АБО"))
        op_cb_good = ttk.Combobox(grid_f, textvariable=self.good_logic_op, values=["І", "АБО"], width=5, state="readonly")
        op_cb_good.grid(row=2, column=1)
        ToolTip(op_cb_good, "«І» — зображення хороше, тільки якщо ширина І висота відповідають порогу.\n"
                            "«АБО» — достатньо, щоб хоча б один з параметрів відповідав.")
        
        ttk.Label(grid_f, text="<--- (Ширина ? Висота) --->", font=("Arial", 7), foreground="gray").grid(row=2, column=2)
        
        self.bad_logic_op = tk.StringVar(value=self.conf.get("bad_logic_operator", "І"))
        op_cb_bad = ttk.Combobox(grid_f, textvariable=self.bad_logic_op, values=["І", "АБО"], width=5, state="readonly")
        op_cb_bad.grid(row=2, column=3)
        ToolTip(op_cb_bad, "«І» — зображення погане, тільки якщо ширина І висота нижче порогу.\n"
                           "«АБО» — достатньо, щоб хоча б один параметр був нижче порогу.")

        height_lbl = ttk.Label(grid_f, text="Висота (px):")
        height_lbl.grid(row=3, column=0, sticky="w", pady=2)
        ToolTip(height_lbl, "Вертикальний розмір зображення у пікселях.")
        self.good_h = tk.IntVar(value=self.conf["good"]["height"])
        good_h_entry = ttk.Entry(grid_f, textvariable=self.good_h, width=8)
        good_h_entry.grid(row=3, column=1)
        ToolTip(good_h_entry, "Мінімальна висота (px) для «хорошого» зображення. Наприклад: 800.")
        self.bad_h = tk.IntVar(value=self.conf["bad"]["height"])
        bad_h_entry = ttk.Entry(grid_f, textvariable=self.bad_h, width=8)
        bad_h_entry.grid(row=3, column=3)
        ToolTip(bad_h_entry, "Максимальна висота (px) для «поганого» зображення. Наприклад: 400.")

        sharp_lbl = ttk.Label(grid_f, text="Різкість (Laplacian):")
        sharp_lbl.grid(row=4, column=0, sticky="w", pady=5)
        ToolTip(sharp_lbl, "Оцінка різкості за методом Лапласіана. "
                           "Чим вище значення — тим чіткіше зображення. "
                           "Розмиті фото мають низький показник (< 50–100).")
        self.good_s = tk.DoubleVar(value=self.conf["good"]["sharpness"])
        good_s_entry = ttk.Entry(grid_f, textvariable=self.good_s, width=8)
        good_s_entry.grid(row=4, column=1)
        ToolTip(good_s_entry, "Мінімальний показник різкості для «хорошого» фото. Наприклад: 80.")
        self.bad_s = tk.DoubleVar(value=self.conf["bad"]["sharpness"])
        bad_s_entry = ttk.Entry(grid_f, textvariable=self.bad_s, width=8)
        bad_s_entry.grid(row=4, column=3)
        ToolTip(bad_s_entry, "Максимальний показник різкості для «поганого» фото. Наприклад: 30.")

        # --- 3. Опції ---
        opts_frame = ttk.LabelFrame(self, text="3. Додаткові перевірки")
        opts_frame.pack(fill="x", padx=10, pady=5)
        opts = self.conf.get("options", DEFAULT_CONFIG["options"])

        # Row 0
        self.opt_shadows = tk.BooleanVar(value=opts.get("check_shadows", False))
        cb_shad = ttk.Checkbutton(opts_frame, text="Тіні / Брудний фон", variable=self.opt_shadows, style="Bold.TCheckbutton")
        cb_shad.grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ToolTip(cb_shad, "Виявляє фотографії з тінями або забрудненим фоном. "
                         "Аналізує рівномірність фону та наявність темних плям.")
        f_shad = ttk.Frame(opts_frame)
        f_shad.grid(row=0, column=1, sticky="w")
        ttk.Label(f_shad, text="Поріг:").pack(side="left")
        self.shadow_thresh = tk.DoubleVar(value=self.conf.get("shadow_threshold", 10.0))
        sc_shad = tk.Scale(f_shad, from_=5.0, to=30.0, resolution=1.0, orient="horizontal", variable=self.shadow_thresh, length=80)
        sc_shad.pack(side="left")
        ToolTip(sc_shad, "Чутливість виявлення тіней (5–30). "
                         "Менше значення — суворіша перевірка (більше спрацьовувань). "
                         "Більше значення — ігнорує незначні відхилення фону.")

        # Row 1
        self.opt_borders = tk.BooleanVar(value=opts.get("check_borders", True))
        cb_bord = ttk.Checkbutton(opts_frame, text="Некадровані (білі поля)", variable=self.opt_borders, style="Bold.TCheckbutton")
        cb_bord.grid(row=1, column=0, sticky="w", padx=10, pady=5)
        ToolTip(cb_bord, "Виявляє зображення, де товар не займає весь кадр і навколо є надмірні білі поля.")
        f_bord = ttk.Frame(opts_frame)
        f_bord.grid(row=1, column=1, sticky="w")
        ttk.Label(f_bord, text="Макс %:").pack(side="left")
        self.border_r = tk.DoubleVar(value=self.conf.get("border_ratio", 0.1))
        self.border_percent = tk.DoubleVar(value=self.border_r.get() * 100)
        def update_border_r(val): self.border_r.set(float(val) / 100.0)
        sc_bord = tk.Scale(f_bord, from_=1, to=50, orient="horizontal", variable=self.border_percent, command=update_border_r, length=80)
        sc_bord.pack(side="left")
        ToolTip(sc_bord, "Максимально допустима частка білих полів від розміру зображення (1–50%). "
                         "Наприклад, 10% означає, що поля не повинні перевищувати 10% ширини/висоти.")

        # Row 2
        self.opt_logos = tk.BooleanVar(value=opts.get("check_logos", False))
        cb_logos = ttk.Checkbutton(opts_frame, text="Логотипи Rozetka", variable=self.opt_logos, style="Bold.TCheckbutton")
        cb_logos.grid(row=2, column=0, sticky="w", padx=10, pady=5)
        ToolTip(cb_logos, "Виявляє логотипи або фірмові елементи Rozetka на зображенні. "
                          "Такі фото, як правило, заборонені маркетплейсами.")
        self.opt_watermark = tk.BooleanVar(value=opts.get("check_watermarks", False))
        cb_wm = ttk.Checkbutton(opts_frame, text="Водяні знаки", variable=self.opt_watermark, style="Bold.TCheckbutton")
        cb_wm.grid(row=2, column=1, sticky="w", padx=0, pady=5)
        ToolTip(cb_wm, "Виявляє будь-які напівпрозорі водяні знаки або текстові нашарування на фото.")

        # Row 3
        self.opt_rus_text = tk.BooleanVar(value=opts.get("check_rus_text", False))
        cb_rus = ttk.Checkbutton(opts_frame, text="Російський текст", variable=self.opt_rus_text, style="Bold.TCheckbutton")
        cb_rus.grid(row=3, column=0, sticky="w", padx=10, pady=5)
        ToolTip(cb_rus, "За допомогою OCR виявляє текст російською мовою на зображенні. "
                        "Увага: ця перевірка може уповільнити обробку.")
        self.opt_qr_url = tk.BooleanVar(value=opts.get("check_qr_url", False))
        cb_qr = ttk.Checkbutton(opts_frame, text="URL / QR коди", variable=self.opt_qr_url, style="Bold.TCheckbutton")
        cb_qr.grid(row=3, column=1, sticky="w", padx=0, pady=5)
        ToolTip(cb_qr, "Виявляє QR-коди або текстові URL-адреси на зображенні.")

        # Row 4
        self.opt_1px = tk.BooleanVar(value=opts.get("check_1px_border", False))
        cb_1px = ttk.Checkbutton(opts_frame, text="Тонка рамка (1px)", variable=self.opt_1px, style="Bold.TCheckbutton")
        cb_1px.grid(row=4, column=0, sticky="w", padx=10, pady=5)
        ToolTip(cb_1px, "Виявляє чорні/темні рамки товщиною 1-2 пікселі по самому краю фото.")

        # --- 4. Керування ---
        ctrl_frame = ttk.LabelFrame(self, text="4. Запуск")
        ctrl_frame.pack(fill="x", padx=10, pady=10)
        f_flows = ttk.Frame(ctrl_frame)
        f_flows.pack(side="left", padx=10)
        ttk.Label(f_flows, text="Потоків:").pack(side="left")
        self.conc_var = tk.IntVar(value=self.conf.get("concurrency", 4))
        self.conc_combo = ttk.Combobox(f_flows, textvariable=self.conc_var, values=[1, 2, 4, 8, 12, 16], width=3, state="readonly")
        self.conc_combo.pack(side="left", padx=5)
        ToolTip(self.conc_combo, "Кількість паралельних потоків для завантаження та обробки зображень. "
                                 "Більше потоків — швидша обробка, але вища навантаженість мережі та CPU. "
                                 "Рекомендовано: 4–8 для стандартних ПК.")

        f_btns = ttk.Frame(ctrl_frame)
        f_btns.pack(side="left", fill="x", expand=True)
        # Єдина динамічна кнопка: «▶ Запустити» / «⏸ Пауза» / «▶ Продовжити»
        self.run_pause_btn = ttk.Button(f_btns, text="▶ ЗАПУСТИТИ ОБРОБКУ", command=self._dynamic_btn_click)
        self.run_pause_btn.pack(side="left", padx=5, fill="x", expand=True)
        ToolTip(self.run_pause_btn, "Запустити обробку файлу. Під час роботи кнопка переключається між «Пауза» та «Продовжити».")
        self.stop_btn = ttk.Button(f_btns, text="Стоп", command=self.stop_process, state="disabled")
        self.stop_btn.pack(side="left", padx=5)
        ToolTip(self.stop_btn, "Зупинити поточну обробку. Вже оброблені результати будуть збережені.")

        f_utils = ttk.Frame(ctrl_frame)
        f_utils.pack(side="right", padx=10)
        folder_btn = ttk.Button(f_utils, text="📁 Папка", command=self.open_output_folder)
        folder_btn.pack(side="left", padx=2)
        ToolTip(folder_btn, "Відкрити папку з результатами останньої обробки у провіднику файлів.")
        cache_btn = ttk.Button(f_utils, text="Очистити кеш", command=self.clear_cache_clicked)
        cache_btn.pack(side="left", padx=2)
        ToolTip(cache_btn, "Видалити локальний кеш завантажених зображень. "
                           "Використовуйте, якщо зображення оновились і потрібно завантажити їх заново.")
        reaggregate_btn = ttk.Button(f_utils, text="🔄 Реагрегація файлу", command=self.update_status_clicked)
        reaggregate_btn.pack(side="left", padx=2)
        ToolTip(reaggregate_btn, "Перерахувати підсумковий файл статусів на основі відредагованого файлу деталей. "
                                 "Корисно, якщо ви вручну виправили статуси у файлі деталей і хочете оновити зведений звіт.")

        # Прогрес
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", padx=10)
        self.progress = ttk.Progressbar(prog_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=5)
        self.progress_label_var = tk.StringVar(value="Очікування...")
        ttk.Label(prog_frame, textvariable=self.progress_label_var).pack()

        # Лог
        log_frame = ttk.LabelFrame(self, text="Лог")
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=10)
        scrolly = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrolly.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrolly.pack(side="right", fill="y")
    
    def on_column_selected(self, event):
        val = self.col_combo.get()
        self.col_status_var.set(f"Обрано: {val}")
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
                for col in cols:
                    if any(x in str(col).lower() for x in ["img", "url", "photo", "link"]):
                        best_col = col; break
            if best_col:
                self.col_combo.set(best_col)
                self.on_column_selected(None)
            else:
                self.col_combo.set(cols[0])
        except Exception as e:
            messagebox.showerror("Помилка", f"Не вдалося прочитати файл: {e}")

    def collect_settings(self):
        return {
            "good": {"width": self.good_w.get(), "height": self.good_h.get(), "sharpness": self.good_s.get()},
            "bad": {"width": self.bad_w.get(), "height": self.bad_h.get(), "sharpness": self.bad_s.get()},
            "bad_logic_operator": self.bad_logic_op.get(),
            "good_logic_operator": self.good_logic_op.get(),
            "concurrency": self.conc_var.get(),
            "last_manual_column": self.col_combo.get(),
            "border_ratio": self.border_r.get(),
            "shadow_threshold": self.shadow_thresh.get(),
            "options": {
                "check_logos": self.opt_logos.get(),
                "check_rus_text": self.opt_rus_text.get(),
                "check_shadows": self.opt_shadows.get(),
                "check_qr_url": self.opt_qr_url.get(),
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
        self.after(0, self.append_log, msg)

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
            self.run_pause_btn.config(text="⏸ Пауза")
            self.stop_btn.config(state="normal")
        else:
            self._proc_state = "idle"
            self.run_pause_btn.config(text="▶ ЗАПУСТИТИ ОБРОБКУ")
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
            self.run_pause_btn.config(text="▶ Продовжити")
            self.append_log("⏸ PAUSED")
        elif self._proc_state == "paused":
            # Відновлюємо — встановлюємо asyncio.Event
            loop.call_soon_threadsafe(pause_ev.set)
            self._proc_state = "running"
            self.run_pause_btn.config(text="⏸ Пауза")
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
            msg = f"✅ Готово! Оновлено записів: {res['count']}.\nФайл: {res['path']}"
            self.gui_callback(msg)
            messagebox.showinfo("Успіх", "Файл статусів успішно оновлено!")

if __name__ == "__main__":
    app = PhotoQualityGUI()
    app.mainloop()