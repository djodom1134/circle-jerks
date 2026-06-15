from __future__ import annotations

import hashlib
from collections import defaultdict
from statistics import mean

from .db import Airport
from .domain import ScanParams
from .geo import Point, bearing_deg, closest_segment_approach_nm, distance_nm, heading_delta_deg

MAX_SEGMENT_GAP_SECONDS = 90
MIN_CIRCLE_SAMPLES = 6
MIN_CIRCLE_PATH_NM = 3.0
MIN_CIRCLE_DURATION_SECONDS = 120
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
        return {
            "id": _event_id("circle", current["icao24"], airport.icao, int(timestamp // 90)),
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


def detect_circles(track: list[dict], airport: Airport, params: ScanParams) -> list[dict]:
    return _detect_home_airport_crossings(samples_in_last(track, 20 * 60), airport, params)


def detect_circles_over_period(
    track: list[dict],
    airport: Airport,
    params: ScanParams,
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    samples = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if start_ts - 20 * 60 <= sample["timestamp"] <= end_ts
    ]
    return _detect_home_airport_crossings(samples, airport, params, start_ts, end_ts)


# Altitude cap above which crossings of the home↔airport line don't count as
# pattern work (airliners passing through at FL200 aren't "circling").
MAX_CIRCLE_CROSS_ALTITUDE_FT_AGL = 5000


def _ccw(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
    """2D cross product sign — positive if a→b→c is counterclockwise."""
    return (by - ay) * (cx - ax) - (bx - ax) * (cy - ay)


def _segments_cross(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """True if segments ab and cd properly intersect (strict, non-collinear)."""
    d1 = _ccw(c[0], c[1], d[0], d[1], a[0], a[1])
    d2 = _ccw(c[0], c[1], d[0], d[1], b[0], b[1])
    d3 = _ccw(a[0], a[1], b[0], b[1], c[0], c[1])
    d4 = _ccw(a[0], a[1], b[0], b[1], d[0], d[1])
    return (d1 * d2) < 0 and (d3 * d4) < 0


def _detect_home_airport_crossings(
    samples: list[dict],
    airport: Airport,
    params: ScanParams,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[dict]:
    """Each time an aircraft's track crosses the line segment from the user's
    home to the airport, count it as one "circle."

    Simpler and more intuitive than the old cumulative-turn detector — a closed
    pattern around the airport that wraps the airport but not the home crosses
    this line once per lap. Crossings are bucketed at 90-second granularity so
    GPS jitter at the crossing point doesn't double-count.
    """
    if len(samples) < 2:
        return []

    # Segment endpoints. Geographic coordinates are fine for short distances —
    # we never approach the antimeridian or polar regions.
    home = (params.user_lat, params.user_lon)
    apt = (airport.lat, airport.lon)

    # Pre-filter samples to those potentially relevant — must have lat/lon,
    # and must be in the ring around the airport (samples way out at cruise
    # altitude passing through aren't pattern work).
    sorted_samples = [
        s for s in sorted(samples, key=lambda row: row["timestamp"])
        if s.get("lat") is not None and s.get("lon") is not None
    ]
    if len(sorted_samples) < 2:
        return []

    events: list[dict] = []
    seen_buckets: set[int] = set()
    for a, b in zip(sorted_samples, sorted_samples[1:]):
        gap = int(b["timestamp"] - a["timestamp"])
        if gap <= 0 or gap > MAX_SEGMENT_GAP_SECONDS:
            continue
        if start_ts is not None and b["timestamp"] < start_ts:
            continue
        if end_ts is not None and a["timestamp"] > end_ts:
            continue

        if not _segments_cross(
            (a["lat"], a["lon"]),
            (b["lat"], b["lon"]),
            home,
            apt,
        ):
            continue

        # Altitude filter — overflight at cruise altitude isn't a pattern lap.
        a_agl = altitude_agl(a, airport)
        b_agl = altitude_agl(b, airport)
        agl = min(filter(lambda v: v is not None, [a_agl, b_agl]), default=None)
        if agl is not None and agl > MAX_CIRCLE_CROSS_ALTITUDE_FT_AGL:
            continue

        timestamp = int((a["timestamp"] + b["timestamp"]) / 2)
        if start_ts is not None and timestamp < start_ts:
            continue
        if end_ts is not None and timestamp > end_ts:
            continue

        bucket = timestamp // 30  # ~30s anti-jitter dedup (GPS noise at the line)
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)

        events.append({
            "id": _event_id("circle", a["icao24"], airport.icao, bucket),
            "type": "circle",
            "icao24": a["icao24"],
            "callsign": a.get("callsign") or a["icao24"].upper(),
            "timestamp": timestamp,
            "airport_icao": airport.icao,
            "alt_band_ft": [int(agl) if agl is not None else None, int(agl) if agl is not None else None],
            "detection_method": "home_airport_line_crossing",
        })
    return events


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


def detect_touch_and_gos_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    recent = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if start_ts - 120 <= sample["timestamp"] <= end_ts + 120
    ]
    if len(recent) < 4:
        return []

    low_by_bucket: dict[int, list[tuple[dict, float, dict | None, float, float | None]]] = defaultdict(list)
    for sample in recent:
        if sample["timestamp"] < start_ts or sample["timestamp"] > end_ts:
            continue
        agl = altitude_agl(sample, airport)
        runway, runway_dist = _nearest_runway(sample, runways)
        # velocity_kt is optional — archive samples don't preserve it. We use
        # it only as a tightening filter when available.
        speed = sample.get("velocity_kt")
        if agl is not None and runway_dist <= 1.5:
            low_by_bucket[int(sample["timestamp"] // 180)].append((sample, agl, runway, runway_dist, speed))

    events = []
    for bucket, low_samples in sorted(low_by_bucket.items()):
        if not low_samples:
            continue

        lowest_sample, lowest_agl, runway, _, speed = min(low_samples, key=lambda row: row[1])
        after = [
            sample for sample in recent
            if lowest_sample["timestamp"] < sample["timestamp"] <= lowest_sample["timestamp"] + 120
        ]
        if not after:
            continue

        latest_agl = altitude_agl(after[-1], airport)
        climbed = latest_agl is not None and latest_agl - lowest_agl >= 150
        vertical_up = any((sample.get("vertical_rate_fpm") or 0) > 250 for sample in after)
        on_ground_seconds = sum(1 for sample in after if sample.get("on_ground")) * 30
        # Speed filter is best-effort: if we have it, require ≤90kt; if we
        # don't (archive samples), trust the altitude + climb-back-up signature.
        speed_ok = speed is None or speed <= 90
        event_type = None
        if lowest_agl <= 200 and speed_ok and (climbed or vertical_up) and on_ground_seconds <= 60:
            event_type = "touch_and_go" if lowest_agl <= 50 else "low_approach"
        if not event_type:
            continue

        # Pick the *directional* runway (e.g. 11 vs 29) from the aircraft's
        # heading at touchdown. Falls back to the positional nearest runway
        # if the heading-based match fails (no heading recorded, etc.).
        directional = _runway_for_direction(lowest_sample, runways)
        used_runway = directional or runway
        runway_heading = used_runway.get("heading_deg") if used_runway else None
        events.append({
            "id": _event_id(event_type, lowest_sample["icao24"], airport.icao, bucket),
            "type": event_type,
            "icao24": lowest_sample["icao24"],
            "callsign": lowest_sample.get("callsign") or lowest_sample["icao24"].upper(),
            "timestamp": int(lowest_sample["timestamp"]),
            "airport_icao": airport.icao,
            "runway_id": used_runway["runway_id"] if used_runway else None,
            "runway_heading_deg": int(runway_heading) if runway_heading is not None else None,
            # Kept for back-compat with older clients reading `runway_used`.
            "runway_used": used_runway["runway_id"] if used_runway else None,
            "min_altitude_ft_agl": int(lowest_agl),
        })
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
    events.extend(detect_circles(track, airport, params))
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
    for event in detect_circles_over_period(samples, airport, params, start_ts, end_ts):
        events_by_id[event["id"]] = event
    for event in detect_touch_and_gos_over_period(samples, airport, runways, start_ts, end_ts):
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
        "passes": counts["pass_over_user"],
    }
