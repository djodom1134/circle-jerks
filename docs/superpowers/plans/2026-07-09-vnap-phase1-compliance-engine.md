# VNAP Phase 1 — Compliance Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute per-aircraft VNAP compliance sub-scores (0–100) and a composite score over a time window for one airport, plus the set averages, and expose it at `GET /airports/{icao}/vnap-compliance`.

**Architecture:** A new pure-logic module `backend/app/vnap.py` holds the per-airport `VnapRuleset` and the sub-score functions (no DB). A DB orchestrator `compute_aircraft_compliance` pulls `operations` (with deviation/turn/runway/wind), report counts, cowboy counts, and registry owner/model in batched queries, groups by aircraft, applies the pure functions, and computes set averages. A thin FastAPI route + typed client expose it. This is the data foundation for the Phase-3 dashboard table + radar and the Phase-4 sidebar.

**Tech Stack:** Python 3 (FastAPI, raw `sqlite3`), pytest; the response is consumed later by React/Recharts.

## Global Constraints

- Backend tests run from `/Users/d/Code/FAA_circle_jerk/backend` with `.venv/bin/python -m pytest` (bare `python` is NOT on PATH). Baseline is currently **226 passing** — do not regress.
- **VNAP score is 0–100 where 100 = fully compliant.** Every sub-score follows this: higher = better behaved.
- **Composite = equal-weight mean of the sub-scores that have data**; an axis with no data for an aircraft is `None` and is EXCLUDED from the mean (not scored 0).
- The seven axes, in this exact order and spelling: `tightness, altitude, timeofday, tg_volume, circle_restraint, left_traffic, runway29`.
- "**Operation**" count = `landing + takeoff + touch_and_go` (consistent with the operations-trends feature); `circles` is a separate count.
- Reuse existing helpers: `db.airport_timezone`, `db.local_hour` (shipped in the operations-trends feature); `flow.headwind_component`. Do NOT reimplement them.
- Registry joins MUST use correlated `(SELECT ... LIMIT 1)` subqueries, NOT `LEFT JOIN` — a duplicate `aircraft_registry.icao_hex` row must never fan out / inflate an aircraft's op counts (lesson from the operations-trends registry fan-out bug).
- All money-numbers (thresholds) live in `VnapRuleset`, not inline literals.

## File Structure

- Create: `backend/app/vnap.py` — ruleset + pure sub-score functions + session grouping + the DB orchestrator.
- Modify: `backend/app/main.py` — new route after the operations-trends route.
- Modify: `frontend/src/lib/api.ts` — `VnapComplianceResponse` type + `getVnapCompliance`.
- Create: `backend/tests/test_vnap_scores.py` — pure-function unit tests.
- Create: `backend/tests/test_vnap_compliance.py` — orchestrator integration tests.
- Modify: `backend/tests/test_api.py` — endpoint test.

---

### Task 1: `vnap.py` — ruleset + pure sub-score functions

**Files:**
- Create: `backend/app/vnap.py`
- Test: `backend/tests/test_vnap_scores.py`

**Interfaces:**
- Produces:
  - `AXES: list[str]` — the 7 axis keys in order.
  - `VnapRuleset` dataclass (frozen) with the thresholds below; `ruleset_for(icao: str) -> VnapRuleset`.
  - Pure scorers, each returning `float | None` (None = no data), clamped [0,100], rounded 1 dp:
    `tightness_score(avg_dev_nm, rules)`, `altitude_score(typical_agl_ft, rules)`,
    `timeofday_score(in_window, total)`, `tg_volume_score(tg_per_session, rules)`,
    `circle_restraint_score(circles_per_session, rules)`, `left_traffic_score(left, known)`,
    `runway_pref_score(on_pref_when_favored, favored_total)`, `composite_score(scores: dict, rules)`.
  - `group_sessions(timestamps_sorted: list[int], gap_min: int) -> list[list[int]]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_vnap_scores.py`:

