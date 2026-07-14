import type { LedgerResponse } from "../lib/types";
import { formatDuration, formatInteger, formatPercent } from "../lib/format";
import { ZeroBanner } from "./ZeroBanner";

/**
 * "Who Actually Stopped?" -- the standard case for a small GA airport is that
 * it brings visitors who spend money in town. That is a claim, and this
 * section is where we test it against what we actually counted.
 *
 * Two figures do all the work here, and they answer two DIFFERENT questions:
 *
 *   1. Of every runway use, how much of it never touched down to stay at all
 *      (low approaches + touch-and-goes)? This is the finding -- see
 *      `.visits-headline` below, which is deliberately the loudest thing in
 *      this section.
 *   2. Of the landings where we could see BOTH the arrival and the departure
 *      (`visits.paired` -- always a small slice of `visits.landings`, see
 *      `visits.coverage`), how many stayed 20+ minutes (`visits.stayed`)
 *      versus a quick turn (`visits.quick_turn`)?
 *
 * Never conflate the two, and never let `visits.stayed` be read as "N
 * aircraft visited Longmont" without `visits.paired` / `visits.landings`
 * sitting right next to it -- the honesty note at the bottom exists
 * specifically to keep that from happening. See ledger-api/app/dwell.py's
 * `visit_summary` docstring for the backend side of this same discipline.
 */
export function RealVisits({ data }: { data: LedgerResponse }) {
  const { summary, visits, airport_icao } = data;

  const neverStopped = summary.by_type.low_approach + summary.by_type.touch_and_go;
  const neverStoppedShare = summary.runway_uses > 0 ? neverStopped / summary.runway_uses : 0;

  const hasPairs = visits.paired > 0;
  const stayedShare = hasPairs ? visits.stayed / visits.paired : 0;
  const quickTurnShare = hasPairs ? visits.quick_turn / visits.paired : 0;

  return (
    <section id="visits" className="section-pad">
      <div className="section-heading">
        <p className="kicker">Who Actually Stopped?</p>
        <h2>A runway use isn't the same thing as a visit</h2>
        <p>
          The standard case for a general-aviation airport is that it brings visitors who spend money in town. That
          is a claim, and it is measurable. A runway use only tells us an aircraft arrived — not that anyone stopped,
          fuelled, ate, or spent a dollar in Longmont. Here is what the data actually shows.
        </p>
      </div>

      <div className="visits-headline">
        <p className="visits-headline-figure">{formatPercent(neverStoppedShare)}</p>
        <p className="visits-headline-text">
          of all runway use at {airport_icao} this window was an aircraft that never stopped at all —{" "}
          <strong>{formatInteger(summary.by_type.low_approach)} low approaches</strong> and{" "}
          <strong>{formatInteger(summary.by_type.touch_and_go)} touch-and-goes</strong>, out of{" "}
          {formatInteger(summary.runway_uses)} total runway uses. Only {formatInteger(summary.by_type.landing)} were
          landings at all.
        </p>
      </div>

      {!hasPairs ? (
        <ZeroBanner title="No landing-to-takeoff pairs to measure this window">
          We could not pair any landing with a subsequent takeoff in this window, so there is nothing here to call a
          "stay" yet. That does not mean nobody stopped — see the coverage note below for what an unpaired landing
          can mean.
        </ZeroBanner>
      ) : (
        <>
          <p className="visits-lede">
            Of the <strong>{formatInteger(visits.paired)} of {formatInteger(visits.landings)}</strong> landings where
            we watched both the arrival <em>and</em> the departure, here is how long they actually stayed on the
            ground.
          </p>

          <div className="locality-grid visits-grid">
            <div className="locality-card">
              <strong>{formatInteger(visits.stayed)}</strong>
              <span className="locality-label">Stayed 20+ minutes</span>
              <p>
                {formatPercent(stayedShare)} of the {formatInteger(visits.paired)} pairs we could measure.
                {visits.median_stay_seconds !== null &&
                  ` Median stay among them: ${formatDuration(visits.median_stay_seconds)}.`}
              </p>
            </div>
            <div className="locality-card unclassified">
              <strong>{formatInteger(visits.quick_turn)}</strong>
              <span className="locality-label">Quick turn, under 20 minutes</span>
              <p>
                {formatPercent(quickTurnShare)} of the {formatInteger(visits.paired)} pairs we could measure — dropped
                someone, picked someone up, refuelled, or just touched a wheel and left.
              </p>
            </div>
          </div>
        </>
      )}

      <p className="honesty-note">
        The 20-minute line is our own judgment call for "actually stopped and did something" — it is not an FAA or
        industry standard. And {formatInteger(visits.paired)} pairs is all we could measure: of{" "}
        {formatInteger(visits.landings)} landings this window, we watched both the arrival and the departure for only{" "}
        {formatPercent(visits.coverage)} of them. The rest are aircraft still on the field, or departures we missed
        entirely — we cannot tell which from here, so we do not guess. The real number of aircraft that genuinely
        stopped at {airport_icao} is higher than {formatInteger(visits.stayed)}, not lower — like every other count
        on this page, this is a floor, not an estimate.
      </p>
    </section>
  );
}
