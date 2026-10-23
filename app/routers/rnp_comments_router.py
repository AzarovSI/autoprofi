# -*- coding: utf-8 -*-
"""Роутер комментариев к дневным ячейкам в матрице «РНП заказы».

Комментарии привязаны к ЯЧЕЙКЕ = (marketplace, товар или категория L3, date): товар —
seller_article, категория третьего уровня — category_key «L1|L2|L3» (ключ узла дерева, с 23.10.2026).
У РНП каждой площадки свои комментарии (до 23.10.2026 площадки не было, и комментарий
показывался в РНП всех площадок).
Аналог комментариев к ячейкам Google Sheets: несколько комментариев на ячейку,
у каждого — автор (Имя Фамилия из app_users), дата создания/изменения.

Эндпоинты (все требуют авторизации):
  GET    /api/rnp-comments?marketplace=&date_from=&date_to=  — список площадки за диапазон
                                                    дат (для видимого периода матрицы)
  POST   /api/rnp-comments                       — создать (автор = текущий юзер)
  PATCH  /api/rnp-comments/{id}                   — редактировать (автор или админ)
  DELETE /api/rnp-comments/{id}                   — удалить (автор или админ)

Права на изменение/удаление: только автор своего комментария ЛИБО пользователь
с ролью admin. Не-админ не может трогать чужие комментарии (403).

Данные грузятся ОТДЕЛЬНЫМ запросом от матрицы и только за видимый период —
матрица не тормозит, объём запроса минимален. Индекс (marketplace, date, seller_article).
"""
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, field_validator, model_validator

from .. import auth, db
from .rnp_sales_router import MP_MAP

router = APIRouter(prefix="/api/rnp-comments", tags=["rnp-comments"])

MAX_BODY = 2000  # предел длины комментария (символов)

MAX_CATEGORY = 600  # предел длины пути категории «L1|L2|L3»

# По порядку: площадка у комментариев, комментарии категорий L3. ALTER существующей таблицы:
# в TimeWeb применять под gen_user до выкладки.
MIGRATIONS = ["2026_10_23_rnp_comments_marketplace", "2026_10_23_rnp_comments_category"]


