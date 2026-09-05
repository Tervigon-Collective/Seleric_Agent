# 24 - API Contracts

## Mission create

```http
POST /v1/missions
```

Sync by default (`wait` omitted / `true`). Swarm analytical queries require an explicit fixture or runtime pack via `scenario_id`.

```json
{
  "query": "Why did CAC increase in the last 3 days?",
  "scenario_id": "cac_regression",
  "scope": {"timezone": "Asia/Kolkata"},
  "mode": "read_only",
  "wait": true
}
```

Async acceptance (`wait: false`) returns immediately with `status: "running"`; poll `GET /v1/missions/{mission_id}` and cancel with `POST /v1/missions/{mission_id}/cancel`.

Lookup / status queries do not require `scenario_id` (it is ignored on that route).

## Mission response (sync completed)

```json
{
  "mission_id": "MS-...",
  "status": "completed",
  "route": "swarm",
  "mission_lead": "performance_agent",
  "final_response": { "...": "..." }
}
```

## Mission response (async accepted)

```json
{
  "mission_id": "MS-...",
  "status": "running",
  "async": true
}
```

## Mission get

```http
GET /v1/missions/{mission_id}
```

## Mission events

```http
GET /v1/missions/{mission_id}/events?family=mission&after_seq=0&limit=200
```

Returns structured control-plane events (`kind`, `ts`, `seq`, `family`, …) persisted with the mission.

Pagination fields:

- `has_more` — true when more events exist after this page
- `next_after_seq` — pass as the next request’s `after_seq` when `has_more` is true

## Agent A2A endpoints

Expose protocol-compliant A2A routes according to the selected SDK. Do not create a parallel proprietary HTTP contract for inter-agent communication unless required for internal optimization.

## Internal service APIs

Use explicit versioned contracts for:

- metric evaluation,
- anomaly inference,
- forecast inference,
- causal analysis,
- evidence persistence,
- registry lookup.
