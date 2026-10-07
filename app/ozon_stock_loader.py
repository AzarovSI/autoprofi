# -*- coding: utf-8 -*-
"""Загрузчик ОБЩЕГО отчёта ОСТАТКОВ склада АВТОПРОФИ (МСК) из 1С.

Это ОБЩИЙ справочник остатков по всем товарам (Ozon и Wildberries),
единый источник для метрики «Остаток на складе АВТОПРОФИ, шт» и для
будущих отчётов. Данные пишутся в универсальную таблицу stock_daily
и дополнительно синхронизируются в ozon_daily_sales.stock_ap_qty
(чтобы существующая матрица «РНП продажи — OZON» работала без изменений).

Формат файла (выгрузка 1С: Настройки выгрузки остатков → Остатки МСК
(Автопрофи) → выбрать дату):
    • лист «TDSheet»;
    • строка 1 — заголовки: «Артикул», «Наименование», «Остаток»;
    • данные со 2-й строки. Ключ строки — Артикул.
    • ДАТЫ В ФАЙЛЕ НЕТ — она передаётся отдельным параметром при загрузке.

Что берём из отчёта:
    • «Остаток» → qty (stock_daily) и stock_ap_qty (ozon_daily_sales).
Столбец «Наименование» НЕ используется (справочник не трогаем).

Запись в stock_daily: UPSERT по ключу (date, upper(seller_article)) —
перезаписывает остаток за эту дату. Синхронизация в ozon_daily_sales:
UPDATE stock_ap_qty для уже существующих строк продаж за эту дату
(строки продаж НЕ создаёт).

Каждая загрузка фиксируется в report_uploads (marketplace='—',
period_kind='stock').
"""
import os
import io
import zipfile
import datetime

import openpyxl

MP_NAME = "—"  # общий отчёт, не привязан к одному маркетплейсу
SHEET_NAME = "TDSheet"
HEADER_ROW = 1  # строка заголовков

# Ключевые заголовки — проверка формата ПО ИМЕНИ, не по позиции.
STOCK_REQUIRED = ["Артикул", "Остаток"]


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


def _build_colidx(ws, header_row):
    idx = {}
    max_col = ws.max_column or 20
    for c in range(1, max_col + 1):
        v = ws.cell(header_row, c).value
        if v is not None and str(v).strip():
            idx.setdefault(str(v).strip(), c)
    return idx


def _journal(conn, path, *, status, message="", rows_loaded=0,
             period_start=None, period_end=None, year=None,
             period_text="", upload_id=None):
    """Создать/обновить запись в report_uploads (period_kind='stock')."""
    try:
        with conn.cursor() as cur:
            if upload_id is None:
                cur.execute(
                    "INSERT INTO report_uploads (marketplace, year, period_text, "
                    "period_start, period_end, source_file, rows_loaded, status, "
                    "message, period_kind) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (MP_NAME, year, period_text, period_start, period_end,
                     os.path.basename(path), rows_loaded, status,
                     (message or "")[:500], "stock"))
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


def _open_workbook(path):
    """Открыть xlsx; при необходимости починить zip выгрузки 1С.

    Выгрузка 1С часто содержит служебные файлы с неверным регистром
    имён (например xl/SharedStrings.xml вместо xl/sharedStrings.xml),
    из-за чего openpyxl падает с KeyError. Здесь мы пробуем открыть
    напрямую, а при ошибке — пересобираем архив в памяти,
    добавляя недостающие имена в нужном регистре.
    """
    try:
        return openpyxl.load_workbook(path, data_only=True)
    except KeyError:
        pass
    canon = {
        "xl/sharedstrings.xml": "xl/sharedStrings.xml",
        "xl/workbook.xml": "xl/workbook.xml",
        "xl/styles.xml": "xl/styles.xml",
    }
    with zipfile.ZipFile(path, "r") as zin:
        names = zin.namelist()
        lower_map = {n.lower(): n for n in names}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            written = set()
            for n in names:
                data = zin.read(n)
                zout.writestr(n, data)
                written.add(n)
            for low, canon_name in canon.items():
                if canon_name in written:
                    continue
                src = lower_map.get(low)
                if src is not None:
                    zout.writestr(canon_name, zin.read(src))
    buf.seek(0)
    return openpyxl.load_workbook(buf, data_only=True)


