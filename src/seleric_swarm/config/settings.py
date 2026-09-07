from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
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

    persistence_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = ""

    llm_provider: Literal["fake", "azure_openai_compatible"] = "fake"
    llm_timeout_s: float = 30.0
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024
    llm_max_retries: int = 2
    llm_fallback_model: str | None = None

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_model: str = ""
    azure_openai_api_version: str = "2024-05-01-preview"
    # "openai_compatible" -> Azure AI Inference; "azure" -> classic Azure OpenAI.
    azure_auth_style: Literal["openai_compatible", "azure"] = "openai_compatible"
    azure_key_vault_url: str | None = None

    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = ""
    langsmith_workspace_id: str = ""
    langsmith_org: str = ""
    langsmith_endpoint: str = ""

    mcp_config_path: str = "config/mcp_servers.yaml"
    seleric_mcp_url: str = ""
    seleric_mcp_token: str = ""
    metric_registry_path: str = "config/metric_registry.yaml"
    prompt_versions_path: str = "config/prompt_versions.yaml"
    prompts_dir: str = "prompts"

    a2a_public_base_url: str = ""
    api_host: str = ""
    api_port: int = 0
    # inprocess = local handlers only; http = remote A2A only; hybrid = local then HTTP fallback
    a2a_transport: Literal["inprocess", "http", "hybrid"] = "inprocess"
    a2a_timeout_s: float = 30.0

    mission_timeout_s: float = 120.0
    max_llm_calls: int = 6
    max_tool_calls: int = 8
    max_agent_calls: int = 30
    max_leadership_transfers: int = 6
    max_coordinator_iterations: int = 12
    completion_threshold: float = 0.90

    allow_write_actions: bool = False
    require_skeptic_for_causal: bool = True
    require_provenance_for_numeric: bool = True

    workflow_name: str = "lookup_v1"
    workflow_version: str = "1.0.0"
    # Swarm mission control plane (Coordinator V1). Only "swarm_v2" exists today —
    # the legacy "swarm_v1" imperative workflow was removed.
    swarm_workflow: Literal["swarm_v2"] = "swarm_v2"
    coordinator_policies_path: str = "config/coordinator_policies.yaml"
    max_remediation_rounds: int = 3

    # API security (v1.13)
    # When set, all non-probe routes require X-API-Key or Authorization: Bearer.
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("api_key", "API_KEY", "SELERIC_API_KEY"),
    )
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60

    @field_validator("llm_fallback_model", "azure_key_vault_url", mode="before")
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator(
        "langsmith_project",
        "langsmith_endpoint",
        "azure_openai_endpoint",
        "azure_openai_model",
        "seleric_mcp_url",
        "a2a_public_base_url",
        "api_host",
        mode="before",
    )
    @classmethod
    def strip_wrapping_quotes(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().strip('"').strip("'")
        return value

    @field_validator(
        "azure_openai_api_key",
        "langsmith_api_key",
        "api_key",
        "seleric_mcp_token",
    )
    @classmethod
    def no_placeholder_secrets(cls, value: str) -> str:
        if value.strip().lower() in {"replace_me", "changeme", "todo"}:
            return ""
        return value

    def is_dev_surface(self) -> bool:
        return self.app_env.lower() in {"local", "development", "dev", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
