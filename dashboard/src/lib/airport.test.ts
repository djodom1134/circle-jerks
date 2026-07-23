import { describe, expect, it } from "vitest";
import { resolveAirport } from "./airport";

describe("resolveAirport", () => {
  it("maps a known subdomain to its ICAO", () => {
    expect(resolveAirport({ hostname: "klmo.airfieldeconomics.org" })).toBe("KLMO");
  });

  it("uppercases and honors the ?airport= override above the hostname", () => {
    expect(resolveAirport({ hostname: "klmo.airfieldeconomics.org", search: "?airport=kbjc" })).toBe("KBJC");
  });

  it("falls back to KLMO for an unknown host", () => {
    expect(resolveAirport({ hostname: "localhost" })).toBe("KLMO");
  });
});
