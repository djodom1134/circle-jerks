# PRD: Circling Aircraft Noise Complaint Assistant

**Status:** Draft v0.4
**Owner:** Darren
**Hosting target:** DigitalOcean
**Last updated:** 2026-04-24

---

## 1. Summary

A public web tool that lets a resident specify their location and a nearby airport, detects circling aircraft, touch-and-gos, and direct overflights across a user-selected time window, ranks the worst offenders, generates a tunable complaint description via a free LLM, and links to the right noise-complaint form.

Version 0.4 adds a **community evidence layer**: anonymous collection of aggregate usage, geographic coverage, submission counts, and a cross-user **Hall of Shame** of tail numbers most frequently reported across all locations. All collection is anonymous, aggregated, and uses only data already public via ADS-B.

## 2. Problem

Residents near regional and GA airports have no shared, tail-number-level view of which operators are disrupting communities across a region. Individual complaints vanish into siloed airport noise offices. There's no cross-community visibility into patterns like "this specific flight school is generating complaints at three different airports" or "this tail number has been reported by 47 different households this month."

The v0.4 evidence layer is intended to:

1. Surface persistent offenders across multiple locations, not just one user's view.
2. Give neighborhood associations a credible shared reference.
3. Help journalists and city officials see patterns across their region.
4. Make the tool itself more useful over time: new users immediately see context ("this aircraft has been reported 34 times this month").

## 3. Goals and non-goals

### Goals

(Previous goals retained.) New in v0.4:

1. Collect anonymous usage analytics: unique sessions, unique locations (geo-bucketed), unique airports monitored, total submissions.
2. Collect per-aircraft report counts across all users: a "Hall of Shame" of tail numbers.
3. Show public aggregate metrics on the landing page.
4. Show a public Hall of Shame page, filterable by time window and region.
5. Surface prior community reports in the per-offender detail panel ("this aircraft has been reported 34 times across 12 locations this month").

### Non-goals

(Previous non-goals retained.) New in v0.4:

1. Tracking individual users across sessions beyond an anonymous session cookie.
2. Collecting any personally identifying information (no names, no emails, no precise coordinates).
3. Publishing aircraft owner names or addresses beyond what FAA registry already exposes publicly.
4. Legal claims about any listed aircraft. Hall of Shame is descriptive, not accusatory.

## 4. Target users

Unchanged from v0.3.

## 5. User stories

(Previous stories retained.) New in v0.4:

1. As a new visitor, I see on the landing page that the tool has been used by 2,400 locations across 180 airports and has generated 14,000 complaint drafts, so I know this is a real thing other people are using.
2. As a resident viewing an offender, I see "N123AB has been reported 47 times by 22 different locations this month," giving me context that this is a known problem.
3. As a neighborhood association lead, I visit the Hall of Shame page and filter by my metro area to see which aircraft are disrupting communities across multiple airports.
4. As a journalist, I can link to a time-bounded, region-filtered Hall of Shame page as evidence in a story.

## 6. Key features (MVP, v0.4 additions)

Sections 6.1 through 6.8 unchanged from v0.3.

### 6.9 Analytics and Hall of Shame (new in v0.4)

#### 6.9.1 Events captured

Every user interaction that signals "this aircraft deserves attention" is logged as an anonymous event. Four event types:

| Event | When fired | Strength | Use |
|---|---|---|---|
| `view` | User opens the per-offender detail panel | Weak | Internal analytics only |
| `generate` | LLM description generated (any slider config) | Medium | Counts as a "draft" in aggregate metrics |
| `copy` | User clicks "Copy to clipboard" | Strong | Primary signal for Hall of Shame |
| `form_open` | User clicks through to the noise-complaint form | Strongest | Proxy for filed complaints |

All four events carry the same payload. `copy` and `form_open` are the signals that drive Hall of Shame rankings.

#### 6.9.2 Event payload

