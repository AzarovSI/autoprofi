# -*- coding: utf-8 -*-
"""«Цены → Ценовая модель»: вводимые параметры с историей (таблица price_model_hist) —
цена категории, плановый ДРР и плановая маржа.

История — одна запись на артикул + площадку + параметр + день (по Москве), правка в тот же
день перезаписывает значение дня. Загружаемая цена считается на фронте
(static/js/views.js, _pmCalcUpload). Правила — docs/business_rules.md.
"""
from pathlib import Path

from . import db

# По порядку: 2026_10_21_price_category удаляет лист 13.10 и создаёт price_category_hist,
# 2026_10_21_price_model_params переносит её строки в общую price_model_hist.
# DROP требует владельца таблиц: в TimeWeb применять под gen_user до выкладки.
MIGRATIONS = ["2026_10_21_price_category", "2026_10_21_price_model_params"]

# Параметр → (единицы, нижняя граница, верхняя граница). Проценты — доли.
PARAMS = {
    "category_price": ("rub", 1, None),
    "drr_pct": ("pct", 0, 1),
    "margin_pct": ("pct", -0.5, 1),
}


def migrate():
    """Миграции по порядку: каждая один раз, атомарно, безопасно для нескольких воркеров."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(13102026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        for name in MIGRATIONS:
            if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (name,)):
                continue
            path = Path(__file__).resolve().parent.parent / "migrations" / (name + ".sql")
            tx.conn.execute(path.read_text(encoding="utf-8"))
            tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (name,))
