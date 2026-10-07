# -*- coding: utf-8 -*-
"""Загрузчик отчёта «Аналитика продаж» Яндекс.Маркета в ya_daily_sales.

Формат: дневные данные (одна строка = день × SKU), 29 колонок, заголовки
в строке 1, данные со 2-й строки. Лист «Аналитика продаж».

Гранулярность: день × «Ваш SKU» (наш артикул). Дата в колонке «День»
формата ДД-ММ-ГГГГ. Читаем ПО ИМЕНИ заголовка (а не по позиции).

Перезагрузка за уже загруженные даты = перезапись (Яндекс тоже может
обновлять данные прошлых периодов):
    DELETE FROM ya_daily_sales WHERE date BETWEEN min_date AND max_date
    затем INSERT батчами по 100 (лимит параметров psycopg3).

Артикулы, которых нет в справочнике, — upsert в catalog_marketplace
(marketplace='Yandex') и catalog_items, чтобы данные не терялись
(останутся «нераспределёнными» до привязки категорий/статуса — это нормально).

КОНВЕРСИИ (согласовано с пользователем):
  • cr_cart_pct  ← «Конверсия из показа в корзину, %» (столбец L). Это M/G.
  • cr_order_pct ← «Конверсия из корзины в заказ, %» (столбец T). Это P/M.
В ya_daily_sales, как в ozon/wb_daily_sales, CR хранятся как ДОЛЯ 0..1
(фронт rp() умножает на 100). Поэтому делим на 100 при загрузке.
  • ctr_pct       — В ФАЙЛЕ ОТДЕЛЬНОГО СТОЛБЦА НЕТ. Считаем сами: клики/показы
                    (J/G) как доля 0..1. Пустой знаменатель → None.

Читаем через python_calamine (единообразно с wb_daily_loader).

Каждая загрузка фиксируется в report_uploads (marketplace='Yandex',
period_kind='ya_daily').
"""
import os
import datetime

from python_calamine import CalamineWorkbook

try:
    from .util import canon_article
except ImportError:  # запуск напрямую
    from util import canon_article

MP_NAME = "Yandex"
SHEET_NAME = "Аналитика продаж"

# Ключевые заголовки файла (проверка формата ПО ИМЕНИ).
YA_REQUIRED = [
    "День", "Ваш SKU",
    "Показы моих товаров, шт.", "Клики по товарам, шт.",
    "Заказанные товары, шт.", "Заказано товаров на сумму, ₽",
]

# Маппинг: поле БД -> заголовок колонки файла. Дата/SKU/имя — отдельно.
# Счётчики штук/сумм (обычные метрики).
METRIC_MAP = [
    ("shows_qty",       "Показы моих товаров, шт."),
    ("card_visits",     "Клики по товарам, шт."),
    ("add_to_cart_qty", "Добавления в корзину, шт."),
    ("orders_qty",      "Заказанные товары, шт."),
    ("orders_rub",      "Заказано товаров на сумму, ₽"),
    ("cancels_qty",     "Отмены и невыкупы за период, шт."),
]

# Конверсии: заголовок → поле БД (приходят как проценты, делим на 100).
CR_MAP = [
    ("cr_cart_pct",  "Конверсия из показа в корзину, %"),
    ("cr_order_pct", "Конверсия из корзины в заказ, %"),
]

# Поля-СЧЁТЧИКИ активности. КОНВЕНЦИЯ «как в Ozon/WB»: если СТРОКА за день
# СУЩЕСТВУЕТ (товар в файле за эту дату = была активность), пустая ячейка
# счётчика = 0 (нулевое значение), НЕ NULL. Фронт покажет «0», светофор учтёт
# нулевые дни как дно шкалы. CR тоже обнуляем (0% при наличии активности).
# НЕ входят метрики-заготовки (spp/pi/ads/rating/reviews/stock_ya) и stock_ap_qty.
ZERO_FILL_COLS = [
    "shows_qty", "card_visits", "add_to_cart_qty",
    "orders_qty", "orders_rub", "cancels_qty",
    "cr_cart_pct", "cr_order_pct", "ctr_pct",
]

# Поля ya_daily_sales, которые наполнят ДРУГИЕ загрузчики / появятся позже.
# При перезаписи дат (DELETE+INSERT) сохраняем их по ключу (date, seller_article):
#   • stock_ap_qty  — остатки ТД АВТОПРОФИ (общий stock_daily; но на всякий
#     случай сохраняем, если синхронизировался в строку)
#   • метрики-заготовки — заполнятся позже отдельными отчётами
PRESERVE_COLS = [
    "spp_pct", "spp_rub", "price_index_pi", "drr_total_pct",
    "ads_expense_rub", "avg_position", "reviews_qty", "rating",
    "stock_ya_qty", "stock_ap_qty",
]