```json
{
  "event": "copy",
  "session_id": "anon_UUID_from_localStorage",
  "ts": 1745512345,
  "icao24": "a1b2c3",
  "callsign": "N123AB",
  "aircraft_type": "C172",
  "airport_icao": "KBJC",
  "user_loc_bucket": "80503",
  "window": "1h",
  "event_counts": { "circles": 4, "tg": 3, "la": 0, "passes": 2 },
  "min_alt_agl": 780,
  "slider_preset": "concerned_neighbor"
}
```

Notes:

1. `session_id` is a random UUID generated client-side on first visit, stored in localStorage only. No fingerprinting. No IP logging (see Privacy, section 15).
2. `user_loc_bucket` is the US ZIP code prefix (first 3 digits) or the equivalent country-specific coarse bucket. Never the precise lat/lon.
3. `icao24` and `callsign` are both public ADS-B identifiers.
4. Dedupe rule: one `copy` or `form_open` event per `(session_id, icao24)` pair per 24 hours. Subsequent events overwrite but do not increment the count.

#### 6.9.3 Aggregate metrics

Precomputed nightly (UTC midnight) and cached for landing-page display:

1. **Unique sessions:** distinct `session_id` in last 7d, 30d, all-time.
2. **Unique locations:** distinct `user_loc_bucket` in last 7d, 30d, all-time.
3. **Unique airports monitored:** distinct `airport_icao` with at least 1 event.
4. **Total generates, copies, form_opens:** raw counts, last 24h, 7d, 30d, all-time.
5. **Unique reported aircraft:** distinct `icao24` with at least 1 `copy` event.
6. **Geographic coverage map:** choropleth of `user_loc_bucket` activity by US state / country.

Displayed on the landing page hero row:

```
 2,412 locations   |   183 airports   |   14,207 drafts   |   1,084 tail numbers reported
```

#### 6.9.4 Hall of Shame

A public page at `/hall-of-shame` ranking aircraft by community report count.

**Ranking formula:**

```
hos_score = 3.0 * unique_copying_sessions
          + 2.0 * unique_user_loc_buckets
          + 1.5 * total_copies
          + 2.0 * total_form_opens
          + severity_signal
```

Where `severity_signal` is a rolling average of the low-altitude bonus and quiet-hours bonus across all reports of that aircraft. The first two terms dominate: what matters most is how many distinct people and locations have reported the aircraft.

**Dedupe:** an aircraft only counts once per session per 24 hours.

**Filters (URL-routable):**

1. Time window: 24h, 7d, 30d, all-time.
2. Region: country, US state, metro area, or a specific airport's catchment.
3. Aircraft category: all, GA (piston single), turboprop, jet, helicopter.

**Columns shown per aircraft:**

| Column | Source |
|---|---|
| Rank | Computed |
| Tail number | From ADS-B callsign / FAA registry |
| Aircraft type | FAA registry |
| Operator | FAA registry, only if commercial operator |
| Home airport | Most-frequent origin across reports |
| Report count | Distinct copy events |
| Unique locations | Distinct `user_loc_bucket` |
| Airports where reported | Distinct `airport_icao` |
| Last reported | Most recent event timestamp |

**Per-aircraft deep link:** clicking a row opens `/hall-of-shame/{icao24}` showing the aircraft's report history across time and regions, plus its public FAA registry data. No owner names by default; a "show registry details" toggle reveals the public FAA record with a privacy disclaimer.

#### 6.9.5 In-context community signal

In the per-offender detail panel (section 7.5), above the LLM description:

```
This aircraft has been reported 47 times by 22 locations this month.
It is currently #4 on the Hall of Shame for KBJC.
```

Rendered only if the aircraft has at least 3 prior reports to avoid false signal from noisy data.

#### 6.9.6 Opt-out

Users can opt out of event collection via a settings toggle. Opt-out sets a localStorage flag; no events fire when set. The site still works fully without analytics. The opt-out state is also respected when Do Not Track browser header is present.

## 7. Output specification

Sections 7.1 through 7.5 unchanged from v0.3. New sections added below.

### 7.6 Landing page metrics row

Four tiles with aggregate counters, updated nightly. Click each tile to drill into the corresponding view (locations map, airports list, submissions timeline, Hall of Shame).

### 7.7 Hall of Shame page

