# -*- coding: utf-8 -*-
"""Роутер управления пользователями системы.

CRUD по таблице app_users:
  GET    /api/users        — список пользователей
  POST   /api/users        — создать пользователя (ФИО, email, пароль, роль)
  PATCH  /api/users/{id}   — изменить пользователя (ФИО, email, роль, пароль)
  POST   /api/users/{id}/disable, /enable — отключить / включить пользователя

Пользователей не удаляют, а отключают (28.10.2026): удаление стирало бы их комментарии РНП.
Отключённый (app_users.disabled_at) не входит на сайт, его записи и имя в них остаются.

Права — функции раздела «Пользователи» (docs/access_roles.md). Роль — из справочника ролей
(app_users.role_id). Роль «Администратор» назначает и роль сотрудника меняет только
администратор; владельца нельзя снять с роли «Администратор», сменить ему e-mail и отключить.
Старая колонка role (admin/user) заполняется по роли — для отката на прежнюю версию сайта.
Пароли хранятся только в виде bcrypt-хэша. Логин = email.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import auth, db

router = APIRouter(prefix="/api/users", tags=["users"])

MIGRATION = "2026_10_28_users_disabled"


def migrate():
    """Колонка app_users.disabled_at: один раз, атомарно, безопасно для нескольких воркеров."""
    with db.transaction() as tx:
        tx.execute("SELECT pg_advisory_xact_lock(28102026, 0)")
        tx.execute("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        if tx.query_one("SELECT name FROM app_schema_migrations WHERE name=%s", (MIGRATION,)):
            return
        path = Path(__file__).resolve().parents[2] / "migrations" / (MIGRATION + ".sql")
        tx.conn.execute(path.read_text(encoding="utf-8"))
        tx.execute("INSERT INTO app_schema_migrations(name) VALUES (%s)", (MIGRATION,))


def _is_owner_email(email):
    return (email or "").strip().lower() in {e.lower() for e in auth.OWNER_EMAILS}


class UserCreate(BaseModel):
    display_name: str
    email: str
    password: str
    role_id: int


class UserUpdate(BaseModel):
    display_name: str
    email: str
    role_id: int
    password: str | None = None


def _bad(detail, code=status.HTTP_400_BAD_REQUEST):
    return HTTPException(status_code=code, detail=detail)


def _role(role_id):
    """Роль из справочника или 400."""
    row = db.query_one("SELECT id, name, is_admin FROM app_roles WHERE id = %s", (role_id,))
    if not row:
        raise _bad("Выберите роль из списка.")
    return row


def _legacy_role(role):
    """Значение старой колонки role: admin — для роли «Администратор», иначе user."""
    return "admin" if role["is_admin"] else "user"


def _check_fields(name, email):
    if not email or "@" not in email:
        raise _bad("Укажите корректный email.")
    if not name:
        raise _bad("Укажите ФИО пользователя.")


def _user_out(r):
    return {
        "id": r["id"],
        "email": r["email"],
        "display_name": r.get("display_name"),
        "role_id": r.get("role_id"),
        "role_name": r.get("role_name"),
        "role_is_admin": bool(r.get("role_is_admin")),
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        "last_seen_at": r["last_seen_at"].isoformat() if r.get("last_seen_at") else None,
        "disabled_at": r["disabled_at"].isoformat() if r.get("disabled_at") else None,
    }


_SELECT = """SELECT u.id, u.email, u.display_name, u.role_id, r.name AS role_name,
                    r.is_admin AS role_is_admin, u.created_at, u.last_seen_at, u.disabled_at
             FROM app_users u LEFT JOIN app_roles r ON r.id = u.role_id"""


@router.get("")
def list_users(admin=Depends(auth.require("users.open"))):
    """Список всех пользователей с ролями (без хэшей паролей)."""
    rows = db.query_all(_SELECT + " ORDER BY u.created_at ASC, u.id ASC")
    return [_user_out(r) for r in rows]


@router.post("")
def create_user(payload: UserCreate, admin=Depends(auth.require("users.add"))):
    """Создать пользователя. Email уникален, пароль хэшируется bcrypt.

    Роль «Администратор» назначает только администратор; РОП — любую другую (решение 9)."""
    email = (payload.email or "").strip().lower()
    name = (payload.display_name or "").strip()
    password = payload.password or ""
    _check_fields(name, email)
    if not password:
        raise _bad("Укажите пароль.")
    role = _role(payload.role_id)
    if role["is_admin"] and not admin["is_admin"]:
        raise _bad("Роль «Администратор» назначает только администратор.", status.HTTP_403_FORBIDDEN)
    if db.query_one("SELECT id FROM app_users WHERE email = %s", (email,)):
        raise _bad("Пользователь с таким email уже существует.", status.HTTP_409_CONFLICT)

    row = db.query_one(
        """
        INSERT INTO app_users (email, password_hash, display_name, role, role_id, created_at)
        VALUES (%s, %s, %s, %s, %s, now())
        RETURNING id
        """,
        (email, auth.hash_password(password), name, _legacy_role(role), role["id"]),
    )
    return _user_out(db.query_one(_SELECT + " WHERE u.id = %s", (row["id"],)))


@router.patch("/{user_id}")
def update_user(user_id: int, payload: UserUpdate, admin=Depends(auth.require("users.edit"))):
    """Редактировать пользователя (ФИО, email, роль, опционально пароль)."""
    email = (payload.email or "").strip().lower()
    name = (payload.display_name or "").strip()
    password = (payload.password or "").strip()
    _check_fields(name, email)
    role = _role(payload.role_id)

    target = db.query_one("SELECT id, email, role_id FROM app_users WHERE id = %s", (user_id,))
    if not target:
        raise _bad("Пользователь не найден.", status.HTTP_404_NOT_FOUND)
    if role["id"] != target.get("role_id") and not admin["is_admin"]:
        raise _bad("Роль сотрудника меняет только администратор.", status.HTTP_403_FORBIDDEN)
    if _is_owner_email(target["email"]) and (not role["is_admin"] or not _is_owner_email(email)):
        raise _bad("Владельца нельзя снять с роли «Администратор» или сменить ему e-mail.")
    # Email должен быть уникален среди ДРУГИХ пользователей.
    if db.query_one("SELECT id FROM app_users WHERE email = %s AND id <> %s", (email, user_id)):
        raise _bad("Пользователь с таким email уже существует.", status.HTTP_409_CONFLICT)

    fields = ["display_name = %s", "email = %s", "role = %s", "role_id = %s"]
    params = [name, email, _legacy_role(role), role["id"]]
    # Пароль меняем только если передан непустой.
    if password:
        fields.append("password_hash = %s")
        params.append(auth.hash_password(password))
    db.execute(f"UPDATE app_users SET {', '.join(fields)} WHERE id = %s", (*params, user_id))
    return _user_out(db.query_one(_SELECT + " WHERE u.id = %s", (user_id,)))


@router.post("/{user_id}/disable")
def disable_user(user_id: int, admin=Depends(auth.require("users.delete"))):
    """Отключить пользователя: вход закрыт сразу, записи остаются. Нельзя себя и владельца."""
    if int(user_id) == int(admin["id"]):
        raise _bad("Нельзя отключить собственную учётную запись.")
    target = db.query_one("SELECT id, email FROM app_users WHERE id = %s", (user_id,))
    if not target:
        raise _bad("Пользователь не найден.", status.HTTP_404_NOT_FOUND)
    if _is_owner_email(target["email"]):
        raise _bad("Учётную запись владельца отключить нельзя.")
    db.execute("UPDATE app_users SET disabled_at = now() WHERE id = %s AND disabled_at IS NULL", (user_id,))
    return _user_out(db.query_one(_SELECT + " WHERE u.id = %s", (user_id,)))


@router.post("/{user_id}/enable")
def enable_user(user_id: int, admin=Depends(auth.require("users.delete"))):
    """Включить отключённого пользователя обратно."""
    if not db.query_one("SELECT id FROM app_users WHERE id = %s", (user_id,)):
        raise _bad("Пользователь не найден.", status.HTTP_404_NOT_FOUND)
    db.execute("UPDATE app_users SET disabled_at = NULL WHERE id = %s", (user_id,))
    return _user_out(db.query_one(_SELECT + " WHERE u.id = %s", (user_id,)))
