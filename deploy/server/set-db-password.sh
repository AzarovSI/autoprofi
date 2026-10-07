#!/usr/bin/env bash
# Задать пароль пользователя БД autoprofi_app. Пароль вводится скрыто, нигде не выводится
# и хранится в /etc/autoprofi/pgpass (0600, владелец — служба сайта); DB_DSN ссылается
# на этот файл через passfile=. Запускает владелец:
#   ssh -t -i ~/.ssh/autoprofi_admin autoprofi@<IP> sudo /srv/autoprofi/bin/set-db-password
set -euo pipefail
ENV=/etc/autoprofi/env
PASSFILE=/etc/autoprofi/pgpass
read -r -s -p "Пароль пользователя БД autoprofi_app (из Lockbox): " pw; echo
[[ -n "$pw" ]] || { echo "Пустой пароль — ничего не изменено."; exit 1; }

DSN=$(sed -n 's/^DB_DSN=//p' "$ENV")
host=$(sed -E 's/.*host=([^ ]+).*/\1/' <<<"$DSN")
# Формат pgpass: в пароле экранируются \ и :
esc=${pw//\\/\\\\}
esc=${esc//:/\\:}
tmp=$(mktemp /etc/autoprofi/pgpass.XXXXXX)
printf '%s:6432:avtoprofi:autoprofi_app:%s\n' "$host" "$esc" >"$tmp"
chown autoprofi-app:autoprofi-app "$tmp" && chmod 600 "$tmp" && mv "$tmp" "$PASSFILE"
unset pw esc

echo "Пароль сохранён. Проверка подключения к БД от имени службы сайта:"
if sudo -u autoprofi-app DB_DSN="$DSN" /srv/autoprofi/current/.venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ['DB_DSN'], connect_timeout=10) as c:
    print('  OK:', c.execute('SELECT current_user, current_database()').fetchone())
"; then
  systemctl restart autoprofi && echo "Сайт перезапущен."
else
  echo "  Не удалось подключиться: неверный пароль, нет прав или версия ещё не выложена."
fi
