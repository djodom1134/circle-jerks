import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  Tooltip, Legend, ResponsiveContainer,
  type TooltipValueType, type TooltipPayloadEntry,
} from "recharts";
import { getVnapCompliance, setOwnerClass, type VnapComplianceResponse, type VnapAircraft, type StatsWindow } from "../lib/api";
import { sortAircraft, radarData, toCsv, OWNER_LABELS, OWNER_OPTIONS } from "../lib/vnapDashboard";
import { getVisitorId } from "../lib/visitor";

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

export default function AircraftDashboard({ icao, win }: { icao: string; win: StatsWindow }) {
  const [data, setData] = useState<VnapComplianceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState("vnap_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc"); // worst (highest VNAP violations) first
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setData(null); setError(null); setSelected(null);
    getVnapCompliance(icao, win)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load compliance."));
  }, [icao, win]);

  const reload = () => getVnapCompliance(icao, win).then(setData).catch(() => {});

  const onOwnerChange = async (icao24: string, owner_type: string) => {
    try {
      await setOwnerClass(icao24, owner_type, getVisitorId());
      await reload();
    } catch {
      /* keep prior value on failure; a toast could be added later */
    }
  };

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

  const downloadCsv = () => {
    if (!data) return;
    const csv = toCsv(sorted, data.axes);
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `vnap-${icao}-${win}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const radarTooltipFormatter = (
    value: TooltipValueType | undefined,
    name: string | number | undefined,
    item: TooltipPayloadEntry,
  ): ReactNode => {
    const row = item?.payload as { selectedNull?: boolean; averageNull?: boolean } | undefined;
    if (name === "Average") return row?.averageNull ? "no data" : (value as ReactNode);
    return row?.selectedNull ? "no data" : (value as ReactNode);
  };

  return (
    <section className="stats-card vnap-dashboard">
      <div className="vnap-dash-head">
        <h2>Aircraft VNAP compliance</h2>
        <button className="vnap-csv-btn" onClick={downloadCsv} disabled={data.aircraft.length === 0}>
          Download CSV
        </button>
      </div>
      <p className="stats-besteffort">
        One row per aircraft over the selected window. Click a row to compare it against the set
        average on the radar. VNAP score starts at 0 (clean) and climbs toward 100 as
        noise-abatement infractions are made — higher = worse.
      </p>
      {data.aircraft.length === 0 ? (
        <p className="stats-empty">No aircraft in this window.</p>
      ) : (
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
                  <td onClick={(e) => e.stopPropagation()}>
                    <select className="vnap-owner-select" value={a.owner_class}
                            onChange={(e) => onOwnerChange(a.icao24, e.target.value)}>
                      {OWNER_OPTIONS.map((o) => (
                        <option key={o} value={o}>{OWNER_LABELS[o] ?? o}</option>
                      ))}
                    </select>
                    {a.owner_source === "community" ? " ✓" : ""}
                  </td>
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
              <Tooltip formatter={radarTooltipFormatter} /><Legend />
            </RadarChart>
          </ResponsiveContainer>
          <p className="stats-besteffort">
            Larger web = more infractions (0–100). A clean aircraft (and axes with no data) sit at the center.
          </p>
        </div>
      </div>
      )}
    </section>
  );
}
