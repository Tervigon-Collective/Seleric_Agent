/**
 * Parcha AI Office map + sprites (MIT).
 * Map model matches convex/maps/firstmap.ts — MidJourney office as 25×25 of 32px tiles.
 * https://github.com/Parcha-ai/ai-office
 */

/** World px per map tile. 48 keeps Parcha art crisp while filling a desktop stage. */
export const TILE = 48;
export const MAP_W = 25;
export const MAP_H = 25;
/** Tileset is 800×800 → 25 columns of 32px tiles. */
export const TILESET_COLS = 25;

export type CharSheet = { ox: number; oy: number };

export const CHAR_SHEETS: CharSheet[] = [
  { ox: 0, oy: 0 },
  { ox: 96, oy: 0 },
  { ox: 192, oy: 0 },
  { ox: 288, oy: 0 },
  { ox: 0, oy: 128 },
  { ox: 96, oy: 128 },
  { ox: 192, oy: 128 },
  { ox: 288, oy: 128 },
];

export function sheetForAgent(agentId: string): CharSheet {
  let h = 0;
  for (let i = 0; i < agentId.length; i++) h = (h + agentId.charCodeAt(i) * 17) % CHAR_SHEETS.length;
  return CHAR_SHEETS[h];
}

type Facing = "N" | "S" | "E" | "W";

export function charFrame(sheet: CharSheet, facing: Facing, walkFrame: number): { sx: number; sy: number; sw: number; sh: number } {
  const f = ((walkFrame % 3) + 3) % 3;
  const row = facing === "S" ? 0 : facing === "W" ? 1 : facing === "E" ? 2 : 3;
  return { sx: sheet.ox + f * 32, sy: sheet.oy + row * 32, sw: 32, sh: 32 };
}

/**
 * Object layer from firstmap.ts — `-1` = walkable, anything else = solid.
 * Typos like `-312` treated as blocked (same as upstream).
 */
export const OBJMAP: number[][] = [
  [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24],
  [25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49],
  [50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74],
  [75,76,77,-1,-1,80,-1,-1,83,84,85,-1,87,88,89,-1,-1,-1,93,94,95,-1,97,98,99],
  [100,101,102,-1,-1,105,-1,-1,108,109,110,-1,112,113,-1,-1,-1,-1,-1,-1,-1,-1,122,123,124],
  [125,126,127,-1,-1,130,-1,-1,-1,-1,-1,-1,137,138,139,-1,141,142,143,144,145,146,147,148,149],
  [150,151,-1,-1,-1,155,-1,-1,-1,-1,-1,-1,162,163,164,-1,166,167,168,169,170,171,172,173,174],
  [175,176,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,199],
  [200,201,202,-1,-1,-1,-1,-1,208,209,-1,-1,-1,-1,-1,-1,216,217,218,219,-1,-1,-1,-1,224],
  [225,226,227,228,229,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,241,242,243,244,-1,-1,-1,248,249],
  [250,251,252,253,254,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,266,267,268,269,-1,-1,-1,273,274],
  [275,276,277,278,279,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,298,299],
  // Upstream typos `-312` etc. corrected to `-1` so corridors stay open (matches intended firstmap).
  [300,301,302,303,304,-1,-1,-1,-1,-1,-1,-1,-1,313,314,-1,316,317,318,319,-1,-1,-1,323,324],
  [325,326,327,328,-1,-1,-1,-1,-1,-1,-1,-1,-1,338,339,-1,341,342,-1,-1,-1,-1,-1,348,349],
  [350,351,352,353,-1,-1,-1,357,358,359,360,-1,-1,363,364,-1,366,367,368,369,370,371,372,373,374],
  [375,376,377,378,-1,-1,-1,382,383,384,385,-1,-1,388,389,-1,391,392,393,394,395,396,397,398,399],
  [400,401,402,403,-1,-1,-1,-1,-1,-1,-1,-1,-1,413,-1,-1,-1,-1,-1,-1,-1,-1,-1,423,424],
  [425,426,-1,-1,-1,-1,-1,-1,-1,434,435,436,437,438,-1,-1,441,442,443,444,445,-1,-1,448,449],
  [450,451,-1,-1,454,455,-1,-1,-1,459,460,461,462,463,-1,-1,466,467,468,469,470,-1,-1,473,474],
  [475,476,-1,-1,479,480,-1,-1,-1,484,485,486,487,488,-1,-1,491,492,493,494,495,-1,-1,498,499],
  [500,501,-1,-1,504,505,-1,-1,-1,509,510,511,512,513,-1,-1,516,517,518,519,520,-1,-1,523,524],
  [525,526,-1,-1,529,530,-1,-1,-1,534,535,536,537,538,-1,-1,541,542,543,544,545,-1,-1,548,549],
  [550,551,-1,-1,-1,-1,-1,-1,-1,559,560,561,562,563,-1,-1,-1,-1,-1,-1,-1,-1,-1,573,574],
  [575,576,577,578,579,580,581,582,583,584,585,586,587,588,589,590,591,592,593,594,595,596,597,598,599],
  [600,601,602,603,604,605,606,607,608,609,610,611,612,613,614,615,616,617,618,619,620,621,622,623,624],
];

