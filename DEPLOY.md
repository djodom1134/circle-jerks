# Deploying to Production (circlejerks.live)

Production runs on a DigitalOcean droplet behind Cloudflare. Deployments are
rsync + remote rebuild — no CI/CD, no git checkout on the box. Run from this
working tree.

## Target

- **Domain**: https://circlejerks.live (Cloudflare → Caddy → web/api)
- **Droplet name**: `circlejerks-prod`
- **Reserved IP**: `137.184.244.248` (this is the SSH target — do NOT use the
  ephemeral droplet IP)
- **DO account**: the API token in `./.env` as `DIGITAL_OCEAN_API_KEY` (not in
  any local doctl context — query the REST API directly)
- **SSH key**: `~/.ssh/id_ed25519_dodom` (fingerprint
  `1c:b9:9c:74:15:f8:1e:5d:1e:73:04:0d:eb:47:78:24`). The default `id_ed25519`
  is **not** authorized — pass `-i ~/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes`
  or you'll get "Too many authentication failures".

### Re-discovering the droplet IP from scratch

If the reserved IP changes or you don't trust this doc:

```bash
DOTOKEN=$(grep "^DIGITAL_OCEAN_API_KEY=" .env | cut -d= -f2)
curl -sS -H "Authorization: Bearer $DOTOKEN" \
  "https://api.digitalocean.com/v2/reserved_ips?per_page=50" \
  | python3 -c "import json,sys; [print(r['ip'], r['droplet']['name']) for r in json.load(sys.stdin)['reserved_ips']]"
```

## Deploy steps

The droplet already has `/srv/circlejerk/app` with a populated `.env` (different
from local). **Never rsync the local `.env`** — it would overwrite prod
credentials. The excludes in step 1 cover this.

### 1. rsync the working tree

```bash
SSH_OPTS="-i $HOME/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes -o BatchMode=yes"
DROPLET=137.184.244.248

rsync -az --delete \
  -e "ssh $SSH_OPTS" \
  --exclude '.git' \
  --exclude '.venv' --exclude 'backend/.venv' \
  --exclude '.env' --exclude '.circlejerk-admin-password' \
  --exclude 'frontend/node_modules' --exclude 'frontend/dist' --exclude 'frontend/.vite' \
  --exclude '**/node_modules' --exclude '**/__pycache__' --exclude '*.pyc' \
  --exclude '.pytest_cache' --exclude '.benchmarks' \
  --exclude 'data/*.sqlite3*' \
  --exclude '.DS_Store' --exclude '.gstack' --exclude '*.log' \
  --exclude '.claude' --exclude '.vscode' \
  ./ root@$DROPLET:/srv/circlejerk/app/
```

You can `--dry-run` first; expect only your changed files + maybe a few
never-deployed metadata files (README, scripts/, prd/).

### 2. Rebuild and recreate containers — **detached, one service at a time**

> ⚠️ **DO NOT** run `docker compose build` (all services) or `up -d --build` on
> the droplet. The box is 2 vCPU / 2GB. Building all images at once — especially
> the frontend `npm ci && vite build` — spikes memory + I/O hard enough to wedge
> the kernel: SSH and ICMP stop responding while Caddy keeps serving a cached
> healthz, so it *looks* up but isn't. The only recovery is a DO power-cycle.
> This wedged prod multiple times. Always build **one service at a time** with
> low CPU/I-O priority.

Two safeguards are now in place:
1. **2GB swapfile** (`/swapfile`, in `/etc/fstab`, `vm.swappiness=10`) absorbs
   the memory spike instead of OOM-wedging.
