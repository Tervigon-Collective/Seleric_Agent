import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AppErrorBoundary } from "./components/ErrorBoundary";
import "@fontsource-variable/inter";
import "./styles/shadcn.css";
import "./index.css";

// A deploy replaces the hashed lazy chunks; a tab opened before it must reload to find them.
// The 10 s guard stops a reload loop when the chunk is genuinely unreachable.
window.addEventListener("vite:preloadError", () => {
  try {
    if (Date.now() - Number(sessionStorage.getItem("preload-reload") ?? 0) < 10_000) return;
    sessionStorage.setItem("preload-reload", String(Date.now()));
  } catch { /* storage blocked: reload once anyway */ }
  location.reload();
});

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </React.StrictMode>,
);
