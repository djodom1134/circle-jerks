import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Calculator } from "./Calculator";
import { SAMPLE_LEDGER_FIXTURE, ZERO_LEDGER_FIXTURE } from "../test/fixtures";

afterEach(cleanup);

describe("Calculator", () => {
  it("wires the real observed runway-use rate into the default $10 projection", () => {
    render(<Calculator data={SAMPLE_LEDGER_FIXTURE} />);

    // 300 runway uses / 30 days = 10/day * $10 * 365 = $36,500 at the default fee.
    expect(screen.getByText("$36,500")).toBeTruthy();
  });

  it("renders an explicit $0 projection -- not a hidden or broken state -- for a zero window", () => {
    render(<Calculator data={ZERO_LEDGER_FIXTURE} />);

    expect(screen.getAllByText("$0").length).toBeGreaterThan(0);
    expect(screen.getByText(/arithmetic is honest, not broken/i)).toBeTruthy();
  });

  it("renders the API's billable_unit_description verbatim", () => {
    render(<Calculator data={SAMPLE_LEDGER_FIXTURE} />);

    expect(screen.getByText(SAMPLE_LEDGER_FIXTURE.methodology.billable_unit_description)).toBeTruthy();
  });
});
