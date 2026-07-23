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
});
