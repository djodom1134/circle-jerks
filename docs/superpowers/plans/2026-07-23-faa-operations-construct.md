# FAA Operation Construct Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `/v1` API a first-class **FAA operation** count — a takeoff or landing weighting derived at read time — exposed both per record and as a hot+cold-merged window summary.

**Architecture:** One small pure module (`faa_operations.py`) owns the type→weight mapping and the matching SQL fragments, so the Python (per-record) and SQL (aggregate) paths cannot drift. `OperationOut` gains a derived `faa_operation_count` field. A new `db.faa_operations_summary()` sums the weights across the same time-disjoint hot/cold seam `read_operations_page` uses, surfaced at `GET /v1/operations/summary`. No stored column, no migration, no backfill/front-fill job — the count is a pure function of data already on every row.

**Tech Stack:** Python 3, FastAPI, Pydantic, SQLite (`ATTACH`-based hot/cold merge), pytest. Docs are a hand-written `docs/api/openapi.yaml` compiled to JSON by `scripts/build_openapi_json.py`.

## Global Constraints

- **Never edit prod code or prod data.** This ships as ordinary branch code on `feat/public-api-keys`, verified locally only. Deploy is a separate, later step.
- **Additive only.** Do NOT change `airport_operations_trends` or `airport_stats` numbers/semantics.
- **Derived at read time.** No new DB column, no migration, no backfill job, no front-fill job. The count comes from `type` + `min_altitude_ft_agl`, both already stored hot and cold.
- **Single source of truth for the weighting.** The Python function and the SQL fragments both derive from the constants in `faa_operations.py`; a test asserts they agree.
- **Weighting:** `takeoff`=1, `landing`=1, `touch_and_go`=2, `low_approach`=2 iff `min_altitude_ft_agl IS NOT NULL AND ≤ LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT (25)` else 0, `circle`=0, `pass_over_user`=0, any other type=0.
- **Scope:** the summary endpoint uses `ops:read` (it lives under `/operations`, mirrors its filter params, and any key that can page raw ops can already count them).
- **Run tests with:** `cd backend && .venv/bin/python -m pytest` (the venv interpreter — the repo has no CI). Baseline before this work: `839 passed`.
- **Docs pipeline:** after editing `docs/api/openapi.yaml`, regenerate the committed JSON with `backend/.venv/bin/python scripts/build_openapi_json.py` and commit both JSON files, or the `test_openapi_drift_gate.py` tests fail.

---

### Task 1: The weighting module (`faa_operations.py`)

The pure core: constants, the Python weighting functions, and the SQL fragments built from the same constants. No I/O, no imports from `app`.

**Files:**
- Create: `backend/app/faa_operations.py`
- Test: `backend/tests/test_faa_operations.py`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces:
  - `LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT: int` (= 25)
  - `is_low_approach_touchdown(min_altitude_ft_agl: int | None) -> bool`
  - `faa_arrivals(op_type: str, min_altitude_ft_agl: int | None) -> int`
  - `faa_departures(op_type: str, min_altitude_ft_agl: int | None) -> int`
  - `faa_operation_count(op_type: str, min_altitude_ft_agl: int | None) -> int`
  - SQL fragment strings (reference bare columns `type`, `min_altitude_ft_agl`): `FAA_OPS_SUM_SQL`, `FAA_ARRIVALS_SUM_SQL`, `FAA_DEPARTURES_SUM_SQL`, `LOW_APPROACH_TOUCHDOWN_COUNT_SQL`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_faa_operations.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from app import faa_operations as fo


@pytest.mark.parametrize(
    "op_type, agl, expected",
    [
        ("takeoff", None, 1),
        ("landing", 0, 1),
        ("touch_and_go", 900, 2),        # T&G is always 2, regardless of AGL
        ("low_approach", 20, 2),         # touchdown: <= 25 ft
        ("low_approach", 25, 2),         # boundary is inclusive
        ("low_approach", 26, 0),         # a real low pass / go-around
        ("low_approach", None, 0),       # missing AGL is never a touchdown
        ("circle", 10, 0),
        ("pass_over_user", None, 0),
        ("something_new", 0, 0),         # unknown type never counts
    ],
)
def test_faa_operation_count(op_type, agl, expected):
    assert fo.faa_operation_count(op_type, agl) == expected


@pytest.mark.parametrize(
    "op_type, agl, arr, dep",
    [
        ("landing", 0, 1, 0),
        ("takeoff", None, 0, 1),
        ("touch_and_go", 900, 1, 1),
        ("low_approach", 20, 1, 1),
        ("low_approach", 120, 0, 0),
        ("circle", 10, 0, 0),
    ],
)
def test_arrivals_and_departures_decompose_the_count(op_type, agl, arr, dep):
    assert fo.faa_arrivals(op_type, agl) == arr
    assert fo.faa_departures(op_type, agl) == dep
    # The invariant the summary relies on: arrivals + departures == the count.
    assert arr + dep == fo.faa_operation_count(op_type, agl)


