import { useEffect, useState } from "react";
import { getAirportFlow, type AirportFlowResponse } from "../lib/api";

function minutesAgo(ts: number): string {
  const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  return mins < 1 ? "just now" : `${mins}m ago`;
}

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
  return (
    <div className="flow-badge" title="Active runway in use">
      <span className="flow-badge-rwy">RWY {flow.active.active_runway_id}</span>
      {change && (
        <span className="flow-badge-change">
          changed {minutesAgo(change.changed_at)} by{" "}
          {change.cowboy_callsign ?? change.cowboy_icao24 ?? "unknown"} 🤠
        </span>
      )}
    </div>
  );
}
