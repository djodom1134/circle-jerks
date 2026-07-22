from __future__ import annotations

import pytest

from app import v1_schemas


def op_row(**kw) -> dict:
    base = {
        "id": "abc123", "airport_icao": "KLMO", "icao24": "a26f5e",
        "callsign": "N256SF", "registration": None, "type": "landing",
        "timestamp": 1784150045, "runway_id": "29", "turn_direction": None,
        "min_altitude_ft_agl": 0, "emitter_category": "A1",
        "deviation_mean_nm": 0.31, "deviation_peak_nm": 1.8,
        "pct_off_pattern": 0.42, "time_off_pattern_s": 42, "time_total_s": 100,
        "wind_from_deg": 30, "wind_speed_kt": 6.0,
        "origin_airport_icao": None, "origin_label": None,
        "operator": None, "flight_school": None,
    }
    base.update(kw)
    return base


def test_the_misnamed_ratio_is_renamed_and_still_a_fraction():
    out = v1_schemas.operation_out(op_row())
    assert out.fraction_off_pattern == 0.42
    assert not hasattr(out, "pct_off_pattern")


def test_the_inputs_behind_the_ratio_are_published():
    # Previously computed and withheld, which left the value unverifiable.
    out = v1_schemas.operation_out(op_row())
    assert out.time_off_pattern_s == 42
    assert out.time_total_s == 100


def test_the_published_inputs_actually_explain_the_published_ratio():
    out = v1_schemas.operation_out(op_row(time_off_pattern_s=42, time_total_s=100,
                                          pct_off_pattern=0.42))
    assert out.fraction_off_pattern == pytest.approx(
        out.time_off_pattern_s / out.time_total_s, abs=0.001
    )


def test_a_fraction_never_exceeds_one():
    out = v1_schemas.operation_out(op_row(pct_off_pattern=1.0))
    assert 0.0 <= out.fraction_off_pattern <= 1.0


def test_an_unknown_internal_column_cannot_reach_the_wire():
    # The whole point of the model layer: a column added to the query
    # tomorrow is filtered structurally, not by a test someone remembers.
    out = v1_schemas.operation_out(op_row(secret_internal_column="leak"))
    assert "secret_internal_column" not in out.model_dump()


def test_deviation_fields_are_absent_when_not_computed():
    out = v1_schemas.operation_out(op_row(
        pct_off_pattern=None, time_off_pattern_s=None, time_total_s=None,
        deviation_mean_nm=None, deviation_peak_nm=None,
    ))
    assert out.fraction_off_pattern is None
    assert out.time_off_pattern_s is None


def track_row(**kw) -> dict:
    base = {
        "icao24": "ad076f", "timestamp": 1784752242, "lat": 40.038288,
        "lon": -105.233185, "altitude_ft": None, "baro_altitude_ft": 0.0,
        "geo_altitude_ft": 5225.0, "heading_deg": 0.0,
        "vertical_rate_fpm": 0.0, "callsign": "N939DM",
        "emitter_category": "A1", "source": "adsbx",
    }
    base.update(kw)
    return base


def test_altitude_carries_its_datum():
    # altitude_ft is populated from a different path than the ADS-B baro/geo
    # fields, so its datum varies by source. A number whose datum cannot be
    # stated is exactly what this whole change exists to eliminate.
    out = v1_schemas.track_sample_out(track_row(altitude_ft=1200.0, source="flightaware"))
    assert out.altitude_ft == 1200.0
    assert out.altitude_datum in {"barometric", "geometric", "unknown"}


def test_adsb_sourced_altitude_is_reported_barometric():
    out = v1_schemas.track_sample_out(track_row(altitude_ft=900.0, source="adsbx"))
    assert out.altitude_datum == "barometric"


def test_datum_is_unknown_when_there_is_no_altitude():
    out = v1_schemas.track_sample_out(track_row(altitude_ft=None))
    assert out.altitude_datum == "unknown"


def test_track_timestamp_uses_the_epoch_suffix():
    out = v1_schemas.track_sample_out(track_row())
    assert out.timestamp_ts == 1784752242
    assert not hasattr(out, "timestamp")
