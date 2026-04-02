# processing_engine.py
import os
import queue
import time
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

from utils import (
    extract_urls, create_session_with_retries, download_image_bytes, format_duration
)
from image_metrics import (
    pil_from_bytes, compute_sharpness_pil, detect_white_borders,
    detect_shadows_on_bg, analyze_text_content, detect_urls_from_text,
    detect_qr_codes, detect_watermark_advanced, analyze_and_classify_photo,
    detect_transparency_in_bytes, detect_ai_generated, detect_1px_border
)

def photo_worker_task(task_data, conf, session, pause_event, stop_event):
    pause_event.wait()
    if stop_event.is_set(): return None, None

    product_id = task_data["product_id"]
    url = task_data["url"]
    photo_index = task_data["photo_index"]
    options = conf.get("options", {})

    details = {
        "ID": product_id,
        "Фото": photo_index,
        "Посилання на фото": url,
        "Статус": "Погане", "Причина": "",
        "Ширина": 0, "Висота": 0, "Різкість": 0.0,
        "Debug Info": ""
    }

    if options.get("check_shadows"): details["Тіні на головному фото"] = "Ні"
    if options.get("check_borders"): details["Некадровані фото"] = "Ні"
    if options.get("check_logos"): details["З логотипом"] = "Ні"
    if options.get("check_watermarks"): details["Водяний знак"] = "Ні"
    if options.get("check_rus_text"): details["Російський текст"] = "Ні"
    if options.get("check_qr_url"): details["URL/QR"] = "Ні"
    if options.get("check_ai"): details["ШІ/Generative"] = "Ні"

    data, err = download_image_bytes(url, session)
    if data is None:
        details["Причина"] = "Не вдалося завантажити"
        details["Debug Info"] = str(err)
        return details, f"[ID:{product_id}] ⚠️ Помилка завантаження: {err}"

    metrics_results = {}
    is_transp, tr_reason = detect_transparency_in_bytes(data)
    if is_transp:
        metrics_results["is_transparent"] = True
        metrics_results["transparency_reason"] = tr_reason

    img = pil_from_bytes(data)
    del data 

    if img is None:
        details["Причина"] = "Файл пошкоджено/Не фото"
        return details, f"[ID:{product_id}] ⚠️ Файл не є зображенням"

    width, height = img.size
    sharpness = compute_sharpness_pil(img)
    details["Різкість"] = round(sharpness, 2)
    details["Ширина"] = width
    details["Висота"] = height

    important_log = []
    if is_transp: important_log.append("Прозорий фон")

    if options.get("check_shadows") and photo_index == 1:
        shadow_thresh = conf.get("shadow_threshold", 10.0)
        has_shadows, reason = detect_shadows_on_bg(img, shadow_std_dev_threshold=shadow_thresh)
        metrics_results["has_shadows"] = has_shadows
        metrics_results["shadows_reason"] = reason
        if has_shadows: 
            details["Тіні на головному фото"] = "Так"
            important_log.append(f"Тіні ({reason})")

    if options.get("check_borders"):
        border_ratio = conf.get("border_ratio", 0.1)
        has_borders, reason = detect_white_borders(img, border_ratio=border_ratio)
        metrics_results["has_white_borders"] = has_borders
        metrics_results["borders_reason"] = reason
        if has_borders: 
            details["Некадровані фото"] = "Так"
            important_log.append(f"Поля ({reason})")

    if options.get("check_1px_border"):
        has_1px, reason_1px = detect_1px_border(img)
        metrics_results["has_1px_border"] = has_1px
        metrics_results["1px_border_reason"] = reason_1px
        if has_1px:
            important_log.append("Рамка 1px")

    if options.get("check_ai"):
        is_ai, reason_ai = detect_ai_generated(img)
        metrics_results["is_ai_generated"] = is_ai
        metrics_results["ai_reason"] = reason_ai
        if is_ai:
            if "ШІ/Generative" in details: details["ШІ/Generative"] = "Так"
            important_log.append(f"AI ({reason_ai})")

    ocr_wm_text = None
    word_count = 0
    qr_urls_found = []

    check_text_needed = (
        options.get("check_rus_text") or 
        options.get("check_qr_url") or 
        options.get("check_logos") or 
        options.get("check_watermarks")
    )
    
    if check_text_needed:
        full_text, has_rus, is_rozetka_logo, ocr_wm_text, word_count = analyze_text_content(img)

        if options.get("check_logos") and is_rozetka_logo:
            metrics_results["has_logo"] = True
            details["З логотипом"] = "Так"
            important_log.append("Лого Rozetka")

        if options.get("check_rus_text") and has_rus:
            metrics_results["has_rus_text"] = True
            details["Російський текст"] = "Так"
            important_log.append("Рос. текст")

        if options.get("check_qr_url"):
            urls = detect_urls_from_text(full_text)
            if urls: qr_urls_found.extend(urls)
            
            has_qr, qr_data = detect_qr_codes(img)
            if has_qr: qr_urls_found.append(f"QR:{qr_data}")
            
            if qr_urls_found:
                metrics_results["has_qr_url"] = True
                metrics_results["qr_url_data"] = "; ".join(qr_urls_found)
                details["URL/QR"] = "Так"
                important_log.append(f"URL/QR ({len(qr_urls_found)})")

    if options.get("check_watermarks"):
        has_wm, reason = detect_watermark_advanced(img, ocr_wm_text=ocr_wm_text, word_count=word_count)
        metrics_results["has_watermark"] = has_wm
        metrics_results["watermark_reason"] = reason
        if has_wm: 
            details["Водяний знак"] = "Так"
            important_log.append("Watermark")

    status, reason_str, debug_str = analyze_and_classify_photo(
        width, height, sharpness, conf, metrics_results
    )
    details["Статус"] = status
    details["Причина"] = reason_str
    
    if metrics_results.get("qr_url_data") and "QR" not in debug_str:
        debug_str += f"; Found: {metrics_results['qr_url_data']}"

    details["Debug Info"] = debug_str.lstrip('; ')

    log_msg = None
    if status == "Погане" or important_log:
        reasons_short = ", ".join(important_log) if important_log else reason_str
        log_msg = f"[ID:{product_id}] ❌ {reasons_short}"

    del img
    return details, log_msg

