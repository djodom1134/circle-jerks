from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

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
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        seed_db(conn)
        conn.commit()
    finally:
        conn.close()


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


def get_airport(conn: sqlite3.Connection, icao: str) -> Airport | None:
    row = conn.execute("SELECT * FROM airports WHERE icao = ?", (icao.upper(),)).fetchone()
    return row_to_airport(row) if row else None


def nearest_airport(conn: sqlite3.Connection, lat: float, lon: float) -> dict | None:
    point = Point(lat, lon)
    candidates = [row_to_airport(row) for row in conn.execute("SELECT * FROM airports")]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda airport: distance_nm(point, Point(airport.lat, airport.lon)))
    data = airport_to_dict(nearest)
    data["distance_nm"] = round(distance_nm(point, Point(nearest.lat, nearest.lon)), 2)
    return data


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
            INSERT INTO submission_aircraft (submission_id, icao24, callsign, registration)
            VALUES (?, ?, ?, ?)
            """,
            (submission_id, icao24, callsign, registration),
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
          r.last_reported_at
        FROM aircraft_report_counts r
        LEFT JOIN aircraft_cache c ON c.icao24 = r.icao24
        WHERE r.report_count >= ?
        ORDER BY r.report_count DESC, r.last_reported_at DESC
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
