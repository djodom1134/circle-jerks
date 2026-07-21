# Admin Google SSO, User Approval, and Self-Service API Keys — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single shared admin account with Google sign-in, a pending-approval queue, three roles, and partner-minted API keys bounded by a server-enforced grant, with the partner API docs published beside the key form.

**Architecture:** Security-critical logic goes in I/O-free modules (`app/api_keys.py`, new `app/admin_users.py`) so it can be unit-tested without HTTP or SQLite, following the precedent already set by `api_keys.py`. Persistence lives in `db.py`, HTTP plumbing in `main.py`. The session cookie keeps its name, TTL, and HMAC scheme; only the claim inside it changes, and the auth dependency loads the user row on every request so suspension is immediate. The frontend keeps role logic as pure functions in `src/lib/` (the repo has no component-testing library — every existing frontend test is a pure `src/lib/*.test.ts`).

**Tech Stack:** FastAPI, pydantic-settings, SQLite via `sqlite3`, `httpx` (already a dependency — no new backend packages), React 18 + Vite + TypeScript, vitest.

**Spec:** `docs/superpowers/specs/2026-07-21-admin-google-sso-design.md`

## Global Constraints

- Base branch is `feat/public-api-keys`. **Not `main`** — `main` is a stale stub roughly 300 commits behind.
- Backend tests: `cd backend && .venv/bin/python -m pytest -q`. Bare `python` and `pytest` are **not** on PATH.
- Frontend tests: `cd frontend && npx vitest run`. Build check: `npm run build`.
- Doc check: `backend/.venv/bin/python scripts/verify_openapi_doc.py` from the repo root.
- No new backend dependencies. No new frontend dependencies.
- Roles are exactly `super_admin`, `admin`, `partner`. Statuses are exactly `pending`, `approved`, `rejected`, `suspended`.
- API scopes are unchanged and remain `ops:read`, `tracks:read`, `aggregates:read`, `ledger:read` (`api_keys.SCOPES`).
- Ownership failures return **404, never 403**, so key ids are not enumerable.
- Keys are revoked, never deleted.
- The dependency name `require_admin` is retained with tightened semantics so the ~20 existing `/admin/*` routes are untouched.
- Settings use `env_prefix="CIRCLEJERK_"`; variables intended to be un-prefixed need an explicit `validation_alias`, matching the existing `GROQ_API_KEY` style.

---

### Task 1: Grant enforcement primitive

The single function that decides whether a user may mint a requested key. Lives in the I/O-free module so it is testable in isolation, and no route re-implements it.

**Files:**
- Modify: `backend/app/api_keys.py` (append to end)
- Test: `backend/tests/test_key_grants.py` (create)

**Interfaces:**
- Consumes: existing `api_keys.SCOPES`, `api_keys.validate_scopes`
- Produces: `api_keys.Grant(scopes: frozenset[str], airports: frozenset[str] | None)`, `api_keys.GrantViolation(ValueError)`, `api_keys.UNRESTRICTED_GRANT`, `api_keys.enforce_grant(requested_scopes: list[str], requested_airports: list[str] | None, grant: Grant) -> None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_key_grants.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_key_grants.py -q`
Expected: FAIL — `ImportError: cannot import name 'UNRESTRICTED_GRANT' from 'app.api_keys'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/api_keys.py`:

```python
class GrantViolation(ValueError):
    """A key request exceeded what its requester was granted."""


@dataclass(frozen=True)
class Grant:
    """The ceiling on the keys a user may mint.

    `airports=None` means every airport. An empty `scopes` set can mint
    nothing, which is what a freshly approved user with no grant gets.
    """

    scopes: frozenset[str]
    airports: frozenset[str] | None


UNRESTRICTED_GRANT = Grant(scopes=frozenset(SCOPES), airports=None)


def enforce_grant(
    requested_scopes: list[str],
    requested_airports: list[str] | None,
    grant: Grant,
) -> None:
    """Raise GrantViolation unless the request fits inside `grant`.

    Called by every key-minting path. Deliberately free of I/O so the check
    that stands between a partner and a wider key than they were given can be
    tested without a database or an HTTP client.
    """
    requested = set(validate_scopes(requested_scopes))
    excess = requested - grant.scopes
    if excess:
        raise GrantViolation(
            f"not granted: {', '.join(sorted(excess))}"
        )

    if grant.airports is None:
        return

    wanted = {icao.strip().upper() for icao in (requested_airports or []) if icao.strip()}
    allowed = ", ".join(sorted(grant.airports))
    if not wanted:
        # An empty airport list means "every airport" everywhere else in this
        # module, so a restricted grant has to reject it explicitly rather
        # than fall through.
        raise GrantViolation(f"this key must be restricted to one of: {allowed}")
    outside = wanted - grant.airports
    if outside:
        raise GrantViolation(
            f"not granted: {', '.join(sorted(outside))}; allowed: {allowed}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_key_grants.py -q`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api_keys.py backend/tests/test_key_grants.py
git commit -m "feat(keys): grant ceiling enforcement primitive"
```

---

### Task 2: Admin user domain module

Roles, statuses, the user record, and the guard predicates that stop a super-admin locking themselves out. Pure, like Task 1.

**Files:**
- Create: `backend/app/admin_users.py`
- Test: `backend/tests/test_admin_users_domain.py` (create)

**Interfaces:**
- Consumes: `api_keys.Grant`, `api_keys.UNRESTRICTED_GRANT`, `api_keys.parse_scopes`, `api_keys.parse_airports`
- Produces: `ROLES`, `STATUSES`, `LOCAL_ADMIN_ID`, `LOCAL_ADMIN_EMAIL`; `AdminUser` dataclass with fields `id, email, name, role, status, granted_scopes, granted_airports`; properties `AdminUser.grant`, `.is_approved`, `.is_admin`, `.is_super_admin`; `from_row(row: dict) -> AdminUser`; `parse_superusers(raw: str | None) -> frozenset[str]`; `guard_modification(actor, target, superusers) -> None` raising `GuardViolation`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_users_domain.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_users_domain.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.admin_users'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/admin_users.py`:

```python
"""Admin user roles, statuses, and the guards around changing them.

Deliberately free of FastAPI and sqlite3, for the same reason api_keys.py is:
the rules that decide who may do what are worth testing without a database or
an HTTP client in the way. Persistence lives in db.py, HTTP plumbing in main.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import api_keys

ROLES: tuple[str, ...] = ("super_admin", "admin", "partner")
STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "suspended")

# Roles whose grant is unrestricted by definition.
_UNRESTRICTED_ROLES = frozenset({"super_admin", "admin"})

LOCAL_ADMIN_ID = "local-admin"
LOCAL_ADMIN_EMAIL = "local-admin@circlejerks.live"


class GuardViolation(ValueError):
    """A user-management change that would be unsafe or self-defeating."""


@dataclass(frozen=True)
class AdminUser:
    id: str
    email: str
    name: str | None
    role: str
    status: str
    granted_scopes: str | None
    granted_airports: str | None

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_admin(self) -> bool:
        return self.role in _UNRESTRICTED_ROLES

    @property
    def grant(self) -> api_keys.Grant:
        """The ceiling on keys this user may mint.

        Stored grant columns are ignored for admin and super_admin. Those roles
        are unrestricted by definition, and honoring a narrower stored value
        would imply an enforcement that never happens.
        """
        if self.role in _UNRESTRICTED_ROLES:
            return api_keys.UNRESTRICTED_GRANT
        return api_keys.Grant(
            scopes=api_keys.parse_scopes(self.granted_scopes or ""),
            airports=api_keys.parse_airports(self.granted_airports),
        )


def from_row(row: dict) -> AdminUser:
    return AdminUser(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
        status=row["status"],
        granted_scopes=row["granted_scopes"],
        granted_airports=row["granted_airports"],
    )


def parse_superusers(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def guard_modification(
    actor: AdminUser,
    target: AdminUser,
    superusers: frozenset[str],
) -> None:
    """Raise GuardViolation when this change would be unsafe.

    Two checks, not three. The spec also called for a "cannot demote the last
    super-admin" guard; it is deliberately absent because it can never fire.
    Only an approved super-admin reaches these routes and no one may target
    their own row, so any super-admin target implies at least two exist and
    demoting one always leaves the actor. A guard that cannot fire is dead
    code, and the one edge where it WOULD fire — a target who is a suspended
    super-admin, and therefore uncounted — is a legitimate edit it would
    wrongly block. ADMIN_SUPERUSERS is the real lockout backstop.
    """
    if actor.id == target.id:
        raise GuardViolation("you cannot change your own access")
    if target.email.lower() in superusers:
        raise GuardViolation(
            "this account is pinned by ADMIN_SUPERUSERS and cannot be changed here"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_users_domain.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/admin_users.py backend/tests/test_admin_users_domain.py
git commit -m "feat(admin): user roles, grants, and modification guards"
```

---

### Task 3: Schema and persistence

**Files:**
- Modify: `backend/app/db.py` — `SCHEMA` string (after the `api_keys` block, around line 363), `_migrate` additions dict (around line 443), and append accessors at end of file
- Test: `backend/tests/test_admin_users_store.py` (create)

**Interfaces:**
- Consumes: `app.admin_users`, `app.api_keys`
- Produces, all in `db`: `upsert_admin_user(conn, *, id, email, google_sub, name, picture, role, status, now) -> dict`; `get_admin_user(conn, user_id) -> dict | None`; `find_admin_user(conn, *, google_sub, email) -> dict | None`; `list_admin_users(conn, status=None) -> list[dict]`; `set_admin_user_access(conn, user_id, *, role, status, granted_scopes, granted_airports, decided_by, now) -> None`; `touch_admin_user_login(conn, user_id, now)`; `list_api_keys(conn, owner_user_id=None)`; `revoke_keys_for_owner(conn, owner_user_id, now) -> int`; `revoke_keys_outside_grant(conn, owner_user_id, grant, now) -> int`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_users_store.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from app import api_keys, db


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(str(tmp_path / "t.sqlite3"))
    yield connection
    connection.close()


def make_user(connection, **kw):
    base = dict(
        id="u1",
        email="p@example.com",
        google_sub="sub-1",
        name="P",
        picture=None,
        role="partner",
        status="pending",
        now=1000,
    )
    base.update(kw)
    return db.upsert_admin_user(connection, **base)


def test_upsert_creates_then_updates_on_google_sub(conn):
    created = make_user(conn)
    assert created["status"] == "pending"
    assert created["granted_scopes"] is None

    again = make_user(conn, name="P2", now=2000)
    assert again["id"] == "u1"
    assert again["name"] == "P2"
    assert len(db.list_admin_users(conn)) == 1


def test_find_matches_by_sub_then_by_email(conn):
    make_user(conn)
    assert db.find_admin_user(conn, google_sub="sub-1", email="other@example.com")["id"] == "u1"
    assert db.find_admin_user(conn, google_sub="missing", email="p@example.com")["id"] == "u1"
    assert db.find_admin_user(conn, google_sub="missing", email="nobody@example.com") is None


def test_email_matching_is_case_insensitive(conn):
    make_user(conn, email="Mixed@Example.com")
    assert db.find_admin_user(conn, google_sub="x", email="mixed@example.com") is not None


def test_list_filters_by_status_newest_first(conn):
    make_user(conn, id="u1", email="a@x.com", google_sub="s1", now=1000)
    make_user(conn, id="u2", email="b@x.com", google_sub="s2", now=3000)
    make_user(conn, id="u3", email="c@x.com", google_sub="s3", status="approved", now=2000)
    pending = db.list_admin_users(conn, status="pending")
    assert [row["id"] for row in pending] == ["u2", "u1"]
    assert [row["id"] for row in db.list_admin_users(conn, status="approved")] == ["u3"]


def test_set_access_records_the_decision(conn):
    make_user(conn)
    db.set_admin_user_access(
        conn,
        "u1",
        role="partner",
        status="approved",
        granted_scopes="ops:read",
        granted_airports="KLMO",
        decided_by="u0",
        now=5000,
    )
    row = db.get_admin_user(conn, "u1")
    assert row["status"] == "approved"
    assert row["granted_scopes"] == "ops:read"
    assert row["decided_by"] == "u0"
    assert row["decided_at"] == 5000


def make_key(connection, key_id, owner, scopes="ops:read", airports="KLMO"):
    db.create_api_key(
        connection,
        key_id=key_id,
        secret_hash="0" * 64,
        name=key_id,
        scopes=scopes,
        airports=airports,
        created_at=1000,
        created_by="someone",
        owner_user_id=owner,
    )


