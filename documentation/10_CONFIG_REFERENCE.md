# 11. Configuration reference

## 11.1 Settings (generated)

Generated from `config/settings.py` (class `Settings`, pydantic-settings; values come from the environment or `.env`, case-insensitive) on 2026-11-11. A field `foo_bar` is read from the variable `FOO_BAR` unless an alias is shown.

| Setting | Type | Default |
|---|---|---|
| `app_env` | `str` | `'local'` |
| `log_level` | `str` | `'INFO'` |
| `persistence_backend` | `Literal['memory', 'postgres', 'file']` | `'memory'` |
| `persistence_path` | `str` | `'.data/persistence'` |
| `database_url` | `str` | `''` |
| `cancellation_backend` | `Literal['memory', 'redis']` | `'memory'` |
| `event_notifier_backend` | `Literal['auto', 'memory', 'redis']` | `'auto'` |
| `redis_url` | `str` | `''` |
| `run_worker_id` | `str` | `''` |
| `run_lease_s` | `float` | `60.0` |
| `run_heartbeat_s` | `float` | `15.0` |
| `run_max_attempts` | `int` | `3` |
| `run_max_concurrency` | `int` | `4` |
| `run_retry_delay_s` | `float` | `5.0` |
| `run_retry_jitter_s` | `float` | `1.0` |
| `run_drain_timeout_s` | `float` | `480.0` |
| `shutdown_timeout_s` | `float` | `10.0` |
| `blob_backend` | `Literal['local', 'minio']` | `'local'` |
| `blob_local_path` | `str` | `'.data/attachments'` |
| `attachment_max_size_bytes` | `int` | `25 * 1024 * 1024` |
| `attachment_allowed_mime_types` | `str` | `'application/json,application/pdf,text/csv,text/plain,image/jpeg,image` |
| `minio_endpoint` | `str` | `''` |
| `minio_access_key` | `str` | `''` |
| `minio_secret_key` | `str` | `''` |
| `minio_bucket` | `str` | `'seleric-attachments'` |
| `minio_secure` | `bool` | `True` |
| `malware_scanner_backend` | `Literal['unavailable', 'clamav']` | `'unavailable'` |
| `clamav_host` | `str` | `''` |
| `clamav_port` | `int` | `3310` |
| `clamav_timeout_s` | `float` | `5.0` |
| `readiness_timeout_s` | `float` | `2.0` |
| `llm_provider` | `Literal['fake', 'azure_openai_compatible']` | `'azure_openai_compatible'` |
| `llm_timeout_s` | `float` | `30.0` |
| `llm_temperature` | `float` | `0.0` |
| `llm_max_tokens` | `int` | `1024` |
| `llm_max_retries` | `int` | `2` |
| `llm_fallback_model` | `str or None` | `None` |
| `llm_fallback_models` | `str` | `''` |
| `azure_openai_endpoint` | `str` | `''` |
| `azure_openai_api_key` | `str` | `''` |
| `azure_openai_model` | `str` | `''` |
| `azure_openai_models` | `str` | `''` |
| `azure_openai_model1` | `str` | `''` |
| `azure_openai_model2` | `str` | `''` |
| `azure_openai_fast_model` | `str` | `''` |
| `azure_openai_planner_model` | `str` | `''` |
| `azure_openai_planner_reasoning_effort` | `str` | `''` |
| `azure_openai_planner_diagnose_reasoning_effort` | `str` | `''` |
| `azure_openai_tool_model` | `str` | `''` |
| `azure_openai_tool_temperature` | `float or None` | `None` |
| `azure_openai_answer_model` | `str` | `''` |
| `azure_openai_answer_reasoning_effort` | `str` | `''` |
| `azure_openai_answer_diagnosis_reasoning_effort` | `str` | `''` |
| `azure_openai_helper_model` | `str` | `''` |
| `azure_openai_api_version` | `str` | `'2024-05-01-preview'` |
| `azure_auth_style` | `Literal['openai_compatible', 'azure']` | `'openai_compatible'` |
| `azure_key_vault_url` | `str or None` | `None` |
| `langfuse_tracing` | `bool` | `True` |
| `langfuse_public_key` | `str` | `''` |
| `langfuse_secret_key` | `str` | `''` |
| `langfuse_base_url` | `str` | `'https://cloud.langfuse.com' (alias)` |
| `langfuse_project_id` | `str` | `''` |
| `langfuse_otel_endpoint` | `str` | `''` |
| `langfuse_otel_headers` | `str` | `''` |
| `langsmith_tracing` | `bool` | `False` |
| `langsmith_api_key` | `str` | `''` |
| `langsmith_project` | `str` | `''` |
| `langsmith_workspace_id` | `str` | `''` |
| `langsmith_org` | `str` | `''` |
| `langsmith_endpoint` | `str` | `''` |
| `otel_enabled` | `bool` | `False` |
| `otel_service_name` | `str` | `'seleric-swarm'` |
| `otel_exporter_otlp_endpoint` | `str` | `''` |
| `otel_exporter_otlp_headers` | `str` | `''` |
| `otel_trace_sample_ratio` | `float` | `1.0` |
| `search_embedding_model` | `str` | `''` |
| `mcp_config_path` | `str` | `'config/mcp_servers.yaml'` |
| `seleric_mcp_url` | `str` | `''` |
| `azure_tenant_id` | `str` | `''` |
| `azure_client_id` | `str` | `''` |
| `azure_client_secret` | `str` | `''` |
| `email_sender` | `str` | `''` |
| `email_contacts_path` | `str` | `'config/email_contacts.yaml'` |
| `email_allowed_domains` | `str` | `'seleric.ai'` |
| `seleric_mcp_token` | `str` | `''` |
| `chronos_base_url` | `str` | `''` |
| `qdrant_url` | `str` | `'' (alias)` |
| `qdrant_api_key` | `str` | `''` |
| `qdrant_collection` | `str` | `'seleric_catalogue'` |
| `qdrant_embedding_model` | `str` | `'text-embedding-3-small'` |
| `catalogue_in_prompt` | `bool` | `False` |
| `a2a_public_base_url` | `str` | `''` |
| `api_host` | `str` | `''` |
| `api_port` | `int` | `0` |
| `a2a_transport` | `Literal['inprocess', 'http', 'hybrid']` | `'inprocess'` |
| `a2a_timeout_s` | `float` | `30.0` |
| `mission_timeout_s` | `float` | `600.0` |
| `value_resolve_timeout_s` | `float` | `15.0` |
| `max_llm_calls` | `int` | `6` |
| `max_tool_calls` | `int` | `300` |
| `mcp_call_timeout_s` | `float` | `15.0` |
| `max_semantic_searches` | `int` | `5` |
| `max_definition_lookups` | `int` | `8` |
| `agent_retries` | `int` | `2` |
| `completion_threshold` | `float` | `0.9` |
| `v3_agent_enabled` | `bool` | `True` |
| `semantic_port_mode` | `Literal['off', 'shadow', 'enforce']` | `'off' (alias)` |
| `default_brand_id` | `str` | `'' (alias)` |
| `scope_dimension_ids` | `str` | `'' (alias)` |
| `workflow_version` | `str` | `'1.0.0'` |
| `api_key` | `str` | `'' (alias)` |
| `rate_limit_enabled` | `bool` | `False` |
| `rate_limit_per_minute` | `int` | `60` |
| `default_workspace_id` | `str` | `'default'` |
| `default_user_id` | `str` | `'default'` |
| `trust_x_forwarded_for` | `bool` | `False` |
| `trust_identity_headers` | `bool` | `False` |
| `voice_enabled` | `bool` | `False` |
| `livekit_url` | `str` | `''` |
| `livekit_api_key` | `str` | `''` |
| `livekit_api_secret` | `str` | `''` |
| `voice_token_ttl_s` | `int` | `900` |
| `stt_provider` | `str` | `'fake'` |
| `voice_stt_language` | `str` | `'multi'` |
| `azure_speech_key` | `str` | `''` |
| `azure_speech_region` | `str` | `''` |
| `voice_stt_languages` | `str` | `'en-IN,hi-IN'` |
| `voice_llm_provider` | `str` | `''` |
| `tts_provider` | `str` | `'fake'` |
| `voice_llm_model` | `str` | `''` |
| `voice_llm_base_url` | `str` | `''` |
| `voice_llm_api_key` | `str` | `''` |
| `voice_tts_voice` | `str` | `''` |
| `voice_narration_enabled` | `bool` | `True` |
| `voice_narration_min_gap_s` | `float` | `4.0` |
| `voice_narration_idle_hold_s` | `float` | `20.0` |
| `voice_mission_timeout_s` | `float` | `600.0` |
| `voice_recording_enabled` | `bool` | `False` |
| `voice_summary_mode` | `Literal['deterministic', 'llm']` | `'deterministic'` |
| `seleric_api_url` | `str` | `'http://localhost:8000' (alias)` |

