"""Endpoint-level coverage for the two additions to GET /airports/{icao}/ledger:

  * `visits` -- of the landings we could pair with a takeoff, how many
    actually stopped (>= 20 minutes) versus a quick turn. See app/dwell.py's
    `visit_summary` for the exhaustive unit tests (boundary, multi-day visit,
    coverage/never-divide-by-zero); this file only proves the endpoint wires
    it through correctly.

  * `projection` -- a PROJECTED annual runway-use rate from our own measured
    data, never from the FAA's Form-5010 estimate. See app/ledger.py's
    `annual_projection` docstring for why. This file proves it is computed
    from the FULL history, independent of whatever `days` window a caller
    requests.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from app import db as app_db
from app import ledger
from app.main import app, settings_dep
from app.settings import Settings

from .dbsupport import PROD_TEST_SCHEMA, KLMO_SEED

BASE = 1780336800  # 2026-06-01 12:00 America/Denver
DAY = 86400


@pytest.fixture(autouse=True)
def _clear_settings_override():
    # Each test below builds its own client via _make_client(), which sets
    # app.dependency_overrides directly (there is no shared `client` fixture
    # here to do it for us) -- clear it after every test, pass or fail, so
    # one test's fixture db never leaks into the next.
    yield
    app.dependency_overrides.clear()


def _make_client(tmp_path, seed: Callable[[sqlite3.Connection], None]) -> TestClient:
    """A TestClient wired to a fresh prod+ledger db pair, seeded by `seed`.

    Mirrors test_ledger_endpoint.py's fixture, but as a helper rather than a
    shared fixture, so each test here can seed its own landing/takeoff
    scenario without fighting a one-size-fits-all fixture.
    """
    prod_path = str(tmp_path / "prod.sqlite3")
    ledger_path = str(tmp_path / "ledger.sqlite3")

    setup = sqlite3.connect(prod_path)
    setup.row_factory = sqlite3.Row
    setup.executescript(PROD_TEST_SCHEMA)
    setup.execute(
        "INSERT INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered, timezone) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        KLMO_SEED,
    )
    seed(setup)
    setup.commit()
    setup.close()

    ro = app_db.open_production_readonly(prod_path)
    rw = app_db.open_ledger_db(ledger_path)
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - 40 * DAY, BASE + DAY)
    rw.commit()
    ro.close()
    rw.close()

    def _settings():
        return Settings(
            production_database_path=prod_path,
            ledger_database_path=ledger_path,
            live_source_priority="adsb_lol,self_hosted",
        )

    app.dependency_overrides[settings_dep] = _settings
    return TestClient(app)


def _insert_op(setup, oid, icao24, callsign, type_, ts):
    setup.execute(
        "INSERT INTO operations (id, icao, icao24, callsign, type, timestamp) "
        "VALUES (?, 'KLMO', ?, ?, ?, ?)",
        (oid, icao24, callsign, type_, ts),
    )


def test_visits_block_pairs_a_real_stay_and_a_quick_turn(tmp_path):
    def seed(setup):
        # Aircraft A: on the ground 30 minutes (>= 1200s) -> a real visit.
        _insert_op(setup, "la", "aaa111", "N1AA", "landing", BASE)
        _insert_op(setup, "ta", "aaa111", "N1AA", "takeoff", BASE + 1800)
        # Aircraft B: on the ground 5 minutes -> a quick turn.
        _insert_op(setup, "lb", "bbb222", "N2BB", "landing", BASE + 3600)
        _insert_op(setup, "tb", "bbb222", "N2BB", "takeoff", BASE + 3600 + 300)

    client = _make_client(tmp_path, seed)
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()

    assert body["visits"] == {
        "min_seconds": 1200,
        "stayed": 1,
        "quick_turn": 1,
        "paired": 2,
        "landings": 2,
        "coverage": 1.0,
        "median_stay_seconds": 1800,
    }


def test_visits_block_reports_honest_zero_when_nothing_can_be_paired(tmp_path):
    def seed(setup):
        # A touch-and-go and a takeoff, but no landing at all -- nothing for
        # visit_summary to pair.
        _insert_op(setup, "g1", "aaa111", "N1AA", "touch_and_go", BASE)
        _insert_op(setup, "t1", "aaa111", "N1AA", "takeoff", BASE + 600)

    client = _make_client(tmp_path, seed)
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()

    visits = body["visits"]
    assert visits["landings"] == 0
    assert visits["paired"] == 0
    assert visits["stayed"] == 0
    assert visits["quick_turn"] == 0
    assert visits["coverage"] == 0.0
    assert visits["median_stay_seconds"] is None


def test_visits_block_does_not_silently_drop_a_multi_day_stay(tmp_path):
    # The whole point of the 1200s/20-minute threshold is to catch a genuine
    # visit -- and a visit can run for days. This must show up as `stayed`,
    # not fall out as an uncovered/unpaired landing.
    def seed(setup):
        _insert_op(setup, "l1", "ccc333", "N3CC", "landing", BASE)
        _insert_op(setup, "t1", "ccc333", "N3CC", "takeoff", BASE + 3 * DAY)

    client = _make_client(tmp_path, seed)
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + 4 * DAY}").json()

    visits = body["visits"]
    assert visits["landings"] == 1
    assert visits["paired"] == 1
    assert visits["stayed"] == 1
    assert visits["quick_turn"] == 0
    assert visits["median_stay_seconds"] == 3 * DAY


def test_projection_block_uses_full_history_regardless_of_the_requested_days_window(tmp_path):
    def seed(setup):
        # 10 days of history (one landing per day), but the request below
        # asks for only a 3-day `days` window. The projection must still
        # reflect the full 10-day history, not the narrower window.
        for i in range(10):
            _insert_op(setup, f"l{i}", "aaa111", "N1AA", "landing", BASE - 9 * DAY + i * DAY)

    client = _make_client(tmp_path, seed)
    body = client.get(f"/airports/KLMO/ledger?days=3&_now={BASE}").json()

    projection = body["projection"]
    assert projection["counting_since"] == BASE - 9 * DAY
    assert projection["days_of_data"] == 9.0
    assert projection["runway_uses_to_date"] == 10
    expected_rate = 10 / 9
    assert projection["observed_daily_rate"] == round(expected_rate, 2)
    assert projection["annualization_days"] == 365
    assert projection["projected_annual_runway_uses"] == round(expected_rate * 365)
    # Sanity: the window param that drives the REST of the page really was 3.
    assert body["window"]["days"] == 3


def test_projection_block_floors_days_of_data_at_one_for_a_brand_new_deployment(tmp_path):
    def seed(setup):
        _insert_op(setup, "l1", "aaa111", "N1AA", "landing", BASE)

    client = _make_client(tmp_path, seed)
    # _now only 60 seconds after the first-ever counted row.
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + 60}").json()

    projection = body["projection"]
    assert projection["counting_since"] == BASE
    assert projection["days_of_data"] == 1.0
    assert projection["runway_uses_to_date"] == 1
    assert projection["observed_daily_rate"] == 1.0
    assert projection["projected_annual_runway_uses"] == 365


def test_projection_block_is_all_zero_with_no_operations_at_all(tmp_path):
    def seed(setup):
        pass  # no operations rows at all for KLMO

    client = _make_client(tmp_path, seed)
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE}").json()

    assert body["projection"] == {
        "counting_since": None,
        "days_of_data": 0.0,
        "runway_uses_to_date": 0,
        "observed_daily_rate": 0.0,
        "annualization_days": 365,
        "projected_annual_runway_uses": 0,
    }
