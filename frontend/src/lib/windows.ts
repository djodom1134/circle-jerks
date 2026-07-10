import type { WindowCode } from "./api";

// "today" is a rolling 24h, not the calendar day — the wire code is kept for
// saved-preference and ?window= back-compat.
export const WINDOW_SECONDS: Record<WindowCode, number> = {
  "5m": 300,
  "30m": 1800,
  "1h": 3600,
  "6h": 21600,
  today: 86400,
};

/** Windows longer than an hour replay a day of archived tracks through the
 *  detectors on a cache miss — seconds, not milliseconds. */
export function isWideWindow(code: WindowCode): boolean {
  return WINDOW_SECONDS[code] > WINDOW_SECONDS["1h"];
}

/** True while a wide window is being built: either nothing is loaded yet, or
 *  what is loaded still belongs to the window the user just switched away from.
 *  Narrow windows resolve fast enough that an indicator would only flicker. */
export function isWindowLoading(requested: WindowCode, loaded: string | null | undefined): boolean {
  return isWideWindow(requested) && loaded !== requested;
}
