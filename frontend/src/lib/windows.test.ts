import { describe, it, expect } from "vitest";
import { isWideWindow, isWindowLoading } from "./windows";

describe("isWideWindow", () => {
  it("treats only windows longer than an hour as wide", () => {
    expect(isWideWindow("5m")).toBe(false);
    expect(isWideWindow("30m")).toBe(false);
    // "longer than 1 hour" is strict: 1h itself resolves fast enough.
    expect(isWideWindow("1h")).toBe(false);
    expect(isWideWindow("6h")).toBe(true);
    expect(isWideWindow("today")).toBe(true);
  });
});

describe("isWindowLoading", () => {
  it("shows while a wide window has no data yet", () => {
    expect(isWindowLoading("today", null)).toBe(true);
    expect(isWindowLoading("6h", null)).toBe(true);
  });

  it("shows while the loaded data is still from the previous window", () => {
    // User just clicked "24 hours"; the map still holds the 1h response.
    expect(isWindowLoading("today", "1h")).toBe(true);
    expect(isWindowLoading("6h", "today")).toBe(true);
  });

  it("hides once the loaded data matches the requested window", () => {
    expect(isWindowLoading("today", "today")).toBe(false);
    expect(isWindowLoading("6h", "6h")).toBe(false);
  });

  it("never shows for narrow windows — they resolve too fast to be anything but flicker", () => {
    expect(isWindowLoading("5m", null)).toBe(false);
    expect(isWindowLoading("1h", "today")).toBe(false);
  });
});
