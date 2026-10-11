#!/usr/bin/env bash
# Разовая (и повторяемая) настройка сервера ТД АВТОПРОФИ на чистой Ubuntu 24.04.
# Запуск на сервере от root из папки с файлами deploy/server:
#   sudo SITE_HOST=<IP или домен> ./setup.sh
# Повторный запуск безопасен: сделанное не ломается, секреты не перегенерируются.
# БД на сервере нет — используется облачная БД TimeWeb Cloud в той же приватной сети.
set -euo pipefail
: "${SITE_HOST:?Укажите SITE_HOST — адрес сайта (IP или домен)}"
HERE=$(cd "$(dirname "$0")" && pwd)
APP=/srv/autoprofi
# Имя из сертификата БД (по нему проверяется TLS) и её адрес в приватной сети
DB_HOST=1a1fbe83529bcc71516c3259.twc1.net
DB_ADDR=192.168.0.4
DB_PORT=5432
ADMIN=autoprofi
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

log "Администратор $ADMIN: вход по ключам root, sudo без пароля (нужно deploy.sh)"
id "$ADMIN" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$ADMIN"
install -d -m 700 -o "$ADMIN" -g "$ADMIN" "/home/$ADMIN/.ssh"
if [[ ! -s "/home/$ADMIN/.ssh/authorized_keys" ]]; then
  [[ -s /root/.ssh/authorized_keys ]] || { echo "Нет ключей в /root/.ssh/authorized_keys — вход не закрываю"; exit 1; }
  install -m 600 -o "$ADMIN" -g "$ADMIN" /root/.ssh/authorized_keys "/home/$ADMIN/.ssh/authorized_keys"
fi
echo "$ADMIN ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/90-$ADMIN"
chmod 440 "/etc/sudoers.d/90-$ADMIN"
visudo -cq

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

log "Сертификат CA TimeWeb Cloud для проверки TLS базы"
curl -fsS -o /etc/autoprofi/db-ca.pem https://st.timeweb.com/cloud-static/ca.crt
chmod 644 /etc/autoprofi/db-ca.pem

log "Настройки сайта (/etc/autoprofi/env создаётся один раз)"
if [[ ! -f /etc/autoprofi/env ]]; then
  umask 077
  cat >/etc/autoprofi/env <<EOF
# Пароль БД — в /etc/autoprofi/pgpass, задаётся: sudo /srv/autoprofi/bin/set-db-password
DB_DSN=host=$DB_HOST hostaddr=$DB_ADDR port=$DB_PORT dbname=avtoprofi user=autoprofi_app sslmode=verify-full sslrootcert=/etc/autoprofi/db-ca.pem passfile=/etc/autoprofi/pgpass
SECRET_KEY=$(openssl rand -hex 32)
TOKEN_TTL_HOURS=24
EOF
  umask 022
fi
# Фоновый прогрев дерева «РНП заказы» (app/rnp_warmup.py); дописывается и в старый env
grep -q '^RNP_WARMUP=' /etc/autoprofi/env || echo 'RNP_WARMUP=1' >>/etc/autoprofi/env
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
