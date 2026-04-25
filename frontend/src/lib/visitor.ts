const VISITOR_ID_KEY = "circlejerk_visitor_id";

function fallbackId() {
  return `visitor-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function getVisitorId() {
  try {
    const existing = window.localStorage.getItem(VISITOR_ID_KEY);
    if (existing) return existing;
    const id = window.crypto?.randomUUID?.() ?? fallbackId();
    window.localStorage.setItem(VISITOR_ID_KEY, id);
    return id;
  } catch {
    return fallbackId();
  }
}
