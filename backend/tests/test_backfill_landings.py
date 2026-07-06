from __future__ import annotations

from app import db
from scripts.backfill_landings import backfill_landings
from tests.test_operations import landing_then_silence_track, seeded_conn


def test_backfill_emits_landing_from_archive(tmp_path):
    conn = seeded_conn(tmp_path / "b.sqlite3")
    t0 = 18000
    now = t0 + 4000  # well past the 5-min settle window
    # Archive a landing track for an aircraft near the KBJC field.
    db.archive_track_samples(conn, "land01", landing_then_silence_track(t0=t0, icao24="land01"))
    conn.commit()

    stats = backfill_landings(conn, "KBJC", now=now, lookback_s=86400)

    rows = db.read_operations(conn, "KBJC", 0, now, types=["landing"])
    assert len(rows) == 1
    assert stats["landings"] >= 1


def test_backfill_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "b.sqlite3")
    t0 = 18000
    now = t0 + 4000
    db.archive_track_samples(conn, "land01", landing_then_silence_track(t0=t0, icao24="land01"))
    conn.commit()

    backfill_landings(conn, "KBJC", now=now)
    backfill_landings(conn, "KBJC", now=now)  # second run must not duplicate

    rows = db.read_operations(conn, "KBJC", 0, now, types=["landing"])
    assert len(rows) == 1
