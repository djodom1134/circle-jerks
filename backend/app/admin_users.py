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
# .invalid is an RFC 2606 reserved TLD: no registry will ever delegate it, so
# Google can never issue a verified id_token for this address. That is what
# keeps a Google-authenticated caller from ever rebinding the break-glass
# super_admin row — do not "fix" this back to a real-looking domain.
LOCAL_ADMIN_EMAIL = "local-admin@invalid"


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
