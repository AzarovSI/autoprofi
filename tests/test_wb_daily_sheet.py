# -*- coding: utf-8 -*-
"""Воронка WB: выбор листа с данными (Google-таблица «Воронка» и выгрузка 1С «TDSheet»)."""
import os
import tempfile
import unittest

import openpyxl

from app import wb_daily_loader as wb

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


if __name__ == "__main__":
    unittest.main()
