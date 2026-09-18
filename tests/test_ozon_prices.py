"""New Ozon daily report; isolated local database only, never production."""
import datetime as dt
import os
import unittest
from decimal import Decimal as D
from unittest.mock import patch

from test_wb_prices import (
    DAY, fake_excel, prepare_local_db, make_app,
    auth, db, price_history, pu, pr, FastAPI, TestClient,
)

HEADERS = ["Дата", "Артикул", "Наименование", "SKU", "Предельная цена, ₽",
           "Наша цена, ₽", "Цена продавца после акций, ₽", "СПП/соинвест, %",
           "Цена продавца с соинвестом, ₽", "Индекс цены (Pi)"]
FIXTURE = os.environ.get("OZON_PRICE_FIXTURE")


def raw(date=DAY, article="SKU-1", up=7937, spp=.5417, buyer=3637.53):
    return [date, article, "Товар", 123456, 4600, 8340, up, spp, buyer, 9.99]


def parse(rows, header=None, sheets=None):
    with fake_excel(rows, header=header or HEADERS, sheets=sheets):
        return pu.parse_ozon("mock.xlsx")


def prepare_ozon(articles=("SKU-1", "SKU-2")):
    prepare_local_db(articles)
    # Retain WB fixtures as an independent control marketplace.
    db.execute("""INSERT INTO catalog_marketplace
                  SELECT seller_article,'Ozon',status FROM catalog_marketplace""")
    db.execute("""INSERT INTO catalog_base_prices_hist
                  SELECT seller_article,'Ozon',base_price,valid_from,valid_to,
                         updated_by_user_id FROM catalog_base_prices_hist""")


