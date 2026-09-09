import { useCallback, useEffect, useMemo, useRef } from "react";
import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";
import { DESK_BY_ID, MEETING_SLOTS, SPOTS, WORLD, ZONE_BY_ID, type Vec } from "../office/layout";
import { animationFor, destinationFor, STATUS_TOKEN } from "../office/stateMachine";
import { findPath } from "../office/navmesh";
import { paletteFor } from "./palette";
import {
  drawCharacter,
  drawConversation,
  drawDecor,
  drawFloor,
  drawSpeechBubble,
  drawWorkstation,
  drawZones,
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

/** An in-person interaction: agents walk together, meet, then disperse. */
interface Interaction {
  key: string;
  kind: "meeting" | "fetch";
  actors: string[];
  slot: Record<string, Vec>;
  bornAt: number;
  arrivedAt?: number;
  hold: number;
}

const LERP = (a: number, b: number, t: number) => a + (b - a) * t;
const WALK_SPEED = 155; // world px / second — brisk office walk, still readable
const dist = (a: Vec, b: Vec) => Math.hypot(a.x - b.x, a.y - b.y);
const keyOf = (v: Vec) => `${Math.round(v.x)},${Math.round(v.y)}`;

export function OfficeCanvas({ dark }: { dark: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const posRef = useRef<Record<string, Pos>>({});
  const interactionsRef = useRef<Interaction[]>([]);
  const seenInteractionRef = useRef<Set<string>>(new Set());
  const camRef = useRef({ x: WORLD.w / 2, y: WORLD.h / 2, zoom: 0.55, targetZoom: 0.55 });
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
      cam.targetZoom = Math.min(1.8, Math.max(0.4, cam.targetZoom * (e.deltaY > 0 ? 0.9 : 1.1)));
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

  // resize
  useEffect(() => {
    const c = canvasRef.current!;
    const ro = new ResizeObserver(() => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      dprRef.current = dpr;
      c.width = c.clientWidth * dpr;
      c.height = c.clientHeight * dpr;
      const ctx = c.getContext("2d");
      if (ctx) ctx.imageSmoothingEnabled = false; // crisp pixel art
    });
    ro.observe(c);
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
    drawZones(ctx, pal, activeZones);
    drawDecor(ctx, pal, time);

    // workstations (behind characters) — lit when their owner is working there
    for (const [id, desk] of Object.entries(DESK_BY_ID)) {
      const a = agents[id];
      const accentKey =
        SPEC_BY_ID[id]?.role === "coordinator"
          ? "coord"
          : SPEC_BY_ID[id]?.domain ?? "neutral";
      const accent = pal.accent[accentKey] ?? pal.accent.neutral;
      const lit = !!a && ["working", "tool_running", "collaborating", "retrieving_evidence"].includes(a.status);
      const p = posRef.current[id];
      const home = { x: desk.home.x, y: desk.home.y };
      const atHome = !p || Math.hypot(p.x - home.x, p.y - home.y) < 40;
      drawWorkstation(ctx, pal, home.x, home.y, accent, lit && atHome);
    }

    const order = st.agentOrder.length ? st.agentOrder : Object.keys(agents);

    // --- in-person interactions: spin one up when a new comms event lands ----
    const last = st.lastEvent;
    if (last && !seenInteractionRef.current.has(last.eventId)) {
      seenInteractionRef.current.add(last.eventId);
      const ix = buildInteraction(last, agents, time);
      if (ix) interactionsRef.current.push(ix);
    }
    // advance / retire interactions
    const override: Record<string, Vec> = {};
    const talking = new Set<string>();
    interactionsRef.current = interactionsRef.current.filter((ix) => {
      for (const id of ix.actors) if (ix.slot[id]) override[id] = ix.slot[id];
      const allHere = ix.actors.every((id) => {
        const p = posRef.current[id];
        return p && dist(p, ix.slot[id]) < 16;
      });
      if (allHere && ix.arrivedAt == null) ix.arrivedAt = time;
      if (ix.arrivedAt != null) ix.actors.forEach((id) => talking.add(id));
      const finished = ix.arrivedAt != null && time - ix.arrivedAt > ix.hold;
      const timedOut = time - ix.bornAt > 22;
      return !finished && !timedOut;
    });

    // --- move every character along a real path (no wall clipping) ----------
    for (const id of order) {
      const a = agents[id];
      if (!a) continue;
      const finalGoal = override[id] ?? destinationFor(a);
      let p = posRef.current[id];
      if (!p) {
        p = { x: finalGoal.x, y: finalGoal.y, px: finalGoal.x, py: finalGoal.y, facing: "S", path: [], goalKey: "", stuck: 0 };
        posRef.current[id] = p;
      }
      p.px = p.x;
      p.py = p.y;

      const gk = keyOf(finalGoal);
      if (gk !== p.goalKey) {
        p.goalKey = gk;
        p.path = st.reducedMotion ? [finalGoal] : findPath(p, finalGoal);
        p.stuck = 0;
      }

      if (st.reducedMotion) {
        p.x = finalGoal.x;
        p.y = finalGoal.y;
        p.path = [];
      } else {
        // consume reached waypoints
        while (p.path.length > 1 && dist(p, p.path[0]) < 8) p.path.shift();
        const wp = p.path[0] ?? finalGoal;
        const dx = wp.x - p.x;
        const dy = wp.y - p.y;
        const d = Math.hypot(dx, dy);
        if (d > 0.5) {
          const move = Math.min(WALK_SPEED * dt, d);
          p.x += (dx / d) * move;
          p.y += (dy / d) * move;
          if (move < 0.4) p.stuck += dt;
          else p.stuck = 0;
        } else if (p.path.length) {
          p.path.shift();
        }
        // re-plan if wedged against something
        if (p.stuck > 0.6) {
          p.path = findPath(p, finalGoal);
          p.stuck = 0;
        }
      }

      const vx = p.x - p.px;
      const vy = p.y - p.py;
      if (Math.hypot(vx, vy) > 0.35) {
        p.facing = Math.abs(vx) > Math.abs(vy) ? (vx > 0 ? "E" : "W") : vy > 0 ? "S" : "N";
      } else if (talking.has(id)) {
        // face the person you're meeting
        const other = interactionsRef.current
          .find((q) => q.actors.includes(id))
          ?.actors.find((o) => o !== id);
        const op = other ? posRef.current[other] : undefined;
        if (op) p.facing = Math.abs(op.x - p.x) > Math.abs(op.y - p.y) ? (op.x > p.x ? "E" : "W") : op.y > p.y ? "S" : "N";
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
      const finalGoal = override[id] ?? destinationFor(a);
      const arrived = dist(p, finalGoal) < 5;
      const atDesk = arrived && dist(p, DESK_BY_ID[id]?.home ?? { x: -9e9, y: -9e9 }) < 16;
      const deskWork = ["working", "tool_running", "thinking", "planning", "reviewing", "retrieving_evidence"].includes(a.status);
      const seated = atDesk && (deskWork || a.status === "idle");
      const helpers = (st.parallelTasks[id] ?? []).filter((t) => t.status !== "done").length;
      // seated workers face their monitor (north); walkers use path facing
      const face: Facing = seated ? "N" : p.facing;
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
        helpers,
        seated,
        talking: talking.has(id),
      });
    }

    // --- conversation cue when people are actually together ----------------
    for (const ix of interactionsRef.current) {
      if (ix.arrivedAt == null || ix.actors.length < 2) continue;
      const a = posRef.current[ix.actors[0]];
      const b = posRef.current[ix.actors[1]];
      if (a && b) {
        drawConversation(ctx, pal, a, b, time, ix.kind === "meeting");
        // short spoken line above the speaker — only while meeting in person
        const speakerId = ix.actors[Math.floor(time * 0.55) % ix.actors.length];
        const speaker = agents[speakerId];
        const sp = posRef.current[speakerId];
        if (speaker?.currentAction && sp) {
          drawSpeechBubble(ctx, pal, sp.x, sp.y - 8, shortenSpeak(speaker.currentAction));
        }
      }
    }

    ctx.restore();
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

/** Turn a comms event into an in-person interaction (walk-and-meet). No message lines. */
function buildInteraction(
  ev: { eventId: string; seq: number; eventType: string; agentId?: string | null; metadata?: Record<string, unknown>; summary?: string },
  agents: Record<string, OfficeAgent>,
  now: number,
): Interaction | null {
  const norm = (x?: string | null) => {
    if (!x) return undefined;
    if (x === "coordinator" || x.endsWith("_agent")) return x;
    return `${x}_agent`;
  };
  const alive = (id?: string | null) => !!(id && agents[id]);

  if (ev.eventType === "leadership_transferred") {
    const from = norm((ev.metadata?.from_agent as string) ?? null);
    const to = ev.agentId ?? norm((ev.metadata?.to_agent as string) ?? null);
    if (!alive(from) || !alive(to) || from === to) return null;
    const [s1, s2] = MEETING_SLOTS.handoff;
    return {
      key: `meeting:${ev.seq}`,
      kind: "meeting",
      actors: [from!, to!],
      slot: { [from!]: s1, [to!]: s2 },
      bornAt: now,
      hold: 4.2,
    };
  }

  // Skeptic REVISE / REJECT → walk to Coordinator's office and talk it through
  if (ev.eventType === "skeptic_revise" || ev.eventType === "skeptic_reject") {
    if (!alive("skeptic_agent") || !alive("coordinator")) return null;
    const [s1, s2] = MEETING_SLOTS.mission;
    return {
      key: `revise:${ev.seq}`,
      kind: "meeting",
      actors: ["skeptic_agent", "coordinator"],
      slot: { skeptic_agent: s1, coordinator: s2 },
      bornAt: now,
      hold: 4.8,
    };
  }

  // Skeptic starts review → Diagnostic (or claim owner) walks over with the artifact
  if (ev.eventType === "skeptic_review_started") {
    const visitor = alive("diagnostic_agent") ? "diagnostic_agent" : alive("strategy_agent") ? "strategy_agent" : null;
    if (!visitor || !alive("skeptic_agent")) return null;
    const [s1, s2] = MEETING_SLOTS.skeptic;
    return {
      key: `review:${ev.seq}`,
      kind: "meeting",
      actors: [visitor, "skeptic_agent"],
      slot: { [visitor]: s1, skeptic_agent: s2 },
      bornAt: now,
      hold: 3.8,
    };
  }

  // Remediation task → Coordinator briefs the assigned agent at the meeting table
  if (ev.eventType === "remediation_created" || ev.eventType === "task_assigned") {
    const who = norm((ev.metadata?.agent as string) ?? (ev.agentId as string) ?? null);
    if (!alive(who) || !alive("coordinator") || who === "coordinator") return null;
    const [s1, s2] = MEETING_SLOTS.handoff;
    return {
      key: `brief:${ev.seq}`,
      kind: "meeting",
      actors: ["coordinator", who!],
      slot: { coordinator: s1, [who!]: s2 },
      bornAt: now,
      hold: 3.6,
    };
  }

  // Evidence fetch — walk to the archive terminal (in person, not a message)
  if (ev.eventType === "evidence_requested" && alive(ev.agentId)) {
    return {
      key: `fetch:${ev.seq}`,
      kind: "fetch",
      actors: [ev.agentId!],
      slot: { [ev.agentId!]: SPOTS.data_terminal },
      bornAt: now,
      hold: 2.8,
    };
  }

  // After evidence arrives, agent may confer with Diagnostic at the lab door hallway
  if (ev.eventType === "evidence_received" && alive(ev.agentId) && alive("diagnostic_agent") && ev.agentId !== "diagnostic_agent") {
    const [s1, s2] = MEETING_SLOTS.handoff;
    return {
      key: `sync:${ev.seq}`,
      kind: "meeting",
      actors: [ev.agentId!, "diagnostic_agent"],
      slot: { [ev.agentId!]: s1, diagnostic_agent: s2 },
      bornAt: now,
      hold: 3.2,
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
// re-export so tests / inspector can reuse zone lookup
export { ZONE_BY_ID };