def migrate():
    """Миграции комментариев по порядку: каждая один раз, атомарно, безопасно для нескольких воркеров."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(23102026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        for name in MIGRATIONS:
            if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (name,)):
                continue
            path = Path(__file__).resolve().parents[2] / "migrations" / (name + ".sql")
            tx.conn.execute(path.read_text(encoding="utf-8"))
            tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (name,))


def _mp(value):
    """Площадка из параметра (ozon | wb | yandex) → имя как в БД; неизвестная — ошибка,
    чтобы комментарий не попал в РНП чужой площадки."""
    mp = MP_MAP.get((value or "").strip().lower())
    if not mp:
        raise ValueError("Неизвестная площадка")
    return mp


class CommentCreate(BaseModel):
    """Ячейка — товар (seller_article) или категория третьего уровня (category_key), ровно одно."""
    marketplace: str
    seller_article: Optional[str] = None
    category_key: Optional[str] = None
    date: date_cls
    body: str

    @field_validator("marketplace")
    @classmethod
    def _market(cls, v):
        return _mp(v)

    @field_validator("seller_article", "category_key")
    @classmethod
    def _target(cls, v):
        v = (v or "").strip()
        if len(v) > MAX_CATEGORY:
            raise ValueError("Слишком длинный адрес ячейки")
        return v or None

    @model_validator(mode="after")
    def _one_target(self):
        if bool(self.seller_article) == bool(self.category_key):
            raise ValueError("Укажите артикул или категорию ячейки")
        return self

    @field_validator("body")
    @classmethod
    def _body(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Пустой комментарий")
        if len(v) > MAX_BODY:
            raise ValueError(f"Комментарий длиннее {MAX_BODY} символов")
        return v


class CommentUpdate(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def _body(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Пустой комментарий")
        if len(v) > MAX_BODY:
            raise ValueError(f"Комментарий длиннее {MAX_BODY} символов")
        return v


def _row_to_dict(r):
    """Единый формат комментария для фронта."""
    return {
        "id": r["id"],
        "marketplace": r["marketplace"],
        "seller_article": r["seller_article"],
        "category_key": r.get("category_key"),
        "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else r["date"],
        "body": r["body"],
        "author_id": r["author_id"],
        "author_name": r.get("author_name") or "—",
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
    }


@router.get("")
def list_comments(
    marketplace: str = Query(..., description="площадка РНП: ozon | wb | yandex"),
    date_from: date_cls = Query(..., description="начало периода (вкл.)"),
    date_to: date_cls = Query(..., description="конец периода (вкл.)"),
    user=Depends(auth.require("rnp.comments.view")),
):
    """Комментарии площадки за видимый диапазон дат. Сортировка: сначала по ячейке,
    внутри ячейки — новые сверху (created_at DESC)."""
    try:
        mp = _mp(marketplace)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    rows = db.query_all(
        "SELECT c.id, c.marketplace, c.seller_article, c.category_key, c.date, c.body, c.author_id, "
        "       u.display_name AS author_name, c.created_at, c.updated_at "
        "FROM rnp_comments c "
        "LEFT JOIN app_users u ON u.id = c.author_id "
        "WHERE c.marketplace = %s AND c.date BETWEEN %s AND %s "
        "ORDER BY c.seller_article, c.category_key, c.date, c.created_at DESC",
        (mp, date_from, date_to),
    )
    return {"comments": [_row_to_dict(r) for r in rows]}


@router.post("")
def create_comment(payload: CommentCreate, user=Depends(auth.require("rnp.comments.add"))):
    """Создать комментарий. Автор = текущий пользователь из сессии."""
    row = db.query_one(
        "INSERT INTO rnp_comments (marketplace, seller_article, category_key, date, body, author_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id, marketplace, seller_article, category_key, date, body, author_id, created_at, updated_at",
        (payload.marketplace, payload.seller_article, payload.category_key, payload.date, payload.body, user["id"]),
    )
    # display_name автора известен из сессии — не делаем лишний запрос.
    row["author_name"] = user.get("display_name")
    return _row_to_dict(row)


def _get_or_404(comment_id: int):
    row = db.query_one(
        "SELECT id, author_id FROM rnp_comments WHERE id = %s", (comment_id,)
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Комментарий не найден")
    return row


def _assert_can_modify(comment_row, user):
    """Свой комментарий — функция «свой», чужой — «чужой» (docs/access_roles.md)."""
    if comment_row["author_id"] == user["id"]:
        auth.check(user, "rnp.comments.edit_own")
    else:
        auth.check(user, "rnp.comments.edit_any")


@router.patch("/{comment_id}")
def update_comment(comment_id: int, payload: CommentUpdate,
                   user=Depends(auth.get_current_user)):
    """Редактировать текст комментария (перезапись). Автор или админ."""
    existing = _get_or_404(comment_id)
    _assert_can_modify(existing, user)
    row = db.query_one(
        "UPDATE rnp_comments SET body = %s, updated_at = now() "
        "WHERE id = %s "
        "RETURNING id, marketplace, seller_article, category_key, date, body, author_id, created_at, updated_at",
        (payload.body, comment_id),
    )
    author = db.query_one(
        "SELECT display_name FROM app_users WHERE id = %s", (row["author_id"],)
    )
    row["author_name"] = author["display_name"] if author else None
    return _row_to_dict(row)


@router.delete("/{comment_id}")
def delete_comment(comment_id: int, user=Depends(auth.get_current_user)):
    """Удалить комментарий. Автор или админ."""
    existing = _get_or_404(comment_id)
    _assert_can_modify(existing, user)
    db.execute("DELETE FROM rnp_comments WHERE id = %s", (comment_id,))
    return {"ok": True, "deleted_id": comment_id}
