#!/usr/bin/env bash
# Полный бэкап кода на Google Диск: собрать архив (recovery/build_backup.py),
# загрузить в папку autoprofi_backups (rclone, remote «gdrive») и сверить MD5.
# Без секретов и без дампа БД — БД резервируется в Yandex Cloud.
#
#   bash backup_to_drive.sh            — собрать и загрузить (только при чистом git)
#   bash backup_to_drive.sh --local    — только собрать архив, без загрузки
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RCLONE="${RCLONE:-$HOME/.local/bin/rclone}"
DEST="gdrive:autoprofi_backups"

# Бэкап должен соответствовать коммиту: незакоммиченные правки в него не попадут молча.
if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo "Есть незакоммиченные изменения — сначала коммит, потом бэкап." >&2
  git -C "$ROOT" status --short >&2
  exit 1
fi

OUT="$(mktemp -d)"
ZIP="$(python3 "$ROOT/recovery/build_backup.py" --output "$OUT" | head -1)"
echo "Архив: $ZIP ($(du -h "$ZIP" | cut -f1)), коммит $(git -C "$ROOT" rev-parse --short HEAD)"
[[ "${1:-}" == "--local" ]] && exit 0

NAME="$(basename "$ZIP")"
"$RCLONE" copy "$ZIP" "$DEST/" 2>/dev/null
LOCAL_MD5="$(md5 -q "$ZIP" 2>/dev/null || md5sum "$ZIP" | cut -d' ' -f1)"
REMOTE_MD5="$("$RCLONE" md5sum "$DEST/$NAME" 2>/dev/null | cut -d' ' -f1)"
if [[ "$LOCAL_MD5" != "$REMOTE_MD5" ]]; then
  echo "ОШИБКА: MD5 на Диске ($REMOTE_MD5) не совпал с локальным ($LOCAL_MD5)" >&2
  exit 1
fi
echo "Загружено на Google Диск: autoprofi_backups/$NAME, MD5 совпал ($LOCAL_MD5)"
