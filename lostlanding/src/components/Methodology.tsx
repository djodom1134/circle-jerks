import type { LedgerResponse } from "../lib/types";
import { formatUnixDate } from "../lib/format";

export function Methodology({ data }: { data: LedgerResponse }) {
  const { methodology } = data;
  const dataSinceLabel = formatUnixDate(methodology.data_since);

  return (
    <section id="method" className="section-pad dark-panel">
      <div className="section-heading light">
        <p className="kicker">How We Counted</p>
        <h2>The counting story is the credibility</h2>
        <p>
          {data.airport_icao} is untowered. There is no tower log — only what ADS-B lets us see. These are the exact
          detection rules behind every number on this page, unedited.
        </p>
      </div>

      <div className="method-grid">
        <div className="method-card">
          <h3>Landing</h3>
          <p>{methodology.definitions.landing}</p>
        </div>
        <div className="method-card">
          <h3>Touch-and-go</h3>
          <p>{methodology.definitions.touch_and_go}</p>
        </div>
        <div className="method-card">
          <h3>Low approach</h3>
          <p>{methodology.definitions.low_approach}</p>
        </div>
      </div>

      <div className="floor-callout">
        <span className="glyph" aria-hidden="true">
          ▲
        </span>
        <p>{methodology.floor_disclaimer}</p>
      </div>

      <p className="data-since">
        {dataSinceLabel ? `Counting continuously since ${dataSinceLabel}.` : "Start date for continuous counting not yet established."}{" "}
        Locality calls require at least {Math.round(methodology.locality_confidence_threshold * 100)}% confidence
        from evidence gathered over a trailing {methodology.locality_lookback_days}-day lookback.
      </p>

      <div className="attribution-list">
        <span>Data sources:</span>
        {Object.values(methodology.attribution).map((source) => (
          <a key={source.url} href={source.url} target="_blank" rel="noreferrer noopener">
            {source.name} ({source.license})
          </a>
        ))}
      </div>
    </section>
  );
}
