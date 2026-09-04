# 11 — A2A Integration

`seleric.swarm.v1` envelopes via transport abstraction:

- `InProcessTransport` — default for local swarm
- `A2AHttpTransport` — remote agents over HTTP
- `HybridTransport` — local first, HTTP fallback

Coordinator activates specialists with task objectives and artifact refs — not transcripts.

Duplicate A2A delivery is absorbed by task `idempotency_key` + artifact fingerprints.

Settings: `a2a_transport`, `a2a_public_base_url`, `a2a_timeout_s`.

HTTP path: `POST {base}/a2a/v1/agents/{agent_id}/messages`
