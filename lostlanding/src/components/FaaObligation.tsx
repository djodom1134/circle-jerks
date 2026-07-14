import type { LedgerResponse } from "../lib/types";
import { formatInteger } from "../lib/format";

const STATUTE_URL = "https://www.law.cornell.edu/uscode/text/49/47107";
const GRANT_ASSURANCES_URL = "https://www.faa.gov/airports/aip/grant_assurances/assurances-airport-sponsors";
const GRANT_AWARD_URL = "https://www.faa.gov/airports/aip/2026_aip_grants/AIP_FY26_1.pdf";

export function FaaObligation({ data }: { data: LedgerResponse }) {
  return (
    <section id="obligation" className="section-pad">
      <div className="section-heading">
        <p className="kicker">The Federal Obligation</p>
        <h2>The FAA already told Longmont to do this</h2>
        <p>
          {data.airport_icao} didn't just decline to charge for the runway. As a condition of every dollar of federal
          airport aid it has ever accepted, the City signed a promise about what this airport's fee structure has to
          try to be. Here is that language, unedited, and the check the FAA cut Longmont this year.
        </p>
      </div>

      <div className="obligation-quotes">
        <figure className="obligation-quote art-card">
          <blockquote>
            &ldquo;a schedule of charges for use of facilities and services at the airport — (A) that will make the
            airport as self-sustaining as possible under the circumstances existing at the airport, including volume
            of traffic and economy of collection.&rdquo;
          </blockquote>
          <figcaption>
            <cite>
              49 U.S.C. § 47107(a)(13)(A) &mdash;{" "}
              <a href={STATUTE_URL} target="_blank" rel="noopener noreferrer">
                law.cornell.edu
              </a>
            </cite>
          </figcaption>
        </figure>

        <figure className="obligation-quote art-card">
          <blockquote>
            &ldquo;will maintain a fee and rental structure for the facilities and services at the airport which will
            make the airport as self-sustaining as possible under the circumstances existing at the particular
            airport, taking into account such factors as the volume of traffic and economy of collection.&rdquo;
          </blockquote>
          <figcaption>
            <cite>
              FAA Grant Assurance 24, &ldquo;Fee and Rental Structure&rdquo; — signed by the City of Longmont &mdash;{" "}
              <a href={GRANT_ASSURANCES_URL} target="_blank" rel="noopener noreferrer">
                faa.gov
              </a>
            </cite>
          </figcaption>
        </figure>
      </div>

      <div className="obligation-grant">
        <p className="grant-figure">$725,000</p>
        <p className="grant-detail">
          The FAA awarded Vance Brand Municipal Airport this sum on <strong>March 17, 2026</strong>, under the
          Airport Improvement Program, to reconstruct a taxilane &mdash;{" "}
          <a href={GRANT_AWARD_URL} target="_blank" rel="noopener noreferrer">
            read the grant award (PDF)
          </a>
          .
        </p>
        <p className="grant-kicker">
          Longmont accepted $725,000 in federal money this year to rebuild a taxilane, while charging nothing at all
          for the runway.
        </p>
      </div>

      <div className="obligation-economy">
        <h3>The excuse this page dissolves</h3>
        <p>
          The statute's own qualifier is <em>&ldquo;economy of collection.&rdquo;</em> That is the historic reason
          small, untowered fields like {data.airport_icao} charge nothing: with no tower and no staff, counting who
          used the runway and billing them cost more than it raised.
        </p>
        <p>
          We already counted {formatInteger(data.summary.runway_uses)} runway uses at {data.airport_icao} in the
          trailing {data.window.days}-day window &mdash; with no tower, no staff, and no budget beyond public ADS-B
          data. Collection is now an arithmetic problem, not a staffing problem. The circumstance the statute told
          Longmont to weigh has changed.
        </p>
      </div>

      <div className="obligation-revenue">
        <figure className="obligation-quote art-card">
          <blockquote>
            &ldquo;will be expended for the capital or operating costs of — (A) the airport; (B) the local airport
            system; or (C) other local facilities owned or operated by the airport owner or operator and directly and
            substantially related to the air transportation of passengers or property.&rdquo;
          </blockquote>
          <figcaption>
            <cite>
              49 U.S.C. § 47107(b) / FAA Grant Assurance 25, &ldquo;Airport Revenues&rdquo; &mdash;{" "}
              <a href={STATUTE_URL} target="_blank" rel="noopener noreferrer">
                law.cornell.edu
              </a>
            </cite>
          </figcaption>
        </figure>
        <p>
          That is not a limitation on this idea &mdash; it is a guarantee. Every dollar a runway-use fee raised would
          be locked to {data.airport_icao} itself: the taxiway, the reserve fund, the emergency fund, the deferred
          maintenance already listed above. Not a slush fund. Not the general fund. Not a road project across town.
        </p>
      </div>

      <p className="legal-note">
        <strong>What the FAA does not require:</strong> the FAA does not mandate a landing fee, and plenty of small
        GA airports charge none and remain fully compliant. What Longmont signed is narrower and harder to wave away:
        a promise to make the airport <strong>as self-sustaining as possible under the circumstances existing</strong>{" "}
        at the airport &mdash; and the City, not the FAA, gets to decide what that means. Now that counting no longer
        needs a tower or a staff, we're asking Longmont to show its work on why zero is still the answer. Fee design,
        exemptions, and collection remain a matter for the City and its aviation counsel.
      </p>
    </section>
  );
}
