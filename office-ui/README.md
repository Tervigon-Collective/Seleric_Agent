# Seleric AI Office

Spatial, real-time visualization of the Seleric intelligence swarm. A 2D walkable
office where each agent is a character at a desk and **every visible behaviour is a
projection of a real Seleric mission event** — not a simulation layered on top.

Full docs: [`../docs/office-ui/`](../docs/office-ui/00_OVERVIEW.md).

## Quick start

```bash
npm install
npm run dev            # http://localhost:5173  — starts in DEMO mode, no backend needed
```

Live mode (against the running Seleric API):

```bash
# terminal A — API (8090; 8080 is often taken on this machine)
API_HOST=127.0.0.1 API_PORT=8090 seleric-api

# terminal B — start a mission you can watch (wait=false)
curl -X POST http://127.0.0.1:8090/v1/missions -H "Content-Type: application/json" -d "{\"query\":\"Why has CAC increased?\",\"wait\":false}"

# terminal C — office UI (proxies /v1 → API)
SELERIC_API_URL=http://127.0.0.1:8090 npm run dev
# open http://localhost:5173/?mission=<mission_id>
# TopBar should show "Live swarm"
```

## Scripts

| | |
| --- | --- |
| `npm run dev` | Vite dev server, `/v1` proxied to the API |
| `npm test` | Vitest (state machine, store, demo replay) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run build` | production bundle to `dist/` |

## Architecture (one paragraph)

`providers/` expose one `SwarmEventProvider` interface with two implementations —
`DemoEventProvider` (scripted CAC fixture) and `SelericEventProvider` (SSE bridge to
`/v1/office`). Both feed the Zustand `store.ts`, which hydrates from an `OfficeSnapshot`
and folds incremental `SwarmUIEvent`s (deduped and ordered by `seq`). `render/OfficeCanvas`
draws a **pixel-office world** (textured floors, walled rooms, desks, furniture, walk-cycle
characters) on a single `requestAnimationFrame` loop — same spatial metaphor as
[Parcha AI Office](https://github.com/Parcha-ai/ai-office), without Convex/Pinecone/Clerk.
HUD overlays (header, board, hover, inspector, timeline) are plain DOM.

## Influences

Parcha AI Office (spatial world / map / camera), AgentOffice (event-driven agent
presence, status/task/progress schema), belle05/agent-office (hover/task/tool
interaction), agent-hq (activity feed). No code or assets were copied — the renderer is
a bespoke lightweight canvas layer with no game engine and no Convex/Pinecone/Clerk.
