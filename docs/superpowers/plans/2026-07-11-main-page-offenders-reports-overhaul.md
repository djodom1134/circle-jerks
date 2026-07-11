# Main-page Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the auto-generated noise-complaint reports, add a per-airport "worst offenders" card section (VNAP × circles) with nearest-airport fallback, add live report preview + per-user prompt persistence, randomize the tagline, and rework the top bar (remove feed icon, add circle/square count badges, a public online-users count).

**Architecture:** Backend is Python 3.12 / FastAPI (`backend/app`), SQLite via `db.py`, LLM reports via Groq in `llm.py` + `services.py`, VNAP scoring in `vnap.py`. Frontend is React 18 + TypeScript + Vite (`frontend/src`), plain global CSS in `styles.css`, HTTP polling (no sockets). Reports are made "steerable" by moving the persona into the (fully replaceable) system prompt and trimming the app-controlled DATA block; worst-offenders reuse the existing VNAP compute + report-count tables via a new endpoint; presence reuses the existing `visitor_activity` heartbeat table via a new public count endpoint.

**Tech Stack:** FastAPI, pydantic v2, SQLite, httpx, Groq (`llama-3.3-70b-versatile`), React 18, Vite 6, vitest 2, pytest 8 (`asyncio_mode=auto`).

## Global Constraints

- Backend tests run from `backend/`: **`.venv/bin/python -m pytest`** (the bare `python` and the repo-root `.venv` are broken — always use `backend/.venv/bin/python`). Config: `testpaths=["tests"]`, `pythonpath=["."]`, `asyncio_mode=auto`. Seed a DB in tests with `conn = db.connect(str(path)); conn.executescript(db.SCHEMA); db.seed_db(conn); conn.commit()`.
- Frontend tests run from `frontend/`: `npm test` (= `vitest run`). Typecheck/build: `npm run build` (= `tsc && vite build`).
- `getJson`/`postJson` in `frontend/src/lib/api.ts` prepend `/api` to the path — pass paths like `/airports/...`, never `/api/...`.
- The report provider is **Groq** (not xAI). Model + key come from `settings.groq_model` / `settings.groq_api_key`.
- Custom system prompt = **full replace** of `DEFAULT_SYSTEM_PROMPT`. Content rules are enforced by the DATA block (facts fed to the model), never by prompt instructions.
- Never show a callsign that is not an N-number. Never emit runway/field elevation, circle counts, or loop radius in reports. Only mention runway changes when explicitly flagged unwarranted.
- Commit after each task with the message shown in its final step. Do not push (leave that to the user).
- Keep 12-hour AM/PM time in all report copy.

---

# Phase 1 — Report generator overhaul (backend)

### Task 1: Full-prompt cache-key fingerprint

The description cache key currently truncates the system prompt to 120 chars (`(system_prompt or "default")[:120]`), so edits past char 120 return stale cached text. Hash the full prompt instead.

**Files:**
- Modify: `backend/app/services.py` (add helper near top-level report functions; edit cache-key builders at ~2002-2018 and ~2157-2173)
- Test: `backend/tests/test_report_prompt.py` (new)

**Interfaces:**
- Produces: `services.system_prompt_fingerprint(system_prompt: str | None) -> str`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_report_prompt.py`:

```python
from __future__ import annotations

from app import services


def test_system_prompt_fingerprint_differs_past_120_chars():
    common = "x" * 130
    a = services.system_prompt_fingerprint(common + "AAA")
    b = services.system_prompt_fingerprint(common + "BBB")
    assert a != b


