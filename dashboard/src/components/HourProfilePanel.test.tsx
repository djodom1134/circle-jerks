import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HourProfilePanel } from "./HourProfilePanel";
import { HOURLY_FIXTURE } from "../fixtures/klmo";

describe("HourProfilePanel", () => {
  it("renders when hourly data is present", () => {
    render(<HourProfilePanel hourly={HOURLY_FIXTURE} />);
    expect(screen.getByText(/by hour/i)).toBeTruthy();
  });
  it("degrades to unavailable when null", () => {
    render(<HourProfilePanel hourly={null} />);
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
  });
  it("distinguishes a real zero-result response from unavailable", () => {
    render(<HourProfilePanel hourly={{ airport_icao: "KLMO", window: { from: "2026-07-01", to: "2026-07-31" }, hours: [] }} />);
    expect(screen.getByText(/no activity/i)).toBeTruthy();
    expect(screen.queryByText(/unavailable/i)).toBeNull();
  });
});
