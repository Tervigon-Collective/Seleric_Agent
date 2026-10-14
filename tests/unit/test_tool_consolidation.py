"""Merged tools: ``find_metrics`` (concept resolver + search fallback + listing) and
``analyze`` (one entry point for the six calculations over fetched evidence)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore


class _Mcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.calls.append((capability, arguments))
        if capability == "seleric.catalogue_resolve_concept":
            if arguments["text"] == "net sales":
                return {"kind": "resolved_concept", "metric_id": "net_sales", "axes": {"basis": "net"}}
            return {"kind": "unknown_concept", "suggestions": []}
        if capability == "seleric.catalogue_search_metrics":
            return {"results": [{"id": "sessions", "name": "Sessions", "score": 0.9}]}
        return {}


def _ctx(mcp: _Mcp) -> SimpleNamespace:
    deps = SelericDeps(
        mission_id="MS3-t",
        as_of=datetime(2026, 10, 7, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(
            metrics=(
                CatalogueMetricMeta(id="net_sales", label="Net Sales", view="commerce"),
                CatalogueMetricMeta(id="sessions", label="Sessions", view="web_sessions"),
            )
        ),
    )
    return SimpleNamespace(deps=deps, run_step=1)






