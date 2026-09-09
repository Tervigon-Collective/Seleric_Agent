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
  bg: "#d9e0c8",
  grid: "#cfd7b8",
  zoneFill: "#f3efe6",
  zoneStroke: "#c4bba8",
  zoneLabel: "#5a5348",
  deskFill: "#d4c4a8",
  text: "#1c2430",
  textDim: "#63708a",
  status: {
    neutral: "#9aa7bd",
    working: "#2f7dd1",
    thinking: "#8257d9",
    waiting: "#d98b28",
    failed: "#d1435b",
    done: "#2fa96a",
    collab: "#2bb0c4",
  },
  accent: {
    coord: "#3457a6",
    domain: "#5a6b8c",
    perf: "#2f7dd1",
    performance: "#2f7dd1",
    commerce: "#c47f2b",
    funnel: "#7a4fc4",
    tech: "#3aa0b0",
    technical: "#3aa0b0",
    finance: "#2fa96a",
    inventory: "#6b8f71",
    procurement: "#8a7a5c",
    diag: "#2fa96a",
    pred: "#d98b28",
    strat: "#c8543f",
    skeptic: "#5b6470",
    neutral: "#8a95a8",
  },
};

export const DARK: Palette = {
  bg: "#11151c",
  grid: "#1b212b",
  zoneFill: "#161c25",
  zoneStroke: "#2a323f",
  zoneLabel: "#8593a8",
  deskFill: "#222b38",
  text: "#e7ecf4",
  textDim: "#8593a8",
  status: {
    neutral: "#6b7688",
    working: "#4f9ae6",
    thinking: "#a07ff0",
    waiting: "#e6a24d",
    failed: "#ec5c74",
    done: "#43c184",
    collab: "#3ec6db",
  },
  accent: {
    coord: "#5b7fd6",
    domain: "#8496b8",
    perf: "#4f9ae6",
    performance: "#4f9ae6",
    commerce: "#e0994a",
    funnel: "#9a72e6",
    tech: "#4fc0d0",
    technical: "#4fc0d0",
    finance: "#43c184",
    inventory: "#7aaf86",
    procurement: "#a89878",
    diag: "#43c184",
    pred: "#e6a24d",
    strat: "#e2745f",
    skeptic: "#939db0",
    neutral: "#9aa5b8",
  },
};

export function paletteFor(dark: boolean): Palette {
  return dark ? DARK : LIGHT;
}
