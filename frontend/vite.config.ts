import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        // The rewrite strips /api before the backend sees it, exactly as Caddy
        // does in production. Declare the stripped prefix so the public /v1
        // OpenAPI schema advertises the right server URL here too.
        headers: { "X-Forwarded-Prefix": "/api" },
        rewrite: (path) => path.replace(/^\/api/, "")
      }
    }
  },
  test: {
    environment: "jsdom",
  },
});

