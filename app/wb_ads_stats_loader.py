# -*- coding: utf-8 -*-
"""Загрузчик «Дашборд - рекламная статистика» Wildberries → раздел «Реклама» и реклама в РНП WB.

Файл владельца со статистикой рекламных кампаний WB за последние дни (обычно 5). Листы — по имени:
  • «Статистика кампаний»  → wb_ads_campaign_daily (день × кампания × артикул WB) — обязательный;
  • «Поисковые кластеры»   → wb_ads_cluster_daily  (день × кампания × запрос) — если есть;
  • «Воронка»              → wb_ads_funnel_daily   (день × артикул WB: карточка в целом) — если есть;
  • «_Справочник товаров»  → wb_ads_items          (артикул WB → артикул продавца) — если есть.
Колонки ищутся по заголовку. Загружаются все колонки листов.

Режим: дни листа перезаписываются целиком — WB дописывает заказы, корзины и отмены за прошлые дни,
а в новой выгрузке появляются строки за уже загруженные дни. Справочник — UPSERT по артикулу WB.
«CTR, %», «CR, %», конверсии и процент выкупа «Воронки» в файле — процентные пункты числом (формат
General; CTR сверен с клики / показы, конверсии — с корзины / переходы и заказы / корзины) → в БД доли 0..1. «Статус» — снимок на момент выгрузки.
Пустой «Артикул продавца» берётся из справочника этого файла, затем из wb_ads_items, затем
из wb_daily_sales.nm_id. Дубли ключа на листе отклоняют всю загрузку.

РНП WB (с 17.11.2026 вместо отчёта 1С «Статистика рекламных кампаний»): за дни листа «Статистика
кампаний» рекламные поля wb_daily_sales (RNP_COLS) сначала очищаются у всех товаров, затем пишутся
суммы по (дата, артикул продавца) — только товары справочника; строки нет — создаётся заглушка.
Расход = Σ «Затраты», CTR = Σ «Клики» / Σ «Показы» (доля; нет показов — NULL), CPC = Σ затрат / Σ кликов.
Берутся только строки рекламируемого товара («Товар в кампании» = «Да»): «Нет, связанный заказ» —
заказ другого товара через чужую кампанию, без затрат, показов и кликов (как в разделе «Реклама»).
Дни до первого файла остаются из 1С.

Фиксируется в report_uploads (marketplace='Wildberries', period_kind='wb_ads_stats').
"""
import os
import datetime

from python_calamine import CalamineWorkbook

from .util import canon_article

MP_NAME = "Wildberries"
PERIOD_KIND = "wb_ads_stats"
SHEET_CAMPAIGNS = "Статистика кампаний"
SHEET_CLUSTERS = "Поисковые кластеры"
SHEET_ITEMS = "_Справочник товаров"
SHEET_FUNNEL = "Воронка"

CAMPAIGN_REQUIRED = ["Дата", "ID кампании", "Артикул WB", "Товар в кампании",
                     "Показы", "Клики", "Затраты", "Заказы"]
CLUSTER_REQUIRED = ["Дата", "ID кампании", "Артикул WB", "Поисковый кластер",
                    "Клики", "Затраты"]
ITEMS_REQUIRED = ["Артикул WB", "Артикул продавца"]
FUNNEL_REQUIRED = ["Дата", "Артикул WB", "Переходы в карточку", "Корзины", "Заказы, шт", "Заказы, сумма"]