```python
from __future__ import annotations

from app import vnap
from app.vnap import ruleset_for


R = ruleset_for("KLMO")


def test_axes_order():
    assert vnap.AXES == ["tightness", "altitude", "timeofday", "tg_volume",
                         "circle_restraint", "left_traffic", "runway29"]


def test_tightness_score():
    assert vnap.tightness_score(0.0, R) == 100.0          # dead on pattern
    assert vnap.tightness_score(R.dev_floor_nm, R) == 0.0  # at the floor -> 0
    assert vnap.tightness_score(0.5, R) == 50.0            # halfway (floor 1.0)
    assert vnap.tightness_score(5.0, R) == 0.0             # clamped, not negative
    assert vnap.tightness_score(None, R) is None


def test_altitude_score():
    assert vnap.altitude_score(1000.0, R) == 100.0   # at/above target
    assert vnap.altitude_score(1500.0, R) == 100.0   # clamped
    assert vnap.altitude_score(0.0, R) == 0.0
    assert vnap.altitude_score(500.0, R) == 50.0
    assert vnap.altitude_score(None, R) is None


def test_timeofday_score():
    assert vnap.timeofday_score(8, 10) == 80.0
    assert vnap.timeofday_score(0, 0) is None


def test_left_traffic_score():
    assert vnap.left_traffic_score(3, 4) == 75.0
    assert vnap.left_traffic_score(0, 0) is None


def test_runway_pref_score():
    assert vnap.runway_pref_score(2, 5) == 40.0
    assert vnap.runway_pref_score(0, 0) is None


def test_session_limit_scores():
    # tg limit 10: a session of 12 -> 100*10/12; a compliant session -> 100.
    assert vnap.tg_volume_score([5, 10], R) == 100.0
    assert vnap.tg_volume_score([20], R) == 50.0
    assert vnap.tg_volume_score([], R) is None
    # circle limit 4
    assert vnap.circle_restraint_score([8], R) == 50.0


def test_composite_skips_missing_axes():
    scores = {"tightness": 100.0, "altitude": None, "timeofday": 50.0}
    # mean of the two present = 75.0
    assert vnap.composite_score(scores, R) == 75.0
    assert vnap.composite_score({"a": None}, R) is None


def test_group_sessions():
    # gap_min=60 -> 3600s. Two clusters separated by > 1h.
    ts = [0, 60, 120, 5000, 5060]
    assert vnap.group_sessions(ts, 60) == [[0, 60, 120], [5000, 5060]]
    assert vnap.group_sessions([], 60) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_vnap_scores.py -v`
Expected: FAIL (module `app.vnap` does not exist).

- [ ] **Step 3: Implement `vnap.py` (ruleset + pure functions)**

Create `backend/app/vnap.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


AXES = [
    "tightness", "altitude", "timeofday", "tg_volume",
    "circle_restraint", "left_traffic", "runway29",
]


@dataclass(frozen=True)
class VnapRuleset:
    # Quiet window (airport-local hours). Compliant = op inside [start, end).
    quiet_start_hour: int = 8      # 08:00
    quiet_end_hour: int = 20       # 20:00
    # Altitude: >= target AGL over the (whole) area -> 100; scales to 0 at zero.
    agl_target_ft: float = 1000.0
    agl_zero_ft: float = 0.0
    # Pattern tightness: mean deviation (nm) at which the score hits 0.
    dev_floor_nm: float = 1.0
    # Per-session limits.
    tg_per_session_limit: int = 10
    circle_per_session_limit: int = 4
    session_gap_min: int = 60
    # Preferred runway + its approximate heading (deg) for the wind-favored test.
    preferred_runway_id: str = "29"
    preferred_runway_heading_deg: float = 290.0


_KLMO = VnapRuleset()
# Generic default for any other airport (no preferred runway assumptions -> the
# runway29 axis just yields None because preferred ops/favored counts stay 0).
_DEFAULT = VnapRuleset(preferred_runway_id="", preferred_runway_heading_deg=0.0)


def ruleset_for(icao: str) -> VnapRuleset:
    return _KLMO if icao.upper() == "KLMO" else _DEFAULT


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def tightness_score(avg_dev_nm: float | None, rules: VnapRuleset) -> float | None:
    if avg_dev_nm is None:
        return None
    return round(_clamp(100.0 * (1.0 - avg_dev_nm / rules.dev_floor_nm)), 1)


def altitude_score(typical_agl_ft: float | None, rules: VnapRuleset) -> float | None:
    if typical_agl_ft is None:
        return None
    span = rules.agl_target_ft - rules.agl_zero_ft
    return round(_clamp(100.0 * (typical_agl_ft - rules.agl_zero_ft) / span), 1)


def timeofday_score(in_window: int, total: int) -> float | None:
    if not total:
        return None
    return round(100.0 * in_window / total, 1)


def left_traffic_score(left: int, known: int) -> float | None:
    if not known:
        return None
    return round(100.0 * left / known, 1)


def runway_pref_score(on_pref_when_favored: int, favored_total: int) -> float | None:
    if not favored_total:
        return None
    return round(100.0 * on_pref_when_favored / favored_total, 1)


def _session_limit_score(per_session_counts: list[int], limit: int) -> float | None:
    if not per_session_counts:
        return None
    scores = [100.0 if c <= limit else 100.0 * limit / c for c in per_session_counts]
    return round(sum(scores) / len(scores), 1)


def tg_volume_score(tg_per_session: list[int], rules: VnapRuleset) -> float | None:
    return _session_limit_score(tg_per_session, rules.tg_per_session_limit)


def circle_restraint_score(circles_per_session: list[int], rules: VnapRuleset) -> float | None:
    return _session_limit_score(circles_per_session, rules.circle_per_session_limit)


def composite_score(scores: dict, rules: VnapRuleset) -> float | None:
    vals = [v for v in scores.values() if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def group_sessions(timestamps_sorted: list[int], gap_min: int) -> list[list[int]]:
    gap = gap_min * 60
    sessions: list[list[int]] = []
    current: list[int] = []
    for ts in timestamps_sorted:
        if current and ts - current[-1] > gap:
            sessions.append(current)
            current = []
        current.append(ts)
    if current:
        sessions.append(current)
    return sessions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_vnap_scores.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/vnap.py backend/tests/test_vnap_scores.py
git commit -m "feat(vnap): ruleset + pure per-axis compliance score functions"
```

