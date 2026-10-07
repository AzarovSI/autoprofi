# -*- coding: utf-8 -*-
"""Загрузчик отчёта «Рейтинг и отзывы» Wildberries (лист «TDSheet») → reviews_qty + spp_pct.

Формат: дневные данные, 7 колонок: Дата · (пусто) · ИдентификаторТовара ·
Артикул · КоличествоОтзывов · РейтингТовара · ПроцентСПП. Заголовки в строке 1.

Пишем reviews_qty (Количество отзывов) и spp_pct (ПроцентСПП) в wb_daily_sales
по ключу (date, seller_article). РейтингТовара НЕ берём — рейтинг уже приходит
из отчёта «Воронка» (rating), чтобы не задваивать источник.

ПроцентСПП в файле — в процентах (напр. 45.01). В БД spp_pct хранится как
ДОЛЯ 0..1 (как у Ozon), поэтому делим на 100 при записи. Пустое значение
СПП не затирает уже сохранённое (обновляем только непустые).

Для строк (date, article), которых ещё нет в wb_daily_sales, создаём
строку-заглушку (только reviews_qty) — по товарам из справочника дашборда,
чтобы отзывы не терялись, если отчёт «Воронка» за эту дату ещё не загружен.

Читаем через python_calamine. Фиксируется в report_uploads
(marketplace='Wildberries', period_kind='wb_reviews').
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

WB_REITING_REQUIRED = ["Дата", "Артикул", "КоличествоОтзывов"]


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
                 "ОШИБКА ФОРМАТА", msg[:500], "wb_reviews"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def load_wb_reviews(conn, path, report_date=None):
    """Загрузить «Рейтинг и отзывы» WB → reviews_qty + spp_pct.

    Файл может быть МНОГОДНЕВНЫМ (несколько дат в колонке «Дата»).
    Раньше загрузчик сваливал все строки в ОДНУ дату (первую) — это
    искажало данные, если в файле несколько дней. Теперь строки
    группируются по дате из файла, и каждая дата грузится отдельно.

    Параметр report_date опционален: если передан, грузим ТОЛЬКО эту дату
    (обратная совместимость). Если не передан — грузим все даты из файла.

    reviews_qty (Количество отзывов) — upsert по (date, article), при
    отсутствии строки создаётся заглушка. spp_pct (ПроцентСПП, проценты →
    доля 0..1) — проставляется отдельным UPDATE по уже существующим строкам
    (в т.ч. только что созданным заглушкам); пустое СПП не затирает
    сохранённое. Только товары из справочника дашборда.
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
    missing = [n for n in WB_REITING_REQUIRED if n not in idx]
    if missing:
        msg = "ОШИБКА ФОРМАТА: не найдены колонки: " + ", ".join(missing)
        _journal_error(conn, path, msg)
        return {"ok": False, "error": msg}

    c_date = idx["Дата"]
    c_art = idx["Артикул"]
    c_rev = idx["КоличествоОтзывов"]
    # Новая колонка «ПроцентСПП» — опциональная (старые файлы без неё).
    c_spp = idx.get("ПроцентСПП")

    force_d = _to_date(report_date)  # если задана — грузим только её

    # Группируем строки по дате: date -> {art: rev}, date -> {art: spp_доля}.
    rev_by_date = {}   # {date: {art: reviews_qty}}
    spp_by_date = {}   # {date: {art: spp_pct(доля)}}
    file_total = 0
    for r in data[1:]:
        def cell(i):
            return r[i] if (i is not None and i < len(r)) else None
        art = canon_article(cell(c_art))
        if not art:
            continue
        d = _to_date(cell(c_date))
        if d is None:
            continue
        if force_d is not None and d != force_d:
            continue
        file_total += 1
        rev = _to_num(cell(c_rev))
        if rev is not None:
            rev_by_date.setdefault(d, {})[art] = rev
        # СПП: проценты → доля 0..1. Пустое пропускаем (не затираем).
        if c_spp is not None:
            spp = _to_num(cell(c_spp))
            if spp is not None:
                spp_by_date.setdefault(d, {})[art] = round(spp / 100.0, 4)

    all_dates = sorted(set(rev_by_date) | set(spp_by_date))
    if not all_dates:
        msg = ("Не удалось определить дату/строки отчёта — "
               "проверьте формат файла.")
        _journal_error(conn, path, msg)
        return {"ok": False, "error": msg}

    # Только товары из справочника дашборда.
    with conn.cursor() as cur:
        cur.execute("SELECT seller_article FROM catalog_items")
        catalog = {canon_article(a) for (a,) in cur.fetchall()}

    d_min, d_max = all_dates[0], all_dates[-1]
    period_text = (d_min.strftime("%d.%m.%Y") if d_min == d_max
                   else f"{d_min.strftime('%d.%m.%Y')}–{d_max.strftime('%d.%m.%Y')}")
    year = d_max.year

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO report_uploads (marketplace, year, period_text, "
            "period_start, period_end, source_file, status, period_kind) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (MP_NAME, year, period_text, d_min, d_max,
             os.path.basename(path), "В ПРОЦЕССЕ", "wb_reviews"))
        upload_id = cur.fetchone()[0]

    tot_updated = 0
    tot_inserted = 0
    tot_spp = 0
    skipped_not_in_catalog = 0
    try:
        with conn.cursor() as cur:
            for d in all_dates:
                rev_map = {a: v for a, v in rev_by_date.get(d, {}).items()
                           if a in catalog}
                spp_map = {a: v for a, v in spp_by_date.get(d, {}).items()
                           if a in catalog}
                skipped_not_in_catalog += (
                    len(rev_by_date.get(d, {})) - len(rev_map))

                # какие артикулы уже есть в продажах за дату
                cur.execute(
                    "SELECT seller_article FROM wb_daily_sales WHERE date=%s",
                    (d,))
                existing = {a for (a,) in cur.fetchall()}

                if rev_map:
                    updated = sum(1 for a in rev_map if a in existing)
                    inserted = len(rev_map) - updated
                    tot_updated += updated
                    tot_inserted += inserted
                    # 1) обновление существующих строк — одним запросом
                    vals = list(rev_map.items())
                    first = "(%s::text, %s::numeric)"
                    rest = ",".join(["(%s,%s)"] * (len(vals) - 1))
                    ph = first + (("," + rest) if rest else "")
                    flat = []
                    for art, rev in vals:
                        flat.extend([art, rev])
                    cur.execute(
                        "UPDATE wb_daily_sales AS t SET reviews_qty = v.rev "
                        f"FROM (VALUES {ph}) AS v(art, rev) "
                        "WHERE t.date = %s AND t.seller_article = v.art",
                        flat + [d])
                    # 2) заглушки для товаров без продаж за дату
                    stub = [(d, a, r, upload_id)
                            for a, r in rev_map.items() if a not in existing]
                    if stub:
                        ph2 = ",".join(["(%s,%s,%s,%s)"] * len(stub))
                        flat2 = []
                        for row in stub:
                            flat2.extend(row)
                        cur.execute(
                            "INSERT INTO wb_daily_sales (date, seller_article, "
                            f"reviews_qty, upload_id) VALUES {ph2} "
                            "ON CONFLICT (date, seller_article) "
                            "DO UPDATE SET reviews_qty=EXCLUDED.reviews_qty",
                            flat2)
                        existing |= {a for a in rev_map if a not in existing}

                # 3) СПП (spp_pct) — по уже существующим строкам за эту дату
                if spp_map:
                    svals = list(spp_map.items())
                    sfirst = "(%s::text, %s::numeric)"
                    srest = ",".join(["(%s,%s)"] * (len(svals) - 1))
                    sph = sfirst + (("," + srest) if srest else "")
                    sflat = []
                    for art, spp in svals:
                        sflat.extend([art, spp])
                    cur.execute(
                        "UPDATE wb_daily_sales AS t SET spp_pct = v.spp "
                        f"FROM (VALUES {sph}) AS v(art, spp) "
                        "WHERE t.date = %s AND t.seller_article = v.art",
                        sflat + [d])
                    tot_spp += (cur.rowcount or 0)

            msg = (f"Период {period_text}; дней: {len(all_dates)}; "
                   f"обновлено строк: {tot_updated}; "
                   f"создано заглушек: {tot_inserted}; "
                   f"СПП проставлено: {tot_spp}")
            if skipped_not_in_catalog:
                msg += f"; вне справочника: {skipped_not_in_catalog}"
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (tot_updated + tot_inserted, msg[:500], upload_id))
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
                "error": "Не удалось сохранить отчёт «Рейтинг и отзывы» WB в базу.",
                "upload_id": upload_id}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "rows_loaded": tot_updated + tot_inserted,
        "updated": tot_updated,
        "inserted": tot_inserted,
        "spp_updated": tot_spp,
        "days": len(all_dates),
        "file_total": file_total,
        "skipped_not_in_catalog": skipped_not_in_catalog,
        "report_date": d_max.isoformat(),
        "period_text": period_text,
        "upload_id": upload_id,
    }
