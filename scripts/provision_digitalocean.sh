#!/usr/bin/env bash
set -euo pipefail

NAME="${DO_DROPLET_NAME:-circlejerks-prod}"
REGION="${DO_REGION:-sfo3}"
SIZE="${DO_SIZE:-s-1vcpu-2gb}"
IMAGE="${DO_IMAGE:-ubuntu-24-04-x64}"
TAG="${DO_TAG:-circlejerks-prod}"
SSH_PUBLIC_KEY_PATH="${SSH_PUBLIC_KEY_PATH:-$HOME/.ssh/id_ed25519.pub}"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${SSH_PUBLIC_KEY_PATH%.pub}}"
SSH_ALLOWED_CIDR="${SSH_ALLOWED_CIDR:-0.0.0.0/0}"
ROOT="/srv/circlejerk"

if [ ! -f .env ]; then
  echo ".env is required and must include DIGITAL_OCEAN_API_KEY plus app secrets" >&2
  exit 1
fi

dotenv_get() {
  python3 - "$1" <<'PY'
from pathlib import Path
import re
import sys

target = sys.argv[1]
for raw in Path(".env").read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() != target:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    else:
        value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
    print(value, end="")
    break
PY
}

env_value() {
  local key="$1"
  local current="${!key-}"
  if [ -n "$current" ]; then
    printf '%s' "$current"
  else
    dotenv_get "$key"
  fi
}

env_quote() {
  python3 -c 'import sys; print("'"'"'" + sys.stdin.read().replace("'"'"'", "'"'"'\"'"'"'\"'"'"'") + "'"'"'", end="")'
}

DIGITAL_OCEAN_API_KEY="$(env_value DIGITAL_OCEAN_API_KEY)"
CLOUDFLARE_API_TOKEN="$(env_value CLOUDFLARE_API_TOKEN)"
CLOUDFLARE_API_KEY="$(env_value CLOUDFLARE_API_KEY)"
CADDY_EMAIL="$(env_value CADDY_EMAIL)"
CADDY_DOMAIN="$(env_value CADDY_DOMAIN)"
GROQ_API_KEY="$(env_value GROQ_API_KEY)"
OPENSKY="$(env_value OPENSKY)"
OPENSKY_CLIENT_ID="$(env_value OPENSKY_CLIENT_ID)"
OPENSKY_CLIENT_SECRET="$(env_value OPENSKY_CLIENT_SECRET)"
CIRCLEJERK_TIMEZONE="$(env_value CIRCLEJERK_TIMEZONE)"
CIRCLEJERK_DEFAULT_AIRPORT_ICAO="$(env_value CIRCLEJERK_DEFAULT_AIRPORT_ICAO)"
CIRCLEJERK_ADMIN_USERNAME="$(env_value CIRCLEJERK_ADMIN_USERNAME)"
CIRCLEJERK_ADMIN_PASSWORD="$(env_value CIRCLEJERK_ADMIN_PASSWORD)"
CIRCLEJERK_ADMIN_PASSWORD_HASH="$(env_value CIRCLEJERK_ADMIN_PASSWORD_HASH)"
CIRCLEJERK_BUY_ME_COFFEE_URL="$(env_value CIRCLEJERK_BUY_ME_COFFEE_URL)"
CIRCLEJERK_LIVE_SOURCE_PRIORITY="$(env_value CIRCLEJERK_LIVE_SOURCE_PRIORITY)"
CIRCLEJERK_LIVE_POLL_INTERVAL_SECONDS="$(env_value CIRCLEJERK_LIVE_POLL_INTERVAL_SECONDS)"
CIRCLEJERK_BBOX_MERGE_DISTANCE_NM="$(env_value CIRCLEJERK_BBOX_MERGE_DISTANCE_NM)"
CIRCLEJERK_OPENSKY_HISTORICAL_ENABLED="$(env_value CIRCLEJERK_OPENSKY_HISTORICAL_ENABLED)"

if [ -z "${DIGITAL_OCEAN_API_KEY:-}" ]; then
  echo "DIGITAL_OCEAN_API_KEY is missing from .env" >&2
  exit 1
fi

