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


def test_track_sample_serializes_null_lat_lon():
    """Regression test: lat/lon can be NULL in the database, and the model must
    serialize them as null rather than raising ValidationError. The published
    OpenAPI schema already documents them as nullable, so the model must match."""
    out = v1_schemas.track_sample_out(track_row(lat=None, lon=None))
    assert out.lat is None
    assert out.lon is None
    # Verify it round-trips through the model as JSON-serializable null
    serialized = out.model_dump()
    assert serialized["lat"] is None
    assert serialized["lon"] is None


@pytest.mark.parametrize("source,expected_datum", [
    ("adsbx", "barometric"),
    ("self_hosted", "barometric"),
    ("adsb_lol", "barometric"),
    ("adsb_fi", "barometric"),
    ("airplanes_live", "barometric"),
    ("opensky", "barometric"),
    ("flightaware_aeroapi", "unknown"),
])
def test_altitude_datum_is_mapped_for_every_known_source(source, expected_datum):
    """Each key in _ALTITUDE_DATUM_BY_SOURCE must be asserted by a specific-value
    test. A typo in any key silently degrades that source's altitude to 'unknown',
    a quiet loss of meaning — precisely what this project exists to prevent."""
    out = v1_schemas.track_sample_out(track_row(altitude_ft=1200.0, source=source))
    assert out.altitude_datum == expected_datum


def test_altitude_datum_is_unknown_for_unmapped_source():
    """Sources not in the map must fall through to 'unknown' rather than raising
    or silently using a stale cached value."""
    out = v1_schemas.track_sample_out(track_row(altitude_ft=1200.0, source="unknown_future_source"))
    assert out.altitude_datum == "unknown"


def test_stopped_percent_becomes_a_fraction():
    # The headline inconsistency: this held 28.9 (a percent) while
    # operations' pct_off_pattern held 0.42 (a fraction). Same prefix.
    stats = {
        "counters": {"circles": 1, "touch_and_gos": 2, "low_approaches": 3,
                     "landings": 4, "passes": 5, "unique_aircraft": 6,
                     "runway_changes": 7},
        "ops_over_time": [{"bucket": 1784000000, "count": 3}],
        "runway_usage": [],
        "stop_classification": {
            "all": {"total": 532, "stopped": 154, "did_not_stop": 378, "stopped_pct": 28.9},
            "pattern": {"total": 237, "stopped": 154, "did_not_stop": 83, "stopped_pct": 65.0},
        },
    }
    out = v1_schemas.airport_stats_out("KLMO", {"code": "7d", "start_ts": 1, "end_ts": 2,
                                                "bucket_seconds": 3600}, stats)
    assert out.stop_classification.all.fraction_stopped == 0.289
    assert 0.0 <= out.stop_classification.all.fraction_stopped <= 1.0
    assert not hasattr(out.stop_classification.all, "stopped_pct")


def test_stopped_fraction_is_none_when_traffic_is_empty():
    # Empty-traffic edge case: when an airport/window has zero operations
    # (total == 0), db.py's _stop_bucket sets stopped_pct to None rather
    # than dividing by zero. The builder's _stop_breakdown must guard this
    # and return None for fraction_stopped, not 0.0 (which would falsely claim
    # "0% stopped"). This is load-bearing: a regressed guard returning 0.0
    # would still pass a test that doesn't inspect the field.
    stats = {
        "counters": {"circles": 0, "touch_and_gos": 0, "low_approaches": 0,
                     "landings": 0, "passes": 0, "unique_aircraft": 0,
                     "runway_changes": 0},
        "ops_over_time": [],
        "runway_usage": [],
        "stop_classification": {
            "all": {"total": 0, "stopped": 0, "did_not_stop": 0, "stopped_pct": None},
            "pattern": {"total": 0, "stopped": 0, "did_not_stop": 0, "stopped_pct": None},
        },
    }
    out = v1_schemas.airport_stats_out("KLMO", {"code": "7d", "start_ts": 1, "end_ts": 2,
                                                "bucket_seconds": 3600}, stats)
    # Both buckets must produce None, not 0.0 or a crash
    assert out.stop_classification.all.fraction_stopped is None
    assert out.stop_classification.pattern.fraction_stopped is None


def test_runways_drop_the_duplicated_internal_icao():
    rows = [{"icao": "KLMO", "runway_id": "29", "lat_threshold": 40.1,
             "lon_threshold": -105.1, "heading_deg": 290, "length_ft": 4800}]
    out = v1_schemas.runways_out("KLMO", rows)
    assert out.airport_icao == "KLMO"
    dumped = out.runways[0].model_dump()
    assert "icao" not in dumped
    assert dumped["runway_id"] == "29"
