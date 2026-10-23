"""Проверка прав на сервере: каждая роль × каждое действие. Только локальная wb_price_test.

Ожидание считается по правам ролей в БД (миграция из xlsx владельца): 403 — ровно когда
у роли нет нужной функции или площадки. Разрешённый запрос может упасть дальше
(в тестовой БД нет части таблиц) — важно только, что это не 403.
"""
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import abc_rules, auth, db, permissions, price_model
from app.main import app
from app.routers import prices_router, rnp_comments_router, users_router

WB, OZ, YA = "Wildberries", "Ozon", "Yandex"
ADMIN = "@admin"  # только роль «Администратор»
FILE = {"file": ("t.xlsx", b"not an excel file")}
DAY = {"stock_date": "2026-01-01", "report_date": "2026-01-01"}


def up(path, code, **form):
    """Загрузка файла: только функция, площадки роли не проверяются (решение 12.10.2026)."""
    return ("POST", path, {"files": FILE, "data": {**DAY, **form}}, [code], [])


def tab(section, mp):
    return permissions.tab(section, mp)


# (метод, путь, аргументы запроса, функции — все нужны; кортеж = любая из, площадки роли)
CASES = [
    ("GET", "/api/auth/me", {}, ["login"], []),
    ("GET", "/api/metrics/weeks", {}, ["login"], []),
    ("GET", "/api/catalog/tree?marketplace=wb", {}, ["login"], []),
    # РНП: площадки роли не ограничивают
    *[("GET", f"/api/rnp_sales/tree?marketplace={p}", {}, ["rnp.open", tab("rnp", mp)], [])
      for p, mp in (("wb", WB), ("ozon", OZ), ("yandex", YA))],
    ("GET", "/api/rnp/product_ue?marketplace=wb&article=SKU-1", {}, ["rnp.unit_popup"], []),
    ("GET", "/api/rnp-comments?marketplace=wb&date_from=2026-01-01&date_to=2026-01-02", {},
     ["rnp.comments.view"], []),
    ("POST", "/api/rnp-comments", {"json": {"marketplace": "wb", "seller_article": "SKU-1",
                                            "date": "2026-01-01", "body": "тест"}}, ["rnp.comments.add"], []),
    ("GET", "/api/rnp-promos?marketplace=wb&date_from=2026-01-01&date_to=2026-01-31", {},
     ["rnp.promos.view"], []),
    ("POST", "/api/rnp-promos", {"json": {"marketplace": "wb", "seller_article": "SKU-1", "name": "тест",
                                          "date_from": "2026-01-01", "date_to_plan": "2026-01-02"}},
     ["rnp.promos.add"], []),
    ("GET", "/api/rnp-promos/journal", {}, ["rnp.promos.journal"], []),
    ("GET", "/api/rnp_sales/anomaly_rules", {}, [("rnp.anomalies.view", "rnp.anomalies.rules")], []),
    ("PUT", "/api/rnp_sales/anomaly_rules", {"json": {"rules": []}}, ["rnp.anomalies.rules"], []),
    ("GET", "/api/rnp_sales/anomalies?marketplace=ozon", {}, ["rnp.anomalies.view"], []),
    up("/api/upload/ozon_daily", "rnp.upload.ozon_daily"),
    up("/api/upload/ozon_ads", "rnp.upload.ozon_ads"),
    up("/api/upload/ozon_reviews", "rnp.upload.ozon_reviews"),
    up("/api/upload/ozon_priceva", "rnp.upload.ozon_priceva"),
    up("/api/upload/wb_daily", "rnp.upload.wb_daily"),
    up("/api/upload/wb_reviews", "rnp.upload.wb_reviews"),
    up("/api/upload/wb_ads", "rnp.upload.wb_ads"),
    up("/api/upload/wb_priceva", "rnp.upload.wb_priceva"),
    up("/api/upload/wb_pi", "rnp.upload.wb_pi"),
    up("/api/upload/ya_daily", "rnp.upload.ya_daily"),
    up("/api/upload/ya_stock", "rnp.upload.ya_stock"),
    up("/api/upload/ozon_stock", "rnp.upload.stock_1c"),
    ("GET", "/api/upload/history?kind=rnps_wb", {}, ["rnp.uploads.log"], []),
    ("GET", "/api/upload/history?kind=unit_econ", {}, ["unit.uploads.log"], []),
    ("GET", "/api/upload/history?kind=cost", {}, ["stock.uploads.log"], []),
    ("GET", "/api/upload/history?kind=ozon_stock", {}, ["stock.uploads.log"], []),
    ("GET", "/api/upload/history?kind=all", {},
     ["rnp.uploads.log", "unit.uploads.log", "stock.uploads.log"], []),
    # Юнит-экономика
    *[("GET", f"/api/rnp/tree?marketplace={p}", {}, ["unit.open", tab("unit", mp)], [mp])
      for p, mp in (("wb", WB), ("ozon", OZ), ("yandex", YA))],
    ("GET", "/api/rnp/weeks?marketplace=ya", {}, ["unit.open", tab("unit", YA)], [YA]),
    ("GET", "/api/rnp/tree_cross", {}, ["unit.open", tab("unit", OZ), tab("unit", WB)], [OZ, WB]),
    ("GET", "/api/rnp/metrics", {}, ["unit.open"], []),
    *[("POST", "/api/upload/report", {"files": FILE, "data": {"marketplace": p}},
       ["unit.uploads.open", code], [])
      for p, code in (("ozon", "unit.upload.ozon"), ("wb", "unit.upload.wb"), ("yandex", "unit.upload.ya"))],
    # Старые эндпоинты юнит-экономики: Яндекс в них = Ozon (MP_MAP роутера)
    ("GET", "/api/metrics/summary?marketplace=yandex&year=2026&week=1", {},
     ["unit.open", tab("unit", OZ)], [OZ]),
    ("GET", "/api/metrics/mp_compare?year=2026&week=1", {},
     ["unit.open", tab("unit", OZ), tab("unit", WB)], [OZ, WB]),
    ("GET", "/api/trend/sku_matrix?marketplace=wb", {}, ["unit.open", tab("unit", WB)], [WB]),
    # Цены: площадки роли не ограничивают
    ("GET", "/api/prices/pricelist", {}, [("prices.list.view", "prices.index.view", "prices.model.view")], []),
    ("POST", "/api/prices/cell", {"json": {"seller_article": "SKU-1", "marketplace": "ozon",
                                           "base_price": None}}, ["prices.list.edit"], []),
    ("GET", "/api/prices/export", {}, ["prices.list.export"], []),
    ("GET", "/api/prices/export?show_cost=true", {}, ["prices.list.export", "prices.list.cost"], []),
    ("GET", "/api/prices/index_export", {}, ["prices.index.export"], []),
    ("GET", "/api/prices/model_params", {}, [("prices.model.view", "prices.index.view")], []),
    ("GET", "/api/prices/spp_history?seller_article=SKU-1&marketplace=wb&date_to=2026-10-20", {},
     [("prices.index.view", "prices.model.view")], []),
    *[("POST", "/api/prices/model_params", {"json": {"seller_article": "SKU-1", "marketplace": "wb",
                                                     "param": param, "value": None}}, [code], [])
      for param, code in (("category_price", "prices.model.edit"), ("drr_pct", "prices.model.drr"),
                          ("margin_pct", "prices.model.margin"))],
    ("PUT", "/api/prices/model_rules", {"json": {"rules": {}}}, ["prices.model.settings"], []),
    ("POST", "/api/prices/model_export", {"json": {}}, ["prices.model.export"], []),
    ("POST", "/api/prices/model_import", {"files": FILE}, ["prices.model.import"], []),
    ("GET", "/api/prices/index_export?show_cost=true", {},
     ["prices.index.export", "prices.list.cost"], []),
    ("POST", "/api/prices/import", {"files": FILE}, ["prices.list.import"], []),
    ("POST", "/api/prices/upload_ozon", {"files": FILE}, ["prices.upload.ozon"], []),
    ("POST", "/api/prices/upload_wb", {"files": FILE}, ["prices.upload.wb"], []),
    ("GET", "/api/prices/upload_log", {}, ["prices.uploads.log"], []),
    # Продажи, шт.
    *[("GET", f"/api/sales/plan_fact?marketplace={p}&year=2026", {},
       ["sales.open", tab("sales", mp)], [mp]) for p, mp in (("wb", WB), ("ozon", OZ), ("yandex", YA))],
    ("GET", "/api/sales/plan_fact?marketplace=cross&year=2026", {}, ["sales.open", "sales.tab.summary"], []),
    ("POST", "/api/sales/plan_cell", {"json": {"seller_article": "SKU-1", "marketplace": "wb",
                                               "year": 2026, "month": 1, "plan_qty": 1}},
     ["sales.open", tab("sales", WB), "sales.plan.edit"], [WB]),
    ("GET", "/api/sales/plan_export?marketplace=ozon&year=2026", {},
     ["sales.open", tab("sales", OZ), "sales.plan.export"], [OZ]),
    ("GET", "/api/sales/report_export?year=2026", {},
     ["sales.open", "sales.tab.summary", "sales.summary.export"], []),
    ("POST", "/api/sales/plan_import", {"files": FILE, "data": {"marketplace": "yandex", "year": "2026"}},
     ["sales.open", tab("sales", YA), "sales.plan.import"], [YA]),
    # ABC
    ("GET", "/api/abc/data", {}, ["abc.open"], []),
    ("PUT", "/api/abc/rules", {"json": {"rules": {"consider_mult": {"all": 3}}}}, ["abc.rules.edit"], []),
    # Склады
    up("/api/upload/wb_stock", "stock.upload.wb"),
    up("/api/upload/ozon_wh_stock", "stock.upload.ozon"),
    up("/api/upload/cost", "stock.upload.cost"),
    # Товары
    # Товары: площадки роли не ограничивают (как «Цены»)
    ("GET", "/api/catalog/items?marketplace=Yandex", {}, ["catalog.open", "catalog.table"], []),
    ("GET", "/api/catalog/export?marketplace=wb", {},
     ["catalog.open", "catalog.table", "catalog.export"], []),
    ("POST", "/api/catalog/import?marketplace=Ozon", {"files": FILE},
     ["catalog.open", "catalog.table", "catalog.import"], []),
    ("PUT", "/api/catalog/items", {"json": {"seller_article": "SKU-1", "marketplace": "Ozon",
                                            "status": "CORE"}},
     [("catalog.edit", "rnp.status.edit", "unit.status.edit", "abc.status.edit")], []),
    ("PUT", "/api/catalog/items", {"json": {"seller_article": "SKU-1", "marketplace": "Wildberries",
                                            "status": "CORE", "category_l1": "Группа"}},
     ["catalog.edit"], []),
    ("PUT", "/api/catalog/items", {"json": {"seller_article": "NEW-ACCESS-1", "marketplace": "Yandex"}},
     ["catalog.add"], []),
    # Пользователи
    ("GET", "/api/users", {}, ["users.open"], []),
    ("POST", "/api/users", {"json": {"display_name": "Тест", "email": "new@local.test",
                                     "password": "x", "role_id": 999999}}, ["users.add"], []),
    ("PATCH", "/api/users/999999", {"json": {"display_name": "Тест", "email": "x@local.test",
                                             "role_id": 999999}}, ["users.edit"], []),
    ("DELETE", "/api/users/999999", {}, ["users.delete"], []),
    # Роли: смотреть — с разделом «Пользователи», менять — только администратор
    ("GET", "/api/roles", {}, ["users.open"], []),
    ("POST", "/api/roles", {"json": {"copy_from": 999999, "name": "Копия"}}, [ADMIN], []),
    ("PUT", "/api/roles/999999", {"json": {"name": "X", "marketplaces": [], "permissions": []}}, [ADMIN], []),
    ("DELETE", "/api/roles/999999", {}, [ADMIN], []),
]
WH = [("rename", {"name": "x"}, "stock.wh.rename"), ("merge", {"canonical_id": 999998}, "stock.wh.merge"),
      ("unmerge", {}, "stock.wh.merge"), ("active", {"is_active": True}, "stock.wh.status"),
      ("incident/open", {"date": "2026-01-01"}, "stock.incidents.edit"),
      ("incident/close", {"date": "2026-01-01"}, "stock.incidents.edit"),
      ("district", {"federal_district": None}, "stock.wh.district")]
