/**
 * Resolve which airport this dashboard is showing. In production the airport
 * is the hostname's leading label (klmo.airfieldeconomics.org -> KLMO); a
 * ?airport= query param overrides it for local dev and cross-airport preview.
 * Adding an airport is one entry in HOST_MAP plus a DNS record — no other code
 * changes.
 */
const HOST_MAP: Record<string, string> = {
  klmo: "KLMO",
};

const DEFAULT_ICAO = "KLMO";

export function resolveAirport(input?: { hostname?: string; search?: string }): string {
  const hostname = input?.hostname ?? (typeof window !== "undefined" ? window.location.hostname : "");
  const search = input?.search ?? (typeof window !== "undefined" ? window.location.search : "");

  const override = new URLSearchParams(search).get("airport");
  if (override && override.trim()) return override.trim().toUpperCase();

  const label = hostname.split(".")[0]?.toLowerCase() ?? "";
  return HOST_MAP[label] ?? DEFAULT_ICAO;
}
