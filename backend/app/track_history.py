"""Build simplified per-flight polylines for the historical track-density view.

Reads come from db.read_track_archive_bbox (all aircraft in a bbox over N
days). Here we group those rows into per-aircraft tracks, split each aircraft
into separate flights on time gaps, and simplify each flight's geometry.
"""

from __future__ import annotations

from itertools import groupby

from .simplify import rdp_keep_mask

GAP_SECONDS = 1200            # >20 min gap => a separate flight
DEFAULT_CEILING_FT_AGL = 5000
DEFAULT_TRACK_CAP = 1500
SIMPLIFY_EPSILON_DEG = 1e-4   # ~11 m; imperceptible at map zoom, big point savings
MIN_DAYS = 1
MAX_DAYS = 7


def split_into_flights(samples: list[dict], gap_seconds: int = GAP_SECONDS) -> list[list[dict]]:
    """Split one aircraft's time-sorted samples into flights on time gaps."""
    if not samples:
        return []
    flights: list[list[dict]] = []
    current: list[dict] = [samples[0]]
    for prev, sample in zip(samples, samples[1:]):
        if sample["timestamp"] - prev["timestamp"] > gap_seconds:
            flights.append(current)
            current = [sample]
        else:
            current.append(sample)
    flights.append(current)
    return flights


def _simplify_flight(flight: list[dict], epsilon: float) -> dict | None:
    """Simplify a single flight's polyline. Returns a track dict or None if it
    collapses below 2 points."""
    if len(flight) < 2:
        return None
    coords = [(s["lat"], s["lon"]) for s in flight]
    mask = rdp_keep_mask(coords, epsilon)
    kept = [s for s, keep in zip(flight, mask) if keep]
    if len(kept) < 2:
        return None
    return {
        "icao24": flight[0]["icao24"],
        "callsign": flight[0].get("callsign"),
        "samples": [
            {
                "lat": s["lat"],
                "lon": s["lon"],
                "altitude_ft": s.get("altitude_ft"),
                "timestamp": s["timestamp"],
            }
            for s in kept
        ],
    }


def build_tracks(
    rows: list[dict],
    *,
    epsilon: float = SIMPLIFY_EPSILON_DEG,
    gap_seconds: int = GAP_SECONDS,
    track_cap: int = DEFAULT_TRACK_CAP,
) -> tuple[list[dict], int]:
    """Group bbox rows into simplified per-flight tracks.

    `rows` must be ordered by (icao24, timestamp) — as read_track_archive_bbox
    returns them. Returns (tracks, total_tracks) where tracks is capped to the
    most recent `track_cap` flights (by last-sample timestamp).
    """
    tracks: list[dict] = []
    for _icao, group in groupby(rows, key=lambda r: r["icao24"]):
        aircraft_samples = list(group)
        for flight in split_into_flights(aircraft_samples, gap_seconds):
            track = _simplify_flight(flight, epsilon)
            if track is not None:
                tracks.append(track)
    total = len(tracks)
    if total > track_cap:
        # Keep the most recent flights by their last sample's timestamp.
        tracks.sort(key=lambda t: t["samples"][-1]["timestamp"], reverse=True)
        tracks = tracks[:track_cap]
    return tracks, total
