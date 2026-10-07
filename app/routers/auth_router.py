# -*- coding: utf-8 -*-
"""Роутер авторизации: вход и сведения о текущем пользователе."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from .. import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    """Вход: username = email, password = пароль. Возвращает JWT и пользователя."""
    user = auth.authenticate(form.username, form.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )
    token = auth.create_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user.get("display_name"),
            "role": user.get("role"),
        },
    }


@router.get("/me")
def me(user=Depends(auth.get_current_user)):
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("display_name"),
        "role": user.get("role"),
    }
