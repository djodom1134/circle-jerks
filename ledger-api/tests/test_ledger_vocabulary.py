from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import ledger

# THE DRIFT GUARDS ARE BACK. They were dropped when this service was split out of
# the main API, on the grounds that a sidecar with no dependency on the main API's
# internals could not import `app.detectors` to check its constants. That reasoning
# was right about the IMPORT and wrong about the GUARD: what has to be verified is
# that a NUMBER printed on a public page still matches the number the detector
# actually uses, and that can be read out of the detector's source TEXT without
# importing it, taking on a dependency, or coupling the two deploys in any way.
#
# The cost of not having them is not hypothetical. RUNWAY_USE_DEFINITIONS is served
# verbatim on the methodology page as this project's factual claim about how it
# counts; if a threshold moves in backend/app/detectors.py and this prose does not,
# the site simply starts lying about its own method — the exact failure the whole
# module exists to prevent, and one a pilot would catch immediately.
_DETECTORS = Path(__file__).resolve().parents[2] / "backend" / "app" / "detectors.py"


def _detector_constant(name: str) -> float:
    """Read a numeric constant out of the main API's detector source.

    Deliberately textual. This service must not import from `backend/` — it is a
    separate process with a separate deploy — but it can still READ the file in the
    repo it shares. Skips (rather than fails) where backend/ is absent, i.e. inside
    this service's own container, which ships without it.
    """
    if not _DETECTORS.exists():
        pytest.skip("backend/app/detectors.py not present (standalone checkout)")
    match = re.search(rf"^{name}\s*=\s*([0-9.]+)", _DETECTORS.read_text(), re.MULTILINE)
    assert match, f"{name} is gone from detectors.py — the published prose now cites nothing"
    return float(match.group(1))


def _plain(value: float) -> str:
    """`300.0` -> "300", `0.25` -> "0.25" — the number as the prose would print it."""
    return str(int(value)) if value == int(value) else str(value)


def test_touch_and_go_prose_cites_the_detector_airborne_floor():
    # The FLOOR is the gate that stops a ground taxi-back being counted as a lap.
    # If it moves, this sentence is false.
    floor = _detector_constant("MIN_CIRCLE_AIRBORNE_AGL_FT")
    assert f"{_plain(floor)} ft" in ledger.RUNWAY_USE_DEFINITIONS["touch_and_go"]


def test_touch_and_go_prose_cites_the_detector_runway_overlap():
    overlap = _detector_constant("RUNWAY_OVERLAP_NM")
    assert f"{_plain(overlap)} nm" in ledger.RUNWAY_USE_DEFINITIONS["touch_and_go"]


def test_landing_prose_cites_the_detector_approach_and_settle_thresholds():
    approach = _detector_constant("APPROACH_MIN_AGL_FT")
    settle = _detector_constant("LANDING_SETTLE_SECONDS")
    text = ledger.RUNWAY_USE_DEFINITIONS["landing"]
    assert f"{_plain(approach)} ft" in text
    assert f"{_plain(settle)} seconds" in text


def test_low_approach_prose_cites_the_detector_approach_threshold():
    approach = _detector_constant("APPROACH_MIN_AGL_FT")
    assert f"{_plain(approach)} ft" in ledger.RUNWAY_USE_DEFINITIONS["low_approach"]


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
