# Seleric Conversation Platform + AI Office

The Phase 3 React shell adds persistent conversation threads, transcript/composer,
run activity and context panels around the existing spatial office. The Office tab
retains the 2D walkable swarm visualization.

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
# open http://localhost:5173/?demo=0 (conversation shell)
# use ?office=1&mission=<mission_id> to open the spatial workspace directly
```

If the API enables shared-key authentication, set `seleric.apiKey` in browser
local storage. HTTP and fetch-based SSE requests both send it as a bearer token.

## Scripts

| | |
| --- | --- |
| `npm run dev` | Vite dev server, `/v1` proxied to the API |
| `npm test` | Vitest (state machine, store, demo replay) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run build` | production bundle to `dist/` |

## Architecture

`api/` mirrors backend contracts and owns HTTP, conversation/mission endpoints,
and authenticated fetch-based SSE with cursor reconnection. `stores/conversation.ts`,
`stores/missionRuntime.ts`, and `stores/shell.ts` keep server data, spatial runtime,
and layout preferences separate.

`providers/` expose one `SwarmEventProvider` interface with two implementations —
`DemoEventProvider` (scripted CAC fixture) and `SelericEventProvider` (SSE bridge to
`/v1/office`). Both feed the Zustand `store.ts`, which hydrates from an `OfficeSnapshot`
and folds incremental `SwarmUIEvent`s (deduped and ordered by `seq`). `render/OfficeCanvas`
draws a **pixel-office world** (textured floors, walled rooms, desks, furniture, walk-cycle
characters) on a single `requestAnimationFrame` loop — same spatial metaphor as
[Parcha AI Office](https://github.com/Parcha-ai/ai-office), without Convex/Pinecone/Clerk.
HUD overlays (header, board, hover, inspector, timeline) are plain DOM.

### Assistant UI runtime boundary

`SelericAssistantRuntimeProvider` adapts the externally owned
`useConversationStore` to `@assistant-ui/react` through
`useExternalStoreRuntime`. Assistant UI supplies the provider plus thread,
message, composer, attachment, cancel, and retry primitives; Zustand remains the
single owner of server messages, run state, and SSE reconnection. Seleric typed
parts cross the runtime as named data parts and are still rendered by
`MessagePartRenderer`, so approval, artifact, source, chart, and activity-panel
semantics remain domain-owned. Retry maps to a new Seleric submission linked to
the source turn because the backend has no regenerate endpoint.

## Influences

Parcha AI Office (spatial world / map / camera), AgentOffice (event-driven agent
presence, status/task/progress schema), belle05/agent-office (hover/task/tool
interaction), agent-hq (activity feed). No code or assets were copied — the renderer is
a bespoke lightweight canvas layer with no game engine and no Convex/Pinecone/Clerk.
