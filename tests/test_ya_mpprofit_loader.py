"""Загрузчик MPPROFIT Яндекса: сверка с ИТОГО и строка «Общие удержания» из разницы ИТОГО − Σ строк (без БД).

Как в месячном отчёте за 09.2025: «Комиссия» по всем товарам 0, вся комиссия — только в ИТОГО; выплата и прибыль
ИТОГО меньше суммы строк ровно на удержания без разбивки (реальные отчёты 09.2025 и 09.2026 сверены 03.11.2026).
"""
import unittest

import openpyxl

from app import loader_core as lc

# (секция строки 4, метрика строки 5); у «Артикул продавца» и «Наименование» метрики нет — только строка 4.
COLS = [
    ("Артикул продавца", None), ("Наименование", None),
    ("Продажи", "Заказы шт."), ("Продажи", "Заказы, руб."), ("Продажи", "Выкупы шт."), ("Продажи", "Возвраты шт."),
    ("Продажи", "Выкупы руб."), ("Продажи", "Возвраты руб."), ("Продажи", "Продажи руб."), ("Продажи", "Продажи шт."),
    ("Себестоимость", "Себестоимость"), ("Комиссии", "Комиссия"), ("Платежи", "Итого"),
    ("Логистика", "Вся логистика"), ("Продвижение", "Итого"), ("Другие удержания", "Хранение"),
    ("Все удержания ЯМ", "Все удержания"), ("Налог", "Налог"), ("Внешние расходы", "Внешние расходы"),
    ("Финансы", "Выплата на р/с"), ("Финансы", "Прибыль"), ("Финансы", "Марж-ность"),
]


def key(sec, met):
    return f"{sec} :: {met}" if met else sec


def item(art, revenue, commission=0.0, logistics=50.0, promo=20.0, cogs=300.0):
    holds = commission + logistics + promo
    return {"Артикул продавца": art, "Наименование": "Товар " + art,
            "Продажи :: Заказы шт.": 5, "Продажи :: Заказы, руб.": revenue * 1.2, "Продажи :: Выкупы шт.": 4,
            "Продажи :: Возвраты шт.": 0, "Продажи :: Выкупы руб.": revenue, "Продажи :: Возвраты руб.": 0,
            "Продажи :: Продажи руб.": revenue, "Продажи :: Продажи шт.": 4, "Себестоимость :: Себестоимость": cogs,
            "Комиссии :: Комиссия": commission, "Логистика :: Вся логистика": logistics, "Продвижение :: Итого": promo,
            "Все удержания ЯМ :: Все удержания": holds, "Финансы :: Выплата на р/с": revenue - holds,
            "Финансы :: Прибыль": revenue - holds - cogs}


def make_ws(rows, unallocated=None, total_override=None):
    """unallocated — удержания, которых нет в строках (только в ИТОГО): {колонка: сумма}."""
    ws = openpyxl.Workbook().active
    ws.cell(2, 3, "Период: 2025-09-01 - 2025-09-30")
    keys = [key(s, m) for s, m in COLS]
    prev = None
    for c, (sec, met) in enumerate(COLS, 1):
        if sec != prev:
            ws.cell(4, c, sec)
        prev = sec
        if met:
            ws.cell(5, c, met)
    for r, row in enumerate(rows, 7):
        for c, k in enumerate(keys, 1):
            if k in row:
                ws.cell(r, c, row[k])
    extra = unallocated or {}
    extra_holds = sum(extra.values())
    for c, k in enumerate(keys, 1):
        if c <= 2:
            continue
        total = sum(row.get(k, 0) or 0 for row in rows) + extra.get(k, 0)
        if k == "Все удержания ЯМ :: Все удержания":
            total += extra_holds
        if k in ("Финансы :: Выплата на р/с", "Финансы :: Прибыль"):
            total -= extra_holds
        ws.cell(6, c, total)
    for k, v in (total_override or {}).items():
        ws.cell(6, keys.index(k) + 1, v)
    return ws


