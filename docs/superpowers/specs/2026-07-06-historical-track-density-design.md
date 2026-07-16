# Historical Track Density View — Design

**Date:** 2026-07-06
**Status:** Approved (Approach A)
**Branch context:** `codex/production-observability`

## Problem

The map shows only *current-window* traffic (the top-30 offenders from the
latest `/scan`, over windows maxing at ~24h). Users want to see **1–7 days of
track data overlaid** to get a feel for where aircraft actually fly — the
recurring pathways around the airport and over neighborhoods — in a way that
stays legible against the streets and the VNAP pattern oval underneath.

The ask has two halves:
1. Represent multiple days of tracks *without* turning the map into an opaque
   scribble — via thinned individual lines, an averaged/representative path, or
   a density-based graphical representation.
2. Ship it as a map toggle (a new button next to the existing 🔥 noise-heatmap
   button) with selectable timeframe and visualization mode.

## What already exists (and what we reuse)

- **Map** (`frontend/src/components/MapView.tsx`, OpenLayers): renders base OSM
  tiles, the 8 nm airport ring, home pin, VNAP pattern ovals, per-aircraft aged
  tracks (altitude-colored), live aircraft markers, and a **canvas noise
  heatmap** toggled by the 🔥 Flame button in `.map-controls`. The noise heatmap
  rasterizes an L_eq field onto `<canvas className="heatmap-canvas">` on every
  `postrender`, interpolating sub-points along each track segment. **We reuse
  this canvas + sub-segment interpolation pattern for the Density mode.**
- **Track storage is tiered:** Redis hot (~4h, `track_ttl_seconds`) →
  SQLite `track_archive` cold. The worker stores samples for **all** aircraft in
  the airport bbox (not just offenders), and `archive.py` copies them to
  `track_archive`. So area-wide density is meaningful.
- `track_archive` columns: `icao24, timestamp, lat, lon, altitude_ft,
  baro_altitude_ft, geo_altitude_ft, heading_deg, vertical_rate_fpm, callsign,
  in_window, source, archived_at`. Indexed on `timestamp` and `(icao24,
  timestamp)`. Everything a polyline needs is present.
- `/scan`'s `tracks_for_response` only returns the top-30 current offenders — not
  usable for an area-wide multi-day view. **This feature needs a new endpoint.**

### The retention gap (important)

Cold storage currently retains only **24h**
(`archive.DEFAULT_ARCHIVE_HORIZON_SECONDS = 24 * 3600`, pruned hourly), despite
code comments aspiring to 7 days. To reach the 7-day range we must **extend the
archive/prune horizon**. History **accrues forward from deploy** — it cannot
appear retroactively, so the full 7-day range fills in over the first week.

## Decisions (approved defaults)

1. **Data horizon → 7 days.** Introduce a configurable setting
   `track_archive_horizon_days` (default **7**) and wire it through
   `archive_loop` (both archive and prune). Disk grows to ~7× the current
   cold-tier size — modest for a single field's GA traffic.
2. **Coverage area → all archived traffic inside the airport ring bbox
   (~8 nm)**, filtered by an **altitude ceiling** (default **5000 ft AGL**) so
   the view is the noise-relevant low/pattern traffic, not high overflights.
3. **Relationship to the noise heatmap → separate, mutually exclusive toggle.**
   Turning the historical view on turns the noise heatmap off, and vice-versa.
4. **While the historical view is on:** hide the live aged-track lines and moving
   aircraft markers (as the noise heatmap already does), but keep the airport
   ring, home pin, and VNAP pattern ovals visible so density lines up against
   the oval and the streets.

## Approach A (chosen)

Server returns **simplified per-flight polylines** for the bbox over N days; the
client renders all three modes from that single payload. Density rasterizes
client-side on the existing heatmap canvas (so it re-sharpens on zoom);
average-path is a testable client geometry function. The payload-size risk on
busy fields is mitigated server-side by RDP simplification, the altitude
ceiling, and a hard track cap.

Rejected: **B** (server pre-aggregated density grid) — fixed resolution, won't
sharpen on zoom, two code paths, and average-path still needs raw lines.
Rejected: **C** (hybrid raw lines + server grid) — more moving parts than v1
needs (YAGNI).

## Visualization modes

- **Lines** — every flight as a faint, altitude-colored hairline
  (reuse `colorFromAltitudeAgl`) at low opacity. Overplotting reveals density;
  the basemap and oval remain visible through it.
- **Density** — the same polylines rasterized into a smooth line-*count*
  heatmap on the canvas. This is **geometric traffic density**, distinct from
  the dB noise heatmap. Each track contributes a Gaussian halo along its
  interpolated sub-points; cell color scales with how many distinct flights
  cross it. Its own legend ("fewer ← flights → more"), not the dB legend.
