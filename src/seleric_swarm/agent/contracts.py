"""Frozen Sprint 0 contracts, materialized as code.

Shapes are frozen in ``docs/refactor/CONTRACTS.md`` — edit that file first,
then this one, per the Sprint 0 gate in ``docs/refactor/SPRINT_PLAN.md``.
Reuses ``conversations/contracts.py`` (``Principal``, ``ContextBundle``,
``ArtifactProvenance``) rather than redefining them — that platform already
exists and is live, not a Sprint 0 invention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from seleric_swarm.conversations.contracts import ArtifactProvenance, ContextBundle, Principal

if TYPE_CHECKING:
    from seleric_swarm.state.artifacts import ArtifactStore


@dataclass(frozen=True)
class ExecutionLimits:
    """Supersedes governance/budget.py::MissionLimits (spec §39, rule 11)."""

    max_tool_calls: int = 8
    max_cube_queries: int = 6
    max_causal_queries: int = 3
    max_prediction_calls: int = 3
    max_validation_revisions: int = 1
    max_runtime_seconds: float = 120.0


class SelericMcpClient(Protocol):
    """Matches protocols/mcp/gateway.py::MCPGateway's call interface."""

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class SelericDeps:
    """One instance per mission run, immutable for the run's lifetime."""

    mission_id: str
    as_of: datetime  # one per mission (rule 7), UTC, tz-aware
    principal: Principal
    thread_id: str
    run_id: str
    trace_id: str
    context: ContextBundle
    mcp_client: SelericMcpClient
    artifact_store: ArtifactStore
    limits: ExecutionLimits


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    artifact_ids: list[str] = Field(default_factory=list)
    summary: str
    provenance: ArtifactProvenance = Field(default_factory=ArtifactProvenance)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def _envelope_invariants(self) -> ToolResult:
        if not self.success and not self.error_code:
            raise ValueError("success=False requires error_code")
        if not self.success and self.artifact_ids:
            raise ValueError("success=False requires empty artifact_ids")
        return self


class EvidenceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    grain: Literal["day", "week", "month", "none"]
    as_of: datetime
    period_start: datetime
    period_end: datetime
    value: float | None
    unit: str | None = None
    source_query: dict[str, Any]
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(tz=None))


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_type: str
    statement: str
    evidence_ids: list[str] = Field(min_length=1)
    metrics: dict[str, float] = Field(default_factory=dict)
    status: Literal["ACTIVE", "REJECTED"] = "ACTIVE"
    supersedes: str | None = None


class CausalArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: dict[str, Any]
    evidence_classification: Literal[
        "OBSERVATION", "ASSOCIATION", "HYPOTHESIS", "CAUSALLY_SUPPORTED", "EXPERIMENTALLY_VALIDATED"
    ]
    effect_estimate: float | None
    refutation_checks: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    method: str

    @model_validator(mode="after")
    def _causally_supported_requires_refutation(self) -> CausalArtifact:
        if self.evidence_classification in {"CAUSALLY_SUPPORTED", "EXPERIMENTALLY_VALIDATED"} and not self.refutation_checks:
            raise ValueError("CAUSALLY_SUPPORTED/EXPERIMENTALLY_VALIDATED requires refutation_checks")
        return self


class PredictionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_id: str
    model_version: str
    prediction_type: str
    value: float
    confidence_interval: tuple[float, float] | None = None
    evidence_ids: list[str] = Field(min_length=1)
    feature_leakage_checked: bool
