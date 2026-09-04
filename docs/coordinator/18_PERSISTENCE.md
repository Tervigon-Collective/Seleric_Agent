# 18 — Persistence

## Backends

| Setting | Store |
| --- | --- |
| `PERSISTENCE_BACKEND=memory` | `InMemoryMissionStore` (default / tests) |
| `PERSISTENCE_BACKEND=postgres` | `PostgresMissionStore` |

## Protocol

`MissionStore`:

- `put(result, raw_state)` — typed `MissionResult` + full raw payload (swarm dict or LangGraph state)
- `get(mission_id)` — typed lookup view
- `get_raw(mission_id)` — full payload (`route=swarm` preferred by API)
- `list_events(mission_id, family=, after_seq=, limit=)` — structured control-plane events

## Postgres durability (v1.7)

Migration `002_mission_payload.sql` adds `route`, `result_json`, `raw_json` on `missions`.

On put, Postgres also:

- upserts evidence / claims
- replaces `mission_events` rows from the structured event log
- replaces `leadership_transfers` for the mission

`get` / `get_raw` reconstruct from JSON so missions survive process restart.

## API

```http
GET /v1/missions/{mission_id}/events?family=mission&after_seq=0&limit=200
```
