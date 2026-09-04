from __future__ import annotations

import os

from dotenv import load_dotenv

from seleric_swarm.config.settings import Settings, get_settings
from seleric_swarm.llm.factory import build_llm
from seleric_swarm.observability.tracing import configure_langsmith_env, configure_logging
from seleric_swarm.paths import repo_root
from seleric_swarm.persistence.postgres import build_store
from seleric_swarm.prompts.registry import PromptRegistry
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.services.metrics import MetricRegistry
from seleric_swarm.services.ontology import OntologyService


def _sync_settings_to_environ(settings: Settings) -> None:
    """Push Settings into os.environ so YAML url_env / auth_token_env lookups work."""

    pairs = {
        "SELERIC_MCP_URL": settings.seleric_mcp_url,
        "SELERIC_MCP_TOKEN": settings.seleric_mcp_token,
        "AZURE_OPENAI_ENDPOINT": settings.azure_openai_endpoint,
        "AZURE_OPENAI_API_KEY": settings.azure_openai_api_key,
        "AZURE_OPENAI_MODEL": settings.azure_openai_model,
        "LANGSMITH_API_KEY": settings.langsmith_api_key,
        "LANGSMITH_PROJECT": settings.langsmith_project,
        "LANGSMITH_ENDPOINT": settings.langsmith_endpoint,
        "DATABASE_URL": settings.database_url,
    }
    for key, value in pairs.items():
        if value:
            os.environ[key] = value


def build_runtime(settings: Settings | None = None) -> SwarmRuntime:
    load_dotenv(repo_root() / ".env")
    settings = settings or get_settings()
    _sync_settings_to_environ(settings)
    configure_logging(settings)
    configure_langsmith_env(settings)
    agents = AgentRegistry(str(repo_root() / "config" / "agent_registry.yaml"))
    mcp = MCPGateway(settings.mcp_config_path, agents=agents)
    return SwarmRuntime(
        settings=settings,
        llm=build_llm(settings),
        prompts=PromptRegistry(settings.prompts_dir, settings.prompt_versions_path),
        mcp=mcp,
        metrics=MetricRegistry(settings.metric_registry_path),
        agents=agents,
        store=build_store(settings.persistence_backend, settings.database_url),
        ontology=OntologyService(mcp),
    )
