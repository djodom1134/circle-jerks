export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export type WindowCode = "5m" | "30m" | "1h" | "6h" | "today";

export interface Airport {
  icao: string;
  iata?: string | null;
  name: string;
  city: string;
  country: string;
  lat: number;
  lon: number;
  elevation_ft: number;
  is_towered: boolean;
  distance_nm?: number;
}

export interface ScanParams {
  airport_icao: string;
  user_lat: number;
  user_lon: number;
  ring_nm: number;
  pass_radius_nm: number;
  pass_ceiling_ft: number;
  window: WindowCode;
}

export interface Offender {
  icao24: string;
  callsign: string;
  score: number;
  circles: number;
  touch_and_gos: number;
  low_approaches: number;
  passes: number;
  first_event_at: number;
  last_event_at: number;
  min_altitude_ft_agl?: number | null;
  avg_altitude_over_user_ft_agl?: number | null;
  min_altitude_over_user_ft_agl?: number | null;
  origin_city?: string | null;
  origin_airport_icao?: string | null;
  origin_label?: string | null;
  origin_source?: string | null;
  report_count?: number;
  /** Average deviation from the matched VNAP circle pattern (nm), if a pattern exists. */
  deviation_mean_nm?: number | null;
  /** True if this aircraft caused an against-the-wind runway change in the window. */
  is_cowboy?: boolean;
  /** Breakdown of T&Gs / low approaches by runway_id, e.g. {"11": 3, "29": 7}. */
  runway_breakdown?: Record<string, number>;
  /** Resolved owner class: community override if present, else registry-inferred. */
  owner_class?: string;
  owner_source?: string;
  aircraft_type?: string | null;
  is_flight_school?: boolean;
  /** VNAP infraction score 0-100 (0 = clean, climbs with noise-abatement violations), null if unmeasured. */
  vnap_score?: number | null;
  /** Per-axis VNAP violation sub-scores (0 = clean per axis), for the map spider diagram. */
  vnap_scores?: Record<string, number | null> | null;
  /** Against-wind runway changes attributed to this aircraft in the window. */
  cowboy_count?: number;
}

export interface TrackSample {
  timestamp: number;
  lat: number;
  lon: number;
  heading_deg?: number | null;
  altitude_ft?: number | null;
  vertical_rate_fpm?: number | null;
  in_window: boolean;
}

export interface Track {
  icao24: string;
  callsign: string;
  samples: TrackSample[];
}

export interface ScanResponse {
  monitor_hash: string;
  window: { code: WindowCode; label: string; start_ts: number; end_ts: number; seconds: number };
  airport: Airport;
  user_location: { lat: number; lon: number; hash: string };
  counters: {
    circles: number;
    touch_and_gos: number;
    low_approaches: number;
    passes: number;
    offenders_active_now: number;
    unique_aircraft: number;
    label: string;
  };
  offenders: Offender[];
  events: Array<Record<string, unknown>>;
  histogram: Array<Record<string, number | string>>;
  tracks: Track[];
  historical_backfill?: {
    enabled: boolean;
    available?: boolean;
    reason?: string;
    requested: number;
    fetched: number;
    skipped_cached: number;
    remaining?: number;
    states_seen?: number;
    limit_seconds?: number;
    resolution_seconds?: number;
    coverage_start_ts?: number;
    coverage_end_ts?: number;
    coverage_complete?: boolean;
    complete_for_requested_window?: boolean;
    backing_off?: boolean;
    retry_after_seconds?: number;
  };
}

export interface ConfigResponse {
  default_airport_icao: string;
  buy_me_coffee_url?: string | null;
  presets: Record<string, ToneSliders>;
}

export interface ToneSliders {
  anger: number;
  niceness: number;
  respect: number;
  detail: number;
  local: number;
}

export interface ComplaintResponse {
  icao24: string;
  sliders: ToneSliders;
  source: "groq" | "fallback" | "cache";
  text: string;
  metadata: Record<string, unknown>;
}

