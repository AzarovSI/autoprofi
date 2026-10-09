# -*- coding: utf-8 -*-
"""Разовая загрузка истории РНП Ozon из таблицы владельца в ozon_daily_sales.

Зачем: после потери БД в Yandex Cloud (docs/history.md) дневная история РНП Ozon
восстанавливается из рабочей таблицы владельца «РНП-ТД OZON 2026». Сопоставление
метрик и правила согласованы с владельцем 09–10.10.2026 (docs/data_sources.md, 3а).

    .venv/bin/python db/import_rnp_ozon_history.py <файл.xlsx>            — разбор и сверка, без записи
    .venv/bin/python db/import_rnp_ozon_history.py <файл.xlsx> --write    — запись (service=autoprofi_owner,
                                                                           другая БД — --dsn)

Устройство листа «РНП-АП Ozon»: строка 4 — даты (с колонки M), строка товара — артикул
в колонке J (там же «Заказы, шт»), SKU Ozon в L; ниже — строки метрик с подписью в J.
Служебные колонки A–H местами сбиты, поэтому товар определяется по J, а не по G.
"""
import argparse
import collections
import datetime
import os
import statistics
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.util import canon_article  # noqa: E402

SHEET = "РНП-АП Ozon"
FIRST_COL = 13          # M — первая дата
DATE_ROW = 4

# Подпись в таблице → поле ozon_daily_sales (None — не загружается, только сверка)
LABELS = {
    "Сумма заказов, руб.": "orders_rub",
    "Ср. загружаемая цена": None,
    "Цена для покупателя": None,
    "Средняя цена конкурентов": "comp_price_avg",
    "Минимальная цена конкурентов": "comp_price_min",
    "Отменено заказов": "cancels_qty",
    "Процент отмен": None,
    "Соинвест": "spp_pct",
    "Индекс цены, Pi": "price_index_pi",
    "ДРР, общая": None,
    "Расходы на рекламу": "ads_expense_rub",
    "Переходы в карточку, общий": "card_visits",
    "CTR, с рк": "ctr_pct",
    "CR, корзина общий": "cr_cart_pct",
    "CR, в заказ общий": "cr_order_pct",
    "Средняя позиция в поиске": "avg_position",
    "Среднее время доставки, часов": None,   # поле занято «Количеством отзывов»
    "Рейтинг товара": "rating",
    "Остаток на складе ОЗОН, шт.": "stock_ozon_qty",
    "Остаток на складе АВТОПРОФИ, шт.": "stock_ap_qty",
}
# Согласовано с владельцем: опечатки в таблице, артикул сверен по SKU с MPPROFIT
ARTICLE_FIX = {"GRN-400 BK/BE": "GRN-400 BR/BE", "MLT-320G BK/BK": "MLT-320G BK"}

ZERO_IS_REAL = {"orders_qty", "orders_rub", "cancels_qty", "ads_expense_rub",
                "card_visits", "stock_ozon_qty", "stock_ap_qty"}
ZERO_IS_EMPTY = {"price_index_pi", "rating", "avg_position", "comp_price_avg",
                 "comp_price_min", "spp_pct"}
DB_COLS = ["orders_qty", "orders_rub", "cancels_qty", "price_index_pi", "spp_pct",
           "card_visits", "cr_cart_pct", "cr_order_pct", "avg_position", "rating",
           "stock_ozon_qty", "comp_price_avg", "comp_price_min", "ads_expense_rub",
           "ctr_pct", "stock_ap_qty"]


