# -*- coding: utf-8 -*-
"""РНП UNIT — юнит-экономика по неделям (иерархическое дерево).

Дерево: Итоги (root) → Уровень 1 → Уровень 2 → Уровень 3 → Товар.
Все недели маркетплейса идут колонками. Для каждого узла по каждой неделе
считается 22 метрики из базовых рублёвых/количественных колонок fact_weekly.

Принцип расчёта (выверен против прод-API):
- Суммируем базовые рублёвые/штучные колонки по всем товарам узла (row_type='Товар').
- Доли (pct) = сумма_рубли / сумма_выручки.
- Удельные (price) = сумма_рубли / сумма_штук.
- Средневзвешенные доли (buyout_pct, spp_pct) = Σ(доля×выручка) / Σ(выручка).
"""

from fastapi import APIRouter, Depends, Query

from .. import db, auth
from ..util import l1_sort_key, cat_sort_key

router = APIRouter(prefix="/api/rnp", tags=["rnp"])

# Сопоставление имени маркетплейса в URL → значение в БД.
MP_MAP = {"ozon": "Ozon", "wildberries": "Wildberries", "wb": "Wildberries",
          "yandex": "Yandex", "ya": "Yandex"}


# ---------------------------------------------------------------------------
# Определения метрик: (ключ, подпись, тип отображения, направление "хорошо")
# kind: pct_node | pct | qty | rub | price ; direction: good | bad | neut
# ---------------------------------------------------------------------------
METRIC_DEFS = [
    ("rnp",            "РНП %",                                  "pct_node", "good"),
    ("cogs_pct",       "Себестоимость % от выр",                 "pct",      "bad"),
    ("commission_pct", "Комиссия %",                             "pct",      "bad"),
    ("logistics_pct",  "Логистика %",                            "pct",      "bad"),
    ("acquiring_pct",  "Эквайринг %",                            "pct",      "bad"),
    ("storage_pct",    "Хранение %",                             "pct",      "bad"),
    ("fines_pct",      "Штрафы %",                               "pct",      "bad"),
    ("promo_pct",      "Реклама %",                              "pct",      "bad"),
    ("other_holds_pct","Прочие удержания %",                     "pct",      "bad"),
    ("ads_diff_pct",   "Разница в начислении и тратах рекламы %","pct",      "neut"),
    ("noart_exp_rub",  "Прочие удержания (общие) руб",           "rub",      "bad"),
    ("noart_exp_pct",  "Расходы без разбивки по артикулам %",    "pct",      "bad"),
    ("other_inc_pct",  "Прочие доходы %",                        "pct",      "good"),
    ("sales_qty",      "Кол-во продаж шт",                       "qty",      "good"),
    ("revenue",        "Выручка руб",                            "rub",      "good"),
    ("avg_price",      "Цена руб/ед",                            "price",    "neut"),
    ("profit",         "Маржа руб",                              "rub",      "good"),
    ("profit_per_unit","Маржа руб/ед",                           "price",    "good"),
    ("buyout_pct",     "Выкуп %",                                "pct",      "good"),
    ("log_per_unit",   "Логистика руб/1шт",                      "price",    "bad"),
    ("log_nr_per_unit","Логистика без возвратов руб/1шт",        "price",    "bad"),
    ("spp_pct",        "СПП %",                                  "pct",      "neut"),
    ("drr_pct",        "ДРР % к заказам",                        "pct",      "bad"),
]

METRIC_KEYS = [m[0] for m in METRIC_DEFS]


# ---------------------------------------------------------------------------
# Метрики уровня ТОВАРА (раскрываются при разворачивании листа-товара).
# Формат прод-API: (ключ, подпись, тип, цвет HEX, жирный, разделитель-сверху).
# Все значения выводятся из тех же базовых сумм узла, поэтому корректно
# агрегируются на любом уровне дерева.
# Порядок (согласованный дизайн «юнит-экономика», сверху вниз):
#   1) Себестоимость → Продвижение (от продаж / от заказов) → Все удержания;
#   2) разделитель (sep) → Прибыль → Продажи (шт/руб) → План →
#      Заказы (шт/руб) → Ср. цена → Выкуп.
# Цвета названий/значений заданы прямо здесь (color HEX): себестоимость и
# план — синий 0000FF; продвижение от продаж — фиолетовый 9900FF; продвижение
# от заказов — бледно-фиолетовый C58CFF; «Все удержания» и «Штрафы» — красный;
# служебные удержания — серый; Прибыль/Заказы/Ср.цена — жирные нейтральные.
# ---------------------------------------------------------------------------
# Кортеж: (ключ, подпись, kind, цвет-HEX-без-#, bold, sep, polarity).
# polarity — полярность для окраски дельты (изменения к прошлой неделе):
#   "pos"  — рост = хорошо (зелёный), падение = плохо (красный);
#   "neg"  — рост = плохо (красный), падение = хорошо (зелёный) — расходные;
#   "none" — дельта не показывается вовсе (План продаж — ставим вручную).
PRODUCT_METRIC_DEFS = [
    # --- Блок 1: себестоимость, продвижение, удержания (расходы: neg) ---
    ("p_cogs_pct",         "Себестоимость от выручки, %",       "pct",   "0000FF", False, False, "neg"),
    # «Продвижение, от продаж» (= промо / выручка) — фиолетовый.
    # На фронте это раскрываемая группа: дочерняя строка «Продвижение,
    # от заказов» идёт сразу после и показывается при раскрытии группы.
    ("p_promo_pct",        "Продвижение, от продаж",            "pct",   "9900FF", False, False, "neg"),
    ("p_promo_orders_pct", "Продвижение, от заказов",           "pct",   "C58CFF", False, False, "neg"),
    # «Все удержания» — красный, жирный. Дети (до след. sep) — служебные
    # удержания серым, штрафы красным.
    ("p_holds_pct",        "Все удержания, %",                  "pct",   "C0392B", True,  False, "neg"),
    ("p_commission_pct",   "Комиссия, %",                       "pct",   "16191D", False, False, "neg"),
    ("p_logistics_pct",    "Логистика, %",                      "pct",   "16191D", False, False, "neg"),
    ("p_log_per_unit",     "Логистика на ед.",                  "price", "16191D", False, False, "neg"),
    ("p_acquiring_pct",    "Эквайринг, %",                      "pct",   "16191D", False, False, "neg"),
    ("p_storage_pct",      "Хранение и проч. удержания, %",  "pct",   "16191D", False, False, "neg"),
    ("p_storage_rub",      "Хранение и проч. удержания, руб.","rub",  "16191D", False, False, "neg"),
    ("p_fines_pct",        "Штрафы, %",                         "pct",   "D11414", False, False, "neg"),
    ("p_fines_rub",        "Штрафы, руб.",                      "rub",   "D11414", False, False, "neg"),
    # --- Блок 2: прибыль, объёмы, план, заказы, цена, выкуп (sep сверху) ---
    # Доходные метрики (pos): рост = хорошо. План продаж — без дельты (none).
    ("profit",             "Прибыль, руб",                      "rub",   "16191D", True,  True,  "pos"),
    ("p_sales_qty",        "Продажи, шт",                       "qty",   "16191D", False, False, "pos"),
    ("p_revenue",          "Продажи, руб",                      "rub",   "16191D", False, False, "pos"),
    ("p_plan_qty",         "План продаж, шт",                   "qty",   "0000FF", False, False, "none"),
    ("p_orders_qty",       "Заказы, шт",                        "qty",   "16191D", True,  False, "pos"),
    ("p_orders_rub",       "Заказы, руб",                       "rub",   "16191D", True,  False, "pos"),
    ("p_avg_price",        "Ср. цена продажи",                 "price", "16191D", False, False, "pos"),
    ("p_buyout_pct",       "Выкуп, %",                          "pct",   "96A5AF", False, False, "pos"),
]