for prefix, mp in (("/api/warehouses", WB), ("/api/ozon_warehouses", OZ)):
    base = ["stock.open", tab("stock", mp)]
    CASES += [
        ("GET", f"{prefix}/by_wh", {}, base, [mp]),
        ("GET", f"{prefix}/catalog", {}, base, [mp]),
        ("GET", f"{prefix}/data_quality", {}, base + ["stock.quality"], [mp]),
        ("POST", f"{prefix}/incident/delete", {"json": {"incident_id": 999999}},
         base + ["stock.incidents.delete"], [mp]),
        *[("POST", f"{prefix}/{path}", {"json": {"id": 999999, **body}}, base + [code], [mp])
          for path, body, code in WH],
    ]
CASES.append(("POST", "/api/ozon_warehouses/cluster", {"json": {"id": 999999, "cluster": None}},
              ["stock.open", tab("stock", OZ), "stock.wh.cluster"], [OZ]))

OWNER = next(iter(auth.OWNER_EMAILS))


def allowed(role, codes, mps):
    if role["is_admin"]:
        return True
    if ADMIN in codes:
        return False
    perms = set(role["permissions"])
    has = all((set(c) & perms) if isinstance(c, tuple) else c in perms for c in codes)
    return has and set(mps) <= set(role["marketplaces"])


class AccessChecksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        price_model.migrate()
        abc_rules.migrate()
        cls.roles = db.query_all(
            """SELECT r.id, r.name, r.is_admin, r.marketplaces,
                      ARRAY(SELECT permission FROM app_role_permissions p WHERE p.role_id = r.id)
                        AS permissions
               FROM app_roles r ORDER BY r.id""")
        for r in cls.roles:
            db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) "
                       "VALUES (%s, %s, %s, 'user', %s)",
                       (100 + r["id"], f"role{r['id']}@local.test", r["name"], r["id"]))
        db.execute("INSERT INTO app_users (id, email, display_name, role) VALUES "
                   "(200, 'norole@local.test', 'Без роли', 'admin'), (201, %s, 'Владелец', 'user')",
                   (OWNER,))
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def call(self, uid, method, path, kwargs=None):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.request(method, path, headers={"Authorization": f"Bearer {token}"},
                                   **(kwargs or {}))

    def test_every_role_every_action(self):
        self.assertEqual(len(self.roles), 8)
        for role in self.roles:
            for method, path, kwargs, codes, mps in CASES:
                with self.subTest(role=role["name"], action=f"{method} {path}"):
                    r = self.call(100 + role["id"], method, path, kwargs)
                    if allowed(role, codes, mps):
                        self.assertNotEqual(r.status_code, 403, r.text)
                    else:
                        self.assertEqual(r.status_code, 403, r.text)
            db.execute("DELETE FROM catalog_items WHERE seller_article = 'NEW-ACCESS-1'")

    def test_user_without_role_cannot_enter(self):
        self.assertEqual(self.call(200, "GET", "/api/auth/me").status_code, 403)

    def test_owner_is_always_admin(self):
        r = self.call(201, "GET", "/api/auth/me")
        self.assertEqual(r.status_code, 200, r.text)
        me = r.json()
        self.assertTrue(me["is_admin"])
        self.assertEqual(set(me["permissions"]), permissions.CODES)
        self.assertEqual(me["marketplaces"], list(permissions.MARKETPLACES))

    def test_me_returns_role_rights(self):
        wb_role = next(r for r in self.roles if r["name"] == "Менеджер WB")
        me = self.call(100 + wb_role["id"], "GET", "/api/auth/me").json()
        self.assertEqual((me["role_name"], me["is_admin"], me["marketplaces"]),
                         ("Менеджер WB", False, [WB]))
        self.assertEqual(set(me["permissions"]), set(wb_role["permissions"]))

    def test_rights_change_without_relogin(self):
        role = next(r for r in self.roles if r["name"] == "Маркетинг")
        uid = 100 + role["id"]
        self.assertNotEqual(self.call(uid, "GET", "/api/abc/data").status_code, 403)
        db.execute("DELETE FROM app_role_permissions WHERE role_id=%s AND permission='abc.open'",
                   (role["id"],))
        try:
            self.assertEqual(self.call(uid, "GET", "/api/abc/data").status_code, 403)
        finally:
            db.execute("INSERT INTO app_role_permissions VALUES (%s, 'abc.open')", (role["id"],))

    def test_only_admin_assigns_admin_role(self):
        rop = next(r for r in self.roles if r["name"] == "РОП")
        r = self.call(100 + rop["id"], "POST", "/api/users",
                      {"json": {"display_name": "Тест", "email": "adm@local.test",
                                "password": "x", "role_id": 1}})
        self.assertEqual(r.status_code, 403, r.text)


