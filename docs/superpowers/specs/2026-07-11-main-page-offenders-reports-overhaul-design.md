# Main-page overhaul: worst offenders, report quality, tagline & top bar

**Date:** 2026-07-11
**Branch:** codex/production-observability
**Status:** Approved design — ready for implementation plan

## Summary

A multi-part refresh of the circlejerks.live main page and its LLM report generator,
spanning six independent workstreams:

- **A.** Rewrite the noise-complaint report generator (Groq) — fix the "system prompt has
  no effect" bug, trim the fed data, focus on repetitive touch-and-goes and noise, N-number
  only, truncate large multi-aircraft selections.
- **B.** New "top 5 worst offenders" card section at the bottom of the main page, ranked by
  `VNAP × circles`, with nearest-airport fallback.
- **C.** Live (debounced) report preview as the user edits the system prompt; keep the
  existing per-browser cookie storage.
- **D.** Randomized / auto-generated tagline per page load.
- **E.** Top-bar changes: remove the feed-status ("OpenSky") icon, replace the plane count
  with a circle badge + tooltip, add an online-users square badge + tooltip, drop the
  "scanning…" status text.
- **F.** New public online-users count endpoint.

All six ship as one spec and one phased implementation plan. They are independent enough to
land and verify separately.

## Locked decisions

1. **Live preview** = debounced full regeneration reusing the existing report endpoint (no
   SSE/streaming infra).
2. **System-prompt storage** = keep the existing per-browser cookie
   (`circlejerks_preferences.system_prompt`); no server-side store.
3. **Custom system prompt = full replace** of the default persona/instructions. Content
   rules are enforced by the app-controlled DATA block (what facts reach the model), not by
   prompt instructions, so trimming holds regardless of the custom prompt.

## Terminology corrections (from code investigation)

- The provider is **Groq** (`llama-3.3-70b-versatile`), not xAI Grok. UI calls it "grok".
- **"overcomes"** does not exist in the codebase. Interpreted as the **cowboy** count
  (unwarranted / against-the-wind runway changes).
- **"low"** = low approaches (a `scoring.py` / submission field, *not* a VNAP axis).
- **"t&g"** = touch-and-goes → VNAP `tg_volume` axis + raw `touch_and_gos` count.
- **VNAP** = Voluntary Noise Abatement Procedures; a per-aircraft, per-airport 0–100
  violation score (higher = worse) over 7 equally-weighted axes: `tightness`, `altitude`,
  `timeofday`, `tg_volume`, `circle_restraint`, `left_traffic`, `runway29`. The "worst
  habit" = the highest-scoring axis in the per-aircraft `scores` dict; labels in
  `frontend/src/lib/vnapDashboard.ts`.

---

## A. Report generation overhaul

**Files:** `backend/app/llm.py`, `backend/app/services.py`; frontend request unchanged in shape.

### A1. Persona → system prompt (fixes "no effect")

Today `build_prompt` / `build_aggregate_prompt` (`llm.py`) pack the persona, tone bands, and
instructions into the **user**-role message; the system prompt is a thin wrapper the huge
user prompt overrides. Restructure:

- **System message** carries the persona/voice/instructions (rewritten default, see A5). A
  user-supplied `system_prompt` **fully replaces** it (`{"role":"system","content": system_prompt or DEFAULT_SYSTEM_PROMPT}` stays, but DEFAULT now holds the real persona).
- **User message** carries only the trimmed `DATA:` block + minimal framing. This is always
  app-controlled, so content rules hold no matter the custom prompt.

### A2. Cache-key bug

`services.py` builds the description cache key with `(system_prompt or "default")[:120]`.
Two custom prompts sharing the first 120 chars collide → edits past char 120 return stale
text. Fix: hash the **full** system prompt (e.g. `sha1(system_prompt)`) into the key. Applies
to both single (`services.py:~2017`) and aggregate (`services.py:~2172`) paths.

### A3. Deterministic-fallback masking

Groq has a hard ~4.0s timeout (`llm.py`) and returns `None` when unconfigured/slow; the code
then serves `deterministic_description` / `deterministic_aggregate_description`, which ignore
the system prompt. Mitigation: modestly raise the Groq timeout, and when a fallback draft is
served (especially with a custom prompt set), the response should be distinguishable so the
UI can label it an offline draft rather than silently implying the prompt took effect. Keep
the deterministic builders trimmed to the same DATA set (A4) so fallbacks stay on-message.

