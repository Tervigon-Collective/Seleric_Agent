import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Seleric API (FastAPI) local port. Override with SELERIC_API_URL.
// Default 8090 — 8080 is often taken by other local services on this machine.
const API = process.env.SELERIC_API_URL ?? "http://127.0.0.1:8090";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/v1": { target: API, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/__tests__/setup.ts"],
  },
});
