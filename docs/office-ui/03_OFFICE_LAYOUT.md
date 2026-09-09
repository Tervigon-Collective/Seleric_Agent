# Office layout

Defined in `office-ui/src/office/layout.ts` (zones, desks, spots) and
`office-ui/src/office/agents.ts` (the roster). Map config is kept separate from all
business logic — edit it freely.

## World

`WORLD = 1680 × 1040` world units (px). Camera: pan (drag), zoom (wheel, 0.4–1.8),
and follow modes `off` / `lead` / `activity`.

## Zones

| Zone | Who / what |
| --- | --- |
| Mission Control | Coordinator, planning board, mission board |
| Domain Operations Floor | Observer, Anomaly |
| Performance / Commerce / Funnel / Technical bays | domain agents |
| Finance / Inventory / Procurement bay | domain agents |
| Diagnostic Lab | Diagnostic — hypothesis testing |
| Prediction Lab | Prediction — forecast terminal |
| Strategy Room | Strategy — intervention cards |
| Skeptic Review Room | Skeptic — visually distinct |
| Handoff / Meeting Room | leadership transfers |
| Evidence Archive | evidence / artifact deep-dive |
| Waiting Lounge | idle / waiting-for-agent |

A zone lights up (accent border + tint + header strip) whenever an agent whose home
desk is in it is non-idle. That is the **causal-frontier cue**: as leadership moves
Performance → Funnel → Technical, the lit zone moves with it.

## Desks & shared spots

Every agent has a home workstation (`DESKS`). States route characters to shared spots
(`SPOTS`) — but only when it means something:

| State | Destination |
| --- | --- |
| `working`, `tool_running`, `collaborating` | home workstation |
| coordinator `planning` / `thinking` | planning board |
| coordinator `working` | mission room |
| `handoff` | handoff area |
| `reviewing` (skeptic) | skeptic desk; (others) handoff area |
| `retrieving_evidence`, `waiting_for_evidence` | data terminal |
| `waiting_for_agent` | lounge |
| `idle`, `offline` | home desk |

Idle / short states never trigger a long walk.
