import type { LedgerResponse } from "./types";

// Points at the ledger sidecar (ledger-api/), NOT the main circlejerks API.
// The sidecar is a separate service/process/port on purpose — see
// ledger-api/app/db.py's module docstring for why the ledger never lives on
// the main API anymore.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "http://localhost:8100").replace(/\/+$/, "");

export class LedgerFetchError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
    this.name = "LedgerFetchError";
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
