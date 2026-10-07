"""Загрузчик остатков Wildberries по складам.

Источник — выгрузка из ЛК WB «Остатки на складах» (.xlsx, лист Sheet1).
В файле даты НЕТ — дата остатков передаётся при загрузке (поле в UI),
как у загрузчика остатков склада АВТОПРОФИ.

Особенность формата: набор и порядок колонок складов ПЛАВАЮЩИЙ —
склады могут добавляться, переименовываться и переставляться. Поэтому:
  * фиксированные колонки ищем ПО НАЗВАНИЮ заголовка (не по индексу);
  * всё, что правее «Всего находится на складах», считаем складами
    (имя склада = заголовок колонки);
  * пишем в «длинную» модель (date, seller_article, warehouse_id, qty) —
    новый/переименованный/переставленный склад не требует правок кода.

Справочник складов wb_warehouses пополняется автоматически при первой
встрече нового имени.

Идемпотентность: повторная загрузка за ту же дату ПОЛНОСТЬЮ перезаписывает
данные этого дня (DELETE по date, затем INSERT).

Сопоставление артикулов со справочником catalog_items (по upper(seller_article)) —
пишем остатки только по товарам, которые есть в справочнике дашборда.
Файл читаем через python_calamine (openpyxl не открывает выгрузку WB —
в архиве нет sharedStrings.xml).
"""

import os
import datetime

from python_calamine import CalamineWorkbook

MP_NAME = "Wildberries"
PERIOD_KIND = "wb_stock"

# Фиксированные колонки (ищем по названию в шапке). Всё, что ПРАВЕЕ
# TOTAL_COL — склады. Порядок в этом списке не важен.
COL_BRAND = "Бренд"
COL_SUBJECT = "Предмет"
COL_SELLER_ART = "Артикул продавца"
COL_WB_ART = "Артикул WB"
COL_VOLUME = "Объем, л"
COL_IN_TRANSIT_CLIENT = "В пути до получателей"
COL_IN_TRANSIT_RETURN = "В пути возвраты на склад WB"
COL_TOTAL = "Всего находится на складах"

# Обязательные для валидности файла.
REQUIRED = [COL_SELLER_ART, COL_TOTAL]


def _to_num(v):
    """Число из ячейки; пусто/«-»/нечисло → None."""
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date_arg(date_str):
    """Разобрать дату остатков (YYYY-MM-DD из <input type=date> или ДД.ММ.ГГГГ)."""
    if not date_str:
        return None
    if isinstance(date_str, datetime.date):
        return date_str
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _journal(conn, path, *, status, message="", rows_loaded=0,
             period_start=None, period_end=None, period_text="",
             upload_id=None):
    """Создать/обновить запись в report_uploads (period_kind='wb_stock')."""
    try:
        with conn.cursor() as cur:
            if upload_id is None:
                cur.execute(
                    "INSERT INTO report_uploads (marketplace, year, period_text, "
                    "period_start, period_end, source_file, rows_loaded, status, "
                    "message, period_kind) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (MP_NAME,
                     (period_start.year if period_start else None),
                     period_text, period_start, period_end,
                     os.path.basename(path), rows_loaded, status,
                     (message or "")[:500], PERIOD_KIND))
                upload_id = cur.fetchone()[0]
            else:
                cur.execute(
                    "UPDATE report_uploads SET rows_loaded=%s, status=%s, "
                    "message=%s WHERE id=%s",
                    (rows_loaded, status, (message or "")[:500], upload_id))
        conn.commit()
        return upload_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return upload_id


def _read_sheet(path):
    """Прочитать первый лист как список списков (calamine)."""
    wb = CalamineWorkbook.from_path(path)
    sheet_names = wb.sheet_names
    sn = "Sheet1" if "Sheet1" in sheet_names else sheet_names[0]
    return wb.get_sheet_by_name(sn).to_python()


def _build_colidx(header):
    """{название заголовка -> индекс колонки}. Первое вхождение при дублях."""
    idx = {}
    for i, v in enumerate(header):
        if v is not None and str(v).strip():
            idx.setdefault(str(v).strip(), i)
    return idx


