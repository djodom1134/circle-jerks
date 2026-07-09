# VNAP Phase 2 — Owner Override + Crowdsourcing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the community correct an aircraft's owner class (flight_school / LLC / individual / …) and add aircraft/operation notes (incl. a "this is a flight school" flag), resolve `owner_class` as **community override > registry-inferred**, and surface the resolved class in the compliance data.

**Architecture:** Mirror the existing crowdsourced runway-pattern editor flow (versioned rows, `visitor_id` + IP attribution, per-editor rate limit). Two new tables + a small db layer, three endpoints, and a batched resolver wired into the Phase-1 compliance orchestrator. Privacy: only the owner bucket, a flight-school flag, and operation/aircraft notes are storable — no named-individual fields.

**Tech Stack:** Python 3 (FastAPI, raw sqlite3, Pydantic), pytest; TS client types.

## Global Constraints

- Backend tests run from `/Users/d/Code/FAA_circle_jerk/backend` with `.venv/bin/python -m pytest` (bare `python` is NOT on PATH). Baseline **239 passing** — do not regress.
- New **tables** go in `db.SCHEMA` via `CREATE TABLE IF NOT EXISTS` (created on every `init_db`, which runs at startup) — no `_migrate` entry needed (that is only for new columns on existing tables).
- **Resolution rule:** `owner_class` = current override's `owner_type` if one exists (`owner_source = "community"`), else the registry `owner_type` (`owner_source = "inferred"`), else `"unknown"`/`"inferred"`.
- Valid owner buckets (the ONLY accepted values, matching the frontend `OwnerType` enum): `individual, llc, corporation, government, flight_school, university, club, trust, unknown`.
- **Privacy:** the notes API stores only `note` (free text about the aircraft/operation), `is_flight_school`, and editor attribution. No fields for pilot names or personal identity. The note field is length-capped.
- Reuse `db.normalize_icao24` (lowercases) for all `icao24` keys; reuse `main.client_ip`, the `visitor_id` Pydantic field shape, and the rate-limit shape from the pattern editor. Do NOT reimplement icao normalization.
- All `icao24` stored/queried lowercased (consistent with `aircraft_report_counts`, `operations`).

## File Structure

- Modify: `backend/app/db.py` — two tables in `SCHEMA`; the override/notes db functions.
- Modify: `backend/app/main.py` — 3 endpoints + request models + `_enforce_owner_edit_limit`.
- Modify: `backend/app/vnap.py` — `compute_aircraft_compliance` resolves owner via overrides (batched).
- Modify: `frontend/src/lib/api.ts` — client fns + types.
- Create: `backend/tests/test_owner_crowdsource.py` — db-layer + resolution tests.
- Modify: `backend/tests/test_api.py` — endpoint tests.
- Modify: `backend/tests/test_vnap_compliance.py` — override-wins assertion.

---

### Task 1: Schema + db layer for overrides & notes

**Files:**
- Modify: `backend/app/db.py` (`SCHEMA` + new functions near the pattern functions ~L1150)
- Test: `backend/tests/test_owner_crowdsource.py` (create)

**Interfaces:**
- Produces:
  - `set_owner_override(conn, icao24, owner_type, editor_visitor_id, editor_ip, change_note) -> dict` (versioned; supersedes prior current).
  - `current_owner_override(conn, icao24) -> dict | None`.
  - `current_owner_overrides(conn) -> dict[str, str]` — batched `{icao24: owner_type}` of all current overrides.
  - `resolve_owner_class(conn, icao24) -> dict` → `{"owner_class": str, "owner_source": "community"|"inferred"}`.
  - `add_community_note(conn, icao24, note, is_flight_school, editor_visitor_id, editor_ip) -> dict`.
  - `list_community_notes(conn, icao24, include_hidden=False) -> list[dict]`.
  - `count_recent_crowd_edits(conn, visitor_id, ip, since_ts) -> int` (overrides + notes combined).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_owner_crowdsource.py`:

```python
from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_override_supersedes_and_resolves(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # No override, no registry row -> unknown/inferred.
    r0 = db.resolve_owner_class(conn, "AA11")
    assert r0 == {"owner_class": "unknown", "owner_source": "inferred"}

    # A registry row makes it inferred=flight_school.
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, owner_type) VALUES (?, ?, ?)",
        ("N1", "AA11", "flight_school"),
    )
    conn.commit()
    assert db.resolve_owner_class(conn, "aa11")["owner_class"] == "flight_school"
    assert db.resolve_owner_class(conn, "aa11")["owner_source"] == "inferred"

    # A community override wins.
    db.set_owner_override(conn, "AA11", "llc", editor_visitor_id="visitor-1234",
                          editor_ip="1.2.3.4", change_note="it's an LLC")
    conn.commit()
    r = db.resolve_owner_class(conn, "aa11")
    assert r == {"owner_class": "llc", "owner_source": "community"}

    # A second override supersedes the first (versioned; only one current).
    db.set_owner_override(conn, "AA11", "individual", editor_visitor_id="visitor-1234",
                          editor_ip="1.2.3.4", change_note="actually individual")
    conn.commit()
    assert db.resolve_owner_class(conn, "aa11")["owner_class"] == "individual"
    cur = conn.execute(
        "SELECT COUNT(*) AS c FROM aircraft_owner_overrides WHERE icao24='aa11' AND is_current=1"
    ).fetchone()["c"]
    assert cur == 1
    assert db.current_owner_overrides(conn)["aa11"] == "individual"


