from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .detectors import event_counts
from .domain import hour_label, minute_label
from .windows import WindowRange


def quiet_hour(ts: int, tz_name: str) -> bool:
    hour = datetime.fromtimestamp(ts, ZoneInfo(tz_name)).hour
    return hour >= 22 or hour < 7


def score_events(events: list[dict], window: WindowRange, tz_name: str) -> float:
    counts = event_counts(events)
    base = (
        2.0 * counts["circles"]
        + 1.5 * counts["touch_and_gos"]
        + 1.0 * counts["low_approaches"]
        + 3.0 * counts["passes"]
    )
    if window.seconds >= 3600:
        base += sum(1.0 for event in events if quiet_hour(event["timestamp"], tz_name))
        base += sum(
            1.0 for event in events
            if event["type"] == "pass_over_user"
            and (event.get("min_altitude_ft_agl") or 99999) < 1000
        )
    return round(base, 2)


def group_events_by_aircraft(events: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        grouped[event["icao24"]].append(event)
    return grouped


def offender_rows(events: list[dict], window: WindowRange, tz_name: str) -> list[dict]:
    rows = []
    for icao24, aircraft_events in group_events_by_aircraft(events).items():
        counts = event_counts(aircraft_events)
        # Per-runway breakdown of the offender's touch-and-gos, e.g. {"11": 4,
        # "29": 7} shown beneath the T&G count. Counts ONLY touch_and_go so the
        # breakdown sums to the displayed T&G number — folding in low approaches
        # made 4 T&G render as "5x11 1x29" (= 6).
        runway_breakdown: dict[str, int] = {}
        for event in aircraft_events:
            if event.get("type") != "touch_and_go":
                continue
            rwy = event.get("runway_id") or event.get("runway_used")
            if not rwy:
                continue
            runway_breakdown[rwy] = runway_breakdown.get(rwy, 0) + 1
        row = {
            "icao24": icao24,
            "callsign": next((event.get("callsign") for event in reversed(aircraft_events) if event.get("callsign")), icao24.upper()),
            "score": score_events(aircraft_events, window, tz_name),
            **counts,
            "first_event_at": min(event["timestamp"] for event in aircraft_events),
            "last_event_at": max(event["timestamp"] for event in aircraft_events),
            "min_altitude_ft_agl": min(
                [
                    event["min_altitude_ft_agl"]
                    for event in aircraft_events
                    if event["type"] == "pass_over_user"
                    and event.get("min_altitude_ft_agl") is not None
                ],
                default=None,
            ),
            "runway_breakdown": runway_breakdown,
        }
        rows.append(row)
    return sorted(rows, key=lambda row: (-row["score"], row["callsign"]))


def _bucket_start_ts(ts: int, tz_name: str, by_minute: bool) -> int:
    """Floor a timestamp to the start of its local minute/hour bucket."""
    local = datetime.fromtimestamp(ts, ZoneInfo(tz_name))
    floored = (
        local.replace(second=0, microsecond=0) if by_minute
        else local.replace(minute=0, second=0, microsecond=0)
    )
    return int(floored.timestamp())


def event_histogram(events: list[dict], window: WindowRange, tz_name: str) -> list[dict]:
    # Bucket on the instant, not the rendered label: labels sorted
    # lexicographically put "10 AM" before "9 AM", and a rolling 24h window
    # covers each clock hour twice, collapsing yesterday's 3 PM into today's.
    by_minute = window.seconds < 3600
    spans_local_days = (
        datetime.fromtimestamp(window.start_ts, ZoneInfo(tz_name)).date()
        != datetime.fromtimestamp(window.end_ts, ZoneInfo(tz_name)).date()
    )
    by_bucket: dict[int, dict[str, int]] = defaultdict(lambda: {
        "circle": 0,
        "touch_and_go": 0,
        "low_approach": 0,
        "landing": 0,
        "pass_over_user": 0,
    })
    for event in events:
        bucket = by_bucket[_bucket_start_ts(event["timestamp"], tz_name, by_minute)]
        # .get keeps an unfamiliar op type from KeyError-ing (and 404-ing /scan).
        bucket[event["type"]] = bucket.get(event["type"], 0) + 1
    label = minute_label if by_minute else hour_label
    return [
        {"bucket": label(bucket_ts, tz_name, spans_local_days), **counts}
        for bucket_ts, counts in sorted(by_bucket.items())
    ]
