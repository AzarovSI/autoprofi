# -*- coding: utf-8 -*-
"""«Цены → Ценовая модель»: вводимые параметры с историей (таблица price_model_hist) —
цена категории, плановый ДРР и плановая маржа; пороги сигнала корректировки (price_model_rules).

История — одна запись на артикул + площадку + параметр + день (по Москве), правка в тот же
день перезаписывает значение дня. Загружаемая цена считается на фронте
(static/js/views.js, _pmCalcUpload). Правила — docs/business_rules.md.
"""
import math
from pathlib import Path

from . import db

# По порядку: 2026_10_21_price_category удаляет лист 13.10 и создаёт price_category_hist,
# 2026_10_21_price_model_params переносит её строки в общую price_model_hist,
# 2026_10_21_price_model_rules — пороги сигнала (⚙).
# DROP требует владельца таблиц: в TimeWeb применять под gen_user до выкладки.
MIGRATIONS = ["2026_10_21_price_category", "2026_10_21_price_model_params", "2026_10_21_price_model_rules"]

# Параметр → (единицы, нижняя граница, верхняя граница, функция-право на правку). Проценты — доли.
PARAMS = {
    "category_price": ("rub", 1, None, "prices.model.edit"),
    "drr_pct": ("pct", 0, 1, "prices.model.drr"),
    "margin_pct": ("pct", -0.5, 1, "prices.model.margin"),
}

# Порог сигнала корректировки по площадке (доля) — как в миграции (кнопка «Сбросить к исходным»).
DEFAULT_RULES = {"Wildberries": 0.05, "Ozon": 0.05}
RULE_MIN, RULE_MAX = 0.001, 0.5   # 0,1% … 50%, шаг 0,1 процента


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


def load_rules():
    """{площадка: порог-доля}; чего нет в таблице — исходное значение."""
    rules = dict(DEFAULT_RULES)
    for r in db.query_all("SELECT marketplace, threshold FROM price_model_rules"):
        if r["marketplace"] in rules:
            rules[r["marketplace"]] = float(r["threshold"])
    return rules


def save_rules(rules, user_id):
    """Записать пороги {площадка: доля} одной транзакцией; ValueError с понятным текстом."""
    rows = []
    for mp, value in (rules or {}).items():
        if mp not in DEFAULT_RULES:
            raise ValueError(f"Неизвестная площадка «{mp}»")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{mp}: нужно число")
        if not math.isfinite(v) or v < RULE_MIN or v > RULE_MAX or abs(v * 1000 - round(v * 1000)) > 1e-6:
            raise ValueError(f"{mp}: порог — от {RULE_MIN * 100:g} до {RULE_MAX * 100:g}%, "
                             "не больше одного знака после запятой")
        rows.append((mp, round(v * 1000) / 1000))
    with db.transaction() as tx:
        for mp, v in rows:
            tx.execute(
                """INSERT INTO price_model_rules (marketplace, threshold, updated_at, updated_by_user_id)
                   VALUES (%s, %s, now(), %s)
                   ON CONFLICT (marketplace) DO UPDATE
                   SET threshold = EXCLUDED.threshold, updated_at = now(),
                       updated_by_user_id = EXCLUDED.updated_by_user_id""",
                (mp, v, user_id))
    return load_rules()
