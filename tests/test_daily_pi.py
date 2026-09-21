"""Date-exact persisted Pi; isolated PostgreSQL only."""
import datetime as dt
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from test_wb_prices import prepare_local_db
from app import db, daily_pi, price_history, cache
from app.routers import rnp_sales_router as rnp

D = dt.date(2026, 9, 18)


def source(mp, day=D, up=1000, buyer=800, spp=.2):
    with db.transaction() as tx:
        tx.execute("""INSERT INTO mp_price_daily
            (seller_article,marketplace,date,upload_price,buyer_price,spp_pct,spp_source_date,
             upload_source,spp_is_estimated)
            VALUES ('SKU-1',%s,%s,%s,%s,%s,%s,%s,false)
            ON CONFLICT (seller_article,marketplace,date) DO UPDATE SET
              upload_price=EXCLUDED.upload_price,buyer_price=EXCLUDED.buyer_price,
              spp_pct=EXCLUDED.spp_pct,spp_source_date=EXCLUDED.spp_source_date,
              spp_is_estimated=false""",
            (mp, day, up, buyer, spp, day, price_history.DIRECT_REPORT_SOURCES[mp]))


def saved(day=D):
    return db.query_one("SELECT * FROM price_index_daily WHERE seller_article='SKU-1' AND date=%s", (day,))


def sales_rows():
    return [dict(seller_article="SKU-1", date=d, item_name="Тестовый товар",
                 category_l1="Продукция ECOM", category_l2="Компрессоры",
                 category_l3="Тестовые товары", status="Активный", manager="Менеджер",
                 orders_qty=2, orders_rub=2000, price_index_pi=.51)
            for d in [dt.date(2026, 8, 30), D, D+dt.timedelta(days=1)]]


def build_fixture(mp="ozon", end="2026-09-19"):
    original = db.query_all

    def query(sql, params=None):
        if "price_index_daily" in sql:
            return original(sql, params)
        if "daily_sales ods" in sql:
            return sales_rows()
        return []
    with patch.object(db, "query_all", side_effect=query), \
         patch.object(db, "query_one", return_value=None), \
         patch.object(rnp.rnp_router, "negative_margin_articles", return_value=set()):
        return rnp._build_rnp_sales_tree(mp, "2026-08-01", end, None, None)


def leaf(tree):
    while tree["children"]:
        tree = tree["children"][0]
    return tree


