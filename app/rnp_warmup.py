# -*- coding: utf-8 -*-
"""Фоновый прогрев кэша дерева «РНП заказы» (Ozon, Wildberries, Яндекс).

Зачем: сборка дерева на сервере — ~4 с (Ozon), и её ждал первый открывший РНП после
перезапуска, каждой загрузки отчёта и истечения TTL кэша (замер 11.10.2026,
docs/history.md). Прогрев считает деревья на период по умолчанию заранее, в отдельном
потоке, через тот же эндпоинт — ключ кэша совпадает с тем, что запросит браузер.

Когда: сразу после старта; через PAUSE секунд после bump() пространств имён РНП
(загрузка отчёта, правка справочника); и раз в INTERVAL секунд — проверка (если дерево
в кэше, это один лёгкий запрос ревизии цен). Другие периоды и фильтры не прогреваются.

Включается переменной окружения RNP_WARMUP=1 (сервер, /etc/autoprofi/env). В тестах
не задана — поток не стартует и не мешает TRUNCATE.
"""
import datetime
import logging
import os
import threading
import time

from . import cache
from .routers import rnp_sales_router as rs

log = logging.getLogger("rnp_warmup")

MARKETPLACES = ("ozon", "wb", "yandex")
INTERVAL = 60   # сек между проверками
PAUSE = 3       # сек после bump: дать загрузке закончить пересчёты (СПП, Pi)

_wake = threading.Event()
_started = False


def default_period(today):
    """Период РНП по умолчанию, как во фронте (static/js/views.js, rnpSales):
    с 1-го числа позапрошлого месяца по последний день текущего."""
    y, m = today.year, today.month - 2
    if m < 1:
        y, m = y - 1, m + 12
    start = datetime.date(y, m, 1)
    nxt = datetime.date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return start.isoformat(), (nxt - datetime.timedelta(days=1)).isoformat()


def warm_once():
    d_from, d_to = default_period(rs._today())
    for mp in MARKETPLACES:
        try:
            rs.rnp_sales_tree(marketplace=mp, date_from=d_from, date_to=d_to,
                              status=None, manager=None, user=None)
        except Exception:
            log.exception("прогрев РНП %s не удался", mp)


def _loop():
    while True:
        _wake.clear()
        warm_once()
        if _wake.wait(INTERVAL):
            time.sleep(PAUSE)


def _on_bump(namespace):
    if namespace in (rs.CACHE_NS, rs.CACHE_NS_WB, rs.CACHE_NS_YA):
        _wake.set()


def start():
    global _started
    if _started or os.environ.get("RNP_WARMUP") != "1":
        return
    _started = True
    cache.on_bump(_on_bump)
    threading.Thread(target=_loop, name="rnp-warmup", daemon=True).start()
