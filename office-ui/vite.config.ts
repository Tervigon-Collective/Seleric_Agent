import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vitest/config";
import react from "@vitejs/plugin-react";

// The Seleric API (FastAPI) local port. Override with SELERIC_API_URL.
// Default matches API_PORT in the repo .env used by scripts/run_dev.py.
const API =
  (globalThis as { process?: { env?: { SELERIC_API_URL?: string } } }).process?.env
    ?.SELERIC_API_URL ?? "http://127.0.0.1:8090";

function devApiKey(): string {
  const fromEnv = (globalThis as { process?: { env?: { API_KEY?: string } } }).process?.env?.API_KEY;
  if (fromEnv) return fromEnv;
  try {
    const text = readFileSync(resolve(import.meta.dirname, "../.env"), "utf8");
    const line = text.split(/\r?\n/).find((row) => row.startsWith("API_KEY="));
    return (line?.slice("API_KEY=".length) ?? "").trim().replace(/^["']|["']$/g, "");
  } catch {
    return "";
  }
}

/** Same contract as the API's /ui/config.js, which is only mounted for a built bundle. */
function devConfigJs(): Plugin {
  return {
    name: "seleric-dev-config",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.split("?")[0] !== "/config.js") {
          next();
          return;
        }
        res.setHeader("Content-Type", "application/javascript");
        res.setHeader("Cache-Control", "no-store");
        res.end(`window.__SELERIC_API_KEY__ = ${JSON.stringify(devApiKey())};\n`);
      });
    },
  };
}

export default defineConfig({
  plugins: [devConfigJs(), react()],
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
