import { describe, expect, it } from "vitest";
import { findPath, isWalkable, snapToWalkable } from "../office/navmesh";
import { DESKS, SPOTS, ZONE_BY_ID } from "../office/layout";

/** A point is "inside a wall" if it's within ~4px of a room's wall line and
 *  not in that room's doorway — the navmesh must never route through one. */
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

  it("wall interiors are blocked", () => {
    const z = ZONE_BY_ID.coordinator;
    // dead centre of the west wall, away from any door
    expect(isWalkable(z.x + 3, z.y + 40)).toBe(false);
  });

  it("snapToWalkable pulls a wall point onto open floor", () => {
    const z = ZONE_BY_ID.performance;
    const snapped = snapToWalkable({ x: z.x + 2, y: z.y + 60 });
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
    expect(findPath({ x: 240, y: 250 }, { x: 240, y: 250 })).toEqual([]);
  });
});
