/**
 * The data contract this dashboard targets. Each shape mirrors a /v1 endpoint
 * (reached via the same-origin /api proxy). These endpoints are owned and
 * implemented outside this repo's frontend; do not add fields the contract
 * does not define, and do not rename fields for display — components read
 * these verbatim and format at render time only.
 *
 * See docs/superpowers/specs/2026-07-22-klmo-dashboard-design.md § Data contract.
 */

export const OPERATION_TYPES = ["landing", "touch_and_go", "low_approach", "takeoff"] as const;
export type OperationType = (typeof OPERATION_TYPES)[number];

export const LOCALITIES = ["local", "out_of_town", "unclassified"] as const;
export type Locality = (typeof LOCALITIES)[number];

/** Per-locality counts for one operation type on one day. */
export type LocalityCounts = Record<Locality, number>;

/** Every operation type's per-locality counts for one day. */
export type DayCounts = Record<OperationType, LocalityCounts>;

export interface DailyOperationsDay {
  date: string; // YYYY-MM-DD in the airport's local timezone
  counts: DayCounts;
}

/** GET /api/airports/{icao}/daily-operations?from=&to= */
export interface DailyOperations {
  airport_icao: string;
  timezone: string;
  /** The bounds of data that actually exists; days outside are "no data yet". */
  coverage: { min_day: string; max_day: string };
  types: OperationType[];
  localities: Locality[];
  days: DailyOperationsDay[];
}

export interface OriginRow {
  icao: string;
  label: string;
  arrivals: number;
}

/** GET /api/airports/{icao}/origins?from=&to= */
export interface OriginsResponse {
  airport_icao: string;
  window: { from: string; to: string };
  total_out_of_town: number;
  origins: OriginRow[];
  other: { count: number; arrivals: number };
}

/** GET /api/airports/{icao}/worst-offenders (existing endpoint, reused). */
export interface WorstOffenderRow {
  icao24: string;
  registration: string | null;
  report_count: number;
}
export interface WorstOffendersResponse {
  airport_icao: string;
  offenders: WorstOffenderRow[];
}

/** GET /api/airports/{icao}/hourly-profile?from=&to= (optional endpoint). */
export interface HourlyProfileResponse {
  airport_icao: string;
  window: { from: string; to: string };
  hours: { hour: number; operations: number }[]; // 0..23
}
