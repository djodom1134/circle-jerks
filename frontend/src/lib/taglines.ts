export function buildTaglines(circlesToday: number | null): string[] {
  const base = [
    "Pattern training loops give us no local benefit, only noise.",
    "Imagine a biker gang circling your block around your house — would that annoy you?",
    "Monitoring 16,000+ airports for abuse.",
    "Small engines, big egos. The 0.0001% who own the sky and 80% of the noise.",
    "Your quiet afternoon, their practice runway.",
  ];
  if (circlesToday && circlesToday > 0) {
    base.push(`${circlesToday} training loops logged today — when you can't hold a conversation outdoors anymore.`);
    base.push(`${circlesToday} circles logged overhead today, and not one of them landing for good.`);
  }
  return base;
}

export function pickTagline(list: string[], rand: number = Math.random()): string {
  if (list.length === 0) return "";
  return list[Math.min(list.length - 1, Math.floor(rand * list.length))];
}
