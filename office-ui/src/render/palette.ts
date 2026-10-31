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
  bg: "#e8e8e8",
  grid: "#d3d3d3",
  zoneFill: "#e0e0e0",
  zoneStroke: "#bdbdbd",
  zoneLabel: "#52525b",
  deskFill: "#d3d3d3",
  text: "#3f3f46",
  textDim: "#52525b",
  status: {
    neutral: "#8e8e93",
    working: "#6366f1",
    thinking: "#8b5cf6",
    waiting: "#f59e0b",
    failed: "#ef4444",
    done: "#22c55e",
    collab: "#06b6d4",
  },
  accent: {
    coord: "#6366f1",
    domain: "#636366",
    perf: "#6366f1",
    performance: "#6366f1",
    commerce: "#f59e0b",
    funnel: "#8b5cf6",
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
  bg: "#26282c",
  grid: "#34363c",
  zoneFill: "#212327",
  zoneStroke: "#464850",
  zoneLabel: "#b4b4bd",
  deskFill: "#34363c",
  text: "#e4e4e7",
  textDim: "#b4b4bd",
  status: {
    neutral: "#8e8e93",
    working: "#818cf8",
    thinking: "#a78bfa",
    waiting: "#fbbf24",
    failed: "#f87171",
    done: "#4ade80",
    collab: "#67e8f9",
  },
  accent: {
    coord: "#818cf8",
    domain: "#98989f",
    perf: "#818cf8",
    performance: "#818cf8",
    commerce: "#fbbf24",
    funnel: "#a78bfa",
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
