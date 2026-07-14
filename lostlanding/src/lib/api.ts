import type { LedgerResponse } from "./types";
import type { AircraftFeesResponse, LivePositionsResponse } from "./liveTypes";

// Points at the ledger sidecar (ledger-api/), NOT the main circlejerks API.
// The sidecar is a separate service/process/port on purpose — see
// ledger-api/app/db.py's module docstring for why the ledger never lives on
// the main API anymore.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "http://localhost:8100").replace(/\/+$/, "");

// Points at the MAIN circlejerks API's already-public /positions endpoint,
// via the same-origin `/live/*` proxy (see Caddyfile's `handle_path /live/*`
// block) -- this service never gets its own copy of that endpoint. Relative
// by default (matches production's same-origin, zero-CORS design); local
// full-stack dev proxies this through vite.config.ts to the main API on
// :8000, same pattern frontend/vite.config.ts already uses for its own /api.
export const LIVE_BASE = (import.meta.env.VITE_LIVE_BASE ?? "/live").replace(/\/+$/, "");

export class LedgerFetchError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "LedgerFetchError";
  }
}

export class LivePositionsFetchError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "LivePositionsFetchError";
  }
}

/**
 * Fetch the runway-use ledger for an airport. Throws LedgerFetchError on any
 * non-2xx response or network failure -- callers are expected to catch this
 * and render an explicit error state rather than fabricating data.
 */
export async function fetchLedger(icao: string, days = 30): Promise<LedgerResponse> {
  const url = `${API_BASE}/airports/${encodeURIComponent(icao)}/ledger?days=${encodeURIComponent(String(days))}`;

  let response: Response;
  try {
    response = await fetch(url);
  } catch (err) {
    throw new LedgerFetchError(
      err instanceof Error ? `Could not reach the ledger API: ${err.message}` : "Could not reach the ledger API.",
    );
  }

  if (!response.ok) {
    throw new LedgerFetchError(`Ledger API returned ${response.status} ${response.statusText}`, response.status);
  }

  return (await response.json()) as LedgerResponse;
}

/**
 * Per-aircraft runway-use totals for the live map's price tags and the hero
 * ticker -- ledger-api/app/fees.py's GET /airports/{icao}/aircraft-fees.
 * Tail numbers only; never a registrant/owner name or address.
 */
export async function fetchAircraftFees(icao: string): Promise<AircraftFeesResponse> {
  const url = `${API_BASE}/airports/${encodeURIComponent(icao)}/aircraft-fees`;

  let response: Response;
  try {
    response = await fetch(url);
  } catch (err) {
    throw new LedgerFetchError(
      err instanceof Error ? `Could not reach the ledger API: ${err.message}` : "Could not reach the ledger API.",
    );
  }

  if (!response.ok) {
    throw new LedgerFetchError(`Ledger API returned ${response.status} ${response.statusText}`, response.status);
  }

  return (await response.json()) as AircraftFeesResponse;
}

/**
 * Live aircraft positions near an airport -- the MAIN circlejerks API's
 * `/positions` endpoint, reached same-origin via the `/live/*` proxy. See
 * this module's LIVE_BASE doc comment for why this is a distinct base from
 * API_BASE (a different service entirely, not the ledger sidecar).
 */
export async function fetchLivePositions(
  icao: string,
  userLat: number,
  userLon: number,
  ringNm = 8,
): Promise<LivePositionsResponse> {
  const params = new URLSearchParams({
    airport_icao: icao,
    user_lat: String(userLat),
    user_lon: String(userLon),
    ring_nm: String(ringNm),
  });
  const url = `${LIVE_BASE}/positions?${params.toString()}`;

  let response: Response;
  try {
    response = await fetch(url);
  } catch (err) {
    throw new LivePositionsFetchError(
      err instanceof Error
        ? `Could not reach live positions: ${err.message}`
        : "Could not reach live positions.",
    );
  }

  if (!response.ok) {
    throw new LivePositionsFetchError(
      `Live positions returned ${response.status} ${response.statusText}`,
      response.status,
    );
  }

  return (await response.json()) as LivePositionsResponse;
}
