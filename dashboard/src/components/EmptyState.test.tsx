import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("names the month with no data", () => {
    render(<EmptyState month="2020-01" />);
    expect(screen.getByText(/no data yet/i)).toBeTruthy();
    expect(screen.getByText(/January 2020/)).toBeTruthy();
  });
});
