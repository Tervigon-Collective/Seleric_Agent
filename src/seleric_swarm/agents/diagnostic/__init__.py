"""Seleric Diagnostic Agent - explicit-hypothesis, test-driven root-cause analysis.

Public surface:

    from seleric_swarm.agents.diagnostic import (
        DiagnosticAgent, DiagnosticRequest, DiagnosticResult, DiagnosticDeps,
    )

    result = await DiagnosticAgent(...).diagnose(
        DiagnosticRequest(mission_id=..., question=..., anomaly_refs=[...], evidence_refs=[...])
    )

Emits ``DiagnosticArtifact`` + ``CausalAnalysisArtifact`` + causal ``Claim[]`` -
the contracts the Skeptic validates. See ``docs/diagnostic/``.
"""

from __future__ import annotations

from seleric_swarm.agents.diagnostic.a2a import DiagnosticA2AAdapter
from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent, diagnostic_deps_from_blackboard
from seleric_swarm.agents.diagnostic.context import DiagnosticContext, DiagnosticDeps
from seleric_swarm.agents.diagnostic.contracts import (
    CausalAnalysisArtifact,
    Claim,
    DiagnosticArtifact,
    DiagnosticFinding,
    DiagnosticHypothesis,
    DiagnosticRequest,
    DiagnosticResult,
    HypothesisTest,
    TestResult,
)
from seleric_swarm.agents.diagnostic.graph import build_diagnostic_graph
from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies

__all__ = [
    "CausalAnalysisArtifact",
    "Claim",
    "DiagnosticA2AAdapter",
    "DiagnosticAgent",
    "DiagnosticArtifact",
    "DiagnosticContext",
    "DiagnosticDeps",
    "DiagnosticFinding",
    "DiagnosticHypothesis",
    "DiagnosticPolicies",
    "DiagnosticRequest",
    "DiagnosticResult",
    "HypothesisTest",
    "TestResult",
    "build_diagnostic_graph",
    "diagnostic_deps_from_blackboard",
]
