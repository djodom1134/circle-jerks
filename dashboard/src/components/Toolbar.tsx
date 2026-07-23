import { OPERATION_TYPES, LOCALITIES, type OperationType, type Locality } from "../lib/types";
import type { FilterState, ColorBy } from "../lib/facets";
import { TYPE_LABEL, LOCALITY_LABEL } from "../lib/palette";
import { formatMonthLabel } from "../lib/format";

interface ToolbarProps {
  airportLabel: string;
  month: string;
  months: string[];
  onMonthChange(month: string): void;
  filter: FilterState;
  onToggleType(t: OperationType): void;
  onToggleLocality(l: Locality): void;
  colorBy: ColorBy;
  onColorByChange(c: ColorBy): void;
}

export function Toolbar(props: ToolbarProps) {
  const { airportLabel, month, months, onMonthChange, filter, onToggleType, onToggleLocality, colorBy, onColorByChange } = props;
  const idx = months.indexOf(month);
  const prev = idx > 0 ? months[idx - 1] : null;
  const next = idx >= 0 && idx < months.length - 1 ? months[idx + 1] : null;

  return (
    <div className="toolbar" role="region" aria-label="Filters">
      <div className="toolbar-row">
        <span className="airport-label">{airportLabel}</span>
        <span className="month-nav">
          <button type="button" aria-label="Previous month" disabled={!prev} onClick={() => prev && onMonthChange(prev)}>‹</button>
          <b>{formatMonthLabel(month)}</b>
          <button type="button" aria-label="Next month" disabled={!next} onClick={() => next && onMonthChange(next)}>›</button>
        </span>
      </div>
      <div className="toolbar-row">
        <span className="group-label">Type</span>
        {OPERATION_TYPES.map((t) => (
          <button key={t} type="button"
            className={`chip ${filter.types.has(t) ? "on" : ""}`}
            aria-pressed={filter.types.has(t)}
            onClick={() => onToggleType(t)}>{TYPE_LABEL[t]}</button>
        ))}
      </div>
      <div className="toolbar-row">
        <span className="group-label">Who</span>
        {LOCALITIES.map((l) => (
          <button key={l} type="button"
            className={`chip who ${filter.localities.has(l) ? "on" : ""}`}
            aria-pressed={filter.localities.has(l)}
            onClick={() => onToggleLocality(l)}>{LOCALITY_LABEL[l]}</button>
        ))}
        <span className="group-label colorby-label">Colour by</span>
        <span className="seg-toggle" role="group" aria-label="Colour by">
          <button type="button" aria-label="Colour by locality"
            className={colorBy === "locality" ? "act" : ""}
            onClick={() => onColorByChange("locality")}>Locality</button>
          <button type="button" aria-label="Colour by type"
            className={colorBy === "type" ? "act" : ""}
            onClick={() => onColorByChange("type")}>Type</button>
        </span>
      </div>
    </div>
  );
}
