#!/usr/bin/env bash
# Разовая (и повторяемая) настройка сервера ТД АВТОПРОФИ на чистой Ubuntu 24.04.
# Запуск на сервере от root из папки с файлами deploy/server:
#   sudo SITE_HOST=<IP или домен> ./setup.sh
# Повторный запуск безопасен: сделанное не ломается, секреты не перегенерируются.
# БД на сервере нет — используется Managed PostgreSQL в той же облачной сети.
set -euo pipefail
: "${SITE_HOST:?Укажите SITE_HOST — адрес сайта (IP или домен)}"
HERE=$(cd "$(dirname "$0")" && pwd)
APP=/srv/autoprofi
DB_HOST=rc1b-4fhafeuci7krrg56.mdb.yandexcloud.net
export DEBIAN_FRONTEND=noninteractive

log() { echo; echo "=== $*"; }

log "Обновление системы и программы"
apt-get update -q
apt-get -y -q upgrade
apt-get -y -q install nginx curl ca-certificates python3-venv unattended-upgrades jq

log "Запасная память на диске (2 ГБ)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi
echo 'vm.swappiness=10' >/etc/sysctl.d/90-autoprofi.conf
sysctl -q -p /etc/sysctl.d/90-autoprofi.conf

log "SSH: вход только по ключу, root не входит"
cat >/etc/ssh/sshd_config.d/10-autoprofi.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t && systemctl reload ssh

log "Пользователь и папки сайта"
id autoprofi-app >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin autoprofi-app
mkdir -p "$APP/releases" "$APP/venvs" "$APP/bin" /etc/autoprofi /var/www/letsencrypt
chmod 755 "$APP"

log "Сертификат CA Yandex Cloud для проверки TLS базы"
curl -fsS -o /etc/autoprofi/yandex-ca.pem https://storage.yandexcloud.net/cloud-certs/CA.pem
chmod 644 /etc/autoprofi/yandex-ca.pem

log "Настройки сайта (/etc/autoprofi/env создаётся один раз)"
if [[ ! -f /etc/autoprofi/env ]]; then
  umask 077
  cat >/etc/autoprofi/env <<EOF
# Пароль БД — в /etc/autoprofi/pgpass, задаётся: sudo /srv/autoprofi/bin/set-db-password
DB_DSN=host=$DB_HOST port=6432 dbname=avtoprofi user=autoprofi_app sslmode=verify-full sslrootcert=/etc/autoprofi/yandex-ca.pem passfile=/etc/autoprofi/pgpass
SECRET_KEY=$(openssl rand -hex 32)
TOKEN_TTL_HOURS=24
EOF
  umask 022
fi
chown root:autoprofi-app /etc/autoprofi/env
chmod 640 /etc/autoprofi/env

log "Скрипты и служба"
install -m 755 "$HERE/activate-release.sh" "$APP/bin/activate-release"
install -m 755 "$HERE/set-db-password.sh" "$APP/bin/set-db-password"
install -m 644 "$HERE/autoprofi.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable autoprofi.service >/dev/null

log "Сертификат для https (Let's Encrypt; для IP нужен certbot 5.4+, ставим в отдельную папку)"
[[ -x /opt/certbot/bin/pip ]] || python3 -m venv /opt/certbot
/opt/certbot/bin/pip install -q --upgrade pip 'certbot>=5.4'
ln -sfn /opt/certbot/bin/certbot /usr/bin/certbot
install -m 644 "$HERE/certbot-renew.service" "$HERE/certbot-renew.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now certbot-renew.timer
if [[ ! -f /etc/letsencrypt/live/autoprofi/fullchain.pem ]]; then
  # Пока сертификата нет, nginx отвечает только на http — чтобы Let's Encrypt проверил адрес
  cat >/etc/nginx/sites-available/autoprofi <<'EOF'
server {
    listen 80 default_server;
    server_name _;
    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 503; }
}
EOF
  ln -sfn /etc/nginx/sites-available/autoprofi /etc/nginx/sites-enabled/autoprofi
  rm -f /etc/nginx/sites-enabled/default
  nginx -t -q && systemctl reload nginx
  identifier=(--domain "$SITE_HOST")
  [[ "$SITE_HOST" =~ ^[0-9.]+$ ]] && identifier=(--ip-address "$SITE_HOST" --preferred-profile shortlived)
  certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
    --webroot --webroot-path /var/www/letsencrypt --cert-name autoprofi "${identifier[@]}"
fi
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' >/etc/letsencrypt/renewal-hooks/deploy/reload-nginx
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx

log "nginx с https"
sed "s/__SITE_HOST__/$SITE_HOST/g" "$HERE/nginx-autoprofi.conf" >/etc/nginx/sites-available/autoprofi
ln -sfn /etc/nginx/sites-available/autoprofi /etc/nginx/sites-enabled/autoprofi
rm -f /etc/nginx/sites-enabled/default
nginx -t -q && systemctl reload nginx

log "Готово. Дальше: задать пароль БД (set-db-password) и выложить версию (deploy/deploy.sh с Mac)"