export interface ActivityAircraft {
  icao24: string;
  callsign?: string | null;
  circles?: number;
  touch_and_gos?: number;
  low_approaches?: number;
  passes_over_user?: number;
  origin_airport_icao?: string | null;
  origin_label?: string | null;
}

export interface ActivityContext {
  visitor_id: string;
  airport_icao?: string | null;
  user_lat?: number | null;
  user_lon?: number | null;
}

export interface AdminDashboardResponse {
  generated_at: number;
  active_window_seconds: number;
  summary: {
    submissions: number;
    aircraft_reports: number;
    distinct_aircraft: number;
    submitters: number;
    current_users: number;
    airports: number;
  };
  current_users: Array<{
    visitor_id: string;
    first_seen: number;
    last_seen: number;
    ip_address?: string | null;
    path?: string | null;
    airport_icao?: string | null;
    airport_city?: string | null;
    user_lat?: number | null;
    user_lon?: number | null;
    submission_count: number;
  }>;
  recent_submissions: Array<{
    id: number;
    created_at: number;
    visitor_id?: string | null;
    ip_address?: string | null;
    airport_icao?: string | null;
    airport_name?: string | null;
    airport_city?: string | null;
    user_lat?: number | null;
    user_lon?: number | null;
    window_code?: string | null;
    mode?: string | null;
    text: string;
    text_hash: string;
    target_count: number;
    aircraft: Array<ActivityAircraft & { registration?: string | null }>;
  }>;
  aircraft_reports: Array<{
    icao24: string;
    callsign?: string | null;
    registration?: string | null;
    type_icao?: string | null;
    operator?: string | null;
    report_count: number;
    first_reported_at: number;
    last_reported_at: number;
  }>;
  ip_history: Array<{
    ip_address: string;
    submissions: number;
    first_submission_at: number;
    last_submission_at: number;
    visitors: number;
    active_visitors: number;
  }>;
  locations: Array<{
    visitor_id: string;
    ip_address?: string | null;
    airport_icao?: string | null;
    airport_city?: string | null;
    user_lat: number;
    user_lon: number;
    first_seen: number;
    last_seen: number;
    submission_count: number;
  }>;
  airports: Array<{
    airport_icao: string;
    name?: string | null;
    city?: string | null;
    submissions: number;
    submitters: number;
    last_submission_at: number;
  }>;
}

const REQUEST_TIMEOUT_MS = 60000;

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailToMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) return String((item as { msg: unknown }).msg);
        return typeof item === "string" ? item : null;
      })
      .filter(Boolean)
      .join("; ");
  }
  return null;
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  const text = await response.text();
  let message = text || response.statusText;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    message = detailToMessage(parsed.detail) ?? message;
  } catch {
    // Keep the plain response text.
  }
  return new ApiError(response.status, message || "Request failed");
}

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init?: RequestInit & { timeoutMs?: number },
) {
  const controller = new AbortController();
  const timeoutMs = init?.timeoutMs ?? REQUEST_TIMEOUT_MS;
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(408, "Request timed out");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function getJson<T>(path: string, timeoutMs?: number): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, { timeoutMs });
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs,
  });
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  return response.json() as Promise<T>;
}

async function putJson<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs,
  });
  if (!response.ok) throw await errorFromResponse(response);
  return response.json() as Promise<T>;
}

// Complaint generation has a tight timeout — if Groq doesn't answer fast,
// the caller falls back to the deterministic browser-side draft instead of
// leaving the user staring at a spinner.
const COMPLAINT_TIMEOUT_MS = 5000;

async function adminJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: init?.body ? { "Content-Type": "application/json" } : init?.headers
  });
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  return response.json() as Promise<T>;
}

export function getConfig() {
  return getJson<ConfigResponse>("/config");
}

export interface SponsorsResponse {
  configured: boolean;
  support_url?: string | null;
  source: string;
  supporters: Array<{
    name: string;
    coffees: number;
    message?: string | null;
    supported_at?: string | null;
  }>;
  total?: number;
  error?: string;
}