function buildBlockedFromObjmap(): Uint8Array {
  const solid = new Uint8Array(MAP_W * MAP_H);
  for (let y = 0; y < MAP_H; y++) {
    for (let x = 0; x < MAP_W; x++) {
      if (OBJMAP[y]?.[x] !== -1) solid[y * MAP_W + x] = 1;
    }
  }
  const isOpen = (x: number, y: number) =>
    x >= 0 && y >= 0 && x < MAP_W && y < MAP_H && solid[y * MAP_W + x] === 0;

  /** Only allow standing in a fully-open 2×2 — kills 1-tile furniture cracks. */
  const clear2 = (x: number, y: number) => {
    for (const [ox, oy] of [
      [0, 0],
      [-1, 0],
      [0, -1],
      [-1, -1],
    ] as const) {
      const x0 = x + ox;
      const y0 = y + oy;
      if (isOpen(x0, y0) && isOpen(x0 + 1, y0) && isOpen(x0, y0 + 1) && isOpen(x0 + 1, y0 + 1)) {
        return true;
      }
    }
    return false;
  };

  /**
   * Painted MidJourney cubicle doorways (walkable in objmap, walls in the art).
   * Keep pathing out of these so agents never idle wedged between partitions.
   */
  const PAINTED_CRACKS = new Set([
    // prediction / handoff cubicle throat
    "11,14",
    "12,14",
    "11,15",
    "12,15",
    "11,16",
    "12,16",
    // technical bay pinches
    "13,10",
    "13,11",
    "14,10",
    "14,11",
  ]);

  const blocked = new Uint8Array(MAP_W * MAP_H);
  for (let y = 0; y < MAP_H; y++) {
    for (let x = 0; x < MAP_W; x++) {
      if (!clear2(x, y) || PAINTED_CRACKS.has(`${x},${y}`)) blocked[y * MAP_W + x] = 1;
    }
  }
  return blocked;
}

export interface ParchaAssets {
  tileset: HTMLImageElement | null;
  chars: HTMLImageElement | null;
  mapCanvas: HTMLCanvasElement;
  blocked: Uint8Array;
  ready: boolean;
}

let cache: ParchaAssets | null = null;
let loading: Promise<ParchaAssets> | null = null;
/** Bump when prerender math changes so HMR rebuilds the map canvas. */
const RENDER_REV = 5;
let builtRev = 0;

function emptyCanvas(): HTMLCanvasElement {
  if (typeof document !== "undefined") return document.createElement("canvas");
  return { width: 0, height: 0 } as HTMLCanvasElement;
}

function ensureCache(): ParchaAssets {
  if (cache) return cache;
  cache = {
    tileset: null,
    chars: null,
    mapCanvas: emptyCanvas(),
    blocked: buildBlockedFromObjmap(),
    ready: false,
  };
  return cache;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`failed to load ${src}`));
    img.src = src;
  });
}

/** Paint the 25×25 office: source art is 32px tiles; world uses TILE. */
function prerenderOffice(tileset: HTMLImageElement): HTMLCanvasElement {
  const src = Math.max(1, Math.round(tileset.naturalWidth / TILESET_COLS)); // 32 on Parcha PNG
  const c = document.createElement("canvas");
  c.width = MAP_W * TILE;
  c.height = MAP_H * TILE;
  const ctx = c.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  for (let y = 0; y < MAP_H; y++) {
    for (let x = 0; x < MAP_W; x++) {
      const id = y * TILESET_COLS + x;
      const sx = (id % TILESET_COLS) * src;
      const sy = Math.floor(id / TILESET_COLS) * src;
      ctx.drawImage(tileset, sx, sy, src, src, x * TILE, y * TILE, TILE, TILE);
    }
  }
  return c;
}

export function loadParchaAssets(): Promise<ParchaAssets> {
  const base = ensureCache();
  const needSize = MAP_W * TILE;
  if (base.ready && base.mapCanvas.width === needSize && builtRev === RENDER_REV) {
    return Promise.resolve(base);
  }
  if (loading) return loading;
  if (typeof Image === "undefined" || typeof document === "undefined") {
    return Promise.resolve(base);
  }
  loading = (async () => {
    try {
      const [tileset, chars] = await Promise.all([
        loadImage("/assets/rpg-tileset.png"),
        loadImage("/assets/OfficeSpriteSet.png"),
      ]);
      base.tileset = tileset;
      base.chars = chars;
      base.mapCanvas = prerenderOffice(tileset);
      base.blocked = buildBlockedFromObjmap();
      base.ready = true;
      builtRev = RENDER_REV;
    } catch (err) {
      console.warn("[office] parcha visual assets unavailable; nav still uses firstmap collision", err);
      loading = null;
    }
    return base;
  })();
  return loading;
}

export function getParchaAssets(): ParchaAssets | null {
  return cache ?? ensureCache();
}

export function __setBlockedForTests(blocked: Uint8Array): void {
  const c = ensureCache();
  c.blocked = blocked;
  c.ready = true;
}

export function tileBlocked(tx: number, ty: number, blocked: Uint8Array): boolean {
  if (tx < 0 || ty < 0 || tx >= MAP_W || ty >= MAP_H) return true;
  return blocked[ty * MAP_W + tx] === 1;
}

export function worldToTile(x: number, y: number): [number, number] {
  return [Math.floor(x / TILE), Math.floor(y / TILE)];
}

export function tileCenter(tx: number, ty: number): { x: number; y: number } {
  return { x: tx * TILE + TILE / 2, y: ty * TILE + TILE / 2 };
}
