"""ABC → «Ревизия матрицы» (abc_revision, 20.11.2026): решения и заметки, права только администратору,
статусы площадок не меняются, Excel-выгрузка. Только локальная wb_price_test."""
import io
import unittest

import psycopg
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import abc_revision, auth, db, permissions
from app.main import app


def reset_revision():
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS abc_revision")
        if conn.execute("SELECT to_regclass('app_schema_migrations')").fetchone()[0]:
            conn.execute("DELETE FROM app_schema_migrations WHERE name = %s", (abc_revision.MIGRATION,))


class AbcRevisionTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        reset_revision()
        abc_revision.migrate()
        abc_revision.migrate()  # повторный запуск ничего не меняет
        admin = db.query_one("SELECT id FROM app_roles WHERE is_admin")["id"]
        other = db.query_one("SELECT id FROM app_roles WHERE NOT is_admin ORDER BY id LIMIT 1")["id"]
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) "
                   "VALUES (101, 'admin@local.test', 'Админ', 'admin', %s), "
                   "(102, 'rop@local.test', 'Сотрудник', 'user', %s)", (admin, other))
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        db.execute("DROP TABLE IF EXISTS abc_revision")
        db.execute("DELETE FROM app_schema_migrations WHERE name = %s", (abc_revision.MIGRATION,))
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def call(self, method, path, uid=101, **kw):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)

    def put(self, body, uid=101):
        return self.call("PUT", "/api/abc/revision", uid=uid, json=body)

    def test_save_load_and_delete_when_empty(self):
        r = self.put({"seller_article": " ak-590l ", "decision": "Вывести"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"seller_article": "AK-590L", "item": {"decision": "Вывести", "note": ""}})
        # заметка не стирает решение, и наоборот
        self.put({"seller_article": "AK-590L", "note": "Убыток, заменён AK-590"})
        items = self.call("GET", "/api/abc/revision").json()["items"]
        self.assertEqual(items["AK-590L"]["decision"], "Вывести")
        self.assertEqual(items["AK-590L"]["note"], "Убыток, заменён AK-590")
        self.assertEqual(items["AK-590L"]["by"], "Админ")
        self.put({"seller_article": "AK-590L", "decision": ""})
        self.assertEqual(self.call("GET", "/api/abc/revision").json()["items"]["AK-590L"]["decision"], "")
        # пустые решение и заметка — строки нет
        r = self.put({"seller_article": "AK-590L", "note": "  "})
        self.assertIsNone(r.json()["item"])
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM abc_revision")["n"], 0)

    def test_validation(self):
        self.assertEqual(self.put({"seller_article": "X", "decision": "CORE"}).status_code, 400)
        self.assertEqual(self.put({"seller_article": "", "decision": "Усилить"}).status_code, 400)
        self.assertEqual(self.put({"seller_article": "X"}).status_code, 400)
        self.assertEqual(self.put({"seller_article": "X", "note": "я" * 501}).status_code, 400)

    def test_platform_statuses_untouched(self):
        db.execute("INSERT INTO catalog_marketplace (seller_article, marketplace, status) VALUES ('AK-590L', 'Ozon', 'CORE')")
        try:
            self.put({"seller_article": "AK-590L", "decision": "Вывести"})
            self.assertEqual(db.query_one("SELECT status FROM catalog_marketplace WHERE seller_article='AK-590L'")["status"], "CORE")
        finally:
            db.execute("DELETE FROM catalog_marketplace WHERE seller_article='AK-590L'")

    def test_admin_only(self):
        """Права №132–133 ролям не выданы: сотрудник без них получает 403 на всё."""
        for code in ("abc.tab.revision", "abc.revision.edit"):
            self.assertIsNone(db.query_one("SELECT 1 AS x FROM app_role_permissions WHERE permission=%s", (code,)))
        self.assertEqual(self.call("GET", "/api/abc/revision", uid=102).status_code, 403)
        self.assertEqual(self.put({"seller_article": "X", "decision": "Усилить"}, uid=102).status_code, 403)
        self.assertEqual(self.call("POST", "/api/abc/revision/export", uid=102, json={"rows": []}).status_code, 403)

    def test_export(self):
        rows = [
            {"level": "total", "name": "Итоги · товаров: 1", "q": 10, "rev": 1000, "share": 1, "prof": -50, "margin": -0.05,
             "q_m": 5, "rev_m": 500, "prof_m": -25},
            {"level": "0", "name": "Продукция ECOM", "q": 10, "rev": 1000, "share": 1, "prof": -50, "margin": -0.05},
            {"level": "item", "depth": 3, "name": "AK-590L", "title": "Компрессор", "q": 10, "rev": 1000, "prof": -50,
             "cr": "C", "cp": "D", "st_ozon": "CORE", "decision": "Вывести", "note": "=проверить цену"},
        ]
        r = self.call("POST", "/api/abc/revision/export", json={"period": "Ноябрь 2025 — Октябрь 2026", "rows": rows})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("filename*=UTF-8''", r.headers["content-disposition"])
        ws = load_workbook(io.BytesIO(r.content)).active
        self.assertIn("Ноябрь 2025 — Октябрь 2026", ws["A1"].value)
        self.assertEqual([c.value for c in ws[6]][:3], ["AK-590L", "Компрессор", 10])
        self.assertEqual(ws.cell(row=6, column=17).value, "Вывести")
        self.assertEqual(ws.row_dimensions[5].outlineLevel, 1)
        self.assertEqual(ws.row_dimensions[6].outlineLevel, 4)
        # товар «Не распределены по группам» (группа первого уровня) — на уровень ниже своей группы
        r2 = self.call("POST", "/api/abc/revision/export", json={"rows": [
            {"level": "0", "name": "Не распределены по группам"}, {"level": "item", "depth": 1, "name": "X-1"}]})
        ws2 = load_workbook(io.BytesIO(r2.content)).active
        self.assertEqual((ws2.row_dimensions[4].outlineLevel, ws2.row_dimensions[5].outlineLevel), (1, 2))
        note = ws.cell(row=6, column=18)
        self.assertEqual((note.value, note.data_type), ("=проверить цену", "s"))   # текст, не формула
        for bad in ("x", [], [1], [{"level": "x"}], [{"level": "item", "q": "10"}]):
            self.assertEqual(self.call("POST", "/api/abc/revision/export", json={"rows": bad}).status_code, 400, bad)


if __name__ == "__main__":
    unittest.main()
