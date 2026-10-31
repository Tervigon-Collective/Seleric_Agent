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
  bg: "#fafafa",
  grid: "#e4e0d6",
  zoneFill: "#f5f1e8",
  zoneStroke: "#cbc7bb",
  zoneLabel: "#575b63",
  deskFill: "#e4e0d6",
  text: "#1f2125",
  textDim: "#575b63",
  status: {
    neutral: "#8e8e93",
    working: "#3b82f6",
    thinking: "#9333ea",
    waiting: "#f59e0b",
    failed: "#ef4444",
    done: "#22c55e",
    collab: "#06b6d4",
  },
  accent: {
    coord: "#3b82f6",
    domain: "#636366",
    perf: "#3b82f6",
    performance: "#3b82f6",
    commerce: "#f59e0b",
    funnel: "#9333ea",
    tech: "#06b6d4",
    technical: "#06b6d4",
    finance: "#22c55e",
    inventory: "#22c55e",
    procurement: "#a2845e",
    diag: "#22c55e",
    pred: "#f59e0b",
    strat: "#ef4444",
    skeptic: "#8e8e93",
    neutral: "#8e8e93",
  },
};

export const DARK: Palette = {
  bg: "#17181a",
  grid: "#34332f",
  zoneFill: "#1f1e1b",
  zoneStroke: "#4a4943",
  zoneLabel: "#b3b1a8",
  deskFill: "#34332f",
  text: "#ecebe6",
  textDim: "#b3b1a8",
  status: {
    neutral: "#8e8e93",
    working: "#60a5fa",
    thinking: "#c084fc",
    waiting: "#fbbf24",
    failed: "#f87171",
    done: "#4ade80",
    collab: "#67e8f9",
  },
  accent: {
    coord: "#60a5fa",
    domain: "#98989f",
    perf: "#60a5fa",
    performance: "#60a5fa",
    commerce: "#fbbf24",
    funnel: "#c084fc",
    tech: "#67e8f9",
    technical: "#67e8f9",
    finance: "#4ade80",
    inventory: "#4ade80",
    procurement: "#ac8e68",
    diag: "#4ade80",
    pred: "#fbbf24",
    strat: "#f87171",
    skeptic: "#8e8e93",
    neutral: "#98989f",
  },
};

export function paletteFor(dark: boolean): Palette {
  return dark ? DARK : LIGHT;
}
