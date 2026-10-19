"""Catalog saves invalidate all marketplace RNP trees; no database writes."""
import io
import unittest
from unittest.mock import patch

import openpyxl
from fastapi import FastAPI, HTTPException, UploadFile
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
                    user={"id": 1, "is_admin": True})
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
                                 "category_l1": "Category"}, user={"id": 1, "is_admin": True})
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
                                          marketplace="Wildberries", user={"id": 1, "is_admin": True})
        self.assertEqual(result["marketplace"], "Wildberries")
        self.assertEqual(execute.call_args_list[1].args[1][0][1], "Wildberries")
        self.assert_invalidated()

    def test_failed_save_does_not_report_success(self):
        self.seed()
        with patch.object(db, "execute", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                catalog.update_item({"seller_article": "SKU/1", "marketplace": "wb",
                                     "status": "CORE"}, user={"id": 1, "is_admin": True})
        self.assertEqual(cache.get(NAMESPACES[1], "status-test:" + NAMESPACES[1]), "old")

    def test_update_endpoint_rejects_anonymous_user(self):
        app = FastAPI()
        app.include_router(catalog.router)
        with TestClient(app) as client, patch.object(db, "execute") as execute:
            response = client.put("/api/catalog/items", json={
                "seller_article": "SKU/1", "marketplace": "Wildberries", "status": "CORE"})
        self.assertEqual(response.status_code, 401)
        execute.assert_not_called()


def _book(statuses):
    book = openpyxl.Workbook()
    book.active.append(["Артикул", "Наименование", "У1", "У2", "У3", "Статус"])
    for i, st in enumerate(statuses, start=1):
        book.active.append([f"SKU/{i}", "Item", "C1", "C2", "C3", st])
    data = io.BytesIO()
    book.save(data)
    data.seek(0)
    return UploadFile(filename="fixture.xlsx", file=data)


class CatalogStatusListTests(unittest.TestCase):
    """Статус — только из списка: регистр приводится, прочее отклоняется (19.10.2026)."""
    ADMIN = {"id": 1, "is_admin": True}

    def test_norm_status(self):
        for raw, want in (("core", "CORE"), ("SALE", "Sale"), (" new ", "NEW"), ("closeout", "Closeout"),
                          ("?", "?"), ("-", "-"), ("", None), (None, None)):
            self.assertEqual(catalog._norm_status(raw), want)
        for bad in ("0", "выведен", "Активный"):
            with self.assertRaises(ValueError):
                catalog._norm_status(bad)

    def test_update_normalizes_case(self):
        with patch.object(db, "execute") as execute:
            catalog.update_item({"seller_article": "SKU/1", "marketplace": "wb", "status": "sale"}, user=self.ADMIN)
        upd = [c for c in execute.call_args_list if "UPDATE catalog_marketplace" in c.args[0]]
        self.assertEqual(upd[0].args[1][0], "Sale")

    def test_update_rejects_unknown_status_without_writes(self):
        with patch.object(db, "execute") as execute:
            with self.assertRaises(HTTPException) as cm:
                catalog.update_item({"seller_article": "SKU/1", "marketplace": "wb", "status": "0"}, user=self.ADMIN)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("«0»", cm.exception.detail)
        execute.assert_not_called()

    def test_import_normalizes_case(self):
        with patch.object(db, "execute_values") as ev:
            catalog.import_items(_book(["core", "SALE", "new", None]), marketplace="Wildberries", user=self.ADMIN)
        mp_rows = ev.call_args_list[1].args[1]
        self.assertEqual([r[2] for r in mp_rows], ["CORE", "Sale", "NEW", None])

    def test_import_rejects_file_with_unknown_status(self):
        with patch.object(db, "execute_values") as ev:
            with self.assertRaises(HTTPException) as cm:
                catalog.import_items(_book(["CORE", "0", "выведен"]), marketplace="Ozon", user=self.ADMIN)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("строка 3, SKU/2: «0»", cm.exception.detail)
        self.assertIn("строка 4, SKU/3: «выведен»", cm.exception.detail)
        ev.assert_not_called()
