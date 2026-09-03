from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration. Secrets come from env or Key Vault, never YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "local"
    log_level: str = "INFO"

    persistence_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = "postgresql+psycopg://seleric:seleric@localhost:5432/seleric_swarm"

    llm_provider: Literal["fake", "azure_openai_compatible"] = "fake"
    llm_timeout_s: float = 30.0
    llm_temperature: float = 0.0
    llm_max_tokens: int = 1024
    llm_max_retries: int = 2
    llm_fallback_model: str | None = None

    azure_openai_endpoint: str = "https://llama4-maverick-prod-resource.services.ai.azure.com"
    azure_openai_api_key: str = ""
    azure_openai_model: str = "Llama-4-Maverick-17B-128E-Instruct-FP8"
    azure_openai_api_version: str = "2024-05-01-preview"
    # "openai_compatible" -> Azure AI Inference (*.services.ai.azure.com, no deployment
    # routing). "azure" -> classic Azure OpenAI deployment resource (*.openai.azure.com).
    azure_auth_style: Literal["openai_compatible", "azure"] = "openai_compatible"
    azure_key_vault_url: str | None = None

    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "seleric-swarm-local"
    langsmith_workspace_id: str = ""
    langsmith_org: str = "default"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    mcp_config_path: str = "config/mcp_servers.yaml"
    metric_registry_path: str = "config/metric_registry.yaml"
    prompt_versions_path: str = "config/prompt_versions.yaml"
    prompts_dir: str = "prompts"
    fixtures_dir: str = "data/fixtures"

    a2a_public_base_url: str = "http://localhost:8000"

    mission_timeout_s: float = 30.0
    max_llm_calls: int = 6
    max_tool_calls: int = 8
    # Coordinator control-plane hard stops for the DECIDE -> EXECUTE cycle.
    max_agent_calls: int = 30
    max_leadership_transfers: int = 6
    max_coordinator_iterations: int = 12
    completion_threshold: float = 0.90

    allow_write_actions: bool = False
    require_skeptic_for_causal: bool = True
    require_provenance_for_numeric: bool = True

    workflow_name: str = "lookup_v1"
    workflow_version: str = "1.0.0"

    @field_validator("llm_fallback_model", "azure_key_vault_url", mode="before")
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("langsmith_project", "azure_openai_endpoint", "azure_openai_model", mode="before")
    @classmethod
    def strip_wrapping_quotes(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().strip('"').strip("'")
        return value

    @field_validator("azure_openai_api_key", "langsmith_api_key")
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
