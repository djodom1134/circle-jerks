# Public API with Scoped API Keys — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only `/v1` HTTP API over circlejerks' operations, raw tracks, aggregates, and KLMO ledger, authenticated by keys the admin mints from the dashboard with adjustable scopes and an optional airport restriction.

**Architecture:** A versioned `APIRouter` mounted inside the existing backend FastAPI app, with response contracts deliberately separate from the ~50 internal frontend routes. Key primitives live in a pure `api_keys.py` with no FastAPI import; persistence goes in `db.py` alongside the existing tables; rate limiting uses a new `incr_counter` on the existing `Store` abstraction. Ledger data is proxied over HTTP to the `ledger-api` service rather than opening its SQLite file, preserving the deliberate write-lock separation.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2 / pydantic-settings, SQLite (`sqlite3` stdlib, WAL), httpx, Redis/Valkey via `redis.asyncio`, pytest + `fastapi.testclient`. Frontend: React 18 + TypeScript, Vite, lucide-react icons, plain CSS in `frontend/src/styles.css`.

**Spec:** `docs/superpowers/specs/2026-07-20-public-api-keys-design.md`

## Global Constraints

- **Read-only.** No endpoint in the `/v1` namespace mutates state. The only writes are `api_keys` row creation/revocation (admin routes) and the throttled `last_used_at` touch.
- **Route prefix.** Caddy (`Caddyfile:39`) and Vite (`frontend/vite.config.ts:12`) both **strip** the `/api` prefix. Declare routes as `/v1/...`; they are reachable in the browser at `/api/v1/...`.
- **Key format:** `cj_<env>_<id16>_<secret43>` where `env` is `live` in production and `settings.environment` otherwise, `id16` is 16 hex characters, and the secret is `secrets.token_urlsafe(32)`. Parse with `split("_", 3)` — the base64url secret itself contains `_`.
- **Scopes:** exactly `ops:read`, `tracks:read`, `aggregates:read`, `ledger:read`. No others.
- **Fixed guardrails, identical for every key:** page `limit` default `500`, hard max `5000`; `/v1/tracks` span cap `31 * 86400` seconds; rate limit `120` requests per minute per key.
- **Error envelope:** `/v1` responses use `{"error": {"code": ..., "message": ...}}`. Internal routes keep FastAPI's `{"detail": ...}` — do not change them.
- **Secret hashing is SHA-256, not bcrypt/argon2.** The secret is 256 bits of CSPRNG output, not a human password; a KDF would only add latency to every request.
- **Never leak which half failed.** Unknown key id and wrong secret both return `unauthorized` with the identical message.
- **No circular imports.** `public_api.py` must not import from `main.py`. It reads settings and store off `request.app.state` via its own local dependencies.
- **Existing test idiom:** every backend test sets `CIRCLEJERK_DATABASE_PATH`, `CIRCLEJERK_REDIS_URL=memory://`, `CIRCLEJERK_ENVIRONMENT=test` via `monkeypatch.setenv`, then calls `get_settings.cache_clear()` before `TestClient(app)`. Follow it exactly.
- **Run tests from `backend/`:** `cd backend && pytest`.

---

### Task 1: Key primitives (`api_keys.py`)

Pure functions and a context dataclass. No FastAPI, no sqlite3, no I/O — so it can be tested standalone and reasoned about in isolation.

**Files:**
- Create: `backend/app/api_keys.py`
- Test: `backend/tests/test_api_keys.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SCOPES: tuple[str, ...]`
  - `generate_key(environment: str) -> tuple[str, str, str]` returning `(full_key, key_id, secret_hash)`
  - `hash_secret(secret: str) -> str`
  - `parse_key(raw: str) -> tuple[str, str] | None` returning `(key_id, secret)`
  - `verify_secret(secret: str, secret_hash: str) -> bool`
  - `validate_scopes(scopes: list[str]) -> list[str]` (raises `ValueError`)
  - `serialize_scopes(scopes: list[str]) -> str`
  - `parse_scopes(raw: str) -> frozenset[str]`
  - `serialize_airports(airports: list[str] | None) -> str | None`
  - `parse_airports(raw: str | None) -> frozenset[str] | None`
  - `display_prefix(key_id: str, environment: str) -> str`
  - `ApiKeyContext` dataclass with fields `key_id: str`, `name: str`, `scopes: frozenset[str]`, `airports: frozenset[str] | None`, and methods `has_scope(scope: str) -> bool`, `allows_airport(icao: str) -> bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_api_keys.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_api_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api_keys'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/api_keys.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_api_keys.py -v`
Expected: PASS, all tests green

- [ ] **Step 5: Commit**

```bash
git add backend/app/api_keys.py backend/tests/test_api_keys.py
git commit -m "feat(api-keys): key generation, parsing, and scope primitives"
```

---

### Task 2: Persist API keys (`db.py`)

**Files:**
- Modify: `backend/app/db.py` — append to the `SCHEMA` string (which ends around line 355) and add CRUD functions near the other table helpers
- Test: `backend/tests/test_api_key_store.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime; stores the already-serialized `scopes` / `airports` strings that Task 1's helpers produce.
- Produces:
  - `create_api_key(conn, *, key_id: str, secret_hash: str, name: str, scopes: str, airports: str | None, created_at: int, created_by: str | None) -> None`
  - `list_api_keys(conn) -> list[dict]`
  - `get_api_key(conn, key_id: str) -> dict | None`
  - `revoke_api_key(conn, key_id: str, now: int) -> bool`
  - `touch_api_key(conn, key_id: str, now: int) -> None`

  Every returned dict has keys `id`, `name`, `scopes`, `airports`, `created_at`, `created_by`, `last_used_at`, `revoked_at`, and never `secret_hash` — except `get_api_key`, which includes `secret_hash` because verification needs it.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_api_key_store.py`:

```python
from __future__ import annotations

from app import db


def _open(tmp_path):
    path = str(tmp_path / "circlejerk.sqlite3")
    db.init_db(path)
    return db.connect(path)


def test_create_and_get_api_key(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read,tracks:read",
        airports="KLMO",
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    row = db.get_api_key(conn, "0123456789abcdef")
    assert row is not None
    assert row["name"] == "partner"
    assert row["scopes"] == "ops:read,tracks:read"
    assert row["airports"] == "KLMO"
    assert row["secret_hash"] == "deadbeef"
    assert row["revoked_at"] is None
    assert row["last_used_at"] is None
    assert db.get_api_key(conn, "ffffffffffffffff") is None


def test_list_api_keys_never_exposes_the_secret_hash(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read",
        airports=None,
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    rows = db.list_api_keys(conn)
    assert len(rows) == 1
    assert "secret_hash" not in rows[0]
    assert rows[0]["airports"] is None


def test_revoke_marks_the_row_and_is_idempotent(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read",
        airports=None,
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    assert db.revoke_api_key(conn, "0123456789abcdef", 1_700_000_100) is True
    conn.commit()
    assert db.get_api_key(conn, "0123456789abcdef")["revoked_at"] == 1_700_000_100

    # Already revoked -> no second write, and an unknown id is a no-op.
    assert db.revoke_api_key(conn, "0123456789abcdef", 1_700_000_200) is False
    assert db.revoke_api_key(conn, "ffffffffffffffff", 1_700_000_200) is False


def test_touch_updates_last_used_at(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read",
        airports=None,
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    db.touch_api_key(conn, "0123456789abcdef", 1_700_000_500)
    conn.commit()
    assert db.get_api_key(conn, "0123456789abcdef")["last_used_at"] == 1_700_000_500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_api_key_store.py -v`
Expected: FAIL with `AttributeError: module 'app.db' has no attribute 'create_api_key'`

- [ ] **Step 3: Add the schema**

In `backend/app/db.py`, append this table to the end of the `SCHEMA` string, immediately before its closing `"""`:

```sql

CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  secret_hash TEXT NOT NULL,
  name TEXT NOT NULL,
  scopes TEXT NOT NULL,
  airports TEXT,
  created_at INTEGER NOT NULL,
  created_by TEXT,
  last_used_at INTEGER,
  revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_api_keys_created ON api_keys(created_at DESC);
```

- [ ] **Step 4: Add the CRUD functions**

Append to the end of `backend/app/db.py`:

