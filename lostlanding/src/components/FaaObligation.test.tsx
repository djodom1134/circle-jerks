import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { FaaObligation } from "./FaaObligation";
import { SAMPLE_LEDGER_FIXTURE, ZERO_LEDGER_FIXTURE } from "../test/fixtures";
import type { LedgerResponse } from "../lib/types";

afterEach(() => {
  cleanup();
});

function renderObligation(data: LedgerResponse = SAMPLE_LEDGER_FIXTURE) {
  render(<FaaObligation data={data} />);
  return { text: () => document.body.textContent ?? "" };
}

describe("FaaObligation", () => {
  it("quotes the self-sustaining statute and Grant Assurance 24 verbatim, linked to their real sources", () => {
    const { text } = renderObligation();

    expect(text()).toMatch(
      /that will make the airport as self-sustaining as possible under the circumstances existing at the airport, including volume of traffic and economy of collection/i,
    );
    expect(text()).toMatch(
      /will maintain a fee and rental structure for the facilities and services at the airport which will make the airport as self-sustaining as possible under the circumstances existing at the particular airport, taking into account such factors as the volume of traffic and economy of collection/i,
    );

    // The same statute (§ 47107) backs both the self-sustaining quote and the
    // Grant Assurance 25 revenue quote below, so it's linked twice.
    const statuteLinks = screen.getAllByRole("link", { name: "law.cornell.edu" });
    expect(statuteLinks.length).toBe(2);
    for (const link of statuteLinks) {
      expect(link.getAttribute("href")).toBe("https://www.law.cornell.edu/uscode/text/49/47107");
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    }

    const assuranceLink = screen.getByRole("link", { name: "faa.gov" });
    expect(assuranceLink.getAttribute("href")).toBe(
      "https://www.faa.gov/airports/aip/grant_assurances/assurances-airport-sponsors",
    );
    expect(assuranceLink.getAttribute("target")).toBe("_blank");
    expect(assuranceLink.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("cites the $725,000 March 17, 2026 AIP grant to Vance Brand, linked to the FAA's own award PDF", () => {
    const { text } = renderObligation();

    expect(text()).toContain("$725,000");
    expect(text()).toMatch(/march 17, 2026/i);
    expect(text()).toMatch(/vance brand municipal airport/i);
    expect(text()).toMatch(
      /longmont accepted \$725,000 in federal money this year to rebuild a taxilane, while charging nothing at all for the runway/i,
    );

    const grantLink = screen.getByRole("link", { name: /read the grant award/i });
    expect(grantLink.getAttribute("href")).toBe("https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1.pdf");
    expect(grantLink.getAttribute("target")).toBe("_blank");
    expect(grantLink.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("makes the economy-of-collection argument: counting is free now, so the historic excuse is gone", () => {
    const { text } = renderObligation();

    expect(text()).toMatch(/economy of collection/i);
    expect(text()).toMatch(/we already counted 300 runway uses/i);
    expect(text()).toMatch(/collection is now an arithmetic problem, not a staffing problem/i);
  });

  it("folds in Grant Assurance 25 (revenue stays on the airport) as a feature, not a footnote", () => {
    const { text } = renderObligation();

    expect(text()).toMatch(/will be expended for the capital or operating costs of/i);
    expect(text()).toMatch(/not a slush fund/i);
  });

  it("states the honest limit plainly: the FAA does not mandate a fee, and the City decides", () => {
    const { text } = renderObligation();

    expect(text()).toMatch(/the faa does not mandate a landing fee/i);
    expect(text()).toMatch(/the city, not the faa, gets to decide what that means/i);
  });

  it("never renders the forbidden claims that would discredit the whole site", () => {
    const { text } = renderObligation();
    const lower = text().toLowerCase();

    expect(lower).not.toContain("requires a landing fee");
    expect(lower).not.toContain("violating federal law");
    expect(lower).not.toContain("illegal");
  });

  it("still renders sensibly against a zero-activity window", () => {
    const { text } = renderObligation(ZERO_LEDGER_FIXTURE);
    expect(text()).toMatch(/we already counted 0 runway uses/i);
  });
});
