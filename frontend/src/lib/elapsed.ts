/** Human-readable "time since" for a unix timestamp (seconds).
 *
 *  Rendering everything in minutes made a 13.8h-old runway change read as
 *  "826m ago", which looks like a stuck clock rather than a quiet day.
 */
export function elapsedLabel(ts: number, nowSeconds: number = Date.now() / 1000): string {
  const total = Math.max(0, Math.round(nowSeconds - ts));
  if (total < 60) return "just now";

  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rem = minutes % 60;
    return rem ? `${hours}h ${rem}m ago` : `${hours}h ago`;
  }

  const days = Math.floor(hours / 24);
  const rem = hours % 24;
  return rem ? `${days}d ${rem}h ago` : `${days}d ago`;
}
