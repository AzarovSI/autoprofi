# -*- coding: utf-8 -*-
"""Роутер авторизации: вход и сведения о текущем пользователе."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from .. import auth, permissions

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(user):
    """Пользователь для фронтенда: роль, права и площадки (по ним интерфейс прячет лишнее)."""
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("display_name"),
        "role": user.get("role"),
        "role_id": user.get("role_id"),
        "role_name": user.get("role_name"),
        "is_admin": user["is_admin"],
        "permissions": sorted(permissions.CODES if user["is_admin"] else user["permissions"]),
        "marketplaces": list(user["marketplaces"]),
    }


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    """Вход: username = email, password = пароль. Возвращает JWT и пользователя."""
    user = auth.authenticate(form.username, form.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )
    user = auth.load_user(user["id"])
    if not auth.can(user, "login"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=auth.NO_LOGIN)
    token = auth.create_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": _user_out(user),
    }


@router.get("/me")
def me(user=Depends(auth.get_current_user)):
    return _user_out(user)