def _product_metrics_payload():
    return [
        {"key": k, "label": lbl, "kind": kind, "color": color, "bold": bold,
         "sep": sep, "polarity": polarity}
        for (k, lbl, kind, color, bold, sep, polarity) in PRODUCT_METRIC_DEFS
    ]


def _f(v):
    return float(v) if v is not None else 0.0


def _div(a, b):
    """Безопасное деление: None при нулевом знаменателе."""
    return (a / b) if b else None


def _compute_cell(agg, mp="Ozon"):
    """Посчитать метрики из агрегата базовых колонок узла за одну неделю.

    mp — маркетплейс ("Ozon"/"Wildberries"): влияет на источник расходов на
    продвижение. У Ozon продвижение хранится в promo_total (отрицательным),
    у Wildberries вся реклама («Вся реклама» / ДРР) лежит в wb_ads_total
    (положительным), а promo_total по товарам = 0.
    """
    rev   = _f(agg.get("revenue"))
    qty   = _f(agg.get("sales_qty"))
    orders = _f(agg.get("orders_qty"))
    profit = _f(agg.get("profit"))

    # Заказы в рублях — ГИБРИД: если в отчёте есть готовая колонка «Заказы, руб.»
    # (orders_rub из БД, просуммированная по узлу и > 0) — берём её. Иначе
    # fallback на старую формулу: заказы (шт.) × средняя цена продажи, где
    # средняя цена = выручка / продано (шт.) — на группах это взвешенная по
    # выручке средняя (Σrevenue / Σsales_qty). При нулевых продажах = None.
    _avg_price = _div(rev, qty)
    _orders_rub_db = agg.get("orders_rub")
    if _orders_rub_db is not None and _f(_orders_rub_db) > 0:
        orders_rub = _f(_orders_rub_db)
    else:
        orders_rub = (orders * _avg_price) if _avg_price is not None else None

    if mp == "Wildberries":
        # === Маржинальность Wildberries «с рекламой» ===
        # Отчёт WB содержит ДВЕ колонки прибыли/маржи:
        #   • «Прибыль» / «Марж-ность»        (db: profit)  — БЕЗ рекламы (SH-040 = 23,4%)
        #   • «Прибыль (с рекл.)» / «Марж-ность (с рекл.)»
        #     (db: wb_profit_with_ads) — С УЧЁТОМ рекламы (SH-040 = 17,8%)
        #
        # По решению пользователя берём ГОТОВУЮ колонку «Прибыль (с рекл.)»
        # напрямую и просто суммируем её по узлу. Эта колонка заполнена
        # и у товаров, и у строки «Общие удержания» (там она = −147414 за нед.24,
        # т.е. уже содержит общую рекламу аккаунта). Поэтому:
        #   Прибыль узла (с рекл.) = Σ wb_profit_with_ads всех строк узла.
        #
        # Это даёт ПОЛНОЕ совпадение с исходным отчётом WB на всех уровнях:
        #   товар SH-040W = 17,8%, итого нед.24 = 23,29% (как в строке «Итого» файла).
        # noart_exp и wb_ads_total ДЛЯ WB НЕ вычитаются — иначе реклама
        # учлась бы дважды (она уже внутри wb_profit_with_ads).
        profit = _f(agg.get("wb_profit_with_ads"))
    else:
        # === Ozon ===
        # Общие удержания маркетплейса (строки row_type='Общие удержания') —
        # расходы БЕЗ привязки к артикулу. Их прибыль в отчёте = −holds_total.
        # Заполняются ТОЛЬКО у корневого узла «Итоги» (agg['noart_exp']),
        # у групп/товаров noart_exp = None. У Ozon реклама уже включена
        # в profit (promo_total отрицательный), поэтому дополнительно
        # рекламу не вычитаем — только общие удержания у «Итого».
        noart_exp_val = agg.get("noart_exp")
        if noart_exp_val is not None:
            profit = profit - _f(noart_exp_val)

    cogs        = _f(agg.get("cogs"))
    commission  = _f(agg.get("commission"))
    acquiring   = _f(agg.get("acquiring"))
    logistics_t = _f(agg.get("logistics_total"))
    logistics_d = _f(agg.get("logistics_direct"))
    storage     = _f(agg.get("storage"))
    disposal    = _f(agg.get("oz_disposal"))   # утилизация (Ozon) — входит в "хранение и проч."
    storage_all = storage + disposal             # Хранение и проч. удержания (прод-API)
    promo       = _f(agg.get("promo_total"))
    wb_ads      = _f(agg.get("wb_ads_total"))
    fines       = _f(agg.get("wb_fines"))
    other_holds = _f(agg.get("wb_other_holds"))
    other_inc   = _f(agg.get("external_exp_inc"))
    noart       = agg.get("noart_exp")  # может быть None (нет расходов без артикула)

    bo_w  = agg.get("buyout_w")   # Σ(buyout_pct×revenue)
    spp_w = agg.get("spp_w")      # Σ(spp_pct×revenue)

    # Реклама ИТОГО (без разбивки по видам): Ozon = клики+заказы+продвижение в поиске,
    # WB = wb_ads_total. Суммы хранятся положительными (расход на рекламу).
    ads_total = (_f(agg.get("oz_pay_per_click")) + _f(agg.get("oz_pay_per_order"))
                 + _f(agg.get("oz_special_placement")) + _f(agg.get("wb_ads_total")))

    # Расход на продвижение (ПОЛОЖИТЕЛЬНый, руб.) — единый смысл «вся реклама»:
    #   Ozon: promo_total хранится отрицательным → берём -promo;
    #   WB:   реклама в wb_ads_total (положительный).
    if mp == "Wildberries":
        promo_eff = wb_ads
    else:
        promo_eff = -promo

    # При нулевой выручке все доли/удельные = None (как в прод-API).
    promo_pct = _div(promo, rev)
    cell = {
        "rnp":             _div(profit, rev),
        "cogs_pct":        _div(cogs, rev),
        "commission_pct":  _div(commission, rev),
        "logistics_pct":   _div(logistics_t, rev),
        "acquiring_pct":   _div(acquiring, rev),
        "storage_pct":     _div(storage, rev),
        "fines_pct":       _div(fines, rev),
        "promo_pct":       promo_pct,
        "other_holds_pct": _div(other_holds, rev),
        "ads_diff_pct":    (-promo_pct) if promo_pct is not None else None,
        "noart_exp_rub":   (_f(noart) if noart is not None else None),
        "noart_exp_pct":   (_div(_f(noart), rev) if noart is not None else None),
        "other_inc_pct":   _div(other_inc, rev),
        "orders_qty":      orders,
        "orders_rub":      orders_rub,
        "sales_qty":       qty,
        "revenue":         rev,
        "avg_price":       _div(rev, qty),
        "profit":          profit,
        "profit_per_unit": _div(profit, qty),
        "buyout_pct":      (_div(_f(bo_w), rev) if bo_w is not None else None),
        "log_per_unit":    _div(logistics_t, qty),
        "log_nr_per_unit": _div(logistics_d, qty),
        "spp_pct":         (_div(_f(spp_w), rev) if spp_w is not None else None),
        "drr_pct":         promo_pct,
    }

    # --- Метрики уровня товара (p_*) ---
    # «Все удержания» (кроме себестоимости): комиссия + эквайринг +
    # логистика + хранение + штрафы. Продвижение (promo_eff) ВЫНЕСЕНО
    # в отдельную группу «Продвижение» и НЕ входит в сумму удержаний
    # (иначе было бы двойное учёт). На прибыль это не влияет — profit
    # считается отдельно (WB: wb_profit_with_ads).
    holds_total_rub = commission + acquiring + logistics_t + storage_all + fines
    cell.update({
        "p_cogs_pct":       _div(cogs, rev),
        "p_holds_pct":      _div(holds_total_rub, rev),
        "p_commission_pct": _div(commission, rev),
        "p_acquiring_pct":  _div(acquiring, rev),
        "p_logistics_pct":  _div(logistics_t, rev),
        "p_log_per_unit":   _div(logistics_t, qty),
        "p_promo_pct":      _div(promo_eff, rev),
        "p_promo_orders_pct": _div(promo_eff, orders_rub),
        "p_storage_rub":    storage_all,
        "p_storage_pct":    _div(storage_all, rev),
        "p_fines_rub":      fines,
        "p_fines_pct":      _div(fines, rev),
        "p_orders_qty":     orders,
        "p_orders_rub":     orders_rub,
        "p_sales_qty":      qty,
        "p_revenue":        rev,
        "p_avg_price":      _div(rev, qty),
        "p_buyout_pct":     (_div(_f(bo_w), rev) if bo_w is not None else None),
    })

    # --- Групповые дополнительные метрики ---
    # Реклама ИТОГО: сумма руб. и доля от выручки.
    cell["ads_total"] = ads_total
    cell["ads_pct"] = _div(ads_total, rev)

    # --- Сырые рублёвые/штучные суммы для СВОДНОЙ вкладки ---
    # Сводная складывает рублёвые величины по двум МП и ПЕРЕСЧИТЫВАЕТ проценты
    # от общей выручки (суммировать готовые % по двум МП некорректно). Прибыль
    # уже посчитана нужным способом для своего МП (Ozon: profit−noart; WB:
    # wb_profit_with_ads), поэтому отдаём её рублём — на сводной просто сложим.
    cell["_raw"] = {
        "revenue":    rev,
        "sales_qty":  qty,
        "orders_qty": orders,
        "orders_rub": (orders_rub if orders_rub is not None else 0.0),
        "profit":     profit,
        "holds_rub":  holds_total_rub,
    }
    return cell


