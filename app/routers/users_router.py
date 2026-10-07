# -*- coding: utf-8 -*-
"""Роутер управления пользователями системы (только для администратора).

CRUD по таблице app_users:
  GET    /api/users        — список пользователей
  POST   /api/users        — создать пользователя (ФИО, email, пароль, роль)
  DELETE /api/users/{id}    — жёсткое удаление пользователя

Все эндпоинты защищены зависимостью require_admin (роль admin).
Пароли хранятся только в виде bcrypt-хэша. Логин = email.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import auth, db

router = APIRouter(prefix="/api/users", tags=["users"])

# Допустимые роли системы.
ALLOWED_ROLES = {"admin", "user"}


class UserCreate(BaseModel):
    display_name: str
    email: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    display_name: str
    email: str
    role: str
    password: str | None = None


@router.get("")
def list_users(admin=Depends(auth.require_admin)):
    """Список всех пользователей (без хэшей паролей)."""
    rows = db.query_all(
        """
        SELECT id, email, display_name, role, created_at, last_seen_at
        FROM app_users
        ORDER BY created_at ASC, id ASC
        """
    )
    return [
        {
            "id": r["id"],
            "email": r["email"],
            "display_name": r.get("display_name"),
            "role": r.get("role"),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "last_seen_at": r["last_seen_at"].isoformat() if r.get("last_seen_at") else None,
        }
        for r in rows
    ]


@router.post("")
def create_user(payload: UserCreate, admin=Depends(auth.require_admin)):
    """Создать пользователя. Email уникален, пароль хэшируется bcrypt."""
    email = (payload.email or "").strip().lower()
    name = (payload.display_name or "").strip()
    role = (payload.role or "user").strip().lower()
    password = payload.password or ""

    if not email or "@" not in email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Укажите корректный email.")
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Укажите ФИО пользователя.")
    if not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Укажите пароль.")
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Недопустимая роль.")

    # Проверка уникальности email.
    existing = db.query_one("SELECT id FROM app_users WHERE email = %s", (email,))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Пользователь с таким email уже существует.")

    pwd_hash = auth.hash_password(password)
    row = db.query_one(
        """
        INSERT INTO app_users (email, password_hash, display_name, role, created_at)
        VALUES (%s, %s, %s, %s, now())
        RETURNING id, email, display_name, role, created_at
        """,
        (email, pwd_hash, name, role),
    )
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row.get("display_name"),
        "role": row.get("role"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@router.patch("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, admin=Depends(auth.require_admin)):
    """Редактировать пользователя (ФИО, email, роль, опционально пароль)."""
    email = (payload.email or "").strip().lower()
    name = (payload.display_name or "").strip()
    role = (payload.role or "").strip().lower()
    password = (payload.password or "").strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Укажите корректный email.")
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Укажите ФИО пользователя.")
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Недопустимая роль.")

    target = db.query_one("SELECT id FROM app_users WHERE id = %s", (user_id,))
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Пользователь не найден.")

    # Email должен быть уникален среди ДРУГИХ пользователей.
    existing = db.query_one(
        "SELECT id FROM app_users WHERE email = %s AND id <> %s", (email, user_id))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Пользователь с таким email уже существует.")

    # Пароль меняем только если передан непустой.
    if password:
        db.execute(
            "UPDATE app_users SET display_name = %s, email = %s, role = %s, password_hash = %s WHERE id = %s",
            (name, email, role, auth.hash_password(password), user_id),
        )
    else:
        db.execute(
            "UPDATE app_users SET display_name = %s, email = %s, role = %s WHERE id = %s",
            (name, email, role, user_id),
        )

    row = db.query_one(
        """
        SELECT id, email, display_name, role, created_at, last_seen_at
        FROM app_users WHERE id = %s
        """,
        (user_id,),
    )
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row.get("display_name"),
        "role": row.get("role"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "last_seen_at": row["last_seen_at"].isoformat() if row.get("last_seen_at") else None,
    }


@router.delete("/{user_id}")
def delete_user(user_id: int, admin=Depends(auth.require_admin)):
    """Жёсткое удаление пользователя. Нельзя удалить самого себя."""
    if int(user_id) == int(admin["id"]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Нельзя удалить собственную учётную запись.")
    target = db.query_one("SELECT id FROM app_users WHERE id = %s", (user_id,))
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Пользователь не найден.")
    db.execute("DELETE FROM app_users WHERE id = %s", (user_id,))
    return {"deleted": user_id}
