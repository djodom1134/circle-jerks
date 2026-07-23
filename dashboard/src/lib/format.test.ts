import { describe, expect, it } from "vitest";
import { formatInteger, formatPercent, formatShortDate, formatMonthLabel } from "./format";

describe("format", () => {
  it("formats integers with grouping", () => { expect(formatInteger(4656)).toBe("4,656"); });
  it("formats a fraction as a whole percent", () => { expect(formatPercent(0.2)).toBe("20%"); });
  it("formats a date without timezone drift", () => { expect(formatShortDate("2026-07-01")).toBe("Jul 1"); });
  it("formats a month key as a long label", () => { expect(formatMonthLabel("2026-07")).toBe("July 2026"); });
});
