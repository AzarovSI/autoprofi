# -*- coding: utf-8 -*-
"""Загрузчик ЕЖЕДНЕВНЫХ ОСТАТКОВ склада Яндекс.Маркет (FBY).

Источник — выгрузка Яндекс.Маркета «Остатки на складе»
(stocks_on_warehouses_*.xlsx). Заполняет метрику
«Остаток на складе Яндекс, шт» в отчёте «РНП заказы — Yandex»
через колонку ya_daily_sales.stock_ya_qty.

Формат файла (лист «Остатки на складе»):
    • строка 4 — заголовки; данные с 6-й строки;
    • колонки: A SSKU, B «Ваш SKU» (=seller_article), C SKU на Яндексе,
      D «Название товара», E Годный, F Резерв,
      G «ДОСТУПНО ДЛЯ ЗАКАЗА» (=остаток для метрики), …, P «Склад».
    • ДАТЫ В ФАЙЛЕ НЕТ — она передаётся отдельным параметром при загрузке.
    • Один SKU может встречаться НЕСКОЛЬКО раз (строка «Склад: все» +
      строки по складам). Остаток агрегируем СУММОЙ «Доступно для заказа»
      по одному seller_article.

Что делаем:
    • «Доступно для заказа» (G) → SUM по seller_article → stock_ya_qty.
    • Идемпотентность по дате: сначала обнуляем stock_ya_qty у всех строк
      продаж Яндекса за дату, затем массовым UPDATE проставляем актуальные.
    • Для товаров с остатком, у которых НЕТ строки продаж за дату (не было
      показов/заказов), заводим строку-ЗАГОТОВКУ (date, seller_article,
      item_name, ya_sku, stock_ya_qty) — остальные метрики NULL → фронт
      покажет «—». Так остаток виден по ВСЕМ товарам с остатком.
    • Новые артикулы автоматически добавляются в catalog_items
      (нераспределённые), как в остальных загрузчиках.

Каждая загрузка фиксируется в report_uploads
(marketplace='Yandex', period_kind='ya_stock').
"""
import os
import datetime

MP_NAME = "Yandex"
PERIOD_KIND = "ya_stock"
SHEET_NAME = "Остатки на складе"
HEADER_ROW = 4          # строка заголовков (1-based)
DATA_START_ROW = 6      # первая строка данных (1-based)

# Ключевые заголовки — проверка формата ПО ИМЕНИ, не по позиции.
COL_ARTICLE = "Ваш SKU"
COL_AVAIL = "Доступно для заказа"
COL_NAME = "Название товара"
COL_YASKU = "SKU на Яндексе"


def _to_num(v):
    """Число из ячейки; пусто/«-»/нечисло → None."""
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _canon(art):
    """Канон артикула: upper + схлопывание пробелов (как в остальных загрузчиках)."""
    if art is None:
        return None
    s = " ".join(str(art).strip().split())
    return s.upper() if s else None


def _parse_date_arg(date_str):
    """Дата остатков (в файле её нет) из строки/date."""
    if isinstance(date_str, datetime.date):
        return date_str
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Некорректная дата остатков: {date_str!r}")


def _read_rows(path):
    """Читает лист остатков. Сначала calamine (надёжнее для выгрузок МП),
    fallback — openpyxl. Возвращает (headers_map, list_of_rows) где
    headers_map: {название->индекс0}, rows: list[list значений]."""
    # 1) python_calamine
    try:
        from python_calamine import CalamineWorkbook
        wb = CalamineWorkbook.from_path(path)
        names = wb.sheet_names
        sn = SHEET_NAME if SHEET_NAME in names else names[0]
        data = wb.get_sheet_by_name(sn).to_python()
        headers = data[HEADER_ROW - 1] if len(data) >= HEADER_ROW else []
        rows = data[DATA_START_ROW - 1:] if len(data) >= DATA_START_ROW else []
        hmap = {}
        for i, h in enumerate(headers):
            if h is not None and str(h).strip():
                hmap[str(h).strip()] = i
        return hmap, rows
    except Exception:
        pass
    # 2) openpyxl fallback
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    names = wb.sheetnames
    sn = SHEET_NAME if SHEET_NAME in names else names[0]
    ws = wb[sn]
    all_rows = list(ws.iter_rows(values_only=True))
    headers = all_rows[HEADER_ROW - 1] if len(all_rows) >= HEADER_ROW else []
    rows = all_rows[DATA_START_ROW - 1:] if len(all_rows) >= DATA_START_ROW else []
    hmap = {}
    for i, h in enumerate(headers):
        if h is not None and str(h).strip():
            hmap[str(h).strip()] = i
    return hmap, rows


def _journal(conn, path, status, message=None, period_start=None,
             period_end=None, period_text=None, rows_loaded=None,
             upload_id=None):
    """Пишет/обновляет запись в report_uploads. Возвращает upload_id."""
    fname = os.path.basename(path)
    with conn.cursor() as cur:
        if upload_id is None:
            cur.execute(
                "INSERT INTO report_uploads (marketplace, year, period_text, "
                " period_start, period_end, source_file, rows_loaded, status, "
                " message, period_kind) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (MP_NAME,
                 (period_start.year if period_start else None),
                 period_text, period_start, period_end, fname,
                 rows_loaded, status, (message or "")[:500], PERIOD_KIND))
            new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
        else:
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status=%s, "
                " message=%s WHERE id=%s",
                (rows_loaded, status, (message or "")[:500], upload_id))
            conn.commit()
            return upload_id


