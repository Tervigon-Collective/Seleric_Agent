# Performance

Target: fluid scene with 15–30 agents, many events, hover cards, inspector, animation.

## Rendering

- One `requestAnimationFrame` loop in `OfficeCanvas`. All character motion is tweened
  on the canvas — **no React re-render per frame or per event**.
- Character positions are integrated toward their state destination each frame
  (`LERP`), so a burst of events doesn't cause teleporting or layout thrash.
- Devicepixelratio capped at 2. Grid + zones are cheap primitives; characters are
  procedural (a few paths each).
- `prefers-reduced-motion` removes per-frame trig (bob, dash offset, walk cycle) —
  characters snap to destination and the loop still runs but does near-zero work.

## State

- Zustand store; DOM overlays subscribe to **narrow selectors** (e.g. `s => s.board`)
  so a `leadership_transferred` event re-renders only the header/board/inspector, not
  the timeline list.
- `ingestEvent` is O(agents) — a shallow map copy + one `switch`. `seenSeq` lookup is
  O(1); duplicates return immediately.
- `timeline` is re-sorted on insert (small N; missions are tens–hundreds of events).

## Transport

- SSE, 1 s poll-and-diff on the server, 15 s heartbeat. The client never polls.
- Reconnect backoff 1 s→15 s; replay overlap is absorbed by seq dedupe.

## If it needs to scale further

Replace `gateway.py`'s poll loop with a push subscription to the mission event bus
(same SSE frames out), and virtualize the timeline list. Neither is needed at current
event volumes.
