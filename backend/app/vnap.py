from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import db as _db
from .flow import headwind_component


AXES = [
    "tightness", "altitude", "timeofday", "tg_volume",
    "circle_restraint", "left_traffic", "runway29",
]


@dataclass(frozen=True)
class VnapRuleset:
    # Quiet window (airport-local hours). Compliant = op inside [start, end).
    quiet_start_hour: int = 8      # 08:00
    quiet_end_hour: int = 20       # 20:00
    # Altitude: >= target AGL over the (whole) area -> 100; scales to 0 at zero.
    agl_target_ft: float = 1000.0
    agl_zero_ft: float = 0.0
    # Pattern tightness scores the share of pattern time spent outside the VNAP
    # corridor (see off_pattern_fraction), so it needs no distance calibration.
    # It does need enough laps to mean anything: below this the axis is skipped.
    # Transients that fly one wide loop near the field get matched to a pattern
    # they never flew; on 7d of KLMO data they were 35% of scored aircraft, all
    # pinned at the ceiling, with a median of 3 ops against 27 for real flyers.
    tightness_min_circles: int = 5
    # Per-session limits.
    tg_per_session_limit: int = 10
    circle_per_session_limit: int = 4
    session_gap_min: int = 60
    # Preferred runway + its approximate heading (deg) for the wind-favored test.
    preferred_runway_id: str = "29"
    preferred_runway_heading_deg: float = 290.0
    # A plane isn't "judged" until it's doing real pattern work: the VNAP score
    # stays 0 until it has at least this many touch-and-gos AND circles (laps).
    # The circle floor is in DE-INFLATED laps: a circle is now one physical lap,
    # so ~3 laps of runway pattern work is enough signal to score. (It was 10
    # back when circles were over-counted ~2.8x per lap.)
    score_min_tg: int = 1
    score_min_circles: int = 3


_KLMO = VnapRuleset()
# Generic default for any other airport (no preferred runway assumptions -> the
# runway29 axis just yields None because preferred ops/favored counts stay 0).
_DEFAULT = VnapRuleset(preferred_runway_id="", preferred_runway_heading_deg=0.0)


def ruleset_for(icao: str) -> VnapRuleset:
    return _KLMO if icao.upper() == "KLMO" else _DEFAULT


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def off_pattern_fraction(rows: list) -> float | None:
    """Fraction of pattern time spent OUTSIDE the VNAP corridor.

    Time-weighted across the aircraft's laps — sum(time_off) / sum(time_total) —
    not a mean of per-lap percentages, so a long lap spent outside the corridor
    is not cancelled by a short one inside it. Laps with no timing (deviation
    never computed, e.g. the airport has no drawn pattern) are ignored.
    """
    off = total = 0
    for row in rows:
        t = row["time_total_s"]
        o = row["time_off_pattern_s"]
        if not t or o is None:
            continue
        off += int(o)
        total += int(t)
    if total <= 0:
        return None
    return round(off / total, 3)


def off_pattern_score(fraction: float | None) -> float | None:
    """Compliance from the off-pattern fraction. Inverted to a VIOLATION score
    by the caller, so an aircraft outside the corridor 68% of the time
    publishes a tightness of 68.

    This replaced a mean-perpendicular-distance score, which read a mild 29 for
    an aircraft that sat outside the corridor two thirds of every lap: averaging
    distance lets the on-corridor stretches mask the excursions. Time outside
    the corridor is already computed per lap, is in real units, and neither
    saturates nor needs a calibration constant.
    """
    if fraction is None:
        return None
    return round(_clamp(100.0 * (1.0 - fraction)), 1)


def altitude_score(typical_agl_ft: float | None, rules: VnapRuleset) -> float | None:
    if typical_agl_ft is None:
        return None
    span = rules.agl_target_ft - rules.agl_zero_ft
    return round(_clamp(100.0 * (typical_agl_ft - rules.agl_zero_ft) / span), 1)


def timeofday_score(in_window: int, total: int) -> float | None:
    if not total:
        return None
    return round(_clamp(100.0 * in_window / total), 1)


