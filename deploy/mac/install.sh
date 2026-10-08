#!/usr/bin/env bash
# Установить (или обновить) ежедневную копию базы на этот Mac:
#   bash deploy/mac/install.sh            — установить и включить
#   bash deploy/mac/install.sh --disable  — выключить расписание (копии остаются)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
LABEL=ru.autoprofi.backup-db
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--disable" ]]; then
  rm -f "$PLIST"
  echo "Расписание выключено. Копии в ~/AUTOPROFI_backups/db не тронуты."
  exit 0
fi

# Скрипт — вне «Рабочего стола»: фоновым задачам macOS доступ туда закрыт
mkdir -p "$HOME/.local/share/autoprofi" "$HOME/AUTOPROFI_backups/db" "$HOME/AUTOPROFI_backups/code" "$HOME/Library/LaunchAgents"
install -m 755 "$HERE/backup-db.sh" "$HOME/.local/share/autoprofi/backup-db.sh"
sed "s#__HOME__#$HOME#g" "$HERE/$LABEL.plist" >"$PLIST"
plutil -lint -s "$PLIST"
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Включено: проверка раз в 4 часа, копии в ~/AUTOPROFI_backups/db (журнал — backup.log)."
