# Mirroring the ledger onto a per-airport hostname (AirfieldEconomics.org)

Companion to `DEPLOY.md` and `DEPLOY-LEDGER.md` — read both first. This doc
covers adding a **byte-identical mirror** of `ledger.circlejerks.live` under a
second hostname, starting with `lmco.airfieldeconomics.org` (the KLMO / Vance
Brand ledger). The eventual goal is a ledger page per airport on
`AirfieldEconomics.org`; this is the template for the first one.

## Why this is almost free

The `lostlanding` frontend (`web-ledger`) calls `/api` (the ledger sidecar) and
`/live` (the main API's positions) as **same-origin relative paths** — see
`lostlanding/src/lib/api.ts` and `lostlanding/.env.production`. So the *exact
same* `web-ledger` + `ledger-api` containers already render correctly under any
hostname Caddy answers for. A mirror needs **no** frontend rebuild, no new
container, and no app code change — only one DNS record and one more hostname on
the existing ledger Caddy block.

This first mirror is **literal**: `lmco.airfieldeconomics.org` shows the same
KLMO page that is live today, "The Lost Landing" branding and all. A future
multi-airport rebrand (airport-generic routing) is a separate change.

## What Phase A already put in the tree (this branch)

These are code changes only — inert until deployed, safe to ship before the
mirror DNS exists (the Caddy address list collapses to just the single ledger
domain when the new var is empty; validated both ways with `caddy validate`):

- `Caddyfile` — the ledger site block address is now
  `{$CADDY_LEDGER_DOMAIN} {$CADDY_LEDGER_MIRROR_DOMAIN}` (two hostnames, one
  block, identical handling).
- `docker-compose.prod.yml` — `caddy` service gains
  `CADDY_LEDGER_MIRROR_DOMAIN: ${CADDY_LEDGER_MIRROR_DOMAIN:-}`.
- `.env.example` — documents `CADDY_LEDGER_MIRROR_DOMAIN=` (empty default).
- `scripts/upsert_cloudflare_dns.py` — new `--no-www` flag to upsert a single
  subdomain `A` record without an apex-style `www` CNAME.

## Prerequisite (done)

`airfieldeconomics.org` is a Cloudflare zone on the same account as
`circlejerks.live` (registered via Cloudflare Registrar). The existing
`CLOUDFLARE_API_KEY` token is account-scoped and already sees it. The zone
starts with no DNS records.

## Phase B — deploy the mirror

```bash
SSH_OPTS="-i $HOME/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes -o BatchMode=yes"
DROPLET=137.184.244.248
```

### 1. DNS — point the mirror host at the box (proxied)

```bash
set -a; source .env; set +a
cloudflare_token="${CLOUDFLARE_API_TOKEN:-${CLOUDFLARE_API_KEY:-}}"
CLOUDFLARE_API_TOKEN="$cloudflare_token" python3 scripts/upsert_cloudflare_dns.py \
  --domain lmco.airfieldeconomics.org --origin-ip 137.184.244.248 --no-www
# Expect: A lmco.airfieldeconomics.org -> 137.184.244.248 created
```

Idempotent, proxied (orange cloud) by default — same as the apex and the
existing ledger record. Do this **before** the Caddy recreate below so Caddy's
ACME HTTP-01 for the new host succeeds on the first try instead of failing and
retrying.

> macOS gotcha: the script uses `urllib`, which on the python.org
> `Python.framework` build can't find the system root CAs and dies with
> `CERTIFICATE_VERIFY_FAILED`. Point it at certifi's bundle:
> `SSL_CERT_FILE="$(python3 -c 'import certifi;print(certifi.where())')" python3 scripts/upsert_cloudflare_dns.py …`

### 2. Cloudflare SSL/TLS mode — MUST be Full or Full (strict), never Flexible

The deploy token can create DNS records but **cannot** read or set zone SSL
settings (`code 9109 Unauthorized`), so verify this once in the dashboard:

> `airfieldeconomics.org` → SSL/TLS → Overview → encryption mode = **Full
> (strict)** (or Automatic, or Full). It must **not** be **Flexible**.

Under Flexible, Cloudflare speaks HTTP to the origin, which hits Caddy's
`http:// { redir https://{$CADDY_DOMAIN} }` block and 301s the mirror host to
`https://circlejerks.live/...` — a wrong-host redirect, not the ledger. Full /
Full (strict) makes Cloudflare talk HTTPS to origin on :443, which is what the
ledger block serves. `circlejerks.live`'s own zone is already Full (strict);
match it.

### 3. Set the env var in the droplet's `.env`

The box's `/srv/circlejerk/app/.env` is **never** rsynced (it holds prod
secrets). Add the line by hand:

