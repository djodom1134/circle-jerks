/**
 * Types for the KLMO runway-use ledger API.
 *
 * GET {VITE_API_BASE}/airports/{icao}/ledger?days=30
 *
 * This mirrors the backend response exactly. Do not add fields the API
 * doesn't send, and do not rename fields for display purposes -- components
 * should read these verbatim and format at render time only.
 */

export interface LedgerWindow {
  days: number;
  start_day: string; // YYYY-MM-DD
  end_day: string; // YYYY-MM-DD
}

export interface RunwayUseByType {
  landing: number;
  touch_and_go: number;
  low_approach: number;
  /** The backend may also send `takeoff` for context, but it is never part of runway_uses. */
  takeoff?: number;
}

export interface DwellSummary {
  median_seconds: number | null;
  sample_size: number;
  landings: number;
  coverage: number; // 0..1
}

/**
 * Of the landing->takeoff pairs we could actually measure, how many stayed
 * `min_seconds` or longer (a real visit) versus a quick turn? `paired` (=
 * stayed + quick_turn) is the ONLY honest denominator for `stayed` and
 * `quick_turn` -- most landings have no matching takeoff in view at all
 * (still on the field, or we missed the departure). `coverage` = paired /
 * landings, published so a reader can see how much of the activity this
 * speaks for. `min_seconds` (1200s / 20 minutes) is a judgment call this
 * project is making, not an FAA or industry standard -- never present it as
 * one.
 */
export interface VisitSummary {
  min_seconds: number;
  stayed: number;
  quick_turn: number;
  paired: number;
  landings: number;
  coverage: number; // 0..1
  median_stay_seconds: number | null;
}

/**
 * A PROJECTED annual runway-use rate, computed from our own measured data
 * only -- never from the FAA's published Form-5010 operations estimate (see
 * ledger-api/app/ledger.py's `annual_projection` docstring for why that
 * figure cannot be the input here). Unlike every other field on this
 * response, this one is not a floor: it assumes a short, present-day rate
 * holds for a full year, and this project's history so far sits inside
 * Colorado's peak flying season -- render that caveat wherever this number
 * appears, every time.
 */
export interface AnnualProjection {
  counting_since: number | null; // unix seconds
  days_of_data: number;
  runway_uses_to_date: number;
  observed_daily_rate: number;
  annualization_days: number;
  projected_annual_runway_uses: number;
}

export interface LedgerSummary {
  runway_uses: number;
  by_type: RunwayUseByType;
  unique_aircraft: number;
  local_aircraft: number;
  non_local_aircraft: number;
  unclassified_aircraft: number;
  dwell: DwellSummary;
}

export interface DailyPoint {
  date: string; // YYYY-MM-DD
  runway_uses: number;
}

export type OperatorLocality = "local" | "non_local" | "unclassified";

export interface LocalityEvidence {
  code: string;
  text: string;
}

export interface OperatorAircraft {
  tail: string;
  runway_uses: number;
}

export interface Operator {
  operator: string;
  owner_type: string;
  runway_uses: number;
  aircraft_count: number;
  aircraft: OperatorAircraft[];
  locality: OperatorLocality;
  locality_evidence: LocalityEvidence[];
}

export interface MethodologyDefinitions {
  landing: string;
  touch_and_go: string;
  low_approach: string;
}

export interface AttributionSource {
  name: string;
  license: string;
  url: string;
}

export interface Methodology {
  billable_unit: string;
  billable_unit_label: string;
  billable_unit_description: string;
  definitions: MethodologyDefinitions;
  floor_disclaimer: string;
  data_since: number | null; // unix seconds
  locality_confidence_threshold: number;
  locality_lookback_days: number;
  attribution: Record<string, AttributionSource>;
}

export interface LedgerResponse {
  airport_icao: string;
  timezone: string;
  window: LedgerWindow;
  summary: LedgerSummary;
  daily: DailyPoint[];
  operators: Operator[];
  visits: VisitSummary;
  projection: AnnualProjection;
  methodology: Methodology;
}
