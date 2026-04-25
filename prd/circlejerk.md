# PRD: Circling Aircraft Noise Complaint Assistant

**Status:** Draft v0.3
**Owner:** Darren
**Hosting target:** DigitalOcean
**Last updated:** 2026-04-24

---

## 1. Summary

A public web tool that lets a resident specify (a) their own location and (b) a nearby airport, then detects and counts three distinct categories of aircraft activity across a user-selected time window: **circles around the airport**, **touch-and-gos at the airport**, and **passes directly over the user's location**. It plots each offender's track on a Mapbox map, ranks the worst offenders by weighted severity and time of day, and generates a **tunable** plain-English description per offender via a free LLM. The user controls the description's tone through flavor sliders (anger, niceness, respect, detail, local flavor) and can scope the data to anything from the last 5 minutes to the last 24 hours.

Version 0.3 adds flavor sliders, presets, and a time-window filter.

## 2. Problem

Residents near regional and GA airports experience persistent low-altitude traffic from flight schools, tour operators, banner pullers, and training traffic. Existing tools show traffic but do not:

1. Distinguish circling vs. through-traffic vs. pattern work vs. direct overflights of a specific address.
2. Produce a written, timestamped, quantified description suitable for a complaint.
3. Let the user match the complaint's tone to their own voice and the severity of the incident.
4. Scope the data to the exact window the user wants to complain about (the last 30 minutes, not all of today).
5. Route the user to the correct reporting form.
6. Rank offenders so the user knows which callsigns to cite first.

Most complaints never get filed, or get filed with generic language that officials dismiss as boilerplate.

## 3. Goals and non-goals

### Goals

1. Identify circling, touch-and-gos, and passes-over-user events from live ADS-B.
2. Support a user-selected time window from 5 minutes up to 24 hours.
3. Rank offenders by weighted severity within the selected window.
4. Generate per-offender complaint text with user-controlled tone (5 flavor sliders + 4 presets).
5. Visualize tracks on a Mapbox map.
6. Provide a one-click link to the correct noise-complaint form.
7. Run on DigitalOcean with predictable, low monthly cost.

### Non-goals

1. Commercial-grade flight tracking.
2. Real-time push or SMS alerts (v2).
3. Legal adjudication.
4. Noise modeling or dB estimation.
5. Historical data beyond 24 hours in MVP.

## 4. Target users

1. **Primary:** Homeowners within 2 to 15 nm of a GA or regional airport.
2. **Secondary:** Neighborhood associations and noise roundtables.
3. **Tertiary:** Local journalists and city council staff.

## 5. User stories

1. As a resident, the site auto-detects my browser location, picks the nearest airport, and shows me current activity with no clicks.
2. As a resident, I can override either my location or the chosen airport.
3. As a resident, I pick a time window (5 min, 30 min, 1 hour, 6 hours, or today) and every counter, ranking, and map layer updates to match.
4. As a resident, I see three separate counters for that window: circles, touch-and-gos, and passes over my location.
5. As a resident, I see a ranked list of the worst offenders with specific numbers and time-of-day breakdowns.
6. As a resident, I click an offender and tune the complaint text with sliders (anger, niceness, respect, detail, local flavor) or pick a preset.
7. As a resident, I copy the finished description and click through to the complaint form.

## 6. Key features (MVP)

### 6.1 Location and airport selection (dual geography)

Two independent points of interest. Both auto-detected, either overridable.

**User location:** Browser Geolocation API by default. Override via Mapbox Geocoding address search or drop-pin on map. Persisted in localStorage.

**Airport of interest:** Nearest towered or public-use airport by default (from OurAirports seed). Override via ICAO/IATA/name search. Defines the monitoring ring (default 8 nm, adjustable 3 to 20 nm).

The user location and airport are independent. The user may live 6 nm from the airport, in which case overflights are a separate phenomenon from circling above the field.

### 6.2 Time window filter (new in v0.3)

A segmented control at the top of the airport page drives the data scope for every panel below it.

