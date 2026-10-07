# -*- coding: utf-8 -*-
"""Тренды по неделям: динамика сводки, матрицы по категориям и SKU."""
from fastapi import APIRouter, Depends, Query

from .. import db, auth

router = APIRouter(prefix="/api/trend", tags=["trend"])

MP_MAP = {"ozon": "Ozon", "wildberries": "Wildberries", "wb": "Wildberries"}


def _f(v):
    return float(v) if v is not None else 0.0


def _wow(cur, prev):
    """Изменение неделя-к-неделе: delta, delta_pct, direction."""
    if prev is None:
        return None
    delta = round(cur - prev, 2)
    pct = round((cur - prev) / abs(prev) * 100, 2) if prev else None
    direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
    return {"delta": delta, "delta_pct": pct, "direction": direction}


def _weeks_for(mp):
    rows = db.query_all(
        """
        SELECT DISTINCT year, week, period_text
        FROM fact_weekly WHERE marketplace=%s
        GROUP BY year, week, period_text ORDER BY year, week
        """,
        (mp,),
    )
    return [{
        "year": r["year"], "week": r["week"],
        "label": f"{r['year']}-W{r['week']}",
        "period_text": r["period_text"],
    } for r in rows]


@router.get("/summary_series")
def summary_series(marketplace: str = Query("ozon"), user=Depends(auth.get_current_user)):
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    weeks = _weeks_for(mp)

    # ОДИН агрегирующий запрос по всем неделям (GROUP BY year, week) вместо
    # отдельного запроса на каждую неделю — на порядок быстрее (см. соседний
    # category_matrix с тем же паттерном). Расчёт WoW ниже последователен
    # (зависит от предыдущей недели), поэтому идёт после сборки строк по неделям.
    agg = db.query_all(
        """
        SELECT year, week,
          SUM(CASE WHEN row_type='Товар' THEN revenue END) AS revenue,
          SUM(CASE WHEN row_type='Товар' THEN profit END)  AS profit_items,
          SUM(CASE WHEN row_type='Товар' THEN sales_qty END) AS sales_qty,
          -COALESCE(SUM(CASE WHEN row_type='Общие удержания' THEN profit END),0) AS holds
        FROM fact_weekly WHERE marketplace=%s
        GROUP BY year, week
        """,
        (mp,),
    )
    by_wk = {(r["year"], r["week"]): r for r in agg}
    _empty = {"revenue": None, "profit_items": None, "sales_qty": None, "holds": 0}

    rows = []
    prev = {}
    for w in weeks:
        r = by_wk.get((w["year"], w["week"]), _empty)
        rev = _f(r["revenue"]); pnet = _f(r["profit_items"]) - _f(r["holds"])
        qty = _f(r["sales_qty"]); margin = round(pnet / rev * 100, 2) if rev else 0.0
        cur = {"revenue": rev, "profit_net": round(pnet, 2), "margin_pct": margin, "sales_qty": qty}
        rows.append({
            "year": w["year"], "week": w["week"], "period_text": w["period_text"],
            "label": w["label"], **cur,
            "wow": {k: _wow(cur[k], prev.get(k)) for k in ("revenue", "profit_net", "margin_pct", "sales_qty")},
        })
        prev = cur
    return {"weeks": weeks, "rows": rows}


@router.get("/category_matrix")
def category_matrix(marketplace: str = Query("ozon"), metric: str = Query("revenue"),
                    user=Depends(auth.get_current_user)):
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    metric = metric if metric in ("revenue", "profit", "sales_qty") else "revenue"
    weeks = _weeks_for(mp)
    wkidx = {(w["year"], w["week"]): i for i, w in enumerate(weeks)}
    rows = db.query_all(
        f"""
        SELECT COALESCE(ci.category_l3,'(без категории)') AS category,
               f.year, f.week, SUM(f.{metric}) AS val
        FROM fact_weekly f
        LEFT JOIN catalog_items ci ON ci.seller_article=f.seller_article
        WHERE f.marketplace=%s AND f.row_type='Товар'
        GROUP BY 1, f.year, f.week
        """,
        (mp,),
    )
    cat = {}
    for r in rows:
        c = cat.setdefault(r["category"], [0.0] * len(weeks))
        idx = wkidx.get((r["year"], r["week"]))
        if idx is not None:
            c[idx] = _f(r["val"])
    out = []
    for category, cells in cat.items():
        total = round(sum(cells), 2)
        prev = cells[-2] if len(cells) >= 2 else None
        out.append({"category": category, "cells": [round(x, 2) for x in cells],
                    "total": total, "wow": _wow(cells[-1], prev) if len(cells) >= 2 else None})
    out.sort(key=lambda x: x["total"], reverse=True)
    return {"weeks": weeks, "metric": metric, "rows": out}


@router.get("/sku_matrix")
def sku_matrix(marketplace: str = Query("ozon"), metric: str = Query("revenue"),
               user=Depends(auth.get_current_user)):
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    metric = metric if metric in ("revenue", "profit", "sales_qty") else "revenue"
    weeks = _weeks_for(mp)
    wkidx = {(w["year"], w["week"]): i for i, w in enumerate(weeks)}
    rows = db.query_all(
        f"""
        SELECT f.seller_article, MAX(f.item_name) AS item_name,
               MAX(ci.category_l3) AS category_l3,
               f.year, f.week, SUM(f.{metric}) AS val
        FROM fact_weekly f
        LEFT JOIN catalog_items ci ON ci.seller_article=f.seller_article
        WHERE f.marketplace=%s AND f.row_type='Товар'
        GROUP BY f.seller_article, f.year, f.week
        """,
        (mp,),
    )
    sku = {}
    for r in rows:
        s = sku.setdefault(r["seller_article"], {
            "seller_article": r["seller_article"], "item_name": r["item_name"],
            "marketplace": mp, "category_l3": r["category_l3"], "cells": [0.0] * len(weeks),
        })
        if r["item_name"]:
            s["item_name"] = r["item_name"]
        if r["category_l3"]:
            s["category_l3"] = r["category_l3"]
        idx = wkidx.get((r["year"], r["week"]))
        if idx is not None:
            s["cells"][idx] = _f(r["val"])
    out = []
    for s in sku.values():
        cells = s["cells"]; total = round(sum(cells), 2)
        prev = cells[-2] if len(cells) >= 2 else None
        s["cells"] = [round(x, 2) for x in cells]
        s["total"] = total
        s["wow"] = _wow(cells[-1], prev) if len(cells) >= 2 else None
        out.append(s)
    out.sort(key=lambda x: x["total"], reverse=True)
    return {"weeks": weeks, "metric": metric, "rows": out}