# Базовые колонки, которые суммируем по узлу.
_SUM_COLS = [
    "revenue", "sales_qty", "orders_qty", "orders_rub", "profit", "cogs", "commission", "acquiring",
    "logistics_total", "logistics_direct", "storage", "oz_disposal", "promo_total",
    "wb_fines", "wb_other_holds", "external_exp_inc",
    # Реклама итого (компоненты): Ozon ppc/ppo/spec, WB wb_ads_total
    "oz_pay_per_click", "oz_pay_per_order", "oz_special_placement", "wb_ads_total",
    # Эталонная прибыль WB «с рекламой» из исходного отчёта
    # (колонка «Прибыль (с рекл.)»). Суммируется по узлу вместе
    # со строкой «Общие удержания» — даёт маржу WB точь-в-точь как в отчёте.
    "wb_profit_with_ads",
]


def _empty_agg():
    a = {c: 0.0 for c in _SUM_COLS}
    a["buyout_w"] = 0.0
    a["spp_w"] = 0.0
    a["noart_exp"] = None
    return a


def _add_row(agg, row):
    """Прибавить строку факта к агрегату узла."""
    for c in _SUM_COLS:
        agg[c] += _f(row.get(c))
    rev = _f(row.get("revenue"))
    agg["buyout_w"] += _f(row.get("buyout_pct")) * rev
    agg["spp_w"] += _f(row.get("spp_pct")) * rev


def negative_margin_articles(mp: str) -> set:
    """Множество артикулов с ОТРИЦАТЕЛЬНОЙ маржинальностью за ПОСЛЕДНЮЮ неделю.

    Маржинальность (rnp = profit / revenue) считается той же _compute_cell,
    что и в popup юнит-экономики — цифры 1-в-1. Берём САМУЮ НОВУЮ
    неделю маркетплейса (MAX year, week) из fact_weekly. Артикул попадает
    в результат, только если rnp определён (revenue ≠ 0) И rnp < 0.
    Нулевая/положительная маржа и случай «rnp = None» (нет выручки) — НЕ попадают.

    Артикулы возвращаются В ВЕРХНЕМ РЕГИСТРЕ (UPPER) — канонический ключ.
    Дёшево: один SELECT по fact_weekly (недельные агрегаты), не трогает
    тяжёлые дневные таблицы РНП заказов.
    """
    last = db.query_all(
        """
        SELECT year, week FROM fact_weekly
        WHERE marketplace = %s
        ORDER BY year DESC, week DESC
        LIMIT 1
        """,
        (mp,),
    )
    if not last:
        return set()
    yy, ww = last[0]["year"], last[0]["week"]
    rows = db.query_all(
        """
        SELECT seller_article,
               orders_qty, orders_rub, revenue, sales_qty, profit, cogs,
               commission, acquiring, logistics_total, logistics_direct,
               storage, oz_disposal, promo_total, wb_fines, wb_other_holds,
               external_exp_inc, buyout_pct, spp_pct,
               oz_pay_per_click, oz_pay_per_order, oz_special_placement,
               wb_ads_total, wb_profit_with_ads
        FROM fact_weekly
        WHERE marketplace = %s AND row_type = 'Товар'
          AND year = %s AND week = %s
        """,
        (mp, yy, ww),
    )
    # Агрегируем по артикулу (на случай нескольких строк на товар).
    aggs = {}
    for r in rows:
        art = (r.get("seller_article") or "").strip().upper()
        if not art:
            continue
        if art not in aggs:
            aggs[art] = _empty_agg()
        _add_row(aggs[art], r)
    neg = set()
    for art, agg in aggs.items():
        cell = _compute_cell(agg, mp)
        rnp = cell.get("rnp")
        if rnp is not None and rnp < 0:
            neg.add(art)
    return neg


@router.get("/weeks")
def rnp_weeks(marketplace: str = Query("ozon"), user=Depends(auth.get_current_user)):
    """Список недель маркетплейса (для шапки)."""
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    rows = db.query_all(
        """
        SELECT DISTINCT year, week, period_text,
               MIN(period_start) AS period_start, MIN(period_end) AS period_end
        FROM fact_weekly
        WHERE marketplace = %s
        GROUP BY year, week, period_text
        ORDER BY year, week
        """,
        (mp,),
    )
    out = []
    for r in rows:
        out.append({
            "year": r["year"],
            "week": r["week"],
            "period_text": r["period_text"],
            "period_start": r["period_start"].isoformat() if r.get("period_start") else None,
            "period_end": r["period_end"].isoformat() if r.get("period_end") else None,
        })
    return out


# Компактный набор метрик для popup юнит-экономики товара (в РНП заказы).
# Ключи берутся из _compute_cell (те же, что в карточке юнит-экономики).
# polarity совпадает с PRODUCT_METRIC_DEFS: neg = расход (рост 🔴), pos = доход (рост 🟢).
PRODUCT_UE_POPUP_DEFS = [
    ("profit",       "Прибыль, руб",             "rub", "pos"),
    ("rnp",          "Маржинальность, %",       "pct", "pos"),
    ("p_cogs_pct",   "Себестоимость, %",        "pct", "neg"),
    ("p_promo_pct",  "Продвижение, от продаж", "pct", "neg"),
    ("p_holds_pct",  "Все удержания, %",       "pct", "neg"),
    ("p_buyout_pct", "Выкуп, %",                 "pct", "pos"),
]


