"""Persisted date-exact Pi. Read-only RNP integration; transactional SQL refresh."""
import calendar
import datetime as dt
from pathlib import Path

from . import db

MIGRATION = "2026_09_21_daily_pi"
MSK = dt.timezone(dt.timedelta(hours=3))


def migrate():
    """One-time, versioned, atomic schema + backfill, safe across app workers."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(21092026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        # Prevent source writes during trigger installation and initial backfill.
        tx.execute("""LOCK TABLE mp_price_daily, catalog_base_prices_hist,
                      catalog_items IN SHARE ROW EXCLUSIVE MODE""")
        path = Path(__file__).resolve().parent.parent / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))


def revision():
    return db.query_one("SELECT revision FROM price_index_revision WHERE singleton")["revision"]


def month_end(year, month, days, date_to=None, today=None):
    """Past month: calendar end (or selected end); current: last displayed day."""
    today = today or dt.datetime.now(MSK).date()
    end = dt.date(year, month, calendar.monthrange(year, month)[1])
    if (year, month) >= (today.year, today.month):
        eligible = [d for d in days if d <= today]
        end = max(eligible) if eligible else min(end, today)
    if date_to:
        end = min(end, date_to)
    return end


def load(articles, start, end, marketplace):
    """One indexed batch read. Ratios are already calculated in the database."""
    if not articles:
        return {}
    rows = db.query_all(
        """SELECT seller_article, date, wb_base_pi, ozon_base_pi, buyer_pi
           FROM price_index_daily
           WHERE seller_article = ANY(%s) AND date BETWEEN %s AND %s""",
        (sorted(articles), start, end),
    )
    base = "wb_base_pi" if marketplace == "Wildberries" else "ozon_base_pi"
    return {
        (r["seller_article"], r["date"].isoformat()): {
            "base_price_pi": float(r[base]) if r[base] is not None else None,
            "buyer_price_pi": float(r["buyer_pi"]) if r["buyer_pi"] is not None else None,
        } for r in rows
    }


EMPTY = {"base_price_pi": None, "buyer_price_pi": None}
