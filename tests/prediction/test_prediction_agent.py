"""Prediction Agent behaviour suite."""

from __future__ import annotations

from seleric_swarm.agents.prediction import PredictionRequest
from tests.prediction.conftest import MISSION, causal


def _req(**kw) -> PredictionRequest:
    kw.setdefault("mission_id", MISSION)
    kw.setdefault("target_metric", "metric.cac")
    kw.setdefault("horizon", "7d")
    kw.setdefault("evidence_refs", ["EV-cac"])
    kw.setdefault("causal_refs", ["CAUS-1"])
    return PredictionRequest(**kw)


# --------------------------------------------------------------------------- #
# 1. registered approved model -> forecast + claim, llm_generated False
# --------------------------------------------------------------------------- #
async def test_registered_model_produces_forecast(approved_model_deps):
    agent, _ = approved_model_deps()
    r = await agent.predict(_req())
    assert r.source == "registered_model"
    assert r.applicability == "in_domain"
    assert r.confidence in {"MODERATE", "STRONG"}
    fa = r.forecast_artifact
    assert fa.prediction == 815.0 and fa.interval == [770.0, 860.0]
    assert fa.model_id == "forecast.cac.v1" and fa.feature_set_id == "features.cac.v1"
    assert fa.llm_generated is False
    assert r.claims and r.claims[0].claim_type == "forecast"
    assert r.claims[0].forecast_refs == [fa.forecast_id]


# --------------------------------------------------------------------------- #
# 2. no registered model, history present -> approved statistical baseline
# --------------------------------------------------------------------------- #
async def test_falls_back_to_statistical_baseline(no_model_deps):
    agent = no_model_deps()
    r = await agent.predict(_req(history=[600, 610, 625, 640, 660, 685, 710, 740, 770, 782]))
    assert r.source == "statistical_baseline"
    fa = r.forecast_artifact
    assert fa.prediction is not None and len(fa.interval) == 2
    assert fa.model_id.startswith("baseline.")
    assert any("statistical baseline" in lim.lower() for lim in r.limitations)
    assert r.claims and r.claims[0].metadata["source"] == "statistical_baseline"


# --------------------------------------------------------------------------- #
# 3. no model and no history -> INSUFFICIENT_PREDICTIVE_EVIDENCE, no claim
# --------------------------------------------------------------------------- #
async def test_insufficient_predictive_evidence(no_model_deps):
    agent = no_model_deps()
    r = await agent.predict(_req(evidence_refs=[], causal_refs=[]))
    assert r.source == "insufficient"
    assert r.confidence == "INSUFFICIENT_PREDICTIVE_EVIDENCE"
    assert r.claims == []
    assert r.forecast_artifact is None
    assert any("INSUFFICIENT_PREDICTIVE_EVIDENCE" in lim for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 4. candidate (not approved) model is skipped -> baseline / insufficient
# --------------------------------------------------------------------------- #
async def test_unapproved_model_is_skipped(approved_model_deps):
    agent, _ = approved_model_deps(model_status="candidate")
    r = await agent.predict(_req(history=[600, 610, 625, 640, 660, 685, 710, 740, 770, 782]))
    assert r.source == "statistical_baseline"
    assert any("status" in lim.lower() for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 5. stale model validation -> skipped
# --------------------------------------------------------------------------- #
async def test_stale_model_is_skipped(approved_model_deps):
    agent, _ = approved_model_deps(last_validated_at="2025-01-01T00:00:00+00:00")
    r = await agent.predict(_req(history=[600, 610, 625, 640, 660, 685, 710, 740, 770, 782]))
    assert r.source == "statistical_baseline"
    assert any("validated" in lim.lower() for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 6. missing feature set -> model skipped
# --------------------------------------------------------------------------- #
async def test_missing_feature_set_skips_model(approved_model_deps):
    agent, _ = approved_model_deps(with_feature_set=False)
    r = await agent.predict(_req(history=[600, 610, 625, 640, 660, 685, 710, 740, 770, 782]))
    assert r.source == "statistical_baseline"
    assert any("feature set" in lim.lower() for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 7. LLM numeric fallback is never used (policy) - a forecast never has llm_generated
# --------------------------------------------------------------------------- #
async def test_no_llm_numeric_fallback(approved_model_deps, no_model_deps):
    r1 = await approved_model_deps()[0].predict(_req())
    r2 = await no_model_deps().predict(_req(history=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]))
    for r in (r1, r2):
        assert r.forecast_artifact.llm_generated is False


# --------------------------------------------------------------------------- #
# 8. drift = red -> forecast is INSUFFICIENT (Skeptic would REJECT anyway)
# --------------------------------------------------------------------------- #
async def test_drift_red_forces_insufficient(approved_model_deps):
    truth = {
        "target": "metric.cac", "horizon": "7d",
        "model": {"id": "forecast.cac.v1", "version": "1", "feature_set": "features.cac.v1",
                  "drift_status": "red", "backtest_metric": "MAPE=0.08"},
        "prediction": 815.0, "interval": [770.0, 860.0],
    }
    agent, _ = approved_model_deps(truth=truth)
    r = await agent.predict(_req())
    assert r.applicability == "out_of_domain"
    assert r.confidence == "INSUFFICIENT_PREDICTIVE_EVIDENCE"
    assert r.claims == []


# --------------------------------------------------------------------------- #
# 9. scenarios come from the interval + cause persistence, never invented
# --------------------------------------------------------------------------- #
async def test_scenarios_bounded_by_interval(approved_model_deps):
    # causally supported -> pessimistic reaches the interval bound
    agent_sup, _ = approved_model_deps(artifacts=[causal(passed=True)])
    r_sup = await agent_sup.predict(_req())
    names = {s.name for s in r_sup.scenarios}
    assert names == {"base", "optimistic", "pessimistic"}
    pess = next(s for s in r_sup.scenarios if s.name == "pessimistic")
    assert pess.prediction == 860.0  # bad-is-up + full persistence -> interval upper

    # not causally confirmed -> pessimistic pulled toward the point
    agent_unsup, _ = approved_model_deps(artifacts=[causal(passed=False)])
    r_unsup = await agent_unsup.predict(_req())
    pess2 = next(s for s in r_unsup.scenarios if s.name == "pessimistic")
    assert 815.0 < pess2.prediction < 860.0


# --------------------------------------------------------------------------- #
# 10. determinism
# --------------------------------------------------------------------------- #
async def test_deterministic(approved_model_deps):
    r1 = await approved_model_deps()[0].predict(_req())
    r2 = await approved_model_deps()[0].predict(_req())
    assert r1.source == r2.source and r1.confidence == r2.confidence
    assert r1.forecast_artifact.prediction == r2.forecast_artifact.prediction
    assert [s.prediction for s in r1.scenarios] == [s.prediction for s in r2.scenarios]
