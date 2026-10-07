# -*- coding: utf-8 -*-
"""РНП заказы Ozon → фильтр аномалий (отклонений от нормы).

Считает по каждому товару (SKU) метрики за ТЕКУЩИЙ и ПРЕДЫДУЩИЙ период и
применяет включённые правила из таблицы anomaly_rules. Возвращает карту
{seller_article -> [сработавшие rule_key]} + сводку.

Уровень — только товар (SKU). Категории на фронте служат контейнерами.

Периоды сравнения (base):
  • week      — «неделя-к-неделе»: текущий = последняя ISO-неделя с данными,
                предыдущий = неделя до неё.
  • rolling7  — «скользящие 7 дней»: текущий = последние 7 дней с данными,
                предыдущий = 7 дней до них.
  • custom    — произвольный: cur_from..cur_to, а предыдущий период той же
                длины непосредственно перед cur_from.

Типы правил (kind в anomaly_rules):
  • rel      — относительный: сравнение среднедневного значения периодов, порог %
               direction: down|up|both|dev
  • abs      — абсолютный порог по snapshot текущего периода (lt|gt)
  • cover    — покрытие остатка в днях = остаток / среднедневные продажи (7 дн),
               срабатывает если покрытие < threshold (0 остаток → 0 дней → сработает)
  • forecast — прогноз выполнения плана: срабатывает если < threshold ИЛИ > threshold2
  • filter   — срез «без рекламы» (нет ads_expense за текущий период)
"""
import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db, auth

router = APIRouter(prefix="/api/rnp_sales", tags=["rnp_anomalies"])

MP_MAP = {"ozon": "Ozon", "wildberries": "Wildberries", "wb": "Wildberries"}


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- правила ----
def _load_rules():
    rows = db.query_all(
        """SELECT rule_key,label,kind,direction,threshold,threshold2,
                  enabled,ads_only,sort_order
           FROM anomaly_rules ORDER BY sort_order"""
    )
    out = []
    for r in rows:
        out.append({
            "rule_key": r["rule_key"], "label": r["label"], "kind": r["kind"],
            "direction": r["direction"],
            "threshold": (None if r["threshold"] is None else float(r["threshold"])),
            "threshold2": (None if r["threshold2"] is None else float(r["threshold2"])),
            "enabled": bool(r["enabled"]), "ads_only": bool(r["ads_only"]),
            "sort_order": r["sort_order"],
        })
    return out


@router.get("/anomaly_rules")
def get_rules(user=Depends(auth.get_current_user)):
    return {"rules": _load_rules()}


class RulePatch(BaseModel):
    rule_key: str
    threshold: float | None = None
    threshold2: float | None = None
    enabled: bool | None = None


class RulesUpdate(BaseModel):
    rules: list[RulePatch]


@router.put("/anomaly_rules")
def update_rules(payload: RulesUpdate, user=Depends(auth.get_current_user)):
    for p in payload.rules:
        sets, params = [], []
        if p.threshold is not None:
            sets.append("threshold=%s"); params.append(p.threshold)
        if p.threshold2 is not None:
            sets.append("threshold2=%s"); params.append(p.threshold2)
        if p.enabled is not None:
            sets.append("enabled=%s"); params.append(p.enabled)
        if not sets:
            continue
        sets.append("updated_at=now()")
        params.append(p.rule_key)
        db.execute(f"UPDATE anomaly_rules SET {', '.join(sets)} WHERE rule_key=%s",
                   tuple(params))
    return {"ok": True, "rules": _load_rules()}


# --------------------------------------------------------------- периоды ----
def _resolve_periods(base, cur_from, cur_to, all_days):
    """Вернуть (cur_start, cur_end, prev_start, prev_end) как date или None.

    all_days — отсортированный список реальных дат в данных.
    """
    if not all_days:
        return None
    dmin, dmax = all_days[0], all_days[-1]

    def _pd(s):
        try:
            return datetime.date.fromisoformat(s.strip()) if s else None
        except (ValueError, AttributeError):
            return None

    if base == "custom":
        cf, ct = _pd(cur_from), _pd(cur_to)
        if not cf or not ct or cf > ct:
            return None
        length = (ct - cf).days + 1
        pe = cf - datetime.timedelta(days=1)
        ps = pe - datetime.timedelta(days=length - 1)
        return (cf, ct, ps, pe)

    if base == "week":
        # Текущая = ISO-неделя, содержащая dmax; предыдущая — на 7 дней раньше.
        iso = dmax.isoweekday()  # 1..7
        cur_start = dmax - datetime.timedelta(days=iso - 1)
        cur_end = cur_start + datetime.timedelta(days=6)
        prev_start = cur_start - datetime.timedelta(days=7)
        prev_end = cur_start - datetime.timedelta(days=1)
        return (cur_start, cur_end, prev_start, prev_end)

    # rolling7 (default)
    cur_end = dmax
    cur_start = cur_end - datetime.timedelta(days=6)
    prev_end = cur_start - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=6)
    return (cur_start, cur_end, prev_start, prev_end)


