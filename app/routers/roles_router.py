# -*- coding: utf-8 -*-
"""Роутер ролей доступа (вкладка «Роли» раздела «Пользователи», docs/access_roles.md).

  GET    /api/roles            — каталог функций и роли (права, площадки, сотрудники)
  POST   /api/roles            — новая роль копией существующей
  PUT    /api/roles/{id}       — сохранить название, площадки и права роли
  DELETE /api/roles/{id}       — удалить роль без сотрудников

Смотреть — всем, кому открыт раздел «Пользователи» (РОП — только просмотр);
менять — только администратору. Роль «Администратор» не меняется и не удаляется.
Права действуют со следующего действия сотрудников (auth.load_user читает их на каждом запросе).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import auth, db, permissions

router = APIRouter(prefix="/api/roles", tags=["roles"])


class RoleCopy(BaseModel):
    copy_from: int
    name: str


class RoleUpdate(BaseModel):
    name: str
    marketplaces: list[str]
    permissions: list[str]


def _bad(detail, code=status.HTTP_400_BAD_REQUEST):
    return HTTPException(status_code=code, detail=detail)


def _role_or_404(tx, role_id):
    row = tx.query_one("SELECT id, name, is_admin FROM app_roles WHERE id = %s", (role_id,))
    if not row:
        raise _bad("Роль не найдена.", status.HTTP_404_NOT_FOUND)
    return row


def _check_name(tx, name, role_id=None):
    name = (name or "").strip()
    if not name:
        raise _bad("Укажите название роли.")
    if tx.query_one("SELECT 1 AS x FROM app_roles WHERE lower(name) = lower(%s) AND id IS DISTINCT FROM %s",
                    (name, role_id)):
        raise _bad("Роль с таким названием уже есть.", status.HTTP_409_CONFLICT)
    return name


@router.get("")
def list_roles(user=Depends(auth.require("users.open"))):
    """Каталог функций (для карточки роли) и роли со своими правами и сотрудниками."""
    roles = db.query_all(
        """SELECT r.id, r.name, r.is_admin, r.marketplaces,
                  ARRAY(SELECT p.permission FROM app_role_permissions p
                         WHERE p.role_id = r.id ORDER BY p.permission) AS permissions
           FROM app_roles r ORDER BY r.id""")
    users = db.query_all(
        "SELECT id, display_name, email, role_id FROM app_users "
        "WHERE role_id IS NOT NULL ORDER BY display_name, email")
    by_role = {}
    for u in users:
        by_role.setdefault(u["role_id"], []).append(
            {"id": u["id"], "name": u.get("display_name") or u["email"], "email": u["email"]})
    return {
        "functions": [
            {"n": n, "code": code, "section": section, "tab": tab, "name": name,
             "kind": kind, "description": description}
            for n, code, section, tab, name, kind, description in permissions.FUNCTIONS
        ],
        "marketplaces": list(permissions.MARKETPLACES),
        "can_edit": bool(user.get("is_admin")),
        "roles": [
            {
                "id": r["id"], "name": r["name"], "is_admin": r["is_admin"],
                "marketplaces": list(permissions.MARKETPLACES) if r["is_admin"] else list(r["marketplaces"] or []),
                # У администратора в БД строк прав нет — всегда все функции.
                "permissions": sorted(permissions.CODES) if r["is_admin"]
                else [p for p in r["permissions"] if p in permissions.CODES],
                "users": by_role.get(r["id"], []),
            }
            for r in roles
        ],
    }


@router.post("")
def copy_role(payload: RoleCopy, admin=Depends(auth.require_admin)):
    """Новая роль — копия существующей (площадки и права); у копии администратора — все права."""
    with db.transaction() as tx:
        src = _role_or_404(tx, payload.copy_from)
        name = _check_name(tx, payload.name)
        row = tx.query_one(
            "INSERT INTO app_roles (name, marketplaces) "
            "SELECT %s, CASE WHEN is_admin THEN %s::text[] ELSE marketplaces END FROM app_roles WHERE id = %s "
            "RETURNING id",
            (name, list(permissions.MARKETPLACES), src["id"]))
        if src["is_admin"]:
            tx.execute_values("INSERT INTO app_role_permissions (role_id, permission) VALUES %s",
                              [(row["id"], c) for c in sorted(permissions.CODES)])
        else:
            tx.execute("INSERT INTO app_role_permissions (role_id, permission) "
                       "SELECT %s, permission FROM app_role_permissions WHERE role_id = %s",
                       (row["id"], src["id"]))
    return {"id": row["id"], "name": name}


@router.put("/{role_id}")
def update_role(role_id: int, payload: RoleUpdate, admin=Depends(auth.require_admin)):
    """Сохранить карточку роли целиком: название, площадки, отмеченные функции."""
    unknown = sorted(set(payload.permissions) - permissions.CODES)
    if unknown:
        raise _bad("Неизвестные функции: " + ", ".join(unknown))
    bad_mp = sorted(set(payload.marketplaces) - set(permissions.MARKETPLACES))
    if bad_mp:
        raise _bad("Неизвестные площадки: " + ", ".join(bad_mp))
    with db.transaction() as tx:
        role = _role_or_404(tx, role_id)
        if role["is_admin"]:
            raise _bad("Роль «Администратор» не меняется: у неё всегда все права.")
        name = _check_name(tx, payload.name, role_id)
        mps = [m for m in permissions.MARKETPLACES if m in payload.marketplaces]
        tx.execute("UPDATE app_roles SET name = %s, marketplaces = %s WHERE id = %s", (name, mps, role_id))
        tx.execute("DELETE FROM app_role_permissions WHERE role_id = %s", (role_id,))
        codes = sorted(set(payload.permissions))
        if codes:
            tx.execute_values("INSERT INTO app_role_permissions (role_id, permission) VALUES %s",
                              [(role_id, c) for c in codes])
    return {"id": role_id, "name": name, "marketplaces": mps, "permissions": codes}


@router.delete("/{role_id}")
def delete_role(role_id: int, admin=Depends(auth.require_admin)):
    """Удалить роль. С сотрудниками — нельзя (сначала назначить им другую роль)."""
    with db.transaction() as tx:
        role = _role_or_404(tx, role_id)
        if role["is_admin"]:
            raise _bad("Роль «Администратор» удалить нельзя.")
        n = tx.query_one("SELECT count(*) AS n FROM app_users WHERE role_id = %s", (role_id,))["n"]
        if n:
            raise _bad(f"У роли есть сотрудники ({n}) — сначала назначьте им другую роль.",
                       status.HTTP_409_CONFLICT)
        tx.execute("DELETE FROM app_roles WHERE id = %s", (role_id,))
    return {"deleted": role_id}
