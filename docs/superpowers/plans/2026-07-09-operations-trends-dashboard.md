# Operations Trends Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Superior-Air-Tracker-style "Operations Trends" section to `/stats` — 1 table + 5 time-series charts — backed by a new `operations-trends` endpoint, with three new measurements (takeoff detection, ADS-B emitter category, airport-local timezone).

**Architecture:** Emitter category is threaded parser → sample dict → runway-event → `operations.emitter_category` (Redis preserves the JSON sample for free; `track_archive` gets one column for the cold path). A new `detect_takeoffs_over_period` reuses the existing runway-low-episode machinery, distinguished from touch-and-go by the *absence* of a prior high-altitude approach. A new `airport_operations_trends()` aggregates monthly/daily/hourly rollups in airport-local time. The frontend adds one `OperationsTrends.tsx` component rendered on the existing stats page.

**Tech Stack:** Python 3 (FastAPI, raw `sqlite3`, no ORM), pytest; React + TypeScript + Recharts + Vite/Vitest.

## Global Constraints

- Backend tests live in `backend/tests/test_*.py`, run with `cd backend && python -m pytest`. Fixtures build a DB via `seeded_conn(path)` = `db.connect` + `executescript(db.SCHEMA)` + `db.seed_db` (see `backend/tests/test_stats.py:6-11`).
- Schema changes are **additive only**, applied two ways: new tables/columns go in `db.SCHEMA` (idempotent `CREATE TABLE IF NOT EXISTS`), and columns added to **existing** tables must ALSO be added in `db._migrate()` via the `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` pattern (`db.py:386-398`) so already-deployed prod databases get them. Sequence these after the pending `runway_patterns` UNIQUE migration.
- "**Operation**" in this feature = `landing + takeoff + touch_and_go` ONLY. Never fold `circle`, `pass_over_user`, or `low_approach` into the operations denominator.
- Emitter category is **forward-only**: never attempt to backfill it; historical rows stay `NULL`.
- Emitter category codes are ADS-B string codes: `A0`–`A7`, `B1`–`B7`, `C1`–`C4`. Category buckets shown to users: `A1`=Light, `A2`=Small, `A6`=High Performance, `B1`=Glider, `B4`=Paraglider/Ultralight, everything else = `Other`.
- Detector event ids are stable sha256 hashes of `(type, icao24, airport, bucket)` via `_event_id(...)`; adding a new type (`takeoff`) with its own event-id seed keeps idempotency intact.
- Frontend charts follow the existing Recharts idiom in `StatsPage.tsx` (`ResponsiveContainer` + `fontSize={11}` axes + `interval="preserveStartEnd"`). Colors reuse the page palette (`#1b3a6b` navy, `#b3231f` red) plus a small extended set defined in Task 9.

## File Structure

**Backend (modify):**
- `backend/app/db.py` — schema columns, `_migrate`, timezone seed, `operation_from_event`, `upsert_operation`, track-archive INSERT/reconstruction, new `airport_operations_trends()`, new tz helpers.
- `backend/app/live_sources.py` — capture `category` in `parse_readsb_aircraft`.
- `backend/app/opensky.py` — capture emitter category (state-vector index 17) in `parse_state_vector`.
- `backend/app/detectors.py` — `_build_runway_event` emitter passthrough, T&G approach guard, new `detect_takeoffs_over_period`, wire into `detect_events_over_period`, `event_counts`.
- `backend/app/main.py` — new `GET /airports/{icao}/operations-trends` route.

**Frontend (create + modify):**
- `frontend/src/components/OperationsTrends.tsx` — **create**: the section component.
- `frontend/src/lib/api.ts` — **modify**: `OperationsTrendsResponse` type + `getOperationsTrends()`.
- `frontend/src/components/StatsPage.tsx` — **modify**: render `<OperationsTrends icao={icao} />`.
- `frontend/src/styles.css` — **modify**: minor styles for the new table/charts.

**Tests (create/extend):**
- `backend/tests/test_emitter_capture.py` — **create**.
- `backend/tests/test_takeoffs.py` — **create**.
- `backend/tests/test_operations_trends.py` — **create**.
- `backend/tests/test_timezone.py` — **create**.

---

### Task 1: Schema — emitter & timezone columns + migration

**Files:**
- Modify: `backend/app/db.py` (`SCHEMA` ~L63/202/236, `_migrate` L386-398, `AIRPORT_SEED` L319, `seed_db` L401-425)
- Test: `backend/tests/test_timezone.py` (create)

**Interfaces:**
- Produces: `airports.timezone` (TEXT, IANA), `operations.emitter_category` (TEXT), `track_archive.emitter_category` (TEXT), all nullable; seeded airports have `timezone` populated.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_timezone.py`:

```python
from __future__ import annotations

from app import db


