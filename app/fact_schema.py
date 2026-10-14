# -*- coding: utf-8 -*-
"""Миграции таблиц фактов MPPROFIT (fact_weekly, fact_monthly).

Колонки заполняет загрузчик app/loader_core.py (METRIC_MAP).
ALTER существующих таблиц: в TimeWeb применять под владельцем таблиц
до выкладки (docs/operations.md), иначе сайт под autoprofi_app не стартует.
"""
from pathlib import Path

from . import db

MIGRATION = "2026_10_14_wb_payout_with_ads"


def migrate():
    """Колонки выплаты WB с рекламой: один раз, атомарно, безопасно для нескольких воркеров."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(14102026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        path = Path(__file__).resolve().parent.parent / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))