def norm(s):
    return " ".join(str(s).split()).strip() if s is not None else ""


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def read_sheet(path):
    """→ dates, products{art: {sku, row, label, orders[], m{label: [(value, fmt)]}}}, groups, problems."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[SHEET]
    dates, products, groups, problems = [], collections.OrderedDict(), 0, []
    cur = None
    for r, row in enumerate(ws.iter_rows(min_row=DATE_ROW), DATE_ROW):
        cell = lambda c: row[c - 1] if len(row) >= c else None
        val = lambda c: (cell(c).value if cell(c) is not None else None)
        if r == DATE_ROW:
            for c in range(FIRST_COL, len(row) + 1):
                v = val(c)
                if not isinstance(v, datetime.datetime):
                    break
                dates.append(v.date())
            continue
        J, G = norm(val(10)), norm(val(7))
        if not J:
            continue
        cells = [(cell(c).value, cell(c).number_format) if cell(c) is not None else (None, None)
                 for c in range(FIRST_COL, FIRST_COL + len(dates))]
        if J in LABELS:
            if cur is None:
                problems.append(f"строка {r}: метрика «{J}» без товара")
            elif J in cur["m"]:
                problems.append(f"строка {r}: повтор метрики «{J}» у {cur['label']}")
            else:
                cur["m"][J] = cells
        elif G:
            art = canon_article(ARTICLE_FIX.get(J, J))
            if art in products:
                problems.append(f"строка {r}: повтор артикула {art}")
            sku = norm(val(12)).split(".")[0]
            cur = {"label": J, "row": r, "sku": int(sku) if sku.isdigit() else None,
                   "orders": [x for x, _ in cells], "m": {}}
            products[art] = cur
        else:
            groups += 1
            cur = None
    # Строка с заполненной G, но без метрик под ней — это группа (напр. «СОЛНЦЕЗАЩИТНЫЕ ЭКРАНЫ»)
    for art in [a for a, p in products.items() if not p["m"]]:
        del products[art]
        groups += 1
    for art, p in products.items():
        miss = [l for l in LABELS if l not in p["m"]]
        if miss:
            problems.append(f"{art}: нет строк {miss}")
    return dates, products, groups, problems


def closest(x, cands, target):
    return min(cands, key=lambda c: abs(c - target))


def convert(dates, products):
    """→ rows[(date, art, sku, {col: value})], stats, check."""
    st = collections.Counter()
    notes = collections.defaultdict(list)
    rows = []
    for art, p in products.items():
        m = {l: [x for x, _ in cells] for l, cells in p["m"].items()}
        f = {l: [fm for _, fm in cells] for l, cells in p["m"].items()}
        for i, d in enumerate(dates):
            o = num(p["orders"][i])
            if isinstance(p["orders"][i], str) and p["orders"][i].strip():
                notes["текст в заказах"].append((art, d, p["orders"][i]))
            raw = {l: m[l][i] for l in m}
            rec = {"orders_qty": o}
            # Сломанные формулы: все метрики = числу заказов → берём только заказы
            if o and all(raw[l] == o for l in raw):
                st["сломанный день: только заказы"] += 1
                notes["сломанные дни"].append((art, d))
                rows.append((d, art, p["sku"], rec))
                continue
            for label, col in LABELS.items():
                if not col:
                    continue
                x, fmt = raw[label], f[label][i]
                if isinstance(x, str):
                    x = fix_text(x, art, d, label, m[label], i, notes)
                x = num(x)
                if x is None:
                    rec[col] = None
                    continue
                if x == 0 and col not in ZERO_IS_REAL:
                    if col in ZERO_IS_EMPTY:
                        x = None
                    elif col in ("cr_cart_pct", "cr_order_pct") and not num(raw["Переходы в карточку, общий"]):
                        x = None
                    elif col == "ctr_pct" and not num(raw["Расходы на рекламу"]):
                        x = None
                if x is not None and col == "spp_pct":
                    x = spp_value(x, raw, st, notes, art, d)
                elif x is not None and col == "cr_order_pct":
                    x = cr_order_value(x, fmt, o, num(raw["Переходы в карточку, общий"]),
                                       st, notes, art, d)
                elif x is not None and col in ("ctr_pct", "cr_cart_pct") and "%" not in (fmt or ""):
                    st[f"{col}: ячейка не в формате %"] += 1
                    notes[f"{col} не в %"].append((art, d, x, fmt))
                rec[col] = x
            rows.append((d, art, p["sku"], rec))
            verify(rec, raw, st, notes, art, d)
    return rows, st, notes


def verify(rec, raw, st, notes, art, d):
    """Сверка: то, что сайт посчитает из загруженного, против чисел таблицы."""
    o, rub, canc, ads, spp = (rec.get(k) for k in
                              ("orders_qty", "orders_rub", "cancels_qty", "ads_expense_rub", "spp_pct"))
    aup = rub / o if (rub is not None and o) else None
    checks = [
        ("Ср. загружаемая цена", aup, num(raw["Ср. загружаемая цена"]), 1.0),
        ("Цена для покупателя", aup * (1 - spp) if (aup is not None and spp is not None) else None,
         num(raw["Цена для покупателя"]), 1.0),
        ("Процент отмен", canc / o if (canc is not None and o) else None, num(raw["Процент отмен"]), 0.001),
        ("ДРР, общая", ads / rub if (ads is not None and rub) else None, num(raw["ДРР, общая"]), 0.001),
    ]
    for name, mine, theirs, tol in checks:
        if mine is None or not theirs:
            continue
        st[f"сверка {name}: всего"] += 1
        if abs(mine - theirs) > tol:
            st[f"сверка {name}: расходится"] += 1
            notes[f"сверка {name}"].append((art, d, round(mine, 4), theirs))


def fix_text(x, art, d, label, series, i, notes):
    """Текст в числовой ячейке. «4?1%» — опечатка: 41% или 4,1%, решают соседние дни."""
    s = x.strip()
    if label == "CTR, с рк" and s == "4?1%":
        nb = [v for v in series[max(0, i - 7):i + 8] if num(v) and v > 0]
        med = statistics.median(nb) if nb else None
        val = closest(None, [0.41, 0.041], med) if med is not None else None
        notes["текст в ячейке"].append((art, d, label, s, f"соседи ≈ {med:.3f}" if med else "соседей нет", val))
        return val
    notes["текст в ячейке"].append((art, d, label, s, "оставлено пустым", None))
    return None


def spp_value(x, raw, st, notes, art, d):
    """Соинвест: в таблице и проценты числом (45,47), и доли (0,4547); формат ячейки
    не помогает (в ячейках с форматом % лежит и 49,33). Проверено 10.10.2026:
    все значения >1 — проценты (16 060 дней сошлись с ценами: цена покупателя =
    ср. цена × (1 − СПП)); значения ≤1 — доли (у тех же товаров обычно 30–50%).
    Где в строке есть обе цены — масштаб сверяется и с ними."""
    v = x / 100 if x > 1 else x
    st["spp: " + ("процент числом → /100" if x > 1 else "доля как есть")] += 1
    if x == 1:
        notes["spp ровно 1 (100% или 1%?)"].append((art, d))
    aup, bp = num(raw["Ср. загружаемая цена"]), num(raw["Цена для покупателя"])
    if aup and bp and aup > 0:
        implied = 1 - bp / aup
        st["spp: сверено с ценами"] += 1
        if abs(v - implied) > 0.01:
            st["spp: расходится с ценами >1 п.п."] += 1
            notes["spp расходится с ценами"].append((art, d, x, round(implied, 4)))
    return v


def cr_order_value(x, fmt, o, visits, st, notes, art, d):
    """CR в заказ: в % формате — доля; иначе масштаб по заказам / переходам."""
    if "%" in (fmt or ""):
        return x
    if visits:
        v = closest(x, [x, x / 100], (o or 0) / visits)
        st["cr_order не в %: по заказам/переходам → " + ("доля" if v == x else "процент")] += 1
        return v
    v = x / 100 if x > 1 else x
    st["cr_order не в %: переходов нет"] += 1
    notes["cr_order не определён"].append((art, d, x, fmt, v))
    return v


def write(conn, rows, dates, source_file):
    """Одна транзакция: справочник (новые артикулы — нераспределённые), журнал
    report_uploads, строки ozon_daily_sales. Диапазон дат должен быть пуст."""
    arts = sorted({a for _, a, _, _ in rows})
    with conn.transaction():
        cur = conn.cursor()
        n = cur.execute("SELECT count(*) FROM ozon_daily_sales WHERE date BETWEEN %s AND %s",
                        (dates[0], dates[-1])).fetchone()[0]
        if n:
            raise SystemExit(f"В ozon_daily_sales уже {n} строк за {dates[0]}–{dates[-1]} — запись отменена")
        known = {r[0] for r in cur.execute("SELECT seller_article FROM catalog_items")}
        new = [a for a in arts if a not in known]
        cur.executemany("INSERT INTO catalog_items (seller_article) VALUES (%s) "
                        "ON CONFLICT (seller_article) DO NOTHING", [(a,) for a in new])
        cur.executemany("INSERT INTO catalog_marketplace (seller_article, marketplace) VALUES (%s, 'Ozon') "
                        "ON CONFLICT (seller_article, marketplace) DO NOTHING", [(a,) for a in arts])
        names = dict(cur.execute("SELECT seller_article, sample_name FROM catalog_items "
                                 "WHERE seller_article = ANY(%s)", (arts,)).fetchall())
        upload_id = cur.execute(
            "INSERT INTO report_uploads (marketplace, year, period_text, period_start, period_end, "
            "source_file, status, period_kind, message) VALUES ('Ozon', %s, %s, %s, %s, %s, "
            "'В ПРОЦЕССЕ', 'daily', %s) RETURNING id",
            (dates[0].year, f"{dates[0]:%d.%m.%Y}–{dates[-1]:%d.%m.%Y}", dates[0], dates[-1],
             source_file, "Разовая загрузка истории РНП Ozon из таблицы владельца "
             "(db/import_rnp_ozon_history.py, docs/data_sources.md 3а)")).fetchone()[0]
        cols = ["date", "seller_article", "sku_ozon", "item_name"] + DB_COLS + ["upload_id"]
        with cur.copy(f"COPY ozon_daily_sales ({', '.join(cols)}) FROM STDIN") as cp:
            for d, a, sku, rec in rows:
                cp.write_row([d, a, sku, names.get(a)] + [rec.get(c) for c in DB_COLS] + [upload_id])
        cur.execute("UPDATE report_uploads SET status='OK', rows_loaded=%s WHERE id=%s", (len(rows), upload_id))
    return upload_id, new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dsn", default="service=autoprofi_owner")
    a = ap.parse_args()
    dates, products, groups, problems = read_sheet(a.xlsx)
    print(f"Даты: {dates[0]:%d.%m.%Y}–{dates[-1]:%d.%m.%Y}, {len(dates)} дн.; товаров {len(products)}; строк групп {groups}")
    for pr in problems:
        print("  ПРОБЛЕМА:", pr)
    rows, st, notes = convert(dates, products)
    print(f"Строк день×артикул: {len(rows)}")
    for k, v in sorted(st.items()):
        print(f"  {k}: {v}")
    for k, v in notes.items():
        print(f"  {k}: {len(v)}; примеры: {v[:5]}")
    if problems:
        raise SystemExit("Есть проблемы разбора — запись невозможна")
    if a.write:
        import psycopg
        with psycopg.connect(a.dsn) as conn:
            upload_id, new = write(conn, rows, dates, "РНП-ТД OZON 2026 - рабочий (2).xlsx")
        print(f"Записано: {len(rows)} строк, upload_id={upload_id}; новые артикулы в справочнике: {new}")


if __name__ == "__main__":
    main()
