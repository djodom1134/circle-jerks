"""The data boundary. Read this file before touching anything else in this
service.

The Lost Landing is a CUSTOMER of the main circlejerks API's database, never a
co-writer of it. This module is the ONLY place that ever opens either
database, and it exposes exactly two ways to get a connection:

  * `open_production_readonly` / `production_readonly_session` — the main
    circlejerks database (`operations`, `airports`, `runways`,
    `aircraft_registry`). Opened via a `file:...?mode=ro` URI connection, which
    makes a write against it fail at the SQLite engine level, not by
    application convention: see `test_production_readonly_cannot_write` for
    the proof. NEVER pass this connection to anything that writes
    `daily_operation_rollup` or `aircraft_home_base`.

  * `open_ledger_db` / `ledger_db_session` — THIS service's own database.
    `daily_operation_rollup` and `aircraft_home_base` live here and NOWHERE
    ELSE. Fully read-write, and it is the only database this process is ever
    allowed to write to.

Every function in ledger.py / homebase.py / dwell.py that touches BOTH
databases takes two separate connection parameters, always named `ro_conn`
(production, read-only) and `rw_conn` (this service's own db) so the two can
never be confused for each other at a call site. There is no ATTACH DATABASE
anywhere in this service, on purpose: attaching the production file onto a
writable connection would make "structurally impossible to write" a matter of
which schema-qualified name you typed, not a guarantee the connection itself
enforces.

Zero write contention with production follows directly from this split: this
service never opens a write transaction against circlejerk.sqlite3, so it can
never be the thing that holds production's single SQLite write lock.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import quote
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Airport:
    icao: str
    iata: str | None
    name: str
    city: str
    country: str
    lat: float
    lon: float
    elevation_ft: int
    is_towered: bool


# This service's OWN schema. Nothing here ever appears in the production
# database, and nothing production owns (`operations`, `airports`, `runways`,
# `aircraft_registry`, ...) is ever created or altered here.
LEDGER_SCHEMA = """
PRAGMA journal_mode=WAL;

-- Pre-aggregated daily counts, keyed by AIRPORT-LOCAL calendar day. Rebuilt
-- idempotently by ledger.rebuild_rollup from the production `operations`
-- table via the read-only connection; never written from anywhere else.
CREATE TABLE IF NOT EXISTS daily_operation_rollup (
  icao TEXT NOT NULL,
  date_local TEXT NOT NULL,        -- 'YYYY-MM-DD' in the airport's local timezone
  event_type TEXT NOT NULL,        -- landing | takeoff | touch_and_go | low_approach
  icao24 TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (icao, date_local, event_type, icao24)
);
CREATE INDEX IF NOT EXISTS idx_rollup_icao_date ON daily_operation_rollup(icao, date_local);

-- Local vs non-local, recomputed nightly by homebase.recompute_airport. See
-- homebase.py's module docstring for `evidence_json` and `signal_strength`.
CREATE TABLE IF NOT EXISTS aircraft_home_base (
  icao TEXT NOT NULL,
  icao24 TEXT NOT NULL,
  locality TEXT NOT NULL,          -- local | non_local | unclassified
  signal_strength REAL NOT NULL,
  based_icao TEXT,
  evidence_json TEXT NOT NULL,
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (icao, icao24)
);
CREATE INDEX IF NOT EXISTS idx_home_base_icao ON aircraft_home_base(icao, locality);
"""


def open_production_readonly(path: str) -> sqlite3.Connection:
    """The ONLY way this service ever touches the production circlejerks
    database. Opened via a read-only URI connection: SQLite refuses ANY write
    issued against it -- INSERT, UPDATE, DELETE, CREATE, DROP, ATTACH-and-write
    -- at the engine level, before it ever reaches application code. Proven by
    `test_production_readonly_cannot_write`.

    The production file must already exist; this never creates or migrates
    it (that is the main circlejerks API's job, not this service's).
    """
    # quote(): a path containing spaces or other characters that are special
    # in a URI (common under pytest's tmp_path, and not impossible in a real
    # deployment path) must be percent-encoded, or sqlite3 either mangles the
    # path or raises "unable to open database file".
    uri_path = quote(str(Path(path).resolve()))
    conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def open_ledger_db(path: str) -> sqlite3.Connection:
    """This service's OWN database. Read-write, and the only database this
    process may ever write to. Initializes its own schema on first use."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(LEDGER_SCHEMA)
    _migrate_ledger_db(conn)
    conn.commit()
    return conn


def _migrate_ledger_db(conn: sqlite3.Connection) -> None:
    """Self-heal `aircraft_home_base` if it predates the `confidence` ->
    `signal_strength` rename (carried over from the main backend's db.py,
    which had this same self-heal before the ledger's tables moved out of
    the production schema). `aircraft_home_base` is a pure derived cache
    rebuilt nightly from production `operations` -- nothing is lost by
    dropping and recreating a shape we cannot use, so a crash becomes a
    self-heal rather than a permanent break.
    """
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(aircraft_home_base)").fetchall()
    }
    if columns and "signal_strength" not in columns:
        conn.execute("DROP TABLE aircraft_home_base")
        conn.executescript(LEDGER_SCHEMA)


@contextmanager
def production_readonly_session(path: str) -> Iterator[sqlite3.Connection]:
    """A read-only production connection, closed on exit. Never commits --
    there is nothing to commit, because nothing written through this
    connection can ever succeed."""
    conn = open_production_readonly(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def ledger_db_session(path: str) -> Iterator[sqlite3.Connection]:
    """A read-write connection to THIS service's own database, committed and
    closed on exit."""
    conn = open_ledger_db(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_airport(row: sqlite3.Row) -> Airport:
    return Airport(
        icao=row["icao"],
        iata=row["iata"],
        name=row["name"],
        city=row["city"],
        country=row["country"],
        lat=row["lat"],
        lon=row["lon"],
        elevation_ft=row["elevation_ft"],
        is_towered=bool(row["is_towered"]),
    )


def get_airport(conn: sqlite3.Connection, icao: str) -> Airport | None:
    """Reads the production `airports` table. `conn` must be a production
    read-only connection."""
    row = conn.execute("SELECT * FROM airports WHERE icao = ?", (icao.upper(),)).fetchone()
    return row_to_airport(row) if row else None


def airport_timezone(conn: sqlite3.Connection, icao: str) -> str | None:
    """Reads the production `airports` table. `conn` must be a production
    read-only connection."""
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


def local_day_key(ts: int, tz: str | None) -> str:
    return _local_dt(ts, tz).strftime("%Y-%m-%d")
