# Testing

## Front-end (`cd office-ui && npm test`) — 20 tests, Vitest + jsdom

| File | Covers |
| --- | --- |
| `src/__tests__/stateMachine.test.ts` | event → state, event → office location, task assignment, tool-running / waiting / blocked / completion states, leadership transfer (single ring), Skeptic REVISE, purity (no input mutation) |
| `src/__tests__/store.test.ts` | snapshot hydration, duplicate event dedupe, out-of-order ordering, snapshot re-emit merge, completion status, leadership ring follow |
| `src/__tests__/demoScenario.test.ts` | fixture completeness, two-handoff sequence, full replay → completed mission, unique seqs |

## Backend (`python -m pytest tests/api/test_office_*.py`) — 13 tests

| File | Covers |
| --- | --- |
| `test_office_normalize.py` | event stream shape & order, `after_seq` incrementality, leadership from/to agent mapping, full roster in snapshot, lead ring follows `mission_lead`, board + stage on completion, running mission keeps active agents, handoffs from history, empty-mission safety |
| `test_office_gateway.py` | `/missions` list, `/snapshot` derivation + 404, SSE stream emits `snapshot` first then `done` on a terminal mission |

## Typecheck / build

```bash
cd office-ui && npm run typecheck && npm run build   # tsc --noEmit, then vite build
python -m ruff check src/seleric_swarm/api/office/
```

## Not yet automated (see 16_PRODUCTION_READINESS.md)

Visual-regression screenshots and axe/keyboard a11y assertions — the canvas renderer
would need a headless-GL harness. The DOM overlays (header, board, hover card,
inspector, timeline) are plain semantic HTML with focus states and `aria-label`s and
are screenshot-friendly once a harness is added.
