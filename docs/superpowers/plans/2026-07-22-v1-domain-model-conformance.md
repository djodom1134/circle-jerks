# `/v1` Domain-Model Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every value the public `/v1` API publishes state its own meaning — unit, scale, and datum — and make the shapes structurally impossible to drift.

**Architecture:** A new I/O-free `backend/app/v1_schemas.py` holds one Pydantic model per response plus the builder that maps an internal row to it. `public_api.py` keeps the routing and auth and gains `response_model=` on every route. All eleven responses become named `components/schemas` entries in `docs/api/openapi.yaml`, and `verify_openapi_doc.py` gains a check binding each model's field set to the schema the document declares.

**Tech Stack:** FastAPI + Pydantic v2, SQLite via `sqlite3`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-22-v1-domain-model-conformance-design.md`

## Global Constraints

- Base branch is `feat/public-api-keys` (current HEAD `1770c57`). It carries one unmerged commit, `68b4b59`, the compose fix. `main` is a stale stub ~300 behind — never base on it.
- Backend tests: `cd backend && .venv/bin/python -m pytest -q`. Bare `python`/`pytest` are **not** on PATH.
- OpenAPI check: from repo root, `backend/.venv/bin/python scripts/verify_openapi_doc.py`.
- **A pytest drift gate already exists** (`backend/tests/test_openapi_drift_gate.py`). It fails when the committed JSON differs from the YAML. **Every task that edits `docs/api/openapi.yaml` MUST run `backend/.venv/bin/python scripts/build_openapi_json.py` and commit both regenerated files** — `frontend/src/generated/openapi.json` and `backend/app/generated/openapi.json` — or the suite goes red.
- **Only the `/v1` contract moves.** Do not rename database columns, internal dict keys, `main.py` routes, or frontend code. Renames happen at the serialization boundary, exactly as `_OPERATION_FIELDS` already does.
- Naming rules, applied uniformly: distances `_nm`; altitudes `_ft_agl`/`_ft_msl`; durations `_s`; epoch timestamps `_ts`; ratios `0..1` with a `fraction_` prefix; counts as plain plural nouns; scores `_score` with a documented scale; climb rate `_fpm`. No abbreviations, no product jargon, no internal identifiers.
- Auth, scopes, rate limits, and the error envelope are untouched.
- Baseline before Task 1: backend **760 passed**; frontend **19 files / 104 tests**; `verify_openapi_doc.py` clean.

---

### Task 1: Schema module foundations and `/v1/meta`

**Files:**
- Create: `backend/app/v1_schemas.py`
- Modify: `backend/app/public_api.py` (the `/meta` handler, ~line 360)
- Modify: `docs/api/openapi.yaml` (`components/schemas/Meta`)
- Test: `backend/tests/test_v1_schemas.py` (create)

**Interfaces:**
- Consumes: `api_keys.ApiKeyContext`
- Produces: `v1_schemas.PATTERN_CORRIDOR_NM`, `v1_schemas.VNAP_SCORE_SCALE`, `v1_schemas.AXIS_SCORE_SCALE`, `v1_schemas.Constants`, `v1_schemas.Limits`, `v1_schemas.MetaOut`, `v1_schemas.meta_out(ctx, environment) -> MetaOut`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_v1_schemas.py`:

```python
from __future__ import annotations

from app import v1_schemas
from app.api_keys import ApiKeyContext


def ctx(scopes=("ops:read",), airports=frozenset({"KLMO"})):
    return ApiKeyContext(key_id="a" * 16, name="k", scopes=frozenset(scopes), airports=airports)


def test_meta_publishes_the_corridor_constant():
    # The 0.25 nm corridor is what "off pattern" means. Publishing the ratio
    # without it leaves the consumer unable to reproduce or interpret it.
    out = v1_schemas.meta_out(ctx(), "production")
    assert out.constants.pattern_corridor_nm == 0.25


def test_meta_publishes_the_score_scales():
    out = v1_schemas.meta_out(ctx(), "production")
    assert out.constants.vnap_score_scale == "0..100"
    assert out.constants.axis_score_scale == "0..100"


def test_meta_reports_the_keys_own_grant():
    out = v1_schemas.meta_out(ctx(scopes=("ops:read", "tracks:read")), "production")
    assert out.scopes == ["ops:read", "tracks:read"]
    assert out.airports == ["KLMO"]


def test_unrestricted_key_reports_null_airports():
    out = v1_schemas.meta_out(ctx(airports=None), "production")
    assert out.airports is None


def test_corridor_constant_matches_the_deviation_module():
    # Binding test: if CORRIDOR_NM changes, the published constant must follow.
    from app.deviation import CORRIDOR_NM
    assert v1_schemas.PATTERN_CORRIDOR_NM == CORRIDOR_NM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_v1_schemas.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.v1_schemas'`

- [ ] **Step 3: Write the module**

Create `backend/app/v1_schemas.py`:

```python
"""The public /v1 contract: one model per response, plus the builders that map
an internal row onto it.

Deliberately free of FastAPI routing, sqlite3, and all I/O. public_api.py owns
auth, scoping, and routing; this module owns the shape. Keeping them apart means
the entire published contract is readable in one sitting, and that a database
column added tomorrow cannot reach a partner — the model filters it structurally
rather than relying on a test somebody remembers to update.

Renames live HERE, at the serialization boundary. Internal dict keys, database
columns, and the frontend keep their existing names.
"""

from __future__ import annotations

from pydantic import BaseModel

from . import api_keys
from .deviation import CORRIDOR_NM

# Published in /v1/meta so a consumer can interpret fraction_off_pattern.
PATTERN_CORRIDOR_NM = CORRIDOR_NM
VNAP_SCORE_SCALE = "0..100"
AXIS_SCORE_SCALE = "0..100"


class Constants(BaseModel):
    pattern_corridor_nm: float
    vnap_score_scale: str
    axis_score_scale: str


class Limits(BaseModel):
    requests_per_minute: int
    max_page_size: int
    max_track_span_seconds: int


class MetaOut(BaseModel):
    version: str
    name: str
    scopes: list[str]
    airports: list[str] | None
    limits: Limits
    constants: Constants


def meta_out(ctx: api_keys.ApiKeyContext, environment: str) -> MetaOut:
    from .public_api import MAX_PAGE_SIZE, MAX_TRACK_SPAN_SECONDS, RATE_LIMIT_PER_MINUTE

    return MetaOut(
        version="v1",
        name=ctx.name,
        scopes=sorted(ctx.scopes),
        airports=sorted(ctx.airports) if ctx.airports is not None else None,
        limits=Limits(
            requests_per_minute=RATE_LIMIT_PER_MINUTE,
            max_page_size=MAX_PAGE_SIZE,
            max_track_span_seconds=MAX_TRACK_SPAN_SECONDS,
        ),
        constants=Constants(
            pattern_corridor_nm=PATTERN_CORRIDOR_NM,
            vnap_score_scale=VNAP_SCORE_SCALE,
            axis_score_scale=AXIS_SCORE_SCALE,
        ),
    )
```