| Label | Value | Default counter label |
|---|---|---|
| 5 min | 300 s | "in the last 5 minutes" |
| 30 min | 1800 s | "in the last 30 minutes" |
| 1 hour | 3600 s | "in the last hour" |
| 6 hours | 21600 s | "in the last 6 hours" |
| Today | until local 00:00 | "today" |

Rules:

1. The server always retains a rolling 24-hour buffer. The filter is a read-side operation.
2. All three detectors emit timestamped events. The filter selects events by timestamp.
3. For the "5 min" and "30 min" windows, the ranking sorts by raw event count with minimal time-of-day weighting (short windows make hour-of-day bonuses noisy).
4. For the "6 hours" and "today" windows, the full severity score (with time-of-day and altitude bonuses) applies.
5. Default on first load: "1 hour" (matches the typical "what's going on right now" use case).
6. Query param `?window=1h` persists selection across deep links.

Map layer behavior:

1. Only tracks with at least one sample inside the time window are drawn.
2. Track segments outside the window are drawn faded at 20 percent opacity for context.
3. The time-of-day histogram rescales to the selected window's hours.

### 6.3 Detection logic (three independent detectors)

Same as v0.2. All detectors run on the full 24-hour rolling buffer and emit timestamped events. The time-window filter is applied downstream, not inside the detectors.

#### 6.3.1 Circles around the airport

Closed-loop detection within the monitoring ring. Cumulative turn > 360 degrees with altitude band under 1500 ft as fallback. Output: `circles_count`, `first_circle_at`, `last_circle_at`, `avg_loop_radius_nm`, `alt_band_ft`.

#### 6.3.2 Touch-and-gos at the airport

Altitude drop to within 200 ft of field elevation + speed drop below 80 kt + climb-out within 60 s, within 1.5 nm of a known runway threshold. Distinguished from full-stop landings (on-ground > 60 s) and low approaches (no descent below 50 ft AGL). Output: `touch_and_go_count`, `low_approach_count`, `runway_used`.

#### 6.3.3 Passes over the user location

Entry and exit of a user-centered cylinder (radius default 0.5 nm, ceiling default 5000 ft AGL), separated by at least 60 s from the previous exit to count as a new pass. Output: `passes_count`, `min_altitude_ft_agl`, `closest_horizontal_nm`, `pass_times[]`.

#### 6.3.4 Severity score and worst-offender ranking

Same formula as v0.2, with two adjustments for v0.3:

1. Time-of-day and low-altitude bonuses are **suppressed** for windows under 1 hour (rankings reduce to raw event counts).
2. For the "today" window, score is computed over the local calendar day, not a rolling 24 hours.

```
score_long_window = 2.0*circles + 1.5*tg + 1.0*la + 3.0*passes
                  + tod_bonus + low_alt_bonus
score_short_window = 2.0*circles + 1.5*tg + 1.0*la + 3.0*passes
```

### 6.4 Time-of-day analysis

Histogram of events by hour across the selected window, separated by event type. Quiet-hours flag (22:00 to 07:00 local). For windows under 1 hour, this panel collapses to a sparkline of event density by minute.

### 6.5 Origin airport inference

First infer from the rolling track buffer: ground sample near an airport, then first-seen-near-airport, then first-seen city/country. If OpenSky credentials are available, use `/flights/aircraft` as enrichment for completed flights; this data is batch-updated nightly, so same-day results may be unavailable. Fallback label: "unknown; first seen at HH:MM."

### 6.6 Map visualization (Mapbox GL JS, free tier)

1. Base: `mapbox://styles/mapbox/light-v11`.
2. Layers: airport marker + monitoring ring, user location marker + pass-over cylinder, per-offender track polyline color-coded by event type, current aircraft icon rotated to heading, origin airport with dashed great-circle line.
3. Tracks filtered by time window per 6.2.

### 6.7 LLM-generated description with flavor sliders (new in v0.3)

Free, low-latency model produces the description. Length 3 to 8 sentences, driven by the Detail slider.

#### 6.7.1 Sliders

Each slider is a 0 to 10 integer. Defaults are tuned for "concerned neighbor."

