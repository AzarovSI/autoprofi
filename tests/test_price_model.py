"""«Цены → Ценовая модель»: справочник цен категории (price_category_hist). Только локальная wb_price_test."""
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


def reset_price_model():
    """Убрать таблицы, чтобы миграция выполнилась заново; старая таблица — как в рабочей БД до миграции."""
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS price_category_hist")
        conn.execute("""CREATE TABLE IF NOT EXISTS price_model_category (
            seller_article text NOT NULL, marketplace text NOT NULL, category_price integer NOT NULL,
            PRIMARY KEY (seller_article, marketplace))""")
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

    def set_(self, mp, price, art="sku-1"):
        return self.call(101, "POST", "/api/prices/model_category",
                         json={"seller_article": art, "marketplace": mp, "category_price": price})

    def items(self, date_to=None):
        path = "/api/prices/model_category" + (f"?date_to={date_to.isoformat()}" if date_to else "")
        r = self.call(101, "GET", path)
        self.assertEqual(r.status_code, 200, r.text)
        return [(i["seller_article"], i["marketplace"], i["category_price"], i["updated_by"])
                for i in r.json()["items"]]

    def hist(self):
        return [(r["marketplace"], r["date"], r["category_price"]) for r in db.query_all(
            "SELECT marketplace, date, category_price FROM price_category_hist ORDER BY marketplace, date")]

    def test_old_table_dropped(self):
        self.assertIsNone(db.query_one("SELECT to_regclass('price_model_category') AS t")["t"])

    def test_three_marketplaces_and_last_value_of_day(self):
        self.assertEqual(self.set_("wb", 970).status_code, 200)
        self.assertEqual(self.set_("ozon", 1000).status_code, 200)
        self.assertEqual(self.set_("ya", 1100).status_code, 200)
        # Повторная правка в тот же день перезаписывает запись дня, а не добавляет новую.
        r = self.set_("wb", 990)
        self.assertEqual((r.json()["category_price"], r.json()["date"]), (990, TODAY.isoformat()))
        self.assertEqual(self.hist(), [("Ozon", TODAY, 1000), ("Wildberries", TODAY, 990), ("Yandex", TODAY, 1100)])
        # Артикул канонизируется, площадки хранятся отдельно, видно автора.
        self.assertEqual(self.items(), [("SKU-1", "Ozon", 1000, "Админ"), ("SKU-1", "Wildberries", 990, "Админ"),
                                        ("SKU-1", "Yandex", 1100, "Админ")])

    def test_history_slice_on_period_end(self):
        db.execute("INSERT INTO price_category_hist (seller_article, marketplace, date, category_price) "
                   "VALUES ('SKU-1', 'Wildberries', %s, 900)", (YESTERDAY,))
        self.set_("wb", 950)
        self.assertEqual(self.items(YESTERDAY), [("SKU-1", "Wildberries", 900, None)])
        self.assertEqual(self.items(TODAY), [("SKU-1", "Wildberries", 950, "Админ")])
        self.assertEqual(self.items(YESTERDAY - datetime.timedelta(days=1)), [])
        # Удаление — запись дня с пустой ценой: сегодня цены нет, вчерашний срез не меняется.
        self.assertEqual(self.set_("wb", None).status_code, 200)
        self.assertEqual(self.items(TODAY), [])
        self.assertEqual(self.items(YESTERDAY), [("SKU-1", "Wildberries", 900, None)])
        self.assertEqual(self.hist(), [("Wildberries", YESTERDAY, 900), ("Wildberries", TODAY, None)])

    def test_unchanged_value_does_not_grow_history(self):
        db.execute("INSERT INTO price_category_hist (seller_article, marketplace, date, category_price) "
                   "VALUES ('SKU-1', 'Ozon', %s, 800)", (YESTERDAY,))
        self.set_("ozon", 800)
        self.assertEqual(self.hist(), [("Ozon", YESTERDAY, 800)])
        # Изменили и в тот же день вернули прежнее — запись дня убирается.
        self.set_("ozon", 850)
        self.set_("ozon", 800)
        self.assertEqual(self.hist(), [("Ozon", YESTERDAY, 800)])
        # Удаление несуществующей цены ничего не пишет.
        self.set_("ya", None)
        self.assertEqual(self.hist(), [("Ozon", YESTERDAY, 800)])

    def test_rejects_bad_values(self):
        for mp, price in (("wb", 0), ("wb", -5), ("ya", 0), ("foo", 100)):
            with self.subTest(mp=mp, price=price):
                r = self.set_(mp, price)
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
