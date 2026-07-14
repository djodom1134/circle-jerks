import { useId, useMemo, useState } from "react";
import type { LedgerResponse } from "../lib/types";
import { computeRevenue } from "../lib/calculator";
import { formatCompactCurrency, formatCurrency, formatInteger } from "../lib/format";
import { useFee } from "../lib/feeContext";

const FEE_PRESETS = [5, 10, 15] as const;

export function Calculator({ data }: { data: LedgerResponse }) {
  // Shared with the live map's price tags and the hero ticker -- one fee
  // slider, one source of truth (see lib/feeContext.tsx), not three
  // independent copies that could silently drift apart.
  const { fee, setFee } = useFee();
  const [billablePercent, setBillablePercent] = useState(100);

  const feeId = useId();
  const billableId = useId();

  const { summary, window: ledgerWindow, methodology } = data;

  const result = useMemo(
    () =>
      computeRevenue({
        runwayUses: summary.runway_uses,
        windowDays: ledgerWindow.days,
        feePerUse: fee,
        billableShare: billablePercent / 100,
      }),
    [summary.runway_uses, ledgerWindow.days, fee, billablePercent],
  );

  const isZeroWindow = summary.runway_uses === 0;

  return (
    <section id="calculator" className="section-pad">
      <div className="section-heading">
        <p className="kicker">The Fair-Use Calculator</p>
        <h2>What if each runway use paid something?</h2>
        <p>{methodology.billable_unit_description}</p>
      </div>

      <div className="calc-grid">
        <div className="controls art-card">
          <label htmlFor={feeId}>
            Fee per runway use <output htmlFor={feeId}>{formatCurrency(fee)}</output>
          </label>
          <input
            id={feeId}
            type="range"
            min={0}
            max={30}
            step={1}
            value={fee}
            onChange={(e) => setFee(Number(e.target.value))}
          />

          <label htmlFor={billableId}>
            Billable share (illustrative) <output htmlFor={billableId}>{billablePercent}%</output>
          </label>
          <input
            id={billableId}
            type="range"
            min={10}
            max={100}
            step={5}
            value={billablePercent}
            onChange={(e) => setBillablePercent(Number(e.target.value))}
          />
          <p className="helptext">
            Billable share models exemptions (e.g. based aircraft discounts, training waivers) — it is a modeling
            assumption you control, not a figure the API reports.
          </p>

          <div className="switches" role="group" aria-label="Fee presets">
            {FEE_PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                className={`preset${fee === preset ? " active" : ""}`}
                aria-pressed={fee === preset}
                onClick={() => setFee(preset)}
              >
                {preset === 5 ? "Conservative" : preset === 10 ? "Fair-use" : "Impact"} {formatCurrency(preset)}
              </button>
            ))}
          </div>
        </div>

        <div className="results">
          <div className="money-card primary">
            <span>Potential annual revenue</span>
            <strong>{formatCurrency(result.perYear)}</strong>
            <small>Projected from the observed daily rate over the last {ledgerWindow.days} days</small>
          </div>
          <div className="money-row">
            <div className="money-card">
              <span>Per month</span>
              <strong>{formatCurrency(result.perMonth)}</strong>
            </div>
            <div className="money-card">
              <span>Per day</span>
              <strong>{formatCurrency(result.perDay)}</strong>
            </div>
          </div>
          <div className="money-row">
            <div className="money-card">
              <span>Five-year reserve</span>
              <strong>{formatCompactCurrency(result.fiveYear)}</strong>
            </div>
            <div className="money-card">
              <span>Ten-year reserve</span>
              <strong>{formatCompactCurrency(result.tenYear)}</strong>
            </div>
          </div>
          <p className="fineprint">
            Anchored to {formatInteger(summary.runway_uses)} real {methodology.billable_unit_label} observed over{" "}
            {ledgerWindow.days} days ({result.dailyRate.toFixed(1)} / day) — not a hardcoded assumption. Illustrative
            gross revenue before collection costs, exemptions, and legal review; not a proposal or a legal opinion.
          </p>
          {isZeroWindow && (
            <p className="fineprint">
              This projects to $0 because zero {methodology.billable_unit_label} were observed in the current
              window — the arithmetic is honest, not broken. A window with detected activity would produce a nonzero
              figure from the same formula.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
