import { useEffect, useMemo, useRef, useState } from "react";
import Feature from "ol/Feature";
import Map from "ol/Map";
import View from "ol/View";
import { boundingExtent, buffer as bufferExtent } from "ol/extent";
import LineString from "ol/geom/LineString";
import Point from "ol/geom/Point";
import Polygon from "ol/geom/Polygon";
import { defaults as defaultInteractions } from "ol/interaction/defaults";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import { fromLonLat, toLonLat } from "ol/proj";
import OSM from "ol/source/OSM";
import VectorSource from "ol/source/Vector";
import { Circle as CircleStyle, Fill, RegularShape, Stroke, Style, Text } from "ol/style";
import type { Airport, ScanResponse, TrackSample } from "../lib/api";

// dB at observer for a single aircraft passage given altitude AGL.
// Anchor: 900 ft AGL → 65 dB (midpoint of the user-supplied 60-70 dB range).
// Inverse-square law applied: each doubling of distance ≈ -6 dB.
function dbFromAltitudeAgl(altitudeAgl: number | null | undefined): number {
  if (altitudeAgl == null || !Number.isFinite(altitudeAgl) || altitudeAgl <= 0) return 30;
  const ratio = 900 / altitudeAgl;
  const db = 65 + 20 * Math.log10(ratio);
  if (db < 30) return 30;
  if (db > 100) return 100;
  return db;
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

function clamp01(value: number): number {
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

const AIRCRAFT_DISPLAY_DELAY_SECONDS = 15;
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

function activityExtent(
  airport?: Airport | null,
  userLocation?: { lat: number; lon: number } | null,
  scanData?: ScanResponse | null
) {
  const coords: number[][] = [];
  if (airport) {
    coords.push(fromLonLat([airport.lon, airport.lat]));
  }
  if (userLocation) {
    coords.push(fromLonLat([userLocation.lon, userLocation.lat]));
  }

  for (const track of scanData?.tracks ?? []) {
    const inWindow = track.samples.filter((sample) => sample.in_window);
    const samples = inWindow.length > 0 ? inWindow : track.samples;
    for (const sample of samples) {
      if (Number.isFinite(sample.lon) && Number.isFinite(sample.lat)) {
        coords.push(fromLonLat([sample.lon, sample.lat]));
      }
    }
  }

  if (coords.length === 0) {
    return null;
  }
  const extent = boundingExtent(coords);
  const width = extent[2] - extent[0];
  const height = extent[3] - extent[1];
  return bufferExtent(extent, Math.max(width, height) * 0.08 || 1400);
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

// Centripetal Catmull-Rom spline through the sample points: each pair of
// consecutive samples gets `steps` interpolated points so polygonal tracks
// from sparse ADS-B data render as smooth curves. The curve PASSES THROUGH
// each real sample — we don't fabricate position data, just smooth the
// interpolation between known points.
function smoothSegment(points: number[][], steps = 12): number[][] {
  if (points.length < 3) return points;
  const out: number[][] = [points[0]];
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? points[i + 1];
    for (let s = 1; s <= steps; s += 1) {
      const t = s / steps;
      const t2 = t * t;
      const t3 = t2 * t;
      const x =
        0.5 *
        (2 * p1[0] +
          (-p0[0] + p2[0]) * t +
          (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
          (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3);
      const y =
        0.5 *
        (2 * p1[1] +
          (-p0[1] + p2[1]) * t +
          (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
          (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3);
      out.push([x, y]);
    }
  }
  return out;
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

export default function MapView({ airport, userLocation, scanData, selectedIcao24, autoZoom = true, showHeatmap = false, onPickLocation }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const sourceRef = useRef<VectorSource | null>(null);
  const aircraftSourceRef = useRef<VectorSource | null>(null);
  const aircraftFeaturesRef = useRef<globalThis.Map<string, Feature<Point>>>(new globalThis.Map());
  const aircraftTracksRef = useRef<globalThis.Map<string, AircraftTrack>>(new globalThis.Map());
  const heatmapCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef<number | null>(null);
  const [mapReady, setMapReady] = useState(false);

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
    return (scanData?.tracks ?? [])
      .map((track) => ({
        icao24: track.icao24,
        callsign: track.callsign,
        selected: selectedIcao24 === track.icao24,
        samples: normalizeTrackSamples(track.samples)
      }))
      .filter((track) => track.samples.length > 0);
  }, [scanData, selectedIcao24, showHeatmap]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    sourceRef.current = new VectorSource();
    aircraftSourceRef.current = new VectorSource();
    const vectorLayer = new VectorLayer({
      source: sourceRef.current,
      style: (feature) => styleForFeature(feature as Feature)
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
        aircraftLayer
      ],
      view: new View({
        center: fromLonLat([userLocation?.lon ?? airport?.lon ?? -105.1172, userLocation?.lat ?? airport?.lat ?? 39.9088]),
        zoom: 10
      })
    });
    mapRef.current.on("click", (event) => {
      const [lon, lat] = toLonLat(event.coordinate);
      onPickLocation(lat, lon);
    });
    setMapReady(true);
  }, [airport?.lat, airport?.lon, userLocation?.lat, userLocation?.lon, onPickLocation]);

  useEffect(() => {
    const source = sourceRef.current;
    if (!source) return;
    source.clear();
    source.addFeatures(features);
  }, [features]);

  // Deferred Heatmap layer init: putting HeatmapLayer in the initial
  // new Map({ layers: [...] }) array silently breaks OL 10.9's renderer and
  // produces zero canvases. Adding it via addLayer() AFTER the map is alive
  // works, but only if we never call setVisible(true/false) — that flip
  // seems to occlude the basemap on first show. Workaround: leave the layer
  // always visible and rely on an empty source rendering nothing.
  // Heatmap rendered as a separate canvas overlay rather than via OL's
  // HeatmapLayer (which wipes the basemap in 10.9) or discrete disc features
  // (which the user correctly called out as not a real heatmap). Drawing
  // radial gradients with `globalCompositeOperation: "lighter"` produces
  // true additive blending so dense areas saturate to bright red.
  // Blob size is in METERS, not pixels, so the gradient gets more granular as
  // you zoom in (the same 80 m audible footprint covers more pixels).
  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const canvas = heatmapCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

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
      if (!showHeatmap) return;

      const resolution = map.getView().getResolution() ?? 1;
      // 90 m noise footprint per sample — roughly the area within which a low
      // GA aircraft is the dominant ambient noise source.
      const blobMeters = 90;
      const blobPx = Math.max(6, blobMeters / resolution);

      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      const tracks = scanData?.tracks ?? [];
      for (let ti = 0; ti < tracks.length; ti += 1) {
        const samples = tracks[ti].samples;
        for (let si = 0; si < samples.length; si += 1) {
          const sample = samples[si];
          if (!sample.in_window) continue;
          const altFt = (sample as TrackSample).altitude_ft;
          const altAgl = altFt != null ? altFt - groundElevFt : null;
          const db = dbFromAltitudeAgl(altAgl);
          if (db < 35) continue;
          const pixel = map.getPixelFromCoordinate(fromLonLat([sample.lon, sample.lat]));
          if (!pixel) continue;
          const [r, g, b] = rgbFromDb(db);
          const intensity = clamp01((db - 30) / 60);
          const peakAlpha = 0.10 + 0.35 * intensity;
          const gradient = ctx.createRadialGradient(
            pixel[0], pixel[1], 0,
            pixel[0], pixel[1], blobPx
          );
          gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${peakAlpha.toFixed(3)})`);
          gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${(peakAlpha * 0.4).toFixed(3)})`);
          gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
          ctx.fillStyle = gradient;
          ctx.beginPath();
          ctx.arc(pixel[0], pixel[1], blobPx, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();
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
      const current = positionAt(track.samples, displayTime) ?? positionAt(track.samples, Number.MAX_SAFE_INTEGER);
      if (!current) continue;
      let feature = existing.get(track.icao24);
      if (!feature) {
        feature = pointFeature(current.lon, current.lat, { kind: "aircraft" }) as Feature<Point>;
        existing.set(track.icao24, feature);
        source.addFeature(feature);
      }
      feature.setProperties({
        kind: "aircraft",
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
      map.getView().fit(extent, { padding: [60, 60, 60, 60], maxZoom: 12, duration: 450 });
    }
  }, [airport, userLocation, scanData, autoZoom]);

  return (
    <div className="map-stage">
      <div className="map openlayers-map" ref={containerRef} />
      <canvas className="heatmap-canvas" ref={heatmapCanvasRef} aria-hidden="true" />
      {showHeatmap && <DbLegend />}
    </div>
  );
}

function DbLegend() {
  return (
    <div className="db-legend" role="figure" aria-label="Decibel intensity legend">
      <div className="db-legend-title">Avg dB (estimated)</div>
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
        65 dB at 900 ft AGL, scaled by inverse-square law per sample.
      </div>
    </div>
  );
}