> Read the actual constant names at the top of `public_api.py` before writing that import — if the limits are spelled differently there, use the real names rather than these.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_v1_schemas.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Wire the route**

In `backend/app/public_api.py`, change the `/meta` route to declare and return the model:

```python
@router.get("/meta", summary="Describe the calling key", response_model=v1_schemas.MetaOut)
async def meta(
    ctx: Annotated[ApiKeyContext, Depends(require_any_scope)],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> v1_schemas.MetaOut:
    return v1_schemas.meta_out(ctx, settings.environment)
```

Add `from . import v1_schemas` to the imports. Keep the existing dependency and summary exactly as they are — read the current handler and change only the return path and the decorator.

- [ ] **Step 6: Update the document**

In `docs/api/openapi.yaml`, add `constants` to `components/schemas/Meta`:

```yaml
        constants:
          type: object
          description: |
            Values a consumer needs in order to interpret other responses.
            Published once here rather than repeated on every row.
          required: [pattern_corridor_nm, vnap_score_scale, axis_score_scale]
          properties:
            pattern_corridor_nm:
              type: number
              description: |
                Perpendicular distance from the published pattern beyond which an
                aircraft counts as "off pattern". `fraction_off_pattern` on
                /v1/operations is measured against this.
              example: 0.25
            vnap_score_scale:
              type: string
              description: Range of every `vnap_score`.
              example: "0..100"
            axis_score_scale:
              type: string
              description: Range of every per-axis `score`.
              example: "0..100"
```

Add `constants` to `Meta`'s `required` list.

- [ ] **Step 7: Regenerate, verify, commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py
backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
```
Expected: verifier clean; suite 765 passed.

```bash
cd /Users/d/Code/FAA_circle_jerk
git add backend/app/v1_schemas.py backend/app/public_api.py backend/tests/test_v1_schemas.py \
        docs/api/openapi.yaml backend/app/generated/openapi.json frontend/src/generated/openapi.json
git commit -m "feat(v1): schema module and published constants on /v1/meta"
```

---

### Task 2: `/v1/operations`

**Files:**
- Modify: `backend/app/v1_schemas.py`
- Modify: `backend/app/public_api.py` (`_OPERATION_FIELDS` ~line 488, `_operation_out` ~498, route ~524)
- Modify: `docs/api/openapi.yaml` (`components/schemas/Operation`)
- Test: `backend/tests/test_public_api_contract.py` (create)

**Interfaces:**
- Consumes: `v1_schemas` from Task 1
- Produces: `v1_schemas.OperationOut`, `v1_schemas.OperationPage`, `v1_schemas.operation_out(row) -> OperationOut`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_public_api_contract.py`:

```python
from __future__ import annotations

import pytest

from app import v1_schemas


def op_row(**kw) -> dict:
    base = {
        "id": "abc123", "airport_icao": "KLMO", "icao24": "a26f5e",
        "callsign": "N256SF", "registration": None, "type": "landing",
        "timestamp": 1784150045, "runway_id": "29", "turn_direction": None,
        "min_altitude_ft_agl": 0, "emitter_category": "A1",
        "deviation_mean_nm": 0.31, "deviation_peak_nm": 1.8,
        "pct_off_pattern": 0.42, "time_off_pattern_s": 42, "time_total_s": 100,
        "wind_from_deg": 30, "wind_speed_kt": 6.0,
        "origin_airport_icao": None, "origin_label": None,
        "operator": None, "flight_school": None,
    }
    base.update(kw)
    return base


def test_the_misnamed_ratio_is_renamed_and_still_a_fraction():
    out = v1_schemas.operation_out(op_row())
    assert out.fraction_off_pattern == 0.42
    assert not hasattr(out, "pct_off_pattern")


def test_the_inputs_behind_the_ratio_are_published():
    # Previously computed and withheld, which left the value unverifiable.
    out = v1_schemas.operation_out(op_row())
    assert out.time_off_pattern_s == 42
    assert out.time_total_s == 100


def test_the_published_inputs_actually_explain_the_published_ratio():
    out = v1_schemas.operation_out(op_row(time_off_pattern_s=42, time_total_s=100,
                                          pct_off_pattern=0.42))
    assert out.fraction_off_pattern == pytest.approx(
        out.time_off_pattern_s / out.time_total_s, abs=0.001
    )


def test_a_fraction_never_exceeds_one():
    out = v1_schemas.operation_out(op_row(pct_off_pattern=1.0))
    assert 0.0 <= out.fraction_off_pattern <= 1.0


def test_an_unknown_internal_column_cannot_reach_the_wire():
    # The whole point of the model layer: a column added to the query
    # tomorrow is filtered structurally, not by a test someone remembers.
    out = v1_schemas.operation_out(op_row(secret_internal_column="leak"))
    assert "secret_internal_column" not in out.model_dump()


def test_deviation_fields_are_absent_when_not_computed():
    out = v1_schemas.operation_out(op_row(
        pct_off_pattern=None, time_off_pattern_s=None, time_total_s=None,
        deviation_mean_nm=None, deviation_peak_nm=None,
    ))
    assert out.fraction_off_pattern is None
    assert out.time_off_pattern_s is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q`
Expected: FAIL — `AttributeError: module 'app.v1_schemas' has no attribute 'operation_out'`

- [ ] **Step 3: Add the model and builder**

Append to `backend/app/v1_schemas.py`:

```python
class OperationOut(BaseModel):
    id: str
    airport_icao: str
    icao24: str
    callsign: str | None
    registration: str | None
    type: str
    timestamp_ts: int
    runway_id: str | None
    turn_direction: str | None
    min_altitude_ft_agl: int | None
    emitter_category: str | None
    deviation_mean_nm: float | None
    deviation_peak_nm: float | None
    # Was `pct_off_pattern`, which held a fraction. The name contradicted the
    # value, and /stats' `stopped_pct` held a real percent — same prefix, two
    # scales, one API.
    fraction_off_pattern: float | None
    time_off_pattern_s: int | None
    time_total_s: int | None
    wind_from_deg: int | None
    wind_speed_kt: float | None
    origin_airport_icao: str | None
    origin_label: str | None
    operator: str | None
    flight_school: str | None


class OperationPage(BaseModel):
    data: list[OperationOut]
    next_cursor: str | None


def operation_out(row) -> OperationOut:
    return OperationOut(
        id=row["id"],
        airport_icao=row["airport_icao"],
        icao24=row["icao24"],
        callsign=row["callsign"],
        registration=row["registration"],
        type=row["type"],
        timestamp_ts=row["timestamp"],
        runway_id=row["runway_id"],
        turn_direction=row["turn_direction"],
        min_altitude_ft_agl=row["min_altitude_ft_agl"],
        emitter_category=row["emitter_category"],
        deviation_mean_nm=row["deviation_mean_nm"],
        deviation_peak_nm=row["deviation_peak_nm"],
        fraction_off_pattern=row["pct_off_pattern"],
        time_off_pattern_s=row["time_off_pattern_s"],
        time_total_s=row["time_total_s"],
        wind_from_deg=row["wind_from_deg"],
        wind_speed_kt=row["wind_speed_kt"],
        origin_airport_icao=row["origin_airport_icao"],
        origin_label=row["origin_label"],
        operator=row["operator"],
        flight_school=row["flight_school"],
    )
```