# --------------------------------------------------------------- расчёт ----
# Относительные метрики: среднедневное по значениям в периоде.
REL_SUM = {"orders_qty", "cancels_qty", "card_visits"}         # среднедневная сумма
REL_AVG = {"cr_cart_pct", "cr_order_pct", "avg_position",       # среднее по дням>0
           "ctr_pct", "spp_pct"}
# ДРР относительный считается из сумм: ads/orders_rub за период.


def _period_metrics(rows, start, end):
    """Агрегация по артикулу за период [start,end].

    Возвращает dict art -> {
        sum_orders_qty, sum_cancels_qty, sum_card_visits, sum_orders_rub,
        sum_ads, days_orders (число дней с orders_qty>0 для среднедневной),
        avg_* (среднее по дням>0), n_days (число дней в периоде с любыми данными)
    }
    """
    acc = {}
    for r in rows:
        d = r["date"]
        if d < start or d > end:
            continue
        a = (r.get("seller_article") or "").upper()
        if not a:
            continue
        e = acc.get(a)
        if e is None:
            e = {"sum_orders_qty": 0.0, "sum_cancels_qty": 0.0,
                 "sum_card_visits": 0.0, "sum_orders_rub": 0.0, "sum_ads": 0.0,
                 "avg": {k: [0.0, 0] for k in REL_AVG}, "days": set()}
            acc[a] = e
        e["days"].add(d)
        e["sum_orders_qty"] += _f(r.get("orders_qty"))
        e["sum_cancels_qty"] += _f(r.get("cancels_qty"))
        e["sum_card_visits"] += _f(r.get("card_visits"))
        e["sum_orders_rub"] += _f(r.get("orders_rub"))
        e["sum_ads"] += _f(r.get("ads_expense_rub"))
        for k in REL_AVG:
            v = r.get(k)
            if v is not None and _f(v) > 0:
                e["avg"][k][0] += _f(v)
                e["avg"][k][1] += 1
    # финализация
    ndays = (end - start).days + 1
    for a, e in acc.items():
        e["n_days"] = ndays
        e["avg_daily"] = {
            "orders_qty": e["sum_orders_qty"] / ndays if ndays else 0.0,
            "cancels_qty": e["sum_cancels_qty"] / ndays if ndays else 0.0,
            "card_visits": e["sum_card_visits"] / ndays if ndays else 0.0,
        }
        e["drr"] = (e["sum_ads"] / e["sum_orders_rub"]) if e["sum_orders_rub"] > 0 else None
        e["avg_val"] = {}
        for k in REL_AVG:
            s, n = e["avg"][k]
            e["avg_val"][k] = (s / n) if n else None
    return acc


def _snapshot_latest(rows, start, end):
    """Последнее значение snapshot-полей за период (rating, отзывы, остатки)."""
    latest = {}  # art -> (date, dict)
    for r in rows:
        d = r["date"]
        if d < start or d > end:
            continue
        a = (r.get("seller_article") or "").upper()
        if not a:
            continue
        cur = latest.get(a)
        if cur is None or d >= cur[0]:
            latest[a] = (d, {
                "rating": r.get("rating"),
                "reviews": r.get("delivery_time_hours"),
                "stock_ozon_qty": r.get("stock_ozon_qty"),
                "stock_ap_qty": r.get("stock_ap_qty"),
                "price_index_pi": r.get("price_index_pi"),
            })
    return {a: v[1] for a, v in latest.items()}


