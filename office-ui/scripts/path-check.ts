/**
 * Quick path sanity (run: npx vite-node scripts/path-check.ts)
 */
import { findPath, isWalkable } from "../src/office/navmesh";
import { homeOf, SPOTS } from "../src/office/layout";

const tests: [string, { x: number; y: number }, { x: number; y: number }][] = [
  ["perf->handoff", homeOf("performance_agent"), SPOTS.handoff_area],
  ["funnel->handoff", homeOf("funnel_agent"), SPOTS.handoff_area],
  ["tech->handoff", homeOf("technical_agent"), SPOTS.handoff_area],
  ["coord->board", homeOf("coordinator"), SPOTS.planning_board],
  ["diag->data", homeOf("diagnostic_agent"), SPOTS.data_terminal],
  ["perf->funnel", homeOf("performance_agent"), homeOf("funnel_agent")],
  ["skeptic->coord", homeOf("skeptic_agent"), homeOf("coordinator")],
  ["skeptic->handoff", homeOf("skeptic_agent"), SPOTS.handoff_area],
];

for (const [name, a, b] of tests) {
  const p = findPath(a, b);
  const clips = p.some((pt, i) => {
    if (i === 0) return false;
    const prev = p[i - 1] ?? a;
    const steps = 8;
    for (let s = 1; s < steps; s++) {
      const t = s / steps;
      const x = prev.x + (pt.x - prev.x) * t;
      const y = prev.y + (pt.y - prev.y) * t;
      if (!isWalkable(x, y)) return true;
    }
    return false;
  });
  console.log(
    name,
    `n=${p.length}`,
    `sw=${isWalkable(a.x, a.y)}`,
    `ew=${isWalkable(b.x, b.y)}`,
    clips ? "CLIPS!" : "ok",
    p.slice(0, 5).map((v) => `${Math.round(v.x)},${Math.round(v.y)}`).join(" > "),
  );
}
