from __future__ import annotations

from app import db


def _seed(conn, *, id, icao, ts, type, agl):
    conn.execute(
        "INSERT INTO operations (id, icao, icao24, type, timestamp, min_altitude_ft_agl) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id, icao, "a26f5e", type, ts, agl),
    )
    conn.commit()


def _cold_db(tmp_path):
    path = str(tmp_path / "cold.sqlite3")
    db.init_db(path)  # same schema as hot
    return path


def test_summary_weights_each_type(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        _seed(conn, id="t", icao="KLMO", ts=1000, type="takeoff", agl=None)
        _seed(conn, id="l", icao="KLMO", ts=1001, type="landing", agl=0)
        _seed(conn, id="tg", icao="KLMO", ts=1002, type="touch_and_go", agl=900)
        _seed(conn, id="la_td", icao="KLMO", ts=1003, type="low_approach", agl=20)   # touchdown -> 2
        _seed(conn, id="la_go", icao="KLMO", ts=1004, type="low_approach", agl=120)  # go-around -> 0
        _seed(conn, id="c", icao="KLMO", ts=1005, type="circle", agl=None)           # 0
        s = db.faa_operations_summary(conn, icao="KLMO", start_ts=0, end_ts=2000)

    # 1 + 1 + 2 + 2 + 0 + 0
    assert s["faa_operations"] == 6
    # arrivals: landing + tg + la_td ; departures: takeoff + tg + la_td
    assert s["arrivals"] == 3
    assert s["departures"] == 3
    assert s["arrivals"] + s["departures"] == s["faa_operations"]
    assert s["low_approach_touchdowns"] == 1
    assert s["by_event_type"] == {
        "takeoff": 1, "landing": 1, "touch_and_go": 1, "low_approach": 2, "circle": 1,
    }


def test_summary_is_empty_over_a_gap(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        s = db.faa_operations_summary(conn, icao="KLMO", start_ts=0, end_ts=2000)
    assert s["faa_operations"] == 0
    assert s["arrivals"] == 0 and s["departures"] == 0
    assert s["low_approach_touchdowns"] == 0
    assert s["by_event_type"] == {}


def test_summary_includes_the_cold_slice(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed(cconn, id="c1", icao="KLMO", ts=5_000, type="touch_and_go", agl=900)  # cold, 2 ops
    with db.connect(hot) as conn:
        _seed(conn, id="h1", icao="KLMO", ts=15_000, type="landing", agl=0)         # hot, 1 op
        s = db.faa_operations_summary(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert s["faa_operations"] == 3               # 2 (cold) + 1 (hot)
    assert s["by_event_type"] == {"touch_and_go": 1, "landing": 1}


def test_summary_does_not_double_count_at_the_seam(tmp_path):
    # The boundary row (ts == cutoff) belongs to hot; an archival-lag copy of it
    # in cold must not also be counted. Mirrors the read_operations_page seam.
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed(cconn, id="boundary", icao="KLMO", ts=10_000, type="landing", agl=0)  # lag copy
    with db.connect(hot) as conn:
        _seed(conn, id="boundary", icao="KLMO", ts=10_000, type="landing", agl=0)   # real copy
        s = db.faa_operations_summary(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert s["faa_operations"] == 1               # counted once, from hot
    assert s["by_event_type"] == {"landing": 1}


def test_summary_recent_window_never_touches_cold(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed(cconn, id="c1", icao="KLMO", ts=5_000, type="touch_and_go", agl=900)
    with db.connect(hot) as conn:
        _seed(conn, id="h1", icao="KLMO", ts=15_000, type="landing", agl=0)
        s = db.faa_operations_summary(
            conn, icao="KLMO", start_ts=12_000, end_ts=20_000,   # starts after cutoff
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert s["faa_operations"] == 1               # only the hot landing
    assert s["by_event_type"] == {"landing": 1}


def test_summary_suppresses_a_coincident_low_approach(tmp_path):
    # A touch-and-go lap [1000,1300] (its circle carries time_total_s=300); a
    # low_approach at 1150 is that lap's OWN touchdown -> suppressed. A standalone
    # low_approach at 2000 is a distinct op -> kept.
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        conn.execute(
            "INSERT INTO operations (id, icao, icao24, type, timestamp, time_total_s) "
            "VALUES ('c', 'KLMO', 'a26f5e', 'circle', 1300, 300)")
        _seed(conn, id="tg", icao="KLMO", ts=1300, type="touch_and_go", agl=0)
        _seed(conn, id="la_dup", icao="KLMO", ts=1150, type="low_approach", agl=0)   # inside lap
        _seed(conn, id="la_real", icao="KLMO", ts=2000, type="low_approach", agl=0)  # distinct
        s = db.faa_operations_summary(conn, icao="KLMO", start_ts=0, end_ts=3000)

    # touch_and_go(2) + kept low_approach touchdown(2); the coincident one is gone.
    assert s["faa_operations"] == 4
    assert s["by_event_type"] == {"circle": 1, "touch_and_go": 1, "low_approach": 1}
    assert s["low_approach_touchdowns"] == 1
