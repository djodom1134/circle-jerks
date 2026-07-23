import type { DailyOperationsDay } from "../lib/types";
import { kpis, type FilterState } from "../lib/facets";
import { formatInteger } from "../lib/format";

export function KpiRow({ days, filter, aircraftCount }: {
  days: DailyOperationsDay[]; filter: FilterState; aircraftCount: number;
}) {
  const k = kpis(days, filter);
  const tiles = [
    { value: formatInteger(k.operations), label: "Operations" },
    { value: `${k.outOfTownPct}%`, label: "Out of town" },
    { value: formatInteger(k.busiestDay), label: "Busiest day" },
    { value: formatInteger(aircraftCount), label: "Aircraft" },
  ];
  return (
    <div className="kpi-row">
      {tiles.map((t) => (
        <div className="kpi" key={t.label}>
          <b>{t.value}</b><span>{t.label}</span>
        </div>
      ))}
    </div>
  );
}
