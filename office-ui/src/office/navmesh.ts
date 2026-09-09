/**
 * Office navigation mesh — coarse walkable grid + A* so characters move like
 * people: down hallways, THROUGH doorways, never across a wall.
 */

import { DOOR_GAP, WALL_T, WORLD, ZONES, type Vec } from "./layout";

const CELL = 16;
const CLEAR = 8; // half body width kept away from walls
const MARGIN = 2;

export const COLS = Math.ceil(WORLD.w / CELL) + MARGIN * 2;
export const ROWS = Math.ceil(WORLD.h / CELL) + MARGIN * 2;

const OX = -MARGIN * CELL;
const OY = -MARGIN * CELL;

function worldToCell(x: number, y: number): [number, number] {
  return [Math.floor((x - OX) / CELL), Math.floor((y - OY) / CELL)];
}
function cellCenter(cx: number, cy: number): Vec {
  return { x: OX + cx * CELL + CELL / 2, y: OY + cy * CELL + CELL / 2 };
}

function inBand(v: number, lo: number, hi: number): boolean {
  return v >= lo - CLEAR && v <= hi + CLEAR;
}

/** Is a world point inside any wall (and not in that wall's doorway)? */
export function pointBlocked(x: number, y: number): boolean {
  if (x < 8 || y < 8 || x > WORLD.w - 8 || y > WORLD.h - 8) return true;
  for (const z of ZONES) {
    const midX = z.x + z.w / 2;
    const midY = z.y + z.h / 2;
    // door opening must stay wider than body — leave CLEAR margin inside gap
    const inDoorX = Math.abs(x - midX) <= DOOR_GAP / 2 - CLEAR - 2;
    const inDoorY = Math.abs(y - midY) <= DOOR_GAP / 2 - CLEAR - 2;
    const spanX = x >= z.x - CLEAR && x <= z.x + z.w + CLEAR;
    const spanY = y >= z.y - CLEAR && y <= z.y + z.h + CLEAR;

    if (spanX && inBand(y, z.y, z.y + WALL_T) && !(z.door === "N" && inDoorX)) return true;
    if (spanX && inBand(y, z.y + z.h - WALL_T, z.y + z.h) && !(z.door === "S" && inDoorX)) return true;
    if (spanY && inBand(x, z.x, z.x + WALL_T) && !(z.door === "W" && inDoorY)) return true;
    if (spanY && inBand(x, z.x + z.w - WALL_T, z.x + z.w) && !(z.door === "E" && inDoorY)) return true;
  }
  return false;
}

const GRID: Uint8Array = (() => {
  const g = new Uint8Array(COLS * ROWS);
  for (let cy = 0; cy < ROWS; cy++) {
    for (let cx = 0; cx < COLS; cx++) {
      const c = cellCenter(cx, cy);
      const blocked =
        pointBlocked(c.x, c.y) ||
        pointBlocked(c.x - CELL / 3, c.y) ||
        pointBlocked(c.x + CELL / 3, c.y) ||
        pointBlocked(c.x, c.y - CELL / 3) ||
        pointBlocked(c.x, c.y + CELL / 3);
      g[cy * COLS + cx] = blocked ? 1 : 0;
    }
  }
  return g;
})();

export function isWalkable(x: number, y: number): boolean {
  const [cx, cy] = worldToCell(x, y);
  if (cx < 0 || cy < 0 || cx >= COLS || cy >= ROWS) return false;
  return GRID[cy * COLS + cx] === 0;
}

export function snapToWalkable(p: Vec): Vec {
  const [cx, cy] = worldToCell(p.x, p.y);
  if (cx >= 0 && cy >= 0 && cx < COLS && cy < ROWS && GRID[cy * COLS + cx] === 0) {
    return cellCenter(cx, cy);
  }
  for (let r = 1; r < 32; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const nx = cx + dx;
        const ny = cy + dy;
        if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) continue;
        if (GRID[ny * COLS + nx] === 0) return cellCenter(nx, ny);
      }
    }
  }
  return p;
}

/** A straight walk from a to b stays on open floor *with clearance* — the
 *  centre line AND a body-width either side must be walkable, sampled fine
 *  enough (~4px) that a wall corner can never be stepped over. */
