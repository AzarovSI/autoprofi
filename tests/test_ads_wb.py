"""Раздел «Реклама → Wildberries»: миграции, загрузчик статистики рекламы WB, дерево отчёта.
Только локальная wb_price_test."""
import datetime
import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from test_wb_prices import prepare_local_db
from test_access_roles import reset_roles
from app import auth, db, permissions, price_model, wb_ads_stats_loader as loader
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
FN_HEAD = ["Дата", "Артикул WB", "Артикул продавца", "Название товара", "Бренд", "Предмет", "Переходы в карточку",
           "Корзины", "Отложенные", "Заказы, шт", "Заказы, сумма", "Выкупы, шт", "Выкупы, сумма",
           "Отмены и возвраты, шт", "Отмены и возвраты, сумма", "Процент выкупа", "Конверсия в корзину, %",
           "Конверсия в заказ, %", "Средняя цена", "Валюта", "Обновлено"]
UPD = datetime.datetime(2026, 10, 28, 10, 29)


def camp(d, cid, place, nm, art, own, views, clicks, spend, orders, osum, carts=1, cancels=0):
    return [d, float(cid), f"РК {cid}", place, "Активна", "За показы", "Ручная", float(nm), art, "Товар",
            "Да" if own else "Нет, связанный заказ", views, clicks, round(clicks / views * 100, 2) if views else 0,
            0, spend, carts, orders, orders, osum, 0, cancels, "RUB", UPD]


def funnel(d, nm, art, visits, carts, orders, osum):
    return [d, float(nm), art, "Товар", "Бренд", "Предмет", visits, carts, 3, orders, osum, 0, 0, 1, 100,
            96, round(carts / visits * 100) if visits else 0, round(orders / carts * 100) if carts else 0, 1000,
            "RUB", UPD]


def book(camps, clusters=None, items=None, funnel_rows=None):
    return {loader.SHEET_CAMPAIGNS: [CAMP_HEAD] + camps,
            loader.SHEET_CLUSTERS: [CL_HEAD] + clusters if clusters is not None else None,
            loader.SHEET_FUNNEL: [FN_HEAD] + funnel_rows if funnel_rows is not None else None,
            loader.SHEET_ITEMS: [["Артикул WB", "Артикул продавца", "Название товара"]] + items if items else None}


def reset_ads():
    with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS wb_ads_campaign_daily, wb_ads_cluster_daily, wb_ads_funnel_daily, "
                     "wb_ads_items, report_uploads")
        if conn.execute("SELECT to_regclass('app_schema_migrations')").fetchone()[0]:
            conn.execute("DELETE FROM app_schema_migrations WHERE name = ANY(%s)",
                         (ads_router.MIGRATIONS + [permissions.ADS_MIGRATION],))


class AdsWbTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()
        reset_roles()
        reset_ads()
        permissions.migrate()
        permissions.migrate_ads()
        ads_router.migrate()
        ads_router.migrate()  # повторный запуск ничего не меняет
        price_model.migrate()
        with psycopg.connect(db.DB_DSN, autocommit=True) as conn:
            # В тестовой схеме у этих таблиц только часть колонок рабочей БД.
            conn.execute("""ALTER TABLE wb_daily_sales ADD COLUMN IF NOT EXISTS nm_id bigint,
                            ADD COLUMN IF NOT EXISTS avg_search_position numeric,
                            ADD COLUMN IF NOT EXISTS stock_wb_qty numeric""")
            conn.execute("DELETE FROM wb_daily_sales WHERE seller_article LIKE 'SKU-%'")
            conn.execute("ALTER TABLE catalog_marketplace ADD COLUMN IF NOT EXISTS manager text, "
                         "ADD COLUMN IF NOT EXISTS product_url text")
            conn.execute("""CREATE TABLE report_uploads (id bigserial PRIMARY KEY, marketplace text, year int,
                period_text text, period_start date, period_end date, source_file text, rows_loaded int,
                status text, message text, period_kind text)""")
            conn.execute("DELETE FROM price_model_hist WHERE seller_article LIKE 'SKU-%'")
        ids = {r["name"]: r["id"] for r in db.query_all("SELECT id, name FROM app_roles")}
        db.execute("INSERT INTO app_users (id, email, display_name, role, role_id) VALUES "
                   "(102, 'mkt@local.test', 'Маркетолог', 'user', %s), (103, 'rop@local.test', 'РОП', 'user', %s)",
                   (ids["Маркетинг"], ids["РОП"]))
        db.execute("UPDATE catalog_marketplace SET status='CORE', manager='Рябец И.'")
        self.client = TestClient(app, raise_server_exceptions=True)

    def tearDown(self):
        db.execute("DELETE FROM price_model_hist WHERE seller_article LIKE %s", ("SKU-%",))
        db.execute("DELETE FROM wb_daily_sales WHERE seller_article LIKE %s", ("SKU-%",))
        reset_ads()
        db.execute("DELETE FROM app_users WHERE id >= 100")
        reset_roles()

    def load(self, data):
        with psycopg.connect(db.DB_DSN) as conn, patch.object(loader, "_read_book", return_value=data):
            return loader.load_wb_ads_stats(conn, "fixture.xlsx")

    def get(self, path, uid=102, **params):
        token = auth.create_token({"id": uid, "email": ""})
        return self.client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})

    def tree(self, uid=102, **params):
        return self.get("/api/ads/wb/tree", uid, **params)

    def test_rights_only_for_marketing(self):
        rows = db.query_all("SELECT r.name, p.permission FROM app_role_permissions p JOIN app_roles r "
                            "ON r.id = p.role_id WHERE p.permission IN ('ads.upload.wb', 'ads.uploads.log')")
        self.assertEqual({(r["name"], r["permission"]) for r in rows},
                         {("Маркетинг", "ads.upload.wb"), ("Маркетинг", "ads.uploads.log")})
        self.assertEqual(self.tree(uid=103).status_code, 403)
        self.assertEqual(self.get("/api/ads/wb/linked", 103, campaign_id=1, date_from="2026-10-22",
                                  date_to="2026-10-22").status_code, 403)

    def test_loader_replaces_file_days_and_fills_article(self):
        d1, d2, d3 = D(2026, 10, 22), D(2026, 10, 23), D(2026, 10, 24)
        first = book([camp(d1, 1, "Поиск", 11, "SKU-1", True, 100, 5, 50.0, 1, 1000),
                      camp(d2, 1, "Поиск", 11, "SKU-1", True, 100, 5, 50.0, 1, 1000)],
                     clusters=[[d2, 1.0, "РК 1", "Поиск", "Активна", "За показы", 11.0, "SKU-1", "Товар", "домкрат",
                                80, 4, 5.0, 10, 500, 40.0, 1, 1, 1, 12.5, UPD]],
                     funnel_rows=[funnel(d1, 11, "SKU-1", 100, 10, 2, 2000), funnel(d2, 11, "SKU-1", 100, 10, 2, 2000)])
        self.assertTrue(self.load(first)["ok"])
        # Новая выгрузка: день 23.10 уточнён (+1 заказ), появился 24.10, артикул продавца пуст — из справочника.
        second = book([camp(d2, 1, "Поиск", 11, None, True, 100, 5, 50.0, 2, 2000),
                       camp(d3, 1, "Поиск", 11, None, True, 200, 10, 90.0, 0, 0),
                       camp(d3, 1, "Поиск", 12, None, False, 0, 0, 0.0, 1, 700)],
                      clusters=[], items=[[11.0, "SKU-1", "Товар 1"], [12.0, "sku-2", "Товар 2"]],
                      funnel_rows=[funnel(d2, 11, None, 120, 12, 3, 3000), funnel(d3, 11, None, 50, 5, 1, 1000)])
        res = self.load(second)
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["no_article"], res["funnel_rows"]), ([], 2))
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
        fn = db.query_all("SELECT date, seller_article, visits, deferred, cr_cart_pct, buyout_pct "
                          "FROM wb_ads_funnel_daily ORDER BY date")
        self.assertEqual([(r["date"], r["seller_article"], float(r["visits"])) for r in fn],
                         [(d1, "SKU-1", 100.0), (d2, "SKU-1", 120.0), (d3, "SKU-1", 50.0)])
        self.assertAlmostEqual(float(fn[1]["cr_cart_pct"]), .10)        # 10 % из файла — доля
        self.assertAlmostEqual(float(fn[1]["buyout_pct"]), .96)
        self.assertEqual(float(fn[1]["deferred"]), 3.0)
        self.assertEqual([r["status"] for r in db.query_all("SELECT status FROM report_uploads ORDER BY id")],
                         ["OK", "OK"])

    def test_loader_rejects_duplicates_and_missing_columns(self):
        d = D(2026, 10, 22)
        row = camp(d, 1, "Поиск", 11, "SKU-1", True, 100, 5, 50.0, 1, 1000)
        res = self.load(book([row, row]))
        self.assertFalse(res["ok"])
        self.assertIn("Дубли", res["error"])
        res = self.load({loader.SHEET_CAMPAIGNS: [CAMP_HEAD[:5], [d, 1, "x", "Поиск", "Активна"]],
                         loader.SHEET_CLUSTERS: None, loader.SHEET_FUNNEL: None, loader.SHEET_ITEMS: None})
        self.assertFalse(res["ok"])
        self.assertIn("Артикул WB", res["error"])
        res = self.load(book([row], funnel_rows=[]) | {loader.SHEET_FUNNEL: [FN_HEAD[:3], [d, 11, "SKU-1"]]})
        self.assertFalse(res["ok"])
        self.assertIn("Воронка", res["error"])
        self.assertEqual(db.query_one("SELECT count(*) AS n FROM wb_ads_campaign_daily")["n"], 0)

    def test_products_ordered_by_article_like_rnp(self):
        """Товары в группе — по артикулу, как в РНП, а не по расходу на рекламу (01.11.2026)."""
        d1 = D(2026, 10, 22)
        self.assertTrue(self.load(book([
            camp(d1, 1, "Поиск", 12, "SKU-2", True, 100, 10, 900.0, 2, 2000),
            camp(d1, 2, "Поиск", 11, "SKU-1", True, 100, 10, 10.0, 1, 1000),
        ], funnel_rows=[]))["ok"])
        r = self.tree(date_from="2026-10-01", date_to="2026-10-31")
        self.assertEqual(r.status_code, 200, r.text)
        group = r.json()["tree"]["children"][0]["children"][0]["children"][0]
        self.assertEqual([p["name"] for p in group["children"]], ["SKU-1", "SKU-2"])

    def test_default_period_three_months_like_rnp(self):
        """Период по умолчанию — месяц последней загрузки и два предыдущих, как в РНП (02.11.2026)."""
        self.assertTrue(self.load(book([
            camp(d, 1, "Поиск", 11, "SKU-1", True, 100, 10, 100.0, 1, 1000)
            for d in (D(2025, 11, 30), D(2025, 12, 1), D(2026, 1, 2))
        ], funnel_rows=[]))["ok"])
        data = self.tree().json()
        self.assertEqual((data["date_from"], data["date_to"]), ("2025-11-01", "2026-01-02"))
        self.assertEqual([m["key"] for m in data["months"]], ["2025-11", "2025-12", "2026-01"])

    def test_stock_days_like_rnp(self):
        """Запас в днях — как в РНП (node_turnover): общая опорная дата, окно 30 дней, остаток строго на опорную дату."""
        d1, d2 = D(2026, 10, 22), D(2026, 10, 23)
        self.assertTrue(self.load(book([
            camp(d2, i, "Поиск", 10 + i, f"SKU-{i}", True, 100, 10, 100.0, 1, 1000) for i in (1, 2, 3, 4)
        ], funnel_rows=[]))["ok"])
        db.execute("INSERT INTO wb_daily_sales (seller_article, date, orders_qty, stock_wb_qty) VALUES "
                   "('SKU-1', '2026-10-22', 10, 90), ('SKU-1', '2026-10-23', 10, 100), "  # 100 / (20 / 2 дня) = 10
                   "('SKU-2', '2026-10-23', 5, 0), "                                      # нет остатка → 0
                   "('SKU-3', '2026-10-22', 10, 100), "                                   # на опорную дату строки нет → нет
                   "('SKU-4', '2026-10-23', 0, 50)")                                      # остаток без заказов → ∞
        data = self.tree(date_from="2026-10-01", date_to="2026-10-31").json()
        prods = {}

        def walk(n):
            if n["kind"] == "product":
                prods[n["name"]] = n
            for ch in n.get("children", []):
                walk(ch)
        walk(data["tree"])
        self.assertEqual({a: p.get("stock_days") for a, p in prods.items()},
                         {"SKU-1": 10, "SKU-2": 0, "SKU-3": None, "SKU-4": "inf"})
        self.assertEqual(data["turnover_cfg"], {"red": 20, "blue": 60, "as_of": d2.isoformat()})
        # Период выбран вручную — запас на его конец, как в РНП: 22.10 — 90 / (10 / 1 день) = 9.
        self.load(book([camp(d1, 1, "Поиск", 11, "SKU-1", True, 100, 10, 100.0, 1, 1000)], funnel_rows=[]))
        early = self.tree(date_from="2026-10-01", date_to="2026-10-22").json()
        self.assertEqual(early["turnover_cfg"]["as_of"], d1.isoformat())
        walk(early["tree"])
        self.assertEqual(prods["SKU-1"]["stock_days"], 9)

    def test_search_position_from_rnp(self):
        """Средняя позиция в поиске — из РНП, только у товара, за дни отчёта; месяц — среднее по дням > 0 (02.11.2026)."""
        d1, d2, d3 = D(2026, 10, 22), D(2026, 10, 23), D(2026, 10, 24)
        self.assertTrue(self.load(book([
            camp(d, 1, "Поиск", 11, "SKU-1", True, 100, 10, 100.0, 1, 1000) for d in (d1, d2, d3)
        ], funnel_rows=[]))["ok"])
        # 22.10 — 10, 23.10 — 0 (не позиция), 24.10 — 20, 25.10 — 40, но рекламы за этот день нет.
        db.execute("INSERT INTO wb_daily_sales (seller_article, date, avg_search_position) VALUES "
                   "('SKU-1', '2026-10-22', 10), ('SKU-1', '2026-10-23', 0), ('SKU-1', '2026-10-24', 20), "
                   "('SKU-1', '2026-10-25', 40)")
        data = self.tree(date_from="2026-10-01", date_to="2026-10-31").json()
        l3 = data["tree"]["children"][0]["children"][0]["children"][0]
        prod = l3["children"][0]
        self.assertEqual(prod["pos"], {"2026-10-22": 10, "2026-10-24": 20, "2026-10": 15})
        self.assertNotIn("pos", l3)
        self.assertNotIn("pos", prod["children"][0])                            # у кампании позиции нет

    def test_tree_ad_funnel_shares_plan_and_linked(self):
        d1, d2 = D(2026, 10, 22), D(2026, 10, 23)
        self.assertTrue(self.load(book([
            camp(d1, 1, "Поиск", 11, "SKU-1", True, 100, 10, 100.0, 2, 2000, carts=4, cancels=1),
            camp(d1, 2, "Рекомендации", 11, "SKU-1", True, 300, 5, 50.0, 1, 1000, carts=2),
            camp(d2, 1, "Поиск", 11, "SKU-1", True, 100, 20, 200.0, 3, 3000, carts=6),
            camp(d2, 1, "Поиск", 12, "SKU-2", False, 0, 0, 0.0, 1, 500),
            camp(d2, 2, "Рекомендации", 13, "SKU-3", False, 0, 0, 0.0, 2, 900),
        ], funnel_rows=[funnel(d1, 11, "SKU-1", 60, 12, 6, 6000)]))["ok"])   # за 23.10 воронки нет
        db.execute("INSERT INTO price_model_hist (seller_article, marketplace, param, date, value) VALUES "
                   "('SKU-1', 'Wildberries', 'drr_pct', '2026-10-01', 0.08), "
                   "('SKU-1', 'Wildberries', 'drr_pct', '2026-10-23', 0.05)")
        r = self.tree(date_from="2026-10-01", date_to="2026-10-31")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        f = {name: i for i, name in enumerate(data["fields"])}
        prod = data["tree"]["children"][0]["children"][0]["children"][0]["children"][0]
        self.assertEqual(prod["name"], "SKU-1")
        m = prod["cells"]["2026-10"]
        self.assertEqual((m[f["views"]], m[f["clicks"]], m[f["carts"]], m[f["orders"]]), (500, 35, 12, 6))
        self.assertEqual((m[f["f_visits"]], m[f["f_carts"]], m[f["f_orders"]]), (60, 12, 6))
        self.assertEqual((m[f["orders_f"]], m[f["f_orders_a"]]), (3, 6))         # доля — только 22.10 (есть воронка)
        self.assertEqual((m[f["spend_f"]], m[f["f_rub_a"]]), (150, 6000))        # ДРР общий 2,5 %
        self.assertEqual(m[f["cancels"]], 1)
        self.assertEqual(m[f["linked"]], 3)                                      # 1 + 2 через обе кампании
        self.assertEqual(prod["plan"], {"2026-10-22": 0.08, "2026-10-23": 0.05, "2026-10": 0.05})
        self.assertIsNone(prod["cells"]["2026-10-23"][f["f_visits"]])            # нет воронки ≠ 0
        camps = {c["campaign_id"]: c for c in prod["children"]}
        self.assertEqual([c["placement"] for c in prod["children"]], ["Поиск", "Рекомендации"])
        self.assertNotIn("campaign_status", camps[1])
        c1 = camps[1]["cells"]["2026-10"]
        self.assertEqual((c1[f["orders_f"]], c1[f["f_orders_a"]], c1[f["linked"]]), (2, 6, 1))
        self.assertEqual(data["tree"]["cells"]["2026-10"][f["views"]], 500)    # «Итоги» — рекламные показы
        self.assertNotIn("SKU-2", [p["name"] for p in data["tree"]["children"][0]["children"][0]["children"][0]["children"]])
        # Список связанных заказов для окна: по кампании и по товару (обе кампании).
        lk = self.get("/api/ads/wb/linked", campaign_id=[1, 2], date_from="2026-10-23", date_to="2026-10-23").json()
        self.assertEqual([(x["seller_article"], x["orders"], x["orders_sum"]) for x in lk["items"]],
                         [("SKU-3", 2, 900), ("SKU-2", 1, 500)])
        one = self.get("/api/ads/wb/linked", campaign_id=1, date_from="2026-10-22", date_to="2026-10-31").json()
        self.assertEqual([x["seller_article"] for x in one["items"]], ["SKU-2"])
        self.assertNotIn("placements", data)                                   # фильтра места показа нет (02.11.2026)


if __name__ == "__main__":
    unittest.main()
