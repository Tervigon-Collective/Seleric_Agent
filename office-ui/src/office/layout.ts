/**
 * Static office map: zones, desks, shared spots, and hallway gutters.
 * Rooms sit on a grid with ≥56px corridors so A* can route through doors
 * without clipping walls. Edit freely — navmesh rebuilds from this data.
 */

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

export type DecorKind =
  | "plant"
  | "rug"
  | "whiteboard"
  | "serverRack"
  | "meetingTable"
  | "coffee"
  | "waterCooler"
  | "bookshelf"
  | "couch"
  | "planningBoard"
  | "archiveShelf"
  | "forecastRig"
  | "deskLamp"
  | "filingCabinet"
  | "printer"
  | "clock"
  | "wallArt"
  | "kitchen"
  | "trash"
  | "sideChair"
  | "waterFountain"
  | "bench"
  | "pottedTree"
  | "standingLamp"
  | "monitorWall";

export interface Decor {
  kind: DecorKind;
  x: number;
  y: number;
}

export const WORLD = { w: 1760, h: 1120 };

/** Doorway opening width — must stay wider than body clearance * 2. */
export const DOOR_GAP = 72;
export const WALL_T = 6;

/**
 * Floor plan (top-down). Columns separated by a 64px vertical hallway;
 * rows separated by a 64px horizontal hallway.
 *
 *   col0 x=40..460     hall x=460..524     col1 x=524..900     hall     col2 x=964..1324    hall     col3 x=1388..1720
 *   row0 y=40..280
 *   hall y=280..344
 *   row1 y=344..584
 *   hall y=584..648
 *   row2 y=648..888
 *   hall y=888..952
 *   row3 y=952..1080  (lounge / meeting strip)
 */
export const ZONES: Zone[] = [
  // Row 0 — control + marketing
  { id: "coordinator", label: "Mission Control", x: 40, y: 40, w: 420, h: 240, accent: "coord", kind: "office", floor: "wood", door: "S" },
  { id: "performance", label: "Performance Marketing", x: 524, y: 40, w: 376, h: 240, accent: "perf", kind: "bay", floor: "carpet", door: "S" },
  { id: "commerce", label: "Commerce Bay", x: 964, y: 40, w: 360, h: 240, accent: "commerce", kind: "bay", floor: "carpet", door: "S" },
  { id: "finance", label: "Finance Bay", x: 1388, y: 40, w: 332, h: 240, accent: "domain", kind: "bay", floor: "wood", door: "S" },

  // Row 1 — ops + funnel + tech + inventory
  { id: "ops_floor", label: "Operations Floor", x: 40, y: 344, w: 420, h: 240, accent: "domain", kind: "bay", floor: "carpet", door: "E" },
  { id: "funnel", label: "Website Funnel Bay", x: 524, y: 344, w: 376, h: 240, accent: "funnel", kind: "bay", floor: "carpet", door: "S" },
  { id: "technical", label: "Technical Operations", x: 964, y: 344, w: 360, h: 240, accent: "tech", kind: "bay", floor: "concrete", door: "S" },
  { id: "inventory", label: "Inventory / Procurement", x: 1388, y: 344, w: 332, h: 240, accent: "domain", kind: "bay", floor: "wood", door: "S" },

  // Row 2 — labs + strategy
  { id: "lounge", label: "Waiting Lounge", x: 40, y: 648, w: 420, h: 240, accent: "neutral", kind: "commons", floor: "wood", door: "E" },
  { id: "diagnostic_lab", label: "Diagnostic Lab", x: 524, y: 648, w: 376, h: 240, accent: "diag", kind: "lab", floor: "tile", door: "N" },
  { id: "prediction_lab", label: "Prediction Lab", x: 964, y: 648, w: 360, h: 240, accent: "pred", kind: "lab", floor: "tile", door: "N" },
  { id: "strategy_room", label: "Strategy Room", x: 1388, y: 648, w: 332, h: 240, accent: "strat", kind: "room", floor: "wood", door: "N" },

  // Row 3 — review / meeting / archive
  { id: "skeptic_room", label: "Skeptic Review Room", x: 40, y: 952, w: 420, h: 140, accent: "skeptic", kind: "room", floor: "checker", door: "N" },
  { id: "handoff_room", label: "Handoff / Meeting Room", x: 524, y: 952, w: 376, h: 140, accent: "neutral", kind: "room", floor: "wood", door: "N" },
  { id: "data_room", label: "Evidence Archive", x: 964, y: 952, w: 360, h: 140, accent: "neutral", kind: "commons", floor: "concrete", door: "N" },
  { id: "break_room", label: "Break / Coffee", x: 1388, y: 952, w: 332, h: 140, accent: "neutral", kind: "commons", floor: "wood", door: "N" },
];

