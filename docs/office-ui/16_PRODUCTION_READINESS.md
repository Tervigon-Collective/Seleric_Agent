# Production readiness

## Security

- The gateway is **read-only**: only `GET` routes, no mutation, no orchestration. It
  cannot change ads / inventory / pricing / deployments / finances.
- It honours the platform API key (`ApiSecurityMiddleware`) when one is set; the
  `/v1/office/*` paths are not in the exempt list.
- CORS is restricted to `localhost` / `127.0.0.1` (dev). Tighten `allow_origin_regex`
  in `main.py` for a real deployment origin.
- `normalize.py` only forwards event envelope + known metadata fields; it strips
  `workflow_name/version` internals. It never forwards secrets/tokens because the
  source control-plane events don't carry them — if that changes, add a redaction
  allow-list in `_event_summary` / the metadata filter.
- No chain-of-thought is ever exposed — only structured stage lists and event summaries.

## Performance

- Canvas renders on one `requestAnimationFrame` loop; per-event work is O(agents) map
  folds, not full-scene React re-renders. DOM overlays subscribe to narrow Zustand
  slices.
- SSE poll interval 1 s; heartbeat 15 s. Fine for 15–30 agents and typical mission
  event rates. For very high event volume, replace the poll-and-diff in `gateway.py`
  with a real subscription to the mission event bus (interface unchanged).
- `registry.py` is bounded (200 ids) so a long-lived API process can't leak.

## Resolved since first cut

1. **Mission enumeration** — `list_missions()` added to `MissionStore`
   (`InMemoryMissionStore` iterates its ordered map; `PostgresMissionStore` runs
   `SELECT … ORDER BY updated_at DESC`). The gateway prefers it, falling back to the
   in-process registry, so it works across workers / after restart. Test:
   `test_office_gateway.py::test_missions_list_prefers_store_list_missions`.
2. **Multi-mission view** — `MissionMinimap` overlay lists every mission the gateway
   knows with a status pip + current lead; click / Enter switches the office; the list
   auto-refreshes every 10 s. Hidden when there is only one.
3. **Sub-agent / parallel-task fan-out** (brief §30) — the snapshot derives
   `parallelTasks` by grouping planned `tasks[]` per assigned agent (only when >1 is in
   flight); the canvas orbits helper blips around that desk and the inspector lists
   them. Test: `test_office_normalize.py::test_snapshot_parallel_tasks_group_by_agent`.
4. **LangSmith deep link** — snapshot passes `trace.{requestId,sessionId}` through and
   builds a project-scoped `traceUrl` when `langsmith_project` (+ optional
   `langsmith_org`) is set; shown in the Debug panel.
5. **A2A evidence exchange** — `evidence_requested` / `evidence_received` mapped
   end-to-end (normalizer → store → state machine → travelling-document link to the
   Evidence Archive). Not fabricated: the path lights up only when the backend emits
   the events. Test: `test_office_normalize.py::test_evidence_a2a_events_normalize`.
6. **Keyboard navigation** — `[` / `]` cycle the selected agent, `Esc` clears; minimap
   rows are keyboard-activatable. jsdom a11y smoke test in `src/__tests__/overlays.test.tsx`.

## Remaining gaps / next steps

1. **SSE = poll-and-diff.** Swap `gateway.py`'s poll loop for a push subscription when
   a mission event bus exists (SSE frames out stay identical).
2. **Visual-regression + full axe run** need a headless-GL / Playwright harness; the DOM
   overlays are built to pass and now have a jsdom smoke test.
3. **Mobile** degrades to the office + inspector; a narrow-width agent-list is
   CSS-stubbed only.
4. **Multi-mission rendering** — still one mission on the floor at a time; a
   small-multiples / picture-in-picture view of a second running mission is future work.

## Run commands

```bash
# demo
cd office-ui && npm install && npm run dev

# live
API_HOST=127.0.0.1 API_PORT=8080 seleric-api
cd office-ui && SELERIC_API_URL=http://127.0.0.1:8080 npm run dev

# tests
cd office-ui && npm test && npm run build
python -m pytest tests/api/test_office_normalize.py tests/api/test_office_gateway.py
```
