from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from .tone import ToneSliders, prompt_bands

FORBIDDEN_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bidiot\b",
        r"\bjerk\b",
        r"\bmalicious\b",
        r"\bintentionally\b",
        r"\btrying to\b",
        r"\basshole\b",
        r"\bfuck\b",
        r"\bshit\b",
    ]
]


@dataclass(frozen=True)
class MessagePreferences:
    include_all_detail: bool = True
    include_elevation: bool = True
    include_circles: bool = True
    include_altitude_over_house: bool = True
    include_db_at_home: bool = True


@dataclass(frozen=True)
class ComplaintContext:
    airport_name: str
    airport_icao: str
    user_location_label: str
    time_window_label: str
    callsign: str
    icao24: str
    aircraft_type: str
    observed_from: str
    observed_to: str
    circles: int
    avg_radius: float | None
    alt_min: int | None
    alt_max: int | None
    touch_and_gos: int
    low_approaches: int
    passes: int
    avg_altitude_user: int | None
    min_altitude_user: int | None
    quiet_hours_events: int
    peak_hours: str
    origin_airport: str
    runway_used: str | None
    airport_elevation_ft: int | None
    previous_report_count: int
    avg_db_at_home: float | None = None
    peak_db_at_home: float | None = None
    audible_seconds_at_home: int | None = None
    message_preferences: MessagePreferences = field(default_factory=MessagePreferences)


@dataclass(frozen=True)
class AggregateComplaintContext:
    airport_name: str
    airport_icao: str
    user_location_label: str
    time_window_label: str
    observed_from: str
    observed_to: str
    aircraft_count: int
    callsigns: list[str]
    total_circles: int
    total_touch_and_gos: int
    total_low_approaches: int
    total_passes: int
    avg_altitude_user: int | None
    min_altitude_user: int | None
    airport_elevation_ft: int | None
    previous_report_total: int
    items: list[ComplaintContext]
    avg_db_at_home: float | None = None
    peak_db_at_home: float | None = None
    audible_seconds_at_home: int | None = None
    message_preferences: MessagePreferences = field(default_factory=MessagePreferences)


def build_prompt(context: ComplaintContext, sliders: ToneSliders) -> str:
    bands = prompt_bands(sliders)
    prefs = context.message_preferences
    return f"""You write per-incident aviation noise complaint descriptions for a resident to paste into an official form.
Produce 3 to 8 sentences, with length governed by the Detail slider. Use specific numbers and local time.
Use 12-hour clock labels with AM/PM only. Do not use 24-hour or military time.
Never speculate about pilot intent. Never use profanity or personal attacks. Blend the five tone sliders into a coherent voice.
Follow the message preferences exactly: include a fact only when the corresponding include flag is true.

TONE CONTROLS:
anger: {bands["anger"]}
niceness: {bands["niceness"]}
respect: {bands["respect"]}
detail: {bands["detail"]}
local_flavor: {bands["local"]}

MESSAGE PREFERENCES:
include_all_detail: {prefs.include_all_detail}
include_airport_elevation: {prefs.include_elevation}
include_number_of_circles: {prefs.include_circles}
include_altitude_over_house: {prefs.include_altitude_over_house}
include_db_at_home: {prefs.include_db_at_home}

DATA:
airport: {context.airport_name} ({context.airport_icao})
airport_field_elevation_ft_msl: {context.airport_elevation_ft if context.airport_elevation_ft is not None else "unknown"}
user_location_label: {context.user_location_label}
time_window_label: {context.time_window_label}
callsign: {context.callsign}
icao24: {context.icao24}
previous_reports_by_this_user_for_aircraft: {context.previous_report_count}
aircraft_type: {context.aircraft_type}
observed_from: {context.observed_from}
observed_to: {context.observed_to}
circles_count: {context.circles}
avg_loop_radius_nm: {context.avg_radius if context.avg_radius is not None else "unknown"}
alt_band_ft: {context.alt_min if context.alt_min is not None else "unknown"}-{context.alt_max if context.alt_max is not None else "unknown"}
touch_and_go_count: {context.touch_and_gos}
low_approach_count: {context.low_approaches}
passes_over_user_count: {context.passes}
avg_altitude_over_user_ft_agl: {context.avg_altitude_user if context.avg_altitude_user is not None else "unknown"}
min_altitude_over_user_ft_agl: {context.min_altitude_user if context.min_altitude_user is not None else "unknown"}
peak_db_at_home: {f"{context.peak_db_at_home:.1f}" if context.peak_db_at_home is not None else "unknown"}
avg_db_at_home_during_audible: {f"{context.avg_db_at_home:.1f}" if context.avg_db_at_home is not None else "unknown"}
audible_seconds_at_home: {context.audible_seconds_at_home if context.audible_seconds_at_home is not None else "unknown"}
quiet_hours_events: {context.quiet_hours_events}
peak_hours: {context.peak_hours}
origin_airport: {context.origin_airport}
runway_used: {context.runway_used or "unknown"}
"""


