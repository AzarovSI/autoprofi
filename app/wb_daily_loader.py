# -*- coding: utf-8 -*-
"""Загрузчик отчёта «Воронка» Wildberries (лист «TDSheet») в wb_daily_sales.

Формат: дневные данные (одна строка = день × артикул), 16 колонок, заголовки
в строке 1, данные со 2-й строки. Столбец B в шапке пустой — читаем ПО ИМЕНИ
заголовка, а не по позиции.

Перезагрузка за уже загруженные даты = перезапись (WB иногда обновляет данные
в прошлых периодах):
    DELETE FROM wb_daily_sales WHERE date BETWEEN min_date AND max_date
    затем INSERT батчами по 100 (лимит параметров psycopg3).

Артикулы, которых нет в справочнике, — upsert в catalog_marketplace
(marketplace='Wildberries') и catalog_items, чтобы данные не терялись
(останутся «нераспределёнными» до привязки категорий/статуса — это нормально).

ВАЖНО про масштаб конверсий: отчёт даёт КонверсияВКорзину/ВЗаказ как ПРОЦЕНТЫ
(напр. 100.0, 4.0, 31.0). В wb_daily_sales, как и в ozon_daily_sales, CR хранятся
как ДОЛЯ 0..1 (фронт rp() умножает на 100). Поэтому делим на 100 при загрузке.

Читаем через python_calamine (openpyxl падает на этих WB-файлах — capital-S
SharedStrings.xml KeyError).

Каждая загрузка фиксируется в report_uploads (marketplace='Wildberries',
period_kind='wb_daily').
"""
import os
import datetime

from python_calamine import CalamineWorkbook

try:
    from .util import canon_article
except ImportError:  # запуск напрямую
    from util import canon_article

MP_NAME = "Wildberries"
SHEET_NAME = "TDSheet"

# Ключевые заголовки файла (проверка формата ПО ИМЕНИ).
WB_VORONKA_REQUIRED = [
    "Дата", "ИдентификаторТовара", "Артикул",
    "ЗаказалиТоваров", "ЗаказалиНаСумму",
    "ПерешлиВКарточку", "Остаток",
]

# Маппинг: поле БД -> заголовок колонки файла. ID/дата/имя — отдельно.
# CR_COLS (конверсии) обрабатываются отдельно — делятся на 100 (проценты → доля).
METRIC_MAP = [
    ("orders_qty",      "ЗаказалиТоваров"),
    ("orders_rub",      "ЗаказалиНаСумму"),
    ("cancels_qty",     "ОтменилиИВернулиТоваров"),
    ("cancels_rub",     "ОтменилиИВернулиНаСумму"),
    ("buyout_qty",      "ВыкупилиТоваров"),
    ("buyout_rub",      "ВыкупилиНаСумму"),
    ("card_visits",     "ПерешлиВКарточку"),
    ("add_to_cart_qty", "ПоложилиВКорзину"),
    ("rating",          "РейтингОтзывов"),
    ("avg_search_position", "СредняяПозицияВПоиске"),
    ("stock_wb_qty",    "Остаток"),
]

# Конверсии: заголовок → поле БД (приходят как проценты, делим на 100).
CR_MAP = [
    ("cr_cart_pct",  "КонверсияВКорзину"),
    ("cr_order_pct", "КонверсияВЗаказ"),
]

# Поля-СЧЁТЧИКИ активности из отчёта «Воронка». КОНВЕНЦИЯ «как в Ozon»:
# если СТРОКА за день СУЩЕСТВУЕТ (товар есть в файле за эту дату = была
# активность), пустая ячейка счётчика = 0 (нулевое значение), НЕ NULL.
# Фронт покажет «0» (а не «—») и светофор учтёт нулевые дни как дно шкалы.
# НЕ входят: rating, avg_search_position, stock_wb_qty (значение-на-дату —
# NULL=нет данных, как SNAP/AVG в Ozon; пустая ячейка НЕ обнуляется в 0)
# и поля других загрузчиков (reviews_qty, ads_*, stock_ap_qty).
ZERO_FILL_COLS = [
    "orders_qty", "orders_rub", "cancels_qty", "cancels_rub",
    "buyout_qty", "buyout_rub", "card_visits", "add_to_cart_qty",
    "cr_cart_pct", "cr_order_pct",
]

