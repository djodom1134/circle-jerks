#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

DOMAIN="${1:-${CADDY_DOMAIN:-}}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%%/*}"

if [ -z "$DOMAIN" ]; then
  echo "CADDY_DOMAIN or a domain argument is required" >&2
  exit 2
fi

if ! command -v doctl >/dev/null 2>&1; then
  echo "doctl is required to provision DigitalOcean uptime checks" >&2
  exit 2
fi

DO_TOKEN="${DIGITAL_OCEAN_API_KEY:-${DIGITALOCEAN_ACCESS_TOKEN:-}}"
if [ -z "$DO_TOKEN" ]; then
  echo "DIGITAL_OCEAN_API_KEY or DIGITALOCEAN_ACCESS_TOKEN is required" >&2
  exit 2
fi

DOCTL=(doctl --access-token "$DO_TOKEN" --http-retry-max 2)
REGIONS="${UPTIME_REGIONS:-us_east,us_west,eu_west}"
ALERT_EMAILS="${UPTIME_ALERT_EMAILS:-${UPTIME_ALERT_EMAIL:-}}"

if [ -z "$ALERT_EMAILS" ]; then
  ALERT_EMAILS="$("${DOCTL[@]}" account get --format Email --no-header | awk 'NR == 1 {print $1}')"
fi

ensure_check() {
  local name="$1"
  local target="$2"
  local id
  id="$("${DOCTL[@]}" monitoring uptime list --format ID,Name,Target --no-header \
    | awk -v target="$target" '$3 == target {print $1; exit}')"

  if [ -z "$id" ]; then
    id="$("${DOCTL[@]}" monitoring uptime create "$name" \
      --target "$target" \
      --type https \
      --regions "$REGIONS" \
      --enabled true \
      --format ID \
      --no-header)"
    echo "created uptime check $name -> $target ($id)" >&2
  else
    echo "uptime check exists $name -> $target ($id)" >&2
  fi

  printf '%s' "$id"
}

ensure_down_alert() {
  local check_id="$1"
  local name="$2"
  local existing
  existing="$("${DOCTL[@]}" monitoring uptime alert list "$check_id" --format ID,Name,Type --no-header \
    | awk -v name="$name" '$2 == name && $3 == "down_global" {print $1; exit}')"

  if [ -n "$existing" ]; then
    echo "uptime alert exists $name ($existing)"
    return
  fi

  local alert_id
  alert_id="$("${DOCTL[@]}" monitoring uptime alert create "$check_id" \
    --name "$name" \
    --type down_global \
    --comparison greater_than \
    --period 2m \
    --emails "$ALERT_EMAILS" \
    --format ID \
    --no-header)"
  echo "created uptime alert $name ($alert_id)"
}

root_target="https://$DOMAIN/healthz"
api_target="https://$DOMAIN/api/healthz"

root_id="$(ensure_check "circlejerks-root-health" "$root_target" | tail -n 1)"
api_id="$(ensure_check "circlejerks-api-health" "$api_target" | tail -n 1)"

ensure_down_alert "$root_id" "circlejerks-root-down"
ensure_down_alert "$api_id" "circlejerks-api-down"

echo "uptime monitoring ready for $DOMAIN in $REGIONS"