- [ ] **Step 4: Make the new columns available**

`time_off_pattern_s` and `time_total_s` are computed by `deviation.compute_deviation` and stored, but the `/v1` query does not select them. Read `_OPERATION_FIELDS` and the operations query in `public_api.py`, add both column names, and confirm with:

```bash
cd backend && .venv/bin/python -c "
from app import db
print([r[1] for r in db.connect('data/circlejerk.sqlite3').execute('PRAGMA table_info(operations)')])" 2>/dev/null | tr ',' '\n' | grep -E "time_off|time_total"
```
Expected: both column names printed. If they are absent from the table, they are computed but never stored — in that case stop and report it, because the spec's reproducibility guarantee depends on them.

- [ ] **Step 5: Wire the route**

Replace `_operation_out` usage in the `/operations` handler so it returns `v1_schemas.OperationPage`, and add `response_model=v1_schemas.OperationPage` to the decorator. Delete the now-unused `_OPERATION_FIELDS` and `_operation_out` from `public_api.py` — the model supersedes them.

- [ ] **Step 6: Update the document**

In `docs/api/openapi.yaml`, in `components/schemas/Operation`: rename `pct_off_pattern` → `fraction_off_pattern` with

```yaml
        fraction_off_pattern:
          type: number
          nullable: true
          minimum: 0
          maximum: 1
          description: |
            Time-weighted fraction of the lap spent farther than
            `constants.pattern_corridor_nm` from the selected published pattern.
            A fraction in 0..1 — 0.42 means 42%. Null when no pattern was matched.
          example: 0.42
        time_off_pattern_s:
          type: integer
          nullable: true
          description: Seconds spent outside the corridor. Numerator of `fraction_off_pattern`.
        time_total_s:
          type: integer
          nullable: true
          description: Seconds of lap track measured. Denominator of `fraction_off_pattern`.
```

Rename `timestamp` → `timestamp_ts` in the same schema.

- [ ] **Step 7: Regenerate, verify, commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py
backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
```

```bash
cd /Users/d/Code/FAA_circle_jerk
git add backend/app/v1_schemas.py backend/app/public_api.py backend/tests/test_public_api_contract.py \
        docs/api/openapi.yaml backend/app/generated/openapi.json frontend/src/generated/openapi.json
git commit -m "feat(v1): operations publishes an honest fraction with its inputs"
```

---

### Task 3: `/v1/tracks`

**Files:**
- Modify: `backend/app/v1_schemas.py`, `backend/app/public_api.py` (`_TRACK_FIELDS` ~570, route ~581)
- Modify: `docs/api/openapi.yaml` (`components/schemas/TrackSample`)
- Test: `backend/tests/test_public_api_contract.py`

**Interfaces:**
- Produces: `v1_schemas.TrackSampleOut`, `v1_schemas.TrackPage`, `v1_schemas.track_sample_out(row) -> TrackSampleOut`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_public_api_contract.py`:

```python
def track_row(**kw) -> dict:
    base = {
        "icao24": "ad076f", "timestamp": 1784752242, "lat": 40.038288,
        "lon": -105.233185, "altitude_ft": None, "baro_altitude_ft": 0.0,
        "geo_altitude_ft": 5225.0, "heading_deg": 0.0,
        "vertical_rate_fpm": 0.0, "callsign": "N939DM",
        "emitter_category": "A1", "source": "adsbx",
    }
    base.update(kw)
    return base


def test_altitude_carries_its_datum():
    # altitude_ft is populated from a different path than the ADS-B baro/geo
    # fields, so its datum varies by source. A number whose datum cannot be
    # stated is exactly what this whole change exists to eliminate.
    out = v1_schemas.track_sample_out(track_row(altitude_ft=1200.0, source="flightaware"))
    assert out.altitude_ft == 1200.0
    assert out.altitude_datum in {"barometric", "geometric", "unknown"}


def test_adsb_sourced_altitude_is_reported_barometric():
    out = v1_schemas.track_sample_out(track_row(altitude_ft=900.0, source="adsbx"))
    assert out.altitude_datum == "barometric"


def test_datum_is_unknown_when_there_is_no_altitude():
    out = v1_schemas.track_sample_out(track_row(altitude_ft=None))
    assert out.altitude_datum == "unknown"


def test_track_timestamp_uses_the_epoch_suffix():
    out = v1_schemas.track_sample_out(track_row())
    assert out.timestamp_ts == 1784752242
    assert not hasattr(out, "timestamp")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k track`
Expected: FAIL — no attribute `track_sample_out`

- [ ] **Step 3: Add the model and builder**

Append to `backend/app/v1_schemas.py`:

```python
# Which datum each live source's `altitude_ft` carries. live_sources.py maps
# ADS-B alt_baro/alt_geom into the explicit baro_/geo_ fields; altitude_ft is
# populated by other paths (flightaware.py, track_history.py) whose datum
# differs. Publishing the number without the datum would leave it unreadable.
_ALTITUDE_DATUM_BY_SOURCE = {
    "adsbx": "barometric",
    "adsb_lol": "barometric",
    "adsb_fi": "barometric",
    "airplanes_live": "barometric",
    "self_hosted": "barometric",
    "opensky": "barometric",
    "flightaware": "unknown",
}


class TrackSampleOut(BaseModel):
    icao24: str
    timestamp_ts: int
    lat: float
    lon: float
    altitude_ft: float | None
    altitude_datum: str
    baro_altitude_ft: float | None
    geo_altitude_ft: float | None
    heading_deg: float | None
    vertical_rate_fpm: float | None
    callsign: str | None
    emitter_category: str | None
    source: str | None


class TrackPage(BaseModel):
    data: list[TrackSampleOut]
    next_cursor: str | None


def track_sample_out(row) -> TrackSampleOut:
    altitude = row["altitude_ft"]
    datum = "unknown"
    if altitude is not None:
        datum = _ALTITUDE_DATUM_BY_SOURCE.get(row["source"], "unknown")
    return TrackSampleOut(
        icao24=row["icao24"],
        timestamp_ts=row["timestamp"],
        lat=row["lat"],
        lon=row["lon"],
        altitude_ft=altitude,
        altitude_datum=datum,
        baro_altitude_ft=row["baro_altitude_ft"],
        geo_altitude_ft=row["geo_altitude_ft"],
        heading_deg=row["heading_deg"],
        vertical_rate_fpm=row["vertical_rate_fpm"],
        callsign=row["callsign"],
        emitter_category=row["emitter_category"],
        source=row["source"],
    )
```

