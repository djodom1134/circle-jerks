export function formatLocalTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true });
}

export function formatDateTime(ts?: number | null) {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true
  });
}

export function titleize(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function numberOrDash(value: number | null | undefined, suffix = "") {
  return value === null || value === undefined ? "-" : `${value}${suffix}`;
}
