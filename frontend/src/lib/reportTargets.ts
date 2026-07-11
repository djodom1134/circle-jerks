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
