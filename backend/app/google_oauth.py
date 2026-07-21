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
