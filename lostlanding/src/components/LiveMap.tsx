/**
 * The live aircraft map. A customer of the MAIN circlejerks API's
 * already-public /positions endpoint, reached same-origin via the `/live/*`
 * Caddy proxy (see Caddyfile) -- this service never gets its own copy of
 * that endpoint.
 *
 * Price tags multiply each aircraft's OBSERVED runway uses by the shared,
 * slider-controlled fee (see lib/feeContext.tsx); an aircraft with no entry
 * in the aircraft-fees payload has never been detected using the runway
 * here, and gets no tag at all -- never a "$0" tag, which would claim a fact
 * ("we've priced this aircraft at zero") we do not have.
 */
import { useEffect, useRef, useState } from "react";
import Map from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import OSM from "ol/source/OSM";
import Overlay from "ol/Overlay";
import { fromLonLat } from "ol/proj";
import { fetchLivePositions } from "../lib/api";
import { useFee } from "../lib/feeContext";
import { useAircraftFees } from "../lib/aircraftFeesContext";
import { latestPositions, type LatestAircraftPosition } from "../lib/livePositions";
import { planeIconSvg } from "../lib/planeIcon";
import { formatCurrency, formatInteger, formatUnixDate } from "../lib/format";
import type { AircraftFeeEntry, AircraftFeesResponse } from "../lib/liveTypes";

const AIRPORT_ICAO = "KLMO";
const AIRPORT_LAT = 40.1637;
const AIRPORT_LON = -105.1633;
const RING_NM = 8;
const POLL_INTERVAL_MS = 10_000;

interface TooltipState {
  icao24: string;
  x: number;
  y: number;
}

function TooltipBreakdown({
  position,
  entry,
  feesData,
  fee,
}: {
  position: LatestAircraftPosition;
  entry: AircraftFeeEntry | undefined;
  feesData: AircraftFeesResponse | null;
  fee: number;
}) {
  const tail = entry?.tail ?? (position.callsign ? position.callsign.trim() : position.icao24.toUpperCase());

  if (!entry) {
    return (
      <div className="plane-tooltip-body">
        <strong>{tail}</strong>
        <p>
          No recorded runway use — we have never detected this aircraft landing, touching-and-going, or making a
          low approach here.
        </p>
      </div>
    );
  }

  const sinceLabel = feesData?.counting_since != null ? formatUnixDate(feesData.counting_since) : "an unknown date";

  return (
    <div className="plane-tooltip-body">
      <strong>{tail}</strong>
      <dl className="plane-tooltip-rows">
        <div className="plane-tooltip-row">
          <dt>Last 24 hours</dt>
          <dd>
            {formatInteger(entry.today)} → {formatCurrency(entry.today * fee)}
          </dd>
        </div>
        <div className="plane-tooltip-row">
          <dt>This month</dt>
          <dd>
            {formatInteger(entry.month)} → {formatCurrency(entry.month * fee)}
          </dd>
        </div>
        <div className="plane-tooltip-row">
          <dt>This year</dt>
          <dd>
            {formatInteger(entry.year)} → {formatCurrency(entry.year * fee)}
          </dd>
        </div>
        <div className="plane-tooltip-row">
          <dt>Since we started counting ({sinceLabel})</dt>
          <dd>
            {formatInteger(entry.total)} → {formatCurrency(entry.total * fee)}
          </dd>
        </div>
      </dl>
    </div>
  );
}