### A4. Trimmed DATA block

**Fed to the model** (single + aggregate `DATA:` lines in `llm.py`):

- N-number (validated — see A6)
- touch-and-go count
- **lowest AGL over the user's house** — NEW field: min AGL among `pass_over_user` events for
  this aircraft (distinct from the general altitude band). This is "how low they flew over
  house / lowest flight over house".
- altitude band (min–max)
- peak noise dB at home
- quiet-hours events count
- origin airport (kept as neutral context)
- unwarranted-runway-change note **only if one exists** (see A7)

**Removed from the DATA block entirely** (not just include-flag gated — the raw lines are
emitted unconditionally today):

- `airport_field_elevation_ft_msl` (field elevation)
- `circles_count` / `total_circles` (circle counts)
- `avg_loop_radius_nm`
- the "runway_changes_against_the_wind: none recorded" line

### A5. Default persona rewrite

New `DEFAULT_SYSTEM_PROMPT` persona: focus on the specific aircraft's (or group's) repetitive
**touch-and-go** behavior and the **noise** it creates — how low it flew over the resident's
house, the lowest pass, the sustained repetition. Do not mention runway/field elevation. Do
not mention runway changes unless flagged unwarranted. Emphasize how bad this particular
aircraft / group is.

### A6. N-number only

Route `callsign` through the existing `extract_n_number_from_callsign()`
(`registry/normalize.py`). Precedence for the reported identifier: valid N-number from
callsign → registry N-number (via `registration` / icao_hex lookup) → drop the identifier
(no raw non-tail callsign reaches the model). Gate at `services.py` where
`context.callsign` is assigned (~1886–1889) and in the aggregate callsign list.

### A7. Runway-change note

`runway_change_note` (`llm.py`) currently emits "none recorded" when there are no
against-the-wind changes. Gate the append so the note is included **only** when genuinely
unwarranted changes exist (the SQL already filters `wind_favored_new = 0`). No note otherwise.

### A8. >10 aircraft truncation

`POST /complaint/summary` accepts up to 40 icao24s and builds one context each with no cap.
Before building contexts (top of `build_summary_description` loop, `services.py:~2062/2084`),
if more than 10 aircraft are provided, keep the **worst 10 by `VNAP × circles`** and drop the
rest. The frontend surfaces a "showing worst 10 of N" note.

### A9. Testing

Unit tests: prompt assembly omits removed fields and includes lowest-AGL; N-number gating
(non-N-number callsign dropped); cache key varies with full prompt; runway-change note absent
when no unwarranted change; truncation keeps 10 worst. Existing report tests updated.

---

## B. Worst offenders

**Files:** `backend/app/main.py`, `backend/app/db.py`, `backend/app/vnap.py` (read only);
frontend `frontend/src/App.tsx`, `frontend/src/lib/api.ts`, `styles.css`, new component.

### B1. Backend endpoint

`GET /airports/{icao}/worst_offenders?limit=5`:

1. Compute VNAP over **all-time** for the airport:
   `vnap.compute_aircraft_compliance(conn, icao, start_ts=0, end_ts=now)`.
2. Rank each aircraft by **`vnap_score × circles`** (both per-airport, all-time). Ranking
   metric intentionally uses the VNAP composite as-is (aircraft below the VNAP scoring gate
   score 0 and drop out — acceptable, matches "worst offenders").
3. Take top `limit` (default 5).
4. Per aircraft attach: N-number/tail, total all-time circles (at this airport),
   lifetime `report_count` + `last_reported_at` (batch join `aircraft_report_counts` by
   icao24), and the **worst axis** = `max(scores.items())` mapped to its display label.
5. **Fallback:** if no aircraft qualify (empty / all-zero product), find the nearest *other*
   airport and recompute there. Response includes `source_icao`, `resolved_icao`,
   `is_fallback`, and a human label for the resolved airport.

### B2. Nearest-other-airport helper

`db.nearest_airport(conn, lat, lon)` exists but returns the airport itself (distance 0) for a
same-airport query. Add a variant (or `exclude_icao` param) that skips the source ICAO. Load
the source airport via `db.get_airport`, then find the nearest excluding it.

### B3. Frontend cards

New section at the **bottom of the main page** (`App.tsx`), below existing content. Fetch on
airport change. Render up to 5 cards; each card shows:

