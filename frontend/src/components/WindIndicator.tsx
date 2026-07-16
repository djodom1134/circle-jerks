import { useEffect, useState } from "react";
import { getWindSummary, type WindSummary, type WindowCode } from "../lib/api";

const WINDOW_HOURS: Record<WindowCode, number> = {
  "5m": 1,
  "30m": 1,
  "1h": 1,
  "6h": 6,
  today: 24,
};

const WINDOW_LABEL: Record<WindowCode, string> = {
  "5m": "1h avg",
  "30m": "1h avg",
  "1h": "1h avg",
  "6h": "6h avg",
  today: "24h avg",
};

function compassPoint(deg: number | null): string {
  if (deg == null) return "—";
  const dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const idx = Math.round(((deg % 360) / 22.5)) % 16;
  return dirs[idx];
}

function fmtSpeed(kt: number | null): string {
  if (kt == null) return "—";
  return `${Math.round(kt)} kt`;
}

interface Props {
  airportIcao: string | null;
  windowCode: WindowCode;
}

export default function WindIndicator({ airportIcao, windowCode }: Props) {
  const [data, setData] = useState<WindSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!airportIcao) {
      setData(null);
      setError(null);
      return;
    }
    let cancelled = false;
    const hours = WINDOW_HOURS[windowCode] ?? 1;
    setError(null);
    getWindSummary(airportIcao, hours)
      .then((result) => {
        if (cancelled) return;
        setData(result);
        if (result.error) setError(result.error);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    // Refresh every 5 min (matches the backend cache TTL).
    const id = window.setInterval(() => {
      getWindSummary(airportIcao, hours)
        .then((result) => {
          if (cancelled) return;
          setData(result);
          if (result.error) setError(result.error);
        })
        .catch(() => undefined);
    }, 5 * 60 * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [airportIcao, windowCode]);

  if (!airportIcao || !data) return null;
  if (error && !data.current && !data.average) {
    // Show a tiny "no METAR" pill rather than a blank gap.
    return (
      <div className="wind-overlay wind-overlay-error" title={`METAR fetch: ${error}`}>
        Wind unavailable
      </div>
    );
  }
  const current = data.current;
  const average = data.average;
  const avgLabel = WINDOW_LABEL[windowCode] ?? "avg";

  return (
    <div className="wind-overlay" role="status" aria-label="Wind summary from METAR">
      <div className="wind-row">
        <div className="wind-row-label">Now</div>
        <WindArrow degrees={current?.wind_from_dir_degrees ?? null} />
        <div className="wind-row-detail">
          <div className="wind-row-speed">{fmtSpeed(current?.wind_speed_kt ?? null)}</div>
          <div className="wind-row-dir">
            from {current?.wind_from_dir_degrees ?? "—"}° {compassPoint(current?.wind_from_dir_degrees ?? null)}
            {current?.wind_gust_kt ? ` · gust ${Math.round(current.wind_gust_kt)} kt` : ""}
          </div>
        </div>
      </div>
      <div className="wind-row">
        <div className="wind-row-label">{avgLabel}</div>
        <WindArrow degrees={average?.wind_from_dir_degrees ?? null} muted />
        <div className="wind-row-detail">
          <div className="wind-row-speed">{fmtSpeed(average?.wind_speed_kt ?? null)}</div>
          <div className="wind-row-dir">
            from {average?.wind_from_dir_degrees ?? "—"}° {compassPoint(average?.wind_from_dir_degrees ?? null)}
            {average?.wind_gust_max_kt ? ` · gust max ${Math.round(average.wind_gust_max_kt)} kt` : ""}
          </div>
        </div>
      </div>
      <div className="wind-source" title={current?.raw_metar ?? undefined}>
        METAR · {airportIcao}
      </div>
    </div>
  );
}

function WindArrow({ degrees, muted = false }: { degrees: number | null; muted?: boolean }) {
  if (degrees == null) {
    return <div className={`wind-arrow${muted ? " muted" : ""} wind-arrow-empty`}>○</div>;
  }
  // METAR direction is FROM. Arrow points TO where the wind is going.
  const goingTo = (degrees + 180) % 360;
  return (
    <div
      className={`wind-arrow${muted ? " muted" : ""}`}
      style={{ transform: `rotate(${goingTo}deg)` }}
      aria-hidden="true"
    >
      ↑
    </div>
  );
}