def _cols(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_new_columns_and_tz_seed(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    db.init_db(path)
    conn = db.connect(path)
    assert "timezone" in _cols(conn, "airports")
    assert "emitter_category" in _cols(conn, "operations")
    assert "emitter_category" in _cols(conn, "track_archive")
    tz = conn.execute("SELECT timezone FROM airports WHERE icao='KLMO'").fetchone()["timezone"]
    assert tz == "America/Denver"


def test_migrate_adds_columns_to_legacy_db(tmp_path):
    # Simulate a pre-existing DB missing the new columns, then migrate.
    path = str(tmp_path / "legacy.sqlite3")
    conn = db.connect(path)
    conn.executescript(
        "CREATE TABLE airports (icao TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE operations (id TEXT PRIMARY KEY, type TEXT);"
        "CREATE TABLE track_archive (icao24 TEXT, timestamp INTEGER, PRIMARY KEY(icao24, timestamp));"
        "CREATE TABLE submission_aircraft (submission_id INTEGER, icao24 TEXT, PRIMARY KEY(submission_id, icao24));"
    )
    conn.commit()
    db._migrate(conn)
    conn.commit()
    assert "emitter_category" in _cols(conn, "operations")
    assert "emitter_category" in _cols(conn, "track_archive")
    assert "timezone" in _cols(conn, "airports")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_timezone.py -v`
Expected: FAIL (columns absent / tz not seeded).

- [ ] **Step 3: Add columns to `SCHEMA`**

In `db.py`, add `timezone TEXT` to the `airports` table (after `is_towered`, L39):

```python
CREATE TABLE IF NOT EXISTS airports (
  icao TEXT PRIMARY KEY,
  iata TEXT,
  name TEXT NOT NULL,
  city TEXT NOT NULL,
  country TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  elevation_ft INTEGER NOT NULL,
  is_towered INTEGER NOT NULL DEFAULT 0,
  timezone TEXT
);
```

Add `emitter_category TEXT` to `operations` (after `min_altitude_ft_agl`, L247) and to `track_archive` (after `source`, L214):

```python
  min_altitude_ft_agl INTEGER,
  emitter_category TEXT,
```

```python
  source TEXT,
  emitter_category TEXT,
```

- [ ] **Step 4: Generalize `_migrate` for the three tables**

Replace `db._migrate` (L386-398) with a table-driven version:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    additions: dict[str, list[tuple[str, str]]] = {
        "submission_aircraft": [
            ("circles", "INTEGER NOT NULL DEFAULT 0"),
            ("touch_and_gos", "INTEGER NOT NULL DEFAULT 0"),
            ("low_approaches", "INTEGER NOT NULL DEFAULT 0"),
            ("passes_over_user", "INTEGER NOT NULL DEFAULT 0"),
            ("origin_airport_icao", "TEXT"),
            ("origin_label", "TEXT"),
        ],
        "airports": [("timezone", "TEXT")],
        "operations": [("emitter_category", "TEXT")],
        "track_archive": [("emitter_category", "TEXT")],
    }
    for table, cols in additions.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not existing:
            continue  # table doesn't exist yet; SCHEMA will create it with the column
        for column, decl in cols:
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
```

- [ ] **Step 5: Seed timezones in `seed_db`**

Add a seed map near `AIRPORT_SEED` (after L330):

```python
AIRPORT_TIMEZONE_SEED = {
    "KBJC": "America/Denver", "KLMO": "America/Denver", "KBDU": "America/Denver",
    "KAPA": "America/Denver", "KCFO": "America/Denver", "KDEN": "America/Denver",
    "KFNL": "America/Denver", "KGXY": "America/Denver", "KCOS": "America/Denver",
    "KFTG": "America/Denver",
}
```

In `seed_db` (after the `runways`/`complaint_forms` seeds, ~L425), add:

```python
    for icao, tz in AIRPORT_TIMEZONE_SEED.items():
        conn.execute("UPDATE airports SET timezone=? WHERE icao=? AND timezone IS NULL", (tz, icao))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_timezone.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Run the full backend suite (guard against regressions)**

Run: `cd backend && python -m pytest -q`
Expected: PASS (no existing test asserts a fixed column count).

- [ ] **Step 8: Commit**

```bash
git add backend/app/db.py backend/tests/test_timezone.py
git commit -m "feat(db): add emitter_category + airport timezone columns and migration"
```

---

### Task 2: Capture ADS-B emitter category in both parsers

**Files:**
- Modify: `backend/app/live_sources.py` (`parse_readsb_aircraft` L94-111)
- Modify: `backend/app/opensky.py` (`parse_state_vector` L127-150)
- Test: `backend/tests/test_emitter_capture.py` (create)

**Interfaces:**
- Produces: every parsed sample dict gains an `"emitter_category"` key (string ADS-B code, or `None`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_emitter_capture.py`:

```python
from __future__ import annotations

from app.live_sources import parse_readsb_aircraft
from app.opensky import parse_state_vector, OPENSKY_CATEGORY_CODES


def test_readsb_captures_category():
    row = {"hex": "a1b2c3", "lat": 40.1, "lon": -105.1, "flight": "N123",
           "alt_baro": 5000, "gs": 80, "track": 120, "t": "C172", "category": "A1"}
    sample = parse_readsb_aircraft(row, payload_now=1_700_000_000, source="adsbfi")
    assert sample["emitter_category"] == "A1"


def test_readsb_missing_category_is_none():
    row = {"hex": "a1b2c3", "lat": 40.1, "lon": -105.1}
    sample = parse_readsb_aircraft(row, payload_now=1_700_000_000, source="adsbfi")
    assert sample["emitter_category"] is None


def test_opensky_maps_numeric_category():
    # 17-element rows previously returned None; index 17 = emitter category (2 = Light).
    row = ["abc123", "N1  ", "US", 1_700_000_000, 1_700_000_000,
           -105.1, 40.1, 1500.0, False, 40.0, 120.0, 0.0, None, 1600.0, None, False, 0, 2]
    sample = parse_state_vector(row, fallback_ts=1_700_000_000)
    assert sample["emitter_category"] == "A1"
    assert OPENSKY_CATEGORY_CODES[9] == "B1"  # glider


def test_opensky_short_row_has_no_category():
    row = ["abc123", "N1  ", "US", 1_700_000_000, 1_700_000_000,
           -105.1, 40.1, 1500.0, False, 40.0, 120.0, 0.0, None, 1600.0]  # len 14
    sample = parse_state_vector(row, fallback_ts=1_700_000_000)
    assert sample is not None
    assert sample["emitter_category"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_emitter_capture.py -v`
Expected: FAIL (`KeyError: 'emitter_category'` / `OPENSKY_CATEGORY_CODES` undefined).

- [ ] **Step 3: Add `emitter_category` to the readsb parser**

In `live_sources.py parse_readsb_aircraft`, add to the returned dict (after `"registration": row.get("r"),` L109):

```python
        "registration": row.get("r"),
        "emitter_category": row.get("category"),
        "source": source,
```

- [ ] **Step 4: Add the OpenSky category map + capture (relax the length guard)**

In `opensky.py`, above `parse_state_vector` (before L127), add the mapping:

```python
# OpenSky state-vector index 17 is the ADS-B emitter category as a 0-20 integer.
# Map it to the standard ADS-B string codes used elsewhere (A0-A7, B1-B7, C1-C4).
OPENSKY_CATEGORY_CODES = {
    0: None, 1: "A0", 2: "A1", 3: "A2", 4: "A3", 5: "A4", 6: "A5", 7: "A6",
    8: "A7", 9: "B1", 10: "B2", 11: "B3", 12: "B4", 14: "B6", 15: "B7",
    17: "C1", 18: "C2", 19: "C3", 20: "C4",
}
```

Then in `parse_state_vector`, keep the existing `if len(row) < 17: return None` guard (it protects indices ≤13) and add the category to the returned dict (after `"geo_altitude_ft": meters_to_feet(row[13]),` L148):

```python
        "geo_altitude_ft": meters_to_feet(row[13]),
        "emitter_category": OPENSKY_CATEGORY_CODES.get(row[17]) if len(row) > 17 else None,
        "source": "opensky",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_emitter_capture.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/live_sources.py backend/app/opensky.py backend/tests/test_emitter_capture.py
git commit -m "feat(ingest): capture ADS-B emitter category from readsb + opensky feeds"
```

---

### Task 3: Thread emitter category onto operations rows

**Files:**
- Modify: `backend/app/detectors.py` (`_build_runway_event` L633-655)
- Modify: `backend/app/db.py` (`operation_from_event` L452-470, `upsert_operation` L473-491, track-archive INSERT `_TRACK_ARCHIVE_COLUMNS` + rows tuple ~L1636-1662, `_track_archive_row_to_sample` L1796-1813)
- Test: `backend/tests/test_operations.py` (extend) — or add to `test_emitter_capture.py`

**Interfaces:**
- Consumes: sample dicts carry `emitter_category` (Task 2).
- Produces: `operations` rows persist `emitter_category`; `_build_runway_event` events carry `emitter_category`; archived samples round-trip it.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_emitter_capture.py`:

```python
from app import db
from app.db import Airport
from app.detectors import _build_runway_event


def _airport():
    return Airport(icao="KBJC", iata="BJC", name="RMMA", city="Broomfield, CO",
                   country="US", lat=39.9088, lon=-105.1172, elevation_ft=5673, is_towered=True)


def test_runway_event_carries_emitter():
    ep = {
        "bucket": 42,
        "lowest_sample": {"icao24": "abc123", "callsign": "N1", "timestamp": 1000,
                          "emitter_category": "A1"},
        "lowest_agl": 20.0,
        "runway": {"runway_id": "30R", "heading_deg": 300},
        "speed": 60,
    }
    event = _build_runway_event("landing", ep, _airport(), [])
    assert event["emitter_category"] == "A1"


def test_operation_row_persists_emitter(tmp_path):
    conn = db.connect(str(tmp_path / "t.sqlite3"))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    op = db.operation_from_event({
        "id": "op1", "type": "landing", "icao24": "abc123", "callsign": "N1",
        "timestamp": 1000, "airport_icao": "KBJC", "emitter_category": "A2",
    })
    db.upsert_operation(conn, op)
    row = conn.execute("SELECT emitter_category FROM operations WHERE id='op1'").fetchone()
    assert row["emitter_category"] == "A2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_emitter_capture.py -k emitter -v`
Expected: FAIL (event lacks key / column not written).

- [ ] **Step 3: Emit `emitter_category` from `_build_runway_event`**

In `detectors.py _build_runway_event`, add to the returned dict (after `"min_altitude_ft_agl": int(ep["lowest_agl"]),` L654):

```python
        "min_altitude_ft_agl": int(ep["lowest_agl"]),
        "emitter_category": lowest_sample.get("emitter_category"),
```

- [ ] **Step 4: Map + persist it in `operation_from_event` / `upsert_operation`**

In `db.py operation_from_event`, add (after `"min_altitude_ft_agl": event.get("min_altitude_ft_agl"),` L469):

```python
        "min_altitude_ft_agl": event.get("min_altitude_ft_agl"),
        "emitter_category": event.get("emitter_category"),
```

In `upsert_operation`, extend the INSERT column and value lists (L482-487):

```python
        INSERT INTO operations
          (id, icao, icao24, callsign, registration, type, timestamp,
           runway_id, runway_heading_deg, turn_direction, min_altitude_ft_agl,
           emitter_category)
        VALUES
          (:id, :icao, :icao24, :callsign, :registration, :type, :timestamp,
           :runway_id, :runway_heading_deg, :turn_direction, :min_altitude_ft_agl,
           :emitter_category)
        ON CONFLICT(id) DO NOTHING
```

- [ ] **Step 5: Round-trip emitter through the track archive**

Find `_TRACK_ARCHIVE_COLUMNS` (defined near the archive INSERT, ~L1620). Add `"emitter_category"` to the end of that list. In the rows-tuple builder (the `.append((...))` ending at L1650-1651), add `sample.get("emitter_category")` after `sample.get("source"),`. In the INSERT statement (L1656-1658), add one `?` to the `VALUES` tuple so the placeholder count matches the new column count (12 → 13 columns, plus `archived_at`):

```python
        INSERT OR IGNORE INTO track_archive
        ({', '.join(_TRACK_ARCHIVE_COLUMNS)}, archived_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(strftime('%s','now') AS INTEGER))
```

In `_track_archive_row_to_sample` (L1800-1813), add before the closing brace:

```python
        "source": row["source"],
        "emitter_category": row["emitter_category"],
    }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_emitter_capture.py tests/test_archive.py -v`
Expected: PASS (new emitter tests + archive round-trip unaffected).

- [ ] **Step 7: Commit**

```bash
git add backend/app/detectors.py backend/app/db.py backend/tests/test_emitter_capture.py
git commit -m "feat(ops): snapshot emitter_category onto operations + track archive"
```

---

### Task 4: Takeoff detector

**Files:**
- Modify: `backend/app/detectors.py` (`detect_touch_and_gos_over_period` L658-683, new `detect_takeoffs_over_period`, `detect_events_over_period` L805-826, `event_counts` L829-839)
- Test: `backend/tests/test_takeoffs.py` (create)

**Interfaces:**
- Consumes: `_runway_low_episodes`, `_after_samples`, `_climbed_out`, `_approached_from_altitude`, `_build_runway_event` (all in `detectors.py`).
- Produces: events with `type="takeoff"`; `event_counts(...)` gains a `"takeoffs"` key.

**Definitions (mutually exclusive over the same runway-low episodes):**
- **takeoff** = runway-low + climbed out + did NOT approach from altitude (originated at the field).
- **touch_and_go** = runway-low ≤50 ft + climbed out + DID approach from altitude.
- **low_approach** = runway-low >50 ft + climbed out + DID approach from altitude.
- **landing** = first runway-low + approached from altitude + did NOT climb out (unchanged).

Adding the approach requirement to touch_and_go/low_approach is an intentional refinement so a pure departure is counted once, as a takeoff, not double-counted as a touch-and-go.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_takeoffs.py`:

```python
from __future__ import annotations

from app.db import Airport
from app.detectors import (
    detect_takeoffs_over_period,
    detect_touch_and_gos_over_period,
    event_counts,
)

RWY = [{"runway_id": "30R", "lat_threshold": 39.8962, "lon_threshold": -105.1013,
        "heading_deg": 300, "length_ft": 9000}]


def _ap():
    return Airport(icao="KBJC", iata="BJC", name="RMMA", city="Broomfield, CO",
                   country="US", lat=39.9088, lon=-105.1172, elevation_ft=5673, is_towered=True)


def _s(ts, agl, vr=0, on_ground=False):
    # Sits on the 30R threshold so _nearest_runway distance ~= 0.
    return {"icao24": "dep01", "callsign": "N9", "timestamp": ts,
            "lat": 39.8962, "lon": -105.1013,
            "geo_altitude_ft": _ap().elevation_ft + agl,
            "velocity_kt": 60, "vertical_rate_fpm": vr, "on_ground": on_ground,
            "emitter_category": "A1"}


def _departure_track():
    # Starts on the ground and climbs out — no prior high-altitude approach.
    return [_s(1000, 0, vr=0, on_ground=True), _s(1030, 15, vr=600),
            _s(1060, 120, vr=800), _s(1120, 400, vr=700), _s(1200, 800, vr=500)]


def _touch_and_go_track():
    # Approaches from pattern altitude (>=500 AGL), touches, climbs out.
    return [_s(900, 900, vr=-400), _s(960, 500, vr=-500), _s(1000, 20, vr=0),
            _s(1060, 160, vr=700), _s(1120, 500, vr=600), _s(1200, 900, vr=400)]


def test_departure_is_takeoff():
    events = detect_takeoffs_over_period(_departure_track(), _ap(), RWY, 1000, 1200)
    assert [e["type"] for e in events] == ["takeoff"]
    assert events[0]["emitter_category"] == "A1"


def test_touch_and_go_is_not_a_takeoff():
    takeoffs = detect_takeoffs_over_period(_touch_and_go_track(), _ap(), RWY, 900, 1200)
    assert takeoffs == []
    tgs = detect_touch_and_gos_over_period(_touch_and_go_track(), _ap(), RWY, 900, 1200)
    assert "touch_and_go" in [e["type"] for e in tgs]


def test_departure_is_not_a_touch_and_go():
    tgs = detect_touch_and_gos_over_period(_departure_track(), _ap(), RWY, 1000, 1200)
    assert tgs == []  # no approach from altitude -> not a T&G


def test_event_counts_includes_takeoffs():
    counts = event_counts([{"type": "takeoff"}, {"type": "landing"}, {"type": "takeoff"}])
    assert counts["takeoffs"] == 2
    assert counts["landings"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_takeoffs.py -v`
Expected: FAIL (`detect_takeoffs_over_period` undefined; `event_counts` has no `takeoffs`).

- [ ] **Step 3: Add the approach guard to touch-and-go / low-approach**

In `detect_touch_and_gos_over_period` (L667-682), require an approach-from-altitude so departures don't match. Change the loop body:

```python
    for ep in episodes:
        lowest_sample = ep["lowest_sample"]
        lowest_agl = ep["lowest_agl"]
        speed = ep["speed"]
        lt = int(lowest_sample["timestamp"])
        after = _after_samples(recent, lowest_sample, CLIMB_OUT_LOOKAHEAD_SECONDS)
        if not after:
            continue
        on_ground_seconds = sum(1 for sample in after if sample.get("on_ground")) * 30
        speed_ok = speed is None or speed <= 90
        if not (lowest_agl <= 200 and speed_ok and _climbed_out(after, lowest_agl, airport)
                and on_ground_seconds <= 60):
            continue
        if not _approached_from_altitude(recent, lt, airport):
            continue  # no prior approach -> a departure (takeoff), handled elsewhere
        event_type = "touch_and_go" if lowest_agl <= 50 else "low_approach"
        events.append(_build_runway_event(event_type, ep, airport, runways))
    return events
```

- [ ] **Step 4: Implement `detect_takeoffs_over_period`**

Add after `detect_landings_over_period` (after L719):

```python
def detect_takeoffs_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    """A takeoff = reached the runway low and climbed out, with NO prior approach
    from altitude — i.e. the aircraft originated at the field and departed.

    The departure complement of touch-and-go over the same runway-low episodes:
    both climb out, but a touch-and-go descended in from >=500 ft AGL first while
    a takeoff started on/near the ground. Only the first low bucket of a presence
    counts, so a T&G's touchdown is never re-counted as a departure.
    """
    recent, episodes = _runway_low_episodes(track, airport, runways, start_ts, end_ts)
    events = []
    for ep in episodes:
        if not ep["is_first_low"]:
            continue
        lowest_sample = ep["lowest_sample"]
        lowest_agl = ep["lowest_agl"]
        lt = int(lowest_sample["timestamp"])
        if lowest_agl > 200:
            continue
        after = _after_samples(recent, lowest_sample, CLIMB_OUT_LOOKAHEAD_SECONDS)
        if not _climbed_out(after, lowest_agl, airport):
            continue  # never climbed out -> landing/other, not a departure
        if _approached_from_altitude(recent, lt, airport):
            continue  # descended in first -> touch-and-go/low-approach, not a takeoff
        events.append(_build_runway_event("takeoff", ep, airport, runways))
    return events
```

- [ ] **Step 5: Wire into `detect_events_over_period` and `event_counts`**

In `detect_events_over_period` (after the landings loop, L822-823), add:

```python
    for event in detect_landings_over_period(samples, airport, runways, start_ts, end_ts):
        events_by_id[event["id"]] = event
    for event in detect_takeoffs_over_period(samples, airport, runways, start_ts, end_ts):
        events_by_id[event["id"]] = event
```

In `event_counts` (L833-839), add the takeoffs key:

```python
    return {
        "circles": counts["circle"],
        "touch_and_gos": counts["touch_and_go"],
        "low_approaches": counts["low_approach"],
        "landings": counts["landing"],
        "takeoffs": counts["takeoff"],
        "passes": counts["pass_over_user"],
    }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_takeoffs.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run detector + worker + operations suites (guard the T&G change)**

Run: `cd backend && python -m pytest tests/test_operations.py tests/test_worker_landings.py tests/test_backfill_landings.py -q`
Expected: PASS. If a pre-existing T&G test used a track with no modeled approach, update that fixture to include a descent-from-altitude segment (the T&G definition now requires it) — do not weaken the new guard.

- [ ] **Step 8: Commit**

```bash
git add backend/app/detectors.py backend/tests/test_takeoffs.py
git commit -m "feat(detectors): detect takeoffs; require approach for touch-and-go"
```

---

### Task 5: Airport-local time helpers

**Files:**
- Modify: `backend/app/db.py` (add helpers near the other query functions)
- Test: `backend/tests/test_timezone.py` (extend)

**Interfaces:**
- Produces:
  - `airport_timezone(conn, icao) -> str | None`
  - `local_hour(ts: int, tz: str | None) -> int` (0-23; UTC when `tz` is None)
  - `local_day_key(ts: int, tz: str | None) -> str` (`"YYYY-MM-DD"`)
  - `local_month_key(ts: int, tz: str | None) -> str` (`"YYYY-MM"`)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_timezone.py`:

```python
from app.db import local_hour, local_day_key, local_month_key


def test_local_hour_denver_dst():
    # 2026-07-01 02:00:00 UTC = 2026-06-30 20:00 MDT (UTC-6 in summer).
    ts = 1782007200  # 2026-07-01T02:00:00Z
    assert local_hour(ts, "America/Denver") == 20
    assert local_day_key(ts, "America/Denver") == "2026-06-30"
    assert local_month_key(ts, "America/Denver") == "2026-06"


def test_local_hour_utc_fallback():
    ts = 1782007200
    assert local_hour(ts, None) == 2
    assert local_day_key(ts, None) == "2026-07-01"
```

Note: if `1782007200` does not correspond to `2026-07-01T02:00:00Z` on your machine, recompute with `python -c "import datetime,calendar;print(calendar.timegm((2026,7,1,2,0,0,0,0,0)))"` and update the literal + expectations.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_timezone.py -k local -v`
Expected: FAIL (helpers undefined).

- [ ] **Step 3: Implement the helpers**

At the top of `db.py`, extend imports:

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
```

Add near the other read helpers (e.g. after `read_operations`, ~L512):

```python
def airport_timezone(conn: sqlite3.Connection, icao: str) -> str | None:
    row = conn.execute("SELECT timezone FROM airports WHERE icao=?", (icao.upper(),)).fetchone()
    return row["timezone"] if row else None


def _local_dt(ts: int, tz: str | None) -> datetime:
    utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    if not tz:
        return utc
    try:
        return utc.astimezone(ZoneInfo(tz))
    except Exception:  # noqa: BLE001 — unknown tz name -> fall back to UTC
        return utc


def local_hour(ts: int, tz: str | None) -> int:
    return _local_dt(ts, tz).hour


def local_day_key(ts: int, tz: str | None) -> str:
    return _local_dt(ts, tz).strftime("%Y-%m-%d")


def local_month_key(ts: int, tz: str | None) -> str:
    return _local_dt(ts, tz).strftime("%Y-%m")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_timezone.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_timezone.py
git commit -m "feat(db): airport-local hour/day/month helpers via zoneinfo"
```

---

### Task 6: `airport_operations_trends()` rollup

**Files:**
- Modify: `backend/app/db.py` (new function)
- Test: `backend/tests/test_operations_trends.py` (create)

**Interfaces:**
- Consumes: `read_operations` / raw SQL over `operations`, `airport_timezone`, `local_*` helpers, the `aircraft_registry` icao_hex join (pattern from `db.py:717-727`).
- Produces:
  ```python
  airport_operations_trends(conn, icao, now_ts, months=12, recent_days=10, top_types=8) -> {
    "timezone": str | None,
    "data_since": int | None,          # min(timestamp) of ops for this airport
    "recent_days": [{"date": "YYYY-MM-DD", "operations": int, "pct_tg": float, "pct_light": float}],
    "monthly": [{"month": "YYYY-MM", "landings": int, "takeoffs": int, "tg": int,
                 "total": int, "pct_tg": float,
                 "by_type": {model: int, "Other": int}, "by_emitter": {code: int, "Other": int}}],
    "time_of_day": [{"hour": 0..23, "operations": int}],   # 24 entries, always present
  }
  ```
  `operations`/`total` count only `landing|takeoff|touch_and_go`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_operations_trends.py`:

```python
from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", emitter=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO", "emitter_category": emitter,
    }))


def test_operations_trends_counts_and_pct(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 2026-06 (June): 2 landings, 1 takeoff, 1 touch_and_go, plus a circle that must be ignored.
    base = 1780000000  # somewhere in June 2026
    _op(conn, "l1", "landing", base + 10, emitter="A1")
    _op(conn, "l2", "landing", base + 20, emitter="A2")
    _op(conn, "t1", "takeoff", base + 30, emitter="A1")
    _op(conn, "g1", "touch_and_go", base + 40, emitter="A1")
    _op(conn, "c1", "circle", base + 50, emitter="A1")  # excluded from operations
    conn.commit()

    trends = db.airport_operations_trends(conn, "KLMO", now_ts=base + 100, months=12)

    assert trends["timezone"] == "America/Denver"
    month = next(m for m in trends["monthly"] if m["total"] > 0)
    assert month["landings"] == 2
    assert month["takeoffs"] == 1
    assert month["tg"] == 1
    assert month["total"] == 4  # circle excluded
    assert month["pct_tg"] == 25.0
    assert month["by_emitter"]["A1"] == 3
    assert month["by_emitter"]["A2"] == 1
    assert len(trends["time_of_day"]) == 24
    assert sum(h["operations"] for h in trends["time_of_day"]) == 4


def test_recent_days_pct_light(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    _op(conn, "l1", "landing", base + 10, emitter="A1")
    _op(conn, "l2", "landing", base + 20, emitter="A6")
    conn.commit()
    trends = db.airport_operations_trends(conn, "KLMO", now_ts=base + 100, months=12)
    day = next(d for d in trends["recent_days"] if d["operations"] > 0)
    assert day["operations"] == 2
    assert day["pct_light"] == 50.0  # one of two ops is A1 (Light)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_operations_trends.py -v`
Expected: FAIL (`airport_operations_trends` undefined).

- [ ] **Step 3: Implement `airport_operations_trends`**

Add to `db.py` (after `airport_stats`). This pulls the ops once and buckets in Python using the local-time helpers (row counts here are small — one airport, ≤12 months):

```python
_OPERATION_TYPES = ("landing", "takeoff", "touch_and_go")
_LIGHT_EMITTER = "A1"


def airport_operations_trends(
    conn: sqlite3.Connection,
    icao: str,
    now_ts: int,
    months: int = 12,
    recent_days: int = 10,
    top_types: int = 8,
) -> dict:
    icao = icao.upper()
    tz = airport_timezone(conn, icao)
    start_ts = now_ts - months * 31 * 86400  # generous lower bound; we key by local month

    rows = conn.execute(
        "SELECT o.timestamp AS ts, o.type AS type, o.icao24 AS icao24, "
        "       o.emitter_category AS emitter, reg.model AS model "
        "FROM operations o "
        "LEFT JOIN aircraft_registry reg ON reg.icao_hex = upper(o.icao24) "
        "WHERE o.icao=? AND o.type IN (?,?,?) AND o.timestamp BETWEEN ? AND ? "
        "ORDER BY o.timestamp ASC",
        (icao, *_OPERATION_TYPES, start_ts, now_ts),
    ).fetchall()

    data_since = conn.execute(
        "SELECT MIN(timestamp) AS t FROM operations WHERE icao=? AND type IN (?,?,?)",
        (icao, *_OPERATION_TYPES),
    ).fetchone()["t"]

    # --- monthly buckets ---
    monthly: dict[str, dict] = {}
    for r in rows:
        key = local_month_key(r["ts"], tz)
        m = monthly.setdefault(key, {
            "month": key, "landings": 0, "takeoffs": 0, "tg": 0, "total": 0,
            "_types": defaultdict(int), "by_emitter": defaultdict(int),
        })
        if r["type"] == "landing":
            m["landings"] += 1
        elif r["type"] == "takeoff":
            m["takeoffs"] += 1
        else:
            m["tg"] += 1
        m["total"] += 1
        m["_types"][r["model"] or "Unknown"] += 1
        m["by_emitter"][_emitter_bucket(r["emitter"])] += 1

    # Global top-N aircraft types across the window; everything else -> "Other".
    type_totals: dict[str, int] = defaultdict(int)
    for m in monthly.values():
        for model, n in m["_types"].items():
            type_totals[model] += n
    top = {t for t, _ in sorted(type_totals.items(), key=lambda kv: kv[1], reverse=True)[:top_types]}

    monthly_out = []
    for key in sorted(monthly.keys()):
        m = monthly[key]
        by_type: dict[str, int] = defaultdict(int)
        for model, n in m["_types"].items():
            by_type[model if model in top else "Other"] += n
        monthly_out.append({
            "month": m["month"], "landings": m["landings"], "takeoffs": m["takeoffs"],
            "tg": m["tg"], "total": m["total"],
            "pct_tg": round(100.0 * m["tg"] / m["total"], 2) if m["total"] else 0.0,
            "by_type": dict(by_type), "by_emitter": dict(m["by_emitter"]),
        })

    # --- recent N local days ---
    day_agg: dict[str, dict] = {}
    for r in rows:
        key = local_day_key(r["ts"], tz)
        d = day_agg.setdefault(key, {"date": key, "operations": 0, "tg": 0, "light": 0})
        d["operations"] += 1
        if r["type"] == "touch_and_go":
            d["tg"] += 1
        if r["emitter"] == _LIGHT_EMITTER:
            d["light"] += 1
    recent_out = []
    for key in sorted(day_agg.keys(), reverse=True)[:recent_days]:
        d = day_agg[key]
        n = d["operations"]
        recent_out.append({
            "date": d["date"], "operations": n,
            "pct_tg": round(100.0 * d["tg"] / n, 2) if n else 0.0,
            "pct_light": round(100.0 * d["light"] / n, 2) if n else 0.0,
        })
    recent_out.reverse()  # oldest -> newest for display

    # --- time of day (local hour, full window) ---
    hours = [0] * 24
    for r in rows:
        hours[local_hour(r["ts"], tz)] += 1
    time_of_day = [{"hour": h, "operations": hours[h]} for h in range(24)]

    return {
        "timezone": tz,
        "data_since": data_since,
        "recent_days": recent_out,
        "monthly": monthly_out,
        "time_of_day": time_of_day,
    }


def _emitter_bucket(code: str | None) -> str:
    # Buckets shown to users; everything else collapses to "Other".
    return code if code in {"A1", "A2", "A6", "B1", "B4"} else "Other"
```

Ensure `from collections import defaultdict` is imported at the top of `db.py` (add if absent).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_operations_trends.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_operations_trends.py
git commit -m "feat(db): airport_operations_trends monthly/day/hour rollup"
```

---

### Task 7: Endpoint + API client

**Files:**
- Modify: `backend/app/main.py` (new route after `get_airport_stats` L1361)
- Modify: `frontend/src/lib/api.ts` (type + fetcher after `getAirportStats` L864)
- Test: `backend/tests/test_api.py` (extend)

**Interfaces:**
- Consumes: `db.airport_operations_trends`.
- Produces: `GET /airports/{icao}/operations-trends` → `{airport_icao, ...trends}`; `getOperationsTrends(icao)` → `OperationsTrendsResponse`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py` (follow the existing TestClient + seeded-DB pattern already used there for `/airports/{icao}/stats`; mirror that test's setup for the DB path and app fixture):

```python
def test_operations_trends_endpoint(client, seeded_db_path):
    # Insert two ops via the same helper the other api tests use, then hit the route.
    import app.db as db
    conn = db.connect(seeded_db_path)
    for oid, typ in (("l1", "landing"), ("g1", "touch_and_go")):
        db.upsert_operation(conn, db.operation_from_event({
            "id": oid, "type": typ, "icao24": "a1", "callsign": "A1",
            "timestamp": 1780000000, "airport_icao": "KLMO", "emitter_category": "A1",
        }))
    conn.commit()
    conn.close()

    resp = client.get("/airports/KLMO/operations-trends")
    assert resp.status_code == 200
    body = resp.json()
    assert body["airport_icao"] == "KLMO"
    assert body["timezone"] == "America/Denver"
    assert len(body["time_of_day"]) == 24
    assert any(m["total"] == 2 for m in body["monthly"])
```

Adapt `client` / `seeded_db_path` to the fixtures already present in `test_api.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_api.py -k operations_trends -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Add the route**

In `main.py`, after `get_airport_stats` (L1361):

```python
@app.get("/airports/{icao}/operations-trends")
async def get_airport_operations_trends(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        trends = db.airport_operations_trends(conn, icao, now_ts=now, months=12)
    return {"airport_icao": icao.upper(), **trends}
```

- [ ] **Step 4: Add the API client type + fetcher**

In `frontend/src/lib/api.ts`, after `getAirportStats` (L864):

```typescript
export interface OperationsTrendsResponse {
  airport_icao: string;
  timezone: string | null;
  data_since: number | null;
  recent_days: { date: string; operations: number; pct_tg: number; pct_light: number }[];
  monthly: {
    month: string; landings: number; takeoffs: number; tg: number; total: number;
    pct_tg: number; by_type: Record<string, number>; by_emitter: Record<string, number>;
  }[];
  time_of_day: { hour: number; operations: number }[];
}

export function getOperationsTrends(icao: string) {
  return getJson<OperationsTrendsResponse>(`/airports/${encodeURIComponent(icao)}/operations-trends`);
}
```

- [ ] **Step 5: Run test + typecheck**

Run: `cd backend && python -m pytest tests/test_api.py -k operations_trends -v`
Expected: PASS.
Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py frontend/src/lib/api.ts backend/tests/test_api.py
git commit -m "feat(api): operations-trends endpoint + client"
```

---

### Task 8: `OperationsTrends.tsx` component

**Files:**
- Create: `frontend/src/components/OperationsTrends.tsx`
- Modify: `frontend/src/lib/api.ts` (already has the fetcher from Task 7)

**Interfaces:**
- Consumes: `getOperationsTrends`, `OperationsTrendsResponse`.
- Produces: default-exported `<OperationsTrends icao={string} />`.

- [ ] **Step 1: Create the component**

Create `frontend/src/components/OperationsTrends.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { getOperationsTrends, type OperationsTrendsResponse } from "../lib/api";

// Categorical palette (extends the stats-page navy/red).
const SERIES = ["#1b3a6b", "#b3231f", "#2e8b57", "#c77d00", "#6a4c93", "#0f766e", "#9d174d", "#374151"];
const EMITTER_LABELS: Record<string, string> = {
  A1: "Light (A1)", A2: "Small (A2)", A6: "High Perf (A6)",
  B1: "Glider (B1)", B4: "Paraglider (B4)", Other: "Other",
};

function monthLabel(m: string): string {
  const [y, mo] = m.split("-");
  return new Date(Number(y), Number(mo) - 1, 1).toLocaleString([], { month: "short", year: "2-digit" });
}

// Union of all keys present across monthly rows, so stacked areas render every series.
function keysAcross(rows: OperationsTrendsResponse["monthly"], pick: (r: OperationsTrendsResponse["monthly"][number]) => Record<string, number>): string[] {
  const s = new Set<string>();
  rows.forEach((r) => Object.keys(pick(r)).forEach((k) => s.add(k)));
  return [...s];
}

export default function OperationsTrends({ icao }: { icao: string }) {
  const [data, setData] = useState<OperationsTrendsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    getOperationsTrends(icao)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load trends."));
  }, [icao]);

  if (error) return <div className="stats-error">{error}</div>;
  if (!data) return <div className="stats-loading">Loading trends…</div>;

  const months = data.monthly.map((m) => ({ ...m, label: monthLabel(m.month) }));
  const emitterKeys = keysAcross(data.monthly, (r) => r.by_emitter);
  const typeKeys = keysAcross(data.monthly, (r) => r.by_type);
  const since = data.data_since ? new Date(data.data_since * 1000).toLocaleDateString() : null;

  // Percent-normalized rows for the stacked-area charts.
  const pct = (rows: OperationsTrendsResponse["monthly"], pick: (r: OperationsTrendsResponse["monthly"][number]) => Record<string, number>, keys: string[]) =>
    rows.map((r) => {
      const src = pick(r);
      const total = keys.reduce((a, k) => a + (src[k] || 0), 0) || 1;
      const out: Record<string, number | string> = { label: monthLabel(r.month) };
      keys.forEach((k) => (out[k] = Math.round((100 * (src[k] || 0)) / total)));
      return out;
    });

  return (
    <>
      <section className="stats-card">
        <h2>Operations trends</h2>
        <p className="stats-besteffort">
          An operation is a landing, takeoff, or touch-and-go. Circles and passes are counted
          separately above and are not included here.{since ? ` Data since ${since}.` : ""}
        </p>
      </section>

      <section className="stats-card">
        <h3>Recent operations</h3>
        {data.recent_days.length === 0 ? <p className="stats-empty">No operations yet.</p> : (
          <table className="stats-table">
            <thead><tr><th>Date</th><th># Operations</th><th>% T&amp;G</th><th>% Light (A1)</th></tr></thead>
            <tbody>
              {data.recent_days.map((d) => (
                <tr key={d.date}>
                  <td>{d.date}</td><td>{d.operations}</td>
                  <td>{d.pct_tg}%</td><td>{d.pct_light}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="stats-card">
        <h3>Landings, takeoffs &amp; touch-and-gos</h3>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={months}>
            <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
            <YAxis allowDecimals={false} fontSize={11} />
            <Tooltip /><Legend />
            <Line dataKey="landings" name="Landings" stroke={SERIES[0]} dot={false} />
            <Line dataKey="takeoffs" name="Takeoffs" stroke={SERIES[1]} dot={false} />
            <Line dataKey="tg" name="Touch & go" stroke={SERIES[2]} dot={false} />
            <Line dataKey="total" name="Total" stroke={SERIES[5]} dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section className="stats-card">
        <h3>% of operations that were touch-and-go</h3>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={months}>
            <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
            <YAxis domain={[0, 100]} fontSize={11} />
            <Tooltip />
            <Line dataKey="pct_tg" name="% T&G" stroke={SERIES[0]} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section className="stats-card">
        <h3>% of operations by aircraft type</h3>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={pct(data.monthly, (r) => r.by_type, typeKeys)}>
            <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
            <YAxis domain={[0, 100]} fontSize={11} />
            <Tooltip /><Legend />
            {typeKeys.map((k, i) => (
              <Area key={k} dataKey={k} name={k} stackId="t" stroke={SERIES[i % SERIES.length]} fill={SERIES[i % SERIES.length]} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </section>

      <section className="stats-card">
        <h3>% of operations by ADS-B emitter category</h3>
        {emitterKeys.length === 0 ? (
          <p className="stats-empty">Collecting emitter data{since ? ` since ${since}` : ""}…</p>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={pct(data.monthly, (r) => r.by_emitter, emitterKeys)}>
              <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
              <YAxis domain={[0, 100]} fontSize={11} />
              <Tooltip /><Legend />
              {emitterKeys.map((k, i) => (
                <Area key={k} dataKey={k} name={EMITTER_LABELS[k] || k} stackId="e" stroke={SERIES[i % SERIES.length]} fill={SERIES[i % SERIES.length]} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </section>

      <section className="stats-card">
        <h3>Operations by time of day{data.timezone ? ` (${data.timezone})` : ""}</h3>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={data.time_of_day}>
            <XAxis dataKey="hour" fontSize={11} />
            <YAxis allowDecimals={false} fontSize={11} />
            <Tooltip />
            <Bar dataKey="operations" fill={SERIES[0]} />
          </BarChart>
        </ResponsiveContainer>
      </section>
    </>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/OperationsTrends.tsx
git commit -m "feat(ui): OperationsTrends charts component"
```

---

### Task 9: Render the section on the stats page

**Files:**
- Modify: `frontend/src/components/StatsPage.tsx` (import + render)
- Modify: `frontend/src/styles.css` (optional `h3` spacing inside cards)

**Interfaces:**
- Consumes: `<OperationsTrends icao={icao} />`.

- [ ] **Step 1: Import and render**

In `StatsPage.tsx`, add the import (after L3):

```tsx
import OperationsTrends from "./OperationsTrends";
```

Render it as the first block inside the `{data && ( <> ... )}` fragment, right after the opening `<>` (before `<section className="stats-tiles">`, L43) so it leads the page like Superior's layout:

```tsx
      {data && (
        <>
          <OperationsTrends icao={icao} />
          <section className="stats-tiles">
```

- [ ] **Step 2: Add minimal styles (optional)**

In `frontend/src/styles.css`, add (if `h3` inside `.stats-card` isn't already styled):

```css
.stats-card h3 { margin: 0 0 0.5rem; font-size: 1rem; }
```

- [ ] **Step 3: Typecheck + build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: clean build.

- [ ] **Step 4: Manual verification**

Run the backend + frontend dev servers (per repo README), open `/stats?airport=KLMO`, and confirm: the "Operations trends" section renders at the top, the recent-operations table lists local dates, the four line/area charts render, the emitter chart shows the "collecting…" empty state (until data accrues), and the time-of-day bar chart shows 24 bars labeled by local hour.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/StatsPage.tsx frontend/src/styles.css
git commit -m "feat(ui): mount OperationsTrends section on the stats page"
```

---

## Self-Review

**Spec coverage:**
- Visual #1 recent-ops table → Task 6 `recent_days` + Task 8 table. ✓
- #2 landings/takeoffs/T&G/total → Task 4 (takeoffs) + Task 6 `monthly` + Task 8 line chart. ✓
- #3 % T&G → Task 6 `pct_tg` + Task 8 line. ✓
- #4 % by aircraft type → Task 6 `by_type` (registry join, top-N+Other) + Task 8 stacked area. ✓
- #5 % by emitter → Tasks 1-3 (capture+snapshot) + Task 6 `by_emitter` + Task 8 stacked area + empty state. ✓
- #6 time of day (airport-local) → Tasks 1/5 (tz) + Task 6 `time_of_day` + Task 8 bar. ✓
- Operations = landing+takeoff+tg only → enforced in Task 6 `_OPERATION_TYPES`. ✓
- Forward-only emitter, additive migrations, tz column → Tasks 1-3. ✓

**Deviation from spec (intentional):** the spec listed an `aircraft_cache.emitter_category` column; the plan drops it because prod has no live `aircraft_cache` write path, and threading emitter through the sample→event→operation flow (Redis preserves the JSON sample; `track_archive` gets one column) is the reliable snapshot mechanism. Operations get emitter directly, which is all the charts need.

**Placeholder scan:** no TBD/TODO; every code step shows full code. The one runtime value (`data_since` date) is computed, not a placeholder. ✓

**Type consistency:** `emitter_category` key name is identical across parser, `_build_runway_event`, `operation_from_event`, `upsert_operation`, and archive round-trip. `airport_operations_trends` return keys match the `OperationsTrendsResponse` TS interface (`recent_days`/`monthly`/`time_of_day`/`timezone`/`data_since`; monthly `landings/takeoffs/tg/total/pct_tg/by_type/by_emitter`). `detect_takeoffs_over_period` signature matches its call in `detect_events_over_period`. ✓
