import { create } from "zustand";

export type ThemeMode = "light" | "system" | "dark";
const KEY = "seleric.theme";

function readMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch { /* storage blocked: follow the system */ }
  return "system";
}

function systemDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function resolveDark(mode: ThemeMode): boolean {
  return mode === "system" ? systemDark() : mode === "dark";
}

interface ThemeState {
  mode: ThemeMode;
  /** The theme actually shown. The CSS and the shadcn `dark` variant key off data-theme, which is always light or dark. */
  dark: boolean;
  setMode: (mode: ThemeMode) => void;
  /** Re-resolve "system" after the OS appearance changes. */
  syncSystem: () => void;
}

export const useThemeStore = create<ThemeState>((set, get) => {
  const mode = readMode();
  return {
    mode,
    dark: resolveDark(mode),
    setMode: (next) => {
      try { localStorage.setItem(KEY, next); } catch { /* best effort */ }
      set({ mode: next, dark: resolveDark(next) });
    },
    syncSystem: () => { if (get().mode === "system") set({ dark: systemDark() }); },
  };
});
