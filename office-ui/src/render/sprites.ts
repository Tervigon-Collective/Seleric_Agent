/**
 * Pixel-office world renderer — Parcha / AI Town inspired.
 * Floors, walled rooms, desks, decor, and directional walk-cycle characters.
 */

import type { AgentSpec } from "../office/agents";
import { DECOR, DOOR_GAP, HALLWAYS, ZONES, type DecorKind, type FloorStyle, type Zone } from "../office/layout";
import type { AnimationName } from "../office/stateMachine";
import type { Palette, StatusToken } from "./palette";

const TWO_PI = Math.PI * 2;

export type Facing = "N" | "S" | "E" | "W";

export function drawFloor(ctx: CanvasRenderingContext2D, pal: Palette, world: { w: number; h: number }) {
  // base building slab
  ctx.fillStyle = mix(pal.bg, "#8a9078", 0.15);
  ctx.fillRect(-80, -80, world.w + 160, world.h + 160);

  // hallway carpet strips first (under rooms)
  for (const h of HALLWAYS) {
    fillFloorStyle(ctx, pal, "hallway", h.x, h.y, h.w, h.h);
  }
}

export function drawZones(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  activeZoneIds: Set<string>,
) {
  for (const z of ZONES) drawRoom(ctx, pal, z, activeZoneIds.has(z.id));
}

function drawRoom(ctx: CanvasRenderingContext2D, pal: Palette, z: Zone, active: boolean) {
  const accent = pal.accent[z.accent] ?? pal.accent.neutral;
  const wall = 6;

  ctx.save();
  fillFloorStyle(ctx, pal, z.floor, z.x, z.y, z.w, z.h);

  if (active) {
    ctx.fillStyle = accent;
    ctx.globalAlpha = 0.12;
    ctx.fillRect(z.x, z.y, z.w, z.h);
    ctx.globalAlpha = 1;
  }

  ctx.fillStyle = mix(accent, "#1a2030", 0.55);
  const doorGap = DOOR_GAP;
  const midX = z.x + z.w / 2;
  const midY = z.y + z.h / 2;

  // N
  if (z.door === "N") {
    ctx.fillRect(z.x, z.y, midX - doorGap / 2 - z.x, wall);
    ctx.fillRect(midX + doorGap / 2, z.y, z.x + z.w - (midX + doorGap / 2), wall);
  } else ctx.fillRect(z.x, z.y, z.w, wall);
  // S
  if (z.door === "S") {
    ctx.fillRect(z.x, z.y + z.h - wall, midX - doorGap / 2 - z.x, wall);
    ctx.fillRect(midX + doorGap / 2, z.y + z.h - wall, z.x + z.w - (midX + doorGap / 2), wall);
  } else ctx.fillRect(z.x, z.y + z.h - wall, z.w, wall);
  // W
  if (z.door === "W") {
    ctx.fillRect(z.x, z.y, wall, midY - doorGap / 2 - z.y);
    ctx.fillRect(z.x, midY + doorGap / 2, wall, z.y + z.h - (midY + doorGap / 2));
  } else ctx.fillRect(z.x, z.y, wall, z.h);
  // E
  if (z.door === "E") {
    ctx.fillRect(z.x + z.w - wall, z.y, wall, midY - doorGap / 2 - z.y);
    ctx.fillRect(z.x + z.w - wall, midY + doorGap / 2, wall, z.y + z.h - (midY + doorGap / 2));
  } else ctx.fillRect(z.x + z.w - wall, z.y, wall, z.h);

  ctx.fillStyle = mix(accent, "#000", 0.35);
  for (const [px, py] of [
    [z.x, z.y],
    [z.x + z.w - wall, z.y],
    [z.x, z.y + z.h - wall],
    [z.x + z.w - wall, z.y + z.h - wall],
  ]) ctx.fillRect(px, py, wall, wall);

  // plaque
  const plaqueW = Math.min(z.w - 20, 240);
  ctx.fillStyle = accent;
  ctx.globalAlpha = active ? 0.95 : 0.72;
  ctx.fillRect(z.x + 10, z.y + 10, plaqueW, 18);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#fff";
  ctx.font = "700 10px ui-sans-serif, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillText(z.label.toUpperCase(), z.x + 16, z.y + 19);
  if (active) {
    ctx.strokeStyle = accent;
    ctx.globalAlpha = 0.5;
    ctx.lineWidth = 2;
    ctx.strokeRect(z.x + 8, z.y + 8, plaqueW + 4, 22);
    ctx.globalAlpha = 1;
  }

  // door mat — shows the walkable opening on the floor
  ctx.fillStyle = "rgba(90, 70, 40, 0.28)";
  const dg = DOOR_GAP - 16;
  if (z.door === "N") ctx.fillRect(midX - dg / 2, z.y - 4, dg, 10);
  if (z.door === "S") ctx.fillRect(midX - dg / 2, z.y + z.h - 6, dg, 10);
  if (z.door === "W") ctx.fillRect(z.x - 4, midY - dg / 2, 10, dg);
  if (z.door === "E") ctx.fillRect(z.x + z.w - 6, midY - dg / 2, 10, dg);

  ctx.restore();
}