@router.get("/product_ue")
def rnp_product_ue(
    marketplace: str = Query("ozon"),
    article: str = Query(...),
    weeks: int = Query(3, ge=1, le=12),
    user=Depends(auth.get_current_user),
):
    """Лёгкая юнит-экономика ОДНОГО артикула за последние N недель.

    Используется для popup в РНП заказы (клик по иконке-монеткам).
    Считает из недельных агрегатов (fact_weekly) — дешёво и быстро,
    не трогает тяжёлые дневные таблицы РНП заказов. Набор метрик
    сокращённый (PRODUCT_UE_POPUP_DEFS), цифры — 1-в-1 с карточкой
    юнит-экономики (та же _compute_cell).
    """
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    art = (article or "").strip()
    if not art:
        return {"marketplace": mp, "article": "", "weeks": [], "metrics": [], "cells": {}}

    # 1) Последние N недель маркетплейса.
    wk_rows = db.query_all(
        """
        SELECT year, week, period_text,
               MIN(period_start) AS period_start, MIN(period_end) AS period_end
        FROM fact_weekly
        WHERE marketplace = %s
        GROUP BY year, week, period_text
        ORDER BY year DESC, week DESC
        LIMIT %s
        """,
        (mp, weeks),
    )
    wk_rows = list(reversed(wk_rows))  # хронологически: старшая → новейшая
    week_keys = [f"{r['year']}-{r['week']}" for r in wk_rows]
    weeks_out = [
        {
            "key": f"{r['year']}-{r['week']}",
            "year": r["year"], "week": r["week"],
            "label": f"Нед {r['week']}",
            "period_text": r.get("period_text"),
            "period_start": r["period_start"].isoformat() if r.get("period_start") else None,
            "period_end": r["period_end"].isoformat() if r.get("period_end") else None,
        }
        for r in wk_rows
    ]

    cells = {}
    if week_keys:
        # 2) Строки товара за эти недели (JOIN не нужен — берём только
        # базовые колонки для _compute_cell). UPPER() — артикулы канонические.
        year_week_pairs = tuple((r["year"], r["week"]) for r in wk_rows)
        placeholders = " OR ".join(["(year = %s AND week = %s)"] * len(year_week_pairs))
        params = [mp, art]
        for (yy, ww) in year_week_pairs:
            params.extend([yy, ww])
        rows = db.query_all(
            f"""
            SELECT year, week,
                   orders_qty, orders_rub, revenue, sales_qty, profit, cogs,
                   commission, acquiring, logistics_total, logistics_direct,
                   storage, oz_disposal, promo_total, wb_fines, wb_other_holds,
                   external_exp_inc, buyout_pct, spp_pct,
                   oz_pay_per_click, oz_pay_per_order, oz_special_placement,
                   wb_ads_total, wb_profit_with_ads
            FROM fact_weekly
            WHERE marketplace = %s AND row_type = 'Товар'
              AND UPPER(seller_article) = UPPER(%s)
              AND ({placeholders})
            """,
            tuple(params),
        )
        aggs = {wk: _empty_agg() for wk in week_keys}
        for r in rows:
            wk = f"{r['year']}-{r['week']}"
            if wk in aggs:
                _add_row(aggs[wk], r)
        cells = {wk: _compute_cell(aggs[wk], mp) for wk in week_keys}

    metrics = [
        {"key": k, "label": lbl, "kind": kind, "polarity": pol}
        for (k, lbl, kind, pol) in PRODUCT_UE_POPUP_DEFS
    ]
    return {
        "marketplace": mp,
        "article": art,
        "weeks": weeks_out,
        "metrics": metrics,
        "cells": cells,
    }


@router.get("/metrics")
def rnp_metrics(user=Depends(auth.get_current_user)):
    """Каталог метрик (ключ, подпись, тип, направление)."""
    return [
        {"key": k, "label": lbl, "kind": kind, "direction": direction}
        for (k, lbl, kind, direction) in METRIC_DEFS
    ]


