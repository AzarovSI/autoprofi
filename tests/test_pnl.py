"""Раздел «P&L» (`/api/pnl/data`, без БД): вкладка → площадки ответа; права вкладки и площадки роли; помесячные итоги."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-test-only")

from fastapi import HTTPException

from app.routers import pnl_router as pnl

ADMIN = {"id": 1, "is_admin": True}


def user(perms, mps=("Ozon", "Wildberries")):
    return {"id": 2, "is_admin": False, "permissions": ["pnl.open", *perms], "marketplaces": list(mps)}


class PnlDataTest(unittest.TestCase):
    def call(self, mp, u):
        with patch.object(pnl, "month_totals", side_effect=lambda keys: {"mp": {k: {} for k in keys}, "partial": {}}):
            return pnl.pnl_data(marketplace=mp, user=u)

    def forbidden(self, mp, u):
        with self.assertRaises(HTTPException) as e:
            self.call(mp, u)
        self.assertEqual(e.exception.status_code, 403)

    def test_tab_to_marketplaces(self):
        self.assertEqual(sorted(self.call("cross", ADMIN)["mp"]), ["ozon", "vb"])
        self.assertEqual(list(self.call("ozon", ADMIN)["mp"]), ["ozon"])
        self.assertEqual(list(self.call("wb", ADMIN)["mp"]), ["vb"])

    def test_unknown_tab(self):
        with self.assertRaises(HTTPException) as e:
            self.call("wildberries", ADMIN)
        self.assertEqual(e.exception.status_code, 400)

    def test_tab_right_required(self):
        self.forbidden("ozon", user(["pnl.tab.wb"]))
        self.assertEqual(list(self.call("wb", user(["pnl.tab.wb"]))["mp"]), ["vb"])

    def test_marketplace_of_role(self):
        """«Сводная» — только при обеих площадках роли; вкладка площадки — при её площадке."""
        self.forbidden("cross", user(["pnl.tab.cross"], mps=("Wildberries",)))
        self.forbidden("ozon", user(["pnl.tab.ozon"], mps=("Wildberries",)))
        self.assertEqual(sorted(self.call("cross", user(["pnl.tab.cross"]))["mp"]), ["ozon", "vb"])


class MonthTotalsTest(unittest.TestCase):
    """«Итоги» помесячной юнит-экономики: ключ месяца «ГГГГ-ММ», пустые месяцы не отдаются, нет показателя — None, а не 0."""

    def test_root_cells_by_month(self):
        def fake_tree(mp, monthly=False, mgr_sel=None):
            self.assertTrue(monthly)
            self.assertIsNone(mgr_sel)
            return {"weeks": [{"key": "2026-9", "year": 2026, "week": 9},
                              {"key": "2026-10", "year": 2026, "week": 10, "partial_through": "2026-10-25"},
                              {"key": "2026-11", "year": 2026, "week": 11}],
                    "tree": {"cells": {
                        "2026-9": {"revenue": 1000.0, "profit": 150.0, "p_payout": 400.0, "p_mp_holds_pct": 0.5,
                                   "noart_pay_pct": 0.02, "p_commission_pct": 0.3, "p_logistics_pct": 0.1,
                                   "p_fines_pct": 0.001, "p_ads_paid_pct": 0.07, "p_cogs_pct": 0.25},
                        "2026-10": {"revenue": 500.0, "profit": 50.0, "p_payout": 200.0, "p_mp_holds_pct": 0.4,
                                    "noart_pay_pct": None},
                        "2026-11": {"revenue": 0.0}}}}

        with patch.object(pnl.rnp_router, "build_tree", side_effect=fake_tree):
            res = pnl.month_totals(("ozon", "vb"))
        for mp in ("ozon", "vb"):
            self.assertEqual(sorted(res["mp"][mp]), ["2026-09", "2026-10"])
            m9 = res["mp"][mp]["2026-09"]
            self.assertEqual((m9["rev"], m9["payout"], m9["profit"]), (1000.0, 400.0, 150.0))
            self.assertEqual((m9["mp_holds"], m9["noart"], m9["commission"], m9["logistics"], m9["fines"]),
                             (0.5, 0.02, 0.3, 0.1, 0.001))
            self.assertIsNone(m9["acceptance"])
            self.assertIsNone(res["mp"][mp]["2026-10"]["noart"])
            self.assertEqual(res["partial"][mp], {"2026-10": "2026-10-25"})


if __name__ == "__main__":
    unittest.main()
