# Runway-Classified Average Loops + Std-Dev Band Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the historical overlay's Average mode to classify each pattern circuit by the runway it touches (or "area"), average within each class, and draw a configurable ±k·σ cross-track band around each mean loop.

**Architecture:** A new backend endpoint slices the archived track around each detected operation into a classified circuit polyline (runway or "area"). The frontend groups those circuits by class, computes a mean loop + per-point cross-track standard deviation, and renders a bold mean line plus a translucent band; the σ multiplier is a client-side control so it adjusts with no refetch.

**Tech Stack:** Backend — Python 3, FastAPI, SQLite (`app/db.py`, `app/main.py`, new `app/pattern_circuits.py`, existing `app/simplify.py`). Frontend — React + TypeScript + OpenLayers + Vitest (`frontend/src`).

## Global Constraints

- Backend tests run from `backend/`: `.venv/bin/python -m pytest tests/... -v` (pytest `pythonpath=["."]`, `asyncio_mode=auto`).
- Frontend tests run from `frontend/`: `npm run test` (Vitest). Build/typecheck: `npm run build` (`tsc && vite build`).
- Backend FastAPI routes are registered WITHOUT an `/api` prefix (the proxy adds `/api`); TestClient calls routes unprefixed (`/airports/{icao}/pattern-circuits`), the frontend client path is `/airports/...` and `getJson` prepends `/api`.
- `track_archive.icao24` is stored **lowercased**; `operations.icao24` must be `.lower()`-normalized before joining, or the join silently yields zero circuits.
- Operation types that form circuits: `touch_and_go`, `low_approach` (carry `runway_id`), and `circle` (no `runway_id`). Class = `runway_id` for the first two (fallback `"area"` if `runway_id` is NULL); `"area"` for `circle`.
- `days` is bounded [1, 7] via `Query(ge=1, le=7)` (422 on out-of-range).
- Endpoint accepts an optional `_now` epoch-seconds query param used ONLY by tests for a deterministic window; defaults to `int(time.time())`.
- Commit after every task; commit messages end with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- This feature REPLACES the bearing-bucket Average (`averagePaths`/`bearingBucket` in `averagePath.ts` and its use in `MapView.tsx`/`App.tsx`). `resamplePath` is retained and reused.

---

### Task 1: `pattern_circuits` module — classify + build circuits

**Files:**
- Create: `backend/app/pattern_circuits.py`
- Test: `backend/tests/test_pattern_circuits.py`

**Interfaces:**
- Consumes: `simplify.rdp_keep_mask(points: list[tuple[float,float]], epsilon: float) -> list[bool]` (existing).
- Produces:
  - Constants `CIRCUIT_WINDOW_S = 75`, `DEFAULT_CIRCUIT_CAP = 2000`, `SIMPLIFY_EPSILON_DEG = 1e-4`, `CIRCUIT_TYPES = ("touch_and_go", "low_approach", "circle")`, `MIN_DAYS = 1`, `MAX_DAYS = 7`.
  - `classify(op: dict) -> tuple[str, str | None]` — `(class_label, runway_id)`. A `circle` → `("area", None)`. A `touch_and_go`/`low_approach` with a truthy `runway_id` → `(str(runway_id), str(runway_id))`; with NULL/empty `runway_id` → `("area", None)`.
  - `build_circuits(operations: list[dict], tracks_by_icao: dict[str, list[dict]], *, window_s: int = CIRCUIT_WINDOW_S, epsilon: float = SIMPLIFY_EPSILON_DEG, cap: int = DEFAULT_CIRCUIT_CAP) -> tuple[list[dict], dict[str, int], int]` → `(circuits, counts_by_class, total)`. Each circuit dict: `{"class": str, "runway_id": str | None, "icao24": str, "samples": [{"lat","lon","timestamp"}]}`. `operations` are dicts (or sqlite3.Row-like) with keys `icao24`, `timestamp`, `type`, `runway_id`. `tracks_by_icao` maps **lowercased** icao24 → time-sorted sample dicts (`lat`,`lon`,`timestamp`). Dedup: operations of the same `(lower(icao24), class)` whose `[t-window, t+window]` ranges overlap collapse to one circuit (keep the earliest by timestamp). Circuits with < 2 points after simplification are dropped. Cap keeps the most recent `cap` circuits by their operation timestamp; `total` is the count before the cap.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_pattern_circuits.py`:

```python
from app import pattern_circuits as pc


def _track(icao, base):
    # A dense little arc of samples around `base`, 10s apart.
    return [
        {"icao24": icao, "timestamp": base + 10 * i, "lat": 40.10 + 0.001 * i, "lon": -105.10 + 0.001 * i}
        for i in range(-8, 9)
    ]


def test_classify_runway_and_area():
    assert pc.classify({"type": "touch_and_go", "runway_id": "29"}) == ("29", "29")
    assert pc.classify({"type": "low_approach", "runway_id": "11"}) == ("11", "11")
    assert pc.classify({"type": "circle", "runway_id": None}) == ("area", None)
    # runway op with missing runway falls back to area
    assert pc.classify({"type": "touch_and_go", "runway_id": None}) == ("area", None)