> Check the real source identifiers against `CIRCLEJERK_LIVE_SOURCE_PRIORITY` in `.env.example` and `live_sources.py`, and use those exact strings.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k track`
Expected: PASS

- [ ] **Step 5: Wire the route and document it**

Add `response_model=v1_schemas.TrackPage`, return the model, delete `_TRACK_FIELDS`/`_track_out`. In `docs/api/openapi.yaml`, rename `timestamp` → `timestamp_ts` in `TrackSample` and add:

```yaml
        altitude_datum:
          type: string
          enum: [barometric, geometric, unknown]
          description: |
            The datum `altitude_ft` is measured against. Varies by `source`;
            `unknown` when no altitude is present or the source does not say.
```

- [ ] **Step 6: Regenerate, verify, commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py && backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
cd /Users/d/Code/FAA_circle_jerk && git add -A backend/app backend/tests docs/api frontend/src/generated
git commit -m "feat(v1): track samples state their altitude datum"
```

---

### Task 4: `/v1/airports/{icao}/stats` and `/runways`

**Files:**
- Modify: `backend/app/v1_schemas.py`, `backend/app/public_api.py` (routes ~698, ~799)
- Modify: `docs/api/openapi.yaml` (`AirportStats`, `Runway`)
- Test: `backend/tests/test_public_api_contract.py`

**Interfaces:**
- Produces: `v1_schemas.StopClassification`, `v1_schemas.AirportStatsOut`, `v1_schemas.RunwayOut`, `v1_schemas.RunwaysOut`, `v1_schemas.airport_stats_out(...)`, `v1_schemas.runways_out(...)`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_public_api_contract.py`:

```python
def test_stopped_percent_becomes_a_fraction():
    # The headline inconsistency: this held 28.9 (a percent) while
    # operations' pct_off_pattern held 0.42 (a fraction). Same prefix.
    stats = {
        "counters": {"circles": 1, "touch_and_gos": 2, "low_approaches": 3,
                     "landings": 4, "passes": 5, "unique_aircraft": 6,
                     "runway_changes": 7},
        "ops_over_time": [{"bucket": 1784000000, "count": 3}],
        "runway_usage": [],
        "stop_classification": {
            "all": {"total": 532, "stopped": 154, "did_not_stop": 378, "stopped_pct": 28.9},
            "pattern": {"total": 237, "stopped": 154, "did_not_stop": 83, "stopped_pct": 65.0},
        },
    }
    out = v1_schemas.airport_stats_out("KLMO", {"code": "7d", "start_ts": 1, "end_ts": 2,
                                                "bucket_seconds": 3600}, stats)
    assert out.stop_classification.all.fraction_stopped == 0.289
    assert 0.0 <= out.stop_classification.all.fraction_stopped <= 1.0
    assert not hasattr(out.stop_classification.all, "stopped_pct")


def test_runways_drop_the_duplicated_internal_icao():
    rows = [{"icao": "KLMO", "runway_id": "29", "lat_threshold": 40.1,
             "lon_threshold": -105.1, "heading_deg": 290, "length_ft": 4800}]
    out = v1_schemas.runways_out("KLMO", rows)
    assert out.airport_icao == "KLMO"
    dumped = out.runways[0].model_dump()
    assert "icao" not in dumped
    assert dumped["runway_id"] == "29"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k "stopped or runways"`
Expected: FAIL — no attribute `airport_stats_out`

- [ ] **Step 3: Add the models and builders**

Append to `backend/app/v1_schemas.py`:

```python
class StopBreakdown(BaseModel):
    total: int
    stopped: int
    did_not_stop: int
    # Was `stopped_pct`, holding 28.9. Now a fraction, per the naming rule.
    fraction_stopped: float


class StopClassification(BaseModel):
    all: StopBreakdown
    pattern: StopBreakdown


class OpsBucket(BaseModel):
    bucket: int
    count: int


class StatsWindow(BaseModel):
    code: str
    start_ts: int
    end_ts: int
    bucket_seconds: int


class Counters(BaseModel):
    circles: int
    touch_and_gos: int
    low_approaches: int
    landings: int
    passes: int
    unique_aircraft: int
    runway_changes: int


class RunwayUsage(BaseModel):
    runway_id: str
    total: int
    upwind: int
    crosswind: int
    downwind: int
    no_wind_data: int


class AirportStatsOut(BaseModel):
    airport_icao: str
    window: StatsWindow
    counters: Counters
    ops_over_time: list[OpsBucket]
    runway_usage: list[RunwayUsage]
    stop_classification: StopClassification


def _stop_breakdown(raw: dict) -> StopBreakdown:
    return StopBreakdown(
        total=raw["total"],
        stopped=raw["stopped"],
        did_not_stop=raw["did_not_stop"],
        fraction_stopped=round(raw["stopped_pct"] / 100.0, 4),
    )


def airport_stats_out(airport_icao: str, window: dict, stats: dict) -> AirportStatsOut:
    return AirportStatsOut(
        airport_icao=airport_icao,
        window=StatsWindow(**window),
        counters=Counters(**stats["counters"]),
        ops_over_time=[OpsBucket(**b) for b in stats["ops_over_time"]],
        runway_usage=[RunwayUsage(**u) for u in stats.get("runway_usage", [])],
        stop_classification=StopClassification(
            all=_stop_breakdown(stats["stop_classification"]["all"]),
            pattern=_stop_breakdown(stats["stop_classification"]["pattern"]),
        ),
    )


class RunwayOut(BaseModel):
    runway_id: str
    lat_threshold: float | None
    lon_threshold: float | None
    heading_deg: float | None
    length_ft: int | None


class RunwaysOut(BaseModel):
    airport_icao: str
    runways: list[RunwayOut]


def runways_out(airport_icao: str, rows) -> RunwaysOut:
    return RunwaysOut(
        airport_icao=airport_icao,
        runways=[
            RunwayOut(
                runway_id=r["runway_id"],
                lat_threshold=r["lat_threshold"],
                lon_threshold=r["lon_threshold"],
                heading_deg=r["heading_deg"],
                length_ft=r["length_ft"],
            )
            for r in rows
        ],
    )
