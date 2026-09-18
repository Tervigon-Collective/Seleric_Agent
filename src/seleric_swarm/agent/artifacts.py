"""Artifact payload schemas — frozen contract, ``docs/refactor/CONTRACTS.md`` §3.

Each is stored as ``conversations.contracts.Artifact.payload`` (the existing,
reused envelope) with ``Artifact.artifact_type`` set to the discriminator
named on each class below. Profile A owns the ``ArtifactStore``
(``state/artifacts.py``); Profile B is the only writer of ``EvidenceArtifact``;
Profile C is the only writer of ``Finding``/``CausalArtifact``/
``PredictionArtifact`` (non-negotiable rule 6/8 — no other profile writes
these types directly).

Change control: this file's shapes are frozen as of the Sprint 0 gate
(2026-09-18). Changing a field requires updating ``CONTRACTS.md`` and noting
it in ``TASK_SHEET.md``'s Log first — don't let the code and the doc drift.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class EvidenceArtifact(BaseModel):
    """``artifact_type="evidence"``, ``classification="factual"``.

    Immutable once written (non-negotiable rule 8) — never edited in place.
    A correction is a new ``EvidenceArtifact`` plus a ``Finding`` with
    ``status=REJECTED, supersedes=<old_finding_id>`` if a downstream finding
    depended on the value being corrected.
    """

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
    fetched_at: datetime = Field(default_factory=_utc_now)


class Finding(BaseModel):
    """``artifact_type="finding"``, ``classification="derived"``.

    Output of the Analytics toolset's calculations over already-fetched
    ``EvidenceArtifact``s (non-negotiable rule 5 — never fetches its own
    data).
    """

    model_config = ConfigDict(extra="forbid")

    finding_type: str
    statement: str
    evidence_ids: list[str] = Field(min_length=1)
    metrics: dict[str, float] = Field(default_factory=dict)
    status: Literal["ACTIVE", "REJECTED"] = "ACTIVE"
    supersedes: str | None = None


class CausalArtifact(BaseModel):
    """``artifact_type="causal"``, ``classification="derived"``."""

    model_config = ConfigDict(extra="forbid")

    query: dict[str, Any]
    evidence_classification: Literal[
        "OBSERVATION", "ASSOCIATION", "HYPOTHESIS",
        "CAUSALLY_SUPPORTED", "EXPERIMENTALLY_VALIDATED",
    ]
    effect_estimate: float | None
    refutation_checks: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    method: str

    @model_validator(mode="after")
    def causally_supported_requires_refutation(self) -> CausalArtifact:
        if (
            self.evidence_classification in {"CAUSALLY_SUPPORTED", "EXPERIMENTALLY_VALIDATED"}
            and not self.refutation_checks
        ):
            raise ValueError("CAUSALLY_SUPPORTED/EXPERIMENTALLY_VALIDATED requires refutation_checks")
        return self


class PredictionArtifact(BaseModel):
    """``artifact_type="prediction"``, ``classification="derived"``."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_id: str
    model_version: str
    prediction_type: str
    value: float
    confidence_interval: tuple[float, float] | None = None
    evidence_ids: list[str] = Field(min_length=1)
    feature_leakage_checked: bool