# Поля wb_daily_sales, которые наполняют ДРУГИЕ загрузчики. При перезаписи дат
# (DELETE+INSERT) сохраняем их по ключу (date, seller_article):
#   • reviews_qty                        — отчёт «Рейтинг» (wb_reviews_loader)
#   • ads_expense_rub, ctr_pct, ads_*    — отчёт рекламы (wb_ads_loader)
#   • stock_ap_qty                       — остатки ТД АВТОПРОФИ (ozon_stock_loader)
#   • comp_price_avg, comp_price_min     — цены конкурентов PriceVA (wb_priceva_loader)
PRESERVE_COLS = [
    "reviews_qty", "ads_expense_rub", "ctr_pct",
    "ads_views", "ads_clicks", "ads_avg_cpc", "ads_atbs",
    "ads_orders", "ads_shks", "ads_sum_price", "stock_ap_qty",
    "comp_price_avg", "comp_price_min",
    "price_index_pi", "spp_pct",
]

# Порядок колонок INSERT (без id/created_at — дефолты БД).
INSERT_COLS = (
    ["date", "seller_article", "nm_id", "item_name"]
    + [m[0] for m in METRIC_MAP]
    + [m[0] for m in CR_MAP]
    + ["upload_id"]
)


def _to_num(v):
    """Число из ячейки; пусто/«-»/нечисло → None."""
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    s = s.replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _to_date(v):
    """Дата из ячейки (datetime/date/строка ISO/ДД.ММ.ГГГГ). Нераспознанное → None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_bigint(v):
    """float→bigint; пусто → None."""
    n = _to_num(v)
    if n is None:
        return None
    try:
        return int(round(n))
    except (ValueError, OverflowError):
        return None


def _build_colidx(header_row):
    """Индекс заголовок->номер колонки (0-based) по строке заголовков."""
    idx = {}
    for c, v in enumerate(header_row):
        if v is not None and str(v).strip():
            idx.setdefault(str(v).strip(), c)
    return idx


def _read_sheet(path):
    """Читаем первый (или TDSheet) лист через calamine → список строк (list)."""
    wb = CalamineWorkbook.from_path(path)
    name = SHEET_NAME if SHEET_NAME in wb.sheet_names else wb.sheet_names[0]
    return wb.get_sheet_by_name(name).to_python(), name


def _journal_error(conn, path, msg, period_start=None, period_end=None, year=None):
    """Записать ОШИБКУ в report_uploads (отдельная запись)."""
    period_text = ""
    if period_start and period_end:
        period_text = (f"{period_start.strftime('%d.%m.%Y')} - "
                       f"{period_end.strftime('%d.%m.%Y')}")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_uploads (marketplace, year, period_text, "
                "period_start, period_end, source_file, rows_loaded, status, "
                "message, period_kind) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (MP_NAME, year, period_text, period_start, period_end,
                 os.path.basename(path), 0, "ОШИБКА ФОРМАТА", msg[:500], "wb_daily"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def load_wb_daily(conn, path):
    """Загрузить отчёт «Воронка» WB в wb_daily_sales.

    conn — psycopg-соединение (без автокоммита; функция сама commit/rollback).
    Возвращает dict {ok, rows_loaded, dates, unmatched, deleted, ...}.
    """
    try:
        data, sheet = _read_sheet(path)
    except Exception as exc:
        _journal_error(conn, path, f"Не удалось прочитать файл: {exc}")
        return {"ok": False, "error": f"Не удалось прочитать файл: {exc}"}

    if not data:
        _journal_error(conn, path, "Пустой файл.")
        return {"ok": False, "error": "Пустой файл."}

    idx = _build_colidx(data[0])
    missing = [name for name in WB_VORONKA_REQUIRED if name not in idx]
    if missing:
        msg = "ОШИБКА ФОРМАТА: не найдены колонки: " + ", ".join(missing)
        _journal_error(conn, path, msg)
        return {"ok": False, "error": msg}

    c_date = idx["Дата"]
    c_art = idx["Артикул"]
    c_nm = idx.get("ИдентификаторТовара")

    rows = []
    dates = set()
    report_arts = {}      # canon_article -> item_name (нет имени в отчёте → None)
    skipped_empty = 0
    skipped_baddate = 0

    for r in data[1:]:
        def cell(i):
            return r[i] if (i is not None and i < len(r)) else None
        raw_date = cell(c_date)
        raw_art = cell(c_art)
        art_canon = canon_article(raw_art)
        d = _to_date(raw_date)

        if d is None and not art_canon:
            skipped_empty += 1
            continue
        if d is None:
            skipped_baddate += 1
            continue
        if not art_canon:
            skipped_empty += 1
            continue

        nm = _to_bigint(cell(c_nm)) if c_nm is not None else None

        rec = {
            "date": d, "seller_article": art_canon,
            "nm_id": nm, "item_name": None,
        }
        for db_col, header in METRIC_MAP:
            c = idx.get(header)
            rec[db_col] = _to_num(cell(c)) if c is not None else None
        # конверсии: проценты → доля 0..1
        for db_col, header in CR_MAP:
            c = idx.get(header)
            v = _to_num(cell(c)) if c is not None else None
            rec[db_col] = (v / 100.0) if v is not None else None

        # «Как в Ozon»: строка за день существует (товар в файле) → пустые
        # счётчики = 0 (нулевое значение), а не NULL. Снапшоты (rating,
        # stock_wb_qty) НЕ трогаем — там NULL означает «нет данных».
        for db_col in ZERO_FILL_COLS:
            if rec.get(db_col) is None:
                rec[db_col] = 0.0

        dates.add(d)
        if art_canon not in report_arts:
            report_arts[art_canon] = None
        rows.append(rec)

    if not rows:
        msg = "В файле нет ни одной валидной строки данных (пустой отчёт)."
        _journal_error(conn, path, msg)
        return {"ok": False, "error": msg}

    min_date = min(dates)
    max_date = max(dates)
    period_text = (f"{min_date.strftime('%d.%m.%Y')} - "
                   f"{max_date.strftime('%d.%m.%Y')}")
    year = min_date.year

    # --- автодобавление артикулов в справочник ---
    existing_cat = {}
    with conn.cursor() as cur:
        cur.execute("SELECT seller_article, category_l1, category_l2, category_l3 "
                    "FROM catalog_items")
        for a, l1, l2, l3 in cur.fetchall():
            existing_cat[canon_article(a)] = (l1, l2, l3)

    new_arts = {a: nm for a, nm in report_arts.items() if a not in existing_cat}
    if new_arts:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO catalog_items (seller_article, sample_name) "
                "VALUES (%s,%s) ON CONFLICT (seller_article) DO NOTHING",
                list(new_arts.items()))
        conn.commit()

    # Гарантируем строку в catalog_marketplace для WB по каждому артикулу.
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO catalog_marketplace (seller_article, marketplace) "
            "VALUES (%s,%s) ON CONFLICT (seller_article, marketplace) DO NOTHING",
            [(a, MP_NAME) for a in report_arts])
    conn.commit()

    unmatched = sorted(
        a for a in report_arts
        if not all(existing_cat.get(a, (None, None, None)))
    )

    # --- журнал: создаём запись заранее (upload_id) ---
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO report_uploads (marketplace, year, period_text, "
            "period_start, period_end, source_file, status, period_kind) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (MP_NAME, year, period_text, min_date, max_date,
             os.path.basename(path), "В ПРОЦЕССЕ", "wb_daily"))
        upload_id = cur.fetchone()[0]

    tuples = []
    for rec in rows:
        rec["upload_id"] = upload_id
        tuples.append(tuple(rec[col] for col in INSERT_COLS))

    # --- перезапись диапазона дат + вставка батчами (+ сохранение PRESERVE_COLS) ---
    try:
        with conn.cursor() as cur:
            preserve_sql = ",".join(PRESERVE_COLS)
            cur.execute(
                f"SELECT date, seller_article, {preserve_sql} "
                "FROM wb_daily_sales WHERE date BETWEEN %s AND %s",
                (min_date, max_date))
            preserved = {}
            for row in cur.fetchall():
                d_key, art_key = row[0], row[1]
                vals = dict(zip(PRESERVE_COLS, row[2:]))
                if any(v is not None for v in vals.values()):
                    preserved[(d_key, art_key)] = vals

            cur.execute(
                "DELETE FROM wb_daily_sales WHERE date BETWEEN %s AND %s",
                (min_date, max_date))
            deleted = cur.rowcount

            cols_sql = ",".join(INSERT_COLS)
            row_tmpl = "(" + ",".join(["%s"] * len(INSERT_COLS)) + ")"
            PAGE = 100
            for i in range(0, len(tuples), PAGE):
                batch = tuples[i:i + PAGE]
                values_sql = ",".join([row_tmpl] * len(batch))
                flat = [v for rr in batch for v in rr]
                cur.execute(
                    f"INSERT INTO wb_daily_sales ({cols_sql}) VALUES {values_sql}",
                    flat)

            restored = 0
            if preserved:
                new_keys = {(rec["date"], rec["seller_article"]) for rec in rows}
                upd_rows = [
                    ((d_key, art_key), vals)
                    for (d_key, art_key), vals in preserved.items()
                    if (d_key, art_key) in new_keys
                ]
                if upd_rows:
                    n_pc = len(PRESERVE_COLS)
                    first_tmpl = ("(%s::date, %s::text, "
                                  + ",".join(["%s::numeric"] * n_pc) + ")")
                    rest_tmpl = "(" + ",".join(["%s"] * (2 + n_pc)) + ")"
                    vals_parts, flat = [], []
                    for i, ((d_key, art_key), vals) in enumerate(upd_rows):
                        vals_parts.append(first_tmpl if i == 0 else rest_tmpl)
                        flat.append(d_key)
                        flat.append(art_key)
                        flat.extend(vals[c] for c in PRESERVE_COLS)
                    set_sql = ",".join(f"{c}=v.{c}" for c in PRESERVE_COLS)
                    cols_list = "d, sa, " + ", ".join(PRESERVE_COLS)
                    cur.execute(
                        f"UPDATE wb_daily_sales AS t SET {set_sql} "
                        f"FROM (VALUES {','.join(vals_parts)}) "
                        f"AS v({cols_list}) "
                        "WHERE t.date = v.d AND t.seller_article = v.sa",
                        flat)
                    restored = cur.rowcount
                # Отдельно загруженные показатели не исчезают, если товар
                # отсутствует в повторном файле воронки. Заказы остаются NULL.
                stub_rows = [
                    (d_key, art_key, *(vals[c] for c in PRESERVE_COLS))
                    for (d_key, art_key), vals in preserved.items()
                    if (d_key, art_key) not in new_keys
                ]
                if stub_rows:
                    cols = ["date", "seller_article", *PRESERVE_COLS]
                    cur.executemany(
                        f"INSERT INTO wb_daily_sales ({','.join(cols)}) "
                        f"VALUES ({','.join(['%s'] * len(cols))})",
                        stub_rows)
                    restored += len(stub_rows)

            msg = (f"Даты {period_text}; удалено старых: {deleted}; "
                   f"вставлено: {len(tuples)}")
            if restored:
                msg += f"; сохранено внешних полей: {restored}"
            if skipped_empty:
                msg += f"; пропущено пустых: {skipped_empty}"
            if skipped_baddate:
                msg += f"; без даты: {skipped_baddate}"
            if new_arts:
                msg += f"; новых артикулов: {len(new_arts)}"
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (len(tuples), msg[:500], upload_id))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        user_msg = ("Не удалось сохранить отчёт «Воронка» WB в базу. "
                    "Проверьте файл на дубли по (дата, артикул).")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE report_uploads SET status='ОШИБКА', message=%s WHERE id=%s",
                    (str(exc)[:500], upload_id))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "error": user_msg, "upload_id": upload_id}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "rows_loaded": len(tuples),
        "deleted": deleted,
        "dates": [d.isoformat() for d in sorted(dates)],
        "period_text": period_text,
        "unmatched": unmatched,
        "new_articles": sorted(new_arts.keys()),
        "skipped_empty": skipped_empty,
        "skipped_baddate": skipped_baddate,
        "upload_id": upload_id,
    }