class OzonParserTests(unittest.TestCase):
    def test_exact_columns_and_direct_buyer_not_derived(self):
        row = parse([raw(buyer=3637.51)]).rows[0]
        self.assertEqual(row.upload_price, D("7937"))
        self.assertEqual(row.spp_pct, D(".5417"))
        self.assertEqual(row.buyer_price, D("3637.51"))
        self.assertEqual(row.time, dt.time.min)

    def test_zero_or_absent_coinvest_clears_buyer(self):
        for spp in (0, None, "", "0%", "—"):
            with self.subTest(spp=spp):
                row = parse([raw(spp=spp, buyer=6198)]).rows[0]
                self.assertIsNone(row.spp_pct)
                self.assertIsNone(row.buyer_price)
                self.assertEqual(row.upload_price, D(7937))

    def test_multiday_and_exact_duplicates(self):
        report = parse([raw(), raw(), raw(date="17.09.2026", up=8000)])
        self.assertEqual(len(report.rows), 2)
        self.assertEqual(report.rows_in_file, 3)
        self.assertEqual(report.duplicates_by_date[DAY], 1)

    def test_conflicting_values_reject_entire_report(self):
        for change in ({"up": 8000}, {"spp": .5}, {"buyer": 3900}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "разные значения"):
                parse([raw(), raw(**change)])

    def test_conflicting_values_do_not_use_optional_time(self):
        with self.assertRaisesRegex(ValueError, "разные значения"):
            parse([raw() + ["09:00"], raw(up=8000) + ["19:00"]], header=HEADERS + ["Время"])

    def test_reordered_columns_and_ignored_nonimported_data(self):
        r = raw(article=" agr-140  Smerch ", spp="54,17%")
        row = parse([r[::-1]], header=HEADERS[::-1]).rows[0]
        self.assertEqual(row.article, "AGR-140 SMERCH")
        self.assertEqual(row.upload_price, D(7937))
        self.assertEqual(row.spp_pct, D(".5417"))
        same = r.copy()
        same[4], same[5], same[9] = 999, 888, 777
        self.assertEqual(len(parse([r, same]).rows), 1)

    def test_invalid_rows_reject(self):
        for change in ({"up": 0}, {"up": -1}, {"up": "NaN"}, {"spp": -.1},
                       {"spp": 100}, {"buyer": None}, {"buyer": 0},
                       {"date": "31.02.2026"}, {"article": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse([raw(), raw(**change)])

    def test_wrong_or_multiple_sheets_reject(self):
        with self.assertRaisesRegex(ValueError, "Старый шаблон"):
            parse([], sheets={"Товары и цены": [["Артикул", "Предельная цена, руб."]]})
        with self.assertRaisesRegex(ValueError, "один лист"):
            parse([], sheets={"A": [HEADERS, raw()], "B": [HEADERS, raw()]})

    def test_limits(self):
        with self.assertRaisesRegex(ValueError, "Слишком много листов"):
            parse([], sheets={str(i): [] for i in range(21)})
        with self.assertRaisesRegex(ValueError, "Слишком большой лист"):
            parse([], sheets={"wide": [[None] * 65]})

    @unittest.skipUnless(FIXTURE, "Set OZON_PRICE_FIXTURE")
    def test_real_workbook(self):
        report = pu.parse_ozon(FIXTURE)
        self.assertEqual(report.rows_in_file, 266)
        self.assertEqual(len(report.rows), 266)
        self.assertEqual(sum(r.spp_pct is None for r in report.rows), 100)
        row = next(r for r in report.rows if r.article == "AGR-35")
        self.assertEqual(row.upload_price, D(7937))
        self.assertEqual(row.buyer_price, D("3637.53"))


class OzonIntegrationTests(unittest.TestCase):
    def setUp(self):
        prepare_ozon()
        self.client = TestClient(make_app())

    def upload(self, report):
        with patch.object(pu, "parse_ozon", return_value=report):
            return self.client.post("/api/prices/upload_ozon", files={"file": ("test.xlsx", b"test")})

    def snapshot(self):
        return db.query_all("SELECT * FROM mp_price_daily ORDER BY marketplace,date,seller_article")

    def test_multiple_days_repeat_and_unknown(self):
        report = parse([raw(date="17.09.2026", up=8000), raw(), raw(article="UNKNOWN")])
        res = self.upload(report)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["report_dates"], ["2026-09-17", "2026-09-18"])
        self.assertEqual(res.json()["rows_upserted"], 2)
        self.assertEqual(res.json()["skipped_unknown"], 1)
        self.assertEqual(len(db.query_all("SELECT * FROM mp_price_upload_log")), 2)
        self.upload(parse([raw(up=9000, spp=0)]))
        rows = self.snapshot()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["upload_price"], D(8000))
        self.assertEqual(rows[1]["upload_price"], D(9000))
        self.assertIsNone(rows[1]["spp_pct"])
        self.assertIsNone(rows[1]["buyer_price"])
        self.assertEqual(rows[1]["upload_source"], "upload_ozon_coinvest")
        self.assertFalse(rows[1]["spp_is_estimated"])

    def test_rnp_cannot_change_direct_or_no_stock_and_wb_is_untouched(self):
        from test_wb_prices import parse_rows, raw_row
        with db.transaction() as tx:
            price_history.sync_wb_report(tx, parse_rows([raw_row()]).rows, 1)
        self.upload(parse([raw(), raw(article="SKU-2", spp=0)]))
        before = self.snapshot()
        db.execute("INSERT INTO ozon_daily_sales VALUES ('SKU-1',%s,.7),('SKU-2',%s,.8)", (DAY, DAY))
        price_history.recalc_spp_and_buyer_from("Ozon", DAY)
        price_history.recalc_spp_and_buyer_from("Wildberries", DAY)
        self.assertEqual(self.snapshot(), before)

    def test_history_base_prices_and_no_future_leak(self):
        self.upload(parse([raw(date="17.09.2026", up=8000, buyer=4000), raw()]))
        db.execute("""INSERT INTO catalog_base_prices_hist
                      VALUES ('SKU-1','Ozon',1200,'2026-09-18 10:00+00',NULL,1)""")
        for day, up, base, buyer in (
            ("2026-09-16", None, 1000, None), ("2026-09-17", 8000, 1000, 4000),
            ("2026-09-18", 7937, 1200, 3637.53), ("2026-09-20", 7937, 1200, 3637.53),
        ):
            res = self.client.get("/api/prices/pricelist", params={"date_from": "2026-09-01", "date_to": day})
            self.assertEqual(res.status_code, 200, res.text)
            row = next(r for r in res.json()["items"] if r["seller_article"] == "SKU-1")
            self.assertEqual((row["oz_upload_price"], row["price_ozon"], row["oz_buyer_price"]), (up, base, buyer))

    def test_legacy_history_preserved_except_reuploaded_key(self):
        with db.transaction() as tx:
            price_history.sync_uploaded_prices(tx, "Ozon", dt.date(2026, 9, 17),
                                              [("SKU-1", 1000)], "upload_ozon", 1)
            price_history.sync_uploaded_prices(tx, "Ozon", DAY, [("SKU-1", 1000)], "upload_ozon", 1)
        self.upload(parse([raw()]))
        db.execute("INSERT INTO ozon_daily_sales VALUES ('SKU-1','2026-09-17',.2)")
        price_history.recalc_spp_and_buyer_from("Ozon", dt.date(2026, 9, 17))
        rows = self.snapshot()
        self.assertEqual(rows[0]["buyer_price"], D(800))
        self.assertEqual(rows[0]["upload_source"], "upload_ozon")
        self.assertEqual(rows[1]["buyer_price"], D("3637.53"))
        self.assertEqual(rows[1]["upload_source"], "upload_ozon_coinvest")

    def test_conflict_returns_422_without_partial_write(self):
        with fake_excel([raw(), raw(up=8000)], header=HEADERS):
            res = self.client.post("/api/prices/upload_ozon", files={"file": ("conflict.xlsx", b"test")})
        self.assertEqual(res.status_code, 422)
        self.assertIn("разные значения", res.json()["detail"])
        self.assertEqual(self.snapshot(), [])
        self.assertEqual(db.query_all("SELECT * FROM mp_price_upload_log"), [])

    def test_log_failure_rolls_back_all_days(self):
        orig = db._Tx.execute_values

        def fail_log(tx, sql, *args, **kwargs):
            if "mp_price_upload_log" in sql:
                raise RuntimeError("simulated log failure")
            return orig(tx, sql, *args, **kwargs)
        with patch.object(db._Tx, "execute_values", fail_log), self.assertRaises(RuntimeError):
            self.upload(parse([raw(), raw(date="17.09.2026")]))
        self.assertEqual(self.snapshot(), [])

    def test_limits_invalid_zip_and_auth(self):
        with patch.object(pu, "WB_MAX_FILE_BYTES", 5):
            res = self.client.post("/api/prices/upload_ozon", files={"file": ("large.xlsx", b"123456")})
        self.assertEqual(res.status_code, 413)
        res = self.client.post("/api/prices/upload_ozon", files={"file": ("broken.xlsx", b"broken")})
        self.assertEqual(res.status_code, 422)
        app = FastAPI()
        app.include_router(pr.router)
        res = TestClient(app).post("/api/prices/upload_ozon", files={"file": ("x.xlsx", b"x")})
        self.assertIn(res.status_code, (401, 403))
        self.assertEqual(self.snapshot(), [])

    @unittest.skipUnless(FIXTURE, "Set OZON_PRICE_FIXTURE")
    def test_real_file_twice_end_to_end(self):
        report = pu.parse_ozon(FIXTURE)
        prepare_ozon(sorted({r.article for r in report.rows}))
        for _ in range(2):
            with open(FIXTURE, "rb") as f:
                res = self.client.post("/api/prices/upload_ozon", files={"file": ("Ozon.xlsx", f)})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["rows_upserted"], 266)
        self.assertEqual(len(self.snapshot()), 266)
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM mp_price_daily WHERE spp_pct IS NULL AND buyer_price IS NULL")["n"], 100)


def tearDownModule():
    if db._pool is not None:
        db._pool.close()
        db._pool = None
