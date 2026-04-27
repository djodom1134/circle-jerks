#!/usr/bin/env bash
set -euo pipefail

ROOT=/srv/circlejerk

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

wait_for_apt() {
  while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
    || fuser /var/lib/apt/lists/lock >/dev/null 2>&1 \
    || fuser /var/cache/apt/archives/lock >/dev/null 2>&1; do
    echo "Waiting for apt lock..."
    sleep 5
  done
  dpkg --configure -a
}

wait_for_container_health() {
  local service="$1"
  local container
  container="$(docker compose -f docker-compose.prod.yml ps -q "$service")"
  if [ -z "$container" ]; then
    echo "No container found for $service" >&2
    return 1
  fi

  for _ in $(seq 1 60); do
    local status
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
      echo "$service is $status"
      return 0
    fi
    sleep 2
  done

  echo "$service did not become healthy" >&2
  docker compose -f docker-compose.prod.yml logs --tail=120 "$service" >&2 || true
  return 1
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

cat >/etc/cron.d/circlejerk-health <<CRON
*/5 * * * * root cd $ROOT/app && ./scripts/check_production_health.sh --local >/var/log/circlejerk-health.log 2>&1 || (cd $ROOT/app && docker compose -f docker-compose.prod.yml restart api web caddy >>/var/log/circlejerk-health.log 2>&1)
CRON

docker compose -f docker-compose.prod.yml up -d --build
wait_for_container_health valkey
wait_for_container_health api
wait_for_container_health web
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate caddy
./scripts/check_production_health.sh --local
docker compose -f docker-compose.prod.yml ps
