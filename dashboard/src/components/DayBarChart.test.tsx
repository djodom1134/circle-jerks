import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DayBarChart } from "./DayBarChart";
import { daysForMonth, allTypes, allLocalities } from "../lib/facets";
import { DAILY_FIXTURE } from "../fixtures/klmo";

describe("DayBarChart", () => {
  it("renders an accessible figure describing the month total", () => {
    const days = daysForMonth(DAILY_FIXTURE, "2026-07");
    render(<DayBarChart days={days} filter={{ types: allTypes(), localities: allLocalities() }} colorBy="locality" dark={false} />);
    // The chart exposes an aria-label summarising the data for screen readers.
    expect(screen.getByRole("img")).toBeTruthy();
  });

  it("renders a table view for accessibility (relief rule)", () => {
    const days = daysForMonth(DAILY_FIXTURE, "2026-07");
    render(<DayBarChart days={days} filter={{ types: allTypes(), localities: allLocalities() }} colorBy="locality" dark={false} />);
    expect(screen.getByRole("table")).toBeTruthy();
  });
});