| Slider | Default | Low end (0) | High end (10) |
|---|---|---|---|
| **Anger** | 3 | Dispassionate, neutral reporting | Strong frustration, emphatic language (never profane) |
| **Niceness** | 6 | Cold, transactional | Warm, cordial, acknowledges shared community |
| **Respect** | 7 | Direct, unvarnished | Highly deferential to authority, thanks officials for their time |
| **Detail** | 6 | 3 sentences, headline numbers only | 8 sentences, every metric and timestamp |
| **Local flavor** | 3 | Generic, no place references beyond the airport name | References the neighborhood, nearby landmarks, resident perspective |

Conflict resolution is left to the LLM. The prompt instructs it to blend the sliders coherently; extreme combinations (high anger + high niceness) produce "firm but civil" output rather than incoherent output.

#### 6.7.2 Presets

Single-click presets that set all five sliders at once. Always editable after selection.

| Preset | Anger | Niceness | Respect | Detail | Local |
|---|---|---|---|---|---|
| **Just the facts** | 0 | 5 | 5 | 10 | 0 |
| **Concerned neighbor** | 3 | 6 | 7 | 6 | 3 |
| **Fed up resident** | 7 | 2 | 4 | 7 | 6 |
| **Formal complaint** | 1 | 3 | 10 | 9 | 1 |
| **Community voice** | 4 | 7 | 6 | 5 | 8 |

#### 6.7.3 Slider-to-prompt mapping

The server translates slider values to natural-language prompt modifiers rather than passing raw integers. Example mapping for Anger:

```
0-1: "Use dispassionate, factual language. No emotional content."
2-4: "Maintain a measured tone. Minor expressions of concern are acceptable."
5-6: "Express clear frustration. The reader should understand this is affecting the resident's quality of life."
7-8: "Convey strong frustration and urgency. Use emphatic phrasing, but remain civil."
9-10: "Convey serious anger and exhaustion with the ongoing situation. No profanity or personal attacks."
```

Similar 5-band mappings exist for Niceness, Respect, Detail, and Local flavor. See Appendix A.

#### 6.7.4 Caching

Description caching key: `(icao24, airport_icao, user_loc_hash, time_window, slider_hash)`. TTL 10 minutes. Regeneration is free to the user (just re-requests). The frontend debounces slider changes by 300 ms before re-requesting.

#### 6.7.5 Fallback

If the LLM is unavailable, a deterministic template produces a neutral description (equivalent to "Just the facts" preset). Sliders are disabled with a tooltip explaining the fallback.

### 6.8 Complaint output

1. Copy-to-clipboard button on the description.
2. Auto-included metadata: local timestamps, airport, user location label, callsign, ICAO24, event counts, time-of-day summary, snapshot URL.
3. "Open complaint form" button linking to the airport's noise-reporting page.

## 7. Output specification

### 7.1 Page header (always visible)

```
[Longmont, CO]  |  Airport: KBJC (Rocky Mountain Metro)
Time window: ( 5m  30m  1h*  6h  Today )      * selected
```

### 7.2 Top-line counters

```
 ------------------------------------------------------------
  12 circles | 8 touch-and-gos | 3 passes over your home
  2 offenders active now | 5 unique aircraft in last hour
 ------------------------------------------------------------
```

Counter labels change with the window ("in the last hour," "today," etc.).

### 7.3 Worst offenders table

Same as v0.2, scoped to the selected time window. Row click opens the detail panel.

### 7.4 Time-of-day panel

1. For windows >= 1 hour: stacked bar chart by hour.
2. For windows < 1 hour: sparkline of event density by minute.

### 7.5 Per-offender detail panel

1. Callsign, ICAO24, aircraft type, operator (if public), origin airport.
2. Event log scoped to the time window.
3. Map track (full 24h, with out-of-window segments faded).
4. **Flavor slider panel** with 5 sliders and 5 preset buttons.
5. Generated description paragraph, auto-regenerating on slider change.
6. Copy + "File complaint" buttons.

## 8. Architecture

