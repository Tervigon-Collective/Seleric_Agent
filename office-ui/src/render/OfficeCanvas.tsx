import { useCallback, useEffect, useMemo, useRef } from "react";
import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";
import { DESK_BY_ID, MEETING_SLOTS, SPOTS, WORLD, type Vec } from "../office/layout";
import { animationFor, destinationFor, STATUS_TOKEN } from "../office/stateMachine";
import { findPath, isWalkable, snapToWalkable } from "../office/navmesh";
import { paletteFor } from "./palette";
import { loadParchaAssets, TILE, tileCenter, worldToTile } from "./parchaAssets";
import {
  drawApproaching,
  drawCharacter,
  drawConversation,
  drawFloor,
  drawRoomLabels,
  drawSpeechBubble,
  type Facing,
} from "./sprites";
import type { OfficeAgent } from "../types";

interface Pos {
  x: number;
  y: number;
  px: number;
  py: number;
  facing: Facing;
  path: Vec[];
  goalKey: string;
  stuck: number;
}

/** An in-person interaction: walk together → talk → return to desks. */
interface Interaction {
  key: string;
  kind: "meeting" | "fetch";
  /** Meeting phases; fetch stays on approach until hold ends. */
  phase: "approach" | "talk" | "disperse";
  actors: string[];
  slot: Record<string, Vec>;
  bornAt: number;
  arrivedAt?: number;
  disperseAt?: number;
  hold: number;
  /** Lines spoken once they meet (alternating). */
  lines?: string[];
}

const LERP = (a: number, b: number, t: number) => a + (b - a) * t;
const WALK_SPEED = 175;
/** Meetings walk at normal office pace — no sprint. */
const MEET_WALK = WALK_SPEED;
/** Last-resort snap only when pathless and still far after a long walk. */
const MEET_SNAP_AFTER = 12;
const DISPERSE_TIMEOUT = 8;
/** Match sprite width so feet can't sit in painted desk/plant cracks. */
const BODY_R = Math.round(TILE * 0.38);
const dist = (a: Vec, b: Vec) => Math.hypot(a.x - b.x, a.y - b.y);
const keyOf = (v: Vec) => `${Math.round(v.x)},${Math.round(v.y)}`;

/** Body clearance against the collision grid (ok while walking between centres). */
function feetClear(x: number, y: number): boolean {
  if (!isWalkable(x, y)) return false;
  for (const [ox, oy] of [
    [BODY_R, 0],
    [-BODY_R, 0],
    [0, BODY_R],
    [0, -BODY_R],
    [BODY_R * 0.7, BODY_R * 0.7],
    [-BODY_R * 0.7, BODY_R * 0.7],
    [BODY_R * 0.7, -BODY_R * 0.7],
    [-BODY_R * 0.7, -BODY_R * 0.7],
  ] as const) {
    if (!isWalkable(x + ox, y + oy)) return false;
  }
  return true;
}

/** Idle / goal stand — near tile centre with full body clearance. */
function canStand(x: number, y: number): boolean {
  const c = tileCenter(...worldToTile(x, y));
  if (Math.hypot(x - c.x, y - c.y) > TILE * 0.42) return false;
  return feetClear(x, y);
}

function snapStand(p: Vec): Vec {
  const s = snapToWalkable(p);
  if (canStand(s.x, s.y)) return s;
  const [cx, cy] = worldToTile(s.x, s.y);
  for (let r = 1; r < 10; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const c = tileCenter(cx + dx, cy + dy);
        if (canStand(c.x, c.y)) return c;
      }
    }
  }
  return s;
}

/** Swept clearance a→b — always use body clearance so feet never clip walls. */
function canStep(ax: number, ay: number, bx: number, by: number): boolean {
  const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / 2));
  for (let i = 1; i <= n; i++) {
    const t = i / n;
    const x = ax + (bx - ax) * t;
    const y = ay + (by - ay) * t;
    if (!feetClear(x, y)) return false;
  }
  return true;
}

function rescueToFloor(p: Pos): void {
  // while walking: step back — never wipe the path with a hard snap
  if (p.path.length > 0) {
    if (feetClear(p.x, p.y)) return;
    if (feetClear(p.px, p.py)) {
      p.x = p.px;
      p.y = p.py;
      return;
    }
    const s = snapToWalkable({ x: p.x, y: p.y });
    // soft pull toward walkable centre (no teleport jump)
    p.x = LERP(p.x, s.x, 0.35);
    p.y = LERP(p.y, s.y, 0.35);
    if (!feetClear(p.x, p.y)) {
      p.x = s.x;
      p.y = s.y;
    }
    p.goalKey = "";
    return;
  }
  // idle: park on a tile centre with clearance
  const c = tileCenter(...worldToTile(p.x, p.y));
  if (canStand(c.x, c.y)) {
    p.x = LERP(p.x, c.x, 0.4);
    p.y = LERP(p.y, c.y, 0.4);
    return;
  }
  if (canStand(p.px, p.py)) {
    p.x = p.px;
    p.y = p.py;
    return;
  }
  const s = snapStand({ x: p.x, y: p.y });
  p.x = LERP(p.x, s.x, 0.5);
  p.y = LERP(p.y, s.y, 0.5);
  if (!feetClear(p.x, p.y)) {
    p.x = s.x;
    p.y = s.y;
  }
}

