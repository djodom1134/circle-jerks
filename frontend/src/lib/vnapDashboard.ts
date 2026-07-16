import type { VnapAircraft } from "./api";

export const AXIS_LABELS: Record<string, string> = {
  tightness: "Time off pattern",
  altitude: "Altitude",
  timeofday: "Time of day",
  tg_volume: "T&G volume",
  circle_restraint: "Circle restraint",
  left_traffic: "Left traffic",
  runway29: "Runway 29 pref",
};

export const OWNER_LABELS: Record<string, string> = {
  individual: "Individual", llc: "LLC", corporation: "Corporation",
  government: "Government", flight_school: "Flight school", university: "University",
  club: "Club", trust: "Trust", skydiving: "Skydiving",
  commercial_airline: "Commercial airline", unknown: "Unknown",
};

export const OWNER_OPTIONS = [
  "individual", "llc", "corporation", "government",
  "flight_school", "university", "club", "trust",
  "skydiving", "commercial_airline", "unknown",
];

type Sortable = number | string | null;

function cellValue(row: VnapAircraft, key: string): Sortable {
  if (key === "owner_class") return OWNER_LABELS[row.owner_class] ?? row.owner_class;
  if (key in row) return (row as unknown as Record<string, Sortable>)[key];
  return row.scores[key] ?? null; // axis columns
}

export function sortAircraft(rows: VnapAircraft[], key: string, dir: "asc" | "desc"): VnapAircraft[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = cellValue(a, key);
    const vb = cellValue(b, key);
    // nulls always last, regardless of direction
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    if (typeof va === "string" || typeof vb === "string") {
      return sign * String(va).localeCompare(String(vb));
    }
    return sign * (va - vb);
  });
}

const CSV_COLUMNS = [
  "tail", "aircraft_type", "owner_class", "owner_source", "vnap_score", "reports",
  "operations", "touch_and_gos", "cowboy_count", "deviation_mean_nm", "circles",
];

function csvEscape(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

const METRIC_COLUMNS = [
  "altitude", "timeofday", "tg_volume", "circle_restraint",
  "left_traffic", "preferred_runway", "rwy_against",
];

export function toCsv(rows: VnapAircraft[]): string {
  const cols = [...CSV_COLUMNS, ...METRIC_COLUMNS];
  const header = cols.join(",");
  const lines = rows.map((r) =>
    cols
      .map((c) => (c in r
        ? csvEscape((r as unknown as Record<string, unknown>)[c])
        : csvEscape(r.metrics?.[c] ?? null)))
      .join(","),
  );
  return [header, ...lines].join("\n");
}

export function radarData(
  axes: string[],
  selected: VnapAircraft | null,
  averages: Record<string, number | null>,
): { axis: string; label: string; selected: number; average: number; selectedNull: boolean; averageNull: boolean }[] {
  return axes.map((axis) => ({
    axis,
    label: AXIS_LABELS[axis] ?? axis,
    selected: selected?.scores[axis] ?? 0,
    average: averages[axis] ?? 0,
    selectedNull: selected?.scores[axis] == null,
    averageNull: averages[axis] == null,
  }));
}

export function resolveHighlight(
  rows: VnapAircraft[],
  param: string | null,
): string | null {
  if (!param) return null;
  const target = param.toLowerCase();
  const match = rows.find((a) => a.icao24.toLowerCase() === target);
  return match ? match.icao24 : null;
}
