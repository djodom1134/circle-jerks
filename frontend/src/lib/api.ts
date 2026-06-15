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
  /** Breakdown of T&Gs / low approaches by runway_id, e.g. {"11": 3, "29": 7}. */
  runway_breakdown?: Record<string, number>;
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

export interface MessagePreferences {
  include_all_detail: boolean;
  include_elevation: boolean;
  include_circles: boolean;
  include_altitude_over_house: boolean;
  include_db_at_home: boolean;
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
  message: MessagePreferences,
  reportCount = 0
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
    include_all_detail: String(message.include_all_detail),
    include_elevation: String(message.include_elevation),
    include_circles: String(message.include_circles),
    include_altitude_over_house: String(message.include_altitude_over_house),
    include_db_at_home: String(message.include_db_at_home),
    previous_report_count: String(reportCount)
  });
  return getJson<ComplaintResponse>(`/aircraft/${icao24}/detail?${query.toString()}`, COMPLAINT_TIMEOUT_MS);
}

export function complaintSummary(
  icao24s: string[],
  params: Pick<ScanParams, "airport_icao" | "user_lat" | "user_lon" | "window">,
  sliders: ToneSliders,
  message: MessagePreferences,
  reportCounts: Record<string, number>
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
      message_preferences: message,
      report_counts: reportCounts
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