export function getSponsors() {
  return getJson<SponsorsResponse>("/sponsors");
}

export interface RepeatOffender {
  icao24: string;
  callsign?: string | null;
  registration?: string | null;
  type_icao?: string | null;
  type_description?: string | null;
  operator?: string | null;
  report_count: number;
  first_reported_at: number;
  last_reported_at: number;
  total_circles: number;
  total_touch_and_gos: number;
  total_low_approaches: number;
  total_passes_over_user: number;
  origin_airport_icao?: string | null;
  origin_label?: string | null;
}

export interface RepeatOffendersResponse {
  min_reports: number;
  count: number;
  aircraft: RepeatOffender[];
}

export function getRepeatOffenders(limit = 10) {
  return getJson<RepeatOffendersResponse>(`/repeat_offenders?limit=${limit}`);
}

export interface WorstOffender {
  icao24: string;
  tail: string;
  total_circles: number;
  vnap_score: number;
  product: number;
  report_count: number;
  last_reported_at: number | null;
  worst_axis: string | null;
  worst_axis_score: number | null;
  aircraft_type?: string | null;
  owner_class?: string | null;
}

export interface WorstOffendersResponse {
  source_icao: string;
  resolved_icao: string;
  resolved_label: string;
  is_fallback: boolean;
  offenders: WorstOffender[];
}

export function getWorstOffenders(icao: string, limit = 5) {
  return getJson<WorstOffendersResponse>(`/airports/${icao}/worst_offenders?limit=${limit}`);
}

export interface OnlineResponse {
  count: number;
}

export function getOnlineCount() {
  return getJson<OnlineResponse>("/activity/online");
}

export interface AtcFeed {
  id: string;
  label: string;
  stream_url: string;
}

export interface AtcFeedsResponse {
  airport_icao: string;
  feeds: AtcFeed[];
  external_search_url: string;
  note?: string;
}

export interface LiveStatusResponse {
  overall: "ok" | "degraded" | "down" | "unknown";
  serving_source: string | null;
  serving_state: "primary" | "fallback" | "unknown";
  primary: string | null;
  paid_configured: { adsbx: boolean; flightaware: boolean };
  paid_healthy: { adsbx: boolean };
  updated_at: number;
}

export function getLiveStatus() {
  return getJson<LiveStatusResponse>("/live_status");
}

export function getAtcFeeds(airportIcao: string) {
  return getJson<AtcFeedsResponse>(`/atc_feeds?airport_icao=${encodeURIComponent(airportIcao)}`);
}

export function nearestAirport(lat: number, lon: number) {
  return getJson<Airport>(`/airports/nearest?lat=${lat}&lon=${lon}`);
}

export function searchAirports(q: string) {
  return getJson<{ airports: Airport[] }>(`/airports/search?q=${encodeURIComponent(q)}`);
}

export function geocode(q: string) {
  return getJson<{
    features: Array<{
      id: string;
      place_name: string;
      center: [number, number];
    }>;
  }>(`/geocode?q=${encodeURIComponent(q)}`);
}

export function reverseGeocode(lat: number, lon: number) {
  return getJson<{ display_name: string | null; short_name: string | null }>(
    `/reverse_geocode?lat=${encodeURIComponent(String(lat))}&lon=${encodeURIComponent(String(lon))}`
  );
}

export interface WindSummary {
  airport_icao: string;
  hours: number;
  current: {
    observed_at_unix: number | null;
    report_time: string | null;
    wind_from_dir_degrees: number | null;
    wind_speed_kt: number | null;
    wind_gust_kt: number | null;
    raw_metar: string | null;
  } | null;
  average: {
    wind_from_dir_degrees: number | null;
    wind_speed_kt: number | null;
    wind_gust_max_kt: number | null;
    sample_count: number;
  } | null;
  error: string | null;
}