def _resolve_warehouses(cur, wh_names, seen_date):
    """Вернуть {имя_склада -> warehouse_id}, создавая недостающие.

    Новый склад заносится в wb_warehouses (first_seen_date = дата загрузки).
    Сопоставление по точному имени заголовка.
    """
    mapping = {}
    if not wh_names:
        return mapping
    cur.execute("SELECT id, name FROM wb_warehouses")
    existing = {name: wid for (wid, name) in cur.fetchall()}
    new_names = []
    for name in wh_names:
        if name in existing:
            mapping[name] = existing[name]
        else:
            new_names.append(name)
    for name in new_names:
        cur.execute(
            "INSERT INTO wb_warehouses (name, first_seen_date, is_active) "
            "VALUES (%s,%s,true) "
            "ON CONFLICT (name) DO UPDATE SET is_active=true "
            "RETURNING id",
            (name, seen_date))
        mapping[name] = cur.fetchone()[0]
    return mapping


def load_wb_stock(conn, path, stock_date=None):
    """Загрузить остатки WB по складам за указанную дату.

    conn — psycopg-соединение (без автокоммита).
    stock_date — дата остатков (обязательна; в файле её нет).

    Возвращает dict {ok, stock_date, rows_products, rows_stock_cells,
    warehouses_total, warehouses_new, skipped_not_in_catalog, file_total, ...}.

    Идемпотентно: сначала удаляет данные за дату из wb_stock_daily и
    wb_stock_meta, затем вставляет заново.
    """
    d = _parse_date_arg(stock_date)
    if d is None:
        msg = ("Не указана дата остатков. Выберите дату, за которую "
               "загружаются остатки складов Wildberries.")
        _journal(conn, path, status="ОШИБКА", message=msg)
        return {"ok": False, "error": msg}

    period_text = d.strftime("%d.%m.%Y")

    # --- чтение файла ---
    try:
        rows = _read_sheet(path)
    except Exception as e:  # noqa: BLE001
        msg = f"Не удалось прочитать файл: {e}"
        _journal(conn, path, status="ОШИБКА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    if not rows or len(rows) < 2:
        msg = "Файл пуст или нет строк данных."
        _journal(conn, path, status="ОШИБКА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    header = rows[0]
    idx = _build_colidx(header)
    missing = [name for name in REQUIRED if name not in idx]
    if missing:
        msg = ("ОШИБКА ФОРМАТА: не найдены обязательные колонки: "
               + ", ".join(missing))
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    c_seller = idx[COL_SELLER_ART]
    c_total = idx[COL_TOTAL]
    c_wb = idx.get(COL_WB_ART)
    c_vol = idx.get(COL_VOLUME)
    c_tr_client = idx.get(COL_IN_TRANSIT_CLIENT)
    c_tr_return = idx.get(COL_IN_TRANSIT_RETURN)

    # --- склады = все колонки правее «Всего находится на складах» ---
    wh_cols = []  # (col_index, warehouse_name)
    for i in range(c_total + 1, len(header)):
        name = header[i]
        if name is not None and str(name).strip():
            wh_cols.append((i, str(name).strip()))
    wh_names = [n for (_i, n) in wh_cols]

    # --- сбор строк товаров ---
    # meta: art_up -> (art_orig, wb_art, vol, tr_client, tr_return, total)
    # cells: art_up -> {wh_name: qty}
    meta = {}
    cells = {}
    file_total = 0
    for r in rows[1:]:
        if c_seller >= len(r):
            continue
        art_raw = r[c_seller]
        if art_raw is None or not str(art_raw).strip():
            continue
        file_total += 1
        art = str(art_raw).strip()
        au = art.upper()
        wb_art = None
        if c_wb is not None and c_wb < len(r) and r[c_wb] not in (None, ""):
            wb_art = str(r[c_wb]).strip()
        meta[au] = (
            art, wb_art,
            _to_num(r[c_vol]) if (c_vol is not None and c_vol < len(r)) else None,
            _to_num(r[c_tr_client]) if (c_tr_client is not None and c_tr_client < len(r)) else None,
            _to_num(r[c_tr_return]) if (c_tr_return is not None and c_tr_return < len(r)) else None,
            _to_num(r[c_total]) if c_total < len(r) else None,
        )
        wh_row = {}
        for (ci, wname) in wh_cols:
            if ci < len(r):
                q = _to_num(r[ci])
                if q is not None:
                    wh_row[wname] = q
        cells[au] = wh_row

    if not meta:
        msg = "В файле нет строк с артикулами."
        _journal(conn, path, status="ОШИБКА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    upload_id = _journal(conn, path, status="ЗАГРУЗКА",
                         period_start=d, period_end=d,
                         period_text=period_text)

    arts_new_list = []
    arts_new = 0
    try:
        with conn.cursor() as cur:
            # 1) справочник артикулов — АВТОЗАВОД новых (единообразно с Ozon).
            #    Артикулы из отчёта, которых нет в catalog_items, ЗАВОДИМ
            #    (seller_article + sample_name из файла) и учитываем их остатки
            #    в стоимостной оценке. Такие артикулы — «нераспределённые»
            #    (arts_new): их надо распределить по категориям в справочнике.
            #    РНП НЕ затрагивается: catalog_items — общий справочник товаров,
            #    stock_daily / stock_ap_qty здесь не участвуют.
            cur.execute("SELECT upper(seller_article) FROM catalog_items")
            catalog = {row[0] for row in cur.fetchall()}
            arts_new_list = [meta[au][0] for au in meta if au not in catalog]
            for art_new in arts_new_list:
                cur.execute(
                    "INSERT INTO catalog_items (seller_article, sample_name) "
                    "VALUES (%s, %s) ON CONFLICT (seller_article) DO NOTHING",
                    (art_new, art_new))
            # После автозавода в отчёт идут ВСЕ артикулы файла.
            arts_ok = list(meta.keys())
            arts_new = len(arts_new_list)
            skipped_not_in_catalog = 0  # больше не пропускаем — оставлено для совместимости ответа

            # 2) справочник складов — создаём недостающие
            wh_before = set()
            cur.execute("SELECT name FROM wb_warehouses")
            wh_before = {row[0] for row in cur.fetchall()}
            wh_map = _resolve_warehouses(cur, wh_names, d)
            warehouses_new = [n for n in wh_names if n not in wh_before]

            # 3) идемпотентность: удаляем данные за дату
            cur.execute("DELETE FROM wb_stock_daily WHERE date=%s", (d,))
            cur.execute("DELETE FROM wb_stock_meta WHERE date=%s", (d,))

            # 4) meta
            meta_rows = []
            for au in arts_ok:
                art, wb_art, vol, trc, trr, total = meta[au]
                meta_rows.append((d, art, wb_art, vol, trc, trr, total))
            if meta_rows:
                cur.executemany(
                    "INSERT INTO wb_stock_meta "
                    "(date, seller_article, wb_article, volume_l, "
                    " in_transit_to_client, in_transit_returns, total_on_wh) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    meta_rows)

            # 5) факты по складам (длинная модель)
            cell_rows = []
            for au in arts_ok:
                art = meta[au][0]
                for wname, q in cells.get(au, {}).items():
                    wid = wh_map.get(wname)
                    if wid is None:
                        continue
                    cell_rows.append((d, art, wid, q))
            if cell_rows:
                cur.executemany(
                    "INSERT INTO wb_stock_daily "
                    "(date, seller_article, warehouse_id, qty) "
                    "VALUES (%s,%s,%s,%s)",
                    cell_rows)

            rows_stock_cells = len(cell_rows)
            rows_products = len(meta_rows)

            msg = (f"Остатки WB за {period_text}: товаров {rows_products}, "
                   f"ячеек склад×товар {rows_stock_cells}, "
                   f"складов {len(wh_names)}")
            if warehouses_new:
                msg += f"; новых складов: {len(warehouses_new)} ({', '.join(warehouses_new)})"
            if arts_new:
                msg += f"; заведено новых артикулов (нераспределённые): {arts_new}"

            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (rows_products, msg[:500], upload_id))
        conn.commit()
    except Exception as e:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        emsg = f"Ошибка записи в БД: {e}"
        _journal(conn, path, status="ОШИБКА", message=emsg, upload_id=upload_id)
        return {"ok": False, "error": emsg}

    return {
        "ok": True,
        "stock_date": d.isoformat(),
        "period_text": period_text,
        "rows_products": rows_products,
        "rows_loaded": rows_products,
        "rows_stock_cells": rows_stock_cells,
        "warehouses_total": len(wh_names),
        "warehouses_new": warehouses_new,
        "skipped_not_in_catalog": skipped_not_in_catalog,
        "arts_new": arts_new,
        "arts_new_list": arts_new_list[:200],
        "file_total": file_total,
    }
