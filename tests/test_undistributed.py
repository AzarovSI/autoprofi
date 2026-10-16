"""Нераспределённые товары: единое правило и счёт за выбранный период (без БД)."""
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DB_DSN", "postgresql://wb_test:local-test-only@127.0.0.1:5432/wb_price_test")
os.environ.setdefault("SECRET_KEY", "isolated-test-only")

from app import db
from app.util import is_undistributed, UNDISTRIBUTED_SQL
from app.routers import catalog_router as catalog, rnp_router as ue

ADMIN = {"id": 1, "is_admin": True}
FULL = dict(category_l1="ECOM", category_l2="Г", category_l3="Т", status="CORE", manager="М")


class RuleTests(unittest.TestCase):
    def test_any_empty_field_means_undistributed(self):
        self.assertFalse(is_undistributed(*FULL.values()))
        for k in FULL:
            for empty in (None, "", "  "):
                with self.subTest(field=k, value=empty):
                    self.assertTrue(is_undistributed(*dict(FULL, **{k: empty}).values()))

    def test_catalog_filter_includes_manager(self):
        where, _ = catalog._items_where("Wildberries", None, True)
        self.assertIn(UNDISTRIBUTED_SQL, where)
        self.assertIn("cm.manager", UNDISTRIBUTED_SQL)


def fact(week, art, revenue=0.0, **kw):
    return dict(year=2026, week=week, seller_article=art, item_name="Товар " + art,
                revenue=revenue, **dict(FULL, **kw))


def ue_tree(rows):
    weeks = [dict(year=2026, week=w, period_text=f"Неделя {w}", period_start=None, period_end=None)
             for w in (40, 41, 42)]

    def query(sql, params=None):
        if "SELECT DISTINCT year" in sql:
            return weeks
        if "row_type = 'Товар'" in sql:
            return [dict(r) for r in rows]
        return []
    with patch.object(db, "query_all", side_effect=query), patch.object(db, "query_one", return_value=None):
        return json.loads(ue.rnp_tree(marketplace="wb", period="week", manager=None, user=ADMIN).body)


class UnitEconomyTests(unittest.TestCase):
    def test_periods_with_movement_and_totals_kept(self):
        res = ue_tree([
            fact(40, "OLD", 700, manager=None),       # движение только в 40-й
            fact(41, "OLD", 0, manager=None),         # строка без движения — не в периоды
            fact(42, "NEW", 300, status=None),
            fact(42, "OK", 1000),
            fact(41, "IDLE", 0, category_l1=None),    # нет движения вовсе — не в сообщении
        ])
        arts = {a["seller_article"]: a["periods"] for a in res["undistributed"]["articles"]}
        self.assertEqual(arts, {"OLD": {"2026-40": 700}, "NEW": {"2026-42": 300}})
        cells = res["tree"]["cells"]
        self.assertEqual((cells["2026-40"]["p_revenue"], cells["2026-42"]["p_revenue"]), (700, 1300))
        self.assertEqual([c["name"] for c in res["tree"]["children"]], ["ECOM"])


if __name__ == "__main__":
    unittest.main()