def test_system_prompt_fingerprint_stable_and_default():
    assert services.system_prompt_fingerprint(None) == services.system_prompt_fingerprint(None)
    assert services.system_prompt_fingerprint("") == services.system_prompt_fingerprint(None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q`
Expected: FAIL with `AttributeError: module 'app.services' has no attribute 'system_prompt_fingerprint'`

- [ ] **Step 3: Add `import hashlib` and the helper**

In `backend/app/services.py`, add `import hashlib` to the stdlib import block (after `import asyncio` on line 5). Then add this helper immediately above `def complaint_context(` (line ~1871):

```python
def system_prompt_fingerprint(system_prompt: str | None) -> str:
    """Stable 16-char hash of the FULL system prompt for cache keying.

    The old key used only the first 120 chars, so two prompts sharing a prefix
    collided and edits past char 120 returned stale cached text.
    """
    return hashlib.sha1((system_prompt or "default").encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Use it in both cache keys**

In `build_description`, replace the last cache_key element (line ~2017):
```python
        (system_prompt or "default")[:120],
```
with:
```python
        system_prompt_fingerprint(system_prompt),
```

In `build_summary_description`, replace the identical line (~2172) the same way.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services.py backend/tests/test_report_prompt.py
git commit -m "fix(reports): hash full system prompt into description cache key"
```

---

### Task 2: N-number-only aircraft identifier

Reports must never show a non-N-number callsign. Add a resolver and use it so `ComplaintContext.callsign` always holds an N-number (or falls back to the ICAO hex, never a raw flight-ID callsign).

**Files:**
- Modify: `backend/app/registry/normalize.py` (add `resolve_display_tail`)
- Modify: `backend/app/services.py:1886-1889` (use it in `complaint_context`) and imports
- Test: `backend/tests/test_registry.py` (append)

**Interfaces:**
- Produces: `normalize.resolve_display_tail(callsign: str | None, registration: str | None, icao24: str) -> str`
- Consumes (Task 10/11): the same resolver for VNAP-sourced offenders.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_registry.py`:

```python
from app.registry.normalize import resolve_display_tail


def test_resolve_display_tail_prefers_n_number_callsign():
    assert resolve_display_tail("N4632F", None, "a5a764") == "N4632F"


def test_resolve_display_tail_drops_airline_callsign_uses_registration():
    assert resolve_display_tail("UAL237", "N123AB", "abc123") == "N123AB"


def test_resolve_display_tail_falls_back_to_hex_when_nothing_valid():
    assert resolve_display_tail("UAL237", None, "abc123") == "ABC123"
    assert resolve_display_tail(None, None, "abc123") == "ABC123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_registry.py -q -k resolve_display_tail`
Expected: FAIL with `ImportError: cannot import name 'resolve_display_tail'`

- [ ] **Step 3: Implement the resolver**

Append to `backend/app/registry/normalize.py`:

```python
def resolve_display_tail(callsign: str | None, registration: str | None, icao24: str) -> str:
    """The identifier to show in a complaint: an N-number when we have one,
    else the uppercase ICAO hex. Never a raw airline/flight-ID callsign."""
    return (
        extract_n_number_from_callsign(callsign)
        or normalize_n_number(registration)
        or icao24.upper()
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_registry.py -q -k resolve_display_tail`
Expected: PASS (3 passed)

- [ ] **Step 5: Use it in `complaint_context`**

In `backend/app/services.py`, add to the import at line 14 or add a new import line after line 16:
```python
from .registry.normalize import resolve_display_tail
```

Then in `complaint_context` replace the callsign resolution (lines 1886-1889):
```python
    callsign = next(
        (sample.get("callsign") for sample in reversed(track) if sample.get("callsign")),
        next((event.get("callsign") for event in reversed(events) if event.get("callsign")), icao24.upper()),
    )
```
with:
```python
    raw_callsign = next(
        (sample.get("callsign") for sample in reversed(track) if sample.get("callsign")),
        next((event.get("callsign") for event in reversed(events) if event.get("callsign")), None),
    )
    callsign = resolve_display_tail(raw_callsign, (aircraft or {}).get("registration"), icao24)
```

- [ ] **Step 6: Run the report smoke test to verify nothing broke**

Run: `cd backend && .venv/bin/python -m pytest tests/test_core.py -q -k "summary or deterministic"`
Expected: PASS (existing report tests still pass — `complaint_context` still returns a string callsign)

- [ ] **Step 7: Commit**

```bash
git add backend/app/registry/normalize.py backend/app/services.py backend/tests/test_registry.py
git commit -m "feat(reports): show only N-numbers as the aircraft identifier"
```

---

### Task 3: Persona → system prompt; trim the DATA block

Move the persona/instructions into `DEFAULT_SYSTEM_PROMPT` (so a full-replace custom prompt actually steers output) and slim the user-prompt DATA to the allowed facts: no elevation, no circles, no loop radius; emphasize touch-and-goes, passes, and lowest altitude over the house.

**Files:**
- Modify: `backend/app/llm.py` (`DEFAULT_SYSTEM_PROMPT` line 277; `build_prompt` 91-138; `build_aggregate_prompt` 141-196; timeout on line 302)
- Test: `backend/tests/test_report_prompt.py` (append)

**Interfaces:**
- Consumes: `ComplaintContext`, `AggregateComplaintContext`, `ToneSliders` (unchanged signatures).
- Produces: trimmed `build_prompt` / `build_aggregate_prompt` output + persona `DEFAULT_SYSTEM_PROMPT`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_report_prompt.py`:

```python
from app.llm import (
    ComplaintContext,
    AggregateComplaintContext,
    DEFAULT_SYSTEM_PROMPT,
    build_prompt,
    build_aggregate_prompt,
)
from app.tone import ToneSliders


def _ctx(**over):
    base = dict(
        airport_name="Vance Brand", airport_icao="KLMO", user_location_label="Longmont, CO",
        time_window_label="in the last hour", callsign="N4632F", icao24="a5a764",
        aircraft_type="Cessna 172", observed_from="6:03 AM", observed_to="7:15 AM",
        circles=12, avg_radius=3.23, alt_min=0, alt_max=2295, touch_and_gos=7,
        low_approaches=5, passes=3, avg_altitude_user=1200, min_altitude_user=650,
        quiet_hours_events=18, peak_hours="6 AM, 7 AM", origin_airport="Broomfield, CO (KBJC)",
        runway_used="29", airport_elevation_ft=5055, previous_report_count=0,
        peak_db_at_home=52.4,
    )
    base.update(over)
    return ComplaintContext(**base)


def test_default_system_prompt_carries_persona_rules():
    p = DEFAULT_SYSTEM_PROMPT.lower()
    assert "touch-and-go" in p
    assert "n-number" in p
    assert "elevation" in p and "do not mention" in p  # explicitly forbids elevation
    assert "military" in p  # 12-hour time rule preserved


def test_build_prompt_omits_forbidden_facts_keeps_noise_facts():
    sliders = ToneSliders(anger=3, niceness=6, respect=7, detail=6, local=3)
    text = build_prompt(_ctx(), sliders)
    assert "elevation" not in text.lower()
    assert "circles_count" not in text
    assert "avg_loop_radius" not in text
    assert "5055" not in text
    assert "touch_and_go_count: 7" in text
    assert "lowest_altitude_over_user_ft_agl: 650" in text
    assert "aircraft_n_number: N4632F" in text


def test_build_aggregate_prompt_omits_forbidden_facts():
    sliders = ToneSliders(anger=3, niceness=6, respect=7, detail=6, local=3)
    agg = AggregateComplaintContext(
        airport_name="Vance Brand", airport_icao="KLMO", user_location_label="Longmont, CO",
        time_window_label="in the last hour", observed_from="6:03 AM", observed_to="7:15 AM",
        aircraft_count=2, callsigns=["N4632F", "N771CJ"], total_circles=20,
        total_touch_and_gos=14, total_low_approaches=6, total_passes=5,
        avg_altitude_user=1100, min_altitude_user=600, airport_elevation_ft=5055,
        previous_report_total=0, items=[_ctx(), _ctx(callsign="N771CJ", icao24="abc999")],
        peak_db_at_home=54.0,
    )
    text = build_aggregate_prompt(agg, sliders)
    assert "elevation" not in text.lower()
    assert "total_circles" not in text
    assert "5055" not in text
    assert "total_touch_and_go_count: 14" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q -k "system_prompt_carries or omits"`
Expected: FAIL (asserts don't hold against current prompt/persona)

- [ ] **Step 3: Rewrite `DEFAULT_SYSTEM_PROMPT`**

In `backend/app/llm.py` replace line 277:
```python
DEFAULT_SYSTEM_PROMPT = "You produce factual, civil aviation noise complaint descriptions. Use 12-hour AM/PM time, never military time."
```
with:
```python
DEFAULT_SYSTEM_PROMPT = (
    "You write a short aviation noise complaint that a resident will paste into an official form. "
    "Write 3 to 8 sentences in the first person, with length governed by the detail tone control. "
    "Focus entirely on how disruptive the aircraft is to people on the ground: its repetitive "
    "touch-and-go landings, the noise it makes, and how low it flies over the resident's home "
    "(call out the lowest pass). Lead with the aircraft's N-number when one is provided. "
    "Do not mention runway or airport field elevation. Do not mention the number of circles or the "
    "loop radius. Do not mention runway changes unless the DATA explicitly flags an unwarranted, "
    "against-the-wind change. Use specific numbers and 12-hour AM/PM local time; never use 24-hour "
    "or military time. Never speculate about the pilot's intent. Never use profanity or personal "
    "attacks. Blend the five tone controls into one coherent voice."
)
```

- [ ] **Step 4: Rewrite `build_prompt`**

Replace `build_prompt` (lines 91-138) with:

```python
def build_prompt(context: ComplaintContext, sliders: ToneSliders) -> str:
    bands = prompt_bands(sliders)
    return f"""Write the complaint described by your instructions using only the facts below.

TONE CONTROLS:
anger: {bands["anger"]}
niceness: {bands["niceness"]}
respect: {bands["respect"]}
detail: {bands["detail"]}
local_flavor: {bands["local"]}

DATA:
airport: {context.airport_name} ({context.airport_icao})
user_location_label: {context.user_location_label}
time_window_label: {context.time_window_label}
aircraft_n_number: {context.callsign}
aircraft_type: {context.aircraft_type}
observed_from: {context.observed_from}
observed_to: {context.observed_to}
touch_and_go_count: {context.touch_and_gos}
passes_over_user_count: {context.passes}
avg_altitude_over_user_ft_agl: {context.avg_altitude_user if context.avg_altitude_user is not None else "unknown"}
lowest_altitude_over_user_ft_agl: {context.min_altitude_user if context.min_altitude_user is not None else "unknown"}
peak_db_at_home: {f"{context.peak_db_at_home:.1f}" if context.peak_db_at_home is not None else "unknown"}
quiet_hours_events: {context.quiet_hours_events}
peak_hours: {context.peak_hours}
origin_airport: {context.origin_airport}
previous_reports_by_this_user_for_aircraft: {context.previous_report_count}
"""
```

- [ ] **Step 5: Rewrite `build_aggregate_prompt`**

Replace `build_aggregate_prompt` (lines 141-196) with:

```python
def build_aggregate_prompt(context: AggregateComplaintContext, sliders: ToneSliders) -> str:
    bands = prompt_bands(sliders)
    aircraft_lines = []
    for item in context.items:
        aircraft_lines.append(
            "; ".join([
                item.callsign,
                f"origin {item.origin_airport}",
                f"touch_and_gos {item.touch_and_gos}",
                f"passes {item.passes}",
                f"lowest_over_house_ft_agl {item.min_altitude_user if item.min_altitude_user is not None else 'unknown'}",
                f"previous_reports {item.previous_report_count}",
            ])
        )
    return f"""You write one consolidated aviation noise complaint for a resident to paste into an official form.
Do not write separate sections per aircraft. Produce one coherent complaint that summarizes all aircraft together, following your instructions.

TONE CONTROLS:
anger: {bands["anger"]}
niceness: {bands["niceness"]}
respect: {bands["respect"]}
detail: {bands["detail"]}
local_flavor: {bands["local"]}

AGGREGATE DATA:
airport: {context.airport_name} ({context.airport_icao})
user_location_label: {context.user_location_label}
time_window_label: {context.time_window_label}
observed_from: {context.observed_from}
observed_to: {context.observed_to}
aircraft_count: {context.aircraft_count}
n_numbers: {", ".join(context.callsigns)}
total_touch_and_go_count: {context.total_touch_and_gos}
total_passes_over_user_count: {context.total_passes}
avg_altitude_over_user_ft_agl: {context.avg_altitude_user if context.avg_altitude_user is not None else "unknown"}
lowest_altitude_over_user_ft_agl: {context.min_altitude_user if context.min_altitude_user is not None else "unknown"}
peak_db_at_home: {f"{context.peak_db_at_home:.1f}" if context.peak_db_at_home is not None else "unknown"}
previous_reports_by_this_user_total: {context.previous_report_total}

AIRCRAFT DETAILS:
{chr(10).join(aircraft_lines)}
"""
```

- [ ] **Step 6: Raise the Groq timeout**

In `generate_with_groq` change line 302 `timeout=4.0` to `timeout=8.0` (custom prompts should reach the model, not fall through to the deterministic draft). Update the comment above it to say the endpoint still falls through on timeout, now at 8s.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add backend/app/llm.py backend/tests/test_report_prompt.py
git commit -m "feat(reports): move persona to system prompt, trim DATA to noise facts"
```

---

### Task 4: Only mention runway changes when unwarranted

`runway_change_note` emits a "none recorded" line even when there are no unwarranted changes, and it is always appended. Return empty when there are none, and append only when non-empty.

**Files:**
- Modify: `backend/app/llm.py:280-292` (`runway_change_note`)
- Modify: `backend/app/services.py` (append sites ~2001/2024 and ~2156/2179)
- Test: `backend/tests/test_report_prompt.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_report_prompt.py`:

```python
from app.llm import runway_change_note


def test_runway_change_note_empty_when_no_changes():
    assert runway_change_note([]) == ""


def test_runway_change_note_present_when_unwarranted():
    note = runway_change_note([{"to_runway_id": "11", "cowboy_callsign": "N9AB"}])
    assert note.startswith("runway_changes_against_the_wind:")
    assert "11" in note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q -k runway_change_note`
Expected: FAIL (`runway_change_note([])` currently returns the "none recorded" string)

- [ ] **Step 3: Return empty for no changes**

In `backend/app/llm.py` replace lines 283-284:
```python
    if not changes:
        return "runway_changes_against_the_wind: none recorded in this window"
```
with:
```python
    if not changes:
        return ""
```

- [ ] **Step 4: Append only when non-empty (both paths)**

In `backend/app/services.py` `build_description`, replace line ~2024:
```python
        prompt = build_prompt(context, sliders) + "\n" + rwy_note + "\n"
```
with:
```python
        prompt = build_prompt(context, sliders)
        if rwy_note:
            prompt += "\n" + rwy_note + "\n"
```

In `build_summary_description`, replace line ~2179:
```python
        prompt = build_aggregate_prompt(aggregate, sliders) + "\n" + rwy_note + "\n"
```
with:
```python
        prompt = build_aggregate_prompt(aggregate, sliders)
        if rwy_note:
            prompt += "\n" + rwy_note + "\n"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q -k runway_change_note`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/app/llm.py backend/app/services.py backend/tests/test_report_prompt.py
git commit -m "feat(reports): only mention runway changes when unwarranted"
```

---

### Task 5: Trim the deterministic fallback drafts

The offline fallbacks still cite circles and field elevation. Trim them to match the new content rules (touch-and-goes + how-low-over-house), and update the existing test that asserts the old behavior.

**Files:**
- Modify: `backend/app/llm.py` (`deterministic_description` 203-239; `deterministic_aggregate_description` 242-274)
- Modify: `backend/tests/test_core.py:812-849` (`test_deterministic_description_respects_message_preferences`)

- [ ] **Step 1: Update the existing test to the new behavior**

In `backend/tests/test_core.py` replace the assertions block (lines 845-849):
```python
    text = deterministic_description(context)
    assert "previously reported this same aircraft 2 complaints" in text
    assert "circling 4 times" in text
    assert "950 ft" not in text
    assert "5673" not in text
```
with:
```python
    text = deterministic_description(context)
    assert "previously reported this same aircraft 2 complaints" in text
    assert "touch-and-go" in text
    assert "circling" not in text
    assert "circles" not in text
    assert "5673" not in text
    assert "elevation" not in text.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_core.py -q -k deterministic_description`
Expected: FAIL (current output contains "circling" / "circles")

- [ ] **Step 3: Trim `deterministic_description`**

Replace `deterministic_description` (lines 203-239) with:

```python
def deterministic_description(context: ComplaintContext) -> str:
    parts = [
        f"{context.time_window_label.capitalize()}, aircraft {context.callsign} was observed conducting "
        f"repetitive pattern work near {context.airport_name} ({context.airport_icao}).",
    ]
    if context.previous_report_count > 0:
        plural = "complaint" if context.previous_report_count == 1 else "complaints"
        parts.append(f"I have previously reported this same aircraft {context.previous_report_count} {plural}.")
    parts.append(
        f"It performed {context.touch_and_gos} touch-and-go operations and made "
        f"{context.passes} direct overflights of my location."
    )
    if context.min_altitude_user is not None:
        parts.append(f"The lowest overflight of my home was about {context.min_altitude_user} ft above ground.")
    elif context.avg_altitude_user is not None:
        parts.append(f"The average overflight of my home was about {context.avg_altitude_user} ft above ground.")
    if context.peak_db_at_home is not None:
        parts.append(
            f"Based on the recorded altitudes and positions, the peak estimated noise at my home reached "
            f"{context.peak_db_at_home:.1f} dB."
        )
    if context.observed_from != "unknown" and context.observed_to != "unknown":
        parts.append(f"The activity ran from {context.observed_from} to {context.observed_to} local time.")
    parts.append("Please review this repetitive low-altitude activity and consider noise-abatement outreach.")
    return " ".join(parts)
```

- [ ] **Step 4: Trim `deterministic_aggregate_description`**

Replace `deterministic_aggregate_description` (lines 242-274) with (keep the "{count} aircraft" sentence — an existing test at `test_core.py:487` asserts `"2 aircraft" in result["text"]`):

```python
def deterministic_aggregate_description(context: AggregateComplaintContext) -> str:
    callsigns = ", ".join(context.callsigns[:8])
    parts = [
        f"{context.time_window_label.capitalize()}, I observed {context.aircraft_count} aircraft conducting "
        f"repetitive pattern work near {context.airport_name} ({context.airport_icao}): {callsigns}.",
    ]
    if context.previous_report_total > 0:
        plural = "complaint" if context.previous_report_total == 1 else "complaints"
        parts.append(f"My browser records show {context.previous_report_total} prior {plural} for aircraft in this group.")
    parts.append(
        f"Together they made {context.total_touch_and_gos} touch-and-go operations and "
        f"{context.total_passes} direct overflights of my location."
    )
    if context.min_altitude_user is not None:
        parts.append(f"The lowest overflight of my home was about {context.min_altitude_user} ft above ground.")
    if context.peak_db_at_home is not None:
        parts.append(f"Across these aircraft the peak estimated noise at my home reached {context.peak_db_at_home:.1f} dB.")
    if context.observed_from != "unknown" and context.observed_to != "unknown":
        parts.append(f"The activity ran from {context.observed_from} to {context.observed_to} local time.")
    parts.append("Please review this combined low-altitude activity and consider noise-abatement follow-up.")
    return " ".join(parts)
```

- [ ] **Step 5: Run the report tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_core.py -q -k "deterministic or summary"`
Expected: PASS (updated deterministic test + the `build_summary_description` fallback test both green)

- [ ] **Step 6: Commit**

```bash
git add backend/app/llm.py backend/tests/test_core.py
git commit -m "feat(reports): trim offline drafts to touch-and-go and low-altitude facts"
```

---

### Task 6: Cap the multi-aircraft report at 10

Sending >10 aircraft confuses Groq. Cap the incoming list to 10 at the top of `build_summary_description` (the frontend sends them worst-first, so the first 10 are the worst 10), and record the original count in metadata.

**Files:**
- Modify: `backend/app/services.py` (`build_summary_description` ~2062-2084; metadata block ~2191-2208)
- Test: `backend/tests/test_report_prompt.py` (append — pure helper)

**Interfaces:**
- Produces: `services.MAX_SUMMARY_AIRCRAFT = 10`; `services.cap_summary_aircraft(icao24s: list[str]) -> list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_report_prompt.py`:

```python
def test_cap_summary_aircraft_keeps_first_ten():
    assert services.MAX_SUMMARY_AIRCRAFT == 10
    ids = [f"h{i}" for i in range(25)]
    assert services.cap_summary_aircraft(ids) == ids[:10]
    assert services.cap_summary_aircraft(["a", "b"]) == ["a", "b"]
    assert services.cap_summary_aircraft([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q -k cap_summary`
Expected: FAIL (`AttributeError: ... 'cap_summary_aircraft'`)

- [ ] **Step 3: Add the constant + helper and cap the list**

In `backend/app/services.py`, add near the other report top-level definitions (just above `def build_summary_description`):
```python
MAX_SUMMARY_AIRCRAFT = 10


def cap_summary_aircraft(icao24s: list[str]) -> list[str]:
    """Groq gets confused by too many aircraft; keep at most MAX_SUMMARY_AIRCRAFT.
    The frontend sends them worst-first, so the kept ones are the worst offenders."""
    return list(icao24s)[:MAX_SUMMARY_AIRCRAFT]
```

Inside `build_summary_description`, right after `prefs = message_preferences or MessagePreferences()` (line ~2070) add:
```python
    requested_count = len(icao24s)
    icao24s = cap_summary_aircraft(icao24s)
```

In the returned `metadata` dict (after `"aircraft_count": aggregate.aircraft_count,` ~2194) add (compare against the cap, not `aircraft_count`, which also shrinks when aircraft are skipped for having no activity):
```python
            "requested_aircraft_count": requested_count,
            "truncated": requested_count > MAX_SUMMARY_AIRCRAFT,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_report_prompt.py -q -k cap_summary && .venv/bin/python -m pytest tests/test_core.py -q -k summary`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services.py backend/tests/test_report_prompt.py
git commit -m "feat(reports): cap consolidated complaint at 10 aircraft"
```

---

# Phase 2 — Worst offenders + online count (backend)

### Task 7: `nearest_airport_excluding`

`nearest_airport` returns the airport itself for a same-airport query. Add a variant that excludes a given ICAO, for the worst-offenders fallback.

**Files:**
- Modify: `backend/app/db.py` (add after `nearest_airport`, ~line 1079)
- Test: `backend/tests/test_stats.py` (append) — or new `backend/tests/test_worst_offenders.py`

**Interfaces:**
- Produces: `db.nearest_airport_excluding(conn, lat: float, lon: float, exclude_icao: str) -> dict | None` (same dict shape as `nearest_airport`, includes `distance_nm`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_worst_offenders.py`:

```python
from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_nearest_airport_excluding_skips_source(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    klmo = db.get_airport(conn, "KLMO")
    out = db.nearest_airport_excluding(conn, klmo.lat, klmo.lon, "KLMO")
    assert out is not None
    assert out["icao"] != "KLMO"
    assert "distance_nm" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q -k nearest`
Expected: FAIL (`AttributeError: ... 'nearest_airport_excluding'`)

- [ ] **Step 3: Implement**

In `backend/app/db.py` add after `nearest_airport` (after line 1078):

```python
def nearest_airport_excluding(conn: sqlite3.Connection, lat: float, lon: float, exclude_icao: str) -> dict | None:
    """Nearest seeded airport to (lat, lon) that is NOT `exclude_icao`.

    Same coarse bbox prefilter as nearest_airport(); used for the worst-offenders
    fallback where the source airport has no scored aircraft."""
    exclude = exclude_icao.upper()
    point = Point(lat, lon)
    for box_deg in (0.6, 2.0, 8.0, 180.0):
        rows = conn.execute(
            """
            SELECT * FROM airports
            WHERE lat BETWEEN ? AND ?
              AND lon BETWEEN ? AND ?
              AND icao != ?
            """,
            (lat - box_deg, lat + box_deg, lon - box_deg, lon + box_deg, exclude),
        ).fetchall()
        if rows:
            candidates = [row_to_airport(row) for row in rows]
            nearest = min(candidates, key=lambda a: distance_nm(point, Point(a.lat, a.lon)))
            data = airport_to_dict(nearest)
            data["distance_nm"] = round(distance_nm(point, Point(nearest.lat, nearest.lon)), 2)
            return data
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q -k nearest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_worst_offenders.py
git commit -m "feat(db): nearest_airport_excluding for worst-offenders fallback"
```

---

### Task 8: `report_meta` batch lookup

Worst-offender cards need each aircraft's lifetime report count + last-reported time. Add a batch lookup keyed by icao24.

**Files:**
- Modify: `backend/app/db.py` (add near `top_repeat_offenders`, ~line 1541)
- Test: `backend/tests/test_worst_offenders.py` (append)

**Interfaces:**
- Produces: `db.report_meta(conn, icao24s: list[str]) -> dict[str, dict]` → `{icao24: {"report_count": int, "last_reported_at": int}}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_worst_offenders.py`:

```python
def test_report_meta_returns_counts_and_last_seen(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    conn.execute(
        "INSERT INTO aircraft_report_counts (icao24, callsign, registration, report_count, first_reported_at, last_reported_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("a5a764", "N4632F", "N4632F", 5, 1780000000, 1780009999),
    )
    conn.commit()
    meta = db.report_meta(conn, ["a5a764", "ffffff"])
    assert meta["a5a764"]["report_count"] == 5
    assert meta["a5a764"]["last_reported_at"] == 1780009999
    assert "ffffff" not in meta


def test_report_meta_empty_list(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    assert db.report_meta(conn, []) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q -k report_meta`
Expected: FAIL (`AttributeError: ... 'report_meta'`)

- [ ] **Step 3: Implement**

In `backend/app/db.py` add after `top_repeat_offenders` (after line 1540):

```python
def report_meta(conn: sqlite3.Connection, icao24s: list[str]) -> dict[str, dict]:
    """Lifetime report_count + last_reported_at for each icao24 (lowercased)."""
    ids = [i.lower() for i in icao24s]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT icao24, report_count, last_reported_at FROM aircraft_report_counts WHERE icao24 IN ({placeholders})",
        ids,
    ).fetchall()
    return {
        r["icao24"]: {"report_count": r["report_count"], "last_reported_at": r["last_reported_at"]}
        for r in rows
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q -k report_meta`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_worst_offenders.py
git commit -m "feat(db): report_meta batch lookup for offender cards"
```

---

### Task 9: `rank_worst_offenders` pure ranking

Pure function that turns a VNAP `aircraft` list + report meta into the top-N worst offenders by `vnap_score × circles`, each with its single worst axis.

**Files:**
- Modify: `backend/app/services.py` (add near the other report helpers)
- Test: `backend/tests/test_worst_offenders.py` (append)

**Interfaces:**
- Consumes: VNAP `aircraft` dicts (keys `icao24, callsign, registration, tail, vnap_score, circles, scores, aircraft_type, owner_class`), `db.report_meta` output, `resolve_display_tail`.
- Produces: `services.rank_worst_offenders(aircraft: list[dict], meta: dict[str, dict], limit: int) -> list[dict]` where each item is `{icao24, tail, total_circles, vnap_score, product, report_count, last_reported_at, worst_axis, worst_axis_score, aircraft_type, owner_class}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_worst_offenders.py`:

```python
from app import services


def _vac(icao24, tail, vnap, circles, scores):
    return {
        "icao24": icao24, "callsign": tail, "registration": tail, "tail": tail,
        "aircraft_type": "Cessna 172", "owner_class": "flight_school",
        "vnap_score": vnap, "circles": circles, "scores": scores,
    }


def test_rank_worst_offenders_orders_by_vnap_times_circles():
    aircraft = [
        _vac("a1", "N1", 40.0, 2, {"altitude": 40.0, "timeofday": 10.0, "tightness": None}),   # 80
        _vac("a2", "N2", 20.0, 10, {"altitude": 5.0, "timeofday": 20.0, "tightness": None}),   # 200
        _vac("a3", "N3", 0.0, 50, {"altitude": None, "timeofday": None, "tightness": None}),   # 0 -> dropped
    ]
    meta = {"a2": {"report_count": 7, "last_reported_at": 1780009999}}
    out = services.rank_worst_offenders(aircraft, meta, limit=5)
    assert [o["icao24"] for o in out] == ["a2", "a1"]  # a3 (product 0) dropped
    assert out[0]["total_circles"] == 10
    assert out[0]["report_count"] == 7
    assert out[0]["last_reported_at"] == 1780009999
    assert out[0]["worst_axis"] == "timeofday"          # 20 > 5
    assert out[1]["worst_axis"] == "altitude"           # 40 > 10
    assert out[1]["report_count"] == 0                  # no meta -> 0


def test_rank_worst_offenders_respects_limit():
    aircraft = [_vac(f"a{i}", f"N{i}", 50.0, i + 1, {"altitude": 50.0}) for i in range(8)]
    out = services.rank_worst_offenders(aircraft, {}, limit=5)
    assert len(out) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q -k rank_worst`
Expected: FAIL (`AttributeError: ... 'rank_worst_offenders'`)

- [ ] **Step 3: Implement**

In `backend/app/services.py` add (near `system_prompt_fingerprint`, above `complaint_context`):

```python
def rank_worst_offenders(aircraft: list[dict], meta: dict[str, dict], limit: int) -> list[dict]:
    """Top-N offenders by VNAP violation score x circles, each with its worst axis.

    Aircraft with a zero product (below the VNAP scoring gate, or no circles) drop out."""
    ranked = []
    for ac in aircraft:
        vnap = ac.get("vnap_score") or 0.0
        circles = ac.get("circles") or 0
        product = vnap * circles
        if product <= 0:
            continue
        scores = ac.get("scores") or {}
        scored = [(axis, val) for axis, val in scores.items() if val is not None]
        worst_axis, worst_val = max(scored, key=lambda kv: kv[1]) if scored else (None, None)
        m = meta.get(ac["icao24"], {})
        ranked.append({
            "icao24": ac["icao24"],
            "tail": resolve_display_tail(ac.get("callsign"), ac.get("registration"), ac["icao24"]),
            "total_circles": circles,
            "vnap_score": round(vnap, 1),
            "product": round(product, 1),
            "report_count": m.get("report_count", 0) or 0,
            "last_reported_at": m.get("last_reported_at"),
            "worst_axis": worst_axis,
            "worst_axis_score": round(worst_val, 1) if worst_val is not None else None,
            "aircraft_type": ac.get("aircraft_type"),
            "owner_class": ac.get("owner_class"),
        })
    ranked.sort(key=lambda o: o["product"], reverse=True)
    return ranked[: max(1, int(limit))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q -k rank_worst`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services.py backend/tests/test_worst_offenders.py
git commit -m "feat(offenders): rank worst offenders by VNAP x circles with worst axis"
```

---

### Task 10: `build_worst_offenders` + `GET /airports/{icao}/worst_offenders`

Tie VNAP compute + report meta + nearest fallback into a builder, and expose it.

**Files:**
- Modify: `backend/app/services.py` (add `build_worst_offenders`)
- Modify: `backend/app/main.py` (new route near the stats route ~1365)
- Test: `backend/tests/test_worst_offenders.py` (append, uses TestClient)

**Interfaces:**
- Consumes: `vnap.compute_aircraft_compliance`, `db.get_airport`, `db.report_meta`, `db.nearest_airport_excluding`, `rank_worst_offenders`.
- Produces: `services.build_worst_offenders(conn, icao: str, *, now: int, limit: int = 5) -> dict` → `{source_icao, resolved_icao, resolved_label, is_fallback, offenders: list}`. Endpoint `GET /airports/{icao}/worst_offenders?limit=5`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_worst_offenders.py` (reuse the `_op` operation seeder pattern from `test_vnap_compliance.py`):

```python
from fastapi.testclient import TestClient
from app.main import app
from app.settings import get_settings


def _seed_klmo_offender(conn):
    # One clear offender at KLMO: enough T&G + circles to clear the VNAP gate,
    # low passes over homes and quiet-hours ops to push the violation score up.
    base = 1780000000
    def op(oid, type_, ts, min_agl=None, turn=None, runway=None):
        db.upsert_operation(conn, db.operation_from_event({
            "id": oid, "type": type_, "icao24": "a5a764", "callsign": "N4632F",
            "timestamp": ts, "airport_icao": "KLMO", "runway_id": runway,
            "turn_direction": turn, "min_altitude_ft_agl": min_agl,
        }))
    for i in range(6):
        op(f"g{i}", "touch_and_go", base + i * 10, turn="right")
    for i in range(6):
        op(f"c{i}", "circle", base + 100 + i * 10, turn="right")
    for i in range(4):
        op(f"p{i}", "pass_over_user", base + 200 + i * 10, min_agl=150)  # very low -> altitude violation
    conn.commit()


def test_build_worst_offenders_ranks_and_reports(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _seed_klmo_offender(conn)
    out = services.build_worst_offenders(conn, "KLMO", now=1780000000 + 100000, limit=5)
    assert out["resolved_icao"] == "KLMO"
    assert out["is_fallback"] is False
    assert out["offenders"], "expected at least one scored offender"
    top = out["offenders"][0]
    assert top["tail"] == "N4632F"
    assert top["total_circles"] == 6
    assert top["worst_axis"] is not None


def test_worst_offenders_endpoint_and_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    with TestClient(app) as client:
        # Seed the offender into the app's DB via a direct connection.
        from app.db import connect as _connect
        conn = _connect(str(tmp_path / "circlejerk.sqlite3"))
        _seed_klmo_offender(conn)
        conn.close()

        ok = client.get("/airports/KLMO/worst_offenders", params={"limit": 5})
        assert ok.status_code == 200
        assert ok.json()["resolved_icao"] == "KLMO"
        assert len(ok.json()["offenders"]) >= 1

        # KBJC has no ops -> falls back to the nearest OTHER airport that does.
        fb = client.get("/airports/KBJC/worst_offenders", params={"limit": 5})
        assert fb.status_code == 200
        body = fb.json()
        assert body["source_icao"] == "KBJC"
        assert body["is_fallback"] is True
        assert body["resolved_icao"] != "KBJC"

        missing = client.get("/airports/ZZZZ/worst_offenders")
        assert missing.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q -k "build_worst or endpoint"`
Expected: FAIL (`AttributeError: ... 'build_worst_offenders'` / 404 route)

- [ ] **Step 3: Implement the builder**

In `backend/app/services.py` add:

```python
def _worst_offenders_for(conn, icao: str, now: int, limit: int) -> list[dict]:
    vnap_result = vnap.compute_aircraft_compliance(conn, icao, 0, now)
    aircraft = vnap_result.get("aircraft", [])
    meta = db.report_meta(conn, [a["icao24"] for a in aircraft])
    return rank_worst_offenders(aircraft, meta, limit)


def build_worst_offenders(conn, icao: str, *, now: int, limit: int = 5) -> dict:
    icao = icao.upper()
    airport = db.get_airport(conn, icao)
    if not airport:
        raise KeyError(f"unknown airport {icao}")
    offenders = _worst_offenders_for(conn, icao, now, limit)
    resolved_icao = icao
    resolved_label = airport.city or airport.name or icao
    is_fallback = False
    if not offenders:
        alt = db.nearest_airport_excluding(conn, airport.lat, airport.lon, icao)
        if alt:
            resolved_icao = alt["icao"]
            resolved_label = alt.get("city") or alt.get("name") or alt["icao"]
            is_fallback = True
            offenders = _worst_offenders_for(conn, resolved_icao, now, limit)
    return {
        "source_icao": icao,
        "resolved_icao": resolved_icao,
        "resolved_label": resolved_label,
        "is_fallback": is_fallback,
        "offenders": offenders,
    }
```

- [ ] **Step 4: Add the endpoint**

`main.py` imports report functions directly (line 35: `from .services import build_description, build_scan_response, build_summary_description`) — there is no `services.` qualifier used anywhere. Add `build_worst_offenders` to that import:
```python
from .services import build_description, build_scan_response, build_summary_description, build_worst_offenders
```

Then add after the `get_airport_stats` route (after line 1380):

```python
@app.get("/airports/{icao}/worst_offenders")
async def get_worst_offenders(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
):
    now = int(time.time())
    try:
        with db_session(settings.database_path) as conn:
            return build_worst_offenders(conn, icao, now=now, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
```

(`Query`, `Annotated`, `Depends`, `Settings`, `settings_dep`, `db_session`, `HTTPException`, `time` are all already imported — used by `get_airport_stats`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_worst_offenders.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services.py backend/app/main.py backend/tests/test_worst_offenders.py
git commit -m "feat(offenders): worst_offenders endpoint with nearest-airport fallback"
```

---

### Task 11: Public online-users count

Expose the existing 90-second active-visitor count (currently admin-only) at a public endpoint.

**Files:**
- Modify: `backend/app/db.py` (add `online_visitor_count`)
- Modify: `backend/app/main.py` (new public route near `/activity/heartbeat` ~588)
- Test: `backend/tests/test_api.py` (append)

**Interfaces:**
- Produces: `db.online_visitor_count(conn, *, now: int, active_window_seconds: int) -> int`; endpoint `GET /activity/online` → `{"count": int}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api.py`:

```python
def test_activity_online_counts_recent_visitors(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    with TestClient(app) as client:
        empty = client.get("/activity/online")
        assert empty.status_code == 200
        assert empty.json()["count"] == 0

        client.post("/activity/heartbeat", json={"visitor_id": "visitor-abc123", "path": "/"})
        client.post("/activity/heartbeat", json={"visitor_id": "visitor-def456", "path": "/"})

        after = client.get("/activity/online")
        assert after.json()["count"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py -q -k activity_online`
Expected: FAIL (404 — route missing)

- [ ] **Step 3: Add the DB helper**

In `backend/app/db.py` add near `admin_dashboard` (just above it, ~line 1565):

```python
def online_visitor_count(conn: sqlite3.Connection, *, now: int, active_window_seconds: int) -> int:
    """Distinct-visitor rows seen within the active window (currently online)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM visitor_activity WHERE last_seen >= ?",
        (now - active_window_seconds,),
    ).fetchone()
    return int(row["n"] if row else 0)
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/main.py` add after the `activity_heartbeat` route (after line 607):

```python
@app.get("/activity/online")
async def activity_online(settings: Annotated[Settings, Depends(settings_dep)]):
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        count = db.online_visitor_count(
            conn, now=now, active_window_seconds=settings.active_user_window_seconds
        )
    return {"count": count}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py -q -k activity_online`
Expected: PASS

- [ ] **Step 6: Run the whole backend suite (guard against regressions)**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: PASS (no failures). If any pre-existing report test asserts removed content, fix it to match the new rules.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py backend/app/main.py backend/tests/test_api.py
git commit -m "feat(activity): public online-visitor count endpoint"
```

---

# Phase 3 — Frontend

### Task 12: API client — worst offenders, online count

**Files:**
- Modify: `frontend/src/lib/api.ts` (add types + fetchers near `getRepeatOffenders` ~398)
- Test: `frontend/src/lib/api.test.ts` (new, mirrors `statsApi.test.ts`)

**Interfaces:**
- Produces: `WorstOffender`, `WorstOffendersResponse`, `getWorstOffenders(icao, limit?)`, `OnlineResponse`, `getOnlineCount()`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { getWorstOffenders, getOnlineCount } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("worst offenders + online", () => {
  it("requests the worst_offenders path with limit", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ offenders: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getWorstOffenders("KLMO", 5);
    expect((fn.mock.calls as unknown[][])[0][0]).toContain("/api/airports/KLMO/worst_offenders?limit=5");
  });

  it("requests the online count path", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ count: 3 }) } as Response));
    vi.stubGlobal("fetch", fn);
    const out = await getOnlineCount();
    expect((fn.mock.calls as unknown[][])[0][0]).toContain("/api/activity/online");
    expect(out.count).toBe(3);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- api.test`
Expected: FAIL (`getWorstOffenders`/`getOnlineCount` not exported)

- [ ] **Step 3: Implement**

In `frontend/src/lib/api.ts` add after `getRepeatOffenders` (after line 400):

```ts
export interface WorstOffender {
  icao24: string;
  tail: string;
  total_circles: number;
  vnap_score: number;
  product: number;
  report_count: number;
  last_reported_at: number | null;
  worst_axis: string | null;
  worst_axis_score: number | null;
  aircraft_type?: string | null;
  owner_class?: string | null;
}

export interface WorstOffendersResponse {
  source_icao: string;
  resolved_icao: string;
  resolved_label: string;
  is_fallback: boolean;
  offenders: WorstOffender[];
}

export function getWorstOffenders(icao: string, limit = 5) {
  return getJson<WorstOffendersResponse>(`/airports/${icao}/worst_offenders?limit=${limit}`);
}

export interface OnlineResponse {
  count: number;
}

export function getOnlineCount() {
  return getJson<OnlineResponse>("/activity/online");
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- api.test`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/api.test.ts
git commit -m "feat(api): worst offenders + online count clients"
```

---

### Task 13: Report target helpers (worst-10 truncation + habit labels)

Pure helpers the report effect and cards use.

**Files:**
- Create: `frontend/src/lib/reportTargets.ts`
- Test: `frontend/src/lib/reportTargets.test.ts`

**Interfaces:**
- Produces: `worstOffenderTargets<T extends {vnap_score?: number|null; circles: number}>(offenders: T[], limit?: number): T[]`; `HABIT_LABELS: Record<string,string>`; `habitLabel(axis: string | null): string`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/reportTargets.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { worstOffenderTargets, habitLabel } from "./reportTargets";

describe("worstOffenderTargets", () => {
  it("keeps the worst N by vnap_score * circles", () => {
    const rows = [
      { icao24: "a", vnap_score: 40, circles: 2 },  // 80
      { icao24: "b", vnap_score: 20, circles: 10 }, // 200
      { icao24: "c", vnap_score: 90, circles: 1 },  // 90
    ];
    expect(worstOffenderTargets(rows, 2).map((r) => r.icao24)).toEqual(["b", "c"]);
  });

  it("treats null vnap_score as 0 and does not mutate input", () => {
    const rows = [{ icao24: "a", vnap_score: null, circles: 9 }, { icao24: "b", vnap_score: 10, circles: 1 }];
    const copy = [...rows];
    expect(worstOffenderTargets(rows, 1).map((r) => r.icao24)).toEqual(["b"]);
    expect(rows).toEqual(copy);
  });
});

describe("habitLabel", () => {
  it("maps axes to human phrases with a fallback", () => {
    expect(habitLabel("altitude")).toMatch(/low/i);
    expect(habitLabel("timeofday")).toMatch(/quiet/i);
    expect(habitLabel(null)).toBe("Repeat pattern flyer");
    expect(habitLabel("unknown_axis")).toBe("Repeat pattern flyer");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- reportTargets`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement**

Create `frontend/src/lib/reportTargets.ts`:

```ts
export function worstOffenderTargets<T extends { vnap_score?: number | null; circles: number }>(
  offenders: T[],
  limit = 10,
): T[] {
  return [...offenders]
    .sort((a, b) => (b.vnap_score ?? 0) * b.circles - (a.vnap_score ?? 0) * a.circles)
    .slice(0, limit);
}

export const HABIT_LABELS: Record<string, string> = {
  tightness: "Sloppy, wide patterns",
  altitude: "Flies too low over homes",
  timeofday: "Flies during quiet hours",
  tg_volume: "Relentless touch-and-goes",
  circle_restraint: "Endless pattern loops",
  left_traffic: "Wrong-way traffic",
  runway29: "Ignores the noise-preferred runway",
};

export function habitLabel(axis: string | null): string {
  if (!axis) return "Repeat pattern flyer";
  return HABIT_LABELS[axis] ?? "Repeat pattern flyer";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- reportTargets`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/reportTargets.ts frontend/src/lib/reportTargets.test.ts
git commit -m "feat(reports): worst-10 target selection and habit labels"
```

---

### Task 14: Tagline pool + picker

**Files:**
- Create: `frontend/src/lib/taglines.ts`
- Test: `frontend/src/lib/taglines.test.ts`

**Interfaces:**
- Produces: `buildTaglines(circlesToday: number | null): string[]`; `pickTagline(list: string[], rand?: number): string`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/taglines.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildTaglines, pickTagline } from "./taglines";

describe("buildTaglines", () => {
  it("omits the dynamic line when there is no count", () => {
    const list = buildTaglines(null);
    expect(list.length).toBeGreaterThan(0);
    expect(list.some((t) => /logged today/.test(t))).toBe(false);
  });

  it("includes a count-driven line when circles > 0", () => {
    const list = buildTaglines(300);
    expect(list.some((t) => t.includes("300") && /logged today/.test(t))).toBe(true);
  });
});

describe("pickTagline", () => {
  it("is deterministic given rand and always in range", () => {
    const list = ["a", "b", "c"];
    expect(pickTagline(list, 0)).toBe("a");
    expect(pickTagline(list, 0.99)).toBe("c");
    expect(pickTagline([], 0.5)).toBe("");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- taglines`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement**

Create `frontend/src/lib/taglines.ts`:

```ts
export function buildTaglines(circlesToday: number | null): string[] {
  const base = [
    "Pattern training loops give us no local benefit, only noise.",
    "Imagine a biker gang circling your block around your house — would that annoy you?",
    "Monitoring 16,000+ airports for abuse.",
    "Small engines, big egos. The 0.0001% who own the sky and 80% of the noise.",
    "Your quiet afternoon, their practice runway.",
  ];
  if (circlesToday && circlesToday > 0) {
    base.push(`${circlesToday} training loops logged today — when you can't hold a conversation outdoors anymore.`);
    base.push(`${circlesToday} circles logged overhead today, and not one of them landing for good.`);
  }
  return base;
}

export function pickTagline(list: string[], rand: number = Math.random()): string {
  if (list.length === 0) return "";
  return list[Math.min(list.length - 1, Math.floor(rand * list.length))];
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- taglines`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/taglines.ts frontend/src/lib/taglines.test.ts
git commit -m "feat(ui): tagline pool with count-driven variants"
```

---

### Task 15: Worst-offenders cards (replace the global leaderboard)

Replace the existing global `RepeatOffendersSection` (ranked by total circles, all airports) with airport-scoped VNAP×circles cards: top 5, 🤡 on #1, total circles, reports, last reported, and the single worst-habit callout. Falls back to the nearest airport.

**Files:**
- Modify: `frontend/src/App.tsx` (imports; state + fetch effect; swap the section at 780; remove the old `RepeatOffendersSection` component 798-868 and its `repeatOffenders` state/fetch)
- Modify: `frontend/src/styles.css` (reuse `.offender-*`; add `.offender-habit`, `.offender-clown`, `.worst-offenders-fallback`)

**Interfaces:**
- Consumes: `getWorstOffenders`, `WorstOffendersResponse` (Task 12); `habitLabel` (Task 13); existing `formatLocalTime`.

- [ ] **Step 1: Remove the old global leaderboard wiring**

In `frontend/src/App.tsx`:
- Line 28: remove `getRepeatOffenders,` from the api import.
- Line 44: remove `type RepeatOffender,` from the api import (it becomes unused).
- Line 135: remove `const [repeatOffenders, setRepeatOffenders] = useState<RepeatOffender[]>([]);`.
- Replace the decorative-sections effect (the whole block, lines 209-237) — this drops `getRepeatOffenders`/`setRepeatOffenders` while keeping sponsors + liveStatus intact:

```tsx
  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [s, ls] = await Promise.all([getSponsors(), getLiveStatus()]);
        if (cancelled) return;
        setSponsors(s);
        setLiveStatus(ls);
      } catch {
        // silent: these are decorative sections
      }
    }
    refresh();
    const sectionsHandle = window.setInterval(refresh, 5 * 60 * 1000);
    // Live status polls more frequently so the topbar reflects real state.
    const statusHandle = window.setInterval(() => {
      getLiveStatus()
        .then((ls) => {
          if (!cancelled) setLiveStatus(ls);
        })
        .catch(() => undefined);
    }, 30 * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(sectionsHandle);
      window.clearInterval(statusHandle);
    };
  }, []);
```
- Delete the entire `RepeatOffendersSection` function (lines ~798-868).

- [ ] **Step 2: Add worst-offenders state + fetch + new imports**

Add to the api import block (near line 28): `getWorstOffenders, type WorstOffendersResponse,`. Add to the reportTargets import (new line near other lib imports): `import { habitLabel } from "./lib/reportTargets";`.

Add state near line 135 (where `repeatOffenders` was):
```ts
  const [worstOffenders, setWorstOffenders] = useState<WorstOffendersResponse | null>(null);
```

Add a fetch effect (place it near the other airport-keyed effects, e.g. after the heartbeat effect ~425):
```ts
  useEffect(() => {
    if (!airport?.icao) {
      setWorstOffenders(null);
      return;
    }
    let cancelled = false;
    getWorstOffenders(airport.icao, 5)
      .then((data) => { if (!cancelled) setWorstOffenders(data); })
      .catch(() => { if (!cancelled) setWorstOffenders(null); });
    return () => { cancelled = true; };
  }, [airport?.icao]);
```

- [ ] **Step 3: Swap the section and add the new component**

Replace the `<RepeatOffendersSection ... />` usage (lines 780-783) with:
```tsx
      <WorstOffenderCards data={worstOffenders} />
```

Add the new component (place it where `RepeatOffendersSection` was defined):
```tsx
function WorstOffenderCards({ data }: { data: WorstOffendersResponse | null }) {
  if (!data || data.offenders.length === 0) {
    return (
      <section className="bottom-section repeat-offenders empty">
        <h2>Hall of Shame</h2>
        <p>No aircraft has flown enough abusive patterns here yet. As offenders rack up circles, the worst five show up here.</p>
      </section>
    );
  }
  return (
    <section className="bottom-section repeat-offenders">
      <header>
        <h2>Hall of Shame</h2>
        <p>
          The five worst offenders{data.is_fallback ? "" : " at this airport"}, ranked by VNAP violations × circles flown.
          {data.is_fallback && (
            <span className="worst-offenders-fallback"> No data here yet — showing the nearest airport, {data.resolved_label} ({data.resolved_icao}).</span>
          )}
        </p>
      </header>
      <ol className="offender-leaderboard">
        {data.offenders.map((row, index) => (
          <li key={row.icao24}>
            <a
              className="offender-card"
              href={statsHighlightHref(data.resolved_icao, row.icao24)}
              title="Open this aircraft in the airport stats (VNAP compliance)"
            >
              <div className="offender-rank" aria-hidden="true">
                {index === 0 ? <span className="offender-clown">🤡</span> : `#${index + 1}`}
              </div>
              <div className="offender-body">
                <div className="offender-card-head">
                  <strong>{row.tail}</strong>
                  <span className="offender-circles" title="Total circles flown at this airport">
                    {row.total_circles}× circles
                  </span>
                </div>
                <div className="offender-habit" title={`Worst VNAP axis: ${row.worst_axis ?? "n/a"} (${row.worst_axis_score ?? 0})`}>
                  {habitLabel(row.worst_axis)}
                </div>
                <div className="offender-meta">
                  {row.report_count}× reported
                  {row.last_reported_at ? ` · last reported ${formatLocalTime(row.last_reported_at)}` : ""}
                </div>
              </div>
            </a>
          </li>
        ))}
      </ol>
    </section>
  );
}
```

- [ ] **Step 4: Add the new CSS**

In `frontend/src/styles.css`, after the existing `.offender-meta` rule (search for `.offender-meta`), add:
```css
.offender-clown {
  font-size: 1.4rem;
  line-height: 1;
}

