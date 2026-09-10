/**
 * Parcha-style pixel office renderer — real tileset + character spritesheets.
 * Map/character art: MIT assets from https://github.com/Parcha-ai/ai-office
 */

import type { AgentSpec } from "../office/agents";
import { ZONES } from "../office/layout";
import type { AnimationName } from "../office/stateMachine";
import type { Palette, StatusToken } from "./palette";
import { charFrame, getParchaAssets, sheetForAgent, TILE } from "./parchaAssets";

const TWO_PI = Math.PI * 2;

export type Facing = "N" | "S" | "E" | "W";

export function drawFloor(ctx: CanvasRenderingContext2D, _pal: Palette, world: { w: number; h: number }) {
  // warm dark stage void (reads like Parcha’s framed map)
  if (typeof ctx.createRadialGradient === "function") {
    const g = ctx.createRadialGradient(world.w / 2, world.h / 2, world.w * 0.2, world.w / 2, world.h / 2, world.w * 0.85);
    if (g) {
      g.addColorStop(0, "#243038");
      g.addColorStop(1, "#12181e");
      ctx.fillStyle = g;
    } else {
      ctx.fillStyle = "#12181e";
    }
  } else {
    ctx.fillStyle = "#12181e";
  }
  ctx.fillRect(-500, -500, world.w + 1000, world.h + 1000);

  const assets = getParchaAssets();
  if (assets?.ready && assets.mapCanvas.width > 0) {
    // soft drop shadow under the office plate
    ctx.save();
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    roundRect(ctx, 10, 14, world.w, world.h, 6);
    ctx.fill();
    ctx.restore();

    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(assets.mapCanvas, 0, 0);

    // thin frame so the map reads as a place, not floating tiles
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.12)";
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, world.w - 2, world.h - 2);
    ctx.strokeStyle = "rgba(0,0,0,0.35)";
    ctx.lineWidth = 4;
    ctx.strokeRect(-1, -1, world.w + 2, world.h + 2);
    ctx.restore();
    return;
  }
  ctx.fillStyle = "#2a3540";
  ctx.fillRect(0, 0, world.w, world.h);
  ctx.fillStyle = "#9aa7bd";
  ctx.font = "14px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("Loading Parcha office tiles…", 24, 40);
}