```
 Browser (React + Mapbox GL JS)
    |
    | HTTPS (JSON), localStorage (user prefs, last sliders)
    v
 DO App Platform
    +-- API layer (FastAPI)
    +-- Poller worker (background, per active bbox)
    +-- Rolling buffer (Redis, 24h TTL)
    +-- Description cache (Redis, 10m TTL, keyed by slider config)
    +-- SQLite: airports, runways, complaint forms, aircraft registry cache
    +-- LLM client (Groq primary, Gemini fallback)
    |
    v
 ADSB.lol live ADS-B, OpenSky enrichment/fallback, Airplanes.live fallback,
 Mapbox (maps + geocoding), LLM provider, FAA registry
```

### 8.1 API endpoints

1. `GET /geocode?q=...` passthrough to Mapbox Geocoding (server-side).
2. `GET /airports/nearest?lat=...&lon=...`
3. `GET /airports/search?q=...`
4. `GET /scan` params:
   1. `airport_icao` (required)
   2. `user_lat`, `user_lon` (required)
   3. `ring_nm` (default 8)
   4. `pass_radius_nm` (default 0.5)
   5. `pass_ceiling_ft` (default 5000)
   6. `window` (one of `5m`, `30m`, `1h`, `6h`, `today`; default `1h`)
5. `GET /aircraft/{icao24}/detail` params:
   1. `airport_icao`, `user_lat`, `user_lon`, `window` (same as scan)
   2. `anger`, `niceness`, `respect`, `detail`, `local` (integers 0 to 10; default to "Concerned neighbor" preset)
   3. `preset` (optional string, overrides individual sliders if provided)
6. `GET /complaint_form?airport_icao=KBJC`

### 8.2 Deployment on DigitalOcean

1. DO App Platform: web service + worker service + Managed Redis (~$20/mo).
2. Lean: single $12/mo Droplet with Docker Compose (api, worker, redis, caddy).
3. Secrets via env vars.

## 9. External dependencies and limits

| Service | Tier | Relevant limit | Mitigation |
|---|---|---|---|
| ADSB.lol `/v2/lat/{lat}/lon/{lon}/dist/{nm}` | Open API | Dynamic load-based limits | Primary live source, merged bbox polling, circuit-breaker fallback |
| OpenSky `/states/all` | OAuth2/anonymous | Separate states credit bucket | Live fallback when ADSB.lol is unavailable |
| OpenSky `/flights/aircraft` | OAuth2 | Separate flights credit bucket, nightly batch availability | Enrichment only, cached per aircraft/time window |
| Airplanes.live `/v2/point/{lat}/{lon}/{radius}` | Open API | 1 request/second, no SLA | Last live fallback only |
| Mapbox GL JS | Free | 50k loads/month | Sufficient |
| Mapbox Geocoding | Free | 100k/month | Cache per address |
| Groq Cloud | Free | Generous RPM | 10m description cache per slider config |
| Gemini 1.5 Flash | Free | 15 RPM | Fallback |
| DO App Platform + Redis | ~$20/mo | n/a | Fits budget |

**Note on LLM volume:** With 5 sliders at 11 positions each, there are 161,051 possible combinations per aircraft, but in practice a user tries 2 to 5 variants per session. Description cache hit rate should sit above 70 percent once presets are popularized.

## 10. Data model

```
airports (icao PK, iata, name, city, country, lat, lon, elevation_ft, is_towered)
runways (icao, runway_id PK composite, lat_threshold, lon_threshold, heading_deg, length_ft)
complaint_forms (icao PK, form_url, phone, email, notes, last_verified)
aircraft_cache (icao24 PK, registration, type_icao, type_description, operator, last_updated)

-- Redis:
--   track:{icao24}           sorted set, score=unix_time, value=state_vector_json, TTL 24h
--   events:{airport}:{uloc}  sorted set, score=unix_time, value=event_json, TTL 24h
--   desc:{icao24}:{cfg_hash} string, value=generated_text, TTL 10m
```

## 11. Detection pseudocode (consolidated)

```python
def process_tick(track, airport, user_loc, params):
    samples = track.last_seconds(30)
    if not samples:
        return []
    events = []
    if in_ring(samples[-1], airport, params.ring_nm):
        events += detect_circles(track, airport, params)
        events += detect_touch_and_gos(track, airport, params)
    events += detect_passes_over_user(track, user_loc, params)
    return events

def score_aircraft(icao24, window):
    evs = load_events(icao24, window)
    base = (2.0*count(evs,"circle") + 1.5*count(evs,"tg")
          + 1.0*count(evs,"la") + 3.0*count(evs,"pass"))
    if window.seconds >= 3600:
        base += sum(tod_weight(e.time_local) for e in evs)
        base += sum(1.0 for e in evs if e.type=="pass" and e.min_alt_agl < 1000)
    return base
```

