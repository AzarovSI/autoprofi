"""«Цены → Ценовая модель»: цена категории (price_model_category). Только локальная wb_price_test."""
import unittest

import psycopg
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions, price_model
from app.main import app


def reset_price_model():
    """Убрать таблицу, чтобы миграция выполнилась заново."""
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS price_model_category")
        if conn.execute("SELECT to_regclass('app_schema_migrations')").fetchone()[0]:
            conn.execute("DELETE FROM app_schema_migrations WHERE name=%s", (price_model.MIGRATION,))


class PriceModelTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        reset_price_model()
        price_model.migrate()
        price_model.migrate()  # повторный запуск ничего не меняет
        ids = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM app_roles")}
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) VALUES "
                   "(101, 'admin@local.test', 'Админ', 'admin', %s), (102, 'rop@local.test', 'РОП', 'user', %s)",
                   (ids["Администратор"], ids["РОП"]))
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def call(self, uid, method, path, **kw):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)

    def items(self):
        r = self.call(101, "GET", "/api/prices/model_category")
        self.assertEqual(r.status_code, 200, r.text)
        return [(i["seller_article"], i["marketplace"], i["category_price"], i["updated_by"])
                for i in r.json()["items"]]

    def test_set_update_delete(self):
        set_ = lambda mp, price, art="sku-1": self.call(
            101, "POST", "/api/prices/model_category",
            json={"seller_article": art, "marketplace": mp, "category_price": price})
        self.assertEqual(set_("wb", 970).status_code, 200)
        self.assertEqual(set_("ozon", 1000).status_code, 200)
        self.assertEqual(set_("wb", 990).json()["category_price"], 990)
        # Артикул канонизируется, площадки хранятся отдельно, видно автора.
        self.assertEqual(self.items(), [("SKU-1", "Ozon", 1000, "Админ"), ("SKU-1", "Wildberries", 990, "Админ")])
        self.assertEqual(set_("wb", None).status_code, 200)
        self.assertEqual(self.items(), [("SKU-1", "Ozon", 1000, "Админ")])

    def test_rejects_bad_values(self):
        for mp, price in (("wb", 0), ("wb", -5), ("ya", 100), ("foo", 100)):
            with self.subTest(mp=mp, price=price):
                r = self.call(101, "POST", "/api/prices/model_category",
                              json={"seller_article": "SKU-1", "marketplace": mp, "category_price": price})
                self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.items(), [])

    def test_only_with_rights(self):
        # У РОП нет функций «Ценовой модели» — ни смотреть, ни менять.
        self.assertEqual(self.call(102, "GET", "/api/prices/model_category").status_code, 403)
        r = self.call(102, "POST", "/api/prices/model_category",
                      json={"seller_article": "SKU-1", "marketplace": "wb", "category_price": 100})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.items(), [])


if __name__ == "__main__":
    unittest.main()
