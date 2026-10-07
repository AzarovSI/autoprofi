# -*- coding: utf-8 -*-
"""Склады — визуализация остатков Wildberries по складам и дням.

Доступ: все авторизованные пользователи (просмотр + редактирование).
Раздел открыт всем 30.07.2026 (ранее был owner-only).

Модель данных:
  • wb_stock_daily(date, seller_article, warehouse_id, qty) — штуки по складу/дню.
  • wb_warehouses(id, name, canonical_id, first_seen_date, is_active) — справочник
    складов. canonical_id (self-FK) склеивает синонимы: в отчётах строки
    группируются по каноничному складу (COALESCE(canonical_id, id)).
  • Средняя цена продажи за единицу — fact_monthly.avg_sale_price (последний
    месяц с ценой по каждому SKU, marketplace='Wildberries'). Себестоимость
    за единицу — ПОКА нет справочника закупки → колонка отдаётся как null («—»).

Эндпоинты:
  GET /api/warehouses/by_wh   — стоимостная оценка склад×день (тепловая карта, KPI).
  GET /api/warehouses/catalog — справочник складов (list).
  POST /api/warehouses/rename — переименование отображаемого имени.
  POST /api/warehouses/merge  — ручная склейка (canonical_id).
  POST /api/warehouses/unmerge— расклейка.
  POST /api/warehouses/active — активен/архив.
"""
import datetime

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from .. import db, auth

