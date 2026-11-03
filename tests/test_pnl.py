"""Раздел «P&L» (`/api/pnl/data`, без БД): вкладка → площадки ответа; права вкладки и площадки роли."""
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


if __name__ == "__main__":
    unittest.main()
