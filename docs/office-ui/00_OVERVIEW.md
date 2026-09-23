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

> The swarm-roster-specific docs that used to live here (product concept,
> reference architecture, office layout, agent states, event protocol,
> leadership/diagnostic/skeptic visualizations, mission lifecycle walkthrough,
> demo mode) described the retired swarm_v2 agent roster and were removed
> during the V3 cleanup. See `docs/CURRENT_ARCHITECTURE.md` for the current
> system; the event vocabulary this UI renders will need updating for
> whatever V3's `api/office/gateway.py` actually emits.

1. [`06_REALTIME_ARCHITECTURE.md`](06_REALTIME_ARCHITECTURE.md) — snapshot + stream, reconnection, dedupe.
2. [`13_PERFORMANCE.md`](13_PERFORMANCE.md) — canvas rendering / perf notes.
3. [`14_ACCESSIBILITY.md`](14_ACCESSIBILITY.md) — status-encoding, reduced-motion, keyboard rules.
4. [`15_TESTING.md`](15_TESTING.md) — what is covered and how to run it.
5. [`16_PRODUCTION_READINESS.md`](16_PRODUCTION_READINESS.md) — security, gaps, next steps.

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