/** Round `v` DOWN to the previous multiple of `p` — used to phase-align every
 *  room's floor pattern to one world grid so planks / carpet stay continuous
 *  as a character walks from room to room. */
const snapDown = (v: number, p: number) => Math.floor(v / p) * p;

function fillFloorStyle(
  ctx: CanvasRenderingContext2D,
  _pal: Palette,
  style: FloorStyle,
  x: number,
  y: number,
  w: number,
  h: number,
) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  const x2 = x + w;
  const y2 = y + h;

  switch (style) {
    case "wood": {
      ctx.fillStyle = "#d4b896";
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = "rgba(120, 80, 40, 0.28)";
      ctx.lineWidth = 1;
      for (let yy = snapDown(y, 12); yy < y2; yy += 12) {
        ctx.beginPath(); ctx.moveTo(x, yy); ctx.lineTo(x2, yy); ctx.stroke();
      }
      for (let xx = snapDown(x, 48); xx < x2; xx += 48) {
        ctx.beginPath(); ctx.moveTo(xx, y); ctx.lineTo(xx, y2); ctx.stroke();
      }
      break;
    }
    case "carpet": {
      ctx.fillStyle = "#c5d0e0";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "rgba(255,255,255,0.14)";
      for (let yy = snapDown(y, 8); yy < y2; yy += 8) {
        const off = ((yy / 8) % 2) * 8;
        for (let xx = snapDown(x, 16) + off; xx < x2; xx += 16) ctx.fillRect(xx, yy, 8, 8);
      }
      break;
    }
    case "tile": {
      ctx.fillStyle = "#dce4ec";
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = "rgba(90,110,130,0.35)";
      ctx.lineWidth = 1;
      for (let xx = snapDown(x, 20); xx <= x2; xx += 20) {
        ctx.beginPath(); ctx.moveTo(xx, y); ctx.lineTo(xx, y2); ctx.stroke();
      }
      for (let yy = snapDown(y, 20); yy <= y2; yy += 20) {
        ctx.beginPath(); ctx.moveTo(x, yy); ctx.lineTo(x2, yy); ctx.stroke();
      }
      break;
    }
    case "checker": {
      for (let yy = snapDown(y, 18); yy < y2; yy += 18) {
        for (let xx = snapDown(x, 18); xx < x2; xx += 18) {
          const on = ((xx / 18) + (yy / 18)) % 2 === 0;
          ctx.fillStyle = on ? "#3a4555" : "#5a6575";
          ctx.fillRect(xx, yy, 18, 18);
        }
      }
      break;
    }
    case "hallway": {
      // warm corridor carpet with runner stripe
      ctx.fillStyle = "#c4b49a";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#b89f7e";
      if (w >= h) {
        // horizontal hall — center runner
        const rh = Math.max(18, Math.floor(h * 0.45));
        ctx.fillRect(x, y + (h - rh) / 2, w, rh);
        ctx.fillStyle = "rgba(255,255,255,0.12)";
        for (let xx = snapDown(x, 24); xx < x2; xx += 24) ctx.fillRect(xx, y + (h - rh) / 2, 12, rh);
      } else {
        const rw = Math.max(18, Math.floor(w * 0.45));
        ctx.fillRect(x + (w - rw) / 2, y, rw, h);
        ctx.fillStyle = "rgba(255,255,255,0.12)";
        for (let yy = snapDown(y, 24); yy < y2; yy += 24) ctx.fillRect(x + (w - rw) / 2, yy, rw, 12);
      }
      ctx.strokeStyle = "rgba(90,70,40,0.2)";
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
      break;
    }
    default: {
      ctx.fillStyle = "#b8c0cc";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "rgba(0,0,0,0.05)";
      for (let yy = snapDown(y, 26); yy < y2; yy += 26) {
        for (let xx = snapDown(x, 34); xx < x2; xx += 34) {
          if (((xx / 34) * 3 + (yy / 26) * 7) % 5 < 2) ctx.fillRect(xx + 6, yy + 8, 3, 2);
        }
      }
    }
  }
  ctx.fillStyle = "rgba(0,0,0,0.06)";
  ctx.fillRect(x, y, w, 4);
  ctx.fillRect(x, y, 4, h);
  ctx.restore();
}