def build_aggregate_prompt(context: AggregateComplaintContext, sliders: ToneSliders) -> str:
    bands = prompt_bands(sliders)
    prefs = context.message_preferences
    aircraft_lines = []
    for item in context.items:
        aircraft_lines.append(
            "; ".join([
                f"{item.callsign} ({item.icao24})",
                f"origin {item.origin_airport}",
                f"circles {item.circles}",
                f"passes {item.passes}",
                f"avg_over_house_ft_agl {item.avg_altitude_user if item.avg_altitude_user is not None else 'unknown'}",
                f"previous_reports {item.previous_report_count}",
            ])
        )
    return f"""You write one consolidated aviation noise complaint for a resident to paste into an official form.
Do not write separate sections per aircraft. Produce one coherent complaint that summarizes all aircraft together.
Use 12-hour clock labels with AM/PM only. Do not use 24-hour or military time.
Never speculate about pilot intent. Never use profanity or personal attacks.
Follow the message preferences exactly: include a fact only when the corresponding include flag is true.

TONE CONTROLS:
anger: {bands["anger"]}
niceness: {bands["niceness"]}
respect: {bands["respect"]}
detail: {bands["detail"]}
local_flavor: {bands["local"]}

MESSAGE PREFERENCES:
include_all_detail: {prefs.include_all_detail}
include_airport_elevation: {prefs.include_elevation}
include_number_of_circles: {prefs.include_circles}
include_altitude_over_house: {prefs.include_altitude_over_house}
include_db_at_home: {prefs.include_db_at_home}

AGGREGATE DATA:
airport: {context.airport_name} ({context.airport_icao})
airport_field_elevation_ft_msl: {context.airport_elevation_ft if context.airport_elevation_ft is not None else "unknown"}
user_location_label: {context.user_location_label}
time_window_label: {context.time_window_label}
observed_from: {context.observed_from}
observed_to: {context.observed_to}
aircraft_count: {context.aircraft_count}
callsigns: {", ".join(context.callsigns)}
total_circles: {context.total_circles}
total_touch_and_go_count: {context.total_touch_and_gos}
total_low_approach_count: {context.total_low_approaches}
total_passes_over_user_count: {context.total_passes}
avg_altitude_over_user_ft_agl: {context.avg_altitude_user if context.avg_altitude_user is not None else "unknown"}
min_altitude_over_user_ft_agl: {context.min_altitude_user if context.min_altitude_user is not None else "unknown"}
peak_db_at_home: {f"{context.peak_db_at_home:.1f}" if context.peak_db_at_home is not None else "unknown"}
avg_db_at_home_during_audible: {f"{context.avg_db_at_home:.1f}" if context.avg_db_at_home is not None else "unknown"}
audible_seconds_at_home: {context.audible_seconds_at_home if context.audible_seconds_at_home is not None else "unknown"}
previous_reports_by_this_user_total: {context.previous_report_total}

AIRCRAFT DETAILS:
{chr(10).join(aircraft_lines)}
"""


def violates_guardrails(text: str) -> bool:
    return any(pattern.search(text) for pattern in FORBIDDEN_PATTERNS)


