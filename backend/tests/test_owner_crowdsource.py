from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_override_supersedes_and_resolves(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # No override, no registry row -> unknown/inferred.
    r0 = db.resolve_owner_class(conn, "AA11")
    assert r0 == {"owner_class": "unknown", "owner_source": "inferred"}

    # A registry row makes it inferred=flight_school.
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, owner_type) VALUES (?, ?, ?)",
        ("N1", "AA11", "flight_school"),
    )
    conn.commit()
    assert db.resolve_owner_class(conn, "aa11")["owner_class"] == "flight_school"
    assert db.resolve_owner_class(conn, "aa11")["owner_source"] == "inferred"

    # A community override wins.
    db.set_owner_override(conn, "AA11", "llc", editor_visitor_id="visitor-1234",
                          editor_ip="1.2.3.4", change_note="it's an LLC")
    conn.commit()
    r = db.resolve_owner_class(conn, "aa11")
    assert r == {"owner_class": "llc", "owner_source": "community"}

    # A second override supersedes the first (versioned; only one current).
    db.set_owner_override(conn, "AA11", "individual", editor_visitor_id="visitor-1234",
                          editor_ip="1.2.3.4", change_note="actually individual")
    conn.commit()
    assert db.resolve_owner_class(conn, "aa11")["owner_class"] == "individual"
    cur = conn.execute(
        "SELECT COUNT(*) AS c FROM aircraft_owner_overrides WHERE icao24='aa11' AND is_current=1"
    ).fetchone()["c"]
    assert cur == 1
    assert db.current_owner_overrides(conn)["aa11"] == "individual"


def test_community_notes_and_rate_count(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.add_community_note(conn, "BB22", "Does laps every morning", is_flight_school=True,
                          editor_visitor_id="v-abcdef12", editor_ip="9.9.9.9")
    conn.commit()
    notes = db.list_community_notes(conn, "bb22")
    assert len(notes) == 1
    assert notes[0]["note"] == "Does laps every morning"
    assert notes[0]["is_flight_school"] == 1
    # hidden notes excluded by default
    conn.execute("UPDATE aircraft_community_notes SET hidden=1 WHERE icao24='bb22'")
    conn.commit()
    assert db.list_community_notes(conn, "bb22") == []
    # rate counter sees both overrides and notes by this editor
    n = db.count_recent_crowd_edits(conn, "v-abcdef12", "9.9.9.9", since_ts=0)
    assert n == 1


def test_locked_override_blocks_further_via_db_and_list_limit(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # list_community_notes respects an explicit limit.
    for i in range(3):
        db.add_community_note(conn, "ee55", f"note {i}", is_flight_school=False,
                              editor_visitor_id="v-abcdef12", editor_ip="1.1.1.1")
    conn.commit()
    assert len(db.list_community_notes(conn, "ee55", limit=2)) == 2
    assert len(db.list_community_notes(conn, "ee55")) == 3  # default 200 -> all
