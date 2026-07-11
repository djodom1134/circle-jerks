// N-numbers: leading "N" + 1 to 5 alphanumeric characters, mirroring the
// backend grammar in backend/app/registry/normalize.py (extract_n_number_from_callsign).
const N_NUMBER_BODY = /^([1-9][0-9]{0,3}[A-HJ-NP-Z]{0,2}|[1-9][0-9]{1,4})$/;

/**
 * The identifier to show in a complaint: an N-number when the callsign is
 * plausibly one, else the uppercase ICAO hex. Never a raw airline/flight-ID
 * callsign, and never a "(hex)" suffix alongside it.
 */
export function displayTail(callsign: string | null | undefined, icao24: string): string {
  const candidate = (callsign ?? "").trim().toUpperCase();
  const body = candidate.startsWith("N") ? candidate.slice(1) : candidate;
  if (N_NUMBER_BODY.test(body)) {
    return "N" + body;
  }
  return icao24.toUpperCase();
}

export function worstOffenderTargets<T extends { vnap_score?: number | null; circles: number }>(
  offenders: T[],
  limit = 10,
): T[] {
  return [...offenders]
    .sort((a, b) => (b.vnap_score ?? 0) * b.circles - (a.vnap_score ?? 0) * a.circles)
    .slice(0, limit);
}

export const HABIT_LABELS: Record<string, string> = {
  tightness: "Sloppy, wide patterns",
  altitude: "Flies too low over homes",
  timeofday: "Flies during quiet hours",
  tg_volume: "Relentless touch-and-goes",
  circle_restraint: "Endless pattern loops",
  left_traffic: "Wrong-way traffic",
  runway29: "Ignores the noise-preferred runway",
};

export function habitLabel(axis: string | null): string {
  if (!axis) return "Repeat pattern flyer";
  return HABIT_LABELS[axis] ?? "Repeat pattern flyer";
}