1. Filter bar: time window, region, aircraft category.
2. Ranked table per 6.9.4.
3. Sparkline per row showing report volume over the selected time window.
4. Region choropleth at the top showing where reports of the filtered set are coming from.
5. Export: CSV download of the current filtered view (for journalists, researchers).

### 7.8 Per-aircraft Hall of Shame page

1. Aircraft summary: tail, type, operator (if commercial), home airport.
2. Report timeline: events over time with event type color coding.
3. Geographic map: all `user_loc_bucket` centroids that have reported the aircraft, sized by report count.
4. Public FAA registry toggle (hidden by default).
5. "I've also been affected by this aircraft" button: fires a `confirm_affected` event, which feeds into the ranking but does not submit a formal complaint.

## 8. Architecture

```
 Browser (React + Mapbox GL JS)
    |
    | HTTPS (JSON), localStorage (session_id, opt-out flag, last sliders)
    v
 DO App Platform
    +-- API layer (FastAPI)
    +-- Poller worker (per active bbox)
    +-- Rolling track buffer (Redis, 24h TTL)
    +-- Description cache (Redis, 10m TTL)
    +-- Event ingest (Redis stream or direct Postgres insert)
    +-- Aggregation worker (nightly rollup job)
    +-- Postgres: events, daily_rollups, hall_of_shame, airports, runways, complaint_forms, aircraft_cache
    +-- LLM client (Groq / Gemini)
    |
    v
 ADSB.lol live ADS-B, OpenSky enrichment/fallback, Airplanes.live fallback,
 Mapbox, LLM provider, FAA registry
```

**Change from v0.3:** SQLite replaced with **Postgres** (DO Managed Postgres, ~$15/mo smallest tier) to support analytics queries and concurrent writes from the event ingest path.

### 8.1 API endpoints

(Previous endpoints retained.) New in v0.4:

1. `POST /events` accepts a batch of event payloads. Rate-limited by session_id (max 100 events/min).
2. `GET /metrics/summary` returns the landing-page tile values.
3. `GET /hall-of-shame?window=30d&region=us-co&category=ga` returns the ranked list.
4. `GET /hall-of-shame/{icao24}` returns per-aircraft detail.
5. `GET /metrics/geo` returns the choropleth data for the coverage map.
6. `GET /aircraft/{icao24}/community` returns the in-panel signal ("reported N times by M locations").

Payload for `/aircraft/{icao24}/community`:

```json
{
  "icao24": "a1b2c3",
  "callsign": "N123AB",
  "reports_30d": 47,
  "unique_locations_30d": 22,
  "hall_of_shame_rank_airport": { "KBJC": 4 },
  "hall_of_shame_rank_global": 312
}
```

### 8.2 Aggregation worker

Runs nightly at local 03:00. Computes:

1. Daily rollups per `(icao24, airport_icao, date)`: event counts by type, unique sessions, unique loc_buckets.
2. Global Hall of Shame ranks for 24h, 7d, 30d, all-time windows.
3. Per-airport Hall of Shame ranks.
4. Per-region Hall of Shame ranks.
5. Coverage map data.

Results written to `daily_rollups` and `hall_of_shame` tables. Frontend reads only from rollups; live event table stays hot for ingest.

### 8.3 Deployment on DigitalOcean

1. App Platform: web service + worker service + Managed Redis + Managed Postgres. Estimate ~$35 to $40/mo.
2. Lean alternative: single $18/mo Droplet with Docker Compose (api, worker, redis, postgres, caddy).

## 9. External dependencies and limits

Unchanged from v0.3 except for the added Managed Postgres line item.

## 10. Data model