- N-number (title) — **🤡 clown emoji on the #1 card**
- total all-time circles
- number of reports
- last reported (relative time via existing `elapsed`/`format` helpers)
- **worst VNAP habit** callout (single axis label + optionally its 0–100 value)

Cards **omit** the low-approach / touch-and-go / cowboy sub-stats. If `is_fallback`, a small
caption notes the data is for the nearest airport (named).

### B4. Testing

Backend: ranking order, top-N slice, fallback selection excludes source, worst-axis
selection. Frontend: `statsApi`/new api test for shape; card renders clown on #1 only.

---

## C. Live preview + prompt storage

**Files:** `frontend/src/App.tsx` (report effect), `frontend/src/lib/api.ts` (unchanged).

- The report-generation effect already debounces ~300ms and depends on a `refreshNonce`.
  Extend its dependency set so a change to `preferences.system_prompt` triggers a debounced
  (~600ms) regeneration, with an "updating…" state on the report panel.
- Storage unchanged: `system_prompt` stays in the `circlejerks_preferences` cookie. Confirm
  the value is read (`App.tsx:~1758`) and passed on both report calls; the A2 cache fix makes
  edits actually re-render.

---

## D. Tagline randomization

**File:** `frontend/src/App.tsx` (constant at ~L68, render at ~L467).

Replace the single `APP_TAGLINE` constant with a `TAGLINES` pool and select one per load via
`useMemo(() => pick(pool), [])`. Pool = the three user-provided lines plus dynamic templates
built from `scanData?.counters.circles` (circles logged today — already in state, default
window is "today"):

Seed pool (editable):
- "Pattern training loops give us no local benefit, only noise."
- "Imagine a biker gang circling your block around your house — annoyed yet?"
- "Monitoring 16,000+ airports for abuse."
- dynamic: "{circles} training loops logged today — when you can't hold a conversation
  outdoors anymore." (only when `circles` is known/>0)
- (2–3 more in the same voice)

If `scanData` isn't loaded yet, fall back to a static line; recompute once (per load), not on
every scan refresh.

---

## E. Top bar

**Files:** `frontend/src/App.tsx` (header ~L459–479), `frontend/src/styles.css`.

- **Remove** `<FeedStatusIcon liveStatus={liveStatus} />` (~L472) — this is the "OpenSky icon"
  (a lucide feed-status badge; no image asset). `BackfillStatusIcon` stays unless directed.
- Replace the `.live-pill` (~L473–476) with a **circle badge** (mirror `.status-icon`,
  36×36, `border-radius:999px`) showing `offenders_active_now`, with a `data-tooltip` (reuse
  the existing `.status-icon::after` CSS tooltip pattern) explaining it — e.g. "Aircraft
  flying patterns right now."
- Add a **square badge** (same base, `border-radius:8px`) to its right showing the
  online-users count (workstream F), tooltip "People viewing the site right now."
- **Remove** the "Scanning current aircraft buffer" status (`setStatus` at ~L349); keep only
  "Updated ___" (~L359). The shared `status` string is also used for load/ready/error — only
  drop the scanning label, don't break the others.
- Add both new badge classes to the responsive media-query rules (styles.css ~1396/1476).

---

## F. Online-users endpoint

**Files:** `backend/app/main.py`, `backend/app/db.py` (helper), frontend `App.tsx` + `api.ts`.

- New **public** `GET /activity/online` → `{ "count": <int> }` using the existing active-window
  query: `COUNT(*) FROM visitor_activity WHERE last_seen >= now - active_user_window_seconds`
  (90s default; the logic currently lives only inside the admin dashboard query — extract a
  small `db.online_visitor_count(conn)` helper and reuse it in both places).
- Frontend: poll it (reuse/adjacent to the existing 30s heartbeat interval in `App.tsx`),
  store in state, feed the E square badge. No auth.

---

## Cross-cutting notes

- No new infra (no SSE/WebSocket, no new DB tables). New columns: none. New helpers only.
- Ranking-gate caveat (B1.2): aircraft below the VNAP gate (need ≥1 T&G and ≥3 of
  max(circles,T&G)) score 0 and won't appear in worst-offenders — intended.
- The report DATA trim (A4) is enforced for everyone including full-replace custom prompts,
  because facts are omitted from the payload rather than merely instructed against.

## Out of scope

- Cross-device prompt sync (no login exists).
- True token-streaming report output.
- Changing VNAP axis definitions or weights.
- Reworking the existing offenders table (only adding the new top-5 card section).
