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
