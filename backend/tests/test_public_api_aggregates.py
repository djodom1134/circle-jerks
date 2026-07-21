from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from test_public_api import auth, configure, mint

PATHS = [
    "/v1/airports/KBJC/stats",
    "/v1/airports/KBJC/worst-offenders",
    "/v1/airports/KBJC/operations-trends",
    "/v1/airports/KBJC/vnap-compliance",
    "/v1/airports/KBJC/runways",
    "/v1/airports/KBJC/patterns",
    "/v1/airports/KBJC/flow",
]


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_require_the_aggregates_scope(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_honour_the_airport_restriction(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_answer_for_a_seeded_airport(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 200
        assert resp.json()["airport_icao"] == "KBJC"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/airports/ZZZZ/stats",
        "/v1/airports/ZZZZ/worst-offenders",
        "/v1/airports/ZZZZ/operations-trends",
        "/v1/airports/ZZZZ/vnap-compliance",
        "/v1/airports/ZZZZ/runways",
        "/v1/airports/ZZZZ/patterns",
        "/v1/airports/ZZZZ/flow",
    ],
)
def test_unknown_airport_is_a_v1_shaped_404(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_stats_window_parameter_is_validated(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        ok = client.get("/v1/airports/KBJC/stats", params={"window": "30d"}, headers=auth(key))
        assert ok.status_code == 200
        assert ok.json()["window"]["code"] == "30d"

        bad = client.get("/v1/airports/KBJC/stats", params={"window": "9y"}, headers=auth(key))
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "invalid_request"


# ─── Gap: a restricted key's 403 must be indistinguishable from a 404 ────────
#
# `_known_airport` enforces the key's airport restriction BEFORE checking the
# airport exists (public_api.py), so a key restricted to one airport must not
# be able to tell "real airport I can't see" from "no such airport" — that
# distinction would let a restricted key enumerate the airports table by
# probing ICAOs and watching for 403 vs 404. ZZZZ is not in AIRPORT_SEED
# (db.py), so this is the case the existing tests never hit: restricted key +
# an ICAO that plain doesn't exist.

@pytest.mark.parametrize(
    "path",
    [
        "/v1/airports/ZZZZ/stats",
        "/v1/airports/ZZZZ/worst-offenders",
        "/v1/airports/ZZZZ/operations-trends",
        "/v1/airports/ZZZZ/vnap-compliance",
        "/v1/airports/ZZZZ/runways",
        "/v1/airports/ZZZZ/patterns",
        "/v1/airports/ZZZZ/flow",
    ],
)
def test_restricted_key_gets_forbidden_not_not_found_for_an_unknown_airport(
    tmp_path, monkeypatch, path,
):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


# ─── Gap: the 200-path tests must prove `icao` is actually threaded through ──
#
# The existing "answers for a seeded airport" tests only assert
# `resp.json()["airport_icao"] == "KBJC"`, which is just the path parameter
# echoed back — a handler that ignored `icao` and queried a hardcoded or
# wrong airport internally would still pass. This is not hypothetical:
# `build_worst_offenders` (services.py) falls back to a nearest-neighbour
# airport when the requested one has no scored aircraft, so the identity of
# the data actually returned is a real variable. Each test below seeds
# DIFFERENT data at KBJC and KLMO and asserts a response for one airport
# never reflects the other's.
#
# Timestamps are seeded relative to `int(time.time())`, captured fresh inside
# each test, rather than a fixed historical constant: stats/trends/offenders
# all read through windows computed from the real wall clock (7d default,
# 12-month trends, "now" for the offenders scan), and a fixed constant
# silently ages out of those windows as real time moves on — two such bugs
# were already found in this feature. Seeding a handful of rows a few seconds
# behind "now" keeps every row inside every window this file exercises,
# indefinitely.

def _operation(icao: str, icao24: str, registration: str, op_type: str,
               timestamp: int, index: int, turn_direction: str = "left") -> dict:
    return {
        "id": f"{icao}-{icao24}-{op_type}-{index:03d}",
        "icao": icao,
        "icao24": icao24,
        "callsign": registration,
        "registration": registration,
        "type": op_type,
        "timestamp": timestamp,
        "runway_id": "11",
        "runway_heading_deg": 110.0,
        "turn_direction": turn_direction,
        "min_altitude_ft_agl": 900,
    }


def _seed_ops(db_path: str, icao: str, icao24: str, registration: str, op_type: str,
              count: int, base_ts: int, turn_direction: str = "left") -> None:
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        for i in range(count):
            db.upsert_operation(
                conn,
                _operation(icao, icao24, registration, op_type, base_ts - i, i, turn_direction),
            )


def test_stats_reflects_only_the_requested_airports_operations(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    now = int(time.time())
    _seed_ops(db_path, "KBJC", "aaaaaa", "N-KBJCAA", "touch_and_go", 3, now)
    _seed_ops(db_path, "KLMO", "bbbbbb", "N-KLMOBB", "touch_and_go", 7, now)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        # window="all" (start_ts=0) so the assertion never depends on where
        # "now" falls relative to a fixed lookback — only that seeded rows are
        # in the past, which they always are.
        kbjc = client.get(
            "/v1/airports/KBJC/stats", params={"window": "all"}, headers=auth(key),
        ).json()
        klmo = client.get(
            "/v1/airports/KLMO/stats", params={"window": "all"}, headers=auth(key),
        ).json()

    assert kbjc["counters"]["touch_and_gos"] == 3
    assert kbjc["counters"]["unique_aircraft"] == 1
    assert klmo["counters"]["touch_and_gos"] == 7
    assert klmo["counters"]["unique_aircraft"] == 1
    assert kbjc["counters"]["touch_and_gos"] != klmo["counters"]["touch_and_gos"]


def test_operations_trends_reflects_only_the_requested_airports_operations(
    tmp_path, monkeypatch,
):
    db_path = configure(tmp_path, monkeypatch)
    now = int(time.time())
    _seed_ops(db_path, "KBJC", "aaaaaa", "N-KBJCAA", "touch_and_go", 4, now)
    _seed_ops(db_path, "KLMO", "bbbbbb", "N-KLMOBB", "touch_and_go", 9, now)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        kbjc = client.get("/v1/airports/KBJC/operations-trends", headers=auth(key)).json()
        klmo = client.get("/v1/airports/KLMO/operations-trends", headers=auth(key)).json()

    # Summed across all monthly buckets rather than assuming a single bucket,
    # so a test run that straddles a local month boundary can't flake.
    kbjc_total = sum(m["total"] for m in kbjc["monthly"])
    klmo_total = sum(m["total"] for m in klmo["monthly"])
    assert kbjc_total == 4
    assert klmo_total == 9
    assert kbjc_total != klmo_total


def test_worst_offenders_reflects_only_the_requested_airports_aircraft(tmp_path, monkeypatch):
    """The highest-value content test: `build_worst_offenders` falls back to a
    nearest OTHER airport when the requested one has no scored aircraft
    (services.build_worst_offenders -> db.nearest_airport_excluding). Seeding
    an aircraft that clears the VNAP scoring gate at BOTH airports proves the
    handler's response reflects the airport actually requested — not a
    hardcoded airport, and not a fallback neighbour.

    The gate (vnap.py): an aircraft only scores once it has >=1 touch-and-go
    AND >=3 total laps (score_min_tg / score_min_circles), and
    rank_worst_offenders multiplies the score by the circle count, so >=1
    "circle" row is required too. turn_direction is "right" for every row
    (not the file's usual "left") so the left_traffic axis is pinned at a
    deterministic 100% violation regardless of the local hour the suite
    happens to run at — otherwise a fully-compliant, all-axes-zero aircraft
    would score 0 and silently vanish from the offenders list depending on
    wall-clock time, triggering the very fallback this test exists to catch.
    """
    db_path = configure(tmp_path, monkeypatch)
    now = int(time.time())
    _seed_ops(db_path, "KBJC", "aaaaaa", "N-KBJCAA", "touch_and_go", 1, now, "right")
    _seed_ops(db_path, "KBJC", "aaaaaa", "N-KBJCAA", "circle", 3, now - 200, "right")
    _seed_ops(db_path, "KLMO", "bbbbbb", "N-KLMOBB", "touch_and_go", 1, now, "right")
    _seed_ops(db_path, "KLMO", "bbbbbb", "N-KLMOBB", "circle", 5, now - 200, "right")
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        kbjc = client.get("/v1/airports/KBJC/worst-offenders", headers=auth(key)).json()
        klmo = client.get("/v1/airports/KLMO/worst-offenders", headers=auth(key)).json()

    assert kbjc["resolved_icao"] == "KBJC"
    assert kbjc["is_fallback"] is False
    assert [o["icao24"] for o in kbjc["offenders"]] == ["aaaaaa"]
    assert kbjc["offenders"][0]["total_circles"] == 3

    assert klmo["resolved_icao"] == "KLMO"
    assert klmo["is_fallback"] is False
    assert [o["icao24"] for o in klmo["offenders"]] == ["bbbbbb"]
    assert klmo["offenders"][0]["total_circles"] == 5


def test_runways_reflect_the_requested_airports_static_runway_table(tmp_path, monkeypatch):
    """Purely static (RUNWAY_SEED in db.py) — KBJC and KLMO already have
    disjoint runway_id sets, so no dynamic seeding is needed to tell them
    apart."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        kbjc = client.get("/v1/airports/KBJC/runways", headers=auth(key)).json()
        klmo = client.get("/v1/airports/KLMO/runways", headers=auth(key)).json()

    kbjc_ids = {row["runway_id"] for row in kbjc["runways"]}
    klmo_ids = {row["runway_id"] for row in klmo["runways"]}
    assert kbjc_ids == {"12L", "30R", "12R", "30L"}
    assert klmo_ids == {"11", "29"}
    assert kbjc_ids.isdisjoint(klmo_ids)


def test_patterns_reflect_the_requested_airports_pattern_rows(tmp_path, monkeypatch):
    """`current_patterns_for_airport` is not time-windowed (WHERE icao=? AND
    is_current=1 only), so seeding one pattern per airport at any timestamp is
    enough to tell the two apart — no wall-clock concern applies here."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with db.db_session(db_path) as conn:
        db.save_runway_pattern(conn, "KBJC", "12L", '{"marker": "KBJC"}', name="kbjc-pattern")
        db.save_runway_pattern(conn, "KLMO", "11", '{"marker": "KLMO"}', name="klmo-pattern")

    with TestClient(app) as client:
        kbjc = client.get("/v1/airports/KBJC/patterns", headers=auth(key)).json()
        klmo = client.get("/v1/airports/KLMO/patterns", headers=auth(key)).json()

    assert [p["runway_id"] for p in kbjc["patterns"]] == ["12L"]
    assert kbjc["patterns"][0]["geometry"] == {"marker": "KBJC"}
    assert [p["runway_id"] for p in klmo["patterns"]] == ["11"]
    assert klmo["patterns"][0]["geometry"] == {"marker": "KLMO"}


def test_flow_reflects_the_requested_airports_flow_and_changes(tmp_path, monkeypatch):
    """`current_flow` and `recent_runway_changes` are not time-windowed either
    (latest open row / ORDER BY ... LIMIT N with no since/until), so a fixed
    timestamp is fine here — there is no lookback for it to age out of."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with db.db_session(db_path) as conn:
        db.open_flow(conn, "KBJC", "12L", established_at=1_700_000_000,
                     wind_from_deg=120, wind_speed_kt=5.0)
        db.open_flow(conn, "KLMO", "11", established_at=1_700_000_000,
                     wind_from_deg=110, wind_speed_kt=8.0)
        db.insert_runway_change(
            conn, "KBJC", from_runway_id="30R", to_runway_id="12L",
            changed_at=1_700_000_000, cowboy=None,
            wind_from_deg=120, wind_speed_kt=5.0, wind_favored_new=1,
        )
        db.insert_runway_change(
            conn, "KLMO", from_runway_id="29", to_runway_id="11",
            changed_at=1_700_000_000, cowboy=None,
            wind_from_deg=110, wind_speed_kt=8.0, wind_favored_new=1,
        )

    with TestClient(app) as client:
        kbjc = client.get("/v1/airports/KBJC/flow", headers=auth(key)).json()
        klmo = client.get("/v1/airports/KLMO/flow", headers=auth(key)).json()

    assert kbjc["active"]["active_runway_id"] == "12L"
    assert klmo["active"]["active_runway_id"] == "11"
    assert [c["to_runway_id"] for c in kbjc["recent_changes"]] == ["12L"]
    assert [c["to_runway_id"] for c in klmo["recent_changes"]] == ["11"]