def test_build_circuits_slices_window_and_tags_class():
    ops = [
        {"icao24": "AAA111", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "BBB222", "timestamp": 5000, "type": "circle", "runway_id": None},
    ]
    tracks = {
        "aaa111": _track("aaa111", 1000),
        "bbb222": _track("bbb222", 5000),
    }
    circuits, counts, total = pc.build_circuits(ops, tracks)
    assert total == 2
    by_class = sorted(c["class"] for c in circuits)
    assert by_class == ["29", "area"]
    rwy = next(c for c in circuits if c["class"] == "29")
    assert rwy["runway_id"] == "29"
    assert rwy["icao24"] == "aaa111"
    # only samples within +/- 75s of ts=1000 (i.e. 925..1075) are kept
    assert all(925 <= s["timestamp"] <= 1075 for s in rwy["samples"])
    assert len(rwy["samples"]) >= 2


def test_build_circuits_dedupes_overlapping_same_class():
    # Two runway-29 ops on the same aircraft 20s apart -> windows overlap -> one circuit.
    ops = [
        {"icao24": "AAA111", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "AAA111", "timestamp": 1020, "type": "low_approach", "runway_id": "29"},
    ]
    tracks = {"aaa111": _track("aaa111", 1000)}
    circuits, counts, total = pc.build_circuits(ops, tracks)
    assert total == 1
    assert len(circuits) == 1
    assert counts == {"29": 1}


