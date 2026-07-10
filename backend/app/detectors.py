from __future__ import annotations

import hashlib
from collections import defaultdict
from statistics import mean

from .db import Airport
from .domain import ScanParams
from .geo import Point, bearing_deg, closest_segment_approach_nm, destination_point, distance_nm, heading_delta_deg

MAX_SEGMENT_GAP_SECONDS = 90
MIN_CIRCLE_SAMPLES = 6
MIN_CIRCLE_PATH_NM = 3.0
MIN_CIRCLE_DURATION_SECONDS = 120
# A physical pattern lap is >= MIN_CIRCLE_DURATION_SECONDS; bucket circle event
# ids at this granularity so multiple line-crossings / GPS jitter within one lap
# collapse to a single row (via the stable event id + ON CONFLICT idempotency).
CIRCLE_BUCKET_SECONDS = 120
# Identity bucket for the closest-approach (runway-pass) anchor. Must be larger
# than sample-to-sample jitter in which point is "closest" (~10-20s) yet smaller
# than the minimum spacing between two pattern laps (~200s+), so re-detections of
# one lap collapse while distinct laps stay distinct.
CIRCLE_LAP_ANCHOR_SECONDS = 150
MAX_CIRCLE_DURATION_SECONDS = 20 * 60
# Require ~one full lap of cumulative heading change. 340° (not 360°) leaves
# slack for ADS-B sampling that drops 1-2 small heading deltas mid-turn.
MIN_CIRCLE_TURN_DEGREES = 340
MIN_CIRCLE_MOVEMENT_NM = 0.03
# Validation closure threshold (start ↔ end of the candidate loop) used when
# emitting an event.
MAX_CIRCLE_CLOSURE_NM = 0.5
# Stricter closure used when deciding whether to add the closure-heading
# bonus. Only when the aircraft is genuinely near its starting point do we
# credit the implicit final wedge of an octagon-style sample set. Without
# this guard, half-loops on short racetrack ovals get falsely credited as
# full laps, doubling the circle count.
TIGHT_CLOSURE_BONUS_NM = 0.5
MAX_CIRCLE_ALTITUDE_FT_AGL = 2000
# A closed lap counts as a touch-and-go when its path passes within this far of
# the runway segment — i.e. it flew over the runway, at any altitude. (Real
# pattern work tracks the runway to within ~0.01 nm; an off-field orbit stays
# well beyond 0.3 nm.) Touch-and-go is now a geometric subset of circles, not a
# <=50 ft touchdown.
RUNWAY_OVERLAP_NM = 0.25

# --- Runway-contact / landing classification ---
# Look this far past a touchdown to decide touch-and-go vs. landing. Widened
# from the old 120s so touch-and-go and landing are exact complements over the
# same 5-minute window.
CLIMB_OUT_LOOKAHEAD_SECONDS = 300
# A landing is only *decided* once we've observed this long past the touchdown
# with no climb-out — prevents emitting a "landing" a later scan would find was
# a touch-and-go.
LANDING_SETTLE_SECONDS = 300
# The aircraft must have descended INTO the field: at least one sample this far
# before touchdown was at/above this AGL. Rejects parked/taxiing transponders.
APPROACH_LOOKBACK_SECONDS = 300
APPROACH_MIN_AGL_FT = 500


def pass_geometry_key(params: ScanParams) -> str:
    # 3-decimal precision (~111 m) so small lat/lon drifts from GPS jitter or
    # accidental map clicks don't fragment the pass-event store into a new
    # geometry bucket. Still much finer than the 0.5 nm (~926 m) pass radius
    # so a deliberate home-location move correctly produces distinct passes.
    return f"{params.user_lat:.3f}:{params.user_lon:.3f}:{params.pass_radius_nm:.2f}"


def _event_id(*parts: object) -> str:
    payload = ":".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def samples_in_last(track: list[dict], seconds: int) -> list[dict]:
    if not track:
        return []
    cutoff = int(track[-1]["timestamp"]) - seconds
    return [sample for sample in track if sample["timestamp"] >= cutoff]


def altitude_agl_from_elevation(sample: dict, elevation_ft: float) -> float | None:
    alt = sample.get("geo_altitude_ft")
    if alt is None:
        alt = sample.get("baro_altitude_ft")
    if alt is None:
        return None
    return max(0.0, float(alt) - elevation_ft)


