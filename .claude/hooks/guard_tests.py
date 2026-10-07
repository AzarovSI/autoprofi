#!/usr/bin/env python3
"""Не даёт запустить тесты против рабочей БД.

Часть тестов пересоздаёт таблицы, а tests/*.py берут DB_DSN из окружения
(setdefault). Поэтому запуск pytest/unittest разрешён только когда DB_DSN
тестовой БД указан прямо в команде. Хук PreToolUse для Bash: exit 2 = блок.
"""
import json
import re
import sys

TEST_DBS = ("autoprofi_test", "wb_price_test")

cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
if not re.search(r"\b(pytest|unittest)\b", cmd):
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
