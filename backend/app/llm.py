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
    return f"""Write the complaint described by your instructions using only the facts below.

TONE CONTROLS:
anger: {bands["anger"]}
niceness: {bands["niceness"]}
respect: {bands["respect"]}
detail: {bands["detail"]}
local_flavor: {bands["local"]}

DATA:
airport: {context.airport_name} ({context.airport_icao})
user_location_label: {context.user_location_label}
time_window_label: {context.time_window_label}
aircraft_n_number: {context.callsign}
aircraft_type: {context.aircraft_type}
observed_from: {context.observed_from}
observed_to: {context.observed_to}
touch_and_go_count: {context.touch_and_gos}
passes_over_user_count: {context.passes}
avg_altitude_over_user_ft_agl: {context.avg_altitude_user if context.avg_altitude_user is not None else "unknown"}
lowest_altitude_over_user_ft_agl: {context.min_altitude_user if context.min_altitude_user is not None else "unknown"}
peak_db_at_home: {f"{context.peak_db_at_home:.1f}" if context.peak_db_at_home is not None else "unknown"}
quiet_hours_events: {context.quiet_hours_events}
peak_hours: {context.peak_hours}
origin_airport: {context.origin_airport}
previous_reports_by_this_user_for_aircraft: {context.previous_report_count}
"""


def build_aggregate_prompt(context: AggregateComplaintContext, sliders: ToneSliders) -> str:
    bands = prompt_bands(sliders)
    aircraft_lines = []
    for item in context.items:
        aircraft_lines.append(
            "; ".join([
                item.callsign,
                f"origin {item.origin_airport}",
                f"touch_and_gos {item.touch_and_gos}",
                f"passes {item.passes}",
                f"lowest_over_house_ft_agl {item.min_altitude_user if item.min_altitude_user is not None else 'unknown'}",
                f"previous_reports {item.previous_report_count}",
            ])
        )
    return f"""You write one consolidated aviation noise complaint for a resident to paste into an official form.
Do not write separate sections per aircraft. Produce one coherent complaint that summarizes all aircraft together, following your instructions.

TONE CONTROLS:
anger: {bands["anger"]}
niceness: {bands["niceness"]}
respect: {bands["respect"]}
detail: {bands["detail"]}
local_flavor: {bands["local"]}

AGGREGATE DATA:
airport: {context.airport_name} ({context.airport_icao})
user_location_label: {context.user_location_label}
time_window_label: {context.time_window_label}
observed_from: {context.observed_from}
observed_to: {context.observed_to}
aircraft_count: {context.aircraft_count}
n_numbers: {", ".join(context.callsigns)}
total_touch_and_go_count: {context.total_touch_and_gos}
total_passes_over_user_count: {context.total_passes}
avg_altitude_over_user_ft_agl: {context.avg_altitude_user if context.avg_altitude_user is not None else "unknown"}
lowest_altitude_over_user_ft_agl: {context.min_altitude_user if context.min_altitude_user is not None else "unknown"}
peak_db_at_home: {f"{context.peak_db_at_home:.1f}" if context.peak_db_at_home is not None else "unknown"}
previous_reports_by_this_user_total: {context.previous_report_total}

AIRCRAFT DETAILS:
{chr(10).join(aircraft_lines)}
"""


def violates_guardrails(text: str) -> bool:
    return any(pattern.search(text) for pattern in FORBIDDEN_PATTERNS)


