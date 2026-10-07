# -*- coding: utf-8 -*-
"""Загрузчик отчёта РЕКЛАМЫ Ozon («Аналитика продвижения») в ozon_daily_sales.

Формат файла (выгрузка «Аналитика продвижения» Ozon):
    • лист «Statistics»;
    • строка 1 — «Период: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ» (дата отчёта, дневной);
    • строка 2 — заголовки колонок;
    • данные с 3-й строки. Ключ строки — SKU (Ozon), один SKU может встречаться
      в нескольких строках (разные инструменты/места размещения).

Что берём из отчёта:
    • «Расход, ₽»  → ads_expense_rub  (СУММА по всем строкам одного SKU);
    • CTR          → ctr_pct          (агрегат = сумма кликов / сумма показов,
                                       хранится как ДОЛЯ 0..1, как spp_pct/cr_*).
ДРР из отчёта НЕ берём — считается на лету в матрице (ads_expense/orders_rub).

Запись: если строка продаж за (date, sku_ozon) уже есть — ОБНОВЛЯЕМ её
(ads_expense_rub, ctr_pct). Если строки НЕТ (у товара в этот день не было
продаж/заказов — это норма, реклама может идти без продаж), СОЗДАЁМ
строку-заглушку с продажами=NULL и только рекламой — чтобы расход не
терялся и корректно считался ДРР. Артикул (seller_article) для такой
строки берём из истории продаж этого SKU (маппинг sku_ozon→seller_article
однозначен). SKU, которые никогда не продавались (нет артикула),
попадают в unmatched (для отчёта пользователю).

Каждая загрузка фиксируется в report_uploads (marketplace='Ozon',
period_kind='ads').
"""
import os
import re
import datetime

import openpyxl

MP_NAME = "Ozon"
SHEET_NAME = "Statistics"

# Ключевые заголовки (строка 2) — проверка формата ПО ИМЕНИ, не по позиции.
ADS_REQUIRED = ["SKU", "Расход, ₽", "CTR, %", "Показы", "Клики"]

_PERIOD_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


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


def _to_bigint(v):
    n = _to_num(v)
    if n is None:
        return None
    try:
        return int(round(n))
    except (ValueError, OverflowError):
        return None


def _build_colidx(ws, header_row):
    idx = {}
    max_col = ws.max_column or 20
    for c in range(1, max_col + 1):
        v = ws.cell(header_row, c).value
        if v is not None and str(v).strip():
            idx.setdefault(str(v).strip(), c)
    return idx


def _parse_period(ws):
    """Дата отчёта из строки 1 «Период: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ».

    Возвращает (min_date, max_date) или (None, None). Для дневного отчёта
    обе даты обычно совпадают.
    """
    txt = ws.cell(1, 1).value
    if not txt:
        return None, None
    found = _PERIOD_RE.findall(str(txt))
    if not found:
        return None, None
    dates = []
    for dd, mm, yyyy in found:
        try:
            dates.append(datetime.date(int(yyyy), int(mm), int(dd)))
        except ValueError:
            continue
    if not dates:
        return None, None
    return min(dates), max(dates)


def _journal(conn, path, *, status, message="", rows_loaded=0,
             period_start=None, period_end=None, year=None,
             period_text="", upload_id=None):
    """Создать/обновить запись в report_uploads (period_kind='ads')."""
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
                     (message or "")[:500], "ads"))
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


