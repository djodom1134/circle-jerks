from app import pattern_circuits as pc


def _track(icao, base):
    # A dense little arc of samples around `base`, 10s apart.
    return [
        {"icao24": icao, "timestamp": base + 10 * i, "lat": 40.10 + 0.001 * i, "lon": -105.10 + 0.001 * i}
        for i in range(-8, 9)
    ]


def test_classify_runway_and_area():
    assert pc.classify({"type": "touch_and_go", "runway_id": "29"}) == ("29", "29")
    assert pc.classify({"type": "low_approach", "runway_id": "11"}) == ("11", "11")
    assert pc.classify({"type": "circle", "runway_id": None}) == ("area", None)
    # runway op with missing runway falls back to area
    assert pc.classify({"type": "touch_and_go", "runway_id": None}) == ("area", None)


def test_build_circuits_slices_window_and_tags_class():
    ops = [
        {"icao24": "AAA111", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "BBB222", "timestamp": 5000, "type": "circle", "runway_id": None},
    ]
    tracks = {
        "aaa111": _track("aaa111", 1000),
        "bbb222": _track("bbb222", 5000),
    }
    circuits, counts, total = pc.build_circuits(ops, tracks)
    assert total == 2
    by_class = sorted(c["class"] for c in circuits)
    assert by_class == ["29", "area"]
    rwy = next(c for c in circuits if c["class"] == "29")
    assert rwy["runway_id"] == "29"
    assert rwy["icao24"] == "aaa111"
    # only samples within +/- 75s of ts=1000 (i.e. 925..1075) are kept
    assert all(925 <= s["timestamp"] <= 1075 for s in rwy["samples"])
    assert len(rwy["samples"]) >= 2


def test_build_circuits_dedupes_overlapping_same_class():
    # Two runway-29 ops on the same aircraft 20s apart -> windows overlap -> one circuit.
    ops = [
        {"icao24": "AAA111", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "AAA111", "timestamp": 1020, "type": "low_approach", "runway_id": "29"},
    ]
    tracks = {"aaa111": _track("aaa111", 1000)}
    circuits, counts, total = pc.build_circuits(ops, tracks)
    assert total == 1
    assert len(circuits) == 1
    assert counts == {"29": 1}


def test_build_circuits_caps_by_recency():
    ops = [
        {"icao24": f"a{i:05d}", "timestamp": 1000 + i * 1000, "type": "circle", "runway_id": None}
        for i in range(6)
    ]
    tracks = {f"a{i:05d}": _track(f"a{i:05d}", 1000 + i * 1000) for i in range(6)}
    circuits, counts, total = pc.build_circuits(ops, tracks, cap=2)
    assert total == 6
    assert counts == {"area": 6}
    assert len(circuits) == 2
    # kept the two most recent operation timestamps (i=4,5)
    kept = {c["icao24"] for c in circuits}
    assert kept == {"a00004", "a00005"}
