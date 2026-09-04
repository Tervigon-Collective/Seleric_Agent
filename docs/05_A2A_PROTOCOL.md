# 05 - A2A Protocol Design

## Role

A2A is used for agent-to-agent interoperability and independent agent deployment boundaries.

## Do not use A2A as

- a replacement for the mission blackboard,
- a replacement for MCP,
- an excuse to send arbitrary prose between every agent,
- a way to bypass capability or permission checks.

## Agent discovery

Maintain Agent Cards / capability descriptors in the registry.

Each agent advertises:

- id and version,
- domain/capabilities,
- accepted task types,
- produced artifacts,
- security requirements,
- supported transport,
- handoff targets.

## Internal envelope

Structured payloads should be placed inside A2A messages/artifacts. See `schemas/a2a_envelope.schema.json`.

## Supported intents

- `task_request`
- `evidence_request`
- `artifact_response`
- `hypothesis_challenge`
- `leadership_transfer`
- `leadership_accept`
- `leadership_reject`
- `clarification_request`
- `completion_candidate`

## Context minimization

Transmit references and the minimum context required by the target agent. Do not forward the full conversation/history unless explicitly needed.

## Transports

| Mode | Class | Setting |
| --- | --- | --- |
| In-process | `InProcessTransport` | `a2a_transport=inprocess` (default) |
| HTTP | `A2AHttpTransport` | `a2a_transport=http` |
| Hybrid | `HybridTransport` | `a2a_transport=hybrid` (local handlers first, else HTTP) |

HTTP endpoint: `POST {base}/a2a/v1/agents/{agent_id}/messages` with `SwarmMessage` JSON.
Headers: `Idempotency-Key`, `X-Seleric-Mission-Id`, `X-Seleric-From-Agent`.

## Idempotency

Every task should have stable `mission_id`, `task_id`, `message_id` and `idempotency_key` fields.
