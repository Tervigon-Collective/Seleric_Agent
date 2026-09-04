"""Prediction -> Skeptic handoff, A2A adapter, swarm bridge."""

from __future__ import annotations

import pytest

from seleric_swarm.agents.prediction import PredictionRequest
from seleric_swarm.agents.prediction.a2a import PredictionA2AAdapter
from seleric_swarm.agents.skeptic import SkepticAgent, SkepticValidationRequest
from seleric_swarm.agents.skeptic.context import SkepticDeps
from seleric_swarm.agents.skeptic.registries import (
    InMemoryArtifactRepository,
    InMemoryEvidenceRepository,
)
from seleric_swarm.agents.skeptic.registries import (
    InMemoryModelRegistry as SkepticModelRegistry,
)
from seleric_swarm.agents.skeptic.registries import (
    ModelRecord as SkepticModelRecord,
)
from tests.prediction.conftest import MISSION, ev

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _req(**kw) -> PredictionRequest:
    kw.setdefault("mission_id", MISSION)
    kw.setdefault("target_metric", "metric.cac")
    kw.setdefault("horizon", "7d")
    kw.setdefault("evidence_refs", ["EV-cac"])
    kw.setdefault("causal_refs", ["CAUS-1"])
    return PredictionRequest(**kw)


# --------------------------------------------------------------------------- #
# A registered-model forecast passes the Skeptic's model + forecast validators
# --------------------------------------------------------------------------- #
async def test_forecast_claim_passes_skeptic(approved_model_deps):
    agent, _ = approved_model_deps()
    result = await agent.predict(_req())
    assert result.claims
    claim = result.claims[0]
    fa = result.forecast_artifact

    ev_repo = InMemoryEvidenceRepository([ev("EV-cac", "metric.cac", 782.0, change_pct=29.5)])
    art_repo = InMemoryArtifactRepository(
        [{**fa.model_dump(), "artifact_type": "prediction", "artifact_id": fa.forecast_id}]
    )
    # the Skeptic needs the model registered + approved to PASS the model validator
    sk_models = SkepticModelRegistry()
    sk_models.add(
        SkepticModelRecord(
            model_id="forecast.cac.v1", version="1", status="approved", target="metric.cac",
            backtest_available=True, last_validated_at="2026-08-20T00:00:00+00:00",
        )
    )
    skeptic = SkepticAgent(
        evidence_repo=ev_repo,
        artifact_repo=art_repo,
        deps=SkepticDeps(model_registry=sk_models),
    )
    verdict = await skeptic.validate_claim(
        SkepticValidationRequest(mission_id=MISSION, claim=claim, evidence_refs=claim.support_refs)
    )
    assert verdict.claim_type == "forecast"
    assert verdict.verdict in {"PASS", "REVISE"}   # synthetic inputs cap at REVISE
    # no blocking model/forecast challenge
    assert not any(c.category in {"model", "forecast"} and c.severity == "blocking" for c in verdict.challenges)


# --------------------------------------------------------------------------- #
# an INSUFFICIENT result yields no claim -> nothing for the Skeptic to pass
# --------------------------------------------------------------------------- #
async def test_insufficient_yields_no_claim(no_model_deps):
    r = await no_model_deps().predict(_req(evidence_refs=[], causal_refs=[]))
    assert r.claims == []
    assert r.forecast_artifact is None


# --------------------------------------------------------------------------- #
# A2A adapter
# --------------------------------------------------------------------------- #
async def test_a2a_adapter(approved_model_deps):
    adapter = PredictionA2AAdapter(approved_model_deps()[0])
    out = await adapter.handle(
        {
            "mission_id": MISSION,
            "intent": "forecast",
            "target_metric": "metric.cac",
            "horizon": "7d",
            "evidence_refs": ["EV-cac"],
            "causal_refs": ["CAUS-1"],
        }
    )
    assert out["ok"] is True
    assert out["produced"] == "forecast_artifact"
    assert out["forecast_artifact"]["model_id"] == "forecast.cac.v1"
    assert out["forecast_artifact"]["llm_generated"] is False
    assert out["claims"]


async def test_a2a_adapter_insufficient(no_model_deps):
    adapter = PredictionA2AAdapter(no_model_deps())
    out = await adapter.handle({"mission_id": MISSION, "intent": "forecast", "target_metric": "metric.cac"})
    assert out["ok"] is True
    assert out["produced"] == "insufficient_predictive_evidence"
    assert out["claims"] == []


# --------------------------------------------------------------------------- #
# swarm bridge keeps the reference mission green with full_prediction=True
# --------------------------------------------------------------------------- #
async def test_reference_mission_full_prediction(runtime):
    from seleric_swarm.swarm.orchestrator import run_swarm_mission

    q = "Why has our CAC increased for the last three days, what happens if this continues, and what should we do?"
    res = await run_swarm_mission(runtime, query=q, as_of="2026-09-03", full_prediction=True)
    assert res.status == "completed"
    assert res.artifacts["prediction"], "prediction artifact posted"
    ev_kinds = [e["kind"] for e in res.events]
    assert "prediction_done" in ev_kinds


async def test_reference_mission_all_three_subsystems(runtime):
    from seleric_swarm.swarm.orchestrator import run_swarm_mission

    q = "Why has our CAC increased for the last three days, what happens if this continues, and what should we do?"
    res = await run_swarm_mission(
        runtime, query=q, as_of="2026-09-03",
        full_diagnostic=True, full_prediction=True, full_skeptic=True,
    )
    assert res.status in {"completed", "partial"}
    assert res.artifacts["hypothesis"] and res.artifacts["causal"]
    assert res.artifacts["prediction"] and res.artifacts["skeptic"]
