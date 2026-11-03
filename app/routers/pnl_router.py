# -*- coding: utf-8 -*-
"""Раздел «P&L» (с 03.11.2026): помесячные прибыли и убытки по ОЗОН, WB и сводная.

Данные — помесячные «Итоги» юнит-экономики (`abc_router.month_totals` → `rnp_router.build_tree`,
period=month), расчёт и порядок строк те же; подача — во фронтенде (`static/js/pnl.js`).
Вкладки: «Сводная» (№128, нужны обе площадки роли), «ОЗОН» (№129), «Wildberries» (№130).
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import auth
from .abc_router import month_totals

router = APIRouter(prefix="/api/pnl", tags=["pnl"])

# Вкладка → площадки в ответе (ключи как в /api/abc/summary) и их имена в БД.
_TABS = {"cross": ("ozon", "vb"), "ozon": ("ozon",), "wb": ("vb",)}
_MP = {"ozon": "Ozon", "vb": "Wildberries"}


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
