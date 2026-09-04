# 21 — API Security & Readiness

## Probes

| Path | Purpose |
| --- | --- |
| `GET /health` | Liveness (process up) |
| `GET /readyz` | Readiness — store / MCP / metrics / LLM port surface (no secrets) |

`/readyz` returns **503** with check details when not ready.

## Optional API key

Set `SELERIC_API_KEY` or `API_KEY`. When non-empty, non-probe routes require:

- `X-API-Key: <key>` or
- `Authorization: Bearer <key>`

## Rate limiting

In-process sliding window (per API worker):

- `RATE_LIMIT_ENABLED` (default true)
- `RATE_LIMIT_PER_MINUTE` (default 60)

Exceeded → **429** with `Retry-After` and `X-RateLimit-*` headers.

Exempt: `/`, `/health`, `/readyz`, `/docs`, `/openapi.json`, `/redoc`.

## Request ID

Every response includes `X-Request-ID` (echoed from the request header when provided, otherwise minted). Use it to correlate API logs with LangSmith / mission traces.
