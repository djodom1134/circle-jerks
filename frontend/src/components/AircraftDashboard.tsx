import { useEffect, useMemo, useState } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { getVnapCompliance, type VnapComplianceResponse, type VnapAircraft, type StatsWindow } from "../lib/api";
import { sortAircraft, radarData, OWNER_LABELS } from "../lib/vnapDashboard";

const COLUMNS: { key: string; label: string; numeric: boolean }[] = [
  { key: "tail", label: "Tail", numeric: false },
  { key: "owner_class", label: "Owner", numeric: false },
  { key: "aircraft_type", label: "Type", numeric: false },
  { key: "vnap_score", label: "VNAP", numeric: true },
  { key: "reports", label: "Reports", numeric: true },
  { key: "operations", label: "Ops", numeric: true },
  { key: "touch_and_gos", label: "T&G", numeric: true },
  { key: "cowboy_count", label: "Cowboy", numeric: true },
  { key: "deviation_mean_nm", label: "Dev nm", numeric: true },
  { key: "circles", label: "Circles", numeric: true },
];

function fmt(v: number | string | null, numeric: boolean): string {
  if (v === null || v === undefined) return "—";
  if (numeric && typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

export default function AircraftDashboard({ icao, window }: { icao: string; window: StatsWindow }) {
  const [data, setData] = useState<VnapComplianceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState("vnap_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc"); // worst (lowest VNAP) first
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setData(null); setError(null); setSelected(null);
    getVnapCompliance(icao, window)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load compliance."));
  }, [icao, window]);

  const sorted = useMemo(
    () => (data ? sortAircraft(data.aircraft, sortKey, sortDir) : []),
    [data, sortKey, sortDir],
  );
  const selectedAc: VnapAircraft | null = useMemo(
    () => sorted.find((a) => a.icao24 === selected) ?? sorted[0] ?? null,
    [sorted, selected],
  );
  const chart = useMemo(
    () => (data ? radarData(data.axes, selectedAc, data.averages) : []),
    [data, selectedAc],
  );

  if (error) return <div className="stats-error">{error}</div>;
  if (!data) return <div className="stats-loading">Loading aircraft…</div>;

  const onHeader = (key: string) => {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir(key === "tail" || key === "owner_class" || key === "aircraft_type" ? "asc" : "desc"); }
  };

  return (
    <section className="stats-card vnap-dashboard">
      <h2>Aircraft VNAP compliance</h2>
      <p className="stats-besteffort">
        One row per aircraft over the selected window. Click a row to compare it against the set
        average on the radar. VNAP score is 0–100 (100 = follows the noise-abatement procedures).
      </p>
      <div className="vnap-split">
        <div className="vnap-table-wrap">
          <table className="stats-table vnap-table">
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key} className="vnap-th" onClick={() => onHeader(c.key)}>
                    {c.label}{sortKey === c.key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((a) => (
                <tr key={a.icao24}
                    className={`vnap-row${a.icao24 === selectedAc?.icao24 ? " selected" : ""}`}
                    onClick={() => setSelected(a.icao24)}>
                  <td>{a.tail}</td>
                  <td>{OWNER_LABELS[a.owner_class] ?? a.owner_class}
                      {a.owner_source === "community" ? " ✓" : ""}</td>
                  <td>{fmt(a.aircraft_type, false)}</td>
                  <td>{fmt(a.vnap_score, true)}</td>
                  <td>{a.reports}</td>
                  <td>{a.operations}</td>
                  <td>{a.touch_and_gos}</td>
                  <td>{a.cowboy_count}</td>
                  <td>{fmt(a.deviation_mean_nm, true)}</td>
                  <td>{a.circles}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="vnap-radar">
          <div className="vnap-radar-title">
            {selectedAc ? `${selectedAc.tail} vs. average` : "Select an aircraft"}
          </div>
          <ResponsiveContainer width="100%" height={320}>
            <RadarChart data={chart} outerRadius="70%">
              <PolarGrid />
              <PolarAngleAxis dataKey="label" fontSize={11} />
              <PolarRadiusAxis domain={[0, 100]} fontSize={10} />
              <Radar name="Average" dataKey="average" stroke="#8a8f98" fill="#8a8f98" fillOpacity={0.25} />
              <Radar name={selectedAc?.tail ?? "Selected"} dataKey="selected"
                     stroke="#1b3a6b" fill="#1b3a6b" fillOpacity={0.4} />
              <Tooltip /><Legend />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
}
