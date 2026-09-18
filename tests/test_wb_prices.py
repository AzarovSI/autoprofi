"""Regression tests. SQL tests refuse any database except local wb_price_test."""
import datetime as dt
import os
from decimal import Decimal as D
from pathlib import Path
from contextlib import contextmanager
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-wb-test-key-not-for-production")

from fastapi import FastAPI
from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict

from app import auth, db, price_history, price_upload as pu
from app.routers import prices_router as pr

DAY = dt.date(2026, 9, 18)
HEADERS = ["Дата", "Время", "Артикул WB", "Артикул продавца", "Ссылка",
           "Цена со скидки (загружаемая)", "Цена на витрине (с СПП)", "СПП, %", "Регион"]
FIXTURE = os.environ.get("WB_PRICE_FIXTURE")


@contextmanager
def fake_excel(rows, header=None, sheets=None):
    data = sheets or {"Лист1": [], "Цены с СПП": [header or HEADERS, *rows]}

    class Workbook:
        sheet_names = list(data)

        def get_sheet_by_name(self, name):
            class Sheet:
                height = len(data[name])
                width = max((len(r) for r in data[name]), default=0)

                def to_python(self):
                    return data[name]
            return Sheet()
    with patch.object(pu, "_validate_wb_container"), patch.object(pu.CalamineWorkbook, "from_path", return_value=Workbook()):
        yield


def raw_row(date=DAY, time="08:24", article="SKU-1", up=1000, buyer=700, spp=0.3, region="Москва"):
    return [date, time, 123, article, "", up, buyer, spp, region]


def parse_rows(rows, **kw):
    with fake_excel(rows, **kw):
        return pu.parse_wb("mock.xlsx")


def make_app():
    app = FastAPI()
    app.include_router(pr.router)
    app.dependency_overrides[auth.get_current_user] = lambda: {"id": 1, "role": "manager"}
    return app


def prepare_local_db(articles=("SKU-1", "SKU-2")):
    info = conninfo_to_dict(db.DB_DSN)
    if info.get("host") not in ("127.0.0.1", "localhost") or info.get("dbname") != "wb_price_test":
        raise RuntimeError("Tests may only write to local wb_price_test")
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute(Path(__file__).with_name("wb_test_schema.sql").read_text())
        conn.execute("""TRUNCATE mp_price_daily, mp_price_upload_log,
            wb_daily_sales, ozon_daily_sales, catalog_items, catalog_marketplace,
            catalog_base_prices, catalog_base_prices_hist, fact_weekly, app_users""")
        conn.execute("INSERT INTO app_users VALUES (1,'qa@local.test','Тестовый менеджер','manager')")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO catalog_marketplace VALUES (%s,'Wildberries','Активный')",
                [(a,) for a in articles],
            )
            cur.executemany(
                """INSERT INTO catalog_items VALUES (%s,%s,'Продукция ECOM','Тестовая группа','Товары')""",
                [(a, a) for a in articles],
            )
            cur.executemany(
                """INSERT INTO fact_weekly(seller_article,marketplace,period_start,period_end,revenue)
                   VALUES (%s,'Wildberries','2026-09-01','2026-09-30',100)""",
                [(a,) for a in articles],
            )
            cur.executemany(
                """INSERT INTO catalog_base_prices_hist
                   VALUES (%s,'Wildberries',1000,'2026-09-01',NULL,1)""",
                [(a,) for a in articles],
            )


