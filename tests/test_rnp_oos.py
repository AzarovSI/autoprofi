"""Галочка «OOS» в РНП (19.11.2026): склад АВТОПРОФИ меньше чем на 7 дней продаж всех площадок. БД не нужна."""
import datetime as dt
import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-test-only")

from app import db
from app.routers import rnp_sales_router as rnp

TODAY = dt.date(2026, 11, 19)


def d(n):
    return TODAY - dt.timedelta(days=n)


def run(sales, stock):
    """sales: {таблица: [(дата, артикул, заказы|None)]}; stock: [(дата, артикул, qty)]."""
    def table_of(sql):
        return re.search(r"FROM (\w+)", sql).group(1)

    def rows_of(sql, params):
        t = table_of(sql)
        if t == "stock_daily":
            return [dict(date=a, seller_article=b, qty=c) for a, b, c in stock]
        rows = [dict(date=a, seller_article=b, orders_qty=c) for a, b, c in sales.get(t, [])]
        if "orders_qty IS NOT NULL" in sql:
            rows = [r for r in rows if r["orders_qty"] is not None]
        return rows

    def query_one(sql, params):
        rows = rows_of(sql, params)
        if "count(DISTINCT date)" in sql:
            lo, hi = params
            return {"n": len({r["date"] for r in rows if lo <= r["date"] <= hi})}
        dates = [r["date"] for r in rows if r["date"] <= params[0]]
        return {"d": max(dates) if dates else None}

    def query_all(sql, params):
        rows = rows_of(sql, params)
        if table_of(sql) == "stock_daily":
            return [dict(a=r["seller_article"], qty=r["qty"]) for r in rows if r["date"] == params[0]]
        lo, hi = params
        out = {}
        for r in rows:
            if lo <= r["date"] <= hi and r["orders_qty"] is not None:
                out[r["seller_article"]] = out.get(r["seller_article"], 0) + r["orders_qty"]
        return [dict(a=k, q=v) for k, v in out.items()]

    with patch.object(db, "query_one", side_effect=query_one), \
         patch.object(db, "query_all", side_effect=query_all), \
         patch.object(rnp, "_today", return_value=TODAY):
        return rnp.oos_flags()


class OosTests(unittest.TestCase):
    def test_days_from_all_marketplaces(self):
        """Дни — остаток / сумма среднедневных заказов площадок: по одной Ozon запаса хватает, со всеми — нет."""
        days = range(1, 11)                                       # 10 дней с заказами у каждой площадки
        sales = {"ozon_daily_sales": [(d(i), "A", 1) for i in days],
                 "wb_daily_sales": [(d(i), "A", 1) for i in days],
                 "ya_daily_sales": [(d(i), "A", 1) for i in days]}
        res = run(sales, [(d(1), "A", 18)])                       # 18 шт / 3 шт в день = 6 дней
        self.assertNotIn("A", res["ok"])
        self.assertEqual(res["low"]["A"], {"qty": 18.0, "per_day": 3.0, "days": 6.0})
        res = run({"ozon_daily_sales": sales["ozon_daily_sales"]}, [(d(1), "A", 18)])
        self.assertIn("A", res["ok"])                             # одна площадка — 18 дней, не OOS
        res = run(sales, [(d(1), "A", 21)])                       # ровно 7 дней — уже не OOS
        self.assertIn("A", res["ok"])

    def test_zero_missing_and_no_orders(self):
        """Остаток 0 — OOS и без заказов; нет в загрузке — OOS; есть остаток, нет заказов — не OOS."""
        res = run({}, [(d(1), "ZERO", 0), (d(1), "IDLE", 5)])
        self.assertEqual(res["zero"], ["ZERO"])
        self.assertEqual(res["ok"], ["IDLE"])
        self.assertFalse(res["stale"])
        self.assertNotIn("MISSING", res["ok"] + res["zero"] + list(res["low"]))
        # Разные написания одного артикула в складе 1С — один товар, остаток суммируется.
        res = run({"ozon_daily_sales": [(d(1), "B 1", 3)]}, [(d(1), "b  1", 0), (d(1), "B 1", 12)])
        self.assertEqual((res["zero"], res["low"]["B 1"]["qty"]), ([], 12.0))

    def test_stale_or_absent_stock(self):
        """Склад 1С не загружали больше 3 дней или не загружали вовсе — stale: OOS у всех товаров."""
        self.assertFalse(run({}, [(d(3), "A", 100)])["stale"])
        res = run({}, [(d(4), "A", 100)])
        self.assertTrue(res["stale"])
        self.assertEqual((res["ok"], res["stock_day"]), ([], d(4).isoformat()))
        self.assertTrue(run({}, [])["stale"])

    def test_window_and_days_with_data(self):
        """Окно — 30 календарных дней по сегодня; делитель — дни окна с данными, а не календарь.
        Дни без заказов (только отзывы, NULL) не считаются; заказы старше 30 дней (давно не загружавшаяся площадка) — тоже."""
        sales = {"ozon_daily_sales": [(d(1), "A", None), (d(2), "A", 10), (d(3), "A", 10), (d(40), "A", 1000)]}
        res = run(sales, [(d(1), "A", 30)])                       # 20 шт / 2 дня = 10 в день → 3 дня
        self.assertEqual(res["low"]["A"]["per_day"], 10.0)
        self.assertEqual(res["low"]["A"]["days"], 3.0)
        res = run({"ya_daily_sales": [(d(60), "A", 500)]}, [(d(1), "A", 5)])
        self.assertIn("A", res["ok"])                             # Яндекс два месяца назад — спроса нет


if __name__ == "__main__":
    unittest.main()
