"""Seleric Skeptic Agent - independent verification / falsification subsystem.

Public surface:

    from seleric_swarm.agents.skeptic import (
        SkepticAgent, SkepticValidationRequest, SkepticVerdict, Claim, SkepticDeps,
    )

    verdict = await SkepticAgent(...).validate_claim(
        SkepticValidationRequest(mission_id=..., claim=Claim(...), evidence_refs=[...])
    )

See ``docs/skeptic/`` for architecture, contracts and integration points.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.a2a import SkepticA2AAdapter
from seleric_swarm.agents.skeptic.agent import SkepticAgent, make_claim, skeptic_from_blackboard
from seleric_swarm.agents.skeptic.context import SkepticContext, SkepticDeps, ValidatorOutcome
from seleric_swarm.agents.skeptic.contracts import (
    AlternativeHypothesis,
    AnomalyArtifact,
    CausalAnalysisArtifact,
    Challenge,
    Claim,
    DiagnosticArtifact,
    EvidenceArtifact,
    EvidenceGap,
    FollowUpTask,
    ForecastArtifact,
    PredictionArtifact,
    SkepticValidationRequest,
    SkepticVerdict,
    StrategyArtifact,
)
from seleric_swarm.agents.skeptic.graph import build_skeptic_graph
from seleric_swarm.agents.skeptic.policies import SkepticPolicies

__all__ = [
    "AlternativeHypothesis",
    "AnomalyArtifact",
    "CausalAnalysisArtifact",
    "Challenge",
    # contracts
    "Claim",
    "DiagnosticArtifact",
    "EvidenceArtifact",
    "EvidenceGap",
    "FollowUpTask",
    "ForecastArtifact",
    "PredictionArtifact",
    "SkepticA2AAdapter",
    "SkepticAgent",
    "SkepticContext",
    "SkepticDeps",
    "SkepticPolicies",
    "SkepticValidationRequest",
    "SkepticVerdict",
    "StrategyArtifact",
    "ValidatorOutcome",
    "build_skeptic_graph",
    "make_claim",
    "skeptic_from_blackboard",
]