# (колонка БД, заголовок, тип): t — текст, n — число, p — проценты в пунктах → доля, ts — дата-время.
CAMPAIGN_COLS = [
    ("campaign_name", "Название кампании", "t"), ("placement", "Место показа", "t"),
    ("campaign_status", "Статус", "t"), ("payment_type", "Тип оплаты", "t"),
    ("bid_type", "Тип ставки", "t"), ("item_name", "Название товара", "t"),
    ("in_campaign_text", "Товар в кампании", "t"),
    ("views", "Показы", "n"), ("clicks", "Клики", "n"), ("ctr_pct", "CTR, %", "p"),
    ("cpc", "CPC", "n"), ("spend", "Затраты", "n"), ("atbs", "Корзины", "n"),
    ("orders", "Заказы", "n"), ("ordered_qty", "Заказано, шт", "n"),
    ("orders_sum", "Сумма заказов", "n"), ("cr_pct", "CR, %", "p"),
    ("cancels_qty", "Отмены, шт", "n"), ("currency", "Валюта", "t"),
    ("source_updated_at", "Обновлено", "ts"),
]
CLUSTER_COLS = [
    ("campaign_name", "Название кампании", "t"), ("placement", "Место показа", "t"),
    ("campaign_status", "Статус", "t"), ("payment_type", "Тип оплаты", "t"),
    ("item_name", "Название товара", "t"),
    ("views", "Показы", "n"), ("clicks", "Клики", "n"), ("ctr_pct", "CTR, %", "p"),
    ("cpc", "CPC", "n"), ("cpm", "CPM", "n"), ("spend", "Затраты", "n"),
    ("atbs", "Корзины", "n"), ("orders", "Заказы", "n"), ("ordered_qty", "Заказано, шт", "n"),
    ("avg_position", "Средняя позиция", "n"), ("source_updated_at", "Обновлено", "ts"),
]
FUNNEL_COLS = [
    ("item_name", "Название товара", "t"), ("brand", "Бренд", "t"), ("subject", "Предмет", "t"),
    ("visits", "Переходы в карточку", "n"), ("carts", "Корзины", "n"), ("deferred", "Отложенные", "n"),
    ("orders", "Заказы, шт", "n"), ("orders_sum", "Заказы, сумма", "n"),
    ("buyouts", "Выкупы, шт", "n"), ("buyouts_sum", "Выкупы, сумма", "n"),
    ("cancels", "Отмены и возвраты, шт", "n"), ("cancels_sum", "Отмены и возвраты, сумма", "n"),
    ("buyout_pct", "Процент выкупа", "p"), ("cr_cart_pct", "Конверсия в корзину, %", "p"),
    ("cr_order_pct", "Конверсия в заказ, %", "p"), ("avg_price", "Средняя цена", "n"),
    ("currency", "Валюта", "t"), ("source_updated_at", "Обновлено", "ts"),
]
PAGE = 500

# Реклама в РНП WB: колонка wb_daily_sales → поле листа «Статистика кампаний» для суммы по артикулу за день.
RNP_SUMS = [("ads_expense_rub", "spend"), ("ads_views", "views"), ("ads_clicks", "clicks"),
            ("ads_atbs", "atbs"), ("ads_orders", "orders"), ("ads_shks", "ordered_qty"),
            ("ads_sum_price", "orders_sum")]
RNP_COLS = [c for c, _ in RNP_SUMS] + ["ctr_pct", "ads_avg_cpc"]


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
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _build_colidx(header_row):
    """Заголовок → номер колонки (первое вхождение)."""
    idx = {}
    for c, v in enumerate(header_row):
        if v is not None and str(v).strip():
            idx.setdefault(str(v).strip(), c)
    return idx


def _to_ts(v):
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, datetime.date):
        return datetime.datetime(v.year, v.month, v.day)
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _to_id(v):
    n = _to_num(v)
    return int(n) if n is not None else None


def _text(v):
    s = str(v).strip() if v is not None else ""
    return s or None


def _value(v, kind):
    if kind == "t":
        return _text(v)
    if kind == "ts":
        return _to_ts(v)
    n = _to_num(v)
    return n / 100 if (kind == "p" and n is not None) else n


def _sheet(wb, name):
    return wb.get_sheet_by_name(name).to_python() if name in wb.sheet_names else None


def _read_book(path):
    wb = CalamineWorkbook.from_path(path)
    return {name: _sheet(wb, name) for name in (SHEET_CAMPAIGNS, SHEET_CLUSTERS, SHEET_FUNNEL, SHEET_ITEMS)}


def _missing(idx, required):
    return [n for n in required if n not in idx]


