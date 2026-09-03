"""Canonical structured artifacts for the two-axis swarm (architecture sec. 15).

Every stage of the intelligence pipeline transforms one typed artifact into the
next: Evidence -> Anomaly -> Hypothesis -> Causal -> Prediction / Strategy ->
Skeptic -> Validated claims. Agents exchange *references* to these on the
Blackboard, never raw datasets.

``data_origin`` / ``synthetic`` are load-bearing: any artifact derived from a
fixture or template provider is marked, and the claim gate refuses to promote a
synthetic-backed claim to a verified business fact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

DataOrigin = Literal["FIXTURE", "MCP", "MODEL", "STATS", "DERIVED", "TEMPLATE"]
ArtifactType = Literal[
    "evidence",
    "anomaly",
    "hypothesis",
    "causal",
    "prediction",
    "strategy",
    "skeptic",
]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _aid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class SwarmArtifact(BaseModel):
    """Common envelope for every artifact posted to the Blackboard."""

    artifact_id: str
    artifact_type: ArtifactType
    mission_id: str
    created_by: str
    created_at: str = Field(default_factory=_now)
    evidence_refs: list[str] = Field(default_factory=list)
    data_origin: DataOrigin = "DERIVED"
    synthetic: bool = False
    quality_flags: list[str] = Field(default_factory=list)

    def mark_synthetic(self) -> None:
        self.synthetic = True
        if "SYNTHETIC" not in self.quality_flags:
            self.quality_flags.append("SYNTHETIC")


class Evidence(SwarmArtifact):
    artifact_type: ArtifactType = "evidence"
    metric_or_fact: str
    value: Any = None
    baseline: Any = None
    change_pct: float | None = None
    unit: str | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    time_range: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def new(cls, *, mission_id: str, created_by: str, prefix: str = "EV", **kw: Any) -> Evidence:
        return cls(artifact_id=_aid(prefix), mission_id=mission_id, created_by=created_by, **kw)


class Anomaly(SwarmArtifact):
    artifact_type: ArtifactType = "anomaly"
    metric_id: str
    observed: float | None = None
    expected_range: list[float] = Field(default_factory=list)
    deviation_pct: float | None = None
    score: float | None = None
    detector: dict[str, Any] = Field(default_factory=dict)
    dimensions: dict[str, Any] = Field(default_factory=dict)
    start_time: str | None = None
    direction: Literal["up", "down", "unknown"] = "unknown"

    @classmethod
    def new(cls, *, mission_id: str, created_by: str, **kw: Any) -> Anomaly:
        return cls(artifact_id=_aid("AN"), mission_id=mission_id, created_by=created_by, **kw)


class Hypothesis(SwarmArtifact):
    artifact_type: ArtifactType = "hypothesis"
    statement: str
    domains: list[str] = Field(default_factory=list)
    status: Literal["proposed", "testing", "retained", "rejected"] = "proposed"
    supporting_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    score: float | None = None

    @classmethod
    def new(cls, *, mission_id: str, created_by: str, **kw: Any) -> Hypothesis:
        return cls(artifact_id=_aid("HYP"), mission_id=mission_id, created_by=created_by, **kw)


class Causal(SwarmArtifact):
    artifact_type: ArtifactType = "causal"
    hypothesis_ref: str | None = None
    treatment: str = ""
    outcome: str = ""
    common_causes: list[str] = Field(default_factory=list)
    graph_id: str = ""
    estimator: str = ""
    effect: float | None = None
    effect_ci: list[float] = Field(default_factory=list)
    refutations: list[dict[str, Any]] = Field(default_factory=list)
    passed: bool = False

    @classmethod
    def new(cls, *, mission_id: str, created_by: str, **kw: Any) -> Causal:
        return cls(artifact_id=_aid("CAUS"), mission_id=mission_id, created_by=created_by, **kw)


class Prediction(SwarmArtifact):
    artifact_type: ArtifactType = "prediction"
    target: str
    horizon: str = ""
    model: dict[str, Any] = Field(default_factory=dict)
    prediction: Any = None
    interval: list[float] = Field(default_factory=list)
    drift_status: str | None = None
    secondary: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def new(cls, *, mission_id: str, created_by: str, **kw: Any) -> Prediction:
        return cls(artifact_id=_aid("PRED"), mission_id=mission_id, created_by=created_by, **kw)


class Strategy(SwarmArtifact):
    artifact_type: ArtifactType = "strategy"
    problem_ref: str | None = None
    objective: str = ""
    options: list[dict[str, Any]] = Field(default_factory=list)
    recommended: list[str] = Field(default_factory=list)
    rationale: str = ""

    @classmethod
    def new(cls, *, mission_id: str, created_by: str, **kw: Any) -> Strategy:
        return cls(artifact_id=_aid("STRAT"), mission_id=mission_id, created_by=created_by, **kw)


class Skeptic(SwarmArtifact):
    artifact_type: ArtifactType = "skeptic"
    target_ref: str | None = None
    verdict: Literal["PASS", "REVISE", "REJECT"] = "PASS"
    attacks_run: list[str] = Field(default_factory=list)
    problems: list[dict[str, Any]] = Field(default_factory=list)
    required_followups: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def new(cls, *, mission_id: str, created_by: str, **kw: Any) -> Skeptic:
        return cls(artifact_id=_aid("SKEP"), mission_id=mission_id, created_by=created_by, **kw)


ARTIFACT_MODELS: dict[str, type[SwarmArtifact]] = {
    "evidence": Evidence,
    "anomaly": Anomaly,
    "hypothesis": Hypothesis,
    "causal": Causal,
    "prediction": Prediction,
    "strategy": Strategy,
    "skeptic": Skeptic,
}
