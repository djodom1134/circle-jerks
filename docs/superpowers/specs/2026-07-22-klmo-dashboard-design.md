# KLMO Landing Operations Dashboard — Design

- **Date:** 2026-07-22
- **Status:** Design for review (precursor to an implementation plan)
- **Branch base:** feature branch off `main` (main is a stale stub ~292 behind — see repo memory). Depends on the live `/v1` public API (`backend/app/public_api.py`).
- **First deploy host:** `klmo.airfieldeconomics.org`

## Goal

A month-at-a-time landing-operations dashboard — one bar per day — that a visitor
can filter by **operation type** and by **who flew it** (local vs out-of-town),
plus a relational diagram of where the out-of-town traffic comes from. It is the
**first customer of the `/v1` API** and the **template for a multi-airport product**:
the same build serves any airport, selected by hostname.

The centerpiece story is airfieldeconomics' framing — *how much of this field's
traffic is transient, and from where* — told through a day-bar chart coloured by
locality and a radial hub of observed origins.

## Scope boundary (read first)

This spec covers **the frontend dashboard app only** — the React + Vite build, its
components, and its client-side data logic, developed and tested against **fixtures**
that match the data contract below.

**Explicitly owned by others / not built by this plan:**
- The **two `/v1` aggregate endpoints** (`daily-operations`, `origins`) and any
  scope-3 helpers — the user owns their implementation. This spec defines them **as a
  contract the frontend targets**, not as work to be done here.
- The **Caddy proxy / hosting** (same-origin `/api/*` → `/v1` with a server-side key)
  — the user owns it. The frontend assumes only that its endpoints are reachable at
  same-origin `/api/*`.
- The **year-long operations backfill** and the **observed-origin pipeline** that
  populates `operations.origin_airport_icao` / locality — a separate, already-running
  process.

The frontend treats locality/origin/coverage as a **data contract** fulfilled
elsewhere. It must degrade gracefully while that data is mid-flight: months with no
data render as honest empty/partial states, never as errors or fabricated zeros
presented as complete. **Delivered against fixtures, it is complete; it shows real
data once the endpoints and backfill land.**

## Non-goals

