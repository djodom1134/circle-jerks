import type { LedgerResponse } from "../lib/types";
import { Hero } from "./Hero";
import { LiveMap } from "./LiveMap";
import { Calculator } from "./Calculator";
import { DailyChart } from "./DailyChart";
import { OperatorLedger } from "./OperatorLedger";
import { LocalityBreakdown } from "./LocalityBreakdown";
import { Methodology } from "./Methodology";
import { FaaObligation } from "./FaaObligation";
import { LawfulUse } from "./LawfulUse";
import { ZeroBanner } from "./ZeroBanner";

export function LedgerPage({ data }: { data: LedgerResponse }) {
  const isZeroWindow = data.summary.runway_uses === 0;

  return (
    <main>
      <Hero data={data} />
      <LiveMap />
      {isZeroWindow && (
        <div className="section-pad" style={{ paddingBottom: 0 }}>
          <ZeroBanner title="This window shows zero runway uses — and that's shown deliberately">
            The ledger API returned zero detected runway uses for {data.window.start_day} to {data.window.end_day}.
            That can mean the nightly rollup hasn't run yet, or that {data.airport_icao} genuinely had no
            ADS-B-visible runway use in this window. Either way, every section below shows the real, honest number —
            never a placeholder.
          </ZeroBanner>
        </div>
      )}
      <Calculator data={data} />
      <DailyChart data={data} />
      <OperatorLedger data={data} />
      <LocalityBreakdown data={data} />
      <Methodology data={data} />
      <FaaObligation data={data} />
      <LawfulUse data={data} />
    </main>
  );
}
