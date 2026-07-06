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
RUNWAY_TYPES = ("touch_and_go", "low_approach")
MIN_LAP_S = 60
MAX_LAP_S = 480
MIN_LAP_POINTS = 4
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


def _slice_range(samples: list[dict], lo_ts: int, hi_ts: int) -> list[dict]:
    return [s for s in samples if lo_ts <= s["timestamp"] <= hi_ts]


def build_circuits(
    operations: list[dict],
    tracks_by_icao: dict[str, list[dict]],
    *,
    window_s: int = CIRCUIT_WINDOW_S,
    epsilon: float = SIMPLIFY_EPSILON_DEG,
    cap: int = DEFAULT_CIRCUIT_CAP,
    min_lap_s: int = MIN_LAP_S,
    max_lap_s: int = MAX_LAP_S,
    min_lap_points: int = MIN_LAP_POINTS,
) -> tuple[list[dict], dict[str, int], int, int]:
    """Segment closed laps between consecutive same-runway ops; everything else
    becomes a faint +/- window context slice.

    A lap = the track slice between two consecutive same-runway operations of one
    aircraft whose timestamps are min_lap_s..max_lap_s apart (touchdown ->
    pattern -> touchdown, a closed loop). Circle ops and any runway op not part
    of such a pair become +/- window_s context slices (is_loop False).

    tracks_by_icao keys must be lowercased icao24. Returns
    (circuits, counts_by_class(laps only), total, context_count).
    """
    ops_sorted = sorted(operations, key=lambda o: int(o["timestamp"]))
    by_icao: dict[str, list[dict]] = {}
    for op in ops_sorted:
        by_icao.setdefault(str(op["icao24"]).lower(), []).append(op)

    def _simplify(rows: list[dict]) -> list[dict]:
        coords = [(s["lat"], s["lon"]) for s in rows]
        mask = rdp_keep_mask(coords, epsilon)
        return [s for s, keep in zip(rows, mask) if keep]

    def _emit(cls, rwy, icao_lower, is_loop, ts, rows) -> dict:
        return {
            "class": cls,
            "runway_id": rwy,
            "icao24": icao_lower,
            "is_loop": is_loop,
            "ts": ts,
            "samples": [
                {"lat": s["lat"], "lon": s["lon"], "timestamp": s["timestamp"]}
                for s in rows
            ],
        }

    built: list[dict] = []
    consumed: set[int] = set()  # id() of ops captured as a lap endpoint

    # Pass 1 -- laps between consecutive same-runway ops.
    for icao_lower, ops in by_icao.items():
        samples = tracks_by_icao.get(icao_lower)
        if not samples:
            continue
        for a, b in zip(ops, ops[1:]):
            cls_a, rwy_a = classify(a)
            cls_b, rwy_b = classify(b)
            if rwy_a is None or rwy_b is None or cls_a != cls_b:
                continue  # not both runway ops, or different runways
            gap = int(b["timestamp"]) - int(a["timestamp"])
            if not (min_lap_s <= gap <= max_lap_s):
                continue
            lap = _slice_range(samples, int(a["timestamp"]), int(b["timestamp"]))
            if len(lap) < 2:
                continue
            kept = _simplify(lap)
            if len(kept) < min_lap_points:
                continue
            consumed.add(id(a))
            consumed.add(id(b))
            built.append(_emit(cls_a, rwy_a, icao_lower, True, int(a["timestamp"]), kept))

    # Pass 2 -- context (+/- window) for every op not consumed by a lap.
    kept_ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for icao_lower, ops in by_icao.items():
        samples = tracks_by_icao.get(icao_lower)
        if not samples:
            continue
        for op in ops:
            if id(op) in consumed:
                continue
            ts = int(op["timestamp"])
            cls, rwy = classify(op)
            lo, hi = ts - window_s, ts + window_s
            ranges = kept_ranges.setdefault((icao_lower, cls), [])
            if any(lo <= r_hi and r_lo <= hi for (r_lo, r_hi) in ranges):
                continue  # overlaps an already-built context slice
            window = _slice_window(samples, ts, window_s)
            if len(window) < 2:
                continue
            kept = _simplify(window)
            if len(kept) < 2:
                continue
            ranges.append((lo, hi))
            built.append(_emit(cls, rwy, icao_lower, False, ts, kept))

    total = len(built)
    counts_by_class: dict[str, int] = {}
    for c in built:
        if c["is_loop"]:
            counts_by_class[c["class"]] = counts_by_class.get(c["class"], 0) + 1
    context_count = sum(1 for c in built if not c["is_loop"])
    if total > cap:
        # Keep laps first, then most-recent context.
        built.sort(key=lambda c: (0 if c["is_loop"] else 1, -c["ts"]))
        built = built[:cap]
    for c in built:
        del c["ts"]  # internal sort key, not part of the response
    return built, counts_by_class, total, context_count
