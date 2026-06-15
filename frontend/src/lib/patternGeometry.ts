import { fromLonLat } from "ol/proj";
import { smoothSegment } from "./spline";

export interface LonLat {
  lat: number;
  lon: number;
}

/** Project control points to map coords and Catmull-Rom smooth them.
 *  When closed, the ring is wrapped back to its first point before smoothing. */
export function projectedPath(points: LonLat[], closed: boolean): number[][] {
  const coords = points.map((p) => fromLonLat([p.lon, p.lat]));
  if (closed && coords.length > 2) {
    coords.push(coords[0]);
  }
  return smoothSegment(coords);
}

export interface DirectionArrow {
  coord: number[];
  rotation: number; // radians, clockwise from north (matches ol RegularShape.rotation)
}

/** Evenly place `count` direction arrows along a projected path. */
export function directionArrows(path: number[][], count = 3): DirectionArrow[] {
  if (path.length < 2 || count < 1) return [];
  const arrows: DirectionArrow[] = [];
  for (let i = 1; i <= count; i += 1) {
    const frac = i / (count + 1);
    const idx = Math.min(path.length - 1, Math.max(1, Math.round(frac * (path.length - 1))));
    const [x1, y1] = path[idx - 1];
    const [x2, y2] = path[idx];
    const dx = x2 - x1;
    const dy = y2 - y1;
    // ol RegularShape rotation is clockwise from up (north); atan2(dx, dy) gives that.
    const rotation = Math.atan2(dx, dy);
    arrows.push({ coord: [x2, y2], rotation });
  }
  return arrows;
}
