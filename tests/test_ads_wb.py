"""Раздел «Реклама → Wildberries»: миграция, загрузчик статистики рекламы WB, дерево отчёта.
Только локальная wb_price_test."""
import datetime
import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions, wb_ads_stats_loader as loader
from app.main import app
from app.routers import ads_router

D = datetime.date
CAMP_HEAD = ["Дата", "ID кампании", "Название кампании", "Место показа", "Статус", "Тип оплаты", "Тип ставки",
             "Артикул WB", "Артикул продавца", "Название товара", "Товар в кампании", "Показы", "Клики", "CTR, %",
             "CPC", "Затраты", "Корзины", "Заказы", "Заказано, шт", "Сумма заказов", "CR, %", "Отмены, шт",
             "Валюта", "Обновлено"]
CL_HEAD = ["Дата", "ID кампании", "Название кампании", "Место показа", "Статус", "Тип оплаты", "Артикул WB",
           "Артикул продавца", "Название товара", "Поисковый кластер", "Показы", "Клики", "CTR, %", "CPC", "CPM",
           "Затраты", "Корзины", "Заказы", "Заказано, шт", "Средняя позиция", "Обновлено"]
UPD = datetime.datetime(2026, 10, 28, 10, 29)


def camp(d, cid, place, nm, art, own, views, clicks, spend, orders, osum, status="Активна"):
    return [d, float(cid), f"РК {cid}", place, status, "За показы", "Ручная", float(nm), art, "Товар",
            "Да" if own else "Нет, связанный заказ", views, clicks, round(clicks / views * 100, 2) if views else 0,
            0, spend, 1, orders, orders, osum, 0, 0, "RUB", UPD]


def book(camps, clusters=None, items=None):
    return {loader.SHEET_CAMPAIGNS: [CAMP_HEAD] + camps,
            loader.SHEET_CLUSTERS: [CL_HEAD] + clusters if clusters is not None else None,
            loader.SHEET_ITEMS: [["Артикул WB", "Артикул продавца", "Название товара"]] + items if items else None}


def reset_ads():
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS wb_ads_campaign_daily, wb_ads_cluster_daily, wb_ads_items, report_uploads")
        if conn.execute("SELECT to_regclass('app_schema_migrations')").fetchone()[0]:
            conn.execute("DELETE FROM app_schema_migrations WHERE name IN (%s, %s)",
                         (ads_router.MIGRATION, permissions.ADS_MIGRATION))


class AdsWbTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        reset_ads()
        permissions.migrate()
        permissions.migrate_ads()
        ads_router.migrate()
        ads_router.migrate()  # повторный запуск ничего не меняет
        with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
            # В тестовой схеме у этих таблиц только часть колонок рабочей БД.
            conn.execute("""ALTER TABLE wb_daily_sales ADD COLUMN IF NOT EXISTS nm_id bigint,
                ADD COLUMN IF NOT EXISTS card_visits numeric, ADD COLUMN IF NOT EXISTS orders_qty numeric,
                ADD COLUMN IF NOT EXISTS orders_rub numeric""")
            conn.execute("ALTER TABLE catalog_marketplace ADD COLUMN IF NOT EXISTS manager text, "
                         "ADD COLUMN IF NOT EXISTS product_url text")
            conn.execute("""CREATE TABLE report_uploads (id bigserial PRIMARY KEY, marketplace text, year int,
                period_text text, period_start date, period_end date, source_file text, rows_loaded int,
                status text, message text, period_kind text)""")
        ids = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM app_roles")}
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) VALUES "
                   "(102, 'mkt@local.test', 'Маркетолог', 'user', %s), (103, 'rop@local.test', 'РОП', 'user', %s)",
                   (ids["Маркетинг"], ids["РОП"]))
        db.execute("UPDATE catalog_marketplace SET status='CORE', manager='Рябец И.'")
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        reset_ads()
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def load(self, data):
        with psycopg.connect(db.DB_DSN) as conn, patch.object(loader, "_read_book", return_value=data):
            return loader.load_wb_ads_stats(conn, "fixture.xlsx")

    def tree(self, uid=102, **params):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.get("/api/ads/wb/tree", params=params, headers={"Authorization": f"Bearer {token}"})

    def test_rights_only_for_marketing(self):
        rows = db.query_all("SELECT r.name, p.permission FROM app_role_permissions p JOIN app_roles r "
                            "ON r.id = p.role_id WHERE p.permission IN ('ads.upload.wb', 'ads.uploads.log')")
        self.assertEqual({(r["name"], r["permission"]) for r in rows},
                         {("Маркетинг", "ads.upload.wb"), ("Маркетинг", "ads.uploads.log")})
        self.assertEqual(self.tree(uid=103).status_code, 403)

    def test_loader_replaces_file_days_and_fills_article(self):
        d1, d2, d3 = D(2026, 10, 22), D(2026, 10, 23), D(2026, 10, 24)
        first = book([camp(d1, 1, "Поиск", 11, "SKU-1", True, 100, 5, 50.0, 1, 1000),
                      camp(d2, 1, "Поиск", 11, "SKU-1", True, 100, 5, 50.0, 1, 1000)],
                     clusters=[[d2, 1.0, "РК 1", "Поиск", "Активна", "За показы", 11.0, "SKU-1", "Товар", "домкрат",
                                80, 4, 5.0, 10, 500, 40.0, 1, 1, 1, 12.5, UPD]])
        self.assertTrue(self.load(first)["ok"])
        # Новая выгрузка: день 23.10 уточнён (+1 заказ), появился 24.10, артикул продавца пуст — из справочника.
        second = book([camp(d2, 1, "Поиск", 11, None, True, 100, 5, 50.0, 2, 2000),
                       camp(d3, 1, "Поиск", 11, None, True, 200, 10, 90.0, 0, 0),
                       camp(d3, 1, "Поиск", 12, None, False, 0, 0, 0.0, 1, 700)],
                      clusters=[], items=[[11.0, "SKU-1", "Товар 1"], [12.0, "sku-2", "Товар 2"]])
        res = self.load(second)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["no_article"], [])
        rows = db.query_all("SELECT date, nm_id, seller_article, in_campaign, orders, ctr_pct "
                            "FROM wb_ads_campaign_daily ORDER BY date, nm_id")
        self.assertEqual([(r["date"], r["nm_id"], r["seller_article"], r["in_campaign"], float(r["orders"]))
                          for r in rows],
                         [(d1, 11, "SKU-1", True, 1.0), (d2, 11, "SKU-1", True, 2.0),
                          (d3, 11, "SKU-1", True, 0.0), (d3, 12, "SKU-2", False, 1.0)])
        self.assertAlmostEqual(float(rows[0]["ctr_pct"]), .05)          # 5 % из файла — доля
        # Кластеры за 23.10 остались: в новом файле лист кластеров пустой — его дни не трогаются.
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM wb_ads_cluster_daily")["n"], 1)
        self.assertEqual(db.query_one("SELECT seller_article FROM wb_ads_items WHERE nm_id=12")["seller_article"], "SKU-2")
        self.assertEqual([r["status"] for r in db.query_all("SELECT status FROM report_uploads ORDER BY id")],
                         ["OK", "OK"])

    def test_loader_rejects_duplicates_and_missing_columns(self):
        d = D(2026, 10, 22)
        row = camp(d, 1, "Поиск", 11, "SKU-1", True, 100, 5, 50.0, 1, 1000)
        res = self.load(book([row, row]))
        self.assertFalse(res["ok"])
        self.assertIn("Дубли", res["error"])
        res = self.load({loader.SHEET_CAMPAIGNS: [CAMP_HEAD[:5], [d, 1, "x", "Поиск", "Активна"]],
                         loader.SHEET_CLUSTERS: None, loader.SHEET_ITEMS: None})
        self.assertFalse(res["ok"])
        self.assertIn("Артикул WB", res["error"])
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM wb_ads_campaign_daily")["n"], 0)

    def test_tree_sums_shares_and_linked_orders(self):
        d0, d1, d2 = D(2026, 10, 21), D(2026, 10, 22), D(2026, 10, 23)
        self.assertTrue(self.load(book([
            camp(d1, 1, "Поиск", 11, "SKU-1", True, 100, 10, 100.0, 2, 2000),
            camp(d1, 2, "Рекомендации", 11, "SKU-1", True, 300, 5, 50.0, 1, 1000, status="На паузе"),
            camp(d2, 1, "Поиск", 11, "SKU-1", True, 100, 20, 200.0, 3, 3000),
            camp(d2, 1, "Поиск", 12, "SKU-2", False, 0, 0, 0.0, 1, 500),
        ]))["ok"])
        # РНП: 21.10 — до первой загрузки рекламы (в знаменатели долей не входит), 23.10 — нет воронки.
        db.execute("INSERT INTO wb_daily_sales (seller_article, date, card_visits, orders_qty, orders_rub) VALUES "
                   "('SKU-1', %s, 1000, 10, 10000), ('SKU-1', %s, 60, 6, 6000), ('SKU-2', %s, 40, 4, 4000)",
                   (d0, d1, d1))
        r = self.tree(date_from="2026-10-01", date_to="2026-10-31")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        f = {name: i for i, name in enumerate(data["fields"])}
        prod = data["tree"]["children"][0]["children"][0]["children"][0]["children"][0]
        self.assertEqual((prod["name"], prod["campaigns"]), ("SKU-1", 2))
        m = prod["cells"]["2026-10"]
        self.assertEqual(m[f["visits"]], 1060)            # переходы РНП за все дни
        self.assertEqual(m[f["visits_a"]], 60)            # только дни с загруженной рекламой
        self.assertEqual(m[f["clicks"]], 35)              # обе кампании, оба дня
        self.assertEqual(m[f["clicks_v"]], 15)            # клики в дни с переходами (22.10)
        self.assertEqual(m[f["spend_r"]], 150)
        self.assertEqual(m[f["ad_orders"]], 6)
        self.assertIsNone(prod["cells"]["2026-10-21"][f["clicks"]])   # нет рекламы ≠ 0
        camps = {c["campaign_id"]: c for c in prod["children"]}
        self.assertEqual([c["placement"] for c in prod["children"]], ["Поиск", "Рекомендации"])
        self.assertEqual(camps[2]["campaign_status"], "На паузе")
        self.assertEqual(camps[1]["cells"]["2026-10"][f["linked"]], 1)  # заказ SKU-2 через РК 1
        # Без рекламы SKU-2 виден только во «Все товары», там у него связанный заказ.
        self.assertNotIn("SKU-2", r.text.split('"tree"')[1])
        allp = self.tree(date_from="2026-10-01", date_to="2026-10-31", scope="all").json()
        group = allp["tree"]["children"][0]["children"][0]["children"][0]
        sku2 = next(p for p in group["children"] if p["name"] == "SKU-2")
        self.assertEqual((sku2["campaigns"], sku2["cells"]["2026-10"][f["linked"]]), (0, 1))
        self.assertEqual(allp["tree"]["cells"]["2026-10"][f["visits"]], 1100)
        # Фильтр места показа — только кампании «Рекомендации».
        rec = self.tree(date_from="2026-10-01", date_to="2026-10-31", placement="Рекомендации").json()
        rprod = rec["tree"]["children"][0]["children"][0]["children"][0]["children"][0]
        self.assertEqual([c["campaign_id"] for c in rprod["children"]], [2])
        self.assertEqual(rprod["cells"]["2026-10"][f["spend"]], 50)


if __name__ == "__main__":
    unittest.main()
