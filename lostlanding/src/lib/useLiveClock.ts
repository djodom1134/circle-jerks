/**
 * Wires liveClock.ts's pure state machine to a real ~10fps interval,
 * document-visibility pausing, and prefers-reduced-motion. See liveClock.ts
 * for the reconciliation rules this hook is just a timer/DOM harness around.
 */
import { useEffect, useRef, useState } from "react";
import { advanceClock, initClockState, reconcileClock, type ClockState } from "./liveClock";

const TICK_MS = 100; // ~10fps, per spec

export function usePrefersReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const onChange = () => setReduced(mql.matches);
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

/**
 * `trueUses` is the latest polled rolling-24h count (null while nothing has
 * loaded yet); `usesPerSecond` is the latest `rate_window.uses_per_second`.
 * Returns the current ESTIMATED runway-use count -- multiply by the live fee
 * at render time for a dollar figure; do not bake fee into this hook, so a
 * fee-slider change applies with zero extra plumbing.
 */
export function useLiveClock(trueUses: number | null, usesPerSecond: number): number {
  const reducedMotion = usePrefersReducedMotion();
  const [state, setState] = useState<ClockState>(() => initClockState(trueUses ?? 0));
  const usesPerSecondRef = useRef(usesPerSecond);
  usesPerSecondRef.current = usesPerSecond;
  const lastTrueUsesRef = useRef<number | null>(null);

  // Reconcile whenever a fresh true count arrives from a poll.
  useEffect(() => {
    if (trueUses === null) return;
    if (lastTrueUsesRef.current === null) {
      // First real value this session: snap directly rather than easing up
      // from the meaningless placeholder of 0.
      lastTrueUsesRef.current = trueUses;
      setState(initClockState(trueUses));
      return;
    }
    if (lastTrueUsesRef.current === trueUses) return; // unchanged this poll
    lastTrueUsesRef.current = trueUses;
    setState((prev) => reconcileClock(prev, trueUses, performance.now(), reducedMotion));
  }, [trueUses, reducedMotion]);

  // The ~10fps tick loop. Skipped entirely under reduced motion; paused
  // while the tab is hidden and NOT caught up on return -- the next poll's
  // reconcile is what corrects any drift, never a burst of replayed ticks.
  useEffect(() => {
    if (reducedMotion) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      setState((prev) => advanceClock(prev, usesPerSecondRef.current, TICK_MS / 1000, performance.now()));
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [reducedMotion]);

  return state.estimatedUses;
}