if [ ! -f "$SSH_PUBLIC_KEY_PATH" ] || [ ! -f "$SSH_PRIVATE_KEY_PATH" ]; then
  echo "SSH key pair not found. Set SSH_PUBLIC_KEY_PATH to a usable public key." >&2
  exit 1
fi

admin_password_file="${CIRCLEJERK_ADMIN_PASSWORD_FILE:-.circlejerk-admin-password}"
if [ -z "${CIRCLEJERK_ADMIN_PASSWORD:-}" ] && [ -z "${CIRCLEJERK_ADMIN_PASSWORD_HASH:-}" ]; then
  if [ -f "$admin_password_file" ]; then
    CIRCLEJERK_ADMIN_PASSWORD="$(head -n 1 "$admin_password_file")"
  else
    CIRCLEJERK_ADMIN_PASSWORD="$(openssl rand -base64 36)"
    umask 077
    printf '%s\n' "$CIRCLEJERK_ADMIN_PASSWORD" >"$admin_password_file"
  fi
fi

DOCTL=(doctl --access-token "$DIGITAL_OCEAN_API_KEY" --http-retry-max 2)
SSH=(ssh -i "$SSH_PRIVATE_KEY_PATH" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15)

fingerprint="$(ssh-keygen -E md5 -lf "$SSH_PUBLIC_KEY_PATH" | awk '{print $2}' | sed 's/^MD5://')"
ssh_key_id="$("${DOCTL[@]}" compute ssh-key list --format ID,FingerPrint --no-header | awk -v fp="$fingerprint" '$2 == fp {print $1; exit}')"
if [ -z "$ssh_key_id" ]; then
  ssh_key_id="$("${DOCTL[@]}" compute ssh-key import "circlejerks-$(hostname -s)" --public-key-file "$SSH_PUBLIC_KEY_PATH" --format ID --no-header)"
fi

droplet_id="$("${DOCTL[@]}" compute droplet list --format ID,Name --no-header | awk -v name="$NAME" '$2 == name {print $1; exit}')"
if [ -z "$droplet_id" ]; then
  droplet_id="$("${DOCTL[@]}" compute droplet create "$NAME" \
    --region "$REGION" \
    --size "$SIZE" \
    --image "$IMAGE" \
    --ssh-keys "$ssh_key_id" \
    --tag-names "$TAG" \
    --enable-monitoring \
    --enable-private-networking \
    --enable-backups \
    --user-data-file deploy/cloud-init.yaml \
    --wait \
    --format ID \
    --no-header)"
fi

reserved_ip="$("${DOCTL[@]}" compute reserved-ip list --format IP,DropletID --no-header | awk -v id="$droplet_id" '$2 == id {print $1; exit}')"
if [ -z "$reserved_ip" ]; then
  reserved_ip="$("${DOCTL[@]}" compute reserved-ip create --droplet-id "$droplet_id" --format IP --no-header)"
fi

firewall_id="$("${DOCTL[@]}" compute firewall list --format ID,Name --no-header | awk -v name="$NAME-fw" '$2 == name {print $1; exit}')"
if [ -z "$firewall_id" ]; then
  "${DOCTL[@]}" compute firewall create \
    --name "$NAME-fw" \
    --tag-names "$TAG" \
    --inbound-rules "protocol:tcp,ports:22,address:$SSH_ALLOWED_CIDR protocol:tcp,ports:80,address:0.0.0.0/0 protocol:tcp,ports:443,address:0.0.0.0/0" \
    --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0 protocol:udp,ports:all,address:0.0.0.0/0 protocol:icmp,address:0.0.0.0/0" \
    --format ID \
    --no-header >/dev/null
fi

domain="${CADDY_DOMAIN:-circlejerks.live}"
app_secret="${APP_SECRET:-$(openssl rand -hex 32)}"
cloudflare_token="${CLOUDFLARE_API_TOKEN:-${CLOUDFLARE_API_KEY:-}}"

if [ -n "$cloudflare_token" ]; then
  CLOUDFLARE_API_TOKEN="$cloudflare_token" \
    python3 scripts/upsert_cloudflare_dns.py --domain "$domain" --origin-ip "$reserved_ip"
else
  echo "CLOUDFLARE_API_TOKEN is not set; skipping automatic DNS upsert for $domain" >&2
fi

