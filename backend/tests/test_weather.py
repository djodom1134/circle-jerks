"""Tests for the METAR wind summary."""

from __future__ import annotations

from app.weather import _vector_average


def test_vector_average_north_winds_cancel():
    # 350° and 10° are both nearly north winds; vector average should be ~0°
    # (north), not the naive scalar average of 180° (south).
    rows = [
        {"wdir": 350, "wspd": 10},
        {"wdir": 10, "wspd": 10},
    ]
    result = _vector_average(rows)
    assert result["sample_count"] == 2
    assert result["wind_speed_kt"] == 10.0
    # Expect near 0°/360°
    assert result["wind_from_dir_degrees"] in (0, 360)


def test_vector_average_uses_meteorological_convention():
    # All winds from 270° (west) → average should also be 270°.
    rows = [
        {"wdir": 270, "wspd": 8},
        {"wdir": 268, "wspd": 9},
        {"wdir": 272, "wspd": 7},
    ]
    result = _vector_average(rows)
    assert 268 <= result["wind_from_dir_degrees"] <= 272
    assert result["wind_speed_kt"] == round(sum([8, 9, 7]) / 3, 1)


def test_variable_winds_contribute_speed_not_direction():
    # VRB (variable) winds have wdir=None per our parser; they contribute to
    # the speed scalar average but not the direction vector.
    rows = [
        {"wdir": 90, "wspd": 5},
        {"wdir": None, "wspd": 3},  # variable
        {"wdir": 90, "wspd": 7},
    ]
    result = _vector_average(rows)
    assert result["sample_count"] == 3
    # Speed avg is scalar over all 3
    assert result["wind_speed_kt"] == round((5 + 3 + 7) / 3, 1)
    # Direction is averaged only over the two real-direction rows
    assert result["wind_from_dir_degrees"] == 90


def test_gust_max_is_max_not_mean():
    rows = [
        {"wdir": 180, "wspd": 5, "wgst": 12},
        {"wdir": 180, "wspd": 5, "wgst": 18},
        {"wdir": 180, "wspd": 5, "wgst": None},
    ]
    result = _vector_average(rows)
    assert result["wind_gust_max_kt"] == 18.0


def test_calm_winds_handled():
    rows = [
        {"wdir": 0, "wspd": 0},
        {"wdir": 0, "wspd": 0},
    ]
    result = _vector_average(rows)
    assert result["sample_count"] == 2
    assert result["wind_speed_kt"] == 0.0
    # With zero magnitude the direction is undefined — should return None
    assert result["wind_from_dir_degrees"] is None
