"""API key primitives: generation, parsing, verification, and the scope model.

Deliberately free of FastAPI, sqlite3, and any I/O so the security-critical
half of the auth path can be tested in isolation. Persistence lives in db.py
and the HTTP plumbing lives in public_api.py.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

KEY_PREFIX = "cj"
ID_LENGTH = 16  # hex characters
SECRET_BYTES = 32

SCOPES: tuple[str, ...] = ("ops:read", "tracks:read", "aggregates:read", "ledger:read")

_HEX_DIGITS = frozenset("0123456789abcdef")


def _env_segment(environment: str) -> str:
    return "live" if environment == "production" else environment


def hash_secret(secret: str) -> str:
    """SHA-256, not a password KDF.

    The secret is 256 bits of CSPRNG output, not a human-chosen password, so
    there is no meaningful search space to slow down. Argon2/bcrypt here would
    only add latency to the hot path of every authenticated request.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_key(environment: str) -> tuple[str, str, str]:
    """Mint a key. Returns (full_key, key_id, secret_hash).

    The caller shows `full_key` to the admin exactly once and persists only
    `key_id` and `secret_hash`. The secret half is unrecoverable afterwards.
    """
    key_id = secrets.token_hex(ID_LENGTH // 2)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    full = f"{KEY_PREFIX}_{_env_segment(environment)}_{key_id}_{secret}"
    return full, key_id, hash_secret(secret)


def parse_key(raw: str) -> tuple[str, str] | None:
    """Split a presented key into (key_id, secret), or None when malformed.

    maxsplit=3 matters: the base64url secret contains "_", so a plain split
    would truncate it.
    """
    if not raw:
        return None
    parts = raw.strip().split("_", 3)
    if len(parts) != 4:
        return None
    prefix, _env, key_id, secret = parts
    if prefix != KEY_PREFIX or not secret:
        return None
    if len(key_id) != ID_LENGTH or not set(key_id) <= _HEX_DIGITS:
        return None
    return key_id, secret


def verify_secret(secret: str, secret_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(secret), secret_hash)


def display_prefix(key_id: str, environment: str) -> str:
    """The non-secret half, safe to show in the admin list and in logs."""
    return f"{KEY_PREFIX}_{_env_segment(environment)}_{key_id}"


def validate_scopes(scopes: list[str]) -> list[str]:
    """Deduplicate and order-normalize, rejecting unknown or empty scope sets."""
    cleaned = {scope.strip() for scope in scopes if scope.strip()}
    unknown = cleaned - set(SCOPES)
    if unknown:
        raise ValueError(f"unknown scopes: {', '.join(sorted(unknown))}")
    if not cleaned:
        raise ValueError("at least one scope is required")
    return [scope for scope in SCOPES if scope in cleaned]


def serialize_scopes(scopes: list[str]) -> str:
    return ",".join(validate_scopes(scopes))


def parse_scopes(raw: str) -> frozenset[str]:
    return frozenset(scope.strip() for scope in raw.split(",") if scope.strip())


def serialize_airports(airports: list[str] | None) -> str | None:
    """None or an empty list both mean "all airports"."""
    if not airports:
        return None
    cleaned = sorted({icao.strip().upper() for icao in airports if icao.strip()})
    return ",".join(cleaned) or None


def parse_airports(raw: str | None) -> frozenset[str] | None:
    if raw is None or not raw.strip():
        return None
    return frozenset(icao.strip().upper() for icao in raw.split(",") if icao.strip())


@dataclass(frozen=True)
class ApiKeyContext:
    key_id: str
    name: str
    scopes: frozenset[str]
    airports: frozenset[str] | None  # None means every airport

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def allows_airport(self, icao: str) -> bool:
        return self.airports is None or icao.upper() in self.airports
