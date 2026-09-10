# Realtime architecture

## Snapshot + stream

1. On mission select the front-end calls `getSnapshot(missionId)` → full derived state.
2. It then `subscribe()`s. The `SelericEventProvider` opens an `EventSource` to
   `/v1/office/missions/{id}/stream`.
3. The stream emits, in order:
   - one `snapshot` frame immediately (so a fresh subscriber is complete even if the
     initial fetch was skipped);
   - `event` frames as new persisted events appear (poll-and-diff, 1 s);
   - a fresh re-derived `snapshot` after each batch of new events (drift correction);
   - `heartbeat` every 15 s of quiet;
   - a terminal `done` frame when the mission reaches a terminal status.

## Reconnection

`SelericEventProvider` reconnects with exponential backoff (1 s → 15 s cap). Because
the store dedupes by `seq`, the small replay overlap after a reconnect is harmless — no
special resume cursor is needed. Connection state (`connecting · live · reconnecting ·
closed · error`) is surfaced in the top bar pill.

## Idempotency & ordering (store.ts)

- `seenSeq: Set<number>` — an event whose `seq` was already applied is ignored
  entirely (no timeline row, no animation, no state change).
- `timeline` is always kept sorted by `seq`, so out-of-order delivery self-heals.
- `hydrate()` **merges** the incoming `timeline` with what's already there (union by
  `seq`) instead of replacing it — a re-emitted snapshot never drops rows or resets
  dedupe. An empty `agents` array means "mission-level patch only" (used by the demo
  provider's per-beat patches).
- `lastSeq` is the max seq ever seen; `hydrate` never moves it backwards.

## Failure modes covered by tests

| Case | Test |
| --- | --- |
| duplicate event | `store.test.ts` "dedupes repeated events by seq" |
| out-of-order events | `store.test.ts` "keeps the timeline ordered" |
| snapshot re-emit mid-stream | `store.test.ts` "merges the timeline instead of wiping it" |
| terminal mission stream | `test_office_gateway.py::test_stream_emits_snapshot_then_done` |
| full replay through the store | `demoScenario.test.ts` "replays through the store" |