echo "Waiting for SSH on $reserved_ip ..."
for _ in $(seq 1 60); do
  if "${SSH[@]}" -o ConnectTimeout=5 root@"$reserved_ip" "true" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

"${SSH[@]}" root@"$reserved_ip" "mkdir -p $ROOT/app"
rsync -az --delete \
  -e "ssh -i $SSH_PRIVATE_KEY_PATH -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude '.circlejerk-admin-password' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/dist' \
  --exclude 'data/*.sqlite3*' \
  ./ root@"$reserved_ip":"$ROOT/app/"

tmp_env="$(mktemp)"
chmod 600 "$tmp_env"
cat >"$tmp_env" <<ENV
PUBLIC_BASE_URL=$(printf '%s' "https://$domain" | env_quote)
APP_SECRET=$(printf '%s' "$app_secret" | env_quote)
CADDY_DOMAIN=$(printf '%s' "$domain" | env_quote)
CADDY_EMAIL=$(printf '%s' "${CADDY_EMAIL:-}" | env_quote)
GROQ_API_KEY=$(printf '%s' "${GROQ_API_KEY:-}" | env_quote)
OPENSKY=$(printf '%s' "${OPENSKY:-}" | env_quote)
OPENSKY_CLIENT_ID=$(printf '%s' "${OPENSKY_CLIENT_ID:-}" | env_quote)
OPENSKY_CLIENT_SECRET=$(printf '%s' "${OPENSKY_CLIENT_SECRET:-}" | env_quote)
CIRCLEJERK_ENVIRONMENT=production
CIRCLEJERK_TIMEZONE=$(printf '%s' "${CIRCLEJERK_TIMEZONE:-America/Denver}" | env_quote)
CIRCLEJERK_DEFAULT_AIRPORT_ICAO=$(printf '%s' "${CIRCLEJERK_DEFAULT_AIRPORT_ICAO:-KBJC}" | env_quote)
CIRCLEJERK_ADMIN_USERNAME=$(printf '%s' "${CIRCLEJERK_ADMIN_USERNAME:-admin}" | env_quote)
CIRCLEJERK_ADMIN_PASSWORD=$(printf '%s' "${CIRCLEJERK_ADMIN_PASSWORD:-}" | env_quote)
CIRCLEJERK_ADMIN_PASSWORD_HASH=$(printf '%s' "${CIRCLEJERK_ADMIN_PASSWORD_HASH:-}" | env_quote)
CIRCLEJERK_BUY_ME_COFFEE_URL=$(printf '%s' "${CIRCLEJERK_BUY_ME_COFFEE_URL:-https://buymeacoffee.com/djodom}" | env_quote)
CIRCLEJERK_LIVE_SOURCE_PRIORITY=$(printf '%s' "${CIRCLEJERK_LIVE_SOURCE_PRIORITY:-adsb_lol,opensky,airplanes_live}" | env_quote)
CIRCLEJERK_LIVE_POLL_INTERVAL_SECONDS=$(printf '%s' "${CIRCLEJERK_LIVE_POLL_INTERVAL_SECONDS:-30}" | env_quote)
CIRCLEJERK_BBOX_MERGE_DISTANCE_NM=$(printf '%s' "${CIRCLEJERK_BBOX_MERGE_DISTANCE_NM:-5}" | env_quote)
CIRCLEJERK_OPENSKY_HISTORICAL_ENABLED=$(printf '%s' "${CIRCLEJERK_OPENSKY_HISTORICAL_ENABLED:-false}" | env_quote)
ENV

scp -i "$SSH_PRIVATE_KEY_PATH" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$tmp_env" root@"$reserved_ip":"$ROOT/app/.env" >/dev/null
rm -f "$tmp_env"

"${SSH[@]}" root@"$reserved_ip" "cd $ROOT/app && ./scripts/deploy_droplet.sh"

./scripts/check_production_health.sh "$domain"
./scripts/provision_uptime_checks.sh "$domain"

echo "reserved_ip=$reserved_ip"
echo "url=https://$domain"
echo "admin_url=https://$domain/admin"
if [ -f "$admin_password_file" ]; then
  echo "admin_password_file=$admin_password_file"
fi
echo "droplet_id=$droplet_id"