def left_traffic_score(left: int, known: int) -> float | None:
    if not known:
        return None
    return round(_clamp(100.0 * left / known), 1)


def runway_pref_score(on_pref_when_favored: int, favored_total: int) -> float | None:
    if not favored_total:
        return None
    return round(_clamp(100.0 * on_pref_when_favored / favored_total), 1)


def _session_limit_score(per_session_counts: list[int], limit: int) -> float | None:
    if not per_session_counts:
        return None
    scores = [100.0 if c <= limit else 100.0 * limit / c for c in per_session_counts]
    return round(sum(scores) / len(scores), 1)


def tg_volume_score(tg_per_session: list[int], rules: VnapRuleset) -> float | None:
    return _session_limit_score(tg_per_session, rules.tg_per_session_limit)


def circle_restraint_score(circles_per_session: list[int], rules: VnapRuleset) -> float | None:
    return _session_limit_score(circles_per_session, rules.circle_per_session_limit)


def composite_score(scores: dict, rules: VnapRuleset) -> float | None:
    vals = [v for v in scores.values() if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 1)


def group_sessions(timestamps_sorted: list[int], gap_min: int) -> list[list[int]]:
    gap = gap_min * 60
    sessions: list[list[int]] = []
    current: list[int] = []
    for ts in timestamps_sorted:
        if current and ts - current[-1] > gap:
            sessions.append(current)
            current = []
        current.append(ts)
    if current:
        sessions.append(current)
    return sessions


_OPERATION_TYPES = ("landing", "takeoff", "touch_and_go")