def altitude_agl(sample: dict, airport: Airport) -> float | None:
    return altitude_agl_from_elevation(sample, airport.elevation_ft)


def _user_reference_elevation_ft(airport: Airport, params: ScanParams) -> float:
    return float(params.user_elevation_ft if params.user_elevation_ft is not None else airport.elevation_ft)


def altitude_over_user_agl(sample: dict, airport: Airport, params: ScanParams) -> float | None:
    return altitude_agl_from_elevation(sample, _user_reference_elevation_ft(airport, params))


def _interpolated_agl(a: dict, b: dict, t: float, elevation_ft: float) -> float | None:
    a_alt = altitude_agl_from_elevation(a, elevation_ft)
    b_alt = altitude_agl_from_elevation(b, elevation_ft)
    if a_alt is None and b_alt is None:
        return None
    if a_alt is None:
        return b_alt
    if b_alt is None:
        return a_alt
    return a_alt + (b_alt - a_alt) * t


def closest_over_user_rows(
    track: list[dict],
    airport: Airport,
    params: ScanParams,
    start_ts: int | None = None,
    end_ts: int | None = None,
    ceiling_ft: int | None = None,
) -> list[dict]:
    samples = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if sample.get("lat") is not None
        and sample.get("lon") is not None
        and (start_ts is None or sample["timestamp"] >= start_ts - MAX_SEGMENT_GAP_SECONDS)
        and (end_ts is None or sample["timestamp"] <= end_ts + MAX_SEGMENT_GAP_SECONDS)
    ]
    user_point = Point(params.user_lat, params.user_lon)
    user_elevation_ft = _user_reference_elevation_ft(airport, params)
    rows_by_ts: dict[int, dict] = {}
    for sample in samples:
        timestamp = int(sample["timestamp"])
        if start_ts is not None and timestamp < start_ts:
            continue
        if end_ts is not None and timestamp > end_ts:
            continue
        agl = altitude_agl_from_elevation(sample, user_elevation_ft)
        distance = distance_nm(Point(sample["lat"], sample["lon"]), user_point)
        if (
            agl is not None
            and distance <= params.pass_radius_nm
            and (ceiling_ft is None or agl <= ceiling_ft)
        ):
            rows_by_ts[timestamp] = {"timestamp": timestamp, "agl": agl, "distance_nm": distance}
    if len(samples) == 1:
        return sorted(rows_by_ts.values(), key=lambda row: row["timestamp"])

    for a, b in zip(samples, samples[1:]):
        gap = int(b["timestamp"] - a["timestamp"])
        if gap <= 0 or gap > MAX_SEGMENT_GAP_SECONDS:
            continue
        distance, t = closest_segment_approach_nm(
            Point(a["lat"], a["lon"]),
            Point(b["lat"], b["lon"]),
            user_point,
        )
        timestamp = int(round(a["timestamp"] + gap * t))
        if start_ts is not None and timestamp < start_ts:
            continue
        if end_ts is not None and timestamp > end_ts:
            continue
        agl = _interpolated_agl(a, b, t, user_elevation_ft)
        if agl is None or distance > params.pass_radius_nm:
            continue
        if ceiling_ft is not None and agl > ceiling_ft:
            continue
        existing = rows_by_ts.get(timestamp)
        if existing is None or distance < existing["distance_nm"]:
            rows_by_ts[timestamp] = {"timestamp": timestamp, "agl": agl, "distance_nm": distance}
    return sorted(rows_by_ts.values(), key=lambda row: row["timestamp"])


