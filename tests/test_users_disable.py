"""Отключение пользователей вместо удаления (28.10.2026). Только локальная wb_price_test."""
import unittest

import psycopg
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions
from app.main import app
from app.routers import users_router

OWNER = next(iter(auth.OWNER_EMAILS))


class UsersDisableTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        ids = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM app_roles")}
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id, password_hash) VALUES "
                   "(101, 'admin@local.test', 'Админ', 'admin', %s, NULL), "
                   "(102, 'mkt@local.test', 'Маркетолог', 'user', %s, %s), "
                   "(104, %s, 'Владелец', 'admin', %s, NULL)",
                   (ids["Администратор"], ids["Маркетинг"], auth.hash_password("pass"), OWNER, ids["Администратор"]))
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def call(self, uid, method, path, **kw):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)

    def login(self):
        return self.client.post("/api/auth/login", data={"username": "mkt@local.test", "password": "pass"})

    def test_migration_adds_column_once(self):
        with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
            conn.execute("ALTER TABLE app_users DROP COLUMN disabled_at")
            conn.execute("DELETE FROM app_schema_migrations WHERE name=%s", (users_router.MIGRATION,))
        users_router.migrate()
        users_router.migrate()  # повторный запуск ничего не меняет
        self.assertIsNone(db.query_one("SELECT disabled_at FROM app_users WHERE id=102")["disabled_at"])

    def test_disabled_user_cannot_log_in_or_use_token(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.call(102, "GET", "/api/auth/me").status_code, 200)
        r = self.call(101, "POST", "/api/users/102/disable")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(r.json()["disabled_at"])
        login = self.login()
        self.assertEqual(login.status_code, 403)
        self.assertEqual(login.json()["detail"], auth.DISABLED)
        self.assertEqual(self.call(102, "GET", "/api/auth/me").status_code, 401)
        # Запись пользователя остаётся — его комментарии и история не теряются.
        self.assertEqual(db.query_one("SELECT display_name FROM app_users WHERE id=102")["display_name"],
                         "Маркетолог")
        users = {u["id"]: u for u in self.call(101, "GET", "/api/users").json()}
        self.assertIsNotNone(users[102]["disabled_at"])
        self.assertIsNone(users[101]["disabled_at"])

    def test_enable_restores_access(self):
        self.call(101, "POST", "/api/users/102/disable")
        r = self.call(101, "POST", "/api/users/102/enable")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["disabled_at"])
        self.assertEqual(self.login().status_code, 200)

    def test_cannot_disable_self_or_owner(self):
        self.assertEqual(self.call(101, "POST", "/api/users/101/disable").status_code, 400)
        self.assertEqual(self.call(101, "POST", "/api/users/104/disable").status_code, 400)
        self.assertEqual(self.call(101, "POST", "/api/users/999999/disable").status_code, 404)

    def test_users_are_not_deleted(self):
        self.assertEqual(self.call(101, "DELETE", "/api/users/102").status_code, 405)
        self.assertIsNotNone(db.query_one("SELECT id FROM app_users WHERE id=102"))


if __name__ == "__main__":
    unittest.main()
