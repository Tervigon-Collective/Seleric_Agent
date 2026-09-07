"""Typed contracts for the Strategy subsystem.

The Strategy Agent answers "given the diagnosed mechanism, what should we
change?" It never proposes an action independent of a diagnosed mechanism —
when the request carries no mechanism (no retained hypothesis / causal
artifact upstream), the result is ``INSUFFICIENT_STRATEGY_EVIDENCE`` with no
options, mirroring Prediction's ``INSUFFICIENT_PREDICTIVE_EVIDENCE`` pattern.
Business-constraint enforcement (inventory, budget, margin, ...) is owned by
the Skeptic's ``BusinessRuleService`` at validation time, not here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

StrategySource = Literal["mechanism_grounded", "insufficient"]

StrategyConfidence = Literal[
    "INSUFFICIENT_STRATEGY_EVIDENCE",
    "WEAK",
    "MODERATE",
    "STRONG",
]

MechanismFit = Literal["very_high", "high", "medium", "low"]


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _rid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class StrategyRequest(BaseModel):
    mission_id: str
    question: str = ""
    owner_domain: str | None = None
    # the mechanism to act on — "<treatment> -> <outcome>" or a free-text
    # statement from a retained hypothesis. Empty means "no diagnosis to act on".
    diagnosed_mechanism: str = ""
    treatment_metric: str | None = None
    outcome_metric: str | None = None
    mechanism_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class InterventionOption(BaseModel):
    action: str
    mechanism_fit: MechanismFit
    expected_impact: str = ""
    cost: str = ""
    risk: str = ""
    reversibility: str = ""
    rationale: str = ""


class InterventionOptionsLLM(BaseModel):
    options: list[InterventionOption] = Field(default_factory=list)


class StrategyResult(BaseModel):
    strategy_run_id: str = Field(default_factory=lambda: _rid("STRATRUN"))
    mission_id: str
    owner_domain: str | None = None
    mechanism_ref: str | None = None
    objective: str = ""

    source: StrategySource = "insufficient"
    confidence: StrategyConfidence = "INSUFFICIENT_STRATEGY_EVIDENCE"

    options: list[InterventionOption] = Field(default_factory=list)
    recommended: list[str] = Field(default_factory=list)
    rationale: str = ""

    methodology: str = ""
    limitations: list[str] = Field(default_factory=list)
    audit: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    synthetic: bool = False
    created_at: str = Field(default_factory=_now)

    def has_options(self) -> bool:
        return self.source != "insufficient" and bool(self.options)
