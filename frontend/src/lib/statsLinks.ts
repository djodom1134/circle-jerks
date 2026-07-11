import type { StatsWindow } from "./api";

const STATS_WINDOWS: readonly StatsWindow[] = ["1d", "7d", "30d", "all"];
const DEFAULT_STATS_WINDOW: StatsWindow = "7d";
const DEFAULT_AIRPORT = "KBJC";

export function statsHighlightHref(
  airportIcao: string | undefined | null,
  icao24: string,
): string {
  const params = new URLSearchParams({
    airport: (airportIcao || DEFAULT_AIRPORT).toUpperCase(),
    aircraft: icao24.toLowerCase(),
    win: "all",
  });
  return `/stats?${params.toString()}`;
}

export function windowFromUrl(search: string): StatsWindow {
  const win = new URLSearchParams(search).get("win");
  return STATS_WINDOWS.includes(win as StatsWindow)
    ? (win as StatsWindow)
    : DEFAULT_STATS_WINDOW;
}
