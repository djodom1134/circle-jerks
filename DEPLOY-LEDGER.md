# Deploying The Lost Landing ledger sidecar (ledger.circlejerks.live)

This is a companion to `DEPLOY.md` — read that first for the droplet basics
(SSH key, reserved IP, box specs, why builds must be paced). This doc covers
**only** the two new services this branch adds: `ledger-api` and
`web-ledger`. It assumes `docker-compose.prod.yml` and `Caddyfile` on the
droplet already have the changes from this branch (i.e. you've rsynced).

## Scope — what this deploy does NOT touch

`api`, `worker`, and `web` are **unchanged** by this branch
(`git diff codex/production-observability -- backend/ frontend/` is empty)
and **keep running on their existing images** the whole time. This runbook
never rebuilds them, never recreates them, never restarts them. The only
containers that get built and (re)created are `ledger-api` and `web-ledger`.
The only existing container that gets touched at all is `caddy`, and only via
`validate` + `reload` (see below) — never rebuilt, never fully restarted.

## Target

Same droplet as `DEPLOY.md`:

- **Droplet**: `circlejerks-prod`, reserved IP `137.184.244.248`
- **SSH**: `-i ~/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes`
- **New subdomain**: `ledger.circlejerks.live` → same droplet, same Caddy
  container, a new site block (see `Caddyfile`)

```bash
SSH_OPTS="-i $HOME/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes -o BatchMode=yes"
DROPLET=137.184.244.248
```

## 0. DNS — point ledger.circlejerks.live at the box

A `CLOUDFLARE_API_KEY` already exists in `.env` (the DNS upsert script itself
reads `CLOUDFLARE_API_TOKEN`; `scripts/provision_digitalocean.sh` already
handles this name mismatch with a fallback, reuse the same pattern rather
than renaming anything):

```bash
set -a; source .env; set +a
cloudflare_token="${CLOUDFLARE_API_TOKEN:-${CLOUDFLARE_API_KEY:-}}"
CLOUDFLARE_API_TOKEN="$cloudflare_token" python3 scripts/upsert_cloudflare_dns.py \
  --domain ledger.circlejerks.live --origin-ip 137.184.244.248
```

This is idempotent (creates or updates in place) and proxied through
Cloudflare by default, same as the apex domain. DNS propagation + Cloudflare
proxy mean Caddy's automatic HTTPS for the new domain will only succeed once
this record is live — if you bring the new Caddy site block up before DNS
propagates, Caddy logs ACME failures for that one domain and retries on its
own; it does **not** affect `circlejerks.live`'s existing certificate or
serving (verified locally: a Caddy instance with one resolvable domain and
one bogus domain kept serving both HTTP config blocks fine while only the
bogus one's cert issuance failed and retried).

## 1. rsync the working tree

Same command as `DEPLOY.md`, with a couple more excludes for the two new
services' local dev artifacts:

```bash
rsync -az --delete \
  -e "ssh $SSH_OPTS" \
  --exclude '.git' \
  --exclude '.venv' --exclude 'backend/.venv' --exclude 'ledger-api/.venv' \
  --exclude '.env' --exclude '.circlejerk-admin-password' \
  --exclude 'frontend/node_modules' --exclude 'frontend/dist' --exclude 'frontend/.vite' \
  --exclude 'lostlanding/node_modules' --exclude 'lostlanding/dist' --exclude 'lostlanding/.vite' \
  --exclude '**/node_modules' --exclude '**/__pycache__' --exclude '*.pyc' \
  --exclude '.pytest_cache' --exclude '.benchmarks' \
  --exclude 'data/*.sqlite3*' --exclude 'ledger-api/data/*.sqlite3*' \
  --exclude '.DS_Store' --exclude '.gstack' --exclude '*.log' \
  --exclude '.claude' --exclude '.vscode' \
  ./ root@$DROPLET:/srv/circlejerk/app/
```

`--dry-run` first, same as always.

## 2. One-time: create the ledger sidecar's data directory

`ledger-api`'s own database (`daily_operation_rollup`, `aircraft_home_base`)
lives in a brand-new volume, separate from `/srv/circlejerk/data` (the
production DB). Create it once, before first `up`:

```bash
ssh $SSH_OPTS root@$DROPLET 'mkdir -p /srv/circlejerk/ledger-data && chmod 700 /srv/circlejerk/ledger-data'
```

`/srv/circlejerk/data` (the real production DB directory) does not need any
new permissions — `ledger-api` only needs read access, which the `:ro` bind
mount in `docker-compose.prod.yml` already enforces regardless of what the
container tries to do.

## 3. Build and start ONLY the two new services — one at a time

> Same 2 vCPU / 2 GB box, same warning as `DEPLOY.md`: never `docker compose
> build` (all services) and never rebuild `api`/`worker`/`web` here — they
> aren't part of this change. Build the two new images **one at a time**,
> detached, under `nice`/`ionice`, so a slow `npm ci && vite build` on
> `lostlanding` can't starve the box while `api`/`worker` keep serving live
> traffic.

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  nohup bash -c "
    nice -n 15 ionice -c3 docker compose -f docker-compose.prod.yml build ledger-api \
    && docker compose -f docker-compose.prod.yml up -d --no-deps ledger-api \
    && nice -n 15 ionice -c3 docker compose -f docker-compose.prod.yml build web-ledger \
    && docker compose -f docker-compose.prod.yml up -d --no-deps web-ledger \
    && echo DONE_ALL
  " > /tmp/ledger-deploy.log 2>&1 < /dev/null &
  disown'
```

Tail it:

```bash
ssh $SSH_OPTS root@$DROPLET 'tail -f /tmp/ledger-deploy.log'   # wait for DONE_ALL
```

`--no-deps` matters here for the same reason it does in `DEPLOY.md`: without
it, compose would also try to (re)create anything `ledger-api`/`web-ledger`
depend on. They don't declare a `depends_on` on `api`/`worker`/`web`, but
`--no-deps` is cheap insurance against ever touching them.

Verify both came up healthy before moving on:

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml ps ledger-api web-ledger'
```

## 4. Caddy — validate, then RELOAD (never restart)

`Caddyfile` is bind-mounted read-only into the running `caddy` container, so
rsyncing step 1 already updated its on-disk copy. Two things need to happen
for the new `ledger.circlejerks.live` site block to actually take effect:

1. **The `caddy` container needs `CADDY_LEDGER_DOMAIN` in its environment.**
   This is a brand-new env var this branch adds to `docker-compose.prod.yml`
   — the *first* time you deploy this, the running `caddy` container was
   created without it, and Docker only injects environment variables at
   container creation, never into an already-running container. This one
   time, `caddy` must be **recreated** (not the whole stack, just this one
   container, via `--no-deps`) to pick it up:

   ```bash
   ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
     docker compose -f docker-compose.prod.yml up -d --no-deps caddy'
   ```

   This is the one unavoidable exception to "never restart caddy" — it's a
   single, fast container recreate (same image, already pulled), not a
   rebuild, and not touching `api`/`worker`/`web`. After this one-time step,
   `CADDY_LEDGER_DOMAIN` is baked into that container's environment for as
   long as it keeps running, and every *future* Caddyfile change (this one
   included) only ever needs a **reload**, never another recreate.

2. **Validate the config the container will load, then reload it — never
   restart:**

   ```bash
   ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
     docker compose -f docker-compose.prod.yml exec -T caddy \
       caddy validate --config /etc/caddy/Caddyfile'
   # Only proceed if that prints "Valid configuration".
   ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
     docker compose -f docker-compose.prod.yml exec -T caddy \
       caddy reload --config /etc/caddy/Caddyfile'
   ```

   `caddy reload` re-adapts the Caddyfile and swaps the running config with
   zero listener downtime — it never unbinds port 80/443, so
   `circlejerks.live` never drops. Verified locally: reloading a Caddy
   instance serving two site blocks (one resolvable, one deliberately fake)
   picks up Caddyfile edits and returns exit 0 with no interruption to the
   other block. This is the only method used for the ledger block, and for
   any future Caddyfile edit to the main block, from now on.

   A syntax error in `Caddyfile` would be caught by `validate` (step above)
   before ever reaching `reload` — see the repo-root validation this branch
   already ran (paste in `.superpowers/sdd/deploy-wiring-report.md`).

## 5. Backfill — the ledger will show ZEROES until you do this

`ledger-api`'s own database starts empty. The nightly recompute path
(`app.worker`) is **not** wired up as a service on this branch (only
`ledger-api` and `web-ledger` were added — see the report for why). Until
someone runs the recompute, `GET /airports/{icao}/ledger` returns real 200s
with `runway_uses: 0` everywhere, because the derived rollup table is empty,
not because anything is broken. Populate it once, right after first deploy:

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml exec -T ledger-api \
    python -m scripts.backfill_ledger'
# Or for just one airport:
#   python -m scripts.backfill_ledger KLMO
```

This reads all of production `operations` history (read-only) and writes
`daily_operation_rollup` + `aircraft_home_base` into the sidecar's own
database — safe to re-run any time, fully idempotent. Until a nightly job is
added (out of scope for this branch), rerun this manually after any
maintenance that changes `operations` history, or on a cron if the zeros
start feeling stale.

## 6. Verify

```bash
# Containers healthy
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml ps'

# Public site responding through the new subdomain
curl -sS https://ledger.circlejerks.live/api/airports/KLMO/ledger?days=30
curl -sS -o /dev/null -w '%{http_code}\n' https://ledger.circlejerks.live/

# circlejerks.live unaffected
curl -sS https://circlejerks.live/healthz
```

## Rollback

`ledger-api` / `web-ledger` misbehaving never needs to touch
`circlejerks.live`'s containers at all:

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml stop ledger-api web-ledger'
```

Then revert the new site block from `Caddyfile` (or just revert
`CADDY_LEDGER_DOMAIN` to empty in `.env` and reload — Caddy will simply have
no domain to bind that block to) and reload:

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml exec -T caddy \
    caddy validate --config /etc/caddy/Caddyfile && \
  docker compose -f docker-compose.prod.yml exec -T caddy \
    caddy reload --config /etc/caddy/Caddyfile'
```

`api`, `worker`, `web` were never stopped, rebuilt, or recreated at any point
in this deploy or this rollback — `circlejerks.live` stays up regardless of
how the ledger sidecar deploy goes.

## Common gotchas (ledger-specific, in addition to `DEPLOY.md`'s list)

- **Zeros on the ledger page right after deploy are expected** — see step 5.
  Don't debug this as a bug before checking whether the backfill has run.
- **`CADDY_LEDGER_DOMAIN` needs a one-time `caddy` recreate** — see step 4.
  A plain `reload` before that recreate will validate fine (Caddy just
  treats the unset placeholder as an empty string) but silently binds no
  host to the ledger block, so `ledger.circlejerks.live` would 404 at the
  edge (wrong-host / no matching site) rather than reaching `web-ledger`.
- **`CLOUDFLARE_API_TOKEN` vs `CLOUDFLARE_API_KEY`** — `.env` has the key
  under `CLOUDFLARE_API_KEY`; `scripts/upsert_cloudflare_dns.py` reads
  `CLOUDFLARE_API_TOKEN`. `scripts/provision_digitalocean.sh` already has a
  fallback for this; step 0 above reuses the same pattern rather than
  renaming anything.
- **Don't add a `ledger-worker` service without also deciding backfill
  cadence** — this branch intentionally ships `ledger-api` (serving) without
  a running `app.worker` loop (nightly recompute). If a future change adds
  that service, it's a third new container, not part of the "two services"
  this doc covers, and the manual-backfill step above becomes redundant
  (but still safe to run).

---

## Gotcha: the Caddyfile is a single-FILE bind mount

`docker-compose.prod.yml` mounts `./Caddyfile:/etc/caddy/Caddyfile:ro`. A single-file
bind mount binds the **inode**, not the path.

`rsync` writes a temp file and renames it into place, which creates a **new inode**. The
running caddy container keeps holding the OLD one. So after rsyncing a changed Caddyfile:

- the host file is new,
- the container still sees the old file,
- and `caddy reload` "succeeds" — it reloads the *stale* file and reports success.

This is silent. It cost a debugging cycle on the live-map deploy: `/live/*` 200'd with
`text/html` (the SPA catch-all) because caddy had never seen the route.

**Either** rsync the Caddyfile with `--inplace` (preserves the inode, so `caddy reload`
works and there is zero downtime):

```bash
rsync -az --inplace -e "ssh $SSH_OPTS" ./Caddyfile root@$DROPLET:/srv/circlejerk/app/Caddyfile
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml exec -T caddy caddy reload --config /etc/caddy/Caddyfile'
```

**or** recreate the container (re-binds the new inode, ~1s blip on circlejerks.live):

```bash
ssh $SSH_OPTS root@$DROPLET 'cd /srv/circlejerk/app && \
  docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate caddy'
```

Prefer `--inplace`. Verify either way — do not trust the reload's exit code:

```bash
ssh $SSH_OPTS root@$DROPLET 'docker exec app-caddy-1 grep -c "handle_path /live" /etc/caddy/Caddyfile'
# 0 = the container is still on the old file.
```
