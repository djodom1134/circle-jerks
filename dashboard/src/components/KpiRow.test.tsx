import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KpiRow } from "./KpiRow";
import { daysForMonth, allTypes, allLocalities } from "../lib/facets";
import { DAILY_FIXTURE } from "../fixtures/klmo";

describe("KpiRow", () => {
  it("renders the four KPIs including a non-zero operations count", () => {
    const days = daysForMonth(DAILY_FIXTURE, "2026-07");
    render(<KpiRow days={days} filter={{ types: allTypes(), localities: allLocalities() }} aircraftCount={312} />);
    expect(screen.getByText(/Operations/i)).toBeTruthy();
    expect(screen.getByText(/Out of town/i)).toBeTruthy();
    expect(screen.getByText("312")).toBeTruthy();
  });
});