## 12. LLM prompt (v0.3)

```
You write per-incident aviation noise complaint descriptions for a
resident to paste into an official form. Produce 3 to 8 sentences
(length governed by the Detail slider). Use specific numbers and
local time. Never speculate about pilot intent. Never use profanity
or personal attacks. Blend the five tone sliders below into a
coherent voice.

TONE CONTROLS:
anger: {anger_band}
niceness: {niceness_band}
respect: {respect_band}
detail: {detail_band}
local_flavor: {local_band}

DATA:
airport: {airport_name} ({airport_icao})
user_location_label: {user_label}
time_window_label: {window_label}
callsign: {callsign}
icao24: {icao24}
aircraft_type: {type_description}
observed_from: {first_seen_local}
observed_to: {last_seen_local}
circles_count: {circles}
avg_loop_radius_nm: {avg_radius}
alt_band_ft: {alt_min}-{alt_max}
touch_and_go_count: {tg}
low_approach_count: {la}
passes_over_user_count: {passes}
min_altitude_over_user_ft_agl: {min_alt_user}
quiet_hours_events: {quiet}
peak_hours: {peak}
origin_airport: {origin_or_unknown}
```

**Example: "Concerned neighbor" preset, 1-hour window**

> In the last hour, Cessna 172 N123AB completed 4 circles within the Rocky Mountain Metro (KBJC) monitoring area and performed 3 touch-and-go operations. It passed directly over my home in Longmont twice, descending to 780 ft above ground on the second pass. Most of the activity happened between 18:00 and 19:00 this evening. The aircraft originated from KBJC. I would appreciate the airport's attention to this pattern, which has become a regular evening occurrence.

**Example: "Fed up resident" preset, same data**

> In just the last hour, N123AB (a Cessna 172 out of KBJC) has circled overhead 4 times, done 3 touch-and-gos, and buzzed directly over my house twice, the second pass at only 780 ft. This is during the dinner hour in a residential neighborhood in Longmont. It is becoming a near-daily problem and is genuinely disruptive. I am asking KBJC to address this pattern with the operator responsible.

**Example: "Formal complaint" preset, 6-hour window**

> Between 13:45 and 19:40 local time on today's date, aircraft N123AB, a Cessna 172 registered and operating out of Rocky Mountain Metropolitan Airport (KBJC), was observed conducting repeated pattern activity within the monitored 8 nm ring of the airport. During this period the aircraft completed 9 circles with an average loop radius of 1.4 nm, executed 7 touch-and-go operations on Runway 30L, and conducted 2 low approaches. The aircraft made 4 direct overflights of my residence in Longmont, Colorado, with a minimum observed altitude of 780 ft above ground level. Peak activity occurred between 07:00 and 09:00 and between 18:00 and 20:00 local time, with 1 event occurring during quiet hours. I respectfully request that the airport noise office review this activity and follow up with the operator. Thank you for your time and attention to this matter.

## 13. Rollout phases

1. **MVP (3 to 4 weeks solo):** dual-geography detectors, time window filter, flavor sliders with 5 presets, 10 seeded complaint forms.
2. **v1.1:** bookmarkable locations, shareable snapshot URLs, crowdsourced form registry.
3. **v1.2:** extend rolling window to 7 days; add "This week" and "This month" filters; weekly worst-offender digests.
4. **v2:** email alerts, multi-location watchlists, mobile PWA, community dashboards.

## 14. Risks and open questions