class ParserTests(unittest.TestCase):
    def test_blank_first_sheet_and_real_percent(self):
        row = parse_rows([raw_row(spp=0.3530458885729556, buyer=9224, up=14257.58)]).rows[0]
        self.assertEqual(row.buyer_price, D("9224.00"))
        self.assertEqual(row.spp_pct, D("0.3530458885729556"))
        self.assertEqual(row.upload_price, D("14257.58"))

    def test_no_stock_even_when_buyer_is_positive(self):
        for spp in (0, 0.0, "0%", None, "", "—"):
            with self.subTest(spp=spp):
                row = parse_rows([raw_row(spp=spp, buyer=700)]).rows[0]
                self.assertIsNone(row.spp_pct)
                self.assertIsNone(row.buyer_price)
                self.assertEqual(row.upload_price, D("1000.00"))

    def test_latest_time_not_last_row_and_multiple_dates(self):
        report = parse_rows([
            raw_row(time="18:00", up=1200),
            raw_row(time="09:00", up=1100),
            raw_row(date=dt.date(2026, 9, 17), time="20:00", up=900),
        ])
        self.assertEqual([r.upload_price for r in report.rows], [D("900"), D("1200")])
        self.assertEqual(report.duplicates_by_date[DAY], 1)
        self.assertEqual(report.rows_in_file, 3)

    def test_duplicate_same_values_is_deduplicated(self):
        report = parse_rows([raw_row(), raw_row()])
        self.assertEqual(len(report.rows), 1)
        self.assertEqual(report.duplicates_by_date[DAY], 1)

    def test_conflicting_equal_times_rejected(self):
        with self.assertRaisesRegex(ValueError, "одну дату и время"):
            parse_rows([raw_row(), raw_row(up=1300)])

    def test_multiple_regions_rejected(self):
        with self.assertRaisesRegex(ValueError, "несколько регионов"):
            parse_rows([raw_row(), raw_row(time="19:00", region="Казань")])

    def test_canonical_article_and_header_alias(self):
        header = HEADERS.copy()
        header[5] = "Цена со скидкой (загружаемая)"
        row = parse_rows([raw_row(article=" agr-140  Smerch ", spp="35,3%", up="1 000,25")], header=header).rows[0]
        self.assertEqual(row.article, "AGR-140 SMERCH")
        self.assertEqual(row.spp_pct, D(".353"))
        self.assertEqual(row.upload_price, D("1000.25"))

    def test_dates_and_excel_time(self):
        for date in ("18.09.2026", "2026-09-18", dt.datetime(2026, 9, 18)):
            row = parse_rows([raw_row(date=date, time=0.5)]).rows[0]
            self.assertEqual(row.date, DAY)
            self.assertEqual(row.time, dt.time(12))

    def test_missing_time(self):
        self.assertEqual(parse_rows([raw_row(time="")]).rows[0].time, dt.time.min)

    def test_bad_values_fail_instead_of_silent_skip(self):
        for kwargs in ({"date": "no"}, {"up": -1}, {"up": 0}, {"up": "NaN"},
                       {"spp": -0.1}, {"spp": 100}, {"time": "25:00"},
                       {"buyer": 0}, {"buyer": None}, {"article": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                parse_rows([raw_row(), raw_row(**kwargs)])

    def test_old_template_rejected(self):
        with self.assertRaisesRegex(ValueError, "Старый шаблон"):
            parse_rows([], sheets={"Sheet1": [["Артикул продавца", "Текущая цена", "Текущая скидка"], ["SKU-1", 100, 5]]})

    def test_sheet_limits(self):
        with self.assertRaisesRegex(ValueError, "Слишком много листов"):
            parse_rows([], sheets={str(i): [] for i in range(21)})
        with self.assertRaisesRegex(ValueError, "Слишком большой лист"):
            parse_rows([], sheets={"wide": [[None] * 65]})

    @unittest.skipUnless(FIXTURE, "Set WB_PRICE_FIXTURE for real workbook test")
    def test_real_workbook(self):
        report = pu.parse_wb(FIXTURE)
        self.assertEqual(report.rows_in_file, 826)
        self.assertEqual(len(report.rows), 826)
        self.assertEqual(sum(r.spp_pct is None for r in report.rows), 378)
        self.assertEqual(sorted(report.rows_by_date.values()), [413, 413])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        self.client = TestClient(make_app())

    def upload(self, report):
        with patch.object(pu, "parse_wb", return_value=report):
            return self.client.post("/api/prices/upload_wb", files={"file": ("test.xlsx", b"test")})

    def snapshot(self):
        return db.query_all("SELECT * FROM mp_price_daily ORDER BY marketplace,date,seller_article")

    def test_multi_day_upload_and_repeat_overwrite_only_matching_key(self):
        first = parse_rows([raw_row(date=dt.date(2026, 9, 17), up=900), raw_row(), raw_row(article="UNKNOWN")])
        res = self.upload(first)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["report_dates"], ["2026-09-17", "2026-09-18"])
        self.assertEqual(res.json()["rows_upserted"], 2)
        self.assertEqual(res.json()["skipped_unknown"], 1)
        self.assertEqual(len(db.query_all("SELECT * FROM mp_price_upload_log")), 2)
        res = self.upload(parse_rows([raw_row(up=1500, spp=0)]))
        self.assertEqual(res.status_code, 200)
        rows = self.snapshot()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["upload_price"], D("900"))
        self.assertEqual(rows[1]["upload_price"], D("1500"))
        self.assertIsNone(rows[1]["buyer_price"])
        self.assertIsNone(rows[1]["spp_pct"])

    def test_rnp_cannot_replace_direct_wb_prices_or_missing_stock(self):
        self.upload(parse_rows([raw_row(), raw_row(article="SKU-2", spp=0)]))
        before = self.snapshot()
        db.execute("INSERT INTO wb_daily_sales VALUES ('SKU-1',%s,.5),('SKU-2',%s,.5)", (DAY, DAY))
        price_history.recalc_spp_and_buyer_from("Wildberries", DAY)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_wb_and_ozon_recalc_remain_unchanged(self):
        db.execute("INSERT INTO wb_daily_sales VALUES ('SKU-1',%s,.4)", (DAY,))
        db.execute("INSERT INTO ozon_daily_sales VALUES ('SKU-1',%s,.2)", (DAY,))
        with db.transaction() as tx:
            for mp, source in (("Wildberries", "upload_wb"), ("Ozon", "upload_ozon")):
                price_history.sync_uploaded_prices(tx, mp, DAY, [("SKU-1", 1000)], source, 1)
        oz_before = db.query_one("SELECT * FROM mp_price_daily WHERE marketplace='Ozon'")
        self.upload(parse_rows([raw_row(spp=.3)]))
        self.assertEqual(db.query_one("SELECT * FROM mp_price_daily WHERE marketplace='Ozon'"), oz_before)
        db.execute("UPDATE ozon_daily_sales SET spp_pct=.1")
        price_history.recalc_spp_and_buyer_from("Ozon", DAY)
        oz = db.query_one("SELECT * FROM mp_price_daily WHERE marketplace='Ozon'")
        self.assertEqual(oz["buyer_price"], D("900"))
        self.assertEqual(oz["spp_pct"], D(".1"))
        db.execute("""INSERT INTO mp_price_daily(seller_article,marketplace,date,upload_price,upload_source)
                      VALUES ('SKU-2','Wildberries',%s,1000,'upload_wb')""", (DAY,))
        db.execute("INSERT INTO wb_daily_sales VALUES ('SKU-2',%s,.4)", (DAY,))
        price_history.recalc_spp_and_buyer_from("Wildberries", DAY)
        legacy = db.query_one("SELECT * FROM mp_price_daily WHERE seller_article='SKU-2'")
        self.assertEqual(legacy["buyer_price"], D("600"))

    def test_history_cutoff_and_base_price_history(self):
        self.upload(parse_rows([raw_row(date=dt.date(2026, 9, 17), up=900, buyer=630),
                                raw_row(up=1100, buyer=770)]))
        db.execute("""INSERT INTO catalog_base_prices_hist
                      VALUES ('SKU-1','Wildberries',1200,'2026-09-18 10:00+00',NULL,1)""")
        for date, upload, base in (("2026-09-16", None, 1000), ("2026-09-17", 900, 1000),
                                  ("2026-09-18", 1100, 1200), ("2026-09-20", 1100, 1200)):
            res = self.client.get("/api/prices/pricelist", params={"date_from": "2026-09-01", "date_to": date})
            self.assertEqual(res.status_code, 200, res.text)
            item = next(r for r in res.json()["items"] if r["seller_article"] == "SKU-1")
            self.assertEqual(item["wb_upload_price"], upload)
            self.assertEqual(item["price_wb"], base)

    def test_validation_failure_writes_nothing(self):
        with patch.object(pu, "parse_wb", side_effect=ValueError("строка 2: неверная дата")):
            res = self.client.post("/api/prices/upload_wb", files={"file": ("bad.xlsx", b"bad")})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.snapshot(), [])
        self.assertEqual(db.query_all("SELECT * FROM mp_price_upload_log"), [])

    def test_oversize_file_rejected_without_write(self):
        with patch.object(pu, "WB_MAX_FILE_BYTES", 5):
            res = self.client.post("/api/prices/upload_wb", files={"file": ("large.xlsx", b"123456")})
        self.assertEqual(res.status_code, 413)
        self.assertEqual(self.snapshot(), [])

    def test_log_failure_rolls_back_entire_upload(self):
        report = parse_rows([raw_row()])
        orig = db._Tx.execute_values

        def fail_log(tx, sql, *args, **kwargs):
            if "mp_price_upload_log" in sql:
                raise RuntimeError("simulated log failure")
            return orig(tx, sql, *args, **kwargs)
        with patch.object(db._Tx, "execute_values", fail_log), self.assertRaisesRegex(RuntimeError, "simulated"):
            self.upload(report)
        self.assertEqual(self.snapshot(), [])

    def test_ozon_requires_manual_date_wb_does_not(self):
        self.assertEqual(self.client.post("/api/prices/upload_ozon", files={"file": ("x.xlsx", b"x")}).status_code, 422)
        with patch.object(pu, "parse_ozon", return_value=[("SKU-1", D(1000))]):
            res = self.client.post("/api/prices/upload_ozon?report_date=2026-09-18", files={"file": ("x.xlsx", b"x")})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["marketplace"], "Ozon")

    def test_upload_requires_auth_in_real_router(self):
        app = FastAPI()
        app.include_router(pr.router)
        res = TestClient(app).post("/api/prices/upload_wb", files={"file": ("x.xlsx", b"x")})
        self.assertIn(res.status_code, (401, 403))

    @unittest.skipUnless(FIXTURE, "Set WB_PRICE_FIXTURE for real workbook test")
    def test_real_file_end_to_end(self):
        report = pu.parse_wb(FIXTURE)
        prepare_local_db(sorted({r.article for r in report.rows}))
        with open(FIXTURE, "rb") as f:
            res = self.client.post("/api/prices/upload_wb", files={"file": ("SPP-VB.xlsx", f)})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["rows_upserted"], 826)
        self.assertEqual(len(self.snapshot()), 826)
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM mp_price_daily WHERE spp_pct IS NULL AND buyer_price IS NULL")["n"], 378)
        row = db.query_one("SELECT * FROM mp_price_daily WHERE seller_article='AGR-120' AND date=%s", (DAY,))
        self.assertEqual(row["buyer_price"], D("9224"))
        self.assertEqual(row["upload_price"], D("14257.58"))
        self.assertFalse(row["spp_is_estimated"])


def tearDownModule():
    if db._pool is not None:
        db._pool.close()
        db._pool = None


if __name__ == "__main__":
    unittest.main()