def regenerate_status_from_details(details_path):
    """
    Оновлює файл статусів на основі відредагованого файлу деталей.
    """
    try:
        # 1. Визначаємо шляхи
        base_dir = os.path.dirname(details_path)
        filename = os.path.basename(details_path)
        
        if "_Деталі" in filename:
            status_filename = filename.replace("_Деталі", "_Статус")
        else:
            return {"error": "Файл не містить '_Деталі'. Неможливо знайти пару."}
            
        status_path = os.path.join(base_dir, status_filename)
        
        if not os.path.exists(status_path):
            return {"error": f"Не знайдено файл статусів: {status_filename}"}

        # 2. Читаємо файли (важливо dtype=str для ID, щоб не було розбіжностей типів)
        df_details = pd.read_excel(details_path, engine="openpyxl", dtype=str)
        df_status = pd.read_excel(status_path, engine="openpyxl", dtype=str)

        if "ID" not in df_details.columns or "ID" not in df_status.columns:
            return {"error": "Відсутня колонка 'ID' у файлах."}

        # Групуємо деталі для швидкого пошуку
        details_grouped = df_details.groupby("ID")

        # Списки для нових даних, які ми заповнимо
        new_statuses = []
        new_problems = []
        new_totals = []
        new_good = []
        new_bad = []
        new_mid = []

        # 3. Ітеруємося по головному списку товарів (зі Статусу)
        # Це гарантує, що ми оновимо кожен товар, навіть якщо його видалили з Деталей
        for pid in df_status["ID"]:
            
            if pid in details_grouped.groups:
                # --- ВАРІАНТ А: Товар є в Деталях (частково або повністю) ---
                grp = details_grouped.get_group(pid)
                s_list = grp["Статус"].tolist()
                
                # Визначаємо новий статус на основі того, що ЗАЛИШИЛОСЯ
                if "Погане" in s_list:
                    fin_status = "Погане"
                elif "Середнє" in s_list:
                    fin_status = "Середнє"
                else:
                    fin_status = "Хороше" # Якщо лишились тільки хороші
                
                # Збираємо текст помилок тільки з наявних рядків
                probs = []
                for _, r in grp.iterrows():
                    st = r.get("Статус", "")
                    reason = r.get("Причина", "")
                    photo_idx = r.get("Фото", "?")
                    
                    if st != "Хороше" and pd.notna(reason) and reason:
                        probs.append(f"{photo_idx} ({reason})")
                
                new_statuses.append(fin_status)
                new_problems.append("; ".join(probs))
                new_totals.append(len(s_list))
                new_good.append(s_list.count("Хороше"))
                new_bad.append(s_list.count("Погане"))
                new_mid.append(s_list.count("Середнє"))
            
            else:
                # --- ВАРІАНТ Б: Товар повністю видалено з Деталей ---
                # Логіка користувача: якщо видалив - значить проблем немає -> Хороше
                new_statuses.append("Хороше")
                new_problems.append("") # Помилок немає
                
                # Питання: скільки писати "Всього фото"? 
                # Логічно поставити 0 або залишити як було, але щоб показати, 
                # що перевірка "чиста", ставимо по нулях, або 1 (формально).
                # Але найчесніше: у звіті деталей 0 записів.
                new_totals.append(0)
                new_good.append(0) 
                new_bad.append(0)
                new_mid.append(0)

        # 4. Записуємо нові дані у DataFrame Статусу
        df_status["Статус"] = new_statuses
        df_status["Проблемні фото"] = new_problems
        df_status["Всього фото"] = new_totals
        df_status["К-ть Хороших"] = new_good
        df_status["К-ть Поганих"] = new_bad
        df_status["К-ть Середніх"] = new_mid

        # 5. Зберігаємо
        df_status.to_excel(status_path, index=False, engine="openpyxl")
        format_excel_header(status_path)

        return {"success": True, "path": status_path, "count": len(df_status)}

    except Exception as e:
        return {"error": f"Update Error: {str(e)}"}
        
