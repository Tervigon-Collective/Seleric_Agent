# Reference architecture

```
 Seleric swarm (existing)                        Office UI gateway (new, read-only)
┌───────────────────────────┐                   ┌──────────────────────────────────┐
│ Coordinator / LangGraph   │  writes events    │ normalize.py                     │
│  graph.py  ctx.emit(...)  │ ────────────────▶ │  control-plane event ─▶ SwarmUIEvent
│ Blackboard.events[]       │                   │  raw payload       ─▶ OfficeSnapshot
│ MissionStore.put(result,  │                   │                                  │
│   raw={events, artifacts, │  store.get_raw    │ gateway.py (FastAPI router)      │
│   mission_lead, tasks,…})  │ ◀──────────────── │  GET /v1/office/missions          │
└───────────────────────────┘   list_events     │  GET .../{id}/snapshot            │
                                                │  GET .../{id}/stream   (SSE)      │
                                                └───────────────┬──────────────────┘
                                                                │ snapshot + events
                                                     ┌──────────▼───────────┐
                                                     │ SwarmEventProvider   │  one interface
                                                     │  ├ SelericEventProvider (SSE)
                                                     │  └ DemoEventProvider    (scripted)
                                                     └──────────┬───────────┘
                                                                │
                                          ┌─────────────────────▼─────────────────────┐
                                          │ Zustand store (store.ts)                  │
                                          │  hydrate(snapshot) · ingestEvent(ev)      │
                                          │  dedupe by seq · order by seq · fold      │
                                          └───────────┬───────────────────┬───────────┘
                                                      │                   │
                                         ┌────────────▼──────┐   ┌────────▼───────────┐
                                         │ OfficeCanvas       │   │ DOM overlays       │
                                         │ camera, movement,  │   │ header, board,     │
                                         │ characters, links  │   │ hovercard, inspector,
                                         │ (requestAnimation) │   │ timeline, debug    │
                                         └────────────────────┘   └────────────────────┘
```

## Key decisions

- **Parcha = visual/world inspiration. Seleric backend = source of truth.** Convex
  simulated agents are *not* mapped to Seleric agents.
- **The gateway reuses events Seleric already emits** (`coordinator/observability/events.py`
  canonical kinds). It does not ask every agent to send cosmetic UI events.
- **Snapshot + incremental stream.** On load: `GET .../snapshot` (full derived state).
  Then subscribe to the SSE stream for `event` frames and periodic re-derived `snapshot`
  frames so drifted / late clients converge without replaying the whole log.
- **The bridge is a poll-and-diff over persistence today.** When the backend grows a
  native event bus, only `gateway.py` changes; `normalize.py` and the whole front-end
  are unaffected.
- **Idempotency lives in the store**, not the transport. Every event carries a `seq`;
  repeated / out-of-order / replayed events never double-apply.

## Frontend stack

Next.js was not adopted — the repo has no existing front-end and the office is a single
full-screen canvas app, so plain **Vite + React 18 + TypeScript + Zustand** keeps it
small (no SSR need). Rendering is a hand-rolled **2D canvas** layer (camera, tweened
movement, procedural characters) — deliberately not a game engine.
