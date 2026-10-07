"""WB percentage scale: both report sources, local DB only."""
import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

import openpyxl
import psycopg
from app import wb_reviews_loader as loader


class PercentTests(unittest.TestCase):
    def test_explicit_scale_and_small_legacy_percent(self):
        for value, fmt, expected in [
            (31.3131, False, .3131), (.313131313, True, .3131),
            ("31,3131%", False, .3131), ("31,3131%", True, .3131),
            (.5, False, .005), (.5, True, .5),
            (1, False, .01), (1, True, 1), (0, True, 0),
            (None, True, None), ("", False, None), ("-", False, None),
        ]:
            with self.subTest(value=value, fmt=fmt):
                self.assertEqual(loader._to_spp(value, fmt), expected)

    def test_invalid_range(self):
        for value, percent in [(-1, False), (101, False), (31, True),
                               (float("nan"), False), (float("inf"), True)]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    loader._to_spp(value, percent)

    def test_workbook_formats_and_fallback_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.xlsx")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "⭐ Рейтинг и отзывы"
            ws.append(["Дата", "Артикул", "КоличествоОтзывов", "ПроцентСПП"])
            formats = ["0.0%", "General", '0.00"%"', r"0.00\%", "0.0%"]
            values = [.313131313, 31.3131, 31.3131, 31.3131, "31,3131%"]
            for i, (value, fmt) in enumerate(zip(values, formats), 2):
                ws.append([dt.date(2026, 10, i), "SKU-1", 42, value])
                ws.cell(i, 4).number_format = fmt
            wb.save(path)
            data, name, percent = loader._read_sheet(path)
            self.assertEqual(name, ws.title)
            self.assertEqual(percent, {(1, 3), (5, 3)})
            for i, row in enumerate(data[1:], 1):
                self.assertEqual(loader._to_spp(row[3], (i, 3) in percent), .3131)

    def test_multiday_repeat_blank_and_catalog_scope(self):
        # Never use DB_DSN: this test is hard-bound to an isolated local database.
        with psycopg.connect("postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test") as conn:
            conn.execute("""
                CREATE TEMP TABLE catalog_items (seller_article text);
                INSERT INTO catalog_items VALUES ('SKU-1');
                CREATE TEMP TABLE wb_daily_sales (
                    date date, seller_article text, reviews_qty numeric,
                    spp_pct numeric, upload_id bigint, orders_qty numeric,
                    PRIMARY KEY(date,seller_article));
                CREATE TEMP TABLE report_uploads (
                    id bigserial PRIMARY KEY, marketplace text, year int,
                    source_file text, rows_loaded int, status text, message text,
                    period_kind text, period_text text, period_start date, period_end date);
                INSERT INTO wb_daily_sales VALUES
                    ('2026-10-06','SKU-1',1,.0031,NULL,7)
            """)
            conn.commit()
            header = ["Дата", "Артикул", "КоличествоОтзывов", "ПроцентСПП"]
            rows = [header, ["06.10.2026", "SKU-1", 42, .313131313],
                    ["07.10.2026", "SKU-1", 43, 32.82828],
                    ["07.10.2026", "UNKNOWN", 50, 31]]
            with patch.object(loader, "_read_sheet", return_value=(rows, "Sheet", {(1, 3)})):
                for _ in range(2):
                    self.assertTrue(loader.load_wb_reviews(conn, "fixture.xlsx")["ok"])
            got = conn.execute("SELECT spp_pct, orders_qty FROM wb_daily_sales ORDER BY date").fetchall()
            self.assertEqual([(float(a), b) for a, b in got], [(.3131, 7), (.3283, None)])
            with patch.object(loader, "_read_sheet", return_value=([header, ["06.10.2026", "SKU-1", 44, None]], "Sheet", set())):
                self.assertTrue(loader.load_wb_reviews(conn, "fixture.xlsx")["ok"])
            self.assertEqual(float(conn.execute("SELECT spp_pct FROM wb_daily_sales WHERE date='2026-10-06'").fetchone()[0]), .3131)


if __name__ == "__main__":
    unittest.main()