def deterministic_description(context: ComplaintContext) -> str:
    prefs = context.message_preferences
    parts = [
        f"{context.time_window_label.capitalize()}, aircraft {context.callsign} ({context.icao24}) was observed near {context.airport_name} ({context.airport_icao}).",
    ]
    if context.previous_report_count > 0:
        plural = "complaint" if context.previous_report_count == 1 else "complaints"
        parts.append(f"I have previously reported this same aircraft {context.previous_report_count} {plural}.")
    if prefs.include_all_detail:
        activity = []
        if prefs.include_circles:
            activity.append(f"{context.circles} circles")
        activity.extend([
            f"{context.touch_and_gos} touch-and-go operations",
            f"{context.low_approaches} low approaches",
            f"{context.passes} direct overflights of my location",
        ])
        parts.append(f"The activity included {', '.join(activity)}.")
    elif prefs.include_circles:
        parts.append(f"The aircraft was detected circling {context.circles} times.")
    if prefs.include_elevation and context.airport_elevation_ft is not None:
        parts.append(f"The reference airport field elevation is {context.airport_elevation_ft} ft MSL.")
    if prefs.include_altitude_over_house and context.avg_altitude_user is not None:
        parts.append(f"The average observed altitude over my location was {context.avg_altitude_user} ft AGL.")
    elif prefs.include_altitude_over_house and context.min_altitude_user is not None:
        parts.append(f"The lowest observed overflight altitude was {context.min_altitude_user} ft AGL.")
    if prefs.include_db_at_home and context.peak_db_at_home is not None and context.avg_db_at_home is not None:
        parts.append(
            f"Based on the recorded altitudes and aircraft positions, the peak estimated noise at my home "
            f"during this incident reached {context.peak_db_at_home:.1f} dB, with an average of "
            f"{context.avg_db_at_home:.1f} dB during audible passes."
        )
    elif prefs.include_db_at_home and context.peak_db_at_home is not None:
        parts.append(
            f"Estimated peak noise at my home from this aircraft was {context.peak_db_at_home:.1f} dB."
        )
    if prefs.include_all_detail and context.observed_from != "unknown" and context.observed_to != "unknown":
        parts.append(f"The relevant activity was observed from {context.observed_from} to {context.observed_to} local time.")
    if prefs.include_all_detail and context.peak_hours != "none":
        parts.append(f"Peak activity occurred around {context.peak_hours}.")
    parts.append("Please review this activity and consider whether additional noise-abatement outreach is appropriate.")
    return " ".join(parts)


def deterministic_aggregate_description(context: AggregateComplaintContext) -> str:
    prefs = context.message_preferences
    callsigns = ", ".join(context.callsigns[:8])
    parts = [
        f"{context.time_window_label.capitalize()}, I observed {context.aircraft_count} aircraft near {context.airport_name} ({context.airport_icao}): {callsigns}.",
    ]
    if context.previous_report_total > 0:
        plural = "complaint" if context.previous_report_total == 1 else "complaints"
        parts.append(f"My browser records show {context.previous_report_total} prior {plural} for aircraft in this group.")
    if prefs.include_all_detail:
        activity = []
        if prefs.include_circles:
            activity.append(f"{context.total_circles} total circles")
        activity.extend([
            f"{context.total_touch_and_gos} touch-and-go operations",
            f"{context.total_low_approaches} low approaches",
            f"{context.total_passes} direct overflights of my location",
        ])
        parts.append(f"The combined activity included {', '.join(activity)}.")
    elif prefs.include_circles:
        parts.append(f"Together, these aircraft were detected circling {context.total_circles} times.")
    if prefs.include_elevation and context.airport_elevation_ft is not None:
        parts.append(f"The reference airport field elevation is {context.airport_elevation_ft} ft MSL.")
    if prefs.include_altitude_over_house and context.avg_altitude_user is not None:
        parts.append(f"The average observed altitude over my location was {context.avg_altitude_user} ft AGL, with a lowest observed pass of {context.min_altitude_user} ft AGL.")
    if prefs.include_db_at_home and context.peak_db_at_home is not None and context.avg_db_at_home is not None:
        parts.append(
            f"Across these aircraft the peak estimated noise at my home reached {context.peak_db_at_home:.1f} dB, "
            f"with an average of {context.avg_db_at_home:.1f} dB during audible passes."
        )
    if prefs.include_all_detail and context.observed_from != "unknown" and context.observed_to != "unknown":
        parts.append(f"The relevant activity was observed from {context.observed_from} to {context.observed_to} local time.")
    parts.append("Please review this combined aircraft activity and consider appropriate noise-abatement follow-up.")
    return " ".join(parts)


async def generate_with_groq(api_key: str | None, model: str, prompt: str) -> str | None:
    if not api_key:
        return None
    async with httpx.AsyncClient(timeout=12.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You produce factual, civil aviation noise complaint descriptions. Use 12-hour AM/PM time, never military time."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.35,
                "max_tokens": 450,
            },
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
        text = payload["choices"][0]["message"]["content"].strip()
        return None if violates_guardrails(text) else text
