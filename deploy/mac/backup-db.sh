#!/usr/bin/env bash
# Ежедневная копия данных АВТОПРОФИ (только схема public) на Mac владельца.
# Запускается launchd раз в 4 часа; делает копию, если за последние 20 часов
# удачной ещё не было (Mac мог спать или быть выключен в «плановое» время).
#   ~/.local/share/autoprofi/backup-db.sh          — по расписанию (или вручную)
#   ~/.local/share/autoprofi/backup-db.sh --force  — сделать копию сейчас
# Подключение: сервис autoprofi_ro из ~/.pg_service.conf (только чтение, пароль в
# ~/.pgpass). Схема azarov_capital в копию не входит.
set -uo pipefail

DEST="${AUTOPROFI_BACKUP_DIR:-$HOME/AUTOPROFI_backups/db}"
CONN="${AUTOPROFI_DB_CONN:-service=autoprofi_ro}"
EXPECT_TABLES="${AUTOPROFI_EXPECT_TABLES:-31}"
KEEP_DAYS=30
PG="/Applications/Postgres.app/Contents/Versions/latest/bin"
LOG="$DEST/backup.log"
OK_STAMP="$DEST/.last-ok"
ALERT_STAMP="$DEST/.last-alert"

mkdir -p "$DEST"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }
notify() { osascript -e "display notification \"$1\" with title \"АВТОПРОФИ: копия базы\"" >/dev/null 2>&1 || true; }
fail() {
  log "ОШИБКА: $*"
  # Уведомление — не чаще раза в сутки, чтобы не надоедать при долгом сбое
  if [[ ! -f "$ALERT_STAMP" ]] || [[ -n "$(find "$ALERT_STAMP" -mmin +1440 2>/dev/null)" ]]; then
    notify "Не удалось сделать копию: $*"
    touch "$ALERT_STAMP"
  fi
  exit 1
}

if [[ "${1:-}" != "--force" && -f "$OK_STAMP" && -z "$(find "$OK_STAMP" -mmin +1200 2>/dev/null)" ]]; then
  exit 0  # свежая копия уже есть
fi
[[ -x "$PG/pg_dump" ]] || fail "не найден pg_dump (Postgres.app)"

name="autoprofi_public_$(date '+%Y-%m-%d_%H%M').dump"
tmp="$DEST/.incoming-$name"
log "Начало: $name"
if ! "$PG/pg_dump" "$CONN connect_timeout=20" -n public -Fc -f "$tmp" 2>>"$LOG"; then
  rm -f "$tmp"
  fail "база недоступна или нет прав (подробности в $LOG)"
fi

# Проверка: архив читается и содержит данные всех таблиц
tables=$("$PG/pg_restore" -l "$tmp" 2>>"$LOG" | grep -c " TABLE DATA public ")
if [[ "$tables" -lt "$EXPECT_TABLES" ]]; then
  rm -f "$tmp"
  fail "в копии $tables таблиц из $EXPECT_TABLES"
fi
mv "$tmp" "$DEST/$name"
touch "$OK_STAMP"
rm -f "$ALERT_STAMP"
log "Готово: $name, таблиц $tables, $(du -h "$DEST/$name" | cut -f1)"

# Храним 30 дней (удаляются только файлы копий в этой папке)
find "$DEST" -maxdepth 1 -name 'autoprofi_public_*.dump' -mtime +"$KEEP_DAYS" -print -delete >>"$LOG" 2>&1
exit 0
