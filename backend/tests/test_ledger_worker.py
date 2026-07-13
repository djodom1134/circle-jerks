"""The nightly ledger worker: keeps `daily_operation_rollup` and
`aircraft_home_base` warm without ever taking the production worker down.

Both tables are DERIVED CACHES rebuilt from `operations` (never pruned, never
modified here). `ledger_tick` is the one-pass unit of work; `_ledger_loop`
wraps it in a `while True` + heartbeat, following the same shape as
`_ingest_loop` / `_detect_loop` / `archive_loop` elsewhere in this file: the
tick's own exceptions are swallowed so one bad night can't stop the next one.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app import db, ledger, worker
from app.settings import Settings
from app.store import MemoryStore

BASE = 1780336800  # 2026-06-01 12:00:00 -06:00 (America/Denver) at KLMO
DAY = 86400


def _seed(path: str) -> None:
    db.init_db(path)
    conn = db.connect(path)
    for i in range(3):
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"g{i}", "type": "touch_and_go", "icao24": "aaa111",
            "callsign": "N111AA", "timestamp": BASE + i * 60, "airport_icao": "KLMO",
        }))
    conn.commit()
    conn.close()


def test_ledger_tick_rolls_up_and_classifies(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    _seed(path)

    settings = Settings(database_path=path)
    result = worker.ledger_tick(settings, now_ts=BASE + DAY)

    assert result["rollup_rows"] >= 1
    assert result["homebase_rows"] == 1

    conn = db.connect(path)
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 3
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM aircraft_home_base WHERE icao='KLMO'"
    ).fetchone()["n"] == 1
    conn.close()


def test_ledger_tick_is_idempotent(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    db.init_db(path)
    conn = db.connect(path)
    db.upsert_operation(conn, db.operation_from_event({
        "id": "g1", "type": "touch_and_go", "icao24": "aaa111", "callsign": "N111AA",
        "timestamp": BASE, "airport_icao": "KLMO",
    }))
    conn.commit()
    conn.close()

    settings = Settings(database_path=path)
    worker.ledger_tick(settings, now_ts=BASE + DAY)
    worker.ledger_tick(settings, now_ts=BASE + DAY)

    conn = db.connect(path)
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1
    conn.close()


def test_ledger_tick_has_no_airports_is_a_noop(tmp_path):
    """An empty `operations` table must not raise — a fresh deploy's first
    nightly tick has nothing to roll up yet."""
    path = str(tmp_path / "empty.sqlite3")
    db.init_db(path)

    settings = Settings(database_path=path)
    result = worker.ledger_tick(settings, now_ts=BASE + DAY)

    assert result == {"rollup_rows": 0, "homebase_rows": 0}


# --- _ledger_loop: never raises out, always heartbeats ----------------------


@pytest.mark.asyncio
async def test_ledger_loop_survives_a_raising_tick_and_reports_heartbeat(tmp_path, monkeypatch):
    """A tick that raises must not kill the loop — a nightly rebuild dying
    would silently stop refreshing the ledger forever. The failure must still
    be visible via the heartbeat, same contract as the other loops."""
    path = str(tmp_path / "t.sqlite3")
    _seed(path)
    settings = Settings(database_path=path)
    store = MemoryStore()

    calls = {"n": 0}

    def _boom(settings_, now_ts):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "ledger_tick", _boom)
    monkeypatch.setattr(worker, "LEDGER_INTERVAL_SECONDS", 0)

    task = asyncio.create_task(worker._ledger_loop(store, settings))
    for _ in range(200):
        if calls["n"] >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] >= 2, "loop must keep ticking after a raising tick"
    heartbeat = await store.get_cache("worker:heartbeat:ledger")
    assert heartbeat is not None
    assert heartbeat["ok"] is False
    assert "boom" in heartbeat["error"]


@pytest.mark.asyncio
async def test_ledger_loop_reports_ok_heartbeat_on_success(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite3")
    _seed(path)
    settings = Settings(database_path=path)
    store = MemoryStore()

    monkeypatch.setattr(worker, "LEDGER_INTERVAL_SECONDS", 0)

    task = asyncio.create_task(worker._ledger_loop(store, settings))
    heartbeat = None
    for _ in range(200):
        heartbeat = await store.get_cache("worker:heartbeat:ledger")
        if heartbeat is not None:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert heartbeat is not None
    assert heartbeat["ok"] is True
    assert "duration_ms" in heartbeat
