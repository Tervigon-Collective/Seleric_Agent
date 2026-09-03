from __future__ import annotations

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


def build_runtime(settings: Settings | None = None) -> SwarmRuntime:
    settings = settings or get_settings()
    configure_logging(settings)
    configure_langsmith_env(settings)
    return SwarmRuntime(
        settings=settings,
        llm=build_llm(settings),
        prompts=PromptRegistry(settings.prompts_dir, settings.prompt_versions_path),
        mcp=MCPGateway(settings.mcp_config_path),
        metrics=MetricRegistry(settings.metric_registry_path),
        agents=AgentRegistry(str(repo_root() / "config" / "agent_registry.yaml")),
        store=build_store(settings.persistence_backend, settings.database_url),
    )