## 11.2 Variables read directly with `os.getenv` (outside `Settings`)

| Variable | Default | Where | Meaning |
|----------|---------|-------|---------|
| `AGENT_MAX_STEPS` | 10 | `agent/loop.py` | Model steps per turn (correction pass excluded) |
| `AGENT_MAX_TOOL_CALLS` | 20 | `agent/loop.py` | Tool calls per turn |
| `AGENT_TURN_BUDGET_S` | 150 | `agent/loop.py` | Soft wall clock; tools withdrawn after it |
| `AGENT_VERIFY_NUMBERS` | 1 | `agent/loop.py` | Figure verification on |
| `AGENT_STREAM_ANSWER` | 1 | `agent/loop.py` | Stream answer tokens |
| `AGENT_LLM_TIMEOUT_S` | 45 (deployed 240) | `agent/model.py` | HTTP read timeout of the model client |
| `AGENT_MAX_TOKENS` | 2000 | `agent/model.py` | Completion cap |
| `AGENT_REASONING_BUDGET` | 6000 | `agent/model.py` | Extra completion headroom for reasoning models |
| `AGENT_REASONING_EFFORT` | low | `agent/model.py` | Reasoning effort |
| `AGENT_ESCALATED_EFFORT` | medium | `agent/model.py` | Effort after `escalate` |
| `AGENT_WRITEUP_EFFORT` | escalated effort (deployed low) | `agent/model.py` | Effort after a causal helper returned |
| `AGENT_LLM_WAIT_BUDGET_S` | 60 (deployed 90) | `agent/model_health.py` | Longest wait for a model |
| `AGENT_LLM_REQUEST_CAP_S` | 60 | `agent/model_health.py` | Per-request cap |
| `AGENT_LLM_STALL_CAP_S` | 30 | `agent/model_health.py` | Stall detection |
| `AGENT_LLM_429_COOLDOWN_MAX_S` | 600 | `agent/model_health.py` | Longest cooldown after a 429 |
| `AGENT_MAX_ROWS` | 60 | `agent/mcp_toolset.py` | List cap inside a tool result |
| `AGENT_MAX_RESULT_CHARS` | 14000 | `agent/mcp_toolset.py` | Result size cap |
| `AGENT_TOOL_TIMEOUT_S` | 45 | `agent/mcp_toolset.py` | Per-tool timeout |
| `AGENT_QUERY_ROW_LIMIT` | 100000 | `agent/mcp_toolset.py` | Row limit sent with query tools |
| `AGENT_MAX_DATASET_EVIDENCE` | 5000 | `agent/mcp_toolset.py` | Evidence points per dataset |
| `SELERIC_SANDBOX_ENABLED` | 1 | `toolsets/sandbox.py` | Enable `run_python` |
| `SELERIC_SANDBOX_TIMEOUT_S` | built-in default | `toolsets/sandbox.py` | Script timeout |
| `SELERIC_MCP_TIMEOUT` | empty | MCP gateway | Gateway call timeout override |
| `SELERIC_REPORTS_DIR` | empty | forecasting | Reports directory |
| `SELERIC_DOMAIN_HEALTH_DIR` | `var/domain_health_snapshots` | domain health | Snapshot directory |
| `BUSINESS_STATE_REFRESH_ENABLED` | 1 (deployed 0 in the API) | business state | In-process refresh |
| `BUSINESS_STATE_REFRESH_INTERVAL_S` | 3600 | business state | Refresh interval |
| `BUSINESS_STATE_MIN_INTERVAL_S` | built-in | business state | Minimum interval |

