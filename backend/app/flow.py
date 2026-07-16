from __future__ import annotations

import math

from . import db as _db

FLOW_WINDOW_S = 3600
MIN_OPS = 3
MIN_WINDOW_S = 600


def trailing_run(ops: list[dict]) -> tuple[str | None, int, int]:
    """Length and time-span of the trailing run of same-runway ops (time-ordered asc)."""
    if not ops:
        return (None, 0, 0)
    last = ops[-1]["runway_id"]
    run = []
    for op in reversed(ops):
        if op["runway_id"] == last:
            run.append(op)
        else:
            break
    span = run[0]["timestamp"] - run[-1]["timestamp"]  # latest - earliest
    return (last, len(run), span)


def established_end(ops: list[dict], previous_end: str | None,
                    min_ops: int = MIN_OPS, min_window_s: int = MIN_WINDOW_S) -> str | None:
    """The currently-established active runway end. A new end only takes over once
    its trailing run reaches min_ops ops OR min_window_s of agreement."""
    rid, run_len, span = trailing_run(ops)
    if rid is None:
        return previous_end
    if rid == previous_end:
        return previous_end
    if run_len >= min_ops or span >= min_window_s:
        return rid
    return previous_end


def cowboy_for_end(ops: list[dict], end: str) -> dict | None:
    """The earliest op of the trailing run for `end` — the aircraft that started it."""
    run = []
    for op in reversed(ops):
        if op["runway_id"] == end:
            run.append(op)
        else:
            break
    return run[-1] if run else None


def headwind_component(heading_deg, wind_from_deg, wind_speed_kt) -> float | None:
    """Signed headwind along the runway heading. Positive = into the wind."""
    if heading_deg is None or wind_from_deg is None or wind_speed_kt is None:
        return None
    return round(float(wind_speed_kt) * math.cos(math.radians(float(heading_deg) - float(wind_from_deg))), 2)


def process(conn, airport_icao: str, runways: list[dict], events: list[dict],
            wind: dict | None, now: int) -> None:
    """Tag new runway ops with headwind and update the active-runway flow / cowboy log."""
    headings = {r["runway_id"]: r["heading_deg"] for r in runways}
    current = (wind or {}).get("current") or {}
    wind_from = current.get("wind_from_dir_degrees")
    wind_speed = current.get("wind_speed_kt")

    # 1. Tag each new runway-tagged op with its headwind component.
    for event in events:
        rid = event.get("runway_id")
        if not rid or not event.get("id"):
            continue
        hk = headwind_component(headings.get(rid), wind_from, wind_speed)
        _db.update_operation_wind(conn, event["id"], wind_from, wind_speed, hk)

    # 2. Determine the established active end from recent runway-tagged ops.
    rows = _db.read_operations(conn, airport_icao, now - FLOW_WINDOW_S, now,
                               types=["touch_and_go", "low_approach"])
    ops = [
        {"timestamp": r["timestamp"], "runway_id": r["runway_id"], "icao24": r["icao24"],
         "callsign": r["callsign"], "registration": r["registration"], "id": r["id"]}
        for r in rows if r["runway_id"]
    ]
    prev = _db.current_flow(conn, airport_icao)
    prev_end = prev["active_runway_id"] if prev else None
    new_end = established_end(ops, prev_end)
    if new_end is None or new_end == prev_end:
        return

    cowboy = cowboy_for_end(ops, new_end)
    hk_new = headwind_component(headings.get(new_end), wind_from, wind_speed)
    wind_favored = 1 if (hk_new is not None and hk_new > 0) else 0

    _db.close_open_flow(conn, airport_icao, now)
    _db.open_flow(conn, airport_icao, new_end, now, wind_from, wind_speed)
    if prev_end is not None:  # a flip (not the first-ever establishment) → log the cowboy
        _db.insert_runway_change(conn, airport_icao, prev_end, new_end, now,
                                 cowboy, wind_from, wind_speed, wind_favored)
