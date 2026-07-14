import { useCallback, useEffect, useState } from "react";
import { fetchLedger } from "./lib/api";
import type { LedgerResponse } from "./lib/types";
import { FeeProvider } from "./lib/feeContext";
import { AircraftFeesProvider } from "./lib/aircraftFeesContext";
import { Header } from "./components/Header";
import { Footer } from "./components/Footer";
import { LoadingState } from "./components/LoadingState";
import { ErrorState } from "./components/ErrorState";
import { LedgerPage } from "./components/LedgerPage";

const AIRPORT_ICAO = "KLMO";
const WINDOW_DAYS = 30;

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: LedgerResponse };

export default function App() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(() => {
    setState({ status: "loading" });
    let cancelled = false;

    fetchLedger(AIRPORT_ICAO, WINDOW_DAYS)
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: err instanceof Error ? err.message : "Unknown error loading the ledger.",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load, attempt]);

  return (
    <FeeProvider>
      <AircraftFeesProvider icao={AIRPORT_ICAO}>
        <div className="app">
          <a className="skip-link" href="#top">
            Skip to main content
          </a>
          <div className="sunburst" aria-hidden="true" />
          <Header />
          {state.status === "loading" && <LoadingState />}
          {state.status === "error" && (
            <ErrorState message={state.message} onRetry={() => setAttempt((n) => n + 1)} />
          )}
          {state.status === "ready" && <LedgerPage data={state.data} />}
          <Footer />
        </div>
      </AircraftFeesProvider>
    </FeeProvider>
  );
}
