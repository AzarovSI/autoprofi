"""«Цены → Ценовая модель»: вводимые параметры с историей (price_model_hist). Только локальная wb_price_test."""
import datetime
import unittest
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions, price_model
from app.main import app

TODAY = datetime.datetime.now(ZoneInfo("Europe/Moscow")).date()
YESTERDAY = TODAY - datetime.timedelta(days=1)


def reset_price_model(category_rows=()):
    """Состояние рабочей БД до миграций модели: лист 13.10 (price_model_category); category_rows —
    строки price_category_hist, если нужно проверить перенос (тогда первая миграция уже применена)."""
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        for t in ("price_model_hist", "price_category_hist", "price_model_category"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        if conn.execute("SELECT to_regclass('app_schema_migrations')").fetchone()[0]:
            conn.execute("DELETE FROM app_schema_migrations WHERE name = ANY(%s)", (price_model.MIGRATIONS,))
        conn.execute("""CREATE TABLE price_model_category (
            seller_article text NOT NULL, marketplace text NOT NULL, category_price integer NOT NULL,
            PRIMARY KEY (seller_article, marketplace))""")
        if category_rows:
            path = "migrations/2026_10_21_price_category.sql"
            conn.execute(open(path, encoding="utf-8").read())
            conn.execute("INSERT INTO app_schema_migrations(name) VALUES (%s) ON CONFLICT DO NOTHING",
                         (price_model.MIGRATIONS[0],))
            for row in category_rows:
                conn.execute("INSERT INTO price_category_hist (seller_article, marketplace, date, category_price) "
                             "VALUES (%s, %s, %s, %s)", row)


class PriceModelTest(unittest.TestCase):
    category_rows = ()

    def setUp(self):
        prepare_local_db()
        reset_roles()
        permissions.migrate()
        reset_price_model(self.category_rows)
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

    def set_(self, mp, value, param="category_price", art="sku-1"):
        return self.call(101, "POST", "/api/prices/model_params",
                         json={"seller_article": art, "marketplace": mp, "param": param, "value": value})

    def items(self, date_to=None):
        path = "/api/prices/model_params" + (f"?date_to={date_to.isoformat()}" if date_to else "")
        r = self.call(101, "GET", path)
        self.assertEqual(r.status_code, 200, r.text)
        return [(i["seller_article"], i["marketplace"], i["param"], i["value"], i["updated_by"])
                for i in r.json()["items"]]

    def hist(self):
        return [(r["marketplace"], r["param"], r["date"], None if r["value"] is None else float(r["value"]))
                for r in db.query_all("SELECT marketplace, param, date, value FROM price_model_hist "
                                      "ORDER BY marketplace, param, date")]


class PriceModelParamsTest(PriceModelTest):
    def test_old_tables_dropped(self):
        for t in ("price_model_category", "price_category_hist"):
            self.assertIsNone(db.query_one("SELECT to_regclass(%s) AS t", (t,))["t"])

    def test_three_params_and_last_value_of_day(self):
        self.assertEqual(self.set_("wb", 970).status_code, 200)
        self.assertEqual(self.set_("wb", 0.125, "drr_pct").status_code, 200)
        self.assertEqual(self.set_("ozon", -0.05, "margin_pct").status_code, 200)
        # Повторная правка в тот же день перезаписывает запись дня, а не добавляет новую.
        r = self.set_("wb", 990)
        self.assertEqual((r.json()["value"], r.json()["date"]), (990, TODAY.isoformat()))
        self.assertEqual(self.hist(), [("Ozon", "margin_pct", TODAY, -0.05),
                                       ("Wildberries", "category_price", TODAY, 990.0),
                                       ("Wildberries", "drr_pct", TODAY, 0.125)])
        # Артикул канонизируется, цена — целым числом, проценты — долями, видно автора.
        self.assertEqual(self.items(), [("SKU-1", "Ozon", "margin_pct", -0.05, "Админ"),
                                        ("SKU-1", "Wildberries", "category_price", 990, "Админ"),
                                        ("SKU-1", "Wildberries", "drr_pct", 0.125, "Админ")])
        self.assertIsInstance(self.items()[1][3], int)

    def test_history_slice_on_period_end(self):
        db.execute("INSERT INTO price_model_hist (seller_article, marketplace, param, date, value) "
                   "VALUES ('SKU-1', 'Wildberries', 'drr_pct', %s, 0.1)", (YESTERDAY,))
        self.set_("wb", 0.15, "drr_pct")
        self.assertEqual(self.items(YESTERDAY), [("SKU-1", "Wildberries", "drr_pct", 0.1, None)])
        self.assertEqual(self.items(TODAY), [("SKU-1", "Wildberries", "drr_pct", 0.15, "Админ")])
        self.assertEqual(self.items(YESTERDAY - datetime.timedelta(days=1)), [])
        # Удаление — запись дня с пустым значением: сегодня значения нет, вчерашний срез не меняется.
        self.assertEqual(self.set_("wb", None, "drr_pct").status_code, 200)
        self.assertEqual(self.items(TODAY), [])
        self.assertEqual(self.items(YESTERDAY), [("SKU-1", "Wildberries", "drr_pct", 0.1, None)])
        self.assertEqual(self.hist(), [("Wildberries", "drr_pct", YESTERDAY, 0.1),
                                       ("Wildberries", "drr_pct", TODAY, None)])

    def test_unchanged_value_does_not_grow_history(self):
        db.execute("INSERT INTO price_model_hist (seller_article, marketplace, param, date, value) "
                   "VALUES ('SKU-1', 'Ozon', 'margin_pct', %s, 0.2)", (YESTERDAY,))
        self.set_("ozon", 0.2, "margin_pct")
        self.assertEqual(self.hist(), [("Ozon", "margin_pct", YESTERDAY, 0.2)])
        # Изменили и в тот же день вернули прежнее — запись дня убирается.
        self.set_("ozon", 0.25, "margin_pct")
        self.set_("ozon", 0.2, "margin_pct")
        self.assertEqual(self.hist(), [("Ozon", "margin_pct", YESTERDAY, 0.2)])
        # Удаление несуществующего значения ничего не пишет.
        self.set_("ozon", None, "drr_pct")
        self.assertEqual(self.hist(), [("Ozon", "margin_pct", YESTERDAY, 0.2)])

    def test_rejects_bad_values(self):
        bad = (("wb", 0, "category_price"), ("wb", -5, "category_price"), ("wb", 10.5, "category_price"),
               ("foo", 100, "category_price"), ("wb", 100, "price"),
               ("wb", -0.001, "drr_pct"), ("wb", 1.001, "drr_pct"), ("wb", 0.1234, "drr_pct"),
               ("ozon", -0.501, "margin_pct"), ("ozon", 1.1, "margin_pct"))
        for mp, value, param in bad:
            with self.subTest(mp=mp, value=value, param=param):
                r = self.set_(mp, value, param)
                self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.items(), [])
        # Границы допустимы.
        for mp, value, param in (("wb", 0, "drr_pct"), ("wb", 1, "margin_pct"), ("ozon", -0.5, "margin_pct")):
            with self.subTest(mp=mp, value=value, param=param):
                self.assertEqual(self.set_(mp, value, param).status_code, 200)

    def test_only_with_rights(self):
        # У РОП нет функций «Ценовой модели» — ни смотреть, ни менять.
        self.assertEqual(self.call(102, "GET", "/api/prices/model_params").status_code, 403)
        r = self.call(102, "POST", "/api/prices/model_params",
                      json={"seller_article": "SKU-1", "marketplace": "wb", "param": "drr_pct", "value": 0.1})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.items(), [])


class PriceModelMoveTest(PriceModelTest):
    """Перенос цен категории, введённых после выкладки 21.10, из price_category_hist."""
    category_rows = (("SKU-1", "Wildberries", YESTERDAY, 900), ("SKU-1", "Wildberries", TODAY, None),
                     ("SKU-2", "Ozon", TODAY, 1500))

    def test_category_rows_moved(self):
        self.assertIsNone(db.query_one("SELECT to_regclass('price_category_hist') AS t")["t"])
        self.assertEqual(self.hist(), [("Ozon", "category_price", TODAY, 1500.0),
                                       ("Wildberries", "category_price", YESTERDAY, 900.0),
                                       ("Wildberries", "category_price", TODAY, None)])
        self.assertEqual(self.items(), [("SKU-2", "Ozon", "category_price", 1500, None)])


if __name__ == "__main__":
    unittest.main()