def compute_aircraft_compliance(conn: sqlite3.Connection, icao: str,
                                start_ts: int, end_ts: int,
                                icao24s: list[str] | None = None) -> dict:
    icao = icao.upper()
    rules = ruleset_for(icao)
    tz = _db.airport_timezone(conn, icao)

    # Optional scope-down to specific aircraft (scan hot path: only offenders'
    # scores are consumed, so filtering here yields identical per-aircraft
    # scores -- each is computed purely from its own rows -- with far less
    # work than scanning every operation for the airport in the window.
    filtered = [i.lower() for i in icao24s] if icao24s else None

    if filtered:
        placeholders = ",".join("?" * len(filtered))
        rows_sql = (
            "SELECT o.icao24 AS icao24, o.callsign AS callsign, o.registration AS registration, "
            "       o.timestamp AS ts, o.type AS type, o.deviation_mean_nm AS dev, "
            "       o.time_off_pattern_s AS time_off_pattern_s, o.time_total_s AS time_total_s, "
            "       o.turn_direction AS turn, o.runway_id AS runway, "
            "       o.wind_from_deg AS wind_from, o.wind_speed_kt AS wind_speed, "
            "       o.min_altitude_ft_agl AS min_agl, "
            "       o.headwind_kt AS headwind, o.runway_heading_deg AS rwy_heading, "
            "       (SELECT reg.owner_type FROM aircraft_registry reg "
            "        WHERE reg.icao_hex = upper(o.icao24) LIMIT 1) AS owner_type, "
            "       (SELECT reg.model FROM aircraft_registry reg "
            "        WHERE reg.icao_hex = upper(o.icao24) LIMIT 1) AS model "
            "FROM operations o "
            f"WHERE o.icao=? AND o.timestamp BETWEEN ? AND ? AND o.icao24 IN ({placeholders}) "
            "ORDER BY o.timestamp ASC"
        )
        rows_params = (icao, start_ts, end_ts, *filtered)
    else:
        rows_sql = (
            "SELECT o.icao24 AS icao24, o.callsign AS callsign, o.registration AS registration, "
            "       o.timestamp AS ts, o.type AS type, o.deviation_mean_nm AS dev, "
            "       o.time_off_pattern_s AS time_off_pattern_s, o.time_total_s AS time_total_s, "
            "       o.turn_direction AS turn, o.runway_id AS runway, "
            "       o.wind_from_deg AS wind_from, o.wind_speed_kt AS wind_speed, "
            "       o.min_altitude_ft_agl AS min_agl, "
            "       o.headwind_kt AS headwind, o.runway_heading_deg AS rwy_heading, "
            "       (SELECT reg.owner_type FROM aircraft_registry reg "
            "        WHERE reg.icao_hex = upper(o.icao24) LIMIT 1) AS owner_type, "
            "       (SELECT reg.model FROM aircraft_registry reg "
            "        WHERE reg.icao_hex = upper(o.icao24) LIMIT 1) AS model "
            "FROM operations o "
            "WHERE o.icao=? AND o.timestamp BETWEEN ? AND ? "
            "ORDER BY o.timestamp ASC"
        )
        rows_params = (icao, start_ts, end_ts)

    rows = conn.execute(rows_sql, rows_params).fetchall()


    # Report counts + cowboy counts + owner overrides, batched (avoid N+1).
    # NOTE: report_counts is a GLOBAL lifetime, cross-airport complaint tally
    # (aircraft_report_counts is not window- or airport-scoped) -- intentional,
    # matching the app's existing report semantics elsewhere.
    if filtered:
        placeholders = ",".join("?" * len(filtered))
        report_counts = {
            r["icao24"]: r["report_count"]
            for r in conn.execute(
                f"SELECT icao24, report_count FROM aircraft_report_counts WHERE icao24 IN ({placeholders})",
                filtered,
            ).fetchall()
        }
    else:
        report_counts = {
            r["icao24"]: r["report_count"]
            for r in conn.execute("SELECT icao24, report_count FROM aircraft_report_counts").fetchall()
        }
    cowboy_counts: dict[str, int] = {}
    if filtered:
        placeholders = ",".join("?" * len(filtered))
        cowboy_rows = conn.execute(
            "SELECT cowboy_icao24 AS icao24, COUNT(*) AS n FROM runway_changes "
            f"WHERE icao=? AND changed_at BETWEEN ? AND ? AND cowboy_icao24 IS NOT NULL "
            f"AND cowboy_icao24 IN ({placeholders}) "
            "GROUP BY cowboy_icao24",
            (icao, start_ts, end_ts, *filtered),
        ).fetchall()
    else:
        cowboy_rows = conn.execute(
            "SELECT cowboy_icao24 AS icao24, COUNT(*) AS n FROM runway_changes "
            "WHERE icao=? AND changed_at BETWEEN ? AND ? AND cowboy_icao24 IS NOT NULL "
            "GROUP BY cowboy_icao24",
            (icao, start_ts, end_ts),
        ).fetchall()
    for r in cowboy_rows:
        cowboy_counts[r["icao24"]] = r["n"]
    owner_overrides = _db.current_owner_overrides(conn)

    by_ac: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_ac.setdefault(row["icao24"], []).append(row)

    aircraft = []
    for icao24, ac_rows in by_ac.items():
        scores = _score_aircraft(ac_rows, rules, tz)
        callsign = next((r["callsign"] for r in reversed(ac_rows) if r["callsign"]), icao24.upper())
        registration = next((r["registration"] for r in ac_rows if r["registration"]), None)
        inferred = next((r["owner_type"] for r in ac_rows if r["owner_type"]), "unknown")
        override = owner_overrides.get(icao24)
        owner_class = override if override else inferred
        owner_source = "community" if override else "inferred"
        model = next((r["model"] for r in ac_rows if r["model"]), None)
        operations = sum(1 for r in ac_rows if r["type"] in _OPERATION_TYPES)
        circles = sum(1 for r in ac_rows if r["type"] == "circle")
        tgs = sum(1 for r in ac_rows if r["type"] == "touch_and_go")
        devs = [r["dev"] for r in ac_rows if r["type"] == "circle" and r["dev"] is not None]
        # VNAP stays 0 until the aircraft is doing real pattern work (>= 1 T&G AND
        # >= score_min_circles laps); otherwise there isn't enough signal to judge it.
        vnap_score = composite_score(scores, rules)
        if not (tgs >= rules.score_min_tg and circles >= rules.score_min_circles):
            vnap_score = 0.0
        aircraft.append({
            "icao24": icao24,
            "callsign": callsign,
            "registration": registration,
            "tail": registration or callsign,
            "aircraft_type": model,
            "owner_class": owner_class,
            "owner_source": owner_source,
            "vnap_score": vnap_score,
            "reports": report_counts.get(icao24, 0),
            "operations": operations,
            "touch_and_gos": tgs,
            "cowboy_count": cowboy_counts.get(icao24, 0),
            "deviation_mean_nm": round(sum(devs) / len(devs), 3) if devs else None,
            "circles": circles,
            "scores": scores,
            "metrics": _metrics_aircraft(ac_rows, rules, tz),
        })

    averages = _averages(aircraft, rules)
    return {"axes": AXES, "averages": averages, "aircraft": aircraft}