export function getWindSummary(airportIcao: string, hours: number) {
  return getJson<WindSummary>(
    `/weather/wind?airport_icao=${encodeURIComponent(airportIcao)}&hours=${hours}`
  );
}

export interface BackfillStatus {
  running: boolean;
  ever_started: boolean;
  airport_icao: string;
  started_at?: number;
  elapsed_seconds?: number;
  estimated_total_seconds?: number;
  eta_seconds?: number;
  completed_at?: number;
  samples_written?: number;
  mode?: "cold_start" | "adsblol_cold_start" | "normal" | null;
  source?: "flightaware" | "adsblol" | null;
  error?: boolean;
}

export function getBackfillStatus(airportIcao: string) {
  return getJson<BackfillStatus>(
    `/backfill_status?airport_icao=${encodeURIComponent(airportIcao)}`
  );
}

export function getAirportSosaUrl(icao: string) {
  return getJson<{ url: string; source: "curated" | "verified-heuristic" | "fallback" }>(
    `/airports/${encodeURIComponent(icao)}/sosa_url`
  );
}

export type OwnerType =
  | "individual"
  | "llc"
  | "corporation"
  | "government"
  | "flight_school"
  | "university"
  | "club"
  | "trust"
  | "skydiving"
  | "commercial_airline"
  | "unknown";

export interface AircraftProfile {
  identity: {
    nNumber: string | null;
    icaoHex: string | null;
    callsign: string | null;
    identityConfidence: number;
    identityNotes: string[];
  };
  aircraft: {
    manufacturer: string | null;
    model: string | null;
    yearManufactured: number | null;
    engineType: string | null;
    typeAircraft: string | null;
    category: string;
  };
  registration: {
    status: string | null;
    statusCode: string | null;
    expirationDate: string | null;
    certificateIssueDate: string | null;
    isExpired: boolean;
    isDeregistered: boolean;
  };
  registrant: {
    name: string | null;
    city: string | null;
    state: string | null;
    country: string | null;
    ownerType: OwnerType;
    ownerTypeConfidence: number;
    ownerTypeReason: string | null;
  };
  airmen: {
    lookupAvailable: boolean;
    lookupUrl: string | null;
    lookupLabel: string | null;
    reason: string;
    disclaimer: string;
  };
  disclaimers: string[];
  sources: Array<{ name: string; updatedAt?: string | null; retrievedAt: string }>;
  profileVersion: string;
}

export function getAircraftProfile(params: {
  nNumber?: string | null;
  icaoHex?: string | null;
  callsign?: string | null;
}) {
  const qs = new URLSearchParams();
  if (params.nNumber) qs.set("nNumber", params.nNumber);
  if (params.icaoHex) qs.set("icaoHex", params.icaoHex);
  if (params.callsign) qs.set("callsign", params.callsign);
  return getJson<AircraftProfile>(`/aircraft/profile?${qs.toString()}`);
}

export function scan(params: ScanParams) {
  const query = new URLSearchParams({
    airport_icao: params.airport_icao,
    user_lat: String(params.user_lat),
    user_lon: String(params.user_lon),
    ring_nm: String(params.ring_nm),
    pass_radius_nm: String(params.pass_radius_nm),
    pass_ceiling_ft: String(params.pass_ceiling_ft),
    window: params.window
  });
  return getJson<ScanResponse>(`/scan?${query.toString()}`);
}

export function complaintForm(airportIcao: string) {
  return getJson<{ airport: Airport; form: { form_url?: string | null; notes?: string | null } }>(
    `/complaint_form?airport_icao=${airportIcao}`
  );
}

