# -*- coding: utf-8 -*-
"""Слой доступа к БД PostgreSQL (Yandex Cloud).

Простые помощники поверх psycopg (psycopg3) с автокоммитом. Соединения берутся
из потокобезопасного пула (psycopg_pool.ConnectionPool), создаваемого лениво при
первом обращении. Пул переиспользует TLS-соединения, поэтому дорогое
рукопожатие платится один раз, а не на каждый запрос.
Все денежные/процентные метрики возвращаются как float.
"""
import os
import threading

import psycopg
import psycopg_pool

# Строка подключения берётся ТОЛЬКО из окружения (DB_DSN).
# Секреты не хранятся в исходниках — передаются через переменные окружения при запуске.
DB_DSN = os.environ.get("DB_DSN", "")
if not DB_DSN:
    raise RuntimeError(
        "Переменная окружения DB_DSN не задана. "
        "Укажите строку подключения к БД при запуске сервиса."
    )

# Пул соединений. Создаётся лениво (не на импорте модуля), потокобезопасно под
# Lock, чтобы импорт не падал, если БД временно недоступна.
MIN_CONN = 2
MAX_CONN = 20
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """Вернуть пул, создав его при первом обращении (double-checked locking)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                # autocommit=True задаётся на уровне новых соединений пула;
                # connect_timeout передаётся в libpq как параметр conninfo.
                _pool = psycopg_pool.ConnectionPool(
                    conninfo=DB_DSN,
                    min_size=MIN_CONN,
                    max_size=MAX_CONN,
                    kwargs={"autocommit": True, "connect_timeout": 10},
                    open=True,
                )
    return _pool


def get_conn():
    """Взять соединение ИЗ ПУЛА (с автокоммитом).

    Совместимый враппер: исторически открывал новое соединение. Теперь отдаёт
    коннект из пула. Вызывающий обязан вернуть его через `put_conn(conn)`.
    """
    conn = _get_pool().getconn()
    conn.autocommit = True
    return conn


def put_conn(conn, close=False):
    """Вернуть соединение в пул (или закрыть его, если close=True).

    В psycopg_pool у putconn нет аргумента close, поэтому «протухшее»
    соединение сначала закрываем сами — пул увидит закрытый коннект,
    выбросит его и при необходимости откроет новый, поддерживая min_size.
    """
    if conn is not None:
        if close:
            try:
                conn.close()
            except Exception:
                pass
        _get_pool().putconn(conn)


def get_cursor(conn):
    """Обычный курсор (без dict_row).

    dict_row строит dict для каждой строки на Python — на выборках в десятки
    тысяч строк это даёт заметное замедление. Обычный курсор отдаёт кортежи;
    dict-ы строятся вручную в query_all/one по именам колонок из cur.description.
    """
    return conn.cursor()


def _run(runner):
    """Выполнить runner(conn) на соединении из пула с одной повторной попыткой.

    Соединение из пула может «протухнуть» (managed БД/pgbouncer закрывает
    простаивающие коннекты). При OperationalError/InterfaceError закрываем это
    соединение, берём новое и повторяем запрос ОДИН раз. Соединение ВСЕГДА
    возвращается в пул в finally.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        try:
            return runner(conn)
        except (psycopg.OperationalError, psycopg.InterfaceError):
            # Протухшее соединение: закрыть и взять новое, повторить один раз.
            try:
                conn.close()
            except Exception:
                pass
            pool.putconn(conn)
            conn = None
            conn = pool.getconn()
            conn.autocommit = True
            return runner(conn)
    finally:
        if conn is not None:
            pool.putconn(conn)


def query_all(sql, params=None):
    """Вернуть все строки запроса как список dict.

    Обычный курсор + ручной zip(колонки, строка): во много раз быстрее
    dict-курсора на больших выборках. Сигнатура/формат результата не меняются.
    """
    def runner(conn):
        cur = get_cursor(conn)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    return _run(runner)


def query_one(sql, params=None):
    """Вернуть первую строку запроса как dict (или None)."""
    def runner(conn):
        cur = get_cursor(conn)
        cur.execute(sql, params or ())
        r = cur.fetchone()
        if not r:
            return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, r))
    return _run(runner)


def execute(sql, params=None):
    """Выполнить запрос без возврата строк. Вернуть число затронутых строк."""
    def runner(conn):
        cur = conn.cursor()
        cur.execute(sql, params or ())
        return cur.rowcount
    return _run(runner)


from contextlib import contextmanager


@contextmanager
def transaction():
    """Атомарный блок: несколько запросов в одной транзакции на одном коннекте.

    Использовать, когда нужно сделать несколько записей «или все, или никакая»
    (напр.: правка цены в витрине + пополнение истории). Внутри блока — методы
    tx.execute(sql, params) и tx.execute_values(sql, rows, template, page_size).
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        try:
            yield _Tx(conn)
            conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.autocommit = True
    finally:
        pool.putconn(conn)


class _Tx:
    """Обёртка вокруг соединения внутри transaction(): те же execute /
    execute_values, что и на модуле, но БЕЗ повторной попытки на
    OperationalError (в транзакции retry небезопасен) и БЕЗ возврата
    коннекта в пул — этим управляет сам transaction()."""
    def __init__(self, conn): self.conn = conn

    def execute(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        return cur.rowcount

    def query_one(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        r = cur.fetchone()
        if not r: return None
        cols = [d.name for d in cur.description]
        return dict(zip(cols, r))

    def query_all(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def execute_values(self, sql, rows, template=None, page_size=500):
        if not rows: return 0
        tmpl = template if template is not None else "(" + ",".join(["%s"] * len(rows[0])) + ")"
        nph = tmpl.count("%s")
        cur = self.conn.cursor()
        total = 0
        for i in range(0, len(rows), page_size):
            batch = rows[i:i + page_size]
            values_sql = ",".join([tmpl] * len(batch))
            full_sql = sql.replace("%s", values_sql, 1)
            flat = []
            for r in batch: flat.extend(r[:nph])
            cur.execute(full_sql, flat)
            rc = cur.rowcount
            total += rc if rc and rc > 0 else 0
        return total


def execute_values(sql, rows, template=None, page_size=500):
    """Пакетная вставка/обновление в ОДНОМ соединении.

    Используется для массовых операций (импорт справочника): вместо
    открытия отдельного TLS-соединения на каждую строку — один коннект и
    пакетная отправка значений. В psycopg3 нет psycopg2.extras.execute_values,
    поэтому VALUES собираем вручную: шаблон одной строки повторяется по числу
    строк батча, а плейсхолдер %s в `sql` заменяется на собранный список.

    sql      — запрос с одним плейсхолдером %s (на место VALUES);
    rows     — список кортежей значений;
    template — шаблон одной строки VALUES, напр. "(%s,%s, now())"; если None —
               строится автоматически по числу полей первой строки;
    page_size — размер батча (ограничивает число параметров на один INSERT).
    """
    if not rows:
        return 0

    # Шаблон одной строки VALUES и число позиционных плейсхолдеров в нём.
    tmpl = template if template is not None else "(" + ",".join(["%s"] * len(rows[0])) + ")"
    nph = tmpl.count("%s")

    def runner(conn):
        cur = conn.cursor()
        total = 0
        for i in range(0, len(rows), page_size):
            batch = rows[i:i + page_size]
            values_sql = ",".join([tmpl] * len(batch))
            full_sql = sql.replace("%s", values_sql, 1)
            flat = []
            for r in batch:
                flat.extend(r[:nph])
            cur.execute(full_sql, flat)
            rc = cur.rowcount
            total += rc if rc and rc > 0 else 0
        return total
    return _run(runner)
