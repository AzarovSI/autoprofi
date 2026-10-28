# -*- coding: utf-8 -*-
"""Раздел «Реклама» (с 27.10.2026): отчёт по рекламе Wildberries.

Источник — файл «Дашборд - рекламная статистика» (app/wb_ads_stats_loader.py): wb_ads_campaign_daily
(кампании по дням) и wb_ads_funnel_daily (лист «Воронка» — карточка в целом); плюс catalog_items (L1–L3),
catalog_marketplace (статус, менеджер, ссылка WB) и price_model_hist (плановый ДРР WB).

Отчёт — только о рекламе (решение владельца 29.10.2026): в дереве товары со своими кампаниями за период.
Дерево: Итоги → L1 → L2 → L3 → товар → кампании товара. Товар без L1–L3 — в группе «Не распределены».
Ячейка узла — массив сумм в порядке FIELDS (доли, CR и ДРР считает фронт):
  • реклама: показы, клики, корзины, заказы, сумма заказов, расходы, отмены; linked — заказы других товаров
    через кампании («Нет, связанный заказ»); у товара — сумма по его кампаниям;
  • карточка (лист «Воронка»): f_visits, f_carts, f_orders — для CR карточки; у кампании — те же значения
    её товара;
  • доля в заказах и ДРР общий — только за дни, где загружены и реклама, и воронка карточки:
    f_orders_a / f_rub_a — карточка за такие дни, orders_f / spend_f — реклама в дни с воронкой.
Плановый ДРР — у товара, `plan`: {день или месяц: доля}; у месяца — план на последний день периода в месяце.
NULL ≠ 0: нет строк — null; 0 из файла — 0.

Эндпоинты:
  GET /api/ads/wb/tree?date_from=&date_to=&manager=&placement=
  GET /api/ads/wb/linked?campaign_id=…&campaign_id=…&date_from=&date_to=   — связанные заказы списком
"""
import datetime
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import auth, db
from ..util import canon_article

router = APIRouter(prefix="/api/ads", tags=["ads"])

MIGRATIONS = ["2026_10_28_wb_ads_stats", "2026_10_29_wb_ads_funnel"]
UNDISTRIBUTED = "Не распределены"
FIELDS = ("views", "clicks", "carts", "orders", "ad_sum", "spend", "cancels", "linked",
          "f_visits", "f_carts", "f_orders", "f_orders_a", "f_rub_a", "orders_f", "spend_f")