/** Draw all static furniture. `time` reserved for subtle idle motion. */
export function drawDecor(ctx: CanvasRenderingContext2D, pal: Palette, _time = 0) {
  for (const d of DECOR) {
    ctx.save();
    ctx.translate(Math.round(d.x), Math.round(d.y));
    DRAWERS[d.kind](ctx, pal);
    ctx.restore();
  }
}

export function drawWorkstation(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  x: number,
  y: number,
  accent: string,
  lit: boolean,
) {
  ctx.save();
  ctx.translate(Math.round(x), Math.round(y));

  // desk
  ctx.fillStyle = "#8b6a45";
  ctx.fillRect(-28, 4, 56, 14);
  ctx.fillStyle = "#a67c52";
  ctx.fillRect(-28, 4, 56, 4);
  ctx.fillStyle = "#5c4630";
  ctx.fillRect(-26, 18, 4, 8);
  ctx.fillRect(22, 18, 4, 8);

  // monitor
  ctx.fillStyle = "#2a3340";
  ctx.fillRect(-12, -18, 24, 18);
  ctx.fillStyle = lit ? mix(accent, "#7ec8e8", 0.45) : "#3a4a58";
  ctx.fillRect(-10, -16, 20, 12);
  if (lit) {
    ctx.fillStyle = "rgba(255,255,255,0.25)";
    ctx.fillRect(-10, -14, 20, 2);
    ctx.fillRect(-10, -10, 20, 2);
  }
  ctx.fillStyle = "#3a4555";
  ctx.fillRect(-3, 0, 6, 4);
  ctx.fillRect(-8, 2, 16, 3);

  // chair
  ctx.fillStyle = mix(pal.deskFill, "#4a5568", 0.45);
  ctx.fillRect(-8, 22, 16, 6);
  ctx.fillRect(-6, 28, 4, 6);
  ctx.fillRect(2, 28, 4, 6);
  ctx.fillStyle = mix(pal.deskFill, "#2d3748", 0.55);
  ctx.fillRect(-8, 14, 16, 10);

  // accent rug under desk
  ctx.globalAlpha = 0.35;
  ctx.fillStyle = accent;
  ctx.fillRect(-22, 20, 44, 6);
  ctx.globalAlpha = 1;
  ctx.restore();
}

export interface CharDraw {
  x: number;
  y: number;
  spec: AgentSpec;
  token: StatusToken;
  anim: AnimationName;
  facing: Facing;
  lead: boolean;
  hovered: boolean;
  selected: boolean;
  phase: number;
  reducedMotion: boolean;
  helpers?: number;
  /** sitting at a desk — hide legs, add a chair back */
  seated?: boolean;
  /** in a face-to-face conversation — show talk dots */
  talking?: boolean;
}