/** Explicit hallway carpet strips (drawn under rooms). */
export const HALLWAYS: { x: number; y: number; w: number; h: number }[] = [
  // vertical halls
  { x: 460, y: 40, w: 64, h: 1052 },
  { x: 900, y: 40, w: 64, h: 1052 },
  { x: 1324, y: 40, w: 64, h: 1052 },
  // horizontal halls
  { x: 40, y: 280, w: 1680, h: 64 },
  { x: 40, y: 584, w: 1680, h: 64 },
  { x: 40, y: 888, w: 1680, h: 64 },
];

export const DECOR: Decor[] = [
  // Mission Control
  { kind: "rug", x: 220, y: 200 },
  { kind: "planningBoard", x: 250, y: 90 },
  { kind: "plant", x: 70, y: 250 },
  { kind: "bookshelf", x: 420, y: 90 },
  { kind: "filingCabinet", x: 420, y: 240 },
  { kind: "clock", x: 250, y: 58 },
  { kind: "deskLamp", x: 190, y: 200 },
  { kind: "sideChair", x: 300, y: 200 },
  { kind: "standingLamp", x: 80, y: 90 },
  { kind: "wallArt", x: 140, y: 58 },
  // Performance
  { kind: "plant", x: 860, y: 70 },
  { kind: "whiteboard", x: 560, y: 70 },
  { kind: "deskLamp", x: 640, y: 150 },
  { kind: "monitorWall", x: 780, y: 90 },
  { kind: "trash", x: 860, y: 250 },
  // Commerce
  { kind: "plant", x: 1280, y: 70 },
  { kind: "bookshelf", x: 1280, y: 200 },
  { kind: "deskLamp", x: 1100, y: 150 },
  { kind: "filingCabinet", x: 1000, y: 250 },
  // Finance
  { kind: "bookshelf", x: 1680, y: 90 },
  { kind: "filingCabinet", x: 1680, y: 220 },
  { kind: "deskLamp", x: 1480, y: 150 },
  { kind: "plant", x: 1420, y: 250 },
  // Ops
  { kind: "whiteboard", x: 80, y: 370 },
  { kind: "coffee", x: 400, y: 380 },
  { kind: "printer", x: 300, y: 550 },
  { kind: "deskLamp", x: 140, y: 450 },
  { kind: "deskLamp", x: 280, y: 450 },
  { kind: "plant", x: 420, y: 550 },
  { kind: "wallArt", x: 220, y: 360 },
  // Funnel
  { kind: "plant", x: 860, y: 370 },
  { kind: "deskLamp", x: 640, y: 450 },
  { kind: "monitorWall", x: 780, y: 390 },
  { kind: "waterFountain", x: 540, y: 560 },
  // Technical
  { kind: "serverRack", x: 1280, y: 390 },
  { kind: "deskLamp", x: 1100, y: 450 },
  { kind: "monitorWall", x: 1020, y: 390 },
  { kind: "trash", x: 1280, y: 550 },
  // Inventory
  { kind: "archiveShelf", x: 1680, y: 390 },
  { kind: "filingCabinet", x: 1680, y: 520 },
  { kind: "deskLamp", x: 1480, y: 420 },
  { kind: "deskLamp", x: 1580, y: 500 },
  { kind: "plant", x: 1420, y: 550 },
  // Lounge
  { kind: "couch", x: 160, y: 720 },
  { kind: "couch", x: 340, y: 720 },
  { kind: "rug", x: 250, y: 760 },
  { kind: "kitchen", x: 400, y: 820 },
  { kind: "plant", x: 70, y: 850 },
  { kind: "trash", x: 380, y: 760 },
  { kind: "pottedTree", x: 80, y: 700 },
  { kind: "bench", x: 250, y: 850 },
  // Diagnostic
  { kind: "whiteboard", x: 840, y: 680 },
  { kind: "deskLamp", x: 640, y: 760 },
  { kind: "plant", x: 560, y: 850 },
  { kind: "trash", x: 860, y: 850 },
  // Prediction
  { kind: "forecastRig", x: 1200, y: 760 },
  { kind: "deskLamp", x: 1100, y: 760 },
  { kind: "plant", x: 1280, y: 850 },
  // Strategy
  { kind: "rug", x: 1550, y: 780 },
  { kind: "meetingTable", x: 1550, y: 770 },
  { kind: "sideChair", x: 1500, y: 770 },
  { kind: "sideChair", x: 1600, y: 770 },
  { kind: "clock", x: 1550, y: 670 },
  { kind: "standingLamp", x: 1420, y: 860 },
  // Skeptic
  { kind: "bookshelf", x: 400, y: 1000 },
  { kind: "deskLamp", x: 200, y: 1020 },
  { kind: "sideChair", x: 280, y: 1020 },
  { kind: "rug", x: 220, y: 1020 },
  // Handoff / meeting
  { kind: "rug", x: 710, y: 1020 },
  { kind: "meetingTable", x: 710, y: 1020 },
  { kind: "sideChair", x: 660, y: 1020 },
  { kind: "sideChair", x: 760, y: 1020 },
  { kind: "clock", x: 710, y: 970 },
  { kind: "plant", x: 860, y: 1060 },
  // Evidence archive
  { kind: "archiveShelf", x: 1000, y: 1000 },
  { kind: "archiveShelf", x: 1120, y: 1000 },
  { kind: "archiveShelf", x: 1240, y: 1000 },
  { kind: "filingCabinet", x: 1060, y: 1060 },
  { kind: "printer", x: 1180, y: 1060 },
  // Break room
  { kind: "kitchen", x: 1550, y: 1020 },
  { kind: "coffee", x: 1480, y: 1000 },
  { kind: "waterCooler", x: 1650, y: 1000 },
  { kind: "bench", x: 1550, y: 1060 },
  { kind: "pottedTree", x: 1680, y: 1060 },
  // Hallway accents
  { kind: "plant", x: 490, y: 300 },
  { kind: "plant", x: 930, y: 300 },
  { kind: "plant", x: 1350, y: 300 },
  { kind: "waterFountain", x: 490, y: 610 },
  { kind: "bench", x: 930, y: 610 },
  { kind: "plant", x: 1350, y: 610 },
  { kind: "pottedTree", x: 490, y: 920 },
  { kind: "plant", x: 930, y: 920 },
];