.offender-habit {
  margin-top: 4px;
  font-weight: 600;
  color: var(--red-dark);
}

.worst-offenders-fallback {
  display: block;
  margin-top: 4px;
  font-style: italic;
  color: var(--muted);
}
```

- [ ] **Step 5: Verify typecheck/build and that the old symbol is gone**

Run: `cd frontend && grep -n "RepeatOffender" src/App.tsx` → expect **no** matches (import, state, component all removed).
Run: `cd frontend && npm run build`
Expected: `tsc` passes, `vite build` succeeds. (`noUnusedLocals` is off, so this won't catch a stray unused symbol — the grep above is the real check.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(ui): airport worst-offenders cards (VNAP x circles) with fallback"
```

---

### Task 16: Live report preview + worst-10 truncation

Make the report regenerate (debounced) when the system prompt changes, and cap "all offenders" reports at the worst 10.

**Files:**
- Modify: `frontend/src/App.tsx` (`DetailPanel`: `targets` memo ~1763; effect deps ~1834; debounce ~1782/1829; status text ~1781/1795; import `worstOffenderTargets`)

- [ ] **Step 1: Truncate "all" targets to the worst 10**

In `DetailPanel`, add to the reportTargets import at the top of the file: `import { habitLabel, worstOffenderTargets } from "./lib/reportTargets";` (merge with Task 15's import).

Replace the `targets` memo (lines 1763-1766):
```ts
  const targets = useMemo(() => {
    if (complaintMode === "all") return offenders;
    return offender ? [offender] : [];
  }, [complaintMode, offender, offenders]);
```
with:
```ts
  const targets = useMemo(() => {
    if (complaintMode === "all") return worstOffenderTargets(offenders, 10);
    return offender ? [offender] : [];
  }, [complaintMode, offender, offenders]);
  const truncatedFrom = complaintMode === "all" && offenders.length > targets.length ? offenders.length : 0;
```

- [ ] **Step 2: Surface the truncation in the status line**

Replace the two "all" status strings inside the effect:
- Line ~1781 `setDetailStatus(complaintMode === "all" ? \`Generating one complaint for ${targets.length} offenders\` : "Generating description");`
- Line ~1795 `setDetailStatus(\`Generated one complaint for ${targets.length} offenders\`);`

with versions that note truncation:
```ts
      setDetailStatus(
        complaintMode === "all"
          ? `Generating one complaint for ${targets.length}${truncatedFrom ? ` of ${truncatedFrom}` : ""} offenders`
          : "Generating description",
      );
```
and
```ts
            setDetailStatus(`Generated one complaint for ${targets.length}${truncatedFrom ? ` of ${truncatedFrom}` : ""} offenders`);
```

- [ ] **Step 3: Regenerate on system-prompt change (debounced)**

Change the debounce delay from `300` to `600` at line ~1829 (`}, 300);` → `}, 600);`).

Add `systemPrompt` to the effect dependency array (line ~1834):
```ts
  }, [complaintMode, targetIdsKey, targetCountKey, refreshNonce, scanParams, sliders, messagePrefs, systemPrompt]);
```

- [ ] **Step 4: Verify build + existing lib tests**

Run: `cd frontend && npm run build && npm test -- reportTargets`
Expected: build passes; reportTargets tests green.

- [ ] **Step 5: Manual verification (dogfood)**

Start the app (`cd frontend && npm run dev` with the backend running), select an airport with offenders, open "AI system prompt (advanced)", and type. Confirm the draft re-generates ~0.6s after you stop typing (status shows "Generating…" then updated text). Switch to "All offenders" with >10 offenders and confirm the status reads "… for 10 of N offenders".

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(reports): live prompt preview + cap all-offenders report at worst 10"
```

---

### Task 17: Tagline wire-in + top bar rework

Randomize the tagline per load, remove the feed-status icon, replace the plane pill with a circle count badge, add a square online-users badge (both with tooltips), and drop the "Scanning…" status text.

**Files:**
- Modify: `frontend/src/App.tsx` (imports; tagline state/effect; header 459-479; remove `setStatus("Scanning current aircraft buffer")` at 349; remove `FeedStatusIcon` usage + definition; online-count state + poll)
- Modify: `frontend/src/styles.css` (add `.count-badge` + `.count-badge.square` + tooltip; responsive)

- [ ] **Step 1: Tagline — state + one-time upgrade with today's circle count**

Add imports near the other lib imports:
```ts
import { buildTaglines, pickTagline } from "./lib/taglines";
```
(`useRef`, `useState`, `useEffect` are already imported from `react` on line 1 — no import change needed.)

In the `App` component, add state + effect (near the tagline data, after `activeNow` ~455 is fine, but place hooks with the others near the top of the component body):
```ts
  const [tagline, setTagline] = useState<string>(() => pickTagline(buildTaglines(null)));
  const taglineChosen = useRef(false);
  useEffect(() => {
    if (taglineChosen.current) return;
    if (scanData) {
      setTagline(pickTagline(buildTaglines(scanData.counters.circles)));
      taglineChosen.current = true;
    }
  }, [scanData]);
```

Replace the render at line 467:
```tsx
            <p className="tagline">{APP_TAGLINE}</p>
```
with:
```tsx
            <p className="tagline">{tagline}</p>
```
Remove the now-unused `APP_TAGLINE` constant (line 68).

- [ ] **Step 2: Online-users state + poll**

Add import: `getOnlineCount` (merge into the api import block). Add state near the other counters:
```ts
  const [onlineCount, setOnlineCount] = useState<number | null>(null);
```
Add a poll effect (near the heartbeat effect ~425):
```ts
  useEffect(() => {
    const poll = () => {
      getOnlineCount().then((d) => setOnlineCount(d.count)).catch(() => undefined);
    };
    poll();
    const id = window.setInterval(poll, 30000);
    return () => window.clearInterval(id);
  }, []);
```

- [ ] **Step 3: Rework the header badges**

Replace the `.topbar-actions` inner block (lines 470-478):
```tsx
        <div className="topbar-actions">
          <BackfillStatusIcon backfill={scanData?.historical_backfill} />
          <FeedStatusIcon liveStatus={liveStatus} />
          <div className="live-pill">
            <span aria-hidden="true" />
            live · tracking {activeNow} {activeNow === 1 ? "plane" : "planes"}
          </div>
          <div className="status">{status}</div>
        </div>
```
with:
```tsx
        <div className="topbar-actions">
          <BackfillStatusIcon backfill={scanData?.historical_backfill} />
          <span
            className="count-badge"
            data-tooltip="Aircraft flying repetitive patterns near this airport right now"
            title="Aircraft flying repetitive patterns near this airport right now"
            tabIndex={0}
            role="status"
            aria-label={`${activeNow} aircraft in the pattern right now`}
          >
            {activeNow}
          </span>
          <span
            className="count-badge square"
            data-tooltip="People viewing circlejerks.live right now"
            title="People viewing circlejerks.live right now"
            tabIndex={0}
            role="status"
            aria-label={`${onlineCount ?? 0} people online right now`}
          >
            {onlineCount ?? "–"}
          </span>
          <div className="status">{status}</div>
        </div>
```

- [ ] **Step 4: Remove the feed icon + all dead `liveStatus` plumbing + the "Scanning…" status**

Removing `FeedStatusIcon` (the "OpenSky icon") makes `liveStatus` dead. Remove all of it so no dead state/poll remains:

- Delete the `FeedStatusIcon` function (lines ~1039-1078). (Its lucide icons `CheckCircle2`/`AlertTriangle`/`CloudOff` stay imported — they're still used by `ComplaintSourceBanner`.)
- Remove the `<FeedStatusIcon liveStatus={liveStatus} />` line — already handled by the header replacement in Step 3.
- Remove the api imports `getLiveStatus,` (line 27) and `type LiveStatusResponse,` (line 40).
- Remove the state `const [liveStatus, setLiveStatus] = useState<LiveStatusResponse | null>(null);` (line 136).
- Replace the decorative-sections effect (the exact block produced by Task 15, keeping only sponsors) with:

```tsx
  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const s = await getSponsors();
        if (!cancelled) setSponsors(s);
      } catch {
        // silent: decorative section
      }
    }
    refresh();
    const sectionsHandle = window.setInterval(refresh, 5 * 60 * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(sectionsHandle);
    };
  }, []);
