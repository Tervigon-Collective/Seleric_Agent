from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime config comes from the environment (or .env). No secrets/URLs in code."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "local"
    log_level: str = "INFO"

    persistence_backend: Literal["memory", "postgres", "file"] = "memory"
    persistence_path: str = ".data/persistence"
    database_url: str = ""
    checkpoint_backend: Literal["none", "memory", "postgres"] = "none"
    cancellation_backend: Literal["memory", "redis"] = "memory"
    event_notifier_backend: Literal["auto", "memory", "redis"] = "auto"
    redis_url: str = ""
    run_worker_id: str = ""
    run_lease_s: float = 60.0
    run_heartbeat_s: float = 15.0
    run_max_attempts: int = 3
    run_retry_delay_s: float = 5.0
    run_retry_jitter_s: float = 1.0
    shutdown_timeout_s: float = 10.0
    blob_backend: Literal["local", "minio"] = "local"
    blob_local_path: str = ".data/attachments"
    attachment_max_size_bytes: int = 25 * 1024 * 1024
    attachment_allowed_mime_types: str = (
        "application/json,application/pdf,text/csv,text/plain,image/jpeg,image/png,image/webp"
    )
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "seleric-attachments"
    minio_secure: bool = True
    malware_scanner_backend: Literal["unavailable", "clamav"] = "unavailable"
    clamav_host: str = ""
    clamav_port: int = 3310
    clamav_timeout_s: float = 5.0
    readiness_timeout_s: float = 2.0

    llm_provider: Literal["fake", "azure_openai_compatible"] = "azure_openai_compatible"
    llm_timeout_s: float = 30.0
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024
    llm_max_retries: int = 2
    llm_fallback_model: str | None = None
    # Comma-separated additional model ids, tried in order after the primary
    # (azure_openai_model) and after llm_fallback_model, when the current one
    # is unhealthy (circuit open) or exhausts its own retries. Empty = no
    # gateway wrapping, single-model behavior unchanged.
    llm_fallback_models: str = ""

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_model: str = ""
    # Preferred way to list models for the LLM gateway to route across (all
    # must be deployed on the same AZURE_OPENAI_ENDPOINT): a JSON array
    # (AZURE_OPENAI_MODELS=["DeepSeek-V4-Flash","gpt-5-mini"]) or a plain
    # comma-separated string (AZURE_OPENAI_MODELS=DeepSeek-V4-Flash,gpt-5-mini).
    # First entry is primary, the rest are fallbacks tried in order.
    azure_openai_models: str = ""
    # Legacy numbered fallback (still supported, used only when
    # azure_openai_models is unset): model1 is primary, model2 is fallback.
    azure_openai_model1: str = ""
    azure_openai_model2: str = ""
    # Optional faster/cheaper deployment used for simple read-only intents
    # (lookup/aggregation/trend) — see resolve_v3_model(prefer_fast=...). Must
    # be deployed on AZURE_OPENAI_ENDPOINT. Empty (default) = model tiering off,
    # every mission uses the normal model chain.
    azure_openai_fast_model: str = ""
    azure_openai_api_version: str = "2024-05-01-preview"
    # "openai_compatible" -> Azure AI Inference; "azure" -> classic Azure OpenAI.
    azure_auth_style: Literal["openai_compatible", "azure"] = "openai_compatible"
    azure_key_vault_url: str | None = None

    # Optional second Azure resource (distinct endpoint + key + quota), tried
    # after every model on the primary resource is exhausted. Unlike
    # AZURE_OPENAI_MODELS (multiple deployments on one resource, sharing one
    # quota), this survives an endpoint-level rate limit. Same auth style as
    # the primary resource. Empty (default) = no second resource, unchanged
    # single-resource fallback behavior.
    azure_openai_endpoint_2: str = ""
    azure_openai_api_key_2: str = ""
    azure_openai_models_2: str = ""

    # Optional independent-provider fallback tier (OpenRouter). Appended after
    # every Azure model in resolve_v3_model's FallbackModel chain, so an
    # Azure-wide 429 (both resources throttled) falls through to a genuinely
    # separate provider pool. OpenAI-compatible; no api-version. Empty (default)
    # = no OpenRouter tier, unchanged Azure-only behavior.
    openrouter_api_key: str = ""
    openrouter_models: str = ""  # JSON array or comma-separated; first = highest priority
    openrouter_endpoint: str = "https://openrouter.ai/api/v1"

    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = ""
    langsmith_workspace_id: str = ""
    langsmith_org: str = ""
    langsmith_endpoint: str = ""
    otel_enabled: bool = False
    otel_service_name: str = "seleric-swarm"
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_headers: str = ""
    otel_trace_sample_ratio: float = 1.0
    langfuse_otel_endpoint: str = ""
    langfuse_otel_headers: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_project_id: str = ""
    search_embedding_model: str = ""

    mcp_config_path: str = "config/mcp_servers.yaml"
    seleric_mcp_url: str = ""
    seleric_mcp_token: str = ""
    # Catalogue LTM (long-term, cross-mission) semantic index — replaces the
    # remote catalogue_search_metrics/catalogue_resolve_term round trips in
    # toolsets/semantic.py::search_semantics with a local Qdrant search kept
    # in sync via scripts/sync_catalogue_to_qdrant.py. Deliberately a
    # separate embedding model from search_embedding_model (conversation
    # search) — different vector space, different purpose.
    qdrant_url: str = Field(default="", validation_alias=AliasChoices("qdrant_url", "QDRANT_URL", "QDRANT_ENDPOINT"))
    qdrant_api_key: str = ""
    qdrant_collection: str = "seleric_catalogue"
    qdrant_embedding_model: str = "text-embedding-3-small"
    # When True, render the whole live catalogue (~8.6k tokens) into every
    # mission prompt. Default False: the agent resolves via search_semantics
    # (glossary-backed) + get_metric_definitions instead of paying the dump on
    # every model turn. Flip on to revert to the in-prompt catalogue.
    catalogue_in_prompt: bool = False
    jev_base_url: str = ""
    jev_api_key: str = ""
    jev_timeout_s: float = 1.0
    metric_registry_path: str = "config/metric_registry.yaml"

    a2a_public_base_url: str = ""
    api_host: str = ""
    api_port: int = 0
    # inprocess = local handlers only; http = remote A2A only; hybrid = local then HTTP fallback
    a2a_transport: Literal["inprocess", "http", "hybrid"] = "inprocess"
    a2a_timeout_s: float = 30.0

    mission_timeout_s: float = 600.0
    # Bound on the pre-loop catalogue_resolve_values call (agent/runner.py).
    # Fail-open: a slow or warming value index just means no value hints.
    value_resolve_timeout_s: float = 6.0
    max_llm_calls: int = 6
    # Ceiling for the V3 loop. Per-intent budgets in agent/runner.py sit under
    # this; unknown intent uses the ceiling itself.
    max_tool_calls: int = 160
    # PydanticAI per-run retries for the V3 agent (tool ModelRetry / output
    # validation recovery). See ExecutionLimits.agent_retries.
    agent_retries: int = 2
    completion_threshold: float = 0.90

    allow_write_actions: bool = False

    # V3 refactor (docs/refactor/) — when True, conversations and
    # POST /v1/missions run Agent[SelericDeps, MissionResult] instead of
    # swarm_v2. Sprint 5: V3 is now the only mission path, default True.
    v3_agent_enabled: bool = True

    workflow_version: str = "1.0.0"

    # API security (v1.13)
    # When set, all non-probe routes require X-API-Key or Authorization: Bearer.
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("api_key", "API_KEY", "SELERIC_API_KEY"),
    )
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60
    # Compatibility identity used until a first-class identity provider is configured.
    default_workspace_id: str = "default"
    default_user_id: str = "default"
    # Only enable behind a proxy that overwrites (rather than appends user-supplied)
    # forwarding headers.
    trust_x_forwarded_for: bool = False
    trust_identity_headers: bool = False

    # Voice agent (LiveKit) — docs/features/voice-agent/. Off by default; the
    # token route and the voice worker are both inert until this is true.
    voice_enabled: bool = False
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    # Room JWT lifetime. Short by design — the browser re-mints on reconnect.
    voice_token_ttl_s: int = 900
    # fake = no vendor calls (CI default, mirrors llm_provider="fake").
    stt_provider: str = "fake"
    tts_provider: str = "fake"
    voice_llm_model: str = ""
    voice_tts_voice: str = ""
    voice_narration_enabled: bool = True
    voice_narration_min_gap_s: float = 4.0
    voice_narration_idle_hold_s: float = 20.0
    # Voice gives up before the mission's own mission_timeout_s bound.
    voice_mission_timeout_s: float = 180.0
    voice_recording_enabled: bool = False
    voice_summary_mode: Literal["deterministic", "llm"] = "deterministic"
    # The voice worker's base URL for the Seleric API it calls back into.
    # Accepts SELERIC_API_BASE_URL too: that is the name docker-compose.yml's
    # voice service historically set, and a silent alias mismatch here means
    # the worker falls back to localhost inside its own container and can
    # never reach the api service.
    seleric_api_url: str = Field(
        default="http://localhost:8000",
        validation_alias=AliasChoices(
            "seleric_api_url", "SELERIC_API_URL", "SELERIC_API_BASE_URL"
        ),
    )

    def missing_voice_credentials(self) -> list[str]:
        """LiveKit settings that voice needs but does not have.

        Deliberately not a model validator: Settings() is constructed by every
        entry point (migrate, recover, the test conftest), and a voice
        misconfiguration must not stop unrelated processes from booting. Checked
        in validate_for_startup() and at the voice route/worker boundary.
        """

        if not self.voice_enabled:
            return []
        return [
            name
            for name, value in (
                ("LIVEKIT_URL", self.livekit_url),
                ("LIVEKIT_API_KEY", self.livekit_api_key),
                ("LIVEKIT_API_SECRET", self.livekit_api_secret),
            )
            if not value.strip()
        ]

    @field_validator("llm_fallback_model", "azure_key_vault_url", mode="before")
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator(
        "langsmith_project",
        "langsmith_endpoint",
        "otel_exporter_otlp_endpoint",
        "langfuse_otel_endpoint",
        "langfuse_base_url",
        "langfuse_project_id",
        "search_embedding_model",
        "azure_openai_endpoint",
        "azure_openai_model",
        "azure_openai_models",
        "azure_openai_model1",
        "azure_openai_model2",
        "azure_openai_endpoint_2",
        "azure_openai_models_2",
        "openrouter_models",
        "openrouter_endpoint",
        "seleric_mcp_url",
        "qdrant_url",
        "qdrant_collection",
        "qdrant_embedding_model",
        "jev_base_url",
        "a2a_public_base_url",
        "api_host",
        "redis_url",
        "run_worker_id",
        "minio_endpoint",
        "minio_access_key",
        "minio_secret_key",
        "clamav_host",
        mode="before",
    )
    @classmethod
    def strip_wrapping_quotes(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().strip('"').strip("'")
        return value

    @field_validator(
        "azure_openai_api_key",
        "azure_openai_api_key_2",
        "openrouter_api_key",
        "langsmith_api_key",
        "api_key",
        "seleric_mcp_token",
        "jev_api_key",
        "qdrant_api_key",
    )
    @classmethod
    def no_placeholder_secrets(cls, value: str) -> str:
        if value.strip().lower() in {"replace_me", "changeme", "todo"}:
            return ""
        return value

    @field_validator("attachment_max_size_bytes")
    @classmethod
    def positive_attachment_limit(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("attachment_max_size_bytes must be positive")
        return value

    @field_validator("minio_bucket")
    @classmethod
    def valid_minio_bucket(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("minio_bucket must not be blank")
        return value

    @model_validator(mode="after")
    def valid_runtime_limits(self) -> Settings:
        positive = {
            "run_lease_s": self.run_lease_s,
            "run_heartbeat_s": self.run_heartbeat_s,
            "llm_timeout_s": self.llm_timeout_s,
            "a2a_timeout_s": self.a2a_timeout_s,
            "mission_timeout_s": self.mission_timeout_s,
            "shutdown_timeout_s": self.shutdown_timeout_s,
            "clamav_timeout_s": self.clamav_timeout_s,
            "readiness_timeout_s": self.readiness_timeout_s,
            "jev_timeout_s": self.jev_timeout_s,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"{', '.join(invalid)} must be positive")
        if self.run_heartbeat_s >= self.run_lease_s:
            raise ValueError("run_heartbeat_s must be less than run_lease_s")
        if self.run_max_attempts < 1:
            raise ValueError("run_max_attempts must be at least 1")
        if self.run_retry_delay_s < 0:
            raise ValueError("run_retry_delay_s must not be negative")
        if self.run_retry_jitter_s < 0:
            raise ValueError("run_retry_jitter_s must not be negative")
        if self.shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be positive")
        if self.llm_max_retries < 0:
            raise ValueError("llm_max_retries must not be negative")
        if not 1 <= self.clamav_port <= 65535:
            raise ValueError("clamav_port must be between 1 and 65535")
        return self

    def is_dev_surface(self) -> bool:
        return self.app_env.lower() in {"local", "development", "dev", "test"}

    def validate_for_startup(self) -> None:
        """Reject unsafe production combinations while preserving local/test defaults."""
        if self.is_dev_surface():
            return
        errors: list[str] = []
        if not self.api_key.strip():
            errors.append("api_key must be configured")
        if self.llm_provider == "fake":
            errors.append("llm_provider=fake is not allowed")
        elif not (
            self.azure_openai_endpoint.strip()
            and self.azure_openai_api_key.strip()
            and self.primary_model()
        ):
            errors.append("Azure LLM endpoint, API key, and model must be configured")
        if self.persistence_backend != "postgres" or not self.database_url.strip():
            errors.append("PostgreSQL persistence and database_url are required")
        if self.checkpoint_backend != "postgres":
            errors.append("checkpoint_backend=postgres is required")
        if self.cancellation_backend != "redis" or not self.redis_url.strip():
            errors.append("Redis cancellation and redis_url are required")
        if self.resolved_event_notifier_backend() != "redis" or not self.redis_url.strip():
            errors.append("Redis event notification is required")
        if self.blob_backend != "minio":
            errors.append("blob_backend=minio is required")
        elif not all(
            value.strip()
            for value in (
                self.minio_endpoint,
                self.minio_access_key,
                self.minio_secret_key,
                self.minio_bucket,
            )
        ):
            errors.append("MinIO endpoint, credentials, and bucket are required")
        if self.malware_scanner_backend != "clamav" or not self.clamav_host.strip():
            errors.append("a ClamAV malware scanner host is required")
        if not self.seleric_mcp_url.strip() or not self.seleric_mcp_token.strip():
            errors.append("Seleric MCP URL and token are required")
        if self.voice_enabled:
            missing_credentials = self.missing_voice_credentials()
            if missing_credentials:
                errors.append(
                    "voice_enabled requires " + ", ".join(missing_credentials)
                )
            # api_key is already required above; voice depends on it, because
            # with no key configured ApiSecurityMiddleware authenticates every
            # caller as the default principal and a room token would be minted
            # for an anonymous user.
            if self.stt_provider.strip().lower() == "fake":
                errors.append("stt_provider=fake is not allowed with voice_enabled")
            if self.tts_provider.strip().lower() == "fake":
                errors.append("tts_provider=fake is not allowed with voice_enabled")
            if self.voice_recording_enabled and not any(
                mime.strip().lower().startswith("audio/")
                for mime in self.attachment_allowed_mime_types.split(",")
            ):
                errors.append(
                    "voice_recording_enabled requires audio/* in "
                    "attachment_allowed_mime_types"
                )
        if errors:
            raise ValueError("Unsafe production settings: " + "; ".join(errors))

    def resolved_event_notifier_backend(self) -> Literal["memory", "redis"]:
        if self.event_notifier_backend != "auto":
            return self.event_notifier_backend
        if not self.is_dev_surface() and self.persistence_backend == "postgres" and self.redis_url:
            return "redis"
        return "memory"

    def resolved_models(self) -> list[str]:
        """Ordered model ids for the LLM gateway: primary first, then
        fallbacks. Reads azure_openai_models (JSON array or comma-separated
        string) if set; otherwise falls back to the legacy
        AZURE_OPENAI_MODEL1/MODEL2 pair, then the singular AZURE_OPENAI_MODEL.
        """
        raw = (self.azure_openai_models or "").strip()
        if raw:
            ids: list[str] = []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    ids = [str(m).strip() for m in parsed if str(m).strip()]
                except (json.JSONDecodeError, TypeError):
                    ids = []
            if not ids:
                ids = [m.strip() for m in raw.split(",") if m.strip()]
            if ids:
                return ids
        ids = [m for m in (self.azure_openai_model1, self.azure_openai_model2) if m]
        if ids:
            return ids
        return [self.azure_openai_model] if self.azure_openai_model else []

    def primary_model(self) -> str:
        models = self.resolved_models()
        return models[0] if models else ""

    def numbered_fallback_models(self) -> list[str]:
        """Every configured model after the primary, in priority order."""
        return self.resolved_models()[1:]


def configured_chat_model(settings: object) -> str:
    """Model id for agent LLM calls.

    Prefer ``primary_model()`` so ``AZURE_OPENAI_MODELS`` / MODEL1+MODEL2 work
    when the legacy singular ``AZURE_OPENAI_MODEL`` is empty.
    """

    getter = getattr(settings, "primary_model", None)
    if callable(getter):
        resolved = getter()
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    return str(getattr(settings, "azure_openai_model", "") or "").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
