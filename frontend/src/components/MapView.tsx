import { useEffect, useMemo, useRef, useState } from "react";
import Feature from "ol/Feature";
import Map from "ol/Map";
import View from "ol/View";
import { boundingExtent, buffer as bufferExtent } from "ol/extent";
import LineString from "ol/geom/LineString";
import Point from "ol/geom/Point";
import Polygon from "ol/geom/Polygon";
import Draw from "ol/interaction/Draw";
import Modify from "ol/interaction/Modify";
import { defaults as defaultInteractions } from "ol/interaction/defaults";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import { fromLonLat, toLonLat } from "ol/proj";
import OSM from "ol/source/OSM";
import VectorSource from "ol/source/Vector";
import { Circle as CircleStyle, Fill, RegularShape, Stroke, Style, Text } from "ol/style";
import { directionArrows, projectedPath } from "../lib/patternGeometry";
import type { Airport, Offender, RunwayPattern, ScanResponse, TrackSample } from "../lib/api";
import { smoothSegment } from "../lib/spline";

// Aircraft climbing under full power are MUCH louder than the same aircraft
// in cruise at the same altitude — engine + propeller noise dominates. Maps
// vertical rate to a dB bonus added to the source level. 1000 fpm climb is
// roughly +20 dB; descending (idle power) gets a small subtraction.
function climbNoiseBonusDb(verticalRateFpm: number | null | undefined): number {
  if (verticalRateFpm == null || !Number.isFinite(verticalRateFpm)) return 0;
  const rate = verticalRateFpm;
  if (rate < -300) return -3;
  if (rate < 100) return 0;
  return Math.min(25, 10 * Math.log10(1 + rate / 100));
}

// dB at observer for a single aircraft passage given altitude AGL plus the
// climb-rate noise bonus. Anchor: 900 ft AGL in level flight → 65 dB.
function dbFromAltAndClimb(
  altitudeAgl: number | null | undefined,
  verticalRateFpm: number | null | undefined
): number {
  if (altitudeAgl == null || !Number.isFinite(altitudeAgl) || altitudeAgl <= 0) return 30;
  const ratio = 900 / altitudeAgl;
  const sourceDb = 65 + climbNoiseBonusDb(verticalRateFpm);
  const db = sourceDb + 20 * Math.log10(ratio);
  if (db < 30) return 30;
  if (db > 110) return 110;
  return db;
}

// Backward-compat for older callers that pass only altitude.
function dbFromAltitudeAgl(altitudeAgl: number | null | undefined): number {
  return dbFromAltAndClimb(altitudeAgl, null);
}

// Color a track segment from red (low alt, loud) to gray (high alt, quiet).
// 0 ft AGL → solid red; 3000 ft+ AGL → cool gray. Linear RGB interpolation
// looks fine in browsers for this short ramp.
function colorFromAltitudeAgl(altitudeAgl: number | null | undefined): [number, number, number] {
  const STOPS: Array<[number, [number, number, number]]> = [
    [0, [220, 38, 38]],       // red-600
    [800, [234, 88, 12]],     // orange-600
    [1500, [217, 119, 6]],    // amber-600
    [2400, [120, 113, 108]],  // stone-500
    [3500, [156, 163, 175]],  // gray-400
  ];
  const value = altitudeAgl == null || !Number.isFinite(altitudeAgl) ? 1500 : altitudeAgl;
  if (value <= STOPS[0][0]) return STOPS[0][1];
  if (value >= STOPS[STOPS.length - 1][0]) return STOPS[STOPS.length - 1][1];
  for (let i = 1; i < STOPS.length; i += 1) {
    const [hi, hiRgb] = STOPS[i];
    if (value <= hi) {
      const [lo, loRgb] = STOPS[i - 1];
      const t = (value - lo) / (hi - lo);
      return [
        Math.round(loRgb[0] + (hiRgb[0] - loRgb[0]) * t),
        Math.round(loRgb[1] + (hiRgb[1] - loRgb[1]) * t),
        Math.round(loRgb[2] + (hiRgb[2] - loRgb[2]) * t),
      ];
    }
  }
  return STOPS[STOPS.length - 1][1];
}

interface Props {
  airport?: Airport | null;
  userLocation: { lat: number; lon: number } | null;
  scanData?: ScanResponse | null;
  selectedIcao24?: string | null;
  autoZoom?: boolean;
  showHeatmap?: boolean;
  onPickLocation: (lat: number, lon: number) => void;
  patterns?: RunwayPattern[] | null;
  editingRunwayId?: string | null;
  editingPoints?: { lat: number; lon: number }[];
  editingClosed?: boolean;
  editSeedKey?: number;
  onEditingPointsChange?: (points: { lat: number; lon: number }[]) => void;
}

// dB → [r, g, b] for additive canvas compositing.
// Stops match the legend (blue 30 → cyan 45 → green 60 → yellow 72 →
// orange 81 → red 90+) with smooth linear interpolation between them.
function rgbFromDb(db: number): [number, number, number] {
  const STOPS: Array<[number, [number, number, number]]> = [
    [30, [29, 78, 216]],
    [45, [6, 182, 212]],
    [60, [34, 197, 94]],
    [72, [250, 204, 21]],
    [81, [249, 115, 22]],
    [95, [239, 68, 68]],
  ];
  if (db <= STOPS[0][0]) return STOPS[0][1];
  if (db >= STOPS[STOPS.length - 1][0]) return STOPS[STOPS.length - 1][1];
  for (let i = 1; i < STOPS.length; i += 1) {
    if (db <= STOPS[i][0]) {
      const [loDb, lo] = STOPS[i - 1];
      const [hiDb, hi] = STOPS[i];
      const t = (db - loDb) / (hiDb - loDb);
      return [
        Math.round(lo[0] + (hi[0] - lo[0]) * t),
        Math.round(lo[1] + (hi[1] - lo[1]) * t),
        Math.round(lo[2] + (hi[2] - lo[2]) * t),
      ];
    }
  }
  return STOPS[STOPS.length - 1][1];
}

// Ambient L_den baseline used as the noise FLOOR before aircraft contribution
// is added. 40 dB is the EU-WHO "quiet residential" reference. Future work:
// replace this constant with a per-cell value from a long-term noise map
// (e.g. OSM road-traffic noise raster — see lukasmartinelli/osm-noise-pollution
// or a paid noise-map.com dataset). Until then the constant gives an honest
// floor instead of pretending quiet cells are at 0 dB.
const AMBIENT_BASELINE_DB = 40;
const AMBIENT_BASELINE_POWER = Math.pow(10, AMBIENT_BASELINE_DB / 10);

