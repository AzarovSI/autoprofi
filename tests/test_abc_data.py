"""ABC (`/api/abc/data`): рекламный расход отдаётся положительным для всех площадок (без БД).

Ozon и Яндекс хранят `promo_total` со знаком минус, WB `wb_ads_total` — плюсом; клиент считает
ДРР = реклама / выручка, поэтому до 19.10.2026 ДРР Ozon и Яндекса в ABC была отрицательной.
"""
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
                margin=0.1, sales_qty=5, orders_qty=6, returns_qty=1, ad_spend=ad_spend, status="CORE")


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


if __name__ == "__main__":
    unittest.main()
