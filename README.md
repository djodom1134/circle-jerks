# Circling Aircraft Noise Complaint Assistant

Greenfield MVP for the PRD in `prd/circlejerk.md`: React + OpenLayers frontend, FastAPI API, ADS-B polling worker, SQLite reference data, Redis-compatible rolling buffer, and Groq-backed complaint text generation with deterministic fallback.

## Local Development

```bash
cp .env.example .env
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements-dev.txt
uvicorn app.main:app --app-dir backend --reload
```

In another shell:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api/*` to FastAPI on port `8000`.

## Worker

The worker polls live ADS-B sources only after the web app registers an active monitor through `/scan`. By default it tries ADSB.lol first, then OpenSky, then Airplanes.live. OpenSky remains the enrichment source for origin lookups when credentials are configured.

```bash
source backend/.venv/bin/activate
python -m app.worker
```

Local development defaults to in-memory runtime storage. Production should use Valkey/Redis through `CIRCLEJERK_REDIS_URL`.

## Production Deployment

Target: one low-cost DigitalOcean Droplet running Docker Compose.

```bash
scp -r . root@YOUR_DROPLET:/srv/circlejerk/app
ssh root@YOUR_DROPLET
cd /srv/circlejerk/app
cp .env.example .env
vim .env
./scripts/deploy_droplet.sh
```

Required production values:

- `PUBLIC_BASE_URL`
- `APP_SECRET`
- `GROQ_API_KEY`
- Optional for enrichment and live fallback: `OPENSKY=client_id:client_secret` or `OPENSKY_CLIENT_ID` plus `OPENSKY_CLIENT_SECRET`
- `CADDY_DOMAIN`

Provider priority is controlled by `CIRCLEJERK_LIVE_SOURCE_PRIORITY`, defaulting to `adsb_lol,opensky,airplanes_live`. Review each upstream provider's terms before public launch.

## Verification

```bash
cd backend && pytest
cd ../frontend && npm run build
curl -f https://YOUR_DOMAIN/healthz
curl -f https://YOUR_DOMAIN/api/healthz
```
