import type { OriginsResponse } from "../lib/types";
import { usePrefersReducedMotion } from "../lib/palette";
import { formatInteger } from "../lib/format";

const CX = 210, CY = 150, R = 110, VIEW_W = 420, VIEW_H = 300;

export function OriginHub({ origins, selectedOrigin, onSelectOrigin, airportIcao }: {
  origins: OriginsResponse | null;
  selectedOrigin: string | null;
  onSelectOrigin(icao: string | null): void;
  airportIcao: string;
}) {
  const reduce = usePrefersReducedMotion();
  if (!origins || origins.origins.length === 0) {
    return <section className="hub"><h5>Where out-of-town traffic comes from</h5>
      <p className="cap">Origin data unavailable for this airport yet.</p></section>;
  }

  const maxArr = origins.origins[0].arrivals || 1;
  const n = origins.origins.length;

  const toggle = (icao: string) => onSelectOrigin(selectedOrigin === icao ? null : icao);

  return (
    <section className="hub">
      <h5>Where out-of-town traffic comes from</h5>
      <p className="cap">{formatInteger(origins.total_out_of_town)} out-of-town arrivals · {n} origins shown · click one to filter the page.</p>
      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className={reduce ? "" : "animate"} role="group" aria-label="Origin airports and their arrivals into the field">
        {origins.origins.map((o, i) => {
          const ang = (-90 + i * (360 / n)) * Math.PI / 180;
          const x = CX + Math.cos(ang) * R, y = CY + Math.sin(ang) * R;
          const w = 1.2 + (o.arrivals / maxArr) * 5;
          const d = `M${x} ${y} Q${(CX + x) / 2 + (CY - y) * 0.12} ${(CY + y) / 2 + (x - CX) * 0.12} ${CX} ${CY}`;
          const dim = selectedOrigin && selectedOrigin !== o.icao;
          return (
            <path key={`e-${o.icao}`} d={d} fill="none" stroke="#eb6834" strokeWidth={w}
              strokeLinecap="round" strokeOpacity={dim ? 0.08 : selectedOrigin ? 0.85 : 0.3} />
          );
        })}
        <circle cx={CX} cy={CY} r={17} fill="#0b1e2b" stroke="#2a78d6" strokeWidth={2} />
        <text x={CX} y={CY + 4} textAnchor="middle" fontSize={9} fontWeight={700} fill="#fff">{airportIcao}</text>
        {origins.origins.map((o, i) => {
          const ang = (-90 + i * (360 / n)) * Math.PI / 180;
          const x = CX + Math.cos(ang) * R, y = CY + Math.sin(ang) * R;
          const r = 5 + (o.arrivals / maxArr) * 8;
          const selected = selectedOrigin === o.icao;
          const dim = selectedOrigin && !selected;
          return (
            <g key={o.icao} role="button" tabIndex={0}
              aria-pressed={selected}
              aria-label={`${o.icao} ${o.label}, ${o.arrivals} arrivals`}
              style={{ cursor: "pointer" }}
              onClick={() => toggle(o.icao)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(o.icao); } }}>
              <circle cx={x} cy={y} r={selected ? r + 2 : r} fill="#eb6834"
                fillOpacity={dim ? 0.2 : 0.9} stroke="var(--surface)" strokeWidth={1.5} />
              <text x={x} y={y - r - 4} textAnchor="middle" fontSize={8} fill="var(--ink2)">{o.icao}</text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}
