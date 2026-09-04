"""Ports + in-memory implementations for the Diagnostic subsystem.

Reuses the generic repositories/registries already defined for the Skeptic
(evidence, artifacts, causal graphs, incidents) and adds Diagnostic-specific
ports: an anomaly repository and a causal *estimation* service (the Diagnostic
produces a ``CausalAnalysisArtifact``; the Skeptic validates one).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from seleric_swarm.agents.diagnostic.contracts import CausalAnalysisArtifact

# reuse the Skeptic's generic infra so there is one implementation of each.
from seleric_swarm.agents.skeptic.registries import (  # noqa: F401
    ArtifactRepository,
    CausalGraph,
    CausalGraphRegistry,
    DeterministicStatsValidator,
    EvidenceRepository,
    IncidentPattern,
    IncidentRegistry,
    InMemoryArtifactRepository,
    InMemoryCausalGraphRegistry,
    InMemoryEvidenceRepository,
    InMemoryIncidentRegistry,
    StatisticalValidatorService,
    causal_graphs_from_yaml,
    repositories_from_blackboard,
)

# --------------------------------------------------------------------------- #
# Anomaly repository
# --------------------------------------------------------------------------- #


@runtime_checkable
class AnomalyRepository(Protocol):
    async def get_many(self, anomaly_ids: list[str]) -> list[dict[str, Any]]: ...

    async def by_mission(self, mission_id: str) -> list[dict[str, Any]]: ...


class InMemoryAnomalyRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            self.add(row)

    def add(self, row: dict[str, Any]) -> str:
        aid = row.get("anomaly_id") or row.get("artifact_id") or f"AN-{len(self._by_id)}"
        row = {**row, "anomaly_id": aid}
        self._by_id[aid] = row
        return aid

    async def get_many(self, anomaly_ids: list[str]) -> list[dict[str, Any]]:
        return [self._by_id[a] for a in anomaly_ids if a in self._by_id]

    async def by_mission(self, mission_id: str) -> list[dict[str, Any]]:
        return [r for r in self._by_id.values() if not mission_id or r.get("mission_id") in ("", mission_id)]


def anomaly_repo_from_blackboard(blackboard: Any) -> InMemoryAnomalyRepository:
    repo = InMemoryAnomalyRepository()
    for payload in blackboard._store.all():
        if payload.get("artifact_type") == "anomaly":
            repo.add({**payload, "anomaly_id": payload.get("artifact_id")})
    return repo


# --------------------------------------------------------------------------- #
# Causal estimation service (the DoWhy boundary, producing side)
# --------------------------------------------------------------------------- #


@dataclass
class CausalEstimationQuery:
    treatment: str
    outcome: str
    common_causes: list[str]
    graph_id: str = ""
    estimator: str = "backdoor.linear_regression"
    refuters: list[str] = field(default_factory=list)
    treatment_started_at: str | None = None
    outcome_started_at: str | None = None
    mission_id: str = ""
    hypothesis_id: str | None = None


@runtime_checkable
class CausalEstimationService(Protocol):
    async def estimate(
        self, query: CausalEstimationQuery, *, observations: Any = None
    ) -> CausalAnalysisArtifact: ...


class TemplateCausalEstimationService:
    """Deterministic, offline. Reads a scenario ``causal_truth`` dict and emits a
    metadata-level ``CausalAnalysisArtifact``. Mirrors the swarm's
    ``TemplateCausalEngine`` so fixture runs work with zero DoWhy/network."""

    def __init__(self, causal_truth: dict[str, Any] | None = None) -> None:
        self._truth = causal_truth or {}

    async def estimate(
        self, query: CausalEstimationQuery, *, observations: Any = None
    ) -> CausalAnalysisArtifact:
        t = self._truth
        matches = t.get("treatment") == query.treatment and t.get("outcome") == query.outcome
        refutations = list(t.get("refutations", [])) if matches else []
        return CausalAnalysisArtifact(
            causal_id=f"CAUS-{abs(hash((query.treatment, query.outcome))) % 10**10}",
            mission_id=query.mission_id,
            treatment=query.treatment,
            outcome=query.outcome,
            graph_id=t.get("graph_id", query.graph_id),
            common_causes=list(query.common_causes or t.get("common_causes", [])),
            estimator=query.estimator,
            estimated_effect=float(t["effect"]) if matches and "effect" in t else None,
            confidence_interval=list(t.get("effect_ci", [])) if matches else [],
            refutation_results=refutations,
            assumptions=["template causal truth; no observations were fitted"],
            limitations=["Metadata-level estimate; unmeasured confounding not excluded."],
            treatment_started_at=query.treatment_started_at,
            outcome_started_at=query.outcome_started_at,
            passed=bool(t.get("passed", False)) if matches else False,
            synthetic=True,
        )