# Порядок колонок INSERT (без id/created_at — дефолты БД).
INSERT_COLS = (
    ["date", "seller_article", "ya_sku", "item_name"]
    + [m[0] for m in METRIC_MAP]
    + [m[0] for m in CR_MAP]
    + ["ctr_pct", "upload_id"]
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
    """Дата из ячейки (datetime/date/строка ISO/ДД-ММ-ГГГГ/ДД.ММ.ГГГГ)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _build_colidx(header_row):
    """Индекс заголовок->номер колонки (0-based) по строке заголовков."""
    idx = {}
    for c, v in enumerate(header_row):
        if v is not None and str(v).strip():
            idx.setdefault(str(v).strip(), c)
    return idx


def _read_sheet(path):
    """Читаем лист «Аналитика продаж» (или первый) через calamine → список строк."""
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
                 os.path.basename(path), 0, "ОШИБКА ФОРМАТА", msg[:500], "ya_daily"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def load_ya_daily(conn, path):
    """Загрузить отчёт «Аналитика продаж» Яндекс в ya_daily_sales.

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
    missing = [name for name in YA_REQUIRED if name not in idx]
    if missing:
        msg = "ОШИБКА ФОРМАТА: не найдены колонки: " + ", ".join(missing)
        _journal_error(conn, path, msg)
        return {"ok": False, "error": msg}

    c_date = idx["День"]
    c_art = idx["Ваш SKU"]
    c_name = idx.get("Название товара")
    c_shows = idx.get("Показы моих товаров, шт.")
    c_clicks = idx.get("Клики по товарам, шт.")

    rows = []
    dates = set()
    report_arts = {}      # canon_article -> item_name
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

        name = cell(c_name)
        name = str(name).strip() if (name is not None and str(name).strip()) else None

        rec = {
            "date": d, "seller_article": art_canon,
            "ya_sku": str(raw_art).strip() if raw_art is not None else None,
            "item_name": name,
        }
        for db_col, header in METRIC_MAP:
            c = idx.get(header)
            rec[db_col] = _to_num(cell(c)) if c is not None else None
        # конверсии: проценты → доля 0..1
        for db_col, header in CR_MAP:
            c = idx.get(header)
            v = _to_num(cell(c)) if c is not None else None
            rec[db_col] = (v / 100.0) if v is not None else None
        # CTR считаем сами: клики/показы (доля 0..1). Нет столбца в файле.
        shows = _to_num(cell(c_shows)) if c_shows is not None else None
        clicks = _to_num(cell(c_clicks)) if c_clicks is not None else None
        rec["ctr_pct"] = (clicks / shows) if (shows and shows > 0 and clicks is not None) else None

        # «Как в Ozon/WB»: строка за день существует → пустые счётчики = 0,
        # а не NULL. Метрики-заготовки НЕ трогаем (их тут нет в rec вовсе).
        for db_col in ZERO_FILL_COLS:
            if rec.get(db_col) is None:
                rec[db_col] = 0.0

        dates.add(d)
        if art_canon not in report_arts:
            report_arts[art_canon] = name
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

    # Гарантируем строку в catalog_marketplace для Yandex по каждому артикулу.
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
             os.path.basename(path), "В ПРОЦЕССЕ", "ya_daily"))
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
                "FROM ya_daily_sales WHERE date BETWEEN %s AND %s",
                (min_date, max_date))
            preserved = {}
            for row in cur.fetchall():
                d_key, art_key = row[0], row[1]
                vals = dict(zip(PRESERVE_COLS, row[2:]))
                if any(v is not None for v in vals.values()):
                    preserved[(d_key, art_key)] = vals

            cur.execute(
                "DELETE FROM ya_daily_sales WHERE date BETWEEN %s AND %s",
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
                    f"INSERT INTO ya_daily_sales ({cols_sql}) VALUES {values_sql}",
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
                        f"UPDATE ya_daily_sales AS t SET {set_sql} "
                        f"FROM (VALUES {','.join(vals_parts)}) "
                        f"AS v({cols_list}) "
                        "WHERE t.date = v.d AND t.seller_article = v.sa",
                        flat)
                    restored = cur.rowcount

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
        _journal_error(conn, path, f"Ошибка записи в БД: {exc}",
                       min_date, max_date, year)
        return {"ok": False, "error": f"Ошибка записи в БД: {exc}"}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "rows_loaded": len(tuples),
        "deleted": deleted,
        "dates": sorted(str(d) for d in dates),
        "period_text": period_text,
        "unmatched": unmatched,
        "new_articles": sorted(new_arts.keys()),
        "upload_id": upload_id,
    }
