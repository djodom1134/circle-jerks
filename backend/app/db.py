from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .geo import Point, distance_nm


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


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS airports (
  icao TEXT PRIMARY KEY,
  iata TEXT,
  name TEXT NOT NULL,
  city TEXT NOT NULL,
  country TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  elevation_ft INTEGER NOT NULL,
  is_towered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runways (
  icao TEXT NOT NULL,
  runway_id TEXT NOT NULL,
  lat_threshold REAL NOT NULL,
  lon_threshold REAL NOT NULL,
  heading_deg REAL NOT NULL,
  length_ft INTEGER NOT NULL,
  PRIMARY KEY (icao, runway_id),
  FOREIGN KEY (icao) REFERENCES airports(icao)
);

CREATE TABLE IF NOT EXISTS complaint_forms (
  icao TEXT PRIMARY KEY,
  form_url TEXT,
  phone TEXT,
  email TEXT,
  notes TEXT,
  last_verified TEXT,
  FOREIGN KEY (icao) REFERENCES airports(icao)
);

CREATE TABLE IF NOT EXISTS aircraft_cache (
  icao24 TEXT PRIMARY KEY,
  registration TEXT,
  type_icao TEXT,
  type_description TEXT,
  operator TEXT,
  last_updated INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS visitor_activity (
  visitor_id TEXT PRIMARY KEY,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  ip_address TEXT,
  user_agent TEXT,
  path TEXT,
  airport_icao TEXT,
  user_lat REAL,
  user_lon REAL,
  submission_count INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (airport_icao) REFERENCES airports(icao)
);

CREATE TABLE IF NOT EXISTS submission_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  visitor_id TEXT,
  ip_address TEXT,
  user_agent TEXT,
  airport_icao TEXT,
  user_lat REAL,
  user_lon REAL,
  window_code TEXT,
  mode TEXT,
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  target_count INTEGER NOT NULL,
  FOREIGN KEY (airport_icao) REFERENCES airports(icao)
);

CREATE TABLE IF NOT EXISTS submission_aircraft (
  submission_id INTEGER NOT NULL,
  icao24 TEXT NOT NULL,
  callsign TEXT,
  registration TEXT,
  circles INTEGER NOT NULL DEFAULT 0,
  touch_and_gos INTEGER NOT NULL DEFAULT 0,
  low_approaches INTEGER NOT NULL DEFAULT 0,
  passes_over_user INTEGER NOT NULL DEFAULT 0,
  origin_airport_icao TEXT,
  origin_label TEXT,
  PRIMARY KEY (submission_id, icao24),
  FOREIGN KEY (submission_id) REFERENCES submission_events(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS aircraft_report_counts (
  icao24 TEXT PRIMARY KEY,
  callsign TEXT,
  registration TEXT,
  report_count INTEGER NOT NULL DEFAULT 0,
  first_reported_at INTEGER NOT NULL,
  last_reported_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submission_events_created_at ON submission_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_submission_events_ip ON submission_events(ip_address);
CREATE INDEX IF NOT EXISTS idx_submission_events_visitor ON submission_events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_submission_events_airport ON submission_events(airport_icao);
CREATE INDEX IF NOT EXISTS idx_visitor_activity_last_seen ON visitor_activity(last_seen DESC);

CREATE TABLE IF NOT EXISTS aircraft_registry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  n_number TEXT NOT NULL UNIQUE,
  icao_hex TEXT,
  serial_number TEXT,
  manufacturer TEXT,
  model TEXT,
  year_manufactured INTEGER,
  type_aircraft_code TEXT,
  type_aircraft_label TEXT,
  engine_type_code TEXT,
  engine_type_label TEXT,
  category_label TEXT,
  registrant_name TEXT,
  registrant_street TEXT,
  registrant_city TEXT,
  registrant_state TEXT,
  registrant_zip TEXT,
  registrant_country TEXT,
  registration_status_code TEXT,
  registration_status_label TEXT,
  certificate_issue_date TEXT,
  registration_expiration_date TEXT,
  owner_type TEXT,
  owner_type_confidence REAL,
  owner_type_reason TEXT,
  source TEXT NOT NULL DEFAULT 'FAA_AIRCRAFT_REGISTRY',
  source_updated_at TEXT,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aircraft_registry_icao_hex ON aircraft_registry(icao_hex);
CREATE INDEX IF NOT EXISTS idx_aircraft_registry_imported_at ON aircraft_registry(imported_at DESC);

CREATE TABLE IF NOT EXISTS aircraft_observed_identity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao_hex TEXT NOT NULL,
  observed_callsign TEXT,
  normalized_n_number TEXT,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 1,
  confidence REAL NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'ADS-B',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(icao_hex, observed_callsign)
);
CREATE INDEX IF NOT EXISTS idx_aircraft_observed_icao_hex ON aircraft_observed_identity(icao_hex);
CREATE INDEX IF NOT EXISTS idx_aircraft_observed_n_number ON aircraft_observed_identity(normalized_n_number);

CREATE TABLE IF NOT EXISTS aircraft_profile_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao_hex TEXT,
  n_number TEXT,
  profile_json TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  generated_at INTEGER NOT NULL,
  expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_aircraft_profile_cache_icao_hex ON aircraft_profile_cache(icao_hex);
CREATE INDEX IF NOT EXISTS idx_aircraft_profile_cache_n_number ON aircraft_profile_cache(n_number);
CREATE INDEX IF NOT EXISTS idx_aircraft_profile_cache_expires ON aircraft_profile_cache(expires_at);

-- Cold-tier track storage. Redis holds the recent ~4h hot window (capped to
-- protect the managed Valkey from OOM). This table archives older samples so
-- scans for the "today" window can still draw on a full 24h of history without
-- bloating Redis memory.
CREATE TABLE IF NOT EXISTS track_archive (
  icao24 TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  lat REAL,
  lon REAL,
  altitude_ft REAL,
  baro_altitude_ft REAL,
  geo_altitude_ft REAL,
  heading_deg REAL,
  vertical_rate_fpm REAL,
  callsign TEXT,
  in_window INTEGER NOT NULL DEFAULT 1,
  source TEXT,
  archived_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
  PRIMARY KEY (icao24, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_track_archive_timestamp ON track_archive(timestamp);
CREATE INDEX IF NOT EXISTS idx_track_archive_icao_ts ON track_archive(icao24, timestamp);

CREATE TABLE IF NOT EXISTS aircraft_registry_imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER NOT NULL,
  finished_at INTEGER,
  status TEXT NOT NULL,
  rows_imported INTEGER NOT NULL DEFAULT 0,
  rows_skipped INTEGER NOT NULL DEFAULT 0,
  rows_invalid INTEGER NOT NULL DEFAULT 0,
  source_url TEXT,
  source_date TEXT,
  duration_seconds REAL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_aircraft_registry_imports_started ON aircraft_registry_imports(started_at DESC);

CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY,
  icao TEXT NOT NULL,
  icao24 TEXT,
  callsign TEXT,
  registration TEXT,
  type TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  runway_id TEXT,
  runway_heading_deg REAL,
  turn_direction TEXT,
  min_altitude_ft_agl INTEGER,
  matched_pattern_id INTEGER,
  deviation_mean_nm REAL,
  deviation_peak_nm REAL,
  time_off_pattern_s INTEGER,
  time_total_s INTEGER,
  pct_off_pattern REAL,
  wind_from_deg INTEGER,
  wind_speed_kt REAL,
  headwind_kt REAL,
  origin_airport_icao TEXT,
  origin_label TEXT,
  operator TEXT,
  flight_school TEXT,
  created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
  FOREIGN KEY (icao) REFERENCES airports(icao)
);
CREATE INDEX IF NOT EXISTS idx_operations_icao_ts ON operations(icao, timestamp);
CREATE INDEX IF NOT EXISTS idx_operations_icao24 ON operations(icao24);
CREATE INDEX IF NOT EXISTS idx_operations_type ON operations(type);

CREATE TABLE IF NOT EXISTS runway_patterns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao TEXT NOT NULL,
  runway_id TEXT NOT NULL,
  geometry_json TEXT NOT NULL,
  name TEXT,
  version INTEGER NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1,
  locked INTEGER NOT NULL DEFAULT 0,
  editor_visitor_id TEXT,
  editor_ip TEXT,
  change_note TEXT,
  created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
  UNIQUE (icao, runway_id, version),
  FOREIGN KEY (icao, runway_id) REFERENCES runways(icao, runway_id)
);
CREATE INDEX IF NOT EXISTS idx_runway_patterns_current ON runway_patterns(icao, runway_id, is_current);

CREATE TABLE IF NOT EXISTS runway_flow (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao TEXT NOT NULL,
  active_runway_id TEXT NOT NULL,
  established_at INTEGER NOT NULL,
  ended_at INTEGER,
  wind_from_deg INTEGER,
  wind_speed_kt REAL,
  op_count INTEGER NOT NULL DEFAULT 0,  -- reserved; op counts are derived from the operations table, not maintained here

  FOREIGN KEY (icao) REFERENCES airports(icao)
);
CREATE INDEX IF NOT EXISTS idx_runway_flow_icao ON runway_flow(icao, established_at DESC);

CREATE TABLE IF NOT EXISTS runway_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao TEXT NOT NULL,
  from_runway_id TEXT,
  to_runway_id TEXT NOT NULL,
  changed_at INTEGER NOT NULL,
  cowboy_icao24 TEXT,
  cowboy_callsign TEXT,
  cowboy_registration TEXT,
  wind_from_deg INTEGER,
  wind_speed_kt REAL,
  wind_favored_new INTEGER,
  trigger_op_id TEXT,
  FOREIGN KEY (icao) REFERENCES airports(icao)
);
CREATE INDEX IF NOT EXISTS idx_runway_changes_icao ON runway_changes(icao, changed_at DESC);
"""


AIRPORT_SEED = [
    ("KBJC", "BJC", "Rocky Mountain Metropolitan Airport", "Broomfield, CO", "US", 39.9088, -105.1172, 5673, 1),
    ("KLMO", "LMO", "Vance Brand Airport", "Longmont, CO", "US", 40.1646, -105.1630, 5055, 0),
    ("KBDU", "WBU", "Boulder Municipal Airport", "Boulder, CO", "US", 40.0394, -105.2258, 5288, 0),
    ("KAPA", "APA", "Centennial Airport", "Englewood, CO", "US", 39.5701, -104.8493, 5885, 1),
    ("KCFO", "CFO", "Colorado Air and Space Port", "Watkins, CO", "US", 39.7853, -104.5431, 5515, 0),
    ("KDEN", "DEN", "Denver International Airport", "Denver, CO", "US", 39.8617, -104.6731, 5431, 1),
    ("KFNL", "FNL", "Northern Colorado Regional Airport", "Loveland, CO", "US", 40.4518, -105.0113, 5016, 1),
    ("KGXY", "GXY", "Greeley-Weld County Airport", "Greeley, CO", "US", 40.4374, -104.6332, 4697, 1),
    ("KCOS", "COS", "Colorado Springs Airport", "Colorado Springs, CO", "US", 38.8058, -104.7008, 6187, 1),
    ("KFTG", "FTG", "Front Range Airport", "Denver, CO", "US", 39.7853, -104.5431, 5515, 0),
]


RUNWAY_SEED = [
    ("KBJC", "12L", 39.9215, -105.1321, 120, 9000),
    ("KBJC", "30R", 39.8962, -105.1013, 300, 9000),
    ("KBJC", "12R", 39.9211, -105.1267, 120, 7002),
    ("KBJC", "30L", 39.9016, -105.1030, 300, 7002),
    ("KLMO", "11", 40.1702, -105.1775, 110, 4800),
    ("KLMO", "29", 40.1591, -105.1487, 290, 4800),
    ("KBDU", "08", 40.0391, -105.2387, 80, 4100),
    ("KBDU", "26", 40.0397, -105.2130, 260, 4100),
    ("KAPA", "17L", 39.5850, -104.8490, 170, 10001),
    ("KAPA", "35R", 39.5551, -104.8496, 350, 10001),
]


COMPLAINT_SEED = [
    ("KBJC", "https://www.jeffco.us/1710/Airport-Noise", None, None, "Airport noise information and reporting.", "2026-04-24"),
    ("KLMO", "https://www.longmontcolorado.gov/departments/departments-a-d/airport", None, None, "Use airport contact information if no dedicated form is available.", "2026-04-24"),
    ("KBDU", "https://bouldercolorado.gov/services/airport", None, None, "Use airport contact information if no dedicated form is available.", "2026-04-24"),
    ("KAPA", "https://www.centennialairport.com/noise-program/", None, None, "Centennial Airport noise program.", "2026-04-24"),
    ("KCFO", "https://www.adcogov.org/colorado-air-and-space-port", None, None, "Use airport contact information if no dedicated form is available.", "2026-04-24"),
    ("KDEN", "https://www.flydenver.com/about-den/community/noise/", None, None, "Denver International Airport noise program.", "2026-04-24"),
    ("KFNL", "https://www.flynoco.com/", None, None, "Use airport contact information if no dedicated form is available.", "2026-04-24"),
    ("KGXY", "https://greeleygov.com/services/airport", None, None, "Use airport contact information if no dedicated form is available.", "2026-04-24"),
    ("KCOS", "https://coloradosprings.gov/flycos", None, None, "Use airport contact information if no dedicated form is available.", "2026-04-24"),
    ("KFTG", "https://www.adcogov.org/colorado-air-and-space-port", None, None, "Use airport contact information if no dedicated form is available.", "2026-04-24"),
]


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # timeout: wait up to 5s for a write lock instead of immediately raising
    # "database is locked" (the worker's archive writes contend with API reads).
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL is set in SCHEMA, but a fresh connection still needs busy_timeout +
    # a sane synchronous level. NORMAL is durable enough under WAL and much
    # faster for the archive's high-frequency small writes.
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        seed_db(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(submission_aircraft)").fetchall()}
    additions = [
        ("circles", "INTEGER NOT NULL DEFAULT 0"),
        ("touch_and_gos", "INTEGER NOT NULL DEFAULT 0"),
        ("low_approaches", "INTEGER NOT NULL DEFAULT 0"),
        ("passes_over_user", "INTEGER NOT NULL DEFAULT 0"),
        ("origin_airport_icao", "TEXT"),
        ("origin_label", "TEXT"),
    ]
    for column, decl in additions:
        if column not in existing:
            conn.execute(f"ALTER TABLE submission_aircraft ADD COLUMN {column} {decl}")


def seed_db(conn: sqlite3.Connection) -> None:
    conn.executemany(
        """
        INSERT OR IGNORE INTO airports
        (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        AIRPORT_SEED,
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO runways
        (icao, runway_id, lat_threshold, lon_threshold, heading_deg, length_ft)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        RUNWAY_SEED,
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO complaint_forms
        (icao, form_url, phone, email, notes, last_verified)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        COMPLAINT_SEED,
    )


@contextmanager
def db_session(path: str) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
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


def operation_from_event(event: dict) -> dict:
    """Map a detector event dict to an `operations` row dict.

    Only the core (always-known) columns are populated here. Deviation, wind,
    and origin columns are filled in by later phases via targeted UPDATEs.
    """
    return {
        "id": event["id"],
        "icao": event.get("airport_icao"),
        "icao24": event.get("icao24"),
        "callsign": event.get("callsign"),
        "registration": event.get("registration"),
        "type": event.get("type"),
        "timestamp": int(event["timestamp"]),
        "runway_id": event.get("runway_id"),
        "runway_heading_deg": event.get("runway_heading_deg"),
        "turn_direction": event.get("turn_direction"),
        "min_altitude_ft_agl": event.get("min_altitude_ft_agl"),
    }


def upsert_operation(conn: sqlite3.Connection, op: dict) -> None:
    """Insert an operation row, ignoring rows whose id already exists.

    The event id is a stable sha256 hash of (type, icao24, airport, time-bucket),
    so DO NOTHING gives cross-restart, cross-monitor idempotency without
    clobbering enrichment columns (deviation/wind/origin) filled by later phases.
    """
    conn.execute(
        """
        INSERT INTO operations
          (id, icao, icao24, callsign, registration, type, timestamp,
           runway_id, runway_heading_deg, turn_direction, min_altitude_ft_agl)
        VALUES
          (:id, :icao, :icao24, :callsign, :registration, :type, :timestamp,
           :runway_id, :runway_heading_deg, :turn_direction, :min_altitude_ft_agl)
        ON CONFLICT(id) DO NOTHING
        """,
        op,
    )


def read_operations(
    conn: sqlite3.Connection,
    icao: str,
    start_ts: int,
    end_ts: int,
    types: list[str] | None = None,
) -> list[sqlite3.Row]:
    query = (
        "SELECT * FROM operations "
        "WHERE icao = ? AND timestamp >= ? AND timestamp <= ?"
    )
    args: list = [icao, start_ts, end_ts]
    if types:
        placeholders = ",".join("?" for _ in types)
        query += f" AND type IN ({placeholders})"
        args.extend(types)
    query += " ORDER BY timestamp ASC"
    return conn.execute(query, args).fetchall()


def persist_events(conn: sqlite3.Connection, events: Iterable[dict]) -> int:
    """Upsert each detector event as an operation row.

    Returns the number of events ATTEMPTED (events missing an id, airport, or type
    are skipped and not counted; duplicates ARE counted even though their insert is
    a no-op via ON CONFLICT). Idempotency is enforced by upsert_operation.
    """
    attempted = 0
    for event in events:
        if not event.get("id") or not event.get("airport_icao") or not event.get("type"):
            continue
        upsert_operation(conn, operation_from_event(event))
        attempted += 1
    return attempted


def update_operation_deviation(
    conn: sqlite3.Connection,
    op_id: str,
    matched_pattern_id: int | None,
    metrics: dict,
) -> None:
    conn.execute(
        """
        UPDATE operations SET
          matched_pattern_id = ?,
          deviation_mean_nm = ?,
          deviation_peak_nm = ?,
          time_off_pattern_s = ?,
          time_total_s = ?,
          pct_off_pattern = ?
        WHERE id = ?
        """,
        (
            matched_pattern_id,
            metrics["deviation_mean_nm"],
            metrics["deviation_peak_nm"],
            metrics["time_off_pattern_s"],
            metrics["time_total_s"],
            metrics["pct_off_pattern"],
            op_id,
        ),
    )


def update_operation_wind(conn: sqlite3.Connection, op_id: str,
                          wind_from_deg: int | None, wind_speed_kt: float | None,
                          headwind_kt: float | None) -> None:
    conn.execute(
        "UPDATE operations SET wind_from_deg = ?, wind_speed_kt = ?, headwind_kt = ? WHERE id = ?",
        (wind_from_deg, wind_speed_kt, headwind_kt, op_id),
    )


def current_flow(conn: sqlite3.Connection, icao: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runway_flow WHERE icao = ? AND ended_at IS NULL "
        "ORDER BY established_at DESC LIMIT 1",
        (icao.upper(),),
    ).fetchone()
    return dict(row) if row else None


def close_open_flow(conn: sqlite3.Connection, icao: str, ended_at: int) -> None:
    conn.execute(
        "UPDATE runway_flow SET ended_at = ? WHERE icao = ? AND ended_at IS NULL",
        (ended_at, icao.upper()),
    )


def open_flow(conn: sqlite3.Connection, icao: str, runway_id: str, established_at: int,
              wind_from_deg: int | None, wind_speed_kt: float | None) -> None:
    conn.execute(
        """
        INSERT INTO runway_flow (icao, active_runway_id, established_at, wind_from_deg, wind_speed_kt)
        VALUES (?, ?, ?, ?, ?)
        """,
        (icao.upper(), runway_id, established_at, wind_from_deg, wind_speed_kt),
    )


def insert_runway_change(conn: sqlite3.Connection, icao: str, from_runway_id: str | None,
                         to_runway_id: str, changed_at: int, cowboy: dict | None,
                         wind_from_deg: int | None, wind_speed_kt: float | None,
                         wind_favored_new: int) -> None:
    cowboy = cowboy or {}
    conn.execute(
        """
        INSERT INTO runway_changes
          (icao, from_runway_id, to_runway_id, changed_at, cowboy_icao24, cowboy_callsign,
           cowboy_registration, wind_from_deg, wind_speed_kt, wind_favored_new, trigger_op_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (icao.upper(), from_runway_id, to_runway_id, changed_at,
         cowboy.get("icao24"), cowboy.get("callsign"), cowboy.get("registration"),
         wind_from_deg, wind_speed_kt, wind_favored_new, cowboy.get("id")),
    )


def recent_runway_changes(conn: sqlite3.Connection, icao: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM runway_changes WHERE icao = ? ORDER BY changed_at DESC LIMIT ?",
        (icao.upper(), limit),
    ).fetchall()
    return [dict(row) for row in rows]


def airport_stats(conn: sqlite3.Connection, icao: str, start_ts: int, end_ts: int,
                  bucket_seconds: int = 86400, top: int = 10) -> dict:
    icao = icao.upper()
    win = (icao, start_ts, end_ts)

    type_counts = {row["type"]: row["n"] for row in conn.execute(
        "SELECT type, COUNT(*) AS n FROM operations WHERE icao=? AND timestamp BETWEEN ? AND ? GROUP BY type",
        win,
    ).fetchall()}
    unique_aircraft = conn.execute(
        "SELECT COUNT(DISTINCT icao24) AS n FROM operations WHERE icao=? AND timestamp BETWEEN ? AND ?", win,
    ).fetchone()["n"]
    change_count = conn.execute(
        "SELECT COUNT(*) AS n FROM runway_changes WHERE icao=? AND changed_at BETWEEN ? AND ?", win,
    ).fetchone()["n"]

    counters = {
        "circles": type_counts.get("circle", 0),
        "touch_and_gos": type_counts.get("touch_and_go", 0),
        "low_approaches": type_counts.get("low_approach", 0),
        "passes": type_counts.get("pass_over_user", 0),
        "unique_aircraft": unique_aircraft,
        "runway_changes": change_count,
    }

    bucket = max(1, int(bucket_seconds))
    ops_over_time = [
        {"bucket": row["bucket"], "count": row["n"]}
        for row in conn.execute(
            f"SELECT (timestamp/{bucket})*{bucket} AS bucket, COUNT(*) AS n "
            "FROM operations WHERE icao=? AND timestamp BETWEEN ? AND ? GROUP BY bucket ORDER BY bucket",
            win,
        ).fetchall()
    ]

    wind_row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN headwind_kt > 0 THEN 1 ELSE 0 END) AS into_hw, "
        "SUM(CASE WHEN headwind_kt <= 0 THEN 1 ELSE 0 END) AS downwind, "
        "SUM(CASE WHEN headwind_kt IS NULL THEN 1 ELSE 0 END) AS nodata "
        "FROM operations WHERE icao=? AND timestamp BETWEEN ? AND ?", win,
    ).fetchone()
    wind = {
        "into_headwind_ops": wind_row["into_hw"] or 0,
        "downwind_ops": wind_row["downwind"] or 0,
        "no_wind_data_ops": wind_row["nodata"] or 0,
    }

    dev_row = conn.execute(
        "SELECT AVG(deviation_mean_nm) AS avg_nm, MAX(deviation_mean_nm) AS max_nm, "
        "SUM(time_off_pattern_s) AS total_off, COUNT(*) AS scored "
        "FROM operations WHERE icao=? AND timestamp BETWEEN ? AND ? AND deviation_mean_nm IS NOT NULL", win,
    ).fetchone()
    worst = [dict(r) for r in conn.execute(
        "SELECT icao24, callsign, deviation_mean_nm FROM operations "
        "WHERE icao=? AND timestamp BETWEEN ? AND ? AND deviation_mean_nm IS NOT NULL "
        "ORDER BY deviation_mean_nm DESC LIMIT ?", (*win, top),
    ).fetchall()]
    deviation = {
        "scored_ops": dev_row["scored"] or 0,
        "avg_mean_nm": round(dev_row["avg_nm"], 3) if dev_row["avg_nm"] is not None else None,
        "max_nm": dev_row["max_nm"],
        "total_time_off_s": dev_row["total_off"] or 0,
        "worst": worst,
    }

    cowboys = [
        {"icao24": r["cowboy_icao24"], "callsign": r["cowboy_callsign"], "changes": r["n"]}
        for r in conn.execute(
            "SELECT cowboy_icao24, cowboy_callsign, COUNT(*) AS n FROM runway_changes "
            "WHERE icao=? AND changed_at BETWEEN ? AND ? AND cowboy_icao24 IS NOT NULL "
            "GROUP BY cowboy_icao24 ORDER BY n DESC LIMIT ?", (*win, top),
        ).fetchall()
    ]
    recent_changes = [dict(r) for r in conn.execute(
        "SELECT * FROM runway_changes WHERE icao=? AND changed_at BETWEEN ? AND ? "
        "ORDER BY changed_at DESC LIMIT ?", (*win, top),
    ).fetchall()]

    repeat_offenders = [dict(r) for r in conn.execute(
        "SELECT icao24, callsign, registration, report_count FROM aircraft_report_counts "
        "ORDER BY report_count DESC LIMIT ?", (top,),
    ).fetchall()]

    # Best-effort: link ops to the FAA registry by ICAO hex for an operator/owner
    # breakdown. icao_hex is stored UPPERCASE and indexed; comparing it to
    # upper(o.icao24) (the small, windowed, de-duped side) lets SQLite use
    # idx_aircraft_registry_icao_hex instead of full-scanning the ~312k-row
    # registry once per op (which previously hung the endpoint for busy airports).
    flight_schools = [
        {"label": r["label"], "count": r["n"]}
        for r in conn.execute(
            "SELECT reg.registrant_name AS label, COUNT(*) AS n "
            "FROM (SELECT DISTINCT icao24 FROM operations "
            "      WHERE icao=? AND timestamp BETWEEN ? AND ?) o "
            "JOIN aircraft_registry reg ON reg.icao_hex = upper(o.icao24) "
            "WHERE reg.registrant_name IS NOT NULL "
            "GROUP BY reg.registrant_name ORDER BY n DESC LIMIT ?", (*win, top),
        ).fetchall()
    ]

    return {
        "counters": counters,
        "ops_over_time": ops_over_time,
        "wind": wind,
        "deviation": deviation,
        "cowboys": cowboys,
        "recent_changes": recent_changes,
        "repeat_offenders": repeat_offenders,
        "flight_schools": flight_schools,
    }


def get_airport(conn: sqlite3.Connection, icao: str) -> Airport | None:
    row = conn.execute("SELECT * FROM airports WHERE icao = ?", (icao.upper(),)).fetchone()
    return row_to_airport(row) if row else None


def nearest_airport(conn: sqlite3.Connection, lat: float, lon: float) -> dict | None:
    """Nearest seeded airport to (lat, lon).

    Pre-filters by a coarse lat/lon bounding box BEFORE doing Python haversine —
    the airports table now has ~16k US rows (was 10), and scanning all of them
    per call was the dominant cost of the scan endpoint (enrich_offenders →
    origin_from_ground_track called this per track-sample, hitting CPU 200%
    for 30+ seconds). The bbox prefilter cuts to a handful of candidates;
    expand the search if no rows match the tight box.
    """
    point = Point(lat, lon)
    for box_deg in (0.6, 2.0, 8.0, 180.0):
        rows = conn.execute(
            """
            SELECT * FROM airports
            WHERE lat BETWEEN ? AND ?
              AND lon BETWEEN ? AND ?
            """,
            (lat - box_deg, lat + box_deg, lon - box_deg, lon + box_deg),
        ).fetchall()
        if rows:
            candidates = [row_to_airport(row) for row in rows]
            nearest = min(candidates, key=lambda a: distance_nm(point, Point(a.lat, a.lon)))
            data = airport_to_dict(nearest)
            data["distance_nm"] = round(distance_nm(point, Point(nearest.lat, nearest.lon)), 2)
            return data
    return None


def search_airports(conn: sqlite3.Connection, q: str, limit: int = 10) -> list[dict]:
    like = f"%{q.strip()}%"
    rows = conn.execute(
        """
        SELECT * FROM airports
        WHERE icao LIKE ? OR iata LIKE ? OR name LIKE ? OR city LIKE ?
        ORDER BY is_towered DESC, icao ASC
        LIMIT ?
        """,
        (like, like, like, like, limit),
    ).fetchall()
    return [airport_to_dict(row_to_airport(row)) for row in rows]


def airport_to_dict(airport: Airport) -> dict:
    return {
        "icao": airport.icao,
        "iata": airport.iata,
        "name": airport.name,
        "city": airport.city,
        "country": airport.country,
        "lat": airport.lat,
        "lon": airport.lon,
        "elevation_ft": airport.elevation_ft,
        "is_towered": airport.is_towered,
    }


def runways_for_airport(conn: sqlite3.Connection, icao: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM runways WHERE icao = ? ORDER BY runway_id",
        (icao.upper(),),
    ).fetchall()
    return [dict(row) for row in rows]


def get_runway(conn: sqlite3.Connection, icao: str, runway_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runways WHERE icao = ? AND runway_id = ?",
        (icao.upper(), runway_id),
    ).fetchone()
    return dict(row) if row else None


def save_runway_pattern(
    conn: sqlite3.Connection,
    icao: str,
    runway_id: str,
    geometry_json: str,
    name: str | None = None,
    editor_visitor_id: str | None = None,
    editor_ip: str | None = None,
    change_note: str | None = None,
) -> dict:
    """Append a new pattern version and make it the current one."""
    icao = icao.upper()
    row = conn.execute(
        "SELECT MAX(version) AS v FROM runway_patterns WHERE icao = ? AND runway_id = ?",
        (icao, runway_id),
    ).fetchone()
    next_version = (row["v"] or 0) + 1
    conn.execute(
        "UPDATE runway_patterns SET is_current = 0 WHERE icao = ? AND runway_id = ?",
        (icao, runway_id),
    )
    conn.execute(
        """
        INSERT INTO runway_patterns
          (icao, runway_id, geometry_json, name, version, is_current,
           editor_visitor_id, editor_ip, change_note)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (icao, runway_id, geometry_json, name, next_version,
         editor_visitor_id, editor_ip, change_note),
    )
    return get_current_pattern(conn, icao, runway_id)


def get_current_pattern(conn: sqlite3.Connection, icao: str, runway_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runway_patterns WHERE icao = ? AND runway_id = ? AND is_current = 1",
        (icao.upper(), runway_id),
    ).fetchone()
    return dict(row) if row else None


def current_patterns_for_airport(conn: sqlite3.Connection, icao: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM runway_patterns WHERE icao = ? AND is_current = 1 ORDER BY runway_id",
        (icao.upper(),),
    ).fetchall()
    return [dict(row) for row in rows]


def list_pattern_versions(conn: sqlite3.Connection, icao: str, runway_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, version, name, is_current, locked, editor_visitor_id, change_note, created_at
        FROM runway_patterns
        WHERE icao = ? AND runway_id = ?
        ORDER BY version DESC
        """,
        (icao.upper(), runway_id),
    ).fetchall()
    return [dict(row) for row in rows]


def revert_pattern(
    conn: sqlite3.Connection,
    icao: str,
    runway_id: str,
    version: int,
    editor_visitor_id: str | None = None,
    editor_ip: str | None = None,
) -> dict | None:
    """Clone an earlier version's geometry into a new current version."""
    src = conn.execute(
        "SELECT geometry_json, name FROM runway_patterns WHERE icao = ? AND runway_id = ? AND version = ?",
        (icao.upper(), runway_id, version),
    ).fetchone()
    if src is None:
        return None
    return save_runway_pattern(
        conn, icao, runway_id, src["geometry_json"],
        name=src["name"],
        editor_visitor_id=editor_visitor_id,
        editor_ip=editor_ip,
        change_note=f"revert to v{version}",
    )


def count_recent_pattern_edits(
    conn: sqlite3.Connection,
    editor_visitor_id: str | None,
    editor_ip: str | None,
    since_ts: int,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM runway_patterns
        WHERE created_at >= ?
          AND ((editor_visitor_id IS NOT NULL AND editor_visitor_id = ?)
               OR (editor_ip IS NOT NULL AND editor_ip = ?))
        """,
        (since_ts, editor_visitor_id, editor_ip),
    ).fetchone()
    return row["c"]


def complaint_form(conn: sqlite3.Connection, icao: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM complaint_forms WHERE icao = ?",
        (icao.upper(),),
    ).fetchone()
    return dict(row) if row else None


def aircraft_detail(conn: sqlite3.Connection, icao24: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM aircraft_cache WHERE icao24 = ?",
        (icao24.lower(),),
    ).fetchone()
    return dict(row) if row else None


def normalize_icao24(icao24: str) -> str:
    return icao24.strip().lower()


def normalized_aircraft_targets(targets: list[dict]) -> list[dict]:
    normalized: dict[str, dict] = {}
    for target in targets:
        icao24 = normalize_icao24(str(target.get("icao24", "")))
        if not icao24:
            continue
        callsign = str(target.get("callsign") or "").strip() or None
        normalized[icao24] = {"icao24": icao24, "callsign": callsign}
    return list(normalized.values())


def record_visitor_activity(
    conn: sqlite3.Connection,
    *,
    visitor_id: str,
    now: int,
    ip_address: str | None,
    user_agent: str | None,
    path: str | None,
    airport_icao: str | None,
    user_lat: float | None,
    user_lon: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO visitor_activity
        (visitor_id, first_seen, last_seen, ip_address, user_agent, path, airport_icao, user_lat, user_lon, submission_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(visitor_id) DO UPDATE SET
          last_seen = excluded.last_seen,
          ip_address = excluded.ip_address,
          user_agent = excluded.user_agent,
          path = excluded.path,
          airport_icao = excluded.airport_icao,
          user_lat = excluded.user_lat,
          user_lon = excluded.user_lon
        """,
        (
            visitor_id,
            now,
            now,
            ip_address,
            user_agent,
            path,
            airport_icao.upper() if airport_icao else None,
            user_lat,
            user_lon,
        ),
    )


def record_submission(
    conn: sqlite3.Connection,
    *,
    now: int,
    visitor_id: str | None,
    ip_address: str | None,
    user_agent: str | None,
    airport_icao: str | None,
    user_lat: float | None,
    user_lon: float | None,
    window_code: str | None,
    mode: str | None,
    text: str,
    text_hash: str,
    targets: list[dict],
) -> int:
    normalized_targets = normalized_aircraft_targets(targets)
    if visitor_id:
        record_visitor_activity(
            conn,
            visitor_id=visitor_id,
            now=now,
            ip_address=ip_address,
            user_agent=user_agent,
            path="/",
            airport_icao=airport_icao,
            user_lat=user_lat,
            user_lon=user_lon,
        )
        conn.execute(
            "UPDATE visitor_activity SET submission_count = submission_count + 1 WHERE visitor_id = ?",
            (visitor_id,),
        )
    cursor = conn.execute(
        """
        INSERT INTO submission_events
        (created_at, visitor_id, ip_address, user_agent, airport_icao, user_lat, user_lon, window_code, mode, text, text_hash, target_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            visitor_id,
            ip_address,
            user_agent,
            airport_icao.upper() if airport_icao else None,
            user_lat,
            user_lon,
            window_code,
            mode,
            text,
            text_hash,
            len(normalized_targets),
        ),
    )
    submission_id = int(cursor.lastrowid)
    for target in normalized_targets:
        icao24 = target["icao24"]
        cache = aircraft_detail(conn, icao24) or {}
        registration = cache.get("registration")
        callsign = target["callsign"]
        conn.execute(
            """
            INSERT INTO submission_aircraft
            (submission_id, icao24, callsign, registration,
             circles, touch_and_gos, low_approaches, passes_over_user,
             origin_airport_icao, origin_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                icao24,
                callsign,
                registration,
                int(target.get("circles") or 0),
                int(target.get("touch_and_gos") or 0),
                int(target.get("low_approaches") or 0),
                int(target.get("passes_over_user") or 0),
                target.get("origin_airport_icao"),
                target.get("origin_label"),
            ),
        )
        conn.execute(
            """
            INSERT INTO aircraft_report_counts
            (icao24, callsign, registration, report_count, first_reported_at, last_reported_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(icao24) DO UPDATE SET
              callsign = COALESCE(excluded.callsign, aircraft_report_counts.callsign),
              registration = COALESCE(excluded.registration, aircraft_report_counts.registration),
              report_count = aircraft_report_counts.report_count + 1,
              last_reported_at = excluded.last_reported_at
            """,
            (icao24, callsign, registration, now, now),
        )
    return submission_id


def top_repeat_offenders(conn: sqlite3.Connection, *, min_reports: int, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
          r.icao24,
          r.callsign,
          COALESCE(r.registration, c.registration) AS registration,
          c.type_icao,
          c.type_description,
          c.operator,
          r.report_count,
          r.first_reported_at,
          r.last_reported_at,
          COALESCE(agg.total_circles, 0) AS total_circles,
          COALESCE(agg.total_touch_and_gos, 0) AS total_touch_and_gos,
          COALESCE(agg.total_low_approaches, 0) AS total_low_approaches,
          COALESCE(agg.total_passes_over_user, 0) AS total_passes_over_user,
          (
            SELECT sa2.origin_airport_icao
            FROM submission_aircraft sa2
            JOIN submission_events se2 ON se2.id = sa2.submission_id
            WHERE sa2.icao24 = r.icao24 AND sa2.origin_airport_icao IS NOT NULL
            ORDER BY se2.created_at DESC
            LIMIT 1
          ) AS origin_airport_icao,
          (
            SELECT sa3.origin_label
            FROM submission_aircraft sa3
            JOIN submission_events se3 ON se3.id = sa3.submission_id
            WHERE sa3.icao24 = r.icao24 AND sa3.origin_label IS NOT NULL
            ORDER BY se3.created_at DESC
            LIMIT 1
          ) AS origin_label
        FROM aircraft_report_counts r
        LEFT JOIN aircraft_cache c ON c.icao24 = r.icao24
        LEFT JOIN (
          SELECT
            icao24,
            SUM(circles) AS total_circles,
            SUM(touch_and_gos) AS total_touch_and_gos,
            SUM(low_approaches) AS total_low_approaches,
            SUM(passes_over_user) AS total_passes_over_user
          FROM submission_aircraft
          GROUP BY icao24
        ) agg ON agg.icao24 = r.icao24
        WHERE r.report_count >= ?
        ORDER BY total_circles DESC, total_passes_over_user DESC, r.report_count DESC, r.last_reported_at DESC
        LIMIT ?
        """,
        (max(1, int(min_reports)), max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def _aircraft_for_submissions(conn: sqlite3.Connection, submission_ids: list[int]) -> dict[int, list[dict]]:
    if not submission_ids:
        return {}
    placeholders = ",".join("?" for _ in submission_ids)
    rows = conn.execute(
        f"""
        SELECT submission_id, icao24, callsign, registration
        FROM submission_aircraft
        WHERE submission_id IN ({placeholders})
        ORDER BY submission_id DESC, callsign COLLATE NOCASE, icao24
        """,
        submission_ids,
    ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["submission_id"], []).append({
            "icao24": row["icao24"],
            "callsign": row["callsign"],
            "registration": row["registration"],
        })
    return grouped


def admin_dashboard(conn: sqlite3.Connection, *, now: int, active_window_seconds: int) -> dict:
    active_since = now - active_window_seconds
    summary = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM submission_events) AS submissions,
          (SELECT COALESCE(SUM(report_count), 0) FROM aircraft_report_counts) AS aircraft_reports,
          (SELECT COUNT(*) FROM aircraft_report_counts) AS distinct_aircraft,
          (SELECT COUNT(DISTINCT COALESCE(visitor_id, ip_address)) FROM submission_events) AS submitters,
          (SELECT COUNT(*) FROM visitor_activity WHERE last_seen >= ?) AS current_users,
          (SELECT COUNT(DISTINCT airport_icao) FROM submission_events WHERE airport_icao IS NOT NULL) AS airports
        """,
        (active_since,),
    ).fetchone()

    submission_rows = conn.execute(
        """
        SELECT
          s.id,
          s.created_at,
          s.visitor_id,
          s.ip_address,
          s.airport_icao,
          a.name AS airport_name,
          a.city AS airport_city,
          s.user_lat,
          s.user_lon,
          s.window_code,
          s.mode,
          s.text,
          s.text_hash,
          s.target_count
        FROM submission_events s
        LEFT JOIN airports a ON a.icao = s.airport_icao
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT 100
        """
    ).fetchall()
    submission_ids = [row["id"] for row in submission_rows]
    aircraft_by_submission = _aircraft_for_submissions(conn, submission_ids)
    recent_submissions = [
        {
            **dict(row),
            "aircraft": aircraft_by_submission.get(row["id"], []),
        }
        for row in submission_rows
    ]

    aircraft_reports = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              r.icao24,
              r.callsign,
              COALESCE(r.registration, c.registration) AS registration,
              c.type_icao,
              c.operator,
              r.report_count,
              r.first_reported_at,
              r.last_reported_at
            FROM aircraft_report_counts r
            LEFT JOIN aircraft_cache c ON c.icao24 = r.icao24
            ORDER BY r.report_count DESC, r.last_reported_at DESC
            LIMIT 100
            """
        ).fetchall()
    ]

    current_users = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              v.visitor_id,
              v.first_seen,
              v.last_seen,
              v.ip_address,
              v.path,
              v.airport_icao,
              a.city AS airport_city,
              v.user_lat,
              v.user_lon,
              v.submission_count
            FROM visitor_activity v
            LEFT JOIN airports a ON a.icao = v.airport_icao
            WHERE v.last_seen >= ?
            ORDER BY v.last_seen DESC
            """,
            (active_since,),
        ).fetchall()
    ]

    ip_history = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              s.ip_address,
              COUNT(*) AS submissions,
              MIN(s.created_at) AS first_submission_at,
              MAX(s.created_at) AS last_submission_at,
              COUNT(DISTINCT s.visitor_id) AS visitors,
              SUM(CASE WHEN v.last_seen >= ? THEN 1 ELSE 0 END) AS active_visitors
            FROM submission_events s
            LEFT JOIN visitor_activity v ON v.visitor_id = s.visitor_id
            WHERE s.ip_address IS NOT NULL
            GROUP BY s.ip_address
            ORDER BY submissions DESC, last_submission_at DESC
            LIMIT 100
            """,
            (active_since,),
        ).fetchall()
    ]

    locations = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              v.visitor_id,
              v.ip_address,
              v.airport_icao,
              a.city AS airport_city,
              v.user_lat,
              v.user_lon,
              v.first_seen,
              v.last_seen,
              v.submission_count
            FROM visitor_activity v
            LEFT JOIN airports a ON a.icao = v.airport_icao
            WHERE v.user_lat IS NOT NULL AND v.user_lon IS NOT NULL
            ORDER BY v.submission_count DESC, v.last_seen DESC
            LIMIT 100
            """
        ).fetchall()
    ]

    airports = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              s.airport_icao,
              a.name,
              a.city,
              COUNT(*) AS submissions,
              COUNT(DISTINCT COALESCE(s.visitor_id, s.ip_address)) AS submitters,
              MAX(s.created_at) AS last_submission_at
            FROM submission_events s
            LEFT JOIN airports a ON a.icao = s.airport_icao
            WHERE s.airport_icao IS NOT NULL
            GROUP BY s.airport_icao
            ORDER BY submissions DESC, last_submission_at DESC
            """
        ).fetchall()
    ]

    return {
        "generated_at": now,
        "active_window_seconds": active_window_seconds,
        "summary": dict(summary),
        "current_users": current_users,
        "recent_submissions": recent_submissions,
        "aircraft_reports": aircraft_reports,
        "ip_history": ip_history,
        "locations": locations,
        "airports": airports,
    }


def backup_database(source_path: str, backup_dir: str) -> str:
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)
    dest = Path(backup_dir) / "circlejerk.sqlite3.backup"
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return str(dest)


# --- FAA aircraft registry accessors ------------------------------------------------

AIRCRAFT_REGISTRY_COLUMNS = (
    "n_number", "icao_hex", "serial_number", "manufacturer", "model",
    "year_manufactured", "type_aircraft_code", "type_aircraft_label",
    "engine_type_code", "engine_type_label", "category_label",
    "registrant_name", "registrant_street", "registrant_city",
    "registrant_state", "registrant_zip", "registrant_country",
    "registration_status_code", "registration_status_label",
    "certificate_issue_date", "registration_expiration_date",
    "owner_type", "owner_type_confidence", "owner_type_reason",
    "source", "source_updated_at",
)


def upsert_aircraft_registry(conn: sqlite3.Connection, row: dict) -> None:
    """UPSERT a single FAA registry row keyed on n_number."""
    columns = list(AIRCRAFT_REGISTRY_COLUMNS)
    values = [row.get(col) for col in columns]
    placeholders = ", ".join(["?"] * len(columns))
    updates = ", ".join(
        f"{col}=excluded.{col}" for col in columns if col != "n_number"
    )
    conn.execute(
        f"""
        INSERT INTO aircraft_registry ({', '.join(columns)}, imported_at, updated_at)
        VALUES ({placeholders}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(n_number) DO UPDATE SET
          {updates},
          imported_at = excluded.imported_at,
          updated_at = CURRENT_TIMESTAMP
        """,
        values,
    )


def get_registry_by_n_number(conn: sqlite3.Connection, n_number: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM aircraft_registry WHERE n_number = ? LIMIT 1",
        (n_number.upper(),),
    ).fetchone()
    return dict(row) if row else None


def get_registry_by_icao_hex(conn: sqlite3.Connection, icao_hex: str) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM aircraft_registry
        WHERE icao_hex = ?
        ORDER BY imported_at DESC
        LIMIT 1
        """,
        (icao_hex.upper(),),
    ).fetchone()
    return dict(row) if row else None


