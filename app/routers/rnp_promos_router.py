# -*- coding: utf-8 -*-
"""Роутер акций товаров в «РНП заказы» (решение владельца 23.10.2026).

Акция = товар площадки (marketplace, seller_article) + название + вход (date_from) + плановый выход
(date_to_plan) + фактический выход при досрочном завершении (date_to_fact). Даты включительно.
У товара на площадке одна акция одновременно. Записи не удаляются: «отменить» помечает акцию
(cancelled_at), она пропадает из РНП, но остаётся в журнале. Каждое действие пишется в
rnp_promo_events — для журнала и статистики. Правила — docs/business_rules.md, «Акции в РНП».

Эндпоинты:
  GET    /api/rnp-promos?marketplace=&date_from=&date_to=  — акции площадки для РНП
  POST   /api/rnp-promos                                    — открыть акцию
  PATCH  /api/rnp-promos/{id}                               — изменить название и даты
  POST   /api/rnp-promos/{id}/close                         — завершить раньше
  POST   /api/rnp-promos/{id}/cancel                        — отменить запись
  GET    /api/rnp-promos/journal?...                        — журнал всех акций
  GET    /api/rnp-promos/{id}/events                        — история изменений акции
"""
import datetime
import json
from datetime import date as date_cls
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from .. import auth, db
from .rnp_sales_router import MP_MAP

router = APIRouter(prefix="/api/rnp-promos", tags=["rnp-promos"])

MIGRATION = "2026_10_23_rnp_promos"
MAX_NAME = 200
_MSK = ZoneInfo("Europe/Moscow")


def migrate():
    """Таблицы акций и права ролей: один раз, атомарно, безопасно для нескольких воркеров.
    Запускается после permissions.migrate() — права выдаются по таблицам ролей."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(23102026, 1)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        path = Path(__file__).resolve().parents[2] / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))


def _today():
    return datetime.datetime.now(_MSK).date()


def _mp(value):
    """Площадка из параметра (ozon | wb | yandex) → имя как в БД; неизвестная — ошибка."""
    mp = MP_MAP.get((value or "").strip().lower())
    if not mp:
        raise ValueError("Неизвестная площадка")
    return mp


def _name(v):
    v = " ".join((v or "").split())
    if not v:
        raise ValueError("Укажите название акции")
    if len(v) > MAX_NAME:
        raise ValueError(f"Название длиннее {MAX_NAME} символов")
    return v


class PromoCreate(BaseModel):
    marketplace: str
    seller_article: str
    name: str
    date_from: date_cls
    date_to_plan: date_cls

    @field_validator("marketplace")
    @classmethod
    def _market(cls, v):
        return _mp(v)

    @field_validator("seller_article")
    @classmethod
    def _art(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Не указан артикул")
        return v

    @field_validator("name")
    @classmethod
    def _nm(cls, v):
        return _name(v)


class PromoUpdate(BaseModel):
    name: Optional[str] = None
    date_from: Optional[date_cls] = None
    date_to_plan: Optional[date_cls] = None

    @field_validator("name")
    @classmethod
    def _nm(cls, v):
        return None if v is None else _name(v)


class PromoClose(BaseModel):
    date_to_fact: date_cls


def _bad(msg):
    raise HTTPException(status_code=400, detail=msg)


def _status(r, today=None):
    """planned — ещё не началась, active — идёт, finished — закончилась по плану,
    closed — завершена раньше, cancelled — отменена."""
    today = today or _today()
    if r["cancelled_at"]:
        return "cancelled"
    if r["date_to_fact"]:
        return "closed"
    if today < r["date_from"]:
        return "planned"
    if today > r["date_to_plan"]:
        return "finished"
    return "active"


def _iso(v):
    return v.isoformat() if v else None


def _row_to_dict(r, today=None):
    end = r["date_to_fact"] or r["date_to_plan"]
    return {
        "id": r["id"],
        "marketplace": r["marketplace"],
        "seller_article": r["seller_article"],
        "name": r["name"],
        "date_from": _iso(r["date_from"]),
        "date_to_plan": _iso(r["date_to_plan"]),
        "date_to_fact": _iso(r["date_to_fact"]),
        "date_to": _iso(end),
        "days": (end - r["date_from"]).days + 1,
        "status": _status(r, today),
        "created_by": r["created_by"],
        "created_by_name": r.get("created_by_name"),
        "created_at": _iso(r["created_at"]),
        "closed_by_name": r.get("closed_by_name"),
        "closed_at": _iso(r["closed_at"]),
        "cancelled_by_name": r.get("cancelled_by_name"),
        "cancelled_at": _iso(r["cancelled_at"]),
    }


_SELECT = """
    SELECT p.*, uc.display_name AS created_by_name, ux.display_name AS closed_by_name,
           un.display_name AS cancelled_by_name
    FROM rnp_promos p
    LEFT JOIN app_users uc ON uc.id = p.created_by
    LEFT JOIN app_users ux ON ux.id = p.closed_by
    LEFT JOIN app_users un ON un.id = p.cancelled_by
