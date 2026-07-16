import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { getBackfillStatus, type BackfillStatus, type WindowCode } from "../lib/api";

interface Props {
  airportIcao: string | null;
  windowCode: WindowCode;
  onShortenWindow: () => void;
}

const POLL_INTERVAL_MS = 2500;
// Only show the banner when the user is asking for a window deeper than what
// live polling has already filled in. For 5m / 30m / 1h the live tier covers
// it within a couple of minutes; the historical wait is wasted UX noise.
const WINDOWS_NEEDING_HISTORY: WindowCode[] = ["6h", "today"];

function fmtRemaining(seconds: number): string {
  if (seconds <= 0) return "almost done";
  if (seconds < 60) return `~${seconds}s remaining`;
  const minutes = Math.ceil(seconds / 60);
  return `~${minutes} min remaining`;
}

export default function BackfillBanner({ airportIcao, windowCode, onShortenWindow }: Props) {
  const [status, setStatus] = useState<BackfillStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    setStatus(null);
    setDismissed(false);
  }, [airportIcao]);

  useEffect(() => {
    if (!airportIcao) return undefined;
    let cancelled = false;

    async function poll() {
      try {
        const next = await getBackfillStatus(airportIcao!);
        if (!cancelled) setStatus(next);
      } catch {
        // Soft-fail — banner just won't appear.
      }
    }

    poll();
    const id = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [airportIcao]);

  if (!airportIcao || dismissed) return null;
  if (!status?.running) return null;
  if (!WINDOWS_NEEDING_HISTORY.includes(windowCode)) return null;

  const eta = status.eta_seconds ?? 0;
  const total = status.estimated_total_seconds ?? 60;
  const elapsed = status.elapsed_seconds ?? 0;
  const pct = Math.min(99, Math.max(2, Math.round((elapsed / total) * 100)));

  return (
    <div className="backfill-banner" role="status" aria-live="polite">
      <div className="backfill-banner__head">
        <Loader2 size={16} className="backfill-banner__spinner" aria-hidden="true" />
        <span className="backfill-banner__title">
          Filling in historical tracks for {status.airport_icao} — {fmtRemaining(eta)}
        </span>
        <button
          type="button"
          className="backfill-banner__close"
          onClick={() => setDismissed(true)}
          title="Hide this banner"
          aria-label="Hide backfill banner"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <div className="backfill-banner__bar" aria-hidden="true">
        <div className="backfill-banner__bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="backfill-banner__actions">
        <button
          type="button"
          className="backfill-banner__shortcut"
          onClick={onShortenWindow}
        >
          Show last hour now
        </button>
        <span className="backfill-banner__hint">
          (live data is already ready)
        </span>
      </div>
    </div>
  );
}
