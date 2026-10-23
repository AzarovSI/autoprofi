"""Акции товаров в РНП (rnp_promos). Только локальная wb_price_test."""
import datetime
import unittest

import psycopg
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions
from app.main import app
from app.routers import rnp_promos_router

D = datetime.date


def reset_promos():
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS rnp_promo_events, rnp_promos")
        if conn.execute("SELECT to_regclass('app_schema_migrations')").fetchone()[0]:
            conn.execute("DELETE FROM app_schema_migrations WHERE name=%s", (rnp_promos_router.MIGRATION,))


class RnpPromosTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        reset_promos()
        rnp_promos_router.migrate()
        rnp_promos_router.migrate()  # повторный запуск ничего не меняет
        ids = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM app_roles")}
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) VALUES "
                   "(101, 'admin@local.test', 'Админ', 'admin', %s), (102, 'mkt@local.test', 'Маркетолог', 'user', %s), "
                   "(103, 'ast@local.test', 'Ассистент', 'user', %s), (104, 'wb@local.test', 'Менеджер WB', 'user', %s), "
                   "(105, 'wb2@local.test', 'Второй менеджер', 'user', %s)",
                   (ids["Администратор"], ids["Маркетинг"], ids["Ассистент"], ids["Менеджер WB"], ids["Менеджер WB"]))
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        reset_promos()
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def call(self, uid, method, path, **kw):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)

    def add(self, uid=104, mp="wb", sa="SKU-1", name="Осенний драйв", d_from="2026-10-10", plan="2026-10-20"):
        return self.call(uid, "POST", "/api/rnp-promos", json={
            "marketplace": mp, "seller_article": sa, "name": name, "date_from": d_from, "date_to_plan": plan})

    def listed(self, mp, uid=104):
        r = self.call(uid, "GET", f"/api/rnp-promos?marketplace={mp}&date_from=2026-10-01&date_to=2026-10-31")
        self.assertEqual(r.status_code, 200, r.text)
        return [(p["seller_article"], p["name"], p["date_from"], p["date_to"]) for p in r.json()["promos"]]

    def test_rights_follow_comments_and_journal_for_rop_and_marketing(self):
        rows = db.query_all("SELECT r.name, p.permission FROM app_role_permissions p "
                            "JOIN app_roles r ON r.id = p.role_id WHERE p.permission LIKE 'rnp.%%'")
        by_role = {}
        for r in rows:
            by_role.setdefault(r["name"], set()).add(r["permission"])
        for role, perms in by_role.items():
            with self.subTest(role=role):
                comments = {p.split(".")[-1] for p in perms if p.startswith("rnp.comments.")}
                promos = {p.split(".")[-1] for p in perms if p.startswith("rnp.promos.") and p != "rnp.promos.journal"}
                self.assertEqual(promos, comments)
                self.assertEqual("rnp.promos.journal" in perms, role in ("РОП", "Маркетинг"))

    def test_create_list_and_one_promo_at_a_time(self):
        r = self.add()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["marketplace"], r.json()["days"]), ("Wildberries", 11))
        self.assertEqual(self.listed("wb"), [("SKU-1", "Осенний драйв", "2026-10-10", "2026-10-20")])
        self.assertEqual(self.listed("ozon"), [])  # своя акция у каждой площадки
        # Пересечение — нельзя; на другой площадке и сразу после окончания — можно.
        r = self.add(name="Вторая", d_from="2026-10-20", plan="2026-10-25")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Осенний драйв", r.json()["detail"])
        self.assertEqual(self.add(mp="ozon", name="Ozon", d_from="2026-10-15", plan="2026-10-25").status_code, 200)
        self.assertEqual(self.add(name="Следом", d_from="2026-10-21", plan="2026-10-25").status_code, 200)
        self.assertEqual(self.add(sa="SKU-2", d_from="2026-10-20", plan="2026-10-19").status_code, 400)
        self.assertEqual(self.add(name="   ").status_code, 422)

    def test_close_edit_cancel_journal_and_history(self):
        pid = self.add().json()["id"]
        close = lambda uid, d: self.call(uid, "POST", f"/api/rnp-promos/{pid}/close", json={"date_to_fact": d})
        self.assertEqual(close(104, "2026-10-09").status_code, 400)  # раньше входа
        self.assertEqual(close(104, "2026-10-21").status_code, 400)  # позже плана — это продление
        self.assertEqual(close(105, "2026-10-15").status_code, 403)  # чужая акция без права «чужую»
        r = close(104, "2026-10-15")
        self.assertEqual((r.status_code, r.json()["status"], r.json()["date_to"], r.json()["days"]),
                         (200, "closed", "2026-10-15", 6))
        # После досрочного выхода место свободно: новая акция с 16.10.
        self.assertEqual(self.add(name="После", d_from="2026-10-16", plan="2026-10-18").status_code, 200)
        patch = lambda body: self.call(104, "PATCH", f"/api/rnp-promos/{pid}", json=body)
        self.assertEqual(patch({"date_to_plan": "2026-10-14"}).status_code, 400)  # не включает 15.10
        r = patch({"name": "Осенний драйв+"})
        self.assertEqual((r.status_code, r.json()["name"]), (200, "Осенний драйв+"))
        # Отменить: чужую — только с правом «чужую» (у администратора все права).
        self.assertEqual(self.call(105, "POST", f"/api/rnp-promos/{pid}/cancel").status_code, 403)
        r = self.call(101, "POST", f"/api/rnp-promos/{pid}/cancel")
        self.assertEqual((r.status_code, r.json()["status"], r.json()["cancelled_by_name"]), (200, "cancelled", "Админ"))
        self.assertEqual([p[1] for p in self.listed("wb")], ["После"])  # отменённой в РНП нет
        # Журнал: отменённая остаётся; права — РОП, Маркетинг, администратор.
        self.assertEqual(self.call(104, "GET", "/api/rnp-promos/journal").status_code, 403)
        r = self.call(102, "GET", "/api/rnp-promos/journal?marketplace=wb")
        self.assertEqual({p["name"]: p["status"] for p in r.json()["promos"]}["Осенний драйв+"], "cancelled")
        self.assertEqual(len(r.json()["promos"]), 2)
        ev = self.call(102, "GET", f"/api/rnp-promos/{pid}/events").json()["events"]
        self.assertEqual([e["action"] for e in ev], ["create", "close", "edit", "cancel"])
        self.assertEqual(ev[2]["changes"], {"name": ["Осенний драйв", "Осенний драйв+"]})
        self.assertEqual(ev[3]["user_name"], "Админ")

    def test_view_only_role_cannot_add(self):
        self.assertEqual(self.add(uid=103).status_code, 403)
        self.assertEqual(self.call(103, "GET", "/api/rnp-promos?marketplace=wb&date_from=2026-10-01"
                                             "&date_to=2026-10-31").status_code, 200)

    def test_status(self):
        r = {"cancelled_at": None, "date_to_fact": None, "date_from": D(2026, 10, 10), "date_to_plan": D(2026, 10, 20)}
        st = lambda today, **kw: rnp_promos_router._status({**r, **kw}, today)
        self.assertEqual(st(D(2026, 10, 9)), "planned")
        self.assertEqual(st(D(2026, 10, 10)), "active")
        self.assertEqual(st(D(2026, 10, 20)), "active")
        self.assertEqual(st(D(2026, 10, 21)), "finished")
        self.assertEqual(st(D(2026, 10, 12), date_to_fact=D(2026, 10, 12)), "closed")
        self.assertEqual(st(D(2026, 10, 12), cancelled_at=datetime.datetime.now()), "cancelled")


if __name__ == "__main__":
    unittest.main()
