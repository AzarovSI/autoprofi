# -*- coding: utf-8 -*-
"""Роутер комментариев к дневным ячейкам товара в матрице «РНП заказы Озон».

Комментарии привязаны к ЯЧЕЙКЕ = (seller_article, date) на уровне ТОВАРА.
Аналог комментариев к ячейкам Google Sheets: несколько комментариев на ячейку,
у каждого — автор (Имя Фамилия из app_users), дата создания/изменения.

Эндпоинты (все требуют авторизации):
  GET    /api/rnp-comments?date_from=&date_to=  — список за диапазон дат
                                                    (для видимого периода матрицы)
  POST   /api/rnp-comments                       — создать (автор = текущий юзер)
  PATCH  /api/rnp-comments/{id}                   — редактировать (автор или админ)
  DELETE /api/rnp-comments/{id}                   — удалить (автор или админ)

Права на изменение/удаление: только автор своего комментария ЛИБО пользователь
с ролью admin. Не-админ не может трогать чужие комментарии (403).

Данные грузятся ОТДЕЛЬНЫМ запросом от матрицы и только за видимый период —
матрица не тормозит, объём запроса минимален. Индекс (date, seller_article).
"""
from datetime import date as date_cls

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, field_validator

from .. import auth, db

router = APIRouter(prefix="/api/rnp-comments", tags=["rnp-comments"])

MAX_BODY = 2000  # предел длины комментария (символов)


class CommentCreate(BaseModel):
    seller_article: str
    date: date_cls
    body: str

    @field_validator("seller_article")
    @classmethod
    def _art(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Не указан артикул ячейки")
        return v

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
        "seller_article": r["seller_article"],
        "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else r["date"],
        "body": r["body"],
        "author_id": r["author_id"],
        "author_name": r.get("author_name") or "—",
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
    }


@router.get("")
def list_comments(
    date_from: date_cls = Query(..., description="начало периода (вкл.)"),
    date_to: date_cls = Query(..., description="конец периода (вкл.)"),
    user=Depends(auth.get_current_user),
):
    """Комментарии за видимый диапазон дат. Сортировка: сначала по ячейке,
    внутри ячейки — новые сверху (created_at DESC)."""
    rows = db.query_all(
        "SELECT c.id, c.seller_article, c.date, c.body, c.author_id, "
        "       u.display_name AS author_name, c.created_at, c.updated_at "
        "FROM rnp_comments c "
        "LEFT JOIN app_users u ON u.id = c.author_id "
        "WHERE c.date BETWEEN %s AND %s "
        "ORDER BY c.seller_article, c.date, c.created_at DESC",
        (date_from, date_to),
    )
    return {"comments": [_row_to_dict(r) for r in rows]}


@router.post("")
def create_comment(payload: CommentCreate, user=Depends(auth.get_current_user)):
    """Создать комментарий. Автор = текущий пользователь из сессии."""
    row = db.query_one(
        "INSERT INTO rnp_comments (seller_article, date, body, author_id) "
        "VALUES (%s, %s, %s, %s) "
        "RETURNING id, seller_article, date, body, author_id, created_at, updated_at",
        (payload.seller_article, payload.date, payload.body, user["id"]),
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
    """Изменять/удалять может автор ИЛИ администратор."""
    is_admin = (user.get("role") or "").lower() == "admin"
    is_author = comment_row["author_id"] == user["id"]
    if not (is_admin or is_author):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Можно изменять только свои комментарии",
        )


@router.patch("/{comment_id}")
def update_comment(comment_id: int, payload: CommentUpdate,
                   user=Depends(auth.get_current_user)):
    """Редактировать текст комментария (перезапись). Автор или админ."""
    existing = _get_or_404(comment_id)
    _assert_can_modify(existing, user)
    row = db.query_one(
        "UPDATE rnp_comments SET body = %s, updated_at = now() "
        "WHERE id = %s "
        "RETURNING id, seller_article, date, body, author_id, created_at, updated_at",
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
