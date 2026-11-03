# -*- coding: utf-8 -*-
"""Раздел «P&L» (с 03.11.2026): помесячные прибыли и убытки по ОЗОН, WB и сводная.

Данные — помесячные «Итоги» юнит-экономики (`rnp_router.build_tree`, period=month): расчёт тот же,
что в помесячной юнит-экономике, без товаров и групп; подача — во фронтенде (`static/js/pnl.js`).
Вкладки: «Сводная» (№128, нужны обе площадки роли), «ОЗОН» (№129), «Wildberries» (№130).
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import auth
from . import rnp_router

router = APIRouter(prefix="/api/pnl", tags=["pnl"])

# Вкладка → площадки в ответе и их имена в БД.
_TABS = {"cross": ("ozon", "vb"), "ozon": ("ozon",), "wb": ("vb",)}
_MP = {"ozon": "Ozon", "vb": "Wildberries"}


def _pct(c, key):
    v = c.get(key)
    return None if v is None else float(v)


def month_totals(keys):
    """Помесячные «Итоги» площадок `keys` ('ozon' | 'vb'): {"mp": {key: {"ГГГГ-ММ": {...}}}, "partial": {key: {...}}}.

    Рубли — продажи, к оплате, прибыль; остальное — доли от продаж месяца: удержания площадки по товарам (mp_holds)
    и их состав, удержания без разбивки (noart, строка общих удержаний), продвижение (ads), себестоимость (cogs).
    Месяц без продаж не отдаётся; partial — «факт по» неполного месяца из журнала загрузок."""
    out, partial = {}, {}
    for key in keys:
        tree = rnp_router.build_tree(_MP[key], monthly=True)
        cells = tree["tree"]["cells"]
        out[key], partial[key] = {}, {}
        for w in tree["weeks"]:
            c = cells.get(w["key"]) or {}
            if not c.get("revenue"):
                continue
            ym = "%d-%02d" % (int(w["year"]), int(w["week"]))
            out[key][ym] = {
                "rev": float(c["revenue"]),
                "profit": c.get("profit"),
                "payout": c.get("p_payout"),
                "mp_holds": _pct(c, "p_mp_holds_pct"),
                "noart": _pct(c, "noart_pay_pct"),
                "commission": _pct(c, "p_commission_pct"),
                "logistics": _pct(c, "p_logistics_pct"),
                "acquiring": _pct(c, "p_acquiring_pct"),
                "storage": _pct(c, "p_storage_pct"),
                "acceptance": _pct(c, "p_acceptance_pct"),
                "fines": _pct(c, "p_fines_pct"),
                "ads": _pct(c, "p_ads_paid_pct"),
                "cogs": _pct(c, "p_cogs_pct"),
            }
            if w.get("partial_through"):
                partial[key][ym] = w["partial_through"]
    return {"mp": out, "partial": partial}


@router.get("/data")
def pnl_data(marketplace: str = Query("cross"), user=Depends(auth.require("pnl.open"))):
    """Помесячные итоги для вкладки `marketplace` ('cross' | 'ozon' | 'wb'):
    {"mp": {"ozon"|"vb": {"ГГГГ-ММ": {...}}}, "partial": {...}}. Без права вкладки или площадки роли — 403."""
    tab = (marketplace or "").strip().lower()
    if tab not in _TABS:
        raise HTTPException(status_code=400, detail="Неизвестная вкладка P&L: " + (marketplace or ""))
    keys = _TABS[tab]
    auth.check(user, "pnl.tab." + tab)
    auth.check_marketplace(user, *[_MP[k] for k in keys])
    return month_totals(keys)