```python
_API_KEY_PUBLIC_COLUMNS = (
    "id, name, scopes, airports, created_at, created_by, last_used_at, revoked_at"
)


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
) -> None:
    conn.execute(
        """
        INSERT INTO api_keys
        (id, secret_hash, name, scopes, airports, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (key_id, secret_hash, name, scopes, airports, int(created_at), created_by),
    )


def list_api_keys(conn: sqlite3.Connection) -> list[dict]:
    """Metadata for the admin list. Never returns `secret_hash`."""
    rows = conn.execute(
        f"SELECT {_API_KEY_PUBLIC_COLUMNS} FROM api_keys ORDER BY created_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def get_api_key(conn: sqlite3.Connection, key_id: str) -> dict | None:
    """Full row including `secret_hash` — this is the verification path."""
    row = conn.execute(
        f"SELECT {_API_KEY_PUBLIC_COLUMNS}, secret_hash FROM api_keys WHERE id = ?",
        (key_id,),
    ).fetchone()
    return dict(row) if row else None


def revoke_api_key(conn: sqlite3.Connection, key_id: str, now: int) -> bool:
    """True when this call performed the revocation.

    Keys are revoked, never deleted, so a key id in a log stays attributable.
    """
    cursor = conn.execute(
        "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (int(now), key_id),
    )
    return cursor.rowcount > 0


def touch_api_key(conn: sqlite3.Connection, key_id: str, now: int) -> None:
    conn.execute(
        "UPDATE api_keys SET last_used_at = ? WHERE id = ?", (int(now), key_id)
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_api_key_store.py -v`
Expected: PASS, 4 tests green

- [ ] **Step 6: Confirm nothing else regressed**

Run: `cd backend && pytest -q`
Expected: PASS — the schema addition is a new `CREATE TABLE IF NOT EXISTS`, so existing databases pick it up on the next `init_db` with no migration needed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py backend/tests/test_api_key_store.py
git commit -m "feat(api-keys): api_keys table and CRUD"
```

---

### Task 3: Rate-limit counter on `Store`

**Files:**
- Modify: `backend/app/store.py` — add an abstract method to `Store` (class begins line 22) and concrete implementations in `RedisStore` (line 98) and `MemoryStore` (line 207)
- Test: `backend/tests/test_store_counter.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `async Store.incr_counter(key: str, ttl: int) -> int` — increments a counter and returns its new value, setting the TTL on first increment only, so a fixed window expires `ttl` seconds after it opened.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_store_counter.py`:

```python
from __future__ import annotations

import pytest

from app.store import MemoryStore


@pytest.mark.asyncio
async def test_incr_counter_increments_and_isolates_keys():
    store = MemoryStore()
    assert await store.incr_counter("a", 60) == 1
    assert await store.incr_counter("a", 60) == 2
    assert await store.incr_counter("b", 60) == 1


@pytest.mark.asyncio
async def test_incr_counter_resets_after_the_window_expires(monkeypatch):
    # Advance the clock rather than passing ttl=0: both implementations clamp
    # ttl to a minimum of 1s, so a zero-length window is not a real input.
    store = MemoryStore()
    clock = {"now": 1_700_000_000}
    monkeypatch.setattr("app.store.time.time", lambda: clock["now"])

    assert await store.incr_counter("a", 60) == 1
    assert await store.incr_counter("a", 60) == 2
    clock["now"] += 61
    assert await store.incr_counter("a", 60) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_store_counter.py -v`
Expected: FAIL with `AttributeError: 'MemoryStore' object has no attribute 'incr_counter'`

(`backend/pyproject.toml` already sets `asyncio_mode = "auto"` with `pytest-asyncio` in the test extras, so `@pytest.mark.asyncio` works as-is — same as `tests/test_archive.py`.)

- [ ] **Step 3: Add the abstract method**

In `backend/app/store.py`, inside `class Store(ABC)`, after `set_cache` (line 94):

```python
    @abstractmethod
    async def incr_counter(self, key: str, ttl: int) -> int:
        """Increment a fixed-window counter and return its new value.

        The TTL is applied on the first increment only, so the window expires
        `ttl` seconds after it opened rather than sliding forward on every hit.

        `ttl` is clamped to a minimum of 1 second in every implementation: a
        rate-limit window shorter than that is meaningless, and leaving the
        clamp to one backend only would make Redis and memory disagree.
        """
```

- [ ] **Step 4: Implement on `RedisStore`**

In `backend/app/store.py`, after `RedisStore.set_cache`:

```python
    async def incr_counter(self, key: str, ttl: int) -> int:
        redis_key = f"count:{key}"
        value = await self.redis.incr(redis_key)
        if value == 1:
            await self.redis.expire(redis_key, max(ttl, 1))
        return int(value)
```

- [ ] **Step 5: Implement on `MemoryStore`**

In `backend/app/store.py`, add `self.counters: dict[str, tuple[int, int]] = {}` to `MemoryStore.__init__` (line 207-213, alongside `self.cache`), then add after `MemoryStore.set_cache`:

```python
    async def incr_counter(self, key: str, ttl: int) -> int:
        now = int(time.time())
        row = self.counters.get(key)
        if not row or row[1] <= now:
            self.counters[key] = (1, now + max(ttl, 1))
            return 1
        value, expires = row
        self.counters[key] = (value + 1, expires)
        return value + 1
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_store_counter.py -v`
Expected: PASS, 2 tests green

- [ ] **Step 7: Commit**

```bash
git add backend/app/store.py backend/tests/test_store_counter.py
git commit -m "feat(store): fixed-window incr_counter for rate limiting"
```

---

### Task 4: Public API skeleton — auth, errors, rate limit, `/v1/meta`, `/v1/docs`

The router, its dependencies, the error envelope, cursor helpers, and the first endpoint. Everything after this task adds routes to an already-working spine.

**Files:**
- Create: `backend/app/public_api.py`
- Modify: `backend/app/main.py` — imports (line 31), `include_router` and exception handler after `app.add_middleware` (line ~60)
- Test: `backend/tests/test_public_api.py`

**Interfaces:**
- Consumes: `api_keys.ApiKeyContext`, `api_keys.parse_key`, `api_keys.verify_secret`, `api_keys.parse_scopes`, `api_keys.parse_airports`, `api_keys.display_prefix` (Task 1); `db.get_api_key`, `db.touch_api_key` (Task 2); `Store.incr_counter` (Task 3).
- Produces:
  - `router: APIRouter` with prefix `/v1`
  - `ApiError(Exception)` with attributes `status_code: int`, `code: str`, `message: str`
  - `api_error_handler(request: Request, exc: ApiError) -> JSONResponse`
  - `require_scope(scope: str)` — dependency factory yielding `ApiKeyContext`
  - `require_airport(ctx: ApiKeyContext, icao: str) -> str` — returns the uppercased ICAO or raises `ApiError`
  - `encode_cursor(timestamp: int, row_id: str) -> str`
  - `decode_cursor(raw: str) -> tuple[int, str]`
  - `parse_time(value: str | None, field: str) -> int | None`
  - `page_limit(value: int) -> int`
  - Constants `DEFAULT_PAGE_SIZE = 500`, `MAX_PAGE_SIZE = 5000`, `MAX_TRACK_SPAN_SECONDS = 31 * 86400`, `RATE_LIMIT_PER_MINUTE = 120`, `TOUCH_THROTTLE_SECONDS = 60`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_public_api.py`:

```python
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import api_keys, db
from app.main import app
from app.settings import get_settings


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return str(tmp_path / "circlejerk.sqlite3")


def mint(db_path: str, *, scopes: list[str], airports: list[str] | None = None) -> str:
    """Insert a key directly and return the full presentable key string."""
    full, key_id, secret_hash = api_keys.generate_key("test")
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        db.create_api_key(
            conn,
            key_id=key_id,
            secret_hash=secret_hash,
            name="test-key",
            scopes=api_keys.serialize_scopes(scopes),
            airports=api_keys.serialize_airports(airports),
            created_at=int(time.time()),
            created_by="admin",
        )
    return full


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_meta_requires_a_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/v1/meta")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


def test_meta_echoes_the_keys_scopes_and_airports(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read", "aggregates:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get("/v1/meta", headers=auth(key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "test-key"
        assert sorted(body["scopes"]) == ["aggregates:read", "ops:read"]
        assert body["airports"] == ["KLMO"]
        assert body["version"] == "v1"


def test_unrestricted_key_reports_null_airports(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"], airports=None)
    with TestClient(app) as client:
        assert client.get("/v1/meta", headers=auth(key)).json()["airports"] is None


def test_x_api_key_header_is_accepted(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        assert client.get("/v1/meta", headers={"X-API-Key": key}).status_code == 200


@pytest.mark.parametrize("presented", ["garbage", "cj_test_0123456789abcdef_wrongsecret"])
def test_unknown_and_wrong_keys_are_indistinguishable(tmp_path, monkeypatch, presented):
    db_path = configure(tmp_path, monkeypatch)
    mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/meta", headers=auth(presented))
        assert resp.status_code == 401
        assert resp.json()["error"] == {
            "code": "unauthorized",
            "message": "invalid or missing API key",
        }


def test_revoked_key_is_rejected(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    key_id = api_keys.parse_key(key)[0]
    with db.db_session(db_path) as conn:
        db.revoke_api_key(conn, key_id, int(time.time()))

    with TestClient(app) as client:
        assert client.get("/v1/meta", headers=auth(key)).status_code == 401


def test_successful_call_records_last_used_at(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    key_id = api_keys.parse_key(key)[0]
    with TestClient(app) as client:
        client.get("/v1/meta", headers=auth(key))
    with db.db_session(db_path) as conn:
        assert db.get_api_key(conn, key_id)["last_used_at"] is not None


def test_rate_limit_headers_and_429(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    monkeypatch.setattr("app.public_api.RATE_LIMIT_PER_MINUTE", 2)

    with TestClient(app) as client:
        first = client.get("/v1/meta", headers=auth(key))
        assert first.headers["X-RateLimit-Limit"] == "2"
        assert first.headers["X-RateLimit-Remaining"] == "1"

        client.get("/v1/meta", headers=auth(key))
        third = client.get("/v1/meta", headers=auth(key))
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "rate_limited"
        assert third.headers["Retry-After"] == "60"


def test_docs_and_openapi_cover_only_the_v1_namespace(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json")
        assert schema.status_code == 200
        paths = schema.json()["paths"]
        assert "/v1/meta" in paths
        assert "/admin/dashboard" not in paths
        assert client.get("/v1/docs").status_code == 200


def test_internal_routes_keep_their_detail_error_shape(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/admin/dashboard")
        assert resp.status_code in (401, 503)
        assert "detail" in resp.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_public_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.public_api'`

