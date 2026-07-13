import type { LedgerResponse } from "../lib/types";
import { formatInteger, formatPercent } from "../lib/format";
import { ZeroBanner } from "./ZeroBanner";

export function LocalityBreakdown({ data }: { data: LedgerResponse }) {
  const { summary } = data;
  const hasAircraft = summary.unique_aircraft > 0;
  const localShare = hasAircraft ? summary.local_aircraft / summary.unique_aircraft : 0;
  const unclassifiedShare = hasAircraft ? summary.unclassified_aircraft / summary.unique_aircraft : 0;

  return (
    <section id="based-here" className="section-pad">
      <div className="section-heading">
        <p className="kicker">Who's Based Here</p>
        <h2>What we can actually tell you</h2>
        <p>
          We classify aircraft as local when the evidence clears a confidence threshold. Everything else is
          unclassified, counted visibly, and never guessed at.
        </p>
      </div>

      {!hasAircraft ? (
        <ZeroBanner title="No aircraft observed in this window">
          Zero unique aircraft were detected at {data.airport_icao} for {data.window.start_day} to{" "}
          {data.window.end_day}, so there is nothing to classify as local or unclassified yet.
        </ZeroBanner>
      ) : (
        <div className="locality-grid">
          <div className="locality-card">
            <strong>{formatInteger(summary.local_aircraft)}</strong>
            <span className="locality-label">Local aircraft</span>
            <p>
              {formatPercent(localShare)} of the {formatInteger(summary.unique_aircraft)} unique aircraft observed
              this window meet our locality-confidence threshold.
            </p>
          </div>
          <div className="locality-card unclassified">
            <strong>{formatInteger(summary.unclassified_aircraft)}</strong>
            <span className="locality-label">Unclassified</span>
            <p>
              {formatPercent(unclassifiedShare)} of unique aircraft — we can't determine their locality. We don't
              publish guesses.
            </p>
          </div>
        </div>
      )}

      <p className="honesty-note">
        There is no "estimated non-local share" on this page. Establishing non-locality requires origin evidence
        that, for this historical window, is forward-fill only — it doesn't exist yet, so
        {summary.non_local_aircraft > 0
          ? ` only ${formatInteger(summary.non_local_aircraft)} aircraft currently clear that bar.`
          : " we currently cannot establish non-locality for this window."}{" "}
        We would rather show you an honest "we don't know" than a confident-looking number we can't back up.
      </p>
    </section>
  );
}
