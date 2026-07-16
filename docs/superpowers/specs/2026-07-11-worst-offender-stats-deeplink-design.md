# Worst-offender → Stats page deep-link highlight

**Date:** 2026-07-11
**Status:** approved (design)

## Problem

The main page's bottom **"Worst offenders"** leaderboard (`RepeatOffendersSection`,
ranked all-time by total circles) has clickable cards, but clicking one only
scrolls down to the complaint panel — and only if that aircraft happens to be in
the current scan window. There's no path from "biggest jerk in the area" to the
richer per-aircraft data.

**Goal:** clicking a worst-offender card navigates to the Stats page and
highlights that tail in the **"Aircraft VNAP compliance"** table
(`AircraftDashboard`), which also drives the "vs. average" radar.

## Approach

A cross-page deep-link via URL params. Routing is pathname-based (no SPA router;
`App.tsx` dispatches on `window.location.pathname`), so navigation is a full-page
load and the highlight target is carried in the query string.

**Flow:** card click → `/stats?airport=<icao>&aircraft=<icao24>&win=all` →
`StatsPage` loads the all-time window → `AircraftDashboard` pre-selects the tail's
row (highlight + radar), scrolls it into view, and pulses it once.

**Why `win=all`:** the leaderboard is all-time, but the Stats Aircraft tab
defaults to `7d`, where most repeat offenders have no row. An all-time offender
has all-time circles, so it is present in the `all` VNAP-compliance data.

## Components & changes (frontend only)

1. **`RepeatOffendersSection`** (`App.tsx`)
   - Each card becomes a real `<a href={statsHighlightHref(airportIcao, row.icao24)}>`
     instead of a `<button onClick={onSelect}>` — matches the existing `/stats`
     anchor (`App.tsx:501`), supports right-click / open-in-new-tab.
   - Drop the `onSelect` prop and App's handler (its old "scroll to complaint
     panel" behavior is replaced).
   - Card `title` updated to describe the new behavior.
   - `airportIcao` falls back to `"KBJC"` (same fallback the header `/stats` link
     uses) when undefined.

2. **`StatsPage`** (`StatsPage.tsx`)
   - Initialize `win` from the URL (`windowFromUrl`, default `"7d"`) so the
     deep-link's `win=all` takes effect. The window buttons (`setWin`) still work.

3. **`AircraftDashboard`** (`AircraftDashboard.tsx`)
   - Read the `aircraft` param once on mount.
   - After `data` loads, resolve the matching row via `resolveHighlight(sorted, param)`
     (case-insensitive on `icao24`). If it resolves and hasn't been applied yet:
     `setSelected(icao24)`, scroll `#vnap-row-<icao24>` into view
     (`behavior:"smooth", block:"center"`), and set a one-shot `pulse` class for
     ~1.2 s. A ref guard applies this at most once (so later `win`/sort changes
     don't re-trigger it).
   - Each `<tr>` gets `id={\`vnap-row-${a.icao24}\`}` and a conditional `pulse`
     class driven by a `pulseIcao` state.

4. **CSS**
   - `@keyframes vnap-pulse` + `.vnap-row.pulse` (a brief background/box-shadow
     flash that settles into the existing `.vnap-row.selected` style).

## Pure helpers (new, unit-tested with vitest)

All existing frontend tests are pure `lib/*.test.ts`; logic lives in helpers so
the components stay thin and the branching is tested.

- `statsHighlightHref(airportIcao: string | undefined, icao24: string): string`
  → `/stats?airport=<ICAO>&aircraft=<icao24>&win=all` (airport upper-cased and
  URL-encoded, icao24 lower-cased). Fallback airport `"KBJC"` when falsy.
- `resolveHighlight(rows: VnapAircraft[], param: string | null): string | null`
  → the `icao24` of the row matching `param` case-insensitively, else `null`.
- `windowFromUrl(search: string): StatsWindow` → the `win` param if it is a valid
  `StatsWindow` (`"1d" | "7d" | "30d" | "all"`), else `"7d"`.

Placement: `statsHighlightHref` + `windowFromUrl` in a small `lib/statsLinks.ts`;
`resolveHighlight` in `lib/vnapDashboard.ts` (alongside `sortAircraft`/`radarData`).

## Behavior / edge cases

- **Tail absent from the loaded window:** `resolveHighlight` returns `null` → no
  scroll/pulse, and `selectedAc` keeps its existing fallback to `sorted[0]` (the
  worst aircraft). Matches the agreed "only act when present" choice; `win=all`
  makes absence rare.
- **No `aircraft` param (normal `/stats` visit):** `resolveHighlight(_, null)`
  returns `null`; the page behaves exactly as today.
- **icao24 case:** backend stores `icao24` lower-case; comparison lower-cases
  both sides regardless.
- **Applied once:** the ref guard prevents re-highlighting when the user later
  changes the window or re-sorts the table.

## Out of scope

- No backend changes.
- No placeholder/synthetic rows for tails with no window activity.
- No change to the dashboard-side `OffenderTable`.

## Verification

- Unit: vitest over the three pure helpers (valid/invalid/missing param, case,
  airport fallback).
- Browser: click a leaderboard card → lands on `/stats` all-time → correct row is
  selected, radar shows "<tail> vs. average", row is scrolled into view and
  pulses; visiting `/stats` without `aircraft` is unchanged.