---

### Task 2: `compute_aircraft_compliance` orchestrator

**Files:**
- Modify: `backend/app/vnap.py` (add the orchestrator)
- Test: `backend/tests/test_vnap_compliance.py`

**Interfaces:**
- Consumes: Task 1 functions; `db.airport_timezone`, `db.local_hour`; `flow.headwind_component`.
- Produces:
  ```python
  compute_aircraft_compliance(conn, icao, start_ts, end_ts) -> {
    "axes": AXES,
    "averages": { <axis>: float|None, "composite": float|None },
    "aircraft": [ { "icao24","callsign","registration","tail","aircraft_type",
                    "owner_class","owner_source",            # owner_source == "inferred" in Phase 1
                    "vnap_score", "reports","operations","touch_and_gos",
                    "cowboy_count","deviation_mean_nm","circles",
                    "scores": { <axis>: float|None } } ] }
  ```
  `operations` counts `landing|takeoff|touch_and_go`; `circles` counts `circle`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_vnap_compliance.py`:

```python
from __future__ import annotations

from app import db, vnap


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", dev=None, turn=None, runway=None,
        wind_from=None, wind_speed=None, min_agl=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO", "runway_id": runway,
        "turn_direction": turn, "min_altitude_ft_agl": min_agl,
    }))
    if dev is not None or wind_from is not None:
        conn.execute(
            "UPDATE operations SET deviation_mean_nm=?, wind_from_deg=?, wind_speed_kt=? WHERE id=?",
            (dev, wind_from, wind_speed, oid),
        )


def test_compliance_basic_counts_and_scores(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    # One aircraft: 2 landings + 1 takeoff + 1 T&G (=4 operations), 1 circle.
    _op(conn, "l1", "landing", base + 10, "aa11", turn="left", runway="29",
        wind_from=290, wind_speed=10)     # rwy 29, wind favors 29 -> compliant pref
    _op(conn, "l2", "landing", base + 20, "aa11", turn="left")
    _op(conn, "t1", "takeoff", base + 30, "aa11", turn="left")
    _op(conn, "g1", "touch_and_go", base + 40, "aa11", turn="right")
    _op(conn, "c1", "circle", base + 50, "aa11", dev=0.0, min_agl=1000)
    conn.commit()

    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)

    assert out["axes"] == vnap.AXES
    ac = next(a for a in out["aircraft"] if a["icao24"] == "aa11")
    assert ac["operations"] == 4          # circle excluded from operations count
    assert ac["circles"] == 1
    assert ac["touch_and_gos"] == 1
    assert ac["scores"]["tightness"] == 100.0      # dev 0.0
    assert ac["scores"]["altitude"] == 100.0       # circle min agl 1000
    # left_traffic: 3 of 4 direction-known ops are left -> 75.0
    assert ac["scores"]["left_traffic"] == 75.0
    # runway29: 1 op where 29 favored, 1 used 29 -> 100.0
    assert ac["scores"]["runway29"] == 100.0
    assert ac["vnap_score"] is not None
    assert "composite" in out["averages"]


