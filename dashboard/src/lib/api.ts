import type {
  DailyOperations, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse,
} from "./types";

// Same-origin proxy path. Production Caddy maps /api/* -> /v1 and injects the
// API key; dev's vite.config.ts proxies /api -> the public /v1. The frontend
// never holds a key. Trailing slashes stripped so path joins are clean.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "/api").replace(/\/+$/, "");

export class DashboardFetchError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "DashboardFetchError";
    this.status = status;
  }
}

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`);
  } catch (err) {
    throw new DashboardFetchError(
      err instanceof Error ? `Could not reach the API: ${err.message}` : "Could not reach the API.",
    );
  }
  if (!response.ok) {
    throw new DashboardFetchError(`API returned ${response.status} ${response.statusText}`, response.status);
  }
  return (await response.json()) as T;
}

const ap = (icao: string) => `/airports/${encodeURIComponent(icao)}`;

export function fetchDailyOperations(
  icao: string, opts?: { from?: string; to?: string; origin?: string },
): Promise<DailyOperations> {
  return getJson<DailyOperations>(`${ap(icao)}/daily-operations${query({ from: opts?.from, to: opts?.to, origin: opts?.origin })}`);
}

export function fetchOrigins(icao: string, opts?: { from?: string; to?: string }): Promise<OriginsResponse> {
  return getJson<OriginsResponse>(`${ap(icao)}/origins${query({ from: opts?.from, to: opts?.to })}`);
}

export function fetchWorstOffenders(icao: string): Promise<WorstOffendersResponse> {
  return getJson<WorstOffendersResponse>(`${ap(icao)}/worst-offenders`);
}

export function fetchHourlyProfile(
  icao: string, opts?: { from?: string; to?: string },
): Promise<HourlyProfileResponse> {
  return getJson<HourlyProfileResponse>(`${ap(icao)}/hourly-profile${query({ from: opts?.from, to: opts?.to })}`);
}
