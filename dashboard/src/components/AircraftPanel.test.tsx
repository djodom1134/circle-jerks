import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AircraftPanel } from "./AircraftPanel";
import { WORST_OFFENDERS_FIXTURE } from "../fixtures/klmo";

describe("AircraftPanel", () => {
  it("lists tail numbers when data is present", () => {
    render(<AircraftPanel offenders={WORST_OFFENDERS_FIXTURE} />);
    expect(screen.getByText("N829SC")).toBeTruthy();
  });
  it("shows an unavailable state when null", () => {
    render(<AircraftPanel offenders={null} />);
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
  });
  it("distinguishes a real zero-result response from unavailable", () => {
    render(<AircraftPanel offenders={{ airport_icao: "KLMO", offenders: [] }} />);
    expect(screen.getByText(/no repeat aircraft/i)).toBeTruthy();
    expect(screen.queryByText(/unavailable/i)).toBeNull();
  });
});
