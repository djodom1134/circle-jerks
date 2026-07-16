import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from "recharts";
import { getAirportStats, type AirportStatsResponse, type StatsWindow } from "../lib/api";
import { windowFromUrl } from "../lib/statsLinks";
import OperationsTrends from "./OperationsTrends";
import AircraftDashboard from "./AircraftDashboard";

const WINDOWS: StatsWindow[] = ["1d", "7d", "30d", "all"];

function airportFromUrl(): string {
  const params = new URLSearchParams(window.location.search);
  return (params.get("airport") || "KBJC").toUpperCase();
}

export default function StatsPage() {
  const icao = airportFromUrl();
  const [win, setWin] = useState<StatsWindow>(() => windowFromUrl(window.location.search));
  const [data, setData] = useState<AirportStatsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"aircraft" | "operations">("aircraft");

  useEffect(() => {
    setData(null);
    setError(null);
    getAirportStats(icao, win)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load stats."));
  }, [icao, win]);

  return (
    <div className="stats-page">
      <header className="stats-head">
        <a className="stats-back" href="/">← Map</a>
        <h1>{icao} — airport stats</h1>
        <div className="stats-windows">
          {WINDOWS.map((w) => (
            <button key={w} className={`stats-win${w === win ? " active" : ""}`} onClick={() => setWin(w)}>{w}</button>
          ))}
        </div>
      </header>

      {error && <div className="stats-error">{error}</div>}
      {!data && !error && <div className="stats-loading">Loading…</div>}

      {data && (
        <>
          <section className="stats-tiles">
            <Tile label="Touch & gos" value={data.counters.touch_and_gos} />
            <Tile label="Circles" value={data.counters.circles} />
            <Tile label="Low approaches" value={data.counters.low_approaches} />
            <Tile label="Landings" value={data.counters.landings} />
            <Tile label="Passes" value={data.counters.passes} />
            <Tile label="Aircraft" value={data.counters.unique_aircraft} />
            <Tile label="Runway changes" value={data.counters.runway_changes} />
          </section>

          <div className="stats-tabs">
            <button className={`stats-tab${tab === "aircraft" ? " active" : ""}`} onClick={() => setTab("aircraft")}>Aircraft</button>
            <button className={`stats-tab${tab === "operations" ? " active" : ""}`} onClick={() => setTab("operations")}>Operations</button>
          </div>

          {tab === "aircraft" && (
            <div className="stats-tab-panel aircraft-panel">
              <AircraftDashboard icao={icao} win={win} />
            </div>
          )}

          {tab === "operations" && (
          <div className="stats-tab-panel ops-panel">
          <OperationsTrends icao={icao} />
          <div className="ops-grid">

          <section className="stats-card ops-grid-wide">
            <h2>Do they actually stop?</h2>
            {data.stop_classification.all.total === 0 ? (
              <p className="stats-empty">No aircraft in this window.</p>
            ) : (
              <>
                <p className="stats-hero">
                  <span className="stats-hero-pct">{data.stop_classification.all.stopped_pct}%</span>
                  <span className="stats-hero-label">of aircraft actually stopped</span>
                </p>
                <p className="stats-besteffort">
                  Counted per aircraft: of the distinct tails that came through, how many stopped (landed and
                  stayed — reached the runway and did not climb back out within 5 min, ≈ stayed ≥5 min) vs. just
                  passed through or did laps and left. A tail that landed at least once counts as stopped.
                </p>
                <table className="stats-table">
                  <thead><tr><th>Of the aircraft that came through…</th><th>Stopped</th><th>Did not stop</th><th>% stopped</th></tr></thead>
                  <tbody>
                    {([["All aircraft", data.stop_classification.all], ["Pattern-working only", data.stop_classification.pattern]] as const).map(([label, b]) => (
                      <tr key={label}>
                        <td><strong>{label}</strong> ({b.total})</td>
                        <td>{b.stopped}</td>
                        <td>{b.did_not_stop}</td>
                        <td>{b.stopped_pct === null ? "—" : `${b.stopped_pct}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.stop_over_time.length > 0 && (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={data.stop_over_time.map((d) => ({
                      t: new Date(d.day * 1000).toLocaleDateString([], { month: "numeric", day: "numeric" }),
                      "Did not stop": d.did_not_stop,
                      "Stopped": d.stopped,
                    }))}>
                      <XAxis dataKey="t" fontSize={11} interval="preserveStartEnd" minTickGap={24} />
                      <YAxis allowDecimals={false} fontSize={11} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="Did not stop" stackId="a" fill="#b3231f" />
                      <Bar dataKey="Stopped" stackId="a" fill="#1b3a6b" />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </>
            )}
          </section>

          <section className="stats-card ops-grid-wide">
            <h2>Operations over time</h2>
            {data.ops_over_time.length === 0 ? <p className="stats-empty">No operations in this window.</p> : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={data.ops_over_time.map((b) => ({
                  t: data.window.bucket_seconds < 86400
                    ? new Date(b.bucket * 1000).toLocaleString([], { weekday: "short", hour: "numeric" })
                    : new Date(b.bucket * 1000).toLocaleDateString([], { month: "numeric", day: "numeric" }),
                  count: b.count,
                }))}>
                  <XAxis dataKey="t" fontSize={11} interval="preserveStartEnd" minTickGap={24} />
                  <YAxis allowDecimals={false} fontSize={11} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#1b3a6b" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </section>

          <section className="stats-card">
            <h2>Runway changes &amp; cowboys 🤠</h2>
            <p className="stats-besteffort">A cowboy switched the active runway to a direction the wind did <em>not</em> favor ({data.counters.runway_changes} total runway change{data.counters.runway_changes === 1 ? "" : "s"} this window).</p>
            {data.cowboys.length === 0 ? <p className="stats-empty">No cowboys — every runway change followed the wind.</p> : (
              <table className="stats-table">
                <thead><tr><th>Aircraft</th><th>Cowboy changes</th></tr></thead>
                <tbody>
                  {data.cowboys.map((c) => (
                    <tr key={c.icao24}><td>{c.callsign ?? c.icao24}</td><td>{c.changes}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="stats-card">
            <h2>Wind &amp; rotation</h2>
            <p>Into headwind: <strong>{data.wind.into_headwind_ops}</strong> ops · Downwind: <strong>{data.wind.downwind_ops}</strong> ops · No data: {data.wind.no_wind_data_ops}</p>
          </section>

          <section className="stats-card">
            <h2>Runway use &amp; wind</h2>
            {(data.runway_usage ?? []).length === 0 ? <p className="stats-empty">No runway-tagged operations in this window.</p> : (
              <table className="stats-table">
                <thead><tr><th>Runway</th><th>Ops</th><th>Into wind</th><th>Crosswind</th><th>Downwind (tailwind)</th><th>No wind data</th></tr></thead>
                <tbody>
                  {(data.runway_usage ?? []).map((u) => (
                    <tr key={u.runway_id}>
                      <td><strong>{u.runway_id}</strong></td>
                      <td>{u.total}</td>
                      <td>{u.upwind}</td>
                      <td>{u.crosswind}</td>
                      <td>{u.downwind}</td>
                      <td>{u.no_wind_data}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="stats-card">
            <h2>Deviation from pattern</h2>
            {data.deviation.scored_ops === 0 ? <p className="stats-empty">No pattern drawn / no scored ops yet.</p> : (
              <>
                <p>Avg <strong>{data.deviation.avg_mean_nm} nm</strong> · max {data.deviation.max_nm} nm · total time off-pattern {Math.round(data.deviation.total_time_off_s / 60)} min ({data.deviation.scored_ops} ops)</p>
                <table className="stats-table">
                  <thead><tr><th>Worst offenders</th><th>Avg deviation (nm)</th><th>Circles</th></tr></thead>
                  <tbody>
                    {data.deviation.worst.map((w) => (
                      <tr key={w.icao24}><td>{w.callsign ?? w.icao24}</td><td>{w.deviation_mean_nm}</td><td>{w.circles}</td></tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </section>

          <section className="stats-card">
            <h2>Repeat offenders</h2>
            <table className="stats-table">
              <thead><tr><th>Aircraft</th><th>Reports</th></tr></thead>
              <tbody>
                {data.repeat_offenders.map((o) => (
                  <tr key={o.icao24}><td>{o.callsign ?? o.icao24}{o.registration ? ` (${o.registration})` : ""}</td><td>{o.report_count}</td></tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="stats-card">
            <h2>Origin / flight school <span className="stats-besteffort">(best-effort)</span></h2>
            {data.flight_schools.length === 0 ? <p className="stats-empty">No registry matches in this window.</p> : (
              <table className="stats-table">
                <thead><tr><th>Owner / operator</th><th>Aircraft</th></tr></thead>
                <tbody>
                  {data.flight_schools.map((f) => (
                    <tr key={f.label}><td>{f.label}</td><td>{f.count}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          </div>
          </div>
          )}
        </>
      )}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="stats-tile">
      <div className="stats-tile-value">{value}</div>
      <div className="stats-tile-label">{label}</div>
    </div>
  );
}
