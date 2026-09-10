import { beforeAll, describe, expect, it } from "vitest";
import { findPath, isWalkable, snapToWalkable } from "../office/navmesh";
import { DESKS, MEETING_SLOTS, SPOTS, WORLD } from "../office/layout";
import { getParchaAssets, MAP_W, MAP_H, TILE } from "../render/parchaAssets";

beforeAll(() => {
  // firstmap collision is baked into getParchaAssets()
  const a = getParchaAssets()!;
  expect(a.blocked.length).toBe(MAP_W * MAP_H);
});

function segmentClearsWalls(a: { x: number; y: number }, b: { x: number; y: number }): boolean {
  const n = Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 4);
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    if (!isWalkable(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)) return false;
  }
  return true;
}

describe("office navmesh", () => {
  it("every desk home and shared spot is on walkable floor", () => {
    for (const d of DESKS) expect(isWalkable(d.home.x, d.home.y), d.agentId).toBe(true);
    for (const [k, v] of Object.entries(SPOTS)) expect(isWalkable(v.x, v.y), k).toBe(true);
  });

  it("map rim / furniture tiles are blocked", () => {
    expect(isWalkable(TILE / 2, TILE / 2)).toBe(false);
    expect(isWalkable(WORLD.w - TILE / 2, WORLD.h - TILE / 2)).toBe(false);
  });

  it("snapToWalkable pulls a blocked point onto open floor", () => {
    const snapped = snapToWalkable({ x: TILE / 2, y: TILE / 2 });
    expect(isWalkable(snapped.x, snapped.y)).toBe(true);
  });

  it("paths between distant rooms never cross a wall", () => {
    const pairs: [string, string][] = [
      ["performance_agent", "skeptic_agent"],
      ["coordinator", "technical_agent"],
      ["diagnostic_agent", "finance_agent"],
      ["prediction_agent", "observer_agent"],
    ];
    const home = Object.fromEntries(DESKS.map((d) => [d.agentId, d.home]));
    for (const [a, b] of pairs) {
      const path = findPath(home[a], home[b]);
      expect(path.length, `${a}->${b} has a path`).toBeGreaterThan(0);
      let prev = home[a];
      for (const wp of path) {
        expect(segmentClearsWalls(prev, wp), `${a}->${b} leg clears walls`).toBe(true);
        prev = wp;
      }
    }
  });

  it("path to the handoff meeting area is reachable from every desk", () => {
    for (const d of DESKS) {
      const path = findPath(d.home, SPOTS.handoff_area);
      expect(path.length, `${d.agentId} -> handoff`).toBeGreaterThan(0);
      const end = path[path.length - 1];
      expect(Math.hypot(end.x - SPOTS.handoff_area.x, end.y - SPOTS.handoff_area.y)).toBeLessThan(20);
    }
  });

  it("returns [] when already at the goal", () => {
    const p = DESKS[0].home;
    expect(findPath(p, p)).toEqual([]);
  });

  it("blocks walking outside the building / off the floor", () => {
    expect(isWalkable(-5, 100)).toBe(false);
    expect(isWalkable(WORLD.w + 5, 100)).toBe(false);
    expect(isWalkable(5, 5)).toBe(false);
  });

  it("meeting slots and shared spots stay reachable and walkable", () => {
    for (const [k, [a, b]] of Object.entries(MEETING_SLOTS)) {
      expect(isWalkable(a.x, a.y), `${k}[0]`).toBe(true);
      expect(isWalkable(b.x, b.y), `${k}[1]`).toBe(true);
    }
    for (const d of DESKS) {
      for (const goal of [SPOTS.handoff_area, SPOTS.data_terminal]) {
        if (Math.hypot(d.home.x - goal.x, d.home.y - goal.y) < 24) continue;
        const path = findPath(d.home, goal);
        expect(path.length, `${d.agentId} -> ${goal.x},${goal.y}`).toBeGreaterThan(0);
        let prev = d.home;
        for (const wp of path) {
          const steps = Math.ceil(Math.hypot(wp.x - prev.x, wp.y - prev.y) / 4);
          for (let i = 0; i <= steps; i++) {
            const t = i / steps;
            expect(
              isWalkable(prev.x + (wp.x - prev.x) * t, prev.y + (wp.y - prev.y) * t),
              `${d.agentId} path clears walls`,
            ).toBe(true);
          }
          prev = wp;
        }
        expect(Math.hypot(path[path.length - 1].x - goal.x, path[path.length - 1].y - goal.y)).toBeLessThan(28);
      }
    }
  });
});
