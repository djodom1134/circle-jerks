import type { LedgerResponse } from "../lib/types";
import { formatCompactCurrency, formatPercent } from "../lib/format";

/**
 * "The $73.6M question" -- a compact teaser on the main ledger that takes apart
 * the State's economic-impact headline and links to the full breakdown page
 * (public/what-the-70m-counts.html, served at /what-the-70m-counts.html).
 *
 * The dollar figures for the CLAIM are constants quoted from CDOT's 2025 study
 * (they are external facts, not something the ledger measures). Everything about
 * the REALITY -- the stop share and the fair-use fee revenue -- is derived live
 * from `data`, the same response every other section renders from, so it can
 * never drift from the numbers on the rest of the page.
 */
const STUDY_VISITOR_SPENDING = 28_900_000; // CDOT 2025: visitor-spending half of $73.6M
const LONGMONT_CITY_TAX_RATE = 0.0353; // City of Longmont sales tax
const FAIR_USE_FEE = 10; // $/runway use -- the ledger's default modelled fee

export function StudyClaim({ data }: { data: LedgerResponse }) {
  const { summary, visits, projection } = data;

  const stopShare = summary.runway_uses > 0 ? visits.stayed / summary.runway_uses : 0;
  const cityTaxOnClaim = STUDY_VISITOR_SPENDING * LONGMONT_CITY_TAX_RATE;
  const fairUseFeeRevenue = projection.projected_annual_runway_uses * FAIR_USE_FEE;

  return (
    <section id="study" className="section-pad">
      <div className="section-heading">
        <p className="kicker">The $73.6 million we were sold</p>
        <h2>A headline inflated end to end</h2>
        <p>
          Every time the runway's free ride comes up, we're told the airport generates <strong>$73.6 million a
          year</strong>. It is gross business revenue — not city income — and it is padded at every step:{" "}
          <strong>$44.7M</strong> is on-airport business (a drive-in skydiving operation that grosses roughly $9M at its
          own prices, then doubled by a "multiplier"), and <strong>$28.9M</strong> is "visitor spending" built on a
          phantom crowd of some 22,000 fly-in visitors the runway never carries. Here is what actually lands.
        </p>
      </div>

      <div className="locality-grid">
        <div className="locality-card">
          <strong>{formatPercent(stopShare)}</strong>
          <span className="locality-label">of runway uses are an actual stop</span>
          <p>
            The other ~97% is training or a fly-through — a low approach or a touch-and-go that never brings a passenger
            into Longmont.
          </p>
        </div>
        <div className="locality-card">
          <strong>{formatCompactCurrency(cityTaxOnClaim)}</strong>
          <span className="locality-label">the city's tax on the inflated visitor claim</span>
          <p>
            Longmont's 3.53% sales tax applied to the study's $28.9M visitor figure. On the visitor traffic that
            actually lands, the real tax is a few thousand dollars.
          </p>
        </div>
        <div className="locality-card">
          <strong>{formatCompactCurrency(fairUseFeeRevenue)}</strong>
          <span className="locality-label">a $10 fair-use runway fee would raise</span>
          <p>
            More than the city's entire tax take from the study's inflated visitor figure — and today the runway
            charges nothing at all.
          </p>
        </div>
      </div>

      <p style={{ marginTop: "22px" }}>
        And the airport's own 2026 books tell the same story: it collects <strong>$597,370 a year</strong> from all 122
        hangar and ground leases — 0.8% of the number it cites, its single biggest tenant a cell tower, and not one cent
        of it from the runway.
      </p>

      <p style={{ marginTop: "20px" }}>
        <a className="cta" href="/what-the-70m-counts.html">
          Read the full breakdown — the study vs. the real data →
        </a>
      </p>
    </section>
  );
}
