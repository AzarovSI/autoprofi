# -*- coding: utf-8 -*-
"""Раздел «Реклама» (с 27.10.2026): отчёт по рекламе Wildberries.

Источники: wb_ads_campaign_daily (файл «Дашборд - рекламная статистика», app/wb_ads_stats_loader.py),
wb_daily_sales (переходы и заказы РНП WB), catalog_items (L1–L3), catalog_marketplace (статус,
менеджер, ссылка WB).

Дерево: Итоги → L1 → L2 → L3 → товар → кампании товара. Товар без L1–L3 — в группе «Не распределены».
Ячейка узла — массив сумм в порядке FIELDS (доли и ДРР считает фронт). Доли рекламы считаются только
по дням, за которые статистика рекламы загружена (есть хоть одна строка за дату), и где у товара есть
знаменатель из РНП: visits_a / orders_qty_a / orders_rub_a — РНП за такие дни; clicks_v — клики в дни
с переходами, ad_orders_q — рекламные заказы в дни с заказами РНП, spend_r — расходы в дни с суммой
заказов РНП. Иначе дни до первой загрузки рекламы занижали бы долю в итоге месяца.
NULL ≠ 0: нет строк рекламы или РНП — null; 0 из файла — 0.

Эндпоинты:
  GET /api/ads/wb/tree?date_from=&date_to=&manager=&placement=&scope=ads|all
"""
import datetime
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import auth, db
from ..util import canon_article

router = APIRouter(prefix="/api/ads", tags=["ads"])

MIGRATION = "2026_10_28_wb_ads_stats"
UNDISTRIBUTED = "Не распределены"
FIELDS = ("visits", "orders_qty", "orders_rub", "views", "clicks", "spend", "atbs", "ad_orders",
          "ad_qty", "ad_sum", "cancels", "linked", "clicks_v", "ad_orders_q", "spend_r",
          "visits_a", "orders_qty_a", "orders_rub_a")
