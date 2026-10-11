# -*- coding: utf-8 -*-
"""Общий остаток склада АВТОПРОФИ (stock_daily) за прошлые дни из таблиц РНП Ozon и WB.

Зачем: stock_daily — общий источник «Остатка на складе АВТОПРОФИ» для всех РНП (обычно его
пишет отчёт остатков 1С). После потери БД (docs/history.md) история восстанавливается из
строк «Остаток на складе АВТОПРОФИ, шт.» двух таблиц владельца. Таблицы между собой
расходятся (в основном ноль в одной из них), поэтому значения сводятся по правилу,
согласованному с владельцем 11.10.2026 (docs/data_sources.md, 3а):
  1) значения совпадают → оно;
  2) в одной таблице 0 (или пусто), в другой > 0 → значение > 0;
  3) обе > 0 и разные → ближе к медиане соседних дней (обеих таблиц), без соседей — WB;
  4) товар есть только в одной таблице → из неё.
Одиночные всплески не фильтруются (владелец: погрешности прошлых периодов допустимы).

    .venv/bin/python db/build_stock_daily_history.py <ozon.xlsx> <wb.xlsx>           — сводка, без записи
    .venv/bin/python db/build_stock_daily_history.py <ozon.xlsx> <wb.xlsx> --write   — запись (service=autoprofi_owner)

Запись — одна транзакция: stock_daily (диапазон дат должен быть пуст) и, как делает загрузка
отчёта 1С, синхронизация ozon_daily_sales.stock_ap_qty для существующих строк.
"""
import argparse
import collections
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import import_rnp_history as R  # noqa: E402

LABEL = "Остаток на складе АВТОПРОФИ, шт."
ONE = datetime.timedelta(days=1)


def series(path, mp):
    cfg = R.MP[mp]
    dates, products, _, problems = R.read_sheet(path, cfg)
    if problems:
        raise SystemExit(f"{mp}: проблемы разбора: {problems[:3]}")
    return {art: {d: R.num(x) for d, (x, _) in zip(dates, p["m"][LABEL])} for art, p in products.items()}


def merge(O, W):
    out, st = {}, collections.Counter()
    for art in sorted(set(O) | set(W)):
        o_s, w_s = O.get(art, {}), W.get(art, {})
        for d in sorted(set(o_s) | set(w_s)):
            o, w = o_s.get(d), w_s.get(d)
            if art not in O or d not in o_s:
                v, rule = w, "4: только WB"
            elif art not in W or d not in w_s:
                v, rule = o, "4: только Ozon"
            elif o is None and w is None:
                continue
            elif o is not None and w is not None and abs(o - w) < 0.5:
                v, rule = w, "1: совпадает"
            elif not o or not w:
                v, rule = (o or w or 0), "2: ноль/пусто в одной"
            else:
                nb = sorted(x for x in (w_s.get(d - ONE), o_s.get(d - ONE), w_s.get(d + ONE), o_s.get(d + ONE)) if x)
                if nb:
                    ref = nb[len(nb) // 2]
                    v = w if abs(w - ref) <= abs(o - ref) else o
                else:
                    v = w
                rule = "3: обе >0, ближе к соседям " + ("WB" if v == w else "Ozon")
            if v is None:
                continue
            out[(d, art)] = v
            st[rule] += 1
    return out, st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ozon_xlsx")
    ap.add_argument("wb_xlsx")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dsn", default="service=autoprofi_owner")
    a = ap.parse_args()
    O, W = series(a.ozon_xlsx, "ozon"), series(a.wb_xlsx, "wb")
    stock, st = merge(O, W)
    dates = sorted({d for d, _ in stock})
    print(f"Общий остаток: {len(stock)} значений, {len({x for _, x in stock})} товаров, "
          f"{dates[0]:%d.%m.%Y}–{dates[-1]:%d.%m.%Y}")
    for k, v in sorted(st.items()):
        print(f"  {k}: {v}")
    changed = sum(1 for (d, art), v in stock.items()
                  if art in O and d in O[art] and O[art][d] is not None and abs(O[art][d] - v) >= 0.5)
    print(f"  в РНП Ozon изменится значений: {changed}")
    if not a.write:
        return
    import psycopg
    with psycopg.connect(a.dsn) as conn, conn.transaction():
        cur = conn.cursor()
        n = cur.execute("SELECT count(*) FROM stock_daily WHERE date BETWEEN %s AND %s",
                        (dates[0], dates[-1])).fetchone()[0]
        if n:
            raise SystemExit(f"В stock_daily уже {n} строк за этот период — запись отменена")
        with cur.copy("COPY stock_daily (date, seller_article, qty) FROM STDIN") as cp:
            for (d, art), v in sorted(stock.items()):
                cp.write_row([d, art, v])
        cur.execute("""UPDATE ozon_daily_sales o SET stock_ap_qty = s.qty
                       FROM stock_daily s
                       WHERE s.date = o.date AND s.seller_article = o.seller_article
                         AND s.date BETWEEN %s AND %s
                         AND o.stock_ap_qty IS DISTINCT FROM s.qty""", (dates[0], dates[-1]))
        print(f"Записано в stock_daily: {len(stock)}; обновлено в ozon_daily_sales: {cur.rowcount}")


if __name__ == "__main__":
    main()
