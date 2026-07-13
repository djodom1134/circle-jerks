from __future__ import annotations

from app import ledger

# NOTE ON WHAT DID NOT MOVE HERE: the original suite (backend/tests/
# test_ledger_vocabulary.py) also carried three drift guards that imported
# `app.detectors` and asserted that RUNWAY_USE_DEFINITIONS' published prose
# stayed in lockstep with the main circlejerks API's detector constants
# (RUNWAY_OVERLAP_NM, APPROACH_MIN_AGL_FT, LANDING_SETTLE_SECONDS). That
# cross-module import was only possible because ledger.py and detectors.py
# used to live in the same codebase. Now that this service is a separate
# process with no dependency on the main API's internals (by design — see
# the sidecar refactor's architectural constraint), there is no way to guard
# that drift automatically without re-introducing a coupling the split was
# meant to remove. This is a real, accepted loss of a safety net, not an
# oversight: if a detector threshold in backend/app/detectors.py changes, a
# human must remember to check RUNWAY_USE_DEFINITIONS here by hand.


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
