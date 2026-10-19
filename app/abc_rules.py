# -*- coding: utf-8 -*-
"""ABC: настраиваемые пороги рекомендаций (таблица abc_rules, решение владельца 19.10.2026).

Рекомендации считаются на фронте (static/js/abc_module.js:makeRecommendation); отсюда —
хранение порогов и их проверка. Правила — docs/business_rules.md, раздел «ABC».
"""
from pathlib import Path

from . import db

MIGRATION = "2026_10_19_abc_rules"

# Параметр → (площадки, нижняя граница, верхняя граница). Рубли — в месяц.
PARAMS = {
    "tail_rev": (("ozon", "vb", "ya"), 0, None),
    "low_margin_profit": (("ozon", "vb", "ya"), 0, None),
    "remove_here_rev": (("ozon", "vb"), 0, None),
    "low_margin_pct": (("all",), 0, 1),       # доля 0..1
    "remove_here_mult": (("all",), 1, None),  # кратность прибыли на другом МП
    "consider_mult": (("all",), 1, None),
}

# Исходные значения — как в миграции (кнопка «Сбросить к исходным»).
DEFAULTS = {
    "tail_rev": {"ozon": 30000, "vb": 30000, "ya": 15000},
    "low_margin_profit": {"ozon": 30000, "vb": 30000, "ya": 15000},
    "remove_here_rev": {"ozon": 100000, "vb": 100000},
    "low_margin_pct": {"all": 0.05},
    "remove_here_mult": {"all": 5},
    "consider_mult": {"all": 3},
}


def migrate():
    """Таблица порогов: один раз, атомарно, безопасно для нескольких воркеров."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(19102026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        path = Path(__file__).resolve().parent.parent / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))


def load():
    """{param: {marketplace: value}}; чего нет в таблице — исходное значение."""
    rules = {p: dict(v) for p, v in DEFAULTS.items()}
    for r in db.query_all("SELECT param, marketplace, value FROM abc_rules"):
        if r["param"] in rules and r["marketplace"] in PARAMS[r["param"]][0]:
            rules[r["param"]][r["marketplace"]] = float(r["value"])
    return rules


def validate(rules):
    """Список (param, marketplace, value) из присланного словаря; ValueError с понятным текстом."""
    out = []
    for param, by_mp in (rules or {}).items():
        if param not in PARAMS:
            raise ValueError(f"Неизвестный параметр «{param}»")
        mps, lo, hi = PARAMS[param]
        for mp, value in (by_mp or {}).items():
            if mp not in mps:
                raise ValueError(f"Параметр «{param}» не задаётся для «{mp}»")
            try:
                v = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"«{param}» ({mp}): нужно число")
            if v != v or v < lo or (hi is not None and v > hi):
                raise ValueError(f"«{param}» ({mp}): значение вне диапазона")
            out.append((param, mp, v))
    return out


def save(rules, user_id):
    """Записать присланные значения одной транзакцией (только известные параметры)."""
    rows = validate(rules)
    with db.transaction() as tx:
        for param, mp, v in rows:
            tx.execute(
                """INSERT INTO abc_rules (param, marketplace, value, updated_at, updated_by_user_id)
                   VALUES (%s, %s, %s, now(), %s)
                   ON CONFLICT (param, marketplace) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = now(),
                       updated_by_user_id = EXCLUDED.updated_by_user_id""",
                (param, mp, v, user_id),
            )
    return load()
