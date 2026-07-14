"""Shared test fixtures for the read-only/read-write database split.

This is NOT a test module (no `test_` prefix) — it is imported BY test
modules to build the two-database topology this service actually runs
against in production: a real "production" sqlite file (read-only, from
this service's point of view) and this service's own database, as two
genuinely separate files opened via two genuinely separate connections —
never one connection standing in for both, and never ATTACH DATABASE. See
app/db.py's module docstring for why that split is the whole point.
"""
from __future__ import annotations

import sqlite3

from app import db

# A minimal replica of the subset of the main circlejerks API's schema this
# service ever reads: `airports`, `runways` (unused by ledger/homebase/dwell
# today, kept for parity), `aircraft_registry`, `operations`. This lives ONLY
# in tests — in production the real file already has this schema, created and
# owned entirely by the main API; this service never creates it.
PROD_TEST_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS runways (
  icao TEXT NOT NULL,
  runway_id TEXT NOT NULL,
  lat_threshold REAL NOT NULL,
  lon_threshold REAL NOT NULL,
  heading_deg REAL NOT NULL,
  length_ft INTEGER NOT NULL,
  PRIMARY KEY (icao, runway_id)
);

CREATE TABLE IF NOT EXISTS aircraft_registry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  n_number TEXT NOT NULL UNIQUE,
  icao_hex TEXT,
  registrant_name TEXT,
  registrant_street TEXT,
  registrant_city TEXT,
  registrant_state TEXT,
  registrant_zip TEXT
);
CREATE INDEX IF NOT EXISTS idx_aircraft_registry_icao_hex ON aircraft_registry(icao_hex);

CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY,
  icao TEXT NOT NULL,
  icao24 TEXT,
  callsign TEXT,
  type TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  origin_airport_icao TEXT,
  -- A closed lap's duration, written onto the CIRCLE row by the main API's
  -- deviation pass. laps.py joins a touch_and_go back to its circle on
  -- (icao24, timestamp) to recover the lap's time span from this.
  time_total_s INTEGER
);
CREATE INDEX IF NOT EXISTS idx_operations_icao_ts ON operations(icao, timestamp);
CREATE INDEX IF NOT EXISTS idx_operations_icao24 ON operations(icao24);
"""

# icao, iata, name, city, country, lat, lon, elevation_ft, is_towered, timezone
KLMO_SEED = ("KLMO", "LMO", "Vance Brand Airport", "Longmont, CO", "US", 40.1646, -105.1630, 5055, 0, "America/Denver")


def build_dbs(tmp_path):
    """Returns (setup_conn, ro_conn, rw_conn).

    `setup_conn` — an ordinary writable connection to the "production" file,
    used ONLY by the `op`/`register` helpers below to insert fixture rows
    (standing in for what the main circlejerks API's detector/registry
    importer already wrote there). Every helper commits immediately, so
    anything inserted is visible to `ro_conn` right away.

    `ro_conn` — opened via `db.open_production_readonly` against that SAME
    file. This is the actual read-only connection the functions under test
    receive; it is what `test_production_readonly_cannot_write` proves cannot
    be written through.

    `rw_conn` — this service's OWN database, via `db.open_ledger_db`, at a
    completely separate file.
    """
    prod_path = tmp_path / "prod.sqlite3"
    ledger_path = tmp_path / "ledger.sqlite3"

    setup_conn = sqlite3.connect(str(prod_path))
    setup_conn.row_factory = sqlite3.Row
    setup_conn.executescript(PROD_TEST_SCHEMA)
    setup_conn.execute(
        "INSERT INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered, timezone) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        KLMO_SEED,
    )
    setup_conn.commit()

    ro_conn = db.open_production_readonly(str(prod_path))
    rw_conn = db.open_ledger_db(str(ledger_path))
    return setup_conn, ro_conn, rw_conn


def op(setup_conn, oid, type_, ts, icao24="a1", callsign=None, origin=None, icao="KLMO",
       time_total_s=None):
    setup_conn.execute(
        "INSERT INTO operations "
        "  (id, icao, icao24, callsign, type, timestamp, origin_airport_icao, time_total_s) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, icao, icao24, callsign or icao24.upper(), type_, ts, origin, time_total_s),
    )
    setup_conn.commit()


def lap(setup_conn, oid, ts, icao24="a1", duration_s=330, icao="KLMO", callsign=None):
    """One detected pattern lap, exactly as the main API writes it: a `circle` row
    carrying the lap's duration, plus the `touch_and_go` derived 1:1 from it and
    sharing its (icao24, timestamp). See detectors.touch_and_gos_from_circles.

    Returns the touch_and_go's operation id — the row a runway-use count counts.
    """
    op(setup_conn, f"c-{oid}", "circle", ts, icao24=icao24, callsign=callsign, icao=icao,
       time_total_s=duration_s)
    op(setup_conn, f"tg-{oid}", "touch_and_go", ts, icao24=icao24, callsign=callsign, icao=icao)
    return f"tg-{oid}"


def register(setup_conn, n_number, icao_hex, registrant_name=None, city=None, state=None,
             street=None, zip_=None):
    setup_conn.execute(
        "INSERT INTO aircraft_registry "
        "  (n_number, icao_hex, registrant_name, registrant_street, registrant_city, "
        "   registrant_state, registrant_zip) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (n_number, icao_hex.upper(), registrant_name, street, city, state, zip_),
    )
    setup_conn.commit()
