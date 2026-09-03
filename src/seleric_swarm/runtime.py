from __future__ import annotations

from dataclasses import dataclass

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.port import LLMPort
from seleric_swarm.persistence.memory import MissionStore
from seleric_swarm.prompts.registry import PromptRegistry
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.services.metrics import MetricRegistry


@dataclass
class SwarmRuntime:
    settings: Settings
    llm: LLMPort
    prompts: PromptRegistry
    mcp: MCPGateway
    metrics: MetricRegistry
    agents: AgentRegistry
    store: MissionStore
