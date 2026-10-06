"""Catalog saves invalidate all marketplace RNP trees; no database writes."""
import io
import unittest
from unittest.mock import patch

import openpyxl
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from app import auth, cache, db
from app.routers import catalog_router as catalog

NAMESPACES = ("rnp_sales_tree", "rnp_sales_tree_wb", "rnp_sales_tree_ya")


class CatalogStatusCacheTests(unittest.TestCase):
    def tearDown(self):
        for ns in (*NAMESPACES, "unrelated"):
            cache.discard_namespace(ns)

    def seed(self):
        for ns in NAMESPACES:
            cache.set(ns, "status-test:" + ns, "old")
        cache.set("unrelated", "status-test:unrelated", "keep")

    def assert_invalidated(self):
        for ns in NAMESPACES:
            self.assertIsNone(cache.get(ns, "status-test:" + ns))
        self.assertEqual(cache.get("unrelated", "status-test:unrelated"), "keep")

    def test_status_saved_only_to_selected_marketplace_and_all_caches_reset(self):
        for mp in ("Wildberries", "Ozon", "Yandex"):
            with self.subTest(mp=mp), patch.object(db, "execute") as execute:
                self.seed()
                result = catalog.update_item(
                    {"seller_article": "SKU/1", "marketplace": mp, "status": "Closeout"},
                    user={"id": 1})
                updates = [c for c in execute.call_args_list
                           if "UPDATE catalog_marketplace" in c.args[0]]
                self.assertEqual(len(updates), 1)
                self.assertEqual(updates[0].args[1], ("Closeout", "SKU/1", mp))
                self.assertEqual(result["marketplace"], mp)
                self.assert_invalidated()

    def test_common_category_save_invalidates_all_marketplaces(self):
        self.seed()
        with patch.object(db, "execute"):
            catalog.update_item({"seller_article": "SKU/1", "marketplace": "wb",
                                 "category_l1": "Category"}, user={"id": 1})
        self.assert_invalidated()

    def test_catalog_import_invalidates_all_marketplaces(self):
        book = openpyxl.Workbook()
        book.active.append(["Артикул", "Наименование", "У1", "У2", "У3", "Статус"])
        book.active.append(["SKU/1", "Item", "C1", "C2", "C3", "NEW"])
        data = io.BytesIO()
        book.save(data)
        data.seek(0)
        self.seed()
        with patch.object(db, "execute_values") as execute:
            result = catalog.import_items(UploadFile(filename="fixture.xlsx", file=data),
                                          marketplace="Wildberries", user={"id": 1})
        self.assertEqual(result["marketplace"], "Wildberries")
        self.assertEqual(execute.call_args_list[1].args[1][0][1], "Wildberries")
        self.assert_invalidated()

    def test_failed_save_does_not_report_success(self):
        self.seed()
        with patch.object(db, "execute", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                catalog.update_item({"seller_article": "SKU/1", "marketplace": "wb",
                                     "status": "CORE"}, user={"id": 1})
        self.assertEqual(cache.get(NAMESPACES[1], "status-test:" + NAMESPACES[1]), "old")

    def test_update_endpoint_rejects_anonymous_user(self):
        app = FastAPI()
        app.include_router(catalog.router)
        with TestClient(app) as client, patch.object(db, "execute") as execute:
            response = client.put("/api/catalog/items", json={
                "seller_article": "SKU/1", "marketplace": "Wildberries", "status": "CORE"})
        self.assertEqual(response.status_code, 401)
        execute.assert_not_called()
