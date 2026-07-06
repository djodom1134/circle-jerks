import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { AlertTriangle, CheckCircle2, Clock3, CloudOff, Copy, Download, ExternalLink, Flame, Github, Headphones, History, LocateFixed, MapPin, RotateCw, Route, Search, Share2, SlidersHorizontal, X } from "lucide-react";
import AboutPage from "./AboutPage";
import AdminDashboard from "./AdminDashboard";
import StatsPage from "./components/StatsPage";
import MapView from "./components/MapView";
import OnboardingTour, { shouldShowOnboarding } from "./components/OnboardingTour";
import PatternEditorPanel from "./components/PatternEditorPanel";
import WindIndicator from "./components/WindIndicator";
import BackfillBanner from "./components/BackfillBanner";
import FlowBadge from "./components/FlowBadge";
import buyMeCoffeeQrUrl from "./assets/buy-me-a-coffee-qr.png";
import logoUrl from "./assets/circle-jerks-logo.png";
import {
  aircraftDetail,
  ApiError,
  complaintSummary,
  complaintForm,
  geocode,
  reverseGeocode,
  getAirportPatterns,
  getAirportSosaUrl,
  getAtcFeeds,
  getConfig,
  getLiveStatus,
  getRepeatOffenders,
  getSponsors,
  getTrackHistory,
  nearestAirport,
  recordHeartbeat,
  recordSubmission,
  scan,
  searchAirports,
  type Airport,
  type AtcFeedsResponse,
  type ComplaintResponse,
  type ConfigResponse,
  type LiveStatusResponse,
  type MessagePreferences,
  type Offender,
  type PatternPoint,
  type RepeatOffender,
  type RunwayPattern,
  type ScanParams,
  type ScanResponse,
  type SponsorsResponse,
  type ToneSliders,
  type TrackHistoryResponse,
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
  return stored && WINDOWS.some((item) => item.code === stored) ? stored : "today";
}

function storedLocation(preferences: StoredPreferences) {
  if (preferences.user_lat === undefined || preferences.user_lon === undefined) return DEFAULT_LOCATION;
  return { lat: preferences.user_lat, lon: preferences.user_lon };
}