function lineOfSight(a: Vec, b: Vec): boolean {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const steps = Math.max(2, Math.ceil(len / 4));
  const nx = -dy / len;
  const ny = dx / len;
  const off = CLEAR - 2;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const x = a.x + dx * t;
    const y = a.y + dy * t;
    if (!isWalkable(x, y)) return false;
    if (!isWalkable(x + nx * off, y + ny * off)) return false;
    if (!isWalkable(x - nx * off, y - ny * off)) return false;
  }
  return true;
}

interface Node {
  i: number;
  g: number;
  f: number;
  parent: number;
}

/**
 * A* on the grid, then string-pull. Returns world waypoints (excluding start).
 * Never returns a straight clip-through-wall path — if unreachable, walks to
 * the nearest walkable cell toward the goal.
 */
export function findPath(from: Vec, to: Vec): Vec[] {
  const start = snapToWalkable(from);
  const goal = snapToWalkable(to);
  const [sx, sy] = worldToCell(start.x, start.y);
  const [gx, gy] = worldToCell(goal.x, goal.y);
  const si = sy * COLS + sx;
  const gi = gy * COLS + gx;

  if (si === gi) return dist(from, to) < 4 ? [] : [goal];
  if (lineOfSight(from, to)) return [to];
  if (lineOfSight(start, goal)) return [goal];

  const open = new Map<number, Node>();
  const all = new Map<number, Node>();
  const closed = new Set<number>();
  const h = (i: number) => {
    const x = i % COLS;
    const y = (i / COLS) | 0;
    const dx = Math.abs(x - gx);
    const dy = Math.abs(y - gy);
    return dx + dy + (Math.SQRT2 - 2) * Math.min(dx, dy);
  };
  const startNode: Node = { i: si, g: 0, f: h(si), parent: -1 };
  open.set(si, startNode);
  all.set(si, startNode);

  const NEI = [
    [1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1],
    [1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [-1, -1, Math.SQRT2],
  ];

  let guard = 0;
  let best: Node = startNode;
  while (open.size && guard++ < 40000) {
    let cur: Node | null = null;
    for (const n of open.values()) if (!cur || n.f < cur.f) cur = n;
    if (!cur) break;
    if (h(cur.i) < h(best.i)) best = cur;

    if (cur.i === gi) {
      const cells: number[] = [];
      let p: number = cur.i;
      while (p !== -1) {
        cells.push(p);
        p = all.get(p)!.parent;
      }
      cells.reverse();
      const pts = cells.map((i) => cellCenter(i % COLS, (i / COLS) | 0));
      pts.push(goal);
      return stringPull([start, ...pts]).slice(1);
    }
    open.delete(cur.i);
    closed.add(cur.i);
    const cx = cur.i % COLS;
    const cy = (cur.i / COLS) | 0;
    for (const [dx, dy, cost] of NEI) {
      const nx = cx + dx;
      const ny = cy + dy;
      if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) continue;
      const ni = ny * COLS + nx;
      if (GRID[ni] === 1 || closed.has(ni)) continue;
      if (dx !== 0 && dy !== 0) {
        if (GRID[cy * COLS + nx] === 1 || GRID[ny * COLS + cx] === 1) continue;
      }
      const g = cur.g + cost;
      const ex = open.get(ni);
      if (!ex || g < ex.g) {
        const node: Node = { i: ni, g, f: g + h(ni), parent: cur.i };
        open.set(ni, node);
        all.set(ni, node);
      }
    }
  }

  // unreachable — walk as far as A* got toward the goal (never clip walls)
  if (best.i !== si) {
    const cells: number[] = [];
    let p: number = best.i;
    while (p !== -1) {
      cells.push(p);
      p = all.get(p)!.parent;
    }
    cells.reverse();
    const pts = cells.map((i) => cellCenter(i % COLS, (i / COLS) | 0));
    return stringPull([start, ...pts]).slice(1);
  }
  return [];
}

/** Greedily drop waypoints a straight line already clears — but every surviving
 *  segment must pass `lineOfSight`, so a pulled path never clips a wall. */
function stringPull(pts: Vec[]): Vec[] {
  if (pts.length <= 2) return pts;
  const out: Vec[] = [pts[0]];
  let anchor = 0;
  while (anchor < pts.length - 1) {
    let far = anchor + 1;
    for (let j = anchor + 2; j < pts.length; j++) {
      if (lineOfSight(pts[anchor], pts[j])) far = j;
      else break;
    }
    out.push(pts[far]);
    anchor = far;
  }
  return out;
}

function dist(a: Vec, b: Vec): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}
