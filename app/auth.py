# -*- coding: utf-8 -*-
"""Авторизация: вход по email/паролю (bcrypt) и JWT-токены.

Токен передаётся в заголовке Authorization: Bearer <token>.
get_current_user() извлекает пользователя из токена для защищённых эндпоинтов —
вместе с ролью, правами и площадками (docs/access_roles.md). Права проверяют
require(код) / check(user, код) / check_marketplace(user, МП); коды — app/permissions.py.
"""
import os
import time
import datetime as dt

import jwt
import bcrypt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer

from . import db, permissions

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


def load_user(uid):
    """Пользователь с ролью, правами и площадками или None.

    Роль и права — тем же запросом, что и пользователь: проверка прав не добавляет
    обращений к БД, а изменённые права действуют со следующего запроса, без перезахода.
    """
    row = db.query_one(
        """SELECT u.id, u.email, u.display_name, u.role, u.role_id, u.disabled_at,
                  r.name AS role_name, r.is_admin, r.marketplaces,
                  ARRAY(SELECT p.permission FROM app_role_permissions p
                         WHERE p.role_id = u.role_id) AS permissions
           FROM app_users u LEFT JOIN app_roles r ON r.id = u.role_id
           WHERE u.id = %s""",
        (uid,),
    )
    return _apply_access(row) if row else None


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
    row = load_user(uid)
    # Отключённого пользователя выводим сразу, даже с действующим токеном: 401 — фронт покажет вход.
    if not row or row.get("disabled_at"):
        raise creds_exc
    if not can(row, "login"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NO_LOGIN)
    # Отмечаем активность по ЛЮБОМУ запросу к защищённым эндпоинтам
    # (с троттлингом). Так "последний визит" отражает реальное использование,
    # а не только момент входа (пользователь может не выходить из системы).
    _touch_last_seen(row["id"])
    return row


# --- Права доступа по ролям ---
NO_LOGIN = "Нет доступа к сайту: роль не назначена или вход для неё закрыт"
DISABLED = "Учётная запись отключена. Обратитесь к администратору."


def _apply_access(user):
    """Дополнить запись пользователя правами: is_admin, permissions, marketplaces.

    Владелец (OWNER_EMAILS) — всегда администратор, какая бы роль ни стояла.
    Без роли — никаких прав (даже на вход).
    """
    owner = (user.get("email") or "").strip().lower() in {e.lower() for e in OWNER_EMAILS}
    user["is_admin"] = bool(user.get("is_admin")) or owner
    user["permissions"] = frozenset(user.get("permissions") or ())
    user["marketplaces"] = (permissions.MARKETPLACES if user["is_admin"]
                            else tuple(m for m in permissions.MARKETPLACES
                                       if m in (user.get("marketplaces") or ())))
    return user


def can(user, code):
    """Есть ли у пользователя функция `code`. Администратору можно всё."""
    return bool(user.get("is_admin")) or code in (user.get("permissions") or ())


def _forbidden(codes):
    names = [permissions.TITLES[c] for c in codes]
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                         detail="Нет доступа: " + " или ".join(names))


def check(user, *codes):
    """403, если у пользователя нет ни одной из функций `codes`."""
    if not any(can(user, c) for c in codes):
        raise _forbidden(codes)
    return user


def require(*codes):
    """Зависимость эндпоинта: нужна хотя бы одна из функций `codes`."""
    unknown = set(codes) - permissions.CODES
    if unknown:
        raise ValueError(f"Неизвестные коды функций: {sorted(unknown)}")

    def dependency(user=Depends(get_current_user)):
        return check(user, *codes)
    return dependency


def check_marketplace(user, *mps):
    """403, если среди площадок роли нет хотя бы одной из `mps` (имена как в БД)."""
    allowed = permissions.MARKETPLACES if user.get("is_admin") else (user.get("marketplaces") or ())
    for mp in mps:
        if mp not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Нет доступа к данным площадки {mp}")
    return user


def require_tab(section, *codes, restrict_mp=True, resolve=permissions.mp_name):
    """Зависимость: раздел, вкладка площадки из параметра `marketplace` и функции `codes`.

    restrict_mp — площадка должна быть у роли (во всех разделах, кроме РНП).
    resolve — разбор параметра так же, как его разбирает сам роутер.
    """
    for code in (f"{section}.open", *codes):
        if code not in permissions.CODES:
            raise ValueError(f"Неизвестный код функции: {code}")

    def dependency(marketplace: str = Query("ozon"), user=Depends(get_current_user)):
        mp = resolve(marketplace)
        check(user, f"{section}.open")
        check(user, permissions.tab(section, mp))
        if restrict_mp:
            check_marketplace(user, mp)
        for code in codes:
            check(user, code)
        return user
    return dependency


def require_tabs(section, *mps):
    """Зависимость для сводных данных нескольких площадок: раздел, вкладки и площадки `mps`."""
    def dependency(user=Depends(require(f"{section}.open"))):
        for mp in mps:
            check(user, permissions.tab(section, mp))
            check_marketplace(user, mp)
        return user
    return dependency


def require_admin(user=Depends(get_current_user)):
    """Зависимость для эндпоинтов, доступных только роли «Администратор».

    Реальная защита на сервере: скрытие разделов на фронте — только удобство UI.
    """
    if not user.get("is_admin"):
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
