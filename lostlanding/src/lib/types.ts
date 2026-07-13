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
  methodology: Methodology;
}