export function LiveMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const overlaysRef = useRef<globalThis.Map<string, Overlay>>(new globalThis.Map());
  const [mapReady, setMapReady] = useState(false);
  const [positions, setPositions] = useState<LatestAircraftPosition[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTooltip, setActiveTooltip] = useState<TooltipState | null>(null);

  const { fee } = useFee();
  const { data: feesData } = useAircraftFees();

  // Poll live positions independently of the aircraft-fees poll (different
  // endpoint, different service, different cadence).
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = () => {
      fetchLivePositions(AIRPORT_ICAO, AIRPORT_LAT, AIRPORT_LON, RING_NM)
        .then((res) => {
          if (cancelled) return;
          setPositions(latestPositions(res.tracks));
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "Could not load live positions.");
        })
        .finally(() => {
          if (cancelled) return;
          setLoaded(true);
          timer = setTimeout(poll, POLL_INTERVAL_MS);
        });
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Map init -- once, ever, for this component instance.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const container = containerRef.current;
    const map = new Map({
      target: container,
      controls: [],
      layers: [new TileLayer({ source: new OSM({ crossOrigin: "anonymous" }) })],
      view: new View({ center: fromLonLat([AIRPORT_LON, AIRPORT_LAT]), zoom: 11 }),
    });
    mapRef.current = map;
    setMapReady(true);

    // OL measures the container ONCE at construction time. If the surrounding
    // layout is still settling (web fonts, the hero image, etc. still
    // reflowing above this section) the map's internal viewport can end up
    // smaller than the container ends up being, and its overlay layer (the
    // plane markers) keeps rendering at the STALE size -- markers drift past
    // the visible tiles into the frame's background. A ResizeObserver calling
    // updateSize() on every real size change is the standard OL fix.
    const resizeObserver = new ResizeObserver(() => map.updateSize());
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      map.setTarget(undefined);
      mapRef.current = null;
    };
  }, []);

  function openTooltip(icao24: string, el: HTMLElement) {
    const mapRect = containerRef.current?.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    if (!mapRect) return;
    setActiveTooltip({ icao24, x: elRect.left - mapRect.left + elRect.width / 2, y: elRect.top - mapRect.top });
  }

  function closeTooltipFor(icao24: string) {
    setActiveTooltip((current) => (current?.icao24 === icao24 ? null : current));
  }

  // Close the open tooltip on Escape, for keyboard users.
  useEffect(() => {
    if (!activeTooltip) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setActiveTooltip(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activeTooltip]);

  // Sync one DOM-overlay marker per live aircraft. Plain DOM elements (not
  // canvas-rendered vector features) so each marker is natively focusable and
  // hoverable -- required for the keyboard/mobile-accessible tooltip.
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;

    const seen = new Set<string>();
    for (const pos of positions) {
      seen.add(pos.icao24);
      let overlay = overlaysRef.current.get(pos.icao24);
      if (!overlay) {
        const element = document.createElement("button");
        element.type = "button";
        element.className = "plane-marker";
        overlay = new Overlay({ element, positioning: "center-center", stopEvent: false });
        map.addOverlay(overlay);
        overlaysRef.current.set(pos.icao24, overlay);
      }
      overlay.setPosition(fromLonLat([pos.lon, pos.lat]));

      const entry = feesData?.aircraft[pos.icao24];
      const priceTag = entry ? entry.total * fee : null;
      const rotation = pos.heading ?? 0;
      const el = overlay.getElement();
      if (el) {
        el.innerHTML = `
          <span class="plane-marker-icon" style="transform: rotate(${rotation}deg);">${planeIconSvg(26)}</span>
          ${priceTag !== null ? `<span class="plane-price-tag">${formatCurrency(priceTag)}</span>` : ""}
        `;
        const tail = entry?.tail ?? (pos.callsign ? pos.callsign.trim() : pos.icao24.toUpperCase());
        el.setAttribute(
          "aria-label",
          priceTag !== null
            ? `${tail}, illustrative total ${formatCurrency(priceTag)}. Show breakdown.`
            : `${tail}, no recorded runway use. Show details.`,
        );
        el.onmouseenter = () => openTooltip(pos.icao24, el);
        el.onmouseleave = () => closeTooltipFor(pos.icao24);
        el.onfocus = () => openTooltip(pos.icao24, el);
        el.onblur = () => closeTooltipFor(pos.icao24);
        el.onclick = () => openTooltip(pos.icao24, el);
      }
    }

    for (const [icao24, overlay] of overlaysRef.current) {
      if (!seen.has(icao24)) {
        map.removeOverlay(overlay);
        overlaysRef.current.delete(icao24);
        closeTooltipFor(icao24);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, mapReady, feesData, fee]);

  const activePosition = activeTooltip ? positions.find((p) => p.icao24 === activeTooltip.icao24) : undefined;
  const activeEntry = activeTooltip ? feesData?.aircraft[activeTooltip.icao24] : undefined;

  return (
    <section className="live-map-section section-pad" aria-labelledby="live-map-heading">
      <div className="section-heading">
        <p className="kicker">Live, Right Now</p>
        <h2 id="live-map-heading">Every plane on this map is really there</h2>
        <p>
          Positions refresh roughly every 10 seconds from live ADS-B tracking within {RING_NM} nm of the field. Price
          tags multiply each aircraft's OBSERVED runway uses by the fee you set below — aircraft we have never
          detected using the runway show no tag at all, not a $0 tag.
        </p>
      </div>

      <div className="live-map-frame art-card">
        <div
          ref={containerRef}
          className="live-map-canvas"
          role="img"
          aria-label="Map of live aircraft positions near Vance Brand Airport, Longmont, Colorado"
        />
        {loaded && positions.length === 0 && !error && (
          <div className="live-map-empty" role="status">
            No aircraft in the pattern right now.
          </div>
        )}
        {!loaded && !error && (
          <div className="live-map-empty" role="status">
            Locating live aircraft…
          </div>
        )}
        {error && (
          <div className="live-map-error" role="alert">
            Couldn't load live positions right now: {error}
          </div>
        )}
        {activeTooltip && activePosition && (
          <div
            className="plane-tooltip"
            role="tooltip"
            style={{ left: `${activeTooltip.x}px`, top: `${activeTooltip.y}px` }}
          >
            <TooltipBreakdown position={activePosition} entry={activeEntry} feesData={feesData} fee={fee} />
          </div>
        )}
      </div>
    </section>
  );
}
