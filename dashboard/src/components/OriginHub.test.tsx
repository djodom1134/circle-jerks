import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OriginHub } from "./OriginHub";
import { ORIGINS_FIXTURE } from "../fixtures/klmo";

describe("OriginHub", () => {
  it("renders a node per origin plus the centre airport", () => {
    render(<OriginHub origins={ORIGINS_FIXTURE} selectedOrigin={null} onSelectOrigin={() => {}} airportIcao="KLMO" />);
    expect(screen.getByText("KBDU")).toBeTruthy();
    expect(screen.getByText("KLMO")).toBeTruthy();
  });

  it("calls onSelectOrigin with the ICAO when a node is clicked", () => {
    const onSelect = vi.fn();
    render(<OriginHub origins={ORIGINS_FIXTURE} selectedOrigin={null} onSelectOrigin={onSelect} airportIcao="KLMO" />);
    fireEvent.click(screen.getByRole("button", { name: /KBDU/ }));
    expect(onSelect).toHaveBeenCalledWith("KBDU");
  });

  it("deselects when the already-selected node is clicked again", () => {
    const onSelect = vi.fn();
    render(<OriginHub origins={ORIGINS_FIXTURE} selectedOrigin="KBDU" onSelectOrigin={onSelect} airportIcao="KLMO" />);
    fireEvent.click(screen.getByRole("button", { name: /KBDU/ }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("shows an unavailable state when origins is null", () => {
    render(<OriginHub origins={null} selectedOrigin={null} onSelectOrigin={() => {}} airportIcao="KLMO" />);
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
  });

  it("shows a zero-result state (not 'unavailable') when origins is present but empty", () => {
    render(<OriginHub
      origins={{
        airport_icao: "KLMO", window: { from: "2026-07-01", to: "2026-07-31" },
        total_out_of_town: 0, origins: [], other: { count: 0, arrivals: 0 },
      }}
      selectedOrigin={null} onSelectOrigin={() => {}} airportIcao="KLMO"
    />);
    expect(screen.getByText(/no out-of-town origins/i)).toBeTruthy();
    expect(screen.queryByText(/unavailable/i)).toBeNull();
  });
});
