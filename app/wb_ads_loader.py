# -*- coding: utf-8 -*-
"""Загрузчик отчёта «Статистика рекламных кампаний» Wildberries → реклама.

Формат: лист «TDSheet», 16 колонок. НЕСКОЛЬКО строк на (Дата статистики +
Артикул) — это разные рекламные кампании по одному товару (норма, не ошибка).
Агрегируем по ключу (date, seller_article):

  • ads_expense_rub = Σ «Затраты (sum)»                (расходы на рекламу)
  • ctr_pct         = Σclicks / Σviews                 (ДОЛЯ 0..1; ПЕРЕСЧЁТ, НЕ сумма/среднее!)
  • ads_views       = Σ views      (future-показ)
  • ads_clicks      = Σ clicks     (future-показ)
  • ads_avg_cpc     = Σ Затраты / Σ clicks             (future; корректная средняя)
  • ads_atbs        = Σ atbs       (future)
  • ads_orders      = Σ orders     (future)
  • ads_shks        = Σ shks       (future)
  • ads_sum_price   = Σ sum_price  (future)
  • «Показатель кликабельности» из отчёта, «Конверсия», «Остаток склада ВБ» — НЕ берём.

Пишем в существующие строки wb_daily_sales по (date, seller_article). Если строки
продаж за дату нет — создаём строку-заглушку (только рекламные поля), по товарам
из справочника дашборда.

Дата берётся ИЗ ФАЙЛА (колонка «Дата статистики»); поддерживает несколько дат.

Читаем через python_calamine. Фиксируется в report_uploads
(marketplace='Wildberries', period_kind='wb_ads').
"""
import os
import datetime

from python_calamine import CalamineWorkbook

try:
    from .util import canon_article
except ImportError:
    from util import canon_article

MP_NAME = "Wildberries"
SHEET_NAME = "TDSheet"

WB_ADS_REQUIRED = [
    "Дата статистики", "Артикул",
    "Количество просмотров (views)", "Количество кликов (clicks)",
    "Затраты (sum)",
]

# Суммируемые числовые поля: заголовок → внутренний ключ агрегата.
SUM_FIELDS = [
    ("views",     "Количество просмотров (views)"),
    ("clicks",    "Количество кликов (clicks)"),
    ("sum",       "Затраты (sum)"),
    ("atbs",      "Количество добавлений товаров в корзину (atbs)"),
    ("orders",    "Количество заказов (orders)"),
    ("shks",      "Количество заказанных товаров (shks)"),
    ("sum_price", "Заказов на сумму (sum_price)"),
]


def _to_num(v):
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _to_date(v):
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


def _build_colidx(header_row):
    idx = {}
    for c, v in enumerate(header_row):
        if v is not None and str(v).strip():
            idx.setdefault(str(v).strip(), c)
    return idx


def _read_sheet(path):
    wb = CalamineWorkbook.from_path(path)
    name = SHEET_NAME if SHEET_NAME in wb.sheet_names else wb.sheet_names[0]
    return wb.get_sheet_by_name(name).to_python(), name