def _metrics_aircraft(rows, rules: VnapRuleset, tz: str | None) -> dict:
    """Real-unit per-axis metrics for the CSV export (distinct from the 0-100 scores)."""
    total = len(rows)
    # Per-day rates use only the days THIS aircraft was actually present (distinct
    # local calendar days with >=1 op), not the whole window.
    active_days = max(1, len({_db.local_day_key(r["ts"], tz) for r in rows}))
    # altitude: % of over-home passes below 1000 ft AGL (the only ops carrying real altitude)
    pass_agls = [r["min_agl"] for r in rows if r["type"] == "pass_over_user" and r["min_agl"] is not None]
    altitude = round(100.0 * sum(1 for a in pass_agls if a < rules.agl_target_ft) / len(pass_agls), 1) if pass_agls else None
    # timeofday: % of ops OUTSIDE the 8-8 local window
    in_window = sum(1 for r in rows if rules.quiet_start_hour <= _db.local_hour(r["ts"], tz) < rules.quiet_end_hour)
    timeofday = round(100.0 * (total - in_window) / total, 1) if total else None
    # tg_volume: touch-and-gos per active day minus the 10/day limit (can be negative)
    tgs = sum(1 for r in rows if r["type"] == "touch_and_go")
    tg_volume = round(tgs / active_days - rules.tg_per_session_limit, 2)
    # circle_restraint: circles per active day
    circles = sum(1 for r in rows if r["type"] == "circle")
    circle_restraint = round(circles / active_days, 2)
    # left_traffic: % of known-direction (pattern) ops that were left-turning
    known = sum(1 for r in rows if r["turn"] in ("left", "right"))
    left = sum(1 for r in rows if r["turn"] == "left")
    left_traffic = round(100.0 * left / known, 1) if known else None
    # takeoff-based runway metrics
    takeoffs = [r for r in rows if r["type"] == "takeoff" and r["runway"]]

    def _hw(r):
        if r["headwind"] is not None:
            return r["headwind"]
        return headwind_component(r["rwy_heading"], r["wind_from"], r["wind_speed"])

    runway29 = round(100.0 * sum(1 for r in takeoffs if r["runway"] == rules.preferred_runway_id) / len(takeoffs), 1) if takeoffs else None
    tw = [r for r in takeoffs if _hw(r) is not None]
    rwy_against = round(100.0 * sum(1 for r in tw if _hw(r) < 0) / len(tw), 1) if tw else None
    return {
        "altitude": altitude, "timeofday": timeofday, "tg_volume": tg_volume,
        "circle_restraint": circle_restraint, "left_traffic": left_traffic,
        "preferred_runway": runway29, "rwy_against": rwy_against,
    }


