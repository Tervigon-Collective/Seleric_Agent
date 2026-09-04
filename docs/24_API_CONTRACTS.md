# 24 - API Contracts

## Mission create

```http
POST /v1/missions
```

```json
{
  "query": "Why did CAC increase in the last 3 days?",
  "scope": {"timezone": "Asia/Kolkata"},
  "mode": "read_only"
}
```

## Mission response

```json
{
  "mission_id": "M-...",
  "status": "running",
  "mission_lead": "performance_agent",
  "active_specialist": "observer_agent"
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
