// Adaptive render clock for the live aircraft map.
//
// The map draws aircraft slightly in the PAST so the render clock has samples on
// both sides to interpolate between (smooth 60fps motion instead of teleporting
// to each freshly-ingested sample). Getting the *delay* right is the whole game:
//
//   - Too small a delay (the bug this replaced: a fixed 15s) puts the render
//     clock at/past each plane's newest sample. Real per-plane ADS-B lag swings
//     with coverage gaps — a feeder does not see every aircraft on every poll —
//     and routinely reaches 25-35s. The moment lag exceeds delay + extrapolation
//     the marker clamps, and the whole map silently freezes though ingestion is
//     healthy.
//
//   - A clock *pinned* to the freshest sample (displayTime = fleetNewest - k)
//     also freezes: fleetNewest only advances when new data lands (~every 10s),
//     so between backend updates the clock stops and planes step rather than
//     glide.
//
// The fix is an ADVANCING clock: displayTime = now - delay, so it ticks every
// frame, while `delay` self-tunes to the observed ingest lag. We size `delay` to
// a slowly-decaying peak of the lag so it always sits behind the freshest
// sample (never clamping the freshest plane) and stays stable between updates
// (so the clock glides). Extrapolation covers planes a little staler than the
// peak across the inter-update gap. Latency stays low when data is fresh and
// grows only as far as stale data forces — and it can never re-freeze the way a
// hard-coded delay did.

export interface RenderClockConfig {
  /** Floor on the delay — the best-case latency when data is very fresh. */
  minDelaySeconds: number;
  /** Cap on the delay — bounds worst-case latency; planes staler than this clamp. */
  maxDelaySeconds: number;
  /** Keep the clock at least this far behind the freshest sample (interpolation runway). */
  marginSeconds: number;
  /** How fast the lag-peak estimate forgets an old high, so latency recovers. */
  peakDecayPerSecond: number;
}

export const DEFAULT_RENDER_CLOCK_CONFIG: RenderClockConfig = {
  minDelaySeconds: 8,
  maxDelaySeconds: 45,
  marginSeconds: 6,
  peakDecayPerSecond: 0.4,
};

/**
 * Newest sample timestamp (epoch seconds) across the given tracks, or 0 when
 * there are none. This is the freshness signal fed to {@link RenderClock.onData}.
 */
export function fleetNewestTimestamp(
  tracks: { samples: { timestamp: number }[] }[],
): number {
  let newest = 0;
  for (const track of tracks) {
    for (const sample of track.samples) {
      if (sample.timestamp > newest) newest = sample.timestamp;
    }
  }
  return newest;
}

export interface RenderClock {
  /** Feed a fresh batch: `fleetNewest` = newest live sample ts, `nowSeconds` = wall clock. */
  onData(fleetNewest: number, nowSeconds: number): void;
  /** Render time for wall-clock `nowSeconds`; advances between onData calls. */
  displayTime(nowSeconds: number): number;
  /** Current delay in seconds (exposed for tests / telemetry). */
  readonly delaySeconds: number;
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

/**
 * Create a stateful adaptive render clock. Hold one instance for the lifetime of
 * the map and share it between the marker-placement effect and the per-frame
 * animation tick so they agree on the delay.
 */
export function createRenderClock(config: RenderClockConfig = DEFAULT_RENDER_CLOCK_CONFIG): RenderClock {
  let peakLag = 0;
  let delay = config.minDelaySeconds;
  let lastNow: number | null = null;
  let primed = false;

  return {
    onData(fleetNewest: number, nowSeconds: number): void {
      const lag = fleetNewest > 0 ? Math.max(0, nowSeconds - fleetNewest) : 0;
      const elapsed = lastNow == null ? 0 : Math.max(0, nowSeconds - lastNow);
      lastNow = nowSeconds;
      // Decaying peak: jump up instantly to a new high (so we never clamp the
      // freshest plane), forget old highs slowly (so latency recovers once
      // ingestion catches up).
      peakLag = primed
        ? Math.max(lag, peakLag - config.peakDecayPerSecond * elapsed)
        : lag;
      primed = true;
      delay = clamp(peakLag + config.marginSeconds, config.minDelaySeconds, config.maxDelaySeconds);
    },
    displayTime(nowSeconds: number): number {
      return nowSeconds - delay;
    },
    get delaySeconds(): number {
      return delay;
    },
  };
}
