# -*- coding: utf-8 -*-
"""ABC-анализ товарной матрицы — источник данных для одноимённой вкладки.

Отдаёт МЕСЯЧНЫЕ строки товаров (fact_monthly, row_type='Товар') обоих
маркетплейсов в «сыром» формате, который ожидает перенесённое ядро
эталонного дашборда (computeAll/aggregate/abcClassify).

ВАЖНО (соответствие эталону):
- сам ABC НЕ берётся из БД — он считается на клиенте по Парето;
  отсюда мы отдаём только базовые рублёвые/штучные величины;
- поле month отдаём РУССКИМ названием («Январь»…), year — числом,
  чтобы клиентский periodLabel() формировал «Янв 2026»;
- sku = seller_article (артикул продавца) — ЕДИНЫЙ ключ во всём
  приложении (справочник, RNP), одинаковый для Ozon и WB; именно по
  нему сопоставляются товары между маркетплейсами. НЕ sku_ozon и НЕ
  barcode_wb (это технические ID площадок). Приводим к верхнему
  регистру для устойчивого сопоставления;
- для Ozon прибыль/маржа — обычные revenue/profit/margin_pct;
  для WB берём показатели С УЧЁТОМ рекламы (wb_profit_with_ads,
  wb_margin_with_ads_pct), т.к. реклама WB вычитается отдельно.

Также отдаём список доступных месяцев с границами дат — клиентский
фильтр периода (календарный диапазон) переводит выбранный диапазон
в набор месяцев по пересечению интервалов.
"""
import calendar

from fastapi import APIRouter, Depends

from .. import db, auth

router = APIRouter(prefix="/api/abc", tags=["abc"])

# Номер месяца → русское название (как ждёт клиентский periodLabel).
MONTH_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def _num(v):
    """Привести значение БД к float (None → 0.0)."""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _txt(v):
    return (v or "").strip()


# --- SQL: товарные строки помесячно по каждому маркетплейсу --------------
# Ключ-артикул для ОБОИХ МП = seller_article (артикул продавца) — единый
# во всём приложении (справочник catalog_items, RNP). По нему товары
# сопоставляются между Ozon и WB на вкладке «Сравнение МП».
# Прибыль/маржа WB — версии «с учётом рекламы».
# Статус товара берём из справочника по МП (catalog_marketplace) через LEFT JOIN.
# РАНЬШЕ был скалярный коррелированный подзапрос с UPPER() — он
# выполнялся ОТДЕЛЬНО для каждой из тысяч месячных строк (N+1 внутри
# SQL, без индекса из-за UPPER) и давал ~1.7с на запрос. JOIN с пред-
# свёрнутым справочником = один hash join вместо тысяч подзапросов.
# ВАЖНО: в справочнике бывают артикулы, различающиеся ТОЛЬКО регистром
# (напр. 'MEX-201 GY' и 'mex-201 gy'), поэтому прямой JOIN по UPPER() размножил
# бы месячные строки и задвоил выручку. Поэтому сначала сворачиваем
# справочник через DISTINCT ON до ОДНОЙ строки на (UPPER(артикул), МП)
# — эквивалент старого LIMIT 1 в подзапросе.
SQL_OZON = """
    SELECT
        f.year, f.month,
        UPPER(COALESCE(f.seller_article, '')) AS sku,
        f.item_name           AS name,
        f.revenue, f.profit, f.margin_pct AS margin,
        f.sales_qty, f.orders_qty, f.returns_qty,
        f.promo_total         AS ad_spend,
        cm.status             AS status
    FROM fact_monthly f
    LEFT JOIN (
        SELECT DISTINCT ON (UPPER(seller_article))
               UPPER(seller_article) AS k, status
        FROM catalog_marketplace
        WHERE marketplace = 'Ozon'
        ORDER BY UPPER(seller_article), seller_article
    ) cm ON cm.k = UPPER(f.seller_article)
    WHERE f.row_type = 'Товар' AND f.marketplace = 'Ozon'
          AND COALESCE(f.seller_article, '') <> ''
"""

SQL_WB = """
    SELECT
        f.year, f.month,
        UPPER(COALESCE(f.seller_article, '')) AS sku,
        f.item_name           AS name,
        f.revenue,
        f.wb_profit_with_ads      AS profit,
        f.wb_margin_with_ads_pct  AS margin,
        f.sales_qty, f.orders_qty, f.returns_qty,
        f.wb_ads_total        AS ad_spend,
        cm.status             AS status
    FROM fact_monthly f
    LEFT JOIN (
        SELECT DISTINCT ON (UPPER(seller_article))
               UPPER(seller_article) AS k, status
        FROM catalog_marketplace
        WHERE marketplace = 'Wildberries'
        ORDER BY UPPER(seller_article), seller_article
    ) cm ON cm.k = UPPER(f.seller_article)
    WHERE f.row_type = 'Товар' AND f.marketplace = 'Wildberries'
          AND COALESCE(f.seller_article, '') <> ''
"""

