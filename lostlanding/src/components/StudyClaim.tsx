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
        <p className="kicker">The $73.6 million question</p>
        <h2>What the State's headline really counts</h2>
        <p>
          The State's economic-impact study says Vance Brand generates <strong>$73.6 million a year</strong>. It is
          gross business revenue — not city income — and most of it never touches the runway: <strong>$44.7M</strong> is
          on-airport business (a drive-in skydiving operation grossing roughly $9M at its own prices, plus a
          multiplier), and <strong>$28.9M</strong> is "visitor spending" that assumes some 22,000 people fly in and
          spend. Here is what the runway actually carries.
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

      <p style={{ marginTop: "24px" }}>
        <a className="cta" href="/what-the-70m-counts.html">
          Read the full breakdown — the study vs. the real data →
        </a>
      </p>
    </section>
  );
}