## 11.3 YAML configuration (`config/`)

| File | Read by | Content |
|------|---------|---------|
| `mcp_servers.yaml` | `protocols/mcp/gateway.py` | MCP servers: `seleric` (streamable HTTP, `SELERIC_MCP_URL`, `SELERIC_MCP_TOKEN`) |
| `scope.yaml` | `exploration/space.py` and scope resolution | Default brand `"20"`; scope dimension ids (`brand_id`, `brand`, `brand_name`) used when the catalogue snapshot declares none |
| `monitoring.yaml` | `services/domain_health/monitor_config.py`, `main.py` | Trust-gate rules (bulk-return and spend-collapse thresholds) and pooling window |
| `domain_health_profiles.yaml` | `catalogue_bootstrap.py`, `domain_health/resolver.py` | Per domain metric selection and thresholds shipped as defaults; user overrides are stored beside the snapshots |
| `business_state_profiles.yaml` | `business_state/features.py`, `profiles.py` | Feature strategies |
| `forecast_policies.yaml`, `forecast_features.yaml` | `forecasting/policies.py`, `features.py` | Eligibility and feature bundles |
| `prediction_policies.yaml`, `skeptic_policies.yaml` | `toolsets/policy_config.py`, `forecasting/engines.py`, `models/service.py` | Forecast budgets, horizons, router fallback order; skeptic checks |
| `calendar_in.yaml`, `data_incidents.yaml` | `forecasting/calendar.py`, `quality.py` | Calendar effects; masked data incidents |
| `model_registry.yaml` (example `model_registry.example.yaml`) | `forecasting/pipeline.py`, `models/service.py`, `evaluation.py` | Approved forecast models |
| `email_contacts.yaml` | `mail/session.py`, `config/settings.py` | Trusted contact names and groups |
| `diagnostic_policies.yaml`, `experiment_registry.yaml` | not referenced by any Python file in `src/` or `scripts/` | |
