import type { DailyOperations, DailyOperationsDay, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse } from "../lib/types";

const WEEKDAY_RHYTHM = [0.72, 0.81, 0.94, 1.0, 0.88, 0.62, 0.55];

function day(dateStr: string, index: number): DailyOperationsDay {
  // A deterministic weekday rhythm; index 13 (Jul 14) is a forced zero-day.
  const isZero = index === 13;
  const base = isZero ? 0 : Math.round(60 * WEEKDAY_RHYTHM[index % 7]);
  const mk = (scale: number) => {
    const total = Math.round(base * scale);
    const out = Math.round(total * 0.2);
    const un = Math.round(total * 0.22);
    return { local: Math.max(total - out - un, 0), out_of_town: out, unclassified: un };
  };
  return {
    date: dateStr,
    counts: {
      landing: mk(0.4),
      touch_and_go: mk(0.8),
      low_approach: mk(1.9),
      takeoff: mk(0.42),
    },
  };
}

function buildDays(): DailyOperationsDay[] {
  const days: DailyOperationsDay[] = [];
  for (let d = 1; d <= 31; d++) days.push(day(`2026-07-${String(d).padStart(2, "0")}`, d - 1));
  for (let d = 1; d <= 3; d++) days.push(day(`2026-08-${String(d).padStart(2, "0")}`, 30 + d));
  return days;
}

export const DAILY_FIXTURE: DailyOperations = {
  airport_icao: "KLMO",
  timezone: "America/Denver",
  coverage: { min_day: "2026-07-01", max_day: "2026-08-03" },
  types: ["landing", "touch_and_go", "low_approach", "takeoff"],
  localities: ["local", "out_of_town", "unclassified"],
  days: buildDays(),
};

export const ORIGINS_FIXTURE: OriginsResponse = {
  airport_icao: "KLMO",
  window: { from: "2026-07-01", to: "2026-07-31" },
  total_out_of_town: 931,
  origins: [
    { icao: "KBDU", label: "Boulder", arrivals: 341 },
    { icao: "KBJC", label: "Rocky Mountain Metro", arrivals: 212 },
    { icao: "KFNL", label: "Fort Collins–Loveland", arrivals: 118 },
    { icao: "KGXY", label: "Greeley–Weld", arrivals: 96 },
    { icao: "KEIK", label: "Erie Municipal", arrivals: 73 },
    { icao: "KDEN", label: "Denver International", arrivals: 51 },
    { icao: "KAPA", label: "Centennial", arrivals: 40 },
  ],
  other: { count: 12, arrivals: 58 },
};

export const WORST_OFFENDERS_FIXTURE: WorstOffendersResponse = {
  airport_icao: "KLMO",
  offenders: [
    { icao24: "a1b2c3", registration: "N829SC", report_count: 214 },
    { icao24: "a1b2c4", registration: "N172RG", report_count: 163 },
    { icao24: "a1b2c5", registration: "N6284L", report_count: 131 },
    { icao24: "a1b2c6", registration: "N5589E", report_count: 94 },
    { icao24: "a1b2c7", registration: null, report_count: 71 },
  ],
};

export const HOURLY_FIXTURE: HourlyProfileResponse = {
  airport_icao: "KLMO",
  window: { from: "2026-07-01", to: "2026-07-31" },
  hours: Array.from({ length: 24 }, (_, h) => ({
    hour: h,
    operations: Math.round(100 * Math.max(0, Math.sin(((h - 5) / 14) * Math.PI))),
  })),
};