```

- Remove line 349 entirely: `setStatus("Scanning current aircraft buffer");`. The status now shows the previous "Updated …"/"Ready" until the fresh scan resolves and sets "Updated …" (line 359).
- Verify no leftovers: `grep -n "liveStatus\|getLiveStatus\|LiveStatusResponse\|FeedStatusIcon" src/App.tsx` → expect **no** matches.

- [ ] **Step 5: Add the badge CSS**

In `frontend/src/styles.css`, after the `.status-icon:hover::after` rule (line ~258), add:
```css
.count-badge {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 36px;
  height: 36px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel-warm);
  color: var(--ink);
  font-size: 0.95rem;
  font-weight: 700;
  outline: none;
}

.count-badge.square {
  border-radius: 8px;
}

.count-badge::after {
  content: attr(data-tooltip);
  position: absolute;
  top: calc(100% + 9px);
  right: 0;
  z-index: 20;
  width: max-content;
  max-width: min(320px, calc(100vw - 32px));
  padding: 9px 11px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 12px 30px rgba(27, 58, 107, 0.18);
  color: var(--text);
  font-size: 0.84rem;
  line-height: 1.35;
  white-space: normal;
  opacity: 0;
  pointer-events: none;
  transform: translateY(-3px);
  transition: opacity 0.12s ease, transform 0.12s ease;
}