```bash
ssh $SSH_OPTS root@$DROPLET \
  "grep -q '^CADDY_LEDGER_MIRROR_DOMAIN=' /srv/circlejerk/app/.env \
     && sed -i 's/^CADDY_LEDGER_MIRROR_DOMAIN=.*/CADDY_LEDGER_MIRROR_DOMAIN=lmco.airfieldeconomics.org/' /srv/circlejerk/app/.env \
     || echo 'CADDY_LEDGER_MIRROR_DOMAIN=lmco.airfieldeconomics.org' >> /srv/circlejerk/app/.env"
ssh $SSH_OPTS root@$DROPLET "grep CADDY_LEDGER_MIRROR_DOMAIN /srv/circlejerk/app/.env"
```

### 4. rsync the two changed files

Only `Caddyfile` and `docker-compose.prod.yml` changed for this deploy (the
`.env` was edited in place above; the app images are untouched). Use the full
`DEPLOY-LEDGER.md` rsync, or just the two files:

```bash
rsync -az --inplace -e "ssh $SSH_OPTS" \
  ./Caddyfile ./docker-compose.prod.yml root@$DROPLET:/srv/circlejerk/app/
```

`--inplace` on the Caddyfile preserves its inode — but see step 5: this deploy
**recreates** `caddy` anyway, which re-binds the fresh file regardless, so the
inode gotcha from `DEPLOY-LEDGER.md` does not bite here.

### 5. One-time `caddy` recreate (picks up the new env var + fresh Caddyfile)

`CADDY_LEDGER_MIRROR_DOMAIN` is a brand-new env var; Docker only injects env at
container **creation**, so `caddy` must be recreated once (not the whole
stack). This single recreate also fresh-mounts the updated Caddyfile, so it
covers both concerns at once. ~1s edge blip on `circlejerks.live` — the same
documented exception as when `CADDY_LEDGER_DOMAIN` was first added
(`DEPLOY-LEDGER.md` §4).

```bash
# Validate the exact Caddyfile the new container will load (throwaway run;
# the caddy image entrypoint is `caddy`, so `validate ...` is appended to it).
# The compose env_file must resolve CADDY_LEDGER_MIRROR_DOMAIN — confirm step 3
# ran first, or validate passes with an empty second host and the recreate
# would then bind only the ledger domain.
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml run --rm --no-deps caddy \
    validate --config /etc/caddy/Caddyfile'
# Only proceed if that prints "Valid configuration".

# Recreate just caddy (new env var + fresh Caddyfile inode), nothing else:
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml up -d --no-deps caddy'
```

`api`, `worker`, `web`, `ledger-api`, `web-ledger` are **not** touched.

### 6. Verify

Cert issuance through Cloudflare can take a few tens of seconds on first hit.

```bash
# Mirror serves the ledger app (200) and the ledger API (JSON):
curl -sS -o /dev/null -w '%{http_code}\n' https://lmco.airfieldeconomics.org/
curl -sS https://lmco.airfieldeconomics.org/api/airports/KLMO/ledger?days=30 | head -c 300; echo

# It is the SAME page as the canonical ledger (compare bodies):
diff <(curl -sS https://lmco.airfieldeconomics.org/) \
     <(curl -sS https://ledger.circlejerks.live/) && echo "IDENTICAL HTML"

# The existing sites are unaffected:
curl -sS -o /dev/null -w 'apex %{http_code}\n'   https://circlejerks.live/healthz
curl -sS -o /dev/null -w 'ledger %{http_code}\n' https://ledger.circlejerks.live/

# Origin cert actually issued for the new host (on the box):
ssh $SSH_OPTS root@$DROPLET \
  'docker exec app-caddy-1 ls /data/caddy/certificates/*/lmco.airfieldeconomics.org 2>/dev/null \
   && echo "cert present" || echo "cert not issued yet — recheck in ~30s"'
```

## Rollback

The mirror never shares containers with `circlejerks.live`, so backing it out
cannot affect the main site:

```bash
# Blank the var on the box and recreate caddy (drops the second hostname):
ssh $SSH_OPTS root@$DROPLET \
  "sed -i 's/^CADDY_LEDGER_MIRROR_DOMAIN=.*/CADDY_LEDGER_MIRROR_DOMAIN=/' /srv/circlejerk/app/.env"
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml up -d --no-deps caddy'

# Optionally delete the DNS record in the Cloudflare dashboard (or leave it —
# with the Caddy hostname gone it just 5xxs at the edge, harmless).
```

## Adding the next airport later

Repeat with a different subdomain, but note the current app is **hardcoded to
KLMO** (`lostlanding/src/App.tsx`: `const AIRPORT_ICAO = "KLMO"`). A second
airport that shows *its own* data needs the frontend to become airport-aware
(route/host → ICAO) — that's a real feature, not another mirror. Until then,
every extra hostname on this block shows the KLMO ledger.
