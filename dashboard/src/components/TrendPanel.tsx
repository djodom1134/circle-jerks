import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import type { DailyOperations } from "../lib/types";
import { monthlyOutOfTownShare } from "../lib/facets";
import { formatMonthLabel } from "../lib/format";

export function TrendPanel({ data, activeMonth }: { data: DailyOperations; activeMonth: string }) {
  const share = monthlyOutOfTownShare(data).slice(-12).map((m) => ({
    label: formatMonthLabel(m.month).replace(/ \d{4}$/, ""),
    month: m.month,
    pct: Math.round(m.share * 100),
  }));
  return (
    <div className="panel">
      <h6>Out-of-town share · 12 months</h6>
      <div style={{ width: "100%", height: 90 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={share} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
            <XAxis dataKey="label" tick={{ fill: "var(--ink2)", fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
            <Tooltip formatter={(value) => [`${Number(value)}%`, "Out of town"]} />
            <Bar dataKey="pct" fill="#eb6834" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="cap">{formatMonthLabel(activeMonth)} is the month in view above.</p>
    </div>
  );
}
