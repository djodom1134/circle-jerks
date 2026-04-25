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
        }
        rows.append(row)
    return sorted(rows, key=lambda row: (-row["score"], row["callsign"]))


def event_histogram(events: list[dict], window: WindowRange, tz_name: str) -> list[dict]:
    by_bucket: dict[str, dict[str, int]] = defaultdict(lambda: {
        "circle": 0,
        "touch_and_go": 0,
        "low_approach": 0,
        "pass_over_user": 0,
    })
    for event in events:
        label = minute_label(event["timestamp"], tz_name) if window.seconds < 3600 else hour_label(event["timestamp"], tz_name)
        by_bucket[label][event["type"]] += 1
    return [
        {"bucket": bucket, **counts}
        for bucket, counts in sorted(by_bucket.items())
    ]
