import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { LedgerResponse } from "../lib/types";
import { formatDuration, formatInteger, formatShortDate } from "../lib/format";

export function DailyChart({ data }: { data: LedgerResponse }) {
  const { daily, summary, window: ledgerWindow } = data;

  const chartData = daily.map((point) => ({
    ...point,
    label: formatShortDate(point.date),
  }));

  const values = daily.map((d) => d.runway_uses);
  const max = values.length ? Math.max(...values) : 0;
  const zeroDayCount = values.filter((v) => v === 0).length;
  const total = values.reduce((sum, v) => sum + v, 0);
  const dwell = summary.dwell;

  return (
    <section id="daily" className="section-pad chart-section dark-panel">
      <div className="section-heading light">
        <p className="kicker">The Daily Count</p>
        <h2>Every day, counted — including the zero days</h2>
        <p>
          Gap-filled zeroes are real zeroes, not missing data. A day with no bar is a day we detected no runway use.
        </p>
      </div>

      <div className="chart-card">
        <div className="chart-title">
          <span>
            {ledgerWindow.days}-day total, {ledgerWindow.start_day} to {ledgerWindow.end_day}
          </span>
          <b>{formatInteger(total)} runway uses</b>
        </div>
        <div
          role="img"
          aria-label={`Daily runway uses from ${ledgerWindow.start_day} to ${ledgerWindow.end_day}: ${formatInteger(
            total,
          )} total across ${daily.length} days, peaking at ${formatInteger(max)} in a single day; ${zeroDayCount} of ${
            daily.length
          } days recorded zero.`}
          style={{ width: "100%", height: 260 }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 10, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#d9a44133" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: "#cabf9e", fontSize: 12 }}
                axisLine={{ stroke: "#d9a44155" }}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                allowDecimals={false}
                tick={{ fill: "#cabf9e", fontSize: 12 }}
                axisLine={{ stroke: "#d9a44155" }}
                tickLine={false}
                width={36}
              />
              <Tooltip
                cursor={{ fill: "#d9a44122" }}
                contentStyle={{
                  background: "#061c25",
                  border: "1px solid #d9a441",
                  color: "#fff9e8",
                  fontFamily: "Barlow Condensed, sans-serif",
                }}
                labelStyle={{ color: "#f4d37b" }}
                formatter={(value) => [`${formatInteger(Number(value))} runway uses`, ""]}
              />
              <Bar dataKey="runway_uses" fill="#d9a441" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="chart-caption">
          {zeroDayCount} of {daily.length} days in this window show zero detected runway uses. That is what the
          sensors saw — we don't smooth it out.
        </p>
      </div>

      <div className="stat-strip">
        <div>
          <strong>{formatInteger(summary.by_type.landing)}</strong>
          <span>Landings</span>
        </div>
        <div>
          <strong>{formatInteger(summary.by_type.touch_and_go)}</strong>
          <span>Touch-and-goes</span>
        </div>
        <div>
          <strong>{formatInteger(summary.by_type.low_approach)}</strong>
          <span>Low approaches</span>
        </div>
        <div>
          <strong>
            {dwell.median_seconds === null || dwell.sample_size === 0 ? "—" : formatDuration(dwell.median_seconds)}
          </strong>
          <span>
            Median time on field
            {dwell.sample_size > 0 ? ` (n=${formatInteger(dwell.sample_size)})` : " (no data)"}
          </span>
        </div>
      </div>
    </section>
  );
}