"""


def _get(promo_id, tx=None):
    q = (tx or db).query_one(_SELECT + " WHERE p.id = %s", (promo_id,))
    if not q:
        raise HTTPException(status_code=404, detail="Акция не найдена")
    return q


def _lock_item(tx, mp, sa):
    """Сериализует правки акций одного товара площадки (проверка «одна акция» без гонок)."""
    tx.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"rnp_promo|{mp}|{sa}",))


def _check_overlap(tx, mp, sa, d_from, d_to, exclude_id=None):
    """У товара на площадке одна акция одновременно: периоды не пересекаются."""
    other = tx.query_one(
        "SELECT name, date_from, COALESCE(date_to_fact, date_to_plan) AS date_to FROM rnp_promos "
        "WHERE marketplace = %s AND seller_article = %s AND cancelled_at IS NULL AND id IS DISTINCT FROM %s "
        "AND date_from <= %s AND COALESCE(date_to_fact, date_to_plan) >= %s "
        "ORDER BY date_from LIMIT 1",
        (mp, sa, exclude_id, d_to, d_from),
    )
    if other:
        _bad(f"У товара уже есть акция «{other['name']}» "
             f"{other['date_from']:%d.%m.%Y} — {other['date_to']:%d.%m.%Y}. Две акции одновременно быть не может.")


def _event(tx, promo_id, action, changes, user):
    tx.execute("INSERT INTO rnp_promo_events (promo_id, action, changes, user_id) VALUES (%s, %s, %s::jsonb, %s)",
               (promo_id, action, json.dumps(changes, ensure_ascii=False, default=str), user["id"]))


def _assert_can_modify(promo, user):
    """Своя акция — функция «свою», чужая — «чужую» (как комментарии)."""
    if promo["created_by"] == user["id"]:
        auth.check(user, "rnp.promos.edit_own")
    else:
        auth.check(user, "rnp.promos.edit_any")


@router.get("")
def list_promos(
    marketplace: str = Query(..., description="площадка РНП: ozon | wb | yandex"),
    date_from: date_cls = Query(...),
    date_to: date_cls = Query(...),
    user=Depends(auth.require("rnp.promos.view")),
):
    """Неотменённые акции площадки, которые пересекают видимый период или идут сегодня
    (ракета у товара не зависит от выбранного периода)."""
    try:
        mp = _mp(marketplace)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    today = _today()
    rows = db.query_all(
        _SELECT + " WHERE p.marketplace = %s AND p.cancelled_at IS NULL "
        "AND ((p.date_from <= %s AND COALESCE(p.date_to_fact, p.date_to_plan) >= %s) "
        "  OR (p.date_from <= %s AND COALESCE(p.date_to_fact, p.date_to_plan) >= %s)) "
        "ORDER BY p.seller_article, p.date_from",
        (mp, date_to, date_from, today, today),
    )
    return {"today": today.isoformat(), "promos": [_row_to_dict(r, today) for r in rows]}


@router.post("")
def create_promo(payload: PromoCreate, user=Depends(auth.require("rnp.promos.add"))):
    if payload.date_to_plan < payload.date_from:
        _bad("Плановый выход раньше входа в акцию")
    with db.transaction() as tx:
        _lock_item(tx, payload.marketplace, payload.seller_article)
        _check_overlap(tx, payload.marketplace, payload.seller_article, payload.date_from, payload.date_to_plan)
        new_id = tx.query_one(
            "INSERT INTO rnp_promos (marketplace, seller_article, name, date_from, date_to_plan, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (payload.marketplace, payload.seller_article, payload.name, payload.date_from,
             payload.date_to_plan, user["id"]),
        )["id"]
        _event(tx, new_id, "create", {"name": payload.name, "date_from": payload.date_from,
                                      "date_to_plan": payload.date_to_plan}, user)
        row = _get(new_id, tx)
    return _row_to_dict(row)


@router.patch("/{promo_id}")
def update_promo(promo_id: int, payload: PromoUpdate, user=Depends(auth.get_current_user)):
    """Изменить название, вход и плановый выход. Дата досрочного выхода остаётся, если
    по-прежнему внутри нового периода."""
    with db.transaction() as tx:
        cur = _get(promo_id, tx)
        _assert_can_modify(cur, user)
        if cur["cancelled_at"]:
            _bad("Акция отменена — изменить нельзя")
        new = {"name": payload.name or cur["name"],
               "date_from": payload.date_from or cur["date_from"],
               "date_to_plan": payload.date_to_plan or cur["date_to_plan"]}
        if new["date_to_plan"] < new["date_from"]:
            _bad("Плановый выход раньше входа в акцию")
        fact = cur["date_to_fact"]
        if fact and not (new["date_from"] <= fact <= new["date_to_plan"]):
            _bad(f"Акция завершена {fact:%d.%m.%Y} — новые даты должны включать этот день")
        changes = {k: [cur[k], v] for k, v in new.items() if cur[k] != v}
        if not changes:
            return _row_to_dict(cur)
        _lock_item(tx, cur["marketplace"], cur["seller_article"])
        _check_overlap(tx, cur["marketplace"], cur["seller_article"], new["date_from"],
                       fact or new["date_to_plan"], exclude_id=promo_id)
        tx.execute("UPDATE rnp_promos SET name = %s, date_from = %s, date_to_plan = %s, updated_at = now() "
                   "WHERE id = %s", (new["name"], new["date_from"], new["date_to_plan"], promo_id))
        _event(tx, promo_id, "edit", changes, user)
        row = _get(promo_id, tx)
    return _row_to_dict(row)


@router.post("/{promo_id}/close")
def close_promo(promo_id: int, payload: PromoClose, user=Depends(auth.get_current_user)):
    """Завершить раньше: последний день акции = date_to_fact (не позже планового выхода)."""
    with db.transaction() as tx:
        cur = _get(promo_id, tx)
        _assert_can_modify(cur, user)
        if cur["cancelled_at"]:
            _bad("Акция отменена")
        d = payload.date_to_fact
        if d < cur["date_from"]:
            _bad(f"Акция начинается {cur['date_from']:%d.%m.%Y} — завершить раньше входа нельзя")
        if d > cur["date_to_plan"]:
            _bad(f"Плановый выход — {cur['date_to_plan']:%d.%m.%Y}. Чтобы продлить акцию, измените плановый выход")
        tx.execute("UPDATE rnp_promos SET date_to_fact = %s, closed_by = %s, closed_at = now(), updated_at = now() "
                   "WHERE id = %s", (d, user["id"], promo_id))
        _event(tx, promo_id, "close", {"date_to_fact": [cur["date_to_fact"], d]}, user)
        row = _get(promo_id, tx)
    return _row_to_dict(row)


@router.post("/{promo_id}/cancel")
def cancel_promo(promo_id: int, user=Depends(auth.get_current_user)):
    """Отменить ошибочную запись: из РНП пропадает, в журнале остаётся с пометкой."""
    with db.transaction() as tx:
        cur = _get(promo_id, tx)
        _assert_can_modify(cur, user)
        if cur["cancelled_at"]:
            return _row_to_dict(cur)
        tx.execute("UPDATE rnp_promos SET cancelled_by = %s, cancelled_at = now(), updated_at = now() "
                   "WHERE id = %s", (user["id"], promo_id))
        _event(tx, promo_id, "cancel", {}, user)
        row = _get(promo_id, tx)
    return _row_to_dict(row)


@router.get("/journal")
def journal(
    marketplace: Optional[str] = Query(None, description="ozon | wb | yandex; пусто — все"),
    date_from: Optional[date_cls] = Query(None, description="акции, пересекающие период"),
    date_to: Optional[date_cls] = Query(None),
    user=Depends(auth.require("rnp.promos.journal")),
):
    """Все акции (и отменённые) с авторами — для вкладки «Журнал акций». Фильтры по статусу и
    поиску — на клиенте."""
    where, params = [], []
    if marketplace:
        try:
            where.append("p.marketplace = %s")
            params.append(_mp(marketplace))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if date_to:
        where.append("p.date_from <= %s")
        params.append(date_to)
    if date_from:
        where.append("COALESCE(p.date_to_fact, p.date_to_plan) >= %s")
        params.append(date_from)
    rows = db.query_all(_SELECT + (" WHERE " + " AND ".join(where) if where else "")
                        + " ORDER BY p.date_from DESC, p.id DESC", params)
    today = _today()
    return {"today": today.isoformat(), "promos": [_row_to_dict(r, today) for r in rows]}


@router.get("/{promo_id}/events")
def events(promo_id: int, user=Depends(auth.require("rnp.promos.journal", "rnp.promos.view"))):
    """История акции: кто, когда и что менял."""
    _get(promo_id)
    rows = db.query_all(
        "SELECT e.action, e.changes, e.at, e.user_id, u.display_name AS user_name "
        "FROM rnp_promo_events e LEFT JOIN app_users u ON u.id = e.user_id "
        "WHERE e.promo_id = %s ORDER BY e.at, e.id", (promo_id,))
    return {"events": [{"action": r["action"], "changes": r["changes"], "at": _iso(r["at"]),
                        "user_name": r["user_name"]} for r in rows]}
