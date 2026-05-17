import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { AlertTriangle, CheckCircle2, Clock3, CloudOff, Copy, Download, ExternalLink, Github, Headphones, History, LocateFixed, Search, Share2, SlidersHorizontal, X } from "lucide-react";
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
  getAtcFeeds,
  getConfig,
  getRepeatOffenders,
  getSponsors,
  nearestAirport,
  recordHeartbeat,
  recordSubmission,
  scan,
  searchAirports,
  type Airport,
  type AtcFeedsResponse,
  type ComplaintResponse,
  type ConfigResponse,
  type MessagePreferences,
  type Offender,
  type RepeatOffender,
  type ScanParams,
  type ScanResponse,
  type SponsorsResponse,
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
const SITE_URL = "https://circlejerks.live";
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
  const [autoZoom, setAutoZoom] = useState(true);
  const [sponsors, setSponsors] = useState<SponsorsResponse | null>(null);
  const [repeatOffenders, setRepeatOffenders] = useState<RepeatOffender[]>([]);

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
    let cancelled = false;
    async function refresh() {
      try {
        const [s, r] = await Promise.all([getSponsors(), getRepeatOffenders(12)]);
        if (cancelled) return;
        setSponsors(s);
        setRepeatOffenders(r.aircraft);
      } catch {
        // silent: these are decorative sections
      }
    }
    refresh();
    const handle = window.setInterval(refresh, 5 * 60 * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, []);

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
          <div className="map-controls">
            <label className="map-toggle" title="When on, the map re-centers on activity. Uncheck to pan and zoom freely.">
              <input
                type="checkbox"
                checked={autoZoom}
                onChange={(event) => setAutoZoom(event.target.checked)}
              />
              <span>Auto-zoom</span>
            </label>
            <AtcListenButton airportIcao={airport?.icao} />
          </div>
          <MapView
            airport={airport}
            userLocation={userLocation}
            scanData={scanData}
            selectedIcao24={selected?.icao24}
            autoZoom={autoZoom}
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
        scanData={scanData}
        config={config}
        formUrl={formUrl}
        preferences={preferences}
        onPreferencesChange={setPreferences}
      />

      <RepeatOffendersSection
        offenders={repeatOffenders}
        airportIcao={airport?.icao}
        onSelect={(icao24) => {
          const match = scanData?.offenders.find((row) => row.icao24 === icao24);
          if (match) {
            setSelected(match);
            document.getElementById("complaint-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        }}
      />

      <SponsorsSection sponsors={sponsors} supportUrl={config?.buy_me_coffee_url || BUY_ME_COFFEE_URL} />
    </div>
  );
}

function RepeatOffendersSection({
  offenders,
  airportIcao,
  onSelect,
}: {
  offenders: RepeatOffender[];
  airportIcao?: string;
  onSelect: (icao24: string) => void;
}) {
  if (offenders.length === 0) {
    return (
      <section className="bottom-section repeat-offenders empty">
        <h2>Repeat offenders</h2>
        <p>
          No aircraft has been reported more than once {airportIcao ? `near ${airportIcao}` : "yet"}. As complaints
          come in, the worst repeat offenders will show up here.
        </p>
      </section>
    );
  }
  return (
    <section className="bottom-section repeat-offenders">
      <header>
        <h2>Repeat offenders</h2>
        <p>Aircraft reported by users more than once — the most-complained-about planes in the area.</p>
      </header>
      <div className="offender-grid">
        {offenders.map((row) => {
          const label =
            row.registration || row.callsign?.trim() || row.icao24.toUpperCase();
          const subtitle = [row.type_description || row.type_icao, row.operator]
            .filter(Boolean)
            .join(" · ");
          return (
            <button
              key={row.icao24}
              className="offender-card"
              onClick={() => onSelect(row.icao24)}
              title="Open complaint panel for this aircraft (if currently active)"
            >
              <div className="offender-card-head">
                <strong>{label}</strong>
                <span className="offender-count">{row.report_count}×</span>
              </div>
              {subtitle && <div className="offender-subtitle">{subtitle}</div>}
              <div className="offender-meta">
                last reported {formatLocalTime(row.last_reported_at)}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function SponsorsSection({
  sponsors,
  supportUrl,
}: {
  sponsors: SponsorsResponse | null;
  supportUrl: string;
}) {
  const supporters = sponsors?.supporters ?? [];
  return (
    <section className="bottom-section sponsors">
      <header>
        <h2>Thanks to our supporters</h2>
        <p>
          Circle Jerks is free to use. Server bills, ADS-B feeds, and AI calls are paid for by{" "}
          <a href={supportUrl} target="_blank" rel="noreferrer">
            generous folks
          </a>{" "}
          on Buy Me a Coffee.
        </p>
      </header>
      {supporters.length === 0 ? (
        <div className="sponsors-empty">
          <p>
            {sponsors?.configured
              ? "No supporters yet — be the first!"
              : "Be the first to chip in for the next month of feeds."}
          </p>
          <a className="sponsor-cta" href={supportUrl} target="_blank" rel="noreferrer">
            Buy me a coffee
          </a>
        </div>
      ) : (
        <>
          <div className="sponsor-grid">
            {supporters.slice(0, 24).map((supporter, idx) => (
              <div key={`${supporter.name}-${idx}`} className="sponsor-card">
                <div className="sponsor-name">{supporter.name}</div>
                <div className="sponsor-coffees">
                  {supporter.coffees}× {supporter.coffees === 1 ? "coffee" : "coffees"}
                </div>
                {supporter.message && <div className="sponsor-message">"{supporter.message}"</div>}
              </div>
            ))}
          </div>
          <a className="sponsor-cta" href={supportUrl} target="_blank" rel="noreferrer">
            Become a supporter
          </a>
        </>
      )}
    </section>
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

function loadCanvasImage(src: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = src;
  });
}

function drawWrappedText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
  maxLines: number
) {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  let line = "";
  let lines = 0;
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > maxWidth && line) {
      ctx.fillText(line, x, y);
      y += lineHeight;
      lines += 1;
      line = word;
      if (lines >= maxLines) {
        ctx.fillText("...", x, y);
        return y + lineHeight;
      }
    } else {
      line = next;
    }
  }
  if (line && lines < maxLines) {
    ctx.fillText(line, x, y);
    y += lineHeight;
  }
  return y;
}

function captureMapSnapshot() {
  const mapElement = document.querySelector(".openlayers-map") as HTMLElement | null;
  if (!mapElement) return null;
  const canvases = Array.from(mapElement.querySelectorAll("canvas"))
    .filter((canvas) => canvas.width > 0 && canvas.height > 0);
  if (canvases.length === 0) return null;

  const mapRect = mapElement.getBoundingClientRect();
  const output = document.createElement("canvas");
  output.width = 1000;
  output.height = 520;
  const ctx = output.getContext("2d");
  if (!ctx || mapRect.width <= 0 || mapRect.height <= 0) return null;
  ctx.fillStyle = "#e8eaed";
  ctx.fillRect(0, 0, output.width, output.height);

  try {
    for (const canvas of canvases) {
      const rect = canvas.getBoundingClientRect();
      const style = window.getComputedStyle(canvas);
      const x = ((rect.left - mapRect.left) / mapRect.width) * output.width;
      const y = ((rect.top - mapRect.top) / mapRect.height) * output.height;
      const width = (rect.width / mapRect.width) * output.width;
      const height = (rect.height / mapRect.height) * output.height;
      ctx.globalAlpha = Number(style.opacity || 1);
      ctx.drawImage(canvas, x, y, width, height);
    }
    ctx.globalAlpha = 1;
    output.toDataURL("image/png");
    return output;
  } catch {
    return null;
  }
}

function reportCountLabel(count: number) {
  return count === 1 ? "1 report" : `${count} reports`;
}

function targetDisplay(target: Offender) {
  return target.callsign || target.icao24.toUpperCase();
}

function shareStats(targets: Offender[], reportCounts: Record<string, number>) {
  return targets.map((target) => ({
    label: targetDisplay(target),
    icao24: target.icao24,
    count: (target.report_count ?? 0) + (reportCounts[target.icao24] ?? 0)
  }));
}

function defaultShareText(
  complaintText: string,
  scanData: ScanResponse | null,
  scanParams: Pick<ScanParams, "airport_icao" | "window">
) {
  const counters = scanData?.counters;
  const airportLabel = scanData?.airport
    ? `${scanData.airport.city} (${scanData.airport.icao})`
    : scanParams.airport_icao;
  const stats = counters
    ? `${counters.circles} circles, ${counters.touch_and_gos} touch-and-gos, ${counters.passes} passes over my location, and ${counters.offenders_active_now} circling now`
    : "repeated aircraft activity";
  return [
    "Share on social media!",
    "",
    `Circle Jerks tracked ${stats} near ${airportLabel} in the selected ${scanParams.window} window.`,
    "",
    complaintText,
    "",
    `See what is circling overhead: ${SITE_URL}`
  ].join("\n");
}

async function makeShareImage(input: {
  text: string;
  scanData: ScanResponse | null;
  scanParams: Pick<ScanParams, "airport_icao" | "window">;
  targetStats: Array<{ label: string; icao24: string; count: number }>;
}) {
  const canvas = document.createElement("canvas");
  canvas.width = 1080;
  canvas.height = 1350;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  const counters = input.scanData?.counters;
  const airportLabel = input.scanData?.airport
    ? `${input.scanData.airport.city} (${input.scanData.airport.icao})`
    : input.scanParams.airport_icao;
  const logo = await loadCanvasImage(logoUrl);
  const map = captureMapSnapshot();

  ctx.fillStyle = "#f6f4ef";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const gradient = ctx.createLinearGradient(0, 0, canvas.width, 360);
  gradient.addColorStop(0, "#ffffff");
  gradient.addColorStop(1, "#eef5fa");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, canvas.width, 360);

  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#e6e1d6";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.roundRect(46, 44, 988, 1260, 24);
  ctx.fill();
  ctx.stroke();

  ctx.save();
  ctx.beginPath();
  ctx.arc(134, 128, 56, 0, Math.PI * 2);
  ctx.clip();
  ctx.drawImage(logo, 78, 72, 112, 112);
  ctx.restore();

  ctx.fillStyle = "#1b3a6b";
  ctx.font = "800 42px Inter, Arial, sans-serif";
  ctx.fillText("Circle Jerks", 216, 116);
  ctx.fillStyle = "#6b7185";
  ctx.font = "700 24px Inter, Arial, sans-serif";
  ctx.fillText(SITE_URL, 216, 154);
  ctx.fillStyle = "#d64b2c";
  ctx.font = "900 32px Inter, Arial, sans-serif";
  ctx.fillText("Share on social media!", 710, 122);

  ctx.fillStyle = "#6b7185";
  ctx.font = "800 20px Inter, Arial, sans-serif";
  ctx.fillText(`${airportLabel} | ${input.scanParams.window}`, 66, 218);

  ctx.save();
  ctx.beginPath();
  ctx.roundRect(66, 244, 948, 480, 18);
  ctx.clip();
  if (map) {
    ctx.drawImage(map, 66, 244, 948, 480);
  } else {
    ctx.fillStyle = "#e8eaed";
    ctx.fillRect(66, 244, 948, 480);
    ctx.fillStyle = "#1b3a6b";
    ctx.font = "800 28px Inter, Arial, sans-serif";
    ctx.fillText("Map snapshot unavailable in this browser.", 120, 456);
    ctx.fillStyle = "#6b7185";
    ctx.font = "600 20px Inter, Arial, sans-serif";
    ctx.fillText("The live map is available at circlejerks.live.", 120, 494);
  }
  ctx.restore();

  const statRows = [
    ["Circles", counters?.circles ?? 0],
    ["T&Gs", counters?.touch_and_gos ?? 0],
    ["Passes", counters?.passes ?? 0],
    ["Circling now", counters?.offenders_active_now ?? 0]
  ];
  statRows.forEach(([label, value], index) => {
    const x = 66 + index * 237;
    ctx.fillStyle = "#fbfaf6";
    ctx.strokeStyle = "#e6e1d6";
    ctx.beginPath();
    ctx.roundRect(x, 748, 214, 118, 16);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#1b3a6b";
    ctx.font = "900 42px Inter, Arial, sans-serif";
    ctx.fillText(String(value), x + 22, 804);
    ctx.fillStyle = "#6b7185";
    ctx.font = "800 20px Inter, Arial, sans-serif";
    ctx.fillText(String(label), x + 22, 838);
  });

  ctx.fillStyle = "#1f2330";
  ctx.font = "800 24px Inter, Arial, sans-serif";
  ctx.fillText("Repeat offender reports", 66, 918);
  ctx.fillStyle = "#6b7185";
  ctx.font = "700 20px Inter, Arial, sans-serif";
  const statText = input.targetStats.length > 0
    ? input.targetStats.slice(0, 6).map((row) => `${row.label}: ${reportCountLabel(row.count)}`).join("  |  ")
    : "No repeat offender stats yet.";
  drawWrappedText(ctx, statText, 66, 952, 948, 28, 2);

  ctx.fillStyle = "#1f2330";
  ctx.font = "800 24px Inter, Arial, sans-serif";
  ctx.fillText("Description", 66, 1034);
  ctx.fillStyle = "#253044";
  ctx.font = "500 25px Inter, Arial, sans-serif";
  drawWrappedText(ctx, input.text, 66, 1074, 948, 34, 5);

  ctx.fillStyle = "#d64b2c";
  ctx.font = "900 28px Inter, Arial, sans-serif";
  ctx.fillText("Document the noise. Share the pattern. Push for change.", 66, 1258);

  return canvas.toDataURL("image/png");
}

function ShareComposerModal({
  complaintText,
  scanData,
  scanParams,
  targets,
  reportCounts,
  onClose
}: {
  complaintText: string;
  scanData: ScanResponse | null;
  scanParams: Pick<ScanParams, "airport_icao" | "window">;
  targets: Offender[];
  reportCounts: Record<string, number>;
  onClose: () => void;
}) {
  const [text, setText] = useState(() => defaultShareText(complaintText, scanData, scanParams));
  const [approved, setApproved] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [status, setStatus] = useState("Rendering share card");
  const targetStats = useMemo(() => shareStats(targets, reportCounts), [targets, reportCounts]);

  useEffect(() => {
    setText(defaultShareText(complaintText, scanData, scanParams));
    setApproved(false);
  }, [complaintText, scanData?.airport.icao, scanParams.airport_icao, scanParams.window]);

  useEffect(() => {
    let cancelled = false;
    setStatus("Rendering share card");
    makeShareImage({ text, scanData, scanParams, targetStats })
      .then((result) => {
        if (cancelled) return;
        setImageUrl(result);
        setStatus(result ? "Preview ready" : "Preview unavailable");
      })
      .catch(() => {
        if (cancelled) return;
        setImageUrl(null);
        setStatus("Preview unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [text, scanData, scanParams, targetStats]);

  async function copyShareText() {
    await navigator.clipboard.writeText(text);
    setStatus("Post text copied");
  }

  function downloadImage() {
    if (!imageUrl) return;
    const link = document.createElement("a");
    link.href = imageUrl;
    link.download = "circle-jerks-social-post.png";
    link.click();
    setStatus("Image downloaded");
  }

  async function shareViaDevice() {
    if (!navigator.share) {
      await copyShareText();
      setStatus("Web Share is unavailable; copied text instead");
      return;
    }
    if (imageUrl) {
      const blob = await fetch(imageUrl).then((response) => response.blob());
      const file = new File([blob], "circle-jerks-social-post.png", { type: "image/png" });
      if (navigator.canShare?.({ files: [file] })) {
        await navigator.share({ title: "Circle Jerks", text, url: SITE_URL, files: [file] });
        setStatus("Share sheet opened");
        return;
      }
    }
    await navigator.share({ title: "Circle Jerks", text, url: SITE_URL });
    setStatus("Share sheet opened");
  }

  return (
    <div className="share-modal-backdrop" role="dialog" aria-modal="true" aria-label="Social media share composer">
      <div className="share-modal">
        <div className="share-modal-header">
          <div>
            <div className="eyebrow">Share on social media</div>
            <h2>Preview and approve your post</h2>
          </div>
          <button className="share-close" onClick={onClose} aria-label="Close share composer"><X size={18} /></button>
        </div>

        <div className="share-modal-grid">
          <div className="share-preview">
            {imageUrl ? <img src={imageUrl} alt="Generated Circle Jerks social post preview" /> : <div>{status}</div>}
          </div>
          <div className="share-editor">
            <div className="section-title">Post text</div>
            <textarea
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                setApproved(false);
              }}
            />
            <div className="section-title offender-share-title">Repeat offender report counts</div>
            <div className="share-offender-stats">
              {targetStats.length === 0 && <span>No selected offenders.</span>}
              {targetStats.map((row) => (
                <span key={row.icao24}>
                  <strong>{row.label}</strong>
                  {reportCountLabel(row.count)}
                </span>
              ))}
            </div>
            <div className="share-status">{approved ? "Approved for posting" : status}</div>
            <div className="share-actions">
              <button onClick={() => { setApproved(true); setStatus("Approved for posting"); }}>
                <CheckCircle2 size={17} /> Approve text
              </button>
              <button onClick={copyShareText} disabled={!approved}>
                <Copy size={17} /> Copy text
              </button>
              <button onClick={downloadImage} disabled={!approved || !imageUrl}>
                <Download size={17} /> Download image
              </button>
              <button className="external-action" onClick={shareViaDevice} disabled={!approved}>
                <Share2 size={17} /> Share
              </button>
            </div>
            <p className="share-note">
              Facebook, Instagram, and Snapchat do not reliably allow web apps to prefill image posts.
              Use the copied text and downloaded image, or the device share sheet where supported.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function DetailPanel({ offender, offenders, scanParams, scanData, config, formUrl, preferences, onPreferencesChange }: {
  offender: Offender | null;
  offenders: Offender[];
  scanParams: Pick<ScanParams, "airport_icao" | "user_lat" | "user_lon" | "window"> | null;
  scanData: ScanResponse | null;
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
  const [shareOpen, setShareOpen] = useState(false);
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
          <button onClick={() => setShareOpen(true)} disabled={!complaint?.text || targets.length === 0}>
            <Share2 size={17} /> Share
          </button>
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

      {shareOpen && complaint && scanParams && (
        <ShareComposerModal
          complaintText={complaint.text}
          scanData={scanData}
          scanParams={scanParams}
          targets={targets}
          reportCounts={reportCounts}
          onClose={() => setShareOpen(false)}
        />
      )}

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
          <div className="complaint-output-header">
            <div className="section-title">Ready to submit complaint</div>
            <CopyButton text={complaint?.text} />
          </div>
          <div className="output-status">{detailStatus}</div>
          <p>{complaint?.text ?? "Complaint text will appear here after an offender is selected."}</p>
        </div>
      </div>
    </section>
  );
}

function AtcListenButton({ airportIcao }: { airportIcao?: string }) {
  const [open, setOpen] = useState(false);
  const [feeds, setFeeds] = useState<AtcFeedsResponse | null>(null);
  const [activeFeedId, setActiveFeedId] = useState<string | null>(null);
  const [feedStatus, setFeedStatus] = useState<Record<string, "idle" | "loading" | "playing" | "error">>({});
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    if (!open || !airportIcao) return;
    setFeeds(null);
    setActiveFeedId(null);
    setFeedStatus({});
    getAtcFeeds(airportIcao)
      .then(setFeeds)
      .catch(() => setFeeds({ airport_icao: airportIcao, feeds: [], external_search_url: `https://www.liveatc.net/search/?icao=${airportIcao}` }));
  }, [open, airportIcao]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const playFeed = (feedId: string, streamUrl: string) => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
    }
    const audio = new Audio(streamUrl);
    audio.preload = "none";
    audioRef.current = audio;
    setActiveFeedId(feedId);
    setFeedStatus((prev) => ({ ...prev, [feedId]: "loading" }));
    audio.addEventListener("playing", () => {
      setFeedStatus((prev) => ({ ...prev, [feedId]: "playing" }));
    });
    audio.addEventListener("error", () => {
      setFeedStatus((prev) => ({ ...prev, [feedId]: "error" }));
      setActiveFeedId((current) => (current === feedId ? null : current));
    });
    audio.play().catch(() => {
      setFeedStatus((prev) => ({ ...prev, [feedId]: "error" }));
    });
  };

  const stopFeed = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    setActiveFeedId(null);
  };

  useEffect(() => () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
    }
  }, []);

  const buttonTitle = airportIcao
    ? `Listen to ATC for ${airportIcao} on LiveATC`
    : "Pick an airport to listen to its ATC";

  return (
    <div className="atc-listen">
      <button
        type="button"
        className={`atc-listen-button${activeFeedId ? " active" : ""}`}
        disabled={!airportIcao}
        onClick={() => setOpen((value) => !value)}
        title={buttonTitle}
        aria-label={buttonTitle}
      >
        <Headphones size={16} aria-hidden="true" />
      </button>
      {open && airportIcao && (
        <div className="atc-popover" role="dialog" aria-label={`Listen to ATC for ${airportIcao}`}>
          <header>
            <div>
              <strong>Live ATC — {airportIcao}</strong>
              <p>Streamed from LiveATC. Click a feed to start; not every airport has every feed.</p>
            </div>
            <button type="button" className="atc-close" onClick={() => { stopFeed(); setOpen(false); }} aria-label="Close">
              <X size={16} aria-hidden="true" />
            </button>
          </header>
          {!feeds ? (
            <p className="atc-loading">Loading feeds…</p>
          ) : feeds.feeds.length === 0 ? (
            <p className="atc-empty">No candidate feeds for this airport.</p>
          ) : (
            <ul className="atc-feed-list">
              {feeds.feeds.map((feed) => {
                const status = feedStatus[feed.id] ?? "idle";
                const isActive = activeFeedId === feed.id;
                return (
                  <li key={feed.id} className={`atc-feed${isActive ? " active" : ""}`}>
                    <div className="atc-feed-meta">
                      <div className="atc-feed-label">{feed.label}</div>
                      <div className="atc-feed-id">{feed.id}</div>
                    </div>
                    {isActive ? (
                      <button type="button" className="atc-feed-stop" onClick={stopFeed}>
                        Stop
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="atc-feed-play"
                        onClick={() => playFeed(feed.id, feed.stream_url)}
                      >
                        {status === "loading"
                          ? "Loading…"
                          : status === "error"
                            ? "No feed"
                            : status === "playing"
                              ? "Playing"
                              : "Play"}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {feeds && (
            <a
              className="atc-external"
              href={feeds.external_search_url}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink size={12} aria-hidden="true" />
              Search all feeds on LiveATC
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function CopyButton({ text }: { text?: string | null }) {
  const [copied, setCopied] = useState(false);
  const disabled = !text || !text.trim();
  return (
    <button
      type="button"
      className={`copy-button${copied ? " copied" : ""}`}
      disabled={disabled}
      onClick={async () => {
        if (!text) return;
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1800);
        } catch {
          // ignore — older browsers without clipboard access
        }
      }}
      title="Copy complaint text"
      aria-label="Copy complaint text"
    >
      <Copy size={14} aria-hidden="true" />
      <span>{copied ? "Copied!" : "Copy"}</span>
    </button>
  );
}
