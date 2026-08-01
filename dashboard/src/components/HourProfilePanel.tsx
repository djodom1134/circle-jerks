import type { HourlyProfileResponse } from "../lib/types";

export function HourProfilePanel({ hourly }: { hourly: HourlyProfileResponse | null }) {
  if (!hourly) {
    return <div className="panel"><h6>By hour</h6><p className="cap">Unavailable for this window yet.</p></div>;
  }
  if (hourly.hours.length === 0) {
    return <div className="panel"><h6>By hour</h6><p className="cap">No activity recorded for this window.</p></div>;
  }
  const max = hourly.hours.reduce((m, h) => Math.max(m, h.operations), 0) || 1;
  return (
    <div className="panel">
      <h6>By hour</h6>
      <div className="heat">
        {hourly.hours.map((h) => (
          <i key={h.hour} title={`${h.hour}:00 — ${h.operations}`}
            style={{ background: "#2a78d6", opacity: 0.15 + (h.operations / max) * 0.85 }} />
        ))}
      </div>
      <p className="cap">Local time, 00–23.</p>
    </div>
  );
}