def _parse_date_arg(date_str):
    """Разобрать дату остатков, переданную при загрузке.

    Принимает YYYY-MM-DD (из <input type=date>) или ДД.ММ.ГГГГ.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def load_ozon_stock(conn, path, stock_date=None):
    """Загрузить общий отчёт остатков АВТОПРОФИ за указанную дату.

    conn — psycopg-соединение (без автокоммита).
    stock_date — дата остатков (str YYYY-MM-DD/ДД.ММ.ГГГГ или datetime.date);
    обязательна, т.к. в файле даты нет.

    Возвращает dict {ok, rows_loaded, updated, sku_total, stock_date, ...}.
    Пишет в stock_daily (UPSERT) и синхронизирует stock_ap_qty
    в ozon_daily_sales для существующих строк продаж за эту дату.
    """
    if isinstance(stock_date, datetime.date):
        d = stock_date
    else:
        d = _parse_date_arg(stock_date)
    if d is None:
        msg = ("Не указана дата остатков. Выберите дату, за которую "
               "загружаются остатки склада.")
        _journal(conn, path, status="ОШИБКА", message=msg)
        return {"ok": False, "error": msg}

    wb = _open_workbook(path)
    if SHEET_NAME in wb.sheetnames:
        ws = wb[SHEET_NAME]
    else:
        ws = wb[wb.sheetnames[0]]

    idx = _build_colidx(ws, header_row=HEADER_ROW)
    missing = [name for name in STOCK_REQUIRED if name not in idx]
    if missing:
        msg = ("ОШИБКА ФОРМАТА: не найдены колонки в строке 1: "
               + ", ".join(missing))
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}

    c_art = idx["Артикул"]
    c_qty = idx["Остаток"]

    # --- сбор артикул -> остаток (последнее значение при дублях) ---
    data = {}  # art_upper -> (art_original, qty)
    skipped_empty = 0
    max_row = ws.max_row or HEADER_ROW
    for r in range(HEADER_ROW + 1, max_row + 1):
        art_raw = ws.cell(r, c_art).value
        if art_raw is None or not str(art_raw).strip():
            skipped_empty += 1
            continue
        qty = _to_num(ws.cell(r, c_qty).value)
        art = str(art_raw).strip()
        data[art.upper()] = (art, qty)

    if not data:
        msg = "В файле нет ни одной валидной строки данных (пустой отчёт)."
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}

    period_text = d.strftime('%d.%m.%Y')
    year = d.year

    # --- журнал: запись заранее ---
    upload_id = _journal(conn, path, status="В ПРОЦЕССЕ", period_text=period_text,
                         period_start=d, period_end=d, year=year)

    try:
        with conn.cursor() as cur:
            # 0) Фильтр по ОБЩЕМУ СПРАВОЧНИКУ товаров дашборда
            #    (catalog_items — товары Ozon и WB без дублей). В файле
            #    1С товаров больше, чем представлено в дашборде, —
            #    пишем остатки ТОЛЬКО по тем, что есть в справочнике.
            cur.execute("SELECT upper(seller_article) FROM catalog_items")
            catalog = {row[0] for row in cur.fetchall()}
            filtered = {au: v for au, v in data.items() if au in catalog}
            skipped_not_in_catalog = len(data) - len(filtered)

            # 1) Перезапись дня в stock_daily. Пакетная вставка
            #    (executemany) — без этого на удалённой БД сотни
            #    отдельных INSERT упираются в таймаут шлюза (524).
            cur.execute("DELETE FROM stock_daily WHERE date=%s", (d,))
            rows = [(d, art_orig, qty) for (art_orig, qty) in filtered.values()]
            if rows:
                cur.executemany(
                    "INSERT INTO stock_daily (date, seller_article, qty) "
                    "VALUES (%s,%s,%s)", rows)
            inserted = len(rows)

            # 2) Синхронизация в ozon_daily_sales.stock_ap_qty для строк
            #    продаж за эту дату (матрица РНП Озон). Сначала
            #    сбрасываем остаток у ВСЕХ строк продаж за дату
            #    (общий отчёт — единый источник истины), затем одним
            #    UPDATE через VALUES-список проставляем актуальные.
            cur.execute(
                "UPDATE ozon_daily_sales SET stock_ap_qty=NULL WHERE date=%s",
                (d,))
            synced = 0
            if filtered:
                # быстрый массовый UPDATE ... FROM (VALUES ...)
                vals = []
                params = []
                for au, (art_orig, qty) in filtered.items():
                    vals.append("(%s,%s)")
                    params.extend([au, qty])
                sql = (
                    "UPDATE ozon_daily_sales AS o "
                    "SET stock_ap_qty = v.qty::numeric "
                    "FROM (VALUES " + ",".join(vals) +
                    ") AS v(art_up, qty) "
                    "WHERE o.date = %s AND upper(o.seller_article) = v.art_up")
                params.append(d)
                cur.execute(sql, params)
                synced = cur.rowcount

            msg = (f"Дата остатков {period_text}; в файле: {len(data)}; "
                   f"в справочнике дашборда: {inserted}; "
                   f"синхр. в РНП Озон: {synced}")
            if skipped_not_in_catalog:
                msg += f"; вне справочника (пропущено): {skipped_not_in_catalog}"
            if skipped_empty:
                msg += f"; пустых: {skipped_empty}"
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (inserted, msg[:500], upload_id))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        user_msg = "Не удалось сохранить отчёт остатков в базу."
        _journal(conn, path, status="ОШИБКА", message=str(exc),
                 upload_id=upload_id)
        return {"ok": False, "error": user_msg, "upload_id": upload_id}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "rows_loaded": inserted,
        "updated": inserted,
        "synced_ozon": synced,
        "sku_total": inserted,
        "file_total": len(data),
        "skipped_not_in_catalog": skipped_not_in_catalog,
        "stock_date": d.isoformat(),
        "dates": [d.isoformat()],
        "period_text": period_text,
        "skipped_empty": skipped_empty,
        "upload_id": upload_id,
    }