def test_list_api_keys_can_scope_to_one_owner(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1")
    make_key(conn, "bbbbbbbbbbbbbbbb", "u2")
    make_key(conn, "cccccccccccccccc", None)
    assert len(db.list_api_keys(conn)) == 3
    assert [k["id"] for k in db.list_api_keys(conn, owner_user_id="u1")] == ["aaaaaaaaaaaaaaaa"]
    # A legacy key with no owner belongs to nobody and must not surface in a
    # partner's list just because their id is also absent.
    assert db.list_api_keys(conn, owner_user_id=None) != []


def test_revoking_for_an_owner_leaves_other_owners_alone(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1")
    make_key(conn, "bbbbbbbbbbbbbbbb", "u1")
    make_key(conn, "cccccccccccccccc", "u2")
    assert db.revoke_keys_for_owner(conn, "u1", 9000) == 2
    assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] == 9000
    assert db.get_api_key(conn, "cccccccccccccccc")["revoked_at"] is None
    # Idempotent: an already-revoked key is not counted twice.
    assert db.revoke_keys_for_owner(conn, "u1", 9500) == 0


def test_narrowing_a_grant_revokes_exactly_the_keys_that_exceed_it(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1", scopes="ops:read", airports="KLMO")
    make_key(conn, "bbbbbbbbbbbbbbbb", "u1", scopes="ops:read,ledger:read", airports="KLMO")
    make_key(conn, "cccccccccccccccc", "u1", scopes="ops:read", airports="KBJC")
    make_key(conn, "dddddddddddddddd", "u1", scopes="ops:read", airports=None)

    narrowed = api_keys.Grant(scopes=frozenset({"ops:read"}), airports=frozenset({"KLMO"}))
    assert db.revoke_keys_outside_grant(conn, "u1", narrowed, 9000) == 3

    assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is None
    for key_id in ("bbbbbbbbbbbbbbbb", "cccccccccccccccc", "dddddddddddddddd"):
        assert db.get_api_key(conn, key_id)["revoked_at"] == 9000


def test_widening_a_grant_revokes_nothing(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1", scopes="ops:read", airports="KLMO")
    assert db.revoke_keys_outside_grant(conn, "u1", api_keys.UNRESTRICTED_GRANT, 9000) == 0


def test_email_is_unique(conn):
    make_user(conn, id="u1", email="dupe@x.com", google_sub="s1")
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert_admin_user(
            conn,
            id="u2",
            email="dupe@x.com",
            google_sub="s2",
            name=None,
            picture=None,
            role="partner",
            status="pending",
            now=1,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_users_store.py -q`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'upsert_admin_user'`

> If `db.connect` has a different name in this codebase, check the top of `db.py` and use the actual connection helper; the rest of the test is unaffected.

- [ ] **Step 3: Add the schema**

In `backend/app/db.py`, in the `SCHEMA` string immediately after the `idx_api_keys_created` index line (around line 363), add:

```sql
CREATE TABLE IF NOT EXISTS admin_users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  google_sub TEXT UNIQUE,
  name TEXT,
  picture TEXT,
  role TEXT NOT NULL,
  status TEXT NOT NULL,
  granted_scopes TEXT,
  granted_airports TEXT,
  requested_at INTEGER NOT NULL,
  decided_at INTEGER,
  decided_by TEXT,
  last_login_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_admin_users_status ON admin_users(status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_keys_owner ON api_keys(owner_user_id);
```

In `_migrate`, add one entry to the `additions` dict:

```python
        "api_keys": [("owner_user_id", "TEXT")],
```

> The `idx_api_keys_owner` index is in `SCHEMA`, which runs after `_migrate` adds the column on an existing database. Confirm that ordering in `init_db` (around line 435); if `SCHEMA` executes *before* `_migrate`, move that one index line into `_migrate` after the `ALTER TABLE` loop instead, or the index creation fails on an existing database.

- [ ] **Step 4: Add the accessors**

Append to `backend/app/db.py`:

```python
_ADMIN_USER_COLUMNS = (
    "id, email, google_sub, name, picture, role, status, granted_scopes, "
    "granted_airports, requested_at, decided_at, decided_by, last_login_at"
)


def upsert_admin_user(
    conn: sqlite3.Connection,
    *,
    id: str,
    email: str,
    google_sub: str | None,
    name: str | None,
    picture: str | None,
    role: str,
    status: str,
    now: int,
) -> dict:
    """Create the row, or refresh the profile fields of the existing one.

    Only profile fields are refreshed on conflict. role and status are decided
    by a super-admin, so a login must never reset them — the one exception is
    the ADMIN_SUPERUSERS pin, which main.py applies explicitly afterwards.
    """
    existing = find_admin_user(conn, google_sub=google_sub, email=email)
    if existing:
        conn.execute(
            "UPDATE admin_users SET email = ?, google_sub = COALESCE(?, google_sub), "
            "name = ?, picture = ? WHERE id = ?",
            (email.lower(), google_sub, name, picture, existing["id"]),
        )
        return get_admin_user(conn, existing["id"])

    conn.execute(
        """
        INSERT INTO admin_users
        (id, email, google_sub, name, picture, role, status, requested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (id, email.lower(), google_sub, name, picture, role, status, int(now)),
    )
    return get_admin_user(conn, id)


def get_admin_user(conn: sqlite3.Connection, user_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {_ADMIN_USER_COLUMNS} FROM admin_users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def find_admin_user(
    conn: sqlite3.Connection, *, google_sub: str | None, email: str | None
) -> dict | None:
    """Match on the Google subject first, then on the email.

    The email fallback is safe ONLY because the caller has already asserted
    email_verified on the ID token. Without that assertion it is an account
    takeover primitive.
    """
    if google_sub:
        row = conn.execute(
            f"SELECT {_ADMIN_USER_COLUMNS} FROM admin_users WHERE google_sub = ?",
            (google_sub,),
        ).fetchone()
        if row:
            return dict(row)
    if email:
        row = conn.execute(
            f"SELECT {_ADMIN_USER_COLUMNS} FROM admin_users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
        if row:
            return dict(row)
    return None


def list_admin_users(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            f"SELECT {_ADMIN_USER_COLUMNS} FROM admin_users WHERE status = ? "
            "ORDER BY requested_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_ADMIN_USER_COLUMNS} FROM admin_users ORDER BY requested_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def set_admin_user_access(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    role: str,
    status: str,
    granted_scopes: str | None,
    granted_airports: str | None,
    decided_by: str | None,
    now: int,
) -> None:
    conn.execute(
        """
        UPDATE admin_users
        SET role = ?, status = ?, granted_scopes = ?, granted_airports = ?,
            decided_by = ?, decided_at = ?
        WHERE id = ?
        """,
        (role, status, granted_scopes, granted_airports, decided_by, int(now), user_id),
    )


def touch_admin_user_login(conn: sqlite3.Connection, user_id: str, now: int) -> None:
    conn.execute(
        "UPDATE admin_users SET last_login_at = ? WHERE id = ?", (int(now), user_id)
    )


def revoke_keys_for_owner(conn: sqlite3.Connection, owner_user_id: str, now: int) -> int:
    """Revoke every live key belonging to one user. Returns the count."""
    cursor = conn.execute(
        "UPDATE api_keys SET revoked_at = ? WHERE owner_user_id = ? AND revoked_at IS NULL",
        (int(now), owner_user_id),
    )
    return cursor.rowcount


def revoke_keys_outside_grant(
    conn: sqlite3.Connection, owner_user_id: str, grant, now: int
) -> int:
    """Revoke this user's live keys that a narrowed grant no longer covers.

    Evaluated in Python rather than SQL because scopes and airports are stored
    as comma-joined strings; a LIKE-based query would be subtly wrong on
    substrings ("ops:read" matching inside a longer scope name).
    """
    revoked = 0
    for row in list_api_keys(conn, owner_user_id=owner_user_id):
        if row["revoked_at"] is not None:
            continue
        key_scopes = api_keys.parse_scopes(row["scopes"])
        key_airports = api_keys.parse_airports(row["airports"])
        if not key_scopes <= grant.scopes:
            outside = True
        elif grant.airports is None:
            outside = False
        elif key_airports is None:
            outside = True  # key covers every airport; the grant no longer does
        else:
            outside = not key_airports <= grant.airports
        if outside and revoke_api_key(conn, row["id"], now):
            revoked += 1
    return revoked
```

Add `from . import api_keys` to the imports at the top of `db.py` if it is not already there.

- [ ] **Step 5: Update the two changed existing functions**

Replace `create_api_key` and `list_api_keys` in `backend/app/db.py`:

```python
def create_api_key(
    conn: sqlite3.Connection,
    *,
    key_id: str,
    secret_hash: str,
    name: str,
    scopes: str,
    airports: str | None,
    created_at: int,
    created_by: str | None,
    owner_user_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO api_keys
        (id, secret_hash, name, scopes, airports, created_at, created_by, owner_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (key_id, secret_hash, name, scopes, airports, int(created_at), created_by, owner_user_id),
    )


def list_api_keys(conn: sqlite3.Connection, owner_user_id: str | None = None) -> list[dict]:
    """Metadata for the admin list. Never returns `secret_hash`.

    `owner_user_id=None` means "every key" — the super-admin view. It does NOT
    mean "keys with no owner"; legacy unowned keys are only reachable through
    the unfiltered call.
    """
    if owner_user_id is not None:
        rows = conn.execute(
            f"SELECT {_API_KEY_PUBLIC_COLUMNS} FROM api_keys WHERE owner_user_id = ? "
            "ORDER BY created_at DESC",
            (owner_user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_API_KEY_PUBLIC_COLUMNS} FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]
```

Extend `_API_KEY_PUBLIC_COLUMNS` with the new column:

```python
_API_KEY_PUBLIC_COLUMNS = (
    "id, name, scopes, airports, created_at, created_by, owner_user_id, "
    "last_used_at, revoked_at"
)
```

- [ ] **Step 6: Run the new tests and the full suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_users_store.py -q`
Expected: PASS, 11 passed

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS, 532+ passed. `_API_KEY_PUBLIC_COLUMNS` now carries `owner_user_id`, so if `_api_key_record` in `main.py` uses an explicit field whitelist (it does), nothing leaks and nothing breaks. If any test fails on an unexpected key, fix the whitelist, not the test.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py backend/tests/test_admin_users_store.py
git commit -m "feat(db): admin_users table and key ownership"
```

---

### Task 4: Session identity and auth dependencies

The pivot: the cookie stops naming a config value and starts naming a row.

**Files:**
- Modify: `backend/app/settings.py` (around line 141)
- Modify: `backend/app/main.py` — `sign_admin_token`, `decode_admin_token`, `require_admin` (lines ~293-334), `admin_login` (~685), `admin_session` (~713)
- Modify: `backend/tests/test_admin_api_keys.py` (one assertion)
- Test: `backend/tests/test_admin_session.py` (create)

**Interfaces:**
- Consumes: `app.admin_users.AdminUser`, `db.upsert_admin_user`, `db.get_admin_user`
- Produces: `main.current_user(...) -> AdminUser` (raises 401/403); `main.require_user`, `main.require_admin`, `main.require_super_admin` — all FastAPI dependencies returning `AdminUser`; `main.apply_superuser_pin(conn, row: dict, settings) -> dict`; `main.superuser_emails(settings) -> frozenset[str]`; `main.sign_admin_token(settings, user_id: str) -> str`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_session.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

PASSWORD = "test-admin-password"


def configure(tmp_path, monkeypatch, **extra):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def login(client: TestClient):
    resp = client.post("/admin/login", json={"username": "admin", "password": PASSWORD})
    assert resp.status_code == 200
    return resp


def test_password_login_creates_a_real_super_admin_row(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        session = client.get("/admin/session").json()
        assert session["role"] == "super_admin"
        assert session["status"] == "approved"
        assert session["id"] == "local-admin"


def test_session_cookie_is_lax_not_strict(tmp_path, monkeypatch):
    # Strict is unreliable across the Google -> callback -> /admin redirect
    # chain. Lax still refuses cross-site POST, and every state-changing admin
    # route is POST or PATCH.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = login(client)
        cookie = resp.headers["set-cookie"].lower()
        assert "samesite=lax" in cookie
        assert "httponly" in cookie


def test_a_suspended_user_loses_access_immediately_without_a_new_cookie(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        assert client.get("/admin/session").status_code == 200

        settings = get_settings()
        with db_session(settings.database_path) as conn:
            db.set_admin_user_access(
                conn,
                "local-admin",
                role="super_admin",
                status="suspended",
                granted_scopes=None,
                granted_airports=None,
                decided_by=None,
                now=1,
            )

        # Same cookie, still cryptographically valid, now refused: the reason
        # the dependency reads the row on every request.
        resp = client.get("/admin/session")
        assert resp.status_code == 403
        assert resp.json()["detail"]["status"] == "suspended"


def test_a_pending_user_is_told_they_are_pending(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        settings = get_settings()
        with db_session(settings.database_path) as conn:
            db.set_admin_user_access(
                conn, "local-admin", role="partner", status="pending",
                granted_scopes=None, granted_airports=None, decided_by=None, now=1,
            )
        resp = client.get("/admin/session")
        assert resp.status_code == 403
        assert resp.json()["detail"]["status"] == "pending"


def test_a_partner_is_refused_by_require_admin_routes(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        settings = get_settings()
        with db_session(settings.database_path) as conn:
            db.set_admin_user_access(
                conn, "local-admin", role="partner", status="approved",
                granted_scopes="ops:read", granted_airports="KLMO",
                decided_by=None, now=1,
            )
        assert client.get("/admin/dashboard").status_code == 403
        assert client.get("/admin/session").status_code == 200


def test_a_forged_cookie_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("circlejerk_admin", "bm90aGluZw.deadbeef")
        assert client.get("/admin/session").status_code == 401


def test_a_cookie_naming_a_deleted_user_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        settings = get_settings()
        with db_session(settings.database_path) as conn:
            conn.execute("DELETE FROM admin_users WHERE id = 'local-admin'")
        assert client.get("/admin/session").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_session.py -q`
Expected: FAIL — `/admin/session` returns no `role` key

- [ ] **Step 3: Add the settings**

In `backend/app/settings.py`, after `admin_session_seconds` (line 141):

```python
    admin_superusers: str | None = Field(default=None, validation_alias="ADMIN_SUPERUSERS")
    google_oauth_client_id: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_ID"
    )
    google_oauth_client_secret: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_SECRET", exclude=True
    )
    google_oauth_redirect_uri: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_REDIRECT_URI"
    )
```

`exclude=True` on the secret matches how `admin_password` is already handled, keeping it out of any settings dump.

- [ ] **Step 4: Rewrite the auth core in main.py**

Replace `sign_admin_token`, `decode_admin_token`, and `require_admin` (lines ~293-334) with:

```python
def sign_admin_token(settings: Settings, user_id: str) -> str:
    payload = {
        "uid": user_id,
        "exp": int(time.time()) + settings.admin_session_seconds,
        "nonce": secrets.token_urlsafe(12),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def decode_admin_token(settings: Settings, token: str) -> dict | None:
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except (json.JSONDecodeError, ValueError):
        return None
    if not payload.get("uid"):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def superuser_emails(settings: Settings) -> frozenset[str]:
    return admin_users.parse_superusers(settings.admin_superusers)


def apply_superuser_pin(conn, row: dict, settings: Settings) -> dict:
    """Force ADMIN_SUPERUSERS accounts to approved super_admin, every login.

    Applied on every resolution rather than only at creation, so a UI misclick
    cannot lock the operator out of their own deployment.
    """
    if row["email"].lower() not in superuser_emails(settings):
        return row
    if row["role"] == "super_admin" and row["status"] == "approved":
        return row
    db.set_admin_user_access(
        conn,
        row["id"],
        role="super_admin",
        status="approved",
        granted_scopes=None,
        granted_airports=None,
        decided_by=row["id"],
        now=int(time.time()),
    )
    return db.get_admin_user(conn, row["id"])


def current_user(
    settings: Annotated[Settings, Depends(settings_dep)],
    admin_session: Annotated[str | None, Cookie(alias=ADMIN_COOKIE_NAME)] = None,
) -> admin_users.AdminUser:
    """Authenticate the cookie and load the row it names.

    The row is read on EVERY request. That is what makes suspension take
    effect immediately instead of whenever a 12-hour cookie happens to expire.
    """
    if not admin_session:
        raise HTTPException(status_code=401, detail="admin login required")
    payload = decode_admin_token(settings, admin_session)
    if not payload:
        raise HTTPException(status_code=401, detail="admin login required")
    with db_session(settings.database_path) as conn:
        row = db.get_admin_user(conn, payload["uid"])
        if row:
            row = apply_superuser_pin(conn, row, settings)
    if not row:
        raise HTTPException(status_code=401, detail="admin login required")
    return admin_users.from_row(row)


def require_user(
    user: Annotated[admin_users.AdminUser, Depends(current_user)],
) -> admin_users.AdminUser:
    if not user.is_approved:
        # The caller's own status, and nothing else — enough for the SPA to
        # render the right screen, no information about anyone else.
        raise HTTPException(status_code=403, detail={"status": user.status})
    return user


def require_admin(
    user: Annotated[admin_users.AdminUser, Depends(require_user)],
) -> admin_users.AdminUser:
    """Name retained deliberately: every existing /admin/* route depends on it."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail={"status": user.status, "role": user.role})
    return user


def require_super_admin(
    user: Annotated[admin_users.AdminUser, Depends(require_user)],
) -> admin_users.AdminUser:
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail={"status": user.status, "role": user.role})
    return user
```

Add `from . import admin_users` to the imports at the top of `main.py`.

Delete `admin_auth_configured` usage from `require_admin` only — the function itself is still used by `admin_login` and stays.

- [ ] **Step 5: Rewrite login and session routes**

Replace `admin_login` and `admin_session` in `backend/app/main.py`:

```python
@app.post("/admin/login")
async def admin_login(
    payload: AdminLoginRequest,
    response: Response,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    """Break-glass login. Google is the normal path.

    Upserts a REAL row rather than minting a synthetic identity, so key
    ownership, created_by, and decided_by all have one identity model with no
    special case threaded through them.
    """
    if not admin_auth_configured(settings):
        raise HTTPException(status_code=503, detail="admin credentials are not configured")
    if payload.username != settings.admin_username or not verify_admin_password(settings, payload.password):
        raise HTTPException(status_code=401, detail="invalid admin credentials")

    now = int(time.time())
    with db_session(settings.database_path) as conn:
        row = db.get_admin_user(conn, admin_users.LOCAL_ADMIN_ID)
        if not row:
            row = db.upsert_admin_user(
                conn,
                id=admin_users.LOCAL_ADMIN_ID,
                email=admin_users.LOCAL_ADMIN_EMAIL,
                google_sub=None,
                name=settings.admin_username,
                picture=None,
                role="super_admin",
                status="approved",
                now=now,
            )
        db.touch_admin_user_login(conn, row["id"], now)

    response.set_cookie(
        ADMIN_COOKIE_NAME,
        sign_admin_token(settings, row["id"]),
        max_age=settings.admin_session_seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    return {"ok": True, "username": settings.admin_username}


@app.get("/admin/session")
async def admin_session(
    user: Annotated[admin_users.AdminUser, Depends(require_user)],
):
    return {
        "ok": True,
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "username": user.name or user.email,
        "role": user.role,
        "status": user.status,
        "scopes": sorted(user.grant.scopes),
        "airports": sorted(user.grant.airports) if user.grant.airports is not None else None,
    }
```

> The break-glass row is created only when absent, so a super-admin who later suspends it stays in control — the login will then fail at `require_user` rather than silently resurrecting access.

- [ ] **Step 6: Update the one existing assertion this changes**

In `backend/tests/test_admin_api_keys.py`, `created_by` is now the acting user's email rather than the config username:

```python
-        assert body["record"]["created_by"] == "admin"
+        assert body["record"]["created_by"] == "local-admin@circlejerks.live"
```

- [ ] **Step 7: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_session.py -q`
Expected: PASS, 7 passed

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS. Any remaining failure is a route still typed `Annotated[dict, Depends(require_admin)]` and *using* the returned value as a dict — change the annotation to `admin_users.AdminUser`. Routes that discard it (`_: Annotated[dict, ...]`) work unchanged; leave them.

- [ ] **Step 8: Commit**

```bash
git add backend/app/settings.py backend/app/main.py backend/tests/test_admin_session.py backend/tests/test_admin_api_keys.py
git commit -m "feat(admin): sessions name a user row, not a config value"
```

---

### Task 5: Google OAuth

**Files:**
- Create: `backend/app/google_oauth.py`
- Modify: `backend/app/main.py` (append routes near the other `/admin/auth` routes)
- Test: `backend/tests/test_google_oauth.py` (create), `backend/tests/test_admin_google_login.py` (create)

**Interfaces:**
- Consumes: `settings.google_oauth_*`, `db.upsert_admin_user`, `main.apply_superuser_pin`, `main.sign_admin_token`
- Produces: `google_oauth.build_authorize_url(client_id, redirect_uri, state, code_challenge) -> str`; `google_oauth.make_pkce() -> tuple[str, str]` (verifier, challenge); `google_oauth.decode_id_token(raw: str) -> dict`; `google_oauth.validate_claims(claims: dict, client_id: str, now: int) -> None` raising `IdTokenError`; `google_oauth.OAUTH_STATE_COOKIE`
- HTTP: `GET /admin/auth/methods`, `GET /admin/auth/google/start`, `GET /admin/auth/google/callback`

- [ ] **Step 1: Write the failing test for the pure half**

Create `backend/tests/test_google_oauth.py`:

```python
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.google_oauth import (
    IdTokenError,
    build_authorize_url,
    decode_id_token,
    make_pkce,
    validate_claims,
)

CLIENT_ID = "client-123.apps.googleusercontent.com"


def encode(claims: dict) -> str:
    def seg(data: dict) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.signature"


def claims(**kw) -> dict:
    base = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "p@example.com",
        "email_verified": True,
        "exp": 2000,
        "name": "P",
    }
    base.update(kw)
    return base


def test_authorize_url_carries_the_required_parameters():
    url = build_authorize_url(CLIENT_ID, "https://x.test/cb", "state-1", "challenge-1")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["https://x.test/cb"]
    assert query["state"] == ["state-1"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge"] == ["challenge-1"]
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == {"openid", "email", "profile"}


def test_pkce_challenge_is_the_s256_of_the_verifier():
    import hashlib

    verifier, challenge = make_pkce()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode().rstrip("=")
    assert challenge == expected
    assert verifier != challenge


def test_decode_reads_the_payload_segment():
    assert decode_id_token(encode(claims()))["sub"] == "google-sub-1"


def test_decode_rejects_a_malformed_token():
    with pytest.raises(IdTokenError):
        decode_id_token("not-a-jwt")


def test_valid_claims_pass():
    validate_claims(claims(), CLIENT_ID, now=1000)


def test_both_accepted_issuer_spellings_pass():
    validate_claims(claims(iss="accounts.google.com"), CLIENT_ID, now=1000)


def test_wrong_audience_is_rejected():
    # A token minted for a different OAuth client is a valid Google token and
    # a total authentication bypass if aud is not checked.
    with pytest.raises(IdTokenError):
        validate_claims(claims(aud="someone-else"), CLIENT_ID, now=1000)


def test_wrong_issuer_is_rejected():
    with pytest.raises(IdTokenError):
        validate_claims(claims(iss="https://evil.test"), CLIENT_ID, now=1000)


def test_expired_token_is_rejected():
    with pytest.raises(IdTokenError):
        validate_claims(claims(exp=999), CLIENT_ID, now=1000)


def test_unverified_email_is_rejected():
    with pytest.raises(IdTokenError) as exc:
        validate_claims(claims(email_verified=False), CLIENT_ID, now=1000)
    assert "verified" in str(exc.value)


def test_missing_email_or_sub_is_rejected():
    for missing in ("email", "sub"):
        broken = claims()
        broken.pop(missing)
        with pytest.raises(IdTokenError):
            validate_claims(broken, CLIENT_ID, now=1000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_google_oauth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.google_oauth'`

- [ ] **Step 3: Write the module**

Create `backend/app/google_oauth.py`:

```python
"""Google OIDC authorization-code flow, minus the I/O.

There is deliberately NO JWKS fetch, cache, or key-rotation handling here. The
ID token is read from the response of a direct, server-to-server TLS call to
Google's token endpoint, which OpenID Connect Core section 3.1.3.7 accepts as
sufficient without re-verifying the signature. Avoiding that machinery — and
the failure modes of a stale key cache — is why this flow was chosen over a
frontend Google Identity Services button.

The token exchange itself (the one network call) lives in main.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from urllib.parse import urlencode

AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
OAUTH_STATE_COOKIE = "circlejerk_oauth"
STATE_TTL_SECONDS = 600
_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class IdTokenError(ValueError):
    """The ID token is malformed or fails a claim check."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_pkce() -> tuple[str, str]:
    """Return (verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(
    client_id: str, redirect_uri: str, state: str, code_challenge: str
) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
            "access_type": "online",
        }
    )
    return f"{AUTHORIZE_ENDPOINT}?{query}"


def decode_id_token(raw: str) -> dict:
    """Read the payload segment. Signature verification is not performed here."""
    parts = raw.split(".")
    if len(parts) != 3:
        raise IdTokenError("malformed id_token")
    payload = parts[1]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise IdTokenError("unreadable id_token") from exc
    if not isinstance(claims, dict):
        raise IdTokenError("unreadable id_token")
    return claims


def validate_claims(claims: dict, client_id: str, now: int) -> None:
    if claims.get("aud") != client_id:
        raise IdTokenError("id_token was not issued for this client")
    if claims.get("iss") not in _ISSUERS:
        raise IdTokenError("unexpected issuer")
    if int(claims.get("exp", 0)) <= now:
        raise IdTokenError("id_token has expired")
    if not claims.get("sub"):
        raise IdTokenError("id_token has no subject")
    if not claims.get("email"):
        raise IdTokenError("id_token has no email")
    if claims.get("email_verified") is not True:
        raise IdTokenError("a verified Google account is required")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_google_oauth.py -q`
Expected: PASS, 11 passed

- [ ] **Step 5: Write the failing route test**

Create `backend/tests/test_admin_google_login.py`:

```python
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

CLIENT_ID = "client-123.apps.googleusercontent.com"
REDIRECT = "https://circlejerks.live/api/admin/auth/google/callback"


def configure(tmp_path, monkeypatch, *, google=True, superusers=None):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", "pw")
    if google:
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", CLIENT_ID)
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
        monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", REDIRECT)
    else:
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    if superusers:
        monkeypatch.setenv("ADMIN_SUPERUSERS", superusers)
    else:
        monkeypatch.delenv("ADMIN_SUPERUSERS", raising=False)
    get_settings.cache_clear()


def id_token(**overrides) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "p@example.com",
        "email_verified": True,
        "exp": 9999999999,
        "name": "Partner P",
        "picture": "https://x.test/p.png",
    }
    claims.update(overrides)

    def seg(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.sig"


def stub_exchange(monkeypatch, token: str | None = None, fail: bool = False):
    """Replace the one network call. Everything else runs for real."""
    async def fake(settings, code, verifier):
        if fail:
            raise RuntimeError("token endpoint said no")
        return {"id_token": token or id_token()}

    monkeypatch.setattr("app.main.exchange_google_code", fake)


def start(client: TestClient) -> str:
    resp = client.get("/admin/auth/google/start", follow_redirects=False)
    assert resp.status_code == 307
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


def test_methods_reports_what_is_configured(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, google=True)
    with TestClient(app) as client:
        assert client.get("/admin/auth/methods").json() == {"google": True, "password": True}

    configure(tmp_path, monkeypatch, google=False)
    with TestClient(app) as client:
        assert client.get("/admin/auth/methods").json()["google"] is False


def test_start_is_503_when_google_is_not_configured(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, google=False)
    with TestClient(app) as client:
        assert client.get("/admin/auth/google/start", follow_redirects=False).status_code == 503


def test_a_new_user_lands_pending_with_no_grant(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 307
        assert resp.headers["location"] == "/admin"

        session = client.get("/admin/session")
        assert session.status_code == 403
        assert session.json()["detail"]["status"] == "pending"

        with db_session(get_settings().database_path) as conn:
            row = db.find_admin_user(conn, google_sub="google-sub-1", email=None)
        assert row["role"] == "partner"
        assert row["granted_scopes"] is None


def test_an_allowlisted_email_becomes_super_admin_even_if_the_row_says_otherwise(
    tmp_path, monkeypatch
):
    configure(tmp_path, monkeypatch, superusers="P@Example.com")
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        client.get(f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False)
        body = client.get("/admin/session").json()
        assert body["role"] == "super_admin"
        assert body["status"] == "approved"

        # Demote in the database, then prove the next request re-pins it.
        with db_session(get_settings().database_path) as conn:
            row = db.find_admin_user(conn, google_sub="google-sub-1", email=None)
            db.set_admin_user_access(
                conn, row["id"], role="partner", status="suspended",
                granted_scopes=None, granted_airports=None, decided_by=None, now=1,
            )
        assert client.get("/admin/session").json()["role"] == "super_admin"


def test_state_mismatch_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        start(client)
        resp = client.get(
            "/admin/auth/google/callback?code=abc&state=wrong", follow_redirects=False
        )
        assert resp.status_code == 400


def test_callback_without_a_state_cookie_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        client.cookies.clear()
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 400


def test_an_unverified_email_is_refused(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, token=id_token(email_verified=False))
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 403


def test_a_token_for_another_client_is_refused(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, token=id_token(aud="some-other-client"))
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 400


def test_replaying_a_consumed_state_is_refused(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        first = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert first.status_code == 307
        # The state cookie is cleared on use, so the same code cannot be
        # replayed against a fresh session.
        replay = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert replay.status_code == 400


def test_a_failed_token_exchange_is_a_502(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, fail=True)
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 502
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_google_login.py -q`
Expected: FAIL — 404 on `/admin/auth/methods`

- [ ] **Step 7: Write the routes**

Add to `backend/app/main.py`, after `admin_session`:

```python
def google_configured(settings: Settings) -> bool:
    return bool(
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_redirect_uri
    )


async def exchange_google_code(settings: Settings, code: str, verifier: str) -> dict:
    """The one network call in this flow. Separated so tests can replace it."""
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        resp = await client.post(
            google_oauth.TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    resp.raise_for_status()
    return resp.json()


@app.get("/admin/auth/methods")
async def admin_auth_methods(settings: Annotated[Settings, Depends(settings_dep)]):
    """Unauthenticated: the login screen needs it before anyone is signed in."""
    return {
        "google": google_configured(settings),
        "password": admin_auth_configured(settings),
    }


@app.get("/admin/auth/google/start")
async def admin_google_start(
    response: Response,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    if not google_configured(settings):
        raise HTTPException(status_code=503, detail="google sign-in is not configured")
    state = secrets.token_urlsafe(24)
    verifier, challenge = google_oauth.make_pkce()
    payload = json.dumps({"state": state, "verifier": verifier}, separators=(",", ":"))
    body = _b64encode(payload.encode("utf-8"))
    signature = hmac.new(
        settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    url = google_oauth.build_authorize_url(
        settings.google_oauth_client_id,
        settings.google_oauth_redirect_uri,
        state,
        challenge,
    )
    redirect = RedirectResponse(url, status_code=307)
    redirect.set_cookie(
        google_oauth.OAUTH_STATE_COOKIE,
        f"{body}.{signature}",
        max_age=google_oauth.STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    return redirect


def _read_state_cookie(settings: Settings, raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        body, signature = raw.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(
        settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        return json.loads(_b64decode(body))
    except (ValueError, json.JSONDecodeError):
        return None


@app.get("/admin/auth/google/callback")
async def admin_google_callback(
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
    code: str | None = None,
    state: str | None = None,
):
    if not google_configured(settings):
        raise HTTPException(status_code=503, detail="google sign-in is not configured")

    stored = _read_state_cookie(settings, request.cookies.get(google_oauth.OAUTH_STATE_COOKIE))
    if not stored or not state or not secrets.compare_digest(stored.get("state", ""), state):
        # One message for every failure mode here: which check failed is not
        # information a caller needs.
        raise HTTPException(status_code=400, detail="sign-in could not be completed")
    if not code:
        raise HTTPException(status_code=400, detail="sign-in could not be completed")

    try:
        tokens = await exchange_google_code(settings, code, stored["verifier"])
    except Exception as exc:  # noqa: BLE001 - upstream failure, logged not surfaced
        logger.warning("google token exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="google sign-in is unavailable") from exc

    try:
        claims = google_oauth.decode_id_token(tokens.get("id_token", ""))
        google_oauth.validate_claims(claims, settings.google_oauth_client_id, int(time.time()))
    except google_oauth.IdTokenError as exc:
        status = 403 if "verified" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    now = int(time.time())
    with db_session(settings.database_path) as conn:
        row = db.upsert_admin_user(
            conn,
            id=secrets.token_hex(16),
            email=claims["email"],
            google_sub=claims["sub"],
            name=claims.get("name"),
            picture=claims.get("picture"),
            role="partner",
            status="pending",
            now=now,
        )
        row = apply_superuser_pin(conn, row, settings)
        db.touch_admin_user_login(conn, row["id"], now)

    redirect = RedirectResponse("/admin", status_code=307)
    redirect.set_cookie(
        ADMIN_COOKIE_NAME,
        sign_admin_token(settings, row["id"]),
        max_age=settings.admin_session_seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    # Consume the state so the same authorization code cannot be replayed.
    redirect.delete_cookie(google_oauth.OAUTH_STATE_COOKIE, path="/")
    return redirect
```

Add to the imports at the top of `main.py`: `from . import google_oauth` and `from fastapi.responses import RedirectResponse`. Confirm `httpx` and `logger` are already imported in `main.py` — if `logger` is absent, use the module's existing logging pattern.

- [ ] **Step 8: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_google_login.py tests/test_google_oauth.py -q`
Expected: PASS, 21 passed

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/google_oauth.py backend/app/main.py backend/tests/test_google_oauth.py backend/tests/test_admin_google_login.py
git commit -m "feat(admin): google sign-in with PKCE and a pending-by-default landing"
```

---

### Task 6: User management routes

**Files:**
- Modify: `backend/app/main.py` (append after the Google routes; add request models near the other pydantic models)
- Test: `backend/tests/test_admin_user_management.py` (create)

**Interfaces:**
- Consumes: `require_super_admin`, `admin_users.guard_modification`, `db.set_admin_user_access`, `db.revoke_keys_for_owner`, `db.revoke_keys_outside_grant`
- Produces: `GET /admin/users`, `POST /admin/users/{user_id}/approve`, `POST /admin/users/{user_id}/reject`, `POST /admin/users/{user_id}/suspend`, `PATCH /admin/users/{user_id}`; response shape `{"users": [{id, email, name, picture, role, status, scopes, airports, requested_at, decided_at, last_login_at}]}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_user_management.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

PASSWORD = "pw"


def configure(tmp_path, monkeypatch, superusers=None):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    if superusers:
        monkeypatch.setenv("ADMIN_SUPERUSERS", superusers)
    else:
        monkeypatch.delenv("ADMIN_SUPERUSERS", raising=False)
    get_settings.cache_clear()


def login(client):
    assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200


def seed_partner(user_id="u2", email="p@example.com", status="pending"):
    with db_session(get_settings().database_path) as conn:
        db.upsert_admin_user(
            conn, id=user_id, email=email, google_sub=f"sub-{user_id}",
            name="P", picture=None, role="partner", status=status, now=100,
        )


def test_only_a_super_admin_reaches_the_user_list(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/admin/users").status_code == 401
        login(client)
        assert client.get("/admin/users").status_code == 200


def test_pending_users_are_listed(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        body = client.get("/admin/users?status=pending").json()
        assert [u["email"] for u in body["users"]] == ["p@example.com"]
        assert body["users"][0]["status"] == "pending"


def test_approving_records_role_and_grant(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["klmo"],
        })
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["status"] == "approved"
        assert user["scopes"] == ["ops:read"]
        assert user["airports"] == ["KLMO"]


def test_approving_as_admin_stores_an_unrestricted_grant(tmp_path, monkeypatch):
    # Storing a narrower grant on a role the server treats as unrestricted
    # would be a value nothing ever enforces.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "admin", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.json()["user"]["airports"] is None
        assert set(resp.json()["user"]["scopes"]) == {
            "ops:read", "tracks:read", "aggregates:read", "ledger:read"
        }


def test_rejecting_is_terminal(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        assert client.post("/admin/users/u2/reject").status_code == 200
        assert client.get("/admin/users?status=pending").json()["users"] == []
        assert client.get("/admin/users?status=rejected").json()["users"][0]["id"] == "u2"


def make_key(owner, key_id, scopes="ops:read", airports="KLMO"):
    with db_session(get_settings().database_path) as conn:
        db.create_api_key(
            conn, key_id=key_id, secret_hash="0" * 64, name=key_id, scopes=scopes,
            airports=airports, created_at=1, created_by="x", owner_user_id=owner,
        )


def test_suspending_revokes_that_users_keys(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        seed_partner(user_id="u3", email="other@example.com", status="approved")
        make_key("u2", "aaaaaaaaaaaaaaaa")
        make_key("u3", "bbbbbbbbbbbbbbbb")

        resp = client.post("/admin/users/u2/suspend")
        assert resp.status_code == 200
        assert resp.json()["revoked_keys"] == 1
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is not None
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is None


def test_narrowing_a_grant_revokes_the_keys_that_exceed_it(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        make_key("u2", "aaaaaaaaaaaaaaaa", scopes="ops:read", airports="KLMO")
        make_key("u2", "bbbbbbbbbbbbbbbb", scopes="ledger:read", airports="KLMO")

        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.json()["revoked_keys"] == 1
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is None
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is not None


def test_widening_a_grant_revokes_nothing(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        make_key("u2", "aaaaaaaaaaaaaaaa", scopes="ops:read", airports="KLMO")
        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read", "ledger:read"], "airports": None,
        })
        assert resp.json()["revoked_keys"] == 0


def test_you_cannot_change_your_own_access(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        resp = client.post("/admin/users/local-admin/suspend")
        assert resp.status_code == 400
        assert "your own" in resp.json()["detail"]


def test_an_allowlisted_account_cannot_be_changed(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, superusers="pinned@example.com")
    with TestClient(app) as client:
        login(client)
        seed_partner(user_id="u9", email="pinned@example.com", status="approved")
        resp = client.post("/admin/users/u9/suspend")
        assert resp.status_code == 400
        assert "ADMIN_SUPERUSERS" in resp.json()["detail"]


def test_one_super_admin_may_demote_another(tmp_path, monkeypatch):
    # There is no "last super-admin" guard, because it could never fire: the
    # actor is an approved super-admin who cannot target themselves, so one
    # always survives. This test pins that demotion is permitted, so nobody
    # reintroduces the dead guard and breaks it.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(user_id="u5", email="s@example.com", status="approved")
        client.post("/admin/users/u5/approve", json={
            "role": "super_admin", "scopes": [], "airports": None,
        })
        resp = client.patch("/admin/users/u5", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "partner"
        # local-admin, the actor, is untouched and still in control.
        assert client.get("/admin/session").json()["role"] == "super_admin"


def test_unknown_user_is_404(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        assert client.post("/admin/users/nope/suspend").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_user_management.py -q`
Expected: FAIL — 404 on `/admin/users`

- [ ] **Step 3: Write the request models**

Add near the other pydantic request models in `backend/app/main.py`:

```python
class UserAccessRequest(BaseModel):
    role: str
    scopes: list[str] = []
    airports: list[str] | None = None
```

- [ ] **Step 4: Write the routes**

Add to `backend/app/main.py`:

```python
def _user_record(row: dict) -> dict:
    """Explicit field list, not {**row}. The row carries google_sub and
    decided_by, which the admin UI has no use for."""
    user = admin_users.from_row(row)
    grant = user.grant
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "picture": row["picture"],
        "role": row["role"],
        "status": row["status"],
        "scopes": sorted(grant.scopes),
        "airports": sorted(grant.airports) if grant.airports is not None else None,
        "requested_at": row["requested_at"],
        "decided_at": row["decided_at"],
        "last_login_at": row["last_login_at"],
    }


def _normalize_access(payload: UserAccessRequest) -> tuple[str, str | None, str | None]:
    """Return (role, granted_scopes, granted_airports) for storage.

    admin and super_admin store NULL grants: those roles are unrestricted by
    definition and a stored narrower value would never be enforced.
    """
    if payload.role not in admin_users.ROLES:
        raise HTTPException(status_code=400, detail=f"unknown role: {payload.role}")
    if payload.role != "partner":
        return payload.role, None, None
    try:
        scopes = api_keys.serialize_scopes(payload.scopes) if payload.scopes else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload.role, scopes, api_keys.serialize_airports(payload.airports)


def _load_target(conn, user_id: str, actor: admin_users.AdminUser, settings: Settings):
    row = db.get_admin_user(conn, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="user not found")
    target = admin_users.from_row(row)
    try:
        admin_users.guard_modification(actor, target, superuser_emails(settings))
    except admin_users.GuardViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return row, target


@app.get("/admin/users")
async def admin_list_users(
    _: Annotated[admin_users.AdminUser, Depends(require_super_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
    status: str | None = None,
):
    if status and status not in admin_users.STATUSES:
        raise HTTPException(status_code=400, detail=f"unknown status: {status}")
    with db_session(settings.database_path) as conn:
        rows = db.list_admin_users(conn, status=status)
    return {"users": [_user_record(row) for row in rows]}


@app.post("/admin/users/{user_id}/approve")
async def admin_approve_user(
    user_id: str,
    payload: UserAccessRequest,
    actor: Annotated[admin_users.AdminUser, Depends(require_super_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    role, scopes, airports = _normalize_access(payload)
    with db_session(settings.database_path) as conn:
        _load_target(conn, user_id, actor, settings)
        db.set_admin_user_access(
            conn, user_id, role=role, status="approved", granted_scopes=scopes,
            granted_airports=airports, decided_by=actor.id, now=int(time.time()),
        )
        row = db.get_admin_user(conn, user_id)
    return {"user": _user_record(row)}


@app.post("/admin/users/{user_id}/reject")
async def admin_reject_user(
    user_id: str,
    actor: Annotated[admin_users.AdminUser, Depends(require_super_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        row, _target = _load_target(conn, user_id, actor, settings)
        db.set_admin_user_access(
            conn, user_id, role=row["role"], status="rejected", granted_scopes=None,
            granted_airports=None, decided_by=actor.id, now=now,
        )
        revoked = db.revoke_keys_for_owner(conn, user_id, now)
        row = db.get_admin_user(conn, user_id)
    return {"user": _user_record(row), "revoked_keys": revoked}


@app.post("/admin/users/{user_id}/suspend")
async def admin_suspend_user(
    user_id: str,
    actor: Annotated[admin_users.AdminUser, Depends(require_super_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    """Suspension revokes their keys in the same transaction.

    Suspending someone while their keys keep working is the obvious foot-gun;
    the cascade removes it.
    """
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        row, _target = _load_target(conn, user_id, actor, settings)
        db.set_admin_user_access(
            conn, user_id, role=row["role"], status="suspended",
            granted_scopes=row["granted_scopes"], granted_airports=row["granted_airports"],
            decided_by=actor.id, now=now,
        )
        revoked = db.revoke_keys_for_owner(conn, user_id, now)
        row = db.get_admin_user(conn, user_id)
    return {"user": _user_record(row), "revoked_keys": revoked}


@app.patch("/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    payload: UserAccessRequest,
    actor: Annotated[admin_users.AdminUser, Depends(require_super_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    """Edit role and grant. Narrowing revokes the keys that no longer fit.

    Already-minted keys carry their own scopes, so widening changes nothing
    retroactively and narrowing must be enforced explicitly.
    """
    role, scopes, airports = _normalize_access(payload)
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        row, _target = _load_target(conn, user_id, actor, settings)
        db.set_admin_user_access(
            conn, user_id, role=role, status=row["status"], granted_scopes=scopes,
            granted_airports=airports, decided_by=actor.id, now=now,
        )
        updated = db.get_admin_user(conn, user_id)
        revoked = db.revoke_keys_outside_grant(
            conn, user_id, admin_users.from_row(updated).grant, now
        )
    return {"user": _user_record(updated), "revoked_keys": revoked}
```

> Reject also revokes keys: a rejected user with live keys would be a contradiction, and a pending user has none, so the call is a no-op in the normal path.

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_user_management.py -q`
Expected: PASS, 12 passed

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_admin_user_management.py
git commit -m "feat(admin): user approval, rejection, suspension, and grant edits"
```

---

### Task 7: User-scoped API keys

**Files:**
- Modify: `backend/app/main.py` — `_api_key_record` (~line 750), and the three `/admin/api-keys` routes (~772-822)
- Test: `backend/tests/test_admin_api_keys_scoped.py` (create)

**Interfaces:**
- Consumes: `require_user`, `api_keys.enforce_grant`, `db.list_api_keys(owner_user_id=...)`
- Produces: key records now carry `owner_user_id` and `owned` (bool, "is this the caller's key")

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_api_keys_scoped.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

PASSWORD = "pw"


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("ADMIN_SUPERUSERS", raising=False)
    get_settings.cache_clear()


def login(client):
    assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200


def become_partner(scopes="ops:read", airports="KLMO"):
    """Demote the logged-in break-glass row so the same cookie is now a partner."""
    with db_session(get_settings().database_path) as conn:
        db.set_admin_user_access(
            conn, "local-admin", role="partner", status="approved",
            granted_scopes=scopes, granted_airports=airports, decided_by=None, now=1,
        )


def test_a_partner_may_mint_inside_their_grant(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "mine", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 200
        assert resp.json()["record"]["owner_user_id"] == "local-admin"


def test_a_partner_cannot_mint_a_scope_they_were_not_granted(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "wider", "scopes": ["ops:read", "ledger:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 400
        assert "ledger:read" in resp.json()["detail"]


def test_a_partner_cannot_mint_an_all_airports_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "everywhere", "scopes": ["ops:read"], "airports": None,
        })
        assert resp.status_code == 400


def test_a_partner_cannot_mint_for_another_airport(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "elsewhere", "scopes": ["ops:read"], "airports": ["KBJC"],
        })
        assert resp.status_code == 400
        assert "KBJC" in resp.json()["detail"]


def test_a_partner_sees_only_their_own_keys(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="bbbbbbbbbbbbbbbb", secret_hash="0" * 64, name="theirs",
                scopes="ops:read", airports="KLMO", created_at=1, created_by="x",
                owner_user_id="someone-else",
            )
            db.create_api_key(
                conn, key_id="cccccccccccccccc", secret_hash="0" * 64, name="legacy",
                scopes="ops:read", airports=None, created_at=1, created_by="x",
                owner_user_id=None,
            )
        become_partner()
        client.post("/admin/api-keys", json={
            "name": "mine", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        names = [k["name"] for k in client.get("/admin/api-keys").json()["keys"]]
        assert names == ["mine"]


def test_a_super_admin_sees_every_key_including_legacy_unowned(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="cccccccccccccccc", secret_hash="0" * 64, name="legacy",
                scopes="ops:read", airports=None, created_at=1, created_by="x",
                owner_user_id=None,
            )
        keys = client.get("/admin/api-keys").json()["keys"]
        legacy = next(k for k in keys if k["name"] == "legacy")
        assert legacy["owner_user_id"] is None
        assert legacy["owned"] is False


def test_a_partner_revoking_someone_elses_key_gets_404_not_403(tmp_path, monkeypatch):
    # 403 would confirm the id exists. 404 makes owned and non-existent keys
    # indistinguishable, so ids are not enumerable.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="bbbbbbbbbbbbbbbb", secret_hash="0" * 64, name="theirs",
                scopes="ops:read", airports="KLMO", created_at=1, created_by="x",
                owner_user_id="someone-else",
            )
        become_partner()
        assert client.post("/admin/api-keys/bbbbbbbbbbbbbbbb/revoke").status_code == 404
        assert client.post("/admin/api-keys/aaaaaaaaaaaaaaaa/revoke").status_code == 404
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is None


def test_a_partner_can_revoke_their_own_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        created = client.post("/admin/api-keys", json={
            "name": "mine", "scopes": ["ops:read"], "airports": ["KLMO"],
        }).json()
        key_id = created["record"]["id"]
        assert client.post(f"/admin/api-keys/{key_id}/revoke").json()["revoked"] is True


def test_a_super_admin_can_revoke_a_legacy_unowned_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="cccccccccccccccc", secret_hash="0" * 64, name="legacy",
                scopes="ops:read", airports=None, created_at=1, created_by="x",
                owner_user_id=None,
            )
        assert client.post("/admin/api-keys/cccccccccccccccc/revoke").json()["revoked"] is True


def test_a_pending_user_cannot_touch_keys_at_all(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.set_admin_user_access(
                conn, "local-admin", role="partner", status="pending",
                granted_scopes="ops:read", granted_airports="KLMO",
                decided_by=None, now=1,
            )
        assert client.get("/admin/api-keys").status_code == 403
        assert client.post("/admin/api-keys", json={
            "name": "x", "scopes": ["ops:read"], "airports": ["KLMO"],
        }).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_api_keys_scoped.py -q`
Expected: FAIL — `KeyError: 'owner_user_id'` in the response record

- [ ] **Step 3: Rewrite the key routes**

In `backend/app/main.py`, extend `_api_key_record` with two fields (keep the existing explicit whitelist — it is the only thing keeping `secret_hash` out of responses):

```python
def _api_key_record(row: dict, settings: Settings, viewer_id: str | None = None) -> dict:
    return {
        "id": row["id"],
        "prefix": api_keys.display_prefix(row["id"], settings.environment),
        "name": row["name"],
        "scopes": sorted(api_keys.parse_scopes(row["scopes"])),
        "airports": (
            sorted(api_keys.parse_airports(row["airports"]))
            if row["airports"] else None
        ),
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "owner_user_id": row["owner_user_id"],
        "owned": viewer_id is not None and row["owner_user_id"] == viewer_id,
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
    }
```

> Keep whatever the existing first lines of `_api_key_record` are (the `prefix` line above is illustrative — read the current function and add only the two new fields plus the `viewer_id` parameter).

Replace the three routes:

```python
@app.get("/admin/api-keys")
async def admin_list_api_keys(
    user: Annotated[admin_users.AdminUser, Depends(require_user)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    """Admins see every key including legacy unowned ones; partners see theirs."""
    with db_session(settings.database_path) as conn:
        rows = db.list_api_keys(conn) if user.is_admin else db.list_api_keys(conn, owner_user_id=user.id)
    return {"keys": [_api_key_record(row, settings, viewer_id=user.id) for row in rows]}


@app.post("/admin/api-keys")
async def admin_create_api_key(
    payload: ApiKeyCreateRequest,
    user: Annotated[admin_users.AdminUser, Depends(require_user)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    """Returns the full key exactly once. It is unrecoverable afterwards."""
    try:
        api_keys.enforce_grant(payload.scopes, payload.airports, user.grant)
        scopes = api_keys.serialize_scopes(payload.scopes)
    except ValueError as exc:
        # GrantViolation subclasses ValueError, so both the unknown-scope and
        # the exceeds-grant cases land here as a 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    airports = api_keys.serialize_airports(payload.airports)
    full_key, key_id, secret_hash = api_keys.generate_key(settings.environment)
    now = int(time.time())

    with db_session(settings.database_path) as conn:
        db.create_api_key(
            conn,
            key_id=key_id,
            secret_hash=secret_hash,
            name=payload.name.strip(),
            scopes=scopes,
            airports=airports,
            created_at=now,
            created_by=user.email,
            owner_user_id=user.id,
        )
        row = db.get_api_key(conn, key_id)

    record = _api_key_record(row, settings, viewer_id=user.id)
    return {"key": full_key, "record": record}


@app.post("/admin/api-keys/{key_id}/revoke")
async def admin_revoke_api_key(
    key_id: str,
    user: Annotated[admin_users.AdminUser, Depends(require_user)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        row = db.get_api_key(conn, key_id)
        # 404 for both "does not exist" and "not yours": a 403 would confirm
        # the id is real, making key ids enumerable.
        if not row or (not user.is_admin and row["owner_user_id"] != user.id):
            raise HTTPException(status_code=404, detail="key not found")
        changed = db.revoke_api_key(conn, key_id, int(time.time()))
    return {"ok": True, "revoked": changed}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_api_keys_scoped.py tests/test_admin_api_keys.py -q`
Expected: PASS

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_admin_api_keys_scoped.py
git commit -m "feat(admin): partners mint and manage only their own keys"
```

---

### Task 8: Authorization matrix

The reviewer gate for the whole feature. Authorization work fails by leaving one route on the wrong dependency, and only an exhaustive table catches that.

**Files:**
- Test: `backend/tests/test_admin_authorization_matrix.py` (create)

**Interfaces:**
- Consumes: every route added in Tasks 4-7

- [ ] **Step 1: Write the test**

Create `backend/tests/test_admin_authorization_matrix.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

PASSWORD = "pw"

# (method, path, json body or None)
SUPER_ADMIN_ONLY = [
    ("GET", "/admin/users", None),
    ("POST", "/admin/users/target-user/approve", {"role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"]}),
    ("POST", "/admin/users/target-user/reject", None),
    ("POST", "/admin/users/target-user/suspend", None),
    ("PATCH", "/admin/users/target-user", {"role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"]}),
]

ADMIN_ROUTES = [
    ("GET", "/admin/dashboard", None),
]

APPROVED_USER_ROUTES = [
    ("GET", "/admin/session", None),
    ("GET", "/admin/api-keys", None),
]

PUBLIC_ROUTES = [
    ("GET", "/admin/auth/methods", None),
]


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("ADMIN_SUPERUSERS", raising=False)
    get_settings.cache_clear()


def call(client, method, path, body):
    return client.request(method, path, json=body)


def sign_in_as(client, role: str, status: str):
    """Log in break-glass, then rewrite that row to the role under test.

    The cookie names a row, so changing the row changes who the cookie is —
    which is the property this whole matrix is checking.
    """
    assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    with db_session(get_settings().database_path) as conn:
        db.upsert_admin_user(
            conn, id="target-user", email="t@example.com", google_sub="sub-t",
            name="T", picture=None, role="partner", status="pending", now=1,
        )
        db.set_admin_user_access(
            conn, "local-admin", role=role, status=status,
            granted_scopes="ops:read" if role == "partner" else None,
            granted_airports="KLMO" if role == "partner" else None,
            decided_by=None, now=1,
        )


@pytest.mark.parametrize("method,path,body", SUPER_ADMIN_ONLY + ADMIN_ROUTES + APPROVED_USER_ROUTES)
def test_anonymous_is_401_everywhere(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert call(client, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", PUBLIC_ROUTES)
def test_public_routes_need_no_session(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert call(client, method, path, body).status_code == 200


@pytest.mark.parametrize("status", ["pending", "rejected", "suspended"])
@pytest.mark.parametrize(
    "method,path,body", SUPER_ADMIN_ONLY + ADMIN_ROUTES + APPROVED_USER_ROUTES
)
def test_non_approved_users_are_403_everywhere(tmp_path, monkeypatch, status, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "super_admin", status)
        resp = call(client, method, path, body)
        assert resp.status_code == 403, f"{method} {path} allowed a {status} user"
        assert resp.json()["detail"]["status"] == status


@pytest.mark.parametrize("method,path,body", SUPER_ADMIN_ONLY + ADMIN_ROUTES)
def test_an_approved_partner_is_403_on_admin_routes(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "partner", "approved")
        assert call(client, method, path, body).status_code == 403, f"{method} {path} leaked to a partner"


@pytest.mark.parametrize("method,path,body", APPROVED_USER_ROUTES)
def test_an_approved_partner_reaches_their_own_routes(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "partner", "approved")
        assert call(client, method, path, body).status_code == 200


@pytest.mark.parametrize("method,path,body", SUPER_ADMIN_ONLY)
def test_a_plain_admin_is_403_on_super_admin_routes(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "admin", "approved")
        assert call(client, method, path, body).status_code == 403, f"{method} {path} leaked to an admin"


@pytest.mark.parametrize("method,path,body", SUPER_ADMIN_ONLY + ADMIN_ROUTES + APPROVED_USER_ROUTES)
def test_a_super_admin_reaches_everything(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "super_admin", "approved")
        assert call(client, method, path, body).status_code == 200, f"{method} {path} refused a super-admin"


def test_every_admin_route_is_covered_by_this_matrix(tmp_path, monkeypatch):
    """Fail when a new /admin route is added without a row in the table above.

    Without this, the matrix silently stops being a matrix the first time
    someone adds a route.
    """
    covered = {
        (method, path)
        for method, path, _ in SUPER_ADMIN_ONLY + ADMIN_ROUTES + APPROVED_USER_ROUTES + PUBLIC_ROUTES
    }
    # Routes exercised elsewhere, or with no meaningful role dimension.
    exempt = {
        ("POST", "/admin/login"),
        ("POST", "/admin/logout"),
        ("GET", "/admin/auth/google/start"),
        ("GET", "/admin/auth/google/callback"),
    }
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/admin"):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            probe = path.replace("{user_id}", "target-user").replace("{key_id}", "aaaaaaaaaaaaaaaa")
            if (method, path) in exempt or (method, probe) in exempt:
                continue
            if (method, probe) not in covered and (method, path) not in covered:
                missing.append(f"{method} {path}")
    assert not missing, (
        "these /admin routes have no authorization-matrix coverage: " + ", ".join(sorted(missing))
    )
```

- [ ] **Step 2: Run it**

Run: `cd backend && .venv/bin/python -m pytest tests/test_admin_authorization_matrix.py -q`
Expected: the last test FAILS, listing every `/admin` route not yet in the tables — including the aircraft-registry, live-sources, and pattern routes.

- [ ] **Step 3: Extend the tables until the coverage test passes**

Add each listed route to `ADMIN_ROUTES` (they are all `require_admin` routes) with a body that passes validation. For routes whose happy path needs seeded data, add them to `exempt` **only** with a comment naming the test file that covers them. Do not weaken the coverage test.

Note: `test_a_super_admin_reaches_everything` will fail for routes that 404 or 422 on empty data. For those, assert `resp.status_code not in (401, 403)` instead by moving them to a separate list `ADMIN_ROUTES_NO_HAPPY_PATH` that is included in every test except the 200-asserting one.

- [ ] **Step 4: Run the full suite**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_admin_authorization_matrix.py
git commit -m "test(admin): role x endpoint authorization matrix"
```

---

### Task 9: Docs pipeline

**Files:**
- Create: `scripts/build_openapi_json.py`
- Create: `frontend/src/generated/openapi.json` (generated, committed)
- Modify: `scripts/verify_openapi_doc.py` (add a drift check)
- Modify: `backend/app/public_api.py` (serve the enriched document)
- Test: covered by `scripts/verify_openapi_doc.py` plus one backend test

**Interfaces:**
- Produces: `frontend/src/generated/openapi.json` — the full OpenAPI document as JSON; `GET /v1/openapi.json` serves the same content

- [ ] **Step 1: Write the generator**

Create `scripts/build_openapi_json.py`:

```python
#!/usr/bin/env python
"""Generate the JSON form of the hand-written partner OpenAPI document.

docs/api/openapi.yaml is the single source of truth. The frontend cannot parse
YAML without a new dependency, and the backend should not re-read the file on
every request, so the JSON is generated here and committed.

    backend/.venv/bin/python scripts/build_openapi_json.py
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "api" / "openapi.yaml"
TARGETS = (
    ROOT / "frontend" / "src" / "generated" / "openapi.json",
    ROOT / "backend" / "app" / "generated" / "openapi.json",
)


def build() -> str:
    document = yaml.safe_load(SOURCE.read_text())
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> int:
    payload = build()
    for target in TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload)
        print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it**

Run: `cd /Users/d/Code/FAA_circle_jerk && backend/.venv/bin/python scripts/build_openapi_json.py`
Expected: two "wrote ..." lines

Then confirm neither output is ignored — both must be committed, and `generated/` is a common gitignore entry:

Run: `git check-ignore -v frontend/src/generated/openapi.json backend/app/generated/openapi.json`
Expected: **no output** (nothing ignored). If a rule matches, add negations to `.gitignore`:

```gitignore
!frontend/src/generated/
!backend/app/generated/
```

Also confirm `backend/app/generated/` is copied into the image — if the backend Dockerfile copies specific paths rather than the whole `app/` directory, add it, or `/v1/openapi.json` raises `FileNotFoundError` in production while passing locally.

- [ ] **Step 3: Add the drift check**

Append to the checks in `scripts/verify_openapi_doc.py` (inside its main check function, following the existing failure-reporting style):

```python
    # The committed JSON is what the admin docs panel and /v1/openapi.json
    # serve. If it drifts from the YAML, partners read one contract while the
    # API advertises another.
    from build_openapi_json import TARGETS, build  # noqa: E402

    expected = build()
    for target in TARGETS:
        if not target.exists():
            failures.append(f"{target} is missing; run scripts/build_openapi_json.py")
        elif target.read_text() != expected:
            failures.append(
                f"{target} is stale; run scripts/build_openapi_json.py"
            )
```

Add `sys.path.insert(0, str(Path(__file__).resolve().parent))` near the top of `verify_openapi_doc.py` so the sibling import resolves.

> Match the existing script's variable name for accumulated failures — read it first; it may not be called `failures`.

- [ ] **Step 4: Serve it from the backend**

In `backend/app/public_api.py`, replace whatever currently serves `/v1/openapi.json` (or add the route if absent):

```python
_OPENAPI_DOCUMENT_PATH = Path(__file__).resolve().parent / "generated" / "openapi.json"


@lru_cache(maxsize=1)
def _openapi_document() -> dict:
    """The hand-written partner contract, not FastAPI's generated schema.

    The generated schema omits the error envelope and the auth and pagination
    rules, and it still advertised 422 after validation moved to 400.
    """
    return json.loads(_OPENAPI_DOCUMENT_PATH.read_text())


@router.get("/openapi.json", include_in_schema=False)
async def public_openapi() -> dict:
    return _openapi_document()
```

Add `from functools import lru_cache` and `from pathlib import Path` to the imports if absent. Confirm the router variable name and prefix by reading the top of `public_api.py`.

- [ ] **Step 5: Add the backend test**

Append to `backend/tests/test_public_api.py`:

```python
def test_served_openapi_is_the_partner_document(tmp_path, monkeypatch):
    # The generated FastAPI schema is thinner: no error envelope, and it
    # advertised 422 after validation moved to 400.
    configure(tmp_path, monkeypatch)  # reuse this file's existing helper
    with TestClient(app) as client:
        body = client.get("/v1/openapi.json").json()
        assert body["info"]["title"]
        assert "/v1" not in "".join(body["paths"].keys()), "paths are relative to the server URL"
        assert any("400" in str(op) for op in body["paths"].values())
```

> Adjust the helper name to whatever `test_public_api.py` already uses to configure a client.

- [ ] **Step 6: Run the checks**

Run: `cd /Users/d/Code/FAA_circle_jerk && backend/.venv/bin/python scripts/verify_openapi_doc.py`
Expected: the existing "11 paths, parameters, and $refs all match" plus no drift failures

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/d/Code/FAA_circle_jerk
git add scripts/build_openapi_json.py scripts/verify_openapi_doc.py backend/app/public_api.py backend/app/generated/openapi.json frontend/src/generated/openapi.json backend/tests/test_public_api.py
git commit -m "feat(api): one OpenAPI source of truth for docs panel and /v1"
```

---

### Task 10: Frontend access logic and API client

Role logic goes in `src/lib/` as pure functions. Every existing frontend test is a pure `src/lib/*.test.ts`; there is no component-testing library and this task does not add one.

**Files:**
- Create: `frontend/src/lib/adminAccess.ts`
- Create: `frontend/src/lib/adminAccess.test.ts`
- Modify: `frontend/src/lib/api.ts` (append near the existing admin functions, ~line 732-790)

**Interfaces:**
- Produces: `AdminSession` interface `{id, email, name, username, role, status, scopes, airports}`; `AdminRole`, `AdminStatus` types; `visibleTabs(session) -> TabId[]`; `canManageUsers(session) -> boolean`; `allowedScopes(session) -> string[]`; `airportRequired(session) -> boolean`; `clampScopes(selected, session) -> string[]`; API client functions `adminAuthMethods`, `adminListUsers`, `adminApproveUser`, `adminRejectUser`, `adminSuspendUser`, `adminUpdateUser`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/adminAccess.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  ALL_TABS,
  airportRequired,
  allowedScopes,
  canManageUsers,
  clampScopes,
  visibleTabs,
  type AdminSession
} from "./adminAccess";

function session(overrides: Partial<AdminSession> = {}): AdminSession {
  return {
    id: "u1",
    email: "p@example.com",
    name: "P",
    username: "P",
    role: "partner",
    status: "approved",
    scopes: ["ops:read"],
    airports: ["KLMO"],
    ...overrides
  };
}

describe("visibleTabs", () => {
  it("gives a partner only keys and docs", () => {
    expect(visibleTabs(session())).toEqual(["keys", "docs"]);
  });

  it("gives an admin everything except user management", () => {
    const tabs = visibleTabs(session({ role: "admin" }));
    expect(tabs).toContain("dashboard");
    expect(tabs).not.toContain("users");
  });

  it("gives a super-admin every tab", () => {
    expect(visibleTabs(session({ role: "super_admin" }))).toEqual(ALL_TABS);
  });

  it("gives a non-approved user nothing, whatever their role says", () => {
    for (const status of ["pending", "rejected", "suspended"] as const) {
      expect(visibleTabs(session({ role: "super_admin", status }))).toEqual([]);
    }
  });

  it("gives a signed-out visitor nothing", () => {
    expect(visibleTabs(null)).toEqual([]);
  });
});

describe("canManageUsers", () => {
  it("is true only for an approved super-admin", () => {
    expect(canManageUsers(session({ role: "super_admin" }))).toBe(true);
    expect(canManageUsers(session({ role: "admin" }))).toBe(false);
    expect(canManageUsers(session({ role: "super_admin", status: "suspended" }))).toBe(false);
    expect(canManageUsers(null)).toBe(false);
  });
});

describe("allowedScopes", () => {
  it("returns the granted scopes for a partner", () => {
    expect(allowedScopes(session())).toEqual(["ops:read"]);
  });

  it("returns every scope for an admin", () => {
    expect(allowedScopes(session({ role: "admin", scopes: ["ops:read"] })).length).toBe(4);
  });
});

describe("airportRequired", () => {
  it("is true when the grant names airports", () => {
    expect(airportRequired(session())).toBe(true);
  });

  it("is false when the grant is unrestricted", () => {
    expect(airportRequired(session({ airports: null }))).toBe(false);
  });
});

describe("clampScopes", () => {
  it("drops scopes outside the grant", () => {
    // Presentation only. The server's enforce_grant is the real boundary;
    // this exists so the form cannot submit a request that is certain to 400.
    expect(clampScopes(["ops:read", "ledger:read"], session())).toEqual(["ops:read"]);
  });

  it("preserves order and removes duplicates", () => {
    expect(clampScopes(["ops:read", "ops:read"], session())).toEqual(["ops:read"]);
  });

  it("leaves an admin's selection untouched", () => {
    const picked = ["ops:read", "ledger:read"];
    expect(clampScopes(picked, session({ role: "admin" }))).toEqual(picked);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/adminAccess.test.ts`
Expected: FAIL — cannot resolve `./adminAccess`

- [ ] **Step 3: Write the module**

Create `frontend/src/lib/adminAccess.ts`:

```ts
import { API_KEY_SCOPES } from "./api";

export type AdminRole = "super_admin" | "admin" | "partner";
export type AdminStatus = "pending" | "approved" | "rejected" | "suspended";
export type TabId = "dashboard" | "users" | "keys" | "docs";

export const ALL_TABS: TabId[] = ["dashboard", "users", "keys", "docs"];

export interface AdminSession {
  id: string;
  email: string;
  name: string | null;
  username: string;
  role: AdminRole;
  status: AdminStatus;
  scopes: string[];
  /** null means every airport. */
  airports: string[] | null;
}

function approved(session: AdminSession | null): session is AdminSession {
  return session != null && session.status === "approved";
}

function isAdmin(session: AdminSession | null): boolean {
  return approved(session) && (session.role === "admin" || session.role === "super_admin");
}

export function visibleTabs(session: AdminSession | null): TabId[] {
  if (!approved(session)) return [];
  if (session.role === "super_admin") return ALL_TABS;
  if (session.role === "admin") return ["dashboard", "keys", "docs"];
  return ["keys", "docs"];
}

export function canManageUsers(session: AdminSession | null): boolean {
  return approved(session) && session.role === "super_admin";
}

export function allowedScopes(session: AdminSession | null): string[] {
  if (!approved(session)) return [];
  return isAdmin(session) ? [...API_KEY_SCOPES] : session.scopes;
}

export function airportRequired(session: AdminSession | null): boolean {
  return approved(session) && !isAdmin(session) && session.airports != null;
}

/**
 * Trim a scope selection to what the session may actually request.
 *
 * Presentation only — the server's enforce_grant is the real boundary. This
 * exists so the form cannot submit a request that is certain to be refused.
 */
export function clampScopes(selected: string[], session: AdminSession | null): string[] {
  const permitted = new Set(allowedScopes(session));
  const seen = new Set<string>();
  return selected.filter((scope) => {
    if (!permitted.has(scope) || seen.has(scope)) return false;
    seen.add(scope);
    return true;
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/adminAccess.test.ts`
Expected: PASS, 12 tests

- [ ] **Step 5: Extend the API client**

In `frontend/src/lib/api.ts`, update `adminSession` and add the new functions:

```ts
export interface AdminUserRecord {
  id: string;
  email: string;
  name: string | null;
  picture: string | null;
  role: "super_admin" | "admin" | "partner";
  status: "pending" | "approved" | "rejected" | "suspended";
  scopes: string[];
  airports: string[] | null;
  requested_at: number;
  decided_at: number | null;
  last_login_at: number | null;
}

export interface AdminSessionResponse {
  ok: boolean;
  id: string;
  email: string;
  name: string | null;
  username: string;
  role: "super_admin" | "admin" | "partner";
  status: "pending" | "approved" | "rejected" | "suspended";
  scopes: string[];
  airports: string[] | null;
}

export function adminSession() {
  return adminJson<AdminSessionResponse>("/admin/session");
}

export function adminAuthMethods() {
  return adminJson<{ google: boolean; password: boolean }>("/admin/auth/methods");
}

export function adminListUsers(status?: string) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return adminJson<{ users: AdminUserRecord[] }>(`/admin/users${query}`);
}

export function adminApproveUser(
  id: string,
  role: string,
  scopes: string[],
  airports: string[] | null
) {
  return adminJson<{ user: AdminUserRecord }>(`/admin/users/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ role, scopes, airports })
  });
}

export function adminRejectUser(id: string) {
  return adminJson<{ user: AdminUserRecord; revoked_keys: number }>(
    `/admin/users/${id}/reject`,
    { method: "POST" }
  );
}

export function adminSuspendUser(id: string) {
  return adminJson<{ user: AdminUserRecord; revoked_keys: number }>(
    `/admin/users/${id}/suspend`,
    { method: "POST" }
  );
}

export function adminUpdateUser(
  id: string,
  role: string,
  scopes: string[],
  airports: string[] | null
) {
  return adminJson<{ user: AdminUserRecord; revoked_keys: number }>(`/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ role, scopes, airports })
  });
}
```

Extend the existing `ApiKeyRecord` interface with the two new fields:

```ts
  owner_user_id: string | null;
  owned: boolean;
```

Confirm `adminJson` sets `Content-Type: application/json` and `credentials: "include"` for non-GET calls; if it only does so when `body` is present, the PATCH above is fine.

- [ ] **Step 6: Run the frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS, all files

Run: `cd frontend && npm run build`
Expected: clean, no TS errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/adminAccess.ts frontend/src/lib/adminAccess.test.ts frontend/src/lib/api.ts
git commit -m "feat(admin-ui): role-aware access helpers and user-management client"
```

---

### Task 11: Frontend components and routing

**Files:**
- Create: `frontend/src/components/AdminLogin.tsx`
- Create: `frontend/src/components/PendingUsersPanel.tsx`
- Create: `frontend/src/components/ApiDocsPanel.tsx`
- Create: `frontend/src/lib/useAdminSession.ts`
- Modify: `frontend/src/AdminDashboard.tsx` (extract login, add tabs and status screens)
- Modify: `frontend/src/components/ApiKeysPanel.tsx` (scope clamping, owner column)
- Modify: `frontend/src/App.tsx:106` (add `/developers`)
- Modify: `frontend/src/styles.css` (styles for the new panels)

**Interfaces:**
- Consumes: everything from Task 10
- Produces: no exported logic other than default component exports — all testable logic already lives in `adminAccess.ts`

- [ ] **Step 1: Add the route**

In `frontend/src/App.tsx`, change line 106:

```diff
-  if (window.location.pathname.startsWith("/admin")) return <AdminDashboard />;
+  if (
+    window.location.pathname.startsWith("/admin") ||
+    window.location.pathname.startsWith("/developers")
+  ) {
+    // Same component either way. Which URL was used does not affect what
+    // renders — role does. /developers exists so partners get a URL that
+    // isn't labelled "admin".
+    return <AdminDashboard />;
+  }
```

- [ ] **Step 2: Write the session hook**

Create `frontend/src/lib/useAdminSession.ts`:

```ts
import { useCallback, useEffect, useState } from "react";
import { ApiError, adminSession } from "./api";
import type { AdminSession, AdminStatus } from "./adminAccess";

export interface SessionState {
  session: AdminSession | null;
  /** Set when authenticated but not approved, so the shell can explain why. */
  blockedStatus: AdminStatus | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useAdminSession(): SessionState {
  const [session, setSession] = useState<AdminSession | null>(null);
  const [blockedStatus, setBlockedStatus] = useState<AdminStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const body = await adminSession();
      setSession(body);
      setBlockedStatus(null);
    } catch (error) {
      setSession(null);
      // A 403 carries the caller's own status, which is the difference
      // between "sign in" and "you are waiting for approval".
      const detail =
        error instanceof ApiError && error.status === 403
          ? (error.detail as { status?: AdminStatus } | undefined)
          : undefined;
      setBlockedStatus(detail?.status ?? null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { session, blockedStatus, loading, refresh };
}
```

> Check `ApiError` in `api.ts`: this hook needs `status` and the parsed `detail` on it. If `ApiError` only carries a message, extend it to keep `status: number` and `detail: unknown` — and keep the existing message behavior so no current caller changes.

- [ ] **Step 3: Extract and extend the login screen**

Create `frontend/src/components/AdminLogin.tsx` by moving `LoginPanel` out of `AdminDashboard.tsx` unchanged, then adding the Google button above the password form:

```tsx
import { useEffect, useState, type FormEvent } from "react";
import { LockKeyhole } from "lucide-react";
import { ApiError, adminAuthMethods, adminLogin } from "../lib/api";

export default function AdminLogin({ onAuthed }: { onAuthed: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [methods, setMethods] = useState<{ google: boolean; password: boolean }>({
    google: false,
    password: true
  });

  useEffect(() => {
    adminAuthMethods()
      .then(setMethods)
      // A failed probe must not hide the password form, or a backend hiccup
      // locks the operator out of the only login that still works.
      .catch(() => setMethods({ google: false, password: true }));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    try {
      await adminLogin(username, password);
      onAuthed();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="admin-login-shell">
      <div className="admin-login-card">
        <div className="admin-login-icon"><LockKeyhole size={24} /></div>
        <h1>Sign in</h1>
        {methods.google && (
          <>
            <a className="admin-google-button" href="/api/admin/auth/google/start">
              Continue with Google
            </a>
            {methods.password && <p className="admin-login-divider">or</p>}
          </>
        )}
        {methods.password && (
          <form onSubmit={submit}>
            <label>
              Username
              <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          </form>
        )}
        {status && <p className="admin-login-error">{status}</p>}
      </div>
    </main>
  );
}
```

> `/api/admin/auth/google/start` is a plain `<a href>`, not a fetch: the browser must follow the redirect to Google as a top-level navigation. An XHR would be blocked by CORS and would not set the cookie. Verify the `/api` prefix matches what `adminJson` prepends — reuse the same base if `api.ts` exports one.

Delete `LoginPanel` from `AdminDashboard.tsx` and import `AdminLogin` instead.

- [ ] **Step 4: Write the pending-users panel**

Create `frontend/src/components/PendingUsersPanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { ShieldCheck, UserCheck, UserX, Ban } from "lucide-react";
import {
  API_KEY_SCOPES,
  ApiError,
  adminApproveUser,
  adminListUsers,
  adminRejectUser,
  adminSuspendUser,
  adminUpdateUser,
  type AdminUserRecord
} from "../lib/api";
import { formatDateTime } from "../lib/format";

type Draft = { role: string; scopes: string[]; airports: string };

const EMPTY_DRAFT: Draft = { role: "partner", scopes: ["aggregates:read"], airports: "" };

export default function PendingUsersPanel() {
  const [users, setUsers] = useState<AdminUserRecord[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [status, setStatus] = useState("");
  // Distinct from an empty list: a failed load must not render as "no pending
  // requests", which a super-admin can read as truth and leave someone waiting.
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    try {
      setUsers((await adminListUsers()).users);
      setLoadFailed(false);
    } catch (error) {
      setLoadFailed(true);
      setStatus(error instanceof ApiError ? error.message : "Could not load users");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function draftFor(user: AdminUserRecord): Draft {
    return (
      drafts[user.id] ?? {
        role: user.role,
        scopes: user.scopes.length ? user.scopes : EMPTY_DRAFT.scopes,
        airports: (user.airports ?? []).join(", ")
      }
    );
  }

  function setDraft(id: string, patch: Partial<Draft>) {
    setDrafts((prev) => ({ ...prev, [id]: { ...(prev[id] ?? EMPTY_DRAFT), ...patch } }));
  }

  function airportList(raw: string): string[] | null {
    const parts = raw.split(",").map((p) => p.trim().toUpperCase()).filter(Boolean);
    return parts.length ? parts : null;
  }

  async function run(id: string, action: () => Promise<{ revoked_keys?: number }>) {
    setBusy(id);
    setStatus("");
    try {
      const result = await action();
      if (result.revoked_keys) {
        setStatus(`Done. ${result.revoked_keys} key(s) revoked.`);
      }
      await load();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Action failed");
    } finally {
      setBusy(null);
    }
  }

  const pending = users.filter((u) => u.status === "pending");
  const decided = users.filter((u) => u.status !== "pending");

  return (
    <section className="admin-panel">
      <h2><ShieldCheck size={18} /> Access requests</h2>
      {status && <p className="admin-panel-status">{status}</p>}
      {loadFailed && <p className="admin-panel-error">Could not load the user list.</p>}

      <h3>Pending {pending.length > 0 && <span className="admin-badge">{pending.length}</span>}</h3>
      {!loadFailed && pending.length === 0 && <p>No pending requests.</p>}
      {pending.map((user) => {
        const draft = draftFor(user);
        return (
          <div key={user.id} className="admin-user-row">
            <div className="admin-user-identity">
              <strong>{user.name ?? user.email}</strong>
              <span>{user.email}</span>
              <span>requested {formatDateTime(user.requested_at)}</span>
            </div>
            <label>
              Role
              <select value={draft.role} onChange={(e) => setDraft(user.id, { role: e.target.value })}>
                <option value="partner">Partner — keys and docs only</option>
                <option value="admin">Admin — full dashboard</option>
                <option value="super_admin">Super-admin — full dashboard and user management</option>
              </select>
            </label>
            {draft.role === "partner" && (
              <>
                <fieldset>
                  <legend>Scopes they may request</legend>
                  {API_KEY_SCOPES.map((scope) => (
                    <label key={scope}>
                      <input
                        type="checkbox"
                        checked={draft.scopes.includes(scope)}
                        onChange={(e) =>
                          setDraft(user.id, {
                            scopes: e.target.checked
                              ? [...draft.scopes, scope]
                              : draft.scopes.filter((s) => s !== scope)
                          })
                        }
                      />
                      {scope}
                    </label>
                  ))}
                </fieldset>
                <label>
                  Airports (comma separated, blank means every airport)
                  <input
                    value={draft.airports}
                    onChange={(e) => setDraft(user.id, { airports: e.target.value })}
                    placeholder="KLMO"
                  />
                </label>
              </>
            )}
            <div className="admin-user-actions">
              <button
                disabled={busy === user.id}
                onClick={() =>
                  run(user.id, () =>
                    adminApproveUser(user.id, draft.role, draft.scopes, airportList(draft.airports))
                  )
                }
              >
                <UserCheck size={14} /> Approve
              </button>
              <button
                className="admin-danger"
                disabled={busy === user.id}
                onClick={() => run(user.id, () => adminRejectUser(user.id))}
              >
                <UserX size={14} /> Reject
              </button>
            </div>
          </div>
        );
      })}

      <h3>Decided</h3>
      {!loadFailed && decided.length === 0 && <p>Nobody has been approved yet.</p>}
      <table className="admin-table">
        <thead>
          <tr>
            <th>User</th><th>Role</th><th>Status</th><th>Scopes</th><th>Airports</th><th>Last seen</th><th></th>
          </tr>
        </thead>
        <tbody>
          {decided.map((user) => (
            <tr key={user.id}>
              <td>{user.email}</td>
              <td>{user.role}</td>
              <td>{user.status}</td>
              <td>{user.scopes.join(", ") || "—"}</td>
              <td>{user.airports?.join(", ") ?? "all"}</td>
              <td>{user.last_login_at ? formatDateTime(user.last_login_at) : "—"}</td>
              <td>
                {user.status === "approved" && (
                  <button
                    className="admin-danger"
                    disabled={busy === user.id}
                    title="Suspends access and revokes every key they hold"
                    onClick={() => run(user.id, () => adminSuspendUser(user.id))}
                  >
                    <Ban size={14} /> Suspend
                  </button>
                )}
                {user.status === "suspended" && (
                  <button
                    disabled={busy === user.id}
                    onClick={() =>
                      run(user.id, () =>
                        adminApproveUser(user.id, user.role, user.scopes, user.airports)
                      )
                    }
                  >
                    Reinstate
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

`adminUpdateUser` is imported for the grant-edit control; wire it to a "Save grant" button in the decided table using the same `draftFor`/`setDraft` pattern as the pending rows, and surface the returned `revoked_keys` count in `status` so a narrowing that kills live keys is never silent.

- [ ] **Step 5: Write the docs panel**

Create `frontend/src/components/ApiDocsPanel.tsx`:

```tsx
import { useState } from "react";
import { BookOpen, Copy } from "lucide-react";
import spec from "../generated/openapi.json";

interface Operation {
  summary?: string;
  description?: string;
  security?: Array<Record<string, string[]>>;
  parameters?: Array<{ name: string; in: string; required?: boolean; description?: string }>;
}

const METHODS = ["get", "post", "put", "patch", "delete"] as const;

function curlFor(path: string): string {
  const base = (spec as any).servers?.[0]?.url ?? "https://circlejerks.live/api/v1";
  return `curl -H "X-Api-Key: $CIRCLEJERK_API_KEY" \\\n  "${base}${path}"`;
}

export default function ApiDocsPanel() {
  const [copied, setCopied] = useState<string | null>(null);
  const paths = (spec as any).paths as Record<string, Record<string, Operation>>;

  async function copy(value: string, id: string) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(value);
      setCopied(id);
    } catch {
      setCopied(null);
    }
  }

  return (
    <section className="admin-panel admin-docs">
      <h2><BookOpen size={18} /> API documentation</h2>
      <p>
        Authenticate with the <code>X-Api-Key</code> header. A key only reaches the scopes and
        airports it was issued for.
      </p>
      {Object.entries(paths).map(([path, operations]) => (
        <article key={path} className="admin-docs-endpoint">
          <h3><code>{path}</code></h3>
          {METHODS.filter((m) => operations[m]).map((method) => {
            const operation = operations[method];
            const scopes = operation.security?.flatMap((s) => Object.values(s).flat()) ?? [];
            return (
              <div key={method}>
                <p>
                  <span className="admin-docs-method">{method.toUpperCase()}</span>
                  {operation.summary}
                </p>
                {scopes.length > 0 && (
                  <p className="admin-docs-scopes">
                    Requires: {scopes.map((s) => <code key={s}>{s}</code>)}
                  </p>
                )}
                {operation.description && <p>{operation.description}</p>}
                {operation.parameters && operation.parameters.length > 0 && (
                  <ul className="admin-docs-params">
                    {operation.parameters.map((p) => (
                      <li key={`${p.in}:${p.name}`}>
                        <code>{p.name}</code> <em>({p.in}{p.required ? ", required" : ""})</em>
                        {p.description ? ` — ${p.description}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
          <pre className="admin-docs-curl">
            <code>{curlFor(path)}</code>
            <button onClick={() => copy(curlFor(path), path)}>
              <Copy size={14} /> {copied === path ? "Copied" : "Copy"}
            </button>
          </pre>
        </article>
      ))}
    </section>
  );
}
```

Add to `frontend/tsconfig.json` under `compilerOptions` if absent: `"resolveJsonModule": true`.

- [ ] **Step 6: Rework the dashboard shell**

In `frontend/src/AdminDashboard.tsx`: replace the local session state with `useAdminSession`, render `AdminLogin` when there is no session and no blocked status, render a status screen when `blockedStatus` is set, and render tabs from `visibleTabs(session)`:

```tsx
const { session, blockedStatus, loading, refresh } = useAdminSession();
const tabs = visibleTabs(session);
const [tab, setTab] = useState<TabId>("keys");

useEffect(() => {
  // Keep the selected tab reachable: a partner must never be left staring at
  // a dashboard tab that no longer exists for them.
  if (tabs.length > 0 && !tabs.includes(tab)) setTab(tabs[0]);
}, [tabs, tab]);

if (loading) return <main className="admin-shell"><p>Loading…</p></main>;

if (blockedStatus) {
  const copy =
    blockedStatus === "pending"
      ? { title: "Access requested", body: "Your request is waiting for an administrator to review it." }
      : { title: "Access denied", body: "This account does not have access to the developer area." };
  return (
    <main className="admin-login-shell">
      <div className="admin-login-card">
        <h1>{copy.title}</h1>
        <p>{copy.body}</p>
        <button onClick={async () => { await adminLogout(); await refresh(); }}>Sign out</button>
      </div>
    </main>
  );
}

if (!session) return <AdminLogin onAuthed={refresh} />;
```

Render the tab bar from `tabs` and the panel bodies: `dashboard` keeps the existing dashboard markup, `users` renders `<PendingUsersPanel />`, `keys` renders `<ApiKeysPanel session={session} />`, `docs` renders `<ApiDocsPanel />`.

- [ ] **Step 7: Clamp the key form**

In `frontend/src/components/ApiKeysPanel.tsx`, accept `session` as a prop and use it:

- Render only `allowedScopes(session)` as checkboxes instead of all `API_KEY_SCOPES`.
- Pass the selection through `clampScopes(scopes, session)` before submitting.
- When `airportRequired(session)`, make the airports field required and pre-fill it with `session.airports?.join(", ")`, with helper text naming the airports they may use.
- Add an "Owner" column showing `created_by`, with `owned` rows marked, and label `owner_user_id === null` rows as "legacy (unowned)".

- [ ] **Step 8: Add the styles**

Add classes used above to `frontend/src/styles.css`, following the existing admin styling: `.admin-google-button`, `.admin-login-divider`, `.admin-badge`, `.admin-user-row`, `.admin-user-identity`, `.admin-user-actions`, `.admin-danger`, `.admin-docs`, `.admin-docs-endpoint`, `.admin-docs-method`, `.admin-docs-scopes`, `.admin-docs-params`, `.admin-docs-curl`, `.admin-panel-status`, `.admin-panel-error`.

- [ ] **Step 9: Verify**

Run: `cd frontend && npx vitest run`
Expected: PASS

Run: `cd frontend && npm run build`
Expected: clean, no TS errors

- [ ] **Step 10: Commit**

```bash
git add frontend/src
git commit -m "feat(admin-ui): google sign-in, approval queue, scoped keys, docs panel"
```

---

### Task 12: Configuration and deployment documentation

**Files:**
- Modify: `.env.example` (create if absent)
- Modify: `DEPLOY.md`
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: Document the variables**

Add to `.env.example`:

```bash
# Admin access -------------------------------------------------------------
# Emails that are always super-admin, forced on every login. Survives a
# database wipe and cannot be demoted through the UI. Comma separated.
ADMIN_SUPERUSERS=you@example.com

# Google OAuth client (type: Web application) from Google Cloud Console.
# The redirect URI is stated explicitly, never inferred from request headers.
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=https://circlejerks.live/api/admin/auth/google/callback
```

- [ ] **Step 2: Document the deploy order**

Add to `DEPLOY.md`, next to the existing Caddy-first constraint:

```markdown
### Google SSO deploy order

Environment variables must land **before** the app restarts. If the app comes
up without `GOOGLE_OAUTH_CLIENT_ID`, `/admin/auth/methods` reports Google as
unavailable and the login screen renders with no Google button — a silent
failure that looks like a frontend bug.

One-time setup in Google Cloud Console:

1. Create an OAuth client, type **Web application**.
2. Authorized redirect URI: `https://circlejerks.live/api/admin/auth/google/callback`
   (Caddy strips `/api` before the app sees it).
3. Configure the consent screen and either publish it or add each intended
   user as a test user.

The first sign-in for an address in `ADMIN_SUPERUSERS` is approved as
super-admin automatically. Everyone else lands in the pending queue at
`/admin` → Access requests.
```

- [ ] **Step 3: Verify the Caddy probe filter still lets these paths through**

Run: `cd /Users/d/Code/FAA_circle_jerk && grep -n "blocked_probe" Caddyfile`

Confirm the regex matches `adminer` and `phpmyadmin` but not bare `admin`. If bare `admin` has been added since, `/admin/auth/google/callback` will be blocked at the edge and Google sign-in will fail with an edge error the app never sees.

- [ ] **Step 4: Record the state**

Append to `.superpowers/sdd/progress.md` a section for this feature: the branch, the deploy-order constraint, and the fact that `samesite` moved from `strict` to `lax` with the reason.

- [ ] **Step 5: Full verification**

```bash
cd /Users/d/Code/FAA_circle_jerk
backend/.venv/bin/python -m pytest backend/tests -q
backend/.venv/bin/python scripts/verify_openapi_doc.py
cd frontend && npx vitest run && npm run build
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add .env.example DEPLOY.md .superpowers/sdd/progress.md
git commit -m "docs: google SSO configuration and deploy order"
```

---

## Verification summary

| Check | Command |
|---|---|
| Backend | `cd backend && .venv/bin/python -m pytest -q` |
| OpenAPI drift | `backend/.venv/bin/python scripts/verify_openapi_doc.py` |
| Frontend tests | `cd frontend && npx vitest run` |
| Frontend build | `cd frontend && npm run build` |

## Known gaps, deliberately left

- No email notification on a new access request; the pending badge is the only signal (spec non-goal).
- Rate limiting on `/admin/auth/google/*` uses whatever global limiter the app already applies. If none reaches these routes, the pending queue is spammable — worth a follow-up, not a blocker, since pending users have zero capability.
- The three pre-existing items from the `/v1` work are untouched: aggregate endpoints splatting internal shapes, FastAPI's own `/docs` live in production, and `/runways` leaking an internal `icao` field.