def load_ozon_ads(conn, path):
    """Загрузить отчёт рекламы Ozon; обновить ads_expense_rub/ctr_pct.

    conn — psycopg-соединение (без автокоммита). Возвращает dict
    {ok, rows_loaded, updated, unmatched, dates, period_text, ...}.
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        msg = f"В файле нет листа «{SHEET_NAME}». Проверьте формат выгрузки."
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}
    ws = wb[SHEET_NAME]

    idx = _build_colidx(ws, header_row=2)
    missing = [name for name in ADS_REQUIRED if name not in idx]
    if missing:
        msg = "ОШИБКА ФОРМАТА: не найдены колонки: " + ", ".join(missing)
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}

    min_date, max_date = _parse_period(ws)
    if min_date is None:
        msg = ("Не удалось определить дату отчёта из строки «Период: …». "
               "Проверьте формат файла.")
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}
    period_text = (f"{min_date.strftime('%d.%m.%Y')} - "
                   f"{max_date.strftime('%d.%m.%Y')}")
    year = min_date.year

    c_sku = idx["SKU"]
    c_spend = idx["Расход, ₽"]
    c_impr = idx["Показы"]
    c_clicks = idx["Клики"]
    c_name = idx.get("Название товара")  # опционально (для отчёта unmatched)

    # --- агрегация по SKU: расход=сумма, показы=сумма, клики=сумма ---
    agg = {}  # sku(int) -> [spend, impressions, clicks]
    ads_names = {}  # sku(int) -> название товара из отчёта рекламы
    skipped_empty = 0
    max_row = ws.max_row or 2
    for r in range(3, max_row + 1):
        sku = _to_bigint(ws.cell(r, c_sku).value)
        if sku is None:
            skipped_empty += 1
            continue
        spend = _to_num(ws.cell(r, c_spend).value) or 0.0
        impr = _to_num(ws.cell(r, c_impr).value) or 0.0
        clicks = _to_num(ws.cell(r, c_clicks).value) or 0.0
        a = agg.setdefault(sku, [0.0, 0.0, 0.0])
        a[0] += spend
        a[1] += impr
        a[2] += clicks
        if c_name and sku not in ads_names:
            nm = ws.cell(r, c_name).value
            if nm is not None and str(nm).strip():
                ads_names[sku] = str(nm).strip()

    if not agg:
        msg = "В файле нет ни одной валидной строки данных (пустой отчёт)."
        _journal(conn, path, status="ОШИБКА ФОРМАТА", message=msg)
        return {"ok": False, "error": msg}

    # CTR как доля 0..1 = сумма кликов / сумма показов (None если показов нет).
    updates = {}  # sku -> (ads_expense_rub, ctr_pct|None)
    for sku, (spend, impr, clicks) in agg.items():
        ctr = (clicks / impr) if impr > 0 else None
        updates[sku] = (spend, ctr)

    # --- журнал: запись заранее ---
    upload_id = _journal(conn, path, status="В ПРОЦЕССЕ", period_text=period_text,
                         period_start=min_date, period_end=max_date, year=year)

    # --- обновление существующих строк продаж по (date, sku_ozon) ---
    try:
        with conn.cursor() as cur:
            # SKU, реально присутствующие в продажах за диапазон дат отчёта.
            cur.execute(
                "SELECT DISTINCT sku_ozon FROM ozon_daily_sales "
                "WHERE date BETWEEN %s AND %s AND sku_ozon IS NOT NULL",
                (min_date, max_date))
            db_skus = {row[0] for row in cur.fetchall()}

            matched = [s for s in updates if s in db_skus]
            no_sales = sorted(s for s in updates if s not in db_skus)

            updated = 0
            for sku in matched:
                spend, ctr = updates[sku]
                cur.execute(
                    "UPDATE ozon_daily_sales SET ads_expense_rub=%s, ctr_pct=%s "
                    "WHERE date BETWEEN %s AND %s AND sku_ozon=%s",
                    (spend, ctr, min_date, max_date, sku))
                updated += cur.rowcount

            # --- SKU без строки продаж за эту дату -------------------------------
            # Реклама/статистика может идти по товару, у которого в этот день
            # не было продаж/заказов — это норма. Чтобы расход не терялся
            # (и корректно считался ДРР), СОЗДАЁМ строку-заглушку с продажами=0.
            # Артикул (seller_article) берём из истории продаж этого SKU
            # (маппинг sku_ozon→seller_article однозначен). SKU без истории
            # (никогда не продавался) остаётся в unmatched.
            created = 0
            unmatched = []
            if no_sales:
                cur.execute(
                    "SELECT DISTINCT ON (sku_ozon) sku_ozon, seller_article, item_name "
                    "FROM ozon_daily_sales "
                    "WHERE sku_ozon = ANY(%s) AND seller_article IS NOT NULL "
                    "ORDER BY sku_ozon, date DESC",
                    (no_sales,))
                sku2art = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
                for sku in no_sales:
                    art_name = sku2art.get(sku)
                    if not art_name:
                        # Артикул неизвестен (товар никогда не продавался). В отчёт
                        # отдаём SKU Озон + название товара из файла рекламы.
                        unmatched.append({"sku": sku,
                                          "name": ads_names.get(sku, "")})
                        continue
                    art, name = art_name
                    spend, ctr = updates[sku]
                    # Строка-заглушка только с рекламой: продажи/заказы НЕ ставим
                    # (остаются NULL — нет данных, а не ноль продаж). На конфликт
                    # (date, seller_article) — обновляем рекламу (строка уже была без sku).
                    cur.execute(
                        "INSERT INTO ozon_daily_sales "
                        "(date, seller_article, sku_ozon, item_name, "
                        " ads_expense_rub, ctr_pct, upload_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (date, seller_article) DO UPDATE SET "
                        "  ads_expense_rub=EXCLUDED.ads_expense_rub, "
                        "  ctr_pct=EXCLUDED.ctr_pct, "
                        "  sku_ozon=COALESCE(ozon_daily_sales.sku_ozon, EXCLUDED.sku_ozon)",
                        (min_date, art, sku, name, spend, ctr, upload_id))
                    created += cur.rowcount

            msg = (f"Дата {period_text}; SKU в отчёте: {len(updates)}; "
                   f"обновлено строк продаж: {updated}")
            if created:
                msg += f"; создано строк (реклама без продаж): {created}"
            if unmatched:
                msg += f"; SKU без артикула (нет в истории): {len(unmatched)}"
            if skipped_empty:
                msg += f"; пропущено пустых: {skipped_empty}"
            cur.execute(
                "UPDATE report_uploads SET rows_loaded=%s, status='OK', "
                "message=%s WHERE id=%s",
                (updated + created, msg[:500], upload_id))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        user_msg = "Не удалось сохранить отчёт рекламы Ozon в базу."
        _journal(conn, path, status="ОШИБКА", message=str(exc),
                 upload_id=upload_id)
        return {"ok": False, "error": user_msg, "upload_id": upload_id}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "rows_loaded": updated + created,
        "updated": updated,
        "created": created,
        "sku_total": len(updates),
        "unmatched": unmatched,
        "dates": sorted({min_date.isoformat(), max_date.isoformat()}),
        "period_text": period_text,
        "skipped_empty": skipped_empty,
        "upload_id": upload_id,
    }
