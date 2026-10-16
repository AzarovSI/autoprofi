"""Юнит-экономика по менеджеру: пересчёт групп и «Итогов», доля общих удержаний (без БД)."""
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-test-only")

from app import db
from app.routers import rnp_router as ue

ADMIN = {"id": 1, "is_admin": True}
WEEK = dict(year=2026, week=40, period_text="Неделя 40", period_start=__import__("datetime").date(2026, 9, 28),
            period_end=__import__("datetime").date(2026, 10, 4))


def fact(art, mgr, revenue, qty, orders, profit, payout, **kw):
    return dict(year=2026, week=40, seller_article=art, item_name="Товар " + art,
                category_l1="ECOM", category_l2="Г", category_l3="Т", status="CORE", manager=mgr,
                revenue=revenue, sales_qty=qty, orders_qty=orders, profit=profit, payout=payout, **kw)


# A — M1, B — M2, C — без менеджера (в «Итогах» магазина, ни у кого из менеджеров)
OZ_ROWS = [fact("A", "M1", 1000, 2, 3, 200, 700), fact("B", "M2", 3000, 3, 4, 600, 2000),
           fact("C", None, 600, 1, 1, 50, 300)]
OZ_NOART = [dict(year=2026, week=40, holds=400, pay=-400, wpay=None, prof=-400)]


def tree(mp, rows, noart, manager=None, wpa=None):
    def query(sql, params=None):
        if "SELECT DISTINCT year" in sql:
            return [dict(WEEK)]
        if "row_type = 'Товар'" in sql:
            return [dict(r) for r in rows]
        if "SUM(wb_profit_with_ads) AS wpa" in sql:
            return wpa or []
        if "row_type = 'Общие удержания'" in sql:
            return [dict(r) for r in noart]
        if "FROM catalog_marketplace cm" in sql:
            return [dict(seller_article=r["seller_article"], manager=r["manager"]) for r in rows]
        if "FROM sales_plan" in sql:
            return [dict(seller_article=a, year=2026, month=10, q=31) for a in ("A", "B")]
        return []
    with patch.object(db, "query_all", side_effect=query), patch.object(db, "query_one", return_value=None):
        return json.loads(ue.rnp_tree(marketplace=mp, period="week", manager=manager, user=ADMIN).body)


def root(res, key):
    return res["tree"]["cells"]["2026-40"][key]


class ManagerSplitTests(unittest.TestCase):
    def test_managers_plus_unassigned_equal_store(self):
        store = tree("ozon", OZ_ROWS, OZ_NOART)
        m1 = tree("ozon", OZ_ROWS, OZ_NOART, "M1")
        m2 = tree("ozon", OZ_ROWS, OZ_NOART, "M2")
        # общие удержания 400 делятся 1000 : 3000 → 100 и 300
        self.assertEqual((root(m1, "profit"), root(m2, "profit")), (100, 300))
        self.assertEqual((root(m1, "p_payout"), root(m2, "p_payout")), (600, 1700))
        self.assertEqual((root(m1, "noart_exp_rub"), root(m2, "noart_exp_rub")), (100, 300))
        self.assertEqual((root(m1, "noart_pay_rub"), root(m2, "noart_pay_rub"), root(store, "noart_pay_rub")),
                         (100, 300, 400))   # строка «Удержания без разбивки, руб» (тестовые вкладки)
        self.assertEqual((m1["noart_share"]["2026-40"], m2["noart_share"]["2026-40"]), (0.25, 0.75))
        for key, unassigned in (("profit", 50), ("p_payout", 300), ("p_revenue", 600), ("p_orders_rub", 600)):
            with self.subTest(key=key):
                self.assertAlmostEqual(root(m1, key) + root(m2, key) + unassigned, root(store, key))
        # маржинальности — из сумм менеджера
        self.assertAlmostEqual(root(m1, "rnp"), 100 / 1000)
        self.assertAlmostEqual(root(m1, "p_net_margin_pct"), 100 / 600)

    def test_manager_view_shows_only_his_products_full_filter_list(self):
        m1 = tree("ozon", OZ_ROWS, OZ_NOART, "M1")
        leaves = m1["tree"]["children"][0]["children"][0]["children"][0]["children"]
        self.assertEqual([lf["leaf_info"]["seller_article"] for lf in leaves], ["A"])
        self.assertEqual(m1["managers"], ["M1", "M2"])
        self.assertEqual(m1["manager"], "M1")
        self.assertEqual(m1["undistributed"]["count"], 0)       # C без менеджера — не его
        self.assertEqual(root(m1, "p_plan_qty"), 4)              # план только по его товару
        self.assertEqual(root(tree("ozon", OZ_ROWS, OZ_NOART), "p_plan_qty"), 8)

    def test_ozon_group_orders_are_sum_of_products(self):
        store = tree("ozon", OZ_ROWS, OZ_NOART)
        grp = store["tree"]["children"][0]["cells"]["2026-40"]["p_orders_rub"]
        self.assertEqual(grp, 3 * 500 + 4 * 1000)                # шт × средняя цена каждого товара
        self.assertEqual(root(store, "p_orders_rub"), 1500 + 4000 + 600)
        self.assertIsNone(store["noart_share"])

    def test_wb_profit_with_ads_share(self):
        rows = [fact("A", "M1", 1000, 2, 3, 250, 700, wb_ads_total=10, wb_profit_with_ads=200, wb_payout_with_ads=690),
                fact("B", "M2", 3000, 3, 4, 650, 2000, wb_ads_total=10, wb_profit_with_ads=600, wb_payout_with_ads=1990)]
        noart = [dict(year=2026, week=40, holds=400, pay=-400, wpay=-400, prof=-400)]
        wpa = [dict(year=2026, week=40, wpa=-400)]
        store = tree("wb", rows, noart, wpa=wpa)
        m1 = tree("wb", rows, noart, "M1", wpa=wpa)
        m2 = tree("wb", rows, noart, "M2", wpa=wpa)
        self.assertEqual((root(store, "profit"), root(m1, "profit"), root(m2, "profit")), (400, 100, 300))
        self.assertEqual(root(m1, "p_payout") + root(m2, "p_payout"), root(store, "p_payout"))

    def test_no_sales_in_column_splits_holds_equally(self):
        rows = [fact("A", "M1", 0, 0, 1, -10, -10), fact("B", "M2", 0, 0, 1, -10, -10)]
        m1 = tree("ozon", rows, OZ_NOART, "M1")
        self.assertEqual(m1["noart_share"]["2026-40"], 0.5)
        self.assertEqual(root(m1, "profit"), -10 - 200)


if __name__ == "__main__":
    unittest.main()
