# -*- coding: utf-8 -*-
"""Склады Ozon — визуализация остатков по складам, кластерам и дням.

Доступ: все авторизованные пользователи (просмотр + редактирование),
как у раздела складов Wildberries.

Модель данных:
  • ozon_stock_daily(date, seller_article, warehouse_id, qty) — штуки по
    складу/дню. qty = сумма H–P из отчёта Ozon (см. ozon_stock_loader).
  • ozon_warehouses(id, name, cluster, federal_district, canonical_id,
    first_seen_date, is_active) — справочник складов. cluster берётся из
    отчёта; federal_district проставляется вручную. canonical_id (self-FK)
    склеивает синонимы: в отчётах группировка по COALESCE(canonical_id, id).
  • Средняя цена продажи за единицу — fact_monthly.avg_sale_price (последний
    месяц с ценой по SKU, marketplace='Ozon' — У OZON СВОЯ ЦЕНА). Себестоимость
    за единицу — ИСТОРИЧНАЯ (item_cost_hist), временной срез по дате остатка
    (общая с WB — себестоимость товара не зависит от площадки).

Группировка в отчёте «По складам» — ДВУХУРОВНЕВАЯ (в отличие от WB):
    Федеральный округ → Кластер → Склад.
Склады без federal_district попадают в группу «Не распределённые»
(туда же пока зарубежные кластеры — Казахстан/Алматы, Беларусь/Минск).

Эндпоинты:
  GET  /api/ozon_warehouses/by_wh        — стоимостная оценка склад×день + дерево.
  GET  /api/ozon_warehouses/data_quality — достоверность (без цены/без с/с на остатках).
  GET  /api/ozon_warehouses/catalog      — справочник складов (list).
  POST /api/ozon_warehouses/rename       — переименование отображаемого имени.
  POST /api/ozon_warehouses/merge        — ручная склейка (canonical_id).
  POST /api/ozon_warehouses/unmerge      — расклейка.
  POST /api/ozon_warehouses/active       — активен/архив.
  POST /api/ozon_warehouses/district     — установить/снять федеральный округ.
  POST /api/ozon_warehouses/cluster      — переопределить кластер склада вручную.
  POST /api/ozon_warehouses/incident/*   — инциденты (историчность), как у WB.
"""
import datetime

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from .. import db, auth

router = APIRouter(prefix="/api/ozon_warehouses", tags=["ozon_warehouses"])


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# --------- Цены за единицу по SKU (OZON) ---------
# price — средняя цена продажи Ozon (последний месяц с ценой, marketplace='Ozon').
# Себестоимость — историчная (item_cost_hist), временной срез по дате остатка.
PRICE_CTE = """
  price AS (
    SELECT DISTINCT ON (upper(seller_article)) upper(seller_article) AS art,
           avg_sale_price AS p
    FROM fact_monthly
    WHERE marketplace = 'Ozon' AND avg_sale_price > 0
    ORDER BY upper(seller_article), month DESC
  )
"""


