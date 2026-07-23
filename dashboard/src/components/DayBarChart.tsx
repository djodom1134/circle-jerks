import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { OPERATION_TYPES, LOCALITIES, type OperationType, type Locality, type DailyOperationsDay } from "../lib/types";
import { filteredDaySeries, type FilterState, type ColorBy } from "../lib/facets";
import { localityColor, typeColor, LOCALITY_LABEL, TYPE_LABEL } from "../lib/palette";
import { formatInteger, formatShortDate } from "../lib/format";

export function DayBarChart({ days, filter, colorBy, dark }: {
  days: DailyOperationsDay[]; filter: FilterState; colorBy: ColorBy; dark: boolean;
}) {
  const series = filteredDaySeries(days, filter, colorBy);
  const keys: string[] = colorBy === "locality"
    ? LOCALITIES.filter((l) => filter.localities.has(l))
    : OPERATION_TYPES.filter((t) => filter.types.has(t));

  const colorOf = (key: string) =>
    colorBy === "locality" ? localityColor(key as Locality, dark) : typeColor(key as OperationType, dark);
  const labelOf = (key: string) =>
    colorBy === "locality" ? LOCALITY_LABEL[key as Locality] : TYPE_LABEL[key as OperationType];

  const rows = series.map((d) => ({ label: formatShortDate(d.date), ...d.segments }));
  const total = series.reduce((s, d) => s + d.total, 0);
  const peak = series.reduce((m, d) => Math.max(m, d.total), 0);

  return (
    <section className="chart-card">
      <div className="chart-title">
        <span>{series.length} days · coloured by {colorBy === "locality" ? "who flew them" : "operation type"}</span>
        <b>{formatInteger(total)} operations</b>
      </div>
      <div role="img"
        aria-label={`Daily operations across ${series.length} days: ${formatInteger(total)} total, peaking at ${formatInteger(peak)} in a single day.`}
        style={{ width: "100%", height: 300 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 10, right: 8, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--line)" />
            <XAxis dataKey="label" tick={{ fill: "var(--ink2)", fontSize: 11 }} tickLine={false} interval="preserveStartEnd" />
            <YAxis allowDecimals={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} tickLine={false} width={36} />
            <Tooltip formatter={(value, name) => [formatInteger(Number(value)), labelOf(String(name))]} />
            {keys.map((key, i) => (
              <Bar key={key} dataKey={key} name={key} stackId="ops" fill={colorOf(key)}
                radius={i === keys.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">
        {keys.map((key) => (
          <span key={key}><i style={{ background: colorOf(key) }} />{labelOf(key)}</span>
        ))}
      </div>
      <table className="visually-hidden">
        <caption>Daily operations by {colorBy}</caption>
        <thead><tr><th>Day</th>{keys.map((k) => <th key={k}>{labelOf(k)}</th>)}<th>Total</th></tr></thead>
        <tbody>
          {series.map((d) => (
            <tr key={d.date}>
              <td>{formatShortDate(d.date)}</td>
              {keys.map((k) => <td key={k}>{formatInteger(d.segments[k] ?? 0)}</td>)}
              <td>{formatInteger(d.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
