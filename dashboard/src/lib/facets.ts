import {
  OPERATION_TYPES, LOCALITIES,
  type DailyOperations, type DailyOperationsDay, type OperationType, type Locality,
} from "./types";

export type FilterState = { types: Set<OperationType>; localities: Set<Locality> };
export type ColorBy = "locality" | "type";

export const allTypes = (): Set<OperationType> => new Set(OPERATION_TYPES);
export const allLocalities = (): Set<Locality> => new Set(LOCALITIES);

const monthKey = (date: string): string => date.slice(0, 7);

export function monthsInCoverage(data: DailyOperations): string[] {
  const seen = new Set<string>();
  for (const d of data.days) seen.add(monthKey(d.date));
  return [...seen].sort();
}

export function daysForMonth(data: DailyOperations, month: string): DailyOperationsDay[] {
  return data.days.filter((d) => monthKey(d.date) === month);
}

export function isMonthInCoverage(data: DailyOperations, month: string): boolean {
  return month >= monthKey(data.coverage.min_day) && month <= monthKey(data.coverage.max_day)
    && daysForMonth(data, month).length > 0;
}

/**
 * Collapse each day's type×locality grid to stacked segments keyed by the
 * active colour dimension, counting only cells whose type AND locality are
 * both selected. A segment key that ends up with zero across the whole month
 * is still emitted only if its dimension member is selected — so the stack
 * order is stable as values change.
 */
export function filteredDaySeries(
  days: DailyOperationsDay[], filter: FilterState, colorBy: ColorBy,
): { date: string; segments: Record<string, number>; total: number }[] {
  const typeList = OPERATION_TYPES.filter((t) => filter.types.has(t));
  const locList = LOCALITIES.filter((l) => filter.localities.has(l));
  const keys = colorBy === "locality" ? locList : typeList;

  return days.map((day) => {
    const segments: Record<string, number> = {};
    for (const k of keys) segments[k] = 0;
    let total = 0;
    for (const t of typeList) {
      for (const l of locList) {
        const v = day.counts[t][l];
        total += v;
        segments[colorBy === "locality" ? l : t] += v;
      }
    }
    return { date: day.date, segments, total };
  });
}

export function kpis(days: DailyOperationsDay[], filter: FilterState): {
  operations: number; outOfTownPct: number; busiestDay: number;
} {
  const typeList = OPERATION_TYPES.filter((t) => filter.types.has(t));
  const locList = LOCALITIES.filter((l) => filter.localities.has(l));
  let operations = 0, outOfTown = 0, busiestDay = 0;
  for (const day of days) {
    let dayTotal = 0;
    for (const t of typeList) for (const l of locList) {
      const v = day.counts[t][l];
      operations += v; dayTotal += v;
      if (l === "out_of_town") outOfTown += v;
    }
    if (dayTotal > busiestDay) busiestDay = dayTotal;
  }
  const outOfTownPct = operations === 0 ? 0 : Math.round((outOfTown / operations) * 100);
  return { operations, outOfTownPct, busiestDay };
}

export function monthlyOutOfTownShare(data: DailyOperations): { month: string; share: number }[] {
  return monthsInCoverage(data).map((month) => {
    let total = 0, out = 0;
    for (const day of daysForMonth(data, month)) {
      for (const t of OPERATION_TYPES) for (const l of LOCALITIES) {
        total += day.counts[t][l];
        if (l === "out_of_town") out += day.counts[t][l];
      }
    }
    return { month, share: total === 0 ? 0 : out / total };
  });
}