@router.get("/by_wh")
def by_wh(
    days: int = Query(30, ge=1, le=400),
    date_from: str = Query("", description="YYYY-MM-DD — начало диапазона (приоритетнее days)"),
    date_to: str = Query("", description="YYYY-MM-DD — конец диапазона"),
    user=Depends(auth.get_current_user),
):
    """Стоимостная оценка остатков Ozon по складам и дням + дерево группировки.

    Период: если заданы date_from/date_to — берём даты с данными в диапазоне;
    иначе — последние `days` дат с данными.

    Возвращает:
      dates        — список дат (возр.).
      warehouses   — [{id, name, is_active, cluster, federal_district, incidents}]
                     каноничные склады (по canonical).
      cells        — {wh_id: {date: {qty, rub_sale, rub_cost}}}.
      totals       — по каждой дате: {qty, rub_sale, rub_cost, wh_count}.
      missing      — по последней дате: склады, что были активны, но пропали.
    """
    df = (date_from or "").strip()
    dt = (date_to or "").strip()
    if df and dt:
        date_rows = db.query_all(
            "SELECT DISTINCT date FROM ozon_stock_daily WHERE date >= %s AND date <= %s ORDER BY date",
            (df, dt),
        )
        dates = sorted([r["date"] for r in date_rows])
    else:
        date_rows = db.query_all(
            "SELECT DISTINCT date FROM ozon_stock_daily ORDER BY date DESC LIMIT %s",
            (days,),
        )
        dates = sorted([r["date"] for r in date_rows])
    if not dates:
        return {"dates": [], "warehouses": [], "cells": {}, "totals": {}, "missing": []}

    d_from = dates[0]
    d_to = dates[-1]

    rows = db.query_all(
        f"""
        WITH {PRICE_CTE}
        SELECT COALESCE(w.canonical_id, w.id) AS wid,
               cw.name AS wname,
               cw.is_active AS is_active,
               cw.cluster AS cluster,
               cw.federal_district AS federal_district,
               d.date AS date,
               SUM(d.qty) AS qty,
               SUM(d.qty * COALESCE(pr.p, 0)) AS rub_sale,
               SUM(d.qty * COALESCE(co.c, 0)) AS rub_cost
        FROM ozon_stock_daily d
        JOIN ozon_warehouses w  ON w.id = d.warehouse_id
        JOIN ozon_warehouses cw ON cw.id = COALESCE(w.canonical_id, w.id)
        LEFT JOIN price pr ON pr.art = upper(d.seller_article)
        LEFT JOIN LATERAL (
            SELECT ich.cost_calc AS c
            FROM item_cost_hist ich
            WHERE upper(ich.seller_article) = upper(d.seller_article)
              AND ich.start_date <= d.date
              AND ich.cost_calc IS NOT NULL AND ich.cost_calc > 0
            ORDER BY ich.start_date DESC
            LIMIT 1
        ) co ON TRUE
        WHERE d.date >= %s AND d.date <= %s
        GROUP BY COALESCE(w.canonical_id, w.id), cw.name, cw.is_active,
                 cw.cluster, cw.federal_district, d.date
        """,
        (d_from, d_to),
    )

    wh_meta = {}
    cells = {}
    totals = {}
    for r in rows:
        wid = r["wid"]
        diso = r["date"].isoformat()
        wh_meta.setdefault(wid, {
            "id": wid, "name": r["wname"], "is_active": bool(r["is_active"]),
            "cluster": r["cluster"] or None,
            "federal_district": r["federal_district"] or None,
            "incidents": [],
        })
        cells.setdefault(wid, {})[diso] = {
            "qty": int(r["qty"] or 0),
            "rub_sale": round(_f(r["rub_sale"])),
            "rub_cost": round(_f(r["rub_cost"])),
        }
        t = totals.setdefault(diso, {"qty": 0, "rub_sale": 0.0, "rub_cost": 0.0, "wh_count": 0})
        t["qty"] += int(r["qty"] or 0)
        t["rub_sale"] += _f(r["rub_sale"])
        t["rub_cost"] += _f(r["rub_cost"])
        if (r["qty"] or 0) > 0:
            t["wh_count"] += 1

    for diso in totals:
        totals[diso]["rub_sale"] = round(totals[diso]["rub_sale"])
        totals[diso]["rub_cost"] = round(totals[diso]["rub_cost"])

    # Инциденты по каноничным складам.
    inc_rows = db.query_all(
        "SELECT id, wh_id, start_date, end_date FROM ozon_wh_incidents ORDER BY wh_id, start_date"
    )
    for ir in inc_rows:
        wid = ir["wh_id"]
        if wid in wh_meta:
            wh_meta[wid]["incidents"].append({
                "id": ir["id"],
                "start": ir["start_date"].isoformat(),
                "end": ir["end_date"].isoformat() if ir["end_date"] else None,
            })

    last = dates[-1].isoformat()
    warehouses = sorted(
        wh_meta.values(),
        key=lambda w: cells.get(w["id"], {}).get(last, {}).get("rub_sale", 0),
        reverse=True,
    )

    missing = []
    if len(dates) >= 2:
        prev = dates[-2].isoformat()
        for w in wh_meta.values():
            r_last = cells.get(w["id"], {}).get(last, {}).get("qty", 0)
            r_prev = cells.get(w["id"], {}).get(prev, {}).get("qty", 0)
            if r_prev > 0 and r_last == 0:
                missing.append({"id": w["id"], "name": w["name"], "prev_qty": r_prev})

    return {
        "dates": [d.isoformat() for d in dates],
        "warehouses": warehouses,
        "cells": cells,
        "totals": totals,
        "missing": missing,
        "last_date": last,
    }


