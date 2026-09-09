# Seleric AI Office — Overview

A real-time **spatial** visualization of the Seleric intelligence swarm. Instead of
boxes, tables and JSON, you look into a living digital office where each agent is a
character at a desk, and every visible behaviour is a projection of a real backend
mission event.

> The virtual office is a spatial projection of Seleric's real mission state, not an
> animation layered on top of it.

## What's here

| Path | What it is |
| --- | --- |
| `office-ui/` | The front-end app (Vite + React + TS + Zustand). Pixel-office canvas renderer (floors, walls, desks, furniture, walk-cycle characters) inspired by [Parcha AI Office](https://github.com/Parcha-ai/ai-office) — no Convex/Pinecone/Clerk. |
| `src/seleric_swarm/api/office/` | The read-only UI gateway: event normalizer + SSE stream. |
| `tests/api/test_office_*.py` | Backend normalizer + gateway tests. |
| `office-ui/src/__tests__/` | Front-end state-machine / store / demo tests. |

## Documents

1. [`01_PRODUCT_CONCEPT.md`](01_PRODUCT_CONCEPT.md) — the idea and non-goals.
2. [`02_REFERENCE_ARCHITECTURE.md`](02_REFERENCE_ARCHITECTURE.md) — how the pieces fit; influences.
3. [`03_OFFICE_LAYOUT.md`](03_OFFICE_LAYOUT.md) — zones, desks, spatial rules.
4. [`04_AGENT_STATES.md`](04_AGENT_STATES.md) — the office state machine.
5. [`05_EVENT_PROTOCOL.md`](05_EVENT_PROTOCOL.md) — `SwarmUIEvent`, `OfficeSnapshot`, kind mapping.
6. [`06_REALTIME_ARCHITECTURE.md`](06_REALTIME_ARCHITECTURE.md) — snapshot + stream, reconnection, dedupe.
7. [`08_LEADERSHIP_VISUALIZATION.md`](08_LEADERSHIP_VISUALIZATION.md) — dynamic leadership + handoffs.
8. [`09_DIAGNOSTIC_VISUALIZATION.md`](09_DIAGNOSTIC_VISUALIZATION.md) — hypotheses / causal frontier.
9. [`10_SKEPTIC_VISUALIZATION.md`](10_SKEPTIC_VISUALIZATION.md) — REVISE → remediation loop.
10. [`11_MISSION_LIFECYCLE.md`](11_MISSION_LIFECYCLE.md) — the primary user journey.
11. [`12_DEMO_MODE.md`](12_DEMO_MODE.md) — the scripted CAC fixture.
12. [`15_TESTING.md`](15_TESTING.md) — what is covered and how to run it.
13. [`16_PRODUCTION_READINESS.md`](16_PRODUCTION_READINESS.md) — security, gaps, next steps.

## Run it

```bash
# 1. front-end, demo mode (no backend needed)
cd office-ui && npm install && npm run dev      # http://localhost:5173  (?demo=1)

# 2. against the live swarm
#    terminal A – API
API_HOST=127.0.0.1 API_PORT=8080 seleric-api    # or: uvicorn seleric_swarm.main:app --port 8080
#    terminal B – UI (proxies /v1 -> :8080)
cd office-ui && SELERIC_API_URL=http://127.0.0.1:8080 npm run dev
#    open http://localhost:5173/?mission=<mission_id>  and pick "Live swarm"
```

## Tests

```bash
cd office-ui && npm test                                   # 20 front-end tests
python -m pytest tests/api/test_office_normalize.py tests/api/test_office_gateway.py
```
