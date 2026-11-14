# 12. Security

Controls found in the code, what they protect, and the gaps that follow from the current design.

## 12.1 Controls

| Area | Control | Enforced in |
|------|---------|-------------|
| Authentication | Shared API key (`x-api-key` or `Authorization: Bearer`), constant-time comparison. With no key and no authenticator configured, requests are treated as authenticated (development behaviour). Deployed: key set | `api/security.py` |
| Public paths | `/`, `/health`, `/readyz`, `/docs`, `/openapi.json`, `/redoc`, `/v1/voice/dev`, `/ui/*`; everything else under `/v1` and `/ready` requires authentication | `_is_exempt` |
| Identity | `Principal(workspace_id, user_id, roles, auth_method)`. Defaults `default_workspace_id` / `default_user_id` (`default`, `default`). Identity headers (`x-workspace-id`, `x-user-id`, `x-principal-id`, `x-roles` and `x-seleric-*` variants) are honoured only when the request is authenticated **and** `TRUST_IDENTITY_HEADERS` is true **and** the trusted-proxy predicate passes | `api/security.py` |
| Ownership | Threads, runs, attachments, drafts and memories are visible only to the owning user in the workspace; foreign ids return 404 or 403; admin routes need `is_admin` | `api/conversations.py`, `api/emails.py`, `api/phase7.py` |
| Rate limiting | Optional sliding window per client key (default 60 requests per minute, 429 with `Retry-After`). Deployed: off | `SlidingWindowRateLimiter` |
| CORS | In a production surface: no cross-origin origins allowed; in a development surface: any origin or localhost. The Office router adds GET-only CORS for localhost origins | `main.py` |
| Data access | The loop may call only 17 read-only Core tools; no SQL tool, no action tools, no ads tools | `agent/mcp_toolset.py::ALLOW` |
| Tool inputs | Arguments validated against the server's published JSON Schema; known misuse blocked; identical calls served from the turn cache | `agent/mcp_toolset.py` |
| Numeric integrity | Deterministic figure check and a single corrective pass | `agent/grounding.py`, `agent/loop.py` |
| Sandboxed Python | Child process with empty environment, resource limits (2 GiB address space, CPU seconds, 64 MiB files, open files), restricted builtins, import allow-list, and an audit hook that refuses process creation, network, native libraries and file access outside the working directory; killed at the timeout | `toolsets/sandbox.py`, `sandbox_child.py` |
| Uploads | Allowed MIME types and size limit (25 MiB); ClamAV scan; a message can reference only `READY` and `CLEAN` attachments of its own thread | `api/conversations.py`, `conversations/blobs.py` |
| Email | Recipients from explicit addresses or a trusted contact list; free addresses limited to `EMAIL_ALLOWED_DOMAINS` (default `seleric.ai`); approval bound to the exact content hash; at most one hand-off to the transport; quoted figures come only from the verified figure pool | `mail/`, `agent/email_tools.py` |
| Failure text | User-facing failures never include provider HTTP bodies; the stored `failure_detail` keeps exception types and status codes only | `agent/runner.py` |
| Secrets | Read from environment/`.env`; not in YAML | `config/settings.py` |
| Voice | The browser receives a short-lived LiveKit JWT scoped to one room derived from a thread the caller owns; it never sees the LiveKit secret | `voice/token.py` |
| Duplicate execution | Lease-fenced attempts and a content-hashed email approval prevent double work and double sends | `recovery.py`, `mail/send.py` |

## 12.2 Network exposure (deployed, from the compose override)

| Service | Binding |
|---------|---------|
| `api` | `0.0.0.0:8090` (public; serves the API and `/ui/`; `/v1` requires the API key) |
| `postgres` | `127.0.0.1:5434` |
| `redis` | not published |
| `minio`, `clamav`, `chronos` | container network only |

## 12.3 Gaps that follow from the design

1. **One shared key means one identity.** With a single `API_KEY` and `TRUST_IDENTITY_HEADERS` off, every caller is workspace `default`, user `default`; ownership checks then separate nothing between callers. Per-user separation requires a trusted proxy that sets identity headers (and `TRUST_IDENTITY_HEADERS=true`) or an external authenticator.
2. `/docs` and `/openapi.json` are unauthenticated and list every route.
3. Rate limiting is disabled in the deployment.
4. The default Postgres credentials in `docker-compose.yml` are `seleric`/`seleric`; the override binds Postgres to loopback to keep them off the network (comment in the override file).
5. `Jenkinsfile.eval` is the only pipeline for this repository and runs static gates only; there is no automated deployment or secret scanning stage in it.
6. The voice worker module docstring is stale (describes an echo-only phase), which can mislead a reviewer about what the worker does.