export function aircraftDetail(
  icao24: string,
  params: Pick<ScanParams, "airport_icao" | "user_lat" | "user_lon" | "window">,
  sliders: ToneSliders,
  reportCount = 0,
  systemPrompt?: string | null
) {
  const query = new URLSearchParams({
    airport_icao: params.airport_icao,
    user_lat: String(params.user_lat),
    user_lon: String(params.user_lon),
    window: params.window,
    anger: String(sliders.anger),
    niceness: String(sliders.niceness),
    respect: String(sliders.respect),
    detail: String(sliders.detail),
    local: String(sliders.local),
    previous_report_count: String(reportCount)
  });
  if (systemPrompt) query.set("system_prompt", systemPrompt);
  return getJson<ComplaintResponse>(`/aircraft/${icao24}/detail?${query.toString()}`, COMPLAINT_TIMEOUT_MS);
}

export function complaintSummary(
  icao24s: string[],
  params: Pick<ScanParams, "airport_icao" | "user_lat" | "user_lon" | "window">,
  sliders: ToneSliders,
  reportCounts: Record<string, number>,
  systemPrompt?: string | null
) {
  return postJson<ComplaintResponse & { icao24s: string[] }>(
    "/complaint/summary",
    {
      airport_icao: params.airport_icao,
      user_lat: params.user_lat,
      user_lon: params.user_lon,
      window: params.window,
      icao24s,
      sliders,
      report_counts: reportCounts,
      system_prompt: systemPrompt ?? null
    },
    COMPLAINT_TIMEOUT_MS,
  );
}

export function recordHeartbeat(context: ActivityContext & { path?: string }) {
  return postJson<{ ok: boolean; seen_at: number }>("/activity/heartbeat", {
    visitor_id: context.visitor_id,
    airport_icao: context.airport_icao,
    user_lat: context.user_lat,
    user_lon: context.user_lon,
    path: context.path ?? window.location.pathname
  });
}

export function recordSubmission(
  context: ActivityContext & {
    window?: WindowCode;
    mode?: string;
    text: string;
    targets: ActivityAircraft[];
  }
) {
  return postJson<{ ok: boolean; submission_id: number }>("/activity/submissions", {
    visitor_id: context.visitor_id,
    airport_icao: context.airport_icao,
    user_lat: context.user_lat,
    user_lon: context.user_lon,
    window: context.window,
    mode: context.mode,
    text: context.text,
    targets: context.targets
  });
}

export function adminLogin(username: string, password: string) {
  return adminJson<{ ok: boolean; username: string }>("/admin/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
}

export function adminLogout() {
  return adminJson<{ ok: boolean }>("/admin/logout", { method: "POST" });
}

export function adminSession() {
  return adminJson<{ ok: boolean; username: string }>("/admin/session");
}

export function adminDashboard() {
  return adminJson<AdminDashboardResponse>("/admin/dashboard");
}

// ─── Pattern API types ────────────────────────────────────────────────────────

export interface PatternPoint {
  lat: number;
  lon: number;
}

export interface PatternGeometry {
  points: PatternPoint[];
  closed: boolean;
  spline: string;
}

export interface RunwayPattern {
  id: number;
  icao: string;
  runway_id: string;
  version: number;
  name: string | null;
  locked: boolean;
  geometry: PatternGeometry;
  change_note: string | null;
  created_at: number;
}

export interface PatternResponse {
  pattern: RunwayPattern | null;
}

export interface AirportPatternsResponse {
  airport_icao: string;
  patterns: RunwayPattern[];
}

export interface PatternTemplateResponse {
  geometry: PatternGeometry;
}

export interface PatternVersion {
  id: number;
  version: number;
  name: string | null;
  is_current: boolean;
  locked: boolean;
  editor_visitor_id: string | null;
  change_note: string | null;
  created_at: number;
}

export interface PatternHistoryResponse {
  versions: PatternVersion[];
}

export interface RunwayInfo {
  icao: string;
  runway_id: string;
  lat_threshold: number;
  lon_threshold: number;
  heading_deg: number;
  length_ft: number;
}

export interface AirportRunwaysResponse {
  airport_icao: string;
  runways: RunwayInfo[];
}

export interface PatternSaveBody {
  points: PatternPoint[];
  closed: boolean;
  name?: string | null;
  change_note?: string | null;
  visitor_id?: string | null;
}

// ─── Pattern API functions ────────────────────────────────────────────────────

const enc = encodeURIComponent;

export function getAirportRunways(icao: string) {
  return getJson<AirportRunwaysResponse>(`/airports/${enc(icao)}/runways`);
}

export function getAirportPatterns(icao: string) {
  return getJson<AirportPatternsResponse>(`/airports/${enc(icao)}/patterns`);
}

export function getRunwayPattern(icao: string, runwayId: string) {
  return getJson<PatternResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern`);
}

export function getPatternTemplate(icao: string, runwayId: string, side: "left" | "right" = "left") {
  return getJson<PatternTemplateResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern/template?side=${side}`);
}