2. Build one service at a time under `nice`/`ionice` so the API keeps serving.

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  nohup bash -c "
    nice -n 15 ionice -c3 docker compose -f docker-compose.prod.yml build api \
    && docker compose -f docker-compose.prod.yml up -d --no-deps api \
    && nice -n 15 ionice -c3 docker compose -f docker-compose.prod.yml build worker \
    && docker compose -f docker-compose.prod.yml up -d --no-deps worker \
    && nice -n 15 ionice -c3 docker compose -f docker-compose.prod.yml build web \
    && docker compose -f docker-compose.prod.yml up -d --no-deps web \
    && echo DONE_ALL
  " > /tmp/circlejerk-deploy.log 2>&1 < /dev/null &
  disown'
```

Only build the services you actually changed (api/worker share an image build
context; web is the frontend). Tail the log:

```bash
ssh $SSH_OPTS root@$DROPLET 'tail -f /tmp/circlejerk-deploy.log'   # wait for DONE_ALL
```

**If the box wedges anyway** (SSH/ping dead, healthz still 200 via Cloudflare),
power-cycle via the DO API and let `restart: unless-stopped` bring the
containers back on the last-built images:

```bash
DOTOKEN=$(grep '^DIGITAL_OCEAN_API_KEY=' .env | cut -d= -f2)
curl -sS -X POST -H "Authorization: Bearer $DOTOKEN" -H 'Content-Type: application/json' \
  -d '{"type":"power_cycle"}' \
  "https://api.digitalocean.com/v2/droplets/567016097/actions"
```

### Long-running data jobs (registry / airports import) — same rules

Bulk SQLite writes (FAA registry ~312k rows, OurAirports ~16k rows) also wedge
the box if they hold the write lock too long. Both importers now **commit in
500-row chunks with a 0.1s sleep between** so the API can interleave. Run them
detached, never inline-blocking. Examples:

```bash
# Aircraft registry (N-number → owner / make / model / ICAO-hex)
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && nohup bash -c "
  docker compose -f docker-compose.prod.yml exec -T api python -c \
    \"import asyncio; from app.registry import importer; from app.settings import get_settings; \
      print(asyncio.run(importer.import_faa_registry(get_settings().database_path)))\"
" > /tmp/registry-import.log 2>&1 < /dev/null & disown'

# US airports (fixes nearest-airport outside Colorado)
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && nohup bash -c "
  docker compose -f docker-compose.prod.yml exec -T api python -c \
    \"import asyncio; from app.airports_import import import_us_airports; from app.settings import get_settings; \
      print(asyncio.run(import_us_airports(get_settings().database_path)))\"
" > /tmp/airports-import.log 2>&1 < /dev/null & disown'
```

### 3. Verify

```bash
# Containers up + healthy
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml ps'

# Public site responding
curl -sS https://circlejerks.live/healthz
# {"ok":true,"environment":"production","sqlite_seeded":true,"airports":10,...}

# Any newly added endpoint, e.g.:
curl -sS "https://circlejerks.live/api/reverse_geocode?lat=40.167&lon=-105.102"
```

## Routing notes (so you don't misroute new endpoints)

`Caddyfile` strips `/api/` before forwarding to the backend, and the frontend
client uses `API_BASE = "/api"` (see `frontend/src/lib/api.ts`). So a backend
route `@app.get("/foo")` is reached from the browser as `/api/foo` and from the
api container directly as `/foo`. `/healthz` is the one exception — Caddy
forwards it without the `/api` prefix.

## Deploy order constraints

Two ordering constraints apply to this app. They are independent but compose
— do both, in either order relative to each other, but each before the thing
it protects.

### 1. Caddy before the app (partner API keys)

The Caddyfile's `format filter` log blocks (`Caddyfile:18-24` and `:94-100`)
strip `Cookie`, `Authorization`, and `X-Api-Key` from the access log. Partners
using the documented `X-Api-Key` header write their live API key straight
into `/data/access.log`, retained 720h (`roll_keep_for 720h`). Deploy Caddy
**before** the app (or atomically), so that filter is already live before any
partner key can be issued. Rolling Caddy back to an older config after a key
is issued re-opens the leak — a Caddy-only deploy is always safe; an app-only
deploy that leaves an older Caddyfile in place is not.

### 2. Environment variables before the app (Google SSO)

Environment variables must land **before** the app restarts. If the app comes
up without `GOOGLE_OAUTH_CLIENT_ID`, `/admin/auth/methods` reports Google as
unavailable and the login screen renders with no Google button — a silent
failure that looks like a frontend bug, not a missing env var.

## Google SSO one-time setup

Done once in Google Cloud Console for the OAuth client, and once in this
app's environment:

1. Create an OAuth client, type **Web application**.
2. Authorized redirect URI: `https://circlejerks.live/api/admin/auth/google/callback`
   — note Caddy strips `/api` before the app sees it (`handle_path /api/*`),
   so the backend route itself is `/admin/auth/google/callback`.
