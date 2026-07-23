import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchDailyOperations, fetchOrigins, fetchWorstOffenders, fetchHourlyProfile,
} from "./lib/api";
import type {
  DailyOperations, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse, OperationType, Locality,
} from "./lib/types";
import {
  monthsInCoverage, allTypes, allLocalities, type FilterState, type ColorBy,
} from "./lib/facets";
import { resolveAirport } from "./lib/airport";
import { usePrefersDark } from "./lib/palette";
import { Toolbar } from "./components/Toolbar";
import { DashboardPage } from "./components/DashboardPage";
import { LoadingState } from "./components/LoadingState";
import { ErrorState } from "./components/ErrorState";

type Load =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: DailyOperations; origins: OriginsResponse | null;
      offenders: WorstOffendersResponse | null; hourly: HourlyProfileResponse | null };

function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) {
    if (next.size === 1) return next; // guard: never empty a filter group
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export default function App() {
  const icao = useMemo(() => resolveAirport(), []);
  const dark = usePrefersDark();

  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [month, setMonth] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterState>({ types: allTypes(), localities: allLocalities() });
  const [colorBy, setColorBy] = useState<ColorBy>("locality");
  const [selectedOrigin, setSelectedOrigin] = useState<string | null>(null);
  const [originData, setOriginData] = useState<DailyOperations | null>(null);

  const loadAll = useCallback(() => {
    setLoad({ status: "loading" });
    let cancelled = false;
    // Optional endpoints (origins/offenders/hourly) are allowed to fail without
    // failing the page — they degrade to "unavailable". Only the daily payload
    // is required.
    fetchDailyOperations(icao)
      .then(async (data) => {
        const [origins, offenders, hourly] = await Promise.all([
          fetchOrigins(icao).catch(() => null),
          fetchWorstOffenders(icao).catch(() => null),
          fetchHourlyProfile(icao).catch(() => null),
        ]);
        if (cancelled) return;
        setLoad({ status: "ready", data, origins, offenders, hourly });
        const months = monthsInCoverage(data);
        setMonth((m) => m ?? months[months.length - 1] ?? data.coverage.max_day.slice(0, 7));
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ status: "error", message: err instanceof Error ? err.message : "Unknown error." });
      });
    return () => { cancelled = true; };
  }, [icao]);

  useEffect(() => loadAll(), [loadAll, attempt]);

  // Cross-filter: fetch an origin-scoped daily payload when a hub node is picked.
  useEffect(() => {
    if (load.status !== "ready") return;
    if (!selectedOrigin) { setOriginData(null); return; }
    let cancelled = false;
    fetchDailyOperations(icao, { origin: selectedOrigin })
      .then((d) => { if (!cancelled) setOriginData(d); })
      .catch(() => { if (!cancelled) setOriginData(null); });
    return () => { cancelled = true; };
  }, [icao, selectedOrigin, load.status]);

  if (load.status === "loading") return <div className="app"><LoadingState /></div>;
  if (load.status === "error") return <div className="app"><ErrorState message={load.message} onRetry={() => setAttempt((n) => n + 1)} /></div>;

  const activeData = originData ?? load.data;
  const months = monthsInCoverage(load.data);
  // months is non-empty for any ready payload, but fall back to coverage so the
  // type stays `string` without a non-null assertion.
  const activeMonth = month ?? months[months.length - 1] ?? load.data.coverage.max_day.slice(0, 7);
  const aircraftCount = load.origins ? load.origins.origins.length + load.origins.other.count : 0;
  const airportLabel = `${load.data.airport_icao}`;

  return (
    <div className="app">
      <Toolbar
        airportLabel={airportLabel}
        month={activeMonth}
        months={months}
        onMonthChange={setMonth}
        filter={filter}
        onToggleType={(t: OperationType) => setFilter((f) => ({ ...f, types: toggleInSet(f.types, t) }))}
        onToggleLocality={(l: Locality) => setFilter((f) => ({ ...f, localities: toggleInSet(f.localities, l) }))}
        colorBy={colorBy}
        onColorByChange={setColorBy}
      />
      <DashboardPage
        data={activeData}
        origins={load.origins}
        offenders={load.offenders}
        hourly={load.hourly}
        airportLabel={airportLabel}
        month={activeMonth}
        filter={filter}
        colorBy={colorBy}
        dark={dark}
        aircraftCount={aircraftCount}
        selectedOrigin={selectedOrigin}
        onSelectOrigin={setSelectedOrigin}
      />
    </div>
  );
}