def search_aircraft_registry(
    conn: sqlite3.Connection, prefix: str, limit: int = 10
) -> list[dict]:
    """Search registry by N-number prefix (returns lightweight rows)."""
    rows = conn.execute(
        """
        SELECT n_number, icao_hex, manufacturer, model, year_manufactured,
               registrant_name, registrant_city, registrant_state, owner_type
        FROM aircraft_registry
        WHERE n_number LIKE ?
        ORDER BY n_number ASC
        LIMIT ?
        """,
        (f"{prefix.upper()}%", limit),
    ).fetchall()
    return [dict(row) for row in rows]


def upsert_observed_identity(
    conn: sqlite3.Connection,
    icao_hex: str,
    callsign: str | None,
    normalized_n_number: str | None,
    timestamp: int,
    confidence: float,
) -> None:
    """Record an ADS-B sighting of (icao_hex, callsign) and increment counts."""
    cs = callsign.strip().upper() if callsign else None
    conn.execute(
        """
        INSERT INTO aircraft_observed_identity (
          icao_hex, observed_callsign, normalized_n_number,
          first_seen_at, last_seen_at, observation_count, confidence
        )
        VALUES (?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(icao_hex, observed_callsign) DO UPDATE SET
          normalized_n_number = COALESCE(excluded.normalized_n_number, aircraft_observed_identity.normalized_n_number),
          last_seen_at = MAX(aircraft_observed_identity.last_seen_at, excluded.last_seen_at),
          observation_count = aircraft_observed_identity.observation_count + 1,
          confidence = MAX(aircraft_observed_identity.confidence, excluded.confidence),
          updated_at = CURRENT_TIMESTAMP
        """,
        (icao_hex.upper(), cs, normalized_n_number, timestamp, timestamp, confidence),
    )


