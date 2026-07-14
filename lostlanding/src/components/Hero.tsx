import type { LedgerResponse } from "../lib/types";
import type { AircraftFeeEntry } from "../lib/liveTypes";
import { formatCurrency, formatCurrencyCents, formatInteger, formatShortDate, formatUnixDate } from "../lib/format";
import { useFee } from "../lib/feeContext";
import { useAircraftFees } from "../lib/aircraftFeesContext";
import { useLiveClock } from "../lib/useLiveClock";

function sumAircraftField(aircraft: Record<string, AircraftFeeEntry>, field: keyof AircraftFeeEntry & string): number {
  let sum = 0;
  for (const entry of Object.values(aircraft)) {
    const value = entry[field];
    if (typeof value === "number") sum += value;
  }
  return sum;
}

export function Hero({ data }: { data: LedgerResponse }) {
  const { summary, window, methodology } = data;
  const rangeLabel = `${formatShortDate(window.start_day)} – ${formatShortDate(window.end_day)}`;

  const { fee } = useFee();
  const { data: feesData, error: feesError } = useAircraftFees();

  // The debt clock ticks in COUNT space (see lib/liveClock.ts), never dollar
  // space, so a fee-slider change applies to whatever count is displayed
  // right now with zero extra plumbing: dollars = estimatedUses * fee, always
  // recomputed fresh at render time.
  const rollingUses = feesData?.today.runway_uses ?? null;
  const usesPerSecond = feesData?.rate_window.uses_per_second ?? 0;
  const estimatedUses = useLiveClock(rollingUses, usesPerSecond);
  const tickerDollars = estimatedUses * fee;
  const ready = feesData != null;
  const hasRecentActivity = (feesData?.rate_window.runway_uses ?? 0) > 0;

  // Companion figures give the headline weight even when the rolling-24h
  // number is thin (e.g. overnight): the SAME calculation, summed across
  // every aircraft, for the current local calendar month and for the
  // project's entire observation window.
  const monthUses = feesData ? sumAircraftField(feesData.aircraft, "month") : null;
  const sinceUses = feesData ? sumAircraftField(feesData.aircraft, "total") : null;
  const countingSinceLabel = feesData?.counting_since != null ? formatUnixDate(feesData.counting_since) : null;

  return (
    <section className="hero" id="top">
      <div className="hero-image" role="img" aria-label="Art Deco illustration of Longmont airport of the future" />
      <div className="hero-overlay" />
      <div className="hero-copy">
        <p className="eyebrow">LONGMONT, COLORADO &bull; {data.airport_icao}</p>

        <div className="hero-ticker">
          <p className="ticker-lede">We missed out on</p>
          <div className="ticker-amount" aria-live="polite" aria-atomic="true">
            {ready ? formatCurrencyCents(tickerDollars) : "—"}
          </div>
          <p className="ticker-qualifier">— the last 24 hours of traffic</p>
          <p className="ticker-rate-note">
            Ticking at the rate observed over the last 20 minutes — reconciled to the counted total on every
            update.{" "}
            {ready && !hasRecentActivity && "No runway use in the last 20 minutes right now."}
          </p>
        </div>

        <p className="hero-fee-line">
          At <strong>{formatCurrency(fee)}</strong> per runway use — a number <em>you</em> control below.
        </p>
        <p className="hero-disclaimer">
          Illustrative gross revenue under a fee Longmont does not currently charge. Not money owed, not collected,
          and not a proposal.
        </p>

        {ready && monthUses != null && sinceUses != null && (
          <div className="hero-companion-stats">
            <div>
              <strong>{formatCurrency(monthUses * fee)}</strong>
              <span>this month ({formatInteger(monthUses)} runway uses)</span>
            </div>
            <div>
              <strong>{formatCurrency(sinceUses * fee)}</strong>
              <span>
                since we started counting{countingSinceLabel ? ` (${countingSinceLabel})` : ""} (
                {formatInteger(sinceUses)} runway uses)
              </span>
            </div>
          </div>
        )}

        {feesError && (
          <p className="hero-fee-error" role="status">
            Couldn't refresh the live totals just now — showing the last successful figures.
          </p>
        )}

        <p className="lede">
          {data.airport_icao} is untowered — there is no tower log of who used the runway or how. Published
          operations figures for fields like this are FAA Form 5010 estimates. This ledger is different: it is built
          from discrete ADS-B-detected events — landings, touch-and-goes, and low approaches — over the trailing{" "}
          {window.days}-day window ({rangeLabel}). Every number is a floor, never an estimate: aircraft without
          ADS-B Out are invisible to us, so real activity is higher — never lower.
        </p>
        <span className="floor-chip">Every number below is a floor, never an estimate</span>
        <div>
          <a className="cta" href="#calculator">
            See what a fair-use fee could raise
          </a>
        </div>
      </div>
      <div className="hero-meter">
        <div>
          <span className="label">
            {methodology.billable_unit_label}, last {window.days} days
          </span>
          <strong>{formatInteger(summary.runway_uses)}</strong>
          <span className="sub">landings + touch-and-goes + low approaches, never takeoffs</span>
        </div>
        <div>
          <span className="label">Unique aircraft observed</span>
          <strong>{formatInteger(summary.unique_aircraft)}</strong>
          <span className="sub">by ADS-B tail number, this window only</span>
        </div>
      </div>
    </section>
  );
}