- [ ] **Step 3: Write `public_api.py`**

Create `backend/app/public_api.py`:

```python
"""The public, key-authenticated /v1 API.

Deliberately separate from the internal routes in main.py: those are shaped for
the frontend and change with it, while everything here is a contract partners
depend on. This module never imports main.py — it reads settings and store off
`request.app.state` so the dependency direction stays one-way.
"""

from __future__ import annotations

import base64
import binascii
import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from . import api_keys, db
from .api_keys import ApiKeyContext
from .db import db_session
from .settings import Settings
from .store import Store

API_VERSION = "v1"

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 5000
MAX_TRACK_SPAN_SECONDS = 31 * 86400
RATE_LIMIT_PER_MINUTE = 120
RATE_LIMIT_WINDOW_SECONDS = 60
# Without this, a hot client turns every read into a SQLite write.
TOUCH_THROTTLE_SECONDS = 60

router = APIRouter(prefix=f"/{API_VERSION}", tags=["public"])


class ApiError(Exception):
    """Anything raised here renders as the /v1 error envelope."""

    def __init__(self, status_code: int, code: str, message: str,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=exc.headers,
    )


# ─── Local dependencies (no main.py import) ──────────────────────────────────

def settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def store_from_app(request: Request) -> Store:
    return request.app.state.store


# ─── Authentication ──────────────────────────────────────────────────────────

_UNAUTHORIZED = "invalid or missing API key"


def _presented_key(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key")


async def resolve_key(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(settings_from_app)],
    store: Annotated[Store, Depends(store_from_app)],
) -> ApiKeyContext:
    raw = _presented_key(request)
    parsed = api_keys.parse_key(raw) if raw else None
    if not parsed:
        raise ApiError(401, "unauthorized", _UNAUTHORIZED)
    key_id, secret = parsed

    with db_session(settings.database_path) as conn:
        row = db.get_api_key(conn, key_id)

    # An unknown id and a wrong secret must be indistinguishable to the caller.
    if not row or row["revoked_at"] is not None:
        raise ApiError(401, "unauthorized", _UNAUTHORIZED)
    if not api_keys.verify_secret(secret, row["secret_hash"]):
        raise ApiError(401, "unauthorized", _UNAUTHORIZED)

    now = int(time.time())
    window = now // RATE_LIMIT_WINDOW_SECONDS
    used = await store.incr_counter(f"apikey:{key_id}:{window}", RATE_LIMIT_WINDOW_SECONDS)
    remaining = max(RATE_LIMIT_PER_MINUTE - used, 0)
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_PER_MINUTE)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if used > RATE_LIMIT_PER_MINUTE:
        raise ApiError(
            429,
            "rate_limited",
            f"rate limit of {RATE_LIMIT_PER_MINUTE} requests per minute exceeded",
            headers={
                "Retry-After": str(RATE_LIMIT_WINDOW_SECONDS),
                "X-RateLimit-Limit": str(RATE_LIMIT_PER_MINUTE),
                "X-RateLimit-Remaining": "0",
            },
        )

    if await store.get_cache(f"apikey:touched:{key_id}") is None:
        with db_session(settings.database_path) as conn:
            db.touch_api_key(conn, key_id, now)
        await store.set_cache(f"apikey:touched:{key_id}", now, TOUCH_THROTTLE_SECONDS)

    return ApiKeyContext(
        key_id=key_id,
        name=row["name"],
        scopes=api_keys.parse_scopes(row["scopes"]),
        airports=api_keys.parse_airports(row["airports"]),
    )


def require_scope(scope: str):
    """Dependency factory: resolves the key, then enforces one scope."""

    async def dependency(
        ctx: Annotated[ApiKeyContext, Depends(resolve_key)],
    ) -> ApiKeyContext:
        if not ctx.has_scope(scope):
            raise ApiError(403, "forbidden_scope", f"key lacks scope {scope}")
        return ctx

    return dependency


def require_airport(ctx: ApiKeyContext, icao: str) -> str:
    """Enforce the key's airport restriction. Returns the normalized ICAO."""
    normalized = icao.upper()
    if not ctx.allows_airport(normalized):
        raise ApiError(403, "forbidden_airport", f"key is not scoped to {normalized}")
    return normalized


# ─── Request helpers ─────────────────────────────────────────────────────────

def page_limit(value: int) -> int:
    return max(1, min(int(value), MAX_PAGE_SIZE))


def parse_time(value: str | None, field: str) -> int | None:
    """Accept a Unix timestamp or an ISO 8601 datetime; return Unix seconds."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(
            400, "invalid_request",
            f"{field} must be a Unix timestamp or an ISO 8601 datetime",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def encode_cursor(timestamp: int, row_id: str) -> str:
    raw = f"{int(timestamp)}:{row_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(raw: str) -> tuple[int, str]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        timestamp, row_id = base64.urlsafe_b64decode(padded).decode("utf-8").split(":", 1)
        return int(timestamp), row_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ApiError(400, "invalid_request", "cursor is not valid") from exc


def paged(rows: list[dict], limit: int, cursor_of) -> dict:
    """Wrap a page of rows, emitting next_cursor only when the page was full."""
    next_cursor = cursor_of(rows[-1]) if len(rows) == limit and rows else None
    return {"data": rows, "next_cursor": next_cursor}


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/meta", summary="Describe the calling key")
async def meta(ctx: Annotated[ApiKeyContext, Depends(resolve_key)]) -> dict:
    """Echoes this key's own scopes and airport restriction, so a 403 can be
    diagnosed without contacting us."""
    return {
        "version": API_VERSION,
        "name": ctx.name,
        "scopes": sorted(ctx.scopes),
        "airports": sorted(ctx.airports) if ctx.airports is not None else None,
        "limits": {
            "requests_per_minute": RATE_LIMIT_PER_MINUTE,
            "max_page_size": MAX_PAGE_SIZE,
            "max_track_span_seconds": MAX_TRACK_SPAN_SECONDS,
        },
    }


@router.get("/openapi.json", include_in_schema=False)
async def public_openapi(request: Request) -> JSONResponse:
    """A schema built from this router alone, so internal routes never leak."""
    return JSONResponse(
        get_openapi(
            title="Circle Jerks Public API",
            version=API_VERSION,
            description="Read-only access to operations, tracks, aggregates, and the ledger.",
            routes=router.routes,
        )
    )


@router.get("/docs", include_in_schema=False)
async def public_docs() -> HTMLResponse:
    # Relative URL on purpose: Caddy and Vite both strip the /api prefix, so
    # the browser sees /api/v1/docs while FastAPI sees /v1/docs. Resolving
    # "openapi.json" against the page URL is correct in both.
    return get_swagger_ui_html(
        openapi_url="openapi.json",
        title="Circle Jerks Public API",
    )
```

- [ ] **Step 4: Mount the router in `main.py`**

In `backend/app/main.py`, extend the package import on line 31:

```python
from . import db, patterns, public_api, track_history, pattern_circuits, vnap
```

Then, immediately after the `app.add_middleware(CORSMiddleware, ...)` block (ends around line 61), add:

```python
# The public /v1 namespace has its own error envelope. The handler is
# registered app-wide, but only public_api raises ApiError, so internal
# routes keep FastAPI's {"detail": ...} shape.
app.add_exception_handler(public_api.ApiError, public_api.api_error_handler)
app.include_router(public_api.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_public_api.py -v`
Expected: PASS, all tests green

- [ ] **Step 6: Confirm the whole suite still passes**