class DailyPiTests(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        daily_pi.migrate()
        db.execute("""INSERT INTO catalog_base_prices_hist
            VALUES ('SKU-1','Ozon',2000,'2026-09-01',NULL,1)""")

    def test_migration_is_idempotent(self):
        daily_pi.migrate()
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM app_schema_migrations WHERE name=%s",
                                     (daily_pi.MIGRATION,))["n"], 1)

    def test_missing_other_marketplace_then_arrival_and_correction(self):
        source("Wildberries")
        self.assertEqual(saved()["wb_base_pi"], 1)
        self.assertIsNone(saved()["buyer_pi"])
        source("Ozon", up=2400, buyer=1200, spp=.5)
        self.assertEqual(saved()["ozon_base_pi"], Decimal("1.2"))
        self.assertEqual(saved()["buyer_pi"], Decimal("1.5"))
        source("Wildberries", buyer=600, spp=.4)
        self.assertEqual(saved()["buyer_pi"], 2)
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM price_index_daily")["n"], 1)

    def test_no_carry_between_dates(self):
        source("Wildberries")
        source("Ozon", day=D+dt.timedelta(days=1))
        self.assertIsNone(saved()["buyer_pi"])
        self.assertIsNone(saved(D+dt.timedelta(days=1))["wb_base_pi"])
        values = daily_pi.load({"SKU-1"}, D, D+dt.timedelta(days=2), "Ozon")
        self.assertNotIn(("SKU-1", (D+dt.timedelta(days=2)).isoformat()), values)

    def test_zero_spp_and_estimated_legacy_not_used(self):
        source("Wildberries")
        source("Ozon", spp=0)
        self.assertIsNone(saved()["buyer_pi"])
        source("Ozon")
        db.execute("""UPDATE mp_price_daily SET spp_is_estimated=true,
            spp_source_date=date-1 WHERE marketplace='Ozon'""")
        self.assertIsNone(saved()["buyer_pi"])
        self.assertEqual(saved()["ozon_base_pi"], Decimal(".5"))

    def test_autoprofi_and_category_change(self):
        source("Wildberries")
        source("Ozon", up=2000, buyer=1000, spp=.5)
        db.execute("UPDATE catalog_items SET category_l1='Продукция ТД АВТОПРОФИ' WHERE seller_article='SKU-1'")
        self.assertEqual(saved()["wb_base_pi"], Decimal(".8"))
        self.assertEqual(saved()["ozon_base_pi"], Decimal(".5"))
        source("Wildberries", spp=None, buyer=None)
        self.assertIsNone(saved()["wb_base_pi"])
        self.assertIsNone(saved()["buyer_pi"])

    def test_base_history_correction_and_moscow_midnight(self):
        source("Wildberries")
        # Next day's midnight Moscow must not affect previous day's price.
        db.execute("""INSERT INTO catalog_base_prices_hist
            VALUES ('SKU-1','Wildberries',500,'2026-09-18 21:00:00+00',NULL,1)""")
        self.assertEqual(saved()["wb_base_pi"], 1)
        source("Wildberries", day=D+dt.timedelta(days=1))
        self.assertEqual(saved(D+dt.timedelta(days=1))["wb_base_pi"], 2)
        db.execute("""UPDATE catalog_base_prices_hist SET base_price=250
            WHERE marketplace='Wildberries' AND seller_article='SKU-1'
              AND valid_from='2026-09-18 21:00:00+00'""")
        self.assertEqual(saved()["wb_base_pi"], 1)
        self.assertEqual(saved(D+dt.timedelta(days=1))["wb_base_pi"], 4)

    def test_delete_and_null_base_invalidate(self):
        source("Wildberries")
        source("Ozon")
        db.execute("UPDATE catalog_base_prices_hist SET base_price=NULL WHERE marketplace='Wildberries'")
        self.assertIsNone(saved()["wb_base_pi"])
        db.execute("DELETE FROM mp_price_daily WHERE marketplace='Ozon'")
        self.assertIsNone(saved()["buyer_pi"])
        db.execute("DELETE FROM mp_price_daily")
        self.assertIsNone(saved())

    def test_atomic_rollback(self):
        source("Wildberries")
        rev = daily_pi.revision()
        with self.assertRaises(RuntimeError):
            with db.transaction() as tx:
                tx.execute("UPDATE mp_price_daily SET upload_price=500")
                self.assertEqual(tx.query_one("SELECT wb_base_pi FROM price_index_daily")["wb_base_pi"], Decimal(".5"))
                raise RuntimeError("rollback")
        self.assertEqual(saved()["wb_base_pi"], 1)
        self.assertEqual(daily_pi.revision(), rev)

    def test_concurrent_imports_converge(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(source, ["Wildberries", "Ozon"]))
        self.assertEqual(saved()["buyer_pi"], 1)

    def test_same_buyer_in_both_rnp_and_existing_pi_unchanged(self):
        source("Wildberries")
        source("Ozon", buyer=1200, up=2400, spp=.5)
        for mp, expected in [("ozon", 1.2), ("wb", 1)]:
            result = build_fixture(mp)
            item = leaf(result["tree"])
            self.assertEqual(item["cells"][D.isoformat()]["buyer_price_pi"], 1.5)
            self.assertEqual(item["cells"][D.isoformat()]["base_price_pi"], expected)
            self.assertEqual(item["cells"][D.isoformat()]["price_index_pi"], .51)
            self.assertIsNone(item["cells"]["2026-09-19"]["buyer_price_pi"])
            self.assertIsNone(item["cells"]["2026-08"]["buyer_price_pi"])
            defs = {m["key"]: m for m in result["product_metrics"]}
            self.assertEqual(defs["price_index_pi"]["sub"], 3)
            self.assertEqual(defs["buyer_price_pi"]["label"], "Цена покупателя Ozon / WB, Pi")
        self.assertNotIn("buyer_price_pi", {m["key"] for m in rnp._metric_payload(
            rnp.PRODUCT_METRIC_DEFS, product=True, is_ya=True)})

    def test_month_end_rules(self):
        today = dt.date(2026, 9, 21)
        self.assertEqual(daily_pi.month_end(2026,8,{dt.date(2026,8,30)},today=today), dt.date(2026,8,31))
        self.assertEqual(daily_pi.month_end(2026,9,{D},today=today), D)
        self.assertEqual(daily_pi.month_end(2026,8,{dt.date(2026,8,30)},dt.date(2026,8,15),today),
                         dt.date(2026,8,15))

    def test_past_month_uses_calendar_end_even_without_orders_that_day(self):
        end = dt.date(2026, 8, 31)
        source("Wildberries", day=end, buyer=500, spp=.5)
        source("Ozon", day=end, buyer=750, spp=.25)
        item = leaf(build_fixture()["tree"])
        self.assertEqual(item["cells"]["2026-08"]["buyer_price_pi"], 1.5)
        self.assertNotIn("2026-08-31", item["cells"])

    def test_backfill_restores_only_existing_source_dates(self):
        source("Wildberries")
        source("Ozon")
        db.execute("TRUNCATE price_index_daily")
        db.execute("SELECT refresh_daily_pi(NULL, NULL)")
        self.assertEqual(saved()["buyer_pi"], 1)
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM price_index_daily")["n"], 1)

    def test_cached_rnp_invalidates_for_either_source(self):
        source("Wildberries")
        with patch.object(rnp, "_build_rnp_sales_tree", return_value={"ok": True}) as build:
            args = ("ozon", "2026-09-18", "2026-09-18", None, None, {})
            rnp.rnp_sales_tree(*args)
            rnp.rnp_sales_tree(*args)
            self.assertEqual(build.call_count, 1)
            source("Ozon")
            rnp.rnp_sales_tree(*args)
            self.assertEqual(build.call_count, 2)
            self.assertEqual(cache.stats()["entries"], 1)


if __name__ == "__main__":
    unittest.main()