// Two sound levels in dB combine ENERGETICALLY, not arithmetically:
// L_total = 10·log10(10^(L_a/10) + 10^(L_b/10))
function combineDb(...levels: number[]): number {
  let energy = 0;
  for (const lvl of levels) energy += Math.pow(10, lvl / 10);
  return 10 * Math.log10(energy);
}

function clamp01(value: number): number {
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

const AIRCRAFT_DISPLAY_DELAY_SECONDS = 15;
// Only paint an aircraft marker if we have a sample inside this window. Older
// tracks come from the ADS-B history / backfill and should NOT show a moving
// icon — they're just past trails, not live aircraft.
const LIVE_AIRCRAFT_FRESHNESS_SECONDS = 60;
const AIRCRAFT_EXTRAPOLATE_SECONDS = 8;

interface AircraftTrack {
  icao24: string;
  callsign: string;
  selected: boolean;
  samples: TrackSample[];
}

interface AircraftPosition {
  lon: number;
  lat: number;
  heading: number | null;
}

// Auto-zoom focuses on the user's home and the nearest airport. Track samples
// influence the fit only when they fall within FOCUS_RADIUS_NM of the home;
// long inbound/outbound legs from where planes originated are excluded so the
// regional sprawl doesn't drag the view out. Rendering is unaffected — those
// far-away track segments still draw, they just don't expand the auto-zoom.
const FOCUS_RADIUS_NM = 3;
const AIRPORT_INCLUDE_RADIUS_NM = 6;
const NM_TO_DEG_LAT = 1 / 60;

function activityExtent(
  airport?: Airport | null,
  userLocation?: { lat: number; lon: number } | null,
  scanData?: ScanResponse | null
) {
  const focusLat = userLocation?.lat ?? airport?.lat;
  const focusLon = userLocation?.lon ?? airport?.lon;
  if (focusLat == null || focusLon == null) return null;
  const cosFocus = Math.cos((focusLat * Math.PI) / 180) || 1;
  const focusRadiusDeg = FOCUS_RADIUS_NM * NM_TO_DEG_LAT;

  const coords: number[][] = [];
  coords.push(fromLonLat([focusLon, focusLat]));
  // Minimum visible window: ±FOCUS_RADIUS_NM around the home so the auto-zoom
  // never goes tighter than neighborhood scale even when there's no track data.
  const dLat = focusRadiusDeg;
  const dLon = focusRadiusDeg / cosFocus;
  coords.push(fromLonLat([focusLon - dLon, focusLat - dLat]));
  coords.push(fromLonLat([focusLon + dLon, focusLat + dLat]));

  if (airport) {
    const aDlat = airport.lat - focusLat;
    const aDlon = (airport.lon - focusLon) * cosFocus;
    const airportDistDeg = Math.sqrt(aDlat * aDlat + aDlon * aDlon);
    if (airportDistDeg <= AIRPORT_INCLUDE_RADIUS_NM * NM_TO_DEG_LAT) {
      coords.push(fromLonLat([airport.lon, airport.lat]));
    }
  }

  for (const track of scanData?.tracks ?? []) {
    const inWindow = track.samples.filter((sample) => sample.in_window);
    const samples = inWindow.length > 0 ? inWindow : track.samples;
    for (const sample of samples) {
      if (!Number.isFinite(sample.lon) || !Number.isFinite(sample.lat)) continue;
      const sDlat = sample.lat - focusLat;
      const sDlon = (sample.lon - focusLon) * cosFocus;
      if (Math.sqrt(sDlat * sDlat + sDlon * sDlon) > focusRadiusDeg) continue;
      coords.push(fromLonLat([sample.lon, sample.lat]));
    }
  }

  const extent = boundingExtent(coords);
  const width = extent[2] - extent[0];
  const height = extent[3] - extent[1];
  return bufferExtent(extent, Math.max(width, height) * 0.05 || 800);
}

function circlePolygon(lat: number, lon: number, radiusNm: number) {
  const coords: number[][] = [];
  const earthNm = 3440.065;
  const latRad = (lat * Math.PI) / 180;
  for (let i = 0; i <= 96; i += 1) {
    const bearing = (i / 96) * 2 * Math.PI;
    const angular = radiusNm / earthNm;
    const pointLat = Math.asin(
      Math.sin(latRad) * Math.cos(angular) +
        Math.cos(latRad) * Math.sin(angular) * Math.cos(bearing)
    );
    const pointLon =
      (lon * Math.PI) / 180 +
      Math.atan2(
        Math.sin(bearing) * Math.sin(angular) * Math.cos(latRad),
        Math.cos(angular) - Math.sin(latRad) * Math.sin(pointLat)
      );
    coords.push([(pointLon * 180) / Math.PI, (pointLat * 180) / Math.PI]);
  }
  return coords.map((coord) => fromLonLat(coord));
}

function splitSegments(samples: TrackSample[], inWindow: boolean) {
  const segments: number[][][] = [];
  let current: number[][] = [];
  for (const sample of samples) {
    if (sample.in_window === inWindow) {
      current.push(fromLonLat([sample.lon, sample.lat]));
    } else if (current.length > 1) {
      segments.push(smoothSegment(current));
      current = [];
    } else {
      current = [];
    }
  }
  if (current.length > 1) {
    segments.push(smoothSegment(current));
  }
  return segments;
}

// Slice in-window samples into 10 time buckets. Each bucket carries:
//   - smoothed coords via Catmull-Rom
//   - ageRatio (0 newest → 1 oldest)
//   - avgAltAglFt (mean AGL for samples in the bucket, or null if unknown)
function agedTrackSegments(
  samples: TrackSample[],
  groundElevFt: number,
  windowStart: number,
  windowEnd: number,
  buckets = 10
): Array<{ coords: number[][]; ageRatio: number; avgAltAglFt: number | null }> {
  const sorted = samples
    .filter((s) => s.in_window)
    .slice()
    .sort((a, b) => a.timestamp - b.timestamp);
  if (sorted.length < 2) return [];
  const span = Math.max(1, windowEnd - windowStart);
  const out: Array<{ coords: number[][]; ageRatio: number; avgAltAglFt: number | null }> = [];
  let bucketStart = 0;
  for (let i = 1; i <= buckets; i += 1) {
    const bucketEndTs = i === buckets ? windowEnd + 1 : windowStart + (span * i) / buckets;
    let split = bucketStart;
    while (split < sorted.length && sorted[split].timestamp <= bucketEndTs) split += 1;
    const slice = sorted.slice(bucketStart, Math.min(split + 1, sorted.length));
    if (slice.length >= 2) {
      const coords = smoothSegment(slice.map((s) => fromLonLat([s.lon, s.lat])));
      const midTs = (slice[0].timestamp + slice[slice.length - 1].timestamp) / 2;
      const ageRatio = clamp01(1 - (midTs - windowStart) / span);
      const altVals = slice
        .map((s) => (s.altitude_ft != null ? s.altitude_ft - groundElevFt : null))
        .filter((v): v is number => v != null && Number.isFinite(v));
      const avgAltAglFt = altVals.length > 0 ? altVals.reduce((a, b) => a + b, 0) / altVals.length : null;
      out.push({ coords, ageRatio, avgAltAglFt });
    }
    bucketStart = Math.max(bucketStart, split - 1);
  }
  return out;
}

function lineToLonLat(feature: Feature<LineString>): { lat: number; lon: number }[] {
  return feature.getGeometry()!.getCoordinates().map((c) => {
    const [lon, lat] = toLonLat(c);
    return { lat, lon };
  });
}

function pointFeature(lon: number, lat: number, properties: Record<string, unknown>) {
  const feature = new Feature(new Point(fromLonLat([lon, lat])));
  feature.setProperties(properties);
  return feature;
}

function lineFeature(coords: number[][], properties: Record<string, unknown>) {
  const feature = new Feature(new LineString(coords));
  feature.setProperties(properties);
  return feature;
}

function polygonFeature(coords: number[][], properties: Record<string, unknown>) {
  const feature = new Feature(new Polygon([coords]));
  feature.setProperties(properties);
  return feature;
}

function normalizeTrackSamples(samples: TrackSample[]) {
  return samples
    .filter((sample) => Number.isFinite(sample.lat) && Number.isFinite(sample.lon) && Number.isFinite(sample.timestamp))
    .slice()
    .sort((a, b) => a.timestamp - b.timestamp);
}

function fallbackHeading(from: TrackSample, to: TrackSample) {
  const fromLat = (from.lat * Math.PI) / 180;
  const toLat = (to.lat * Math.PI) / 180;
  const deltaLon = ((to.lon - from.lon) * Math.PI) / 180;
  const y = Math.sin(deltaLon) * Math.cos(toLat);
  const x =
    Math.cos(fromLat) * Math.sin(toLat) -
    Math.sin(fromLat) * Math.cos(toLat) * Math.cos(deltaLon);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function headingForLeg(from: TrackSample, to: TrackSample) {
  return to.heading_deg ?? from.heading_deg ?? fallbackHeading(from, to);
}

function interpolateHeading(start: number | null | undefined, end: number | null | undefined, ratio: number) {
  if (start == null && end == null) return null;
  if (start == null) return end ?? null;
  if (end == null) return start;
  const delta = ((end - start + 540) % 360) - 180;
  return (start + delta * ratio + 360) % 360;
}

function interpolateSample(from: TrackSample, to: TrackSample, ratio: number): AircraftPosition {
  const startHeading = from.heading_deg ?? fallbackHeading(from, to);
  const endHeading = to.heading_deg ?? startHeading;
  return {
    lon: from.lon + (to.lon - from.lon) * ratio,
    lat: from.lat + (to.lat - from.lat) * ratio,
    heading: interpolateHeading(startHeading, endHeading, ratio)
  };
}

function positionAt(samples: TrackSample[], displayTimeSeconds: number): AircraftPosition | null {
  const normalized = normalizeTrackSamples(samples);
  if (normalized.length === 0) return null;
  if (normalized.length === 1 || displayTimeSeconds <= normalized[0].timestamp) {
    const first = normalized[0];
    return { lon: first.lon, lat: first.lat, heading: first.heading_deg ?? null };
  }

  for (let index = 1; index < normalized.length; index += 1) {
    const previous = normalized[index - 1];
    const next = normalized[index];
    if (displayTimeSeconds <= next.timestamp) {
      const span = Math.max(next.timestamp - previous.timestamp, 1);
      const ratio = Math.max(0, Math.min(1, (displayTimeSeconds - previous.timestamp) / span));
      return interpolateSample(previous, next, ratio);
    }
  }

  const previous = normalized[normalized.length - 2];
  const latest = normalized[normalized.length - 1];
  const span = latest.timestamp - previous.timestamp;
  if (span <= 0) {
    return { lon: latest.lon, lat: latest.lat, heading: latest.heading_deg ?? null };
  }
  const driftSeconds = Math.min(Math.max(displayTimeSeconds - latest.timestamp, 0), AIRCRAFT_EXTRAPOLATE_SECONDS);
  const ratio = driftSeconds / span;
  return {
    lon: latest.lon + (latest.lon - previous.lon) * ratio,
    lat: latest.lat + (latest.lat - previous.lat) * ratio,
    heading: latest.heading_deg ?? headingForLeg(previous, latest)
  };
}

function styleForFeature(feature: Feature) {
  const kind = feature.get("kind");
  const selected = feature.get("selected") === 1;
  if (kind === "ring" || kind === "pass") {
    return new Style({
      fill: new Fill({ color: kind === "pass" ? "rgba(214, 75, 44, 0.08)" : "rgba(27, 58, 107, 0.06)" }),
      stroke: new Stroke({ color: kind === "pass" ? "#d64b2c" : "#1b3a6b", width: 1.25, lineDash: [6, 6] })
    });
  }
  if (kind === "track_context") {
    return new Style({
      stroke: new Stroke({
        color: selected ? "rgba(214, 75, 44, 0.36)" : "rgba(27, 58, 107, 0.2)",
        width: selected ? 4 : 2
      })
    });
  }
  if (kind === "track_active") {
    const ageRatio = Number(feature.get("age_ratio") ?? 0);
    // ageRatio 0 = newest (opaque), 1 = oldest in window (faint)
    const alpha = Math.max(0.08, 1 - ageRatio * 0.92);
    const altAgl = feature.get("alt_agl_ft");
    const [r, g, b] = selected
      ? [214, 75, 44]
      : colorFromAltitudeAgl(typeof altAgl === "number" ? altAgl : null);
    return new Style({
      stroke: new Stroke({
        color: `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`,
        width: selected ? 5 : 3,
      }),
    });
  }
  if (kind === "track_sample") {
    return new Style({
      image: new CircleStyle({
        radius: selected ? 4 : 3,
        fill: new Fill({ color: selected ? "rgba(214, 75, 44, 0.9)" : "rgba(27, 58, 107, 0.72)" }),
        stroke: new Stroke({ color: "#ffffff", width: selected ? 1.5 : 1 })
      })
    });
  }

  if (kind === "aircraft") {
    const heading = Number(feature.get("heading") ?? 0);
    return new Style({
      image: new RegularShape({
        points: 3,
        radius: selected ? 13 : 10,
        rotation: (heading * Math.PI) / 180,
        angle: Math.PI / 2,
        rotateWithView: true,
        fill: new Fill({ color: selected ? "#d64b2c" : "#1b3a6b" }),
        stroke: new Stroke({ color: "#ffffff", width: selected ? 2.5 : 2 })
      }),
      text: new Text({
        text: String(feature.get("label") ?? ""),
        offsetY: selected ? -24 : -20,
        font: selected ? "800 12px Inter, sans-serif" : "700 11px Inter, sans-serif",
        fill: new Fill({ color: selected ? "#b8391e" : "#1b3a6b" }),
        stroke: new Stroke({ color: "#ffffff", width: 3 })
      })
    });
  }

  if (kind === "pattern_edit") {
    return new Style({
      stroke: new Stroke({ color: "#d64b2c", width: 2.5, lineDash: [6, 4] }),
      image: new CircleStyle({
        radius: 5,
        fill: new Fill({ color: "#ffffff" }),
        stroke: new Stroke({ color: "#1b3a6b", width: 2 }),
      }),
    });
  }
  if (kind === "pattern") {
    return new Style({
      stroke: new Stroke({ color: "#1b3a6b", width: 2.5 }),
    });
  }
  if (kind === "pattern_arrow") {
    return new Style({
      image: new RegularShape({
        points: 3,
        radius: 7,
        rotation: (feature.get("rotation") as number) ?? 0,
        fill: new Fill({ color: "#d64b2c" }),
        stroke: new Stroke({ color: "#ffffff", width: 1 }),
      }),
    });
  }

  const colors: Record<string, string> = {
    airport: "#1b3a6b",
    home: "#2ea84c"
  };
  return new Style({
    image: new CircleStyle({
      radius: selected ? 8 : 6,
      fill: new Fill({ color: colors[String(kind)] ?? "#111827" }),
      stroke: new Stroke({ color: "#ffffff", width: 2 })
    }),
    text: new Text({
      text: String(feature.get("label") ?? ""),
      offsetY: -16,
      font: "700 12px Inter, sans-serif",
      fill: new Fill({ color: "#1b3a6b" }),
      stroke: new Stroke({ color: "#ffffff", width: 3 })
    })
  });
}

export default function MapView({ airport, userLocation, scanData, selectedIcao24, autoZoom = true, showHeatmap = false, onPickLocation, patterns, editingRunwayId, editingPoints, editingClosed, editSeedKey, onEditingPointsChange }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const sourceRef = useRef<VectorSource | null>(null);
  const aircraftSourceRef = useRef<VectorSource | null>(null);
  const patternSourceRef = useRef<VectorSource | null>(null);
  const editSourceRef = useRef<VectorSource | null>(null);
  const editLineRef = useRef<Feature<LineString> | null>(null);
  const onEditChangeRef = useRef(onEditingPointsChange);
  onEditChangeRef.current = onEditingPointsChange;
  const aircraftFeaturesRef = useRef<globalThis.Map<string, Feature<Point>>>(new globalThis.Map());
  const aircraftTracksRef = useRef<globalThis.Map<string, AircraftTrack>>(new globalThis.Map());
  const heatmapCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const heatmapOffscreenRef = useRef<HTMLCanvasElement | null>(null);
  const heatmapAccumulatorRef = useRef<{
    sumPower: Float32Array;
    peakDb: Float32Array;
    aircraftLeq: Float32Array;
    accW: number;
    accH: number;
    accScale: number;
    windowSeconds: number;
    baselineDb: number;
  } | null>(null);
  const frameRef = useRef<number | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [hoverDb, setHoverDb] = useState<{
    x: number;
    y: number;
    peak: number;
    aircraftLeq: number;
    combinedLeq: number;
    baseline: number;
  } | null>(null);
  const [contextMenu, setContextMenu] = useState<
    | { x: number; y: number; lat: number; lon: number }
    | null
  >(null);
  const [featureHover, setFeatureHover] = useState<
    | {
        kind: "home";
        x: number;
        y: number;
        passes: number;
        aircraftCount: number;
        peakDb: number | null;
        lowestOverheadFt: number | null;
      }
    | {
        kind: "aircraft";
        x: number;
        y: number;
        callsign: string;
        icao24: string;
        circles: number;
        touchAndGos: number;
        lowApproaches: number;
        passes: number;
        origin: string;
        avgOverheadFt: number | null;
        minOverheadFt: number | null;
      }
    | null
  >(null);

  const windowStart = scanData?.window?.start_ts ?? 0;
  const windowEnd = scanData?.window?.end_ts ?? 0;
  const groundElevFt = airport?.elevation_ft ?? 0;

  const features = useMemo(() => {
    const rows: Feature[] = [];
    if (airport) {
      rows.push(polygonFeature(circlePolygon(airport.lat, airport.lon, 8), { kind: "ring" }));
      rows.push(pointFeature(airport.lon, airport.lat, { kind: "airport", label: airport.icao }));
    }
    if (userLocation) {
      rows.push(polygonFeature(circlePolygon(userLocation.lat, userLocation.lon, 0.5), { kind: "pass" }));
      rows.push(pointFeature(userLocation.lon, userLocation.lat, { kind: "home", label: "Home" }));
    }
    // In heatmap mode the canvas overlay draws the gradient; here we only
    // emit the airport ring + home pin so the user still has reference points.
    if (showHeatmap) return rows;
    for (const track of scanData?.tracks ?? []) {
      const isSelected = selectedIcao24 === track.icao24;
      for (const coords of splitSegments(track.samples, false)) {
        rows.push(lineFeature(coords, { kind: "track_context", selected: isSelected ? 1 : 0 }));
      }
      const aged = windowStart && windowEnd
        ? agedTrackSegments(track.samples, groundElevFt, windowStart, windowEnd)
        : splitSegments(track.samples, true).map((coords) => ({
            coords,
            ageRatio: 0,
            avgAltAglFt: null as number | null,
          }));
      for (const { coords, ageRatio, avgAltAglFt } of aged) {
        rows.push(lineFeature(coords, {
          kind: "track_active",
          selected: isSelected ? 1 : 0,
          age_ratio: ageRatio,
          alt_agl_ft: avgAltAglFt,
        }));
      }
    }
    return rows;
  }, [airport, userLocation, scanData, selectedIcao24, showHeatmap, windowStart, windowEnd, groundElevFt]);

  const aircraftTracks = useMemo<AircraftTrack[]>(() => {
    if (showHeatmap) return [];
    const liveCutoff = Date.now() / 1000 - LIVE_AIRCRAFT_FRESHNESS_SECONDS;
    return (scanData?.tracks ?? [])
      .map((track) => ({
        icao24: track.icao24,
        callsign: track.callsign,
        selected: selectedIcao24 === track.icao24,
        samples: normalizeTrackSamples(track.samples)
      }))
      .filter((track) => {
        if (track.samples.length === 0) return false;
        // Only consider it a live aircraft if its most recent sample is fresh.
        let latest = 0;
        for (const sample of track.samples) {
          if (sample.timestamp > latest) latest = sample.timestamp;
        }
        return latest >= liveCutoff;
      });
  }, [scanData, selectedIcao24, showHeatmap]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    sourceRef.current = new VectorSource();
    aircraftSourceRef.current = new VectorSource();
    patternSourceRef.current = new VectorSource();
    editSourceRef.current = new VectorSource();
    const vectorLayer = new VectorLayer({
      source: sourceRef.current,
      style: (feature) => styleForFeature(feature as Feature)
    });
    const patternLayer = new VectorLayer({
      source: patternSourceRef.current,
      style: (feature) => styleForFeature(feature as Feature),
      zIndex: 5,
    });
    const editLayer = new VectorLayer({
      source: editSourceRef.current,
      style: (feature) => styleForFeature(feature as Feature),
      zIndex: 6,
    });
    const aircraftLayer = new VectorLayer({
      source: aircraftSourceRef.current,
      style: (feature) => styleForFeature(feature as Feature),
      declutter: true,
      zIndex: 10
    });
    mapRef.current = new Map({
      target: containerRef.current,
      interactions: defaultInteractions({ mouseWheelZoom: false }),
      layers: [
        new TileLayer({ source: new OSM({ crossOrigin: "anonymous" }) }),
        vectorLayer,
        patternLayer,
        editLayer,
        aircraftLayer
      ],
      view: new View({
        center: fromLonLat([userLocation?.lon ?? airport?.lon ?? -105.1172, userLocation?.lat ?? airport?.lat ?? 39.9088]),
        zoom: 10
      })
    });
    setMapReady(true);
  }, [airport?.lat, airport?.lon, userLocation?.lat, userLocation?.lon, onPickLocation]);

  // Right-click context menu. Registered in its own effect (NOT inside the
  // map-init effect) because the init effect early-returns on subsequent
  // renders, which would otherwise destroy this listener every time
  // userLocation changes.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const viewport = map.getViewport();
    const onContextMenu = (domEvent: Event) => {
      domEvent.preventDefault();
      const coordinate = map.getEventCoordinate(domEvent as MouseEvent);
      if (!coordinate) return;
      const [lon, lat] = toLonLat(coordinate);
      const mouseEvent = domEvent as MouseEvent;
      setContextMenu({ x: mouseEvent.clientX, y: mouseEvent.clientY, lat, lon });
    };
    viewport.addEventListener("contextmenu", onContextMenu);
    return () => viewport.removeEventListener("contextmenu", onContextMenu);
  }, [mapReady]);

  // Close the context menu on outside click, scroll, or Escape. Click inside
  // the menu wrapper has stopPropagation, so it doesn't reach the window
  // listener.
  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [contextMenu]);

  useEffect(() => {
    const source = sourceRef.current;
    if (!source) return;
    source.clear();
    source.addFeatures(features);
  }, [features]);

  useEffect(() => {
    const source = patternSourceRef.current;
    if (!mapReady || !source) return;
    source.clear();
    for (const pattern of patterns ?? []) {
      const path = projectedPath(pattern.geometry.points, pattern.geometry.closed);
      if (path.length < 2) continue;
      const line = new Feature(new LineString(path));
      line.set("kind", "pattern");
      line.set("runwayId", pattern.runway_id);
      source.addFeature(line);
      for (const arrow of directionArrows(path, 3)) {
        const marker = new Feature(new Point(arrow.coord));
        marker.set("kind", "pattern_arrow");
        marker.set("rotation", arrow.rotation);
        source.addFeature(marker);
      }
    }
  }, [patterns, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    const source = editSourceRef.current;
    if (!mapReady || !map || !source) return;

    source.clear();
    editLineRef.current = null;

    if (!editingRunwayId) return; // edit mode off

    const seedCoords = (editingPoints ?? []).map((p) => fromLonLat([p.lon, p.lat]));
    if (seedCoords.length >= 2) {
      const line = new Feature(new LineString(seedCoords));
      line.set("kind", "pattern_edit");
      editLineRef.current = line;
      source.addFeature(line);
    }

    const emit = () => {
      if (editLineRef.current) onEditChangeRef.current?.(lineToLonLat(editLineRef.current));
    };

    const modify = new Modify({ source });
    modify.on("modifyend", emit);
    map.addInteraction(modify);

    let draw: Draw | null = null;
    if (!editLineRef.current) {
      draw = new Draw({ source, type: "LineString" });
      draw.on("drawend", (event) => {
        const f = event.feature as Feature<LineString>;
        f.set("kind", "pattern_edit");
        editLineRef.current = f;
        if (draw) map.removeInteraction(draw);
        onEditChangeRef.current?.(lineToLonLat(f));
      });
      map.addInteraction(draw);
    }

    return () => {
      map.removeInteraction(modify);
      if (draw) map.removeInteraction(draw);
    };
    // Re-seed only when the runway or an explicit seed token changes — NOT on every
    // editingPoints update (those originate from this effect's own emits).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, editingRunwayId, editSeedKey, editingClosed]);

  // Deferred Heatmap layer init: putting HeatmapLayer in the initial
  // new Map({ layers: [...] }) array silently breaks OL 10.9's renderer and
  // produces zero canvases. Adding it via addLayer() AFTER the map is alive
  // works, but only if we never call setVisible(true/false) — that flip
  // seems to occlude the basemap on first show. Workaround: leave the layer
  // always visible and rely on an empty source rendering nothing.
  // Continuous-field heatmap using L_eq (equivalent continuous sound level).
  // For each in-window pair of consecutive samples in a track we interpolate
  // K points along the segment — turning the discrete sample stream back
  // into the smooth flight vector it represents. Each interpolated point
  // contributes acoustic POWER (10^(dB/10) × dt seconds) to the accumulator
  // via a Gaussian halo. The cell color = L_eq = 10·log10(Σpower / Twindow):
  // a cell crossed 100 times accumulates 100× the power and reads ~20 dB
  // hotter than a cell crossed once — exactly the density behaviour the
  // user asked for.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const canvas = heatmapCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (!heatmapOffscreenRef.current) {
      heatmapOffscreenRef.current = document.createElement("canvas");
    }
    const off = heatmapOffscreenRef.current;

    const draw = () => {
      const size = map.getSize();
      if (!size) return;
      const dpr = window.devicePixelRatio || 1;
      const widthCss = size[0];
      const heightCss = size[1];
      if (canvas.width !== widthCss * dpr || canvas.height !== heightCss * dpr) {
        canvas.width = widthCss * dpr;
        canvas.height = heightCss * dpr;
        canvas.style.width = `${widthCss}px`;
        canvas.style.height = `${heightCss}px`;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, widthCss, heightCss);
      if (!showHeatmap) {
        heatmapAccumulatorRef.current = null;
        return;
      }

      // 1/4-res accumulator. Coarser cells let the browser's bilinear
      // upscale do the heavy smoothing for us.
      const accScale = 4;
      const accW = Math.max(8, Math.floor(widthCss / accScale));
      const accH = Math.max(8, Math.floor(heightCss / accScale));
      const sumPower = new Float32Array(accW * accH);
      const peakDb = new Float32Array(accW * accH);
      const aircraftLeq = new Float32Array(accW * accH);

      const view = map.getView();
      const resolution = view.getResolution() ?? 1;
      // 160 m halo per interpolated point, capped at 18 accumulator px so
      // single passes don't draw huge discs at street zoom. With per-second
      // interpolation, samples densely overlap and the visual smooths out.
      const radiusMeters = 160;
      const radiusPx = Math.min(18, Math.max(8, radiusMeters / resolution / accScale));
      const radiusPxInt = Math.ceil(radiusPx);
      const inv2Sigma2 = 1.0 / (radiusPx * radiusPx);

      const windowSeconds = Math.max(60, windowEnd - windowStart);
      const tracks = scanData?.tracks ?? [];

      // Pre-projection cache: project samples once, reuse for segments.
      for (let ti = 0; ti < tracks.length; ti += 1) {
        const allSamples = tracks[ti].samples;
        // Filter to in-window samples sorted by timestamp.
        const samples: Array<{
          lon: number;
          lat: number;
          ts: number;
          altFt: number;
          climbFpm: number;
        }> = [];
        for (let si = 0; si < allSamples.length; si += 1) {
          const s = allSamples[si];
          if (!s.in_window) continue;
          const a = (s as TrackSample).altitude_ft;
          if (a == null) continue;
          const v = (s as TrackSample).vertical_rate_fpm;
          samples.push({
            lon: s.lon,
            lat: s.lat,
            ts: s.timestamp,
            altFt: a,
            climbFpm: v == null || !Number.isFinite(v) ? 0 : v,
          });
        }
        if (samples.length < 1) continue;
        samples.sort((a, b) => a.ts - b.ts);

        // Walk consecutive sample pairs as track VECTORS and interpolate
        // sub-points at 1 s spacing. Singletons (no neighbor in window) get
        // a single 10 s contribution at their own location.
        for (let si = 0; si < samples.length; si += 1) {
          const s1 = samples[si];
          const s2 = si + 1 < samples.length ? samples[si + 1] : null;
          let dt = 0;
          let kPoints = 1;
          if (s2) {
            dt = s2.ts - s1.ts;
            if (dt <= 0 || dt > 90) {
              // Stale or huge gap; treat as singleton 10 s contribution.
              dt = 10;
              kPoints = 1;
            } else {
              // 1 interpolated point per second, but cap to keep total work
              // bounded. Each sub-point represents dt/kPoints seconds.
              kPoints = Math.min(20, Math.max(2, Math.ceil(dt)));
            }
          } else {
            dt = 10;
            kPoints = 1;
          }
          const dtPerPoint = dt / kPoints;

          for (let k = 0; k < kPoints; k += 1) {
            const tFrac = kPoints === 1 ? 0 : k / (kPoints - 1);
            const lon = s2 ? s1.lon + (s2.lon - s1.lon) * tFrac : s1.lon;
            const lat = s2 ? s1.lat + (s2.lat - s1.lat) * tFrac : s1.lat;
            const altFt = s2 ? s1.altFt + (s2.altFt - s1.altFt) * tFrac : s1.altFt;
            const climbFpm = s2 ? s1.climbFpm + (s2.climbFpm - s1.climbFpm) * tFrac : s1.climbFpm;
            const altAgl = altFt - groundElevFt;
            const dbSrc = dbFromAltAndClimb(altAgl, climbFpm);
            if (dbSrc < 28) continue;
            const powerSrc = Math.pow(10, dbSrc / 10);
            const pixel = map.getPixelFromCoordinate(fromLonLat([lon, lat]));
            if (!pixel) continue;
            const px = pixel[0] / accScale;
            const py = pixel[1] / accScale;
            if (px < -radiusPxInt || px >= accW + radiusPxInt) continue;
            if (py < -radiusPxInt || py >= accH + radiusPxInt) continue;
            const cx0 = Math.max(0, Math.floor(px - radiusPxInt));
            const cx1 = Math.min(accW, Math.ceil(px + radiusPxInt));
            const cy0 = Math.max(0, Math.floor(py - radiusPxInt));
            const cy1 = Math.min(accH, Math.ceil(py + radiusPxInt));
            const contribCenter = powerSrc * dtPerPoint;
            for (let y = cy0; y < cy1; y += 1) {
              const dy = y - py;
              const dy2 = dy * dy;
              for (let x = cx0; x < cx1; x += 1) {
                const dx = x - px;
                const d2 = dx * dx + dy2;
                if (d2 > radiusPxInt * radiusPxInt) continue;
                const fall = Math.exp(-d2 * inv2Sigma2);
                const idx = y * accW + x;
                sumPower[idx] += contribCenter * fall;
                // Instantaneous dB at this cell = source - attenuation, where
                // the gaussian fall converts to dB attenuation via 10·log10(fall).
                // Track the maximum across all contributing samples = peak.
                const dbAtCell = dbSrc + 10 * Math.log10(fall);
                if (dbAtCell > peakDb[idx]) peakDb[idx] = dbAtCell;
              }
            }
          }
        }
      }

      // Convert accumulated power to aircraft L_eq, then energetically
      // sum with the ambient baseline. 10×aircraft passes → aircraft
      // L_eq +10 dB. Energy-summing with the ambient floor means cells
      // with only quiet cruise aircraft stay near baseline (not 0 dB) and
      // loud climbout corridors push well above baseline.
      const acc = ctx.createImageData(accW, accH);
      const tinyPower = 1; // need at least some aircraft contribution
      for (let i = 0; i < accW * accH; i += 1) {
        const p = sumPower[i];
        if (p < tinyPower) continue;
        const acftLeq = 10 * Math.log10(p / windowSeconds);
        aircraftLeq[i] = acftLeq;
        // Energetic sum of per-second ambient power + aircraft average
        // per-second power → combined L_eq over the window.
        const combinedPowerPerSec = AMBIENT_BASELINE_POWER + p / windowSeconds;
        const combinedLeq = 10 * Math.log10(combinedPowerPerSec);
        if (combinedLeq < 32) continue;
        const [r, g, b] = rgbFromDb(combinedLeq);
        // Alpha tied to how much aircraft pushes above baseline. At baseline
        // (no aircraft), alpha is faint; loud cells solid red.
        const above = Math.max(0, combinedLeq - AMBIENT_BASELINE_DB);
        const alphaT = clamp01(above / 25);
        const alpha = Math.round((0.18 + 0.72 * alphaT) * 255);
        const o = i * 4;
        acc.data[o] = r;
        acc.data[o + 1] = g;
        acc.data[o + 2] = b;
        acc.data[o + 3] = alpha;
      }

      off.width = accW;
      off.height = accH;
      const offCtx = off.getContext("2d");
      if (!offCtx) return;
      offCtx.putImageData(acc, 0, 0);

      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(off, 0, 0, widthCss, heightCss);

      heatmapAccumulatorRef.current = {
        sumPower,
        peakDb,
        aircraftLeq,
        accW,
        accH,
        accScale,
        windowSeconds,
        baselineDb: AMBIENT_BASELINE_DB,
      };
    };

    draw();
    map.on("postrender", draw);
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    return () => {
      map.un("postrender", draw);
      window.removeEventListener("resize", onResize);
    };
  }, [showHeatmap, scanData, groundElevFt, mapReady]);

  // Mouse-over dB readout. Sample the accumulator at the cursor position
  // and surface the weighted-average dB in a floating tooltip.
  useEffect(() => {
    if (!showHeatmap || !mapReady || !mapRef.current) {
      setHoverDb(null);
      return;
    }
    const map = mapRef.current;
    const onMove = (event: { pixel?: number[] | null }) => {
      const acc = heatmapAccumulatorRef.current;
      const pixel = event.pixel;
      if (!acc || !pixel) {
        setHoverDb(null);
        return;
      }
      const x = pixel[0] / acc.accScale;
      const y = pixel[1] / acc.accScale;
      if (x < 0 || x >= acc.accW || y < 0 || y >= acc.accH) {
        setHoverDb(null);
        return;
      }
      const ix = Math.floor(x);
      const iy = Math.floor(y);
      const idx = iy * acc.accW + ix;
      const p = acc.sumPower[idx];
      const peak = acc.peakDb[idx];
      if (p < 1 || peak < 30) {
        setHoverDb(null);
        return;
      }
      const aircraftL = acc.aircraftLeq[idx];
      const combinedLeq = combineDb(aircraftL, acc.baselineDb);
      setHoverDb({
        x: pixel[0],
        y: pixel[1],
        peak,
        aircraftLeq: aircraftL,
        combinedLeq,
        baseline: acc.baselineDb,
      });
    };
    const onOut = () => setHoverDb(null);
    map.on("pointermove", onMove);
    map.getViewport().addEventListener("mouseleave", onOut);
    return () => {
      map.un("pointermove", onMove);
      map.getViewport().removeEventListener("mouseleave", onOut);
    };
  }, [showHeatmap, mapReady]);

  useEffect(() => {
    if (!mapReady || !aircraftSourceRef.current) return;
    const source = aircraftSourceRef.current;
    const existing = aircraftFeaturesRef.current;
    const nextTracks = new globalThis.Map(aircraftTracks.map((track) => [track.icao24, track]));
    aircraftTracksRef.current = nextTracks;

    for (const [icao24, feature] of existing) {
      if (!nextTracks.has(icao24)) {
        source.removeFeature(feature);
        existing.delete(icao24);
      }
    }

    const displayTime = Date.now() / 1000 - AIRCRAFT_DISPLAY_DELAY_SECONDS;
    for (const track of aircraftTracks) {
      const current = positionAt(track.samples, displayTime);
      if (!current) continue;
      let feature = existing.get(track.icao24);
      if (!feature) {
        feature = pointFeature(current.lon, current.lat, { kind: "aircraft" }) as Feature<Point>;
        existing.set(track.icao24, feature);
        source.addFeature(feature);
      }
      feature.setProperties({
        kind: "aircraft",
        icao24: track.icao24,
        label: track.callsign,
        selected: track.selected ? 1 : 0,
        heading: current.heading ?? feature.get("heading") ?? 0
      });
    }
  }, [aircraftTracks, mapReady]);

  useEffect(() => {
    if (!mapReady) return;
    const tick = () => {
      const displayTime = Date.now() / 1000 - AIRCRAFT_DISPLAY_DELAY_SECONDS;
      for (const [icao24, feature] of aircraftFeaturesRef.current) {
        const track = aircraftTracksRef.current.get(icao24);
        if (!track) continue;
        const current = positionAt(track.samples, displayTime);
        if (!current) continue;
        feature.getGeometry()?.setCoordinates(fromLonLat([current.lon, current.lat]));
        feature.set("heading", current.heading ?? feature.get("heading") ?? 0);
        feature.changed();
      }
      frameRef.current = window.requestAnimationFrame(tick);
    };
    frameRef.current = window.requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [mapReady]);

  useEffect(() => {
    if (!autoZoom) return;
    const map = mapRef.current;
    if (!map || !airport || !userLocation) return;
    const extent = activityExtent(airport, userLocation, scanData);
    if (extent && extent.every(Number.isFinite)) {
      map.getView().fit(extent, { padding: [40, 40, 40, 40], maxZoom: 14.5, duration: 450 });
    }
  }, [airport, userLocation, scanData, autoZoom]);

  // Hover tooltips for the Home pin and aircraft markers. Looks up the
  // nearest feature at the cursor and surfaces per-aircraft stats / aggregate
  // home stats. Coexists with the heatmap dB hover above; this handler only
  // fires when a relevant feature is directly under the cursor.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const offendersByIcao = new globalThis.Map<string, Offender>();
    for (const o of scanData?.offenders ?? []) {
      offendersByIcao.set(o.icao24, o);
    }
    const offenders = scanData?.offenders ?? [];
    const totalPasses = scanData?.counters.passes ?? offenders.reduce((sum, o) => sum + (o.passes ?? 0), 0);

    const onMove = (event: { pixel?: number[] | null }) => {
      const pixel = event.pixel;
      if (!pixel) {
        setFeatureHover(null);
        return;
      }
      let target: Feature | null = null;
      map.forEachFeatureAtPixel(
        pixel,
        (feat) => {
          const kind = feat.get("kind");
          if (kind === "home" || kind === "aircraft") {
            target = feat as Feature;
            return true;
          }
          return false;
        },
        { hitTolerance: 6 }
      );
      if (!target) {
        setFeatureHover(null);
        return;
      }
      const targetFeature = target as Feature;
      const kind = targetFeature.get("kind") as string;
      if (kind === "home") {
        let peakDb: number | null = null;
        let lowestOverhead: number | null = null;
        let aircraftWithOverhead = 0;
        for (const o of offenders) {
          const overhead = o.min_altitude_over_user_ft_agl ?? o.avg_altitude_over_user_ft_agl ?? null;
          if (overhead != null && Number.isFinite(overhead)) {
            const db = dbFromAltAndClimb(overhead, null);
            if (peakDb == null || db > peakDb) peakDb = db;
            if (lowestOverhead == null || overhead < lowestOverhead) lowestOverhead = overhead;
            aircraftWithOverhead += 1;
          }
        }
        setFeatureHover({
          kind: "home",
          x: pixel[0],
          y: pixel[1],
          passes: totalPasses,
          aircraftCount: aircraftWithOverhead || offenders.length,
          peakDb,
          lowestOverheadFt: lowestOverhead,
        });
        return;
      }
      const icao24 = (targetFeature.get("icao24") as string | undefined) ?? "";
      const offender = offendersByIcao.get(icao24);
      const callsign = (targetFeature.get("label") as string | undefined) ?? offender?.callsign ?? icao24;
      const origin = offender?.origin_label ?? offender?.origin_airport_icao ?? offender?.origin_city ?? "unknown";
      setFeatureHover({
        kind: "aircraft",
        x: pixel[0],
        y: pixel[1],
        callsign,
        icao24,
        circles: offender?.circles ?? 0,
        touchAndGos: offender?.touch_and_gos ?? 0,
        lowApproaches: offender?.low_approaches ?? 0,
        passes: offender?.passes ?? 0,
        origin,
        avgOverheadFt: offender?.avg_altitude_over_user_ft_agl ?? null,
        minOverheadFt: offender?.min_altitude_over_user_ft_agl ?? null,
      });
    };
    const onOut = () => setFeatureHover(null);
    map.on("pointermove", onMove);
    map.getViewport().addEventListener("mouseleave", onOut);
    return () => {
      map.un("pointermove", onMove);
      map.getViewport().removeEventListener("mouseleave", onOut);
    };
  }, [mapReady, scanData]);

  // Overlay only blocks the map until the first scan response. After that —
  // even if `tracks` is empty — the scan has succeeded and the map should be
  // usable. (Short windows like 5m legitimately come back empty; gating on
  // backfill progress would otherwise leave the overlay up indefinitely.)
  const showLoadingOverlay = !scanData;

  return (
    <div className="map-stage">
      <div className="map openlayers-map" ref={containerRef} />
      <canvas className="heatmap-canvas" ref={heatmapCanvasRef} aria-hidden="true" />
      {showLoadingOverlay && (
        <div className="map-loading-overlay" role="status" aria-live="polite">
          <div className="map-loading-card">
            <div className="map-loading-spinner" aria-hidden="true" />
            <div className="map-loading-text">Loading a bunch of data, hold tight…</div>
          </div>
        </div>
      )}
      {showHeatmap && hoverDb && (
        <div
          className="db-tooltip"
          style={{ left: hoverDb.x + 14, top: hoverDb.y + 14 }}
          role="status"
        >
          <div className="db-tooltip-row">
            <span className="db-tooltip-label">Peak</span>
            <strong>{hoverDb.peak.toFixed(1)} dB</strong>
          </div>
          <div className="db-tooltip-row">
            <span className="db-tooltip-label">Aircraft L_eq</span>
            <strong>{hoverDb.aircraftLeq.toFixed(1)} dB</strong>
          </div>
          <div className="db-tooltip-row">
            <span className="db-tooltip-label">Combined</span>
            <strong>{hoverDb.combinedLeq.toFixed(1)} dB</strong>
          </div>
          <div className="db-tooltip-footnote">
            includes {hoverDb.baseline} dB ambient floor
          </div>
        </div>
      )}
      {showHeatmap && <DbLegend />}
      {contextMenu && (
        <div
          className="map-context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          role="menu"
          onMouseDown={(e) => e.stopPropagation()}
          onContextMenu={(e) => e.preventDefault()}
        >
          <button
            type="button"
            role="menuitem"
            className="map-context-menu-item"
            onClick={() => {
              onPickLocation(contextMenu.lat, contextMenu.lon);
              setContextMenu(null);
            }}
          >
            Set home location here
          </button>
          <div className="map-context-menu-meta">
            {contextMenu.lat.toFixed(4)}°, {contextMenu.lon.toFixed(4)}°
          </div>
        </div>
      )}
      {featureHover && (
        <div
          className="feature-tooltip"
          style={{ left: featureHover.x + 14, top: featureHover.y + 14 }}
          role="status"
        >
          {featureHover.kind === "home" ? (
            <>
              <div className="feature-tooltip-title">Home</div>
              <div className="feature-tooltip-row">
                <span>Passes overhead</span>
                <strong>{featureHover.passes}</strong>
              </div>
              <div className="feature-tooltip-row">
                <span>Aircraft this window</span>
                <strong>{featureHover.aircraftCount}</strong>
              </div>
              {featureHover.peakDb != null && (
                <div className="feature-tooltip-row">
                  <span>Loudest overflight</span>
                  <strong>~{featureHover.peakDb.toFixed(0)} dB</strong>
                </div>
              )}
              {featureHover.lowestOverheadFt != null && (
                <div className="feature-tooltip-row">
                  <span>Lowest over you</span>
                  <strong>{Math.round(featureHover.lowestOverheadFt)} ft AGL</strong>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="feature-tooltip-title">
                {featureHover.callsign}
                <span className="feature-tooltip-sub"> ({featureHover.icao24})</span>
              </div>
              <div className="feature-tooltip-row">
                <span>Origin</span>
                <strong>{featureHover.origin}</strong>
              </div>
              <div className="feature-tooltip-row">
                <span>Circles</span>
                <strong>{featureHover.circles}</strong>
              </div>
              <div className="feature-tooltip-row">
                <span>Touch &amp; gos</span>
                <strong>{featureHover.touchAndGos}</strong>
              </div>
              {featureHover.lowApproaches > 0 && (
                <div className="feature-tooltip-row">
                  <span>Low approaches</span>
                  <strong>{featureHover.lowApproaches}</strong>
                </div>
              )}
              <div className="feature-tooltip-row">
                <span>Passes over you</span>
                <strong>{featureHover.passes}</strong>
              </div>
              {featureHover.minOverheadFt != null && (
                <div className="feature-tooltip-row">
                  <span>Lowest over you</span>
                  <strong>{Math.round(featureHover.minOverheadFt)} ft AGL</strong>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function DbLegend() {
  return (
    <div className="db-legend" role="figure" aria-label="Decibel intensity legend">
      <div className="db-legend-title">Combined dB (aircraft + ambient)</div>
      <div className="db-legend-bar" />
      <div className="db-legend-scale">
        <span>30</span>
        <span>45</span>
        <span>60</span>
        <span>72</span>
        <span>81</span>
        <span>90+</span>
      </div>
      <div className="db-legend-note">
        Aircraft L_eq energetically summed with a 40 dB residential ambient
        floor. Future: per-cell baseline from an OSM road-noise raster.
      </div>
    </div>
  );
}