Run: `cd backend && pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/public_api.py backend/app/main.py backend/tests/test_public_api.py
git commit -m "feat(api): /v1 skeleton with key auth, rate limiting, and docs"
```

---

### Task 5: `GET /v1/operations`

**Files:**
- Modify: `backend/app/db.py` — add `read_operations_page` next to `read_operations` (line 554)
- Modify: `backend/app/public_api.py` — add the route and a row serializer
- Test: `backend/tests/test_public_api_operations.py`

**Interfaces:**
- Consumes: `require_scope`, `require_airport`, `encode_cursor`, `decode_cursor`, `parse_time`, `page_limit`, `paged` (Task 4).
- Produces:
  - `db.read_operations_page(conn, *, icao: str, start_ts: int, end_ts: int, types: list[str] | None = None, icao24: str | None = None, runway_id: str | None = None, after: tuple[int, str] | None = None, limit: int = 500) -> list[sqlite3.Row]`
  - `public_api.operation_row(row) -> dict` — the frozen public field list

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_public_api_operations.py`:

```python
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import db
from app.main import app
from test_public_api import auth, configure, mint

NOW = 1_700_000_000

# upsert_operation binds every one of these by name; omitting any raises
# sqlite3.ProgrammingError. Only emitter_category and pass_geometry_key
# are defaulted by the function itself.
OP_DEFAULTS = {
    "icao24": "a1b2c3",
    "callsign": "N333RX",
    "registration": "N333RX",
    "runway_id": "11",
    "runway_heading_deg": 110.0,
    "turn_direction": "left",
    "min_altitude_ft_agl": 900,
}


def seed_operations(db_path: str, icao: str, count: int) -> None:
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        for index in range(count):
            db.upsert_operation(conn, {
                **OP_DEFAULTS,
                "id": f"op-{index:03d}",
                "icao": icao,
                "type": "touch_and_go",
                "timestamp": NOW + index,
            })


def test_operations_requires_the_ops_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_operations_enforces_the_airport_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_operations_returns_a_stable_field_set(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 1)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "since": NOW - 10, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert set(row) == {
            "id", "airport_icao", "icao24", "callsign", "registration", "type",
            "timestamp", "runway_id", "turn_direction", "min_altitude_ft_agl",
            "emitter_category", "deviation_mean_nm", "deviation_peak_nm",
            "pct_off_pattern", "wind_from_deg", "wind_speed_kt",
            "origin_airport_icao", "origin_label", "operator", "flight_school",
        }
        assert row["airport_icao"] == "KBJC"


def test_operations_paginates_without_gaps_or_overlap(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 5)
    key = mint(db_path, scopes=["ops:read"])
    seen: list[str] = []
    with TestClient(app) as client:
        params = {"airport": "KBJC", "since": NOW - 10, "until": NOW + 100, "limit": 2}
        cursor = None
        for _ in range(5):
            page = client.get(
                "/v1/operations",
                params={**params, **({"cursor": cursor} if cursor else {})},
                headers=auth(key),
            ).json()
            seen.extend(item["id"] for item in page["data"])
            cursor = page["next_cursor"]
            if not cursor:
                break
    assert seen == [f"op-{i:03d}" for i in range(5)]


def test_operations_clamps_limit_and_rejects_a_bad_cursor(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 1)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        ok = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "limit": 99999},
            headers=auth(key),
        )
        assert ok.status_code == 200

        bad = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "cursor": "!!!not-base64!!!"},
            headers=auth(key),
        )
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "invalid_request"


def test_operations_filters_by_type_and_icao24(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_operations(db_path, "KBJC", 2)
    with db.db_session(db_path) as conn:
        db.upsert_operation(conn, {
            **OP_DEFAULTS,
            "id": "op-landing",
            "icao": "KBJC",
            "icao24": "ffffff",
            "type": "landing",
            "timestamp": NOW + 50,
        })
    key = mint(db_path, scopes=["ops:read"])
    # Explicit bounds: the default lookback is 7 days from the REAL wall clock,
    # which excludes the fixed NOW constant entirely. The window is deliberately
    # wide enough to include all three seeded rows, so the filters — not the
    # range — are what narrow the result to one.
    window = {"since": NOW - 10, "until": NOW + 100}
    with TestClient(app) as client:
        by_type = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "type": "landing", **window},
            headers=auth(key),
        ).json()
        assert [row["id"] for row in by_type["data"]] == ["op-landing"]

        by_aircraft = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "icao24": "FFFFFF", **window},
            headers=auth(key),
        ).json()
        assert [row["id"] for row in by_aircraft["data"]] == ["op-landing"]


