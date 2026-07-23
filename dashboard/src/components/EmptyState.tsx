import { formatMonthLabel } from "../lib/format";

export function EmptyState({ month }: { month: string }) {
  return (
    <div className="state" role="status">
      <p>No data yet for {formatMonthLabel(month)}.</p>
      <p className="detail">This period is outside the data we currently hold for this airport.</p>
    </div>
  );
}
