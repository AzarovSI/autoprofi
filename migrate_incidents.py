# -*- coding: utf-8 -*-
"""Миграция: таблица wb_wh_incidents (историчность инцидентов) + перенос текущих
incident-статусов из wb_warehouses.status в интервалы.

Модель:
  wb_wh_incidents(id, wh_id, start_date, end_date null, created_at)
  • wh_id — каноничный склад (COALESCE(canonical_id, id)).
  • start_date — дата начала инцидента (включительно).
  • end_date — дата окончания (ВКЛючительно последний день инцидента);
    NULL = инцидент ещё действует (открыт).
  • Несколько строк на склад = историчность (разные периоды).
"""
import os
import psycopg

DSN = os.environ["DB_DSN"]

DDL = """
CREATE TABLE IF NOT EXISTS wb_wh_incidents (
    id          bigserial PRIMARY KEY,
    wh_id       bigint NOT NULL REFERENCES wb_warehouses(id) ON DELETE CASCADE,
    start_date  date NOT NULL,
    end_date    date,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wh_incidents_wh ON wb_wh_incidents(wh_id);
CREATE INDEX IF NOT EXISTS ix_wh_incidents_dates ON wb_wh_incidents(wh_id, start_date, end_date);
"""

with psycopg.connect(DSN) as conn:
    with conn.cursor() as cur:
        cur.execute(DDL)
        # Миграция текущих incident-статусов (только с датой) в открытые интервалы,
        # если для склада ещё нет ни одного интервала (идемпотентность).
        cur.execute("""
            SELECT id, COALESCE(canonical_id, id) AS wid, status, status_date
            FROM wb_warehouses
            WHERE status = 'incident' AND status_date IS NOT NULL
        """)
        rows = cur.fetchall()
        migrated = 0
        for (wid_raw, wid, status, status_date) in rows:
            # проверим, нет ли уже интервала на этот каноничный склад с этой датой
            cur.execute(
                "SELECT 1 FROM wb_wh_incidents WHERE wh_id=%s AND start_date=%s",
                (wid, status_date),
            )
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO wb_wh_incidents (wh_id, start_date, end_date) VALUES (%s, %s, NULL)",
                (wid, status_date),
            )
            migrated += 1
        conn.commit()
        # отчёт
        cur.execute("SELECT wh_id, start_date, end_date FROM wb_wh_incidents ORDER BY wh_id, start_date")
        allrows = cur.fetchall()
        print("MIGRATED_OPEN_INTERVALS=", migrated)
        print("TOTAL_INTERVALS=", len(allrows))
        for r in allrows:
            print("  wh_id=%s start=%s end=%s" % (r[0], r[1], r[2]))
print("DONE")