@router.get("/tree")
def rnp_tree(marketplace: str = Query("ozon"), user=Depends(auth.get_current_user)):
    """Иерархическое дерево юнит-экономики со всеми неделями-колонками."""
    mp = MP_MAP.get(marketplace.lower(), "Ozon")

    # 3 независимых запроса — последовательно на тёплом пуле: недели (колонки),
    # товарные строки и общие удержания. noart_wpa (только для WB) — ниже, как есть.
    wk_rows = db.query_all(
        """
        SELECT DISTINCT year, week, period_text,
               MIN(period_start) AS period_start, MIN(period_end) AS period_end
        FROM fact_weekly
        WHERE marketplace = %s
        GROUP BY year, week, period_text
        ORDER BY year, week
        """,
        (mp,),
    )
    rows = db.query_all(
        """
        SELECT f.year, f.week, f.seller_article, f.sku_ozon, f.item_name,
               ci.category_l1, ci.category_l2, ci.category_l3,
               cm.status, cm.manager,
               f.orders_qty, f.orders_rub, f.returns_qty, f.holds_total,
               f.revenue, f.sales_qty, f.profit, f.cogs, f.commission, f.acquiring,
               f.logistics_total, f.logistics_direct, f.storage, f.oz_disposal, f.promo_total,
               f.wb_fines, f.wb_other_holds, f.external_exp_inc,
               f.buyout_pct, f.spp_pct, f.abc_revenue,
               f.oz_pay_per_click, f.oz_pay_per_order, f.oz_special_placement, f.wb_ads_total,
               f.wb_profit_with_ads
        FROM fact_weekly f
        LEFT JOIN catalog_items ci ON ci.seller_article = f.seller_article
        LEFT JOIN catalog_marketplace cm
               ON cm.seller_article = f.seller_article AND cm.marketplace = f.marketplace
        WHERE f.marketplace = %s AND f.row_type = 'Товар'
        ORDER BY f.id
        """,
        (mp,),
    )
    noart_rows = db.query_all(
        """
        SELECT year, week, SUM(holds_total) AS holds
        FROM fact_weekly
        WHERE marketplace = %s AND row_type = 'Общие удержания'
        GROUP BY year, week
        """,
        (mp,),
    )
    # Артикулы, заведённые вручную в справочник по этому МП — доп.
    # источник списка товаров. Нужно, чтобы новинка появилась в РНП
    # СРАЗУ — ещё до загрузки недельного факта. Факт «приклеится»
    # автоматически по seller_article, как только появится в отчёте.
    cat_only_rows = db.query_all(
        """
        SELECT cm.seller_article, ci.sample_name AS item_name,
               ci.category_l1, ci.category_l2, ci.category_l3,
               cm.status, cm.manager
        FROM catalog_marketplace cm
        LEFT JOIN catalog_items ci ON ci.seller_article = cm.seller_article
        WHERE cm.marketplace = %s
        """,
        (mp,),
    )

    # 1) Недели (колонки). period_start/period_end нужны фронтенду для фильтра
    # по периоду: неделя показывается, если её интервал пересекается с выбранным
    # диапазоном дат (хотя бы один день недели попадает в фильтр).
    weeks = []
    for r in wk_rows:
        wkey = f"{r['year']}-{r['week']}"
        weeks.append({
            "year": r["year"], "week": r["week"],
            "period_text": r["period_text"],
            "period_start": r["period_start"].isoformat() if r.get("period_start") else None,
            "period_end": r["period_end"].isoformat() if r.get("period_end") else None,
            "key": wkey, "label": f"Неделя {r['week']}",
        })
    week_keys = [w["key"] for w in weeks]

    # Недельный план продаж по этому МП (та же раскладка, что в Сводной, но с
    # фильтром sales_plan.marketplace = текущий МП). Ключи plan_map совпадают с
    # ключами узлов дерева (__root__, l1, l1|l2, l1|l2|l3, l1|l2|l3|art).
    plan_map = _plan_by_node_week(weeks, mp)

    # 2-) Синтетические строки-заглушки для вручную заведённых
    # артикулов, которых ещё НЕТ в fact_weekly. Добавляем по одной
    # нулевой строке на «якорную» (последнюю) неделю — товар
    # появится в дереве с прочерками/нулями. Реальные данные
    # придут обычными строками после загрузки отчёта. Если недель
    # нет вовсе — показывать негде (колонок-недель нет).
    #
    # ВАЖНО: добавляем ТОЛЬКО полностью распределённые карточки
    # (заполнены K1/K2/K3 + статус + менеджер). Иначе сотни старых
    # записей catalog_marketplace без распределения, никогда не
    # имевших продаж, посыпались бы в «нераспределённые» и засоряли
    # отчёт. Незаполненная карточка и так не попала бы в иерархию —
    # показывать её как заглушку смысла нет. Новинка появляется
    # сразу, как только менеджер заполнил все поля в «Справочнике».
    if week_keys:
        existing_arts = {(r.get("seller_article") or "") for r in rows}
        anchor_year, anchor_week = weeks[-1]["year"], weeks[-1]["week"]
        for cr in cat_only_rows:
            a = cr.get("seller_article") or ""
            if not a or a in existing_arts:
                continue
            # Пропускаем не полностью распределённые карточки.
            if (not cr.get("category_l1") or not cr.get("category_l2")
                    or not cr.get("category_l3") or not cr.get("status")
                    or not cr.get("manager")):
                continue
            existing_arts.add(a)
            rows.append({
                "year": anchor_year, "week": anchor_week,
                "seller_article": a, "sku_ozon": None,
                "item_name": cr.get("item_name"),
                "category_l1": cr.get("category_l1"),
                "category_l2": cr.get("category_l2"),
                "category_l3": cr.get("category_l3"),
                "status": cr.get("status"), "manager": cr.get("manager"),
            })

    # 2) Все строки-товары + справочник (LEFT JOIN) — получены выше параллельно.
    #    Категории (K1/K2/K3) и наименование — ОБЩИЕ по артикулу (catalog_items).
    #    СТАТУС и МЕНЕДЖЕР — РАЗДЕЛЬНЫЕ по маркетплейсу
    #    (catalog_marketplace, ключ seller_article + marketplace).

    # 3) Сбор дерева. Узел: key,name,level,_agg(по неделям),children(dict),leaf_info
    def new_node(key, name, level):
        return {
            "key": key, "name": name, "level": level,
            "_agg": {wk: _empty_agg() for wk in week_keys},
            "children": {}, "leaf_info": None,
            "_abc": {},   # только для листьев: ABC-класс по выручке по неделям {wk: 'A'/'B'/'C'}
        }

    root = new_node("__root__", "Итоги", 0)
    undistributed = {}  # seller_article -> item_name (нераспределённые)

    # Предпроход: артикулы, у которых ЕСТЬ движение хотя бы в одной неделе
    # загруженного периода. «Движение» = любое ненулевое из revenue / sales_qty /
    # orders_qty / returns_qty / profit / holds_total. Нужно, чтобы скрывать
    # товары со статусом «-» (выведен из продаж) без движений, но оставлять их,
    # если активность была. Считаем по всем строкам артикула, т.к. статус «-»
    # относится к товару целиком, а строки идут понедельно.
    moved = set()
    for r in rows:
        art0 = r.get("seller_article") or ""
        if not art0 or art0 in moved:
            continue
        if (_f(r.get("revenue")) or _f(r.get("sales_qty")) or _f(r.get("orders_qty"))
                or _f(r.get("returns_qty")) or _f(r.get("profit")) or _f(r.get("holds_total"))):
            moved.add(art0)

    for r in rows:
        wk = f"{r['year']}-{r['week']}"
        if wk not in root["_agg"]:
            continue
        l1, l2, l3 = r.get("category_l1"), r.get("category_l2"), r.get("category_l3")
        st, mgr = r.get("status"), r.get("manager")
        art = r.get("seller_article") or ""

        # Статус «-» (выведен из продаж) — распределён, но скрываем артикул из
        # отчёта целиком, если за загруженный период у него НЕ было движений.
        # «?» и прочие статусы никогда не скрываем по этому правилу.
        st_norm = (st or "").strip()
        if st_norm == "-" and art and art not in moved:
            continue

        # Единое правило (Ozon и Wildberries): товар считается НЕраспределённым,
        # если выполнено хотя бы одно условие —
        #   • не заполнена хотя бы одна категория (L1/L2/L3);
        #   • отсутствует статус;
        #   • отсутствует менеджер.
        is_undist = (not l1 or not l2 or not l3 or not st or not mgr)
        if is_undist and art and art not in undistributed:
            undistributed[art] = r.get("item_name")

        # Строка «Итоги» (root) включает ВСЕ товары — и распределённые,
        # и нераспределённые (обороты нераспределённых учитываются в итоге).
        _add_row(root["_agg"][wk], r)

        # В дерево групп нераспределённые товары НЕ попадают.
        if is_undist:
            continue

        # L1
        k1 = l1
        n1 = root["children"].get(k1)
        if not n1:
            n1 = new_node(k1, l1, 1); root["children"][k1] = n1
        _add_row(n1["_agg"][wk], r)
        # L2
        k2 = f"{l1}|{l2}"
        n2 = n1["children"].get(k2)
        if not n2:
            n2 = new_node(k2, l2, 2); n1["children"][k2] = n2
        _add_row(n2["_agg"][wk], r)
        # L3
        k3 = f"{l1}|{l2}|{l3}"
        n3 = n2["children"].get(k3)
        if not n3:
            n3 = new_node(k3, l3, 3); n2["children"][k3] = n3
        _add_row(n3["_agg"][wk], r)
        # Item
        k4 = f"{l1}|{l2}|{l3}|{art}"
        n4 = n3["children"].get(k4)
        if not n4:
            n4 = new_node(k4, art, 4)
            n4["leaf_info"] = {
                "seller_article": art,
                "sku_ozon": r.get("sku_ozon"),
                "status": st,
                "manager": r.get("manager"),
            }
            n3["children"][k4] = n4
        # Имя листа: самое длинное непустое наименование среди строк артикула
        # (так прод выбирает вариант, включая пометку "Уцененный товар").
        nm = (r.get("item_name") or "").strip()
        if nm:
            cur_nm = n4["name"]
            if cur_nm == art or len(nm) > len(cur_nm):
                n4["name"] = nm
        if not n4["leaf_info"].get("sku_ozon") and r.get("sku_ozon"):
            n4["leaf_info"]["sku_ozon"] = r.get("sku_ozon")
        # ABC-класс товара за эту неделю (может меняться по неделям)
        abc = r.get("abc_revenue")
        if abc:
            n4["_abc"][wk] = abc
        _add_row(n4["_agg"][wk], r)

    # 4) Преобразование во вложенный список с посчитанными ячейками
    def finalize(node):
        cells = {wk: _compute_cell(node["_agg"][wk], mp) for wk in week_keys}
        # Недельный план продаж (синий, нежирный на фронте) — на всех уровнях,
        # т.к. plan_map заполнен по всем ключам узлов (корень + группы + товар).
        node_plan = plan_map.get(node["key"], {}) or {}
        for wk in week_keys:
            pv = node_plan.get(wk, 0.0)
            cells[wk]["p_plan_qty"] = (round(pv, 1) if pv else None)
        # Для листьев-товаров добавляем ABC-класс по выручке в ячейку недели —
        # нужен для фронтовой метрики «Доля A-товаров» у групп.
        if node["level"] == 4:
            for wk in week_keys:
                cells[wk]["abc"] = node["_abc"].get(wk)
        children = [finalize(c) for c in node["children"].values()]
        # Сортировка детей — единые правила (app/util.py):
        #  - L1 — фиксированный список (ECOM, затем ТД «АВТОПРОФИ»);
        #  - L2/L3 — по алфавиту;
        #  - L4 — по артикулу продавца. Метрика не участвует.
        if children:
            if all(c.get("level") == 4 for c in children):
                children.sort(
                    key=lambda c: ((c.get("leaf_info") or {}).get("seller_article") or "")
                )
            elif node["level"] == 0:
                children.sort(key=lambda c: l1_sort_key(c["name"]))
            else:
                children.sort(key=lambda c: cat_sort_key(c["name"]))
        return {
            "key": node["key"], "name": node["name"], "level": node["level"],
            "cells": cells, "leaf_info": node["leaf_info"],
            "children": children,
        }

    # Расходы без разбивки по артикулам (строки row_type='Общие удержания')
    # отображаются ТОЛЬКО на уровне Итоги. Доля = удержания / выручка товаров.
    # noart_rows получены выше параллельно; применяем к корневому агрегату.
    for nr in noart_rows:
        wk = f"{nr['year']}-{nr['week']}"
        if wk in root["_agg"] and nr.get("holds") is not None:
            root["_agg"][wk]["noart_exp"] = _f(nr["holds"])

    # Для Wildberries прибыль узла берётся из готовой колонки wb_profit_with_ads
    # (см. _compute_cell). Строка «Общие удержания» имеет СВОЁ значение
    # wb_profit_with_ads (например −147414 за нед.24, уже включает общую
    # рекламу аккаунта) и НЕ попадает в товарные строки (row_type='Товар').
    # Чтобы Итого совпал с эталоном (Прибыль (с рекл.) Итого = Σтоваров +
    # строка удержаний), добавляем wb_profit_with_ads строки общих удержаний
    # в корневой агрегат. Для Ozon эта колонка = 0/NULL — влияния нет.
    if mp == "Wildberries":
        noart_wpa = db.query_all(
            """
            SELECT year, week, SUM(wb_profit_with_ads) AS wpa
            FROM fact_weekly
            WHERE marketplace = %s AND row_type = 'Общие удержания'
            GROUP BY year, week
            """,
            (mp,),
        )
        for nr in noart_wpa:
            wk = f"{nr['year']}-{nr['week']}"
            if wk in root["_agg"] and nr.get("wpa") is not None:
                root["_agg"][wk]["wb_profit_with_ads"] += _f(nr["wpa"])

    tree = finalize(root)

    undist_list = [{"seller_article": a, "item_name": n} for a, n in undistributed.items()]

    # Справочники для фильтров (менеджеры/статусы) — уникальные значения
    # из справочника товаров, отсортированные (как в прод-API).
    managers = set()
    statuses = set()
    def collect(n):
        li = n.get("leaf_info")
        if li:
            if li.get("manager"):
                managers.add(li["manager"])
            if li.get("status"):
                statuses.add(li["status"])
        for c in n.get("children", []):
            collect(c)
    collect(tree)

    return {
        "marketplace": mp,
        "weeks": weeks,
        "metrics": [
            {"key": k, "label": lbl, "kind": kind, "direction": direction}
            for (k, lbl, kind, direction) in METRIC_DEFS
        ],
        "product_metrics": _product_metrics_payload(),
        "tree": tree,
        "undistributed": {"count": len(undist_list), "articles": undist_list},
        "managers": sorted(managers),
        "statuses": sorted(statuses),
    }


