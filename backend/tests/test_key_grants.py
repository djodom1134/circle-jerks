from __future__ import annotations

import pytest

from app.api_keys import (
    UNRESTRICTED_GRANT,
    Grant,
    GrantViolation,
    enforce_grant,
)


def grant(scopes: list[str], airports: list[str] | None) -> Grant:
    return Grant(
        scopes=frozenset(scopes),
        airports=frozenset(airports) if airports is not None else None,
    )


def test_requested_scopes_within_the_grant_are_allowed():
    enforce_grant(["ops:read"], ["KLMO"], grant(["ops:read", "tracks:read"], ["KLMO"]))


def test_scope_outside_the_grant_is_rejected_and_named():
    with pytest.raises(GrantViolation) as exc:
        enforce_grant(["ops:read", "ledger:read"], ["KLMO"], grant(["ops:read"], ["KLMO"]))
    assert "ledger:read" in str(exc.value)
    assert "ops:read" not in str(exc.value)


def test_airport_outside_the_grant_is_rejected_and_named():
    with pytest.raises(GrantViolation) as exc:
        enforce_grant(["ops:read"], ["KBJC"], grant(["ops:read"], ["KLMO"]))
    assert "KBJC" in str(exc.value)


def test_airport_comparison_is_case_insensitive():
    enforce_grant(["ops:read"], ["klmo"], grant(["ops:read"], ["KLMO"]))


def test_a_restricted_grant_refuses_an_all_airports_key():
    # The load-bearing case. A partner granted KLMO must not mint a key with
    # airports=None, which every /v1 route reads as "every airport".
    with pytest.raises(GrantViolation) as exc:
        enforce_grant(["ops:read"], None, grant(["ops:read"], ["KLMO"]))
    assert "KLMO" in str(exc.value)

    with pytest.raises(GrantViolation):
        enforce_grant(["ops:read"], [], grant(["ops:read"], ["KLMO"]))


def test_an_unrestricted_grant_allows_all_airports_and_every_scope():
    enforce_grant(list(sorted({"ops:read", "tracks:read"})), None, UNRESTRICTED_GRANT)
    enforce_grant(["ledger:read"], ["KBJC"], UNRESTRICTED_GRANT)


def test_an_empty_grant_can_mint_nothing():
    with pytest.raises(GrantViolation):
        enforce_grant(["ops:read"], None, grant([], None))


def test_unknown_scopes_are_rejected_before_the_grant_is_consulted():
    with pytest.raises(ValueError):
        enforce_grant(["nonsense:read"], None, UNRESTRICTED_GRANT)
