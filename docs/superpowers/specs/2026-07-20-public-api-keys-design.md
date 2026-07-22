# Public API with Scoped API Keys

**Date:** 2026-07-20
**Status:** Approved design

## Problem

Circlejerks holds four families of data with no supported way to read them from
outside the app: distilled operations, raw ADS-B tracks, precomputed aggregates,
and the KLMO fees ledger. Today the only consumers are the frontend and ad-hoc
scripts reaching into SQLite directly.

We want a read-only HTTP API covering all four families, authenticated by keys
that the admin issues from the existing dashboard. Each key carries an
adjustable set of scopes and an optional airport restriction.

## Audience and Scope

Primary consumers are our own tooling (frontend, mirror sites, scripts) plus a
small number of trusted partners. This is not a public self-serve product, and
there is no signup flow, billing, or usage metering. Keys are minted by hand in
the admin dashboard.

The API is read-only. No endpoint mutates state.

## Architecture

A versioned `/v1` router lives inside the existing backend FastAPI app. It gets
its own response contracts, deliberately decoupled from the ~50 internal routes
the frontend uses, so frontend refactors never break a partner.

Caddy routes `handle_path /api/*` to `api:8000` and **strips** the `/api` prefix
(`Caddyfile:39`). Routes are therefore declared as `/v1/...` in FastAPI and are
reachable in production at `https://circlejerks.live/api/v1/...`.

