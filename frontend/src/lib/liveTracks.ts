import type { Track } from "./api";

/**
 * Merge fast live positions into the authoritative full-history tracks.
 *
 * The map is fed two sources: `base` = the (slower, ~5-30s) /scan tracks, which
 * carry the full historical trail (offender paths pulled from the SQLite archive
 * over the whole window); and `live` = the (fast, ~2.5s) /positions poll, which
 * carries only the recent hot-buffer samples. Replacing base with live drops the
 * history (it flashes in on the first scan, then vanishes when the first live
 * poll lands) and truncates trails. Instead, keep every base track and append
 * only the live samples newer than that track's newest sample — so historical
 * trails persist and live planes keep drawing their tail between scan refreshes.
 * Live aircraft the scan hasn't included yet are added as-is.
 */
export function mergeLiveTracks(base: Track[], live: Track[]): Track[] {
  const byIcao = new Map<string, Track>();
  for (const t of base) byIcao.set(t.icao24.toLowerCase(), t);
  for (const lt of live) {
    const key = lt.icao24.toLowerCase();
    const bt = byIcao.get(key);
    if (!bt) {
      byIcao.set(key, lt);
      continue;
    }
    const lastTs = bt.samples.reduce((max, s) => Math.max(max, s.timestamp), -Infinity);
    const fresher = lt.samples
      .filter((s) => s.timestamp > lastTs)
      .sort((a, b) => a.timestamp - b.timestamp);
    if (fresher.length > 0) {
      byIcao.set(key, {
        ...bt,
        callsign: lt.callsign || bt.callsign,
        samples: [...bt.samples, ...fresher],
      });
    }
  }
  return Array.from(byIcao.values());
}