ROWS = [item("A-1", 1000), item("B-2", 2000)]
# 09.2025: комиссия 1 428 686 ₽ — только в ИТОГО; плюс нераспределённая логистика.
UNALLOC = {"Комиссии :: Комиссия": 400.0, "Логистика :: Вся логистика": 30.0}


class YaCheckTest(unittest.TestCase):
    def check(self, rows=ROWS, **kw):
        ws = make_ws(rows, **kw)
        return lc.check_ya_report(ws, lc.build_ya_colidx(ws))

    def test_consistent_report_passes(self):
        self.assertEqual(self.check(), ([], [], {}))

    def test_unallocated_commission_goes_to_hold_row(self):
        errors, warnings, deltas = self.check(unallocated=UNALLOC)
        self.assertEqual((errors, warnings), ([], []))
        self.assertEqual(deltas, {"holds_total": 430.0, "payout": -430.0, "profit": -430.0,
                                  "commission": 400.0, "logistics_total": 30.0})

    def test_negative_remainder_kept(self):
        # Возврат удержаний без разбивки: остаток отрицательный — не обнуляется (как Ozon с 14.10.2026).
        _, _, deltas = self.check(unallocated={"Логистика :: Вся логистика": -25.0})
        self.assertEqual(deltas["holds_total"], -25.0)
        self.assertEqual(deltas["payout"], 25.0)

    def test_row_without_article_goes_to_hold_row(self):
        # load_report не пишет товар без артикула — его суммы уходят в «Общие удержания», а не пропадают.
        orphan = dict(item("", 500, logistics=10, promo=0), **{"Артикул продавца": None})
        ws = make_ws(ROWS + [orphan])
        errors, _, deltas = lc.check_ya_report(ws, lc.build_ya_colidx(ws))
        self.assertTrue(any("«Продажи :: Продажи руб.»" in e for e in errors))   # продажи без артикула — отказ
        ws = make_ws(ROWS + [dict(orphan, **{"Продажи :: Продажи руб.": 0, "Продажи :: Выкупы руб.": 0,
                                             "Продажи :: Продажи шт.": 0, "Продажи :: Выкупы шт.": 0,
                                             "Себестоимость :: Себестоимость": 0, "Финансы :: Выплата на р/с": -10,
                                             "Финансы :: Прибыль": -10})])
        errors, _, deltas = lc.check_ya_report(ws, lc.build_ya_colidx(ws))
        self.assertEqual(errors, [])
        self.assertEqual((deltas["holds_total"], deltas["logistics_total"], deltas["payout"]), (10.0, 10.0, -10.0))

    def test_strict_column_mismatch_rejected(self):
        errors, _, _ = self.check(total_override={"Продажи :: Продажи руб.": 3500})
        self.assertTrue(any("«Продажи :: Продажи руб.»" in e for e in errors))

    def test_orders_total_mismatch_only_warns(self):
        errors, warnings, _ = self.check(total_override={"Продажи :: Заказы шт.": 1})
        self.assertEqual(errors, [])
        self.assertIn("«Продажи :: Заказы шт.»", warnings[0])

    def test_row_payout_identity_only_warns(self):
        # У Яндекса такие строки есть в самом отчёте MPPROFIT (EDV25-20RW 10.2026) — период не отклоняем.
        bad = dict(ROWS[0], **{"Финансы :: Выплата на р/с": 999})
        errors, warnings, _ = self.check(rows=[bad, ROWS[1]])
        self.assertEqual(errors, [])
        self.assertTrue(any("«Выплата на р/с» ≠ «Продажи руб.» − «Все удержания» в 1 стр." in w for w in warnings))

    def test_buyouts_rub_column_name(self):
        # В отчёте «Выкупы руб.» без запятой; до 03.11.2026 загрузчик искал «Выкупы, руб.» и не читал колонку.
        ws = make_ws(ROWS)
        idx = lc.build_ya_colidx(ws)
        self.assertIn(dict(lc.YA_MAP)["sales_gross"], idx)


if __name__ == "__main__":
    unittest.main()