def _parse(data, key_cols, cols, ids=("ID кампании", "Артикул WB")):
    """Строки листа → ({ключ: {колонка: значение}}, пропущено, дубли).
    Ключ — (date, *ids[, текстовые key_cols]): (date, campaign_id, nm_id[, cluster]) или (date, nm_id) у «Воронки»."""
    idx = _build_colidx(data[0])
    rows, skipped, dups = {}, 0, 0
    for r in data[1:]:
        def cell(name):
            i = idx.get(name)
            return r[i] if (i is not None and i < len(r)) else None
        d = _to_date(cell("Дата"))
        nums = tuple(_to_id(cell(h)) for h in ids)
        extra = tuple(_text(cell(h)) for h in key_cols)
        if d is None or None in nums or None in extra:
            if any(v not in (None, "") for v in r):
                skipped += 1
            continue
        key = (d,) + nums + extra
        if key in rows:
            dups += 1
            continue
        row = {c: _value(cell(h), kind) for c, h, kind in cols if h in idx}
        row["seller_article"] = canon_article(cell("Артикул продавца")) or None
        row["nm_id"] = nums[-1]
        rows[key] = row
    return rows, skipped, dups


def _insert(cur, table, columns, rows, suffix=""):
    """Пакетная вставка: один INSERT … VALUES на PAGE строк; suffix — например, ON CONFLICT …"""
    tmpl = "(" + ",".join(["%s"] * len(columns)) + ")"
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES "
    for i in range(0, len(rows), PAGE):
        batch = rows[i:i + PAGE]
        cur.execute(sql + ",".join([tmpl] * len(batch)) + suffix, [v for r in batch for v in r])


def _write_rnp(cur, camps, dates, upload_id):
    """Реклама в РНП WB за дни `dates`: очистить рекламные поля и записать суммы по товарам справочника.
    Возвращает (строк записано, артикулов вне справочника)."""
    cur.execute("SELECT seller_article FROM catalog_items")
    catalog = {canon_article(a) for (a,) in cur.fetchall()}
    agg = {}
    for (d, _cid, _nm), v in camps.items():
        art = v["seller_article"]
        if art and (v.get("in_campaign_text") or "").lower() == "да":
            a = agg.setdefault((d, art), dict.fromkeys((f for _, f in RNP_SUMS), 0.0))
            for _, f in RNP_SUMS:
                a[f] += v.get(f) or 0
    rows, outside = [], set()
    for (d, art), a in sorted(agg.items()):
        if art not in catalog:
            outside.add(art)
            continue
        ctr = a["clicks"] / a["views"] if a["views"] > 0 else None
        cpc = a["spend"] / a["clicks"] if a["clicks"] > 0 else None
        rows.append([d, art] + [a[f] for _, f in RNP_SUMS] + [ctr, cpc, upload_id])
    cur.execute(f"UPDATE wb_daily_sales SET {', '.join(c + '=NULL' for c in RNP_COLS)} WHERE date = ANY(%s)",
                (dates,))
    _insert(cur, "wb_daily_sales", ["date", "seller_article"] + RNP_COLS + ["upload_id"], rows,
            " ON CONFLICT (date, seller_article) DO UPDATE SET " + ", ".join(f"{c}=EXCLUDED.{c}" for c in RNP_COLS))
    return len(rows), len(outside)


def _journal_error(conn, path, msg):
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO report_uploads (marketplace, year, source_file, rows_loaded, status, message, period_kind) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (MP_NAME, None, os.path.basename(path), 0, "ОШИБКА ФОРМАТА", msg[:500], PERIOD_KIND))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _fail(conn, path, msg):
    _journal_error(conn, path, msg)
    return {"ok": False, "error": msg}


