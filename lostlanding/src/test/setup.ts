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
