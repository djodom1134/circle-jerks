"""Classify pattern circuits by runway and slice their geometry.

For the historical overlay's Average mode: each detected operation
(touch-and-go / low-approach / circle) becomes a "circuit" = the slice of that
aircraft's archived track within +/- CIRCUIT_WINDOW_S of the operation time,
tagged with a class (the runway used, or "area" for pure circling).
"""

from __future__ import annotations

from .simplify import rdp_keep_mask

CIRCUIT_WINDOW_S = 75
DEFAULT_CIRCUIT_CAP = 2000
SIMPLIFY_EPSILON_DEG = 1e-4
CIRCUIT_TYPES = ("touch_and_go", "low_approach", "circle")
MIN_DAYS = 1
MAX_DAYS = 7


def classify(op) -> tuple[str, str | None]:
    """(class_label, runway_id) for an operation. Runway ops use their runway;
    circles and runway-less ops are 'area'."""
    op_type = op["type"]
    runway_id = op["runway_id"] if _has_key(op, "runway_id") else None
    if op_type in ("touch_and_go", "low_approach") and runway_id:
        return str(runway_id), str(runway_id)
    return "area", None


def _has_key(op, key) -> bool:
    try:
        return op[key] is not None
    except (KeyError, IndexError, TypeError):
        return False


def _slice_window(samples: list[dict], center_ts: int, window_s: int) -> list[dict]:
    lo, hi = center_ts - window_s, center_ts + window_s
    return [s for s in samples if lo <= s["timestamp"] <= hi]


def build_circuits(
    operations: list[dict],
    tracks_by_icao: dict[str, list[dict]],
    *,
    window_s: int = CIRCUIT_WINDOW_S,
    epsilon: float = SIMPLIFY_EPSILON_DEG,
    cap: int = DEFAULT_CIRCUIT_CAP,
) -> tuple[list[dict], dict[str, int], int]:
    """Slice each operation's aircraft track into a classified circuit polyline.

    tracks_by_icao keys must be lowercased icao24. Overlapping same-(icao24,class)
    windows collapse to one circuit (earliest kept). See module docstring.
    """
    # Sort operations by time so "earliest kept" dedup is deterministic.
    ops_sorted = sorted(operations, key=lambda o: int(o["timestamp"]))
    # kept_ranges[(icao_lower, class)] = list of (lo, hi) already taken
    kept_ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
    built: list[dict] = []
    for op in ops_sorted:
        icao_lower = str(op["icao24"]).lower()
        ts = int(op["timestamp"])
        class_label, runway_id = classify(op)
        lo, hi = ts - window_s, ts + window_s
        ranges = kept_ranges.setdefault((icao_lower, class_label), [])
        if any(lo <= r_hi and r_lo <= hi for (r_lo, r_hi) in ranges):
            continue  # overlaps an already-built circuit of this aircraft+class
        samples = tracks_by_icao.get(icao_lower)
        if not samples:
            continue
        window = _slice_window(samples, ts, window_s)
        if len(window) < 2:
            continue
        coords = [(s["lat"], s["lon"]) for s in window]
        mask = rdp_keep_mask(coords, epsilon)
        kept = [s for s, keep in zip(window, mask) if keep]
        if len(kept) < 2:
            continue
        ranges.append((lo, hi))
        built.append({
            "class": class_label,
            "runway_id": runway_id,
            "icao24": icao_lower,
            "ts": ts,
            "samples": [
                {"lat": s["lat"], "lon": s["lon"], "timestamp": s["timestamp"]}
                for s in kept
            ],
        })
    total = len(built)
    if total > cap:
        built.sort(key=lambda c: c["ts"], reverse=True)
        built = built[:cap]
    counts_by_class: dict[str, int] = {}
    for c in built:
        counts_by_class[c["class"]] = counts_by_class.get(c["class"], 0) + 1
        del c["ts"]  # internal sort key, not part of the response
    return built, counts_by_class, total
