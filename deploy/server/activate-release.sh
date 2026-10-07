#!/usr/bin/env bash
# Включить выложенную версию: /srv/autoprofi/releases/<sha> (уже распакована deploy.sh).
#   sudo /srv/autoprofi/bin/activate-release <sha>   — переключиться на версию
#   sudo /srv/autoprofi/bin/activate-release --rollback — вернуть предыдущую
# Зависимости ставятся офлайн из recovery/wheels строго по recovery/requirements.lock
# (с проверкой хешей); окружение общее для всех версий с тем же lock.
# Если сайт после переключения не ответил на /health — автоматически возврат назад.
set -euo pipefail
APP=/srv/autoprofi
log() { echo "[выкладка] $*"; }

exec 9>/run/autoprofi-deploy.lock
flock -n 9 || { log "Уже идёт другая выкладка"; exit 1; }

health_ok() {
  for _ in $(seq 1 40); do
    curl -fsS --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}
switch_to() {
  ln -sfn "releases/$1" "$APP/current.new"
  mv -T "$APP/current.new" "$APP/current"
  systemctl restart autoprofi
}
current_sha() { [[ -L "$APP/current" ]] && basename "$(readlink -f "$APP/current")" || echo none; }

if [[ "${1:-}" == "--rollback" ]]; then
  now=$(current_sha)
  prev=$(ls -1t "$APP/releases" | grep -v -e '^\.' -e "^$now\$" | head -1 || true)
  [[ -n "$prev" ]] || { log "Нет прошлой версии для отката"; exit 1; }
  log "Откат: $now → $prev"
  switch_to "$prev"
  health_ok && log "Откат выполнен, сайт отвечает" || { log "ВНИМАНИЕ: после отката сайт не отвечает"; exit 1; }
  exit 0
fi

sha="${1:?Укажите версию (sha)}"
dir="$APP/releases/$sha"
[[ -d "$dir" ]] || { log "Нет папки $dir"; exit 1; }

lock="$dir/recovery/requirements.lock"
venv="$APP/venvs/$(sha256sum "$lock" | cut -c1-16)"
if [[ ! -x "$venv/bin/python" ]]; then
  log "Ставлю зависимости (офлайн, по lock с хешами) в $venv"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install -q --no-index --find-links "$dir/recovery/wheels" \
    --require-hashes -r "$lock"
  "$venv/bin/python" -m pip check
fi
ln -sfn "$venv" "$dir/.venv"
chown -R root:root "$dir" && chmod -R a+rX "$dir"

now=$(current_sha)
log "Переключаю: $now → $sha"
switch_to "$sha"
if health_ok; then
  log "Версия $sha работает"
else
  log "Версия $sha не ответила на /health — возвращаю $now"
  journalctl -u autoprofi -n 30 --no-pager || true
  if [[ "$now" != "none" && -d "$APP/releases/$now" ]]; then
    switch_to "$now"
    health_ok && log "Возврат на $now выполнен" || log "ВНИМАНИЕ: и прошлая версия не отвечает"
  fi
  exit 1
fi

# Храним 5 последних версий — для быстрого отката
ls -1t "$APP/releases" | grep -v '^\.' | tail -n +6 | while read -r old; do
  [[ "$old" == "$sha" || "$old" == "$now" ]] || rm -rf "${APP:?}/releases/$old"
done
