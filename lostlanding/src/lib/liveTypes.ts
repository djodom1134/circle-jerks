/**
 * Types for the two LIVE endpoints the map/ticker consume, as distinct from
 * the (already-typed, in types.ts) 30-day ledger snapshot:
 *
 *  - GET /live/positions?airport_icao=...&user_lat=...&user_lon=...&ring_nm=...
 *    Same-origin proxy (see Caddyfile's `handle_path /live/*`) straight to the
 *    MAIN circlejerks API's already-public `/positions` endpoint. This
 *    service never gets its own copy of that endpoint -- see lib/api.ts.
 *
 *  - GET {VITE_API_BASE}/airports/{icao}/aircraft-fees
 *    THIS service's own endpoint (ledger-api/app/fees.py). Tail numbers only
 *    -- never a registrant/owner name or address.
 */

export interface LiveTrackSample {
  timestamp: number;
  lat: number;
  lon: number;
  heading_deg: number | null;
  altitude_ft: number | null;
  vertical_rate_fpm: number | null;
  in_window: boolean;
}

export interface LiveTrack {
  icao24: string;
  callsign: string;
  samples: LiveTrackSample[];
}

export interface LivePositionsWindow {
  code: string;
  label: string;
  start_ts: number;
  end_ts: number;
  seconds: number;
}

export interface LivePositionsResponse {
  airport_icao: string;
  window: LivePositionsWindow;
  tracks: LiveTrack[];
  active_now: number;
  updated_at: number;
}

/** One aircraft's runway-use counts. `today` is a ROLLING 24h count (see
 * AircraftFeesToday below), never a local calendar day. `month`/`year` ARE
 * local-calendar (month-to-date / year-to-date). `total` is since
 * `counting_since` -- our observation window, NEVER call it "lifetime". */
export interface AircraftFeeEntry {
  tail: string;
  total: number;
  today: number;
  month: number;
  year: number;
}

export interface AircraftFeesToday {
  /** Always "rolling_24h" -- a trailing window, not a local calendar day. */
  window: string;
  since_ts: number;
  until_ts: number;
  runway_uses: number;
}

export interface AircraftFeesRateWindow {
  seconds: number;
  runway_uses: number;
  uses_per_second: number;
}

export interface AircraftFeesResponse {
  airport_icao: string;
  timezone: string;
  /** MIN(timestamp) across all operations for this airport -- when OUR
   * observation began, not the aircraft's manufacture date. */
  counting_since: number | null;
  today: AircraftFeesToday;
  rate_window: AircraftFeesRateWindow;
  aircraft: Record<string, AircraftFeeEntry>;
}
