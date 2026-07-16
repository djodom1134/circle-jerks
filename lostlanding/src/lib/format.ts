const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const compactFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});

const integerFormatter = new Intl.NumberFormat("en-US");

export function formatCurrency(n: number): string {
  return currencyFormatter.format(n);
}

export function formatCompactCurrency(n: number): string {
  return `$${compactFormatter.format(n)}`;
}

export function formatInteger(n: number): string {
  return integerFormatter.format(Math.round(n));
}

export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/** Render a median-seconds dwell figure as "4m 12s" / "38s" / etc. */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.round(totalSeconds);
  const minutes = Math.floor(seconds / 60);
  const remainderSeconds = seconds % 60;
  if (minutes <= 0) return `${remainderSeconds}s`;
  return `${minutes}m ${remainderSeconds}s`;
}

/** Format a YYYY-MM-DD date string as "Jul 13" without timezone shifting. */
export function formatShortDate(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(year, (month ?? 1) - 1, day ?? 1));
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(date);
}

/** Format a unix-seconds timestamp as a plain date, or null-safe fallback. */
export function formatUnixDate(unixSeconds: number | null): string | null {
  if (unixSeconds === null || unixSeconds === undefined) return null;
  return new Intl.DateTimeFormat("en-US", { year: "numeric", month: "long", day: "numeric" }).format(
    new Date(unixSeconds * 1000),
  );
}
