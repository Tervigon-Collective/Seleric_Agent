import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// The Seleric API (FastAPI) local port. Override with SELERIC_API_URL.
// Default 8091 — matches API_PUBLISH_PORT in repo .env (8090 is often taken).
const API =
  (globalThis as { process?: { env?: { SELERIC_API_URL?: string } } }).process?.env
    ?.SELERIC_API_URL ?? "http://127.0.0.1:8091";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
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
