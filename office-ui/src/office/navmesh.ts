/**
 * Navigation on the Parcha tilemap collision grid.
 */

import { MAP_H, MAP_W, TILE, getParchaAssets, tileBlocked, worldToTile, tileCenter } from "../render/parchaAssets";
import { WORLD, type Vec } from "./layout";

export const COLS = MAP_W;
export const ROWS = MAP_H;

function blockedAt(tx: number, ty: number): boolean {
  const assets = getParchaAssets();
  if (!assets) {
    if (tx < 1 || ty < 1 || tx >= MAP_W - 1 || ty >= MAP_H - 1) return true;
    return false;
  }
  return tileBlocked(tx, ty, assets.blocked);
}

export function pointBlocked(x: number, y: number): boolean {
  if (x < 0 || y < 0 || x >= WORLD.w || y >= WORLD.h) return true;
  const [tx, ty] = worldToTile(x, y);
  return blockedAt(tx, ty);
}

export function isWalkable(x: number, y: number): boolean {
  return !pointBlocked(x, y);
}

export function snapToWalkable(p: Vec): Vec {
  const [cx, cy] = worldToTile(p.x, p.y);
  if (!blockedAt(cx, cy)) return tileCenter(cx, cy);
  for (let r = 1; r < 24; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const nx = cx + dx;
        const ny = cy + dy;
        if (!blockedAt(nx, ny)) return tileCenter(nx, ny);
      }
    }
  }
  return p;
}

function dist(a: Vec, b: Vec) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Supercover LOS — no corner-cutting through blocked tiles. */
function lineOfSight(a: Vec, b: Vec): boolean {
  const [x0, y0] = worldToTile(a.x, a.y);
  const [x1, y1] = worldToTile(b.x, b.y);
  let x = x0;
  let y = y0;
  const dx = Math.abs(x1 - x0);
  const dy = Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1;
  const sy = y0 < y1 ? 1 : -1;
  let err = dx - dy;
  for (;;) {
    if (blockedAt(x, y)) return false;
    if (x === x1 && y === y1) break;
    const e2 = 2 * err;
    if (e2 > -dy) {
      err -= dy;
      x += sx;
      // when stepping diagonally through a corner, both adjacent edges must be open
      if (e2 < dx) {
        if (blockedAt(x, y - sy) && blockedAt(x - sx, y)) return false;
      }
    }
    if (e2 < dx) {
      err += dx;
      y += sy;
    }
  }
  // also dense world-space samples for feet between tile centres
  const steps = Math.max(2, Math.ceil(dist(a, b) / Math.max(2, TILE / 8)));
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    if (!isWalkable(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)) return false;
  }
  return true;
}

export function findPath(from: Vec, to: Vec, opts?: { tilePath?: boolean }): Vec[] {
  const start = snapToWalkable(from);
  const goal = snapToWalkable(to);
  const [sx, sy] = worldToTile(start.x, start.y);
  const [gx, gy] = worldToTile(goal.x, goal.y);
  if (sx === gx && sy === gy) return dist(from, to) < 4 ? [] : [goal];
  // Meetings use full tile paths — diagonal LOS shortcuts often stall on furniture edges.
  if (!opts?.tilePath && lineOfSight(start, goal)) return [goal];

  const key = (x: number, y: number) => y * COLS + x;
  const open = new Map<number, { x: number; y: number; g: number; f: number; parent: number }>();
  const all = new Map<number, { x: number; y: number; g: number; f: number; parent: number }>();
  const closed = new Set<number>();
  const h = (x: number, y: number) => Math.abs(x - gx) + Math.abs(y - gy);
  const si = key(sx, sy);
  const startN = { x: sx, y: sy, g: 0, f: h(sx, sy), parent: -1 };
  open.set(si, startN);
  all.set(si, startN);
  let best = startN;
  let guard = 0;
  const NEI = [
    [1, 0], [-1, 0], [0, 1], [0, -1],
  ];

  while (open.size && guard++ < 20000) {
    let cur: typeof startN | null = null;
    for (const n of open.values()) if (!cur || n.f < cur.f) cur = n;
    if (!cur) break;
    if (h(cur.x, cur.y) < h(best.x, best.y)) best = cur;
    const ci = key(cur.x, cur.y);
    if (cur.x === gx && cur.y === gy) {
      const cells: { x: number; y: number }[] = [];
      let p = ci;
      while (p !== -1) {
        const n = all.get(p)!;
        cells.push({ x: n.x, y: n.y });
        p = n.parent;
      }
      cells.reverse();
      const pts = cells.map((c) => tileCenter(c.x, c.y));
      // drop the start cell; keep every tile centre (no string-pull corner cuts)
      return pts.length <= 1 ? [goal] : [...pts.slice(1), goal];
    }
    open.delete(ci);
    closed.add(ci);
    for (const [dx, dy] of NEI) {
      const nx = cur.x + dx;
      const ny = cur.y + dy;
      if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) continue;
      if (blockedAt(nx, ny)) continue;
      const ni = key(nx, ny);
      if (closed.has(ni)) continue;
      const g = cur.g + 1;
      const ex = open.get(ni);
      if (!ex || g < ex.g) {
        const node = { x: nx, y: ny, g, f: g + h(nx, ny), parent: ci };
        open.set(ni, node);
        all.set(ni, node);
      }
    }
  }

  if (best.parent !== -1 || (best.x === sx && best.y === sy && best.g === 0 && (best.x !== gx || best.y !== gy))) {
    if (best.x === sx && best.y === sy && best.g === 0) return [];
    const cells: { x: number; y: number }[] = [];
    let p = key(best.x, best.y);
    while (p !== -1) {
      const n = all.get(p)!;
      cells.push({ x: n.x, y: n.y });
      p = n.parent;
    }
    cells.reverse();
    return cells.length <= 1
      ? [tileCenter(best.x, best.y)]
      : [...cells.slice(1).map((c) => tileCenter(c.x, c.y))];
  }
  return [];
}
