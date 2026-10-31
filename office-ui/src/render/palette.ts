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
  bg: "#f6f7fb",
  grid: "#e0e3ee",
  zoneFill: "#eceef5",
  zoneStroke: "#c9cee0",
  zoneLabel: "#585f75",
  deskFill: "#e0e3ee",
  text: "#161a26",
  textDim: "#585f75",
  status: {
    neutral: "#8a90a5",
    working: "#3b5bdb",
    thinking: "#7c5cd6",
    waiting: "#c27a00",
    failed: "#c42d3c",
    done: "#1f8a5b",
    collab: "#0e8fb8",
  },
  accent: {
    coord: "#3b5bdb",
    domain: "#585f75",
    perf: "#3b5bdb",
    performance: "#3b5bdb",
    commerce: "#c27a00",
    funnel: "#7c5cd6",
    tech: "#0e8fb8",
    technical: "#0e8fb8",
    finance: "#1f8a5b",
    inventory: "#1f8a5b",
    procurement: "#a2845e",
    diag: "#1f8a5b",
    pred: "#c27a00",
    strat: "#c42d3c",
    skeptic: "#8a90a5",
    neutral: "#8a90a5",
  },
};

export const DARK: Palette = {
  bg: "#0f1219",
  grid: "#242a38",
  zoneFill: "#171b25",
  zoneStroke: "#353c4f",
  zoneLabel: "#a4abc0",
  deskFill: "#1f2431",
  text: "#e8ebf5",
  textDim: "#a4abc0",
  status: {
    neutral: "#8087a0",
    working: "#7c9cff",
    thinking: "#b39bff",
    waiting: "#f0b34a",
    failed: "#ff7b87",
    done: "#5fd3a0",
    collab: "#6cd4f2",
  },
  accent: {
    coord: "#7c9cff",
    domain: "#a4abc0",
    perf: "#7c9cff",
    performance: "#7c9cff",
    commerce: "#f0b34a",
    funnel: "#b39bff",
    tech: "#6cd4f2",
    technical: "#6cd4f2",
    finance: "#5fd3a0",
    inventory: "#5fd3a0",
    procurement: "#ac8e68",
    diag: "#5fd3a0",
    pred: "#f0b34a",
    strat: "#ff7b87",
    skeptic: "#8087a0",
    neutral: "#a4abc0",
  },
};

export function paletteFor(dark: boolean): Palette {
  return dark ? DARK : LIGHT;
}