# ---------------------------------------------------------------------------
# СВОДНАЯ вкладка РНП: одно дерево, агрегирующее ОБА маркетплейса (Ozon + WB).
# ---------------------------------------------------------------------------
# Принципы (по требованию пользователя):
#   • Рублёвые/штучные величины СКЛАДЫВАЕМ по двум МП (выручка, продажи шт,
#     заказы шт/руб, прибыль руб, удержания руб).
#   • Проценты НЕ суммируем — ПЕРЕСЧИТЫВАЕМ от общей (сводной) выручки:
#       РНП %        = (прибыль_oz + прибыль_wb) / (выручка_oz + выручка_wb)
#       Все удерж. % = (удерж_руб_oz + удерж_руб_wb) / (выручка_oz + выручка_wb)
#   • Прибыль уже посчитана нужным способом для своего МП (Ozon: profit−noart;
#     WB: wb_profit_with_ads) — в _raw.profit, поэтому просто складываем рублём.
#   • НОВАЯ метрика «План продаж, шт» — только в Сводной. Источник: sales_plan
#     (помесячно, оба МП). Недельный план = месячный_план / дней_в_месяце ×
#     дней_недели_в_этом_месяце. Реализуется ПОДНЕВНОЙ раскладкой: каждому дню
#     приписываем plan_qty_месяца / дней_в_месяце, затем суммируем дни недели
#     (period_start..period_end). Недели на границе месяцев получают долю из
#     каждого месяца автоматически. Цвет синий, шрифт нежирный (на фронте).

# Набор метрик СВОДНОЙ вкладки (порядок = порядок вывода в листе товара).
# Формат: (ключ, подпись, тип, цвет HEX, жирный, разделитель-сверху).
CROSS_METRIC_DEFS = [
    ("c_orders_qty",  "Заказы, шт.",        "qty",   "666666", True,  False),
    ("c_orders_rub",  "Заказы, руб.",       "rub",   "666666", True,  False),
    ("c_plan_qty",    "План продаж, шт",    "qty",   "0000FF", False, False),
    ("c_sales_qty",   "Продажи, шт",        "qty",   "666666", False, False),
    ("c_revenue",     "Продажи, руб",       "rub",   "666666", False, False),
    ("c_profit",      "Прибыль, руб",       "rub",   "",       True,  False),
    ("c_holds_pct",   "Все удержания, %",   "pct",   "666666", True,  True),
]


def _cross_product_metrics_payload():
    return [
        {"key": k, "label": lbl, "kind": kind, "color": color, "bold": bold, "sep": sep}
        for (k, lbl, kind, color, bold, sep) in CROSS_METRIC_DEFS
    ]


# Верхняя (переключаемая) метрика для Сводной — как в Ozon/WB РНП, но из
# сводного набора. По умолчанию РНП % (маржинальность товара).
CROSS_TOP_METRICS = [
    ("rnp",          "Маржинальность %",  "pct_node"),
    ("c_orders_qty", "Заказы, шт.",       "qty"),
    ("c_sales_qty",  "Продажи, шт",       "qty"),
    ("c_revenue",    "Продажи, руб",      "rub"),
    ("c_profit",     "Прибыль, руб",      "rub"),
    ("c_holds_pct",  "Все удержания, %",  "pct"),
]