```sql
airports (icao PK, iata, name, city, country, lat, lon, elevation_ft, is_towered)
runways (icao, runway_id, lat_threshold, lon_threshold, heading_deg, length_ft, PK(icao, runway_id))
complaint_forms (icao PK, form_url, phone, email, notes, last_verified)
aircraft_cache (icao24 PK, registration, type_icao, type_description, operator, last_updated)

-- New in v0.4:

sessions (
  session_id UUID PK,
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  loc_bucket TEXT,       -- coarse geo only, e.g. ZIP3
  country TEXT,
  region TEXT,           -- e.g. US state
  opt_out BOOLEAN DEFAULT FALSE
)

events (
  id BIGSERIAL PK,
  ts TIMESTAMP,
  session_id UUID REFERENCES sessions,
  event_type TEXT CHECK (event_type IN ('view','generate','copy','form_open','confirm_affected')),
  icao24 TEXT,
  callsign TEXT,
  aircraft_type TEXT,
  airport_icao TEXT,
  loc_bucket TEXT,
  window_label TEXT,
  event_counts JSONB,
  min_alt_agl INTEGER,
  slider_preset TEXT
)
CREATE INDEX ON events (icao24, ts);
CREATE INDEX ON events (airport_icao, ts);
CREATE INDEX ON events (session_id, icao24, ts);  -- for dedupe

daily_rollups (
  date DATE,
  icao24 TEXT,
  airport_icao TEXT,
  region TEXT,
  copy_count INTEGER,
  form_open_count INTEGER,
  unique_sessions INTEGER,
  unique_loc_buckets INTEGER,
  PK (date, icao24, airport_icao, region)
)

hall_of_shame (
  window_label TEXT,        -- '24h','7d','30d','all'
  region_filter TEXT,       -- 'global','us-co','KBJC', etc.
  icao24 TEXT,
  rank INTEGER,
  hos_score REAL,
  copy_count INTEGER,
  unique_sessions INTEGER,
  unique_loc_buckets INTEGER,
  airports_list JSONB,
  updated_at TIMESTAMP,
  PK (window_label, region_filter, icao24)
)

metrics_snapshots (
  snapshot_at TIMESTAMP PK,
  unique_sessions_alltime INTEGER,
  unique_loc_buckets_alltime INTEGER,
  unique_airports INTEGER,
  total_generates INTEGER,
  total_copies INTEGER,
  total_form_opens INTEGER,
  unique_reported_aircraft INTEGER
)
```

## 11. Detection pseudocode

Unchanged from v0.3.

## 12. LLM prompt

