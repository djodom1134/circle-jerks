import type { LedgerResponse } from "../lib/types";

const METHODOLOGY: LedgerResponse["methodology"] = {
  billable_unit: "runway_use",
  billable_unit_label: "runway uses",
  billable_unit_description:
    "A runway use is one arrival at the runway: a landing, a touch-and-go, or a low approach. It is not an FAA 'operation' — an operation is a takeoff OR a landing, so a touch-and-go counts as two, and multiplying a fee by an operations count would double-count every one of them.",
  definitions: {
    landing:
      "The aircraft descended from at least 500 ft above the field, came within 1.5 nm of the runway at 200 ft AGL or below, and did not climb back out within 300 seconds.",
    touch_and_go:
      "The aircraft flew a closed pattern circuit whose track passed within 0.25 nm of the runway. This is a geometric test: it does not confirm that the wheels touched the pavement. We count it as a use of the runway, not as a verified touchdown.",
    low_approach:
      "The aircraft approached from at least 500 ft above the field, came within 1.5 nm of the runway at 200 ft AGL or below at 90 knots or less, spent no more than 60 seconds on the ground, and climbed back out.",
  },
  floor_disclaimer:
    "Every count on this page is a floor, not an estimate. Aircraft without ADS-B Out are invisible to us, so real activity is higher than what we report — never lower.",
  data_since: null,
  locality_confidence_threshold: 0.5,
  locality_lookback_days: 180,
  attribution: {
    adsb_lol: {
      name: "adsb.lol",
      license: "ODbL (Open Database License) — volunteer ADS-B feeder community",
      url: "https://adsb.lol",
    },
  },
};

/** Exactly what the live API returned for KLMO on a day with no rolled-up data. */
export const ZERO_LEDGER_FIXTURE: LedgerResponse = {
  airport_icao: "KLMO",
  timezone: "America/Denver",
  window: { days: 30, start_day: "2026-06-14", end_day: "2026-07-13" },
  summary: {
    runway_uses: 0,
    by_type: { landing: 0, touch_and_go: 0, low_approach: 0 },
    unique_aircraft: 0,
    local_aircraft: 0,
    non_local_aircraft: 0,
    unclassified_aircraft: 0,
    dwell: { median_seconds: null, sample_size: 0, landings: 0, coverage: 0 },
  },
  daily: Array.from({ length: 30 }, (_, i) => ({
    date: `2026-06-${String(14 + i).padStart(2, "0")}`,
    runway_uses: 0,
  })),
  operators: [],
  visits: {
    min_seconds: 1200,
    stayed: 0,
    quick_turn: 0,
    paired: 0,
    landings: 0,
    coverage: 0,
    median_stay_seconds: null,
  },
  projection: {
    counting_since: null,
    days_of_data: 0,
    runway_uses_to_date: 0,
    observed_daily_rate: 0,
    annualization_days: 365,
    projected_annual_runway_uses: 0,
  },
  methodology: METHODOLOGY,
};

/** A synthetic non-zero window, shaped like the real API, for exercising the populated UI. */
export const SAMPLE_LEDGER_FIXTURE: LedgerResponse = {
  airport_icao: "KLMO",
  timezone: "America/Denver",
  window: { days: 30, start_day: "2026-06-14", end_day: "2026-07-13" },
  summary: {
    runway_uses: 300,
    by_type: { landing: 120, touch_and_go: 150, low_approach: 30 },
    unique_aircraft: 42,
    local_aircraft: 20,
    non_local_aircraft: 0,
    unclassified_aircraft: 22,
    dwell: { median_seconds: 252, sample_size: 90, landings: 120, coverage: 0.75 },
  },
  daily: Array.from({ length: 30 }, (_, i) => ({
    date: `2026-06-${String(14 + i).padStart(2, "0")}`,
    runway_uses: i % 7 === 0 ? 0 : 10,
  })),
  operators: [
    {
      operator: "Longmont Flight Academy",
      owner_type: "flight_school",
      runway_uses: 140,
      aircraft_count: 4,
      aircraft: [
        { tail: "N172LM", runway_uses: 60 },
        { tail: "N738BJ", runway_uses: 40 },
        { tail: "N221PA", runway_uses: 25 },
        { tail: "N904AT", runway_uses: 15 },
      ],
      locality: "local",
      locality_evidence: [{ code: "based_field", text: "Registered home base matches KLMO." }],
    },
    {
      operator: "Private / unaffiliated",
      owner_type: "private",
      runway_uses: 160,
      aircraft_count: 18,
      aircraft: [
        { tail: "N615MC", runway_uses: 12 },
        { tail: "N842SP", runway_uses: 9 },
      ],
      locality: "unclassified",
      locality_evidence: [],
    },
  ],
  visits: {
    min_seconds: 1200,
    stayed: 40,
    quick_turn: 65,
    paired: 105,
    landings: 120,
    coverage: 0.875,
    median_stay_seconds: 2400,
  },
  projection: {
    counting_since: 1750000000, // 2025-06-15 UTC-ish
    days_of_data: 30,
    runway_uses_to_date: 300,
    observed_daily_rate: 10,
    annualization_days: 365,
    projected_annual_runway_uses: 3650,
  },
  methodology: METHODOLOGY,
};
