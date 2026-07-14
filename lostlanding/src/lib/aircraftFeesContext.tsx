/**
 * Polls GET /airports/{icao}/aircraft-fees (ledger-api/app/fees.py) on a
 * shared interval and exposes the result to both the live map (price tags +
 * hover breakdown) and the hero ticker (the debt clock) -- one poll loop, one
 * source of truth, instead of two components independently hammering the
 * same endpoint on their own clocks.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { fetchAircraftFees } from "./api";
import type { AircraftFeesResponse } from "./liveTypes";

// "every 15-30s" per spec; 20s splits the difference.
const DEFAULT_POLL_INTERVAL_MS = 20_000;

interface AircraftFeesContextValue {
  data: AircraftFeesResponse | null;
  error: string | null;
  loading: boolean;
}

const AircraftFeesContext = createContext<AircraftFeesContextValue | null>(null);

export function AircraftFeesProvider({
  icao,
  children,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}: {
  icao: string;
  children: ReactNode;
  pollIntervalMs?: number;
}) {
  const [data, setData] = useState<AircraftFeesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = () => {
      fetchAircraftFees(icao)
        .then((next) => {
          if (cancelled) return;
          setData(next);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "Could not load aircraft fees.");
        })
        .finally(() => {
          if (cancelled) return;
          setLoading(false);
          // Recursive setTimeout (not setInterval): never schedules the next
          // poll until this one has finished, so a slow/hanging request can't
          // pile up overlapping requests.
          timer = setTimeout(poll, pollIntervalMs);
        });
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [icao, pollIntervalMs]);

  return <AircraftFeesContext.Provider value={{ data, error, loading }}>{children}</AircraftFeesContext.Provider>;
}

export function useAircraftFees(): AircraftFeesContextValue {
  const ctx = useContext(AircraftFeesContext);
  if (!ctx) {
    throw new Error("useAircraftFees() must be called within an <AircraftFeesProvider>");
  }
  return ctx;
}
