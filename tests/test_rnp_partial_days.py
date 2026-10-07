"""Partial-day RNP: temporary tables in local wb_price_test only."""
import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-test-only")

import openpyxl
import psycopg
from psycopg.conninfo import conninfo_to_dict
from app import db, ozon_reviews_loader as reviews, ozon_daily_loader as oz, wb_daily_loader as wb
from app.routers import rnp_sales_router as rnp

DAY = dt.date(2026, 9, 24)
PREV = DAY - dt.timedelta(days=1)


def leaves(node):
    if node["level"] == 4:
        yield node
    for child in node["children"]:
        yield from leaves(child)


def row(day=DAY, art="SKU-1", **values):
    return dict(date=day, seller_article=art, category_l1="Продукция ECOM",
                category_l2="Группа", category_l3="Товары", status="Активный",
                manager="Менеджер", **values)


def tree(mp="ozon", sales=None, stocks=None, plan=300):
    def query(sql, params=None):
        if "daily_sales ods" in sql:
            return [dict(r) for r in (sales or [])]
        if "FROM stock_daily sd" in sql:
            return [dict(r) for r in (stocks or [])]
        if "FROM sales_plan" in sql:
            return [dict(seller_article="SKU-1", year=2026, month=9, q=plan)]
        return []
    with patch.object(db, "query_all", side_effect=query), \
         patch.object(db, "query_one", return_value=None), \
         patch.object(rnp, "_today", return_value=DAY), \
         patch.object(rnp.daily_pi, "load", return_value={}), \
         patch.object(rnp.rnp_router, "negative_margin_articles", return_value=set()):
        return rnp._build_rnp_sales_tree(mp, "2026-09-01", "2026-09-24", None, None)