@router.get("/anomalies")
def anomalies(
    marketplace: str = Query("ozon"),
    base: str = Query("rolling7"),          # week | rolling7 | custom
    cur_from: str = Query(None),
    cur_to: str = Query(None),
    rules: str = Query(""),                 # csv из rule_key; пусто = все включённые
    user=Depends(auth.get_current_user),
):
    mp = MP_MAP.get((marketplace or "").lower(), "Ozon")
    all_rules = {r["rule_key"]: r for r in _load_rules()}

    # какие правила применяем
    sel = [k for k in (rules or "").split(",") if k.strip()]
    if sel:
        active = [all_rules[k] for k in sel if k in all_rules and all_rules[k]["enabled"]]
    else:
        active = [r for r in all_rules.values() if r["enabled"]]

    # все дневные строки Ozon (по товару)
    rows = db.query_all(
        """SELECT ods.date, ods.seller_article,
                  ods.orders_qty, ods.orders_rub, ods.cancels_qty, ods.card_visits,
                  ods.ads_expense_rub, ods.spp_pct,
                  ods.cr_cart_pct, ods.cr_order_pct, ods.avg_position, ods.ctr_pct,
                  ods.delivery_time_hours, ods.price_index_pi,
                  ods.rating, ods.stock_ozon_qty, ods.stock_ap_qty
           FROM ozon_daily_sales ods
           ORDER BY ods.date"""
    )
    all_days = sorted({r["date"] for r in rows})
    per = _resolve_periods(base, cur_from, cur_to, all_days)
    if per is None:
        return {"periods": None, "hits": {}, "counts": {}, "total": 0,
                "rel_available": False, "note": "Нет данных для выбранного периода."}
    cur_s, cur_e, prev_s, prev_e = per

    cur_m = _period_metrics(rows, cur_s, cur_e)
    prev_m = _period_metrics(rows, prev_s, prev_e)
    cur_snap = _snapshot_latest(rows, cur_s, cur_e)
    # есть ли реальные данные в предыдущем периоде (для относительных правил)
    rel_available = any(prev_s <= d <= prev_e for d in all_days)

    # средние продажи за последние 7 дней (для покрытия остатков) — от cur_e назад
    cover_start = cur_e - datetime.timedelta(days=6)
    cover_m = _period_metrics(rows, cover_start, cur_e)

    # план (для forecast) — переиспользуем месячную логику упрощённо:
    # прогноз = факт за текущий период, экстраполированный, / план. Но план
    # помесячный. Для аномалий берём готовый прогноз из дерева — здесь считаем
    # по тому же принципу, что rnp_sales_router.node_forecast, но помесячно
    # сложно. Упростим: forecast-правило работает на месячном прогнозе,
    # вычисленном в отдельном запросе.
    plan_forecast = _forecast_by_art(rows, mp) if any(
        r["kind"] == "forecast" for r in active) else {}

    hits = {}       # art -> [rule_key,...]
    counts = {}     # rule_key -> число сработавших SKU

    def _add(art, rk):
        hits.setdefault(art, [])
        if rk not in hits[art]:
            hits[art].append(rk)
        counts[rk] = counts.get(rk, 0) + 1

    # множество всех артикулов текущего периода
    arts = set(cur_m.keys()) | set(cur_snap.keys())

    for art in arts:
        cm = cur_m.get(art)
        pm = prev_m.get(art)
        sn = cur_snap.get(art, {})
        for r in active:
            rk = r["kind"]; key = r["rule_key"]; thr = r["threshold"]; thr2 = r["threshold2"]
            direction = r["direction"]

            # реклама-only: пропускаем товары без рекламы
            if r["ads_only"]:
                ads = (cm["sum_ads"] if cm else 0.0)
                if not ads or ads <= 0:
                    continue

            if rk == "rel":
                if not rel_available or cm is None or pm is None:
                    continue
                cur_v, prev_v = _rel_values(key, cm, pm)
                if cur_v is None or prev_v is None or prev_v == 0:
                    continue
                change = (cur_v - prev_v) / abs(prev_v) * 100.0
                if direction == "down" and change <= -thr:
                    _add(art, key)
                elif direction == "up" and change >= thr:
                    _add(art, key)
                elif direction == "both" and abs(change) >= thr:
                    _add(art, key)
                elif direction == "dev" and abs(change) >= thr:
                    _add(art, key)

            elif rk == "abs":
                val = None
                if key == "rating": val = _num(sn.get("rating"))
                elif key == "reviews": val = _num(sn.get("reviews"))
                elif key == "price_index_pi": val = _num(sn.get("price_index_pi"))
                if val is None:
                    continue
                if direction == "lt" and val < thr:
                    _add(art, key)
                elif direction == "gt" and val > thr:
                    _add(art, key)

            elif rk == "cover":
                stock = _num(sn.get("stock_ozon_qty" if key == "stock_ozon_cover" else "stock_ap_qty"))
                if stock is None:
                    continue
                cov = cover_m.get(art)
                avg_day = cov["avg_daily"]["orders_qty"] if cov else 0.0
                if avg_day <= 0:
                    # нет продаж → покрытие бесконечно, но остаток 0 всё равно проблема
                    if stock <= 0:
                        _add(art, key)
                    continue
                cover_days = stock / avg_day
                if cover_days < thr:
                    _add(art, key)

            elif rk == "forecast":
                fc = plan_forecast.get(art)   # доля (1.0 = 100%)
                if fc is None:
                    continue
                pct = fc * 100.0
                if pct < thr or (thr2 is not None and pct > thr2):
                    _add(art, key)

            elif rk == "filter" and key == "no_ads":
                ads = (cm["sum_ads"] if cm else 0.0)
                if not ads or ads <= 0:
                    _add(art, key)

    return {
        "periods": {
            "base": base,
            "cur": [cur_s.isoformat(), cur_e.isoformat()],
            "prev": [prev_s.isoformat(), prev_e.isoformat()],
        },
        "rel_available": rel_available,
        "hits": hits,
        "counts": counts,
        "total": len(hits),
        "note": ("" if rel_available else
                 "Нет предыдущего периода — относительные правила не применяются."),
    }


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rel_values(key, cm, pm):
    """Вернуть (cur, prev) сопоставимые значения для относительного правила."""
    if key in ("orders_qty", "cancels_qty", "card_visits"):
        return (cm["avg_daily"][key], pm["avg_daily"][key])
    if key == "drr_total_pct":
        return (cm["drr"], pm["drr"])
    if key in REL_AVG:
        return (cm["avg_val"].get(key), pm["avg_val"].get(key))
    return (None, None)


