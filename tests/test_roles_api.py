"""Вкладка «Роли» и роль пользователя из справочника. Только локальная wb_price_test."""
import unittest

from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions
from app.main import app

OWNER = next(iter(auth.OWNER_EMAILS))


class RolesApiTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        self.ids = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM app_roles")}
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) VALUES "
                   "(101, 'admin@local.test', 'Админ', 'admin', %s), (102, 'rop@local.test', 'РОП', 'user', %s), "
                   "(103, 'mkt@local.test', 'Маркетолог', 'user', %s), (104, %s, 'Владелец', 'admin', %s)",
                   (self.ids["Администратор"], self.ids["РОП"], self.ids["Маркетинг"], OWNER,
                    self.ids["Администратор"]))
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def call(self, uid, method, path, **kw):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)

    def role(self, data, name):
        return next(r for r in data["roles"] if r["name"] == name)

    # --- список и просмотр ---
    def test_list_for_rop_is_read_only(self):
        r = self.call(102, "GET", "/api/roles")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertFalse(data["can_edit"])
        self.assertEqual(len(data["functions"]), len(permissions.FUNCTIONS))
        self.assertEqual(len(data["roles"]), 8)
        self.assertEqual(len(self.role(data, "Администратор")["permissions"]), len(permissions.FUNCTIONS))
        # 92 по матрице минус «Аномалии» (№14–15, permissions.REMOVED) — их список не показывает.
        self.assertEqual(len(self.role(data, "РОП")["permissions"]), 90)
        self.assertEqual([u["email"] for u in self.role(data, "Маркетинг")["users"]], ["mkt@local.test"])
        self.assertTrue(self.call(101, "GET", "/api/roles").json()["can_edit"])

    def test_rop_cannot_change_roles(self):
        body = {"name": "РОП", "marketplaces": [], "permissions": []}
        self.assertEqual(self.call(102, "PUT", f"/api/roles/{self.ids['РОП']}", json=body).status_code, 403)
        self.assertEqual(self.call(102, "POST", "/api/roles",
                                   json={"copy_from": self.ids["РОП"], "name": "X"}).status_code, 403)
        self.assertEqual(self.call(102, "DELETE", f"/api/roles/{self.ids['Ассистент']}").status_code, 403)

    # --- копия, сохранение, удаление ---
    def test_copy_save_delete(self):
        r = self.call(101, "POST", "/api/roles", json={"copy_from": self.ids["Маркетинг"], "name": "Аналитик"})
        self.assertEqual(r.status_code, 200, r.text)
        new_id = r.json()["id"]
        data = self.call(101, "GET", "/api/roles").json()
        self.assertEqual(sorted(self.role(data, "Аналитик")["permissions"]),
                         sorted(self.role(data, "Маркетинг")["permissions"]))
        self.assertEqual(self.role(data, "Аналитик")["marketplaces"], self.role(data, "Маркетинг")["marketplaces"])

        body = {"name": "Аналитик WB", "marketplaces": ["Wildberries"],
                "permissions": ["login", "abc.open", "abc.tab.wb"]}
        r = self.call(101, "PUT", f"/api/roles/{new_id}", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        saved = self.role(self.call(101, "GET", "/api/roles").json(), "Аналитик WB")
        self.assertEqual((saved["marketplaces"], saved["permissions"]),
                         (["Wildberries"], ["abc.open", "abc.tab.wb", "login"]))
        self.assertEqual(self.call(101, "DELETE", f"/api/roles/{new_id}").status_code, 200)
        self.assertIsNone(db.query_one("SELECT 1 AS x FROM app_roles WHERE id=%s", (new_id,)))

    def test_copy_of_admin_has_all_rights(self):
        r = self.call(101, "POST", "/api/roles", json={"copy_from": self.ids["Администратор"], "name": "Почти админ"})
        saved = self.role(self.call(101, "GET", "/api/roles").json(), "Почти админ")
        self.assertFalse(saved["is_admin"])
        self.assertEqual(set(saved["permissions"]), permissions.CODES)
        self.assertEqual(saved["marketplaces"], list(permissions.MARKETPLACES))
        self.assertEqual(r.status_code, 200)

    def test_save_applies_to_employees_at_once(self):
        mkt = self.ids["Маркетинг"]
        self.assertIn("abc.open", self.call(103, "GET", "/api/auth/me").json()["permissions"])
        data = self.call(101, "GET", "/api/roles").json()
        perms = [p for p in self.role(data, "Маркетинг")["permissions"] if p != "abc.open"]
        r = self.call(101, "PUT", f"/api/roles/{mkt}",
                      json={"name": "Маркетинг", "marketplaces": ["Ozon"], "permissions": perms})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.call(103, "GET", "/api/abc/data").status_code, 403)
        me = self.call(103, "GET", "/api/auth/me").json()
        self.assertEqual(me["marketplaces"], ["Ozon"])

    def test_admin_role_is_fixed(self):
        adm = self.ids["Администратор"]
        r = self.call(101, "PUT", f"/api/roles/{adm}", json={"name": "A", "marketplaces": [], "permissions": []})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.call(101, "DELETE", f"/api/roles/{adm}").status_code, 400)

    def test_role_with_employees_cannot_be_deleted(self):
        r = self.call(101, "DELETE", f"/api/roles/{self.ids['Маркетинг']}")
        self.assertEqual(r.status_code, 409)
        self.assertIn("сотрудники", r.json()["detail"])

    def test_validation(self):
        rid = self.ids["Ассистент"]
        bad = [({"name": "Ассистент", "marketplaces": [], "permissions": ["no.such"]}, 400),
               ({"name": "Ассистент", "marketplaces": ["Avito"], "permissions": []}, 400),
               ({"name": "  ", "marketplaces": [], "permissions": []}, 400),
               ({"name": "рОп", "marketplaces": [], "permissions": []}, 409)]
        for body, code in bad:
            with self.subTest(body=body):
                self.assertEqual(self.call(101, "PUT", f"/api/roles/{rid}", json=body).status_code, code)

    # --- роль пользователя ---
    def test_users_get_role_from_directory(self):
        r = self.call(101, "POST", "/api/users", json={"display_name": "Новый", "email": "new@local.test",
                                                      "password": "x", "role_id": self.ids["Менеджер WB"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["role_name"], r.json()["role_is_admin"]), ("Менеджер WB", False))
        row = db.query_one("SELECT role, role_id FROM app_users WHERE email='new@local.test'")
        self.assertEqual((row["role"], row["role_id"]), ("user", self.ids["Менеджер WB"]))
        users = {u["email"]: u for u in self.call(102, "GET", "/api/users").json()}
        self.assertEqual(users["mkt@local.test"]["role_name"], "Маркетинг")

    def test_admin_changes_role_legacy_column_follows(self):
        r = self.call(101, "PATCH", "/api/users/103", json={"display_name": "Маркетолог", "email": "mkt@local.test",
                                                            "role_id": self.ids["Администратор"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(db.query_one("SELECT role FROM app_users WHERE id=103")["role"], "admin")

    def test_rop_adds_any_role_but_admin(self):
        ok = self.call(102, "POST", "/api/users", json={"display_name": "Н", "email": "n@local.test",
                                                       "password": "x", "role_id": self.ids["Ассистент"]})
        self.assertEqual(ok.status_code, 200, ok.text)
        no = self.call(102, "POST", "/api/users", json={"display_name": "Н", "email": "m@local.test",
                                                       "password": "x", "role_id": self.ids["Администратор"]})
        self.assertEqual(no.status_code, 403)

    def test_owner_stays_admin(self):
        r = self.call(101, "PATCH", "/api/users/104", json={"display_name": "Владелец", "email": OWNER,
                                                            "role_id": self.ids["РОП"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.call(101, "DELETE", "/api/users/104").status_code, 400)

    def test_unknown_role_rejected(self):
        r = self.call(101, "POST", "/api/users", json={"display_name": "Н", "email": "z@local.test",
                                                      "password": "x", "role_id": 999999})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
