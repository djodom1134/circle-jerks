import { useEffect, useState } from "react";
import {
  getAirportRunways,
  getPatternHistory,
  getPatternTemplate,
  getRunwayPattern,
  revertPattern,
  savePattern,
  type PatternPoint,
  type PatternVersion,
  type RunwayInfo,
} from "../lib/api";
import { getVisitorId } from "../lib/visitor";

interface Props {
  airportIcao: string;
  runwayId: string | null;
  points: PatternPoint[];
  onSelectRunway: (runwayId: string) => void;
  onSeedPoints: (points: PatternPoint[]) => void;
  onClose: () => void;
  onSaved: () => void;
}

export default function PatternEditorPanel({
  airportIcao, runwayId, points, onSelectRunway, onSeedPoints, onClose, onSaved,
}: Props) {
  const [runways, setRunways] = useState<RunwayInfo[]>([]);
  const [history, setHistory] = useState<PatternVersion[]>([]);
  const [side, setSide] = useState<"left" | "right">("left");
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    getAirportRunways(airportIcao).then((r) => setRunways(r.runways)).catch(() => setRunways([]));
  }, [airportIcao]);

  useEffect(() => {
    if (!runwayId) return;
    getRunwayPattern(airportIcao, runwayId)
      .then((r) => { if (r.pattern) onSeedPoints(r.pattern.geometry.points); })
      .catch(() => undefined);
    refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [airportIcao, runwayId]);

  function refreshHistory() {
    if (!runwayId) return;
    getPatternHistory(airportIcao, runwayId).then((r) => setHistory(r.versions)).catch(() => setHistory([]));
  }

  async function handleTemplate() {
    if (!runwayId) return;
    const r = await getPatternTemplate(airportIcao, runwayId, side);
    onSeedPoints(r.geometry.points);
    setStatus("Template loaded — drag points to adjust.");
  }

  async function handleSave() {
    if (!runwayId || points.length < 1) return;
    setStatus("Saving…");
    try {
      await savePattern(airportIcao, runwayId, { points, closed: true, visitor_id: getVisitorId() });
      setStatus("Saved.");
      refreshHistory();
      onSaved();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Save failed.");
    }
  }

  async function handleRevert(version: number) {
    if (!runwayId) return;
    const r = await revertPattern(airportIcao, runwayId, version, getVisitorId());
    if (r.pattern) onSeedPoints(r.pattern.geometry.points);
    setStatus(`Reverted to v${version}.`);
    refreshHistory();
    onSaved();
  }

  return (
    <div className="pattern-editor">
      <div className="pattern-editor-head">
        <strong>Edit VNAP pattern</strong>
        <button className="pattern-editor-close" onClick={onClose} aria-label="Close pattern editor">×</button>
      </div>

      <label className="pattern-editor-row">
        Runway end
        <select value={runwayId ?? ""} onChange={(e) => onSelectRunway(e.target.value)}>
          <option value="" disabled>Select…</option>
          {runways.map((r) => <option key={r.runway_id} value={r.runway_id}>{r.runway_id}</option>)}
        </select>
      </label>

      <div className="pattern-editor-row">
        <label>
          Traffic
          <select value={side} onChange={(e) => setSide(e.target.value as "left" | "right")}>
            <option value="left">Left</option>
            <option value="right">Right</option>
          </select>
        </label>
        <button className="pattern-editor-btn" onClick={handleTemplate} disabled={!runwayId}>Generate from runway</button>
      </div>

      <div className="pattern-editor-meta">{points.length} control point(s). Click the map to add, drag to move.</div>

      <div className="pattern-editor-row">
        <button className="pattern-editor-btn primary" onClick={handleSave} disabled={!runwayId || points.length < 1}>Save pattern</button>
      </div>

      {status && <div className="pattern-editor-status">{status}</div>}

      {history.length > 0 && (
        <div className="pattern-editor-history">
          <div className="pattern-editor-history-title">History</div>
          {history.map((v) => (
            <div key={v.id} className="pattern-editor-history-row">
              <span>v{v.version}{v.is_current ? " (current)" : ""}{v.change_note ? ` — ${v.change_note}` : ""}</span>
              {!v.is_current && <button className="pattern-editor-link" onClick={() => handleRevert(v.version)}>revert</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