def _forecast_by_art(rows, mp):
    """Прогноз выполнения плана по артикулу (доля) — по последнему месяцу данных.

    Повторяет логику rnp_sales_router.node_forecast на уровне артикула:
    projected = факт/passed*dim; forecast = projected/plan.
    """
    import calendar
    # Реальная дата по Москве (синхронно с rnp_sales_router — была захардкожена,
    # из-за чего прогноз выполнения завышался).
    _MSK = datetime.timezone(datetime.timedelta(hours=3))
    TODAY = datetime.datetime.now(_MSK).date()
    # факт заказов по (art, year, month) + множество дней с данными по месяцу
    fact = {}
    months_seen = set()
    month_days = {}  # (y,m) -> set дат с данными
    for r in rows:
        d = r["date"]
        a = (r.get("seller_article") or "").upper()
        if not a:
            continue
        ym = (d.year, d.month)
        months_seen.add(ym)
        month_days.setdefault(ym, set()).add(d)
        fact[(a, ym)] = fact.get((a, ym), 0.0) + _f(r.get("orders_qty"))
    if not months_seen:
        return {}
    last_ym = max(months_seen)
    y, m = last_ym
    # план
    plan_rows = db.query_all(
        """SELECT seller_article, year, month, SUM(plan_qty) AS q
           FROM sales_plan WHERE marketplace=%s GROUP BY seller_article,year,month""",
        (mp,),
    )
    plan = {}
    for r in plan_rows:
        a = (r.get("seller_article") or "").upper()
        yy = int(r["year"] or 0); mm = int(r["month"] or 0)
        if a and 1 <= mm <= 12:
            plan[(a, (yy, mm))] = _f(r.get("q"))
    dim = calendar.monthrange(y, m)[1]
    # Текущий месяц → число дней с фактическими данными (не календарное
    # TODAY.day), закрытый → dim (фактическое выполнение), будущий → 0.
    if (y, m) < (TODAY.year, TODAY.month):
        passed = dim
    elif (y, m) == (TODAY.year, TODAY.month):
        passed = len(month_days.get(last_ym, ()))
    else:
        passed = 0
    out = {}
    for (a, ym), fq in fact.items():
        if ym != last_ym:
            continue
        pv = plan.get((a, ym))
        if not pv or passed <= 0:
            continue
        projected = fq if passed >= dim else (fq / passed * dim)
        out[a] = projected / pv if pv else None
    return out
