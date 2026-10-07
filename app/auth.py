# -*- coding: utf-8 -*-
"""Авторизация: вход по email/паролю (bcrypt) и JWT-токены.

Токен передаётся в заголовке Authorization: Bearer <token>.
get_current_user() извлекает пользователя из токена для защищённых эндпоинтов.
"""
import os
import time
import datetime as dt

import jwt
import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from . import db

# Ключ подписи JWT берётся только из окружения; дефолта-заглушки нет.
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError(
        "Переменная окружения SECRET_KEY не задана. "
        "Укажите секретный ключ подписи токенов при запуске сервиса."
    )
ALGORITHM = "HS256"
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "24"))

# Троттлинг обновления "последнего визита": не чаще, чем раз в N секунд на
# одного пользователя. Кэш в памяти процесса: {user_id: monotonic_ts}.
# Это исключает апдейт last_seen_at на КАЖДЫЙ запрос (иначе на каждый клик —
# отдельный UPDATE). Точности "раз в ~1.5 минуты" достаточно для отметки визита.
_LAST_SEEN_THROTTLE_SEC = 90
_last_seen_cache = {}


def _touch_last_seen(user_id):
    """Отметить активность пользователя (last_seen_at = now()) с троттлингом.

    Ошибки БД здесь намеренно проглатываются: обновление метки визита не должно
    ломать основной запрос пользователя.
    """
    now = time.monotonic()
    prev = _last_seen_cache.get(user_id)
    if prev is not None and (now - prev) < _LAST_SEEN_THROTTLE_SEC:
        return
    _last_seen_cache[user_id] = now
    try:
        db.execute("UPDATE app_users SET last_seen_at = now() WHERE id = %s", (user_id,))
    except Exception:
        # Не мешаем основному запросу, если апдейт метки не прошёл.
        pass

# Схема OAuth2: фронтенд шлёт POST на /api/auth/login с полями username/password.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def verify_password(plain, hashed):
    """Проверка пароля напрямую через bcrypt (без passlib — совместимо с bcrypt 5.x)."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def hash_password(plain):
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def authenticate(email, password):
    """Проверить email+пароль. Вернуть запись пользователя или None."""
    row = db.query_one(
        "SELECT id, email, password_hash, display_name, role FROM app_users WHERE email = %s",
        (email,),
    )
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return row


def create_token(user):
    """Собрать JWT с sub=id, email и сроком жизни."""
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=TOKEN_TTL_HOURS)
    payload = {"sub": str(user["id"]), "email": user["email"], "exp": exp}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)):
    """Достать пользователя из Bearer-токена. 401 при отсутствии/ошибке."""
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не авторизовано",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise creds_exc
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub")
    except Exception:
        raise creds_exc
    if not uid:
        raise creds_exc
    row = db.query_one(
        "SELECT id, email, display_name, role FROM app_users WHERE id = %s",
        (uid,),
    )
    if not row:
        raise creds_exc
    # Отмечаем активность по ЛЮБОМУ запросу к защищённым эндпоинтам
    # (с троттлингом). Так "последний визит" отражает реальное использование,
    # а не только момент входа (пользователь может не выходить из системы).
    _touch_last_seen(row["id"])
    return row


def require_admin(user=Depends(get_current_user)):
    """Зависимость для эндпоинтов, доступных только администратору.

    Реальная защита на сервере: скрытие разделов на фронте — только удобство UI.
    Если роль не admin — 403 Доступ запрещён.
    """
    if (user.get("role") or "").lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён: требуются права администратора",
        )
    return user


# --- Владелец (owner-only разделы в разработке) ---
# Разделы «в разработке» (напр. «Склады») временно доступны ТОЛЬКО
# владельцу. Когда раздел готов и его надо открыть всем — заменить
# зависимость require_owner на require_admin на соответствующих эндпоинтах.
OWNER_EMAILS = {"azarov.si@gmail.com"}


def require_owner(user=Depends(get_current_user)):
    """Зависимость для эндпоинтов owner-only разделов (напр. «Склады»).

    Доступ только у e-mail'ов из OWNER_EMAILS. Иначе — 403.
    """
    email = (user.get("email") or "").strip().lower()
    if email not in {e.lower() for e in OWNER_EMAILS}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён: раздел в разработке, доступен только владельцу",
        )
    return user
