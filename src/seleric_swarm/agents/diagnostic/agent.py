"""DiagnosticAgent - the stable boundary for the Coordinator / swarm.

    result = await DiagnosticAgent(...).diagnose(request)

Runs the LangGraph workflow and returns a :class:`DiagnosticResult` carrying the
``DiagnosticArtifact``, the ``CausalAnalysisArtifact`` and the causal
``Claim[]`` -- exactly the contracts the Skeptic validates. Deterministic when no
reasoning model is supplied.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from seleric_swarm.agents.diagnostic.context import DiagnosticContext, DiagnosticDeps
from seleric_swarm.agents.diagnostic.contracts import DiagnosticRequest, DiagnosticResult
from seleric_swarm.agents.diagnostic.graph import build_diagnostic_graph
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies

_log = structlog.get_logger("seleric_swarm.agents.diagnostic")

_COMPILED_GRAPH = None


def _graph():
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_diagnostic_graph()
    return _COMPILED_GRAPH


class DiagnosticAgent:
    agent_id = "diagnostic_agent"
    agent_version = "1.0.0"

    def __init__(
        self,
        *,
        deps: DiagnosticDeps | None = None,
        policies: DiagnosticPolicies | None = None,
    ) -> None:
        self.deps = deps or DiagnosticDeps()
        self.policies = policies or DiagnosticPolicies.load()

    async def diagnose(self, request: DiagnosticRequest) -> DiagnosticResult:
        started = time.perf_counter()
        ctx = DiagnosticContext(request=request, policies=self.policies, deps=self.deps)
        final_state = await _graph().ainvoke(
            {
                "mission_id": request.mission_id,
                "diagnostic_run_id": f"DIAG-{int(started * 1000) % 10_000_000}",
                "request": request.model_dump(exclude={"observations"}),
                "_context": ctx,
            }
        )
        result: DiagnosticResult = final_state["_result"]
        self._emit(result, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        return result

    def _emit(self, result: DiagnosticResult, *, elapsed_ms: float) -> None:
        _log.info(
            "diagnostic.run",
            mission_id=result.mission_id,
            diagnostic_run_id=result.diagnostic_run_id,
            outcome_metric=result.outcome_metric,
            hypotheses=len(result.hypotheses),
            retained=[h.hypothesis_id for h in result.retained()],
            rejected=len(result.rejected()),
            causal_confidence=result.finding.causal_confidence if result.finding else None,
            claims=len(result.claims),
            synthetic=result.synthetic,
            elapsed_ms=elapsed_ms,
        )


def diagnostic_deps_from_blackboard(blackboard: Any, *, base: DiagnosticDeps | None = None) -> DiagnosticDeps:
    """Build DiagnosticDeps whose repositories read a live swarm Blackboard."""

    from seleric_swarm.agents.diagnostic.registries import (
        anomaly_repo_from_blackboard,
        repositories_from_blackboard,
    )

    base = base or DiagnosticDeps()
    evidence_repo, artifact_repo = repositories_from_blackboard(blackboard)
    return DiagnosticDeps(
        evidence_repo=evidence_repo,
        artifact_repo=artifact_repo,
        anomaly_repo=anomaly_repo_from_blackboard(blackboard),
        causal_graphs=base.causal_graphs,
        incident_registry=base.incident_registry,
        stats=base.stats,
        causal_service=base.causal_service,
        reasoning=base.reasoning,
        ontology=base.ontology,
    )