export function drawCharacter(ctx: CanvasRenderingContext2D, pal: Palette, d: CharDraw) {
  const accent =
    pal.accent[d.spec.role === "coordinator" ? "coord" : d.spec.domain ?? roleAccent(d.spec.role)] ??
    pal.accent.neutral;
  const statusColor = pal.status[d.token];
  const bob = d.reducedMotion ? 0 : bobFor(d.anim, d.phase);
  const walk = !d.reducedMotion && d.anim === "walk" ? Math.sin(d.phase * 10) : 0;
  const cx = Math.round(d.x);
  const cy = Math.round(d.y + bob);
  const face = d.facing;

  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,0.22)";
  ctx.beginPath();
  ctx.ellipse(cx, d.y + 14, d.seated ? 9 : 12, d.seated ? 3 : 4, 0, 0, TWO_PI);
  ctx.fill();
  ctx.restore();

  if (d.lead) {
    ctx.save();
    ctx.strokeStyle = statusColor;
    ctx.lineWidth = 2.5;
    ctx.globalAlpha = 0.85 + (d.reducedMotion ? 0 : Math.sin(d.phase * 3) * 0.1);
    ctx.beginPath();
    ctx.ellipse(cx, d.y + 14, 18, 7, 0, 0, TWO_PI);
    ctx.stroke();
    ctx.restore();
  }

  ctx.save();
  ctx.translate(cx, cy);
  ctx.imageSmoothingEnabled = false;
  if (face === "W") ctx.scale(-1, 1);

  if (d.seated) {
    // office chair — person sits facing the desk (toward N visually)
    ctx.fillStyle = mix(pal.deskFill, "#2d3748", 0.55);
    ctx.fillRect(-10, -4, 20, 14); // chair back
    ctx.fillStyle = mix(pal.deskFill, "#4a5568", 0.35);
    ctx.fillRect(-9, 8, 18, 5); // seat
    // legs tucked under desk — short stubs only
    ctx.fillStyle = mix(accent, "#1a2030", 0.45);
    ctx.fillRect(-6, 10, 4, 4);
    ctx.fillRect(2, 10, 4, 4);
  } else {
    const legOff = Math.round(walk * 3);
    const pants = mix(accent, "#1a2030", 0.45);
    ctx.fillStyle = pants;
    ctx.fillRect(-5, 6, 4, 8 + (legOff > 0 ? 1 : 0));
    ctx.fillRect(1, 6, 4, 8 + (legOff < 0 ? 1 : 0));
    ctx.fillStyle = "#2a2430";
    ctx.fillRect(-6, 13, 5, 3);
    ctx.fillRect(1, 13, 5, 3);
  }

  ctx.fillStyle = accent;
  if (d.spec.role === "coordinator") {
    ctx.fillRect(-8, -6, 16, 14);
    ctx.fillStyle = mix(accent, "#ffffff", 0.35);
    ctx.fillRect(-2, -4, 4, 10);
  } else if (d.spec.role === "specialist") {
    ctx.fillRect(-7, -8, 14, 16);
    ctx.fillStyle = "#f0f4f8";
    ctx.fillRect(-7, -8, 14, 4);
    ctx.fillStyle = accent;
    ctx.fillRect(-5, -4, 10, 10);
  } else {
    ctx.fillRect(-7, -6, 14, 14);
    ctx.fillStyle = mix(accent, "#000", 0.2);
    ctx.fillRect(-7, -6, 14, 3);
  }

  ctx.fillStyle = accent;
  if ((d.anim === "sit_type" || d.anim === "retrieve") && !d.reducedMotion) {
    const t = Math.round(Math.sin(d.phase * 14) * 2);
    ctx.fillRect(-11, t, 4, 6);
    ctx.fillRect(7, -t, 4, 6);
  } else {
    ctx.fillRect(-11, -2, 4, 8);
    ctx.fillRect(7, -2, 4, 8);
  }

  const skin = skinTone(d.spec.agentId);
  ctx.fillStyle = skin;
  ctx.fillRect(-6, -18, 12, 12);
  // ears
  ctx.fillRect(-8, -14, 2, 4);
  ctx.fillRect(6, -14, 2, 4);
  ctx.fillStyle = hairColor(d.spec.agentId);
  if (d.spec.role === "coordinator") {
    ctx.fillRect(-7, -20, 14, 5);
    ctx.fillRect(-7, -16, 3, 4);
    ctx.fillRect(4, -16, 3, 4);
  } else if (d.spec.agentId.includes("skeptic")) {
    ctx.fillRect(-7, -20, 14, 6);
    ctx.fillStyle = "#2a2430";
    ctx.fillRect(-6, -12, 12, 3);
    ctx.strokeStyle = "#2a2430";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(-6, -13, 5, 4);
    ctx.strokeRect(1, -13, 5, 4);
  } else {
    ctx.fillRect(-6, -20, 12, 5);
  }

  // face direction: hide eyes when facing N (back of head)
  if (face !== "N") {
    ctx.fillStyle = "#1a2030";
    ctx.fillRect(-4, -12, 2, 2);
    ctx.fillRect(2, -12, 2, 2);
  }

  if (d.selected || d.hovered) {
    ctx.strokeStyle = d.selected ? pal.text : statusColor;
    ctx.lineWidth = d.selected ? 2 : 1.5;
    ctx.strokeRect(-12, -22, 24, 40);
  }
  ctx.restore();

  // status ring
  ctx.save();
  ctx.strokeStyle = statusColor;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(cx, cy - 26, 7, 0, TWO_PI);
  ctx.stroke();
  if ((d.anim === "think" || d.anim === "review") && !d.reducedMotion) {
    const a = (d.phase * 2.4) % TWO_PI;
    ctx.strokeStyle = pal.bg;
    ctx.beginPath();
    ctx.arc(cx, cy - 26, 7, a, a + 1.1);
    ctx.stroke();
  }
  if (d.lead) {
    ctx.fillStyle = statusColor;
    roundRect(ctx, cx - 14, cy - 40, 28, 11, 3);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "700 8px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("LEAD", cx, cy - 34);
  }
  // parallel helper chips
  if (d.helpers && d.helpers > 0) {
    ctx.fillStyle = pal.status.collab;
    roundRect(ctx, cx + 10, cy - 30, 16, 12, 3);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "700 9px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`+${d.helpers}`, cx + 18, cy - 24);
  }
  // talk dots when in a face-to-face conversation
  if (d.talking) {
    const n = d.reducedMotion ? 3 : 1 + Math.floor((d.phase * 3) % 3);
    ctx.fillStyle = pal.status.collab;
    for (let i = 0; i < n; i++) ctx.fillRect(cx + 8 + i * 4, cy - 33, 2, 2);
  }
  ctx.restore();

  // nameplate
  ctx.save();
  ctx.font = "600 10px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const label = d.spec.name;
  const tw = ctx.measureText(label).width + 10;
  ctx.fillStyle = "rgba(20,28,40,0.85)";
  roundRect(ctx, cx - tw / 2, d.y + 18, tw, 14, 4);
  ctx.fill();
  ctx.fillStyle = "#f4f7fb";
  ctx.fillText(label, cx, d.y + 20);
  ctx.restore();

  if (d.token === "waiting" || d.token === "failed") {
    ctx.save();
    ctx.fillStyle = pal.status[d.token];
    ctx.beginPath();
    ctx.arc(cx + 12, cy - 18, 6, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "700 9px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("!", cx + 12, cy - 17);
    ctx.restore();
  }
}

