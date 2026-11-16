# -*- coding: utf-8 -*-
"""Прайс-лист: состав товаров за период — движение в fact_weekly + распределённые новинки
справочника со статусом не «-» (решение владельца 16.11.2026). Только локальная wb_price_test."""
import unittest

from test_wb_prices import prepare_local_db
from app import db
from app.routers import prices_router as pr

ADMIN = {"id": 1, "role": "admin", "is_admin": True}


def add_card(art, mp="Wildberries", status="Активный", manager="Иванов",
             l1="Продукция ECOM", l2="Тестовая группа", l3="Товары"):
    db.execute("INSERT INTO catalog_items VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
               (art, art, l1, l2, l3))
    db.execute("INSERT INTO catalog_marketplace (seller_article, marketplace, status, manager) "
               "VALUES (%s,%s,%s,%s)", (art, mp, status, manager))


def arts(date_from="2026-09-01", date_to="2026-09-30"):
    return {i["seller_article"] for i in pr.pricelist(date_from, date_to, ADMIN)["items"]}


class PricelistUniverseTest(unittest.TestCase):
    def setUp(self):
        prepare_local_db()   # SKU-1, SKU-2 — с движением в сентябре

    def test_new_card_without_sales_is_shown(self):
        add_card("NEW-WB")
        add_card("NEW-OZ", mp="Ozon", status="?")
        self.assertEqual(arts(), {"SKU-1", "SKU-2", "NEW-WB", "NEW-OZ"})
        # Новинка видна в любом периоде, даже где у старых товаров движения нет.
        self.assertEqual(arts("2026-11-01", "2026-11-30"), {"NEW-WB", "NEW-OZ"})

    def test_minus_undistributed_and_yandex_are_hidden(self):
        add_card("MINUS", status=" - ")
        add_card("NO-MGR", manager=" ")
        add_card("NO-STATUS", status=None)
        add_card("NO-L3", l3="")
        add_card("YA-ONLY", mp="Yandex")
        self.assertEqual(arts(), {"SKU-1", "SKU-2"})

    def test_minus_on_one_mp_and_ready_on_other_is_shown(self):
        add_card("TWO-MP", mp="Ozon", status="-")
        add_card("TWO-MP", mp="Wildberries")
        self.assertIn("TWO-MP", arts())

    def test_sales_still_show_item_with_minus_status(self):
        db.execute("UPDATE catalog_marketplace SET status='-' WHERE seller_article='SKU-1'")
        self.assertIn("SKU-1", arts())


if __name__ == "__main__":
    unittest.main()
