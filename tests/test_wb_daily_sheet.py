# -*- coding: utf-8 -*-
"""Воронка WB: выбор листа с данными (Google-таблица «Воронка» и выгрузка 1С «TDSheet»)."""
import os
import tempfile
import unittest

import openpyxl

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-test-only")

from app import wb_daily_loader as wb
from app.routers import rnp_sales_router as rnp

HEADER = ["Дата", "ИдентификаторТовара", "Артикул", "ЗаказалиТоваров"]


def make_xlsx(sheets):
    """sheets: [(имя, строки)] → путь к временному .xlsx."""
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets:
        ws = book.create_sheet(name)
        for r in rows:
            ws.append(r)
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    book.save(path)
    return path


class WbDailySheetTest(unittest.TestCase):
    def read(self, sheets):
        path = make_xlsx(sheets)
        try:
            return wb._read_sheet(path)
        finally:
            os.remove(path)

    def test_google_sheet_skips_empty_first_sheet(self):
        data, name = self.read([
            ("Лист1", []),
            ("Воронка", [HEADER, ["2026-11-02", 3746071, "AGR-140 Smerch", 0]]),
            ("_История воронки", [["Дата", "Артикул WB", "Рейтинг", "Позиция", "Остаток"]]),
        ])
        self.assertEqual(name, "Воронка")
        self.assertEqual(data[0], HEADER)

    def test_1c_tdsheet_still_supported(self):
        data, name = self.read([("TDSheet", [HEADER, ["31.10.2026", 3746071, "AGR-140 SMERCH", 1]])])
        self.assertEqual(name, "TDSheet")
        self.assertEqual(data[1][2], "AGR-140 SMERCH")


class WbFbsRowsTest(unittest.TestCase):
    """РНП WB: «Остаток на складе WB» раскрывается в склады FBS; у Ozon этих строк нет."""

    def payload(self, **kw):
        return rnp._metric_payload(rnp.PRODUCT_METRIC_DEFS, product=True, **kw)

    def test_wb_stock_row_expands_to_fbs(self):
        rows = self.payload(is_wb=True)
        i = [m["key"] for m in rows].index("stock_ozon_qty")
        self.assertEqual(rows[i]["sub"], 5)
        self.assertEqual([(m["label"], m["sub"]) for m in rows[i + 1:i + 4]],
                         [("Склад FBS Москва", 6), ("Склад FBS Краснодар", 6), ("Склад FBS Артём", 6)])

    def test_ozon_and_yandex_have_no_fbs_rows(self):
        for kw in ({}, {"is_ya": True}):
            rows = self.payload(**kw)
            self.assertFalse([m for m in rows if m["key"].startswith("stock_fbs_")])
            self.assertEqual(next(m for m in rows if m["key"] == "stock_ozon_qty")["sub"], 0)

    def test_cell_keeps_fbs_only_with_value(self):
        agg = rnp.Agg()
        agg.add({"stock_ozon_qty": 5})
        self.assertNotIn("stock_fbs_msk", rnp._cell_from_agg(agg))
        agg.add({"stock_fbs_msk": 0, "stock_fbs_krd": 7})
        cell = rnp._cell_from_agg(agg)
        self.assertEqual((cell["stock_fbs_msk"], cell["stock_fbs_krd"]), (0, 7))
        self.assertNotIn("stock_fbs_art", cell)


if __name__ == "__main__":
    unittest.main()
