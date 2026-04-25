import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { AlertTriangle, CheckCircle2, Clock3, CloudOff, Copy, ExternalLink, Github, History, LocateFixed, Search, SlidersHorizontal } from "lucide-react";
import AboutPage from "./AboutPage";
import AdminDashboard from "./AdminDashboard";
import MapView from "./components/MapView";
import buyMeCoffeeQrUrl from "./assets/buy-me-a-coffee-qr.png";
import logoUrl from "./assets/circle-jerks-logo.png";
import {
  aircraftDetail,
  ApiError,
  complaintSummary,
  complaintForm,
  geocode,
  getConfig,
  nearestAirport,
  recordHeartbeat,
  recordSubmission,
  scan,
  searchAirports,
  type Airport,
  type ComplaintResponse,
  type ConfigResponse,
  type MessagePreferences,
  type Offender,
  type ScanParams,
  type ScanResponse,
  type ToneSliders,
  type WindowCode
} from "./lib/api";
import { formatLocalTime, numberOrDash, titleize } from "./lib/format";
import {
  readPreferences,
  writePreferences,
  type ComplaintMode,
  type StoredPreferences
} from "./lib/preferences";
import { getVisitorId } from "./lib/visitor";

const DEFAULT_LOCATION = { lat: 40.1672, lon: -105.1019 };
const APP_TITLE = "Automated Noise Complaint Generator";
const APP_TAGLINE = "Small engines, big egos. The 0.0001% who control the sky and cause 80% of the noise pollution.";
const FAA_ANCIR_URL = "https://ancir.faa.gov/ancir?id=ancir_sc_cat_item&sys_id=6149ade187a1f550b0d987b9cebb357e";
const BUY_ME_COFFEE_URL = "https://buymeacoffee.com/djodom";
const GITHUB_ISSUES_URL = "https://github.com/djodom1134/circle-jerks/issues";
const WINDOWS: Array<{ code: WindowCode; label: string }> = [
  { code: "5m", label: "5 min" },
  { code: "30m", label: "30 min" },
  { code: "1h", label: "1 hour" },
  { code: "6h", label: "6 hours" },
  { code: "today", label: "Today" }
];

function windowFromQuery(): WindowCode {
  const value = new URLSearchParams(window.location.search).get("window");
  if (WINDOWS.some((item) => item.code === value)) return value as WindowCode;
  const stored = readPreferences().window;
  return stored && WINDOWS.some((item) => item.code === stored) ? stored : "1h";
}

function storedLocation(preferences: StoredPreferences) {
  if (preferences.user_lat === undefined || preferences.user_lon === undefined) return DEFAULT_LOCATION;
  return { lat: preferences.user_lat, lon: preferences.user_lon };
}

