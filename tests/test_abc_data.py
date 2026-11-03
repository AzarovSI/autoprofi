"""ABC (`/api/abc/data`, без БД): рекламный расход положительный для всех площадок; неполные месяцы.

Ozon и Яндекс хранят `promo_total` со знаком минус, WB `wb_ads_total` — плюсом; клиент считает
ДРР = реклама / выручка, поэтому до 19.10.2026 ДРР Ozon и Яндекса в ABC была отрицательной.
"""
import datetime
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-test-only")

from app import db
from app.routers import abc_router as abc

ADMIN = {"id": 1, "is_admin": True}


def row(sku, ad_spend, revenue=1000.0):
    return dict(year=2026, month=9, sku=sku, name="Товар " + sku, revenue=revenue, profit=100.0,
                margin=0.1, payout=600.0, sales_qty=5, orders_qty=6, returns_qty=1, ad_spend=ad_spend,
                status="CORE")


class AdSpendSignTest(unittest.TestCase):
    def data(self, oz, wb, ya):
        def fake(sql, *a, **kw):
            if sql is abc.SQL_OZON:
                return oz
            if sql is abc.SQL_WB:
                return wb
            if sql is abc.SQL_YA:
                return ya
            return []
        with patch.object(db, "query_all", side_effect=fake):
            return abc.abc_data(user=ADMIN)

    def test_ad_spend_positive_on_every_marketplace(self):
        res = self.data([row("OZ-1", -80.0)], [row("WB-1", 60.0)], [row("YA-1", -15.0)])
        got = {r["mp"]: r["ad_spend"] for r in res["rows"]}
        self.assertEqual(got, {"ozon": 80.0, "vb": 60.0, "ya": 15.0})

    def test_no_ads_is_plain_zero(self):
        res = self.data([row("OZ-1", None)], [row("WB-1", 0)], [row("YA-1", 0.0)])
        for r in res["rows"]:
            self.assertEqual(r["ad_spend"], 0.0)
            self.assertEqual(str(r["ad_spend"]), "0.0")  # не «-0.0»

    def test_payout_without_yandex(self):
        """«К оплате на р/с» — у Ozon и WB; выплату Яндекса не сверяли — None, а не 0."""
        res = self.data([row("OZ-1", -1.0)], [row("WB-1", 1.0)], [row("YA-1", -1.0)])
        got = {r["mp"]: r["payout"] for r in res["rows"]}
        self.assertEqual(got, {"ozon": 600.0, "vb": 600.0, "ya": None})


class PartialMonthTest(unittest.TestCase):
    """Неполный месяц: «факт по дату» последней месячной загрузки; текущий календарный — всегда."""

    def months(self, uploads, today):
        def fake(sql, *a, **kw):
            if sql is abc.SQL_OZON:
                return [dict(row("OZ-1", -1.0), month=9), dict(row("OZ-1", -1.0), month=10)]
            if sql is abc.SQL_WB:
                return [dict(row("WB-1", 1.0), month=10)]
            if sql is abc.SQL_YA:
                return [dict(row("YA-1", -1.0), month=10)]
            if "FROM report_uploads" in sql:
                return uploads
            return []

        class FakeDate(datetime.date):
            @classmethod
            def today(cls):
                return today

        with patch.object(db, "query_all", side_effect=fake), patch.object(abc.dt, "date", FakeDate):
            return {(m["year"], m["month"]): m["partial"] for m in abc.abc_data(user=ADMIN)["months"]}

    def test_fact_through_and_current_month(self):
        uploads = [dict(marketplace="Ozon", year=2026, month=10, fact_through_date=datetime.date(2026, 10, 15)),
                   dict(marketplace="Wildberries", year=2026, month=10, fact_through_date=datetime.date(2026, 10, 13)),
                   dict(marketplace="Yandex", year=2026, month=10, fact_through_date=None)]
        got = self.months(uploads, datetime.date(2026, 10, 19))
        self.assertEqual(got[(2026, 9)], {})
        # Яндекс загружен без даты, но месяц текущий — всё равно неполный.
        self.assertEqual(got[(2026, 10)], {"ozon": "2026-10-15", "vb": "2026-10-13", "ya": ""})

    def test_closed_month_after_month_end(self):
        got = self.months([dict(marketplace="Yandex", year=2026, month=10, fact_through_date=None)],
                          datetime.date(2026, 11, 5))
        self.assertEqual(got[(2026, 10)], {})



class RulesTest(unittest.TestCase):
    """Пороги рекомендаций (⚙): исходные значения, проверка присланных (решение владельца 19.10.2026)."""

    def test_defaults_in_data_when_table_empty(self):
        with patch.object(db, "query_all", return_value=[]):
            res = abc.abc_data(user=ADMIN)
        self.assertEqual(res["rules"]["tail_rev"], {"ozon": 30000, "vb": 30000, "ya": 15000})
        self.assertEqual(res["rules"]["remove_here_rev"], {"ozon": 100000, "vb": 100000})
        self.assertEqual(res["rules_defaults"]["low_margin_pct"], {"all": 0.05})

    def test_saved_value_overrides_default(self):
        with patch.object(db, "query_all", return_value=[
                dict(param="tail_rev", marketplace="ya", value=12000),
                dict(param="unknown", marketplace="ya", value=1)]):
            rules = abc.abc_rules.load()
        self.assertEqual(rules["tail_rev"]["ya"], 12000.0)
        self.assertNotIn("unknown", rules)

    def test_validate(self):
        ok = abc.abc_rules.validate({"tail_rev": {"ya": "15000"}, "low_margin_pct": {"all": 0.05}})
        self.assertEqual(ok, [("tail_rev", "ya", 15000.0), ("low_margin_pct", "all", 0.05)])
        for bad in ({"nope": {"ya": 1}}, {"remove_here_rev": {"ya": 1}}, {"tail_rev": {"ozon": -1}},
                    {"low_margin_pct": {"all": 5}}, {"consider_mult": {"all": 0.5}}, {"tail_rev": {"vb": "x"}}):
            with self.assertRaises(ValueError):
                abc.abc_rules.validate(bad)

    def test_save_endpoint_rejects_bad_value(self):
        with self.assertRaises(abc.HTTPException) as cm:
            abc.save_rules({"rules": {"tail_rev": {"ozon": -5}}}, user=ADMIN)
        self.assertEqual(cm.exception.status_code, 400)