def test_community_notes_and_rate_count(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.add_community_note(conn, "BB22", "Does laps every morning", is_flight_school=True,
                          editor_visitor_id="v-abcdef12", editor_ip="9.9.9.9")
    conn.commit()
    notes = db.list_community_notes(conn, "bb22")
    assert len(notes) == 1
    assert notes[0]["note"] == "Does laps every morning"
    assert notes[0]["is_flight_school"] == 1
    # hidden notes excluded by default
    conn.execute("UPDATE aircraft_community_notes SET hidden=1 WHERE icao24='bb22'")
    conn.commit()
    assert db.list_community_notes(conn, "bb22") == []
    # rate counter sees both overrides and notes by this editor
    n = db.count_recent_crowd_edits(conn, "v-abcdef12", "9.9.9.9", since_ts=0)
    assert n == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_owner_crowdsource.py -v`
Expected: FAIL (tables/functions missing).

- [ ] **Step 3: Add the two tables to `SCHEMA`**

In `db.py`, append inside the `SCHEMA` string (after the `runway_changes` block, before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS aircraft_owner_overrides (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao24 TEXT NOT NULL,
  owner_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1,
  locked INTEGER NOT NULL DEFAULT 0,
  editor_visitor_id TEXT,
  editor_ip TEXT,
  change_note TEXT,
  created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
);
CREATE INDEX IF NOT EXISTS idx_owner_overrides_current ON aircraft_owner_overrides(icao24, is_current);

CREATE TABLE IF NOT EXISTS aircraft_community_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao24 TEXT NOT NULL,
  note TEXT NOT NULL,
  is_flight_school INTEGER NOT NULL DEFAULT 0,
  editor_visitor_id TEXT,
  editor_ip TEXT,
  hidden INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
);
CREATE INDEX IF NOT EXISTS idx_community_notes_icao ON aircraft_community_notes(icao24, created_at DESC);
```

- [ ] **Step 4: Add the db functions**

In `db.py`, near the pattern functions (after `count_recent_pattern_edits`, ~L1168):

```python
def set_owner_override(conn: sqlite3.Connection, icao24: str, owner_type: str,
                       editor_visitor_id: str | None = None, editor_ip: str | None = None,
                       change_note: str | None = None) -> dict:
    """Append a new current owner-class override (mirrors save_runway_pattern)."""
    icao24 = normalize_icao24(icao24)
    row = conn.execute(
        "SELECT MAX(version) AS v FROM aircraft_owner_overrides WHERE icao24=?", (icao24,),
    ).fetchone()
    next_version = (row["v"] or 0) + 1
    conn.execute("UPDATE aircraft_owner_overrides SET is_current=0 WHERE icao24=?", (icao24,))
    conn.execute(
        "INSERT INTO aircraft_owner_overrides "
        "  (icao24, owner_type, version, is_current, editor_visitor_id, editor_ip, change_note) "
        "VALUES (?, ?, ?, 1, ?, ?, ?)",
        (icao24, owner_type, next_version, editor_visitor_id, editor_ip, change_note),
    )
    return current_owner_override(conn, icao24)


def current_owner_override(conn: sqlite3.Connection, icao24: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM aircraft_owner_overrides WHERE icao24=? AND is_current=1",
        (normalize_icao24(icao24),),
    ).fetchone()
    return dict(row) if row else None


def current_owner_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["icao24"]: r["owner_type"]
        for r in conn.execute(
            "SELECT icao24, owner_type FROM aircraft_owner_overrides WHERE is_current=1"
        ).fetchall()
    }


def resolve_owner_class(conn: sqlite3.Connection, icao24: str) -> dict:
    override = current_owner_override(conn, icao24)
    if override:
        return {"owner_class": override["owner_type"], "owner_source": "community"}
    row = conn.execute(
        "SELECT owner_type FROM aircraft_registry WHERE icao_hex = upper(?) LIMIT 1",
        (normalize_icao24(icao24),),
    ).fetchone()
    return {"owner_class": (row["owner_type"] if row and row["owner_type"] else "unknown"),
            "owner_source": "inferred"}


def add_community_note(conn: sqlite3.Connection, icao24: str, note: str, is_flight_school: bool,
                       editor_visitor_id: str | None = None, editor_ip: str | None = None) -> dict:
    icao24 = normalize_icao24(icao24)
    cur = conn.execute(
        "INSERT INTO aircraft_community_notes "
        "  (icao24, note, is_flight_school, editor_visitor_id, editor_ip) VALUES (?, ?, ?, ?, ?)",
        (icao24, note, 1 if is_flight_school else 0, editor_visitor_id, editor_ip),
    )
    row = conn.execute(
        "SELECT * FROM aircraft_community_notes WHERE id=?", (cur.lastrowid,),
    ).fetchone()
    return dict(row)


def list_community_notes(conn: sqlite3.Connection, icao24: str,
                         include_hidden: bool = False) -> list[dict]:
    q = ("SELECT id, icao24, note, is_flight_school, created_at FROM aircraft_community_notes "
         "WHERE icao24=?")
    if not include_hidden:
        q += " AND hidden=0"
    q += " ORDER BY created_at DESC"
    return [dict(r) for r in conn.execute(q, (normalize_icao24(icao24),)).fetchall()]


def count_recent_crowd_edits(conn: sqlite3.Connection, visitor_id: str | None,
                             ip: str | None, since_ts: int) -> int:
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM aircraft_owner_overrides
            WHERE created_at >= ?
              AND ((editor_visitor_id IS NOT NULL AND editor_visitor_id = ?)
                   OR (editor_ip IS NOT NULL AND editor_ip = ?))) +
          (SELECT COUNT(*) FROM aircraft_community_notes
            WHERE created_at >= ?
              AND ((editor_visitor_id IS NOT NULL AND editor_visitor_id = ?)
                   OR (editor_ip IS NOT NULL AND editor_ip = ?))) AS c
        """,
        (since_ts, visitor_id, ip, since_ts, visitor_id, ip),
    ).fetchone()
    return row["c"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_owner_crowdsource.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Full suite + commit**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest -q`
Expected: PASS (≥ 239 + 2).

```bash
git add backend/app/db.py backend/tests/test_owner_crowdsource.py
git commit -m "feat(crowdsource): owner-override + community-notes tables and db layer"
```

---

### Task 2: Endpoints + request models + API client

**Files:**
- Modify: `backend/app/main.py` (models near the other `BaseModel`s ~L118; endpoints near the pattern editor; a rate-limit helper)
- Modify: `frontend/src/lib/api.ts`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 1 db functions; `client_ip`, `db_session`, `db.get_airport` (not needed — aircraft are global), the `visitor_id` field shape.
- Produces:
  - `PUT /aircraft/{icao24}/owner-class` (body: owner_type, visitor_id, change_note) → resolved class. 422 on bad bucket, 429 on rate limit, 400 on unidentifiable editor.
  - `POST /aircraft/{icao24}/notes` (body: note, is_flight_school, visitor_id) → the created note.
  - `GET /aircraft/{icao24}/notes` → `{owner: {owner_class, owner_source}, notes: [...]}`.
  - api.ts: `setOwnerClass`, `addAircraftNote`, `getAircraftNotes` + types.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py` (mirror the monkeypatch fixture pattern used by the other endpoint tests):

```python
def test_owner_class_override_and_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    with TestClient(app) as client:
        # Bad bucket -> 422.
        bad = client.put("/aircraft/aa11/owner-class",
                         json={"owner_type": "spaceship", "visitor_id": "visitor-1234"})
        assert bad.status_code == 422
        # Valid override -> resolved community class.
        ok = client.put("/aircraft/aa11/owner-class",
                        json={"owner_type": "flight_school", "visitor_id": "visitor-1234",
                              "change_note": "local school"})
        assert ok.status_code == 200
        assert ok.json()["owner_class"] == "flight_school"
        assert ok.json()["owner_source"] == "community"
        # Note round-trips and GET returns it + the resolved owner.
        client.post("/aircraft/aa11/notes",
                    json={"note": "laps at dawn", "is_flight_school": True,
                          "visitor_id": "visitor-1234"})
        got = client.get("/aircraft/aa11/notes")
        assert got.status_code == 200
        assert got.json()["owner"]["owner_class"] == "flight_school"
        assert got.json()["notes"][0]["note"] == "laps at dawn"
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_api.py -k owner_class -v`
Expected: FAIL (routes missing).

- [ ] **Step 3: Add request models + valid-bucket set**

In `main.py`, near the other `BaseModel` classes (~L118):

```python
VALID_OWNER_TYPES = frozenset({
    "individual", "llc", "corporation", "government",
    "flight_school", "university", "club", "trust", "unknown",
})


class OwnerClassRequest(BaseModel):
    owner_type: str
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)
    change_note: str | None = Field(default=None, max_length=280)


class CommunityNoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=280)
    is_flight_school: bool = False
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)
```

- [ ] **Step 4: Add the rate-limit helper + endpoints**

In `main.py`, near `_enforce_pattern_edit_limit` (~L1543) add a sibling (reuse `PATTERN_EDIT_WINDOW_S`/`PATTERN_EDIT_MAX_PER_WINDOW`):

```python
def _enforce_crowd_edit_limit(conn, visitor_id: str | None, ip: str | None, now: int) -> None:
    if not visitor_id and not ip:
        raise HTTPException(status_code=400, detail="cannot identify editor")
    recent = db.count_recent_crowd_edits(conn, visitor_id, ip, now - PATTERN_EDIT_WINDOW_S)
    if recent >= PATTERN_EDIT_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="too many edits; slow down")
```

Add the endpoints (place after the pattern editor endpoints):

```python
@app.put("/aircraft/{icao24}/owner-class")
async def set_aircraft_owner_class(
    icao24: str,
    payload: OwnerClassRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    if payload.owner_type not in VALID_OWNER_TYPES:
        raise HTTPException(status_code=422, detail="invalid owner_type")
    now = int(time.time())
    ip = client_ip(request)
    with db_session(settings.database_path) as conn:
        _enforce_crowd_edit_limit(conn, payload.visitor_id, ip, now)
        db.set_owner_override(conn, icao24, payload.owner_type,
                              editor_visitor_id=payload.visitor_id, editor_ip=ip,
                              change_note=payload.change_note)
        resolved = db.resolve_owner_class(conn, icao24)
    return resolved


@app.post("/aircraft/{icao24}/notes")
async def add_aircraft_note(
    icao24: str,
    payload: CommunityNoteRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    ip = client_ip(request)
    with db_session(settings.database_path) as conn:
        _enforce_crowd_edit_limit(conn, payload.visitor_id, ip, now)
        note = db.add_community_note(conn, icao24, payload.note, payload.is_flight_school,
                                     editor_visitor_id=payload.visitor_id, editor_ip=ip)
    return {"id": note["id"], "note": note["note"],
            "is_flight_school": bool(note["is_flight_school"]), "created_at": note["created_at"]}


@app.get("/aircraft/{icao24}/notes")
async def get_aircraft_notes(
    icao24: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        owner = db.resolve_owner_class(conn, icao24)
        notes = db.list_community_notes(conn, icao24)
    return {"owner": owner,
            "notes": [{"id": n["id"], "note": n["note"],
                       "is_flight_school": bool(n["is_flight_school"]),
                       "created_at": n["created_at"]} for n in notes]}
```

- [ ] **Step 5: Add the API client**

In `frontend/src/lib/api.ts` (after the vnap client):

```typescript
export interface ResolvedOwner { owner_class: string; owner_source: string; }
export interface AircraftNote { id: number; note: string; is_flight_school: boolean; created_at: number; }

export function setOwnerClass(icao24: string, owner_type: string, visitor_id: string, change_note?: string) {
  return postJson<ResolvedOwner>(`/aircraft/${encodeURIComponent(icao24)}/owner-class`, "PUT",
    { owner_type, visitor_id, change_note });
}
export function addAircraftNote(icao24: string, note: string, is_flight_school: boolean, visitor_id: string) {
  return postJson<AircraftNote>(`/aircraft/${encodeURIComponent(icao24)}/notes`, "POST",
    { note, is_flight_school, visitor_id });
}
export function getAircraftNotes(icao24: string) {
  return getJson<{ owner: ResolvedOwner; notes: AircraftNote[] }>(
    `/aircraft/${encodeURIComponent(icao24)}/notes`);
}
```

If `api.ts` has no `postJson` helper with a method arg, follow the existing mutation idiom in that file (search for an existing `PUT`/`POST` fetcher such as the pattern-save call, e.g. `savePattern`, and mirror its exact signature/body-encoding). Match what exists — do not invent a new fetch wrapper.

- [ ] **Step 6: Run test + typecheck**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_api.py -k owner_class -v`
Expected: PASS.
Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 7: Full backend suite + commit**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add backend/app/main.py frontend/src/lib/api.ts backend/tests/test_api.py
git commit -m "feat(crowdsource): owner-class + community-notes endpoints and client"
```

---

### Task 3: Resolve community overrides in the compliance engine

**Files:**
- Modify: `backend/app/vnap.py` (`compute_aircraft_compliance`)
- Test: `backend/tests/test_vnap_compliance.py` (add an override-wins assertion)

**Interfaces:**
- Consumes: `db.current_owner_overrides` (batched).
- Produces: each compliance aircraft's `owner_class` reflects a community override when present, with `owner_source` = `"community"` else `"inferred"`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_vnap_compliance.py`:

```python
def test_compliance_uses_community_owner_override(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    _op(conn, "l1", "landing", base + 10, "dd44")
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, owner_type) VALUES (?, ?, ?)",
        ("N9", "DD44", "individual"),
    )
    db.set_owner_override(conn, "dd44", "flight_school", editor_visitor_id="v-12345678")
    conn.commit()
    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == "dd44")
    assert ac["owner_class"] == "flight_school"   # override beats registry "individual"
    assert ac["owner_source"] == "community"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_vnap_compliance.py -k override -v`
Expected: FAIL (`owner_source` is "inferred").

- [ ] **Step 3: Wire the batched overrides into the orchestrator**

In `vnap.py compute_aircraft_compliance`, after the `report_counts` / `cowboy_counts` batched lookups, add:

```python
    owner_overrides = _db.current_owner_overrides(conn)
```

Then in the per-aircraft loop, replace the existing owner assignment. Currently it does:

```python
        owner_type = next((r["owner_type"] for r in ac_rows if r["owner_type"]), "unknown")
```
and sets `"owner_class": owner_type, "owner_source": "inferred"`. Change to:

```python
        inferred = next((r["owner_type"] for r in ac_rows if r["owner_type"]), "unknown")
        override = owner_overrides.get(icao24)
        owner_class = override if override else inferred
        owner_source = "community" if override else "inferred"
```
and set `"owner_class": owner_class, "owner_source": owner_source` in the aircraft dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_vnap_compliance.py -v`
Expected: PASS (all, incl. the override test).

- [ ] **Step 5: Full suite + commit**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest -q`
Expected: PASS.

```bash
git add backend/app/vnap.py backend/tests/test_vnap_compliance.py
git commit -m "feat(vnap): compliance owner_class reflects community overrides"
```

---

## Self-Review

**Spec coverage (Phase 2):**
- Owner override store, versioned, override > inferred → Task 1 (`set_owner_override`, `resolve_owner_class`). ✓
- Community notes + flight-school flag + `hidden` + privacy scoping (no personal fields) → Task 1 tables + Task 2 model (`note` capped, only bucket/flag/note). ✓
- Rate-limited, visitor+IP attributed, mirrors pattern editor → Task 2 `_enforce_crowd_edit_limit`. ✓
- Bucket validation (422) → Task 2. ✓
- Resolved class flows into compliance → Task 3 (batched, no N+1). ✓
- Endpoints + client → Task 2. ✓

**Placeholder scan:** no TBD/TODO; full code each step. The one prose fallback (api.ts mutation idiom) points at an existing pattern to mirror, not a blank. ✓

**Type consistency:** `owner_class`/`owner_source` keys identical across `resolve_owner_class`, the endpoints, `compute_aircraft_compliance`, and the TS `ResolvedOwner`; `VALID_OWNER_TYPES` matches the frontend `OwnerType` enum; `count_recent_crowd_edits` signature matches `_enforce_crowd_edit_limit`'s call. ✓

**Deviation from design (intentional):** rate limit reuses the pattern-editor window/max constants (`3600s` / `30`) rather than new ones — same crowdsourcing surface, one knob. Profile-card wiring (surfacing the resolved class on `AircraftProfileCard`) is deferred to Phase 3/4 where the UI is touched; Phase 2 wires the resolved class into the compliance engine (what the dashboard consumes) and exposes the endpoints.
