import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local full-stack dev proxies /live straight to the main circlejerks API's
// own dev port (:8000) -- same convention frontend/vite.config.ts already
// uses for its own /api proxy -- and strips the /live prefix, since the main
// API's /positions route lives at the root, not under /api.
//
// Set VITE_DEV_PROXY=prod to instead proxy /live at PRODUCTION over HTTPS
// (https://circlejerks.live/api/positions), rewriting /live -> /api to match
// circlejerks.live's own external URL convention (see Caddyfile's
// `handle_path /api/*` block). This is how the live map was verified against
// real production data before this branch shipped -- see
// .superpowers/sdd/livemap-report.md. It is NEVER the default: nobody's
// ordinary `npm run dev` should silently start talking to prod.
const useProd = process.env.VITE_DEV_PROXY === "prod";

const liveProxy = useProd
  ? {
      target: "https://circlejerks.live",
      changeOrigin: true,
      secure: true,
      rewrite: (path: string) => path.replace(/^\/live/, "/api"),
    }
  : {
      target: "http://localhost:8000",
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/live/, ""),
    };

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/live": liveProxy,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
