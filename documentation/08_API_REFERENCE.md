# 9. HTTP API reference (generated)

Generated from the FastAPI decorators in `main.py` and `api/*.py`, `voice/token.py` on 2026-11-11. Router prefixes are applied. Every `/v1` route sits behind `ApiSecurityMiddleware`.

| Method | Path | Handler | Source |
|---|---|---|---|
| GET | `/` | `root`  | `main.py` |
| GET | `/health` | `health`  | `main.py` |
| GET | `/ready` | `readyz` — Dependency readiness — 200 when ready, 503 when not. | `main.py` |
| GET | `/readyz` | `readyz` — Dependency readiness — 200 when ready, 503 when not. | `main.py` |
| GET | `/ui/config.js` | `office_ui_config`  | `main.py` |
| GET | `/v1/admin/capabilities` | `backend_capabilities`  | `api/phase7.py` |
| GET | `/v1/admin/runs/{run_id}/diagnostics` | `run_diagnostics`  | `api/phase7.py` |
| POST | `/v1/admin/runs/{run_id}/replay` | `replay_metadata`  | `api/phase7.py` |
| DELETE | `/v1/attachments/{attachment_id}` | `delete_attachment`  | `api/conversations.py` |
| GET | `/v1/attachments/{attachment_id}` | `get_attachment`  | `api/conversations.py` |
| POST | `/v1/attachments/{attachment_id}/complete` | `complete_presigned_upload`  | `api/conversations.py` |
| PUT | `/v1/attachments/{attachment_id}/content` | `upload_attachment_content`  | `api/conversations.py` |
| GET | `/v1/attachments/{attachment_id}/download` | `download_attachment`  | `api/conversations.py` |
| GET | `/v1/attachments/{attachment_id}/presigned-download` | `presigned_download`  | `api/conversations.py` |
| POST | `/v1/business-state` | `business_state`  | `api/business_state.py` |
| GET | `/v1/emails/{draft_id}` | `email_status`  | `api/emails.py` |
| POST | `/v1/emails/{draft_id}/approve` | `approve_and_send`  | `api/emails.py` |
| POST | `/v1/llm/ping` | `llm_ping`  | `main.py` |
| GET | `/v1/memories` | `list_memories`  | `api/conversations.py` |
| POST | `/v1/memories` | `create_memory`  | `api/conversations.py` |
| GET | `/v1/memories/preference` | `get_memory_preference`  | `api/conversations.py` |
| PUT | `/v1/memories/preference` | `set_memory_preference`  | `api/conversations.py` |
| DELETE | `/v1/memories/{memory_id}` | `delete_memory`  | `api/conversations.py` |
| GET | `/v1/memories/{memory_id}` | `get_memory`  | `api/conversations.py` |
| PATCH | `/v1/memories/{memory_id}` | `update_memory`  | `api/conversations.py` |
| POST | `/v1/memories/{memory_id}/archive` | `archive_memory`  | `api/conversations.py` |
| POST | `/v1/memories/{memory_id}/confirm` | `confirm_memory`  | `api/conversations.py` |
| POST | `/v1/memories/{memory_id}/move` | `move_memory`  | `api/conversations.py` |
| POST | `/v1/memories/{memory_id}/pin` | `pin_memory`  | `api/conversations.py` |
| GET | `/v1/metrics/definitions` | `list_metric_definitions`  | `api/conversations.py` |
| POST | `/v1/missions` | `create_mission`  | `main.py` |
| GET | `/v1/missions/{mission_id}` | `get_mission`  | `main.py` |
| POST | `/v1/missions/{mission_id}/cancel` | `cancel_mission` — Cancel a running async mission (cooperative / best-effort). | `main.py` |
| GET | `/v1/missions/{mission_id}/events` | `get_mission_events` — Return structured control-plane events for a persisted mission. | `main.py` |
| GET | `/v1/missions/{mission_id}/trace` | `get_mission_trace` — Return trace metadata (request/session/langfuse ids) plus the full event timeline. | `main.py` |
| GET | `/v1/monitoring/catalogue/{domain}` | `catalogue`  | `api/monitoring.py` |
| GET | `/v1/monitoring/config` | `config`  | `api/monitoring.py` |
| DELETE | `/v1/monitoring/config/{domain}` | `reset_config`  | `api/monitoring.py` |
| PUT | `/v1/monitoring/config/{domain}` | `save_config`  | `api/monitoring.py` |
| POST | `/v1/monitoring/refresh/{domain}` | `refresh`  | `api/monitoring.py` |
| GET | `/v1/monitoring/state` | `state`  | `api/monitoring.py` |
| GET | `/v1/monitoring/targets` | `targets` — Findings and latest evaluations from the ``monitor`` schema (Postgres), written hourly by the monitoring runne | `api/monitoring.py` |
| GET | `/v1/office/chart-types` | `office_chart_types` — The chart vocabulary the backend can emit, for the UI to render against. | `api/office/gateway.py` |
| GET | `/v1/office/missions` | `list_office_missions` — Recent missions. Prefers the store's own listing (durable, multi-worker); | `api/office/gateway.py` |
| GET | `/v1/office/missions/{mission_id}/snapshot` | `office_snapshot`  | `api/office/gateway.py` |
| GET | `/v1/office/missions/{mission_id}/stream` | `office_stream`  | `api/office/gateway.py` |
| POST | `/v1/query/enhance` | `enhance_query` — Fast rewrite of an ambiguous draft into a system-ready analytics question. | `api/conversations.py` |
| POST | `/v1/runs/{run_id}/cancel` | `cancel_run`  | `api/conversations.py` |
| GET | `/v1/runs/{run_id}/events` | `list_run_events`  | `api/conversations.py` |
| GET | `/v1/runs/{run_id}/events/stream` | `stream_run_events`  | `api/conversations.py` |
| GET | `/v1/runs/{run_id}/memories` | `list_run_memories`  | `api/conversations.py` |
| GET | `/v1/search` | `search`  | `api/phase7.py` |
| GET | `/v1/threads` | `list_threads`  | `api/conversations.py` |
| POST | `/v1/threads` | `create_thread`  | `api/conversations.py` |
| GET | `/v1/threads/paginated` | `list_threads_paginated`  | `api/conversations.py` |
| DELETE | `/v1/threads/{thread_id}` | `delete_thread`  | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}` | `get_thread`  | `api/conversations.py` |
| PATCH | `/v1/threads/{thread_id}` | `update_thread`  | `api/conversations.py` |
| POST | `/v1/threads/{thread_id}/archive` | `archive_thread`  | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}/attachments` | `list_attachments`  | `api/conversations.py` |
| POST | `/v1/threads/{thread_id}/attachments` | `initiate_attachment`  | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}/events` | `list_thread_events`  | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}/events/stream` | `stream_thread_events`  | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}/feedback` | `list_thread_feedback` — All reader verdicts on one thread — training-data export surface. | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}/messages` | `list_messages`  | `api/conversations.py` |
| POST | `/v1/threads/{thread_id}/messages` | `submit_message`  | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}/messages/paginated` | `list_messages_paginated`  | `api/conversations.py` |
| GET | `/v1/threads/{thread_id}/messages/{message_id}/feedback` | `get_message_feedback`  | `api/conversations.py` |
| POST | `/v1/threads/{thread_id}/messages/{message_id}/feedback` | `submit_message_feedback`  | `api/conversations.py` |
| POST | `/v1/threads/{thread_id}/restore` | `restore_thread`  | `api/conversations.py` |
| GET | `/v1/voice/dev` | `voice_dev_page`  | `voice/dev_page.py` |
| POST | `/v1/voice/token` | `mint_voice_token`  | `voice/token.py` |