RNP_FIELDS = ("visits", "orders_qty", "orders_rub")
AD_FIELDS = ("views", "clicks", "spend", "atbs", "ad_orders", "ad_qty", "ad_sum", "cancels")
PLACEMENT_ORDER = {"Поиск": 0, "Поиск + рекомендации": 1, "Рекомендации": 2}
MONTHS_RU = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
             "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def migrate():
    """Таблицы статистики рекламы WB и права №120–121: один раз, после permissions.migrate()."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(28102026, 0)")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        path = Path(__file__).resolve().parents[2] / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))


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


@router.get("/wb/tree")
def wb_tree(date_from: Optional[str] = None, date_to: Optional[str] = None,
            manager: Optional[str] = None, placement: Optional[str] = None,
            scope: str = Query("ads", pattern="^(ads|all)$"),
            user=Depends(auth.require_tabs("ads", "Wildberries"))):
    d0, d1 = _period(_date(date_from, "date_from"), _date(date_to, "date_to"))

    ad_rows = db.query_all(
        """SELECT date, campaign_id, seller_article, in_campaign, placement,
                  sum(views) views, sum(clicks) clicks, sum(spend) spend, sum(atbs) atbs,
                  sum(orders) ad_orders, sum(ordered_qty) ad_qty, sum(orders_sum) ad_sum,
                  sum(cancels_qty) cancels
           FROM wb_ads_campaign_daily
           WHERE date BETWEEN %s AND %s AND (%s::text IS NULL OR placement = %s)
           GROUP BY 1, 2, 3, 4, 5""",
        (d0, d1, placement or None, placement or None))
    rnp_rows = db.query_all(
        "SELECT date, seller_article, card_visits AS visits, orders_qty, orders_rub "
        "FROM wb_daily_sales WHERE date BETWEEN %s AND %s", (d0, d1))
    ad_dates = {r["date"] for r in db.query_all(
        "SELECT DISTINCT date FROM wb_ads_campaign_daily WHERE date BETWEEN %s AND %s", (d0, d1))}
    placements = [r["placement"] for r in db.query_all(
        "SELECT DISTINCT placement FROM wb_ads_campaign_daily WHERE date BETWEEN %s AND %s "
        "AND placement IS NOT NULL", (d0, d1))]
    placements.sort(key=lambda p: (PLACEMENT_ORDER.get(p, 9), p))

    camp_ids = sorted({r["campaign_id"] for r in ad_rows if r["in_campaign"]})
    # Название, место и статус — по последней выгрузке кампании (статус в файле — снимок «сейчас»).
    campaigns = {r["campaign_id"]: r for r in db.query_all(
        """SELECT DISTINCT ON (campaign_id) campaign_id, campaign_name, placement, campaign_status,
                  payment_type, source_updated_at
           FROM wb_ads_campaign_daily WHERE campaign_id = ANY(%s) AND in_campaign
           ORDER BY campaign_id, source_updated_at DESC NULLS LAST, date DESC""", (camp_ids,))} if camp_ids else {}
    cat = {canon_article(r["seller_article"]): r for r in db.query_all(
        """SELECT ci.seller_article, ci.category_l1, ci.category_l2, ci.category_l3,
                  cm.status, cm.manager, cm.product_url
           FROM catalog_items ci
           LEFT JOIN catalog_marketplace cm ON cm.seller_article = ci.seller_article AND cm.marketplace = 'Wildberries'""")}
    updated = db.query_one("SELECT max(source_updated_at) AS t FROM wb_ads_campaign_daily")

    # Свод по товару и дню.
    rnp = defaultdict(dict)
    for r in rnp_rows:
        rnp[canon_article(r["seller_article"])][r["date"]] = r
    own = defaultdict(lambda: defaultdict(dict))      # товар → кампания → день → строка
    linked_in = defaultdict(lambda: defaultdict(float))
    linked_out = defaultdict(lambda: defaultdict(float))
    for r in ad_rows:
        art = canon_article(r["seller_article"])
        if r["in_campaign"]:
            own[art][r["campaign_id"]][r["date"]] = r
        else:
            linked_in[art][r["date"]] += float(r["ad_orders"] or 0)
            linked_out[r["campaign_id"]][r["date"]] += float(r["ad_orders"] or 0)

    arts = set(own)
    if scope == "all":
        arts |= {a for a, days in rnp.items()
                 if any((d.get("visits") or 0) > 0 or (d.get("orders_qty") or 0) > 0 for d in days.values())}
        arts |= set(linked_in)
    arts.discard("")
    if manager:
        arts = {a for a in arts if ((cat.get(a) or {}).get("manager") or "") == manager}

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
                          url=c.get("product_url"), campaigns=len(own.get(art, {})))
        # РНП: переходы, заказы — и знаменатели долей рекламы.
        rnp_days = rnp.get(art, {})
        ad_by_day = defaultdict(lambda: defaultdict(float))
        for cid, by_day in own.get(art, {}).items():
            info = campaigns.get(cid) or {}
            camp = prod.child(f"{pkey}|c{cid}", info.get("campaign_name") or str(cid), "campaign",
                              campaign_id=cid, placement=info.get("placement"),
                              campaign_status=info.get("campaign_status"), payment_type=info.get("payment_type"))
            for d, r in by_day.items():
                days.add(d)
                cell = camp.cells[d.isoformat()]
                for f in AD_FIELDS:
                    cell.add(f, r[f])
                    ad_by_day[d][f] += float(r[f] or 0)
            for d, n in linked_out.get(cid, {}).items():
                camp.cells[d.isoformat()].add("linked", n)
        for d, r in rnp_days.items():
            days.add(d)
            cell = prod.cells[d.isoformat()]
            for f in RNP_FIELDS:
                cell.add(f, r[f])
                if d in ad_dates:
                    cell.add(f + "_a", r[f])
        for d, s in ad_by_day.items():
            cell = prod.cells[d.isoformat()]
            for f in AD_FIELDS:
                cell.add(f, s[f])
            r = rnp_days.get(d) or {}
            if r.get("visits") is not None:
                cell.add("clicks_v", s["clicks"])
            if r.get("orders_qty") is not None:
                cell.add("ad_orders_q", s["ad_orders"])
            if r.get("orders_rub") is not None:
                cell.add("spend_r", s["spend"])
        for d, n in linked_in.get(art, {}).items():
            days.add(d)
            prod.cells[d.isoformat()].add("linked", n)

    # Месяцы — суммы дней; группы и «Итоги» — суммы товаров.
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

    return {
        "date_from": d0.isoformat(), "date_to": d1.isoformat(),
        "fields": list(FIELDS), "months": months, "placements": placements,
        "managers": sorted({(cat.get(a) or {}).get("manager") for a in set(own) | set(rnp)} - {None, ""}),
        "updated_at": updated["t"].isoformat() if updated and updated["t"] else None,
        "tree": root.out(),
    }
