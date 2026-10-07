"""Read-only DB connectivity and compatibility check; never runs migrations."""
import json
import os
from pathlib import Path
import sys

import psycopg

contract = json.loads((Path(__file__).parent / "schema_contract.json").read_text())
try:
    with psycopg.connect(os.environ["DB_DSN"], connect_timeout=15,
                         options="-c default_transaction_read_only=on -c statement_timeout=20000") as conn:
        current = set(conn.execute("""
            SELECT table_name,column_name FROM information_schema.columns
            WHERE table_schema='public'
        """).fetchall())
        missing = [(t, c) for t, columns in contract["tables"].items()
                   for c in columns if (t, c) not in current]
        if missing:
            print("Несовместимая/неполная БД. Отсутствуют колонки:", missing[:15])
            sys.exit(1)
        # A schema-only dump has the tables but no migration/revision metadata.
        # Catch it before the application's startup migration can attempt DDL.
        migration = conn.execute(
            "SELECT 1 FROM app_schema_migrations WHERE name=%s",
            ("2026_09_21_daily_pi",)).fetchone()
        revision = conn.execute(
            "SELECT 1 FROM price_index_revision WHERE singleton").fetchone()
        if not migration or not revision:
            raise SystemExit("БД восстановлена не полностью: отсутствуют служебные данные миграций/Pi.")
    print("DB CHECK OK: соединение и обязательные таблицы/колонки доступны.")
except psycopg.Error:
    # Do not leak connection details or secrets through a raw exception.
    raise SystemExit("Не удалось проверить БД. Проверьте DB_DSN, SSL, права и сетевой доступ.")
