import type { DailyOperationsDay } from "../lib/types";
import { kpis, type FilterState } from "../lib/facets";
import { formatInteger } from "../lib/format";

export function KpiRow({ days, filter, originCount }: {
  days: DailyOperationsDay[]; filter: FilterState; originCount: number;
}) {
  const k = kpis(days, filter);
  const tiles = [
    { value: formatInteger(k.operations), label: "Operations" },
    { value: `${k.outOfTownPct}%`, label: "Out of town" },
    { value: formatInteger(k.busiestDay), label: "Busiest day" },
    { value: formatInteger(originCount), label: "Origins" },
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
