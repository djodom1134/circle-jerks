#!/usr/bin/env bash
set -euo pipefail

ROOT=/srv/circlejerk

wait_for_apt() {
  while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
    || fuser /var/lib/apt/lists/lock >/dev/null 2>&1 \
    || fuser /var/cache/apt/archives/lock >/dev/null 2>&1; do
    echo "Waiting for apt lock..."
    sleep 5
  done
  dpkg --configure -a
}

mkdir -p "$ROOT/data" "$ROOT/redis" "$ROOT/caddy/data" "$ROOT/caddy/config" "$ROOT/backups"
chmod 700 "$ROOT/data" "$ROOT/redis" "$ROOT/backups"

export DEBIAN_FRONTEND=noninteractive
cloud-init status --wait || true
wait_for_apt
apt-get update
apt-get install -y ca-certificates curl fail2ban sqlite3 ufw

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker
systemctl enable --now fail2ban

ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

cat >/etc/cron.d/circlejerk-backup <<CRON
17 * * * * root $ROOT/app/scripts/backup_sqlite.sh >/var/log/circlejerk-backup.log 2>&1
CRON

docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