export default function App() {
  if (window.location.pathname.startsWith("/admin")) return <AdminDashboard />;
  if (window.location.pathname.startsWith("/about")) return <AboutPage />;
  if (window.location.pathname.startsWith("/stats")) return <StatsPage />;

  const [preferences, setPreferences] = useState<StoredPreferences>(() => readPreferences());
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [airport, setAirport] = useState<Airport | null>(null);
  const [userLocation, setUserLocation] = useState(() => storedLocation(preferences));
  const [windowCode, setWindowCode] = useState<WindowCode>(windowFromQuery());
  const [scanData, setScanData] = useState<ScanResponse | null>(null);
  const [selected, setSelected] = useState<Offender | null>(null);
  const [formUrl, setFormUrl] = useState<string | null>(null);
  const [status, setStatus] = useState("Loading configuration");
  const [airportQuery, setAirportQuery] = useState(
    () => preferences.airport_query ?? preferences.airport_icao ?? ""
  );
  const [airportResults, setAirportResults] = useState<Airport[]>([]);
  const [addressQuery, setAddressQuery] = useState(() => preferences.user_address ?? "");
  const [autoZoom, setAutoZoom] = useState(true);
  const [mapOverlay, setMapOverlay] = useState<"none" | "noise" | "history">("none");
  const [historyDays, setHistoryDays] = useState(3);
  const [historyMode, setHistoryMode] = useState<"lines" | "density" | "average">("lines");
  const [historyData, setHistoryData] = useState<TrackHistoryResponse | null>(null);
  const showHeatmap = mapOverlay === "noise";
  const [showOnboarding, setShowOnboarding] = useState(() => shouldShowOnboarding());
  const [sponsors, setSponsors] = useState<SponsorsResponse | null>(null);
  const [repeatOffenders, setRepeatOffenders] = useState<RepeatOffender[]>([]);
  const [liveStatus, setLiveStatus] = useState<LiveStatusResponse | null>(null);
  const [sosaUrl, setSosaUrl] = useState<string>("https://www.saveourskiesalliance.org/");
  const [patternEditing, setPatternEditing] = useState(false);
  const [editingRunwayId, setEditingRunwayId] = useState<string | null>(null);
  const [editingPoints, setEditingPoints] = useState<PatternPoint[]>([]);
  const [editSeedKey, setEditSeedKey] = useState(0);
  const [patterns, setPatterns] = useState<RunwayPattern[]>([]);

  function seedEditingPoints(points: PatternPoint[]) {
    setEditingPoints(points);
    setEditSeedKey((k) => k + 1);
  }

  function reloadPatterns() {
    if (!airport?.icao) return;
    getAirportPatterns(airport.icao).then((r) => setPatterns(r.patterns)).catch(() => setPatterns([]));
  }

  useEffect(() => { reloadPatterns(); /* eslint-disable-next-line */ }, [airport?.icao]);

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch((error) => setStatus(`Configuration failed: ${error.message}`));
  }, []);

  // Suppress browser-level zoom (Ctrl/Cmd + wheel, Ctrl/Cmd + +/-/0, pinch
  // trackpad). The map has its own zoom controls; page zoom just breaks the
  // grid layout and confuses users.
  useEffect(() => {
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault();
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (["+", "-", "=", "_", "0"].includes(event.key)) {
        event.preventDefault();
      }
    };
    window.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    nearestAirport(userLocation.lat, userLocation.lon)
      .then((nearest) => {
        if (cancelled) return;
        setAirport(nearest);
        setStatus("Ready");
      })
      .catch(() => {
        if (cancelled || airport) return;
        const fallback = config?.default_airport_icao;
        if (!fallback) return;
        searchAirports(fallback)
          .then((res) => {
            if (cancelled) return;
            setAirport(res.airports[0] ?? null);
          })
          .catch(() => undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [userLocation.lat, userLocation.lon, config?.default_airport_icao]);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const [s, r, ls] = await Promise.all([getSponsors(), getRepeatOffenders(10), getLiveStatus()]);
        if (cancelled) return;
        setSponsors(s);
        setRepeatOffenders(r.aircraft);
        setLiveStatus(ls);
      } catch {
        // silent: these are decorative sections
      }
    }
    refresh();
    const sectionsHandle = window.setInterval(refresh, 5 * 60 * 1000);
    // Live status polls more frequently so the topbar reflects real state.
    const statusHandle = window.setInterval(() => {
      getLiveStatus()
        .then((ls) => {
          if (!cancelled) setLiveStatus(ls);
        })
        .catch(() => undefined);
    }, 30 * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(sectionsHandle);
      window.clearInterval(statusHandle);
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

  // Reflect detected/stored/recomputed airport in the input box. Updates
  // unconditionally on every airport change — picking a new home location
  // (geocode, right-click, or geolocation) recomputes the nearest airport,
  // and the input must follow. If the user is in the middle of typing an
  // airport search, the dropdown of results still appears below, and they
  // can click a result to override.
  useEffect(() => {
    if (!airport) return;
    setAirportQuery(airport.icao);
  }, [airport?.icao]);

  // Look up the Save Our Skies Alliance page for the current airport so the
  // "Volunteer / get involved" CTA points to the right airport-specific page.
  // Falls back to the SOSA home when no per-airport page exists yet.
  useEffect(() => {
    if (!airport?.icao) return;
    let cancelled = false;
    getAirportSosaUrl(airport.icao)
      .then((result) => {
        if (!cancelled && result?.url) setSosaUrl(result.url);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [airport?.icao]);

  // Reverse-geocode the user's coords to fill the location input on first
  // detection. Skip if the user already typed an address or we have one stored.
  useEffect(() => {
    if (addressQuery.trim() !== "") return;
    if (!Number.isFinite(userLocation.lat) || !Number.isFinite(userLocation.lon)) return;
    let cancelled = false;
    reverseGeocode(userLocation.lat, userLocation.lon)
      .then((result) => {
        if (cancelled) return;
        const label = result.short_name ?? result.display_name ?? "";
        if (label) {
          setAddressQuery((current) => (current.trim() === "" ? label : current));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [userLocation.lat, userLocation.lon]);

  // Persist whatever the user (or auto-fill) put into the inputs so we can
  // restore on the next visit.
  useEffect(() => {
    setPreferences((current) => {
      if ((current.airport_query ?? "") === airportQuery) return current;
      return { ...current, airport_query: airportQuery };
    });
  }, [airportQuery]);

  useEffect(() => {
    setPreferences((current) => {
      if ((current.user_address ?? "") === addressQuery) return current;
      return { ...current, user_address: addressQuery };
    });
  }, [addressQuery]);

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
    if (mapOverlay !== "history" || !airport?.icao) {
      setHistoryData(null);
      return;
    }
    let cancelled = false;
    getTrackHistory(airport.icao, historyDays)
      .then((data) => { if (!cancelled) setHistoryData(data); })
      .catch(() => { if (!cancelled) setHistoryData(null); });
    return () => { cancelled = true; };
  }, [mapOverlay, airport?.icao, historyDays]);

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
          <FeedStatusIcon liveStatus={liveStatus} />
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
            <a className="icon-button" href={`/stats?airport=${encodeURIComponent(airport?.icao ?? "KBJC")}`} title="Airport history & stats" aria-label="Airport history & stats"><History size={18} /></a>
          </div>
          {airportResults.length > 0 && (
            <div className="result-menu">
              {airportResults.map((candidate) => (
                <button key={candidate.icao} onClick={() => { setAirport(candidate); setAirportQuery(candidate.icao); setAirportResults([]); }}>
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
            <button
              type="button"
              className={`map-icon-toggle${showHeatmap ? " active" : ""}`}
              onClick={() => setMapOverlay((o) => (o === "noise" ? "none" : "noise"))}
              title={showHeatmap ? "Showing noise-intensity heatmap. Click to return to individual tracks." : "Show a noise-intensity heatmap (lower aircraft = brighter)."}
              aria-label="Toggle noise heatmap"
              aria-pressed={showHeatmap}
            >
              <Flame size={16} aria-hidden="true" />
            </button>
            <button
              type="button"
              className={`map-icon-toggle${mapOverlay === "history" ? " active" : ""}`}
              onClick={() => setMapOverlay((o) => (o === "history" ? "none" : "history"))}
              title={mapOverlay === "history" ? "Showing historical track density. Click to hide." : "Show 1–7 days of historical flight paths."}
              aria-label="Toggle historical track density"
              aria-pressed={mapOverlay === "history"}
            >
              <Route size={16} aria-hidden="true" />
            </button>
            <button
              className={`map-icon-toggle${patternEditing ? " active" : ""}`}
              title="Edit VNAP patterns"
              aria-label="Edit VNAP patterns"
              onClick={() => { setPatternEditing((v) => !v); if (patternEditing) { setEditingRunwayId(null); setEditingPoints([]); } }}
              disabled={!airport?.icao}
            >
              <MapPin size={16} />
            </button>
            <AtcListenButton airportIcao={airport?.icao} />
          </div>
          {mapOverlay === "history" && (
            <div className="history-controls" role="group" aria-label="Historical track density controls">
              <div className="history-controls-row">
                <span className="history-controls-label">Days</span>
                <div className="segmented history-days">
                  {[1, 2, 3, 4, 5, 6, 7].map((d) => (
                    <button
                      key={d}
                      className={historyDays === d ? "active" : ""}
                      onClick={() => setHistoryDays(d)}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </div>
              <div className="history-controls-row">
                <span className="history-controls-label">View</span>
                <div className="segmented history-mode">
                  {([["lines", "Lines"], ["density", "Density"], ["average", "Average"]] as const).map(([mode, label]) => (
                    <button
                      key={mode}
                      className={historyMode === mode ? "active" : ""}
                      onClick={() => setHistoryMode(mode)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="history-controls-caption">
                {historyData
                  ? historyData.truncated
                    ? `Showing ${historyData.tracks.length.toLocaleString()} of ${historyData.total_tracks.toLocaleString()} flights`
                    : `${historyData.tracks.length.toLocaleString()} flights`
                  : "Loading history…"}
              </div>
            </div>
          )}
          <WindIndicator airportIcao={airport?.icao ?? null} windowCode={windowCode} />
          <BackfillBanner
            airportIcao={airport?.icao ?? null}
            windowCode={windowCode}
            onShortenWindow={() => setWindowCode("1h")}
          />
          <MapView
            airport={airport}
            userLocation={userLocation}
            scanData={scanData}
            selectedIcao24={selected?.icao24}
            autoZoom={autoZoom}
            showHeatmap={showHeatmap}
            historyTracks={mapOverlay === "history" ? historyData?.tracks ?? null : null}
            historyMode={mapOverlay === "history" ? historyMode : null}
            onPickLocation={(lat, lon) => setUserLocation({ lat, lon })}
            patterns={patterns}
            editingRunwayId={patternEditing ? editingRunwayId : null}
            editingPoints={editingPoints}
            editingClosed={true}
            editSeedKey={editSeedKey}
            onEditingPointsChange={setEditingPoints}
          />
          {patternEditing && airport?.icao && (
            <PatternEditorPanel
              airportIcao={airport.icao}
              runwayId={editingRunwayId}
              points={editingPoints}
              onSelectRunway={(rwy) => { setEditingRunwayId(rwy); setEditingPoints([]); setEditSeedKey((k) => k + 1); }}
              onSeedPoints={seedEditingPoints}
              onClose={() => { setPatternEditing(false); setEditingRunwayId(null); setEditingPoints([]); }}
              onSaved={reloadPatterns}
            />
          )}
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
          <Counters data={scanData} status={status} onRefresh={refreshScan} airportIcao={airport?.icao} />
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

      <MovementSection
        supportUrl={config?.buy_me_coffee_url || BUY_ME_COFFEE_URL}
        volunteerUrl={sosaUrl}
        airportLabel={airport ? `${airport.name}${airport.iata ? ` (${airport.iata})` : ""}` : null}
      />

      <SponsorsSection sponsors={sponsors} supportUrl={config?.buy_me_coffee_url || BUY_ME_COFFEE_URL} />

      {showOnboarding && <OnboardingTour onClose={() => setShowOnboarding(false)} />}
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
        <h2>Worst offenders</h2>
        <p>
          No aircraft has been reported more than once {airportIcao ? `near ${airportIcao}` : "yet"}. As complaints
          come in, the worst offenders will show up here, ranked by total circles flown.
        </p>
      </section>
    );
  }
  return (
    <section className="bottom-section repeat-offenders">
      <header>
        <h2>Worst offenders</h2>
        <p>Ranked by total circles flown — the biggest jerks in the area.</p>
      </header>
      <ol className="offender-leaderboard">
        {offenders.map((row, index) => {
          const label =
            row.registration || row.callsign?.trim() || row.icao24.toUpperCase();
          const subtitle = [row.type_description || row.type_icao, row.operator]
            .filter(Boolean)
            .join(" · ");
          const origin = row.origin_label || row.origin_airport_icao;
          return (
            <li key={row.icao24}>
              <button
                className="offender-card"
                onClick={() => onSelect(row.icao24)}
                title="Open complaint panel for this aircraft (if currently active)"
              >
                <div className="offender-rank" aria-hidden="true">#{index + 1}</div>
                <div className="offender-body">
                  <div className="offender-card-head">
                    <strong>{label}</strong>
                    <span className="offender-circles" title="Total circles flown — the biggest jerk signal">
                      {row.total_circles}× circles
                    </span>
                  </div>
                  {subtitle && <div className="offender-subtitle">{subtitle}</div>}
                  {origin && (
                    <div className="offender-origin">
                      Based at <strong>{origin}</strong>
                    </div>
                  )}
                  <div className="offender-stats">
                    <span title="Low approaches">{row.total_low_approaches} low</span>
                    <span title="Passes over reporters' houses">{row.total_passes_over_user} over homes</span>
                    <span title="Touch-and-gos">{row.total_touch_and_gos} t&amp;g</span>
                    <span title="Times reported by neighbors">{row.report_count}× reported</span>
                  </div>
                  <div className="offender-meta">
                    last reported {formatLocalTime(row.last_reported_at)}
                  </div>
                </div>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function MovementSection({
  supportUrl,
  volunteerUrl,
  airportLabel,
}: {
  supportUrl: string;
  volunteerUrl: string;
  airportLabel: string | null;
}) {
  const isAirportSpecific = volunteerUrl !== "https://www.saveourskiesalliance.org/";
  return (
    <section className="bottom-section movement">
      <header>
        <h2>Take back the skies.</h2>
        <p>
          A handful of pilots and special-interest lobbyists treat the airspace
          over your house as their personal practice yard. We're a grass-roots
          effort to balance the scales — and to remind the people in the sky
          that the rest of us live, sleep, and raise kids underneath it.
        </p>
      </header>

      <div className="movement-pillars">
        <article className="movement-card">
          <h3>Educate the cockpit</h3>
          <p>
            Donations fund outreach to flight schools and student pilots:
            noise-abatement procedures, safer departure tracks over populated
            areas, and the basic civic awareness that "legal" is not the same
            as "neighborly."
          </p>
        </article>

        <article className="movement-card">
          <h3>Speak louder than the lobby</h3>
          <p>
            AOPA and friends are organized, funded, and loud. We're catching up.
            Every complaint filed, every neighbor signed up, every dollar in the
            jar makes it harder for our elected officials to keep pretending
            this isn't a public-health issue.
          </p>
        </article>

        <article className="movement-card">
          <h3>Volunteer</h3>
          <p>
            We need analysts, designers, organizers, lawyers, and locals in
            every airport community. If you can spare an evening — or a
            lifetime — we'd love your help.
          </p>
        </article>
      </div>

      <div className="movement-actions">
        <a className="movement-cta primary" href={supportUrl} target="_blank" rel="noreferrer">
          Donate to the effort
        </a>
        <a
          className="movement-cta secondary"
          href={volunteerUrl}
          target="_blank"
          rel="noreferrer"
          title={
            isAirportSpecific && airportLabel
              ? `Save Our Skies Alliance page for ${airportLabel}`
              : "Save Our Skies Alliance"
          }
        >
          {isAirportSpecific && airportLabel
            ? `Volunteer at ${airportLabel}`
            : "Volunteer / get involved"}
        </a>
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

function FeedStatusIcon({ liveStatus }: { liveStatus: LiveStatusResponse | null }) {
  if (!liveStatus) return null;
  const labelFor: Record<string, string> = {
    adsbx: "ADSBExchange (paid)",
    adsb_lol: "adsb.lol (free)",
    adsb_fi: "adsb.fi (free)",
    airplanes_live: "airplanes.live (free)",
    opensky: "OpenSky (free)",
    self_hosted: "Self-hosted feeder",
  };
  const serving = liveStatus.serving_source ? labelFor[liveStatus.serving_source] ?? liveStatus.serving_source : "no live source";
  let tone: "ok" | "warning" | "error" | "pending" = "ok";
  let Icon = CheckCircle2;
  let message = `Live data: ${serving} — primary feed healthy.`;
  if (liveStatus.overall === "degraded") {
    tone = "warning";
    Icon = AlertTriangle;
    message = `Live data: paid feed is down, falling back to ${serving}. Quality may dip.`;
  } else if (liveStatus.overall === "down") {
    tone = "error";
    Icon = CloudOff;
    message = "Live data feeds are all unhealthy right now.";
  } else if (liveStatus.overall === "unknown") {
    tone = "pending";
    Icon = Clock3;
    message = "Live data status pending — the worker hasn't reported yet.";
  }
  return (
    <span
      className={`status-icon ${tone}`}
      title={message}
      data-tooltip={message}
      role="status"
      tabIndex={0}
      aria-label={message}
    >
      <Icon size={18} aria-hidden="true" />
    </span>
  );
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

function Counters({
  data,
  status,
  onRefresh,
  airportIcao,
}: {
  data: ScanResponse | null;
  status: string;
  onRefresh: () => Promise<void> | void;
  airportIcao?: string | null;
}) {
  const counters = data?.counters;
  const [refreshing, setRefreshing] = useState(false);
  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  };
  return (
    <div className="panel-block counters-block">
      <div className="counters-header">
        <span className="counters-status" title={status}>{status}</span>
        <button
          type="button"
          className="counters-refresh"
          onClick={handleRefresh}
          disabled={refreshing}
          title="Force a fresh scan now (otherwise auto-refreshes every 5s)"
          aria-label="Refresh scan data"
        >
          <RotateCw size={14} aria-hidden="true" className={refreshing ? "spin" : undefined} />
          <span>{refreshing ? "Refreshing…" : "Refresh"}</span>
        </button>
      </div>
      <div className="counter-strip">
        <div><strong>{counters?.circles ?? 0}</strong><span>circles</span></div>
        <div><strong>{counters?.touch_and_gos ?? 0}</strong><span>touch-and-gos</span></div>
        <div><strong>{counters?.passes ?? 0}</strong><span>passes over you</span></div>
        <div><strong>{counters?.offenders_active_now ?? 0}</strong><span>circling now</span></div>
        <div><strong>{data?.tracks.length ?? 0}</strong><span>tracked paths {counters?.label ?? ""}</span></div>
      </div>
      <FlowBadge airportIcao={airportIcao} />
    </div>
  );
}

function runwayBreakdownLabel(breakdown: Record<string, number>): string {
  const entries = Object.entries(breakdown);
  if (entries.length === 0) return "";
  // Sorted descending by count so the most-used runway leads.
  entries.sort((a, b) => b[1] - a[1]);
  if (entries.length === 1) {
    return `rwy ${entries[0][0]}`;
  }
  return entries.map(([rwy, n]) => `${n}×${rwy}`).join(" ");
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
          <span>Callsign</span><span>Origin</span><span>Score</span><span>Cir</span><span>TG</span><span>Dev</span><span>Pass</span><span>Avg over you</span>
        </div>
        {offenders.length === 0 && <div className="empty-row">No events in this window yet.</div>}
        {offenders.slice(0, 10).map((row) => (
          <button
            key={row.icao24}
            className={`table-row ${selected?.icao24 === row.icao24 ? "selected" : ""}`}
            onClick={() => onSelect(row)}
          >
            <span>
              <strong>{row.callsign}{row.is_cowboy ? " 🤠" : ""}</strong>
              <small>{row.icao24}{reportCounts[row.icao24] ? ` | reported ${reportCounts[row.icao24]}x` : ""}</small>
            </span>
            <span>{originDisplay(row).label}<small>{originDisplay(row).detail}</small></span>
            <span>{row.score}</span>
            <span>{row.circles}</span>
            <span>
              {row.touch_and_gos}
              {row.runway_breakdown && Object.keys(row.runway_breakdown).length > 0 && (
                <small>{runwayBreakdownLabel(row.runway_breakdown)}</small>
              )}
            </span>
            <span>{row.deviation_mean_nm != null ? `${row.deviation_mean_nm} nm` : "—"}</span>
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
  if (error instanceof ApiError && error.status === 408) {
    return "AI request timed out; generated a local draft instead.";
  }
  if (error instanceof Error) return `${error.message}; generated a local draft instead.`;
  return "Combined complaint failed; generated a local draft instead.";
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
  const systemPrompt = preferences.system_prompt;
  const [complaint, setComplaint] = useState<GeneratedComplaint | null>(null);
  const [detailStatus, setDetailStatus] = useState("Select an offender");
  const [shareOpen, setShareOpen] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const targets = useMemo(() => {
    if (complaintMode === "all") return offenders;
    return offender ? [offender] : [];
  }, [complaintMode, offender, offenders]);
  // Intentionally keyed on icao24 only (not live metrics) so polling refreshes
  // of scanData don't re-trigger Groq calls. Use the Regenerate button to
  // rebuild the draft against the latest observations.
  const targetIdsKey = targets.map((target) => target.icao24).join(",");
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
          reportCounts,
          systemPrompt
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
        reportCounts[target.icao24] ?? 0,
        systemPrompt
      )))
        .then((results: ComplaintResponse[]) => {
          if (cancelled) return;
          setComplaint({ text: results[0]?.text ?? "", source: results[0]?.source ?? "unknown" });
          setDetailStatus("Generated description");
        })
        .catch((error) => {
          if (cancelled) return;
          const target = targets[0];
          setComplaint({
            text: localSingleComplaint(target, scanParams, messagePrefs, reportCounts[target.icao24] ?? 0),
            source: "local fallback"
          });
          setDetailStatus(complaintErrorLabel(error));
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
  }, [complaintMode, targetIdsKey, targetCountKey, refreshNonce, scanParams, sliders, messagePrefs]);

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
        targets: targets.map((target) => ({
          icao24: target.icao24,
          callsign: target.callsign,
          circles: target.circles,
          touch_and_gos: target.touch_and_gos,
          low_approaches: target.low_approaches,
          passes_over_user: target.passes,
          origin_airport_icao: target.origin_airport_icao ?? null,
          origin_label: target.origin_label ?? null,
        }))
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

      <details className="grok-prompt">
        <summary>AI system prompt (advanced)</summary>
        <p className="grok-prompt-hint">Tune how the AI writes your complaints. Saved in your browser only. Leave blank for the default. Click Regenerate to apply.</p>
        <textarea
          className="grok-prompt-input"
          rows={3}
          value={preferences.system_prompt ?? ""}
          placeholder="You produce factual, civil aviation noise complaint descriptions. Use 12-hour AM/PM time, never military time."
          onChange={(event) => onPreferencesChange((current) => ({ ...current, system_prompt: event.target.value || undefined }))}
        />
        {preferences.system_prompt ? (
          <button className="grok-prompt-reset" onClick={() => onPreferencesChange((current) => ({ ...current, system_prompt: undefined }))}>Reset to default</button>
        ) : null}
      </details>

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
            <label>
              <input
                type="checkbox"
                checked={messagePrefs.include_db_at_home}
                onChange={(event) => updateMessagePreference("include_db_at_home", event.target.checked)}
              />
              <span>dB at home (avg + peak)</span>
            </label>
          </div>
        </div>

        <div className="complaint-output">
          <div className="complaint-output-header">
            <div className="section-title">Ready to submit complaint</div>
            <div className="complaint-output-actions">
              <button
                type="button"
                className="complaint-icon-button"
                onClick={() => setRefreshNonce((value) => value + 1)}
                disabled={targets.length === 0}
                title="Regenerate complaint from latest observations"
                aria-label="Regenerate complaint"
              >
                <RotateCw size={15} aria-hidden="true" />
              </button>
              <CopyButton text={complaint?.text} />
            </div>
          </div>
          <ComplaintSourceBanner source={complaint?.source} />
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

function ComplaintSourceBanner({ source }: { source?: string }) {
  if (!source) return null;
  if (source === "groq" || source === "cache") {
    return (
      <div className="complaint-source-banner ok" role="status">
        <CheckCircle2 size={14} aria-hidden="true" />
        <span>AI-generated from your real observations.</span>
      </div>
    );
  }
  if (source === "fallback") {
    return (
      <div className="complaint-source-banner warn" role="status">
        <AlertTriangle size={14} aria-hidden="true" />
        <span>
          <strong>AI is temporarily unavailable.</strong> Text below is a plain-template draft using the real
          call sign, times, and altitudes the worker recorded — no values fabricated.
        </span>
      </div>
    );
  }
  if (source === "local fallback") {
    return (
      <div className="complaint-source-banner error" role="status">
        <CloudOff size={14} aria-hidden="true" />
        <span>
          <strong>Server unreachable.</strong> This is a draft built in your browser from the data already on
          screen — same real observations, no AI involved.
        </span>
      </div>
    );
  }
  return null;
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
