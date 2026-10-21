# -*- coding: utf-8 -*-
"""«Цены → Ценовая модель»: справочник средних цен категории (таблица price_category_hist).

История — одна запись на артикул + площадку + день (по Москве), правка в тот же день
перезаписывает значение дня. Правила — docs/business_rules.md.
"""
from pathlib import Path

from . import db

MIGRATION = "2026_10_21_price_category"


def migrate():
    """Таблица цен категории: один раз, атомарно, безопасно для нескольких воркеров."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(13102026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        path = Path(__file__).resolve().parent.parent / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))
