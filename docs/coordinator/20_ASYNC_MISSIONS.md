# 20 — Async Missions

Default: `POST /v1/missions` with `wait=true` runs synchronously and returns the finished mission.

## Async accept (`wait=false`)

1. API validates input (and `scenario_id` for swarm routes).
2. Seeds a pollable placeholder: `status=running`, `async=true`, `route=pending`.
3. Schedules background execution with a preassigned `mission_id`.
4. Returns the placeholder immediately.
5. Client polls `GET /v1/missions/{mission_id}` (and optionally `/events`) until status is terminal:

`completed` | `prototype_completed` | `partial` | `blocked` | `failed` | `cancelled`

## Cancel

`POST /v1/missions/{mission_id}/cancel` — cooperative cancel while `status=running`.

- Marks mission `cancelled` immediately and sets an in-process cancel flag.
- Background worker skips overwrite if cancel won the race.
- Already-terminal / non-running missions → **409**.
- Unknown id → **404**.

Failures in the background worker persist `status=failed` with an error limitation (never leave a hung `running` forever when the worker exits).
