from __future__ import annotations

from app import detectors, ledger


def test_takeoff_is_never_a_runway_use():
    # An FAA "operation" is a takeoff OR a landing. Counting both double-counts
    # a touch-and-go. The billable unit excludes takeoffs by construction.
    assert ledger.is_runway_use("takeoff") is False
    assert "takeoff" not in ledger.RUNWAY_USE_TYPES


def test_runway_uses_are_landing_tg_and_low_approach():
    assert set(ledger.RUNWAY_USE_TYPES) == {"landing", "touch_and_go", "low_approach"}


def test_circles_and_passes_are_not_runway_uses():
    assert ledger.is_runway_use("circle") is False
    assert ledger.is_runway_use("pass_over_user") is False


def test_every_runway_use_type_has_a_published_definition():
    # The site publishes exactly how each billable event is detected. A type
    # without a definition would be an unexplained number on a public page.
    for event_type in ledger.RUNWAY_USE_TYPES:
        assert event_type in ledger.RUNWAY_USE_DEFINITIONS
        assert len(ledger.RUNWAY_USE_DEFINITIONS[event_type]) > 40


def test_touch_and_go_definition_admits_it_is_geometric():
    # The detector does NOT verify a touchdown. The published definition must
    # say so; this is the single most attackable claim on the site.
    text = ledger.RUNWAY_USE_DEFINITIONS["touch_and_go"].lower()
    assert "does not confirm" in text or "not verified" in text


# --- Drift guards ---------------------------------------------------------
#
# RUNWAY_USE_DEFINITIONS is published VERBATIM on the public methodology page.
# The numbers in those strings must stay in lockstep with the detector
# constants they claim to describe -- otherwise the site silently starts
# publishing a threshold it doesn't actually measure. These tests pull the
# real constant out of app.detectors and assert its value appears in the
# corresponding definition string, so a future tuning of a detector constant
# that isn't mirrored in the prose fails the suite immediately.
#
# Coverage is necessarily partial: some thresholds referenced in the prose
# (200 ft AGL / 1.5 nm runway-contact gate in `_runway_low_episodes`; the 90
# knot and 60 second-on-ground checks in `detect_touch_and_gos_over_period`)
# are inline literals in detectors.py, not named constants, and this task is
# additive-only with respect to detectors.py (production runs on it). Those
# are NOT guarded here -- see the fix report for the full list.

def test_touch_and_go_overlap_threshold_matches_detector():
    # RUNWAY_OVERLAP_NM gates _loop_over_runway (detectors.py:433-485), which
    # backs touch_and_gos_from_circles -- the geometric test the published
    # touch_and_go definition describes.
    assert f"{detectors.RUNWAY_OVERLAP_NM} nm" in ledger.RUNWAY_USE_DEFINITIONS["touch_and_go"]


def test_approach_altitude_threshold_matches_detector():
    # APPROACH_MIN_AGL_FT backs _approached_from_altitude, shared by the
    # landing and low_approach detectors -- both definitions claim it.
    for event_type in ("landing", "low_approach"):
        assert f"{detectors.APPROACH_MIN_AGL_FT} ft" in ledger.RUNWAY_USE_DEFINITIONS[event_type]


def test_landing_settle_threshold_matches_detector():
    # LANDING_SETTLE_SECONDS is the post-touchdown window detect_landings_over_period
    # waits before deciding a touchdown was a landing, not a touch-and-go.
    assert f"{detectors.LANDING_SETTLE_SECONDS} seconds" in ledger.RUNWAY_USE_DEFINITIONS["landing"]