def pass_crossing_rows(
    samples: list[dict],
    airport: Airport,
    params: ScanParams,
    start_ts: int | None = None,
    end_ts: int | None = None,
    ceiling_ft: int | None = None,
) -> list[dict]:
    sorted_samples = [
        sample for sample in sorted(samples, key=lambda row: row["timestamp"])
        if sample.get("lat") is not None
        and sample.get("lon") is not None
        and (start_ts is None or sample["timestamp"] >= start_ts - MAX_SEGMENT_GAP_SECONDS)
        and (end_ts is None or sample["timestamp"] <= end_ts + MAX_SEGMENT_GAP_SECONDS)
    ]
    if len(sorted_samples) < 2:
        return []

    user_point = Point(params.user_lat, params.user_lon)
    user_elevation_ft = _user_reference_elevation_ft(airport, params)
    rows_by_ts: dict[int, dict] = {}
    for a, b in zip(sorted_samples, sorted_samples[1:]):
        gap = int(b["timestamp"] - a["timestamp"])
        if gap <= 0 or gap > MAX_SEGMENT_GAP_SECONDS:
            continue
        segment_nm = distance_nm(Point(a["lat"], a["lon"]), Point(b["lat"], b["lon"]))
        if segment_nm < MIN_CIRCLE_MOVEMENT_NM:
            continue
        distance, t = closest_segment_approach_nm(
            Point(a["lat"], a["lon"]),
            Point(b["lat"], b["lon"]),
            user_point,
        )
        if distance > params.pass_radius_nm:
            continue

        timestamp = int(round(a["timestamp"] + gap * t))
        if start_ts is not None and timestamp < start_ts:
            continue
        if end_ts is not None and timestamp > end_ts:
            continue

        agl = _interpolated_agl(a, b, t, user_elevation_ft)
        if ceiling_ft is not None and agl is not None and agl > ceiling_ft:
            continue
        existing = rows_by_ts.get(timestamp)
        if existing is None or distance < existing["distance_nm"]:
            rows_by_ts[timestamp] = {
                "timestamp": timestamp,
                "agl": agl,
                "distance_nm": distance,
                "segment_start_ts": int(a["timestamp"]),
                "segment_end_ts": int(b["timestamp"]),
            }
    return sorted(rows_by_ts.values(), key=lambda row: row["timestamp"])