# ==================== Достоверность цифр (баннер) ====================
@router.get("/data_quality")
def data_quality(
    date: str = Query("", description="YYYY-MM-DD — дата остатков; пусто = последняя"),
    user=Depends(auth.get_current_user),
):
    """Достоверность стоимостной оценки остатков Ozon на дату.

    Артикулы НА ОСТАТКАХ (qty>0), у которых НЕТ себестоимости (срез
    item_cost_hist на дату) ИЛИ НЕТ средней цены Ozon (fact_monthly).
    """
    d = (date or "").strip()
    if d:
        row = db.query_one("SELECT MAX(date) AS d FROM ozon_stock_daily WHERE date <= %s", (d,))
    else:
        row = db.query_one("SELECT MAX(date) AS d FROM ozon_stock_daily")
    the_date = row["d"] if row else None
    if not the_date:
        return {"date": None, "total_arts": 0, "total_qty": 0,
                "no_cost": 0, "no_price": 0, "no_both": 0, "items": []}

    rows = db.query_all(
        f"""
        WITH {PRICE_CTE}
        SELECT d.seller_article AS article,
               SUM(d.qty) AS qty,
               MAX(pr.p) AS price,
               MAX(co.c) AS cost
        FROM ozon_stock_daily d
        LEFT JOIN price pr ON pr.art = upper(d.seller_article)
        LEFT JOIN LATERAL (
            SELECT ich.cost_calc AS c
            FROM item_cost_hist ich
            WHERE upper(ich.seller_article) = upper(d.seller_article)
              AND ich.start_date <= d.date
              AND ich.cost_calc IS NOT NULL AND ich.cost_calc > 0
            ORDER BY ich.start_date DESC
            LIMIT 1
        ) co ON TRUE
        WHERE d.date = %s
        GROUP BY d.seller_article
        HAVING SUM(d.qty) > 0
        """,
        (the_date,),
    )

    total_arts = len(rows)
    total_qty = 0
    no_cost = no_price = no_both = 0
    items = []
    for r in rows:
        qty = int(r["qty"] or 0)
        total_qty += qty
        has_cost = r["cost"] is not None and _f(r["cost"]) > 0
        has_price = r["price"] is not None and _f(r["price"]) > 0
        if has_cost and has_price:
            continue
        if not has_cost and not has_price:
            no_both += 1
        elif not has_cost:
            no_cost += 1
        else:
            no_price += 1
        items.append({
            "article": r["article"],
            "qty": qty,
            "has_cost": has_cost,
            "has_price": has_price,
        })
    items.sort(key=lambda x: ((x["has_cost"] or x["has_price"]), -x["qty"]))

    return {
        "date": the_date.isoformat(),
        "total_arts": total_arts,
        "total_qty": total_qty,
        "no_cost": no_cost,
        "no_price": no_price,
        "no_both": no_both,
        "problem_arts": len(items),
        "items": items,
    }


# ============================ Справочник складов ============================
@router.get("/catalog")
def catalog(user=Depends(auth.get_current_user)):
    """Справочник складов Ozon: склады с кластером, ФО, статусами."""
    seen = {r["warehouse_id"]: r for r in db.query_all(
        """SELECT warehouse_id, MAX(date) AS last_seen, COUNT(DISTINCT date) AS days_seen,
                  SUM(qty) AS tot_qty
           FROM ozon_stock_daily GROUP BY warehouse_id"""
    )}
    max_date = db.query_one("SELECT MAX(date) AS d FROM ozon_stock_daily")
    max_date = max_date["d"] if max_date else None

    rows = db.query_all(
        """SELECT w.id, w.name, w.cluster, w.federal_district, w.canonical_id,
                  w.first_seen_date, w.is_active, cw.name AS canonical_name
           FROM ozon_warehouses w
           LEFT JOIN ozon_warehouses cw ON cw.id = w.canonical_id
           ORDER BY w.name"""
    )
    out = []
    for r in rows:
        s = seen.get(r["id"], {})
        last_seen = s.get("last_seen")
        missing = bool(max_date and last_seen and last_seen < max_date)
        out.append({
            "id": r["id"],
            "name": r["name"],
            "cluster": r["cluster"] or None,
            "federal_district": r["federal_district"] or None,
            "canonical_id": r["canonical_id"],
            "canonical_name": r["canonical_name"],
            "first_seen_date": r["first_seen_date"].isoformat() if r["first_seen_date"] else None,
            "last_seen_date": last_seen.isoformat() if last_seen else None,
            "is_active": bool(r["is_active"]),
            "days_seen": int(s.get("days_seen") or 0),
            "tot_qty": int(s.get("tot_qty") or 0),
            "missing": missing,
        })
    return {"warehouses": out, "max_date": max_date.isoformat() if max_date else None}


