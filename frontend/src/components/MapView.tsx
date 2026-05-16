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

interface Props {
  airport?: Airport | null;
  userLocation: { lat: number; lon: number } | null;
  scanData?: ScanResponse | null;
  selectedIcao24?: string | null;
  autoZoom?: boolean;
  onPickLocation: (lat: number, lon: number) => void;
}

const AIRCRAFT_DISPLAY_DELAY_SECONDS = 35;
const AIRCRAFT_EXTRAPOLATE_SECONDS = 18;

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
      segments.push(current);
      current = [];
    } else {
      current = [];
    }
  }
  if (current.length > 1) {
    segments.push(current);
  }
  return segments;
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
    return new Style({
      stroke: new Stroke({
        color: selected ? "#d64b2c" : "#1b3a6b",
        width: selected ? 5 : 3
      })
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

export default function MapView({ airport, userLocation, scanData, selectedIcao24, autoZoom = true, onPickLocation }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const sourceRef = useRef<VectorSource | null>(null);
  const aircraftSourceRef = useRef<VectorSource | null>(null);
  const aircraftFeaturesRef = useRef<globalThis.Map<string, Feature<Point>>>(new globalThis.Map());
  const aircraftTracksRef = useRef<globalThis.Map<string, AircraftTrack>>(new globalThis.Map());
  const frameRef = useRef<number | null>(null);
  const [mapReady, setMapReady] = useState(false);

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
    for (const track of scanData?.tracks ?? []) {
      const isSelected = selectedIcao24 === track.icao24;
      for (const coords of splitSegments(track.samples, false)) {
        rows.push(lineFeature(coords, { kind: "track_context", selected: isSelected ? 1 : 0 }));
      }
      for (const coords of splitSegments(track.samples, true)) {
        rows.push(lineFeature(coords, { kind: "track_active", selected: isSelected ? 1 : 0 }));
      }
      track.samples.forEach((sample, index) => {
        if (!sample.in_window) return;
        if (!isSelected && index % 2 === 1) return;
        rows.push(pointFeature(sample.lon, sample.lat, {
          kind: "track_sample",
          selected: isSelected ? 1 : 0
        }));
      });
    }
    return rows;
  }, [airport, userLocation, scanData, selectedIcao24]);

  const aircraftTracks = useMemo<AircraftTrack[]>(() => {
    return (scanData?.tracks ?? [])
      .map((track) => ({
        icao24: track.icao24,
        callsign: track.callsign,
        selected: selectedIcao24 === track.icao24,
        samples: normalizeTrackSamples(track.samples)
      }))
      .filter((track) => track.samples.length > 0);
  }, [scanData, selectedIcao24]);

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

  return <div className="map openlayers-map" ref={containerRef} />;
}