export function OfficeCanvas({ dark }: { dark: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const posRef = useRef<Record<string, Pos>>({});
  const interactionsRef = useRef<Interaction[]>([]);
  const pendingMeetingsRef = useRef<Interaction[]>([]);
  const seenInteractionRef = useRef<Set<string>>(new Set());
  const camRef = useRef({ x: WORLD.w / 2, y: WORLD.h / 2, zoom: 1, targetZoom: 1, fitted: false });
  const assetsReadyRef = useRef(false);
  const dragRef = useRef<{ on: boolean; sx: number; sy: number; cx: number; cy: number }>({
    on: false, sx: 0, sy: 0, cx: 0, cy: 0,
  });
  const dprRef = useRef(1);

  const hover = useOffice((s) => s.hover);
  const select = useOffice((s) => s.select);

  const screenToWorld = useCallback((sx: number, sy: number) => {
    const c = canvasRef.current!;
    const rect = c.getBoundingClientRect();
    const cam = camRef.current;
    const x = (sx - rect.left - rect.width / 2) / cam.zoom + cam.x;
    const y = (sy - rect.top - rect.height / 2) / cam.zoom + cam.y;
    return { x, y };
  }, []);

  const pickAgent = useCallback((sx: number, sy: number): string | null => {
    const w = screenToWorld(sx, sy);
    let best: string | null = null;
    let bestD = 26;
    for (const [id, p] of Object.entries(posRef.current)) {
      const d = Math.hypot(p.x - w.x, p.y - w.y);
      if (d < bestD) { bestD = d; best = id; }
    }
    return best;
  }, [screenToWorld]);

  // pointer handlers
  useEffect(() => {
    const c = canvasRef.current!;
    const onMove = (e: MouseEvent) => {
      if (dragRef.current.on) {
        const cam = camRef.current;
        cam.x = dragRef.current.cx - (e.clientX - dragRef.current.sx) / cam.zoom;
        cam.y = dragRef.current.cy - (e.clientY - dragRef.current.sy) / cam.zoom;
        useOffice.getState().followMode !== "off" && useOffice.getState().setFollow("off");
        return;
      }
      const id = pickAgent(e.clientX, e.clientY);
      hover(id);
      c.style.cursor = id ? "pointer" : "grab";
    };
    const onDown = (e: MouseEvent) => {
      const id = pickAgent(e.clientX, e.clientY);
      if (id) { select(id); return; }
      dragRef.current = { on: true, sx: e.clientX, sy: e.clientY, cx: camRef.current.x, cy: camRef.current.y };
      c.style.cursor = "grabbing";
    };
    const onUp = () => { dragRef.current.on = false; c.style.cursor = "grab"; };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const cam = camRef.current;
      cam.targetZoom = Math.min(3.2, Math.max(0.4, cam.targetZoom * (e.deltaY > 0 ? 0.9 : 1.1)));
      cam.fitted = true; // user took over
    };
    c.addEventListener("mousemove", onMove);
    c.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      c.removeEventListener("mousemove", onMove);
      c.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      c.removeEventListener("wheel", onWheel);
    };
  }, [pickAgent, hover, select]);

  // resize + fit camera so the painted office fills the stage (Parcha-like framing)
  useEffect(() => {
    const c = canvasRef.current!;
    const fit = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      dprRef.current = dpr;
      c.width = c.clientWidth * dpr;
      c.height = c.clientHeight * dpr;
      const ctx = c.getContext("2d");
      if (ctx) ctx.imageSmoothingEnabled = false;
      // Fill stage without ultra-zoom (claustrophobic = looks "stuck")
      const pad = 0.9;
      const z = Math.min(c.clientWidth / WORLD.w, c.clientHeight / WORLD.h) * pad;
      const cam = camRef.current;
      if (!cam.fitted) {
        cam.targetZoom = Math.max(0.55, Math.min(1.6, z));
        cam.zoom = cam.targetZoom;
        cam.x = WORLD.w / 2;
        cam.y = WORLD.h / 2;
        cam.fitted = true;
      }
    };
    const ro = new ResizeObserver(fit);
    ro.observe(c);
    fit();
    return () => ro.disconnect();
  }, []);

  // keyboard: [ / ] cycle selected agent, Esc clears
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      const st = useOffice.getState();
      const order = st.agentOrder;
      if (e.key === "Escape") { st.select(null); return; }
      if (e.key !== "[" && e.key !== "]") return;
      const i = st.selectedAgentId ? order.indexOf(st.selectedAgentId) : -1;
      const next = e.key === "]" ? (i + 1) % order.length : (i - 1 + order.length) % order.length;
      st.select(order[next] ?? null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Parcha tileset + character sprites (MIT assets)
  useEffect(() => {
    let cancelled = false;
    // tile scale / world size can change on HMR — reset positions + camera
    posRef.current = {};
    camRef.current = { x: WORLD.w / 2, y: WORLD.h / 2, zoom: 1, targetZoom: 1, fitted: false };
    loadParchaAssets()
      .then(() => {
        if (!cancelled) assetsReadyRef.current = true;
      })
      .catch((err) => console.warn("[office] parcha assets failed", err));
    return () => {
      cancelled = true;
    };
  }, []);

  // render loop
  useEffect(() => {
    let raf = 0;
    let t0 = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - t0) / 1000);
      t0 = now;
      draw(now / 1000, dt);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dark]);

  const draw = (time: number, dt: number) => {
    const c = canvasRef.current;
    if (!c) return;
    const ctx = c.getContext("2d")!;
    const dpr = dprRef.current;
    const pal = paletteFor(dark);
    const st = useOffice.getState();
    const agents = st.agents;
    const cam = camRef.current;

    cam.zoom = LERP(cam.zoom, cam.targetZoom, Math.min(1, dt * 8));

    // camera follow
    const followId =
      st.followMode === "lead"
        ? st.leadAgentId
        : st.followMode === "activity"
          ? st.lastEvent?.agentId ?? null
          : null;
    if (followId && posRef.current[followId]) {
      const p = posRef.current[followId];
      cam.x = LERP(cam.x, p.x, Math.min(1, dt * 2.2));
      cam.y = LERP(cam.y, p.y, Math.min(1, dt * 2.2));
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, c.clientWidth, c.clientHeight);
    ctx.save();
    ctx.translate(Math.round(c.clientWidth / 2), Math.round(c.clientHeight / 2));
    ctx.scale(cam.zoom, cam.zoom);
    ctx.translate(-Math.round(cam.x), -Math.round(cam.y));

    drawFloor(ctx, pal, WORLD);

    // active zones = working agents + current lead (causal frontier cue)
    const activeZones = new Set<string>();
    for (const a of Object.values(agents)) {
      if (a.status !== "idle" && a.status !== "offline") {
        const z = DESK_BY_ID[a.agentId]?.zone;
        if (z) activeZones.add(z);
      }
      if (a.missionLead) {
        const z = DESK_BY_ID[a.agentId]?.zone;
        if (z) activeZones.add(z);
      }
    }
    const order = st.agentOrder.length ? st.agentOrder : Object.keys(agents);

    // --- in-person meetings: walk → talk → return to desk --------------------
    // Scan the whole timeline — at demo speed, several events can land between
    // frames; only looking at lastEvent skipped most walk-and-meets.
    for (const ev of st.timeline) {
      if (seenInteractionRef.current.has(ev.eventId)) continue;
      seenInteractionRef.current.add(ev.eventId);
      const ix = buildInteraction(ev, agents, time, posRef.current);
      if (!ix) continue;
      if (ix.kind === "meeting") pendingMeetingsRef.current.push(ix);
      else interactionsRef.current.push(ix);
    }
    // One meeting at a time so pairs finish walk + talk + disperse
    if (!interactionsRef.current.some((q) => q.kind === "meeting") && pendingMeetingsRef.current.length) {
      const next = pendingMeetingsRef.current.shift()!;
      if (next.actors.length >= 2) {
        const refreshed = meetingOf(
          [next.actors[0], next.actors[1]],
          posRef.current,
          time,
          next.key,
          next.hold,
          next.lines ?? [],
        );
        interactionsRef.current.push(refreshed);
      } else {
        interactionsRef.current.push(next);
      }
    }
    if (pendingMeetingsRef.current.length > 6) {
      pendingMeetingsRef.current = pendingMeetingsRef.current.slice(-4);
    }

    const override: Record<string, Vec> = {};
    const talking = new Set<string>();
    const approaching = new Set<string>();
    const dispersing = new Set<string>();

    // Pending meet actors stay at their desks until their scene starts
    for (const pend of pendingMeetingsRef.current) {
      for (const id of pend.actors) {
        const home = DESK_BY_ID[id]?.home;
        if (home) override[id] = home;
      }
    }

    interactionsRef.current = interactionsRef.current.filter((ix) => {
      if (ix.kind === "fetch") {
        for (const id of ix.actors) if (ix.slot[id]) override[id] = ix.slot[id];
        const allHere = ix.actors.every((id) => {
          const p = posRef.current[id];
          return p && dist(p, ix.slot[id]) < TILE * 0.55;
        });
        if (allHere && ix.arrivedAt == null) ix.arrivedAt = time;
        const finished = ix.arrivedAt != null && time - ix.arrivedAt > ix.hold;
        const timedOut = time - ix.bornAt > 55;
        return !finished && !timedOut;
      }

      // --- meeting phases ---
      if (ix.phase === "approach") {
        for (const id of ix.actors) if (ix.slot[id]) override[id] = ix.slot[id];
        ix.actors.forEach((id) => approaching.add(id));

        // Last resort: only soft-slide if pathless AND still far after a long walk
        if (time - ix.bornAt > MEET_SNAP_AFTER) {
          for (const id of ix.actors) {
            const s = ix.slot[id];
            const p = posRef.current[id];
            if (!p || !s) continue;
            if (dist(p, s) < TILE * 0.55) continue;
            if (p.path.length > 0) continue;
            p.x = LERP(p.x, s.x, 0.35);
            p.y = LERP(p.y, s.y, 0.35);
            if (dist(p, s) < TILE * 0.4) {
              p.x = s.x;
              p.y = s.y;
              p.path = [];
              p.stuck = 0;
              p.goalKey = keyOf(s);
            }
          }
        }

        const allHere = ix.actors.every((id) => {
          const p = posRef.current[id];
          return p && dist(p, ix.slot[id]) < TILE * 0.55;
        });
        if (allHere) {
          ix.phase = "talk";
          ix.arrivedAt = time;
        }
        return time - ix.bornAt <= 55;
      }

      if (ix.phase === "talk") {
        for (const id of ix.actors) if (ix.slot[id]) override[id] = ix.slot[id];
        ix.actors.forEach((id) => talking.add(id));
        if (ix.arrivedAt != null && time - ix.arrivedAt > ix.hold) {
          ix.phase = "disperse";
          ix.disperseAt = time;
          for (const id of ix.actors) {
            const home = DESK_BY_ID[id]?.home ?? snapStand(ix.slot[id]);
            ix.slot[id] = home;
            const p = posRef.current[id];
            if (p) {
              p.goalKey = "";
              p.path = [];
              p.stuck = 0;
            }
          }
        }
        return true;
      }

      // disperse — walk home
      for (const id of ix.actors) if (ix.slot[id]) override[id] = ix.slot[id];
      ix.actors.forEach((id) => dispersing.add(id));
      const allHome = ix.actors.every((id) => {
        const p = posRef.current[id];
        return p && dist(p, ix.slot[id]) < TILE * 0.55;
      });
      const disperseDone =
        allHome || (ix.disperseAt != null && time - ix.disperseAt > DISPERSE_TIMEOUT);
      return !disperseDone;
    });

    // Demo / debug: office is busy while a meeting is queued or on the floor
    const officeBusy =
      pendingMeetingsRef.current.length > 0 ||
      interactionsRef.current.some((q) => q.kind === "meeting");
    (window as unknown as { __officeBusy?: boolean }).__officeBusy = officeBusy;

    // Fan out agents that share a destination (a shared spot like the lounge or
    // the data terminal) so they don't pile onto one point and jitter forever.
    const goalGroups: Record<string, string[]> = {};
    for (const id of order) {
      if (!agents[id] || override[id]) continue;
      const k = keyOf(destinationFor(agents[id]));
      (goalGroups[k] ??= []).push(id);
    }
    const goalFor = (id: string): Vec => {
      if (override[id]) {
        // keep exact meeting feet — don't snap partners apart
        const s = override[id];
        return isWalkable(s.x, s.y) ? s : snapStand(s);
      }
      const base = destinationFor(agents[id]);
      const grp = goalGroups[keyOf(base)];
      if (!grp || grp.length < 2) return snapStand(base);
      const idx = grp.indexOf(id);
      const [bx, by] = worldToTile(base.x, base.y);
      const ring: Vec[] = [tileCenter(bx, by)];
      for (let r = 1; r <= 3 && ring.length < grp.length; r++) {
        for (let dy = -r; dy <= r; dy++) {
          for (let dx = -r; dx <= r; dx++) {
            if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
            const c = tileCenter(bx + dx, by + dy);
            if (canStand(c.x, c.y)) ring.push(c);
          }
        }
      }
      return ring[idx % ring.length] ?? snapStand(base);
    };

    // --- move every character along a real path (no wall clipping) ----------
    for (const id of order) {
      const a = agents[id];
      if (!a) continue;
      const finalGoal = goalFor(id);
      let p = posRef.current[id];
      if (!p) {
        const home = DESK_BY_ID[id]?.home ?? finalGoal;
        const start = snapStand(home);
        p = { x: start.x, y: start.y, px: start.x, py: start.y, facing: "S", path: [], goalKey: "", stuck: 0 };
        posRef.current[id] = p;
      }
      p.px = p.x;
      p.py = p.y;
      const meetLoose = !!override[id];
      const inScene = talking.has(id) || approaching.has(id) || dispersing.has(id);
      if (!meetLoose) rescueToFloor(p);

      const gk = keyOf(finalGoal);
      if (gk !== p.goalKey) {
        p.goalKey = gk;
        p.path = st.reducedMotion
          ? [finalGoal]
          : findPath(p, finalGoal, meetLoose ? { tilePath: true } : undefined);
        p.stuck = 0;
      }

      // Face partner while talking; hold position softly
      if (talking.has(id) && !st.reducedMotion) {
        p.path = [];
        const nx = p.x + (finalGoal.x - p.x) * Math.min(1, dt * 2.5);
        const ny = p.y + (finalGoal.y - p.y) * Math.min(1, dt * 2.5);
        if (canStep(p.x, p.y, nx, ny)) {
          p.x = nx;
          p.y = ny;
        }
        const other = interactionsRef.current
          .find((q) => q.actors.includes(id))
          ?.actors.find((o) => o !== id);
        const op = other ? posRef.current[other] : undefined;
        if (op) {
          p.facing =
            Math.abs(op.x - p.x) > Math.abs(op.y - p.y)
              ? op.x > p.x ? "E" : "W"
              : op.y > p.y ? "S" : "N";
        }
        continue;
      }

      if (st.reducedMotion) {
        const g = meetLoose || canStand(finalGoal.x, finalGoal.y) ? finalGoal : snapToWalkable(finalGoal);
        p.x = g.x;
        p.y = g.y;
        p.path = [];
      } else {
        while (p.path.length > 1 && dist(p, p.path[0]) < TILE * 0.2) p.path.shift();
        const wp = p.path[0] ?? finalGoal;
        const dx = wp.x - p.x;
        const dy = wp.y - p.y;
        const d = Math.hypot(dx, dy);
        if (d > 0.5) {
          const speed = meetLoose || inScene ? MEET_WALK : WALK_SPEED;
          const move = Math.min(speed * dt, d);
          const sx = (dx / d) * move;
          const sy = (dy / d) * move;
          const partner = interactionsRef.current
            .find((q) => q.kind === "meeting" && q.actors.includes(id) && q.phase !== "disperse")
            ?.actors.find((o) => o !== id);
          const blockedByAgent = (nx: number, ny: number) => {
            for (const oid of order) {
              if (oid === id || oid === partner) continue;
              const op = posRef.current[oid];
              if (!op) continue;
              if (Math.hypot(op.x - nx, op.y - ny) < TILE * 0.85) return true;
            }
            return false;
          };
          const tryMove = (nx: number, ny: number) =>
            canStep(p.x, p.y, nx, ny) && !blockedByAgent(nx, ny);
          if (tryMove(p.x + sx, p.y + sy)) {
            p.x += sx;
            p.y += sy;
            p.stuck = 0;
          } else if (tryMove(p.x + sx, p.y)) {
            p.x += sx;
            p.stuck += dt;
          } else if (tryMove(p.x, p.y + sy)) {
            p.y += sy;
            p.stuck += dt;
          } else {
            p.stuck += dt;
          }
        } else if (p.path.length) {
          p.path.shift();
        }
      // Prefer repath; hard snap only after a long pathless wedge (no mid-walk jumps)
        if (p.stuck > 1.4 && p.stuck <= 4) {
          p.path = findPath(p, finalGoal, meetLoose ? { tilePath: true } : undefined);
          p.stuck = 4.01;
        } else if (meetLoose && dist(p, finalGoal) > TILE * 0.55 && p.stuck > 4) {
          p.path = findPath(p, finalGoal, { tilePath: true });
          if (p.stuck > 10 && p.path.length === 0) {
            // last resort — soft slide onto the goal tile
            p.x = LERP(p.x, finalGoal.x, 0.45);
            p.y = LERP(p.y, finalGoal.y, 0.45);
            if (dist(p, finalGoal) < TILE * 0.4) {
              p.x = finalGoal.x;
              p.y = finalGoal.y;
              p.path = [];
              p.stuck = 0;
            }
          } else {
            p.stuck += dt;
          }
        } else if (!meetLoose && p.stuck > 8) {
          const free = snapStand(finalGoal);
          p.x = LERP(p.x, free.x, 0.4);
          p.y = LERP(p.y, free.y, 0.4);
          if (dist(p, free) < 4) {
            p.x = free.x;
            p.y = free.y;
            p.path = [];
            p.stuck = 0;
          }
        }
      }

      if (!meetLoose) rescueToFloor(p);
      // Anyone standing in furniture / wall cracks — ease back onto open floor
      if (!inScene && !feetClear(p.x, p.y)) {
        const safe = snapStand(p);
        p.x = LERP(p.x, safe.x, 0.55);
        p.y = LERP(p.y, safe.y, 0.55);
        if (dist(p, safe) < 6) {
          p.x = safe.x;
          p.y = safe.y;
          p.path = [];
          p.goalKey = "";
        }
      }

      const vx = p.x - p.px;
      const vy = p.y - p.py;
      if (Math.hypot(vx, vy) > 0.35) {
        p.facing = Math.abs(vx) > Math.abs(vy) ? (vx > 0 ? "E" : "W") : vy > 0 ? "S" : "N";
      } else if (talking.has(id) || approaching.has(id)) {
        const other = interactionsRef.current
          .find((q) => q.actors.includes(id))
          ?.actors.find((o) => o !== id);
        const op = other ? posRef.current[other] : undefined;
        if (op) p.facing = Math.abs(op.x - p.x) > Math.abs(op.y - p.y) ? (op.x > p.x ? "E" : "W") : op.y > p.y ? "S" : "N";
      }
    }

    // Keep idle agents from stacking. Skip anyone walking a path or in a meet scene.
    const ids = order.filter((id) => posRef.current[id]);
    const claimed = new Map<string, string>();
    const occupied: Array<[number, number]> = [];
    const tooClose = (tx: number, ty: number, id: string) => {
      if (talking.has(id) || approaching.has(id) || dispersing.has(id) || override[id]) return false;
      return occupied.some(([ox, oy]) => Math.max(Math.abs(ox - tx), Math.abs(oy - ty)) < 2);
    };
    const prefer = [...ids].sort((a, b) => {
      const aa = agents[a];
      const bb = agents[b];
      const pa = aa?.missionLead ? 0 : aa && aa.status !== "idle" ? 1 : 2;
      const pb = bb?.missionLead ? 0 : bb && bb.status !== "idle" ? 1 : 2;
      return pa - pb;
    });
    for (const id of prefer) {
      const p = posRef.current[id]!;
      // Don't hard-snap walkers or meeting actors onto tile centres
      if (override[id] || approaching.has(id) || talking.has(id) || dispersing.has(id) || p.path.length > 0) {
        const [tx, ty] = worldToTile(p.x, p.y);
        claimed.set(`${tx},${ty}`, id);
        occupied.push([tx, ty]);
        continue;
      }
      if (!override[id]) rescueToFloor(p);
      const [tx, ty] = worldToTile(p.x, p.y);
      const tryClaim = (nx: number, ny: number, requireGap: boolean): boolean => {
        const ck = `${nx},${ny}`;
        if (claimed.has(ck)) return false;
        const c = tileCenter(nx, ny);
        if (!canStand(c.x, c.y)) return false;
        if (requireGap && tooClose(nx, ny, id)) return false;
        // soft slide onto the free tile — avoid pop-jumps
        p.x = LERP(p.x, c.x, 0.55);
        p.y = LERP(p.y, c.y, 0.55);
        if (dist(p, c) < 3) {
          p.x = c.x;
          p.y = c.y;
        }
        claimed.set(ck, id);
        occupied.push([nx, ny]);
        return true;
      };
      if (tryClaim(tx, ty, true)) continue;
      let placed = false;
      for (let r = 0; r < 10 && !placed; r++) {
        for (let dy = -r; dy <= r && !placed; dy++) {
          for (let dx = -r; dx <= r && !placed; dx++) {
            if (Math.max(Math.abs(dx), Math.abs(dy)) !== r && r > 0) continue;
            if (tryClaim(tx + dx, ty + dy, true)) placed = true;
          }
        }
      }
      if (!placed) {
        for (let r = 0; r < 8 && !placed; r++) {
          for (let dy = -r; dy <= r && !placed; dy++) {
            for (let dx = -r; dx <= r && !placed; dx++) {
              if (Math.max(Math.abs(dx), Math.abs(dy)) !== r && r > 0) continue;
              if (tryClaim(tx + dx, ty + dy, false)) placed = true;
            }
          }
        }
      }
      if (!placed) {
        const s = snapStand(p);
        p.x = s.x;
        p.y = s.y;
      }
    }

    // Soft personal-space: stop stacking / walking through each other (no teleports).
    // Meeting partners may stand adjacent; everyone else keeps ~1.5 tiles clear.
    {
      const bodies = ids.map((id) => ({ id, p: posRef.current[id]! }));
      const partnerOf = (id: string): string | undefined => {
        const ix = interactionsRef.current.find(
          (q) => q.kind === "meeting" && q.actors.includes(id) && q.phase !== "disperse",
        );
        return ix?.actors.find((o) => o !== id);
      };
      for (let i = 0; i < bodies.length; i++) {
        for (let j = i + 1; j < bodies.length; j++) {
          const A = bodies[i];
          const B = bodies[j];
          const paired = partnerOf(A.id) === B.id;
          const minD = paired ? TILE * 0.9 : TILE * 1.55;
          const d = dist(A.p, B.p);
          if (d >= minD || d < 0.01) continue;
          const ux = (A.p.x - B.p.x) / d;
          const uy = (A.p.y - B.p.y) / d;
          const push = Math.min((minD - d) * 0.45, WALK_SPEED * dt * 1.2);
          const moveA = !(talking.has(A.id) && paired);
          const moveB = !(talking.has(B.id) && paired);
          // Prefer shoving the non-meeting / lower-priority body
          const aMeet = approaching.has(A.id) || talking.has(A.id) || dispersing.has(A.id);
          const bMeet = approaching.has(B.id) || talking.has(B.id) || dispersing.has(B.id);
          if (moveA && (!aMeet || bMeet)) {
            const nx = A.p.x + ux * push;
            const ny = A.p.y + uy * push;
            if (canStep(A.p.x, A.p.y, nx, ny)) {
              A.p.x = nx;
              A.p.y = ny;
            }
          }
          if (moveB && (!bMeet || aMeet)) {
            const nx = B.p.x - ux * push;
            const ny = B.p.y - uy * push;
            if (canStep(B.p.x, B.p.y, nx, ny)) {
              B.p.x = nx;
              B.p.y = ny;
            }
          }
        }
      }
    }

    // Room labels under characters; skip tags that would cover feet nameplates
    const feet = order
      .map((id) => posRef.current[id])
      .filter(Boolean)
      .map((p) => ({ x: p!.x, y: p!.y }));
    drawRoomLabels(ctx, pal, activeZones, feet);

    // Talk / approach links UNDER sprites so the line never crosses faces
    for (const ix of interactionsRef.current) {
      if (ix.kind !== "meeting" || ix.actors.length < 2) continue;
      const a = posRef.current[ix.actors[0]];
      const b = posRef.current[ix.actors[1]];
      if (!a || !b) continue;
      if (ix.phase === "approach") {
        // Only draw when reasonably close — long lines cut through the whole floor
        if (dist(a, b) < TILE * 5) drawApproaching(ctx, pal, a, b, time);
      } else if (ix.phase === "talk") {
        drawConversation(ctx, pal, a, b, time, true);
      }
    }

    // --- draw characters (y-sorted; active + lead on top) ------------------
    const sorted = [...order].sort((a, b) => {
      const r = rank(agents[a]) - rank(agents[b]);
      if (r !== 0) return r;
      return (posRef.current[a]?.y ?? 0) - (posRef.current[b]?.y ?? 0);
    });
    for (const id of sorted) {
      const a = agents[id];
      const spec = SPEC_BY_ID[id];
      const p = posRef.current[id];
      if (!a || !spec || !p) continue;
      const finalGoal = goalFor(id);
      const arrived = dist(p, finalGoal) < TILE * 0.2;
      const atDesk = arrived && dist(p, DESK_BY_ID[id]?.home ?? { x: -9e9, y: -9e9 }) < TILE * 0.4;
      const deskWork = ["working", "tool_running", "thinking", "planning", "reviewing", "retrieving_evidence"].includes(a.status);
      const seated = atDesk && (deskWork || a.status === "idle");
      const helpers = (st.parallelTasks[id] ?? []).filter((t) => t.status !== "done").length;
      const face: Facing = seated ? "N" : p.facing;
      const inMeet = talking.has(id) || approaching.has(id) || dispersing.has(id);
      const showChip =
        !inMeet &&
        (st.selectedAgentId === id || a.missionLead || st.lastEvent?.agentId === id);
      // Stagger crowded nameplates sideways so stacked agents don't cover each other
      let labelNudgeX = 0;
      for (const oid of order) {
        if (oid === id) continue;
        const op = posRef.current[oid];
        if (!op) continue;
        if (Math.abs(op.y - p.y) < TILE * 0.9 && Math.abs(op.x - p.x) < TILE * 1.2) {
          labelNudgeX = hashPhase(id) > 0.5 ? 18 : -18;
          break;
        }
      }
      drawCharacter(ctx, pal, {
        x: Math.round(p.x),
        y: Math.round(p.y),
        spec,
        token: STATUS_TOKEN[a.status],
        anim: arrived ? animationFor(a.status) : "walk",
        facing: face,
        lead: a.missionLead,
        hovered: st.hoveredAgentId === id,
        selected: st.selectedAgentId === id,
        phase: time + hashPhase(id),
        reducedMotion: st.reducedMotion,
        helpers: showChip ? helpers : 0,
        seated,
        talking: talking.has(id) || approaching.has(id),
        action: showChip ? a.currentAction : undefined,
        labelNudgeX,
      });
    }

    // Speech bubbles last (above everyone) — one speaker at a time
    for (const ix of interactionsRef.current) {
      if (ix.kind !== "meeting" || ix.actors.length < 2) continue;
      if (ix.phase === "disperse") continue;
      const lines = ix.lines?.length
        ? ix.lines
        : ix.actors.map((id) => agents[id]?.currentAction).filter(Boolean) as string[];
      if (ix.phase === "approach") {
        const walker = ix.actors[Math.floor(time * 0.55) % ix.actors.length];
        const wp = posRef.current[walker];
        if (wp) drawSpeechBubble(ctx, pal, wp.x, wp.y - 6, "Coming over…");
        continue;
      }
      if (ix.phase !== "talk" || !lines.length) continue;
      const speakerIdx = Math.floor(time * 0.55) % lines.length;
      const speakerId = ix.actors[speakerIdx % ix.actors.length];
      const sp = posRef.current[speakerId];
      const line = lines[speakerIdx % lines.length];
      if (sp && line) drawSpeechBubble(ctx, pal, sp.x, sp.y - 6, shortenSpeak(line));
    }

    ctx.restore();

    // debug hook for verifying walk-and-meet (browser console / CDP)
    (window as unknown as { __officeIx?: unknown }).__officeIx = {
      busy: officeBusy,
      active: interactionsRef.current.map((q) => ({
        key: q.key,
        kind: q.kind,
        phase: q.phase,
        actors: q.actors,
        arrived: q.arrivedAt != null,
        age: +(time - q.bornAt).toFixed(1),
        slots: Object.fromEntries(
          q.actors.map((id) => {
            const s = q.slot[id];
            const p = posRef.current[id];
            return [
              id,
              {
                slot: s ? [Math.round(s.x), Math.round(s.y)] : null,
                pos: p ? [Math.round(p.x), Math.round(p.y)] : null,
                dist: p && s ? +dist(p, s).toFixed(1) : null,
                path: p?.path.length ?? null,
                stuck: p ? +p.stuck.toFixed(2) : null,
              },
            ];
          }),
        ),
      })),
      pending: pendingMeetingsRef.current.length,
      timeline: st.timeline.length,
      talking: [...talking],
      approaching: [...approaching],
      dispersing: [...dispersing],
    };
  };

  const followMode = useOffice((s) => s.followMode);
  const label = useMemo(
    () => ({ off: "Free camera", lead: "Following lead", activity: "Following activity" }[followMode]),
    [followMode],
  );

  return (
    <div className="office-canvas-wrap">
      <canvas ref={canvasRef} className="office-canvas" aria-label="Seleric office floor" />
      <div className="cam-hint">{label} · scroll zoom · drag pan · [ ] cycle agents</div>
    </div>
  );
}

