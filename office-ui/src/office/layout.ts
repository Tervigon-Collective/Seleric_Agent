/**
 * Office map aligned to Parcha AI Office firstmap (25×25 tiles).
 * Homes are sparse on open floor — map art already has desks; we don't stack overlays.
 */

import { MAP_H, MAP_W, TILE } from "../render/parchaAssets";

export interface Vec {
  x: number;
  y: number;
}

export type FloorStyle = "wood" | "carpet" | "tile" | "checker" | "concrete" | "hallway";

export interface Zone {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  accent: string;
  kind: "office" | "bay" | "lab" | "room" | "commons";
  floor: FloorStyle;
  door: "N" | "S" | "E" | "W";
}

export const WORLD = { w: MAP_W * TILE, h: MAP_H * TILE };

const T = (tx: number, ty: number): Vec => ({ x: tx * TILE + TILE / 2, y: ty * TILE + TILE / 2 });
const Z = (
  id: string,
  label: string,
  tx: number,
  ty: number,
  tw: number,
  th: number,
  accent: string,
  kind: Zone["kind"],
  door: Zone["door"],
): Zone => ({
  id,
  label,
  x: tx * TILE,
  y: ty * TILE,
  w: tw * TILE,
  h: th * TILE,
  accent,
  kind,
  floor: "carpet",
  door,
});

/** Fewer zone labels — only major bays, so the floor stays readable. */
export const ZONES: Zone[] = [
  Z("coordinator", "Mission Control", 3, 3, 5, 5, "coord", "office", "S"),
  Z("performance", "Performance", 8, 4, 5, 4, "perf", "bay", "S"),
  Z("ops_floor", "Operations", 3, 8, 5, 5, "domain", "bay", "E"),
  Z("funnel", "Funnel", 9, 8, 5, 5, "funnel", "bay", "S"),
  Z("technical", "Tech", 14, 7, 4, 5, "tech", "bay", "W"),
  Z("diagnostic_lab", "Diagnostics", 3, 14, 5, 4, "diag", "lab", "N"),
  Z("handoff_room", "Handoff", 6, 15, 4, 3, "neutral", "room", "N"),
  Z("skeptic_room", "Skeptic", 2, 18, 4, 3, "skeptic", "room", "N"),
  Z("lounge", "Lounge", 14, 18, 6, 4, "neutral", "commons", "N"),
];

export const ZONE_BY_ID: Record<string, Zone> = Object.fromEntries(ZONES.map((z) => [z.id, z]));

export const SPOTS: Record<string, Vec> = {
  planning_board: T(4, 5),
  mission_room: T(6, 7),
  handoff_area: T(7, 16),
  skeptic_desk: T(2, 19),
  data_terminal: T(9, 13),
  lounge: T(6, 21),
  coffee: T(10, 9),
  break_coffee: T(6, 19),
};

export const MEETING_SLOTS: Record<string, [Vec, Vec]> = {
  handoff: [T(6, 16), T(8, 16)],
  strategy: [T(9, 13), T(8, 17)],
  skeptic: [T(2, 19), T(3, 19)],
  mission: [T(6, 7), T(4, 6)],
};

export interface DeskDef {
  agentId: string;
  zone: string;
  home: Vec;
}

/**
 * Sparse homes — Chebyshev ≥ 4 on fully reachable floor (no islands).
 * Spread across the whole walkable office so agents don't clump in one bay.
 */
export const DESKS: DeskDef[] = [
  { agentId: "coordinator", zone: "coordinator", home: T(6, 7) },
  { agentId: "inventory_agent", zone: "coordinator", home: T(3, 3) },
  { agentId: "commerce_agent", zone: "performance", home: T(7, 3) },
  { agentId: "performance_agent", zone: "performance", home: T(11, 5) },
  { agentId: "observer_agent", zone: "ops_floor", home: T(2, 7) },
  { agentId: "finance_agent", zone: "technical", home: T(15, 7) },
  { agentId: "funnel_agent", zone: "funnel", home: T(10, 9) },
  { agentId: "anomaly_agent", zone: "ops_floor", home: T(5, 11) },
  { agentId: "technical_agent", zone: "technical", home: T(15, 11) },
  { agentId: "prediction_agent", zone: "funnel", home: T(9, 13) },
  { agentId: "diagnostic_agent", zone: "diagnostic_lab", home: T(4, 15) },
  { agentId: "procurement_agent", zone: "handoff_room", home: T(8, 17) },
  { agentId: "skeptic_agent", zone: "skeptic_room", home: T(2, 19) },
  { agentId: "strategy_agent", zone: "lounge", home: T(6, 21) },
];

export const DESK_BY_ID: Record<string, DeskDef> = Object.fromEntries(DESKS.map((d) => [d.agentId, d]));

export function homeOf(agentId: string): Vec {
  return DESK_BY_ID[agentId]?.home ?? { x: WORLD.w / 2, y: WORLD.h / 2 };
}