class RenameBody(BaseModel):
    id: int
    name: str


@router.post("/rename")
def rename(body: RenameBody, user=Depends(auth.get_current_user)):
    """Переименование отображаемого имени склада (история по id сохраняется)."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Пустое имя")
    exists = db.query_one("SELECT id FROM ozon_warehouses WHERE lower(name)=lower(%s) AND id<>%s",
                          (name, body.id))
    if exists:
        raise HTTPException(409, "Склад с таким именем уже есть")
    db.execute("UPDATE ozon_warehouses SET name=%s WHERE id=%s", (name, body.id))
    return {"ok": True}


class MergeBody(BaseModel):
    id: int
    canonical_id: int


@router.post("/merge")
def merge(body: MergeBody, user=Depends(auth.get_current_user)):
    """Склейка синонима к каноничному складу (canonical_id)."""
    if body.id == body.canonical_id:
        raise HTTPException(400, "Нельзя склеить склад сам с собой")
    canon = db.query_one("SELECT id, canonical_id FROM ozon_warehouses WHERE id=%s", (body.canonical_id,))
    if not canon:
        raise HTTPException(404, "Каноничный склад не найден")
    if canon["canonical_id"] is not None:
        raise HTTPException(400, "Нельзя клеить к синониму — выберите корневой склад")
    db.execute("UPDATE ozon_warehouses SET canonical_id=%s WHERE canonical_id=%s",
               (body.canonical_id, body.id))
    db.execute("UPDATE ozon_warehouses SET canonical_id=%s WHERE id=%s",
               (body.canonical_id, body.id))
    return {"ok": True}


class IdBody(BaseModel):
    id: int


@router.post("/unmerge")
def unmerge(body: IdBody, user=Depends(auth.get_current_user)):
    """Расклейка: склад снова становится самостоятельным (canonical_id=NULL)."""
    db.execute("UPDATE ozon_warehouses SET canonical_id=NULL WHERE id=%s", (body.id,))
    return {"ok": True}


class ActiveBody(BaseModel):
    id: int
    is_active: bool


@router.post("/active")
def set_active(body: ActiveBody, user=Depends(auth.get_current_user)):
    """Пометить склад активным/архивным."""
    db.execute("UPDATE ozon_warehouses SET is_active=%s WHERE id=%s", (body.is_active, body.id))
    return {"ok": True}


# ============================ Инциденты складов ============================
def _canonical_id(wh_id: int):
    r = db.query_one("SELECT COALESCE(canonical_id, id) AS cid FROM ozon_warehouses WHERE id=%s", (wh_id,))
    if not r:
        raise HTTPException(404, "Склад не найден")
    return r["cid"]


def _parse_date(s: str, field: str):
    s = (s or "").strip()
    if not s:
        raise HTTPException(400, f"Не задана дата: {field}")
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Некорректная дата {field} (нужен формат YYYY-MM-DD)")


class IncidentOpenBody(BaseModel):
    id: int
    date: str


@router.post("/incident/open")
def incident_open(body: IncidentOpenBody, user=Depends(auth.get_current_user)):
    """Открыть инцидент на складе с указанной даты (новый интервал, end=NULL).

    Защита от дублей (на 1 склад — максимум 1 ОТКРЫТЫЙ интервал):
    если по складу уже есть открытый инцидент (end_date IS NULL), второй
    НЕ создаём. Если новая дата начала раньше — расширяем существующий
    интервал влево (сдвигаем start_date). Это защищает от бага, когда
    открытие задним числом (start раньше уже открытого) плодило дубли.
    """
    cid = _canonical_id(body.id)
    dt = _parse_date(body.date, "начала")
    open_iv = db.query_one(
        """SELECT id, start_date FROM ozon_wh_incidents
           WHERE wh_id=%s AND end_date IS NULL
           ORDER BY start_date LIMIT 1""",
        (cid,),
    )
    if open_iv:
        if dt < open_iv["start_date"]:
            db.execute(
                "UPDATE ozon_wh_incidents SET start_date=%s WHERE id=%s",
                (dt, open_iv["id"]),
            )
            return {"ok": True, "extended": open_iv["id"], "start": dt.isoformat()}
        return {"ok": True, "skipped": "по складу уже есть открытый инцидент", "incident_id": open_iv["id"]}
    row = db.query_one(
        "INSERT INTO ozon_wh_incidents (wh_id, start_date, end_date) VALUES (%s, %s, NULL) RETURNING id",
        (cid, dt),
    )
    return {"ok": True, "incident_id": row["id"], "wh_id": cid, "start": dt.isoformat()}


class IncidentCloseBody(BaseModel):
    id: int
    date: str


@router.post("/incident/close")
def incident_close(body: IncidentCloseBody, user=Depends(auth.get_current_user)):
    """Закрыть активный инцидент с указанной даты (последний день = date-1)."""
    cid = _canonical_id(body.id)
    dt = _parse_date(body.date, "окончания")
    end = dt - datetime.timedelta(days=1)
    iv = db.query_one(
        """SELECT id, start_date, end_date FROM ozon_wh_incidents
           WHERE wh_id=%s AND start_date<=%s AND (end_date IS NULL OR end_date>=%s)
           ORDER BY start_date DESC LIMIT 1""",
        (cid, dt, dt),
    )
    if not iv:
        raise HTTPException(404, "На эту дату нет активного инцидента")
    if end < iv["start_date"]:
        db.execute("DELETE FROM ozon_wh_incidents WHERE id=%s", (iv["id"],))
        return {"ok": True, "deleted": iv["id"]}
    db.execute("UPDATE ozon_wh_incidents SET end_date=%s WHERE id=%s", (end, iv["id"]))
    return {"ok": True, "incident_id": iv["id"], "end": end.isoformat()}


class IncidentDeleteBody(BaseModel):
    incident_id: int


@router.post("/incident/delete")
def incident_delete(body: IncidentDeleteBody, user=Depends(auth.get_current_user)):
    """Полное удаление интервала инцидента по id."""
    db.execute("DELETE FROM ozon_wh_incidents WHERE id=%s", (body.incident_id,))
    return {"ok": True}


# Федеральные округа РФ (как у WB) + зарубежные пока не распределяются.
FEDERAL_DISTRICTS = [
    "Центральный",
    "Северо-Западный",
    "Приволжский",
    "Южный",
    "Северо-Кавказский",
    "Уральский",
    "Сибирский",
    "Дальневосточный",
]


class DistrictBody(BaseModel):
    id: int
    federal_district: str | None = None


@router.post("/district")
def set_district(body: DistrictBody, user=Depends(auth.get_current_user)):
    """Установить/снять федеральный округ склада (пусто → «Не распределённые»)."""
    fd = (body.federal_district or "").strip() or None
    if fd is not None and fd not in FEDERAL_DISTRICTS:
        raise HTTPException(400, f"Недопустимый федеральный округ: {fd}")
    db.execute("UPDATE ozon_warehouses SET federal_district=%s WHERE id=%s", (fd, body.id))
    return {"ok": True, "federal_district": fd}


class ClusterBody(BaseModel):
    id: int
    cluster: str | None = None


@router.post("/cluster")
def set_cluster(body: ClusterBody, user=Depends(auth.get_current_user)):
    """Переопределить кластер склада вручную (пусто → снять)."""
    cl = (body.cluster or "").strip() or None
    db.execute("UPDATE ozon_warehouses SET cluster=%s WHERE id=%s", (cl, body.id))
    return {"ok": True, "cluster": cl}