1. **Live ADS-B source coverage gaps:** show coverage confidence on the map and fall back across providers.
2. **Mode S only aircraft:** no position. Document.
3. **False positives from legitimate pattern work:** surface facts, user files the complaint.
4. **User location privacy:** truncate lat/lon to 2 decimal places in cache keys; no server-side logging of precise coordinates.
5. **Slider abuse / LLM jailbreaking:** the prompt has explicit guardrails (no profanity, no personal attacks, no pilot-intent speculation). Run a safety pass on generated output and redact if it violates guardrails.
6. **Runway dataset coverage:** seed from OurAirports; fall back to airport center if unknown.
7. **Description fatigue:** high-detail output can feel machine-generated. QA against real complaint letters during MVP testing.
8. **Form URL rot:** quarterly re-verification.

## 15. Success metrics

1. Time from landing on site to copied complaint text under 90 seconds (including slider tuning).
2. >= 60 percent of users who open the detail panel try at least one slider change.
3. >= 70 percent of flagged offenders have a valid origin airport label.
4. >= 80 percent of supported airports have a working complaint-form link.
5. Under $25/month operating cost at MVP traffic.

## 16. Appendix A: full slider-to-prompt band mappings

### Anger

| Band | Value | Prompt instruction |
|---|---|---|
| 0-1 | Dispassionate | "Use neutral, factual language. No emotional content." |
| 2-4 | Measured | "Maintain a measured tone; minor expressions of concern are acceptable." |
| 5-6 | Frustrated | "Express clear frustration; the reader should understand this affects the resident's quality of life." |
| 7-8 | Strong frustration | "Convey strong frustration and urgency; use emphatic phrasing but remain civil." |
| 9-10 | Serious anger | "Convey serious anger and exhaustion with the ongoing situation. No profanity, no personal attacks." |

### Niceness

| Band | Value | Prompt instruction |
|---|---|---|
| 0-1 | Cold | "Transactional and cold. No pleasantries." |
| 2-4 | Neutral | "Neutral; skip pleasantries but remain civil." |
| 5-6 | Friendly | "Acknowledge the reader as a person doing their job." |
| 7-8 | Warm | "Warm and cordial; acknowledge the shared goal of a livable community." |
| 9-10 | Very warm | "Very warm; express appreciation for the reader's work and willingness to engage." |

### Respect

| Band | Value | Prompt instruction |
|---|---|---|
| 0-1 | Blunt | "Direct and blunt; no honorifics, no thanks." |
| 2-4 | Direct | "Direct; skip formalities." |
| 5-6 | Professional | "Professional and appropriately formal." |
| 7-8 | Deferential | "Deferential; thank the reader for their time." |
| 9-10 | Highly deferential | "Highly deferential; formal salutation, explicit thanks, acknowledge authority's role." |

### Detail

| Band | Value | Prompt instruction |
|---|---|---|
| 0-2 | Headline | "3 sentences. Include only the highest-impact numbers (total events, min altitude)." |
| 3-5 | Summary | "4 to 5 sentences. Include event counts by type and peak hours." |
| 6-7 | Detailed | "5 to 6 sentences. Include all counts, altitudes, loop radius, and origin airport." |
| 8-10 | Exhaustive | "7 to 8 sentences. Include every metric in the data, specific timestamps, and runway used if known." |

### Local flavor

| Band | Value | Prompt instruction |
|---|---|---|
| 0-1 | Generic | "No place references beyond the airport name." |
| 2-4 | Light | "Mention the user's city once." |
| 5-6 | Moderate | "Mention the user's city and the neighborhood context (residential area, quiet street, etc.)." |
| 7-8 | Strong | "Frame the message as a resident of the specific community; mention local geography if relevant." |
| 9-10 | Heavy | "Write from a strong local-resident perspective; reference nearby landmarks or neighborhood character naturally, without dialect or slang." |

## 17. Appendix B: free LLM comparison

| Provider | Model | Free tier | Latency | Notes |
|---|---|---|---|---|
| Groq | Llama 3.3 70B Versatile | Generous | ~300ms | Best speed/quality ratio; handles slider blending well |
| Groq | Llama 3.1 8B Instant | Generous | ~150ms | Fine for default presets; may struggle with extreme slider combos |
| Google | Gemini 1.5 Flash | 15 RPM | ~500ms | Good fallback |

Recommendation: Groq 70B as primary (slider blending benefits from the larger model), Gemini Flash as fallback, deterministic template as final fallback.
