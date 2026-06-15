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
