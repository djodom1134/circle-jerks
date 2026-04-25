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

