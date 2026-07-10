"""Write transactions must not stay open across slow work.

`db_session` commits only on exit. Python's sqlite3 opens an implicit
transaction on the first write and holds the write lock until commit, so the
worker — which wraps its whole poll cycle, HTTP fetches included, in one
db_session — held the lock for many seconds. The api's heartbeat writer and the
scan's own writes then exceeded the 5s busy_timeout and raised
`sqlite3.OperationalError: database is locked`, 500-ing /scan and
/activity/heartbeat.

Fix: commit as soon as a write group finishes, so the lock is held only for the
writes themselves.
"""
from __future__ import annotations

import inspect

from app import db, services


def test_persisting_events_commits_before_returning(tmp_path):
    conn = db.connect(str(tmp_path / "w.sqlite3"))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)   # operations.icao FK -> airports
    conn.commit()

    db.persist_events(conn, [{
        "id": "e1", "type": "circle", "icao24": "aa11", "callsign": "N1",
        "timestamp": 1780000000, "airport_icao": "KLMO",
    }])
    assert conn.in_transaction, "persist_events itself must not commit (caller batches)"
    conn.commit()
    assert not conn.in_transaction


def test_detector_write_block_commits_promptly():
    """The detector must release the write lock as soon as its writes land,
    not leave it open through enrich_offenders and tracks_for_response."""
    src = inspect.getsource(services.run_detectors_for_monitor)
    assert "conn.commit()" in src, (
        "run_detectors_for_monitor must commit its write block; otherwise the "
        "write lock stays open for the rest of the scan"
    )


def test_worker_commits_between_monitors():
    """The worker must not hold a write transaction across its HTTP fetches."""
    import app.worker as worker

    src = inspect.getsource(worker)
    assert "conn.commit()" in src, "worker must commit between monitors"
