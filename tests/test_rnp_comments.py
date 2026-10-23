"""Комментарии РНП — свои у каждой площадки (rnp_comments.marketplace). Только локальная wb_price_test."""
import unittest

import psycopg
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions
from app.main import app
from app.routers import rnp_comments_router

PERIOD = "date_from=2026-10-01&date_to=2026-10-31"


def reset_comments():
    """rnp_comments как в рабочей БД до миграции 23.10.2026: без площадки, с одним комментарием.
    Без внешнего ключа на app_users: reset_roles() пересоздаёт app_users."""
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS rnp_comments")
        if conn.execute("SELECT to_regclass('app_schema_migrations')").fetchone()[0]:
            conn.execute("DELETE FROM app_schema_migrations WHERE name=%s", (rnp_comments_router.MIGRATION,))
        conn.execute("""CREATE TABLE rnp_comments (
            id bigserial PRIMARY KEY, seller_article text NOT NULL, date date NOT NULL,
            body text NOT NULL, author_id bigint NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())""")
        conn.execute("CREATE INDEX idx_rnp_comments_date_art ON rnp_comments (date, seller_article)")
        conn.execute("INSERT INTO rnp_comments (seller_article, date, body, author_id) "
                     "VALUES ('SKU-1', '2026-10-21', 'старый', 101)")


class RnpCommentsTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        reset_comments()
        rnp_comments_router.migrate()
        rnp_comments_router.migrate()  # повторный запуск ничего не меняет
        admin = db.query_one("SELECT id FROM app_roles WHERE name='Администратор'")["id"]
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) "
                   "VALUES (101, 'admin@local.test', 'Админ', 'admin', %s)", (admin,))
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        db.execute("DROP TABLE IF EXISTS rnp_comments")
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def call(self, method, path, **kw):
        token = auth.create_token({"id": 101, "email": ""})
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)

    def add(self, mp, body):
        r = self.call("POST", "/api/rnp-comments",
                      json={"marketplace": mp, "seller_article": "SKU-1", "date": "2026-10-22", "body": body})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def bodies(self, mp):
        r = self.call("GET", f"/api/rnp-comments?marketplace={mp}&{PERIOD}")
        self.assertEqual(r.status_code, 200, r.text)
        return sorted(c["body"] for c in r.json()["comments"])

    def test_old_comments_go_to_wb(self):
        self.assertEqual([r["marketplace"] for r in db.query_all("SELECT marketplace FROM rnp_comments")],
                         ["Wildberries"])

    def test_comment_shown_only_in_its_marketplace(self):
        self.assertEqual(self.add("wb", "бюджет WB")["marketplace"], "Wildberries")
        self.add("ozon", "бюджет Ozon")
        self.assertEqual(self.bodies("wb"), ["бюджет WB", "старый"])
        self.assertEqual(self.bodies("ozon"), ["бюджет Ozon"])
        self.assertEqual(self.bodies("yandex"), [])

    def test_marketplace_required(self):
        r = self.call("POST", "/api/rnp-comments",
                      json={"seller_article": "SKU-1", "date": "2026-10-22", "body": "без площадки"})
        self.assertEqual(r.status_code, 422)
        r = self.call("POST", "/api/rnp-comments",
                      json={"marketplace": "avito", "seller_article": "SKU-1", "date": "2026-10-22", "body": "x"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.call("GET", f"/api/rnp-comments?{PERIOD}").status_code, 422)
        self.assertEqual(self.call("GET", f"/api/rnp-comments?marketplace=avito&{PERIOD}").status_code, 422)


if __name__ == "__main__":
    unittest.main()
