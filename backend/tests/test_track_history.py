from app import track_history as th


def _s(ts, lat=40.1, lon=-105.1, alt=3000, icao="aaa111", cs="N1"):
    return {"icao24": icao, "timestamp": ts, "lat": lat, "lon": lon,
            "altitude_ft": alt, "callsign": cs}


def test_split_into_flights_breaks_on_gap():
    samples = [_s(1000), _s(1030), _s(1060), _s(9000), _s(9030)]
    flights = th.split_into_flights(samples, gap_seconds=1200)
    assert [len(f) for f in flights] == [3, 2]


def test_split_into_flights_single_flight_when_dense():
    samples = [_s(1000), _s(1030), _s(1060)]
    assert len(th.split_into_flights(samples, gap_seconds=1200)) == 1


def test_build_tracks_groups_splits_and_simplifies():
    rows = [
        # aaa111: one flight, collinear middle point should be simplified out
        _s(1000, lat=40.10, lon=-105.10),
        _s(1030, lat=40.11, lon=-105.11),
        _s(1060, lat=40.12, lon=-105.12),
        # bbb222: two flights (gap)
        _s(1000, icao="bbb222", cs="N2"),
        _s(9000, icao="bbb222", cs="N2"),
        _s(9030, icao="bbb222", cs="N2"),
    ]
    tracks, total = th.build_tracks(rows)
    # aaa111 -> 1 flight; bbb222 -> 2 flights (one of them a singleton, dropped)
    # Singleton flights (<2 points) are dropped.
    assert total == 2
    by_icao = {t["icao24"] for t in tracks}
    assert by_icao == {"aaa111", "bbb222"}
    aaa = next(t for t in tracks if t["icao24"] == "aaa111")
    # collinear -> only endpoints survive
    assert len(aaa["samples"]) == 2
    assert aaa["samples"][0]["timestamp"] == 1000
    assert aaa["samples"][-1]["timestamp"] == 1060


def test_build_tracks_caps_by_recency_and_reports_total():
    rows = []
    # 10 aircraft, each one 2-point flight, with DISTINCT last-sample timestamps
    # so the "keep most recent N" cap is actually pinned (not a tie).
    for i in range(10):
        icao = f"a{i:05d}"
        base = 1000 + i * 100
        rows.append(_s(base, icao=icao))
        rows.append(_s(base + 30, icao=icao))
    tracks, total = th.build_tracks(rows, track_cap=4)
    assert total == 10
    assert len(tracks) == 4
    # The survivors must be the 4 aircraft with the latest last-sample timestamps.
    kept = {t["icao24"] for t in tracks}
    assert kept == {"a00006", "a00007", "a00008", "a00009"}