```

`RunwayUsage` was verified against the live response: `runway_id`, `total`, `upwind`,
`crosswind`, `downwind`, `no_wind_data` — no placeholder needed.

- [ ] **Step 4: Wire both routes, update the document**

Add `response_model=` to both, return the models, and delete the `**stats` splat in the stats handler. In `docs/api/openapi.yaml`: in `AirportStats`, rename `stopped_pct` → `fraction_stopped` with `minimum: 0, maximum: 1`; in `Runway`, **remove the `icao` property** and drop it from `required`.

- [ ] **Step 5: Regenerate, verify, commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py && backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
cd /Users/d/Code/FAA_circle_jerk && git add -A backend/app backend/tests docs/api frontend/src/generated
git commit -m "feat(v1): stats publishes a fraction, runways drops its internal icao"
```

---

### Task 5: `/v1/airports/{icao}/worst-offenders` and `/operations-trends`

**Files:**
- Modify: `backend/app/v1_schemas.py`, `backend/app/public_api.py` (routes ~748, ~767)
- Modify: `docs/api/openapi.yaml` (`WorstOffenders`, the inline trends schema)
- Test: `backend/tests/test_public_api_contract.py`

**Interfaces:**
- Produces: `v1_schemas.OffenderOut`, `v1_schemas.WorstOffendersOut`, `v1_schemas.TrendsOut`, `v1_schemas.worst_offenders_out(...)`, `v1_schemas.trends_out(...)`

- [ ] **Step 1: Write the failing test**

```python
def test_offender_uses_registration_not_tail():
    # /v1/operations already calls this `registration`. Two names for one
    # concept in one API is exactly what a domain model removes.
    row = {"icao24": "acbc30", "tail": "N92DV", "total_circles": 498,
           "vnap_score": 30.3, "product": 15089.4, "report_count": 3,
           "last_reported_at": 1783772707, "worst_axis": "tightness",
           "worst_axis_score": 65.0, "aircraft_type": None, "owner_class": "unknown"}
    out = v1_schemas.worst_offenders_out("KLMO", "KLMO", "Vance Brand", False, [row])
    o = out.offenders[0]
    assert o.registration == "N92DV"
    assert not hasattr(o, "tail")


def test_the_internal_sort_key_is_not_published():
    # `product` is total_circles * vnap_score — the ranking scalar, not a
    # property of the aircraft.
    row = {"icao24": "acbc30", "tail": "N92DV", "total_circles": 498,
           "vnap_score": 30.3, "product": 15089.4, "report_count": 3,
           "last_reported_at": 1783772707, "worst_axis": "tightness",
           "worst_axis_score": 65.0, "aircraft_type": None, "owner_class": "unknown"}
    out = v1_schemas.worst_offenders_out("KLMO", "KLMO", "Vance Brand", False, [row])
    assert "product" not in out.offenders[0].model_dump()


def test_fallback_provenance_is_named_honestly():
    out = v1_schemas.worst_offenders_out("KLMO", "KBJC", "Rocky Mountain", True, [])
    assert out.requested_airport_icao == "KLMO"
    assert out.resolved_airport_icao == "KBJC"
    assert out.is_fallback is True


def test_trend_ratios_are_fractions_and_abbreviations_expand():
    recent = [{"date": "2026-07-21", "operations": 40, "pct_light": 0.8, "pct_tg": 0.55}]
    monthly = [{"month": "2026-07", "total": 900, "landings": 300, "takeoffs": 300,
                "tg": 300, "pct_tg": 0.33, "by_type": {}, "by_emitter": {}}]
    out = v1_schemas.trends_out("KLMO", "America/Denver", 1780000000, recent, monthly,
                                [{"hour": 14, "operations": 12}])
    assert out.recent_days[0].fraction_light_aircraft == 0.8
    assert out.recent_days[0].fraction_touch_and_go == 0.55
    assert out.monthly[0].touch_and_gos == 300
    assert not hasattr(out.monthly[0], "tg")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k "offender or trend"`
Expected: FAIL — no attribute `worst_offenders_out`

- [ ] **Step 3: Add models and builders**

```python
class OffenderOut(BaseModel):
    icao24: str
    registration: str | None
    aircraft_type: str | None
    owner_class: str | None
    total_circles: int
    report_count: int
    vnap_score: float | None
    worst_axis: str | None
    worst_axis_score: float | None
    last_reported_at_ts: int | None


class WorstOffendersOut(BaseModel):
    requested_airport_icao: str
    resolved_airport_icao: str
    resolved_airport_label: str | None
    is_fallback: bool
    offenders: list[OffenderOut]


def worst_offenders_out(requested: str, resolved: str, label: str | None,
                        is_fallback: bool, rows) -> WorstOffendersOut:
    return WorstOffendersOut(
        requested_airport_icao=requested,
        resolved_airport_icao=resolved,
        resolved_airport_label=label,
        is_fallback=is_fallback,
        offenders=[
            OffenderOut(
                icao24=r["icao24"],
                registration=r["tail"],
                aircraft_type=r["aircraft_type"],
                owner_class=r["owner_class"],
                total_circles=r["total_circles"],
                report_count=r["report_count"],
                vnap_score=r["vnap_score"],
                worst_axis=r["worst_axis"],
                worst_axis_score=r["worst_axis_score"],
                last_reported_at_ts=r["last_reported_at"],
            )
            for r in rows
        ],
    )


class RecentDay(BaseModel):
    date: str
    operations: int
    fraction_light_aircraft: float | None
    fraction_touch_and_go: float | None


class MonthlyTrend(BaseModel):
    month: str
    total: int
    landings: int
    takeoffs: int
    touch_and_gos: int
    fraction_touch_and_go: float | None
    by_type: dict[str, int]
    by_emitter: dict[str, int]


class HourBucket(BaseModel):
    hour: int
    operations: int


class TrendsOut(BaseModel):
    airport_icao: str
    timezone: str
    data_since_ts: int
    recent_days: list[RecentDay]
    monthly: list[MonthlyTrend]
    time_of_day: list[HourBucket]


def trends_out(airport_icao: str, timezone: str, data_since: int,
               recent, monthly, time_of_day) -> TrendsOut:
    return TrendsOut(
        airport_icao=airport_icao,
        timezone=timezone,
        data_since_ts=data_since,
        recent_days=[
            RecentDay(date=d["date"], operations=d["operations"],
                      fraction_light_aircraft=d["pct_light"],
                      fraction_touch_and_go=d["pct_tg"])
            for d in recent
        ],
        monthly=[
            MonthlyTrend(month=m["month"], total=m["total"], landings=m["landings"],
                         takeoffs=m["takeoffs"], touch_and_gos=m["tg"],
                         fraction_touch_and_go=m["pct_tg"],
                         by_type=m["by_type"], by_emitter=m["by_emitter"])
            for m in monthly
        ],
        time_of_day=[HourBucket(**h) for h in time_of_day],
    )
```