def load_ya_stock(conn, path, stock_date=None):
    """Загрузка остатков склада Яндекс за дату stock_date.

    Возвращает dict {ok, rows_loaded, updated, inserted, sku_total,
    file_total, stock_date, period_text, upload_id, ...}.
    """
    d = _parse_date_arg(stock_date)
    period_text = d.strftime("%d.%m.%Y")

    try:
        hmap, rows = _read_rows(path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Не удалось прочитать файл: {e}"}

    # Проверка формата по обязательным заголовкам.
    for req in (COL_ARTICLE, COL_AVAIL):
        if req not in hmap:
            return {"ok": False,
                    "error": (f"В файле не найден столбец «{req}» "
                              f"(лист «{SHEET_NAME}», заголовки в строке "
                              f"{HEADER_ROW}).")}

    ia = hmap[COL_ARTICLE]
    iv = hmap[COL_AVAIL]
    iname = hmap.get(COL_NAME)
    iysku = hmap.get(COL_YASKU)

    # Агрегация «Доступно для заказа» СУММОЙ по seller_article.
    agg = {}          # canon -> сумма остатка (float)
    art_orig = {}     # canon -> оригинальный артикул
    art_name = {}     # canon -> название (первое непустое)
    art_ysku = {}     # canon -> ya_sku (первое непустое)
    file_rows = 0
    for row in rows:
        if row is None:
            continue
        art_raw = row[ia] if ia < len(row) else None
        canon = _canon(art_raw)
        if not canon:
            continue
        file_rows += 1
        art_orig.setdefault(canon, " ".join(str(art_raw).strip().split()))
        if iname is not None and iname < len(row):
            nm = row[iname]
            if nm and canon not in art_name:
                art_name[canon] = str(nm).strip()
        if iysku is not None and iysku < len(row):
            ys = row[iysku]
            if ys and canon not in art_ysku:
                art_ysku[canon] = str(ys).strip()
        val = _to_num(row[iv]) if iv < len(row) else None
        if val is not None:
            agg[canon] = agg.get(canon, 0.0) + val

    if not art_orig:
        msg = "В файле нет строк с артикулами."
        _journal(conn, path, status="ОШИБКА", message=msg,
                 period_start=d, period_end=d, period_text=period_text)
        return {"ok": False, "error": msg}

    sku_total = len(art_orig)
    # Итоговый остаток по каждому SKU (в т.ч. 0). Для метрики важны и нули
    # (товар есть, остаток 0), и заготовки для товаров с остатком>0.
    stock_by_canon = {c: agg.get(c, 0.0) for c in art_orig}

    upload_id = _journal(conn, path, status="ЗАГРУЗКА",
                         period_start=d, period_end=d,
                         period_text=period_text)

    try:
        with conn.cursor() as cur:
            # 1) справочник артикулов: автозаводим отсутствующие (нераспределённые).
            cur.execute("SELECT upper(seller_article) FROM catalog_items")
            catalog = {r[0] for r in cur.fetchall()}
            new_rows = []
            for canon, orig in art_orig.items():
                if canon.upper() not in catalog:
                    new_rows.append((orig, art_name.get(canon)))
            if new_rows:
                cur.executemany(
                    "INSERT INTO catalog_items (seller_article, sample_name) "
                    "VALUES (%s,%s) ON CONFLICT (seller_article) DO NOTHING",
                    new_rows)
            arts_new = len(new_rows)

            # 2) какие seller_article уже есть в ya_daily_sales за эту дату.
            cur.execute(
                "SELECT upper(seller_article) FROM ya_daily_sales WHERE date=%s",
                (d,))
            existing = {r[0] for r in cur.fetchall()}

            # 3) идемпотентность: обнуляем остаток у всех строк продаж за дату.
            cur.execute(
                "UPDATE ya_daily_sales SET stock_ya_qty=NULL WHERE date=%s",
                (d,))

            # 4) массовый UPDATE остатка для существующих строк продаж.
            upd_vals = [(art_orig[c], stock_by_canon[c])
                        for c in stock_by_canon if c.upper() in existing]
            updated = 0
            if upd_vals:
                cur.executemany(
                    "UPDATE ya_daily_sales SET stock_ya_qty=%s "
                    "WHERE date=%s AND upper(seller_article)=upper(%s)",
                    [(qty, d, orig) for orig, qty in upd_vals])
                updated = len(upd_vals)

            # 5) заготовки для товаров с остатком>0 без строки продаж за дату.
            #    (политика пользователя: показывать остаток всегда).
            ins_rows = []
            for c in stock_by_canon:
                if c.upper() in existing:
                    continue
                if (stock_by_canon[c] or 0) <= 0:
                    continue  # нет остатка и нет продаж — строку не заводим
                ins_rows.append((d, art_orig[c], art_ysku.get(c),
                                 art_name.get(c), stock_by_canon[c]))
            inserted = 0
            if ins_rows:
                cur.executemany(
                    "INSERT INTO ya_daily_sales "
                    "(date, seller_article, ya_sku, item_name, stock_ya_qty) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    ins_rows)
                inserted = len(ins_rows)

            rows_loaded = updated + inserted
            nonzero = sum(1 for c in stock_by_canon if (stock_by_canon[c] or 0) > 0)
            msg = (f"Остатки Яндекс за {period_text}: SKU в файле {sku_total}, "
                   f"с остатком>0 {nonzero}; строк обновлено {updated}, "
                   f"заведено заготовок {inserted}")
            if arts_new:
                msg += f"; новых артикулов (нераспределённые): {arts_new}"

            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (rows_loaded, msg[:500], upload_id))
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
        "marketplace": MP_NAME,
        "rows_loaded": rows_loaded,
        "updated": updated,
        "inserted": inserted,
        "sku_total": sku_total,
        "file_total": file_rows,
        "arts_new": arts_new,
        "stock_date": d.isoformat(),
        "period_text": period_text,
        "upload_id": upload_id,
    }