def rows(user_row, role_row):
    """db.query_one: запрос к app_roles — роль, остальные — пользователь."""
    return lambda sql, params=None: role_row if "FROM app_roles" in sql else user_row


def fake(**kw):
    return auth._apply_access({"id": 1, "email": "u@local.test", **kw})


class AccessRulesTest(unittest.TestCase):
    """Правила внутри действий — без БД."""

    def test_comments_own_and_others(self):
        own, other = {"author_id": 1}, {"author_id": 2}
        user = fake(permissions=["rnp.comments.edit_own"])
        rnp_comments_router._assert_can_modify(own, user)
        with self.assertRaises(HTTPException) as e:
            rnp_comments_router._assert_can_modify(other, user)
        self.assertEqual(e.exception.status_code, 403)
        rnp_comments_router._assert_can_modify(other, fake(permissions=["rnp.comments.edit_any"]))
        with self.assertRaises(HTTPException):
            rnp_comments_router._assert_can_modify(own, fake(permissions=["rnp.comments.view"]))

    def test_pricelist_cost_only_with_right(self):
        row = {"seller_article": "SKU-1", "cost": 100, "cost_date": None}
        for perms, expected in ((["prices.list.view"], None),
                                (["prices.list.view", "prices.list.cost"], 100.0)):
            with self.subTest(perms=perms), \
                 patch.object(db, "query_all", return_value=[dict(row)]), \
                 patch.object(db, "query_one", return_value=None):
                data = prices_router.pricelist(date_from=None, date_to=None, user=fake(permissions=perms))
                self.assertEqual(data["items"][0]["cost"], expected)

    def test_owner_cannot_be_demoted_or_deleted(self):
        admin = fake(is_admin=True)
        target = {"id": 5, "email": OWNER, "role_id": 1}
        with patch.object(db, "query_one", side_effect=rows(target, {"id": 3, "is_admin": False})), \
             patch.object(db, "execute") as ex:
            with self.assertRaises(HTTPException) as e:
                users_router.delete_user(5, admin=admin)
            self.assertEqual(e.exception.status_code, 400)
            body = users_router.UserUpdate(display_name="В", email=OWNER, role_id=3)
            with self.assertRaises(HTTPException) as e:
                users_router.update_user(5, body, admin=admin)
            self.assertEqual(e.exception.status_code, 400)
            ex.assert_not_called()

    def test_only_admin_changes_role(self):
        rop = fake(permissions=["users.edit"])
        target = {"id": 5, "email": "w@local.test", "role_id": 3}
        body = users_router.UserUpdate(display_name="В", email="w@local.test", role_id=1)
        with patch.object(db, "query_one", side_effect=rows(target, {"id": 1, "is_admin": True})), \
             patch.object(db, "execute") as ex:
            with self.assertRaises(HTTPException) as e:
                users_router.update_user(5, body, admin=rop)
            self.assertEqual(e.exception.status_code, 403)
            ex.assert_not_called()

    def test_unknown_code_is_a_bug(self):
        with self.assertRaises(ValueError):
            auth.require("no.such.code")


if __name__ == "__main__":
    unittest.main()