3. Configure the consent screen and either publish it or add each intended
   user as a test user — an unpublished app only lets listed test users
   complete the OAuth flow.
4. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and
   `GOOGLE_OAUTH_REDIRECT_URI` (see `.env.example`) before the app starts.

The first sign-in for an address listed in `ADMIN_SUPERUSERS` is auto-approved
as `super_admin` — on **every** login, not just the first row insert, so a
UI misclick can never lock the operator out, and the pin survives a database
wipe. Everyone else lands in the pending queue at `/admin` → Access requests
until a super-admin approves them.

## Production `app_secret` guard

`CIRCLEJERK_APP_SECRET` signs the admin session cookie and the OAuth state
cookie. Since this feature added real user rows and roles behind that cookie,
an unconfigured value is a full session-forgery hole: anyone who reads this
open-source default can mint a valid session for any user id.

The app now **refuses to start** when `CIRCLEJERK_ENVIRONMENT=production` and
`CIRCLEJERK_APP_SECRET` is left at its development default (see
`Settings._require_app_secret_override_in_production` in
`backend/app/settings.py`). Generate a real value before the first production
deploy after this feature:

```bash
openssl rand -hex 32
```

`local` and `test` environments are unaffected — only a `production` deploy
with the default secret fails to start.

## When to use `scripts/deploy_droplet.sh` instead

The slim deploy above just rebuilds containers. Use the full script when you've
changed provisioning (ufw rules, cron jobs, apt deps, Caddy bootstrap) or when
the droplet hasn't been touched in a long time. It's idempotent but slower (~30s
extra for the apt/ufw bits).

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  nohup DEBIAN_FRONTEND=noninteractive ./scripts/deploy_droplet.sh \
    > /tmp/circlejerk-deploy.log 2>&1 < /dev/null & disown'
```

## Common gotchas

- **"Too many authentication failures"** — SSH is iterating through all your
  keys. Always pass `-i ~/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes`.
- **Box wedged: SSH + ping dead but healthz returns 200** — classic resource
  exhaustion from building all images at once or an unpaced bulk DB write. The
  200 is Cloudflare serving a still-warm origin; the host kernel is starved.
  Power-cycle via the DO API (see step 2). Prevention: 2GB swap + one-service
  builds + chunked importers — all now in place.
- **Foreground SSH dies mid-build** — the local SSH session can drop (laptop
  sleep, network blip) but the Docker daemon on the droplet keeps building
  in the background. Containers may still recreate successfully. Always
  `ps`/`tail` to confirm before re-running.
- **Don't rsync `.env`** — the droplet's `.env` holds `PUBLIC_BASE_URL`,
  `APP_SECRET`, `GROQ_API_KEY`, `CADDY_DOMAIN`, etc. Overwriting it with the
  local dev copy will break the site.
- **Database lives outside the app dir** — bind-mounted from
  `/srv/circlejerk/data/` into the api/worker containers. rsync's `--delete`
  is safe because `/srv/circlejerk/app/data/*.sqlite3*` is excluded and the
  real DB isn't in that path anyway.