class TreeTests(unittest.TestCase):
    def test_wb_reviews_only_day_keeps_turnover_but_not_daily_stock(self):
        result = tree("wb", [row(PREV, orders_qty=10, stock_ozon_qty=100),
                             row(delivery_time_hours=42, spp_pct=.31)])
        product = next(leaves(result["tree"]))
        self.assertEqual(product["turnover"]["2026-09"], 10)
        self.assertEqual(result["tree"]["turnover"]["2026-09"], 10)
        self.assertIsNone(product["cells"][DAY.isoformat()]["stock_ozon_qty"])
        self.assertIsNone(product["cells"]["2026-09"]["stock_ozon_qty"])
        self.assertEqual(result["turnover_cfg"]["as_of"], PREV.isoformat())

    def test_wb_turnover_does_not_resurrect_missing_or_zero_stock(self):
        for stock in (None, 0):
            with self.subTest(stock=stock):
                result = tree("wb", [row(PREV, orders_qty=10, stock_ozon_qty=100),
                                     row(orders_qty=10, stock_ozon_qty=stock)])
                self.assertEqual(result["tree"]["turnover"]["2026-09"], stock)
                self.assertEqual(result["turnover_cfg"]["as_of"], DAY.isoformat())

    def test_wb_turnover_no_orders_source_has_no_stock_date(self):
        result = tree("wb", [row(delivery_time_hours=42)])
        self.assertIsNone(result["turnover_cfg"]["as_of"])
        self.assertIsNone(result["tree"]["turnover"]["2026-09"])

    def test_wb_turnover_uses_common_reference_day_not_stale_article_stock(self):
        result = tree("wb", [row(PREV, orders_qty=10, stock_ozon_qty=100),
                             row(art="SKU-2", orders_qty=10, stock_ozon_qty=20)])
        products = {n["leaf_info"]["seller_article"]: n for n in leaves(result["tree"])}
        self.assertIsNone(products["SKU-1"]["turnover"]["2026-09"])
        self.assertEqual(products["SKU-2"]["turnover"]["2026-09"], 4)

    def test_wb_blank_status_inactive_reviews_do_not_require_distribution(self):
        for status in (None, "", "  "):
            with self.subTest(status=status):
                daily = row(delivery_time_hours=42, orders_qty=0)
                daily.update(status=status, manager=None, category_l1=None)
                stock = dict(daily, stock_ap_qty=80)
                result = tree("wb", [daily], [stock])
                self.assertEqual(result["undistributed"]["count"], 0)
                self.assertEqual(list(leaves(result["tree"])), [])
                self.assertNotIn(DAY.isoformat(), result["tree"]["cells"])
                self.assertEqual(daily["delivery_time_hours"], 42)

    def test_wb_blank_status_with_activity_still_requires_distribution(self):
        for field in ("orders_qty", "orders_rub", "ads_expense_rub", "stock_ozon_qty"):
            with self.subTest(field=field):
                daily = row(delivery_time_hours=42, **{field: 1})
                daily.update(status=None, manager=None)
                result = tree("wb", [daily])
                self.assertEqual(result["undistributed"]["count"], 1)
                self.assertEqual(result["tree"]["cells"][DAY.isoformat()][field], 1)

    def test_wb_activity_on_previous_day_keeps_reviews_on_current_day(self):
        sales = [row(PREV, orders_qty=1), row(delivery_time_hours=42)]
        for daily in sales:
            daily.update(status=None, manager=None)
        result = tree("wb", sales)
        self.assertEqual(result["undistributed"]["count"], 1)
        self.assertIn(DAY.isoformat(), result["tree"]["cells"])

    def test_wb_whitespace_status_with_activity_is_unassigned(self):
        daily = row(orders_qty=1)
        daily["status"] = "   "
        result = tree("wb", [daily])
        self.assertEqual(result["undistributed"]["count"], 1)
        self.assertEqual(list(leaves(result["tree"])), [])
        self.assertEqual(result["tree"]["cells"][DAY.isoformat()]["orders_qty"], 1)

    def test_wb_assigned_status_keeps_inactive_partial_data(self):
        for status in ("CORE", "NEW", "?"):
            with self.subTest(status=status):
                daily = row(delivery_time_hours=42)
                daily["status"] = status
                result = tree("wb", [daily])
                self.assertEqual(len(list(leaves(result["tree"]))), 1)
                cell = next(leaves(result["tree"]))["cells"][DAY.isoformat()]
                self.assertEqual(cell["delivery_time_hours"], 42)
                self.assertIsNone(cell["orders_qty"])

    def test_blank_status_rule_does_not_change_other_marketplaces(self):
        for mp in ("ozon", "ya"):
            with self.subTest(mp=mp):
                daily = row(delivery_time_hours=42)
                daily.update(status=None, manager=None)
                self.assertEqual(tree(mp, [daily])["undistributed"]["count"], 1)

    def test_today_without_orders_keeps_values_and_dashes(self):
        for mp in ("ozon", "wb"):
            with self.subTest(mp=mp):
                result = tree(mp, [row(orders_qty=None, comp_price_avg=1200,
                                       delivery_time_hours=42, spp_pct=.2)])
                cell = next(leaves(result["tree"]))["cells"][DAY.isoformat()]
                self.assertEqual(cell["comp_price_avg"], 1200)
                self.assertEqual(cell["delivery_time_hours"], 42)
                for key in ("orders_qty", "orders_rub", "avg_upload_price", "drr_total_pct"):
                    self.assertIsNone(cell[key])
                self.assertIn(DAY.isoformat(), [d["key"] for d in result["months"][0]["days"]])

    def test_stock_updates_existing_day_without_adding_stock_only_article(self):
        for mp in ("ozon", "wb"):
            result = tree(mp, [row(stock_ap_qty=5)],
                          [row(stock_ap_qty=80), row(art="SKU-2", stock_ap_qty=0)])
            self.assertEqual(result["tree"]["cells"][DAY.isoformat()]["stock_ap_qty"], 80)
            items = {n["leaf_info"]["seller_article"]: n for n in leaves(result["tree"])}
            self.assertEqual(set(items), {"SKU-1"})
            self.assertEqual(result["undistributed"]["count"], 0)

    def test_new_stock_day_for_existing_article_keeps_partial_values(self):
        for mp in ("ozon", "wb"):
            with self.subTest(mp=mp):
                result = tree(mp, [row(PREV, orders_qty=10)],
                              [row(stock_ap_qty=80), row(art="SKU-2", stock_ap_qty=900)])
                items = list(leaves(result["tree"]))
                self.assertEqual(len(items), 1)
                cell = items[0]["cells"][DAY.isoformat()]
                self.assertEqual(cell["stock_ap_qty"], 80)
                self.assertIsNone(cell["orders_qty"])
                self.assertEqual(result["tree"]["cells"][DAY.isoformat()]["stock_ap_qty"], 80)
                self.assertEqual(result["tree"]["forecast"]["2026-09"], 1)

    def test_stock_only_unallocated_article_does_not_affect_warning_or_totals(self):
        for mp in ("ozon", "wb"):
            with self.subTest(mp=mp):
                extra = row(art="STOCK-ONLY", stock_ap_qty=900)
                extra.update(category_l1=None, status=None, manager=None)
                result = tree(mp, [row(orders_qty=2)], [row(stock_ap_qty=80), extra])
                self.assertEqual(result["undistributed"]["count"], 0)
                self.assertEqual(result["tree"]["cells"][DAY.isoformat()]["stock_ap_qty"], 80)
                self.assertEqual(result["tree"]["cells"][DAY.isoformat()]["orders_qty"], 2)

    def test_existing_unallocated_article_stays_in_warning_and_totals(self):
        for mp in ("ozon", "wb"):
            with self.subTest(mp=mp):
                daily = row(orders_qty=2)
                daily["manager"] = None
                stock = row(stock_ap_qty=80)
                stock["manager"] = None
                result = tree(mp, [daily], [stock])
                self.assertEqual(result["undistributed"]["count"], 1)
                self.assertEqual(list(leaves(result["tree"])), [])
                self.assertEqual(result["tree"]["cells"][DAY.isoformat()]["stock_ap_qty"], 80)

    def test_common_stock_does_not_activate_inactive_dash_status(self):
        for mp in ("ozon", "wb"):
            with self.subTest(mp=mp):
                daily = row(PREV, orders_qty=0)
                daily["status"] = "-"
                stock = row(stock_ap_qty=80)
                stock["status"] = "-"
                result = tree(mp, [daily], [stock])
                self.assertEqual(list(leaves(result["tree"])), [])
                self.assertEqual(result["undistributed"]["count"], 0)
                self.assertNotIn(DAY.isoformat(), result["tree"]["cells"])

    def test_stock_without_daily_source_keeps_report_empty(self):
        for mp in ("ozon", "wb"):
            with self.subTest(mp=mp):
                result = tree(mp, stocks=[row(stock_ap_qty=80)])
                self.assertEqual(list(leaves(result["tree"])), [])
                self.assertEqual(result["months"], [])
                self.assertEqual(result["undistributed"]["count"], 0)

    def test_snapshot_day_does_not_reduce_forecast_or_raise_turnover(self):
        for mp in ("ozon", "wb"):
            result = tree(mp, [row(PREV, orders_qty=10, stock_ozon_qty=100),
                               row(orders_qty=None, stock_ozon_qty=100)])
            self.assertEqual(result["tree"]["forecast"]["2026-09"], 1)
            self.assertEqual(result["tree"]["turnover"]["2026-09"], 10)

    def test_actual_zero_is_a_day_with_orders_data(self):
        result = tree(sales=[row(PREV, orders_qty=10), row(orders_qty=0)])
        self.assertEqual(result["tree"]["forecast"]["2026-09"], .5)

    def test_snapshot_only_has_no_forecast(self):
        self.assertIsNone(tree(sales=[row()], stocks=[row(stock_ap_qty=50)])["tree"]["forecast"]["2026-09"])

    def test_no_carry_from_yesterday(self):
        result = tree(sales=[row(PREV, orders_qty=2, delivery_time_hours=9),
                             row(comp_price_avg=1200)])
        self.assertIsNone(next(leaves(result["tree"]))["cells"][DAY.isoformat()]["delivery_time_hours"])

    def test_yandex_does_not_join_common_stock_or_change_forecast(self):
        result = tree("ya", [row(PREV, orders_qty=10), row()], [row(art="SKU-2", stock_ap_qty=50)])
        self.assertEqual(result["tree"]["forecast"]["2026-09"], .5)
        self.assertEqual(len(list(leaves(result["tree"]))), 1)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        info = conninfo_to_dict(db.DB_DSN)
        if info.get("host") not in ("127.0.0.1", "localhost") or info.get("dbname") != "wb_price_test":
            raise RuntimeError("Local test DB only")
        self.conn = psycopg.connect(db.DB_DSN)
        self.addCleanup(self.conn.close)
        self.conn.execute("""CREATE TEMP TABLE catalog_items (
            seller_article text PRIMARY KEY, sample_name text,
            category_l1 text, category_l2 text, category_l3 text);
            CREATE TEMP TABLE catalog_marketplace (
            seller_article text, marketplace text, PRIMARY KEY(seller_article,marketplace));
            CREATE TEMP TABLE report_uploads (
            id bigserial PRIMARY KEY, marketplace text, year int, period_text text,
            period_start date, period_end date, source_file text, rows_loaded int,
            status text, message text, period_kind text)""")
        for module, table in ((oz, "ozon_daily_sales"), (wb, "wb_daily_sales")):
            columns = sorted(set(module.INSERT_COLS + module.PRESERVE_COLS)
                             - {"date", "seller_article", "item_name"})
            self.conn.execute(f"""CREATE TEMP TABLE {table} (
                date date, seller_article text, item_name text,
                {','.join(c + ' numeric' for c in columns)},
                PRIMARY KEY(date,seller_article))""")
        self.conn.execute("INSERT INTO catalog_items(seller_article) VALUES ('SKU-1'),('SKU-2')")
        self.conn.commit()

    def review_import(self, rating=4.8, count=42):
        with patch.object(reviews, "_read_report_rows",
                          return_value=(reviews.REQUIRED, [["sku-1", rating, count], ["UNKNOWN", 5, 1]])):
            result = reviews.load_ozon_reviews(self.conn, "local.xlsx", DAY)
        self.assertTrue(result["ok"], result)
        return result

    def daily_import(self, module, art):
        if module is wb:
            headers = wb.WB_VORONKA_REQUIRED
            values = [DAY, 123, art, 2, 2000, 20, 50]
            with patch.object(wb, "_read_sheet", return_value=([headers, values], "Sheet1")):
                result = wb.load_wb_daily(self.conn, "local.xlsx")
        else:
            with tempfile.NamedTemporaryFile(suffix=".xlsx") as f:
                book = openpyxl.Workbook()
                sheet = book.active
                sheet.title = oz.SHEET_NAME
                headers = list(dict.fromkeys(oz.OZ_DAILY_REQUIRED + [v for _, v in oz.METRIC_MAP]))
                data = {"Дата": DAY, "Артикул": art}
                for col, title in oz.METRIC_MAP:
                    if col == "orders_qty":
                        data[title] = 2
                sheet.append(headers)
                sheet.append([data.get(h) for h in headers])
                book.save(f.name)
                result = oz.load_ozon_daily(self.conn, f.name)
        self.assertTrue(result["ok"], result)

    def test_reviews_before_orders_upsert_canonical_and_repeat(self):
        self.review_import()
        self.review_import(4.9, 43)
        value = self.conn.execute("SELECT seller_article,rating,delivery_time_hours,orders_qty FROM ozon_daily_sales").fetchone()
        self.assertEqual(value[0], "SKU-1")
        self.assertEqual(float(value[1]), 4.9)
        self.assertEqual(value[2], 43)
        self.assertIsNone(value[3])
        self.daily_import(oz, "SKU-1")
        value = self.conn.execute("SELECT rating,delivery_time_hours,orders_qty FROM ozon_daily_sales").fetchone()
        self.assertEqual(value[1:], (43, 2))
        self.review_import()
        self.assertEqual(self.conn.execute("SELECT orders_qty FROM ozon_daily_sales").fetchone()[0], 2)

    def test_ozon_missing_from_orders_preserves_snapshot_not_orders(self):
        self.review_import()
        self.conn.execute("UPDATE ozon_daily_sales SET orders_qty=99")
        self.conn.commit()
        self.daily_import(oz, "SKU-2")
        value = self.conn.execute("SELECT delivery_time_hours,orders_qty FROM ozon_daily_sales WHERE seller_article='SKU-1'").fetchone()
        self.assertEqual(value, (42, None))

    def test_wb_preserves_spp_pi_reviews_for_matched_and_missing_articles(self):
        for art in ("SKU-1", "SKU-2"):
            self.conn.execute("""INSERT INTO wb_daily_sales
                (date,seller_article,orders_qty,reviews_qty,spp_pct,price_index_pi)
                VALUES (%s,%s,99,42,.2,1.1)""", (DAY, art))
        self.conn.commit()
        self.daily_import(wb, "SKU-1")
        rows = self.conn.execute("SELECT seller_article,orders_qty,reviews_qty,spp_pct,price_index_pi FROM wb_daily_sales ORDER BY seller_article").fetchall()
        self.assertEqual(rows[0][1:3], (2, 42))
        self.assertEqual(rows[1][1:3], (None, 42))
        self.assertEqual(float(rows[0][3]), .2)
        self.assertEqual(float(rows[1][4]), 1.1)


if __name__ == "__main__":
    unittest.main()
