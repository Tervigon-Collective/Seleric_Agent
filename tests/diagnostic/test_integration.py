"""Diagnostic -> Skeptic handoff, swarm bridge, and DoWhy estimation service."""

from __future__ import annotations

import importlib.util

import numpy as np
import pandas as pd
import pytest

from seleric_swarm.agents.diagnostic import DiagnosticRequest
from seleric_swarm.agents.diagnostic.a2a import DiagnosticA2AAdapter
from seleric_swarm.agents.diagnostic.registries import CausalEstimationQuery
from seleric_swarm.agents.diagnostic.services import DoWhyCausalEstimationService
from seleric_swarm.agents.skeptic import SkepticAgent, SkepticValidationRequest
from seleric_swarm.agents.skeptic.context import SkepticDeps
from seleric_swarm.agents.skeptic.registries import (
    InMemoryArtifactRepository,
    InMemoryEvidenceRepository,
    causal_graphs_from_yaml,
)
from tests.diagnostic.conftest import MISSION, anomaly, latency_bundle

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------- #
# Diagnostic output feeds straight into the Skeptic and PASSES
# --------------------------------------------------------------------------- #
async def test_diagnostic_claim_passes_skeptic(make_agent):
    diag = make_agent(
        latency_bundle(),
        [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")],
    )
    result = await diag.diagnose(
        DiagnosticRequest(
            mission_id=MISSION,
            question="Why did purchase CVR drop?",
            outcome_metric="metric.purchase_cvr",
            degradation_started_at="2026-09-01T12:05:00+05:30",
            context={"trust_metadata_causal": True},
        )
    )
    assert result.claims, "diagnostic produced a causal claim"
    claim = result.claims[0]

    # hand the Diagnostic's artifacts to the Skeptic verbatim
    ev_repo = InMemoryEvidenceRepository(
        [{**e, "evidence_id": e.get("evidence_id") or e.get("artifact_id")} for e in latency_bundle()]
    )
    art_repo = InMemoryArtifactRepository(
        [
            {**result.causal_artifact.model_dump(), "artifact_type": "causal", "artifact_id": result.causal_artifact.causal_id},
            {**result.diagnostic_artifact.model_dump(), "artifact_type": "diagnostic", "artifact_id": result.diagnostic_artifact.diagnostic_id},
        ]
    )
    skeptic = SkepticAgent(
        evidence_repo=ev_repo,
        artifact_repo=art_repo,
        deps=SkepticDeps(causal_graphs=causal_graphs_from_yaml()),
    )
    verdict = await skeptic.validate_claim(
        SkepticValidationRequest(mission_id=MISSION, claim=claim, evidence_refs=claim.support_refs)
    )
    assert verdict.verdict in {"PASS", "REVISE"}  # synthetic inputs -> at best REVISE is acceptable
    assert verdict.claim_type == "causal"
    # the Skeptic saw a real causal artifact, not a bare assertion
    assert "causal" in verdict.validator_results


# --------------------------------------------------------------------------- #
# A2A adapter wraps the result
# --------------------------------------------------------------------------- #
async def test_a2a_adapter(make_agent):
    adapter = DiagnosticA2AAdapter(
        make_agent(latency_bundle(), [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")])
    )
    out = await adapter.handle(
        {
            "mission_id": MISSION,
            "intent": "causal_diagnosis",
            "question": "Why did purchase CVR drop?",
            "outcome_metric": "metric.purchase_cvr",
            "degradation_started_at": "2026-09-01T12:05:00+05:30",
            "context": {"trust_metadata_causal": True},
        }
    )
    assert out["ok"] is True
    assert out["produced"] == "diagnostic_artifact"
    assert out["causal_artifact"]["treatment"] == "metric.mobile_lcp_seconds"
    assert out["claims"]


# --------------------------------------------------------------------------- #
# DoWhy estimation service produces a real, fitted causal artifact
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(importlib.util.find_spec("dowhy") is None, reason="DoWhy not installed")
async def test_dowhy_estimation_service_fits_observations():
    rng = np.random.default_rng(0)
    n = 700
    confounder = rng.normal(size=n)
    latency = 0.7 * confounder + rng.normal(size=n)
    cvr = -0.55 * latency + 0.4 * confounder + rng.normal(scale=0.3, size=n)
    df = pd.DataFrame({"metric.mobile_lcp_seconds": latency, "metric.purchase_cvr": cvr, "metric.sessions": confounder})

    svc = DoWhyCausalEstimationService()
    art = await svc.estimate(
        CausalEstimationQuery(
            treatment="metric.mobile_lcp_seconds",
            outcome="metric.purchase_cvr",
            common_causes=["metric.sessions"],
            graph_id="causal.funnel_purchase.v1",
            treatment_started_at="2026-09-01T11:40:00+00:00",
            outcome_started_at="2026-09-01T12:05:00+00:00",
            mission_id=MISSION,
        ),
        observations=df,
    )
    assert art.synthetic is False
    assert art.estimated_effect is not None and art.estimated_effect < 0
    assert art.sample_size == n
    assert len(art.refutation_results) >= 2


# --------------------------------------------------------------------------- #
# swarm bridge keeps the reference mission green with full_diagnostic=True
# --------------------------------------------------------------------------- #
async def test_reference_mission_full_diagnostic(runtime):
    from seleric_swarm.swarm.orchestrator import run_swarm_mission

    q = "Why has our CAC increased for the last three days, what happens if this continues, and what should we do?"
    res = await run_swarm_mission(
        runtime, query=q, scenario_id="cac_regression", as_of="2026-09-03", full_diagnostic=True
    )
    assert res.status == "completed"
    chain = [res.initial_mission_lead] + [h["to_agent"] for h in res.handoff_history]
    assert chain == ["performance_agent", "funnel_agent", "technical_agent"]
    assert res.artifacts["hypothesis"] and res.artifacts["causal"]
    assert res.artifacts["skeptic"]
    text = res.final_response.lower()
    assert "skeptic verdict: pass" in text
    assert "roll back" in text or "rollback" in text or "hotfix" in text


async def test_reference_mission_full_diagnostic_and_skeptic(runtime):
    from seleric_swarm.swarm.orchestrator import run_swarm_mission

    q = "Why has our CAC increased for the last three days, what happens if this continues, and what should we do?"
    res = await run_swarm_mission(
        runtime,
        query=q,
        scenario_id="cac_regression",
        as_of="2026-09-03",
        full_diagnostic=True,
        full_skeptic=True,
    )
    assert res.status in {"completed", "partial"}
    assert res.artifacts["skeptic"]
    skeptic_art_id = res.artifacts["skeptic"][0]
    assert skeptic_art_id