def format_excel_header(path):
    try:
        wb = load_workbook(path)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            ws.row_dimensions[1].height = 50
            for cell in ws[1]:
                cell.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
                cell.font = Font(bold=True)
        wb.save(path)
    except Exception: pass

def process_file(input_path, conf, gui_callback, manual_url_column, pause_event, stop_event):
    def log(msg):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}")
        if gui_callback:
            try: gui_callback(f"[{ts}] {msg}")
            except: pass

    try:
        df = pd.read_excel(input_path, engine="openpyxl", dtype=str)
    except Exception as e:
        return {"error": f"Open Error: {e}"}

    if df.shape[0] == 0: return {"error": "Файл порожній"}

    id_col = df.columns[0]
    best_col = None
    if manual_url_column and manual_url_column in df.columns:
        best_col = manual_url_column
    else:
        log("Авто-пошук колонки з URL...")
        candidates = []
        for col in df.columns:
            if any(x in str(col).lower() for x in ["img", "url", "photo", "link", "foto"]):
                candidates.append(col)
        if candidates: best_col = candidates[0]
        else: best_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

    log(f"Колонка: '{best_col}'")

    task_queue = queue.Queue()
    total_photos = 0
    product_photo_map = {}

    for idx, row in df.iterrows():
        product_id = str(row.get(id_col, "")).strip()
        if not product_id: product_id = f"ROW_{idx + 2}"

        cell = row.get(best_col, "")
        urls = extract_urls(cell)

        product_photo_map[product_id] = []
        if not urls:
            task = {"product_id": product_id, "url": "", "photo_index": 1, "is_empty": True}
            task_queue.put(task)
            total_photos += 1
            product_photo_map[product_id].append(1)
        else:
            for i, url in enumerate(urls[:100], start=1):
                task = {"product_id": product_id, "url": url, "photo_index": i, "is_empty": False}
                task_queue.put(task)
                total_photos += 1
                product_photo_map[product_id].append(i)

    if gui_callback: gui_callback(("progress_max", total_photos))
    concurrency = int(conf.get("concurrency", 4))
    all_results = []
    stats = {"Good": 0, "Bad": 0, "Medium": 0, "Error": 0}
    start_time = time.time()

    # Лічильник для ETA
    processed_count = 0

    try:
        with create_session_with_retries() as session:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {}
                while not task_queue.empty():
                    if stop_event.is_set(): break
                    task = task_queue.get()
                    if task["is_empty"]:
                        res = {
                            "ID": task["product_id"], "Фото": "", "Посилання на фото": "",
                            "Статус": "Погане", "Причина": "Немає фото",
                            "Debug Info": ""
                        }
                        all_results.append(res)
                        stats["Bad"] += 1
                        
                        # Оновлення прогресу для пустих завдань
                        processed_count += 1
                        if gui_callback:
                             # Передаємо processed_count, total_photos, eta_seconds
                            gui_callback(("progress_update", (processed_count, total_photos, 0)))
                        continue

                    fut = executor.submit(photo_worker_task, task, conf, session, pause_event, stop_event)
                    futures[fut] = task["product_id"]

                for i, fut in enumerate(as_completed(futures), 1):
                    processed_count += 1
                    
                    # Розрахунок ETA
                    elapsed_now = time.time() - start_time
                    avg_time_per_item = elapsed_now / processed_count if processed_count > 0 else 0
                    remaining_items = total_photos - processed_count
                    eta_seconds = avg_time_per_item * remaining_items

                    try:
                        res, log_msg = fut.result()
                        if res: 
                            all_results.append(res)
                            st = res.get("Статус", "Error")
                            if st == "Хороше": stats["Good"] += 1
                            elif st == "Погане": stats["Bad"] += 1
                            elif st == "Середнє": stats["Medium"] += 1
                            else: stats["Error"] += 1
                            if log_msg: log(log_msg)
                            
                            if i % 50 == 0: 
                                log(f"--- {i}/{total_photos} ---")
                                gc.collect()
                    except Exception as e:
                        log(f"Err: {e}")
                        stats["Error"] += 1
                    
                    # Оновлення прогресу + ETA
                    if gui_callback: 
                        gui_callback(("progress_update", (processed_count, total_photos, eta_seconds)))
                    
                    if stop_event.is_set():
                        for f in futures:
                            if not f.done(): f.cancel()
                        break
    except Exception as e:
        return {"error": f"Crit: {e}"}

    if not all_results: return {"error": "No results."}

    log("Збереження...")
    try:
        details_df = pd.DataFrame(all_results)
        details_df['ID'] = pd.Categorical(details_df['ID'], categories=product_photo_map.keys(), ordered=True)
        details_df = details_df.sort_values(by=["ID", "Фото"])

        options = conf.get("options", {})
        cols = ["ID", "Фото", "Посилання на фото", "Статус", "Причина"]
        OPTIONAL_COLS_MAP = {
            "check_shadows": "Тіні на головному фото",
            "check_borders": "Некадровані фото",
            "check_logos": "З логотипом",
            "check_watermarks": "Водяний знак",
            "check_rus_text": "Російський текст",
            "check_qr_url": "URL/QR",
            "check_ai": "ШІ/Generative"
        }
        for key, col_name in OPTIONAL_COLS_MAP.items():
            if options.get(key): cols.append(col_name)
        cols.extend(["Ширина", "Висота", "Різкість", "Debug Info"])

        for c in cols:
            if c not in details_df.columns: details_df[c] = ""
        details_df = details_df[cols]

        df_out = df.copy()
        status_map, problems_map = {}, {}
        stats_data = []

        for pid, grp in details_df.groupby("ID", observed=True):
            s_list = grp["Статус"].tolist()
            fin = "Хороше"
            if "Погане" in s_list: fin = "Погане"
            elif "Середнє" in s_list: fin = "Середнє"
            status_map[pid] = fin
            probs = [f"{r['Фото']} ({r['Причина']})" for _, r in grp.iterrows() if r['Статус'] != "Хороше" and r['Причина']]
            problems_map[pid] = "; ".join(probs)
            stats_data.append({
                "ID": pid, "Всього фото": len(s_list),
                "К-ть Хороших": s_list.count("Хороше"),
                "К-ть Поганих": s_list.count("Погане"),
                "К-ть Середніх": s_list.count("Середнє")
            })

        stats_df = pd.DataFrame(stats_data).set_index("ID") if stats_data else pd.DataFrame()
        df_out["Статус"] = df_out[id_col].map(status_map).fillna("N/A")
        df_out["Проблемні фото"] = df_out[id_col].map(problems_map).fillna("")

        if not stats_df.empty:
            for c in ["Всього фото", "К-ть Хороших", "К-ть Поганих", "К-ть Середніх"]:
                df_out[c] = df_out[id_col].map(stats_df[c]).fillna(0)

        base_dir = os.path.dirname(input_path)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        suffix = "_PARTIAL" if stop_event.is_set() else ""
        p1 = os.path.join(base_dir, f"{base_name}_Деталі{suffix}.xlsx")
        p2 = os.path.join(base_dir, f"{base_name}_Статус{suffix}.xlsx")

        details_df.to_excel(p1, index=False, engine="openpyxl")
        df_out.to_excel(p2, index=False, engine="openpyxl")
        format_excel_header(p1)
        format_excel_header(p2)

        elapsed = time.time() - start_time
        # Використовуємо нове форматування часу
        elapsed_str = format_duration(elapsed)
        
        log("="*30)
        log(f"🏁 DONE in {elapsed_str}")
        log(f"✅ Ok: {stats['Good']} | ❌ Bad: {stats['Bad']} | ⚠️ Mid: {stats['Medium']}")
        log("="*30)
        
        res = {"details": p1, "with_status": p2}
        if stop_event.is_set(): res["stopped"] = True
        return res
    except Exception as e:
        return {"error": f"Save Error: {e}"}