function rank(a?: OfficeAgent): number {
  if (!a) return 0;
  return a.status === "idle" || a.status === "offline" ? 0 : a.missionLead ? 2 : 1;
}

/**
 * Pick two adjacent walkable tiles near the pair's midpoint so they walk
 * toward each other and stand face-to-face.
 */
function meetPairSlots(a: Vec, b: Vec): [Vec, Vec] {
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const [mx, my] = worldToTile(mid.x, mid.y);
  const candidates: Array<[Vec, Vec]> = [];
  // Search a wider plaza around the midpoint for an adjacent face-to-face pair.
  for (let r = 0; r < 10; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r && r > 0) continue;
        const c0 = tileCenter(mx + dx, my + dy);
        if (!isWalkable(c0.x, c0.y)) continue;
        // Prefer open plaza tiles with neighbour clearance (no wall clip at meet)
        const open0 =
          isWalkable(c0.x + BODY_R * 0.5, c0.y) &&
          isWalkable(c0.x - BODY_R * 0.5, c0.y) &&
          isWalkable(c0.x, c0.y + BODY_R * 0.5) &&
          isWalkable(c0.x, c0.y - BODY_R * 0.5);
        if (!open0) continue;
        for (const [ox, oy] of [
          [1, 0],
          [-1, 0],
          [0, 1],
          [0, -1],
        ] as const) {
          const c1 = tileCenter(mx + dx + ox, my + dy + oy);
          if (!isWalkable(c1.x, c1.y)) continue;
          const open1 =
            isWalkable(c1.x + BODY_R * 0.5, c1.y) &&
            isWalkable(c1.x - BODY_R * 0.5, c1.y) &&
            isWalkable(c1.x, c1.y + BODY_R * 0.5) &&
            isWalkable(c1.x, c1.y - BODY_R * 0.5);
          if (!open1) continue;
          candidates.push([c0, c1]);
        }
      }
    }
    // Prefer a nearby pair, but gather a few options before scoring walks.
    if (candidates.length >= 8) break;
  }
  if (!candidates.length) {
    const [s1, s2] = MEETING_SLOTS.handoff;
    return [s1, s2];
  }
  // Minimize total walk + stay near the midpoint so both clearly walk toward each other.
  let best: [Vec, Vec] | null = null;
  let bestScore = Infinity;
  for (const [u0, v0] of candidates) {
    const assign =
      dist(a, u0) + dist(b, v0) <= dist(a, v0) + dist(b, u0) ? ([u0, v0] as [Vec, Vec]) : ([v0, u0] as [Vec, Vec]);
    const walk = dist(a, assign[0]) + dist(b, assign[1]);
    const meetMid = { x: (assign[0].x + assign[1].x) / 2, y: (assign[0].y + assign[1].y) / 2 };
    const score = walk + dist(meetMid, mid) * 0.35;
    if (score < bestScore) {
      bestScore = score;
      best = assign;
    }
  }
  return best ?? candidates[0];
}

