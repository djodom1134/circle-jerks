import type { ToneSliders, WindowCode } from "./api";

export type ComplaintMode = "one" | "all";

export interface StoredPreferences {
  airport_icao?: string;
  airport_query?: string;
  user_lat?: number;
  user_lon?: number;
  user_address?: string;
  window?: WindowCode;
  complaint_mode: ComplaintMode;
  sliders: ToneSliders;
  report_counts: Record<string, number>;
  /** Optional per-user override for the AI complaint system prompt (stored client-side only). */
  system_prompt?: string;
}

const COOKIE_NAME = "circlejerks_preferences";
const YEAR_SECONDS = 60 * 60 * 24 * 365;

export const DEFAULT_STORED_PREFS: StoredPreferences = {
  complaint_mode: "one",
  sliders: { anger: 3, niceness: 6, respect: 7, detail: 6, local: 3 },
  report_counts: {}
};

function cookieValue(name: string) {
  return document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.split("=")
    .slice(1)
    .join("=");
}

function normalizeReportCounts(raw: unknown): Record<string, number> {
  if (!raw || typeof raw !== "object") return {};
  return Object.fromEntries(
    Object.entries(raw as Record<string, unknown>)
      .map(([icao24, count]) => [icao24.toLowerCase(), Number(count)] as [string, number])
      .filter(([, count]) => Number.isFinite(count) && count > 0)
      .sort((a, b) => Number(b[1]) - Number(a[1]))
      .slice(0, 80)
  ) as Record<string, number>;
}

export function readPreferences(): StoredPreferences {
  const raw = cookieValue(COOKIE_NAME);
  if (!raw) return DEFAULT_STORED_PREFS;
  try {
    const parsed = JSON.parse(decodeURIComponent(raw)) as Partial<StoredPreferences>;
    return {
      ...DEFAULT_STORED_PREFS,
      ...parsed,
      sliders: { ...DEFAULT_STORED_PREFS.sliders, ...(parsed.sliders ?? {}) },
      report_counts: normalizeReportCounts(parsed.report_counts),
      complaint_mode: parsed.complaint_mode === "all" ? "all" : "one"
    };
  } catch {
    return DEFAULT_STORED_PREFS;
  }
}

export function writePreferences(preferences: StoredPreferences) {
  const payload: StoredPreferences = {
    ...preferences,
    airport_icao: preferences.airport_icao?.toUpperCase(),
    user_lat: preferences.user_lat === undefined ? undefined : Number(preferences.user_lat.toFixed(5)),
    user_lon: preferences.user_lon === undefined ? undefined : Number(preferences.user_lon.toFixed(5)),
    report_counts: normalizeReportCounts(preferences.report_counts)
  };
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${COOKIE_NAME}=${encodeURIComponent(JSON.stringify(payload))}; Max-Age=${YEAR_SECONDS}; Path=/; SameSite=Lax${secure}`;
}
