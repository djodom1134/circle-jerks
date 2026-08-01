import type { ComponentProps } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Toolbar } from "./Toolbar";
import { allTypes, allLocalities } from "../lib/facets";

function setup(overrides: Partial<ComponentProps<typeof Toolbar>> = {}) {
  const props = {
    airportLabel: "KLMO · Longmont",
    month: "2026-07",
    months: ["2026-06", "2026-07", "2026-08"],
    onMonthChange: vi.fn(),
    filter: { types: allTypes(), localities: allLocalities() },
    onToggleType: vi.fn(),
    onToggleLocality: vi.fn(),
    colorBy: "locality" as const,
    onColorByChange: vi.fn(),
    ...overrides,
  };
  render(<Toolbar {...props} />);
  return props;
}

describe("Toolbar", () => {
  it("shows the current month label and airport", () => {
    setup();
    expect(screen.getByText(/July 2026/)).toBeTruthy();
    expect(screen.getByText(/KLMO · Longmont/)).toBeTruthy();
  });

  it("advances the month when the next button is clicked", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /next month/i }));
    expect(props.onMonthChange).toHaveBeenCalledWith("2026-08");
  });

  it("disables next at the last available month", () => {
    setup({ month: "2026-08" });
    expect(screen.getByRole("button", { name: /next month/i })).toHaveProperty("disabled", true);
  });

  it("toggles a type when its chip is clicked", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /touch & go/i }));
    expect(props.onToggleType).toHaveBeenCalledWith("touch_and_go");
  });

  it("switches colour-by when the type option is chosen", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /colour by type/i }));
    expect(props.onColorByChange).toHaveBeenCalledWith("type");
  });
});