function meetingOf(
  actors: [string, string],
  pos: Record<string, Pos>,
  now: number,
  key: string,
  hold: number,
  lines: string[],
): Interaction {
  const pa = pos[actors[0]] ?? { x: WORLD.w / 2, y: WORLD.h / 2 };
  const pb = pos[actors[1]] ?? { x: WORLD.w / 2, y: WORLD.h / 2 };
  const [s1, s2] = meetPairSlots(pa, pb);
  return {
    key,
    kind: "meeting",
    phase: "approach",
    actors,
    slot: { [actors[0]]: s1, [actors[1]]: s2 },
    bornAt: now,
    hold,
    lines,
  };
}

/** Turn a swarm event into an in-person walk-and-talk. */
function buildInteraction(
  ev: { eventId: string; seq: number; eventType: string; agentId?: string | null; metadata?: Record<string, unknown>; summary?: string },
  agents: Record<string, OfficeAgent>,
  now: number,
  pos: Record<string, Pos>,
): Interaction | null {
  const norm = (x?: string | null) => {
    if (!x) return undefined;
    if (x === "coordinator" || x.endsWith("_agent")) return x;
    return `${x}_agent`;
  };
  const alive = (id?: string | null) => !!(id && agents[id]);
  const summary = (ev.summary ?? "").trim();

  // Fewer, clearer scenes: leadership handoffs + skeptic beats + remediation briefs.
  // Skip task_started / agent_started waves — status chips cover desk work.

  if (ev.eventType === "leadership_transferred") {
    const from = norm((ev.metadata?.from_agent as string) ?? null);
    const to = ev.agentId ?? norm((ev.metadata?.to_agent as string) ?? null);
    if (!alive(from) || !alive(to) || from === to) return null;
    return meetingOf([from!, to!], pos, now, `meeting:${ev.seq}`, 4, [
      agents[from!]?.currentAction ?? "Handing off leadership",
      agents[to!]?.currentAction ?? (summary || "Taking the lead"),
    ]);
  }

  if (ev.eventType === "skeptic_revise" || ev.eventType === "skeptic_reject") {
    if (!alive("skeptic_agent") || !alive("coordinator")) return null;
    return meetingOf(["skeptic_agent", "coordinator"], pos, now, `revise:${ev.seq}`, 4.5, [
      ev.eventType === "skeptic_revise" ? "REVISE — missing traffic-mix control" : "REJECT — claim doesn't hold",
      "Got it — planning remediation",
    ]);
  }

  if (ev.eventType === "skeptic_review_started") {
    const visitor = alive("diagnostic_agent") ? "diagnostic_agent" : alive("strategy_agent") ? "strategy_agent" : null;
    if (!visitor || !alive("skeptic_agent")) return null;
    return meetingOf([visitor, "skeptic_agent"], pos, now, `review:${ev.seq}`, 4, [
      "Bringing the claim for review",
      "I'll pressure-test this",
    ]);
  }

  if (ev.eventType === "skeptic_pass") {
    if (!alive("skeptic_agent") || !alive("coordinator")) return null;
    return meetingOf(["skeptic_agent", "coordinator"], pos, now, `pass:${ev.seq}`, 3.5, [
      "PASS — claim holds",
      "Mission can close",
    ]);
  }

  if (ev.eventType === "task_assigned") {
    const who = norm((ev.metadata?.agent as string) ?? (ev.agentId as string) ?? null);
    if (!alive(who) || !alive("coordinator") || who === "coordinator") return null;
    return meetingOf(["coordinator", who!], pos, now, `brief:${ev.seq}`, 4, [
      "Briefing the remediation task",
      agents[who!]?.currentAction ?? "On it",
    ]);
  }

  if (ev.eventType === "evidence_requested" && alive(ev.agentId)) {
    return {
      key: `fetch:${ev.seq}`,
      kind: "fetch",
      phase: "approach",
      actors: [ev.agentId!],
      slot: { [ev.agentId!]: SPOTS.data_terminal },
      bornAt: now,
      hold: 2.8,
    };
  }

  return null;
}

function hashPhase(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) % 997;
  return h / 997;
}
function shortenSpeak(s: string): string {
  const t = s.replace(/^Verdict:\s*/i, "").trim();
  return t.length > 42 ? t.slice(0, 40) + "…" : t;
}
