from __future__ import annotations

import pytest

from app.admin_users import (
    AdminUser,
    GuardViolation,
    guard_modification,
    parse_superusers,
)


def user(**kw) -> AdminUser:
    base = dict(
        id="u1",
        email="partner@example.com",
        name="Partner",
        role="partner",
        status="approved",
        granted_scopes="ops:read",
        granted_airports="KLMO",
    )
    base.update(kw)
    return AdminUser(**base)


def test_partner_grant_comes_from_the_stored_columns():
    grant = user().grant
    assert grant.scopes == frozenset({"ops:read"})
    assert grant.airports == frozenset({"KLMO"})


def test_admin_and_super_admin_carry_an_unrestricted_grant():
    # Stored grant columns are ignored for these roles: they are unrestricted
    # by definition, and honoring a narrower stored value would imply an
    # enforcement the server does not perform.
    for role in ("admin", "super_admin"):
        grant = user(role=role, granted_scopes="ops:read", granted_airports="KLMO").grant
        assert grant.scopes == frozenset({"ops:read", "tracks:read", "aggregates:read", "ledger:read"})
        assert grant.airports is None


def test_a_user_with_no_stored_grant_can_mint_nothing():
    grant = user(granted_scopes=None, granted_airports=None).grant
    assert grant.scopes == frozenset()


def test_only_approved_users_are_approved():
    assert user(status="approved").is_approved
    for status in ("pending", "rejected", "suspended"):
        assert not user(status=status).is_approved


def test_superuser_list_is_parsed_lowercased_and_trimmed():
    assert parse_superusers(" A@B.com , c@d.com ") == frozenset({"a@b.com", "c@d.com"})
    assert parse_superusers(None) == frozenset()
    assert parse_superusers("") == frozenset()


def test_a_user_cannot_modify_their_own_row():
    actor = user(id="u1", role="super_admin")
    with pytest.raises(GuardViolation) as exc:
        guard_modification(actor, actor, frozenset())
    assert "your own" in str(exc.value)


def test_an_env_allowlisted_account_cannot_be_modified():
    actor = user(id="u1", role="super_admin", email="boss@example.com")
    target = user(id="u2", role="super_admin", email="owner@example.com")
    with pytest.raises(GuardViolation) as exc:
        guard_modification(actor, target, frozenset({"owner@example.com"}))
    assert "ADMIN_SUPERUSERS" in str(exc.value)


def test_the_allowlist_check_is_case_insensitive():
    actor = user(id="u1", role="super_admin", email="boss@example.com")
    target = user(id="u2", role="super_admin", email="Owner@Example.com")
    with pytest.raises(GuardViolation):
        guard_modification(actor, target, frozenset({"owner@example.com"}))


def test_one_super_admin_may_demote_another():
    # Lockout is impossible without a third guard: the actor is always an
    # approved super-admin and cannot target themselves, so at least one
    # always survives. ADMIN_SUPERUSERS is the backstop beyond that.
    actor = user(id="u1", role="super_admin", email="a@example.com")
    target = user(id="u2", role="super_admin", email="b@example.com")
    guard_modification(actor, target, frozenset())


def test_modifying_an_ordinary_target_is_permitted():
    actor = user(id="u1", role="super_admin", email="a@example.com")
    target = user(id="u2", role="partner", email="b@example.com")
    guard_modification(actor, target, frozenset())


def test_roles_constant_has_all_three_roles():
    from app.admin_users import ROLES
    assert ROLES == ("super_admin", "admin", "partner")


def test_statuses_constant_has_all_four_statuses():
    from app.admin_users import STATUSES
    assert STATUSES == ("pending", "approved", "rejected", "suspended")


def test_is_admin_and_is_super_admin_matrix():
    """Partner cannot do anything; admin is unrestricted; super_admin is unrestricted.

    This matrix pins the behavior that guards all downstream authorization checks.
    Dropping a role from _UNRESTRICTED_ROLES or inverting either property must fail.
    """
    # partner: is_admin False, is_super_admin False
    partner = user(role="partner")
    assert not partner.is_admin
    assert not partner.is_super_admin

    # admin: is_admin True, is_super_admin False
    admin = user(role="admin")
    assert admin.is_admin
    assert not admin.is_super_admin

    # super_admin: is_admin True, is_super_admin True
    super_admin = user(role="super_admin")
    assert super_admin.is_admin
    assert super_admin.is_super_admin


def test_from_row_maps_all_fields():
    """from_row must correctly map all seven fields from a dict to AdminUser.

    A field-mapping typo would otherwise surface only in task 3's integration layer.
    """
    from app.admin_users import from_row

    row = {
        "id": "test-id",
        "email": "test@example.com",
        "name": "Test Name",
        "role": "admin",
        "status": "approved",
        "granted_scopes": "ops:read,tracks:read",
        "granted_airports": "KLMO,KJFK",
    }
    admin = from_row(row)
    assert admin.id == "test-id"
    assert admin.email == "test@example.com"
    assert admin.name == "Test Name"
    assert admin.role == "admin"
    assert admin.status == "approved"
    assert admin.granted_scopes == "ops:read,tracks:read"
    assert admin.granted_airports == "KLMO,KJFK"
