#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="public"
DOMAIN="${CADDY_DOMAIN:-}"

usage() {
  cat <<'USAGE'
Usage: check_production_health.sh [--local] [domain]

Checks production DNS and HTTP health. Public mode resolves through DNS-over-HTTPS
and curls Cloudflare directly, so a broken local resolver does not hide the real
edge/origin state. --local tests the deployed Caddy/API path on the droplet.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --local)
      MODE="local"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      DOMAIN="$1"
      ;;
  esac
  shift
done

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

DOMAIN="${DOMAIN:-${CADDY_DOMAIN:-}}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%%/*}"

if [ -z "$DOMAIN" ]; then
  echo "CADDY_DOMAIN or a domain argument is required" >&2
  exit 2
fi

curl_health() {
  local url="$1"
  curl -fsS --max-time 20 "$url" >/dev/null
}

curl_health_resolved() {
  local host="$1"
  local ip="$2"
  local path="$3"
  curl -fsS --max-time 20 --resolve "$host:443:$ip" "https://$host$path" >/dev/null
}

public_dns_ips() {
  python3 - "$DOMAIN" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

domain = sys.argv[1]
query = urllib.parse.urlencode({"name": domain, "type": "A"})
request = urllib.request.Request(
    f"https://dns.google/resolve?{query}",
    headers={"Accept": "application/dns-json"},
)
with urllib.request.urlopen(request, timeout=15) as response:
    payload = json.load(response)
if payload.get("Status") != 0:
    raise SystemExit(f"DNS lookup failed with status {payload.get('Status')}")
answers = [
    answer["data"]
    for answer in payload.get("Answer", [])
    if answer.get("type") == 1 and answer.get("data")
]
if not answers:
    raise SystemExit("DNS lookup returned no A records")
print("\n".join(answers))
PY
}

if [ "$MODE" = "local" ]; then
  curl -fsSk --max-time 20 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/healthz" >/dev/null
  curl -fsSk --max-time 20 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/healthz" >/dev/null
  echo "local health ok for $DOMAIN"
  exit 0
fi

IPS=()
while IFS= read -r ip; do
  [ -n "$ip" ] && IPS+=("$ip")
done <<EOF
$(public_dns_ips)
EOF

for ip in "${IPS[@]}"; do
  curl_health_resolved "$DOMAIN" "$ip" "/healthz"
  curl_health_resolved "$DOMAIN" "$ip" "/api/healthz"
done

if ! curl_health "https://$DOMAIN/healthz"; then
  echo "warning: system resolver could not fetch https://$DOMAIN/healthz; public DNS-over-HTTPS path passed" >&2
fi

echo "public health ok for $DOMAIN via ${IPS[*]}"
