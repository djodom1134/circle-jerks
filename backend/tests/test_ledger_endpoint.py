from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, ledger
from app.main import app, settings_dep
from app.settings import Settings


BASE = 1780336800   # 2026-06-01 12:00 America/Denver
DAY = 86400


@pytest.fixture
def client(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    conn = db.connect(path)
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)

    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_name, "
        "  registrant_city, registrant_state) VALUES ('N111AA','AAA111',"
        "  'BOULDER FLIGHT SCHOOL LLC','Boulder','CO')"
    )
    for i in range(6):
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"g{i}", "type": "touch_and_go", "icao24": "aaa111",
            "callsign": "N111AA", "timestamp": BASE + i * 60, "airport_icao": "KLMO",
        }))
    db.upsert_operation(conn, db.operation_from_event({
        "id": "t1", "type": "takeoff", "icao24": "aaa111", "callsign": "N111AA",
        "timestamp": BASE + 600, "airport_icao": "KLMO",
    }))
    ledger.rebuild_rollup(conn, "KLMO", BASE - 40 * DAY, BASE + DAY)
    conn.commit()
    conn.close()

    def _settings():
        return Settings(database_path=path, live_source_priority="adsb_lol,self_hosted")

    app.dependency_overrides[settings_dep] = _settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_ledger_returns_runway_uses_excluding_takeoffs(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert body["summary"]["runway_uses"] == 6      # 6 t&g; the takeoff does not count
    assert body["summary"]["unique_aircraft"] == 1


def test_ledger_daily_series_is_gap_filled_to_the_requested_length(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert len(body["daily"]) == 30
    assert sum(d["runway_uses"] for d in body["daily"]) == 6


def test_ledger_names_the_flight_school(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert body["operators"][0]["operator"] == "BOULDER FLIGHT SCHOOL LLC"
    assert body["operators"][0]["runway_uses"] == 6


def test_methodology_is_served_by_the_api_not_the_frontend(client):
    # The site's published caveats must never drift from the code that produced the
    # numbers, so they ship WITH the numbers.
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    method = body["methodology"]
    assert method["billable_unit"] == "runway_use"
    assert "floor" in method["floor_disclaimer"].lower()
    assert set(method["definitions"]) == set(ledger.RUNWAY_USE_TYPES)
    assert "does not confirm" in method["definitions"]["touch_and_go"].lower()
    # The credits block is plain data, not a gate: it lists the sources this
    # deployment ingests. adsb_lol is configured in the fixture's settings.
    assert method["attribution"]["adsb_lol"]["license"].startswith("ODbL")


def test_methodology_reports_unclassified_count(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert "unclassified_aircraft" in body["summary"]


def test_unknown_airport_is_404(client):
    assert client.get("/airports/ZZZZ/ledger").status_code == 404