export const ZONE_BY_ID: Record<string, Zone> = Object.fromEntries(ZONES.map((z) => [z.id, z]));

export const SPOTS: Record<string, Vec> = {
  planning_board: { x: 250, y: 140 },
  mission_room: { x: 220, y: 210 },
  handoff_area: { x: 710, y: 1030 },
  skeptic_desk: { x: 200, y: 1030 },
  data_terminal: { x: 1140, y: 1030 },
  lounge: { x: 250, y: 780 },
  coffee: { x: 400, y: 400 },
  break_coffee: { x: 1480, y: 1020 },
};

/** Facing seats at meeting tables — used for in-person handoffs / reviews. */
export const MEETING_SLOTS: Record<string, [Vec, Vec]> = {
  handoff: [
    { x: 676, y: 1030 },
    { x: 744, y: 1030 },
  ],
  strategy: [
    { x: 1516, y: 770 },
    { x: 1584, y: 770 },
  ],
  skeptic: [
    { x: 180, y: 1030 },
    { x: 250, y: 1030 },
  ],
  mission: [
    { x: 200, y: 210 },
    { x: 270, y: 210 },
  ],
};

export interface DeskDef {
  agentId: string;
  zone: string;
  home: Vec;
}

export const DESKS: DeskDef[] = [
  { agentId: "coordinator", zone: "coordinator", home: { x: 160, y: 200 } },
  { agentId: "observer_agent", zone: "ops_floor", home: { x: 140, y: 450 } },
  { agentId: "anomaly_agent", zone: "ops_floor", home: { x: 280, y: 450 } },
  { agentId: "performance_agent", zone: "performance", home: { x: 640, y: 160 } },
  { agentId: "commerce_agent", zone: "commerce", home: { x: 1100, y: 160 } },
  { agentId: "funnel_agent", zone: "funnel", home: { x: 640, y: 460 } },
  { agentId: "technical_agent", zone: "technical", home: { x: 1100, y: 460 } },
  { agentId: "finance_agent", zone: "finance", home: { x: 1480, y: 160 } },
  { agentId: "inventory_agent", zone: "inventory", home: { x: 1480, y: 420 } },
  { agentId: "procurement_agent", zone: "inventory", home: { x: 1600, y: 500 } },
  { agentId: "diagnostic_agent", zone: "diagnostic_lab", home: { x: 640, y: 760 } },
  { agentId: "prediction_agent", zone: "prediction_lab", home: { x: 1100, y: 760 } },
  { agentId: "strategy_agent", zone: "strategy_room", home: { x: 1550, y: 770 } },
  { agentId: "skeptic_agent", zone: "skeptic_room", home: { x: 200, y: 1030 } },
];

export const DESK_BY_ID: Record<string, DeskDef> = Object.fromEntries(DESKS.map((d) => [d.agentId, d]));

export function homeOf(agentId: string): Vec {
  return DESK_BY_ID[agentId]?.home ?? { x: WORLD.w / 2, y: WORLD.h / 2 };
}

/** Door portal just outside a room — useful waypoints for path smoothing. */
export function doorOutside(zoneId: string): Vec | null {
  const z = ZONE_BY_ID[zoneId];
  if (!z) return null;
  const midX = z.x + z.w / 2;
  const midY = z.y + z.h / 2;
  const pad = 22;
  switch (z.door) {
    case "N": return { x: midX, y: z.y - pad };
    case "S": return { x: midX, y: z.y + z.h + pad };
    case "E": return { x: z.x + z.w + pad, y: midY };
    case "W": return { x: z.x - pad, y: midY };
  }
}
