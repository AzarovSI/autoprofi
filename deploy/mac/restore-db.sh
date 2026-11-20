#!/usr/bin/env bash
# Восстановить копию базы АВТОПРОФИ (из backup-db.sh) в ПУСТУЮ базу.
#   bash deploy/mac/restore-db.sh <файл.dump> "<строка подключения к новой базе>"
# Пример: bash deploy/mac/restore-db.sh ~/AUTOPROFI_backups/db/autoprofi_public_2026-10-09_0900.dump \
#           "host=... port=5432 dbname=avtoprofi user=... sslmode=verify-full"
# Восстанавливает таблицы, данные, индексы, функции и триггеры схемы public.
# Строка CREATE SCHEMA public пропускается: в новой базе схема уже есть.
set -euo pipefail
DUMP="${1:?Укажите файл копии}"
CONN="${2:?Укажите строку подключения к новой (пустой) базе}"
PG="/Applications/Postgres.app/Contents/Versions/latest/bin"

existing=$("$PG/psql" "$CONN" -X -At -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
[[ "$existing" == "0" ]] || { echo "В целевой базе уже есть таблицы public ($existing) — восстанавливаю только в пустую базу."; exit 1; }

list=$(mktemp); sql=$(mktemp)
trap 'rm -f "$list" "$sql"' EXIT
"$PG/pg_restore" -l "$DUMP" | grep -vE "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - public " >"$list"
# Через текст, а не pg_restore -d: управляемый PostgreSQL TimeWeb ставит колонкам новых таблиц служебный параметр
# pg_attver (с 20.11.2026 — у abc_revision), обычный PostgreSQL его не знает и восстановление обрывается.
# Эти строки пропускаем. Сначала весь SQL в файл (битая копия — остановка до записи в базу), затем одной транзакцией:
# первая ошибка откатывает всё.
"$PG/pg_restore" -L "$list" --no-owner --no-privileges -f "$sql" "$DUMP"
grep -vE '^ALTER TABLE ONLY [^ ]+ ALTER COLUMN [^ ]+ SET \(pg_attver=[0-9]+\);$' "$sql" \
  | "$PG/psql" "$CONN" -X -q -v ON_ERROR_STOP=1 --single-transaction >/dev/null
"$PG/psql" "$CONN" -X -At -c "SELECT 'восстановлено таблиц: ' || count(*) FROM pg_tables WHERE schemaname='public'"
echo "Дальше: права пользователю сайта (docs/operations.md) и проверка recovery/check_database.py."
