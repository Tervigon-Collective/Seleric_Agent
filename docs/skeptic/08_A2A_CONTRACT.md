# 08 - A2A Contract

`agents/skeptic/a2a.py` is the **only** file that knows about envelopes. The
domain logic (`agent.py`, validators, ...) is protocol-independent.

## Supported intents

```
claim_validation
artifact_validation
hypothesis_challenge
completion_candidate
challenge            (legacy alias used by the swarm orchestrator)
```

## Inbound payload

```json
{
  "mission_id": "M-100",
  "task_id": "SK-92",
  "intent": "claim_validation",
  "claim": {
    "mission_id": "M-100",
    "claim_type": "causal",
    "statement": "mobile latency regression drove the CVR decline",
    "origin_agent": "diagnostic_agent",
    "support_refs": ["EV-11", "EV-22"],
    "causal_refs": ["CAUS-3"]
  },
  "evidence_refs": ["EV-11", "EV-22"],
  "available_artifact_refs": ["CAUS-3", "STRAT-1"],
  "risk_context": {"impact": "high"}
}
```

If `claim` is omitted the adapter synthesizes one from `statement`/`objective` +
the `*_refs` lists (`_claim_from_refs`).

## Outbound (artifact_response)

```json
{
  "ok": true,
  "protocol": "seleric.swarm.v1",
  "mission_id": "M-100",
  "task_id": "SK-92",
  "intent": "artifact_response",
  "produced": "skeptic_verdict",
  "artifact": { ... full SkepticVerdict ... },
  "verdict": "REVISE",
  "required_followups": [ { "task_id": "FUP-...", "requested_capability": "...", ... } ]
}
```

## In-process transport

`SkepticA2AAdapter.as_handler` matches the
`seleric_swarm.swarm.transport.InProcessTransport` handler signature
`(SwarmMessage) -> dict`, so the swarm orchestrator can register the full Skeptic
subsystem in place of the lightweight `swarm/specialists/skeptic.py`:

```python
from seleric_swarm.agents.skeptic import SkepticAgent, SkepticA2AAdapter, skeptic_from_blackboard

adapter = SkepticA2AAdapter(skeptic_from_blackboard(blackboard, deps=deps))
transport.register("skeptic_agent", adapter.as_handler)
```

An `A2AHttpTransport` later needs no Skeptic change - only a different transport
behind the same adapter.