def test_missing_axis_is_none_and_excluded_from_composite(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    # Aircraft with no circle -> tightness/altitude None; no wind -> runway29 None.
    _op(conn, "l1", "landing", base + 10, "bb22", turn="left")
    conn.commit()
    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == "bb22")
    assert ac["scores"]["tightness"] is None
    assert ac["scores"]["runway29"] is None
    # composite is the mean of only the non-None axes (left_traffic + timeofday here)
    present = [v for k, v in ac["scores"].items() if v is not None]
    assert ac["vnap_score"] == round(sum(present) / len(present), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_vnap_compliance.py -v`
Expected: FAIL (`compute_aircraft_compliance` undefined).

- [ ] **Step 3: Implement the orchestrator**

Add to `backend/app/vnap.py`:

```python
import sqlite3

from . import db as _db
from .flow import headwind_component

_OPERATION_TYPES = ("landing", "takeoff", "touch_and_go")


def compute_aircraft_compliance(conn: sqlite3.Connection, icao: str,
                                start_ts: int, end_ts: int) -> dict:
    icao = icao.upper()
    rules = ruleset_for(icao)
    tz = _db.airport_timezone(conn, icao)

    rows = conn.execute(
        "SELECT o.icao24 AS icao24, o.callsign AS callsign, o.registration AS registration, "
        "       o.timestamp AS ts, o.type AS type, o.deviation_mean_nm AS dev, "
        "       o.turn_direction AS turn, o.runway_id AS runway, "
        "       o.wind_from_deg AS wind_from, o.wind_speed_kt AS wind_speed, "
        "       o.min_altitude_ft_agl AS min_agl, "
        "       (SELECT reg.owner_type FROM aircraft_registry reg "
        "        WHERE reg.icao_hex = upper(o.icao24) LIMIT 1) AS owner_type, "
        "       (SELECT reg.model FROM aircraft_registry reg "
        "        WHERE reg.icao_hex = upper(o.icao24) LIMIT 1) AS model "
        "FROM operations o "
        "WHERE o.icao=? AND o.timestamp BETWEEN ? AND ? "
        "ORDER BY o.timestamp ASC",
        (icao, start_ts, end_ts),
    ).fetchall()

    # Report counts + cowboy counts, batched (avoid N+1).
    report_counts = {
        r["icao24"]: r["report_count"]
        for r in conn.execute("SELECT icao24, report_count FROM aircraft_report_counts").fetchall()
    }
    cowboy_counts: dict[str, int] = {}
    for r in conn.execute(
        "SELECT cowboy_icao24 AS icao24, COUNT(*) AS n FROM runway_changes "
        "WHERE icao=? AND changed_at BETWEEN ? AND ? AND cowboy_icao24 IS NOT NULL "
        "GROUP BY cowboy_icao24",
        (icao, start_ts, end_ts),
    ).fetchall():
        cowboy_counts[r["icao24"]] = r["n"]

    by_ac: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_ac.setdefault(row["icao24"], []).append(row)

    aircraft = []
    for icao24, ac_rows in by_ac.items():
        scores = _score_aircraft(ac_rows, rules, tz)
        callsign = next((r["callsign"] for r in reversed(ac_rows) if r["callsign"]), icao24.upper())
        registration = next((r["registration"] for r in ac_rows if r["registration"]), None)
        owner_type = next((r["owner_type"] for r in ac_rows if r["owner_type"]), "unknown")
        model = next((r["model"] for r in ac_rows if r["model"]), None)
        operations = sum(1 for r in ac_rows if r["type"] in _OPERATION_TYPES)
        circles = sum(1 for r in ac_rows if r["type"] == "circle")
        tgs = sum(1 for r in ac_rows if r["type"] == "touch_and_go")
        devs = [r["dev"] for r in ac_rows if r["type"] == "circle" and r["dev"] is not None]
        aircraft.append({
            "icao24": icao24,
            "callsign": callsign,
            "registration": registration,
            "tail": registration or callsign,
            "aircraft_type": model,
            "owner_class": owner_type,
            "owner_source": "inferred",
            "vnap_score": composite_score(scores, rules),
            "reports": report_counts.get(icao24, 0),
            "operations": operations,
            "touch_and_gos": tgs,
            "cowboy_count": cowboy_counts.get(icao24, 0),
            "deviation_mean_nm": round(sum(devs) / len(devs), 3) if devs else None,
            "circles": circles,
            "scores": scores,
        })

    averages = _averages(aircraft, rules)
    return {"axes": AXES, "averages": averages, "aircraft": aircraft}


def _score_aircraft(rows: list, rules: VnapRuleset, tz: str | None) -> dict:
    # tightness: mean deviation over circle ops that have it.
    devs = [r["dev"] for r in rows if r["type"] == "circle" and r["dev"] is not None]
    avg_dev = sum(devs) / len(devs) if devs else None

    # altitude: median min-AGL over circle ops (their lap-low approximates pattern alt).
    agls = sorted(r["min_agl"] for r in rows if r["type"] == "circle" and r["min_agl"] is not None)
    typical_agl = agls[len(agls) // 2] if agls else None

    # timeofday: fraction of ALL ops inside the local quiet window [start, end).
    total = len(rows)
    in_window = 0
    for r in rows:
        hour = _db.local_hour(r["ts"], tz)
        if rules.quiet_start_hour <= hour < rules.quiet_end_hour:
            in_window += 1

    # tg_volume / circle_restraint: per-session counts.
    tg_ts = sorted(r["ts"] for r in rows if r["type"] == "touch_and_go")
    circle_ts = sorted(r["ts"] for r in rows if r["type"] == "circle")
    tg_sessions = [len(s) for s in group_sessions(tg_ts, rules.session_gap_min)]
    circle_sessions = [len(s) for s in group_sessions(circle_ts, rules.session_gap_min)]

    # left_traffic: fraction left of ops with a known direction.
    known = sum(1 for r in rows if r["turn"] in ("left", "right"))
    left = sum(1 for r in rows if r["turn"] == "left")

    # runway29: of ops where the preferred runway was wind-favored, fraction that used it.
    favored_total = 0
    on_pref = 0
    for r in rows:
        if not r["runway"] or r["wind_from"] is None or r["wind_speed"] is None:
            continue
        hw = headwind_component(rules.preferred_runway_heading_deg, r["wind_from"], r["wind_speed"])
        if hw is not None and hw > 0:
            favored_total += 1
            if r["runway"] == rules.preferred_runway_id:
                on_pref += 1

    return {
        "tightness": tightness_score(avg_dev, rules),
        "altitude": altitude_score(float(typical_agl) if typical_agl is not None else None, rules),
        "timeofday": timeofday_score(in_window, total),
        "tg_volume": tg_volume_score(tg_sessions, rules),
        "circle_restraint": circle_restraint_score(circle_sessions, rules),
        "left_traffic": left_traffic_score(left, known),
        "runway29": runway_pref_score(on_pref, favored_total),
    }


def _averages(aircraft: list[dict], rules: VnapRuleset) -> dict:
    out: dict[str, float | None] = {}
    for axis in AXES:
        vals = [a["scores"][axis] for a in aircraft if a["scores"][axis] is not None]
        out[axis] = round(sum(vals) / len(vals), 1) if vals else None
    comps = [a["vnap_score"] for a in aircraft if a["vnap_score"] is not None]
    out["composite"] = round(sum(comps) / len(comps), 1) if comps else None
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_vnap_compliance.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the vnap suite + guard against regressions**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_vnap_scores.py tests/test_vnap_compliance.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/vnap.py backend/tests/test_vnap_compliance.py
git commit -m "feat(vnap): per-aircraft compliance orchestrator with set averages"
```

---

### Task 3: Endpoint + API client

**Files:**
- Modify: `backend/app/main.py` (route after `get_airport_operations_trends`)
- Modify: `frontend/src/lib/api.ts` (type + fetcher)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `vnap.compute_aircraft_compliance`.
- Produces: `GET /airports/{icao}/vnap-compliance?window=1d|7d|30d|all` → `{airport_icao, window, ...compliance}`; `getVnapCompliance(icao, window)` → `VnapComplianceResponse`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py` (match the existing monkeypatch fixture pattern used by `test_track_history_endpoint` / the operations-trends test — `monkeypatch.setenv(...)`, `get_settings.cache_clear()`, `db.init_db`, `db.db_session` inserts, `TestClient(app)`; add a `_now` override on the route for deterministic time like the operations-trends route):

```python
def test_vnap_compliance_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    base = 1780000000
    with db.db_session(settings.database_path) as conn:
        for oid, typ in (("l1", "landing"), ("g1", "touch_and_go"), ("c1", "circle")):
            db.upsert_operation(conn, db.operation_from_event({
                "id": oid, "type": typ, "icao24": "aa11", "callsign": "AA11",
                "timestamp": base + 10, "airport_icao": "KLMO",
            }))
        conn.commit()
    with TestClient(app) as client:
        resp = client.get("/airports/KLMO/vnap-compliance?window=all&_now=%d" % (base + 100))
        assert resp.status_code == 200
        body = resp.json()
        assert body["airport_icao"] == "KLMO"
        assert body["axes"][0] == "tightness"
        ac = next(a for a in body["aircraft"] if a["icao24"] == "aa11")
        assert ac["operations"] == 1
        assert ac["circles"] == 1
        assert client.get("/airports/ZZZZ/vnap-compliance").status_code == 404
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_api.py -k vnap -v`
Expected: FAIL (404 route missing / assertion).

- [ ] **Step 3: Add the route**

In `backend/app/main.py`, after `get_airport_operations_trends`. `_STATS_WINDOWS`, `db_session`, `db`, `time`, `Annotated`, `Depends`, `Settings`, `settings_dep`, `HTTPException` are already imported there — reuse them. Add `from . import vnap` to the imports at the top of main.py (verify it isn't already imported):

```python
@app.get("/airports/{icao}/vnap-compliance")
async def get_airport_vnap_compliance(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    window: Annotated[str, Query(pattern="^(1d|7d|30d|all)$")] = "7d",
    _now: int | None = None,
):
    now = _now if _now is not None else int(time.time())
    lookback, _bucket = _STATS_WINDOWS[window]
    start_ts = 0 if lookback is None else now - lookback
    with db_session(settings.database_path) as conn:
        if db.get_airport(conn, icao) is None:
            raise HTTPException(status_code=404, detail="airport not found")
        compliance = vnap.compute_aircraft_compliance(conn, icao, start_ts, now)
    return {
        "airport_icao": icao.upper(),
        "window": {"code": window, "start_ts": start_ts, "end_ts": now},
        **compliance,
    }
```

- [ ] **Step 4: Add the API client type + fetcher**

In `frontend/src/lib/api.ts`, after `getOperationsTrends`:

```typescript
export interface VnapAircraft {
  icao24: string; callsign: string; registration: string | null; tail: string;
  aircraft_type: string | null; owner_class: string; owner_source: string;
  vnap_score: number | null; reports: number; operations: number;
  touch_and_gos: number; cowboy_count: number; deviation_mean_nm: number | null;
  circles: number; scores: Record<string, number | null>;
}

export interface VnapComplianceResponse {
  airport_icao: string;
  window: { code: StatsWindow; start_ts: number; end_ts: number };
  axes: string[];
  averages: Record<string, number | null>;
  aircraft: VnapAircraft[];
}

export function getVnapCompliance(icao: string, window: StatsWindow = "7d") {
  return getJson<VnapComplianceResponse>(
    `/airports/${encodeURIComponent(icao)}/vnap-compliance?window=${window}`,
  );
}
```

- [ ] **Step 5: Run test + typecheck**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_api.py -k vnap -v`
Expected: PASS.
Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 6: Full backend suite + commit**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest -q`
Expected: PASS (≥ 226 + new tests).

```bash
git add backend/app/main.py frontend/src/lib/api.ts backend/tests/test_api.py
git commit -m "feat(api): vnap-compliance endpoint + client"
```

---

## Self-Review

**Spec coverage (Phase 1):**
- All 7 axes computed with the design's formulas → Task 1 pure fns + Task 2 orchestrator. ✓
- Composite = equal-weight mean skipping missing axes → `composite_score` + test. ✓
- Per-aircraft counts (reports, operations, T&G, cowboy, deviation, circles) → Task 2. ✓
- Owner class (inferred) + aircraft type via correlated subquery (no fan-out) → Task 2. ✓
- Set averages over the windowed set → `_averages`. ✓
- Endpoint over the selected window + unknown-ICAO 404 + deterministic `_now` → Task 3. ✓
- Reuses `airport_timezone`/`local_hour`/`headwind_component` (no reimplementation). ✓

**Placeholder scan:** no TBD/TODO; every step has full code. ✓

**Type consistency:** `AXES` order identical in `vnap.py`, tests, and the TS `axes` array; orchestrator return keys match the `VnapComplianceResponse`/`VnapAircraft` interfaces (`vnap_score`, `owner_source`, `scores` map, `averages` incl. `composite`); `_now`/404 mirror the operations-trends route. ✓

**Deviation from design (intentional):** the `altitude` axis uses the **median min-AGL over the aircraft's circle ops** as the v1 "typical pattern altitude" signal (db-only, no track read), per the design's stated v1 latitude. Track-based ring-wide altitude is a later refinement.
