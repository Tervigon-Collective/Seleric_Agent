# Testing `POST /v1/missions` with curl / Postman

Default port: **8000** (uvicorn default; Docker exposes 8000).

Auth: `x-api-key: <key>` (or `Authorization: Bearer <key>`). If no `api_key` is
configured in settings, auth is open — drop the header.

Constraints: `mode` must be `read_only`, `execution_mode` must be `development`
(any other value returns 400). `wait` defaults to `true` if omitted.

## Async (recommended — accepts immediately, then poll)

```bash
curl -s -X POST http://127.0.0.1:8000/v1/missions \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{
    "query": "Why has CAC increased over the last three days?",
    "scope": {"timezone": "Asia/Kolkata"},
    "mode": "read_only",
    "execution_mode": "development",
    "wait": false
  }'
```

Poll with the returned id:

```bash
curl -s http://127.0.0.1:8000/v1/missions/MISSION_ID -H "x-api-key: YOUR_API_KEY"
```

## Sync (blocks until the mission finishes — can be slow)

```bash
curl -s -X POST http://127.0.0.1:8000/v1/missions \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{"query":"Why has CAC increased over the last three days?","wait":true}'
```

## Health check (no auth)

```bash
curl -s http://127.0.0.1:8000/readyz
```

## Postman

Same URL/body: set header `x-api-key`, Body -> raw -> JSON. Import quickly via
**Import -> Raw text** and paste any curl above.

## Full debug trace (every event / step / process)

There is **no `trace=true` request flag** — tracing is always on. Two layers:

- **`trace.steps`** (in the mission response) — per-step agent record: every
  tool call with its args, every tool return, retries, and model text. Present
  even when the mission fails with `EXECUTION_LIMIT_EXCEEDED`, so you can see
  exactly which calls blew the tool-call budget. No flag needed.
- **`trace.events`** — mission-lifecycle timeline. Families: `mission`,
  `decomposition`, `task`, `artifact`, `leadership`, `claim`, `skeptic`,
  `remediation`.

Example `trace.steps` entry:

```json
{ "seq": 3, "kind": "tool_call", "tool": "catalogue_search_metrics",
  "args": {"q": "gross sales"} }
```

### Deepest layer: OTel / LangSmith spans

For full LLM/tool spans (token usage, latency, prompts) in a UI, enable
tracing — PydanticAI agents are auto-instrumented once an OTel provider is
configured:

- **OTLP / Langfuse:** set `OTEL_ENABLED=true` + `OTEL_EXPORTER_OTLP_ENDPOINT`
  (or `LANGFUSE_OTEL_ENDPOINT` + headers).
- **LangSmith:** set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`
  (+ `LANGSMITH_PROJECT`). `langsmith_run_url` then populates in the response.

### 1. Full trace timeline (metadata + all events)

```bash
curl -s http://127.0.0.1:8000/v1/missions/MISSION_ID/trace \
  -H "x-api-key: YOUR_API_KEY"
```

Returns `{mission_id, status, trace: {request_id, session_id, langsmith...}, events: [...]}`.

### 2. Raw event stream (paginated, filter by family)

```bash
# all events, max page size
curl -s "http://127.0.0.1:8000/v1/missions/MISSION_ID/events?limit=1000" \
  -H "x-api-key: YOUR_API_KEY"

# only one family (e.g. task steps)
curl -s "http://127.0.0.1:8000/v1/missions/MISSION_ID/events?family=task&limit=1000" \
  -H "x-api-key: YOUR_API_KEY"
```

Paginate with `after_seq=<next_after_seq>` from the previous response until
`has_more` is false.

### 3. Inline with a sync run

`wait:true` responses already embed `trace.events` in the mission body — no
second call needed:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/missions \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{"query":"Why has CAC increased over the last three days?","wait":true}'
```

### 4. Server-side debug logs

For internal step-by-step process logs (LLM calls, tool dispatch, retries),
raise the log level on the server, not the request:

```bash
LOG_LEVEL=DEBUG uvicorn seleric_swarm.main:app --port 8000
```