> **Verify `pct_light` and `pct_tg` are genuinely 0..1 before mapping them to `fraction_*`.** Check the producing code in `db.py`; if either is a 0..100 percent, divide by 100 in the builder as Task 4 does for `stopped_pct`. Getting this backwards would reintroduce the exact defect this plan exists to remove.

- [ ] **Step 4: Wire routes, update the document, regenerate, commit**

Both routes get `response_model=`. In the YAML, update `WorstOffenders` (rename the three provenance fields, `tail` → `registration`, `last_reported_at` → `last_reported_at_ts`, **delete `product`**) and replace the inline trends schema with a named `OperationsTrends` component.

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py && backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
cd /Users/d/Code/FAA_circle_jerk && git add -A backend/app backend/tests docs/api frontend/src/generated
git commit -m "feat(v1): honest offender and trend vocabulary"
```

---

### Task 6: `/v1/airports/{icao}/vnap-compliance`

The largest single reshape: airport-specific field names become a uniform list.

**Files:**
- Modify: `backend/app/v1_schemas.py`, `backend/app/public_api.py` (route ~780)
- Modify: `docs/api/openapi.yaml` (inline → named `VnapCompliance`)
- Test: `backend/tests/test_public_api_contract.py`

**Interfaces:**
- Produces: `v1_schemas.AxisScore`, `v1_schemas.VnapAircraftOut`, `v1_schemas.VnapComplianceOut`, `v1_schemas.vnap_compliance_out(...)`, `v1_schemas.AXIS_LABELS`

- [ ] **Step 1: Write the failing test**

```python
def test_axes_are_a_uniform_list_not_airport_specific_keys():
    # `averages.runway29` made the response schema change shape per airport.
    # No typed client can model that, and no schema can honestly describe it.
    averages = {"tightness": 42.9, "runway29": 86.7, "composite": 51.0}
    out = v1_schemas.vnap_compliance_out(
        "KLMO", {"code": "7d", "start_ts": 1, "end_ts": 2},
        ["tightness", "runway29"], averages, [])
    codes = [a.code for a in out.axes]
    assert "runway_29" in codes
    assert all(a.scale == "0..100" for a in out.axes)
    assert isinstance(out.axes, list)


def test_every_axis_carries_a_human_label():
    out = v1_schemas.vnap_compliance_out(
        "KLMO", {"code": "7d", "start_ts": 1, "end_ts": 2},
        ["tightness"], {"tightness": 42.9}, [])
    assert out.axes[0].label
    assert out.axes[0].label != "tightness"


def test_composite_is_reported_separately_from_the_axes():
    out = v1_schemas.vnap_compliance_out(
        "KLMO", {"code": "7d", "start_ts": 1, "end_ts": 2},
        ["tightness"], {"tightness": 42.9, "composite": 51.0}, [])
    assert out.composite_score == 51.0
    assert "composite" not in [a.code for a in out.axes]


def test_two_airports_produce_the_same_schema():
    a = v1_schemas.vnap_compliance_out("KLMO", {"code": "7d", "start_ts": 1, "end_ts": 2},
                                       ["tightness", "runway29"],
                                       {"tightness": 1.0, "runway29": 2.0}, [])
    b = v1_schemas.vnap_compliance_out("KBJC", {"code": "7d", "start_ts": 1, "end_ts": 2},
                                       ["tightness", "runway11"],
                                       {"tightness": 3.0, "runway11": 4.0}, [])
    assert a.model_dump().keys() == b.model_dump().keys()
    assert a.axes[0].model_dump().keys() == b.axes[0].model_dump().keys()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k "axes or composite or two_airports"`
Expected: FAIL — no attribute `vnap_compliance_out`

- [ ] **Step 3: Add models and builders**

```python
AXIS_LABELS = {
    "tightness": "Pattern tightness",
    "altitude": "Pattern altitude",
    "timeofday": "Time of day",
    "tg_volume": "Touch-and-go volume",
    "circle_restraint": "Circle restraint",
    "left_traffic": "Left traffic adherence",
    "preferred_runway": "Preferred runway use",
}


def _axis_label(code: str) -> str:
    """Runway axes are named per airport (`runway29` at KLMO). Label them
    generically so the schema does not encode one airport's layout."""
    if code in AXIS_LABELS:
        return AXIS_LABELS[code]
    if code.startswith("runway"):
        return f"Runway {code.removeprefix('runway')} preference"
    return code.replace("_", " ").capitalize()


def _axis_code(code: str) -> str:
    """Normalize `runway29` to `runway_29` so codes read consistently."""
    if code.startswith("runway") and not code.startswith("runway_"):
        return "runway_" + code.removeprefix("runway")
    return code


class AxisScore(BaseModel):
    code: str
    label: str
    score: float | None
    scale: str


class VnapAircraftOut(BaseModel):
    icao24: str
    registration: str | None
    callsign: str | None
    aircraft_type: str | None
    owner_class: str | None
    operations: int
    circles: int
    touch_and_gos: int
    reports: int
    # Was `cowboy_count`. Confirm the semantics in vnap.py and adjust the name
    # if it counts something other than flagged operations.
    flagged_operation_count: int | None
    deviation_mean_nm: float | None
    vnap_score: float | None
    axes: list[AxisScore]


class VnapWindow(BaseModel):
    code: str
    start_ts: int
    end_ts: int


class VnapComplianceOut(BaseModel):
    airport_icao: str
    window: VnapWindow
    composite_score: float | None
    axes: list[AxisScore]
    aircraft: list[VnapAircraftOut]


def _axes(codes, scores: dict) -> list[AxisScore]:
    return [
        AxisScore(code=_axis_code(c), label=_axis_label(c),
                  score=scores.get(c), scale=AXIS_SCORE_SCALE)
        for c in codes
    ]