def _journal_error(conn, path, msg):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_uploads (marketplace, year, source_file, "
                "rows_loaded, status, message, period_kind) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (MP_NAME, None, os.path.basename(path), 0,
                 "ОШИБКА ФОРМАТА", msg[:500], "wb_ads"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def load_wb_ads(conn, path):
    """Загрузить рекламу WB и обновить wb_daily_sales по (date, seller_article).

    Возвращает dict {ok, rows_loaded, updated, inserted, dates, ...}.
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
    missing = [n for n in WB_ADS_REQUIRED if n not in idx]
    if missing:
        msg = "ОШИБКА ФОРМАТА: не найдены колонки: " + ", ".join(missing)
        _journal_error(conn, path, msg)
        return {"ok": False, "error": msg}

    c_date = idx["Дата статистики"]
    c_art = idx["Артикул"]

    # агрегат: (date, art) -> {field: сумма}
    agg = {}
    dates = set()
    file_total = 0
    for r in data[1:]:
        def cell(i):
            return r[i] if (i is not None and i < len(r)) else None
        d = _to_date(cell(c_date))
        art = canon_article(cell(c_art))
        if d is None or not art:
            continue
        file_total += 1
        dates.add(d)
        key = (d, art)
        a = agg.setdefault(key, {k: 0.0 for k, _ in SUM_FIELDS})
        for fkey, header in SUM_FIELDS:
            c = idx.get(header)
            v = _to_num(cell(c)) if c is not None else None
            if v is not None:
                a[fkey] += v

    if not agg:
        msg = "В файле нет ни одной валидной строки (дата + артикул)."
        _journal_error(conn, path, msg)
        return {"ok": False, "error": msg}

    # Только товары из справочника дашборда.
    with conn.cursor() as cur:
        cur.execute("SELECT seller_article FROM catalog_items")
        catalog = {canon_article(a) for (a,) in cur.fetchall()}

    min_date, max_date = min(dates), max(dates)
    period_text = (f"{min_date.strftime('%d.%m.%Y')}" if min_date == max_date
                   else f"{min_date.strftime('%d.%m.%Y')} - {max_date.strftime('%d.%m.%Y')}")
    year = min_date.year

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO report_uploads (marketplace, year, period_text, "
            "period_start, period_end, source_file, status, period_kind) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (MP_NAME, year, period_text, min_date, max_date,
             os.path.basename(path), "В ПРОЦЕССЕ", "wb_ads"))
        upload_id = cur.fetchone()[0]

    try:
        with conn.cursor() as cur:
            updated = 0
            inserted = 0
            skipped_not_in_catalog = 0
            for (d, art), a in agg.items():
                if art not in catalog:
                    skipped_not_in_catalog += 1
                    continue
                views = a["views"]
                clicks = a["clicks"]
                expense = a["sum"]
                # CTR храним КАК ДОЛЮ 0..1 (фронт сам умножает на 100 для pct-метрик).
                # Единая конвенция с cr_cart_pct/cr_order_pct и с Ozon (ozon_ads_loader).
                ctr = (clicks / views) if views > 0 else None
                avg_cpc = (expense / clicks) if clicks > 0 else None
                fields = {
                    "ads_expense_rub": expense,
                    "ctr_pct": ctr,
                    "ads_views": views,
                    "ads_clicks": clicks,
                    "ads_avg_cpc": avg_cpc,
                    "ads_atbs": a["atbs"],
                    "ads_orders": a["orders"],
                    "ads_shks": a["shks"],
                    "ads_sum_price": a["sum_price"],
                }
                set_sql = ", ".join(f"{k}=%s" for k in fields)
                cur.execute(
                    f"UPDATE wb_daily_sales SET {set_sql} "
                    "WHERE date=%s AND seller_article=%s",
                    list(fields.values()) + [d, art])
                if cur.rowcount:
                    updated += cur.rowcount
                else:
                    cols = ["date", "seller_article"] + list(fields.keys()) + ["upload_id"]
                    vals = [d, art] + list(fields.values()) + [upload_id]
                    ph = ",".join(["%s"] * len(cols))
                    upd = ", ".join(f"{k}=EXCLUDED.{k}" for k in fields)
                    cur.execute(
                        f"INSERT INTO wb_daily_sales ({','.join(cols)}) VALUES ({ph}) "
                        f"ON CONFLICT (date, seller_article) DO UPDATE SET {upd}",
                        vals)
                    inserted += 1

            msg = (f"Даты {period_text}; обновлено строк: {updated}; "
                   f"создано заглушек: {inserted}; пар (дата,арт): {len(agg)}")
            if skipped_not_in_catalog:
                msg += f"; вне справочника: {skipped_not_in_catalog}"
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (updated + inserted, msg[:500], upload_id))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
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
        return {"ok": False,
                "error": "Не удалось сохранить отчёт рекламы WB в базу.",
                "upload_id": upload_id}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "rows_loaded": updated + inserted,
        "updated": updated,
        "inserted": inserted,
        "sku_total": len(agg),
        "skipped_not_in_catalog": skipped_not_in_catalog,
        "dates": [d.isoformat() for d in sorted(dates)],
        "period_text": period_text,
        "upload_id": upload_id,
    }
