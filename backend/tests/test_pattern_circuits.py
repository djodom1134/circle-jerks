from app import pattern_circuits as pc


def _track(icao, base):
    # A dense diagonal arc of samples around `base`, 10s apart (RDP -> ~2 points).
    return [
        {"icao24": icao, "timestamp": base + 10 * i, "lat": 40.10 + 0.001 * i, "lon": -105.10 + 0.001 * i}
        for i in range(-8, 9)
    ]


def _laptrack(base, dur, step=5):
    # Samples from base..base+dur cycling a square's corners so RDP keeps >=4
    # turn points -- a stand-in for a closed pattern lap.
    corners = [(40.10, -105.10), (40.12, -105.10), (40.12, -105.12), (40.10, -105.12)]
    out = []
    t, i = base, 0
    while t <= base + dur:
        lat, lon = corners[i % 4]
        out.append({"lat": lat, "lon": lon, "timestamp": t})
        t += step
        i += 1
    return out


def test_classify_runway_and_area():
    assert pc.classify({"type": "touch_and_go", "runway_id": "29"}) == ("29", "29")
    assert pc.classify({"type": "low_approach", "runway_id": "11"}) == ("11", "11")
    assert pc.classify({"type": "circle", "runway_id": None}) == ("area", None)
    # runway op with missing runway falls back to area
    assert pc.classify({"type": "touch_and_go", "runway_id": None}) == ("area", None)


# --- lap segmentation ---------------------------------------------------------

def test_two_consecutive_same_runway_ops_make_one_lap():
    ops = [
        {"icao24": "ABC123", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "ABC123", "timestamp": 1300, "type": "touch_and_go", "runway_id": "29"},
    ]
    tracks = {"abc123": _laptrack(900, 600)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    laps = [c for c in circuits if c["is_loop"]]
    assert len(laps) == 1
    assert laps[0]["class"] == "29"
    assert laps[0]["runway_id"] == "29"
    assert all(1000 <= s["timestamp"] <= 1300 for s in laps[0]["samples"])
    assert counts == {"29": 1}
    assert context == 0


def test_gap_too_large_no_lap():
    ops = [
        {"icao24": "ABC123", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "ABC123", "timestamp": 3000, "type": "touch_and_go", "runway_id": "29"},
    ]
    tracks = {"abc123": _laptrack(900, 2200)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert [c for c in circuits if c["is_loop"]] == []
    assert counts == {}
    assert context == 2  # both become context slices (non-overlapping windows)


def test_gap_too_small_no_lap():
    ops = [
        {"icao24": "A", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1030, "type": "touch_and_go", "runway_id": "29"},
    ]
    tracks = {"a": _laptrack(950, 200, step=3)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert [c for c in circuits if c["is_loop"]] == []


def test_different_runways_no_lap():
    ops = [
        {"icao24": "A", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1300, "type": "touch_and_go", "runway_id": "11"},
    ]
    tracks = {"a": _laptrack(900, 600)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert [c for c in circuits if c["is_loop"]] == []
    assert context == 2


def test_chain_of_three_makes_two_laps():
    ops = [
        {"icao24": "A", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1300, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1600, "type": "touch_and_go", "runway_id": "29"},
    ]
    tracks = {"a": _laptrack(900, 900)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert len([c for c in circuits if c["is_loop"]]) == 2
    assert counts == {"29": 2}
    assert context == 0


def test_circle_op_is_context_area():
    ops = [{"icao24": "A", "timestamp": 1000, "type": "circle", "runway_id": None}]
    tracks = {"a": _laptrack(925, 150, step=5)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert circuits and circuits[0]["is_loop"] is False
    assert circuits[0]["class"] == "area"
    assert counts == {}
    assert context == 1


def test_lone_runway_op_is_context():
    ops = [{"icao24": "A", "timestamp": 1000, "type": "low_approach", "runway_id": "29"}]
    tracks = {"a": _laptrack(925, 150, step=5)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert circuits[0]["is_loop"] is False
    assert counts == {}
    assert context == 1


def test_lap_dropped_when_too_few_points_after_rdp():
    # A straight diagonal slice RDP-collapses to 2 points (< MIN_LAP_POINTS).
    ops = [
        {"icao24": "A", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1300, "type": "touch_and_go", "runway_id": "29"},
    ]
    tracks = {"a": _track("a", 1150)}  # diagonal line covering ~1070..1230
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert [c for c in circuits if c["is_loop"]] == []


# --- context slices (previously the only circuit type) ------------------------

def test_build_circuits_context_slices_window_and_tags_class():
    ops = [
        {"icao24": "AAA111", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "BBB222", "timestamp": 5000, "type": "circle", "runway_id": None},
    ]
    tracks = {
        "aaa111": _track("aaa111", 1000),
        "bbb222": _track("bbb222", 5000),
    }
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert total == 2
    assert context == 2
    assert counts == {}  # neither is part of a lap
    by_class = sorted(c["class"] for c in circuits)
    assert by_class == ["29", "area"]
    rwy = next(c for c in circuits if c["class"] == "29")
    assert rwy["runway_id"] == "29"
    assert rwy["icao24"] == "aaa111"
    assert rwy["is_loop"] is False
    # only samples within +/- 75s of ts=1000 (i.e. 925..1075) are kept
    assert all(925 <= s["timestamp"] <= 1075 for s in rwy["samples"])
    assert len(rwy["samples"]) >= 2


def test_build_circuits_dedupes_overlapping_same_class_context():
    # Two runway-29 ops 20s apart -> gap < MIN_LAP_S so no lap; windows overlap
    # -> collapse to one context slice.
    ops = [
        {"icao24": "AAA111", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "AAA111", "timestamp": 1020, "type": "low_approach", "runway_id": "29"},
    ]
    tracks = {"aaa111": _track("aaa111", 1000)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert total == 1
    assert context == 1
    assert counts == {}


def test_build_circuits_caps_context_by_recency():
    ops = [
        {"icao24": f"a{i:05d}", "timestamp": 1000 + i * 1000, "type": "circle", "runway_id": None}
        for i in range(6)
    ]
    tracks = {f"a{i:05d}": _track(f"a{i:05d}", 1000 + i * 1000) for i in range(6)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks, cap=2)
    assert total == 6
    assert counts == {}
    assert len(circuits) == 2
    # kept the two most recent operation timestamps (i=4,5)
    kept = {c["icao24"] for c in circuits}
    assert kept == {"a00004", "a00005"}
