# Airfield Operations Dashboard

A multi-airport landing-operations dashboard. First host: `klmo.airfieldeconomics.org`.
Month view, one bar per day, filterable by operation type and locality, with a radial
hub of observed origins that cross-filters the page.

## Scope

**Frontend only.** The data comes from two `/v1` endpoints reached via a same-origin
`/api/*` proxy — `daily-operations` and `origins` (plus optional `worst-offenders`
and `hourly-profile`). Those endpoints and the Caddy proxy that injects the API key
are owned **outside this app**; see
`docs/superpowers/specs/2026-07-22-klmo-dashboard-design.md`. Until they exist, the
checked-in fixtures in `src/fixtures/` are the contract.

## Develop

```bash
cd dashboard
npm install
npm run dev     # http://localhost:5175 — proxies /api -> https://circlejerks.live/v1
npm test        # vitest
npm run build   # tsc + vite build -> dist/
```

The airport is resolved from the hostname (`klmo.*` -> `KLMO`); use `?airport=KXYZ`
to preview another.

## Deploy (owned separately)

Serve `dashboard/dist` and reverse-proxy `/api/*` -> the `/v1` API with the per-host
`Authorization: Bearer` key injected by Caddy. Not built by this repo's frontend code.
