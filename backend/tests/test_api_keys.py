from __future__ import annotations

import pytest

from app import api_keys


def test_generate_key_round_trips_through_parse_and_verify():
    full, key_id, secret_hash = api_keys.generate_key("production")

    assert full.startswith("cj_live_")
    assert len(key_id) == 16

    parsed = api_keys.parse_key(full)
    assert parsed is not None
    parsed_id, secret = parsed
    assert parsed_id == key_id
    assert api_keys.verify_secret(secret, secret_hash) is True


def test_generate_key_uses_environment_name_outside_production():
    full, _key_id, _hash = api_keys.generate_key("local")
    assert full.startswith("cj_local_")


def test_secret_containing_underscores_still_parses():
    # token_urlsafe emits base64url, which includes "_". The parser must split
    # with maxsplit so an underscore in the secret never truncates it.
    raw = "cj_live_0123456789abcdef_aa_bb_cc"
    parsed = api_keys.parse_key(raw)
    assert parsed == ("0123456789abcdef", "aa_bb_cc")


def test_wrong_secret_is_rejected():
    _full, _key_id, secret_hash = api_keys.generate_key("test")
    assert api_keys.verify_secret("not-the-secret", secret_hash) is False


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "nope",
        "cj_live_short_secret",
        "xx_live_0123456789abcdef_secret",
        "cj_live_0123456789abcdef_",
        "cj_live_0123456789abcdeZ_secret",
    ],
)
def test_malformed_keys_return_none(raw):
    assert api_keys.parse_key(raw) is None


def test_validate_scopes_accepts_known_and_rejects_unknown():
    assert api_keys.validate_scopes(["ops:read", "ops:read"]) == ["ops:read"]
    with pytest.raises(ValueError):
        api_keys.validate_scopes(["ops:write"])
    with pytest.raises(ValueError):
        api_keys.validate_scopes([])


def test_scope_serialization_round_trips():
    encoded = api_keys.serialize_scopes(["tracks:read", "ops:read"])
    assert api_keys.parse_scopes(encoded) == frozenset({"ops:read", "tracks:read"})


def test_airport_serialization_round_trips_and_normalizes_case():
    assert api_keys.serialize_airports(None) is None
    assert api_keys.serialize_airports([]) is None
    encoded = api_keys.serialize_airports(["klmo", " kbjc "])
    assert api_keys.parse_airports(encoded) == frozenset({"KLMO", "KBJC"})
    assert api_keys.parse_airports(None) is None
    assert api_keys.parse_airports("") is None


def test_context_scope_and_airport_checks():
    restricted = api_keys.ApiKeyContext(
        key_id="0123456789abcdef",
        name="partner",
        scopes=frozenset({"ops:read"}),
        airports=frozenset({"KLMO"}),
    )
    assert restricted.has_scope("ops:read") is True
    assert restricted.has_scope("tracks:read") is False
    assert restricted.allows_airport("klmo") is True
    assert restricted.allows_airport("KBJC") is False

    unrestricted = api_keys.ApiKeyContext(
        key_id="0123456789abcdef",
        name="internal",
        scopes=frozenset(api_keys.SCOPES),
        airports=None,
    )
    assert unrestricted.allows_airport("KBJC") is True


def test_display_prefix_shows_env_and_id_but_no_secret():
    assert api_keys.display_prefix("0123456789abcdef", "production") == "cj_live_0123456789abcdef"