- **Average path** — tracks grouped by direction (inbound bearing to the
  airport, bucketed; runway association is a later refinement), each group
  resampled to a fixed number of points along arc-length and averaged into one
  bold representative centerline per group. Maximum clarity.

## Architecture

### Data layer
- **Setting:** `track_archive_horizon_days: int = 7` in `settings.py` (env
  `TRACK_ARCHIVE_HORIZON_DAYS`). `archive_loop` uses
  `horizon_seconds = track_archive_horizon_days * 86400` for both archive and
  prune passes (replacing the hard-coded `DEFAULT_ARCHIVE_HORIZON_SECONDS` at
  the call site; the constant stays as the default arg).
- **New query:** `db.read_track_archive_bbox(conn, min_lat, max_lat, min_lon,
  max_lon, start_ts, end_ts, ceiling_ft_msl, row_cap)` → rows for all icao24 in
  bbox + time, ordered `icao24, timestamp`, with a hard `row_cap` (e.g. 200k)
  so a runaway query can't wedge the process. Altitude filtering uses MSL
  (`altitude_ft`) with the ceiling converted from AGL + airport elevation at the
  call site. Uses the `timestamp` index; lat/lon filtered in the `WHERE`.

### Backend endpoint
`GET /airports/{icao}/track-history?days=N&ceiling_ft=5000`
- Validate `days` ∈ [1, 7] (clamp), `ceiling_ft` sane default 5000 (AGL).
- Resolve airport → bbox from the ring radius (~8 nm) and elevation.
- Read archive rows via the new bbox query.
- **Split each aircraft's samples into flights** on a time gap
  (`> ~20 min`) so one plane that visited twice over the week yields two tracks.
- **RDP-simplify** each flight polyline (new `backend/app/simplify.py`,
  unit-tested) with an epsilon tuned in degrees (~1e-4). Drop flights with < 2
  points after simplification.
- **Cap** total tracks (default ~1500). Prefer most-recent when over cap; set
  `truncated: true` and report `total_tracks` so the UI states honestly what
  fraction is shown.
- Response:
  ```json
  {
    "airport": {"icao": "...", "lat": 0, "lon": 0, "elevation_ft": 0},
    "days": 7,
    "ceiling_ft": 5000,
    "window": {"start_ts": 0, "end_ts": 0},
    "tracks": [
      {"icao24": "...", "callsign": "...",
       "samples": [{"lat": 0, "lon": 0, "altitude_ft": 0, "timestamp": 0}]}
    ],
    "total_tracks": 4200,
    "truncated": true
  }
  ```

### Frontend
- **API client:** `getTrackHistory(icao, days, ceilingFt)` + `TrackHistoryResponse`
  / `HistoricalTrack` types in `lib/api.ts`.
- **State (App.tsx):** replace the single `showHeatmap` boolean with an overlay
  selector so noise-heatmap and historical are mutually exclusive
  (e.g. `mapOverlay: "none" | "noise" | "history"`), plus `historyDays` (1–7)
  and `historyMode: "lines" | "density" | "average"`. Fetch track history when
  the historical overlay is active (and refetch on airport/days/ceiling change);
  do **not** put it on the 5s scan interval — it's heavier and static-ish.
- **Controls:** new `.map-icon-toggle` button next to 🔥 (lucide `Spline` or
  `Route`). When active, a small floating panel (styled like the dB legend)
  offers the **1–7 day** timeframe and the **Lines / Density / Average** mode
  switch, plus a "showing X of Y flights" caption when `truncated`.
- **MapView:** new `history` prop (`{ tracks, mode, ... } | null`). Rendering:
  - *Lines*: OL vector features, faint altitude-colored strokes.
  - *Density*: canvas rasterization reusing the heatmap canvas + the existing
    sub-segment interpolation; color by per-cell distinct-flight count; own
    legend.
  - *Average*: bold centerlines from `lib/averagePath.ts`.
  - Suppress live aged-tracks + aircraft markers while active (as heatmap does);
    keep ring/home/patterns.

## Testing
- **Backend:** `read_track_archive_bbox` (bbox + ceiling + time filtering);
  flight gap-splitting; RDP `simplify` (collinear reduction, endpoint
  preservation, epsilon behavior); endpoint (days clamp, ceiling filter,
  truncation flag + `total_tracks`).
- **Frontend:** flight-splitting and `averagePath` geometry unit tests (matches
  existing `*.test.ts` convention: `spline.test.ts`, `patternGeometry.test.ts`).
  Density canvas verified manually.

## Out of scope (v1)
- Retroactive backfill of >24h history (data accrues forward only).
- Runway-accurate average-path clustering (v1 buckets by inbound bearing).
- Server-side pre-aggregated density grid (Approach B) — revisit only if
  payloads prove too large on the busiest fields.
- Per-cell ambient/road-noise fusion (that's the noise heatmap's concern).
