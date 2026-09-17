"""Seleric Diagnostic Agent - causal-graph-first root-cause analysis.

Candidate discovery walks the registered causal graph backward from the
outcome metric to find every node with a path to it (``causal_discovery.py``);
DoWhy (``causal/estimator.py``) estimates and refutes an effect for each one
(``causal_graph_builder.py`` derives the graph from ``config/metric_registry
.yaml``'s ``depends_on`` fields, merged with the hand-authored dimension-level
graph in ``config/causal_graphs.example.yaml``). The LLM
(``scenarios.py``) only narrates already-confirmed candidates afterward -- it
never proposes a mechanism or decides retain/reject.

Public surface:

    from seleric_swarm.agents.diagnostic import (
        DiagnosticAgent, DiagnosticRequest, DiagnosticResult, DiagnosticDeps,
    )

    result = await DiagnosticAgent(...).diagnose(
        DiagnosticRequest(mission_id=..., question=..., anomaly_refs=[...], evidence_refs=[...])
    )

Emits ``DiagnosticArtifact`` + ``CausalAnalysisArtifact`` + causal ``Claim[]`` -
the contracts the Skeptic validates.
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