def vnap_compliance_out(airport_icao: str, window: dict, axis_codes,
                        averages: dict, aircraft_rows) -> VnapComplianceOut:
    return VnapComplianceOut(
        airport_icao=airport_icao,
        window=VnapWindow(**window),
        composite_score=averages.get("composite"),
        axes=_axes(axis_codes, averages),
        aircraft=[
            VnapAircraftOut(
                icao24=r["icao24"],
                # These rows carry BOTH `registration` and `tail` — the same
                # value under two names. Take `registration`; `tail` is dropped.
                registration=r["registration"],
                callsign=r["callsign"],
                aircraft_type=r["aircraft_type"],
                owner_class=r["owner_class"],
                operations=r["operations"],
                circles=r["circles"],
                touch_and_gos=r["touch_and_gos"],
                reports=r["reports"],
                flagged_operation_count=r.get("cowboy_count"),
                deviation_mean_nm=r["deviation_mean_nm"],
                vnap_score=r["vnap_score"],
                axes=_axes(axis_codes, r.get("scores") or {}),
            )
            for r in aircraft_rows
        ],
    )
```

Three fields on these rows are deliberately **not** carried forward. Say in your report which
call you made on each:

- **`metrics`** — a second opaque map with a *different* key set from `scores`
  (`preferred_runway`, `rwy_against` appear here but not there). If it is meaningful to a
  consumer, model it as a second labelled list of `AxisScore`; if it is internal, dropping
  it is correct. Read `vnap.py` to decide.
- **`tail`** — a duplicate of `registration` on the same row. Dropped; keep `registration`.
- **`owner_source`** — provenance of the ownership lookup, an internal detail. Drop unless
  `vnap.py` shows it carries meaning a partner needs.

- [ ] **Step 4: Wire the route, document it, regenerate, commit**

Replace the inline schema in the YAML with a named `VnapCompliance` component whose `axes` is an array of an `AxisScore` component. Document the `0..100` scale and that axis codes vary by airport while the shape does not.

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py && backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
cd /Users/d/Code/FAA_circle_jerk && git add -A backend/app backend/tests docs/api frontend/src/generated
git commit -m "feat(v1): vnap axes are airport-independent"
```

---

### Task 7: `/v1/airports/{icao}/flow`, `/patterns`, and the ledger envelope

**Files:**
- Modify: `backend/app/v1_schemas.py`, `backend/app/public_api.py` (routes ~811, ~823, ~852; `_pattern_out` ~685)
- Modify: `docs/api/openapi.yaml`
- Test: `backend/tests/test_public_api_contract.py`

**Interfaces:**
- Produces: `v1_schemas.ActiveFlowOut`, `v1_schemas.RunwayChangeOut`, `v1_schemas.FlowOut`, `v1_schemas.PatternOut` (one pattern), `v1_schemas.PatternsOut` (the envelope), `v1_schemas.LedgerEnvelope`, `v1_schemas.flow_out(...)`, `v1_schemas.patterns_out(...)`

- [ ] **Step 1: Write the failing test**

```python
def test_flow_publishes_no_internal_identifiers():
    active = {"id": 91, "icao": "KLMO", "active_runway_id": "29",
              "established_at": 1784700000, "ended_at": None,
              "wind_from_deg": 30, "wind_speed_kt": 6.0, "op_count": 12}
    changes = [{"id": 5, "icao": "KLMO", "changed_at": 1784690000,
                "from_runway_id": "11", "to_runway_id": "29",
                "trigger_op_id": "abc123", "wind_favored_new": True,
                "wind_from_deg": 30, "wind_speed_kt": 6.0,
                "cowboy_callsign": "N919CW", "cowboy_icao24": "acb861",
                "cowboy_registration": "N919CW"}]
    out = v1_schemas.flow_out("KLMO", active, changes)
    dumped = out.model_dump()
    for banned in ("id", "icao", "trigger_op_id"):
        assert banned not in dumped["active"]
        assert banned not in dumped["recent_changes"][0]


def test_product_voice_becomes_domain_language():
    changes = [{"id": 5, "icao": "KLMO", "changed_at": 1784690000,
                "from_runway_id": "11", "to_runway_id": "29",
                "trigger_op_id": "abc123", "wind_favored_new": True,
                "wind_from_deg": 30, "wind_speed_kt": 6.0,
                "cowboy_callsign": "N919CW", "cowboy_icao24": "acb861",
                "cowboy_registration": "N919CW"}]
    out = v1_schemas.flow_out("KLMO", None, changes)
    c = out.recent_changes[0]
    assert c.triggering_callsign == "N919CW"
    assert c.triggering_icao24 == "acb861"
    assert not hasattr(c, "cowboy_callsign")


def test_flow_timestamps_use_the_epoch_suffix():
    active = {"id": 91, "icao": "KLMO", "active_runway_id": "29",
              "established_at": 1784700000, "ended_at": None,
              "wind_from_deg": 30, "wind_speed_kt": 6.0, "op_count": 12}
    out = v1_schemas.flow_out("KLMO", active, [])
    assert out.active.established_at_ts == 1784700000
    assert out.active.ended_at_ts is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k flow`
Expected: FAIL — no attribute `flow_out`

- [ ] **Step 3: Add models and builders**

```python
class ActiveFlowOut(BaseModel):
    active_runway_id: str | None
    established_at_ts: int | None
    ended_at_ts: int | None
    wind_from_deg: int | None
    wind_speed_kt: float | None
    op_count: int | None


class RunwayChangeOut(BaseModel):
    changed_at_ts: int
    from_runway_id: str | None
    to_runway_id: str | None
    wind_favored_new: bool | None
    wind_from_deg: int | None
    wind_speed_kt: float | None
    # Was `cowboy_*` — the product's voice, not a term a partner can interpret.
    triggering_callsign: str | None
    triggering_icao24: str | None
    triggering_registration: str | None


class FlowOut(BaseModel):
    airport_icao: str
    active: ActiveFlowOut | None
    recent_changes: list[RunwayChangeOut]


def flow_out(airport_icao: str, active, changes) -> FlowOut:
    return FlowOut(
        airport_icao=airport_icao,
        active=None if not active else ActiveFlowOut(
            active_runway_id=active["active_runway_id"],
            established_at_ts=active["established_at"],
            ended_at_ts=active["ended_at"],
            wind_from_deg=active["wind_from_deg"],
            wind_speed_kt=active["wind_speed_kt"],
            op_count=active["op_count"],
        ),
        recent_changes=[
            RunwayChangeOut(
                changed_at_ts=c["changed_at"],
                from_runway_id=c["from_runway_id"],
                to_runway_id=c["to_runway_id"],
                wind_favored_new=c["wind_favored_new"],
                wind_from_deg=c["wind_from_deg"],
                wind_speed_kt=c["wind_speed_kt"],
                triggering_callsign=c["cowboy_callsign"],
                triggering_icao24=c["cowboy_icao24"],
                triggering_registration=c["cowboy_registration"],
            )
            for c in changes
        ],
    )
```

For `/patterns`, wrap the existing `_pattern_out` shape in a `PatternsOut` model — it is already an explicit field list, so this is a lift, not a redesign. For the ledger, model only the envelope and type the payload as pass-through per spec D7, documenting it as the sidecar's schema.

