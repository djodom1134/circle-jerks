from __future__ import annotations

from app import v1_schemas
from app.api_keys import ApiKeyContext


def ctx(scopes=("ops:read",), airports=frozenset({"KLMO"})):
    return ApiKeyContext(key_id="a" * 16, name="k", scopes=frozenset(scopes), airports=airports)


def test_meta_publishes_the_corridor_constant():
    # The 0.25 nm corridor is what "off pattern" means. Publishing the ratio
    # without it leaves the consumer unable to reproduce or interpret it.
    out = v1_schemas.meta_out(ctx())
    assert out.constants.pattern_corridor_nm == 0.25


def test_meta_publishes_the_score_scales():
    out = v1_schemas.meta_out(ctx())
    assert out.constants.vnap_score_scale == "0..100"
    assert out.constants.axis_score_scale == "0..100"


def test_meta_reports_the_keys_own_grant():
    out = v1_schemas.meta_out(ctx(scopes=("tracks:read", "ops:read")))
    assert out.scopes == ["ops:read", "tracks:read"]
    assert out.airports == ["KLMO"]


def test_unrestricted_key_reports_null_airports():
    out = v1_schemas.meta_out(ctx(airports=None))
    assert out.airports is None


def test_corridor_constant_matches_the_deviation_module():
    # Binding test: if CORRIDOR_NM changes, the published constant must follow.
    from app.deviation import CORRIDOR_NM
    assert v1_schemas.PATTERN_CORRIDOR_NM == CORRIDOR_NM
