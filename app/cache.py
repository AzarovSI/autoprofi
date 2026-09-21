# -*- coding: utf-8 -*-
"""Простой потокобезопасный in-memory кэш ответов API.

Назначение: тяжёлые агрегирующие эндпоинты (напр. дерево «РНП заказы Ozon»,
~13 МБ JSON, ~8 с сборки) считать один раз и отдавать из памяти на повторных
запросах. Так первый вход за день остаётся 5–8 с, а все последующие
переключения на раздел — мгновенные.

Механика:
  • Значения хранятся по строковому ключу (обычно marketplace+период+фильтры).
  • Инвалидация — через ГЛОБАЛЬНУЮ ВЕРСИЮ (namespace-версию). При загрузке
    новых данных вызывается bump(namespace) — версия namespace растёт, и все
    ранее сохранённые под старой версией значения перестают считаться валидными
    (фактически отбрасываются при следующем обращении). Это надёжнее, чем
    перечислять конкретные ключи: не нужно знать все комбинации периодов/фильтров.
  • TTL — подстраховка: даже без явной инвалидации значение протухает за ttl
    секунд (на случай, если какой-то путь записи не забампил версию).

Кэш живёт в памяти процесса uvicorn. При перезапуске/редеплое он пуст —
это ожидаемо (первый запрос после старта прогреет его заново).
"""
import threading
import time

# Версии пространств имён: namespace -> int. bump() увеличивает версию,
# делая все старые записи этого namespace невалидными.
_versions = {}
# Хранилище: key -> (namespace, version_at_store, expires_at, value)
_store = {}
_lock = threading.Lock()


def version(namespace: str) -> int:
    """Текущая версия пространства имён (0, если ещё не бампалась)."""
    with _lock:
        return _versions.get(namespace, 0)


def bump(namespace: str) -> int:
    """Инвалидировать все записи пространства имён (увеличить его версию).

    Вызывается из точек ЗАПИСИ данных (загрузка отчётов, правки справочника).
    Возвращает новую версию.
    """
    with _lock:
        v = _versions.get(namespace, 0) + 1
        _versions[namespace] = v
        return v


def get(namespace: str, key: str):
    """Вернуть валидное значение из кэша или None.

    None означает «нет валидного значения» — надо посчитать заново.
    Значение невалидно, если: его нет; namespace забамплен позже сохранения;
    истёк TTL.
    """
    now = time.time()
    with _lock:
        rec = _store.get(key)
        if rec is None:
            return None
        ns, ver_at_store, expires_at, value = rec
        if ns != namespace:
            return None
        if expires_at is not None and now >= expires_at:
            _store.pop(key, None)
            return None
        if ver_at_store != _versions.get(namespace, 0):
            _store.pop(key, None)
            return None
        return value


def set(namespace: str, key: str, value, ttl: float = 3600.0):
    """Сохранить значение под текущей версией namespace с TTL (сек)."""
    now = time.time()
    with _lock:
        ver = _versions.get(namespace, 0)
        expires_at = (now + ttl) if ttl and ttl > 0 else None
        _store[key] = (namespace, ver, expires_at, value)


def stats() -> dict:
    """Диагностика (для отладки): число записей и версии namespace'ов."""
    with _lock:
        return {"entries": len(_store), "versions": dict(_versions)}
