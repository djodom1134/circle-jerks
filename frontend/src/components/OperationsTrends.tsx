import { useEffect, useState } from "react";
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { getOperationsTrends, type OperationsTrendsResponse } from "../lib/api";

// Categorical palette (extends the stats-page navy/red).
const SERIES = ["#1b3a6b", "#b3231f", "#2e8b57", "#c77d00", "#6a4c93", "#0f766e", "#9d174d", "#374151"];
const EMITTER_LABELS: Record<string, string> = {
  A1: "Light (A1)", A2: "Small (A2)", A6: "High Perf (A6)",
  B1: "Glider (B1)", B4: "Paraglider (B4)", Other: "Other",
};

function monthLabel(m: string): string {
  const [y, mo] = m.split("-");
  return new Date(Number(y), Number(mo) - 1, 1).toLocaleString([], { month: "short", year: "2-digit" });
}

// Union of all keys present across monthly rows, so stacked areas render every series.
function keysAcross(rows: OperationsTrendsResponse["monthly"], pick: (r: OperationsTrendsResponse["monthly"][number]) => Record<string, number>): string[] {
  const s = new Set<string>();
  rows.forEach((r) => Object.keys(pick(r)).forEach((k) => s.add(k)));
  return [...s];
}

export default function OperationsTrends({ icao }: { icao: string }) {
  const [data, setData] = useState<OperationsTrendsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    getOperationsTrends(icao)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load trends."));
  }, [icao]);

  if (error) return <div className="stats-error">{error}</div>;
  if (!data) return <div className="stats-loading">Loading trends…</div>;

  const months = data.monthly.map((m) => ({ ...m, label: monthLabel(m.month) }));
  const emitterKeys = keysAcross(data.monthly, (r) => r.by_emitter);
  const typeKeys = keysAcross(data.monthly, (r) => r.by_type);
  const since = data.data_since ? new Date(data.data_since * 1000).toLocaleDateString() : null;

  // Percent-normalized rows for the stacked-area charts.
  const pct = (rows: OperationsTrendsResponse["monthly"], pick: (r: OperationsTrendsResponse["monthly"][number]) => Record<string, number>, keys: string[]) =>
    rows.map((r) => {
      const src = pick(r);
      const total = keys.reduce((a, k) => a + (src[k] || 0), 0) || 1;
      const out: Record<string, number | string> = { label: monthLabel(r.month) };
      keys.forEach((k) => (out[k] = Math.round((100 * (src[k] || 0)) / total)));
      return out;
    });

  return (
    <>
      <section className="stats-card">
        <h2>Operations trends</h2>
        <p className="stats-besteffort">
          An operation is a landing, takeoff, or touch-and-go. Circles and passes are counted
          separately above and are not included here.{since ? ` Data since ${since}.` : ""}
        </p>
      </section>

      <section className="stats-card">
        <h3>Recent operations</h3>
        {data.recent_days.length === 0 ? <p className="stats-empty">No operations yet.</p> : (
          <table className="stats-table">
            <thead><tr><th>Date</th><th># Operations</th><th>% T&amp;G</th><th>% Light (A1)</th></tr></thead>
            <tbody>
              {data.recent_days.map((d) => (
                <tr key={d.date}>
                  <td>{d.date}</td><td>{d.operations}</td>
                  <td>{d.pct_tg}%</td><td>{d.pct_light}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="stats-card">
        <h3>Landings, takeoffs &amp; touch-and-gos</h3>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={months}>
            <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
            <YAxis allowDecimals={false} fontSize={11} />
            <Tooltip /><Legend />
            <Line dataKey="landings" name="Landings" stroke={SERIES[0]} dot={false} />
            <Line dataKey="takeoffs" name="Takeoffs" stroke={SERIES[1]} dot={false} />
            <Line dataKey="tg" name="Touch & go" stroke={SERIES[2]} dot={false} />
            <Line dataKey="total" name="Total" stroke={SERIES[5]} dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section className="stats-card">
        <h3>% of operations that were touch-and-go</h3>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={months}>
            <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
            <YAxis domain={[0, 100]} fontSize={11} />
            <Tooltip />
            <Line dataKey="pct_tg" name="% T&G" stroke={SERIES[0]} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section className="stats-card">
        <h3>% of operations by aircraft type</h3>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={pct(data.monthly, (r) => r.by_type, typeKeys)}>
            <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
            <YAxis domain={[0, 100]} fontSize={11} />
            <Tooltip /><Legend />
            {typeKeys.map((k, i) => (
              <Area key={k} dataKey={k} name={k} stackId="t" stroke={SERIES[i % SERIES.length]} fill={SERIES[i % SERIES.length]} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </section>

      <section className="stats-card">
        <h3>% of operations by ADS-B emitter category</h3>
        {emitterKeys.length === 0 ? (
          <p className="stats-empty">Collecting emitter data{since ? ` since ${since}` : ""}…</p>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={pct(data.monthly, (r) => r.by_emitter, emitterKeys)}>
              <XAxis dataKey="label" fontSize={11} interval="preserveStartEnd" minTickGap={20} />
              <YAxis domain={[0, 100]} fontSize={11} />
              <Tooltip /><Legend />
              {emitterKeys.map((k, i) => (
                <Area key={k} dataKey={k} name={EMITTER_LABELS[k] || k} stackId="e" stroke={SERIES[i % SERIES.length]} fill={SERIES[i % SERIES.length]} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </section>

      <section className="stats-card">
        <h3>Operations by time of day{data.timezone ? ` (${data.timezone})` : ""}</h3>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={data.time_of_day}>
            <XAxis dataKey="hour" fontSize={11} />
            <YAxis allowDecimals={false} fontSize={11} />
            <Tooltip />
            <Bar dataKey="operations" fill={SERIES[0]} />
          </BarChart>
        </ResponsiveContainer>
      </section>
    </>
  );
}
