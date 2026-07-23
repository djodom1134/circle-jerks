import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrendPanel } from "./TrendPanel";
import { DAILY_FIXTURE } from "../fixtures/klmo";

describe("TrendPanel", () => {
  it("renders a 12-month out-of-town-share heading", () => {
    render(<TrendPanel data={DAILY_FIXTURE} activeMonth="2026-07" />);
    expect(screen.getByText(/out.of.town/i)).toBeTruthy();
  });
});
