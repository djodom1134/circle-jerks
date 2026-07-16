import { useEffect, useState } from "react";
import { getAirportFlow, type AirportFlowResponse } from "../lib/api";
import { elapsedLabel } from "../lib/elapsed";

export default function FlowBadge({ airportIcao }: { airportIcao?: string | null }) {
  const [flow, setFlow] = useState<AirportFlowResponse | null>(null);

  useEffect(() => {
    if (!airportIcao) {
      setFlow(null);
      return;
    }
    let active = true;
    const load = () => getAirportFlow(airportIcao).then((f) => { if (active) setFlow(f); }).catch(() => undefined);
    load();
    const timer = setInterval(load, 60_000);
    return () => { active = false; clearInterval(timer); };
  }, [airportIcao]);

  if (!flow?.active) return null;
  const change = flow.recent_changes[0];
  // Only a change the wind did NOT favor is a "cowboy" move. Wind-driven changes
  // are legitimate airmanship, so drop the 🤠 for them.
  const isCowboy = change != null && change.wind_favored_new === 0;
  return (
    <div className="flow-badge" title="Active runway in use">
      <span className="flow-badge-rwy">RWY {flow.active.active_runway_id}</span>
      {change && (
        <span className="flow-badge-change">
          changed {elapsedLabel(change.changed_at)} by{" "}
          {change.cowboy_callsign ?? change.cowboy_icao24 ?? "unknown"}
          {isCowboy ? " 🤠" : ""}
        </span>
      )}
    </div>
  );
}