def _score_aircraft(rows: list, rules: VnapRuleset, tz: str | None) -> dict:
    # tightness: share of pattern time spent OUTSIDE the VNAP corridor. Scored
    # from time, not mean distance -- an aircraft can average a modest 0.29nm
    # off-centreline while actually sitting outside the corridor 68% of the lap.
    circle_rows = [r for r in rows if r["type"] == "circle"]
    off_pattern = (
        off_pattern_fraction(circle_rows)
        if len(circle_rows) >= rules.tightness_min_circles else None
    )

    # altitude: how low the aircraft flew over homes/town. Only pass-over-user ops
    # measure altitude over a residence (runway ops carry only touchdown lows; the
    # circle detector never populates min_altitude_ft_agl). None => axis skipped.
    pass_agls = sorted(
        r["min_agl"] for r in rows
        if r["type"] == "pass_over_user" and r["min_agl"] is not None
    )
    typical_agl = pass_agls[len(pass_agls) // 2] if pass_agls else None

    # timeofday: fraction of ALL traffic (incl. circles) inside the local quiet
    # window [start, end) -- noise compliance applies to every flight, unlike the
    # `operations` count field (landing+takeoff+tg only).
    total = len(rows)
    in_window = 0
    for r in rows:
        hour = _db.local_hour(r["ts"], tz)
        if rules.quiet_start_hour <= hour < rules.quiet_end_hour:
            in_window += 1

    # tg_volume / circle_restraint: per-session counts.
    tg_ts = sorted(r["ts"] for r in rows if r["type"] == "touch_and_go")
    circle_ts = sorted(r["ts"] for r in rows if r["type"] == "circle")
    tg_sessions = [len(s) for s in group_sessions(tg_ts, rules.session_gap_min)]
    circle_sessions = [len(s) for s in group_sessions(circle_ts, rules.session_gap_min)]

    # left_traffic: fraction left of ALL traffic (incl. circles) with a known
    # direction -- circles carry turn_direction, so they count here by design,
    # distinct from the `operations` count field (landing+takeoff+tg only).
    known = sum(1 for r in rows if r["turn"] in ("left", "right"))
    left = sum(1 for r in rows if r["turn"] == "left")

    # runway29: of ops where the preferred runway was wind-favored, fraction that used it.
    favored_total = 0
    on_pref = 0
    for r in rows:
        if not r["runway"] or r["wind_from"] is None or r["wind_speed"] is None:
            continue
        hw = headwind_component(rules.preferred_runway_heading_deg, r["wind_from"], r["wind_speed"])
        # Favored gate per spec: headwind component >= 0 (not strictly > 0).
        if hw is not None and hw >= 0:
            favored_total += 1
            if r["runway"] == rules.preferred_runway_id:
                on_pref += 1

    compliance = {
        "tightness": off_pattern_score(off_pattern),
        "altitude": altitude_score(float(typical_agl) if typical_agl is not None else None, rules),
        "timeofday": timeofday_score(in_window, total),
        "tg_volume": tg_volume_score(tg_sessions, rules),
        "circle_restraint": circle_restraint_score(circle_sessions, rules),
        "left_traffic": left_traffic_score(left, known),
        "runway29": runway_pref_score(on_pref, favored_total),
    }
    # VNAP is reported as a VIOLATION score: 0 = fully compliant, climbing toward
    # 100 as noise-abatement infractions accumulate. The scorer helpers above
    # return per-axis COMPLIANCE (100 = good); invert here so the whole surface
    # (per-axis scores + composite + averages) reads as "higher = worse".
    return {axis: (None if v is None else round(100.0 - v, 1)) for axis, v in compliance.items()}


def _averages(aircraft: list[dict], rules: VnapRuleset) -> dict:
    # NOTE: `composite` below is the mean of PER-AIRCRAFT composites, not the
    # composite of these per-axis averages -- a Phase-3 radar "average" polygon
    # built from the per-axis averages will not exactly equal this composite.
    out: dict[str, float | None] = {}
    for axis in AXES:
        vals = [a["scores"][axis] for a in aircraft if a["scores"][axis] is not None]
        out[axis] = round(sum(vals) / len(vals), 1) if vals else None
    comps = [a["vnap_score"] for a in aircraft if a["vnap_score"] is not None]
    out["composite"] = round(sum(comps) / len(comps), 1) if comps else None
    return out