class CategoriesTest(unittest.TestCase):
    """Категории для фильтра «Группа / Подгруппа»: из справочника, только для товаров в данных (19.10.2026)."""

    def test_cats_only_for_skus_in_data(self):
        def fake(sql, *a, **kw):
            if sql is abc.SQL_OZON:
                return [row("OZ-1", -1.0)]
            if sql is abc.SQL_WB:
                return [row("WB-1", 1.0)]
            if sql is abc.SQL_CATS:
                return [dict(sku="OZ-1", category_l1="Продукция ECOM", category_l2="Компрессоры", category_l3="АГРЕССОР"),
                        dict(sku="WB-1", category_l1=None, category_l2=None, category_l3=None),
                        dict(sku="OTHER", category_l1="X", category_l2="Y", category_l3="Z")]
            return []
        with patch.object(db, "query_all", side_effect=fake):
            cats = abc.abc_data(user=ADMIN)["cats"]
        self.assertEqual(cats, {"OZ-1": ["Продукция ECOM", "Компрессоры", "АГРЕССОР"], "WB-1": ["", "", ""]})



class SummaryV2Test(unittest.TestCase):
    """«Сводная v2» (`/api/abc/summary`): «Итоги» помесячной юнит-экономики ОЗОН и WB; удержания площадки —
    по товарам плюс без разбивки; ключ месяца — «ГГГГ-ММ», как у месяцев ABC; пустые месяцы не отдаются."""

    def test_root_cells_by_month(self):
        def fake_tree(mp, monthly=False, mgr_sel=None):
            self.assertTrue(monthly)
            self.assertIsNone(mgr_sel)
            return {"weeks": [{"key": "2026-9", "year": 2026, "week": 9},
                              {"key": "2026-10", "year": 2026, "week": 10, "partial_through": "2026-10-25"},
                              {"key": "2026-11", "year": 2026, "week": 11}],
                    "tree": {"cells": {
                        "2026-9": {"revenue": 1000.0, "profit": 150.0, "p_payout": 400.0, "orders_rub": 1200.0,
                                   "sales_qty": 5, "p_mp_holds_pct": 0.5, "noart_pay_pct": 0.02,
                                   "p_commission_pct": 0.3, "p_logistics_pct": 0.1, "p_fines_pct": 0.001,
                                   "p_ads_paid_pct": 0.07, "p_cogs_pct": 0.25, "buyout_pct": 0.9},
                        "2026-10": {"revenue": 500.0, "profit": 50.0, "p_payout": 200.0,
                                    "p_mp_holds_pct": 0.4, "noart_pay_pct": None},
                        "2026-11": {"revenue": 0.0}}}}

        with patch.object(abc.rnp_router, "build_tree", side_effect=fake_tree):
            res = abc.summary(user=ADMIN)
        for mp in ("ozon", "vb"):
            self.assertEqual(sorted(res["mp"][mp]), ["2026-09", "2026-10"])
            self.assertAlmostEqual(res["mp"][mp]["2026-09"]["holds"], 0.52)
            self.assertAlmostEqual(res["mp"][mp]["2026-10"]["holds"], 0.4)   # без строки общих удержаний
            self.assertEqual(res["mp"][mp]["2026-09"]["payout"], 400.0)
            self.assertEqual(res["partial"][mp], {"2026-10": "2026-10-25"})
            # «P&L» ABC: части удержаний по отдельности (доли от продаж); нет показателя — None, а не 0
            m9 = res["mp"][mp]["2026-09"]
            self.assertEqual((m9["mp_holds"], m9["noart"], m9["commission"], m9["logistics"], m9["fines"]),
                             (0.5, 0.02, 0.3, 0.1, 0.001))
            self.assertIsNone(m9["acceptance"])
            self.assertIsNone(res["mp"][mp]["2026-10"]["noart"])

    def test_only_marketplaces_of_role(self):
        """Роль только с WB получает только WB; без права №126 — 403."""
        calls = []

        def fake_tree(mp, monthly=False, mgr_sel=None):
            calls.append(mp)
            return {"weeks": [], "tree": {"cells": {}}}

        wb_only = {"id": 2, "is_admin": False, "permissions": ["abc.open", "abc.tab.summary_v2"],
                   "marketplaces": ["Wildberries"]}
        with patch.object(abc.rnp_router, "build_tree", side_effect=fake_tree):
            res = abc.summary(user=wb_only)
            self.assertEqual(calls, ["Wildberries"])
            self.assertEqual(list(res["mp"]), ["vb"])
            with self.assertRaises(abc.HTTPException) as e:
                abc.summary(user=dict(wb_only, permissions=["abc.open"]))
            self.assertEqual(e.exception.status_code, 403)
            # право вкладки «P&L» (№127) открывает тот же ответ
            self.assertEqual(list(abc.summary(user=dict(wb_only, permissions=["abc.open", "abc.tab.pnl"]))["mp"]), ["vb"])

if __name__ == "__main__":
    unittest.main()
