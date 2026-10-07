#!/usr/bin/env python3
"""Не даёт запустить тесты против рабочей БД.

Часть тестов пересоздаёт таблицы, а tests/*.py берут DB_DSN из окружения
(setdefault). Поэтому запуск pytest/unittest разрешён только когда DB_DSN
тестовой БД указан прямо в команде. Хук PreToolUse для Bash: exit 2 = блок.
"""
import json
import re
import sys

TEST_DBS = ("wb_price_test",)  # autoprofi_test — для запуска сайта, тесты делают TRUNCATE

cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
# Установка пакетов (pip/uv install pytest) — не запуск тестов.
# Текст в кавычках (сообщения коммитов и т.п.) запуском не считается.
unquoted = re.sub(r"\"[^\"]*\"|'[^']*'", "", cmd)
segments = [s for s in re.split(r"&&|\|\||[;|\n]", unquoted)
            if not re.search(r"\b(pip|uv)\b.*\b(install|add)\b", s)]
if not any(re.search(r"(^|[\s/(])pytest(\s|$)|-m\s+(pytest|unittest)\b", s)
           for s in segments):
    sys.exit(0)
dsn = re.search(r"DB_DSN=(\"[^\"]*\"|'[^']*'|\S+)", cmd)
if dsn and any(db in dsn.group(1) for db in TEST_DBS):
    sys.exit(0)
print(
    "Тесты запускаются только на тестовой БД: укажите DB_DSN с "
    + " или ".join(TEST_DBS)
    + " прямо в команде (часть тестов пересоздаёт таблицы).",
    file=sys.stderr,
)
sys.exit(2)
