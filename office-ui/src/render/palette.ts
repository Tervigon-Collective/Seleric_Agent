/**
 * Status visual language (brief §58). JS mirror of the design tokens in
 * index.css so the canvas and the DOM stay in sync. Never hard-code a status
 * colour anywhere else.
 */

export type StatusToken = "neutral" | "working" | "thinking" | "waiting" | "failed" | "done" | "collab";

export interface Palette {
  bg: string;
  grid: string;
  zoneFill: string;
  zoneStroke: string;
  zoneLabel: string;
  deskFill: string;
  text: string;
  textDim: string;
  status: Record<StatusToken, string>;
  accent: Record<string, string>;
}

export const LIGHT: Palette = {
  bg: "#ffffff",
  grid: "#e5e5ea",
  zoneFill: "#f2f2f7",
  zoneStroke: "#c6c6c8",
  zoneLabel: "#3c3c43",
  deskFill: "#e5e5ea",
  text: "#000000",
  textDim: "#3c3c43",
  status: {
    neutral: "#8e8e93",
    working: "#007aff",
    thinking: "#af52de",
    waiting: "#ff9500",
    failed: "#ff3b30",
    done: "#34c759",
    collab: "#32ade6",
  },
  accent: {
    coord: "#007aff",
    domain: "#636366",
    perf: "#007aff",
    performance: "#007aff",
    commerce: "#ff9500",
    funnel: "#af52de",
    tech: "#32ade6",
    technical: "#32ade6",
    finance: "#34c759",
    inventory: "#34c759",
    procurement: "#a2845e",
    diag: "#34c759",
    pred: "#ff9500",
    strat: "#ff3b30",
    skeptic: "#8e8e93",
    neutral: "#8e8e93",
  },
};

export const DARK: Palette = {
  bg: "#000000",
  grid: "#2c2c2e",
  zoneFill: "#1c1c1e",
  zoneStroke: "#38383a",
  zoneLabel: "#ebebf5",
  deskFill: "#2c2c2e",
  text: "#ffffff",
  textDim: "#98989f",
  status: {
    neutral: "#8e8e93",
    working: "#0a84ff",
    thinking: "#bf5af2",
    waiting: "#ff9f0a",
    failed: "#ff453a",
    done: "#30d158",
    collab: "#64d2ff",
  },
  accent: {
    coord: "#0a84ff",
    domain: "#98989f",
    perf: "#0a84ff",
    performance: "#0a84ff",
    commerce: "#ff9f0a",
    funnel: "#bf5af2",
    tech: "#64d2ff",
    technical: "#64d2ff",
    finance: "#30d158",
    inventory: "#30d158",
    procurement: "#ac8e68",
    diag: "#30d158",
    pred: "#ff9f0a",
    strat: "#ff453a",
    skeptic: "#8e8e93",
    neutral: "#98989f",
  },
};

export function paletteFor(dark: boolean): Palette {
  return dark ? DARK : LIGHT;
}
