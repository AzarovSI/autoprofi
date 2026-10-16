"""Выгрузка справочника — с тем же фильтром, что таблица на странице (без записи в БД)."""
import io
import unittest
from unittest.mock import patch

import openpyxl

from app import db
from app.routers import catalog_router as catalog

ADMIN = {"id": 1, "is_admin": True}
ROW = {"seller_article": "SKU/1", "sample_name": "Товар", "category_l1": None, "category_l2": None,
       "category_l3": None, "status": None, "manager": "М", "product_url": None}


async def _collect(it):
    return b"".join([chunk async for chunk in it])


class CatalogExportFilterTests(unittest.TestCase):
    def run_export(self, **kw):
        import asyncio
        with patch.object(db, "query_all", return_value=[ROW]) as q:
            resp = catalog.export_items(marketplace=kw.get("marketplace", "Wildberries"),
                                        search=kw.get("search"),
                                        only_uncategorized=kw.get("only_uncategorized", False),
                                        articles=kw.get("articles"), user=ADMIN)
            body = asyncio.run(_collect(resp.body_iterator))
        return q.call_args.args[0], q.call_args.args[1], body

    def test_full_export_without_filter(self):
        sql, params, _ = self.run_export()
        self.assertEqual(params, ("Wildberries",))
        self.assertNotIn(catalog.UNDISTRIBUTED_SQL, sql)

    def test_only_uncategorized_same_condition_as_table(self):
        sql, params, body = self.run_export(only_uncategorized=True, search="AK")
        self.assertIn(catalog.UNDISTRIBUTED_SQL, sql)
        self.assertEqual(params, ("Wildberries", "%AK%"))
        ws = openpyxl.load_workbook(io.BytesIO(body)).active
        self.assertEqual(ws.max_row, 2)  # шапка + отобранная строка
        # таблица страницы строит тот же отбор
        where, p = catalog._items_where("Wildberries", "AK", True)
        self.assertTrue(all(w in sql for w in where))

    def test_articles_list_from_rnp(self):
        sql, params, _ = self.run_export(articles="ak-590\nPR-800 ")
        self.assertIn("cm.seller_article = ANY(%s)", sql)
        self.assertEqual(params, ("Wildberries", ["AK-590", "PR-800"]))

    def test_empty_articles_list_is_not_full_export(self):
        sql, params, _ = self.run_export(articles="")
        self.assertIn("cm.seller_article = ANY(%s)", sql)
        self.assertEqual(params, ("Wildberries", []))


if __name__ == "__main__":
    unittest.main()