AD_FIELDS = ("views", "clicks", "carts", "orders", "ad_sum", "spend", "cancels")
FUNNEL_FIELDS = ("f_visits", "f_carts", "f_orders", "f_orders_a", "f_rub_a")
PLACEMENT_ORDER = {"Поиск": 0, "Поиск + рекомендации": 1, "Рекомендации": 2}
MONTHS_RU = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
             "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def migrate():
    """Миграции раздела по порядку: каждая один раз, после permissions.migrate()."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(28102026, 0)")
        for name in MIGRATIONS:
            if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (name,)):
                continue
            path = Path(__file__).resolve().parents[2] / "migrations" / (name + ".sql")
            tx.conn.execute(path.read_text(encoding="utf-8"))
            tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (name,))


def _date(value, name):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Неверная дата {name}: {value}")


def _num(v):
    return float(v) if v is not None else None


class Cell:
    """Суммы одной ячейки; поле остаётся None, пока в него ничего не добавили."""
    __slots__ = ("v",)

    def __init__(self):
        self.v = dict.fromkeys(FIELDS)

    def add(self, field, value):
        if value is not None:
            self.v[field] = (self.v[field] or 0.0) + float(value)

    def merge(self, other):
        for f, value in other.v.items():
            self.add(f, value)

    def out(self):
        return [None if x is None else round(x, 2) for x in (self.v[f] for f in FIELDS)]


class Node:
    def __init__(self, key, name, level, kind, **extra):
        self.key, self.name, self.level, self.kind = key, name, level, kind
        self.extra = extra
        self.cells = defaultdict(Cell)
        self.children = {}

    def child(self, key, name, kind, **extra):
        if key not in self.children:
            self.children[key] = Node(key, name, self.level + 1, kind, **extra)
        return self.children[key]

    def spend(self):
        return sum(c.v["spend"] or 0 for k, c in self.cells.items() if len(k) == 7)

    def out(self):
        kids = list(self.children.values())
        if self.kind in ("root", "group"):
            kids.sort(key=lambda n: (n.name == UNDISTRIBUTED, n.name) if n.kind == "group" else (-n.spend(), n.name))
        elif self.kind == "product":
            kids.sort(key=lambda n: (PLACEMENT_ORDER.get(n.extra.get("placement"), 9), n.name))
        res = {"key": self.key, "name": self.name, "level": self.level, "kind": self.kind,
               "cells": {k: c.out() for k, c in self.cells.items()}}
        res.update(self.extra)
        if kids:
            res["children"] = [k.out() for k in kids]
        return res


def _period(date_from, date_to):
    d1 = date_to
    if d1 is None:
        row = db.query_one("SELECT max(date) AS d FROM wb_ads_campaign_daily")
        d1 = row["d"] if row and row["d"] else datetime.date.today()
    d0 = date_from or d1.replace(day=1)
    if d0 > d1:
        raise HTTPException(status_code=400, detail="Начало периода позже конца")
    return d0, d1


def _plan_on(hist, d):
    """Плановый ДРР, действующий на дату: последняя запись истории с датой ≤ d."""
    v = None
    for pd, pv in hist:
        if pd <= d:
            v = pv
    return v


@router.get("/wb/tree")
def wb_tree(date_from: Optional[str] = None, date_to: Optional[str] = None,
            manager: Optional[str] = None, placement: Optional[str] = None,
            user=Depends(auth.require_tabs("ads", "Wildberries"))):
    d0, d1 = _period(_date(date_from, "date_from"), _date(date_to, "date_to"))

    ad_rows = db.query_all(
        """SELECT date, campaign_id, seller_article, in_campaign,
                  sum(views) views, sum(clicks) clicks, sum(atbs) carts, sum(orders) orders,
                  sum(orders_sum) ad_sum, sum(spend) spend, sum(cancels_qty) cancels
           FROM wb_ads_campaign_daily
           WHERE date BETWEEN %s AND %s AND (%s::text IS NULL OR placement = %s)
           GROUP BY 1, 2, 3, 4""",
        (d0, d1, placement or None, placement or None))
    funnel_rows = db.query_all(
        "SELECT date, seller_article, sum(visits) visits, sum(carts) carts, sum(orders) orders, "
        "sum(orders_sum) orders_sum FROM wb_ads_funnel_daily WHERE date BETWEEN %s AND %s GROUP BY 1, 2", (d0, d1))
    ad_dates = {r["date"] for r in db.query_all(
        "SELECT DISTINCT date FROM wb_ads_campaign_daily WHERE date BETWEEN %s AND %s", (d0, d1))}
    placements = [r["placement"] for r in db.query_all(
        "SELECT DISTINCT placement FROM wb_ads_campaign_daily WHERE date BETWEEN %s AND %s "
        "AND placement IS NOT NULL", (d0, d1))]
    placements.sort(key=lambda p: (PLACEMENT_ORDER.get(p, 9), p))

    camp_ids = sorted({r["campaign_id"] for r in ad_rows if r["in_campaign"]})
    # Название и место — по последней выгрузке кампании.
    campaigns = {r["campaign_id"]: r for r in db.query_all(
        """SELECT DISTINCT ON (campaign_id) campaign_id, campaign_name, placement, payment_type
           FROM wb_ads_campaign_daily WHERE campaign_id = ANY(%s) AND in_campaign
           ORDER BY campaign_id, source_updated_at DESC NULLS LAST, date DESC""", (camp_ids,))} if camp_ids else {}
    cat = {canon_article(r["seller_article"]): r for r in db.query_all(
        """SELECT ci.seller_article, ci.category_l1, ci.category_l2, ci.category_l3,
                  cm.status, cm.manager, cm.product_url
           FROM catalog_items ci
           LEFT JOIN catalog_marketplace cm ON cm.seller_article = ci.seller_article AND cm.marketplace = 'Wildberries'""")}
    plans = defaultdict(list)
    for r in db.query_all("SELECT seller_article, date, value FROM price_model_hist "
                          "WHERE marketplace = 'Wildberries' AND param = 'drr_pct' AND date <= %s ORDER BY date", (d1,)):
        plans[canon_article(r["seller_article"])].append((r["date"], _num(r["value"])))

    funnel = defaultdict(dict)                         # товар → день → воронка карточки
    for r in funnel_rows:
        funnel[canon_article(r["seller_article"])][r["date"]] = r
    own = defaultdict(lambda: defaultdict(dict))      # товар → кампания → день → строка
    linked_out = defaultdict(lambda: defaultdict(float))
    for r in ad_rows:
        if r["in_campaign"]:
            own[canon_article(r["seller_article"])][r["campaign_id"]][r["date"]] = r
        else:
            linked_out[r["campaign_id"]][r["date"]] += float(r["orders"] or 0)

    arts = set(own) - {""}
    if manager:
        arts = {a for a in arts if ((cat.get(a) or {}).get("manager") or "") == manager}

    def add_funnel(cell, fd, d):
        """Воронка карточки за день в ячейку товара или кампании."""
        cell.add("f_visits", fd.get("visits"))
        cell.add("f_carts", fd.get("carts"))
        cell.add("f_orders", fd.get("orders"))
        if d in ad_dates:
            cell.add("f_orders_a", fd.get("orders"))
            cell.add("f_rub_a", fd.get("orders_sum"))

    def add_ads(cell, r, fd):
        for f in AD_FIELDS:
            cell.add(f, r[f])
        if fd.get("orders") is not None:
            cell.add("orders_f", r["orders"])
        if fd.get("orders_sum") is not None:
            cell.add("spend_f", r["spend"])

    root = Node("__root__", "Итоги", 0, "root")
    days = set()
    for art in arts:
        c = cat.get(art) or {}
        path = [c.get("category_l1"), c.get("category_l2"), c.get("category_l3")]
        node, key = root, ""
        if all((p or "").strip() for p in path):
            for p in path:
                key = f"{key}|{p}" if key else p
                node = node.child(key, p, "group")
        else:
            key = UNDISTRIBUTED
            node = node.child(key, UNDISTRIBUTED, "group")
        pkey = f"{key}|{art}"
        prod = node.child(pkey, art, "product", status=c.get("status"), manager=c.get("manager"),
                          url=c.get("product_url"))
        fdays = funnel.get(art, {})
        for cid, by_day in own[art].items():
            info = campaigns.get(cid) or {}
            camp = prod.child(f"{pkey}|c{cid}", info.get("campaign_name") or str(cid), "campaign",
                              campaign_id=cid, placement=info.get("placement"), payment_type=info.get("payment_type"))
            for d, r in by_day.items():
                days.add(d)
                fd = fdays.get(d) or {}
                add_ads(camp.cells[d.isoformat()], r, fd)
                add_ads(prod.cells[d.isoformat()], r, fd)
            for d, n in linked_out.get(cid, {}).items():
                days.add(d)
                camp.cells[d.isoformat()].add("linked", n)
                prod.cells[d.isoformat()].add("linked", n)
        for d, fd in fdays.items():
            if d not in ad_dates:
                continue                               # день без загруженной рекламы — не колонка отчёта
            days.add(d)
            add_funnel(prod.cells[d.isoformat()], fd, d)
            for camp in prod.children.values():
                add_funnel(camp.cells[d.isoformat()], fd, d)
        hist = plans.get(art)
        if hist:
            prod.extra["plan"] = {}

    # Месяцы — суммы дней; группы и «Итоги» — суммы товаров (кампании не складываются: они уже в товаре).
    def roll(node):
        for k, cell in list(node.cells.items()):
            if len(k) == 10:
                node.cells[k[:7]].merge(cell)
        for ch in node.children.values():
            roll(ch)
            if ch.kind != "campaign":
                for k, cell in ch.cells.items():
                    node.cells[k].merge(cell)
    for ch in root.children.values():
        roll(ch)
        for k, cell in ch.cells.items():
            root.cells[k].merge(cell)

    months = []
    for d in sorted(days):
        mk = d.strftime("%Y-%m")
        if not months or months[-1]["key"] != mk:
            months.append({"key": mk, "label": f"{MONTHS_RU[d.month - 1]} {d.year}", "days": []})
        months[-1]["days"].append({"key": d.isoformat(), "label": d.strftime("%d")})

    # Плановый ДРР товара: по дням — действующий на день, по месяцу — на последний день месяца в отчёте.
    def fill_plans(node):
        if node.kind == "product" and "plan" in node.extra:
            hist = plans[node.name]
            for m in months:
                for dd in m["days"]:
                    node.extra["plan"][dd["key"]] = _plan_on(hist, datetime.date.fromisoformat(dd["key"]))
                node.extra["plan"][m["key"]] = _plan_on(hist, datetime.date.fromisoformat(m["days"][-1]["key"]))
        for ch in node.children.values():
            fill_plans(ch)
    fill_plans(root)

    return {
        "date_from": d0.isoformat(), "date_to": d1.isoformat(),
        "fields": list(FIELDS), "months": months, "placements": placements,
        "managers": sorted({(cat.get(a) or {}).get("manager") for a in own} - {None, ""}),
        "tree": root.out(),
    }


@router.get("/wb/linked")
def wb_linked(campaign_id: List[int] = Query(...), date_from: str = Query(...), date_to: str = Query(...),
              user=Depends(auth.require_tabs("ads", "Wildberries"))):
    """Связанные заказы кампаний за период: какие товары заказали через их рекламу (без затрат)."""
    d0, d1 = _date(date_from, "date_from"), _date(date_to, "date_to")
    rows = db.query_all(
        """SELECT seller_article, max(item_name) AS item_name, sum(orders) AS orders, sum(orders_sum) AS orders_sum
           FROM wb_ads_campaign_daily
           WHERE NOT in_campaign AND campaign_id = ANY(%s) AND date BETWEEN %s AND %s
           GROUP BY seller_article HAVING sum(orders) > 0
           ORDER BY sum(orders) DESC, seller_article""", (campaign_id[:50], d0, d1))
    return {"items": [{"seller_article": r["seller_article"], "item_name": r["item_name"],
                       "orders": _num(r["orders"]), "orders_sum": _num(r["orders_sum"])} for r in rows]}