export function drawSpeechBubble(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  x: number,
  y: number,
  text: string,
) {
  if (!text) return;
  ctx.save();
  ctx.font = "600 11px ui-sans-serif, system-ui, sans-serif";
  const maxW = 180;
  const lines = wrap(ctx, text, maxW);
  const w = Math.min(maxW, Math.max(...lines.map((l) => ctx.measureText(l).width))) + 16;
  const h = lines.length * 14 + 12;
  const bx = x - w / 2;
  const by = y - 56 - h;
  ctx.fillStyle = pal.text;
  ctx.globalAlpha = 0.94;
  roundRect(ctx, bx, by, w, h, 8);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(x - 6, by + h);
  ctx.lineTo(x + 6, by + h);
  ctx.lineTo(x, by + h + 7);
  ctx.closePath();
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.fillStyle = pal.bg;
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  lines.forEach((l, i) => ctx.fillText(l, bx + 8, by + 6 + i * 14));
  ctx.restore();
}

export function drawLink(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  a: { x: number; y: number },
  b: { x: number; y: number },
  phase: number,
  reduced: boolean,
  kind: "handoff" | "evidence" = "handoff",
) {
  ctx.save();
  ctx.strokeStyle = kind === "evidence" ? pal.status.working : pal.status.collab;
  ctx.lineWidth = 2.5;
  ctx.setLineDash([5, 7]);
  ctx.lineDashOffset = reduced ? 0 : -phase * 50;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
  ctx.setLineDash([]);
  if (!reduced) {
    const t = (Math.sin(phase * 1.8) + 1) / 2;
    const px = a.x + (b.x - a.x) * t;
    const py = a.y + (b.y - a.y) * t;
    if (kind === "evidence") {
      ctx.fillStyle = "#f4f0e6";
      ctx.fillRect(px - 5, py - 6, 10, 12);
      ctx.fillStyle = pal.status.working;
      ctx.fillRect(px - 5, py - 6, 10, 3);
    } else {
      ctx.fillStyle = "#c4a574";
      ctx.fillRect(px - 6, py - 7, 12, 14);
      ctx.fillStyle = "#8b6a45";
      ctx.fillRect(px - 6, py - 7, 12, 3);
      ctx.fillStyle = pal.status.collab;
      ctx.fillRect(px - 2, py - 1, 4, 5);
    }
  }
  ctx.restore();
}

/** A short in-person exchange: two people standing together. Draws a subtle
 *  ground shadow linking them and, for a handoff meeting, a folder passing
 *  hands — no cross-office "message" line. */
export function drawConversation(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  a: { x: number; y: number },
  b: { x: number; y: number },
  phase: number,
  isMeeting: boolean,
) {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  ctx.save();
  ctx.globalAlpha = 0.18;
  ctx.fillStyle = pal.status.collab;
  ctx.beginPath();
  ctx.ellipse(mx, my + 14, Math.max(18, Math.abs(a.x - b.x) / 2 + 12), 8, 0, 0, TWO_PI);
  ctx.fill();
  ctx.restore();

  if (isMeeting) {
    const t = (Math.sin(phase * 1.6) + 1) / 2;
    const fx = a.x + (b.x - a.x) * (0.35 + t * 0.3);
    const fy = a.y + (b.y - a.y) * (0.35 + t * 0.3) - 4;
    ctx.fillStyle = "#c4a574";
    ctx.fillRect(Math.round(fx) - 5, Math.round(fy) - 6, 10, 12);
    ctx.fillStyle = "#8b6a45";
    ctx.fillRect(Math.round(fx) - 5, Math.round(fy) - 6, 10, 3);
  }
}

// --- decor drawers ---------------------------------------------------------