/** Room names on the floor. `avoid` = agent feet so tags don't cover nameplates. */
export function drawRoomLabels(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  activeZoneIds: Set<string>,
  avoid: Array<{ x: number; y: number }> = [],
) {
  ctx.save();
  ctx.font = `700 ${Math.max(9, Math.round(TILE * 0.18))}px ui-sans-serif, system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  for (const z of ZONES) {
    const accent = pal.accent[z.accent] ?? pal.accent.neutral;
    const active = activeZoneIds.has(z.id);
    const label = z.label.toUpperCase();
    const tw = ctx.measureText(label).width + 12;
    const cx = z.x + z.w / 2;
    // Prefer top of zone (corridor side); fall back if that still hits an agent
    let top = z.y + 6;
    const hits = (ty: number) =>
      avoid.some((p) => Math.abs(p.x - cx) < tw / 2 + 28 && Math.abs(p.y - (ty + 7)) < 22);
    if (hits(top)) top = z.y + z.h - 18;
    if (hits(top)) continue; // both ends crowded — skip rather than overlap nameplates
    ctx.globalAlpha = active ? 0.75 : 0.4;
    ctx.fillStyle = "rgba(8,12,18,0.4)";
    roundRect(ctx, cx - tw / 2 + 1, top + 1, tw, 14, 5);
    ctx.fill();
    ctx.fillStyle = active ? accent : "rgba(32,40,52,0.85)";
    roundRect(ctx, cx - tw / 2, top, tw, 14, 5);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.globalAlpha = 1;
    ctx.fillText(label, cx, top + 7);
  }
  ctx.globalAlpha = 1;
  ctx.restore();
}

/** Computer desk fully ABOVE the sit tile — sprite and monitor never overlap. */
export function drawWorkstation(
  ctx: CanvasRenderingContext2D,
  _pal: Palette,
  x: number,
  y: number,
  accent: string,
  lit: boolean,
) {
  const deskX = Math.round(x);
  // One full tile north of feet so the character never covers the screen.
  const deskY = Math.round(y - TILE * 1.05);
  ctx.save();
  ctx.imageSmoothingEnabled = false;

  ctx.fillStyle = "#4a3828";
  ctx.fillRect(deskX - 24, deskY + 2, 48, 18);
  ctx.fillStyle = "#6e5238";
  ctx.fillRect(deskX - 24, deskY + 2, 48, 5);
  ctx.fillStyle = "#2e2418";
  ctx.fillRect(deskX - 22, deskY + 18, 8, 5);
  ctx.fillRect(deskX + 14, deskY + 18, 8, 5);

  ctx.fillStyle = "#12161c";
  ctx.fillRect(deskX - 16, deskY - 26, 32, 24);
  ctx.fillStyle = "#0a0c10";
  ctx.fillRect(deskX - 14, deskY - 24, 28, 18);
  ctx.fillStyle = lit ? accent : "#1e2834";
  ctx.fillRect(deskX - 13, deskY - 23, 26, 16);
  if (lit) {
    ctx.fillStyle = "rgba(255,255,255,0.65)";
    ctx.fillRect(deskX - 11, deskY - 21, 10, 2);
    ctx.fillRect(deskX - 11, deskY - 17, 18, 1);
    ctx.fillRect(deskX - 11, deskY - 14, 14, 1);
    ctx.fillRect(deskX - 11, deskY - 11, 16, 1);
    ctx.fillStyle = "rgba(255,255,255,0.25)";
    ctx.fillRect(deskX - 13, deskY - 23, 8, 16);
  } else {
    ctx.fillStyle = "#2a3440";
    ctx.fillRect(deskX - 6, deskY - 16, 12, 2);
  }
  ctx.fillStyle = "#12161c";
  ctx.fillRect(deskX - 3, deskY - 2, 6, 6);
  ctx.fillRect(deskX - 10, deskY + 2, 20, 4);

  ctx.fillStyle = "#1a2028";
  ctx.fillRect(deskX - 16, deskY + 8, 32, 9);
  ctx.fillStyle = lit ? "#d0d8e0" : "#5a6270";
  for (let r = 0; r < 2; r++) {
    for (let i = 0; i < 7; i++) ctx.fillRect(deskX - 14 + i * 4, deskY + 9 + r * 3, 3, 2);
  }

  const cy = Math.round(y + Math.round(TILE * 0.12));
  ctx.fillStyle = "#2c3644";
  ctx.fillRect(deskX - 11, cy, 22, 6);
  ctx.fillStyle = "#1c2430";
  ctx.fillRect(deskX - 9, cy + 5, 18, 4);

  ctx.restore();
}

export function drawPlanningBoard(ctx: CanvasRenderingContext2D, pal: Palette, x: number, y: number, active: boolean) {
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  const bx = Math.round(x) - 30;
  const by = Math.round(y - TILE * 0.95);
  ctx.fillStyle = "#2a2018";
  ctx.fillRect(bx - 4, by - 4, 68, 48);
  ctx.fillStyle = active ? "#f4f7fb" : "#dce2ea";
  ctx.fillRect(bx, by, 60, 40);
  ctx.fillStyle = "#9aa8b8";
  ctx.fillRect(bx + 3, by + 3, 54, 2);
  ctx.fillStyle = pal.accent.coord;
  ctx.fillRect(bx + 6, by + 10, 20, 5);
  ctx.fillStyle = pal.accent.perf;
  ctx.fillRect(bx + 6, by + 18, 30, 5);
  ctx.fillStyle = pal.accent.funnel;
  ctx.fillRect(bx + 6, by + 26, 16, 5);
  ctx.fillStyle = "#1c2430";
  ctx.font = "700 10px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("BOARD", bx + 36, by + 36);
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
  seated?: boolean;
  talking?: boolean;
  /** Short line of what the agent is doing right now (from swarm events). */
  action?: string;
  /** Nudge feet nameplate sideways when agents are crowded. */
  labelNudgeX?: number;
}

export function drawCharacter(ctx: CanvasRenderingContext2D, pal: Palette, d: CharDraw) {
  const statusColor = pal.status[d.token];
  const bob = d.reducedMotion ? 0 : bobFor(d.anim, d.phase);
  const cx = Math.round(d.x);
  const cy = Math.round(d.y + bob);
  const labelX = cx + (d.labelNudgeX ?? 0);
  const assets = getParchaAssets();
  const walking = d.anim === "walk" && !d.reducedMotion;
  const walkFrame = walking ? Math.floor(d.phase * 8) % 3 : 1;
  const focus = d.hovered || d.selected || d.lead || d.talking;
  // Everyone gets a name; lead/focus get the bright pill
  const showLabel = true;

  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,0.3)";
  ctx.beginPath();
  ctx.ellipse(cx, d.y + Math.round(TILE * 0.22), Math.round(TILE * 0.22), Math.round(TILE * 0.09), 0, 0, TWO_PI);
  ctx.fill();
  ctx.restore();

  if (d.lead) {
    ctx.save();
    ctx.strokeStyle = statusColor;
    ctx.lineWidth = 2.5;
    ctx.globalAlpha = 0.9;
    ctx.beginPath();
    ctx.ellipse(cx, d.y + Math.round(TILE * 0.22), Math.round(TILE * 0.38), Math.round(TILE * 0.14), 0, 0, TWO_PI);
    ctx.stroke();
    ctx.restore();
  }

  if (assets?.ready && assets.chars) {
    const sheet = sheetForAgent(d.spec.agentId);
    const fr = charFrame(sheet, d.facing, walkFrame);
    // sized to tile — keep under desk height so monitors stay readable
    const scale = (TILE / 32) * 0.95;
    const dw = fr.sw * scale;
    const dh = fr.sh * scale;
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(assets.chars, fr.sx, fr.sy, fr.sw, fr.sh, cx - dw / 2, cy - dh + Math.round(TILE * 0.18), dw, dh);
    if (d.selected || d.hovered) {
      ctx.strokeStyle = d.selected ? "#fff" : statusColor;
      ctx.lineWidth = d.selected ? 2 : 1.5;
      ctx.strokeRect(cx - dw / 2 - 1, cy - dh + Math.round(TILE * 0.18) - 1, dw + 2, dh + 2);
    }
    ctx.restore();
  } else {
    ctx.fillStyle = pal.accent.coord;
    ctx.fillRect(cx - 8, cy - 20, 16, 28);
  }

  ctx.save();
  // Status floor tick only — no stacked chrome above the head here
  if (!d.talking && (d.token === "working" || d.token === "collab")) {
    ctx.fillStyle = statusColor;
    ctx.beginPath();
    ctx.ellipse(cx, d.y + Math.round(TILE * 0.26), 5, 2.5, 0, 0, TWO_PI);
    ctx.fill();
  }
  if ((d.anim === "think" || d.anim === "review") && !d.talking && !d.action) {
    drawThoughtBubble(ctx, cx, cy - Math.round(TILE * 0.85), d.phase, d.reducedMotion, statusColor);
  }
  if (d.helpers && d.helpers > 0 && !d.talking) {
    ctx.fillStyle = pal.status.collab;
    roundRect(ctx, cx + 14, cy - Math.round(TILE * 0.55), 18, 13, 3);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "700 9px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`+${d.helpers}`, cx + 23, cy - Math.round(TILE * 0.55) + 7);
  }
  ctx.restore();

  // One nameplate under feet — lead is a single combined pill (not two bubbles)
  if (showLabel) {
    const ny = d.y + Math.round(TILE * 0.38);
    ctx.save();
    ctx.font = `700 ${Math.max(10, Math.round(TILE * 0.2))}px ui-sans-serif, system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const label = d.lead ? `${d.spec.name} · LEAD` : d.spec.name;
    const tw = ctx.measureText(label).width + 12;
    const nx = labelX - tw / 2;
    if (focus || d.lead || d.action) {
      ctx.fillStyle = d.lead || focus ? statusColor : "rgba(20,28,40,0.9)";
      roundRect(ctx, nx, ny, tw, 14, 4);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = "#fff";
    } else {
      // Quiet names for everyone else — readable, not shouty
      ctx.globalAlpha = 0.85;
      ctx.fillStyle = "rgba(20,28,40,0.72)";
      roundRect(ctx, nx, ny, tw, 13, 4);
      ctx.fill();
      ctx.fillStyle = "#f2f5f8";
    }
    ctx.fillText(label, labelX, ny + 7);
    ctx.globalAlpha = 1;
    ctx.restore();

    if (d.action && !d.talking) {
      const ay = cy - Math.round(TILE * 0.95);
      ctx.save();
      ctx.font = `600 ${Math.max(9, Math.round(TILE * 0.16))}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const line = d.action.length > 36 ? d.action.slice(0, 34) + "…" : d.action;
      const aw = Math.min(160, ctx.measureText(line).width + 14);
      ctx.fillStyle = "#f8fafc";
      roundRect(ctx, labelX - aw / 2, ay, aw, 16, 5);
      ctx.fill();
      ctx.strokeStyle = statusColor;
      ctx.lineWidth = 1.25;
      ctx.stroke();
      ctx.fillStyle = "#1c2430";
      ctx.fillText(line, labelX, ay + 8);
      ctx.restore();
    }
  }

  if (d.token === "waiting" || d.token === "failed") {
    ctx.save();
    ctx.fillStyle = pal.status[d.token];
    ctx.beginPath();
    ctx.arc(cx + 16, cy - Math.round(TILE * 0.55), 7, 0, TWO_PI);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "700 10px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("!", cx + 16, cy - Math.round(TILE * 0.55) + 1);
    ctx.restore();
  }
}

function drawThoughtBubble(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  phase: number,
  reduced: boolean,
  accent: string,
) {
  const bob = reduced ? 0 : Math.sin(phase * 3) * 1.5;
  const bx = x + 12;
  const by = y - 8 + bob;
  const puff = (px: number, py: number, r: number) => {
    ctx.beginPath();
    ctx.arc(px, py, r, 0, TWO_PI);
    ctx.fill();
    ctx.stroke();
  };
  ctx.fillStyle = "#f7fafc";
  ctx.strokeStyle = accent;
  ctx.lineWidth = 1.5;
  puff(bx - 4, by + 12, 2.5);
  puff(bx + 1, by + 5, 3.5);
  puff(bx - 8, by - 4, 8);
  puff(bx + 8, by - 5, 9);
  puff(bx, by - 12, 10);
  puff(bx + 1, by, 8);
  ctx.fillStyle = accent;
  ctx.font = "700 12px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const n = reduced ? 3 : 1 + Math.floor((phase * 2.2) % 3);
  ctx.fillText(".".repeat(n), bx, by - 5);
}

export function drawSpeechBubble(
  ctx: CanvasRenderingContext2D,
  _pal: Palette,
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
  // Always high-contrast (palette bg/text are both dark in the light office theme)
  ctx.fillStyle = "#f8fafc";
  ctx.globalAlpha = 0.97;
  roundRect(ctx, bx, by, w, h, 8);
  ctx.fill();
  ctx.strokeStyle = "rgba(28,36,48,0.35)";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x - 6, by + h);
  ctx.lineTo(x + 6, by + h);
  ctx.lineTo(x, by + h + 7);
  ctx.closePath();
  ctx.fill();
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#1c2430";
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  lines.forEach((l, i) => ctx.fillText(l, bx + 8, by + 6 + i * 14));
  ctx.restore();
}

export function drawConversation(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  a: { x: number; y: number },
  b: { x: number; y: number },
  phase: number,
  _isMeeting: boolean,
) {
  ctx.save();
  // Feet-level link (drawn under sprites) — never cuts through faces
  ctx.strokeStyle = pal.status.collab;
  ctx.lineWidth = 2;
  ctx.globalAlpha = 0.55;
  ctx.setLineDash([5, 4]);
  ctx.lineDashOffset = -phase * 14;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y + 6);
  ctx.lineTo(b.x, b.y + 6);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  ctx.restore();
}

/** Thin dashed line while two agents are still walking to each other. */
export function drawApproaching(
  ctx: CanvasRenderingContext2D,
  pal: Palette,
  a: { x: number; y: number },
  b: { x: number; y: number },
  phase: number,
) {
  ctx.save();
  ctx.strokeStyle = pal.status.collab;
  ctx.globalAlpha = 0.4;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([4, 5]);
  ctx.lineDashOffset = -phase * 14;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y + 6);
  ctx.lineTo(b.x, b.y + 6);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function bobFor(anim: AnimationName, phase: number): number {
  switch (anim) {
    case "walk": return Math.abs(Math.sin(phase * 9)) * -2;
    case "idle": return Math.sin(phase * 1.6) * 0.8;
    case "celebrate": return Math.abs(Math.sin(phase * 6)) * -3;
    case "wait": return Math.sin(phase * 2.2) * 1.2;
    default: return 0;
  }
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