def _detect_circles_in_samples(
    samples: list[dict],
    airport: Airport,
    params: ScanParams,
    start_ts: int | None = None,
    end_ts: int | None = None,
    runways: list[dict] | None = None,
) -> list[dict]:
    if len(samples) < MIN_CIRCLE_SAMPLES:
        return []

    airport_point = Point(airport.lat, airport.lon)
    events = []

    previous_sample: dict | None = None
    previous_course: float | None = None
    first_course: float | None = None
    loop_samples: list[dict] = []
    cumulative_turn = 0.0
    path_nm = 0.0

    def reset(current: dict | None = None) -> None:
        nonlocal previous_sample, previous_course, first_course, loop_samples, cumulative_turn, path_nm
        previous_sample = current
        previous_course = None
        first_course = None
        loop_samples = [current] if current is not None else []
        cumulative_turn = 0.0
        path_nm = 0.0

    def circle_event(current: dict, turn_degrees: float, loop_path_nm: float) -> dict | None:
        if len(loop_samples) < MIN_CIRCLE_SAMPLES:
            return None
        timestamp = int(current["timestamp"])
        if start_ts is not None and timestamp < start_ts:
            return None
        if end_ts is not None and timestamp > end_ts:
            return None

        duration_seconds = int(loop_samples[-1]["timestamp"] - loop_samples[0]["timestamp"])
        if not MIN_CIRCLE_DURATION_SECONDS <= duration_seconds <= MAX_CIRCLE_DURATION_SECONDS:
            return None
        if loop_path_nm < MIN_CIRCLE_PATH_NM:
            return None

        start_point = Point(loop_samples[0]["lat"], loop_samples[0]["lon"])
        end_point = Point(loop_samples[-1]["lat"], loop_samples[-1]["lon"])
        closure_nm = distance_nm(start_point, end_point)
        if closure_nm > MAX_CIRCLE_CLOSURE_NM:
            return None

        altitudes = [
            alt for sample in loop_samples
            if (alt := altitude_agl(sample, airport)) is not None
        ]
        if not altitudes or min(altitudes) > MAX_CIRCLE_ALTITUDE_FT_AGL:
            return None

        radius_values = [
            distance_nm(Point(sample["lat"], sample["lon"]), airport_point)
            for sample in loop_samples
        ]
        # Anchor the event id on the lap's closest approach to the field — the
        # physical "pass over the runway". That point is a stable feature of the
        # lap, so every re-detection across the sliding scan window resolves to
        # the same id and collapses (via ON CONFLICT) to one row. The old anchor
        # was the lap-END sample, which drifts as the window slides and smeared
        # one lap across ~3 buckets (2.8x over-count on real KLMO pattern work).
        closest_idx = min(range(len(loop_samples)), key=lambda i: radius_values[i])
        pass_ts = int(loop_samples[closest_idx]["timestamp"])
        pass_anchor = round(pass_ts / CIRCLE_LAP_ANCHOR_SECONDS)
        return {
            "id": _event_id("circle", current["icao24"], airport.icao, pass_anchor),
            "pass_anchor": pass_anchor,
            "over_runway": _loop_over_runway(loop_samples, runways) if runways else None,
            "type": "circle",
            "icao24": current["icao24"],
            "callsign": current.get("callsign") or current["icao24"].upper(),
            "timestamp": timestamp,
            "airport_icao": airport.icao,
            "avg_loop_radius_nm": round(mean(radius_values), 2),
            "path_nm": round(loop_path_nm, 2),
            "closure_nm": round(closure_nm, 2),
            "turn_degrees": int(round(turn_degrees)),
            "turn_direction": "right" if turn_degrees > 0 else "left",
            "detection_method": "course_turn_closed_lap",
            "start_timestamp": int(loop_samples[0]["timestamp"]),
            "end_timestamp": int(loop_samples[-1]["timestamp"]),
            "alt_band_ft": [
                int(min(altitudes)) if altitudes else None,
                int(max(altitudes)) if altitudes else None,
            ],
        }

    for current in sorted(samples, key=lambda row: row["timestamp"]):
        if current.get("lat") is None or current.get("lon") is None:
            reset()
            continue

        current_point = Point(current["lat"], current["lon"])
        if distance_nm(current_point, airport_point) > params.ring_nm:
            reset()
            continue

        if previous_sample is None:
            reset(current)
            continue

        gap_seconds = int(current["timestamp"] - previous_sample["timestamp"])
        if gap_seconds <= 0:
            continue
        if gap_seconds > MAX_SEGMENT_GAP_SECONDS:
            reset(current)
            continue

        previous_point = Point(previous_sample["lat"], previous_sample["lon"])
        segment_nm = distance_nm(previous_point, current_point)
        if segment_nm < MIN_CIRCLE_MOVEMENT_NM:
            previous_sample = current
            continue

        course = bearing_deg(previous_point, current_point)
        if previous_course is not None:
            delta = heading_delta_deg(previous_course, course)
            if abs(delta) >= 3:
                cumulative_turn += delta
        else:
            first_course = course
        previous_course = course
        previous_sample = current
        loop_samples.append(current)
        path_nm += segment_nm

        # Effective turn: cumulative_turn alone, plus an implicit
        # "wedge back to start" credit only when the aircraft is GENUINELY
        # near its starting point. The closure-tight gate is what keeps
        # racetrack half-loops (180° + far-side closure) from being mistaken
        # for full circles.
        effective_turn = cumulative_turn
        if first_course is not None and loop_samples:
            start_pt = Point(loop_samples[0]["lat"], loop_samples[0]["lon"])
            if distance_nm(start_pt, current_point) <= TIGHT_CLOSURE_BONUS_NM:
                effective_turn = cumulative_turn + heading_delta_deg(course, first_course)

        if abs(effective_turn) < MIN_CIRCLE_TURN_DEGREES:
            continue
        event = circle_event(current, effective_turn, path_nm)
        if event:
            events.append(event)
            reset(current)
        elif abs(cumulative_turn) >= 720:
            # Safety valve: two laps' worth of turn without ever emitting a
            # valid event (e.g. closure or altitude failed every time) — reset
            # rather than letting cumulative_turn grow without bound.
            reset(current)
    return events


def _runway_segments(runways: list[dict]) -> list[tuple[str, Point, Point, float | None]]:
    segments = []
    for r in runways:
        thr = Point(r["lat_threshold"], r["lon_threshold"])
        far = destination_point(thr, r["heading_deg"], (r.get("length_ft") or 4000) / 6076.12)
        segments.append((r["runway_id"], thr, far, r.get("heading_deg")))
    return segments


def _loop_over_runway(loop_samples: list[dict], runways: list[dict]) -> tuple[str, float | None] | None:
    """The (runway_id, heading) whose segment the loop passes over, or None.

    A loop flies "over the runway" when any of its samples come within
    RUNWAY_OVERLAP_NM of the runway segment.
    """
    if not runways:
        return None
    segments = _runway_segments(runways)
    best: tuple[str, float | None] | None = None
    best_d = RUNWAY_OVERLAP_NM
    for sample in loop_samples:
        point = Point(sample["lat"], sample["lon"])
        for rid, a, b, hdg in segments:
            d = closest_segment_approach_nm(a, b, point)[0]
            if d < best_d:
                best_d, best = d, (rid, hdg)
    return best