def test_python_and_sql_agree():
    """The SQL CASE fragments must score a set of rows identically to the
    Python function — this is what stops the two paths from drifting."""
    samples = [
        ("takeoff", None), ("landing", 0), ("touch_and_go", 900),
        ("low_approach", 20), ("low_approach", 25), ("low_approach", 26),
        ("low_approach", None), ("circle", 10), ("pass_over_user", None),
    ]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE ops (type TEXT, min_altitude_ft_agl INTEGER)")
    conn.executemany("INSERT INTO ops (type, min_altitude_ft_agl) VALUES (?, ?)", samples)

    row = conn.execute(
        f"SELECT {fo.FAA_OPS_SUM_SQL} AS ops, "
        f"{fo.FAA_ARRIVALS_SUM_SQL} AS arr, "
        f"{fo.FAA_DEPARTURES_SUM_SQL} AS dep, "
        f"{fo.LOW_APPROACH_TOUCHDOWN_COUNT_SQL} AS la_td FROM ops"
    ).fetchone()

    assert row["ops"] == sum(fo.faa_operation_count(t, a) for t, a in samples)
    assert row["arr"] == sum(fo.faa_arrivals(t, a) for t, a in samples)
    assert row["dep"] == sum(fo.faa_departures(t, a) for t, a in samples)
    assert row["la_td"] == sum(
        1 for t, a in samples if t == "low_approach" and fo.is_low_approach_touchdown(a)
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_faa_operations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.faa_operations'`.

- [ ] **Step 3: Write the module**

Create `backend/app/faa_operations.py`:

```python
"""The FAA-operation weighting: the single source of truth mapping our detector
event types to the canonical FAA sense of an *operation* — one takeoff or one
landing.

    takeoff        -> 1   (one departure)
    landing        -> 1   (one arrival)
    touch_and_go   -> 2   (one arrival + one departure)
    low_approach   -> 2 if it was a real touchdown (min AGL <= threshold) else 0
    circle         -> 0   (a pattern lap, not a runway movement)
    pass_over_user -> 0   (not an airport movement)

A Python path (per record, in v1_schemas.operation_out) and a SQL path (the
aggregate in db.faa_operations_summary) both derive from the constants here, so
the two cannot drift. test_faa_operations.py asserts they agree.
"""

from __future__ import annotations

# From the KLMO audit: a low approach whose lowest point was <= this AGL is a
# real touchdown (a touch-and-go worth 2 operations); a higher low pass / go-
# around completes neither a takeoff nor a landing and is worth 0.
LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT = 25


def is_low_approach_touchdown(min_altitude_ft_agl: int | None) -> bool:
    return (
        min_altitude_ft_agl is not None
        and min_altitude_ft_agl <= LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT
    )


def faa_arrivals(op_type: str, min_altitude_ft_agl: int | None) -> int:
    if op_type in ("landing", "touch_and_go"):
        return 1
    if op_type == "low_approach":
        return 1 if is_low_approach_touchdown(min_altitude_ft_agl) else 0
    return 0


def faa_departures(op_type: str, min_altitude_ft_agl: int | None) -> int:
    if op_type in ("takeoff", "touch_and_go"):
        return 1
    if op_type == "low_approach":
        return 1 if is_low_approach_touchdown(min_altitude_ft_agl) else 0
    return 0


def faa_operation_count(op_type: str, min_altitude_ft_agl: int | None) -> int:
    """FAA operations a single detected event represents (0, 1, or 2)."""
    return (
        faa_arrivals(op_type, min_altitude_ft_agl)
        + faa_departures(op_type, min_altitude_ft_agl)
    )


# --- SQL fragments, built from the same constant -----------------------------
#
# Each references the bare columns `type` and `min_altitude_ft_agl`, so it drops
# into a SELECT over either `operations` or `hist.operations` unchanged. The
# threshold is an int, so interpolation is injection-safe.

_TOUCHDOWN_SQL = (
    "min_altitude_ft_agl IS NOT NULL "
    f"AND min_altitude_ft_agl <= {LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT}"
)

FAA_ARRIVALS_SUM_SQL = (
    "SUM(CASE "
    "WHEN type IN ('landing','touch_and_go') THEN 1 "
    f"WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 1 "
    "ELSE 0 END)"
)

FAA_DEPARTURES_SUM_SQL = (
    "SUM(CASE "
    "WHEN type IN ('takeoff','touch_and_go') THEN 1 "
    f"WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 1 "
    "ELSE 0 END)"
)

FAA_OPS_SUM_SQL = (
    "SUM(CASE "
    "WHEN type IN ('takeoff','landing') THEN 1 "
    "WHEN type='touch_and_go' THEN 2 "
    f"WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 2 "
    "ELSE 0 END)"
)

LOW_APPROACH_TOUCHDOWN_COUNT_SQL = (
    f"SUM(CASE WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 1 ELSE 0 END)"
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_faa_operations.py -v`
Expected: PASS (all parametrizations + the agreement test).

- [ ] **Step 5: Commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
git add backend/app/faa_operations.py backend/tests/test_faa_operations.py
git commit -m "feat(faa-ops): weighting module (type->FAA operation count) with Python/SQL parity"
```

---

### Task 2: Per-record `faa_operation_count` on `OperationOut`

Expose the derived weight on every `/v1/operations` row, and bring the OpenAPI doc into line (add the field, add the missing `takeoff` enum value, refresh the example, regenerate JSON).

**Files:**
- Modify: `backend/app/v1_schemas.py` (import; `OperationOut` field; `operation_out` builder)
- Modify: `backend/tests/test_public_api_operations.py:101` (add the field to the stable-field-set assertion)
- Modify: `docs/api/openapi.yaml` (the `Operation` schema + the `/v1/operations` `type` enum + example)
- Regenerate: `frontend/src/generated/openapi.json`, `backend/app/generated/openapi.json`
- Test: `backend/tests/test_public_api_contract.py` (new unit test on `operation_out`)

**Interfaces:**
- Consumes: `faa_operations.faa_operation_count` (Task 1).
- Produces: `OperationOut.faa_operation_count: int` on every `/v1/operations` row.

- [ ] **Step 1: Write the failing unit test**

Append to `backend/tests/test_public_api_contract.py` (it already imports `v1_schemas` and defines `op_row`):

```python
def test_faa_operation_count_is_derived_per_type():
    assert v1_schemas.operation_out(op_row(type="landing", min_altitude_ft_agl=0)).faa_operation_count == 1
    assert v1_schemas.operation_out(op_row(type="takeoff", min_altitude_ft_agl=0)).faa_operation_count == 1
    assert v1_schemas.operation_out(op_row(type="touch_and_go", min_altitude_ft_agl=900)).faa_operation_count == 2
    assert v1_schemas.operation_out(op_row(type="low_approach", min_altitude_ft_agl=15)).faa_operation_count == 2
    assert v1_schemas.operation_out(op_row(type="low_approach", min_altitude_ft_agl=120)).faa_operation_count == 0
    assert v1_schemas.operation_out(op_row(type="circle", min_altitude_ft_agl=10)).faa_operation_count == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py::test_faa_operation_count_is_derived_per_type -v`
Expected: FAIL — `AttributeError: 'OperationOut' object has no attribute 'faa_operation_count'`.

- [ ] **Step 3: Add the import to `v1_schemas.py`**

In `backend/app/v1_schemas.py`, change line 20:

```python
from . import api_keys
```
to:
```python
from . import api_keys, faa_operations
```

- [ ] **Step 4: Add the field to `OperationOut`**

In `backend/app/v1_schemas.py`, in `class OperationOut`, add the field immediately after `type: str` (line 77):

```python
    type: str
    faa_operation_count: int
```

- [ ] **Step 5: Set the field in `operation_out`**

In `backend/app/v1_schemas.py`, in `def operation_out`, add the argument immediately after `type=row["type"],` (line 111):

```python
        type=row["type"],
        faa_operation_count=faa_operations.faa_operation_count(
            row["type"], row["min_altitude_ft_agl"]
        ),
```

- [ ] **Step 6: Run the unit test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py::test_faa_operation_count_is_derived_per_type -v`
Expected: PASS.

- [ ] **Step 7: Update the stable-field-set assertion**

In `backend/tests/test_public_api_operations.py`, in `test_operations_returns_a_stable_field_set`, add `"faa_operation_count"` to the expected set (after `"type",` on line 102):

```python
            "id", "airport_icao", "icao24", "callsign", "registration", "type",
            "faa_operation_count",
            "timestamp_ts", "runway_id", "turn_direction", "min_altitude_ft_agl",
```

- [ ] **Step 8: Update the OpenAPI YAML**

In `docs/api/openapi.yaml`, in the `Operation` schema (around line 725):

Add `faa_operation_count` to `required` (line 728):
```yaml
      required: [id, airport_icao, type, timestamp_ts, faa_operation_count]
```

Add `takeoff` to the `type` enum (line 737) — it is a real, always-possible operation type the doc currently omits:
```yaml
          enum: [touch_and_go, circle, low_approach, landing, takeoff, pass_over_user]
```

Add the property immediately after the `type` block (after line 737):
```yaml
        faa_operation_count:
          type: integer
          enum: [0, 1, 2]
          description: |
            FAA operations this event represents: a takeoff or a landing is 1, a
            touch-and-go is 2, a low approach is 2 when it was a real touchdown
            (min_altitude_ft_agl <= 25) else 0, and circles / passes are 0.
            Derived from `type` and `min_altitude_ft_agl`; not a stored column.
```

In the `/v1/operations` `type` query parameter (line 206), add `takeoff` to that enum too:
```yaml
            enum: [touch_and_go, circle, low_approach, landing, takeoff, pass_over_user]
```

In the `/v1/operations` 200 response example (the `type: touch_and_go` row near line 248), add the field right after `type: touch_and_go`:
```yaml
                    type: touch_and_go
                    faa_operation_count: 2
```

- [ ] **Step 9: Regenerate the committed OpenAPI JSON**

Run: `cd /Users/d/Code/FAA_circle_jerk && backend/.venv/bin/python scripts/build_openapi_json.py`
Expected: prints `wrote frontend/src/generated/openapi.json` and `wrote backend/app/generated/openapi.json`.

- [ ] **Step 10: Run the affected tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_operations.py tests/test_public_api_contract.py tests/test_openapi_drift_gate.py -v`
Expected: PASS (field-set assertion updated; drift gate green because YAML and JSON now agree).

- [ ] **Step 11: Commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
git add backend/app/v1_schemas.py backend/tests/test_public_api_operations.py \
        backend/tests/test_public_api_contract.py docs/api/openapi.yaml \
        frontend/src/generated/openapi.json backend/app/generated/openapi.json
git commit -m "feat(faa-ops): expose faa_operation_count per /v1/operations row"
```

---

### Task 3: `db.faa_operations_summary()` — hot+cold weighted aggregate

The read function that sums the FAA weights over a window, merging the cold store on the exact same time-disjoint seam `read_operations_page` uses.

**Files:**
- Modify: `backend/app/db.py` (import at line 13; new function near `read_operations_page`, after line 742)
- Test: `backend/tests/test_faa_operations_summary.py`

**Interfaces:**
- Consumes: `faa_operations.FAA_OPS_SUM_SQL`, `FAA_ARRIVALS_SUM_SQL`, `FAA_DEPARTURES_SUM_SQL`, `LOW_APPROACH_TOUCHDOWN_COUNT_SQL` (Task 1); the same `history_path` / `hot_cutoff_ts` seam as `read_operations_page`.
- Produces:
  ```python
  db.faa_operations_summary(
      conn, *, icao: str, start_ts: int, end_ts: int,
      history_path: str | None = None, hot_cutoff_ts: int | None = None,
  ) -> dict  # keys: faa_operations, arrivals, departures,
             #       low_approach_touchdowns, by_event_type: dict[str, int]
  ```

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_faa_operations_summary.py`:

```python
from __future__ import annotations

from app import db


def _seed(conn, *, id, icao, ts, type, agl):
    conn.execute(
        "INSERT INTO operations (id, icao, icao24, type, timestamp, min_altitude_ft_agl) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id, icao, "a26f5e", type, ts, agl),
    )
    conn.commit()


def _cold_db(tmp_path):
    path = str(tmp_path / "cold.sqlite3")
    db.init_db(path)  # same schema as hot
    return path


def test_summary_weights_each_type(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        _seed(conn, id="t", icao="KLMO", ts=1000, type="takeoff", agl=None)
        _seed(conn, id="l", icao="KLMO", ts=1001, type="landing", agl=0)
        _seed(conn, id="tg", icao="KLMO", ts=1002, type="touch_and_go", agl=900)
        _seed(conn, id="la_td", icao="KLMO", ts=1003, type="low_approach", agl=20)   # touchdown -> 2
        _seed(conn, id="la_go", icao="KLMO", ts=1004, type="low_approach", agl=120)  # go-around -> 0
        _seed(conn, id="c", icao="KLMO", ts=1005, type="circle", agl=None)           # 0
        s = db.faa_operations_summary(conn, icao="KLMO", start_ts=0, end_ts=2000)

    # 1 + 1 + 2 + 2 + 0 + 0
    assert s["faa_operations"] == 6
    # arrivals: landing + tg + la_td ; departures: takeoff + tg + la_td
    assert s["arrivals"] == 3
    assert s["departures"] == 3
    assert s["arrivals"] + s["departures"] == s["faa_operations"]
    assert s["low_approach_touchdowns"] == 1
    assert s["by_event_type"] == {
        "takeoff": 1, "landing": 1, "touch_and_go": 1, "low_approach": 2, "circle": 1,
    }


def test_summary_is_empty_over_a_gap(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        s = db.faa_operations_summary(conn, icao="KLMO", start_ts=0, end_ts=2000)
    assert s["faa_operations"] == 0
    assert s["arrivals"] == 0 and s["departures"] == 0
    assert s["low_approach_touchdowns"] == 0
    assert s["by_event_type"] == {}


def test_summary_includes_the_cold_slice(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed(cconn, id="c1", icao="KLMO", ts=5_000, type="touch_and_go", agl=900)  # cold, 2 ops
    with db.connect(hot) as conn:
        _seed(conn, id="h1", icao="KLMO", ts=15_000, type="landing", agl=0)         # hot, 1 op
        s = db.faa_operations_summary(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert s["faa_operations"] == 3               # 2 (cold) + 1 (hot)
    assert s["by_event_type"] == {"touch_and_go": 1, "landing": 1}


def test_summary_does_not_double_count_at_the_seam(tmp_path):
    # The boundary row (ts == cutoff) belongs to hot; an archival-lag copy of it
    # in cold must not also be counted. Mirrors the read_operations_page seam.
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed(cconn, id="boundary", icao="KLMO", ts=10_000, type="landing", agl=0)  # lag copy
    with db.connect(hot) as conn:
        _seed(conn, id="boundary", icao="KLMO", ts=10_000, type="landing", agl=0)   # real copy
        s = db.faa_operations_summary(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert s["faa_operations"] == 1               # counted once, from hot
    assert s["by_event_type"] == {"landing": 1}


def test_summary_recent_window_never_touches_cold(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed(cconn, id="c1", icao="KLMO", ts=5_000, type="touch_and_go", agl=900)
    with db.connect(hot) as conn:
        _seed(conn, id="h1", icao="KLMO", ts=15_000, type="landing", agl=0)
        s = db.faa_operations_summary(
            conn, icao="KLMO", start_ts=12_000, end_ts=20_000,   # starts after cutoff
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert s["faa_operations"] == 1               # only the hot landing
    assert s["by_event_type"] == {"landing": 1}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_faa_operations_summary.py -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'faa_operations_summary'`.

- [ ] **Step 3: Add the import to `db.py`**

In `backend/app/db.py`, change line 13:

```python
from . import admin_users, api_keys
```
to:
```python
from . import admin_users, api_keys, faa_operations
```

- [ ] **Step 4: Write the function**

In `backend/app/db.py`, insert this immediately after `read_operations_page` ends (after line 742, before `def read_track_archive_page`):

```python
def faa_operations_summary(
    conn: sqlite3.Connection,
    *,
    icao: str,
    start_ts: int,
    end_ts: int,
    history_path: str | None = None,
    hot_cutoff_ts: int | None = None,
) -> dict:
    """Weighted FAA-operation totals for an airport over [start_ts, end_ts].

    Mirrors read_operations_page's hot/cold seam: when history_path is set and
    the window reaches older than hot_cutoff_ts, the older slice is summed from
    the cold store (timestamp < cutoff) and the recent slice from hot
    (timestamp >= cutoff). The two slices are time-disjoint, so summing their
    weighted counts never double-counts the boundary. With no history_path (or a
    fully-recent window) only the hot `operations` table is read.

    Returns keys: faa_operations, arrivals, departures, low_approach_touchdowns,
    and by_event_type (a {type: count} map over every observed type).
    """
    icao = icao.upper()

    def _slice(table: str, lo: int, hi: int) -> dict:
        agg = conn.execute(
            f"SELECT {faa_operations.FAA_OPS_SUM_SQL} AS faa_operations, "
            f"{faa_operations.FAA_ARRIVALS_SUM_SQL} AS arrivals, "
            f"{faa_operations.FAA_DEPARTURES_SUM_SQL} AS departures, "
            f"{faa_operations.LOW_APPROACH_TOUCHDOWN_COUNT_SQL} AS low_approach_touchdowns "
            f"FROM {table} WHERE icao = ? AND timestamp >= ? AND timestamp <= ?",
            (icao, int(lo), int(hi)),
        ).fetchone()
        by_type = {
            r["type"]: r["n"]
            for r in conn.execute(
                f"SELECT type, COUNT(*) AS n FROM {table} "
                "WHERE icao = ? AND timestamp >= ? AND timestamp <= ? GROUP BY type",
                (icao, int(lo), int(hi)),
            ).fetchall()
        }
        return {
            "faa_operations": agg["faa_operations"] or 0,   # SUM over 0 rows is NULL
            "arrivals": agg["arrivals"] or 0,
            "departures": agg["departures"] or 0,
            "low_approach_touchdowns": agg["low_approach_touchdowns"] or 0,
            "by_event_type": by_type,
        }

    def _merge(a: dict, b: dict) -> dict:
        by_type = dict(a["by_event_type"])
        for t, n in b["by_event_type"].items():
            by_type[t] = by_type.get(t, 0) + n
        return {
            "faa_operations": a["faa_operations"] + b["faa_operations"],
            "arrivals": a["arrivals"] + b["arrivals"],
            "departures": a["departures"] + b["departures"],
            "low_approach_touchdowns": a["low_approach_touchdowns"] + b["low_approach_touchdowns"],
            "by_event_type": by_type,
        }

    use_cold = (
        history_path is not None
        and hot_cutoff_ts is not None
        and int(start_ts) < int(hot_cutoff_ts)
    )
    if not use_cold:
        return _slice("operations", start_ts, end_ts)

    cutoff = int(hot_cutoff_ts)
    result = {
        "faa_operations": 0, "arrivals": 0, "departures": 0,
        "low_approach_touchdowns": 0, "by_event_type": {},
    }
    attached = False
    try:
        conn.execute("ATTACH ? AS hist", (history_path,))
        attached = True
        cold_hi = min(int(end_ts), cutoff - 1)
        if int(start_ts) <= cold_hi:  # older slice -> cold
            result = _merge(result, _slice("hist.operations", start_ts, cold_hi))
        if int(end_ts) >= cutoff:  # recent slice -> hot
            result = _merge(result, _slice("operations", max(int(start_ts), cutoff), end_ts))
    finally:
        if attached:
            conn.execute("DETACH hist")
    return result
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_faa_operations_summary.py -v`
Expected: PASS (all five tests, including the cold-slice and seam cases).

- [ ] **Step 6: Commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
git add backend/app/db.py backend/tests/test_faa_operations_summary.py
git commit -m "feat(faa-ops): db.faa_operations_summary hot+cold weighted aggregate"
```

---

### Task 4: `GET /v1/operations/summary` endpoint + schema

Wire the aggregate to a route, with the same auth/airport gate and cold-store gating as `/v1/operations`, and document it.

**Files:**
- Modify: `backend/app/v1_schemas.py` (`OperationsSummaryOut` model + `operations_summary_out` builder)
- Modify: `backend/app/public_api.py` (new route after `list_operations`, ends line 563)
- Modify: `docs/api/openapi.yaml` (new `/v1/operations/summary` path + `OperationsSummary` schema)
- Regenerate: `frontend/src/generated/openapi.json`, `backend/app/generated/openapi.json`
- Test: `backend/tests/test_public_api_operations_summary.py`

**Interfaces:**
- Consumes: `db.faa_operations_summary` (Task 3); `faa_operations.LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT` (Task 1); existing `_known_airport`, `resolve_range`, `require_scope`, `history_store.available/airport_allowed`, `settings_from_app`, `db_session`.
- Produces: `GET /v1/operations/summary?airport=&since=&until=` → `OperationsSummaryOut`.

- [ ] **Step 1: Write the failing endpoint tests**

Create `backend/tests/test_public_api_operations_summary.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.settings import get_settings
from test_public_api import auth, configure, mint

NOW = 1_700_000_000


def _seed(db_path, *, icao, id, ts, type, agl):
    # init_db seeds KBJC and KLMO into `airports`, so _known_airport resolves
    # them (same reliance as test_public_api_operations.py's seed_operations).
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO operations (id, icao, icao24, type, timestamp, min_altitude_ft_agl) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id, icao, "a26f5e", type, ts, agl),
        )
        conn.commit()


def test_summary_requires_the_ops_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations/summary", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_summary_enforces_the_airport_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations/summary", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_summary_404s_an_unknown_airport(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations/summary", params={"airport": "ZZZZ"}, headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_summary_totals_and_shape(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    _seed(db_path, icao="KBJC", id="t", ts=NOW, type="takeoff", agl=None)
    _seed(db_path, icao="KBJC", id="tg", ts=NOW + 1, type="touch_and_go", agl=900)
    _seed(db_path, icao="KBJC", id="la", ts=NOW + 2, type="low_approach", agl=20)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/operations/summary",
            params={"airport": "KBJC", "since": NOW - 10, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "airport_icao", "since_ts", "until_ts", "faa_operations",
            "arrivals", "departures", "by_event_type",
            "low_approach_touchdowns", "low_approach_touchdown_max_agl_ft",
        }
        assert body["airport_icao"] == "KBJC"
        assert body["faa_operations"] == 5           # 1 + 2 + 2
        assert body["arrivals"] + body["departures"] == body["faa_operations"]
        assert body["low_approach_touchdowns"] == 1
        assert body["low_approach_touchdown_max_agl_ft"] == 25
        assert body["by_event_type"] == {"takeoff": 1, "touch_and_go": 1, "low_approach": 1}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_operations_summary.py -v`
Expected: FAIL — the route 404s (not registered), so `test_summary_totals_and_shape` fails on `status_code == 200`.

- [ ] **Step 3: Add the schema + builder to `v1_schemas.py`**

In `backend/app/v1_schemas.py`, add immediately after `def operation_out` ends (after line 128, before the `_ALTITUDE_DATUM_BY_SOURCE` comment block):

```python
class OperationsSummaryOut(BaseModel):
    airport_icao: str
    since_ts: int
    until_ts: int
    faa_operations: int
    arrivals: int
    departures: int
    by_event_type: dict[str, int]
    low_approach_touchdowns: int
    low_approach_touchdown_max_agl_ft: int


def operations_summary_out(icao: str, since_ts: int, until_ts: int, summary: dict) -> OperationsSummaryOut:
    return OperationsSummaryOut(
        airport_icao=icao,
        since_ts=since_ts,
        until_ts=until_ts,
        faa_operations=summary["faa_operations"],
        arrivals=summary["arrivals"],
        departures=summary["departures"],
        by_event_type=summary["by_event_type"],
        low_approach_touchdowns=summary["low_approach_touchdowns"],
        low_approach_touchdown_max_agl_ft=faa_operations.LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT,
    )
```

(`faa_operations` is already imported from Task 2, Step 3.)

- [ ] **Step 4: Add the route to `public_api.py`**

In `backend/app/public_api.py`, insert immediately after `list_operations` ends (after line 563, before the `# Matches the scan ring…` comment on line 566):

```python
@router.get(
    "/operations/summary", summary="FAA operation totals for an airport",
    response_model=v1_schemas.OperationsSummaryOut,
)
async def operations_summary(
    ctx: Annotated[ApiKeyContext, Depends(require_scope("ops:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    airport: str,
    since: str | None = None,
    until: str | None = None,
) -> v1_schemas.OperationsSummaryOut:
    with db_session(settings.database_path) as conn:
        icao = _known_airport(conn, ctx, airport)
        start_ts, end_ts = resolve_range(since, until, now=int(time.time()))
        hist_path = (
            settings.history_database_path
            if history_store.available(settings) and history_store.airport_allowed(icao, settings)
            else None
        )
        summary = db.faa_operations_summary(
            conn,
            icao=icao,
            start_ts=start_ts,
            end_ts=end_ts,
            history_path=hist_path,
            hot_cutoff_ts=int(time.time()) - settings.track_archive_horizon_days * 86400,
        )
    return v1_schemas.operations_summary_out(icao, start_ts, end_ts, summary)
```

- [ ] **Step 5: Run the endpoint tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_operations_summary.py -v`
Expected: PASS (all four tests — scope 403, airport 403, unknown 404, totals/shape 200).

- [ ] **Step 6: Document the endpoint in the OpenAPI YAML**

In `docs/api/openapi.yaml`, add a new path immediately after the `/v1/operations` block ends (after its `"429"` response line 270, before `/v1/tracks:` line 272):

```yaml
  /v1/operations/summary:
    get:
      tags: [operations]
      summary: FAA operation totals for an airport
      description: |
        Window totals in the canonical FAA sense of an operation — one takeoff
        or one landing. A takeoff or landing counts 1, a touch-and-go counts 2,
        a low approach counts 2 when it was a real touchdown
        (min_altitude_ft_agl <= 25) else 0, and circles / passes count 0.
        `arrivals + departures == faa_operations`. Draws from the long-term
        (cold) store, so it covers the full retained history, not just the hot
        window. Requires the `ops:read` scope.
      operationId: operationsSummary
      parameters:
        - name: airport
          in: query
          required: true
          description: ICAO code, case-insensitive.
          schema: { type: string, example: KLMO }
        - name: since
          in: query
          description: Start of range, inclusive. Defaults to 7 days before `until`.
          schema: { type: string, example: "2025-07-21T00:00:00Z" }
        - name: until
          in: query
          description: End of range, inclusive. Defaults to now.
          schema: { type: string, example: "1784667715" }
      responses:
        "200":
          description: FAA operation totals for the window.
          headers:
            X-RateLimit-Limit: { $ref: "#/components/headers/RateLimitLimit" }
            X-RateLimit-Remaining: { $ref: "#/components/headers/RateLimitRemaining" }
          content:
            application/json:
              schema: { $ref: "#/components/schemas/OperationsSummary" }
              example:
                airport_icao: KLMO
                since_ts: 1721520000
                until_ts: 1753056000
                faa_operations: 142712
                arrivals: 70406
                departures: 72306
                by_event_type:
                  landing: 14005
                  takeoff: 15905
                  touch_and_go: 28591
                  low_approach: 30976
                  circle: 31000
                low_approach_touchdowns: 27810
                low_approach_touchdown_max_agl_ft: 25
        "400": { $ref: "#/components/responses/InvalidRequest" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "403": { $ref: "#/components/responses/Forbidden" }
        "404": { $ref: "#/components/responses/NotFound" }
        "429": { $ref: "#/components/responses/RateLimited" }
```

In `docs/api/openapi.yaml`, add the schema immediately after the `Operation` schema ends (after `flight_school` line 768, before `TrackSample:` line 770):

```yaml
    OperationsSummary:
      type: object
      description: FAA operation totals for a window. Derived, not stored.
      required:
        [airport_icao, since_ts, until_ts, faa_operations, arrivals, departures,
         by_event_type, low_approach_touchdowns, low_approach_touchdown_max_agl_ft]
      properties:
        airport_icao: { type: string }
        since_ts: { type: integer, description: "Unix seconds, inclusive." }
        until_ts: { type: integer, description: "Unix seconds, inclusive." }
        faa_operations:
          type: integer
          description: Total FAA operations (takeoffs + landings; a touch-and-go is 2).
        arrivals: { type: integer, description: Landings + touch-and-gos + low-approach touchdowns. }
        departures: { type: integer, description: Takeoffs + touch-and-gos + low-approach touchdowns. }
        by_event_type:
          type: object
          additionalProperties: { type: integer }
          description: Raw event count per detector type in the window (for transparency).
        low_approach_touchdowns:
          type: integer
          description: Low approaches counted as touchdowns (each worth 2 operations).
        low_approach_touchdown_max_agl_ft:
          type: integer
          description: The AGL threshold at or below which a low approach is a touchdown.
```

- [ ] **Step 7: Regenerate the committed OpenAPI JSON**

Run: `cd /Users/d/Code/FAA_circle_jerk && backend/.venv/bin/python scripts/build_openapi_json.py`
Expected: prints `wrote frontend/src/generated/openapi.json` and `wrote backend/app/generated/openapi.json`.

- [ ] **Step 8: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS — every prior test plus the new ones; the drift gate green (YAML and JSON agree). No failures.

- [ ] **Step 9: Commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
git add backend/app/public_api.py backend/app/v1_schemas.py \
        backend/tests/test_public_api_operations_summary.py docs/api/openapi.yaml \
        frontend/src/generated/openapi.json backend/app/generated/openapi.json
git commit -m "feat(faa-ops): GET /v1/operations/summary window totals (hot+cold)"
```

---

## Self-Review

**Spec coverage:**
- FAA weighting (single source of truth) → Task 1 (`faa_operations.py` + Python/SQL parity test). ✓
- Low-approach split by AGL ≤ 25 → Task 1 constant + fragments; tested at 25/26/None. ✓
- Per-record field → Task 2 (`OperationOut.faa_operation_count`, doc, field-set test). ✓
- Summary aggregate, hot+cold merged → Task 3 (`db.faa_operations_summary`) + Task 4 (route + schema). ✓
- Arrivals+departures == faa_operations invariant → asserted in Tasks 1, 3, 4. ✓
- by_event_type transparency + low_approach_touchdowns + threshold echo → Tasks 3/4. ✓
- No backfill/front-fill job; derived at read time → no such task exists, by design (Global Constraints). ✓
- trends/stats untouched → no task edits them. ✓
- No prod edits; local verification only → Global Constraints; every commit is branch-local. ✓
- Docs (openapi.yaml + JSON) → Tasks 2 and 4 update YAML and regenerate JSON; drift gate covers it. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every run step shows the command and expected result. The illustrative numbers in the Task 4 YAML example are an OpenAPI `example` (documentation), not an assertion. ✓

**Type consistency:** `faa_operation_count(op_type, min_altitude_ft_agl)` is spelled identically in Tasks 1, 2. `db.faa_operations_summary(...)` keys (`faa_operations`, `arrivals`, `departures`, `low_approach_touchdowns`, `by_event_type`) are produced in Task 3 and consumed unchanged in Task 4's `operations_summary_out`. The `OperationsSummaryOut` field set in Task 4 Step 3 matches the test assertion in Task 4 Step 1 and the YAML `OperationsSummary` `required` list in Step 6. ✓
