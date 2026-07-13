import type { LedgerResponse } from "../lib/types";
import { formatInteger, formatShortDate } from "../lib/format";

export function Hero({ data }: { data: LedgerResponse }) {
  const { summary, window, methodology } = data;
  const rangeLabel = `${formatShortDate(window.start_day)} – ${formatShortDate(window.end_day)}`;

  return (
    <section className="hero" id="top">
      <div className="hero-image" role="img" aria-label="Art Deco illustration of Longmont airport of the future" />
      <div className="hero-overlay" />
      <div className="hero-copy">
        <p className="eyebrow">LONGMONT, COLORADO &bull; {data.airport_icao}</p>
        <h1>
          We didn't estimate this number.
          <span>We counted it, one detected runway use at a time.</span>
        </h1>
        <p className="lede">
          {data.airport_icao} is untowered — there is no tower log of who used the runway or how. Published
          operations figures for fields like this are FAA Form 5010 estimates. This ledger is different: it is built
          from discrete ADS-B-detected events — landings, touch-and-goes, and low approaches — over the trailing{" "}
          {window.days}-day window ({rangeLabel}).
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