def touch_and_gos_from_circles(circles: list[dict]) -> list[dict]:
    """A touch-and-go is a circle whose loop passed over the runway. One lap
    over the runway is therefore both a circle and a touch-and-go; the two share
    the same per-lap runway-pass anchor so their ids stay stable across scans.
    """
    events = []
    for circle in circles:
        over = circle.get("over_runway")
        if not over:
            continue
        rid, heading = over
        events.append({
            "id": _event_id("touch_and_go", circle["icao24"], circle["airport_icao"],
                            circle["pass_anchor"]),
            "type": "touch_and_go",
            "icao24": circle["icao24"],
            "callsign": circle["callsign"],
            "timestamp": circle["timestamp"],
            "airport_icao": circle["airport_icao"],
            "runway_id": rid,
            "runway_heading_deg": int(heading) if heading is not None else None,
            "runway_used": rid,
            "min_altitude_ft_agl": (circle.get("alt_band_ft") or [None])[0],
            "turn_direction": circle.get("turn_direction"),
            "emitter_category": None,
        })
    return events


def detect_circles(track: list[dict], airport: Airport, params: ScanParams,
                   runways: list[dict] | None = None) -> list[dict]:
    # A "circle" is a detected closed pattern lap (>= ~340 deg of cumulative
    # turn, <= 2000 ft AGL, tight closure). The older home<->airport
    # line-crossing counter was removed: a track orbiting the airport crosses
    # the home<->airport chord ~twice per revolution, so it counted ~2x per
    # lap and inflated circle totals.
    recent = samples_in_last(track, 20 * 60)
    return _detect_circles_in_samples(recent, airport, params, runways=runways)


def detect_circles_over_period(
    track: list[dict],
    airport: Airport,
    params: ScanParams,
    start_ts: int,
    end_ts: int,
    runways: list[dict] | None = None,
) -> list[dict]:
    samples = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if start_ts - 20 * 60 <= sample["timestamp"] <= end_ts
    ]
    # Closed-lap events carry the lap window that deviation needs, so the
    # historical/backfill variant runs the same detector as the live path.
    return _detect_circles_in_samples(samples, airport, params, runways=runways)


def _nearest_runway(sample: dict, runways: list[dict]) -> tuple[dict | None, float]:
    if not runways or sample.get("lat") is None or sample.get("lon") is None:
        return None, 999.0
    point = Point(sample["lat"], sample["lon"])
    ranked = [
        (runway, distance_nm(point, Point(runway["lat_threshold"], runway["lon_threshold"])))
        for runway in runways
    ]
    return min(ranked, key=lambda row: row[1])


def _runway_for_direction(
    sample: dict,
    runways: list[dict],
    tolerance_deg: float = 45.0,
) -> dict | None:
    """Pick the runway whose `heading_deg` best matches the aircraft's heading.

    A physical runway is represented as two rows (e.g. "11" and "29"); choosing
    the one whose direction aligns with the aircraft's path is what gives us
    "T&G on runway 29" instead of just "T&G on runway 11/29".
    """
    if not runways:
        return None
    aircraft_heading = sample.get("heading_deg")
    if aircraft_heading is None:
        return None
    best: tuple[float, dict] | None = None
    for runway in runways:
        delta = abs(heading_delta_deg(float(aircraft_heading), float(runway["heading_deg"])))
        if delta <= tolerance_deg and (best is None or delta < best[0]):
            best = (delta, runway)
    return best[1] if best else None


def detect_touch_and_gos(track: list[dict], airport: Airport, runways: list[dict]) -> list[dict]:
    recent = samples_in_last(track, 120)
    return detect_touch_and_gos_over_period(
        recent,
        airport,
        runways,
        int(recent[0]["timestamp"]) if recent else 0,
        int(recent[-1]["timestamp"]) if recent else 0,
    )


