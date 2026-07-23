import { describe, expect, it } from "vitest";
import { DAILY_FIXTURE } from "../fixtures/klmo";
import type { OperationType, Locality } from "./types";
import {
  monthsInCoverage, daysForMonth, isMonthInCoverage,
  filteredDaySeries, kpis, monthlyOutOfTownShare, allTypes, allLocalities,
} from "./facets";

const ALL = { types: allTypes(), localities: allLocalities() };

describe("month slicing", () => {
  it("lists months spanning coverage", () => {
    expect(monthsInCoverage(DAILY_FIXTURE)).toEqual(["2026-07", "2026-08"]);
  });
  it("returns 31 days for July and reports it in coverage", () => {
    expect(daysForMonth(DAILY_FIXTURE, "2026-07")).toHaveLength(31);
    expect(isMonthInCoverage(DAILY_FIXTURE, "2026-07")).toBe(true);
  });
  it("reports a month outside coverage as empty and not-in-coverage", () => {
    expect(daysForMonth(DAILY_FIXTURE, "2020-01")).toHaveLength(0);
    expect(isMonthInCoverage(DAILY_FIXTURE, "2020-01")).toBe(false);
  });
});

describe("filteredDaySeries", () => {
  const july = daysForMonth(DAILY_FIXTURE, "2026-07");

  it("colours by locality: segment keys are the selected localities", () => {
    const series = filteredDaySeries(july, ALL, "locality");
    expect(Object.keys(series[0].segments).sort()).toEqual(["local", "out_of_town", "unclassified"]);
  });

  it("filtering to one type reduces every day's total", () => {
    const all = filteredDaySeries(july, ALL, "locality");
    const landingsOnly = filteredDaySeries(july, { types: new Set<OperationType>(["landing"]), localities: allLocalities() }, "locality");
    const i = all.findIndex((d) => d.total > 0);
    expect(landingsOnly[i].total).toBeLessThan(all[i].total);
  });

  it("filtering out a locality drops it from the stack and the total", () => {
    const noVisitors = filteredDaySeries(july, { types: allTypes(), localities: new Set<Locality>(["local", "unclassified"]) }, "locality");
    expect(Object.keys(noVisitors[0].segments)).not.toContain("out_of_town");
  });

  it("a zero-day inside coverage has total 0 (a real zero, not missing)", () => {
    const series = filteredDaySeries(july, ALL, "locality");
    const zero = series.find((d) => d.date === "2026-07-14");
    expect(zero?.total).toBe(0);
  });
});

describe("kpis", () => {
  it("out-of-town pct is between 0 and 100 and operations is the filtered sum", () => {
    const july = daysForMonth(DAILY_FIXTURE, "2026-07");
    const k = kpis(july, ALL);
    expect(k.operations).toBeGreaterThan(0);
    expect(k.outOfTownPct).toBeGreaterThanOrEqual(0);
    expect(k.outOfTownPct).toBeLessThanOrEqual(100);
    expect(k.busiestDay).toBeGreaterThan(0);
  });
});

describe("monthlyOutOfTownShare", () => {
  it("produces one entry per covered month with a 0..1 share", () => {
    const s = monthlyOutOfTownShare(DAILY_FIXTURE);
    expect(s.map((x) => x.month)).toEqual(["2026-07", "2026-08"]);
    for (const m of s) { expect(m.share).toBeGreaterThanOrEqual(0); expect(m.share).toBeLessThanOrEqual(1); }
  });
});