def load_wb_ads_stats(conn, path):
    """Загрузить файл статистики рекламы WB. Возвращает dict {ok, campaign_rows, cluster_rows, items, dates, …}."""
    try:
        book = _read_book(path)
    except Exception as exc:
        return _fail(conn, path, f"Не удалось прочитать файл: {exc}")

    camp_data = book[SHEET_CAMPAIGNS]
    if not camp_data:
        return _fail(conn, path, f"ОШИБКА ФОРМАТА: нет листа «{SHEET_CAMPAIGNS}» или он пустой.")
    for data, name, required in ((camp_data, SHEET_CAMPAIGNS, CAMPAIGN_REQUIRED),
                                 (book[SHEET_CLUSTERS], SHEET_CLUSTERS, CLUSTER_REQUIRED),
                                 (book[SHEET_FUNNEL], SHEET_FUNNEL, FUNNEL_REQUIRED),
                                 (book[SHEET_ITEMS], SHEET_ITEMS, ITEMS_REQUIRED)):
        if data:
            miss = _missing(_build_colidx(data[0]), required)
            if miss:
                return _fail(conn, path, f"ОШИБКА ФОРМАТА: на листе «{name}» нет колонок: " + ", ".join(miss))

    camps, camp_skipped, camp_dups = _parse(camp_data, (), CAMPAIGN_COLS)
    clusters, cl_skipped, cl_dups = (_parse(book[SHEET_CLUSTERS], ("Поисковый кластер",), CLUSTER_COLS)
                                     if book[SHEET_CLUSTERS] else ({}, 0, 0))
    funnel, fn_skipped, fn_dups = (_parse(book[SHEET_FUNNEL], (), FUNNEL_COLS, ids=("Артикул WB",))
                                   if book[SHEET_FUNNEL] else ({}, 0, 0))
    if camp_dups or cl_dups or fn_dups:
        return _fail(conn, path, f"Дубли строк (дата + [кампания +] артикул WB [+ кластер]): кампании — {camp_dups}, "
                                 f"кластеры — {cl_dups}, воронка — {fn_dups}. Загрузка отклонена.")
    if not camps:
        return _fail(conn, path, "На листе «Статистика кампаний» нет ни одной строки с датой, кампанией и артикулом WB.")

    items = {}
    if book[SHEET_ITEMS]:
        idx = _build_colidx(book[SHEET_ITEMS][0])
        for r in book[SHEET_ITEMS][1:]:
            nm = _to_id(r[idx["Артикул WB"]]) if idx["Артикул WB"] < len(r) else None
            art = canon_article(r[idx["Артикул продавца"]]) if idx["Артикул продавца"] < len(r) else ""
            if nm is not None and art:
                name_i = idx.get("Название товара")
                items[nm] = (art, _text(r[name_i]) if name_i is not None and name_i < len(r) else None)

    camp_dates = sorted({k[0] for k in camps})
    cl_dates = sorted({k[0] for k in clusters})
    fn_dates = sorted({k[0] for k in funnel})
    all_dates = sorted(set(camp_dates) | set(cl_dates) | set(fn_dates))
    d0, d1 = all_dates[0], all_dates[-1]
    period_text = d0.strftime("%d.%m.%Y") if d0 == d1 else f"{d0.strftime('%d.%m.%Y')} - {d1.strftime('%d.%m.%Y')}"

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO report_uploads (marketplace, year, period_text, period_start, period_end, "
            "source_file, status, period_kind) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (MP_NAME, d0.year, period_text, d0, d1, os.path.basename(path), "В ПРОЦЕССЕ", PERIOD_KIND))
        upload_id = cur.fetchone()[0]
    conn.commit()

    try:
        with conn.cursor() as cur:
            # Артикул продавца для строк без него: справочник файла → wb_ads_items → воронка РНП.
            sheets = list(camps.values()) + list(clusters.values()) + list(funnel.values())
            need = {v["nm_id"] for v in sheets if not v["seller_article"]}
            known = {nm: art for nm, (art, _) in items.items()}
            rest = sorted(need - set(known))
            if rest:
                cur.execute("SELECT nm_id, seller_article FROM wb_ads_items WHERE nm_id = ANY(%s)", (rest,))
                known.update(dict(cur.fetchall()))
                rest = sorted(need - set(known))
            if rest:
                cur.execute("SELECT DISTINCT ON (nm_id) nm_id, seller_article FROM wb_daily_sales "
                            "WHERE nm_id = ANY(%s) AND seller_article IS NOT NULL ORDER BY nm_id, date DESC", (rest,))
                known.update({nm: canon_article(a) for nm, a in cur.fetchall()})
            no_article = set()
            for v in sheets:
                if not v["seller_article"]:
                    v["seller_article"] = known.get(v["nm_id"])
                    if not v["seller_article"]:
                        no_article.add(v["nm_id"])

            item_rows = list(items.items())
            for i in range(0, len(item_rows), PAGE):
                batch = item_rows[i:i + PAGE]
                cur.execute(
                    "INSERT INTO wb_ads_items (nm_id, seller_article, item_name, upload_id) VALUES "
                    + ",".join(["(%s,%s,%s,%s)"] * len(batch))
                    + " ON CONFLICT (nm_id) DO UPDATE SET seller_article=EXCLUDED.seller_article, "
                      "item_name=EXCLUDED.item_name, upload_id=EXCLUDED.upload_id, updated_at=now()",
                    [v for nm, (art, name) in batch for v in (nm, art, name, upload_id)])

            cur.execute("DELETE FROM wb_ads_campaign_daily WHERE date = ANY(%s)", (camp_dates,))
            ccols = [c for c, _, _ in CAMPAIGN_COLS]
            _insert(cur, "wb_ads_campaign_daily",
                    ["date", "campaign_id", "nm_id", "in_campaign", "seller_article", "upload_id"] + ccols,
                    [[d, cid, nm, (v.get("in_campaign_text") or "").lower() == "да", v["seller_article"], upload_id]
                     + [v.get(c) for c in ccols] for (d, cid, nm), v in camps.items()])
            if clusters:
                cur.execute("DELETE FROM wb_ads_cluster_daily WHERE date = ANY(%s)", (cl_dates,))
                kcols = [c for c, _, _ in CLUSTER_COLS]
                _insert(cur, "wb_ads_cluster_daily",
                        ["date", "campaign_id", "nm_id", "cluster", "seller_article", "upload_id"] + kcols,
                        [[d, cid, nm, cl, v["seller_article"], upload_id] + [v.get(c) for c in kcols]
                         for (d, cid, nm, cl), v in clusters.items()])
            if funnel:
                cur.execute("DELETE FROM wb_ads_funnel_daily WHERE date = ANY(%s)", (fn_dates,))
                fcols = [c for c, _, _ in FUNNEL_COLS]
                _insert(cur, "wb_ads_funnel_daily", ["date", "nm_id", "seller_article", "upload_id"] + fcols,
                        [[d, nm, v["seller_article"], upload_id] + [v.get(c) for c in fcols]
                         for (d, nm), v in funnel.items()])

            rnp_rows, rnp_outside = _write_rnp(cur, camps, camp_dates, upload_id)

            msg = (f"Даты {period_text}; кампании: {len(camps)} строк за {len(camp_dates)} дн.; "
                   f"кластеры: {len(clusters)} строк; воронка: {len(funnel)} строк; справочник: {len(items)} товаров; "
                   f"РНП WB: {rnp_rows} строк (дата, артикул)")
            if rnp_outside:
                msg += f"; вне справочника (в РНП не попали): {rnp_outside}"
            if camp_skipped or cl_skipped or fn_skipped:
                msg += f"; пропущено строк без даты/кампании/артикула: {camp_skipped + cl_skipped + fn_skipped}"
            if no_article:
                msg += f"; без артикула продавца (артикулы WB): {len(no_article)}"
            cur.execute("UPDATE report_uploads SET rows_loaded=%s, status='OK', message=%s WHERE id=%s",
                        (len(camps) + len(clusters) + len(funnel), msg[:500], upload_id))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE report_uploads SET status='ОШИБКА', message=%s WHERE id=%s",
                            (str(exc)[:500], upload_id))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "error": "Не удалось сохранить статистику рекламы WB в базу.", "upload_id": upload_id}

    return {
        "ok": True,
        "marketplace": MP_NAME,
        "campaign_rows": len(camps),
        "cluster_rows": len(clusters),
        "funnel_rows": len(funnel),
        "items": len(items),
        "rnp_rows": rnp_rows,
        "rnp_outside": rnp_outside,
        "dates": [d.isoformat() for d in camp_dates],
        "period_text": period_text,
        "skipped": camp_skipped + cl_skipped + fn_skipped,
        "no_article": sorted(no_article),
        "upload_id": upload_id,
    }
