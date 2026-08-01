import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom does not implement ResizeObserver, which recharts' ResponsiveContainer
// requires. Polyfill it so chart-bearing components can render in tests.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverPolyfill {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverPolyfill as unknown as typeof ResizeObserver;
}

// `test.globals` is off in vite.config.ts (tests import describe/it/vi
// explicitly), so @testing-library/react's built-in auto-cleanup — which
// only registers itself against a *global* afterEach — never fires. Without
// this, every test after the first `render()` in a suite accumulates DOM
// from prior renders, breaking role/text queries that expect one match.
afterEach(() => {
  cleanup();
});
