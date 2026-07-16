import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  Tooltip, ResponsiveContainer,
} from "recharts";
import type { Offender } from "../lib/api";
import { OWNER_LABELS, AXIS_LABELS } from "../lib/vnapDashboard";

const VNAP_BAD = 60;

interface Props {
  offender: Offender;
  offenders: Offender[];
  onClose: () => void;
}

function fmt(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="amc-row">
      <span className="amc-label">{label}</span>
      <span className="amc-value">{value}</span>
    </div>
  );
}

export default function AircraftMapCard({ offender, offenders, onClose }: Props) {
  const topCowboys = new Set(
    [...offenders]
      .filter((o) => (o.cowboy_count ?? 0) > 0)
      .sort((a, b) => (b.cowboy_count ?? 0) - (a.cowboy_count ?? 0))
      .slice(0, 5)
      .map((o) => o.icao24)
  );
  const isTopCowboy = topCowboys.has(offender.icao24);
  const isBadVnap = offender.vnap_score != null && offender.vnap_score >= VNAP_BAD;

  const ownerLabel = offender.owner_class
    ? (OWNER_LABELS[offender.owner_class] ?? offender.owner_class)
    : "—";
  const ownerValue = (
    <>
      {ownerLabel}
      {offender.owner_source === "community" ? " (community)" : ""}
      {offender.is_flight_school ? " ✈" : ""}
    </>
  );

  const origin = offender.origin_label ?? offender.origin_city ?? offender.origin_airport_icao;

  // The `tightness` axis is the share of pattern time spent OUTSIDE the VNAP
  // corridor — a real percentage, so render it as one rather than a bare score.
  const offPatternPct = offender.vnap_scores?.tightness;
  const offPattern = offPatternPct == null ? "—" : `${offPatternPct}%`;

  const axes = Object.keys(AXIS_LABELS);
  const chart = axes.map((a) => ({
    label: AXIS_LABELS[a],
    value: offender.vnap_scores?.[a] ?? 0,
  }));

  return (
    <div className="aircraft-map-card">
      <div className="amc-head">
        <h3>{offender.callsign} ({offender.icao24})</h3>
        <button className="amc-close" onClick={onClose} aria-label="Close aircraft detail">×</button>
      </div>

      <div className="amc-rows">
        <Row label="Owner" value={ownerValue} />
        <Row label="Aircraft type" value={fmt(offender.aircraft_type)} />
        <Row label="Origin" value={fmt(origin)} />
        <Row label="VNAP score" value={<>{fmt(offender.vnap_score)}{isBadVnap ? " 🤡" : ""}</>} />
        <Row label="Cowboy" value={<>{fmt(offender.cowboy_count)}{isTopCowboy ? " 🤠" : ""}</>} />
        <Row label="Circles" value={fmt(offender.circles)} />
        <Row label="Touch & gos" value={fmt(offender.touch_and_gos)} />
        <Row label="Low approaches" value={fmt(offender.low_approaches)} />
        <Row label="Passes" value={fmt(offender.passes)} />
        <Row label="Avg altitude over you (ft)" value={fmt(offender.avg_altitude_over_user_ft_agl)} />
        <Row label="Min altitude over you (ft)" value={fmt(offender.min_altitude_over_user_ft_agl)} />
        <Row label="Time off pattern" value={offPattern} />
        <Row label="Deviation (nm)" value={fmt(offender.deviation_mean_nm)} />
        <Row label="Times reported" value={fmt(offender.report_count)} />
      </div>

      <div className="amc-radar">
        <ResponsiveContainer width="100%" height={220}>
          <RadarChart data={chart} outerRadius="70%">
            <PolarGrid />
            <PolarAngleAxis dataKey="label" fontSize={10} />
            <PolarRadiusAxis domain={[0, 100]} fontSize={9} />
            <Radar dataKey="value" name="Infractions" stroke="#b3231f" fill="#b3231f" fillOpacity={0.35} />
            <Tooltip />
          </RadarChart>
        </ResponsiveContainer>
        <p className="amc-radar-caption">Bigger web = more infractions (0 = clean).</p>
      </div>
    </div>
  );
}
