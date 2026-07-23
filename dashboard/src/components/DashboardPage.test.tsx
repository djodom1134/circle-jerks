import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardPage } from "./DashboardPage";
import { DAILY_FIXTURE, ORIGINS_FIXTURE, WORST_OFFENDERS_FIXTURE, HOURLY_FIXTURE } from "../fixtures/klmo";
import { allTypes, allLocalities } from "../lib/facets";

const base = {
  data: DAILY_FIXTURE, origins: ORIGINS_FIXTURE, offenders: WORST_OFFENDERS_FIXTURE, hourly: HOURLY_FIXTURE,
  airportLabel: "KLMO · Longmont", filter: { types: allTypes(), localities: allLocalities() },
  colorBy: "locality" as const, dark: false, originCount: 312,
  selectedOrigin: null, onSelectOrigin: () => {},
};

describe("DashboardPage", () => {
  it("renders the hero chart and the panels for a month in coverage", () => {
    render(<DashboardPage {...base} month="2026-07" />);
    expect(screen.getByRole("img")).toBeTruthy();       // hero DayBarChart
    expect(screen.getByText("N829SC")).toBeTruthy();     // AircraftPanel → panels render
  });

  it("renders the empty state for a month outside coverage", () => {
    render(<DashboardPage {...base} month="2020-01" />);
    expect(screen.getByText(/no data yet/i)).toBeTruthy();
  });
});
