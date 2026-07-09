from __future__ import annotations

from dataclasses import dataclass


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
    # Pattern tightness: mean deviation (nm) at which the score hits 0.
    dev_floor_nm: float = 1.0
    # Per-session limits.
    tg_per_session_limit: int = 10
    circle_per_session_limit: int = 4
    session_gap_min: int = 60
    # Preferred runway + its approximate heading (deg) for the wind-favored test.
    preferred_runway_id: str = "29"
    preferred_runway_heading_deg: float = 290.0


_KLMO = VnapRuleset()
# Generic default for any other airport (no preferred runway assumptions -> the
# runway29 axis just yields None because preferred ops/favored counts stay 0).
_DEFAULT = VnapRuleset(preferred_runway_id="", preferred_runway_heading_deg=0.0)


def ruleset_for(icao: str) -> VnapRuleset:
    return _KLMO if icao.upper() == "KLMO" else _DEFAULT


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def tightness_score(avg_dev_nm: float | None, rules: VnapRuleset) -> float | None:
    if avg_dev_nm is None:
        return None
    return round(_clamp(100.0 * (1.0 - avg_dev_nm / rules.dev_floor_nm)), 1)


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