- [ ] **Step 4: Wire routes, document, regenerate, commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py && backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
cd /Users/d/Code/FAA_circle_jerk && git add -A backend/app backend/tests docs/api frontend/src/generated
git commit -m "feat(v1): flow drops internal ids and speaks domain language"
```

---

### Task 8: Bind the document to the models, and the cross-cutting guarantees

**Files:**
- Modify: `scripts/verify_openapi_doc.py`
- Test: `backend/tests/test_public_api_contract.py`, `backend/tests/test_openapi_drift_gate.py`

**Interfaces:**
- Consumes: every model from Tasks 1-7

- [ ] **Step 1: Write the failing tests**

```python
import json
import re

from fastapi.testclient import TestClient

# Names retired by this change. If any reappears in any /v1 response, a
# regression has reintroduced an internal shape.
RETIRED = ("pct_off_pattern", "stopped_pct", "pct_light", "pct_tg",
           "product", "tail", "cowboy_callsign", "cowboy_icao24",
           "cowboy_registration", "trigger_op_id", "source_icao",
           "resolved_icao")


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_keys(item)


def test_no_retired_name_appears_in_any_v1_response(v1_client_and_key):
    client, key = v1_client_and_key
    for path in ("/v1/meta", "/v1/operations?airport=KLMO&limit=1",
                 "/v1/airports/KLMO/stats", "/v1/airports/KLMO/runways",
                 "/v1/airports/KLMO/worst-offenders",
                 "/v1/airports/KLMO/operations-trends",
                 "/v1/airports/KLMO/vnap-compliance",
                 "/v1/airports/KLMO/flow", "/v1/airports/KLMO/patterns"):
        resp = client.get(path, headers={"X-Api-Key": key})
        assert resp.status_code == 200, path
        keys = set(_walk_keys(resp.json()))
        leaked = keys & set(RETIRED)
        assert not leaked, f"{path} still publishes {sorted(leaked)}"


def test_every_fraction_field_is_within_zero_and_one(v1_client_and_key):
    # The test that would have caught stopped_pct: 28.9 sitting beside
    # pct_off_pattern: 0.42.
    client, key = v1_client_and_key
    for path in ("/v1/operations?airport=KLMO&limit=50",
                 "/v1/airports/KLMO/stats",
                 "/v1/airports/KLMO/operations-trends"):
        body = client.get(path, headers={"X-Api-Key": key}).json()
        for k, v in _walk_pairs(body):
            if k.startswith("fraction_") and isinstance(v, (int, float)):
                assert 0.0 <= v <= 1.0, f"{path}: {k} = {v}"
```

The spec also asks for a per-endpoint "exact key set" freeze. That is satisfied
*structurally* rather than by a separate test: `response_model=` fixes wire keys to the
model's fields, and Step 3's gate binds the model's fields to the published schema — so
document, model, and wire are transitively pinned. The tests above cover what that chain
cannot: that retired names never come back, and that values stay inside their declared
ranges.

Write `_walk_pairs` as the key/value twin of `_walk_keys`. Build `v1_client_and_key` as a pytest fixture that configures a temp database, seeds one airport with a handful of operations, mints an unrestricted key, and yields `(client, full_key)` — follow the setup already used by `backend/tests/test_public_api.py` rather than inventing one.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k "retired or fraction_field"`
Expected: FAIL — fixture undefined.

- [ ] **Step 3: Extend the drift gate**

In `scripts/verify_openapi_doc.py`, after the existing checks, add a comparison between each response model's fields and the schema the document declares:

```python
    # Bind the served shape to the published one. The existing checks cover
    # paths, parameters and $refs — nothing compared response BODIES, which is
    # how /runways published an `icao` nobody intended with the suite green.
    from app import v1_schemas  # noqa: E402

    MODEL_FOR_SCHEMA = {
        "Meta": v1_schemas.MetaOut,
        "Operation": v1_schemas.OperationOut,
        "TrackSample": v1_schemas.TrackSampleOut,
        "AirportStats": v1_schemas.AirportStatsOut,
        "WorstOffenders": v1_schemas.WorstOffendersOut,
        "Runway": v1_schemas.RunwayOut,
        "OperationsTrends": v1_schemas.TrendsOut,
        "VnapCompliance": v1_schemas.VnapComplianceOut,
        "Flow": v1_schemas.FlowOut,
        "Pattern": v1_schemas.PatternOut,
    }
    for schema_name, model in MODEL_FOR_SCHEMA.items():
        declared = set((doc["components"]["schemas"].get(schema_name, {})
                        .get("properties") or {}).keys())
        actual = set(model.model_fields)
        for missing in sorted(actual - declared):
            problems.append(f"{schema_name}: model field '{missing}' is not documented")
        for extra in sorted(declared - actual):
            problems.append(f"{schema_name}: documented '{extra}' is not a model field")
```

Use the accumulator name the script actually uses — it is `problems`, not `failures`.

- [ ] **Step 4: Prove the gate is load-bearing**

Add a field to any model without touching the YAML:

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/verify_openapi_doc.py; echo "exit=$?"
```
Expected: non-zero exit naming that field. Remove the field, re-run, expect clean. **Report both outputs** — a gate never seen to fail is not a gate.

- [ ] **Step 5: Full sweep and commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python scripts/build_openapi_json.py
backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
cd ../frontend && npx vitest run && npm run build
```
Expected: all green.

```bash
cd /Users/d/Code/FAA_circle_jerk
git add scripts/verify_openapi_doc.py backend/tests/test_public_api_contract.py \
        backend/app/generated/openapi.json frontend/src/generated/openapi.json
git commit -m "test(v1): bind published schemas to response models"
```

---

## Verification summary

| Check | Command |
|---|---|
| Backend | `cd backend && .venv/bin/python -m pytest -q` |
| Contract + drift | `backend/.venv/bin/python scripts/verify_openapi_doc.py` |
| Frontend | `cd frontend && npx vitest run` |
| Frontend build | `cd frontend && npm run build` |

After merging, redeploy and confirm the contract live:

```bash
curl -s https://circlejerks.live/api/v1/meta -H "X-Api-Key: $KEY" | jq .constants
```
Expected: `pattern_corridor_nm: 0.25` and both scales.

## Known gaps, deliberately left

- `/v1/ledger/...` payload stays pass-through per spec D7.
- `runway_usage` inside `/stats` and the per-aircraft `metrics` map inside `/vnap-compliance` are called out in Tasks 4 and 6 as shapes to inspect and freeze during implementation; neither may ship as `dict`.
- FastAPI's own `/docs` and `/openapi.json` remain live in production, and the `/v1` error envelope is still not total for unhandled 500s. Both pre-date this work.