def get_observed_identity(conn: sqlite3.Connection, icao_hex: str) -> dict | None:
    """Latest known mapping for an ICAO hex (newest callsign / highest confidence)."""
    row = conn.execute(
        """
        SELECT * FROM aircraft_observed_identity
        WHERE icao_hex = ?
        ORDER BY normalized_n_number IS NULL ASC,
                 confidence DESC,
                 last_seen_at DESC
        LIMIT 1
        """,
        (icao_hex.upper(),),
    ).fetchone()
    return dict(row) if row else None


def latest_registry_import(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM aircraft_registry_imports ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def record_import_start(
    conn: sqlite3.Connection, *, started_at: int, source_url: str | None
) -> int:
    cur = conn.execute(
        """
        INSERT INTO aircraft_registry_imports (started_at, status, source_url)
        VALUES (?, 'running', ?)
        """,
        (started_at, source_url),
    )
    return int(cur.lastrowid)


def record_import_finish(
    conn: sqlite3.Connection,
    *,
    import_id: int,
    finished_at: int,
    status: str,
    rows_imported: int,
    rows_skipped: int,
    rows_invalid: int,
    duration_seconds: float,
    source_date: str | None,
    error: str | None,
) -> None:
    conn.execute(
        """
        UPDATE aircraft_registry_imports
        SET finished_at = ?, status = ?, rows_imported = ?,
            rows_skipped = ?, rows_invalid = ?, duration_seconds = ?,
            source_date = COALESCE(?, source_date), error = ?
        WHERE id = ?
        """,
        (
            finished_at, status, rows_imported, rows_skipped, rows_invalid,
            duration_seconds, source_date, error, import_id,
        ),
    )


def aircraft_registry_row_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM aircraft_registry").fetchone()
    return int(row["c"]) if row else 0


# --- Track archive (cold tier) ----------------------------------------------

_TRACK_ARCHIVE_COLUMNS = (
    "icao24", "timestamp", "lat", "lon",
    "altitude_ft", "baro_altitude_ft", "geo_altitude_ft",
    "heading_deg", "vertical_rate_fpm",
    "callsign", "in_window", "source",
)


def archive_track_samples(
    conn: sqlite3.Connection,
    icao24: str,
    samples: list[dict],
) -> int:
    """Bulk-INSERT track samples into the cold archive. Returns rows written.

    INSERT OR IGNORE — if (icao24, timestamp) already exists, the existing
    archived row wins. Safe to call with samples that overlap with previously
    archived data.
    """
    if not samples:
        return 0
    icao_lower = icao24.lower()
    rows = []
    for sample in samples:
        ts = sample.get("timestamp")
        if ts is None:
            continue
        rows.append((
            icao_lower,
            int(ts),
            sample.get("lat"),
            sample.get("lon"),
            sample.get("altitude_ft"),
            sample.get("baro_altitude_ft"),
            sample.get("geo_altitude_ft"),
            sample.get("heading_deg"),
            sample.get("vertical_rate_fpm"),
            sample.get("callsign"),
            1 if sample.get("in_window", True) else 0,
            sample.get("source"),
        ))
    if not rows:
        return 0
    cur = conn.executemany(
        f"""
        INSERT OR IGNORE INTO track_archive
        ({', '.join(_TRACK_ARCHIVE_COLUMNS)}, archived_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(strftime('%s','now') AS INTEGER))
        """,
        rows,
    )
    return cur.rowcount or 0


def read_track_archive(
    conn: sqlite3.Connection,
    icao24: str,
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT {', '.join(_TRACK_ARCHIVE_COLUMNS)}
        FROM track_archive
        WHERE icao24 = ? AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp ASC
        """,
        (icao24.lower(), int(start_ts), int(end_ts)),
    ).fetchall()
    return [_track_archive_row_to_sample(row) for row in rows]


def bulk_read_track_archive(
    conn: sqlite3.Connection,
    icao24s: list[str],
    start_ts: int,
    end_ts: int,
) -> dict[str, list[dict]]:
    """Read archived samples for many aircraft at once."""
    if not icao24s:
        return {}
    icao_lower = [i.lower() for i in icao24s]
    out: dict[str, list[dict]] = {i: [] for i in icao_lower}
    # SQLite has a 999-parameter limit; chunk.
    chunk_size = 500
    for offset in range(0, len(icao_lower), chunk_size):
        chunk = icao_lower[offset : offset + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT {', '.join(_TRACK_ARCHIVE_COLUMNS)}
            FROM track_archive
            WHERE icao24 IN ({placeholders}) AND timestamp BETWEEN ? AND ?
            ORDER BY icao24 ASC, timestamp ASC
            """,
            (*chunk, int(start_ts), int(end_ts)),
        ).fetchall()
        for row in rows:
            sample = _track_archive_row_to_sample(row)
            out[row["icao24"]].append(sample)
    return out


def prune_track_archive(conn: sqlite3.Connection, older_than_ts: int) -> int:
    """Delete archived samples older than the cutoff. Returns rows deleted."""
    cur = conn.execute(
        "DELETE FROM track_archive WHERE timestamp < ?",
        (int(older_than_ts),),
    )
    return cur.rowcount or 0


def list_archive_aircraft(
    conn: sqlite3.Connection,
    start_ts: int,
    end_ts: int,
) -> list[str]:
    """Distinct icao24s with archived samples in [start_ts, end_ts]."""
    rows = conn.execute(
        """
        SELECT DISTINCT icao24
        FROM track_archive
        WHERE timestamp BETWEEN ? AND ?
        """,
        (int(start_ts), int(end_ts)),
    ).fetchall()
    return [row["icao24"] for row in rows]


def track_archive_stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS samples,
               COUNT(DISTINCT icao24) AS aircraft,
               MIN(timestamp) AS oldest_ts,
               MAX(timestamp) AS newest_ts
        FROM track_archive
        """
    ).fetchone()
    return dict(row) if row else {}


def _track_archive_row_to_sample(row: sqlite3.Row) -> dict:
    # `icao24` is duplicated into the sample dict (it's the table PK + an
    # in-sample field) because downstream code (detectors, services) reads
    # `sample["icao24"]` inline rather than carrying the icao24 alongside.
    return {
        "icao24": row["icao24"],
        "timestamp": int(row["timestamp"]),
        "lat": row["lat"],
        "lon": row["lon"],
        "altitude_ft": row["altitude_ft"],
        "baro_altitude_ft": row["baro_altitude_ft"],
        "geo_altitude_ft": row["geo_altitude_ft"],
        "heading_deg": row["heading_deg"],
        "vertical_rate_fpm": row["vertical_rate_fpm"],
        "callsign": row["callsign"],
        "in_window": bool(row["in_window"]),
        "source": row["source"],
    }

