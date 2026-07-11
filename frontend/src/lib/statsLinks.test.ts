import { describe, it, expect } from "vitest";
import { statsHighlightHref, windowFromUrl } from "./statsLinks";

describe("statsHighlightHref", () => {
  it("builds the deep-link with all-time window", () => {
    expect(statsHighlightHref("KLMO", "a23a01")).toBe(
      "/stats?airport=KLMO&aircraft=a23a01&win=all",
    );
  });
  it("upper-cases the airport and lower-cases the icao24", () => {
    expect(statsHighlightHref("klmo", "A23A01")).toBe(
      "/stats?airport=KLMO&aircraft=a23a01&win=all",
    );
  });
  it("falls back to KBJC when the airport is missing", () => {
    expect(statsHighlightHref(undefined, "a23a01")).toBe(
      "/stats?airport=KBJC&aircraft=a23a01&win=all",
    );
  });
});

describe("windowFromUrl", () => {
  it("returns the win param when valid", () => {
    expect(windowFromUrl("?win=all")).toBe("all");
    expect(windowFromUrl("?airport=KLMO&win=30d")).toBe("30d");
  });
  it("defaults to 7d for missing or invalid win", () => {
    expect(windowFromUrl("")).toBe("7d");
    expect(windowFromUrl("?win=bogus")).toBe("7d");
  });
});
