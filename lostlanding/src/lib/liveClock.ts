/**
 * The hero ticker's "debt clock" state machine, isolated from React/timers so
 * it can be unit tested directly.
 *
 * The clock does not display dollars -- it tracks an ESTIMATED runway-use
 * COUNT (a continuous, fractional real number) which the caller multiplies by
 * the (live, slider-controlled) fee at render time. Tracking count instead of
 * dollars is what makes a fee-slider change apply instantly with zero special
 * casing: the count keeps ticking independent of fee, and `estimatedUses *
 * fee` is recomputed fresh every render.
 *
 * Ticking between polls is an EXTRAPOLATION, not a fact -- see
 * ledger-api/app/fees.py's `rate_window` for where the slope comes from. The
 * counted `today.runway_uses` from each poll is the source of truth, and
 * every poll reconciles the estimate back to it:
 *   - If truth has caught up to (or passed) our estimate, ease UP to it over
 *     EASE_DURATION_MS -- never an instant jump.
 *   - If our estimate has drifted AHEAD of truth (the recent-rate
 *     extrapolation over-predicted), freeze in place rather than snapping
 *     backwards; a later poll whose truth catches up resumes ticking.
 */

export interface ClockState {
  estimatedUses: number;
  frozen: boolean;
  easeFrom: number | null;
  easeTo: number | null;
  easeStartMs: number | null;
}

export const EASE_DURATION_MS = 400;

export function initClockState(trueUses: number): ClockState {
  return { estimatedUses: trueUses, frozen: false, easeFrom: null, easeTo: null, easeStartMs: null };
}

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

/**
 * Advance the clock by one tick. `usesPerSecond` is the current tick rate
 * (ledger-api's `rate_window.uses_per_second`); `tickSeconds` is how much
 * time this tick covers (e.g. 0.1 for a 100ms/~10fps tick).
 */
export function advanceClock(
  state: ClockState,
  usesPerSecond: number,
  tickSeconds: number,
  nowMs: number,
): ClockState {
  if (state.easeTo !== null && state.easeFrom !== null && state.easeStartMs !== null) {
    const elapsed = nowMs - state.easeStartMs;
    const t = Math.min(1, elapsed / EASE_DURATION_MS);
    const value = state.easeFrom + (state.easeTo - state.easeFrom) * easeOutCubic(t);
    if (t >= 1) {
      return { estimatedUses: state.easeTo, frozen: false, easeFrom: null, easeTo: null, easeStartMs: null };
    }
    return { ...state, estimatedUses: value };
  }
  if (state.frozen) return state;
  if (usesPerSecond <= 0) return state; // no traffic in the rate window -- the clock stands still
  return { ...state, estimatedUses: state.estimatedUses + usesPerSecond * tickSeconds };
}

/**
 * Reconcile the clock to a freshly-polled true count. Called once per poll
 * (~15-30s), never per tick.
 */
export function reconcileClock(
  state: ClockState,
  trueUses: number,
  nowMs: number,
  reducedMotion: boolean,
): ClockState {
  if (reducedMotion) {
    return initClockState(trueUses);
  }
  if (trueUses >= state.estimatedUses) {
    return { estimatedUses: state.estimatedUses, frozen: false, easeFrom: state.estimatedUses, easeTo: trueUses, easeStartMs: nowMs };
  }
  // Our estimate ran ahead of the real count -- hold flat, do not snap back.
  return { estimatedUses: state.estimatedUses, frozen: true, easeFrom: null, easeTo: null, easeStartMs: null };
}
