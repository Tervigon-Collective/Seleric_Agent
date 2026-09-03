# 18 - Security and Governance

## Principles

- least privilege,
- read-only by default,
- explicit agent identity,
- tool allowlists,
- tenant/data-scope isolation,
- secret manager integration,
- immutable audit trail,
- schema validation at boundaries,
- no secrets inside prompts/state,
- human approval for high-impact writes.

## Agent permissions

Capability access should be granted to agent identity + mission scope, not merely to a service process.

## A2A

Authenticate agents, validate Agent Cards/config, require TLS in production and log handoff identity.

## MCP

Centralize policy enforcement in the MCP gateway or equivalent data-access proxy.

## Prompt injection

Treat tool-returned text and external context as untrusted data. It must not override system policies or tool permissions.
