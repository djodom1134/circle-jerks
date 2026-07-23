import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the SPA and proxies same-origin /api/* to the public
// circlejerks /v1 API over HTTPS, rewriting /api -> /v1. This mirrors the
// production Caddy proxy (which also injects the API key — the dev proxy does
// NOT, so live dev only reaches endpoints that tolerate an unauthenticated
// request or a key supplied out of band). Fixtures, not this proxy, are the
// default data source in tests.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      "/api": {
        target: "https://circlejerks.live",
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/api/, "/v1"),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
