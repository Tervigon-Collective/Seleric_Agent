import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Seleric API (FastAPI) default dev port. Override with SELERIC_API_URL.
const API = process.env.SELERIC_API_URL ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: API, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [],
  },
});