- No changes to how operations or origins are *produced* (that's the other process).
- No live map / raw-track view (the `/v1/tracks` surface is untouched).
- No per-airport branding system yet — one neutral analytics look, brand-tunable later.
- No auth/login for viewers; the dashboard shows public aggregate data.

---

## Architecture

### A new `dashboard/` app, airport from hostname

A new React + Vite + TypeScript app at repo root `dashboard/` (mirrors the
`lostlanding/` and `frontend/` shape: `src/`, `recharts`, `vitest`). It does **not**
extend `lostlanding` — that app is a single-narrative advocacy site wired to
`ledger-api`'s own DB; keeping the analytics product separate is what makes "first
customer of the public API" true rather than aspirational.

**Airport resolution:** the app reads the ICAO from `window.location.hostname`:
`klmo.airfieldeconomics.org` → `KLMO`. A small map (`{ "klmo": "KLMO", … }`) plus a
`?airport=` dev override. Adding an airport later is a DNS record + one map entry
(or a future wildcard `*.airfieldeconomics.org` with on-demand TLS). Nothing in the
UI hard-codes KLMO; every label, title, and query derives from the resolved airport
and its `/v1/meta`.

### Same-origin `/api/*` (proxy owned externally)

The frontend calls its endpoints at **same-origin relative paths** (`/api/…`) and
holds **no API key**. The Caddy proxy that serves `dashboard/dist`, reverse-proxies
`/api/*` → `/v1`, and injects `Authorization: Bearer <key>` (resolved **per host**,
so each customer airport gets its own key and 120 req/min budget) is **owned by the
user, not built here** — it follows the existing ledger block's same-origin `/api`
pattern. The frontend's only assumption is that `GET /api/airports/{icao}/…` reaches
the corresponding `/v1` route.

**Rate-budget consequence the frontend must respect:** the 120 req/min limit is
**shared across all visitors** of a host, so the app is designed to be *chunky, not
chatty* — one whole-history fetch, then client-side arithmetic (see below).

---

## Data contract

### Locality (the hard part)

Each operation carries the locality of the **aircraft that flew it** — one of three
buckets, derived by the backend following `ledger-api/app/homebase.py`'s
**asymmetric, evidence-based** rules (absence is never evidence):

| bucket | meaning | rule |
|---|---|---|
| `out_of_town` | a visitor | the aircraft was **positively observed arriving from a known other airport** (a named-origin observation) |
| `local` | based here | positive evidence the aircraft is based at this field (e.g. observed overnight stays / repeated same-field departures), per homebase.py |
| `unclassified` | unknown | no positive evidence either way — including operations that never leave the pattern (touch-and-goes, low approaches) with no home-base signal |

`out_of_town` requires a **positive** origin observation; anything unproven is
`unclassified`, **never** downgraded to a side we prefer. This holds homebase.py's
permanent contract: a false `local` costs nobody; a false `out_of_town` is a public
claim about a named party. The dashboard consumes these three buckets as a contract;
the exact derivation (and whether locality is stored per-op or joined from a
per-aircraft classification) lives in the backend/backfill, not the frontend.

### Endpoints — the contract the frontend targets (implemented by the user)

These are **not built by this plan**. They are the interface the app is written and
fixtured against; the user implements them on `/v1` (reached via the proxy at
same-origin `/api/*`). Shapes are normative — the fixtures mirror them exactly.

**1. `GET /api/airports/{icao}/daily-operations?from=&to=` — the workhorse (required)**

Returns **every day** in `[from, to]` (default: full available history), each day
faceted by **operation type × locality**. Scope: `aggregates:read`.

```jsonc
{
  "airport_icao": "KLMO",
  "timezone": "America/Denver",
  "coverage": { "min_day": "2025-07-21", "max_day": "2026-07-22" },  // what data actually exists
  "types": ["landing", "touch_and_go", "low_approach", "takeoff"],
  "localities": ["local", "out_of_town", "unclassified"],
  "days": [
    { "date": "2026-07-01",
      "counts": { "landing": {"local": 31, "out_of_town": 12, "unclassified": 8}, … } },
    …  // gap-filled: a real zero-day is {counts: all zeros}, distinct from outside coverage
  ]
}
```

The client fetches this **once** on load. Month navigation, every toolbar filter
combination (`type[] × locality[]`), the KPI numbers, and the 12-month
out-of-town-share trend are all **client-side arithmetic over this one payload** —
zero further API calls, trivially edge-cacheable. Sizing: 4 types × 3 localities ×
~365 days ≈ 50 KB. `coverage` lets the UI distinguish "outside our data" from "a day
with genuinely zero operations."

**Optional drill-in param `&origin=<icao>`:** returns the daily series for arrivals
from a single origin (faceted by type only — a single origin is by definition
out-of-town). One request per hub click; see the relational hub.

**2. `GET /api/airports/{icao}/origins?from=&to=` — feeds the relational hub (required)**

Per-origin **arrival counts** for the window, ranked, **top N + `other`** (origin
cardinality is unbounded, so this cannot live in the daily payload). Scope:
`aggregates:read`.

```jsonc
{ "airport_icao": "KLMO", "window": {"from": "…", "to": "…"},
  "total_out_of_town": 931,
  "origins": [ { "icao": "KBDU", "label": "Boulder", "arrivals": 341 }, … ],
  "other": { "count": 12, "arrivals": 58 } }
```

**Scope-3 panels — the frontend targets whichever source the user provides:**
- **Busiest aircraft:** the existing `/api/airports/{icao}/worst-offenders` is
  reachable today; the panel targets it unless the user prefers a per-aircraft
  op-count aggregate.
- **Hour-of-day profile:** needs a windowed 24-bucket source. The panel targets a
  `GET /api/airports/{icao}/hourly-profile?from=&to=` if the user provides one, and
  otherwise falls back to the existing `/operations-trends` `time_of_day` (all-time).
  The component is written source-agnostically behind `api.ts`.

Endpoints 1 and 2 are the required contract (hero chart, KPIs, trend, hub); the two
scope-3 panels degrade to "unavailable" cleanly if their source isn't wired yet.

---

## UI

### Layout A — toolbar + one hero chart, then supporting panels (scope 3)

```
┌──────────────────────────────────────────────────────────┐
│  KLMO · Longmont  ◄ July 2026 ►     [Type chips] [Who chips] │  toolbar
│                                       Colour by: (Locality|Type)│
├──────────────────────────────────────────────────────────┤
│  [ Operations ] [ Out of town % ] [ Busiest day ] [ Aircraft ] │  KPI row (filter-responsive)
├──────────────────────────────────────────────────────────┤
│                                                            │
│        ▐▐▐ HERO: one bar per day, stacked ▐▐▐             │  day-bar chart
│                                                            │
├─────────────┬─────────────────┬──────────────────────────┤
│ 12-mo trend │  Hour-of-day     │  Busiest aircraft         │  scope-3 panel row
├─────────────┴─────────────────┴──────────────────────────┤
│           RADIAL HUB — origins → KLMO (control)            │  relational section
└──────────────────────────────────────────────────────────┘
```

- **Filters are two independent dimensions.** Type chips (`landing / touch_and_go /
  low_approach / takeoff`) and Who chips (`local / out_of_town / unclassified`) combine
  freely ("touch-and-goes by out-of-town aircraft"). All applied client-side.
- **Colour-by switch, default `Locality`.** The hero bar stacks by locality by
  default (the airfieldeconomics story); a toolbar toggle re-stacks by operation
  type. Colour follows the entity, never its rank.
- **KPI row** recomputes from the current filter selection.
- **12-month trend** = client-side monthly rollup of the daily payload (out-of-town
  share), giving context above the single month in view.

### Relational hub — radial, and a control

KLMO at the centre; observed origin airports orbit it, **sized by arrivals**, arcs
drawing in on load with a continuous inbound pulse. **Hover** a node → an animated
message/receipt ("KBDU · Boulder — 341 arrivals, weekday mornings"). **Click** a node
→ **cross-filters the whole page** to that origin's arrivals: the hero chart, KPIs,
and trend narrow to it (`daily-operations?origin=KBDU`), a filter pill appears with a
✕ to clear. Hover to read, click to drive. Beyond the top N, origins fold into
`other` (never a generated 9th colour). Smooth transitions throughout
(transform/opacity, SVG stroke-dashoffset draw-in), honouring `prefers-reduced-motion`.

### Colour & accessibility

Colour is chosen **last**, from the validated dataviz palette (locality: blue=local,
orange=out_of_town, neutral-grey=unclassified; type uses the categorical order). The
palette is run through `dataviz/scripts/validate_palette.js` for light **and** dark
before shipping. Legend always present for ≥2 series; a table view exists; identity is
never colour-alone; dark mode is stepped for its own surface, not an auto-flip.

---

## Component isolation

Each is understandable and testable on its own:

- `lib/airport.ts` — hostname → ICAO resolution (+ `?airport=` override). Pure.
- `lib/api.ts` — typed fetch of the `/api/*` (proxied `/v1`) endpoints; returns the
  data contract types. No React.
- `lib/facets.ts` — pure reducers over the daily payload: apply `{types, localities}`
  → per-day series; monthly rollup; KPI aggregates. **Fully unit-tested; the
  arithmetic core.**
- `components/Toolbar.tsx` — month nav, type chips, who chips, colour-by switch.
- `components/DayBarChart.tsx` — the hero stacked bar; props = series + colour-by.
- `components/KpiRow.tsx`, `TrendPanel.tsx`, `HourProfilePanel.tsx`, `AircraftPanel.tsx`.
- `components/OriginHub.tsx` — the radial diagram; emits `onSelectOrigin(icao|null)`.
- `App.tsx` — holds filter + selected-origin state; wires panels to `facets` and `api`.

State lives in `App`; panels are prop-driven and side-effect-free except `api.ts`.

---

## States & edge cases

- **Backfill in progress:** `coverage` bounds the month picker; months entirely
  outside coverage show "No data yet for this period" (not zeros). Partial months
  render what exists and label the gap.
- **Genuine zero-day:** a day inside coverage with zero ops is a real bar of height
  zero, captioned — distinct from out-of-coverage (mirrors lostlanding's "zeroes are
  real zeroes" discipline).
- **Rate-limit / fetch error:** one visible error state with retry; never a silent
  empty chart.
- **Reduced motion:** hub arcs/pulses become static; draw-in is skipped.

## Multi-airport generalization

- Airport from hostname; labels/titles from `/v1/meta` + the airport record.
- Per-host API key via the proxy → per-customer rate budget & accounting.
- Adding an airport = DNS record + proxy key entry + hostname-map entry (no code).
- Locality/origin already generalize (any airport with an origin pipeline populated).

## Testing (frontend only — vitest + testing-library)

- `lib/facets.ts`: exhaustive unit tests — filter combinations, monthly rollup,
  coverage vs zero-day, KPI math. The arithmetic core.
- `lib/airport.ts`: hostname resolution + override.
- `lib/api.ts`: contract-shape parsing against **fixtures** that mirror the endpoint
  shapes above (checked into `dashboard/src/fixtures/`).
- Component tests: Toolbar filter emits, DayBarChart colour-by re-stack, OriginHub
  `onSelectOrigin` cross-filter, empty/partial-coverage rendering.
- No backend tests in this plan — the endpoints are owned and tested by the user.

## Dependencies (explicit — owned outside this plan)

The dashboard is buildable, testable, and mergeable **now** against fixtures. It
shows real KLMO data once the user has: (1) implemented the `daily-operations` and
`origins` endpoints and the Caddy proxy, and (2) the separate process has classified
the year of operations and populated `operations.origin_airport_icao` so locality
resolves. Until then the fixtures stand in, and against live-but-partial data the UI
honestly shows what exists, bounded by `coverage`.

## Decisions locked (via brainstorming + visual companion)

1. **Frontend only.** The `/v1` endpoints and the Caddy proxy are a contract this app
   targets; the user owns their implementation. Built and tested against fixtures.
2. Locality from observed origin, homebase.py's asymmetric rules → `local /
   out_of_town / unclassified`.
3. Filters = **two independent dimensions** (type × locality).
4. **New `dashboard/` app**, airport from **hostname**.
5. Frontend calls **same-origin `/api/*`**; key/proxy owned externally, per-host key.
6. Daily endpoint contract = **one request, whole history, pre-faceted** by type ×
   locality; all toolbar filtering is client-side.
7. **Layout A** (toolbar + one hero chart).
8. Colour-by default **locality**, toggle to type.
9. Composition **scope 3** (full dashboard).
10. Relational diagram = **radial hub**, interactive as a **control** (click origin →
    cross-filter the page).
