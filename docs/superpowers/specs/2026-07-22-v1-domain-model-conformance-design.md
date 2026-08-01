# Conforming the public `/v1` API to the domain model

**Date:** 2026-07-22
**Status:** Approved design, ready for planning
**Branch base:** `feat/lost-landing-ledger` (where PR #2 merged). Not `main` — see
`.superpowers/sdd/progress.md`.

## Problem

An early user asked for "a simple API wrapper that can talk domain model." The wrapper is
not the interesting part of that sentence. The API cannot currently *be* wrapped in domain
terms, because its responses do not state what they mean.

Three concrete demonstrations, all verified against the live API:

**1. The same prefix, two different scales.**

```
/v1/operations   pct_off_pattern : 0.42    a fraction (0..1)
/v1/airports/{icao}/stats  stopped_pct : 28.9    a percent (0..100)
```

A consumer cannot know which is which without reading our source.

**2. A derived value published without its inputs or its threshold.**
`pct_off_pattern` is, per `deviation.py`, the time-weighted fraction of time an aircraft
spent more than `CORRIDOR_NM = 0.25` nautical miles from the *selected* pattern polyline.
The API publishes the ratio, withholds `time_off_pattern_s` and `time_total_s` (both
computed), and never states the 0.25 nm corridor. The value cannot be reproduced or
verified by the consumer.

**3. An internal sort key published as a domain field.**
`/v1/airports/{icao}/worst-offenders` returns `product: 15089.4`, which is
`total_circles × vnap_score` (498 × 30.3 — exact). It is the ranking scalar, not a
property of the aircraft.

Beyond those: `/v1/airports/{icao}/flow` exposes raw database row ids (`id`, `icao`,
`trigger_op_id`); `/v1/airports/{icao}/vnap-compliance` returns `averages.runway29`, a
KLMO-specific runway number used as a *field name*, so the response schema changes shape
per airport; `/flow` carries `cowboy_callsign`, product voice rather than domain language;
`tail` on one endpoint is `registration` on another; and six of the eleven endpoints splat
internal database shapes straight to the wire.

## Goals

1. Every published value states its own meaning: unit, scale, and datum legible from the
   field name and the schema.
2. Response shapes are frozen and enforced structurally — a new database column cannot
   reach a partner.
3. The published document and the served responses cannot drift apart.
4. The schema is identical across airports, so a typed client can model it once.

## Non-goals

- Restructuring resources or introducing entity relationships and links. That is a much
  larger project and would invalidate the SDK conversation currently underway with the
  requester.
- Building the client SDK itself. This design makes an SDK *possible* to write honestly;
  it does not write one.
- Changing any internal shape, database column, or frontend route. Only the `/v1` contract
  moves.
- Rate limits, per-key quotas, or auth changes. Untouched.

## Decisions

### D1. Depth: honest fields and stated semantics

Freeze explicit field lists on all eleven endpoints; fix misnamed fields; put units in
every name; expose the inputs behind derived values; declare closed enums, scales, and
thresholds in the schema. Response shapes stay broadly familiar — the values become
self-describing.

Rejected: nesting related scalars into structured value objects (a bigger change to every
consumer's parsing for a smaller gain), and full resource restructuring (see Non-goals).

### D2. Version: change `/v1` in place

Exactly one API key exists — the operator's own scratch key, named `asfasd`, minted the
same day. External consumers: zero. This is the cheapest this change will ever be, and the
price rises with every key issued.

Rejected: a parallel `/v2` (two contracts, two test suites, and a deprecation story
maintained permanently to protect one scratch key), and additive-only deprecation (the
wrong values stay on the wire indefinitely).

### D3. Enforcement: Pydantic response models plus an extended drift gate

One model per response in a new `backend/app/v1_schemas.py`, wired through FastAPI's
`response_model=`. Leakage becomes structurally impossible rather than test-dependent: an
added database column is filtered by the framework, not by a test someone might forget to
update.

Its own module because `public_api.py` is already ~900 lines, and the contract is
precisely the thing that should be readable in one sitting — the same reasoning that keeps
`api_keys.py` and `admin_users.py` separate.

Rejected: field-list constants alone (nothing binds them to the published document), and
generating the YAML from the models (reverses D7 of the previous design and pushes the
hand-written descriptions, examples, and error envelope into code annotations).

### D4. Ratios are fractions, named `fraction_*`

`0..1` values carry a `fraction_` prefix. Fractions compose — multiply, average,
threshold — without anyone wondering about ×100, and the prefix makes the range
unguessable-at.

This also resolves the `pct_off_pattern` / `stopped_pct` contradiction by removing the
`pct` prefix from the vocabulary entirely.

### D5. Airport-specific axes become a list of objects

`/vnap-compliance` currently keys `averages` and `scores` by axis code, including
`runway29`. Those become:

```json
"axes": [
  {"code": "runway_29", "label": "Runway 29 preference", "score": 86.7, "scale": "0..100"}
]
```

The schema is then identical at every airport, a typed client models it once, and adding
an axis at a new airport is not a breaking change.

### D6. Internal jargon and abbreviations are renamed

`cowboy_callsign` → `triggering_callsign`; `tg` → `touch_and_gos`; `pct_tg` →
`fraction_touch_and_go`; `rwy_against` → spelled out. A partner reading the document should
not need to know the product's voice to parse a field. The personality stays in the UI
copy, which is read by humans who are in on it.

### D7. The ledger endpoint is explicitly pass-through

`/v1/ledger/airports/{icao}/{resource}` is an HTTP proxy to the `ledger-api` sidecar. Its
body is the sidecar's shape, not ours. The envelope is frozen; the payload is documented as
the ledger's own schema and marked pass-through.

Pretending to own a shape we proxy would be a lie in the document, and fully typing it is a
separate effort against the sidecar.

### D8. The worst-offenders fallback fields stay, renamed

`source_icao` → `requested_airport_icao`, `resolved_icao` → `resolved_airport_icao`,
`resolved_label` → `resolved_airport_label`; `is_fallback` keeps its name.

A consumer must be able to tell that data came from a neighbouring airport. Concealing the
substitution is precisely what made the cross-airport leak in
`2026-07-20-public-api-keys-design.md` possible.

## Naming rules

Applied uniformly across all eleven endpoints.

| Kind | Convention | Example |
|---|---|---|
| Distance | `_nm` | `deviation_peak_nm` |
| Altitude | `_ft_agl` or `_ft_msl` — datum always explicit | `min_altitude_ft_agl` |
| Duration | `_s` | `time_off_pattern_s` |
| Epoch timestamp | `_ts` | `established_at_ts` |
| Ratio `0..1` | `fraction_` prefix | `fraction_off_pattern` |
| Count | plain plural noun | `touch_and_gos` |
| Score | `_score`, with `scale` documented in the schema | `vnap_score` |
| Rate of climb | `_fpm` | `vertical_rate_fpm` |

No abbreviations. No product jargon. No internal identifiers — database row ids and
foreign keys are never published.

## Contract changes, per endpoint

### `/v1/meta`

Gains a `constants` block, so thresholds and scales are stated once rather than repeated
on every row:

```json
"constants": {
  "pattern_corridor_nm": 0.25,
  "vnap_score_scale": "0..100",
  "axis_score_scale": "0..100"
}
```

### `/v1/operations`

| Now | Becomes | Why |
|---|---|---|
| `pct_off_pattern` (0..1) | `fraction_off_pattern` | D4; the name currently contradicts the value |
| — | `time_off_pattern_s` | already computed, currently withheld |
| — | `time_total_s` | already computed; makes the fraction reproducible |

`registration` is already correct and becomes the vocabulary for all endpoints.

### `/v1/tracks`

`timestamp` → `timestamp_ts`, per the epoch convention.

Altitude needs more than a rename. `live_sources.py` maps ADS-B `alt_baro` →
`baro_altitude_ft` and `alt_geom` → `geo_altitude_ft`, both with known datums. But
`altitude_ft` is populated from a *different* path — `flightaware.py` and
`track_history.py` — and its datum therefore **varies by source**. Publishing an altitude
whose datum cannot be stated violates this design's own first goal.

Resolution: keep `altitude_ft` (dropping it would lose the only altitude on
FlightAware-sourced samples) and add a sibling `altitude_datum` enum —
`"barometric" | "geometric" | "unknown"` — so the value states its own meaning. The
response already carries `source`, but a consumer should not have to maintain a
source-to-datum table to interpret a number.

### `/v1/airports/{icao}/stats`

`stop_classification.{all,pattern}.stopped_pct` (0..100) → `fraction_stopped` (0..1).
`counters.*` are counts and keep their names. `ops_over_time[]` keeps `bucket` and `count`.
The whole response is frozen behind a model.

### `/v1/airports/{icao}/runways`

Drop the duplicated internal `icao`; `airport_icao` remains on the envelope.

### `/v1/airports/{icao}/worst-offenders`

| Now | Becomes |
|---|---|
| `tail` | `registration` |
| `product` | **removed** — internal ranking scalar (`total_circles × vnap_score`) |
| `source_icao` | `requested_airport_icao` |
| `resolved_icao` | `resolved_airport_icao` |
| `resolved_label` | `resolved_airport_label` |
| `last_reported_at` | `last_reported_at_ts` |

`vnap_score` and `worst_axis_score` keep their names and gain a documented `0..100` scale.

### `/v1/airports/{icao}/operations-trends`

`pct_light` → `fraction_light_aircraft`; `pct_tg` → `fraction_touch_and_go`; `tg` →
`touch_and_gos`. The `by_emitter` and `by_type` maps are documented with their key sets.

### `/v1/airports/{icao}/vnap-compliance`

`averages` (keyed by axis, including `runway29`) → `axes: [{code, label, score, scale}]`
per D5. Per-aircraft `scores` and `metrics` — currently two parallel opaque maps with
*different* key sets (`scores` has `runway29`; `metrics` has `preferred_runway` and
`rwy_against`) — become lists of the same axis-object shape, with the abbreviation spelled
out. `tail` → `registration`.

### `/v1/airports/{icao}/flow`

Drop `id`, `icao`, `trigger_op_id`. `cowboy_callsign` / `cowboy_icao24` /
`cowboy_registration` → `triggering_callsign` / `triggering_icao24` /
`triggering_registration`. `established_at` → `established_at_ts`, `ended_at` →
`ended_at_ts`, `changed_at` → `changed_at_ts`.

### `/v1/ledger/airports/{icao}/{resource}`

Envelope frozen. Payload documented as pass-through per D7.

## Enforcement architecture

```
docs/api/openapi.yaml         hand-written, the published contract
        │
        ├── scripts/build_openapi_json.py → committed JSON (SPA + /v1/openapi.json)
        │
        └── scripts/verify_openapi_doc.py
                ├── paths, parameters, $refs        (exists)
                └── response fields vs v1_schemas   (NEW)

backend/app/v1_schemas.py     one Pydantic model per response
        └── public_api.py     response_model= on every route
```

The new gate check is what closes the hole that let `/runways` publish an undocumented
`icao` with the full suite green. It compares each model's field set against the schema the
document declares for that response, and fails on a mismatch in either direction.

## Testing

**Shape freeze, per endpoint.** For each of the eleven, assert the exact top-level and
nested key sets. These are the tests that fail when someone adds a database column.

**Leak regression.** A test asserting that no response contains any of the retired internal
names — `icao` (outside `airport_icao`), `id`, `trigger_op_id`, `product`, `cowboy_*`,
`pct_*`. One test, checked against every endpoint's response, so a reintroduction anywhere
fails.

**Scale correctness.** `fraction_*` fields assert `0.0 <= v <= 1.0`; `*_score` fields
assert the documented range. This is the test that would have caught `stopped_pct: 28.9`
sitting next to `pct_off_pattern: 0.42`.

**Reproducibility.** `fraction_off_pattern == time_off_pattern_s / time_total_s`, within
rounding — proving the published inputs actually explain the published ratio.

**Drift gate.** Add a documented field to a model without touching the YAML and confirm
`verify_openapi_doc.py` fails; then the reverse.

**Airport-independence.** The `/vnap-compliance` response validates against the same schema
for two different airports — the property D5 exists to create.

## Rollout

No migration story. `/v1` changes in place per D2, and the only key in existence is the
operator's scratch key.

No frontend page consumes these shapes. Verified: the single `/v1` reference in
`frontend/src` outside the generated JSON is `ApiDocsPanel.tsx:39`, which reads
`servers[0].url` to build curl examples — it depends on the base URL, not on any response
body. The previous design deliberately duplicated `_WINDOWS` and `_pattern_out` rather than
sharing them with the internal routes, precisely so the public contract could move
independently. That decoupling is what makes this change cheap.

Deploy order is unchanged from the existing constraints: Caddy and environment before the
app. The committed OpenAPI JSON must be regenerated in the same commit as the model
changes, or the pytest drift gate fails — which is the intended behavior.

## Risks

**Eleven endpoints change at once.** Mitigated by the shape-freeze tests and by there being
no external consumer. If it is going to happen, it has to happen now.

**`response_model` changes serialization behavior.** FastAPI will coerce and validate
outbound data; a field whose runtime type disagrees with the model becomes an error rather
than a silent pass-through. That is the point, but it may surface existing data-quality
problems — `/tracks` currently returns `altitude_ft: null` alongside `baro_altitude_ft:
0.0` and `geo_altitude_ft: 5225.0`, which is worth understanding rather than modelling
around.

**The ledger carve-out is a documented hole.** D7 is honest about it, but a partner reading
the document sees one endpoint whose payload we do not guarantee.

## Open items carried forward

Unchanged by this work:

- FastAPI's own `/docs` and `/openapi.json` remain reachable in production, enumerating
  internal routes. Set `docs_url=None, openapi_url=None`.
- The `/v1` error envelope is still not total for unhandled 500s (Starlette plaintext).
- `/admin/users` has no pagination.