router = APIRouter(prefix="/api/warehouses", tags=["warehouses"])


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# --------- Цены за единицу по SKU ---------
# price  — средняя цена продажи (последний месяц с ценой, Wildberries).
#          Источник — отчёты MPProfit (fact_monthly.avg_sale_price). Берём
#          ПОСЛЕДНЮЮ цену, что есть в базе — применяется ко всем датам
#          остатков (смена цены в MPProfit → остатки пересчитываются).
# Себестоимость — ИСТОРИЧНАЯ (item_cost_hist): временной срез по дате
# остатка — берётся запись с самым свежим start_date <= дата остатка
# (см. LATERAL в запросе). Сопоставление по upper(seller_article).
PRICE_CTE = """
  price AS (
    SELECT DISTINCT ON (upper(seller_article)) upper(seller_article) AS art,
           avg_sale_price AS p
    FROM fact_monthly
    WHERE marketplace = 'Wildberries' AND avg_sale_price > 0
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
    """Стоимостная оценка остатков по складам и дням.

    Период: если заданы date_from/date_to — берём даты с данными в этом
    диапазоне; иначе — последние `days` дат с данными.

    Возвращает:
      dates        — список дат (возр.).
      warehouses   — [{id, name, is_active}] каноничные склады (по canonical).
      cells        — {wh_id: {date: {qty, rub_sale, rub_cost}}} — стоимость в ценах
                     продажи и в себестоимости.
      totals       — по каждой дате: {date: {qty, rub_sale, rub_cost, wh_count}}.
      missing      — по последней дате: склады, что были активны, но пропали.
    """
    # Список дат с данными.
    df = (date_from or "").strip()
    dt = (date_to or "").strip()
    if df and dt:
        date_rows = db.query_all(
            "SELECT DISTINCT date FROM wb_stock_daily WHERE date >= %s AND date <= %s ORDER BY date",
            (df, dt),
        )
        dates = sorted([r["date"] for r in date_rows])
    else:
        date_rows = db.query_all(
            "SELECT DISTINCT date FROM wb_stock_daily ORDER BY date DESC LIMIT %s",
            (days,),
        )
        dates = sorted([r["date"] for r in date_rows])
    if not dates:
        return {"dates": [], "warehouses": [], "cells": {}, "totals": {}, "missing": []}

    d_from = dates[0]
    d_to = dates[-1]

    # Каноничный склад: COALESCE(canonical_id, id). Имя — каноничного склада.
    # Агрегируем qty и rub (qty×цена) по каноничному складу и дню.
    rows = db.query_all(
        f"""
        WITH {PRICE_CTE}
        SELECT COALESCE(w.canonical_id, w.id) AS wid,
               cw.name AS wname,
               cw.is_active AS is_active,
               cw.federal_district AS federal_district,
               d.date AS date,
               SUM(d.qty) AS qty,
               SUM(d.qty * COALESCE(pr.p, 0)) AS rub_sale,
               SUM(d.qty * COALESCE(co.c, 0)) AS rub_cost
        FROM wb_stock_daily d
        JOIN wb_warehouses w  ON w.id = d.warehouse_id
        JOIN wb_warehouses cw ON cw.id = COALESCE(w.canonical_id, w.id)
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
        GROUP BY COALESCE(w.canonical_id, w.id), cw.name, cw.is_active, cw.federal_district, d.date
        """,
        (d_from, d_to),
    )

    wh_meta = {}       # wid -> {id, name, is_active}
    cells = {}         # wid -> {date_iso -> {qty, rub_sale, rub_cost}}
    totals = {}        # date_iso -> {qty, rub_sale, rub_cost, wh_count}
    for r in rows:
        wid = r["wid"]
        diso = r["date"].isoformat()
        wh_meta.setdefault(wid, {
            "id": wid, "name": r["wname"], "is_active": bool(r["is_active"]),
            "federal_district": r["federal_district"] or None,
            "incidents": [],  # заполняется ниже интервалами
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

    # Интервалы инцидентов по каноничным складам (историчность).
    # end_date = NULL — инцидент открыт (действует по текущий день включительно).
    inc_rows = db.query_all(
        "SELECT id, wh_id, start_date, end_date FROM wb_wh_incidents ORDER BY wh_id, start_date"
    )
    for ir in inc_rows:
        wid = ir["wh_id"]
        if wid in wh_meta:
            wh_meta[wid]["incidents"].append({
                "id": ir["id"],
                "start": ir["start_date"].isoformat(),
                "end": ir["end_date"].isoformat() if ir["end_date"] else None,
            })

    # Порядок складов — по стоимости продажи на последнюю дату (убыв.).
    last = dates[-1].isoformat()
    warehouses = sorted(
        wh_meta.values(),
        key=lambda w: cells.get(w["id"], {}).get(last, {}).get("rub_sale", 0),
        reverse=True,
    )

    # Пропавшие склады: имели остаток на предыдущей дате, но не на последней.
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
    """Достоверность стоимостной оценки остатков WB на дату.

    Возвращает артикулы НА ОСТАТКАХ (qty>0 на указанную дату), у которых
    НЕТ себестоимости (временной срез item_cost_hist на дату) ИЛИ НЕТ
    средней цены (fact_monthly, MPProfit). Такие товары занижают KPI
    (в расчёте их стоимость/себестоимость = 0).

    Ответ:
      date        — дата, на которую считали.
      total_arts  — всего артикулов на остатках.
      total_qty   — всего штук.
      no_cost/no_price/no_both — счётчики проблемных артикулов.
      items       — список {article, qty, has_cost, has_price} (только проблемные).
    """
    d = (date or "").strip()
    if d:
        row = db.query_one("SELECT MAX(date) AS d FROM wb_stock_daily WHERE date <= %s", (d,))
    else:
        row = db.query_one("SELECT MAX(date) AS d FROM wb_stock_daily")
    the_date = row["d"] if row else None
    if not the_date:
        return {"date": None, "total_arts": 0, "total_qty": 0,
                "no_cost": 0, "no_price": 0, "no_both": 0, "items": []}

    # Агрегация по артикулу на дату: qty, наличие цены/себестоимости.
    # Цена — последняя (price CTE); себестоимость — временной срез на дату.
    rows = db.query_all(
        f"""
        WITH {PRICE_CTE}
        SELECT d.seller_article AS article,
               SUM(d.qty) AS qty,
               MAX(pr.p) AS price,
               MAX(co.c) AS cost
        FROM wb_stock_daily d
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
    # Сортировка: сначала «без обоих», потом по убыванию количества.
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
    """Справочник складов: все склады с сопоставлением и статусами.

    Для каждого склада:
      id, name, canonical_id, canonical_name, first_seen_date, is_active,
      last_seen_date (макс. дата в wb_stock_daily), missing (был, но пропал),
      days_seen (кол-во дат с остатком).
    """
    # last_seen и days_seen из wb_stock_daily.
    seen = {r["warehouse_id"]: r for r in db.query_all(
        """SELECT warehouse_id, MAX(date) AS last_seen, COUNT(DISTINCT date) AS days_seen,
                  SUM(qty) AS tot_qty
           FROM wb_stock_daily GROUP BY warehouse_id"""
    )}
    max_date = db.query_one("SELECT MAX(date) AS d FROM wb_stock_daily")
    max_date = max_date["d"] if max_date else None

    rows = db.query_all(
        """SELECT w.id, w.name, w.canonical_id, w.first_seen_date, w.is_active,
                  w.federal_district, cw.name AS canonical_name
           FROM wb_warehouses w
           LEFT JOIN wb_warehouses cw ON cw.id = w.canonical_id
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
            "canonical_id": r["canonical_id"],
            "canonical_name": r["canonical_name"],
            "first_seen_date": r["first_seen_date"].isoformat() if r["first_seen_date"] else None,
            "last_seen_date": last_seen.isoformat() if last_seen else None,
            "is_active": bool(r["is_active"]),
            "federal_district": r["federal_district"] or None,
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
    exists = db.query_one("SELECT id FROM wb_warehouses WHERE lower(name)=lower(%s) AND id<>%s",
                          (name, body.id))
    if exists:
        raise HTTPException(409, "Склад с таким именем уже есть")
    db.execute("UPDATE wb_warehouses SET name=%s WHERE id=%s", (name, body.id))
    return {"ok": True}


class MergeBody(BaseModel):
    id: int            # склад-синоним (кого клеим)
    canonical_id: int  # каноничный склад (к кому клеим)


@router.post("/merge")
def merge(body: MergeBody, user=Depends(auth.get_current_user)):
    """Склейка синонима к каноничному складу (canonical_id).

    В отчётах строки группируются по COALESCE(canonical_id, id), поэтому
    синоним начинает суммироваться с каноничным. Нельзя клеить склад сам к себе
    и нельзя клеить к синониму (только к «корневому» каноничному складу).
    """
    if body.id == body.canonical_id:
        raise HTTPException(400, "Нельзя склеить склад сам с собой")
    canon = db.query_one("SELECT id, canonical_id FROM wb_warehouses WHERE id=%s", (body.canonical_id,))
    if not canon:
        raise HTTPException(404, "Каноничный склад не найден")
    if canon["canonical_id"] is not None:
        raise HTTPException(400, "Нельзя клеить к синониму — выберите корневой склад")
    # Если к клеящемуся уже привязаны другие синонимы — перецепим их на новый корень.
    db.execute("UPDATE wb_warehouses SET canonical_id=%s WHERE canonical_id=%s",
               (body.canonical_id, body.id))
    db.execute("UPDATE wb_warehouses SET canonical_id=%s WHERE id=%s",
               (body.canonical_id, body.id))
    return {"ok": True}


class IdBody(BaseModel):
    id: int


@router.post("/unmerge")
def unmerge(body: IdBody, user=Depends(auth.get_current_user)):
    """Расклейка: склад снова становится самостоятельным (canonical_id=NULL)."""
    db.execute("UPDATE wb_warehouses SET canonical_id=NULL WHERE id=%s", (body.id,))
    return {"ok": True}


class ActiveBody(BaseModel):
    id: int
    is_active: bool


@router.post("/active")
def set_active(body: ActiveBody, user=Depends(auth.get_current_user)):
    """Пометить склад активным/архивным."""
    db.execute("UPDATE wb_warehouses SET is_active=%s WHERE id=%s", (body.is_active, body.id))
    return {"ok": True}


# ============================ Инциденты складов (историчность) ============================
# Модель: wb_wh_incidents(wh_id, start_date, end_date null). Несколько интервалов
# на склад = история. end_date = NULL — инцидент открыт (действует).
# Старый механизм wb_warehouses.status/status_date ЗАКОНСЕРВИРОВАН
# (колонки остаются в БД, но не используются).


def _canonical_id(wh_id: int):
    """Возвращает каноничный id склада (инциденты крепятся к каноничному)."""
    r = db.query_one("SELECT COALESCE(canonical_id, id) AS cid FROM wb_warehouses WHERE id=%s", (wh_id,))
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
    id: int          # склад (любой id; инцидент крепится к каноничному)
    date: str        # YYYY-MM-DD — дата начала инцидента (включительно)


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
        """SELECT id, start_date FROM wb_wh_incidents
           WHERE wh_id=%s AND end_date IS NULL
           ORDER BY start_date LIMIT 1""",
        (cid,),
    )
    if open_iv:
        if dt < open_iv["start_date"]:
            db.execute(
                "UPDATE wb_wh_incidents SET start_date=%s WHERE id=%s",
                (dt, open_iv["id"]),
            )
            return {"ok": True, "extended": open_iv["id"], "start": dt.isoformat()}
        return {"ok": True, "skipped": "по складу уже есть открытый инцидент", "incident_id": open_iv["id"]}
    row = db.query_one(
        "INSERT INTO wb_wh_incidents (wh_id, start_date, end_date) VALUES (%s, %s, NULL) RETURNING id",
        (cid, dt),
    )
    return {"ok": True, "incident_id": row["id"], "wh_id": cid, "start": dt.isoformat()}


class IncidentCloseBody(BaseModel):
    id: int          # склад
    date: str        # YYYY-MM-DD — дата, С КОТОРОЙ инцидент отменён (последний день инцидента = date-1)


@router.post("/incident/close")
def incident_close(body: IncidentCloseBody, user=Depends(auth.get_current_user)):
    """Закрыть активный инцидент с указанной даты.

    Пользователь кликает по ячейке-дате и выбирает «Закрыть с этой даты»: значит
    в этот день инцидента уже НЕТ, последний день инцидента = date - 1.
    Ищем активный интервал, покрывающий date (или ближайший открытый до date).

    Если date <= start интервала — инцидент не успел начаться → удаляем интервал.
    """
    cid = _canonical_id(body.id)
    dt = _parse_date(body.date, "окончания")
    end = dt - datetime.timedelta(days=1)  # последний день инцидента
    # Активный (открытый) или покрывающий date интервал.
    iv = db.query_one(
        """SELECT id, start_date, end_date FROM wb_wh_incidents
           WHERE wh_id=%s AND start_date<=%s AND (end_date IS NULL OR end_date>=%s)
           ORDER BY start_date DESC LIMIT 1""",
        (cid, dt, dt),
    )
    if not iv:
        raise HTTPException(404, "На эту дату нет активного инцидента")
    if end < iv["start_date"]:
        # Закрытие раньше начала — инцидент вообще убираем.
        db.execute("DELETE FROM wb_wh_incidents WHERE id=%s", (iv["id"],))
        return {"ok": True, "deleted": iv["id"]}
    db.execute("UPDATE wb_wh_incidents SET end_date=%s WHERE id=%s", (end, iv["id"]))
    return {"ok": True, "incident_id": iv["id"], "end": end.isoformat()}


class IncidentDeleteBody(BaseModel):
    incident_id: int


@router.post("/incident/delete")
def incident_delete(body: IncidentDeleteBody, user=Depends(auth.get_current_user)):
    """Полное удаление интервала инцидента по id (отмена ошибочной записи)."""
    db.execute("DELETE FROM wb_wh_incidents WHERE id=%s", (body.incident_id,))
    return {"ok": True}


# Федеральные округа РФ (полные названия) в порядке удаления от Центрального.
# Значение хранится строкой; пусто/None → склад не распределён.
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
    federal_district: str | None = None  # один из FEDERAL_DISTRICTS или пусто/None (снять)


@router.post("/district")
def set_district(body: DistrictBody, user=Depends(auth.get_current_user)):
    """Установить/снять федеральный округ склада.

    federal_district пусто/None → снят (NULL): склад попадает в группу
    «Не распределённые». Округ ставится на конкретный склад по id (в т.ч. каноничный).
    """
    fd = (body.federal_district or "").strip() or None
    if fd is not None and fd not in FEDERAL_DISTRICTS:
        raise HTTPException(400, f"Недопустимый федеральный округ: {fd}")
    db.execute("UPDATE wb_warehouses SET federal_district=%s WHERE id=%s", (fd, body.id))
    return {"ok": True, "federal_district": fd}