export function savePattern(icao: string, runwayId: string, body: PatternSaveBody) {
  return putJson<PatternResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern`, body);
}

export function getPatternHistory(icao: string, runwayId: string) {
  return getJson<PatternHistoryResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern/history`);
}

export function revertPattern(icao: string, runwayId: string, version: number, visitorId: string | null) {
  return postJson<PatternResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern/revert`, {
    version,
    visitor_id: visitorId,
  });
}

export interface RunwayFlow {
  id: number;
  icao: string;
  active_runway_id: string;
  established_at: number;
  ended_at: number | null;
  wind_from_deg: number | null;
  wind_speed_kt: number | null;
  op_count: number;
}

export interface RunwayChange {
  id: number;
  from_runway_id: string | null;
  to_runway_id: string;
  changed_at: number;
  cowboy_icao24: string | null;
  cowboy_callsign: string | null;
  cowboy_registration: string | null;
  wind_from_deg: number | null;
  wind_speed_kt: number | null;
  wind_favored_new: number | null;
  trigger_op_id: string | null;
}

export interface AirportFlowResponse {
  airport_icao: string;
  active: RunwayFlow | null;
  recent_changes: RunwayChange[];
}

export function getAirportFlow(icao: string) {
  return getJson<AirportFlowResponse>(`/airports/${enc(icao)}/flow`);
}

export type StatsWindow = "1d" | "7d" | "30d" | "all";

export interface AirportStatsResponse {
  airport_icao: string;
  window: { code: StatsWindow; start_ts: number; end_ts: number; bucket_seconds: number };
  counters: {
    circles: number; touch_and_gos: number; low_approaches: number;
    landings: number;
    passes: number; unique_aircraft: number; runway_changes: number;
  };
  ops_over_time: { bucket: number; count: number }[];
  stop_classification: {
    all: { total: number; stopped: number; did_not_stop: number; stopped_pct: number | null };
    pattern: { total: number; stopped: number; did_not_stop: number; stopped_pct: number | null };
  };
  stop_over_time: { day: number; total: number; stopped: number; did_not_stop: number; stopped_pct: number | null }[];
  wind: { into_headwind_ops: number; downwind_ops: number; no_wind_data_ops: number };
  deviation: {
    scored_ops: number; avg_mean_nm: number | null; max_nm: number | null;
    total_time_off_s: number; worst: { icao24: string; callsign: string | null; deviation_mean_nm: number; circles: number }[];
  };
  cowboys: { icao24: string; callsign: string | null; changes: number }[];
  recent_changes: RunwayChange[];
  repeat_offenders: { icao24: string; callsign: string | null; registration: string | null; report_count: number }[];
  flight_schools: { label: string; count: number }[];
  runway_usage: { runway_id: string; total: number; upwind: number; crosswind: number; downwind: number; no_wind_data: number }[];
}

export function getAirportStats(icao: string, window: StatsWindow = "7d") {
  return getJson<AirportStatsResponse>(`/airports/${encodeURIComponent(icao)}/stats?window=${window}`);
}

export interface OperationsTrendsResponse {
  airport_icao: string;
  timezone: string | null;
  data_since: number | null;
  recent_days: { date: string; operations: number; pct_tg: number; pct_light: number }[];
  monthly: {
    month: string; landings: number; takeoffs: number; tg: number; total: number;
    pct_tg: number; by_type: Record<string, number>; by_emitter: Record<string, number>;
  }[];
  time_of_day: { hour: number; operations: number }[];
}

export function getOperationsTrends(icao: string) {
  return getJson<OperationsTrendsResponse>(`/airports/${encodeURIComponent(icao)}/operations-trends`);
}

export interface VnapAircraft {
  icao24: string; callsign: string; registration: string | null; tail: string;
  aircraft_type: string | null; owner_class: string; owner_source: string;
  vnap_score: number | null; reports: number; operations: number;
  touch_and_gos: number; cowboy_count: number; deviation_mean_nm: number | null;
  circles: number; scores: Record<string, number | null>;
  metrics?: Record<string, number | null> | null;
}

export interface VnapComplianceResponse {
  airport_icao: string;
  window: { code: StatsWindow; start_ts: number; end_ts: number };
  axes: string[];
  averages: Record<string, number | null>;
  aircraft: VnapAircraft[];
}

export function getVnapCompliance(icao: string, window: StatsWindow = "7d") {
  return getJson<VnapComplianceResponse>(
    `/airports/${encodeURIComponent(icao)}/vnap-compliance?window=${window}`,
  );
}

export interface HistoricalTrackSample {
  lat: number;
  lon: number;
  altitude_ft: number | null;
  timestamp: number;
}

export interface HistoricalTrack {
  icao24: string;
  callsign: string | null;
  samples: HistoricalTrackSample[];
}

export interface TrackHistoryResponse {
  airport: { icao: string; lat: number; lon: number; elevation_ft: number };
  days: number;
  ceiling_ft: number;
  window: { start_ts: number; end_ts: number };
  tracks: HistoricalTrack[];
  total_tracks: number;
  truncated: boolean;
}

export function getTrackHistory(icao: string, days: number, ceilingFt = 5000) {
  return getJson<TrackHistoryResponse>(
    `/airports/${encodeURIComponent(icao)}/track-history?days=${days}&ceiling_ft=${ceilingFt}`
  );
}

export interface PatternCircuitSample {
  lat: number;
  lon: number;
  timestamp: number;
}

export interface PatternCircuit {
  class: string;
  runway_id: string | null;
  icao24: string;
  is_loop: boolean;
  samples: PatternCircuitSample[];
}

export interface PatternCircuitsResponse {
  airport: { icao: string; lat: number; lon: number; elevation_ft: number };
  days: number;
  window: { start_ts: number; end_ts: number };
  circuits: PatternCircuit[];
  counts_by_class: Record<string, number>;
  context_count: number;
  total_circuits: number;
  truncated: boolean;
}

export function getPatternCircuits(icao: string, days: number) {
  return getJson<PatternCircuitsResponse>(
    `/airports/${encodeURIComponent(icao)}/pattern-circuits?days=${days}`
  );
}

// ─── Owner-class + community-notes API functions ──────────────────────────────

export interface ResolvedOwner { owner_class: string; owner_source: string; }
export interface AircraftNote { id: number; note: string; is_flight_school: boolean; created_at: number; }

export function setOwnerClass(icao24: string, owner_type: string, visitor_id: string, change_note?: string) {
  return putJson<ResolvedOwner>(`/aircraft/${encodeURIComponent(icao24)}/owner-class`,
    { owner_type, visitor_id, change_note });
}

export function addAircraftNote(icao24: string, note: string, is_flight_school: boolean, visitor_id: string) {
  return postJson<AircraftNote>(`/aircraft/${encodeURIComponent(icao24)}/notes`,
    { note, is_flight_school, visitor_id });
}

export function getAircraftNotes(icao24: string) {
  return getJson<{ owner: ResolvedOwner; notes: AircraftNote[] }>(
    `/aircraft/${encodeURIComponent(icao24)}/notes`);
}