def _collect_mp_raw(mp):
    """Построить дерево одного МП и вернуть карту узлов с сырыми суммами.

    Возвращает: (node_map, weeks)
      node_map[node_key] = {
          "name": str, "level": int, "leaf_info": dict|None,
          "raw": {wk: {revenue, sales_qty, orders_qty, orders_rub, profit, holds_rub}}
      }
    Логика построения идентична rnp_tree (товары row_type='Товар' с авто-скрытием
    статуса «-» без движений, нераспределённые в дерево групп не попадают, общие
    удержания применяются к корню). Прибыль/удержания берутся из _compute_cell(mp),
    т.е. _raw уже учитывает: Ozon profit−noart на корне, WB wb_profit_with_ads.
    """
    wk_rows = db.query_all(
        """
        SELECT DISTINCT year, week, period_text,
               MIN(period_start) AS period_start, MIN(period_end) AS period_end
        FROM fact_weekly WHERE marketplace = %s
        GROUP BY year, week, period_text ORDER BY year, week
        """,
        (mp,),
    )
    rows = db.query_all(
        """
        SELECT f.year, f.week, f.seller_article, f.sku_ozon, f.item_name,
               ci.category_l1, ci.category_l2, ci.category_l3,
               cm.status, cm.manager,
               f.orders_qty, f.orders_rub, f.returns_qty, f.holds_total,
               f.revenue, f.sales_qty, f.profit, f.cogs, f.commission, f.acquiring,
               f.logistics_total, f.logistics_direct, f.storage, f.oz_disposal, f.promo_total,
               f.wb_fines, f.wb_other_holds, f.external_exp_inc,
               f.buyout_pct, f.spp_pct, f.abc_revenue,
               f.oz_pay_per_click, f.oz_pay_per_order, f.oz_special_placement, f.wb_ads_total,
               f.wb_profit_with_ads
        FROM fact_weekly f
        LEFT JOIN catalog_items ci ON ci.seller_article = f.seller_article
        LEFT JOIN catalog_marketplace cm
               ON cm.seller_article = f.seller_article AND cm.marketplace = f.marketplace
        WHERE f.marketplace = %s AND f.row_type = 'Товар'
        ORDER BY f.id
        """,
        (mp,),
    )
    noart_rows = db.query_all(
        """
        SELECT year, week, SUM(holds_total) AS holds
        FROM fact_weekly WHERE marketplace = %s AND row_type = 'Общие удержания'
        GROUP BY year, week
        """,
        (mp,),
    )

    weeks = []
    for r in wk_rows:
        weeks.append({
            "year": r["year"], "week": r["week"], "period_text": r["period_text"],
            "period_start": r["period_start"].isoformat() if r.get("period_start") else None,
            "period_end": r["period_end"].isoformat() if r.get("period_end") else None,
            "key": f"{r['year']}-{r['week']}", "label": f"Неделя {r['week']}",
        })
    week_keys = [w["key"] for w in weeks]

    def new_node(key, name, level):
        return {"key": key, "name": name, "level": level,
                "_agg": {wk: _empty_agg() for wk in week_keys},
                "children": {}, "leaf_info": None}

    root = new_node("__root__", "Итоги", 0)

    moved = set()
    for r in rows:
        a0 = r.get("seller_article") or ""
        if not a0 or a0 in moved:
            continue
        if (_f(r.get("revenue")) or _f(r.get("sales_qty")) or _f(r.get("orders_qty"))
                or _f(r.get("returns_qty")) or _f(r.get("profit")) or _f(r.get("holds_total"))):
            moved.add(a0)

    for r in rows:
        wk = f"{r['year']}-{r['week']}"
        if wk not in root["_agg"]:
            continue
        l1, l2, l3 = r.get("category_l1"), r.get("category_l2"), r.get("category_l3")
        st, mgr = r.get("status"), r.get("manager")
        art = r.get("seller_article") or ""
        if (st or "").strip() == "-" and art and art not in moved:
            continue
        is_undist = (not l1 or not l2 or not l3 or not st or not mgr)
        _add_row(root["_agg"][wk], r)
        if is_undist:
            continue
        k1 = l1
        n1 = root["children"].get(k1) or new_node(k1, l1, 1); root["children"][k1] = n1
        _add_row(n1["_agg"][wk], r)
        k2 = f"{l1}|{l2}"
        n2 = n1["children"].get(k2) or new_node(k2, l2, 2); n1["children"][k2] = n2
        _add_row(n2["_agg"][wk], r)
        k3 = f"{l1}|{l2}|{l3}"
        n3 = n2["children"].get(k3) or new_node(k3, l3, 3); n2["children"][k3] = n3
        _add_row(n3["_agg"][wk], r)
        k4 = f"{l1}|{l2}|{l3}|{art}"
        n4 = n3["children"].get(k4)
        if not n4:
            n4 = new_node(k4, art, 4)
            n4["leaf_info"] = {"seller_article": art, "sku_ozon": r.get("sku_ozon"),
                               "status": st, "manager": r.get("manager")}
            n3["children"][k4] = n4
        nm = (r.get("item_name") or "").strip()
        if nm and (n4["name"] == art or len(nm) > len(n4["name"])):
            n4["name"] = nm
        _add_row(n4["_agg"][wk], r)

    # Общие удержания → корень.
    for nr in noart_rows:
        wk = f"{nr['year']}-{nr['week']}"
        if wk in root["_agg"] and nr.get("holds") is not None:
            root["_agg"][wk]["noart_exp"] = _f(nr["holds"])
    if mp == "Wildberries":
        for nr in db.query_all(
            """SELECT year, week, SUM(wb_profit_with_ads) AS wpa FROM fact_weekly
               WHERE marketplace = %s AND row_type = 'Общие удержания' GROUP BY year, week""",
            (mp,),
        ):
            wk = f"{nr['year']}-{nr['week']}"
            if wk in root["_agg"] and nr.get("wpa") is not None:
                root["_agg"][wk]["wb_profit_with_ads"] += _f(nr["wpa"])

    # Плоская карта узлов с сырыми суммами (через _compute_cell для корректной прибыли).
    node_map = {}

    def walk(node):
        raw = {}
        for wk in week_keys:
            cell = _compute_cell(node["_agg"][wk], mp)
            raw[wk] = cell["_raw"]
        node_map[node["key"]] = {
            "name": node["name"], "level": node["level"],
            "leaf_info": node["leaf_info"], "raw": raw,
        }
        for c in node["children"].values():
            walk(c)

    walk(root)
    return node_map, weeks