Ledger data lives in a separate service. `ledger-api` opens the main
`circlejerk.sqlite3` read-only and writes its derived tables to its own
`ledger.sqlite3`, a deliberate split that keeps two writers off one SQLite write
lock (see `ledger-api/app/db.py`'s module docstring). The public API preserves
that boundary by proxying ledger requests over HTTP to `ledger-api:8100` rather
than opening the second database.

Rejected alternatives:

- **Key-auth on existing routes.** Cheapest, but it freezes ~50 frontend-shaped
  endpoints as a public contract.
- **Standalone `public-api` container.** Best blast-radius isolation, but costs
  a new image, Caddy route, deploy step, and duplicated models — too heavy for
  this consumer set.

## Endpoint Surface

| Endpoint | Scope | Parameters |
|---|---|---|
| `GET /v1/meta` | any valid key | none |
| `GET /v1/operations` | `ops:read` | `airport` (required), `since`, `until`, `type`, `icao24`, `runway`, `cursor`, `limit` |
| `GET /v1/tracks` | `tracks:read` | `icao24` or `airport` (one required), `since` and `until` (both required), `cursor`, `limit` |
| `GET /v1/airports/{icao}/stats` | `aggregates:read` | per existing internal route |
| `GET /v1/airports/{icao}/worst-offenders` | `aggregates:read` | per existing internal route |
| `GET /v1/airports/{icao}/operations-trends` | `aggregates:read` | per existing internal route |
| `GET /v1/airports/{icao}/vnap-compliance` | `aggregates:read` | per existing internal route |
| `GET /v1/airports/{icao}/runways` | `aggregates:read` | per existing internal route |
| `GET /v1/airports/{icao}/patterns` | `aggregates:read` | per existing internal route |
| `GET /v1/airports/{icao}/flow` | `aggregates:read` | per existing internal route |
| `GET /v1/ledger/airports/{icao}/ledger` | `ledger:read` | proxied to `ledger-api` |
| `GET /v1/ledger/airports/{icao}/aircraft-fees` | `ledger:read` | proxied to `ledger-api` |

`since` and `until` accept a Unix timestamp or an ISO 8601 datetime, and are
returned as Unix timestamps to match the storage layer.

`GET /v1/meta` echoes the calling key's name, scopes, airport restriction, and
the API version, so a partner can diagnose a 403 without contacting us.

When `ledger-api` is unreachable — which is the normal case in local
development, where only the main backend runs — ledger endpoints return 503
with a message naming the unavailable upstream, not a generic proxy error.

FastAPI's generated documentation is exposed at `/v1/docs`, backed by a schema
built from the `/v1` router alone. Internal routes are not touched and do not
appear there.

## Authentication

### Key format

```
cj_live_<id16>_<secret43>
```

`id16` is 16 hex characters, the indexed database handle. `secret43` is 32
bytes of `secrets.token_bytes` in base64url, and is **never stored**. The `live`
segment reflects `settings.environment`, so a development key is visibly
distinct from a production one.

### Storage

A new `api_keys` table, appended to the `SCHEMA` string in `backend/app/db.py`
following the existing `CREATE TABLE IF NOT EXISTS` idiom:

```sql
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  secret_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  scopes TEXT NOT NULL,
  airports TEXT,
  created_at INTEGER NOT NULL,
  created_by TEXT,
  last_used_at INTEGER,
  revoked_at INTEGER
);
```

`scopes` is a comma-separated list. `airports` is a comma-separated list of
uppercase ICAO codes, or `NULL` meaning all airports. `created_by` records
`settings.admin_username` at creation time — a single value today, but it keeps
the column meaningful if admin accounts are ever added.

### Verification

Keys are presented as `Authorization: Bearer cj_live_...`, with `X-API-Key`
accepted as an alternative. Verification parses the `id16` segment, looks up the
row, and compares `sha256(secret)` against `secret_hash` using
`hmac.compare_digest`. A revoked key (non-null `revoked_at`) is rejected.

SHA-256 is the correct choice here rather than bcrypt or argon2: the secret is
256 bits of CSPRNG output, not a human-chosen password, so there is no
meaningful search space to slow down. A password-hardening KDF would only add
latency to every request on the hot path.

`last_used_at` is updated on successful verification. To keep a hot loop from
issuing a write per request, the update is throttled — it is written at most
once per 60 seconds per key.

### Authorization

A `require_scope("ops:read")` FastAPI dependency factory resolves the key and
yields an `ApiKeyContext` carrying the key's id, name, scopes, and airport
restriction. Handlers that take an airport call `ctx.assert_airport(icao)`,
which raises `forbidden_airport` when the key is restricted and the requested
ICAO is not in its list.

The four scopes are `ops:read`, `tracks:read`, `aggregates:read`, and
`ledger:read`. They are coarse by design: granular enough to withhold raw tracks
from a partner who should only see aggregates, coarse enough to stay
comprehensible as four checkboxes in the admin UI.

## Guardrails

Guardrails are fixed constants, identical for every key. They are not
per-key adjustable — the only adjustable dimensions are scopes and airports.

**Cursor pagination.** List endpoints return an opaque cursor encoding the last
row's `(timestamp, id)` tuple as base64. `limit` defaults to 500 and is capped
at 5000. Responses carry `{"data": [...], "next_cursor": "..." | null}`.

**Track span cap.** `/v1/tracks` rejects a requested range longer than 31 days
with `invalid_request`. A year of KLMO history is therefore twelve paged
requests rather than one query that stalls the single SQLite reader also serving
the live site.

**Rate limit.** 120 requests per minute per key, enforced with a fixed window in
the existing Valkey/Redis store. This requires a new `incr_counter(key, ttl)`
method on the `Store` ABC in `backend/app/store.py`, implemented as `INCR` plus
`EXPIRE` in `RedisStore` and as an equivalent counter in `MemoryStore`.
Exceeding the limit returns 429 with `Retry-After`. Every response carries
`X-RateLimit-Limit` and `X-RateLimit-Remaining`, errors included — the one
exception is a 401 raised before the key is resolved, which has no counter to
report and must not invent one.

## Admin Interface

### Backend routes

Three routes on the existing cookie-based `require_admin` dependency
(`backend/app/main.py:298`):

- `GET /admin/api-keys` — list all keys with metadata. Never returns secrets.
- `POST /admin/api-keys` — body `{name, scopes[], airports[] | null}`. Returns
  the full key string exactly once, in this response only.
- `POST /admin/api-keys/{id}/revoke` — sets `revoked_at`.

Keys are revoked, never deleted, so a key id appearing in logs stays
attributable.

### Frontend

A new `frontend/src/components/ApiKeysPanel.tsx`, rendered as a
`<section className="admin-panel">` inside `AdminDashboard.tsx`. The panel is
its own component because `AdminDashboard.tsx` is already 305 lines, and
`frontend/src/components/` is where the existing dashboard pieces live.

The panel uses the existing `admin-table` / `admin-row` markup and contains:

- A table of keys: name, truncated prefix (`cj_live_a3f9c2b1…`), scopes,
  airports (or "all"), created, last used, and a revoke button.
- A create form: name field, four scope checkboxes, and an airports input where
  blank means all airports.
- A one-time reveal. On successful creation the full key appears in a copy box
  with an explicit warning that it will not be shown again. It is held in
  component state only and is cleared on dismiss or reload.

## Error Contract

The `/v1` namespace uses a consistent envelope, distinct from FastAPI's default
`{"detail": ...}`:

```json
{"error": {"code": "forbidden_scope", "message": "key lacks scope tracks:read"}}
```

| Code | Status | Meaning |
|---|---|---|
| `unauthorized` | 401 | Missing, malformed, unknown, or revoked key |
| `forbidden_scope` | 403 | Valid key without the required scope |
| `forbidden_airport` | 403 | Valid key restricted away from the requested ICAO |
| `invalid_request` | 400 | Bad parameter, bad cursor, span over the cap, or a request `ledger-api` rejected |
| `not_found` | 404 | Unknown airport or aircraft |
| `rate_limited` | 429 | Over 120 requests/minute |
| `upstream_unavailable` | 503 | `ledger-api` unreachable or failing (5xx) |

`invalid_request` is 400 and only 400: FastAPI's own query/path validation
errors are re-enveloped at 400 rather than its default 422, so the code and the
status never disagree.

Existing internal routes keep FastAPI's `detail` shape. The envelope applies
only to the `/v1` namespace, via an exception handler scoped to that router.

Error responses never distinguish "no such key" from "wrong secret" — both are
`unauthorized` with the same message.

## Module Layout

| File | Responsibility |
|---|---|
| `backend/app/api_keys.py` (new) | Key generation, hashing, verification, the scope and airport model. Pure logic, imports no FastAPI, testable standalone. |
| `backend/app/public_api.py` (new) | The `/v1` `APIRouter`, its dependencies, pagination helpers, and the error handler. |
| `backend/app/db.py` | `api_keys` schema plus CRUD functions, following existing conventions. |
| `backend/app/store.py` | `incr_counter` on the `Store` ABC, `RedisStore`, and `MemoryStore`. |
| `backend/app/main.py` | `include_router` for `/v1`, plus the three admin routes. |
| `backend/app/settings.py` | `ledger_api_base_url`, defaulting to `http://ledger-api:8100`. |
| `frontend/src/components/ApiKeysPanel.tsx` (new) | The admin panel. |
| `frontend/src/AdminDashboard.tsx` | Renders the new panel. |

## Testing

**`backend/tests/test_api_keys.py`** — generate/verify round-trip; wrong secret
rejected; unknown id rejected; revoked key rejected; scope checks pass and fail;
airport restriction allows listed and denies unlisted ICAOs; `NULL` airports
allows any.

**`backend/tests/test_public_api.py`** — for each endpoint: 401 with no key, 403
with a key lacking the scope, 403 with a key restricted to another airport, 200
on the happy path. Plus cursor round-trip returning the next page without
overlap or gaps, `limit` clamped at 5000, track span over 31 days rejected, and
the 429 path with rate-limit headers. Ledger endpoints are tested against a
stubbed upstream, including the 503 when it is unreachable.

**`backend/tests/test_admin_api_keys.py`** — create, list, and revoke under
cookie auth; the secret appears in the create response and in no subsequent
list; a revoked key no longer authenticates; the admin routes reject an
unauthenticated caller.

## Out of Scope

Write endpoints, self-serve signup, billing or usage metering, per-key rate
limits or quotas, key expiry dates, per-key row caps, OAuth, and bulk file
exports. Per-key limits and expiry were considered and explicitly declined;
revocation covers the immediate need.