.count-badge:hover::after,
.count-badge:focus-visible::after {
  opacity: 1;
  transform: translateY(0);
}
```

- [ ] **Step 6: Verify build**

Run: `cd frontend && npm run build`
Expected: `tsc` passes (no unused `APP_TAGLINE`/`FeedStatusIcon`/`liveStatus` errors), `vite build` succeeds.

- [ ] **Step 7: Manual verification (dogfood)**

With backend + `npm run dev` running: confirm the top bar shows a round badge with the plane count and a square badge with the online count (open a second tab to see it rise), both with hover tooltips; the OpenSky/feed icon is gone; the status shows only "Updated …" (no "Scanning current aircraft buffer"); and the tagline differs across page reloads.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(ui): randomized tagline; circle/online count badges; drop feed icon + scanning status"
```

---

## Final verification

- [ ] Backend: `cd backend && .venv/bin/python -m pytest -q` → all pass.
- [ ] Frontend: `cd frontend && npm test && npm run build` → all pass, build clean.
- [ ] Dogfood the full flow against a running stack: select an airport → reports omit elevation/circles/runway-change and use the N-number; edit the system prompt and watch the draft update; the Hall of Shame shows 5 cards with 🤡 on #1 and a worst-habit line; an airport with no data shows the nearest-airport fallback caption; the top bar badges and randomized tagline behave.

## Notes for the implementer

- Do not push; the user deploys via `DEPLOY.md`. There is one pending prod migration (`runway_patterns` UNIQUE) unrelated to this work.
- The VNAP scoring gate means aircraft with circles but fewer than 1 T&G / 3 laps score 0 and won't appear in the Hall of Shame — this is intended ("worst offenders" = real pattern abusers).
- `message_preferences` include_* flags remain in the API surface for backward compatibility but no longer gate the LLM prompt (facts are omitted at the source instead).