def _plan_by_node_week(weeks, marketplace=None):
    """Подневная раскладка месячного плана продаж по узлам/неделям.

    Возвращает plan_map[node_key][wk] = план_шт (float). Узлы дерева строятся
    по той же иерархии L1/L2/L3/Товар из catalog_items, ключи совпадают с
    node_map из _collect_mp_raw. Неделя получает сумму дневных долей плана за
    дни, попадающие в её интервал period_start..period_end.

    marketplace=None → суммарный план по обоим МП (как в Сводной).
    marketplace='Ozon'/'Wildberries' → план только по этому МП (Ozon/WB РНП).
    """
    import calendar, datetime

    if marketplace:
        plan_rows = db.query_all(
            """SELECT seller_article, year, month, SUM(plan_qty) AS q
               FROM sales_plan WHERE marketplace = %s
               GROUP BY seller_article, year, month""",
            (marketplace,),
        )
    else:
        plan_rows = db.query_all(
            """SELECT seller_article, year, month, SUM(plan_qty) AS q
               FROM sales_plan GROUP BY seller_article, year, month""",
        )
    cat_rows = db.query_all(
        """SELECT seller_article, category_l1, category_l2, category_l3
           FROM catalog_items""",
    )
    cat = {r["seller_article"]: r for r in cat_rows}

    # Дневная доля плана: day_plan[(art, date)] = plan_qty_месяца / дней_в_месяце.
    # Храним помесячно как (art -> {(year,month): daily}) и считаем по дате на лету.
    daily = {}  # art -> {(y,m): per_day}
    for r in plan_rows:
        art = r.get("seller_article") or ""
        y = int(r["year"] or 0); m = int(r["month"] or 0)
        if not art or not (1 <= m <= 12):
            continue
        q = _f(r.get("q"))
        if q == 0.0:
            continue
        dim = calendar.monthrange(y, m)[1]
        daily.setdefault(art, {})[(y, m)] = q / dim

    # Сумма плана по артикулу за интервал недели.
    def art_week_plan(art, ps, pe):
        per = daily.get(art)
        if not per:
            return 0.0
        total = 0.0
        d = ps
        one = datetime.timedelta(days=1)
        while d <= pe:
            v = per.get((d.year, d.month))
            if v:
                total += v
            d += one
        return total

    # Интервалы недель как объекты date.
    wk_iv = []
    for w in weeks:
        if w.get("period_start") and w.get("period_end"):
            ps = datetime.date.fromisoformat(w["period_start"])
            pe = datetime.date.fromisoformat(w["period_end"])
            wk_iv.append((w["key"], ps, pe))

    plan_map = {}

    def addp(key, wk, val):
        if val:
            d = plan_map.setdefault(key, {})
            d[wk] = d.get(wk, 0.0) + val

    for art, per in daily.items():
        ci = cat.get(art) or {}
        l1, l2, l3 = ci.get("category_l1"), ci.get("category_l2"), ci.get("category_l3")
        # Только полностью распределённые артикулы попадают в дерево групп
        # (как в дереве факта). План по нераспределённым учитываем лишь в корне.
        for (wk, ps, pe) in wk_iv:
            val = art_week_plan(art, ps, pe)
            if not val:
                continue
            addp("__root__", wk, val)
            if l1 and l2 and l3:
                addp(l1, wk, val)
                addp(f"{l1}|{l2}", wk, val)
                addp(f"{l1}|{l2}|{l3}", wk, val)
                addp(f"{l1}|{l2}|{l3}|{art}", wk, val)
    return plan_map


@router.get("/tree_cross")
def rnp_tree_cross(user=Depends(auth.get_current_user)):
    """Сводное дерево юнит-экономики: агрегация Ozon + Wildberries.

    Рублёвые/штучные суммируются по двум МП; проценты пересчитываются от общей
    выручки. Добавлена метрика «План продаж, шт» (подневная раскладка sales_plan).
    """
    oz_map, oz_weeks = _collect_mp_raw("Ozon")
    wb_map, _wb_weeks = _collect_mp_raw("Wildberries")

    # Объединённый список недель (по ключу year-week). Берём из Ozon как базу,
    # добавляем недостающие из WB; сортируем по (year, week).
    wk_index = {}
    for w in oz_weeks + _wb_weeks:
        wk_index.setdefault(w["key"], w)
    weeks = sorted(wk_index.values(), key=lambda w: (w["year"], w["week"]))
    week_keys = [w["key"] for w in weeks]

    plan_map = _plan_by_node_week(weeks)

    # Объединение узлов: ключи из обоих МП + узлы, имеющие только план.
    all_keys = set(oz_map) | set(wb_map) | set(plan_map)

    # Метаданные узла (name/level/leaf_info): берём из того МП, где узел есть.
    def node_meta(key):
        m = oz_map.get(key) or wb_map.get(key)
        if m:
            name, level, leaf_info = m["name"], m["level"], m["leaf_info"]
            # Для листа (товара) отдаём ОБА статуса по отдельности, чтобы в сводной
            # видеть расхождения OZ/WB. leaf_info копируем — нельзя мутировать
            # общий oz_map/wb_map (там лежит исходный объект).
            if level == 4 and leaf_info:
                leaf_info = dict(leaf_info)
                leaf_info["status_oz"] = ((oz_map.get(key, {}).get("leaf_info")) or {}).get("status")
                leaf_info["status_wb"] = ((wb_map.get(key, {}).get("leaf_info")) or {}).get("status")
            return name, level, leaf_info
        # узел только из плана — восстановим из ключа
        parts = key.split("|")
        if key == "__root__":
            return "Итоги", 0, None
        lvl = len(parts)
        if lvl == 4:
            return parts[-1], lvl, {"seller_article": parts[-1], "status_oz": None, "status_wb": None}
        return parts[-1], lvl, None

    def cross_cell(key, wk):
        oz = (oz_map.get(key, {}).get("raw") or {}).get(wk) or {}
        wb = (wb_map.get(key, {}).get("raw") or {}).get(wk) or {}
        rev = _f(oz.get("revenue")) + _f(wb.get("revenue"))
        qty = _f(oz.get("sales_qty")) + _f(wb.get("sales_qty"))
        orders = _f(oz.get("orders_qty")) + _f(wb.get("orders_qty"))
        orders_rub = _f(oz.get("orders_rub")) + _f(wb.get("orders_rub"))
        profit = _f(oz.get("profit")) + _f(wb.get("profit"))
        holds = _f(oz.get("holds_rub")) + _f(wb.get("holds_rub"))
        plan_q = (plan_map.get(key, {}) or {}).get(wk, 0.0)
        return {
            "rnp":          _div(profit, rev),
            "c_orders_qty": orders,
            "c_orders_rub": (orders_rub if orders_rub else None),
            "c_plan_qty":   (round(plan_q, 1) if plan_q else None),
            "c_sales_qty":  qty,
            "c_revenue":    rev,
            "c_profit":     profit,
            "c_holds_pct":  _div(holds, rev),
            "revenue":      rev,  # для сортировки/совместимости фронта
        }

    # Построение вложенной структуры. Соберём детей по родителям из all_keys.
    children_of = {}
    for key in all_keys:
        if key == "__root__":
            continue
        parts = key.split("|")
        parent = "__root__" if len(parts) == 1 else "|".join(parts[:-1])
        children_of.setdefault(parent, []).append(key)

    def build(key):
        name, level, leaf_info = node_meta(key)
        cells = {wk: cross_cell(key, wk) for wk in week_keys}
        kids = [build(c) for c in children_of.get(key, [])]
        if kids:
            # Те же единые правила порядка, что и в основном дереве.
            if all(c["level"] == 4 for c in kids):
                kids.sort(key=lambda c: ((c.get("leaf_info") or {}).get("seller_article") or ""))
            elif level == 0:
                kids.sort(key=lambda c: l1_sort_key(c["name"]))
            else:
                kids.sort(key=lambda c: cat_sort_key(c["name"]))
        return {"key": key, "name": name, "level": level,
                "cells": cells, "leaf_info": leaf_info, "children": kids}

    tree = build("__root__")

    # Справочники фильтров — объединение менеджеров/статусов обоих МП.
    managers, statuses = set(), set()
    for mp_map in (oz_map, wb_map):
        for v in mp_map.values():
            li = v.get("leaf_info")
            if li:
                if li.get("manager"):
                    managers.add(li["manager"])
                if li.get("status"):
                    statuses.add(li["status"])

    return {
        "marketplace": "cross",
        "weeks": weeks,
        "metrics": [{"key": k, "label": lbl, "kind": kind} for (k, lbl, kind) in CROSS_TOP_METRICS],
        "product_metrics": _cross_product_metrics_payload(),
        "tree": tree,
        "managers": sorted(managers),
        "statuses": sorted(statuses),
    }