export default function App() {
  if (window.location.pathname.startsWith("/admin")) return <AdminDashboard />;
  if (window.location.pathname.startsWith("/about")) return <AboutPage />;

  const [preferences, setPreferences] = useState<StoredPreferences>(() => readPreferences());
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [airport, setAirport] = useState<Airport | null>(null);
  const [userLocation, setUserLocation] = useState(() => storedLocation(preferences));
  const [windowCode, setWindowCode] = useState<WindowCode>(windowFromQuery());
  const [scanData, setScanData] = useState<ScanResponse | null>(null);
  const [selected, setSelected] = useState<Offender | null>(null);
  const [formUrl, setFormUrl] = useState<string | null>(null);
  const [status, setStatus] = useState("Loading configuration");
  const [airportQuery, setAirportQuery] = useState("");
  const [airportResults, setAirportResults] = useState<Airport[]>([]);
  const [addressQuery, setAddressQuery] = useState("");

  useEffect(() => {
    getConfig()
      .then(async (result) => {
        setConfig(result);
        const found = await searchAirports(preferences.airport_icao ?? result.default_airport_icao);
        setAirport(found.airports[0] ?? null);
        setStatus("Ready");
      })
      .catch((error) => setStatus(`Configuration failed: ${error.message}`));
  }, [preferences.airport_icao]);

  useEffect(() => {
    if (!navigator.geolocation || preferences.user_lat !== undefined || preferences.user_lon !== undefined) return;
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const loc = { lat: position.coords.latitude, lon: position.coords.longitude };
        setUserLocation(loc);
        try {
          const nearest = await nearestAirport(loc.lat, loc.lon);
          setAirport(nearest);
        } catch {
          // Keep seeded default airport.
        }
      },
      () => undefined,
      { maximumAge: 300000, timeout: 6000 }
    );
  }, [preferences.user_lat, preferences.user_lon]);

  useEffect(() => {
    setPreferences((current) => ({
      ...current,
      airport_icao: airport?.icao ?? current.airport_icao,
      user_lat: userLocation.lat,
      user_lon: userLocation.lon,
      window: windowCode
    }));
  }, [airport?.icao, userLocation.lat, userLocation.lon, windowCode]);

  useEffect(() => {
    writePreferences(preferences);
  }, [preferences]);

  const scanParams = useMemo<ScanParams | null>(() => {
    if (!airport) return null;
    return {
      airport_icao: airport.icao,
      user_lat: userLocation.lat,
      user_lon: userLocation.lon,
      ring_nm: 8,
      pass_radius_nm: 0.5,
      pass_ceiling_ft: 5000,
      window: windowCode
    };
  }, [airport, userLocation, windowCode]);

  const refreshScan = useCallback(async () => {
    if (!scanParams) return;
    setStatus("Scanning current aircraft buffer");
    try {
      const result = await scan(scanParams);
      setScanData(result);
      setSelected((current) => {
        if (!current) return result.offenders[0] ?? null;
        return result.offenders.find((row) => row.icao24 === current.icao24) ?? result.offenders[0] ?? null;
      });
      const form = await complaintForm(scanParams.airport_icao);
      setFormUrl(form.form.form_url ?? null);
      setStatus(`Updated ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true })}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Scan failed");
    }
  }, [scanParams]);

  useEffect(() => {
    refreshScan();
    const id = window.setInterval(refreshScan, 5000);
    return () => window.clearInterval(id);
  }, [refreshScan]);

  useEffect(() => {
    if (!scanParams) return;
    const sendHeartbeat = () => {
      void recordHeartbeat({
        visitor_id: getVisitorId(),
        airport_icao: scanParams.airport_icao,
        user_lat: scanParams.user_lat,
        user_lon: scanParams.user_lon,
        path: window.location.pathname
      }).catch(() => undefined);
    };
    sendHeartbeat();
    const id = window.setInterval(sendHeartbeat, 30000);
    return () => window.clearInterval(id);
  }, [scanParams]);

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("window", windowCode);
    window.history.replaceState(null, "", url.toString());
  }, [windowCode]);

  async function handleAirportSearch() {
    if (!airportQuery.trim()) return;
    const result = await searchAirports(airportQuery.trim());
    setAirportResults(result.airports);
  }

  async function handleAddressSearch() {
    if (!addressQuery.trim()) return;
    const result = await geocode(addressQuery.trim());
    const first = result.features[0];
    if (!first) return;
    const [lon, lat] = first.center;
    setUserLocation({ lat, lon });
    const nearest = await nearestAirport(lat, lon);
    setAirport(nearest);
  }

  const activeNow = scanData?.counters.offenders_active_now ?? 0;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <a className="brand-logo-link" href="/about" aria-label="About Circle Jerks">
            <img className="brand-logo" src={logoUrl} alt="Circle Jerks" />
          </a>
          <div className="brand-copy">
            <div className="eyebrow">{airport ? `${airport.city} | Airport: ${airport.icao}` : "Select airport"}</div>
            <h1>{APP_TITLE}</h1>
            <p className="tagline">{APP_TAGLINE}</p>
          </div>
        </div>
        <div className="topbar-actions">
          <BackfillStatusIcon backfill={scanData?.historical_backfill} />
          <div className="live-pill">
            <span aria-hidden="true" />
            live · {activeNow} circling now
          </div>
          <div className="status">{status}</div>
        </div>
      </header>

      <section className="toolbar">
        <div className="control-group">
          <label>Time window</label>
          <div className="segmented">
            {WINDOWS.map((item) => (
              <button
                key={item.code}
                className={windowCode === item.code ? "active" : ""}
                onClick={() => setWindowCode(item.code)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <div className="control-group search-control">
          <label>Airport</label>
          <div className="input-row">
            <input value={airportQuery} onChange={(event) => setAirportQuery(event.target.value)} placeholder="ICAO, IATA, or name" />
            <button className="icon-button" onClick={handleAirportSearch} title="Search airports"><Search size={18} /></button>
          </div>
          {airportResults.length > 0 && (
            <div className="result-menu">
              {airportResults.map((candidate) => (
                <button key={candidate.icao} onClick={() => { setAirport(candidate); setAirportResults([]); }}>
                  <strong>{candidate.icao}</strong> {candidate.name}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="control-group search-control">
          <label>User location</label>
          <div className="input-row">
            <input value={addressQuery} onChange={(event) => setAddressQuery(event.target.value)} placeholder="Address or neighborhood" />
            <button className="icon-button" onClick={handleAddressSearch} title="Find address"><LocateFixed size={18} /></button>
          </div>
        </div>
      </section>

      <main className="main-grid">
        <section className="map-panel">
          <MapView
            airport={airport}
            userLocation={userLocation}
            scanData={scanData}
            selectedIcao24={selected?.icao24}
            onPickLocation={(lat, lon) => setUserLocation({ lat, lon })}
          />
        </section>

        <section className="side-panel">
          <div className="report-card">
            <div className="report-card-intro">
              <div className="report-card-copy">
                <div className="eyebrow">A plane over your house?</div>
                <h2>Log the circler.</h2>
                <p>Select an offender, tune the complaint, and send it to the airport authority.</p>
              </div>
              <div className="report-card-actions">
                <a
                  className="issue-action"
                  href={GITHUB_ISSUES_URL}
                  target="_blank"
                  rel="noreferrer"
                  title="Report an issue"
                  aria-label="Report an issue"
                >
                  <Github size={18} aria-hidden="true" />
                </a>
                <a
                  className="coffee-qr-action"
                  href={config?.buy_me_coffee_url || BUY_ME_COFFEE_URL}
                  target="_blank"
                  rel="noreferrer"
                  title="Buy me a coffee"
                  aria-label="Buy me a coffee"
                >
                  <img src={buyMeCoffeeQrUrl} alt="" />
                </a>
              </div>
            </div>
            <button
              className="primary-action"
              onClick={() => document.getElementById("complaint-panel")?.scrollIntoView({ behavior: "smooth", block: "start" })}
            >
              Prepare complaint
            </button>
          </div>
          <Counters data={scanData} />
          <OffenderTable
            offenders={scanData?.offenders ?? []}
            selected={selected}
            reportCounts={preferences.report_counts}
            onSelect={setSelected}
          />
          <Histogram rows={scanData?.histogram ?? []} />
        </section>
      </main>

      <DetailPanel
        offender={selected}
        offenders={scanData?.offenders ?? []}
        scanParams={scanParams}
        config={config}
        formUrl={formUrl}
        preferences={preferences}
        onPreferencesChange={setPreferences}
      />
    </div>
  );
}

function backfillStatus(backfill?: ScanResponse["historical_backfill"]) {
  if (!backfill) return null;
  const retryMinutes = backfill.retry_after_seconds ? Math.ceil(backfill.retry_after_seconds / 60) : null;
  if (backfill.enabled === false) {
    return {
      tone: "info",
      icon: History,
      message: backfill.reason ?? "Historical coverage is built from live polling for this airport."
    };
  }
  if (backfill.available === false) {
    return {
      tone: "error",
      icon: CloudOff,
      message: backfill.reason ?? "Historical coverage is unavailable."
    };
  }
  if (backfill.backing_off) {
    return {
      tone: "warning",
      icon: AlertTriangle,
      message: `OpenSky historical backfill is backing off after rate limiting${retryMinutes ? `; retry in about ${retryMinutes} min.` : "."}`
    };
  }
  if (backfill.complete_for_requested_window) {
    return {
      tone: "ok",
      icon: CheckCircle2,
      message: "Historical coverage is complete for this window."
    };
  }
  return {
    tone: "pending",
    icon: Clock3,
    message: `Historical coverage is still filling at ${backfill.resolution_seconds ?? "?"}s resolution.`
  };
}

function BackfillStatusIcon({ backfill }: { backfill?: ScanResponse["historical_backfill"] }) {
  const status = backfillStatus(backfill);
  if (!status) return null;
  const Icon = status.icon;
  return (
    <span
      className={`status-icon ${status.tone}`}
      title={status.message}
      data-tooltip={status.message}
      role="status"
      tabIndex={0}
      aria-label={status.message}
    >
      <Icon size={18} aria-hidden="true" />
    </span>
  );
}

function Counters({ data }: { data: ScanResponse | null }) {
  const counters = data?.counters;
  return (
    <div className="counter-strip">
      <div><strong>{counters?.circles ?? 0}</strong><span>circles</span></div>
      <div><strong>{counters?.touch_and_gos ?? 0}</strong><span>touch-and-gos</span></div>
      <div><strong>{counters?.passes ?? 0}</strong><span>passes over you</span></div>
      <div><strong>{counters?.offenders_active_now ?? 0}</strong><span>circling now</span></div>
      <div><strong>{data?.tracks.length ?? 0}</strong><span>tracked paths {counters?.label ?? ""}</span></div>
    </div>
  );
}

function originDisplay(row: Offender) {
  const label = row.origin_label ?? row.origin_city ?? row.origin_airport_icao ?? "unknown";
  const detail = row.origin_airport_icao && !label.includes(row.origin_airport_icao) ? row.origin_airport_icao : "";
  return { label, detail };
}

function OffenderTable({ offenders, selected, reportCounts, onSelect }: {
  offenders: Offender[];
  selected: Offender | null;
  reportCounts: Record<string, number>;
  onSelect: (offender: Offender) => void;
}) {
  return (
    <div className="panel-block">
      <div className="section-title">Worst Offenders</div>
      <div className="table">
        <div className="table-row header">
          <span>Callsign</span><span>Origin</span><span>Score</span><span>Cir</span><span>TG</span><span>Pass</span><span>Avg over you</span>
        </div>
        {offenders.length === 0 && <div className="empty-row">No events in this window yet.</div>}
        {offenders.map((row) => (
          <button
            key={row.icao24}
            className={`table-row ${selected?.icao24 === row.icao24 ? "selected" : ""}`}
            onClick={() => onSelect(row)}
          >
            <span>
              <strong>{row.callsign}</strong>
              <small>{row.icao24}{reportCounts[row.icao24] ? ` | reported ${reportCounts[row.icao24]}x` : ""}</small>
            </span>
            <span>{originDisplay(row).label}<small>{originDisplay(row).detail}</small></span>
            <span>{row.score}</span>
            <span>{row.circles}</span>
            <span>{row.touch_and_gos}</span>
            <span>{row.passes}</span>
            <span>{numberOrDash(row.avg_altitude_over_user_ft_agl, " ft")}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Histogram({ rows }: { rows: Array<Record<string, number | string>> }) {
  const max = Math.max(
    1,
    ...rows.map((row) =>
      Number(row.circle ?? 0) +
      Number(row.touch_and_go ?? 0) +
      Number(row.low_approach ?? 0) +
      Number(row.pass_over_user ?? 0)
    )
  );
  return (
    <div className="panel-block">
      <div className="section-title">Time Of Day</div>
      <div className="histogram">
        {rows.length === 0 && <div className="empty-row">No event density to show.</div>}
        {rows.map((row) => {
          const total = Number(row.circle ?? 0) + Number(row.touch_and_go ?? 0) + Number(row.low_approach ?? 0) + Number(row.pass_over_user ?? 0);
          return (
            <div className="histogram-row" key={String(row.bucket)}>
              <span>{row.bucket}</span>
              <div><i style={{ width: `${Math.max(4, (total / max) * 100)}%` }} /></div>
              <b>{total}</b>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface GeneratedComplaint {
  text: string;
  source: string;
}

function listLabel(values: string[]) {
  if (values.length <= 1) return values[0] ?? "";
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")}, and ${values[values.length - 1]}`;
}

function complaintErrorLabel(error: unknown) {
  if (error instanceof ApiError && error.status === 404) {
    return "Combined complaint endpoint was not available, so a local combined draft was generated.";
  }
  if (error instanceof Error) return error.message;
  return "Combined complaint failed";
}

function localCombinedComplaint(
  targets: Offender[],
  scanParams: Pick<ScanParams, "airport_icao" | "user_lat" | "user_lon" | "window">,
  messagePrefs: MessagePreferences,
  reportCounts: Record<string, number>
) {
  const callsigns = targets.map((target) => target.callsign || target.icao24.toUpperCase());
  const first = Math.min(...targets.map((target) => target.first_event_at).filter(Boolean));
  const last = Math.max(...targets.map((target) => target.last_event_at).filter(Boolean));
  const totalCircles = targets.reduce((sum, target) => sum + target.circles, 0);
  const totalTouchAndGos = targets.reduce((sum, target) => sum + target.touch_and_gos, 0);
  const totalLowApproaches = targets.reduce((sum, target) => sum + target.low_approaches, 0);
  const totalPasses = targets.reduce((sum, target) => sum + target.passes, 0);
  const previousReports = targets.reduce((sum, target) => sum + (reportCounts[target.icao24] ?? 0), 0);
  const avgAltitudes = targets
    .map((target) => target.avg_altitude_over_user_ft_agl)
    .filter((value): value is number => value != null);
  const minAltitudes = targets
    .map((target) => target.min_altitude_over_user_ft_agl)
    .filter((value): value is number => value != null);
  const origins = Array.from(new Set(
    targets
      .map((target) => target.origin_label)
      .filter((value): value is string => Boolean(value && value !== "unknown"))
  ));
  const parts = [
    `In the selected ${scanParams.window} window, I observed ${targets.length} aircraft near ${scanParams.airport_icao}: ${listLabel(callsigns.slice(0, 8))}.`
  ];
  if (messagePrefs.include_all_detail && Number.isFinite(first) && Number.isFinite(last)) {
    parts.push(`The relevant activity was observed from ${formatLocalTime(first)} to ${formatLocalTime(last)} local time.`);
  }
  if (origins.length > 0) {
    parts.push(`The available origin information points to ${listLabel(origins.slice(0, 4))}.`);
  }
  if (previousReports > 0) {
    const plural = previousReports === 1 ? "prior complaint" : "prior complaints";
    parts.push(`My browser records show ${previousReports} ${plural} for aircraft in this group.`);
  }
  if (messagePrefs.include_all_detail) {
    const activity = [];
    if (messagePrefs.include_circles) activity.push(`${totalCircles} total circles`);
    activity.push(`${totalTouchAndGos} touch-and-go operations`);
    activity.push(`${totalLowApproaches} low approaches`);
    activity.push(`${totalPasses} direct overflights of my location`);
    parts.push(`The combined activity included ${activity.join(", ")}.`);
  } else if (messagePrefs.include_circles) {
    parts.push(`Together, these aircraft were detected circling ${totalCircles} times.`);
  }
  if (messagePrefs.include_altitude_over_house && avgAltitudes.length > 0) {
    const avg = Math.round(avgAltitudes.reduce((sum, value) => sum + value, 0) / avgAltitudes.length);
    const lowest = minAltitudes.length > 0 ? `, with the lowest observed pass at ${Math.min(...minAltitudes)} ft AGL` : "";
    parts.push(`The average observed altitude over my location was ${avg} ft AGL${lowest}.`);
  }
  parts.push("Please review this combined aircraft activity and consider appropriate noise-abatement follow-up.");
  return parts.join(" ");
}

function localSingleComplaint(
  target: Offender,
  scanParams: Pick<ScanParams, "airport_icao" | "user_lat" | "user_lon" | "window">,
  messagePrefs: MessagePreferences,
  reportCount: number
) {
  const callsign = target.callsign || target.icao24.toUpperCase();
  const parts = [
    `In the selected ${scanParams.window} window, aircraft ${callsign} (${target.icao24}) was observed near ${scanParams.airport_icao}.`
  ];
  if (messagePrefs.include_all_detail && target.first_event_at && target.last_event_at) {
    parts.push(`The relevant activity was observed from ${formatLocalTime(target.first_event_at)} to ${formatLocalTime(target.last_event_at)} local time.`);
  }
  if (target.origin_label && target.origin_label !== "unknown") {
    parts.push(`The available origin information points to ${target.origin_label}.`);
  }
  if (reportCount > 0) {
    const plural = reportCount === 1 ? "prior complaint" : "prior complaints";
    parts.push(`My browser records show ${reportCount} ${plural} for this aircraft.`);
  }
  if (messagePrefs.include_all_detail) {
    const activity = [];
    if (messagePrefs.include_circles) activity.push(`${target.circles} circles`);
    activity.push(`${target.touch_and_gos} touch-and-go operations`);
    activity.push(`${target.low_approaches} low approaches`);
    activity.push(`${target.passes} direct overflights of my location`);
    parts.push(`The activity included ${activity.join(", ")}.`);
  } else if (messagePrefs.include_circles) {
    parts.push(`The aircraft was detected circling ${target.circles} times.`);
  }
  if (messagePrefs.include_altitude_over_house && target.avg_altitude_over_user_ft_agl != null) {
    const lowest = target.min_altitude_over_user_ft_agl != null
      ? `, with the lowest observed pass at ${target.min_altitude_over_user_ft_agl} ft AGL`
      : "";
    parts.push(`The average observed altitude over my location was ${target.avg_altitude_over_user_ft_agl} ft AGL${lowest}.`);
  }
  parts.push("Please review this activity and consider appropriate noise-abatement follow-up.");
  return parts.join(" ");
}

function DetailPanel({ offender, offenders, scanParams, config, formUrl, preferences, onPreferencesChange }: {
  offender: Offender | null;
  offenders: Offender[];
  scanParams: Pick<ScanParams, "airport_icao" | "user_lat" | "user_lon" | "window"> | null;
  config: ConfigResponse | null;
  formUrl: string | null;
  preferences: StoredPreferences;
  onPreferencesChange: Dispatch<SetStateAction<StoredPreferences>>;
}) {
  const sliders = preferences.sliders;
  const messagePrefs = preferences.message;
  const complaintMode = preferences.complaint_mode;
  const reportCounts = preferences.report_counts;
  const [complaint, setComplaint] = useState<GeneratedComplaint | null>(null);
  const [detailStatus, setDetailStatus] = useState("Select an offender");
  const targets = useMemo(() => {
    if (complaintMode === "all") return offenders;
    return offender ? [offender] : [];
  }, [complaintMode, offender, offenders]);
  const targetKey = targets.map((target) => [
    target.icao24,
    target.first_event_at,
    target.last_event_at,
    target.circles,
    target.touch_and_gos,
    target.low_approaches,
    target.passes,
    target.avg_altitude_over_user_ft_agl ?? "",
    target.min_altitude_over_user_ft_agl ?? "",
    target.origin_label ?? ""
  ].join(":")).join(",");
  const targetCountKey = targets.map((target) => `${target.icao24}:${reportCounts[target.icao24] ?? 0}`).join(",");

  useEffect(() => {
    if (!scanParams || targets.length === 0) {
      setComplaint(null);
      setDetailStatus(complaintMode === "all" ? "No offenders in this window" : "Select an offender");
      return;
    }
    let cancelled = false;
    setComplaint(null);
    setDetailStatus(complaintMode === "all" ? `Generating one complaint for ${targets.length} offenders` : "Generating description");
    const id = window.setTimeout(() => {
      if (complaintMode === "all") {
        complaintSummary(
          targets.map((target) => target.icao24),
          scanParams,
          sliders,
          messagePrefs,
          reportCounts
        )
          .then((result) => {
            if (cancelled) return;
            setComplaint({ text: result.text, source: result.source });
            setDetailStatus(`Generated one complaint for ${targets.length} offenders`);
          })
          .catch((error) => {
            if (cancelled) return;
            setComplaint({
              text: localCombinedComplaint(targets, scanParams, messagePrefs, reportCounts),
              source: "local fallback"
            });
            setDetailStatus(complaintErrorLabel(error));
          });
        return;
      }
      Promise.all(targets.map((target) => aircraftDetail(
        target.icao24,
        scanParams,
        sliders,
        messagePrefs,
        reportCounts[target.icao24] ?? 0
      )))
        .then((results: ComplaintResponse[]) => {
          if (cancelled) return;
          setComplaint({ text: results[0]?.text ?? "", source: results[0]?.source ?? "unknown" });
          setDetailStatus(`Description source: ${results[0]?.source ?? "unknown"}`);
        })
        .catch((error) => {
          if (cancelled) return;
          const target = targets[0];
          setComplaint({
            text: localSingleComplaint(target, scanParams, messagePrefs, reportCounts[target.icao24] ?? 0),
            source: "local fallback"
          });
          setDetailStatus(error instanceof Error ? error.message : "Description failed; generated a local draft instead.");
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
  }, [complaintMode, targetKey, targetCountKey, scanParams, sliders, messagePrefs, reportCounts]);

  function updateSlider(name: keyof ToneSliders, value: number) {
    onPreferencesChange((current) => ({
      ...current,
      sliders: { ...current.sliders, [name]: value }
    }));
  }

  function updateMessagePreference(name: keyof MessagePreferences, value: boolean) {
    onPreferencesChange((current) => ({
      ...current,
      message: { ...current.message, [name]: value }
    }));
  }

  function updateComplaintMode(mode: ComplaintMode) {
    onPreferencesChange((current) => ({ ...current, complaint_mode: mode }));
  }

  function markReported() {
    if (targets.length === 0) return;
    onPreferencesChange((current) => {
      const nextCounts = { ...current.report_counts };
      for (const target of targets) {
        nextCounts[target.icao24] = (nextCounts[target.icao24] ?? 0) + 1;
      }
      return { ...current, report_counts: nextCounts };
    });
    setDetailStatus(targets.length === 1 ? "Marked offender reported" : `Marked ${targets.length} offenders reported`);
  }

  async function copyText() {
    if (!complaint?.text) return;
    await navigator.clipboard.writeText(complaint.text);
    if (scanParams) {
      await recordSubmission({
        visitor_id: getVisitorId(),
        airport_icao: scanParams.airport_icao,
        user_lat: scanParams.user_lat,
        user_lon: scanParams.user_lon,
        window: scanParams.window,
        mode: complaintMode,
        text: complaint.text,
        targets: targets.map((target) => ({ icao24: target.icao24, callsign: target.callsign }))
      }).catch(() => undefined);
    }
    setDetailStatus("Copied description");
  }

  return (
    <section className="detail-band" id="complaint-panel">
      <div className="detail-header">
        <div>
          <div className="eyebrow">{complaintMode === "all" ? "Combined complaint text" : "Per-offender complaint text"}</div>
          <h2>
            {complaintMode === "all"
              ? `${targets.length} offenders selected`
              : offender ? `${offender.callsign} (${offender.icao24})` : "No offender selected"}
          </h2>
          {offender && (
            <p>
              {formatLocalTime(offender.first_event_at)} to {formatLocalTime(offender.last_event_at)}
              {" | "}Origin: {offender.origin_label ?? "unknown"}
              {" | "}Avg over you: {numberOrDash(offender.avg_altitude_over_user_ft_agl, " ft")}
              {" | "}Reported: {reportCounts[offender.icao24] ?? 0}x
            </p>
          )}
        </div>
        <div className="detail-actions">
          <button onClick={copyText} disabled={!complaint?.text}><Copy size={17} /> Copy</button>
          <button onClick={markReported} disabled={targets.length === 0}>
            <CheckCircle2 size={17} /> Mark reported
          </button>
          <button className="external-action" onClick={() => formUrl && window.open(formUrl, "_blank", "noopener")} disabled={!formUrl}>
            <ExternalLink size={17} /> Open complaint form
          </button>
          <button className="external-action" onClick={() => window.open(FAA_ANCIR_URL, "_blank", "noopener")}>
            <ExternalLink size={17} /> File with FAA
          </button>
        </div>
      </div>

      <div className="mode-switch" role="group" aria-label="Complaint target">
        <button className={complaintMode === "one" ? "active" : ""} onClick={() => updateComplaintMode("one")}>
          Selected offender
        </button>
        <button className={complaintMode === "all" ? "active" : ""} onClick={() => updateComplaintMode("all")} disabled={offenders.length === 0}>
          All offenders
        </button>
      </div>

      <div className="detail-grid">
        <div className="slider-panel">
          <div className="section-title"><SlidersHorizontal size={17} /> Flavor</div>
          <div className="preset-row">
            {Object.entries(config?.presets ?? {}).map(([name, values]) => (
              <button
                key={name}
                onClick={() => onPreferencesChange((current) => ({ ...current, sliders: values }))}
              >
                {titleize(name)}
              </button>
            ))}
          </div>
          {(Object.keys(sliders) as Array<keyof ToneSliders>).map((name) => (
            <label className="slider-row" key={name}>
              <span>{titleize(name)}</span>
              <input
                type="range"
                min="0"
                max="10"
                value={sliders[name]}
                onChange={(event) => updateSlider(name, Number(event.target.value))}
              />
              <b>{sliders[name]}</b>
            </label>
          ))}
          <div className="section-title message-title">Message includes</div>
          <div className="preference-grid">
            <label>
              <input
                type="checkbox"
                checked={messagePrefs.include_all_detail}
                onChange={(event) => updateMessagePreference("include_all_detail", event.target.checked)}
              />
              <span>Full detail</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={messagePrefs.include_elevation}
                onChange={(event) => updateMessagePreference("include_elevation", event.target.checked)}
              />
              <span>Elevation</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={messagePrefs.include_circles}
                onChange={(event) => updateMessagePreference("include_circles", event.target.checked)}
              />
              <span>Number of circles</span>
            </label>
            <label>
              <input
                type="checkbox"
                checked={messagePrefs.include_altitude_over_house}
                onChange={(event) => updateMessagePreference("include_altitude_over_house", event.target.checked)}
              />
              <span>Altitude over house</span>
            </label>
          </div>
        </div>

        <div className="complaint-output">
          <div className="section-title">Ready to submit complaint</div>
          <div className="output-status">{detailStatus}</div>
          <p>{complaint?.text ?? "Complaint text will appear here after an offender is selected."}</p>
        </div>
      </div>
    </section>
  );
}