def test_build_circuits_caps_by_recency():
    ops = [
        {"icao24": f"a{i:05d}", "timestamp": 1000 + i * 1000, "type": "circle", "runway_id": None}
        for i in range(6)
    ]
    tracks = {f"a{i:05d}": _track(f"a{i:05d}", 1000 + i * 1000) for i in range(6)}
    circuits, counts, total = pc.build_circuits(ops, tracks, cap=2)
    assert total == 6
    assert len(circuits) == 2
    # kept the two most recent operation timestamps (i=4,5)
    kept = {c["icao24"] for c in circuits}
    assert kept == {"a00004", "a00005"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_pattern_circuits.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pattern_circuits'`.

- [ ] **Step 3: Implement the module**

Create `backend/app/pattern_circuits.py`:

```python
"""Classify pattern circuits by runway and slice their geometry.

For the historical overlay's Average mode: each detected operation
(touch-and-go / low-approach / circle) becomes a "circuit" = the slice of that
aircraft's archived track within +/- CIRCUIT_WINDOW_S of the operation time,
tagged with a class (the runway used, or "area" for pure circling).
"""

from __future__ import annotations

from .simplify import rdp_keep_mask

CIRCUIT_WINDOW_S = 75
DEFAULT_CIRCUIT_CAP = 2000
SIMPLIFY_EPSILON_DEG = 1e-4
CIRCUIT_TYPES = ("touch_and_go", "low_approach", "circle")
MIN_DAYS = 1
MAX_DAYS = 7


def classify(op) -> tuple[str, str | None]:
    """(class_label, runway_id) for an operation. Runway ops use their runway;
    circles and runway-less ops are 'area'."""
    op_type = op["type"]
    runway_id = op["runway_id"] if _has_key(op, "runway_id") else None
    if op_type in ("touch_and_go", "low_approach") and runway_id:
        return str(runway_id), str(runway_id)
    return "area", None


def _has_key(op, key) -> bool:
    try:
        return op[key] is not None
    except (KeyError, IndexError, TypeError):
        return False


def _slice_window(samples: list[dict], center_ts: int, window_s: int) -> list[dict]:
    lo, hi = center_ts - window_s, center_ts + window_s
    return [s for s in samples if lo <= s["timestamp"] <= hi]


def build_circuits(
    operations: list[dict],
    tracks_by_icao: dict[str, list[dict]],
    *,
    window_s: int = CIRCUIT_WINDOW_S,
    epsilon: float = SIMPLIFY_EPSILON_DEG,
    cap: int = DEFAULT_CIRCUIT_CAP,
) -> tuple[list[dict], dict[str, int], int]:
    """Slice each operation's aircraft track into a classified circuit polyline.

    tracks_by_icao keys must be lowercased icao24. Overlapping same-(icao24,class)
    windows collapse to one circuit (earliest kept). See module docstring.
    """
    # Sort operations by time so "earliest kept" dedup is deterministic.
    ops_sorted = sorted(operations, key=lambda o: int(o["timestamp"]))
    # kept_ranges[(icao_lower, class)] = list of (lo, hi) already taken
    kept_ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
    built: list[dict] = []
    for op in ops_sorted:
        icao_lower = str(op["icao24"]).lower()
        ts = int(op["timestamp"])
        class_label, runway_id = classify(op)
        lo, hi = ts - window_s, ts + window_s
        ranges = kept_ranges.setdefault((icao_lower, class_label), [])
        if any(lo <= r_hi and r_lo <= hi for (r_lo, r_hi) in ranges):
            continue  # overlaps an already-built circuit of this aircraft+class
        samples = tracks_by_icao.get(icao_lower)
        if not samples:
            continue
        window = _slice_window(samples, ts, window_s)
        if len(window) < 2:
            continue
        coords = [(s["lat"], s["lon"]) for s in window]
        mask = rdp_keep_mask(coords, epsilon)
        kept = [s for s, keep in zip(window, mask) if keep]
        if len(kept) < 2:
            continue
        ranges.append((lo, hi))
        built.append({
            "class": class_label,
            "runway_id": runway_id,
            "icao24": icao_lower,
            "ts": ts,
            "samples": [
                {"lat": s["lat"], "lon": s["lon"], "timestamp": s["timestamp"]}
                for s in kept
            ],
        })
    total = len(built)
    if total > cap:
        built.sort(key=lambda c: c["ts"], reverse=True)
        built = built[:cap]
    counts_by_class: dict[str, int] = {}
    for c in built:
        counts_by_class[c["class"]] = counts_by_class.get(c["class"], 0) + 1
        del c["ts"]  # internal sort key, not part of the response
    return built, counts_by_class, total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_pattern_circuits.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/pattern_circuits.py backend/tests/test_pattern_circuits.py
git commit -m "feat(pattern-circuits): classify + slice pattern circuits by runway

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `GET /airports/{icao}/pattern-circuits` endpoint

**Files:**
- Modify: `backend/app/main.py` (add endpoint after `get_airport_track_history`)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `db.get_airport`, `db.read_operations(conn, icao, start_ts, end_ts, types)` (returns sqlite3.Row list), `db.bulk_read_track_archive(conn, icao24s, start_ts, end_ts)` (returns `dict[lowercased-icao24, list[sample dict]]`), `pattern_circuits.build_circuits` + constants (Task 1).
- Produces: `GET /airports/{icao}/pattern-circuits?days=<1-7>` → JSON:
  ```json
  {
    "airport": {"icao": "...", "lat": 0, "lon": 0, "elevation_ft": 0},
    "days": 7,
    "window": {"start_ts": 0, "end_ts": 0},
    "circuits": [{"class": "29", "runway_id": "29", "icao24": "...", "samples": [{"lat": 0, "lon": 0, "timestamp": 0}]}],
    "counts_by_class": {"29": 0, "area": 0},
    "total_circuits": 0,
    "truncated": false
  }
  ```
  Unknown airport → 404. `days` out of [1,7] → 422.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py`:

```python
def test_pattern_circuits_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    now = 2000000000

    def _op(op_id, icao24, ts, op_type, runway):
        return {
            "id": op_id, "icao": "KTST", "icao24": icao24, "callsign": "N1",
            "registration": None, "type": op_type, "timestamp": ts,
            "runway_id": runway, "runway_heading_deg": None,
            "turn_direction": None, "min_altitude_ft_agl": None,
        }

    with db.db_session(settings.database_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("KTST", "TST", "Test Field", "Testville", "US", 40.1, -105.1, 5000, 0),
        )
        db.upsert_operation(conn, _op("op1", "AAA111", now - 3600, "touch_and_go", "29"))
        db.upsert_operation(conn, _op("op2", "BBB222", now - 3000, "circle", None))
        # archived tracks (lowercased icao24 in the table via archive_track_samples)
        db.archive_track_samples(conn, "AAA111", [
            {"timestamp": now - 3600 + 10 * i, "lat": 40.10 + 0.001 * i, "lon": -105.10 + 0.001 * i}
            for i in range(-4, 5)
        ])
        db.archive_track_samples(conn, "BBB222", [
            {"timestamp": now - 3000 + 10 * i, "lat": 40.12 + 0.001 * i, "lon": -105.12 + 0.001 * i}
            for i in range(-4, 5)
        ])
        conn.commit()

    with TestClient(app) as client:
        resp = client.get("/airports/KTST/pattern-circuits?days=7&_now=2000000000")
        assert resp.status_code == 200
        body = resp.json()
        assert body["airport"]["icao"] == "KTST"
        assert body["total_circuits"] == 2
        assert body["truncated"] is False
        classes = sorted(c["class"] for c in body["circuits"])
        assert classes == ["29", "area"]
        assert body["counts_by_class"] == {"29": 1, "area": 1}

    with TestClient(app) as client:
        assert client.get("/airports/ZZZZ/pattern-circuits").status_code == 404
        assert client.get("/airports/KTST/pattern-circuits?days=0").status_code == 422
        assert client.get("/airports/KTST/pattern-circuits?days=8").status_code == 422
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_pattern_circuits_endpoint -v`
Expected: FAIL — route not defined (404/422 on the days=7 call).

- [ ] **Step 3: Add the import**

In `backend/app/main.py`, add `pattern_circuits` to the existing `from . import ...` line (it currently reads `from . import db, patterns, track_history` plus `from .geo import bbox_for_radius`). Change to include `pattern_circuits`:

```python
from . import db, patterns, track_history, pattern_circuits
```

- [ ] **Step 4: Implement the endpoint**

In `backend/app/main.py`, immediately after the `get_airport_track_history` function, add:

```python
@app.get("/airports/{icao}/pattern-circuits")
async def get_airport_pattern_circuits(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    days: Annotated[int, Query(ge=pattern_circuits.MIN_DAYS, le=pattern_circuits.MAX_DAYS)] = 7,
    _now: Annotated[int | None, Query()] = None,
):
    """Pattern circuits (touch-and-go / low-approach / circle) over the last
    `days` days, each classified by runway (or 'area') and sliced to a short
    window of archived track around the operation. Feeds the Average overlay."""
    now = int(_now) if _now is not None else int(time.time())
    start_ts = now - days * 86400
    window = pattern_circuits.CIRCUIT_WINDOW_S
    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, icao)
        if airport is None:
            raise HTTPException(status_code=404, detail="airport not found")
        ops = [dict(row) for row in db.read_operations(
            conn, airport.icao, start_ts, now, types=list(pattern_circuits.CIRCUIT_TYPES)
        )]
        icao24s = sorted({str(o["icao24"]).lower() for o in ops if o.get("icao24")})
        tracks_by_icao = db.bulk_read_track_archive(
            conn, icao24s, start_ts - window, now + window,
        ) if icao24s else {}
    circuits, counts_by_class, total = pattern_circuits.build_circuits(ops, tracks_by_icao)
    return {
        "airport": {
            "icao": airport.icao,
            "lat": airport.lat,
            "lon": airport.lon,
            "elevation_ft": airport.elevation_ft,
        },
        "days": days,
        "window": {"start_ts": start_ts, "end_ts": now},
        "circuits": circuits,
        "counts_by_class": counts_by_class,
        "total_circuits": total,
        "truncated": total > len(circuits),
    }
```

- [ ] **Step 5: Run the endpoint test**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_pattern_circuits_endpoint -v`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite (no regressions)**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(api): GET /airports/{icao}/pattern-circuits endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Frontend API client + types for pattern circuits

**Files:**
- Modify: `frontend/src/lib/api.ts` (add after `getTrackHistory`)
- Test: `frontend/src/lib/patternCircuitsApi.test.ts`

**Interfaces:**
- Consumes: `getJson<T>` (existing).
- Produces:
  ```ts
  export interface PatternCircuitSample { lat: number; lon: number; timestamp: number; }
  export interface PatternCircuit { class: string; runway_id: string | null; icao24: string; samples: PatternCircuitSample[]; }
  export interface PatternCircuitsResponse {
    airport: { icao: string; lat: number; lon: number; elevation_ft: number };
    days: number;
    window: { start_ts: number; end_ts: number };
    circuits: PatternCircuit[];
    counts_by_class: Record<string, number>;
    total_circuits: number;
    truncated: boolean;
  }
  export function getPatternCircuits(icao: string, days: number): Promise<PatternCircuitsResponse>;
  ```

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/patternCircuitsApi.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { getPatternCircuits } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("getPatternCircuits", () => {
  it("requests the pattern-circuits path with the days param", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ circuits: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getPatternCircuits("KBJC", 5);
    const url = String((fn.mock.calls as unknown[][])[0][0]);
    expect(url).toContain("/api/airports/KBJC/pattern-circuits?days=5");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- patternCircuitsApi`
Expected: FAIL — `getPatternCircuits` is not exported.

- [ ] **Step 3: Implement the client**

In `frontend/src/lib/api.ts`, after the `getTrackHistory` function, add:

```ts
export interface PatternCircuitSample {
  lat: number;
  lon: number;
  timestamp: number;
}

export interface PatternCircuit {
  class: string;
  runway_id: string | null;
  icao24: string;
  samples: PatternCircuitSample[];
}

export interface PatternCircuitsResponse {
  airport: { icao: string; lat: number; lon: number; elevation_ft: number };
  days: number;
  window: { start_ts: number; end_ts: number };
  circuits: PatternCircuit[];
  counts_by_class: Record<string, number>;
  total_circuits: number;
  truncated: boolean;
}

export function getPatternCircuits(icao: string, days: number) {
  return getJson<PatternCircuitsResponse>(
    `/airports/${encodeURIComponent(icao)}/pattern-circuits?days=${days}`
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- patternCircuitsApi`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/patternCircuitsApi.test.ts
git commit -m "feat(api-client): getPatternCircuits + types

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `meanAndBand` geometry in averagePath.ts

**Files:**
- Modify: `frontend/src/lib/averagePath.ts` (add — do NOT remove `averagePaths` yet; Task 7 removes it)
- Test: `frontend/src/lib/averagePath.test.ts` (add a new describe block)

**Interfaces:**
- Consumes: existing `resamplePath(points: LonLat[], n: number): LonLat[]`, `LonLat` (existing in the file).
- Produces:
  ```ts
  export interface ClassCircuits { class: string; samples: Array<{ lon: number; lat: number }>[]; }
  export interface ClassAverage { class: string; count: number; mean: LonLat[]; band: LonLat[]; }
  export function meanAndBand(
    circuits: Array<{ class: string; samples: Array<{ lon: number; lat: number }> }>,
    opts?: { resampleN?: number; minCount?: number; sigmaK?: number }
  ): ClassAverage[];
  ```
  Groups circuits by `class`; for each class with ≥ `minCount` (default 3) circuits: resample each circuit to `resampleN` (default 48), average component-wise into `mean`; at each mean point compute the cross-track σ = stddev of members' signed perpendicular offset (projected onto the local normal), and build `band` as a closed ring = mean offset `+sigmaK·σ` forward then `−sigmaK·σ` back along local normals. `sigmaK` default 1. Result sorted by `count` descending. Classes below `minCount` are omitted. (`ClassCircuits` is exported for consumers that pre-group; not required internally.)

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/lib/averagePath.test.ts`:

```ts
import { meanAndBand } from "./averagePath";

describe("meanAndBand", () => {
  // Three horizontal circuits at lat 1.0, 1.1, 1.2 -> mean lat 1.1.
  const circuits = [1.0, 1.1, 1.2].map((lat) => ({
    class: "29",
    samples: [{ lon: 0, lat }, { lon: 1, lat }, { lon: 2, lat }],
  }));

  it("averages same-class circuits into a mean between them", () => {
    const out = meanAndBand(circuits, { resampleN: 3, minCount: 3, sigmaK: 1 });
    expect(out).toHaveLength(1);
    expect(out[0].class).toBe("29");
    expect(out[0].count).toBe(3);
    out[0].mean.forEach((p) => expect(p[1]).toBeCloseTo(1.1, 6));
  });

  it("band half-width scales with sigmaK", () => {
    const width = (k: number) => {
      const [avg] = meanAndBand(circuits, { resampleN: 3, minCount: 3, sigmaK: k });
      // band ring = forward offsets then reversed backward offsets; sample the
      // vertical spread at the first mean point vs its mirrored ring point.
      const top = avg.band[0][1];
      const bottom = avg.band[avg.band.length - 1][1];
      return Math.abs(top - bottom);
    };
    const w1 = width(1);
    const w2 = width(2);
    expect(w2).toBeGreaterThan(w1 * 1.8);
    expect(w2).toBeLessThan(w1 * 2.2);
  });

  it("omits a class below minCount", () => {
    const one = [{ class: "11", samples: [{ lon: 0, lat: 0 }, { lon: 1, lat: 0 }] }];
    expect(meanAndBand(one, { minCount: 3 })).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- averagePath`
Expected: FAIL — `meanAndBand` not exported.

- [ ] **Step 3: Implement `meanAndBand`**

In `frontend/src/lib/averagePath.ts`, append (keep existing exports intact):

```ts
export interface ClassAverage {
  class: string;
  count: number;
  mean: LonLat[];
  band: LonLat[];
}

// Unit normal (left-hand) to the local tangent at index i of a polyline.
function normalAt(points: LonLat[], i: number): LonLat {
  const a = points[Math.max(0, i - 1)];
  const b = points[Math.min(points.length - 1, i + 1)];
  const tx = b[0] - a[0];
  const ty = b[1] - a[1];
  const len = Math.hypot(tx, ty) || 1;
  // rotate tangent +90 deg
  return [-ty / len, tx / len];
}

export function meanAndBand(
  circuits: Array<{ class: string; samples: Array<{ lon: number; lat: number }> }>,
  opts?: { resampleN?: number; minCount?: number; sigmaK?: number }
): ClassAverage[] {
  const resampleN = opts?.resampleN ?? 48;
  const minCount = opts?.minCount ?? 3;
  const sigmaK = opts?.sigmaK ?? 1;

  const groups = new Map<string, LonLat[][]>();
  for (const circuit of circuits) {
    if (circuit.samples.length < 2) continue;
    const resampled = resamplePath(
      circuit.samples.map((p) => [p.lon, p.lat] as LonLat),
      resampleN
    );
    const list = groups.get(circuit.class) ?? [];
    list.push(resampled);
    groups.set(circuit.class, list);
  }

  const out: ClassAverage[] = [];
  for (const [cls, members] of groups) {
    if (members.length < minCount) continue;
    const mean: LonLat[] = [];
    for (let i = 0; i < resampleN; i += 1) {
      let sx = 0;
      let sy = 0;
      for (const m of members) {
        sx += m[i][0];
        sy += m[i][1];
      }
      mean.push([sx / members.length, sy / members.length]);
    }
    // Per-point cross-track sigma, then build the band ring.
    const upper: LonLat[] = [];
    const lower: LonLat[] = [];
    for (let i = 0; i < resampleN; i += 1) {
      const [nx, ny] = normalAt(mean, i);
      let sumSq = 0;
      for (const m of members) {
        const dx = m[i][0] - mean[i][0];
        const dy = m[i][1] - mean[i][1];
        const off = dx * nx + dy * ny; // signed cross-track offset
        sumSq += off * off;
      }
      const sigma = Math.sqrt(sumSq / members.length);
      const d = sigmaK * sigma;
      upper.push([mean[i][0] + nx * d, mean[i][1] + ny * d]);
      lower.push([mean[i][0] - nx * d, mean[i][1] - ny * d]);
    }
    // Closed ring: forward along upper, back along lower.
    const band: LonLat[] = [...upper, ...lower.slice().reverse()];
    out.push({ class: cls, count: members.length, mean, band });
  }
  out.sort((a, b) => b.count - a.count);
  return out;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm run test -- averagePath`
Expected: PASS (existing resample/averagePaths tests plus the 3 new `meanAndBand` tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/averagePath.ts frontend/src/lib/averagePath.test.ts
git commit -m "feat(geometry): meanAndBand — per-class mean loop + cross-track sigma band

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: MapView — render classified mean loops + σ bands

**Files:**
- Modify: `frontend/src/components/MapView.tsx`

**Interfaces:**
- Consumes: `PatternCircuit` (Task 3), `meanAndBand` + `ClassAverage` (Task 4), existing `polygonFeature`, `lineFeature`, `smoothSegment`, `fromLonLat`, `Fill`, `Stroke`, `Style`.
- Produces: MapView accepts `historyCircuits?: PatternCircuit[] | null` and `historySigmaK?: number`. In Average mode it renders, per class, a `history_band` filled polygon and a `history_avg` mean line, each carrying a `color` property; the density/lines paths are unchanged.

- [ ] **Step 1: Add imports and prop types**

In `MapView.tsx`, extend the api type import to include `PatternCircuit`:

```ts
import type { Airport, HistoricalTrack, Offender, PatternCircuit, RunwayPattern, ScanResponse, TrackSample } from "../lib/api";
```

Change the `averagePath` import from `averagePaths` to `meanAndBand`:

```ts
import { meanAndBand } from "../lib/averagePath";
```

Extend the `Props` interface (after `historyLineAlpha?: number;`):

```ts
  historyCircuits?: PatternCircuit[] | null;
  historySigmaK?: number;
```

- [ ] **Step 2: Destructure new props and broaden `historyActive`**

In the `MapView(...)` signature, add `historyCircuits = null, historySigmaK = 1` to the destructured props.

Change the `historyActive` line from:

```ts
  const historyActive = historyMode != null && historyTracks != null;
```

to:

```ts
  const historyActive = historyMode != null && (historyTracks != null || historyCircuits != null);
```

- [ ] **Step 3: Add a class-color helper**

Near the other module-level helpers (e.g. after `colorFromAltitudeAgl`), add:

```ts
// Stable color per circuit class. Runway classes take saturated hues in a
// fixed order; "area" is neutral gray.
const CLASS_PALETTE = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#d97706", "#0891b2"];
function classColor(cls: string, index: number): string {
  if (cls === "area") return "#6b7185";
  return CLASS_PALETTE[index % CLASS_PALETTE.length];
}
```

- [ ] **Step 4: Style `history_band` and update `history_avg` to use a per-feature color**

In `styleForHistoryFeature` (added in the alpha-slider work), replace its body so it handles `history_avg` and `history_band` with the feature's `color`, keeping `history_line` and the default delegation:

```ts
function styleForHistoryFeature(feature: Feature, lineAlpha: number) {
  const kind = feature.get("kind");
  if (kind === "history_line") {
    const altAgl = feature.get("alt_agl_ft");
    const [r, g, b] = colorFromAltitudeAgl(typeof altAgl === "number" ? altAgl : null);
    return new Style({
      stroke: new Stroke({ color: `rgba(${r}, ${g}, ${b}, ${lineAlpha})`, width: 1 }),
    });
  }
  if (kind === "history_band") {
    const color = String(feature.get("color") ?? "#6b7185");
    return new Style({ fill: new Fill({ color: hexToRgba(color, 0.16) }) });
  }
  if (kind === "history_avg") {
    const color = String(feature.get("color") ?? "#1b3a6b");
    return new Style({ stroke: new Stroke({ color, width: 3.5 }) });
  }
  return styleForFeature(feature);
}
```

Also add this small helper next to `classColor`:

```ts
function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
```

Remove the now-duplicate `history_avg` branch from `styleForFeature` (it is superseded by `styleForHistoryFeature`; the history layer is the only source of `history_avg`). Delete these lines from `styleForFeature`:

```ts
  if (kind === "history_avg") {
    return new Style({
      stroke: new Stroke({ color: "rgba(27, 58, 107, 0.9)", width: 4 }),
    });
  }
```

- [ ] **Step 5: Replace the Average render branch**

In the history render effect, replace the `else if (historyMode === "average") { ... }` branch (which currently calls `averagePaths`) with:

```ts
    } else if (historyMode === "average") {
      if (!historyCircuits) return;
      const classes = meanAndBand(
        historyCircuits.map((c) => ({ class: c.class, samples: c.samples })),
        { sigmaK: historySigmaK }
      );
      // Deterministic color order: runway classes sorted numerically, area last.
      const order = classes
        .map((c) => c.class)
        .sort((a, b) => (a === "area" ? 1 : b === "area" ? -1 : Number(a) - Number(b)));
      for (const cls of classes) {
        const color = classColor(cls.class, order.indexOf(cls.class));
        if (cls.band.length >= 4) {
          source.addFeature(polygonFeature(
            cls.band.map(([lon, lat]) => fromLonLat([lon, lat])),
            { kind: "history_band", color }
          ));
        }
        source.addFeature(lineFeature(
          smoothSegment(cls.mean.map(([lon, lat]) => fromLonLat([lon, lat]))),
          { kind: "history_avg", color }
        ));
      }
    }
```

- [ ] **Step 6: Add `historyCircuits`/`historySigmaK` to the render effect deps**

In that same effect's dependency array, replace:

```ts
  }, [mapReady, historyActive, historyMode, historyTracks, airport, groundElevFt]);
```

with:

```ts
  }, [mapReady, historyActive, historyMode, historyTracks, historyCircuits, historySigmaK, airport, groundElevFt]);
```

- [ ] **Step 7: Typecheck / build**

Run: `cd frontend && npm run build`
Expected: PASS (tsc + vite). `App.tsx` still passes the old props only, so `historyCircuits`/`historySigmaK` default to null/1 and Average shows nothing until Task 6 wires the fetch — expected.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/MapView.tsx
git commit -m "feat(map): render classified mean loops + sigma bands in Average mode

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: App — fetch circuits, σ control, class key, wire props

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `getPatternCircuits`, `PatternCircuitsResponse` (Task 3); MapView `historyCircuits`/`historySigmaK` props (Task 5).
- Produces: Average mode fetches pattern circuits, exposes a 1σ/2σ/3σ control + a class key, and passes `historyCircuits`/`historySigmaK` to MapView. The bearing-bucket `averageGroupCount`/`averagePaths` usage is removed.

- [ ] **Step 1: Imports and state**

In `App.tsx`, replace the import line:

```ts
import { averagePaths } from "./lib/averagePath";
```

with:

```ts
import { getPatternCircuits, type PatternCircuitsResponse } from "./lib/api";
```

(If `getPatternCircuits`/`PatternCircuitsResponse` are more naturally added to the existing multi-line `./lib/api` import block, add them there instead and delete the `averagePaths` import line entirely.)

After the `historyLineAlpha` state line, add:

```ts
  const [historySigmaK, setHistorySigmaK] = useState(1);
  const [historyCircuits, setHistoryCircuits] = useState<PatternCircuitsResponse | null>(null);
```

- [ ] **Step 2: Remove the bearing-bucket `averageGroupCount` memo**

Delete the entire `averageGroupCount` `useMemo` block (the one that calls `averagePaths`). It is replaced by circuit-based state.

- [ ] **Step 3: Fetch pattern circuits in Average mode**

After the existing track-history fetch effect, add:

```ts
  useEffect(() => {
    if (mapOverlay !== "history" || historyMode !== "average" || !airport?.icao) {
      setHistoryCircuits(null);
      return;
    }
    let cancelled = false;
    setHistoryCircuits(null);
    getPatternCircuits(airport.icao, historyDays)
      .then((data) => { if (!cancelled) setHistoryCircuits(data); })
      .catch(() => { if (!cancelled) setHistoryCircuits(null); });
    return () => { cancelled = true; };
  }, [mapOverlay, historyMode, airport?.icao, historyDays]);
```

- [ ] **Step 4: Add the σ control and class key (Average mode only)**

In the history controls panel, immediately after the `View` mode row's closing `</div>`, add:

```tsx
              {historyMode === "average" && (
                <>
                  <div className="history-controls-row">
                    <span className="history-controls-label">Spread</span>
                    <div className="segmented history-sigma">
                      {[1, 2, 3].map((k) => (
                        <button
                          key={k}
                          className={historySigmaK === k ? "active" : ""}
                          onClick={() => setHistorySigmaK(k)}
                        >
                          {k}σ
                        </button>
                      ))}
                    </div>
                  </div>
                  {historyCircuits && Object.keys(historyCircuits.counts_by_class).length > 0 && (
                    <div className="history-key">
                      {Object.entries(historyCircuits.counts_by_class)
                        .sort((a, b) => (a[0] === "area" ? 1 : b[0] === "area" ? -1 : Number(a[0]) - Number(b[0])))
                        .map(([cls, n]) => (
                          <span key={cls} className="history-key-item">
                            <i className={`history-key-dot cls-${cls === "area" ? "area" : "rwy"}`} />
                            {cls === "area" ? "Area" : `Rwy ${cls}`} ({n})
                          </span>
                        ))}
                    </div>
                  )}
                </>
              )}
```

- [ ] **Step 5: Replace the caption for Average mode**

Replace the existing caption block (which references `averageGroupCount`) with one that reports circuits in Average mode and flights otherwise:

```tsx
              <div className="history-controls-caption">
                {historyError
                  ? "Couldn't load history"
                  : historyMode === "average"
                    ? !historyCircuits
                      ? "Loading circuits…"
                      : historyCircuits.total_circuits === 0
                        ? "No classified circuits yet"
                        : `${historyCircuits.total_circuits.toLocaleString()} circuits${historyCircuits.truncated ? " (capped)" : ""}`
                    : !historyData
                      ? "Loading history…"
                      : historyData.truncated
                        ? `Showing ${historyData.tracks.length.toLocaleString()} of ${historyData.total_tracks.toLocaleString()} flights`
                        : `${historyData.tracks.length.toLocaleString()} flights`}
              </div>
```

- [ ] **Step 6: Pass the new props to MapView**

In the `<MapView .../>` element, after `historyLineAlpha={historyLineAlpha}`, add:

```tsx
            historyCircuits={mapOverlay === "history" && historyMode === "average" ? historyCircuits?.circuits ?? null : null}
            historySigmaK={historySigmaK}
```

- [ ] **Step 7: Styles for the key/dots**

In `frontend/src/styles.css`, after the `.history-alpha-value` rule, add:

```css
.history-sigma button {
  min-width: 30px;
}
.history-key {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  color: #4b5563;
}
.history-key-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.history-key-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
}
.history-key-dot.cls-rwy { background: #2563eb; }
.history-key-dot.cls-area { background: #6b7185; }
```

(The key dots use a single representative runway color; the map itself colors each runway class distinctly. This keeps the key legend simple without threading per-class colors through the DOM.)

- [ ] **Step 8: Build + full frontend test suite**

Run: `cd frontend && npm run build && npm run test`
Expected: PASS (build clean; all tests green — note `averagePaths`/`bearingBucket` are still defined and still tested at this point; Task 7 removes them).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(app): Average mode fetches classified circuits + sigma control + key

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Remove the dead bearing-bucket average

**Files:**
- Modify: `frontend/src/lib/averagePath.ts`, `frontend/src/lib/averagePath.test.ts`

**Interfaces:**
- Removes: `averagePaths`, `bearingBucket`, and the `AveragedPath` interface (no longer referenced by MapView or App after Tasks 5–6). Retains `resamplePath`, `LonLat`, and `meanAndBand`.

- [ ] **Step 1: Confirm nothing else references them**

Run: `cd frontend && grep -rn "averagePaths\|bearingBucket\|AveragedPath" src`
Expected: matches ONLY in `src/lib/averagePath.ts` and `src/lib/averagePath.test.ts` (no `MapView.tsx`/`App.tsx` hits). If any other file references them, STOP — a prior task left a dangling use; report it.

- [ ] **Step 2: Remove the functions**

In `frontend/src/lib/averagePath.ts`, delete the `AveragedPath` interface, the `bearingBucket` function, the `bearingFromAirport` helper, and the `averagePaths` function. Keep `LonLat`, `resamplePath`, `dist2` only if still used (`dist2`/`bearingFromAirport` were used only by `averagePaths` — remove them too), `normalAt`, and `meanAndBand`.

- [ ] **Step 3: Remove their tests**

In `frontend/src/lib/averagePath.test.ts`, delete the `describe("bearingBucket", ...)` and `describe("averagePaths", ...)` blocks and their imports of `averagePaths`/`bearingBucket`. Keep the `resamplePath` and `meanAndBand` describe blocks. The import line should read:

```ts
import { meanAndBand, resamplePath } from "./averagePath";
```

- [ ] **Step 4: Build + test**

Run: `cd frontend && npm run build && npm run test`
Expected: PASS — build clean (no unused-symbol type errors), all remaining tests green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/averagePath.ts frontend/src/lib/averagePath.test.ts
git commit -m "refactor(geometry): drop dead bearing-bucket average (replaced by meanAndBand)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Full suites**

Run: `cd backend && .venv/bin/python -m pytest -q && cd ../frontend && npm run test && npm run build`
Expected: all green.

- [ ] **Step 2: Endpoint shape check (if a local backend is available)**

If a local backend + DB with operations is running:
Run: `curl -sS "http://localhost:8000/api/airports/KBJC/pattern-circuits?days=3" | python3 -m json.tool | head -30`
Expected: JSON with `circuits`, `counts_by_class`, `total_circuits`, `truncated`. (If no local stack, this is covered by `test_pattern_circuits_endpoint`.)

- [ ] **Step 3: UI check (needs running stack)**

In the browser, with the history overlay on and **Average** selected:
1. Mean loops appear colored per runway class, each inside a translucent band.
2. The 1σ/2σ/3σ control widens/narrows the bands instantly (no network refetch).
3. The class key lists the runway/area classes with counts; the caption shows "N circuits".
4. Switching to Lines/Density still works; toggling the overlay off restores live tracks.

- [ ] **Step 4: Commit any verification fixes**

```bash
git add -A
git commit -m "fix: runway-classified average verification follow-ups

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Per-circuit classification by runway / "area" (incl. NULL-runway fallback) → Task 1 (`classify`). ✓
- ±75 s window slice + RDP + dedup + cap → Task 1 (`build_circuits`). ✓
- icao24 lowercase join → Task 1 (dedup keys lowercased) + Task 2 (endpoint lowercases icao24s). ✓
- Endpoint (days clamp, 404, counts_by_class, truncated, `_now`) → Task 2. ✓
- API client + types → Task 3. ✓
- Mean loop + per-point cross-track σ + ±k·σ band, client-side, sorted by count, minCount → Task 4 (`meanAndBand`). ✓
- Classes rendered together, colored, band beneath mean → Task 5. ✓
- σ control (1/2/3), class key, fetch in Average mode, caption → Task 6. ✓
- Remove bearing-bucket average → Task 7. ✓
- Testing (backend classify/window/dedup/cap/endpoint; frontend meanAndBand) → Tasks 1–4. ✓

**Placeholder scan:** No TBD/TODO; every code step carries complete code and exact commands.

**Type consistency:** `PatternCircuit` (Task 3) is consumed unchanged by MapView (Task 5) and App (Task 6). `meanAndBand(circuits, {sigmaK})` and `ClassAverage {class,count,mean,band}` match between Task 4 (def) and Task 5 (use). Endpoint response keys (`circuits`, `counts_by_class`, `total_circuits`, `truncated`) match between Task 2, the Task 3 `PatternCircuitsResponse`, and Task 6 usage. `historyCircuits: PatternCircuit[] | null` and `historySigmaK: number` are consistent across MapView (Task 5) and App (Task 6). `build_circuits` returns `(circuits, counts_by_class, total)` in Task 1 and is destructured the same way in Task 2. `styleForHistoryFeature` gains `history_band`/`history_avg` handling (Task 5), and the duplicate `history_avg` branch is removed from `styleForFeature` in the same task to avoid a stale second definition.
