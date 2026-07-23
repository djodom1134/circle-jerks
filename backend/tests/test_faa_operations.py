from __future__ import annotations

import sqlite3

import pytest

from app import faa_operations as fo


@pytest.mark.parametrize(
    "op_type, agl, expected",
    [
        ("takeoff", None, 1),
        ("landing", 0, 1),
        ("touch_and_go", 900, 2),        # T&G is always 2, regardless of AGL
        ("low_approach", 20, 2),         # touchdown: <= 25 ft
        ("low_approach", 25, 2),         # boundary is inclusive
        ("low_approach", 26, 0),         # a real low pass / go-around
        ("low_approach", None, 0),       # missing AGL is never a touchdown
        ("circle", 10, 0),
        ("pass_over_user", None, 0),
        ("something_new", 0, 0),         # unknown type never counts
    ],
)
def test_faa_operation_count(op_type, agl, expected):
    assert fo.faa_operation_count(op_type, agl) == expected


@pytest.mark.parametrize(
    "op_type, agl, arr, dep",
    [
        ("landing", 0, 1, 0),
        ("takeoff", None, 0, 1),
        ("touch_and_go", 900, 1, 1),
        ("low_approach", 20, 1, 1),
        ("low_approach", 120, 0, 0),
        ("circle", 10, 0, 0),
    ],
)
def test_arrivals_and_departures_decompose_the_count(op_type, agl, arr, dep):
    assert fo.faa_arrivals(op_type, agl) == arr
    assert fo.faa_departures(op_type, agl) == dep
    # The invariant the summary relies on: arrivals + departures == the count.
    assert arr + dep == fo.faa_operation_count(op_type, agl)


def test_python_and_sql_agree():
    """The SQL CASE fragments must score a set of rows identically to the
    Python function — this is what stops the two paths from drifting."""
    samples = [
        ("takeoff", None), ("landing", 0), ("touch_and_go", 900),
        ("low_approach", 20), ("low_approach", 25), ("low_approach", 26),
        ("low_approach", None), ("circle", 10), ("pass_over_user", None),
    ]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE ops (type TEXT, min_altitude_ft_agl INTEGER)")
    conn.executemany("INSERT INTO ops (type, min_altitude_ft_agl) VALUES (?, ?)", samples)

    row = conn.execute(
        f"SELECT {fo.FAA_OPS_SUM_SQL} AS ops, "
        f"{fo.FAA_ARRIVALS_SUM_SQL} AS arr, "
        f"{fo.FAA_DEPARTURES_SUM_SQL} AS dep, "
        f"{fo.LOW_APPROACH_TOUCHDOWN_COUNT_SQL} AS la_td FROM ops"
    ).fetchone()

    assert row["ops"] == sum(fo.faa_operation_count(t, a) for t, a in samples)
    assert row["arr"] == sum(fo.faa_arrivals(t, a) for t, a in samples)
    assert row["dep"] == sum(fo.faa_departures(t, a) for t, a in samples)
    assert row["la_td"] == sum(
        1 for t, a in samples if t == "low_approach" and fo.is_low_approach_touchdown(a)
    )