def deterministic_description(context: ComplaintContext) -> str:
    parts = [
        f"{context.time_window_label.capitalize()}, aircraft {context.callsign} was observed conducting "
        f"repetitive pattern work near {context.airport_name} ({context.airport_icao}).",
    ]
    if context.previous_report_count > 0:
        plural = "complaint" if context.previous_report_count == 1 else "complaints"
        parts.append(f"I have previously reported this same aircraft {context.previous_report_count} {plural}.")
    parts.append(
        f"It performed {context.touch_and_gos} touch-and-go operations and made "
        f"{context.passes} direct overflights of my location."
    )
    if context.min_altitude_user is not None:
        parts.append(f"The lowest overflight of my home was about {context.min_altitude_user} ft above ground.")
    elif context.avg_altitude_user is not None:
        parts.append(f"The average overflight of my home was about {context.avg_altitude_user} ft above ground.")
    if context.peak_db_at_home is not None:
        parts.append(
            f"Based on the recorded altitudes and positions, the peak estimated noise at my home reached "
            f"{context.peak_db_at_home:.1f} dB."
        )
    if context.observed_from != "unknown" and context.observed_to != "unknown":
        parts.append(f"The activity ran from {context.observed_from} to {context.observed_to} local time.")
    parts.append("Please review this repetitive low-altitude activity and consider noise-abatement outreach.")
    return " ".join(parts)


def deterministic_aggregate_description(context: AggregateComplaintContext) -> str:
    callsigns = ", ".join(context.callsigns[:8])
    parts = [
        f"{context.time_window_label.capitalize()}, I observed {context.aircraft_count} aircraft conducting "
        f"repetitive pattern work near {context.airport_name} ({context.airport_icao}): {callsigns}.",
    ]
    if context.previous_report_total > 0:
        plural = "complaint" if context.previous_report_total == 1 else "complaints"
        parts.append(f"My browser records show {context.previous_report_total} prior {plural} for aircraft in this group.")
    parts.append(
        f"Together they made {context.total_touch_and_gos} touch-and-go operations and "
        f"{context.total_passes} direct overflights of my location."
    )
    if context.min_altitude_user is not None:
        parts.append(f"The lowest overflight of my home was about {context.min_altitude_user} ft above ground.")
    if context.peak_db_at_home is not None:
        parts.append(f"Across these aircraft the peak estimated noise at my home reached {context.peak_db_at_home:.1f} dB.")
    if context.observed_from != "unknown" and context.observed_to != "unknown":
        parts.append(f"The activity ran from {context.observed_from} to {context.observed_to} local time.")
    parts.append("Please review this combined low-altitude activity and consider noise-abatement follow-up.")
    return " ".join(parts)


DEFAULT_SYSTEM_PROMPT = (
    "You write a short aviation noise complaint that a resident will paste into an official form. "
    "Write 3 to 8 sentences in the first person, with length governed by the detail tone control. "
    "Focus entirely on how disruptive the aircraft is to people on the ground: its repetitive "
    "touch-and-go landings, the noise it makes, and how low it flies over the resident's home "
    "(call out the lowest pass). Lead with the aircraft's N-number when one is provided. "
    "Do not mention runway or airport field elevation. Do not mention the number of circles or the "
    "loop radius. Do not mention runway changes unless the DATA explicitly flags an unwarranted, "
    "against-the-wind change. Use specific numbers and 12-hour AM/PM local time; never use 24-hour "
    "or military time. Never speculate about the pilot's intent. Never use profanity or personal "
    "attacks. Blend the five tone controls into one coherent voice."
)


def runway_change_note(changes: list[dict]) -> str:
    """One-line prompt context about runway changes the wind did NOT favor
    (cowboy moves). `changes` are runway_changes rows (wind_favored_new=0), newest first."""
    if not changes:
        return ""
    recent = changes[0]
    who = recent.get("cowboy_callsign") or recent.get("cowboy_icao24") or "an aircraft"
    to_rwy = recent.get("to_runway_id") or "?"
    return (
        f"runway_changes_against_the_wind: {len(changes)} "
        f"(the active runway was changed to a direction the wind did NOT favor; "
        f"most recently to runway {to_rwy} by {who})"
    )


async def generate_with_groq(
    api_key: str | None, model: str, prompt: str, system_prompt: str | None = None
) -> str | None:
    if not api_key:
        return None
    # Timeout so the API endpoint still falls through to the deterministic
    # local draft instead of leaving the user staring at a spinner, now at 8s
    # to give custom system prompts a real chance to reach the model.
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": (system_prompt or DEFAULT_SYSTEM_PROMPT)},
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
