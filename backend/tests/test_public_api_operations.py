from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import db
from app.main import app
from test_public_api import auth, configure, mint

NOW = 1_700_000_000

# upsert_operation binds every one of these by name; omitting any raises
# sqlite3.ProgrammingError. Only emitter_category and pass_geometry_key
# are defaulted by the function itself.
OP_DEFAULTS = {
    "icao24": "a1b2c3",
    "callsign": "N333RX",
    "registration": "N333RX",
    "runway_id": "11",
    "runway_heading_deg": 110.0,
    "turn_direction": "left",
    "min_altitude_ft_agl": 900,
}


def seed_operations(db_path: str, icao: str, count: int) -> None:
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        for index in range(count):
            db.upsert_operation(conn, {
                **OP_DEFAULTS,
                "id": f"op-{index:03d}",
                "icao": icao,
                "type": "touch_and_go",
                "timestamp": NOW + index,
            })


def test_operations_requires_the_ops_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_operations_enforces_the_airport_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_operations_returns_a_stable_field_set(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 1)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "since": NOW - 10, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert set(row) == {
            "id", "airport_icao", "icao24", "callsign", "registration", "type",
            "timestamp", "runway_id", "turn_direction", "min_altitude_ft_agl",
            "emitter_category", "deviation_mean_nm", "deviation_peak_nm",
            "pct_off_pattern", "wind_from_deg", "wind_speed_kt",
            "origin_airport_icao", "origin_label", "operator", "flight_school",
        }
        assert row["airport_icao"] == "KBJC"


def test_operations_paginates_without_gaps_or_overlap(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 5)
    key = mint(db_path, scopes=["ops:read"])
    seen: list[str] = []
    with TestClient(app) as client:
        params = {"airport": "KBJC", "since": NOW - 10, "until": NOW + 100, "limit": 2}
        cursor = None
        for _ in range(5):
            page = client.get(
                "/v1/operations",
                params={**params, **({"cursor": cursor} if cursor else {})},
                headers=auth(key),
            ).json()
            seen.extend(item["id"] for item in page["data"])
            cursor = page["next_cursor"]
            if not cursor:
                break
    assert seen == [f"op-{i:03d}" for i in range(5)]


def test_operations_clamps_limit_and_rejects_a_bad_cursor(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 1)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        ok = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "limit": 99999},
            headers=auth(key),
        )
        assert ok.status_code == 200

        bad = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "cursor": "!!!not-base64!!!"},
            headers=auth(key),
        )
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "invalid_request"


def test_operations_filters_by_type_and_icao24(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 2)
    with db.db_session(db_path) as conn:
        db.upsert_operation(conn, {
            **OP_DEFAULTS,
            "id": "op-landing",
            "icao": "KBJC",
            "icao24": "ffffff",
            "type": "landing",
            "timestamp": NOW + 50,
        })
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        # since/until pinned to NOW: without them this falls back to the
        # default 7-day-before-wall-clock lookback, and NOW is a fixed
        # historical constant that ages out of that window over time.
        window = {"since": NOW - 10, "until": NOW + 100}
        by_type = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "type": "landing", **window},
            headers=auth(key),
        ).json()
        assert [row["id"] for row in by_type["data"]] == ["op-landing"]

        by_aircraft = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "icao24": "FFFFFF", **window},
            headers=auth(key),
        ).json()
        assert [row["id"] for row in by_aircraft["data"]] == ["op-landing"]


def test_operations_rejects_an_inverted_range(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "since": NOW, "until": NOW - 1},
            headers=auth(key),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"