# --- SQL: Яндекс (аналогично Ozon) --------------------------------------
# У Яндекса прибыль/маржа — обычные profit/margin_pct (как Ozon),
# реклама (ad_spend) — promo_total (у Яндекса хранится отрицательным,
# как Ozon — клиент берёт по модулю). Статус из catalog_marketplace(Yandex).
SQL_YA = """
    SELECT
        f.year, f.month,
        UPPER(COALESCE(f.seller_article, '')) AS sku,
        f.item_name           AS name,
        f.revenue, f.profit, f.margin_pct AS margin,
        f.sales_qty, f.orders_qty, f.returns_qty,
        f.promo_total         AS ad_spend,
        cm.status             AS status
    FROM fact_monthly f
    LEFT JOIN (
        SELECT DISTINCT ON (UPPER(seller_article))
               UPPER(seller_article) AS k, status
        FROM catalog_marketplace
        WHERE marketplace = 'Yandex'
        ORDER BY UPPER(seller_article), seller_article
    ) cm ON cm.k = UPPER(f.seller_article)
    WHERE f.row_type = 'Товар' AND f.marketplace = 'Yandex'
          AND COALESCE(f.seller_article, '') <> ''
"""

# --- SQL: ОБЩИЕ (нераспределённые) удержания на уровне МП ----------------
# Строки row_type <> 'Товар' (грузятся как 'Общие удержания'). У них нет
# артикула и выручки — это удержания МП, не привязанные к конкретному
# товару. В товарных «Прибылях» они НЕ учтены, поэтому для итоговой
# прибыли/маржи МП их нужно прибавлять помесячно отдельно.
# Прибыль берём так же, как у товаров: Ozon — profit, WB — с учётом рекламы.
# Яндекс — как Ozon (обычный profit); WB — с учётом рекламы.
SQL_HOLDS = """
    SELECT
        year, month, marketplace,
        CASE WHEN marketplace = 'Wildberries'
             THEN wb_profit_with_ads ELSE profit END AS profit
    FROM fact_monthly
    WHERE row_type <> 'Товар'
"""


def _shape(row, mp):
    """Строка БД → raw-объект эталона."""
    y = int(row["year"])
    m = int(row["month"])
    return {
        "mp": mp,                       # 'ozon' | 'vb' | 'ya'
        "sku": _txt(row["sku"]),
        "name": _txt(row["name"]),
        "month": MONTH_RU.get(m, str(m)),
        "year": y,
        "revenue": _num(row["revenue"]),
        "profit": _num(row["profit"]),
        "margin": _num(row["margin"]),
        "sales_qty": _num(row["sales_qty"]),
        "orders_qty": _num(row["orders_qty"]),
        "returns_qty": _num(row["returns_qty"]),
        "ad_spend": _num(row["ad_spend"]),
        "status": _txt(row["status"]),
    }


@router.get("/data")
def abc_data(user=Depends(auth.get_current_user)):
    """Все месячные товарные строки обоих МП + список доступных месяцев."""
    # 4 независимых запроса (Ozon, Wildberries, Yandex, общие удержания) — последовательно на тёплом пуле.
    oz = db.query_all(SQL_OZON)
    wb = db.query_all(SQL_WB)
    ya = db.query_all(SQL_YA)
    hold_rows = db.query_all(SQL_HOLDS)

    rows = ([_shape(r, "ozon") for r in oz]
            + [_shape(r, "vb") for r in wb]
            + [_shape(r, "ya") for r in ya])

    # Общие удержания — отдельным массивом (не смешиваем с товарными
    # строками, чтобы не сломать ABC-классификацию и сопоставление по SKU).
    # Месяц — русским названием, как у товарных строк (клиент парсит «Янв 2026»).
    _HOLD_MP = {"Wildberries": "vb", "Yandex": "ya", "Ozon": "ozon"}
    holds = [
        {
            "mp": _HOLD_MP.get(r["marketplace"], "ozon"),
            "year": int(r["year"]),
            "month": MONTH_RU.get(int(r["month"]), str(r["month"])),
            "profit": _num(r["profit"]),
        }
        for r in hold_rows
    ]

    # Уникальные месяцы с границами дат (для фильтра периода и базовой даты).
    seen = {}
    for r in oz + wb + ya:
        y, m = int(r["year"]), int(r["month"])
        key = (y, m)
        if key not in seen:
            last = calendar.monthrange(y, m)[1]
            seen[key] = {
                "year": y,
                "month": m,
                "label": "%s %d" % (MONTH_RU.get(m, str(m))[:3], y),
                "period_start": "%04d-%02d-01" % (y, m),
                "period_end": "%04d-%02d-%02d" % (y, m, last),
            }
    months = sorted(seen.values(), key=lambda x: (x["year"], x["month"]))

    return {"rows": rows, "holds": holds, "months": months}