const DRAWERS: Record<DecorKind, (ctx: CanvasRenderingContext2D, pal: Palette) => void> = {
  plant(ctx) {
    ctx.fillStyle = "#6b4423";
    ctx.fillRect(-6, 4, 12, 10);
    ctx.fillStyle = "#3d8b4a";
    ctx.beginPath();
    ctx.ellipse(0, -2, 12, 10, 0, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = "#2f6b3a";
    ctx.beginPath();
    ctx.ellipse(-6, 0, 6, 5, -0.4, 0, TWO_PI);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(6, -2, 6, 5, 0.4, 0, TWO_PI);
    ctx.fill();
  },
  rug(ctx, pal) {
    ctx.fillStyle = mix(pal.accent.skeptic, "#c4a574", 0.5);
    roundRect(ctx, -48, -28, 96, 56, 4);
    ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,0.12)";
    ctx.lineWidth = 2;
    ctx.stroke();
  },
  whiteboard(ctx) {
    ctx.fillStyle = "#e8eef5";
    ctx.fillRect(-36, -22, 72, 44);
    ctx.strokeStyle = "#8a95a8";
    ctx.lineWidth = 3;
    ctx.strokeRect(-36, -22, 72, 44);
    ctx.strokeStyle = "#2f7dd1";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(-24, -8);
    ctx.lineTo(8, -12);
    ctx.lineTo(20, 4);
    ctx.stroke();
    ctx.fillStyle = "#d1435b";
    ctx.fillRect(-20, 8, 18, 3);
  },
  planningBoard(ctx) {
    ctx.fillStyle = "#2a3544";
    ctx.fillRect(-70, -40, 140, 80);
    ctx.fillStyle = "#f4f0e6";
    ctx.fillRect(-64, -34, 128, 68);
    const notes = ["#f6d860", "#7ec8e8", "#a8e6a1", "#f5a97f", "#d4b5f5", "#f0f0f0"];
    notes.forEach((c, i) => {
      const col = i % 3;
      const row = Math.floor(i / 3);
      ctx.fillStyle = c;
      ctx.fillRect(-52 + col * 40, -26 + row * 28, 32, 22);
      ctx.fillStyle = "rgba(0,0,0,0.15)";
      ctx.fillRect(-52 + col * 40, -26 + row * 28, 32, 3);
    });
  },
  serverRack(ctx) {
    ctx.fillStyle = "#1e2430";
    ctx.fillRect(-14, -28, 28, 56);
    for (let i = 0; i < 5; i++) {
      ctx.fillStyle = "#2f7dd1";
      ctx.fillRect(-10, -22 + i * 10, 20, 4);
      ctx.fillStyle = i % 2 ? "#2fa96a" : "#d98b28";
      ctx.fillRect(6, -21 + i * 10, 3, 2);
    }
  },
  meetingTable(ctx) {
    ctx.fillStyle = "#7a5a3a";
    ctx.beginPath();
    ctx.ellipse(0, 0, 48, 22, 0, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = "#9a754c";
    ctx.beginPath();
    ctx.ellipse(0, -2, 44, 18, 0, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = "#4a5568";
    for (const [dx, dy] of [[-40, -18], [40, -18], [-40, 18], [40, 18], [0, -28], [0, 28]] as const) {
      ctx.fillRect(dx - 6, dy - 4, 12, 8);
    }
  },
  coffee(ctx) {
    ctx.fillStyle = "#4a5568";
    ctx.fillRect(-10, -8, 20, 24);
    ctx.fillStyle = "#2a3340";
    ctx.fillRect(-6, -16, 12, 10);
    ctx.fillStyle = "#c47f2b";
    ctx.fillRect(-4, -4, 8, 6);
  },
  waterCooler(ctx) {
    ctx.fillStyle = "#5a6b8c";
    ctx.fillRect(-8, 0, 16, 20);
    ctx.fillStyle = "#7ec8e8";
    ctx.beginPath();
    ctx.ellipse(0, -10, 10, 12, 0, 0, TWO_PI);
    ctx.fill();
  },
  bookshelf(ctx) {
    ctx.fillStyle = "#6b4423";
    ctx.fillRect(-16, -28, 32, 56);
    const colors = ["#3457a6", "#c47f2b", "#2fa96a", "#c8543f", "#7a4fc4"];
    for (let row = 0; row < 4; row++) {
      ctx.fillStyle = "#5a3820";
      ctx.fillRect(-14, -24 + row * 13, 28, 2);
      for (let b = 0; b < 4; b++) {
        ctx.fillStyle = colors[(row + b) % colors.length];
        ctx.fillRect(-12 + b * 7, -22 + row * 13, 5, 10);
      }
    }
  },
  couch(ctx) {
    ctx.fillStyle = "#5b7fa6";
    roundRect(ctx, -36, -10, 72, 28, 6);
    ctx.fill();
    ctx.fillStyle = "#4a6a8c";
    roundRect(ctx, -36, -18, 14, 20, 4);
    ctx.fill();
    roundRect(ctx, 22, -18, 14, 20, 4);
    ctx.fill();
    ctx.fillStyle = "#6d91b8";
    ctx.fillRect(-20, -8, 40, 12);
  },
  archiveShelf(ctx) {
    ctx.fillStyle = "#5c4630";
    ctx.fillRect(-18, -30, 36, 60);
    for (let i = 0; i < 4; i++) {
      ctx.fillStyle = i % 2 ? "#d4c4a8" : "#c4b498";
      ctx.fillRect(-14, -24 + i * 14, 28, 10);
      ctx.fillStyle = "#8b6a45";
      ctx.fillRect(-4, -20 + i * 14, 8, 2);
    }
  },
  forecastRig(ctx) {
    ctx.fillStyle = "#1e2430";
    ctx.fillRect(-30, -20, 60, 40);
    ctx.fillStyle = "#0d1118";
    ctx.fillRect(-26, -16, 52, 28);
    ctx.strokeStyle = "#2fa96a";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(-22, 6);
    ctx.lineTo(-10, -2);
    ctx.lineTo(0, 2);
    ctx.lineTo(12, -10);
    ctx.lineTo(22, -6);
    ctx.stroke();
  },
  deskLamp(ctx) {
    ctx.fillStyle = "#3a4555";
    ctx.fillRect(-3, 2, 6, 6);
    ctx.strokeStyle = "#3a4555";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, 4);
    ctx.lineTo(-2, -6);
    ctx.lineTo(6, -10);
    ctx.stroke();
    ctx.fillStyle = "#f6d860";
    ctx.beginPath();
    ctx.moveTo(2, -8);
    ctx.lineTo(10, -12);
    ctx.lineTo(9, -6);
    ctx.closePath();
    ctx.fill();
  },
  filingCabinet(ctx) {
    ctx.fillStyle = "#8a95a8";
    ctx.fillRect(-11, -18, 22, 36);
    ctx.fillStyle = "#6b7688";
    for (let i = 0; i < 3; i++) {
      ctx.fillRect(-9, -15 + i * 12, 18, 9);
      ctx.fillStyle = "#3a4555";
      ctx.fillRect(-2, -12 + i * 12, 4, 2);
      ctx.fillStyle = "#6b7688";
    }
  },
  printer(ctx) {
    ctx.fillStyle = "#c7cede";
    ctx.fillRect(-14, -8, 28, 18);
    ctx.fillStyle = "#8a95a8";
    ctx.fillRect(-14, -12, 28, 6);
    ctx.fillStyle = "#f4f7fb";
    ctx.fillRect(-8, 8, 16, 6);
    ctx.fillStyle = "#2fa96a";
    ctx.fillRect(8, -6, 3, 2);
  },
  clock(ctx) {
    ctx.fillStyle = "#f4f7fb";
    ctx.beginPath();
    ctx.arc(0, 0, 9, 0, TWO_PI);
    ctx.fill();
    ctx.strokeStyle = "#3a4555";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(0, -6);
    ctx.moveTo(0, 0);
    ctx.lineTo(4, 2);
    ctx.stroke();
  },
  wallArt(ctx, pal) {
    ctx.fillStyle = "#5c4630";
    ctx.fillRect(-16, -12, 32, 24);
    ctx.fillStyle = mix(pal.accent.perf, "#ffffff", 0.25);
    ctx.fillRect(-13, -9, 26, 18);
    ctx.fillStyle = mix(pal.accent.strat, "#000000", 0.1);
    ctx.beginPath();
    ctx.moveTo(-13, 9);
    ctx.lineTo(-2, -4);
    ctx.lineTo(6, 4);
    ctx.lineTo(13, -2);
    ctx.lineTo(13, 9);
    ctx.closePath();
    ctx.fill();
  },
  kitchen(ctx) {
    ctx.fillStyle = "#c7cede";
    ctx.fillRect(-24, -10, 48, 22);
    ctx.fillStyle = "#8a95a8";
    ctx.fillRect(-24, -14, 48, 5);
    ctx.fillStyle = "#3a4555";
    ctx.fillRect(-16, -4, 10, 10);
    ctx.fillStyle = "#5a6575";
    ctx.beginPath();
    ctx.arc(10, 2, 5, 0, TWO_PI);
    ctx.fill();
  },
  trash(ctx) {
    ctx.fillStyle = "#5a6575";
    ctx.beginPath();
    ctx.moveTo(-6, -8);
    ctx.lineTo(6, -8);
    ctx.lineTo(4, 10);
    ctx.lineTo(-4, 10);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#6b7688";
    ctx.fillRect(-7, -10, 14, 3);
  },
  sideChair(ctx, pal) {
    ctx.fillStyle = mix(pal.deskFill, "#4a5568", 0.4);
    ctx.fillRect(-7, -2, 14, 6);
    ctx.fillRect(-7, -12, 14, 10);
    ctx.fillStyle = mix(pal.deskFill, "#2d3748", 0.5);
    ctx.fillRect(-6, 4, 3, 6);
    ctx.fillRect(3, 4, 3, 6);
  },
  waterFountain(ctx) {
    ctx.fillStyle = "#8a95a8";
    ctx.fillRect(-7, -4, 14, 16);
    ctx.fillStyle = "#c7cede";
    ctx.fillRect(-6, -8, 12, 5);
    ctx.fillStyle = "#7ec8e8";
    ctx.fillRect(-2, -3, 4, 3);
  },
  bench(ctx) {
    ctx.fillStyle = "#9c6b3c";
    ctx.fillRect(-20, -3, 40, 6);
    ctx.fillStyle = "#7a4f28";
    ctx.fillRect(-18, 3, 4, 8);
    ctx.fillRect(14, 3, 4, 8);
  },
  pottedTree(ctx) {
    ctx.fillStyle = "#6b4423";
    ctx.fillRect(-7, 8, 14, 12);
    ctx.fillStyle = "#2f6b3a";
    ctx.beginPath();
    ctx.ellipse(0, -8, 14, 18, 0, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = "#3d8b4a";
    ctx.beginPath();
    ctx.ellipse(-4, -12, 8, 10, 0, 0, TWO_PI);
    ctx.fill();
  },
  standingLamp(ctx) {
    ctx.strokeStyle = "#3a4555";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, 14);
    ctx.lineTo(0, -10);
    ctx.stroke();
    ctx.fillStyle = "#3a4555";
    ctx.fillRect(-6, 12, 12, 3);
    ctx.fillStyle = "#f6d860";
    ctx.beginPath();
    ctx.moveTo(-9, -10);
    ctx.lineTo(9, -10);
    ctx.lineTo(6, -20);
    ctx.lineTo(-6, -20);
    ctx.closePath();
    ctx.fill();
  },
  monitorWall(ctx, pal) {
    ctx.fillStyle = "#1e2430";
    ctx.fillRect(-28, -18, 56, 34);
    for (let r = 0; r < 2; r++) {
      for (let c = 0; c < 3; c++) {
        ctx.fillStyle = (r + c) % 2 ? mix(pal.status.working, "#000", 0.2) : mix(pal.status.thinking, "#000", 0.2);
        ctx.fillRect(-25 + c * 18, -15 + r * 16, 15, 13);
      }
    }
  },
};

// --- helpers ---------------------------------------------------------------

function bobFor(anim: AnimationName, phase: number): number {
  switch (anim) {
    case "walk": return Math.abs(Math.sin(phase * 9)) * -2;
    case "idle": return Math.sin(phase * 1.6) * 0.8;
    case "celebrate": return Math.abs(Math.sin(phase * 6)) * -3;
    case "wait": return Math.sin(phase * 2.2) * 1.2;
    default: return 0;
  }
}

function roleAccent(role: string): string {
  if (role === "specialist") return "diag";
  if (role === "coordinator") return "coord";
  return "domain";
}

function hairColor(id: string): string {
  const colors = ["#2a2430", "#4a3728", "#1a3050", "#5c4030", "#3a3a48", "#6b4423", "#8b6914"];
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h + id.charCodeAt(i) * 17) % colors.length;
  return colors[h];
}

function skinTone(id: string): string {
  const tones = ["#f0c9a0", "#e8b898", "#d4a574", "#c68642", "#8d5524", "#f5d0b0", "#deb887"];
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h + id.charCodeAt(i) * 13) % tones.length;
  return tones[h];
}

export function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function wrap(ctx: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let line = "";
  for (const w of words) {
    const test = line ? `${line} ${w}` : w;
    if (ctx.measureText(test).width > maxW && line) {
      lines.push(line);
      line = w;
    } else line = test;
  }
  if (line) lines.push(line);
  return lines.slice(0, 4);
}

function mix(a: string, b: string, t: number): string {
  const pa = hex(a);
  const pb = hex(b);
  return `rgb(${Math.round(pa[0] + (pb[0] - pa[0]) * t)},${Math.round(pa[1] + (pb[1] - pa[1]) * t)},${Math.round(pa[2] + (pb[2] - pa[2]) * t)})`;
}

function hex(c: string): [number, number, number] {
  const m = c.replace("#", "");
  const n = m.length === 3 ? m.split("").map((x) => x + x).join("") : m;
  return [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16)];
}