def test_operations_rejects_an_inverted_range(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/operations",
            params={"airport": "KBJC", "since": NOW, "until": NOW - 1},
            headers=auth(key),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_public_api_operations.py -v`
Expected: FAIL with 404 responses — `/v1/operations` does not exist yet

- [ ] **Step 3: Add the keyset query to `db.py`**

In `backend/app/db.py`, immediately after `read_operations` (ends around line 578):

```python
def read_operations_page(
    conn: sqlite3.Connection,
    *,
    icao: str,
    start_ts: int,
    end_ts: int,
    types: list[str] | None = None,
    icao24: str | None = None,
    runway_id: str | None = None,
    after: tuple[int, str] | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    """One keyset-paginated page of operations, ordered by (timestamp, id).

    Keyset rather than OFFSET: an offset scan re-walks every skipped row, which
    turns a deep page over a year of KLMO history into a full table scan on the
    same connection that serves the live site.
    """
    query = (
        "SELECT * FROM operations "
        "WHERE icao = ? AND timestamp >= ? AND timestamp <= ?"
    )
    args: list = [icao.upper(), int(start_ts), int(end_ts)]
    if types:
        query += f" AND type IN ({','.join('?' for _ in types)})"
        args.extend(types)
    if icao24:
        query += " AND icao24 = ?"
        args.append(icao24.lower())
    if runway_id:
        query += " AND runway_id = ?"
        args.append(runway_id)
    if after is not None:
        query += " AND (timestamp > ? OR (timestamp = ? AND id > ?))"
        args.extend([after[0], after[0], after[1]])
    query += " ORDER BY timestamp ASC, id ASC LIMIT ?"
    args.append(int(limit))
    return conn.execute(query, args).fetchall()
```

- [ ] **Step 4: Add the route to `public_api.py`**

Append to `backend/app/public_api.py`:

```python
# Default lookback when the caller supplies no range.
DEFAULT_LOOKBACK_SECONDS = 7 * 86400

# Frozen on purpose: new columns on `operations` must not silently appear in
# the public contract.
_OPERATION_FIELDS = (
    "id", "icao24", "callsign", "registration", "type", "timestamp",
    "runway_id", "turn_direction", "min_altitude_ft_agl", "emitter_category",
    "deviation_mean_nm", "deviation_peak_nm", "pct_off_pattern",
    "wind_from_deg", "wind_speed_kt", "origin_airport_icao", "origin_label",
    "operator", "flight_school",
)


def operation_row(row) -> dict:
    out = {field: row[field] for field in _OPERATION_FIELDS}
    out["airport_icao"] = row["icao"]
    return out


def resolve_range(
    since: str | None, until: str | None, *, now: int, max_span: int | None = None
) -> tuple[int, int]:
    end_ts = parse_time(until, "until") or now
    start_ts = parse_time(since, "since")
    if start_ts is None:
        start_ts = end_ts - DEFAULT_LOOKBACK_SECONDS
    if start_ts > end_ts:
        raise ApiError(400, "invalid_request", "since must be before until")
    if max_span is not None and end_ts - start_ts > max_span:
        raise ApiError(
            400, "invalid_request",
            f"requested range exceeds the {max_span} second maximum; page with the cursor instead",
        )
    return start_ts, end_ts


@router.get("/operations", summary="Classified operations for an airport")
async def list_operations(
    ctx: Annotated[ApiKeyContext, Depends(require_scope("ops:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    airport: str,
    since: str | None = None,
    until: str | None = None,
    type: str | None = None,
    icao24: str | None = None,
    runway: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict:
    icao = require_airport(ctx, airport)
    start_ts, end_ts = resolve_range(since, until, now=int(time.time()))
    size = page_limit(limit)
    after = decode_cursor(cursor) if cursor else None

    with db_session(settings.database_path) as conn:
        rows = db.read_operations_page(
            conn,
            icao=icao,
            start_ts=start_ts,
            end_ts=end_ts,
            types=[type] if type else None,
            icao24=icao24,
            runway_id=runway,
            after=after,
            limit=size,
        )

    return paged(
        [operation_row(row) for row in rows],
        size,
        lambda row: encode_cursor(row["timestamp"], row["id"]),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_public_api_operations.py -v`
Expected: PASS, 7 tests green

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/public_api.py backend/tests/test_public_api_operations.py
git commit -m "feat(api): GET /v1/operations with keyset pagination"
```

---

### Task 6: `GET /v1/tracks`

**Files:**
- Modify: `backend/app/db.py` — add `read_track_archive_page` after `read_operations_page`
- Modify: `backend/app/public_api.py` — add the route
- Test: `backend/tests/test_public_api_tracks.py`

**Interfaces:**
- Consumes: everything from Task 4, plus `resolve_range` from Task 5 and `geo.bbox_for_radius`.
- Produces:
  - `db.read_track_archive_page(conn, *, start_ts: int, end_ts: int, icao24: str | None = None, bbox: tuple[float, float, float, float] | None = None, after: tuple[int, str] | None = None, limit: int = 500) -> list[sqlite3.Row]` where `bbox` is `(min_lat, min_lon, max_lat, max_lon)`
  - `public_api.track_row(row) -> dict`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_public_api_tracks.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.public_api import MAX_TRACK_SPAN_SECONDS
from test_public_api import auth, configure, mint

NOW = 1_700_000_000
# KBJC is in the seeded airport table; these coordinates sit inside its ring.
KBJC_LAT, KBJC_LON = 39.9088, -105.1172


def seed_tracks(db_path: str, count: int, *, icao24: str = "a1b2c3") -> None:
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        for index in range(count):
            conn.execute(
                """
                INSERT OR REPLACE INTO track_archive
                (icao24, timestamp, lat, lon, altitude_ft, heading_deg,
                 vertical_rate_fpm, callsign, in_window, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'test')
                """,
                (icao24, NOW + index, KBJC_LAT, KBJC_LON, 6500.0, 110.0, 0.0, "N333RX"),
            )


def test_tracks_requires_the_tracks_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"icao24": "a1b2c3", "since": NOW, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_tracks_requires_an_icao24_or_airport(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks", params={"since": NOW, "until": NOW + 10}, headers=auth(key)
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"


def test_tracks_requires_an_explicit_range(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/tracks", params={"icao24": "a1b2c3"}, headers=auth(key))
        assert resp.status_code == 400
        assert "since" in resp.json()["error"]["message"]


def test_tracks_rejects_a_span_over_the_cap(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={
                "icao24": "a1b2c3",
                "since": NOW,
                "until": NOW + MAX_TRACK_SPAN_SECONDS + 1,
            },
            headers=auth(key),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"


def test_tracks_returns_samples_for_an_aircraft(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 1)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"icao24": "a1b2c3", "since": NOW - 5, "until": NOW + 5},
            headers=auth(key),
        )
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert set(row) == {
            "icao24", "timestamp", "lat", "lon", "altitude_ft",
            "baro_altitude_ft", "geo_altitude_ft", "heading_deg",
            "vertical_rate_fpm", "callsign", "emitter_category", "source",
        }
        assert row["icao24"] == "a1b2c3"


def test_tracks_paginate_without_gaps(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 5)
    key = mint(db_path, scopes=["tracks:read"])
    seen: list[int] = []
    with TestClient(app) as client:
        params = {"icao24": "a1b2c3", "since": NOW - 5, "until": NOW + 50, "limit": 2}
        cursor = None
        for _ in range(5):
            page = client.get(
                "/v1/tracks",
                params={**params, **({"cursor": cursor} if cursor else {})},
                headers=auth(key),
            ).json()
            seen.extend(item["timestamp"] for item in page["data"])
            cursor = page["next_cursor"]
            if not cursor:
                break
    assert seen == [NOW + i for i in range(5)]


def test_tracks_by_airport_filters_to_the_ring_and_honours_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 1)
    with db.db_session(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO track_archive
            (icao24, timestamp, lat, lon, altitude_ft, in_window, source)
            VALUES ('ffffff', ?, 0.0, 0.0, 3000.0, 1, 'test')
            """,
            (NOW + 1,),
        )

    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        body = client.get(
            "/v1/tracks",
            params={"airport": "KBJC", "since": NOW - 5, "until": NOW + 5},
            headers=auth(key),
        ).json()
        assert {row["icao24"] for row in body["data"]} == {"a1b2c3"}

    restricted = mint(db_path, scopes=["tracks:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"airport": "KBJC", "since": NOW - 5, "until": NOW + 5},
            headers=auth(restricted),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_tracks_for_an_unknown_airport_is_404(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"airport": "ZZZZ", "since": NOW, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_public_api_tracks.py -v`
Expected: FAIL with 404 responses — `/v1/tracks` does not exist yet

- [ ] **Step 3: Add the query to `db.py`**

In `backend/app/db.py`, after `read_operations_page`:

```python
def read_track_archive_page(
    conn: sqlite3.Connection,
    *,
    start_ts: int,
    end_ts: int,
    icao24: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    after: tuple[int, str] | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    """One keyset-paginated page of raw track samples, ordered by
    (timestamp, icao24) — which is also the cursor tuple.

    `bbox` is (min_lat, min_lon, max_lat, max_lon).
    """
    query = "SELECT * FROM track_archive WHERE timestamp >= ? AND timestamp <= ?"
    args: list = [int(start_ts), int(end_ts)]
    if icao24:
        query += " AND icao24 = ?"
        args.append(icao24.lower())
    if bbox is not None:
        query += " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        args.extend([bbox[0], bbox[2], bbox[1], bbox[3]])
    if after is not None:
        query += " AND (timestamp > ? OR (timestamp = ? AND icao24 > ?))"
        args.extend([after[0], after[0], after[1]])
    query += " ORDER BY timestamp ASC, icao24 ASC LIMIT ?"
    args.append(int(limit))
    return conn.execute(query, args).fetchall()
```

- [ ] **Step 4: Add the route to `public_api.py`**

First extend the geo import at the top of `backend/app/public_api.py`:

```python
from .geo import bbox_for_radius
```

Then append:

```python
# Matches the scan ring used by the historical track-density view.
TRACK_RING_NM = 8.0

_TRACK_FIELDS = (
    "icao24", "timestamp", "lat", "lon", "altitude_ft", "baro_altitude_ft",
    "geo_altitude_ft", "heading_deg", "vertical_rate_fpm", "callsign",
    "emitter_category", "source",
)


def track_row(row) -> dict:
    return {field: row[field] for field in _TRACK_FIELDS}


@router.get("/tracks", summary="Raw ADS-B position samples")
async def list_tracks(
    ctx: Annotated[ApiKeyContext, Depends(require_scope("tracks:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    since: str | None = None,
    until: str | None = None,
    icao24: str | None = None,
    airport: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict:
    if not icao24 and not airport:
        raise ApiError(400, "invalid_request", "one of icao24 or airport is required")
    # Declared optional so a missing range raises the /v1 envelope rather than
    # FastAPI's 422 {"detail": ...}. Raw tracks are never served unbounded.
    if not since or not until:
        raise ApiError(400, "invalid_request", "both since and until are required")
    start_ts, end_ts = resolve_range(
        since, until, now=int(time.time()), max_span=MAX_TRACK_SPAN_SECONDS
    )
    size = page_limit(limit)
    after = decode_cursor(cursor) if cursor else None

    bbox = None
    with db_session(settings.database_path) as conn:
        if airport:
            icao = require_airport(ctx, airport)
            found = db.get_airport(conn, icao)
            if found is None:
                raise ApiError(404, "not_found", f"unknown airport {icao}")
            min_lat, min_lon, max_lat, max_lon = bbox_for_radius(
                found.lat, found.lon, TRACK_RING_NM
            )
            bbox = (min_lat, min_lon, max_lat, max_lon)

        rows = db.read_track_archive_page(
            conn,
            start_ts=start_ts,
            end_ts=end_ts,
            icao24=icao24,
            bbox=bbox,
            after=after,
            limit=size,
        )

    return paged(
        [track_row(row) for row in rows],
        size,
        lambda row: encode_cursor(row["timestamp"], row["icao24"]),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_public_api_tracks.py -v`
Expected: PASS, 8 tests green

`geo.bbox_for_radius(lat, lon, radius_nm)` returns `(min_lat, min_lon, max_lat, max_lon)` — already the order `read_track_archive_page` expects, so no conversion is needed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/public_api.py backend/tests/test_public_api_tracks.py
git commit -m "feat(api): GET /v1/tracks with span cap and keyset pagination"
```

---

### Task 7: Aggregate endpoints

Seven read-only views reusing the functions the internal routes already call, each re-wrapped in a public-shaped response.

**Files:**
- Modify: `backend/app/public_api.py`
- Test: `backend/tests/test_public_api_aggregates.py`

**Interfaces:**
- Consumes: `require_scope`, `require_airport`, `ApiError` (Task 4); `db.airport_stats`, `db.airport_operations_trends`, `db.get_airport`, `db.current_flow`, `db.recent_runway_changes`, `db.runways_for_airport`, `db.current_patterns_for_airport` (existing); `services.build_worst_offenders`, `vnap.compute_aircraft_compliance` (existing).
- Produces: seven routes under `/v1/airports/{icao}/`. No new shared helpers.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_public_api_aggregates.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from test_public_api import auth, configure, mint

PATHS = [
    "/v1/airports/KBJC/stats",
    "/v1/airports/KBJC/worst-offenders",
    "/v1/airports/KBJC/operations-trends",
    "/v1/airports/KBJC/vnap-compliance",
    "/v1/airports/KBJC/runways",
    "/v1/airports/KBJC/patterns",
    "/v1/airports/KBJC/flow",
]


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_require_the_aggregates_scope(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_honour_the_airport_restriction(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_answer_for_a_seeded_airport(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 200
        assert resp.json()["airport_icao"] == "KBJC"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/airports/ZZZZ/stats",
        "/v1/airports/ZZZZ/worst-offenders",
        "/v1/airports/ZZZZ/operations-trends",
        "/v1/airports/ZZZZ/vnap-compliance",
        "/v1/airports/ZZZZ/runways",
        "/v1/airports/ZZZZ/patterns",
        "/v1/airports/ZZZZ/flow",
    ],
)
def test_unknown_airport_is_a_v1_shaped_404(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_stats_window_parameter_is_validated(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        ok = client.get("/v1/airports/KBJC/stats", params={"window": "30d"}, headers=auth(key))
        assert ok.status_code == 200
        assert ok.json()["window"]["code"] == "30d"

        bad = client.get("/v1/airports/KBJC/stats", params={"window": "9y"}, headers=auth(key))
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "invalid_request"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_public_api_aggregates.py -v`
Expected: FAIL with 404 responses — none of these routes exist yet

- [ ] **Step 3: Add the routes**

First extend the imports at the top of `backend/app/public_api.py`. Add `import json` to the stdlib block (alphabetically after `import binascii`) — `_pattern_out` below needs it — and widen the package imports:

```python
from . import api_keys, db, vnap
from .services import build_worst_offenders
```

Then append:

```python
# Mirrors main.py's _STATS_WINDOWS so the public windows match the site's.
_WINDOWS = {"1d": (86400, 3600), "7d": (7 * 86400, 86400),
            "30d": (30 * 86400, 86400), "all": (None, 86400)}


def _window(code: str, now: int) -> tuple[int, int, int]:
    """Return (start_ts, end_ts, bucket_seconds) for a window code."""
    if code not in _WINDOWS:
        raise ApiError(
            400, "invalid_request",
            f"window must be one of {', '.join(_WINDOWS)}",
        )
    lookback, bucket = _WINDOWS[code]
    return (0 if lookback is None else now - lookback), now, bucket


def _known_airport(conn, ctx: ApiKeyContext, icao: str) -> str:
    """Enforce the key's restriction, then confirm the airport exists."""
    normalized = require_airport(ctx, icao)
    if db.get_airport(conn, normalized) is None:
        raise ApiError(404, "not_found", f"unknown airport {normalized}")
    return normalized


def _pattern_out(row) -> dict:
    return {
        "id": row["id"],
        "airport_icao": row["icao"],
        "runway_id": row["runway_id"],
        "version": row["version"],
        "name": row["name"],
        "locked": bool(row["locked"]),
        "geometry": json.loads(row["geometry_json"]),
        "created_at": row["created_at"],
    }


@router.get("/airports/{icao}/stats", summary="Operation counts and buckets")
async def airport_stats(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    window: str = "7d",
) -> dict:
    now = int(time.time())
    start_ts, end_ts, bucket = _window(window, now)
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        stats = db.airport_stats(conn, normalized, start_ts, end_ts, bucket_seconds=bucket)
    return {
        "airport_icao": normalized,
        "window": {"code": window, "start_ts": start_ts, "end_ts": end_ts,
                   "bucket_seconds": bucket},
        **stats,
    }


@router.get("/airports/{icao}/worst-offenders", summary="Most-reported aircraft")
async def airport_worst_offenders(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    limit: int = 5,
) -> dict:
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        offenders = build_worst_offenders(
            conn, normalized, now=now, limit=max(1, min(int(limit), 10))
        )
    # airport_icao last: build_worst_offenders returns a whole response body in
    # main.py, so it may already carry the key. Ours is the normalized one.
    return {**offenders, "airport_icao": normalized}


@router.get("/airports/{icao}/operations-trends", summary="Twelve-month trends")
async def airport_operations_trends(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        trends = db.airport_operations_trends(conn, normalized, now_ts=now, months=12)
    return {"airport_icao": normalized, **trends}


@router.get("/airports/{icao}/vnap-compliance", summary="Per-aircraft VNAP compliance")
async def airport_vnap_compliance(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    window: str = "7d",
) -> dict:
    now = int(time.time())
    start_ts, end_ts, _bucket = _window(window, now)
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        compliance = vnap.compute_aircraft_compliance(conn, normalized, start_ts, end_ts)
    return {
        "airport_icao": normalized,
        "window": {"code": window, "start_ts": start_ts, "end_ts": end_ts},
        **compliance,
    }


@router.get("/airports/{icao}/runways", summary="Runway geometry")
async def airport_runways(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        runways = db.runways_for_airport(conn, normalized)
    return {"airport_icao": normalized, "runways": runways}


@router.get("/airports/{icao}/patterns", summary="Current traffic patterns")
async def airport_patterns(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        rows = db.current_patterns_for_airport(conn, normalized)
    return {"airport_icao": normalized, "patterns": [_pattern_out(row) for row in rows]}


@router.get("/airports/{icao}/flow", summary="Active runway and recent changes")
async def airport_flow(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        active = db.current_flow(conn, normalized)
        changes = db.recent_runway_changes(conn, normalized, 20)
    return {"airport_icao": normalized, "active": active, "recent_changes": changes}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_public_api_aggregates.py -v`
Expected: PASS, all parametrized cases green

- [ ] **Step 5: Commit**

```bash
git add backend/app/public_api.py backend/tests/test_public_api_aggregates.py
git commit -m "feat(api): /v1 aggregate endpoints for stats, offenders, trends, VNAP, runways, patterns, flow"
```

---

### Task 8: `GET /v1/ledger/...` proxy

**Files:**
- Modify: `backend/app/settings.py` — add `ledger_api_base_url` near the other service URLs (after line 66's `adsbx_rapidapi_host`)
- Modify: `backend/app/public_api.py` — add the proxy route
- Modify: `.env.example` — document the new setting
- Test: `backend/tests/test_public_api_ledger.py`

**Interfaces:**
- Consumes: `require_scope`, `require_airport`, `ApiError` (Task 4).
- Produces:
  - `Settings.ledger_api_base_url: str` defaulting to `"http://ledger-api:8100"`
  - `public_api.LEDGER_RESOURCES: dict[str, str]` mapping the public suffix to the upstream path template
  - Route `GET /v1/ledger/airports/{icao}/{resource}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_public_api_ledger.py`:

```python
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from test_public_api import auth, configure, mint


def stub_transport(handler):
    """Patch httpx.AsyncClient so no real ledger-api call is made."""
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    return patched


def test_ledger_requires_the_ledger_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_ledger_honours_the_airport_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"], airports=["KBJC"])
    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_unknown_ledger_resource_is_404(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/nonsense", headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_ledger_proxies_upstream_json_and_forwards_query_params(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"airport_icao": "KLMO", "total_fees": 1234})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get(
            "/v1/ledger/airports/KLMO/ledger", params={"days": 7}, headers=auth(key)
        )
        assert resp.status_code == 200
        assert resp.json()["total_fees"] == 1234
    assert seen["url"].endswith("/airports/KLMO/ledger?days=7")


def test_ledger_forwards_upstream_404_as_a_v1_error(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "airport not found"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/ZZZZ/ledger", headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_unreachable_ledger_service_returns_503(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "upstream_unavailable"
        assert "ledger" in resp.json()["error"]["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_public_api_ledger.py -v`
Expected: FAIL with 404 responses — the ledger route does not exist yet

- [ ] **Step 3: Add the setting**

In `backend/app/settings.py`, after `adsbx_rapidapi_host` (line 66):

```python
    # The ledger sidecar. Proxied rather than read directly: ledger-api owns
    # its own SQLite file, and opening it from here would put two writers on
    # one lock — the exact thing that split was made to avoid.
    ledger_api_base_url: str = "http://ledger-api:8100"
```

- [ ] **Step 4: Document it in `.env.example`**

Append to `.env.example`:

```bash
# Base URL of the ledger sidecar, used by the public /v1/ledger/* proxy.
# Defaults to the docker-compose service name; override only if you run
# ledger-api elsewhere. Leave unset for local dev — /v1/ledger/* then
# returns 503 until the sidecar is running.
CIRCLEJERK_LEDGER_API_BASE_URL=http://ledger-api:8100
```

- [ ] **Step 5: Add the route**

Append to `backend/app/public_api.py` (`httpx` is already imported in `main.py` but not here — add `import httpx` to the third-party import block):

```python
# Public suffix -> upstream path on ledger-api. An explicit allowlist, not a
# passthrough: this must never become an open proxy into the sidecar.
LEDGER_RESOURCES = {
    "ledger": "/airports/{icao}/ledger",
    "aircraft-fees": "/airports/{icao}/aircraft-fees",
}


@router.get("/ledger/airports/{icao}/{resource}", summary="KLMO fees ledger")
async def ledger_proxy(
    icao: str,
    resource: str,
    request: Request,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("ledger:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> JSONResponse:
    template = LEDGER_RESOURCES.get(resource)
    if template is None:
        raise ApiError(
            404, "not_found",
            f"unknown ledger resource {resource}; expected one of {', '.join(LEDGER_RESOURCES)}",
        )
    normalized = require_airport(ctx, icao)
    url = settings.ledger_api_base_url.rstrip("/") + template.format(icao=normalized)

    # Drop internal-only escape hatches (_now) rather than forwarding them.
    params = {k: v for k, v in request.query_params.items() if not k.startswith("_")}

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            upstream = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise ApiError(
            503, "upstream_unavailable",
            "the ledger service is not reachable right now",
        ) from exc

    if upstream.status_code == 404:
        raise ApiError(404, "not_found", f"no ledger data for {normalized}")
    if upstream.status_code >= 400:
        raise ApiError(
            503, "upstream_unavailable",
            f"the ledger service returned {upstream.status_code}",
        )
    return JSONResponse(status_code=200, content=upstream.json())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_public_api_ledger.py -v`
Expected: PASS, 6 tests green

- [ ] **Step 7: Commit**

```bash
git add backend/app/settings.py backend/app/public_api.py backend/tests/test_public_api_ledger.py .env.example
git commit -m "feat(api): /v1/ledger proxy to the ledger sidecar"
```

---

### Task 9: Admin key-management routes

**Files:**
- Modify: `backend/app/main.py` — add request models near the other `BaseModel` classes and three routes after `admin_live_sources` (line 709-728)
- Test: `backend/tests/test_admin_api_keys.py`

**Interfaces:**
- Consumes: `api_keys.generate_key`, `api_keys.validate_scopes`, `api_keys.serialize_scopes`, `api_keys.serialize_airports`, `api_keys.parse_scopes`, `api_keys.parse_airports`, `api_keys.display_prefix` (Task 1); `db.create_api_key`, `db.list_api_keys`, `db.revoke_api_key` (Task 2); `require_admin` (existing, `main.py:298`).
- Produces:
  - `GET /admin/api-keys` → `{"keys": [{id, name, prefix, scopes, airports, created_at, created_by, last_used_at, revoked_at}]}`
  - `POST /admin/api-keys` → `{"key": "<full key, once>", "record": {...same shape as a list entry...}}`
  - `POST /admin/api-keys/{key_id}/revoke` → `{"ok": true, "revoked": bool}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_admin_api_keys.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings

PASSWORD = "test-admin-password"


def configure_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    get_settings.cache_clear()


def login(client: TestClient) -> None:
    resp = client.post("/admin/login", json={"username": "admin", "password": PASSWORD})
    assert resp.status_code == 200


def test_admin_key_routes_reject_an_unauthenticated_caller(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/admin/api-keys").status_code == 401
        assert client.post("/admin/api-keys", json={
            "name": "x", "scopes": ["ops:read"], "airports": None
        }).status_code == 401
        assert client.post("/admin/api-keys/deadbeefdeadbeef/revoke").status_code == 401


def test_create_returns_the_secret_exactly_once(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        created = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:read", "aggregates:read"], "airports": ["klmo"]
        })
        assert created.status_code == 200
        body = created.json()
        full_key = body["key"]
        assert full_key.startswith("cj_test_")
        assert body["record"]["name"] == "partner"
        assert body["record"]["airports"] == ["KLMO"]
        assert sorted(body["record"]["scopes"]) == ["aggregates:read", "ops:read"]

        listed = client.get("/admin/api-keys").json()["keys"]
        assert len(listed) == 1
        # The secret must never reappear in any later response.
        assert full_key not in client.get("/admin/api-keys").text
        assert listed[0]["prefix"] == f"cj_test_{body['record']['id']}"
        assert "secret_hash" not in listed[0]


def test_created_key_authenticates_against_v1(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        full_key = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:read"], "airports": None
        }).json()["key"]

        meta = client.get("/v1/meta", headers={"Authorization": f"Bearer {full_key}"})
        assert meta.status_code == 200
        assert meta.json()["scopes"] == ["ops:read"]


def test_revoke_stops_the_key_working(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        created = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:read"], "airports": None
        }).json()
        full_key, key_id = created["key"], created["record"]["id"]

        revoked = client.post(f"/admin/api-keys/{key_id}/revoke")
        assert revoked.status_code == 200
        assert revoked.json() == {"ok": True, "revoked": True}

        assert client.get(
            "/v1/meta", headers={"Authorization": f"Bearer {full_key}"}
        ).status_code == 401

        # Idempotent: a second revoke succeeds but reports no change.
        assert client.post(f"/admin/api-keys/{key_id}/revoke").json()["revoked"] is False


def test_create_rejects_an_unknown_scope(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        resp = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:write"], "airports": None
        })
        assert resp.status_code == 400
        assert "ops:write" in resp.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_admin_api_keys.py -v`
Expected: FAIL — the admin key routes return 404

- [ ] **Step 3: Add the request models**

In `backend/app/main.py`, add `api_keys` to the package import on line 31:

```python
from . import api_keys, db, patterns, public_api, track_history, pattern_circuits, vnap
```

Then add near the other `BaseModel` classes (search for `class AdminLoginRequest`) :

```python
class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str]
    airports: list[str] | None = None
```

- [ ] **Step 4: Add the routes**

In `backend/app/main.py`, after `admin_live_sources` (ends around line 728):

```python
def _api_key_record(row: dict, settings: Settings) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": api_keys.display_prefix(row["id"], settings.environment),
        "scopes": sorted(api_keys.parse_scopes(row["scopes"])),
        "airports": (
            sorted(api_keys.parse_airports(row["airports"]))
            if row["airports"] else None
        ),
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
    }


@app.get("/admin/api-keys")
async def admin_list_api_keys(
    _: Annotated[dict, Depends(require_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        rows = db.list_api_keys(conn)
    return {"keys": [_api_key_record(row, settings) for row in rows]}


@app.post("/admin/api-keys")
async def admin_create_api_key(
    payload: ApiKeyCreateRequest,
    _: Annotated[dict, Depends(require_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    """Returns the full key exactly once. It is unrecoverable afterwards."""
    try:
        scopes = api_keys.serialize_scopes(payload.scopes)
    except ValueError as exc:
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
            created_by=settings.admin_username,
        )
        row = db.get_api_key(conn, key_id)

    record = _api_key_record(row, settings)
    return {"key": full_key, "record": record}


@app.post("/admin/api-keys/{key_id}/revoke")
async def admin_revoke_api_key(
    key_id: str,
    _: Annotated[dict, Depends(require_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        changed = db.revoke_api_key(conn, key_id, int(time.time()))
    return {"ok": True, "revoked": changed}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_admin_api_keys.py -v`
Expected: PASS, 5 tests green

- [ ] **Step 6: Confirm the whole backend suite passes**

Run: `cd backend && pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_admin_api_keys.py
git commit -m "feat(admin): mint, list, and revoke API keys"
```

---

### Task 10: Admin dashboard panel

**Files:**
- Modify: `frontend/src/lib/api.ts` — types and three functions next to the existing admin helpers (line 732-750)
- Create: `frontend/src/components/ApiKeysPanel.tsx`
- Modify: `frontend/src/AdminDashboard.tsx` — import and render the panel
- Modify: `frontend/src/styles.css` — styles for the create form and the one-time reveal
- Test: `frontend/src/lib/apiKeysApi.test.ts`

**Interfaces:**
- Consumes: the three admin routes from Task 9.
- Produces:
  - `ApiKeyRecord` interface: `{ id: string; name: string; prefix: string; scopes: string[]; airports: string[] | null; created_at: number; created_by: string | null; last_used_at: number | null; revoked_at: number | null }`
  - `API_KEY_SCOPES: readonly string[]`
  - `adminListApiKeys(): Promise<{ keys: ApiKeyRecord[] }>`
  - `adminCreateApiKey(name, scopes, airports): Promise<{ key: string; record: ApiKeyRecord }>`
  - `adminRevokeApiKey(id): Promise<{ ok: boolean; revoked: boolean }>`
  - Default-exported `ApiKeysPanel` React component taking no props

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/apiKeysApi.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { adminCreateApiKey, adminListApiKeys, adminRevokeApiKey } from "./api";

function mockFetch(body: unknown, status = 200) {
  const spy = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("admin api key client", () => {
  it("lists keys with credentials included", async () => {
    const spy = mockFetch({ keys: [] });
    const result = await adminListApiKeys();
    expect(result.keys).toEqual([]);
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/admin/api-keys");
    expect(init.credentials).toBe("include");
  });

  it("posts the create payload", async () => {
    const spy = mockFetch({ key: "cj_test_x", record: { id: "x" } });
    const result = await adminCreateApiKey("partner", ["ops:read"], ["KLMO"]);
    expect(result.key).toBe("cj_test_x");
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/admin/api-keys");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      name: "partner",
      scopes: ["ops:read"],
      airports: ["KLMO"],
    });
  });

  it("sends null airports when the list is empty", async () => {
    const spy = mockFetch({ key: "cj_test_x", record: { id: "x" } });
    await adminCreateApiKey("partner", ["ops:read"], []);
    expect(JSON.parse(spy.mock.calls[0][1].body).airports).toBeNull();
  });

  it("posts to the revoke route", async () => {
    const spy = mockFetch({ ok: true, revoked: true });
    const result = await adminRevokeApiKey("abc");
    expect(result.revoked).toBe(true);
    expect(spy.mock.calls[0][0]).toBe("/api/admin/api-keys/abc/revoke");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/apiKeysApi.test.ts`
Expected: FAIL with an import error — `adminListApiKeys` is not exported

- [ ] **Step 3: Add the API client functions**

In `frontend/src/lib/api.ts`, after `adminDashboard()` (line 747-749):

```ts
export const API_KEY_SCOPES = [
  "ops:read",
  "tracks:read",
  "aggregates:read",
  "ledger:read"
] as const;

export interface ApiKeyRecord {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  airports: string[] | null;
  created_at: number;
  created_by: string | null;
  last_used_at: number | null;
  revoked_at: number | null;
}

export function adminListApiKeys() {
  return adminJson<{ keys: ApiKeyRecord[] }>("/admin/api-keys");
}

export function adminCreateApiKey(name: string, scopes: string[], airports: string[]) {
  return adminJson<{ key: string; record: ApiKeyRecord }>("/admin/api-keys", {
    method: "POST",
    body: JSON.stringify({
      name,
      scopes,
      // An empty list means "all airports"; the backend stores that as NULL.
      airports: airports.length ? airports : null
    })
  });
}

export function adminRevokeApiKey(id: string) {
  return adminJson<{ ok: boolean; revoked: boolean }>(`/admin/api-keys/${id}/revoke`, {
    method: "POST"
  });
}
```

- [ ] **Step 4: Run the client test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/apiKeysApi.test.ts`
Expected: PASS, 4 tests green

- [ ] **Step 5: Build the panel component**

Create `frontend/src/components/ApiKeysPanel.tsx`:

```tsx
import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Copy, Ban } from "lucide-react";
import {
  adminCreateApiKey,
  adminListApiKeys,
  adminRevokeApiKey,
  API_KEY_SCOPES,
  ApiError,
  type ApiKeyRecord
} from "../lib/api";

function formatDateTime(ts?: number | null) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true
  });
}

export default function ApiKeysPanel() {
  const [keys, setKeys] = useState<ApiKeyRecord[]>([]);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["aggregates:read"]);
  const [airports, setAirports] = useState("");
  const [revealed, setRevealed] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setKeys((await adminListApiKeys()).keys);
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Could not load API keys");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function toggleScope(scope: string) {
    setScopes((current) =>
      current.includes(scope) ? current.filter((s) => s !== scope) : [...current, scope]
    );
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim() || scopes.length === 0) {
      setStatus("A name and at least one scope are required.");
      return;
    }
    setBusy(true);
    setStatus("");
    try {
      const parsedAirports = airports
        .split(",")
        .map((icao) => icao.trim().toUpperCase())
        .filter(Boolean);
      const created = await adminCreateApiKey(name.trim(), scopes, parsedAirports);
      setRevealed(created.key);
      setName("");
      setAirports("");
      await load();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Could not create the key");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string, keyName: string) {
    setBusy(true);
    try {
      await adminRevokeApiKey(id);
      setStatus(`Revoked ${keyName}.`);
      await load();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Could not revoke the key");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-panel">
      <div className="admin-section-title">
        <KeyRound size={16} /> API keys
      </div>

      {revealed && (
        <div className="api-key-reveal">
          <strong>Copy this key now — it will not be shown again.</strong>
          <code>{revealed}</code>
          <div className="api-key-reveal-actions">
            <button
              type="button"
              onClick={() => void navigator.clipboard.writeText(revealed)}
            >
              <Copy size={14} /> Copy
            </button>
            <button type="button" onClick={() => setRevealed(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      <form className="api-key-form" onSubmit={create}>
        <input
          type="text"
          placeholder="Key name (e.g. lmco-mirror)"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <div className="api-key-scopes">
          {API_KEY_SCOPES.map((scope) => (
            <label key={scope}>
              <input
                type="checkbox"
                checked={scopes.includes(scope)}
                onChange={() => toggleScope(scope)}
              />
              {scope}
            </label>
          ))}
        </div>
        <input
          type="text"
          placeholder="Airports (comma separated, blank = all)"
          value={airports}
          onChange={(event) => setAirports(event.target.value)}
        />
        <button type="submit" disabled={busy}>
          Create key
        </button>
      </form>

      {status && <div className="admin-empty">{status}</div>}

      <div className="admin-table api-key-table">
        <div className="admin-row header">
          <span>Name</span>
          <span>Prefix</span>
          <span>Scopes</span>
          <span>Airports</span>
          <span>Last used</span>
          <span>Status</span>
        </div>
        {keys.length === 0 && <div className="admin-empty">No API keys yet.</div>}
        {keys.map((key) => (
          <div className="admin-row" key={key.id}>
            <span>{key.name}</span>
            <span><code>{key.prefix}</code></span>
            <span>{key.scopes.join(", ")}</span>
            <span>{key.airports ? key.airports.join(", ") : "all"}</span>
            <span>{formatDateTime(key.last_used_at)}</span>
            <span>
              {key.revoked_at ? (
                `Revoked ${formatDateTime(key.revoked_at)}`
              ) : (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void revoke(key.id, key.name)}
                >
                  <Ban size={14} /> Revoke
                </button>
              )}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 6: Render the panel**

In `frontend/src/AdminDashboard.tsx`, add the import after the existing `./lib/api` import block (line 15-22):

```tsx
import ApiKeysPanel from "./components/ApiKeysPanel";
```

Then add `<ApiKeysPanel />` immediately before the closing `</main>` (line 302), after the "Airport submissions" section.

- [ ] **Step 7: Add the styles**

Append to `frontend/src/styles.css`:

```css
.api-key-table .admin-row {
  grid-template-columns: 1.2fr 1.6fr 1.8fr 1fr 1fr 1fr;
}

.api-key-form {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  margin-bottom: 0.75rem;
}

.api-key-form input[type="text"] {
  flex: 1 1 14rem;
  padding: 0.4rem 0.6rem;
}

.api-key-scopes {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  font-size: 0.85rem;
}

.api-key-scopes label {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.api-key-reveal {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.75rem;
  margin-bottom: 0.75rem;
  border: 1px solid currentColor;
  border-radius: 6px;
}

.api-key-reveal code {
  word-break: break-all;
  font-size: 0.85rem;
}

.api-key-reveal-actions {
  display: flex;
  gap: 0.5rem;
}
```

- [ ] **Step 8: Verify the frontend builds and its tests pass**

Run: `cd frontend && npm run build && npx vitest run`
Expected: build succeeds with no TypeScript errors; all vitest suites pass

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/apiKeysApi.test.ts \
        frontend/src/components/ApiKeysPanel.tsx frontend/src/AdminDashboard.tsx \
        frontend/src/styles.css
git commit -m "feat(admin): API key management panel"
```

- [ ] **Step 10: Full verification**

Run:
```bash
cd backend && pytest -q
cd ../frontend && npm run build && npx vitest run
```
Expected: both suites pass with no failures.

---

## Deployment Notes

No migration script is needed: `api_keys` is a `CREATE TABLE IF NOT EXISTS` inside `SCHEMA`, and `db.init_db` runs `executescript(SCHEMA)` on every API start (`backend/app/main.py` lifespan). Deploying the new image creates the table.

`CIRCLEJERK_LEDGER_API_BASE_URL` defaults to the compose service name and needs no `.env` change on the production droplet. No Caddy change is required — `/api/*` already proxies to `api:8000`, so `/api/v1/*` is reachable the moment the router is mounted.

After deploy, mint the first key from the admin dashboard and verify:

```bash
curl -sf https://circlejerks.live/api/v1/meta -H "Authorization: Bearer cj_live_..."
```
