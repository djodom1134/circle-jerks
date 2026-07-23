import type { WorstOffendersResponse } from "../lib/types";
import { formatInteger } from "../lib/format";

export function AircraftPanel({ offenders }: { offenders: WorstOffendersResponse | null }) {
  if (!offenders || offenders.offenders.length === 0) {
    return <div className="panel"><h6>Busiest aircraft</h6><p className="cap">Unavailable for this airport yet.</p></div>;
  }
  const max = offenders.offenders[0].report_count || 1;
  return (
    <div className="panel">
      <h6>Busiest aircraft</h6>
      {offenders.offenders.map((o) => (
        <div className="rowbar" key={o.icao24}>
          <span className="nm">{o.registration ?? o.icao24}</span>
          <span className="tr"><span className="fl" style={{ width: `${Math.round((o.report_count / max) * 100)}%` }} /></span>
          <span className="ct">{formatInteger(o.report_count)}</span>
        </div>
      ))}
    </div>
  );
}
