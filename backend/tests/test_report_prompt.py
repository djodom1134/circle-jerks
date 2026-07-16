from __future__ import annotations

from app import services


def test_system_prompt_fingerprint_differs_past_120_chars():
    common = "x" * 130
    a = services.system_prompt_fingerprint(common + "AAA")
    b = services.system_prompt_fingerprint(common + "BBB")
    assert a != b


def test_system_prompt_fingerprint_stable_and_default():
    assert services.system_prompt_fingerprint(None) == services.system_prompt_fingerprint(None)
    assert services.system_prompt_fingerprint("") == services.system_prompt_fingerprint(None)


from app.llm import (
    ComplaintContext,
    AggregateComplaintContext,
    DEFAULT_SYSTEM_PROMPT,
    build_prompt,
    build_aggregate_prompt,
    violates_guardrails,
)
from app.tone import ToneSliders


def test_guardrail_allows_benign_trying_to_but_blocks_profanity():
    # "trying to rest/sleep" is the resident's own benign phrasing about themselves,
    # not an accusation about the pilot -- it must NOT nuke the whole complaint.
    assert violates_guardrails("The noise wakes me when I am trying to sleep.") is False
    assert violates_guardrails("I am trying to rest during the quiet morning hours.") is False
    # genuine profanity / insults are still blocked
    assert violates_guardrails("This pilot is an idiot.") is True
    assert violates_guardrails("What an asshole move by the pilot.") is True
    # intent-accusation guards remain
    assert violates_guardrails("The pilot intentionally buzzed my house.") is True


def _ctx(**over):
    base = dict(
        airport_name="Vance Brand", airport_icao="KLMO", user_location_label="Longmont, CO",
        time_window_label="in the last hour", callsign="N4632F", icao24="a5a764",
        aircraft_type="Cessna 172", observed_from="6:03 AM", observed_to="7:15 AM",
        circles=12, avg_radius=3.23, alt_min=0, alt_max=2295, touch_and_gos=7,
        low_approaches=5, passes=3, avg_altitude_user=1200, min_altitude_user=650,
        quiet_hours_events=18, peak_hours="6 AM, 7 AM", origin_airport="Broomfield, CO (KBJC)",
        runway_used="29", airport_elevation_ft=5055, previous_report_count=0,
        peak_db_at_home=52.4,
    )
    base.update(over)
    return ComplaintContext(**base)


def test_default_system_prompt_carries_persona_rules():
    p = DEFAULT_SYSTEM_PROMPT.lower()
    assert "touch-and-go" in p
    assert "n-number" in p
    assert "elevation" in p and "do not mention" in p  # explicitly forbids elevation
    assert "military" in p  # 12-hour time rule preserved


def test_build_prompt_omits_forbidden_facts_keeps_noise_facts():
    sliders = ToneSliders(anger=3, niceness=6, respect=7, detail=6, local=3)
    text = build_prompt(_ctx(), sliders)
    assert "elevation" not in text.lower()
    assert "circles_count" not in text
    assert "avg_loop_radius" not in text
    assert "5055" not in text
    assert "touch_and_go_count: 7" in text
    assert "lowest_altitude_over_user_ft_agl: 650" in text
    assert "aircraft_n_number: N4632F" in text


def test_build_aggregate_prompt_omits_forbidden_facts():
    sliders = ToneSliders(anger=3, niceness=6, respect=7, detail=6, local=3)
    agg = AggregateComplaintContext(
        airport_name="Vance Brand", airport_icao="KLMO", user_location_label="Longmont, CO",
        time_window_label="in the last hour", observed_from="6:03 AM", observed_to="7:15 AM",
        aircraft_count=2, callsigns=["N4632F", "N771CJ"], total_circles=20,
        total_touch_and_gos=14, total_low_approaches=6, total_passes=5,
        avg_altitude_user=1100, min_altitude_user=600, airport_elevation_ft=5055,
        previous_report_total=0, items=[_ctx(), _ctx(callsign="N771CJ", icao24="abc999")],
        peak_db_at_home=54.0,
    )
    text = build_aggregate_prompt(agg, sliders)
    assert "elevation" not in text.lower()
    assert "total_circles" not in text
    assert "5055" not in text
    assert "total_touch_and_go_count: 14" in text


from app.llm import runway_change_note


def test_runway_change_note_empty_when_no_changes():
    assert runway_change_note([]) == ""


def test_runway_change_note_present_when_unwarranted():
    note = runway_change_note([{"to_runway_id": "11", "cowboy_callsign": "N9AB"}])
    assert note.startswith("runway_changes_against_the_wind:")
    assert "11" in note


def test_cap_summary_aircraft_keeps_first_ten():
    assert services.MAX_SUMMARY_AIRCRAFT == 10
    ids = [f"h{i}" for i in range(25)]
    assert services.cap_summary_aircraft(ids) == ids[:10]
    assert services.cap_summary_aircraft(["a", "b"]) == ["a", "b"]
    assert services.cap_summary_aircraft([]) == []