Unchanged from v0.3. (Community context is shown separately in the UI, not injected into the LLM prompt, to keep the complaint text grounded in the user's own observations.)

## 13. Rollout phases

1. **MVP (4 to 5 weeks solo, bumped from v0.3's 3 to 4):** dual geography, all three detectors, time window filter, flavor sliders with 5 presets, event ingest, nightly aggregation, landing page metrics, Hall of Shame page, 10 seeded complaint forms.
2. **v1.1:** bookmarkable locations, shareable snapshot URLs, crowdsourced form registry with moderation, "I've also been affected" button feeding rankings.
3. **v1.2:** 7-day rolling detection window, weekly worst-offender digests, RSS feeds per airport.
4. **v2:** email alerts, multi-location watchlists, mobile PWA, regional community dashboards, CSV/JSON API for researchers.

## 14. Risks and open questions

(Previous risks retained.) New in v0.4:

1. **Reporting gaming / brigading:** a motivated group could inflate a specific aircraft's Hall of Shame rank by repeated visits from different browsers. Mitigations:
   1. Dedupe per (session_id, icao24) per 24h at ingest.
   2. Require at least 3 distinct `loc_bucket` values before an aircraft is eligible for the public Hall of Shame list.
   3. Rate-limit `POST /events` per IP to 100 events/minute (IPs not logged, only rate-limited).
   4. Server-side anomaly detection on sudden rank changes; flagged entries held for manual review before appearing publicly.
2. **Defamation / reputational risk:** a commercial operator named repeatedly could push back legally. Mitigations:
   1. Every metric displayed is descriptive ("reported N times") not accusatory ("violated N times").
   2. Clear disclaimer at the top of Hall of Shame explaining it is user-generated community data, not official enforcement.
   3. "Dispute this listing" link on per-aircraft page in v1.1.
   4. Owner names hidden behind an explicit toggle with privacy warning.
3. **Legal / regulatory:** consult counsel before launch on whether the Hall of Shame as framed is defensible. Preemptive stance: aggregate community reports of publicly broadcast ADS-B data is clearly protected expression in the US, but tone of framing matters.
4. **Privacy drift:** even coarse location buckets plus time-of-day could re-identify a small-population ZIP3 user. Mitigations:
   1. Hide `loc_bucket` entries with fewer than 5 sessions in public views.
   2. Aggregate at state or metro level in public-facing maps.
5. **Analytics cost creep:** Postgres size and query cost grow with event volume. Mitigations:
   1. Events older than 180 days move to cold storage (DO Spaces S3-compatible).
   2. Daily rollups are the primary read path; raw events queried only for Hall of Shame recomputation.

## 15. Privacy framework (new in v0.4)

This section is intentionally a first-class feature, not a footnote.

1. **No PII collected.** No names, no emails, no IP logging (IPs used only for ephemeral rate-limiting and discarded).
2. **Anonymous sessions.** `session_id` is a random UUID generated client-side. It is not tied to any identity. Users can clear it by clearing localStorage.
3. **Coarse geography only.** User lat/lon is never sent to the server. The client derives `loc_bucket` (ZIP3 or equivalent) and sends only that. The full precision lives only in the user's browser.
4. **Opt-out.** One toggle in settings. Respects DNT header.
5. **Do not log.** Server access logs (nginx/caddy) retained for 24h max and scrubbed of query strings and IPs beyond that window.
6. **Aircraft data is public.** All tail numbers, types, and tracks shown are already broadcast in the clear over 1090 MHz. The tool does not reveal any non-public information.
7. **Owner data stays opt-in to view.** The FAA registry is public, but we do not surface owner names by default.
8. **Aggregate publishing threshold.** Any public metric must aggregate across at least 5 sessions and 3 loc_buckets, else it's hidden.
9. **Transparency page.** `/privacy` explains all of the above in plain language, including what each event type captures and how to inspect the payload (dev tools guidance).
10. **No advertising, no third-party trackers, no analytics SDKs.** First-party event capture only.

## 16. Success metrics

(Previous metrics retained.) New in v0.4:

1. Within 90 days of launch: at least 500 unique locations have generated at least one description.
2. Within 90 days: at least 50 unique airports monitored.
3. Hall of Shame shows at least 100 aircraft with >= 5 reports.
4. >= 40 percent of generated descriptions result in a `copy` event.
5. >= 15 percent of `copy` events are followed by a `form_open` event within 10 minutes.
6. Opt-out rate stays below 10 percent (if higher, rework onboarding copy around privacy).

## 17. Appendix A: full slider-to-prompt band mappings

Unchanged from v0.3.

## 18. Appendix B: free LLM comparison

Unchanged from v0.3.

## 19. Appendix C: metrics and Hall of Shame example responses

### `/metrics/summary`

```json
{
  "unique_sessions_alltime": 12847,
  "unique_loc_buckets_alltime": 2412,
  "unique_airports": 183,
  "total_generates_alltime": 28914,
  "total_copies_alltime": 14207,
  "total_form_opens_alltime": 3182,
  "unique_reported_aircraft_alltime": 1084,
  "last_updated": "2026-04-24T03:00:00Z"
}
```

### `/hall-of-shame?window=30d&region=us-co`

```json
{
  "window": "30d",
  "region": "us-co",
  "region_label": "Colorado, USA",
  "updated_at": "2026-04-24T03:00:00Z",
  "entries": [
    {
      "rank": 1,
      "icao24": "a1b2c3",
      "callsign": "N123AB",
      "aircraft_type": "C172",
      "operator": "Skytech Flight Academy",
      "home_airport": "KBJC",
      "copy_count": 184,
      "unique_locations": 38,
      "airports_where_reported": ["KBJC","KAPA","KFNL"],
      "last_reported": "2026-04-23T19:42:00Z",
      "hos_score": 641.2
    }
  ]
}
```

### `/aircraft/a1b2c3/community` (in-panel signal)

```json
{
  "icao24": "a1b2c3",
  "callsign": "N123AB",
  "reports_30d": 184,
  "unique_locations_30d": 38,
  "hall_of_shame_rank_airport": { "KBJC": 1 },
  "hall_of_shame_rank_global": 12
}
```
