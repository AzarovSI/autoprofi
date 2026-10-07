#!/usr/bin/env bash
# Выложить текущий коммит на сервер (запуск на Mac):
#   bash deploy/deploy.sh             — выложить HEAD
#   bash deploy/deploy.sh --rollback  — вернуть предыдущую версию
# Код передаётся архивом git по SSH в /srv/autoprofi/releases/<sha>, затем сервер
# ставит зависимости, переключает версию и проверяет /health (при сбое — откат сам).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${AUTOPROFI_HOST:-51.250.30.154}"
SSH=(ssh -i "$HOME/.ssh/autoprofi_admin" -o IdentitiesOnly=yes "autoprofi@$HOST")

if [[ "${1:-}" == "--rollback" ]]; then
  "${SSH[@]}" sudo /srv/autoprofi/bin/activate-release --rollback
  exit
fi

if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo "Есть незакоммиченные изменения — выкладывается только закоммиченный код." >&2
  git -C "$ROOT" status --short >&2
  exit 1
fi
sha=$(git -C "$ROOT" rev-parse HEAD)
echo "[выкладка] $sha → $HOST"
git -C "$ROOT" archive --format=tar HEAD |
  "${SSH[@]}" "sudo rm -rf /srv/autoprofi/releases/.incoming-$sha &&
    sudo mkdir -p /srv/autoprofi/releases/.incoming-$sha &&
    sudo tar -x -C /srv/autoprofi/releases/.incoming-$sha &&
    sudo rm -rf /srv/autoprofi/releases/$sha &&
    sudo mv /srv/autoprofi/releases/.incoming-$sha /srv/autoprofi/releases/$sha"
"${SSH[@]}" sudo /srv/autoprofi/bin/activate-release "$sha"
