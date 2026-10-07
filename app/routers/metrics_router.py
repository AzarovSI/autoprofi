# -*- coding: utf-8 -*-
"""Метрики обзорных экранов: недели, сводка, разбивки, сравнение МП."""
from fastapi import APIRouter, Depends, Query

from .. import db, auth

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

MP_MAP = {"ozon": "Ozon", "wildberries": "Wildberries", "wb": "Wildberries"}


def _f(v):
    return float(v) if v is not None else 0.0


@router.get("/weeks")
def weeks(user=Depends(auth.get_current_user)):
    """Все недели (по убыванию) по всем маркетплейсам."""
    return db.query_all(
        """
        SELECT DISTINCT year, week, period_text
        FROM fact_weekly
        GROUP BY year, week, period_text
        ORDER BY year DESC, week DESC
        """
    )


def _summary_row(mp, year, week):
    r = db.query_one(
        """
        SELECT
          SUM(CASE WHEN row_type='Товар' THEN revenue END)        AS revenue,
          SUM(CASE WHEN row_type='Товар' THEN profit END)         AS profit_items,
          SUM(CASE WHEN row_type='Товар' THEN sales_qty END)      AS sales_qty,
          SUM(CASE WHEN row_type='Товар' THEN cogs END)           AS cogs,
          SUM(CASE WHEN row_type='Товар' THEN commission END)     AS commission,
          SUM(CASE WHEN row_type='Товар' THEN logistics_total END) AS logistics,
          SUM(CASE WHEN row_type='Товар' THEN storage END)        AS storage,
          SUM(CASE WHEN row_type='Товар' THEN promo_total END)    AS promo,
          SUM(CASE WHEN row_type='Товар' THEN tax END)            AS tax,
          COUNT(*) FILTER (WHERE row_type='Товар')                AS n_items,
          -COALESCE(SUM(CASE WHEN row_type='Общие удержания' THEN profit END),0) AS holds_total
        FROM fact_weekly
        WHERE marketplace=%s AND year=%s AND week=%s
        """,
        (mp, year, week),
    )
    if not r or r.get("revenue") is None:
        return None
    rev = _f(r["revenue"]); pit = _f(r["profit_items"]); holds = _f(r["holds_total"])
    pnet = pit - holds
    return {
        "revenue": rev,
        "profit_items": round(pit, 2),
        "holds_total": round(holds, 2),
        "profit_net": round(pnet, 2),
        "margin_pct": round(pnet / rev * 100, 2) if rev else 0.0,
        "sales_qty": _f(r["sales_qty"]),
        "cogs": round(_f(r["cogs"]), 2),
        "commission": round(_f(r["commission"]), 2),
        "logistics": round(_f(r["logistics"]), 2),
        "storage": round(_f(r["storage"]), 2),
        "promo": round(_f(r["promo"]), 2),
        "tax": round(_f(r["tax"]), 2),
        "n_items": int(r["n_items"] or 0),
    }


@router.get("/summary")
def summary(marketplace: str = Query("ozon"), year: int = Query(...), week: int = Query(...),
            user=Depends(auth.get_current_user)):
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    return _summary_row(mp, year, week) or {}


@router.get("/by_category")
def by_category(marketplace: str = Query("ozon"), year: int = Query(...), week: int = Query(...),
                level: int = Query(1), user=Depends(auth.get_current_user)):
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    col = {1: "ci.category_l1", 2: "ci.category_l2", 3: "ci.category_l3"}.get(level, "ci.category_l1")
    rows = db.query_all(
        f"""
        SELECT COALESCE({col}, '(без категории)') AS category,
               SUM(f.revenue) AS revenue, SUM(f.profit) AS profit,
               SUM(f.sales_qty) AS sales_qty, COUNT(*) AS n
        FROM fact_weekly f
        LEFT JOIN catalog_items ci ON ci.seller_article=f.seller_article
        WHERE f.marketplace=%s AND f.year=%s AND f.week=%s AND f.row_type='Товар'
        GROUP BY 1
        ORDER BY revenue DESC NULLS LAST
        """,
        (mp, year, week),
    )
    for r in rows:
        r["revenue"] = _f(r["revenue"]); r["profit"] = round(_f(r["profit"]), 2)
        r["sales_qty"] = _f(r["sales_qty"]); r["n"] = int(r["n"])
    return rows


@router.get("/by_sku")
def by_sku(marketplace: str = Query("ozon"), year: int = Query(...), week: int = Query(...),
           user=Depends(auth.get_current_user)):
    mp = MP_MAP.get(marketplace.lower(), "Ozon")
    rows = db.query_all(
        """
        SELECT f.seller_article, f.sku_ozon, f.item_name,
               ci.category_l1, ci.category_l2, ci.category_l3, ci.status,
               f.revenue, f.sales_qty, f.profit, f.margin_pct, f.buyout_pct
        FROM fact_weekly f
        LEFT JOIN catalog_items ci ON ci.seller_article=f.seller_article
        WHERE f.marketplace=%s AND f.year=%s AND f.week=%s AND f.row_type='Товар'
        ORDER BY f.revenue DESC NULLS LAST
        """,
        (mp, year, week),
    )
    for r in rows:
        for k in ("revenue", "sales_qty", "profit", "margin_pct", "buyout_pct"):
            r[k] = _f(r[k])
    return rows


@router.get("/mp_compare")
def mp_compare(year: int = Query(...), week: int = Query(...), user=Depends(auth.get_current_user)):
    # 2 независимых запроса (Ozon и Wildberries) — последовательно на тёплом пуле.
    mps = ("Ozon", "Wildberries")
    summaries = [_summary_row(mp, year, week) for mp in mps]
    out = []
    for mp, s in zip(mps, summaries):
        if s:
            out.append({
                "marketplace": mp, "revenue": s["revenue"],
                "profit_net": s["profit_net"], "sales_qty": s["sales_qty"],
                "margin_pct": s["margin_pct"],
            })
    return out
