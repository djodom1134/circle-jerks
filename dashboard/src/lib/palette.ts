import { useEffect, useState } from "react";
import type { Locality, OperationType } from "./types";

// Validated against dataviz/scripts/validate_palette.js in light AND dark
// (see the plan's Global Constraints). `unclassified` is a deliberate neutral,
// not a hue — the legend + table view satisfy the relief rule for it.
const LOCALITY_LIGHT: Record<Locality, string> = {
  local: "#2a78d6", out_of_town: "#eb6834", unclassified: "#b9b7ae",
};
const LOCALITY_DARK: Record<Locality, string> = {
  local: "#3987e5", out_of_town: "#d95926", unclassified: "#6b6a64",
};
const TYPE_LIGHT: Record<OperationType, string> = {
  landing: "#2a78d6", touch_and_go: "#eb6834", low_approach: "#1baf7a", takeoff: "#eda100",
};
const TYPE_DARK: Record<OperationType, string> = {
  landing: "#3987e5", touch_and_go: "#d95926", low_approach: "#199e70", takeoff: "#c98500",
};

export const LOCALITY_LABEL: Record<Locality, string> = {
  local: "Local", out_of_town: "Out of town", unclassified: "Unclassified",
};
export const TYPE_LABEL: Record<OperationType, string> = {
  landing: "Landings", touch_and_go: "Touch & go", low_approach: "Low approach", takeoff: "Takeoffs",
};

export function localityColor(l: Locality, dark: boolean): string {
  return (dark ? LOCALITY_DARK : LOCALITY_LIGHT)[l];
}
export function typeColor(t: OperationType, dark: boolean): string {
  return (dark ? TYPE_DARK : TYPE_LIGHT)[t];
}

function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() =>
    typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

export const usePrefersReducedMotion = () => useMediaQuery("(prefers-reduced-motion: reduce)");
export const usePrefersDark = () => useMediaQuery("(prefers-color-scheme: dark)");