def _runway_low_episodes(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> tuple[list[dict], list[dict]]:
    """Find each runway-contact episode (≤200 ft AGL within 1.5 nm of a runway).

    Shared by the touch-and-go and landing detectors so they classify the exact
    same episodes and can never disagree. Returns (recent, episodes). Each
    episode is the lowest sample in a 180 s bucket, tagged `is_first_low` when
    the previous 180 s window held no runway-low sample for this aircraft (i.e.
    this bucket is an arrival, not a continuation of an on-ground presence).
    """
    recent = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if start_ts - APPROACH_LOOKBACK_SECONDS <= sample["timestamp"] <= end_ts + CLIMB_OUT_LOOKAHEAD_SECONDS
    ]
    if len(recent) < 4:
        return recent, []

    low_by_bucket: dict[int, list[tuple[dict, float, dict | None, float | None]]] = defaultdict(list)
    for sample in recent:
        if sample["timestamp"] < start_ts or sample["timestamp"] > end_ts:
            continue
        agl = altitude_agl(sample, airport)
        runway, runway_dist = _nearest_runway(sample, runways)
        speed = sample.get("velocity_kt")
        if agl is not None and agl <= 200 and runway_dist <= 1.5:
            low_by_bucket[int(sample["timestamp"] // 180)].append((sample, agl, runway, speed))

    # Merge CONSECUTIVE low buckets into one episode. A single touchdown whose
    # low samples straddle a 180 s boundary otherwise became two episodes (two
    # rows ~10-30 s apart); merging the run and taking its overall lowest sample
    # yields one event per physical runway contact without merging two genuinely
    # separate touchdowns (those are always >= a full lap, i.e. many buckets,
    # apart). The run's first bucket seeds the id so it is stable across scans.
    episodes = []
    for bucket in sorted(low_by_bucket):
        if (bucket - 1) in low_by_bucket:
            continue  # continuation of the previous run — folded in below
        run_samples: list[tuple[dict, float, dict | None, float | None]] = []
        b = bucket
        while b in low_by_bucket:
            run_samples.extend(low_by_bucket[b])
            b += 1
        lowest_sample, lowest_agl, runway, speed = min(run_samples, key=lambda row: row[1])
        episodes.append({
            "bucket": bucket,
            "lowest_sample": lowest_sample,
            "lowest_agl": lowest_agl,
            "runway": runway,
            "speed": speed,
            "is_first_low": True,
        })
    return recent, episodes


def _after_samples(recent: list[dict], lowest_sample: dict, seconds: int) -> list[dict]:
    lt = lowest_sample["timestamp"]
    return [s for s in recent if lt < s["timestamp"] <= lt + seconds]


def _climbed_out(after: list[dict], lowest_agl: float, airport: Airport) -> bool:
    if not after:
        return False
    latest_agl = altitude_agl(after[-1], airport)
    climbed = latest_agl is not None and latest_agl - lowest_agl >= 150
    vertical_up = any((s.get("vertical_rate_fpm") or 0) > 250 for s in after)
    return climbed or vertical_up


def _approached_from_altitude(recent: list[dict], lt: int, airport: Airport) -> bool:
    for sample in recent:
        ts = sample["timestamp"]
        if ts >= lt or ts < lt - APPROACH_LOOKBACK_SECONDS:
            continue
        agl = altitude_agl(sample, airport)
        if agl is not None and agl >= APPROACH_MIN_AGL_FT:
            return True
    return False


def _build_runway_event(event_type: str, ep: dict, airport: Airport, runways: list[dict]) -> dict:
    """Build a runway-contact event dict shared by touch-and-go and landing.

    Touch-and-go and landing are complements over the same episodes, so their
    event shape MUST stay identical (same 10 keys, same values) — the only
    per-detector variation is `event_type` (which also seeds the `_event_id`).
    """
    lowest_sample = ep["lowest_sample"]
    directional = _runway_for_direction(lowest_sample, runways)
    used_runway = directional or ep["runway"]
    runway_heading = used_runway.get("heading_deg") if used_runway else None
    return {
        "id": _event_id(event_type, lowest_sample["icao24"], airport.icao, ep["bucket"]),
        "type": event_type,
        "icao24": lowest_sample["icao24"],
        "callsign": lowest_sample.get("callsign") or lowest_sample["icao24"].upper(),
        "timestamp": int(lowest_sample["timestamp"]),
        "airport_icao": airport.icao,
        "runway_id": used_runway["runway_id"] if used_runway else None,
        "runway_heading_deg": int(runway_heading) if runway_heading is not None else None,
        "runway_used": used_runway["runway_id"] if used_runway else None,
        "min_altitude_ft_agl": int(ep["lowest_agl"]),
        "emitter_category": lowest_sample.get("emitter_category"),
    }


def detect_touch_and_gos_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    recent, episodes = _runway_low_episodes(track, airport, runways, start_ts, end_ts)
    events = []
    for ep in episodes:
        lowest_sample = ep["lowest_sample"]
        lowest_agl = ep["lowest_agl"]
        speed = ep["speed"]
        lt = int(lowest_sample["timestamp"])
        after = _after_samples(recent, lowest_sample, CLIMB_OUT_LOOKAHEAD_SECONDS)
        if not after:
            continue
        on_ground_seconds = sum(1 for sample in after if sample.get("on_ground")) * 30
        # Speed filter is best-effort: archive samples have no velocity, so trust
        # the altitude + climb-back-up signature when speed is absent.
        speed_ok = speed is None or speed <= 90
        if not (lowest_agl <= 200 and speed_ok and _climbed_out(after, lowest_agl, airport)
                and on_ground_seconds <= 60):
            continue
        if not _approached_from_altitude(recent, lt, airport):
            continue  # no prior approach -> a departure (takeoff), handled elsewhere
        # Touch-and-go is now derived geometrically from circles that cross the
        # runway (touch_and_gos_from_circles). This episode path emits only the
        # low-approach sub-metric — a low pass over the runway that climbed out.
        events.append(_build_runway_event("low_approach", ep, airport, runways))
    return events


def detect_landings_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    """A landing = reached the runway low and did NOT climb back out within 5 min.

    The complement of a touch-and-go over the same runway-low episodes. Guards:
    only the first low bucket of an arrival (`is_first_low`), the aircraft
    descended in from ≥500 ft AGL, and we've observed ≥5 min past touchdown
    (settle) so we won't retract it as a touch-and-go on a later scan.
    """
    recent, episodes = _runway_low_episodes(track, airport, runways, start_ts, end_ts)
    events = []
    for ep in episodes:
        if not ep["is_first_low"]:
            continue
        lowest_sample = ep["lowest_sample"]
        lowest_agl = ep["lowest_agl"]
        lt = int(lowest_sample["timestamp"])
        if lowest_agl > 200:
            continue
        if end_ts - lt < LANDING_SETTLE_SECONDS:
            continue  # not enough post-touchdown data yet — decide on a later pass
        after = _after_samples(recent, lowest_sample, LANDING_SETTLE_SECONDS)
        if _climbed_out(after, lowest_agl, airport):
            continue  # climbed back out -> touch-and-go, not a landing
        if not _approached_from_altitude(recent, lt, airport):
            continue  # never descended in (parked/taxiing) -> not a landing

        events.append(_build_runway_event("landing", ep, airport, runways))
    return events


def detect_takeoffs_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    """A takeoff = reached the runway low and climbed out, with NO prior approach
    from altitude — i.e. the aircraft originated at the field and departed.

    The departure complement of touch-and-go over the same runway-low episodes:
    both climb out, but a touch-and-go descended in from >=500 ft AGL first while
    a takeoff started on/near the ground. Only the first low bucket of a presence
    counts, so a T&G's touchdown is never re-counted as a departure.
    """
    recent, episodes = _runway_low_episodes(track, airport, runways, start_ts, end_ts)
    events = []
    for ep in episodes:
        if not ep["is_first_low"]:
            continue
        lowest_sample = ep["lowest_sample"]
        lowest_agl = ep["lowest_agl"]
        lt = int(lowest_sample["timestamp"])
        if lowest_agl > 200:
            continue
        after = _after_samples(recent, lowest_sample, CLIMB_OUT_LOOKAHEAD_SECONDS)
        if not _climbed_out(after, lowest_agl, airport):
            continue  # never climbed out -> landing/other, not a departure
        if _approached_from_altitude(recent, lt, airport):
            continue  # descended in first -> touch-and-go/low-approach, not a takeoff
        # NOTE: misclassification risk runs the other direction too. With sparse
        # ADS-B coverage, or a genuinely low/tight pattern that never clears 500 ft
        # AGL, a real touch-and-go can fail the approach guard above and fall
        # through to be counted here as a takeoff instead. `total` op count is
        # preserved either way — only the takeoff/T&G split is biased.
        events.append(_build_runway_event("takeoff", ep, airport, runways))
    return events


def _pass_events_from_inside(samples: list[dict], airport: Airport, params: ScanParams, inside: list[dict]) -> list[dict]:
    if not inside:
        return []

    groups: list[list[dict]] = []
    current: list[dict] = []
    for row in inside:
        if current and row["timestamp"] - current[-1]["timestamp"] > 90:
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)

    events = []
    for group in groups:
        latest = next((sample for sample in reversed(samples) if sample["timestamp"] <= group[-1]["timestamp"]), samples[-1])
        best = min(group, key=lambda row: row["distance_nm"])
        bucket = int(group[0]["timestamp"] // 60)
        altitudes = [row["agl"] for row in group if row.get("agl") is not None]
        geometry_key = pass_geometry_key(params)
        events.append({
            "id": _event_id("pass", latest["icao24"], params.airport_icao, geometry_key, bucket),
            "type": "pass_over_user",
            "icao24": latest["icao24"],
            "callsign": latest.get("callsign") or latest["icao24"].upper(),
            "timestamp": int(group[-1]["timestamp"]),
            "airport_icao": airport.icao,
            "min_altitude_ft_agl": int(round(min(altitudes))) if altitudes else None,
            "avg_altitude_ft_agl": int(round(mean(altitudes))) if altitudes else None,
            "closest_horizontal_nm": round(best["distance_nm"], 3),
            "pass_times": [int(row["timestamp"]) for row in group],
            "pass_geometry_key": geometry_key,
            "pass_radius_nm": params.pass_radius_nm,
        })
    return events


def detect_passes(track: list[dict], airport: Airport, params: ScanParams) -> list[dict]:
    recent = samples_in_last(track, 12 * 60)
    if len(recent) < 2:
        return []

    # Pass = any track segment that bisects the user's home circle. We do NOT
    # filter by altitude — pattern aircraft at exactly pattern altitude (5000
    # AGL at GA airports) sit right on the old 5000ft ceiling and got dropped.
    # If callers want altitude filtering they can do it after the fact.
    inside = pass_crossing_rows(recent, airport, params, ceiling_ft=None)
    return _pass_events_from_inside(recent, airport, params, inside)


def detect_passes_over_period(
    track: list[dict],
    airport: Airport,
    params: ScanParams,
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    samples = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if start_ts - MAX_SEGMENT_GAP_SECONDS <= sample["timestamp"] <= end_ts + MAX_SEGMENT_GAP_SECONDS
        and sample.get("lat") is not None
        and sample.get("lon") is not None
    ]
    if len(samples) < 2:
        return []
    # Geometric pass = any segment crossing the home circle, regardless of
    # altitude. Matches the on-screen rendering of "lines through the dashed
    # red ring."
    inside = pass_crossing_rows(samples, airport, params, start_ts, end_ts, ceiling_ft=None)
    return _pass_events_from_inside(samples, airport, params, inside)


def detect_events(track: list[dict], airport: Airport, runways: list[dict], params: ScanParams) -> list[dict]:
    if not track:
        return []
    events = []
    circles = detect_circles(track, airport, params, runways)
    events.extend(circles)
    # A touch-and-go is a circle whose loop crossed the runway (see
    # touch_and_gos_from_circles); the episode detector below now emits only
    # low approaches and landings, never touch-and-gos.
    events.extend(touch_and_gos_from_circles(circles))
    events.extend(detect_touch_and_gos(track, airport, runways))
    events.extend(detect_passes(track, airport, params))
    return events


def detect_events_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    params: ScanParams,
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    samples = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if start_ts - 20 * 60 <= sample["timestamp"] <= end_ts + 120
    ]
    events_by_id = {}
    circles = detect_circles_over_period(samples, airport, params, start_ts, end_ts, runways)
    for event in circles:
        events_by_id[event["id"]] = event
    for event in touch_and_gos_from_circles(circles):
        events_by_id[event["id"]] = event
    for event in detect_touch_and_gos_over_period(samples, airport, runways, start_ts, end_ts):
        events_by_id[event["id"]] = event
    for event in detect_landings_over_period(samples, airport, runways, start_ts, end_ts):
        events_by_id[event["id"]] = event
    for event in detect_takeoffs_over_period(samples, airport, runways, start_ts, end_ts):
        events_by_id[event["id"]] = event
    for event in detect_passes_over_period(samples, airport, params, start_ts, end_ts):
        events_by_id[event["id"]] = event
    return sorted(events_by_id.values(), key=lambda event: event["timestamp"])


def event_counts(events: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for event in events:
        counts[event["type"]] += 1
    return {
        "circles": counts["circle"],
        "touch_and_gos": counts["touch_and_go"],
        "low_approaches": counts["low_approach"],
        "landings": counts["landing"],
        "takeoffs": counts["takeoff"],
        "passes": counts["pass_over_user"],
    }
