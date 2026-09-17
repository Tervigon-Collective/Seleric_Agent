from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from seleric_swarm.config.settings import Settings
from seleric_swarm.llm.port import LLMPort
from seleric_swarm.persistence.memory import MissionStore
from seleric_swarm.prompts.registry import PromptRegistry
from seleric_swarm.protocols.mcp.gateway import MCPGateway
from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap
from seleric_swarm.services.metrics import MetricRegistry
from seleric_swarm.services.ontology import OntologyPort

if TYPE_CHECKING:
    from seleric_swarm.cancellation import CancellationBackend
    from seleric_swarm.checkpointing import CheckpointProvider
    from seleric_swarm.conversations.blobs import BlobStore
    from seleric_swarm.conversations.events import ActivityEventSink
    from seleric_swarm.conversations.repositories import ConversationRepositories
    from seleric_swarm.services.business_state import BusinessStateService


@dataclass
class SwarmRuntime:
    settings: Settings
    llm: LLMPort
    prompts: PromptRegistry
    mcp: MCPGateway
    metrics: MetricRegistry
    agents: AgentRegistry
    store: MissionStore
    ontology: OntologyPort | None = None
    # Live Cube catalogue cache — created by build_runtime(), warmed lazily on
    # first _resolve_measure() call.  None only in tests that bypass build_runtime.
    bootstrap: CatalogueBootstrap | None = None
    # docs/features/business-state-service — None only in tests that bypass
    # build_runtime and don't need it.
    business_state: BusinessStateService | None = None
    # Normalized thread/message/run persistence, separate from the legacy mission store.
    conversations: ConversationRepositories | None = None
    # Append-only public event bridge. None only in tests that construct runtimes directly.
    activity_events: ActivityEventSink | None = None
    checkpoint_provider: CheckpointProvider | None = None
    cancellation: CancellationBackend | None = None
    blob_store: BlobStore | None = None
