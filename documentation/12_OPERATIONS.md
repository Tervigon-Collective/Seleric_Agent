# 13. Operations

## 13.1 Start, stop, rebuild

```bash
cd /opt/seleric/Seleric_Agent
docker compose up -d                                   # postgres, redis, minio, clamav, api, recovery, cron loops
docker compose --profile voice up -d voice             # voice worker (deployed: running)
docker compose build api && docker compose up -d api recovery business-state-cron
docker compose up -d monitoring-cron                   # reuses seleric_agent-business-state-cron:latest (pull_policy never)
docker compose logs -f api recovery
curl -s localhost:8090/readyz
```

`seleric-migrate` runs in the API container's command before the server starts and applies `migrations/001…018`. Chronos has its own compose file in `chronos/`.

## 13.2 Health and readiness

`GET /readyz` (alias `/ready`) returns `200` with `ready: true` when every required dependency passes its bounded probe (timeout `READINESS_TIMEOUT_S`, default 2 s). Observed on 2026-11-11, all required checks `ok`:

| Check | Backend |
|-------|---------|
| `database`, `store` | postgres |
| `redis` | redis |
| `blob` | minio |
| `scanner` | clamav |
| `queue` | `DurablePollingRunQueue` |
| `provider` | mcp (20 capabilities discovered) |
| `mcp` | the Core MCP server |

The compose healthcheck calls `/readyz` every 15 s (5 s timeout, 5 retries, 25 s start period).

## 13.3 Scheduled jobs

See 5.9. The cron containers log to Docker; their outputs are snapshot files (volume `domain_health`), forecast reports (volume `forecast_reports`) and rows in Postgres schema `monitor`.

## 13.4 Observability

| Signal | Where |
|--------|-------|
| Request ids | `RequestIdMiddleware`; every log line and error carries it |
| Activity events | `run_events` table; `/v1/runs/{id}/events` |
| Mission trace and stats | `GET /v1/missions/{id}/trace`; `LoopResult.stats`: LLM calls, tool calls, duplicate calls, prompt and completion tokens, cached tokens, tool milliseconds, per-step LLM timings, time to first token, revisions, degenerate retries, stream fallbacks, errors |
| Admin diagnostics and replay | `GET /v1/admin/runs/{run_id}/diagnostics`, `POST /v1/admin/runs/{run_id}/replay`, `GET /v1/admin/capabilities` |
| Tracing | Langfuse (enabled), OpenTelemetry (`OTEL_ENABLED`), LangSmith (disabled) |
| Feedback | `message_feedback` table via `/v1/threads/{id}/messages/{mid}/feedback` |
| Monitoring panel | Office UI Ops tab and Monitoring page over `/v1/monitoring/*` |

## 13.5 Useful queries (read-only)

```sql
-- run outcomes
SELECT status, count(*) FROM runs GROUP BY 1 ORDER BY 2 DESC;
-- attempts currently leased
SELECT run_id, attempt_number, worker_id, lease_expires_at FROM run_attempts WHERE status = 'RUNNING';
-- latest monitoring evaluations
SELECT * FROM monitor.evaluations ORDER BY day DESC LIMIT 20;
-- open findings
SELECT * FROM monitor.findings ORDER BY 1 DESC LIMIT 20;
```

On 2026-11-11 the first query returned: COMPLETED 1,281; CANCELLED 10; FAILED 3.

## 13.6 Backup and data lifetime

State lives in Postgres (volume `seleric_postgres`), MinIO (`seleric_minio`), the `domain_health` and `forecast_reports` volumes, and the files under `.data/` (sandbox work directories per mission). The repository defines no backup job for these volumes. Conversation data has no automatic expiry in the code; deletion happens through the API (`DELETE /v1/threads/{id}`, `DELETE /v1/memories/{id}`, `DELETE /v1/attachments/{id}`).

## 13.7 Troubleshooting

See the table in 5.12. Additional checks:

| Symptom | Check |
|---------|-------|
| `/readyz` returns 503 | The body names the failing check; `provider`/`mcp` failures usually mean `SELERIC_MCP_URL`/`SELERIC_MCP_TOKEN` or Agent Core's host allow-list (an in-network URL is refused with HTTP 421; use the public `https://mcp.seleric.com/mcp`) |
| Streaming answers stall | `agent.stream_fallbacks` in stats; the loop redoes the turn unstreamed after a stalled stream |
| Model errors under load | Per-model cooldowns in `MODEL_HEALTH`; the fallback chain `gpt-5-mini` → `gpt-4o-code`; `AGENT_LLM_WAIT_BUDGET_S` |
| Many cancelled runs | Users cancelling, or the UI re-submitting; `run.cancelled` events carry the actor |
