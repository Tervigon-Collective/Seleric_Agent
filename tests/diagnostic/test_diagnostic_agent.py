"""Diagnostic Agent behaviour suite."""

from __future__ import annotations

from seleric_swarm.agents.diagnostic import DiagnosticRequest
from tests.diagnostic.conftest import MISSION, anomaly, ev, latency_bundle


def _req(**kw) -> DiagnosticRequest:
    kw.setdefault("mission_id", MISSION)
    kw.setdefault("question", "Why did purchase conversion drop?")
    return DiagnosticRequest(**kw)


# --------------------------------------------------------------------------- #
# 1. reference mission: latency regression is retained, alternatives rejected
# --------------------------------------------------------------------------- #
async def test_reference_latency_regression_retained(make_agent):
    agent = make_agent(
        latency_bundle(),
        [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30"),
         anomaly("AN-lcp", "metric.mobile_lcp_seconds", 164.0, direction="up", start_time="2026-09-01T11:47:00+05:30")],
    )
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    assert r.finding is not None
    assert r.finding.causal_confidence in {"CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS", "STRONGLY_SUPPORTED"}
    assert "latency" in r.finding.statement.lower()
    retained = r.retained()
    assert len(retained) == 1 and retained[0].treatment_metric == "metric.mobile_lcp_seconds"
    assert len(r.rejected()) >= 3
    assert r.claims and r.claims[0].claim_type == "causal"
    # falsification bookkeeping: rejected alternatives carry the evidence that
    # actually falsified them, not just a status flip with a hidden reason
    assert r.contradictions
    assert all(c.hypothesis_id != retained[0].hypothesis_id for c in r.contradictions)
    assert all(c.severity in {"info", "warning", "blocking"} for c in r.contradictions)
    assert any("confounding" in lim.lower() for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 1a. retained mechanism's domain differs from the current lead ->
#     leadership transfer is *recommended*, not executed; incident_type set
# --------------------------------------------------------------------------- #
async def test_leadership_transfer_recommended_and_incident_type_set(make_agent):
    agent = make_agent(
        latency_bundle(),
        [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30"),
         anomaly("AN-lcp", "metric.mobile_lcp_seconds", 164.0, direction="up", start_time="2026-09-01T11:47:00+05:30")],
    )
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        lead_domain="performance_agent",
        context={"trust_metadata_causal": True},
    ))
    assert r.leadership_transfer_recommended is True
    assert r.recommended_domain_lead == "technical_agent"
    assert r.leadership_transfer_reason and "performance" in r.leadership_transfer_reason
    assert r.incident_type == "technical"


async def test_no_leadership_transfer_when_lead_already_owns_mechanism(make_agent):
    agent = make_agent(
        latency_bundle(),
        [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30"),
         anomaly("AN-lcp", "metric.mobile_lcp_seconds", 164.0, direction="up", start_time="2026-09-01T11:47:00+05:30")],
    )
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        lead_domain="technical_agent",
        context={"trust_metadata_causal": True},
    ))
    assert r.leadership_transfer_recommended is False
    assert r.recommended_domain_lead is None


# --------------------------------------------------------------------------- #
# 1b. a reported (not merely absent) small sample size downgrades support via
#     the shared deterministic statistics service - never treated as zero
# --------------------------------------------------------------------------- #
async def test_reported_small_sample_size_downgrades_evidence_sufficiency(make_agent):
    bundle = latency_bundle()
    for row in bundle:
        if row.get("metric_id") == "metric.mobile_lcp_seconds":
            row["sample_size"] = 5  # far below the deterministic validator's power threshold
    agent = make_agent(
        bundle,
        [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30"),
         anomaly("AN-lcp", "metric.mobile_lcp_seconds", 164.0, direction="up", start_time="2026-09-01T11:47:00+05:30")],
    )
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    latency_h = next(h for h in r.hypotheses if h.treatment_metric == "metric.mobile_lcp_seconds")
    ev_check = next(t for t in latency_h.test_results if t.kind == "evidence_sufficiency")
    assert ev_check.passed is False
    assert "sample_size_check" in ev_check.detail
    assert ev_check.detail["sample_size_check"]["sample_size"] == 5


# --------------------------------------------------------------------------- #
# 2. impossible temporal ordering -> hypothesis rejected on the hard gate
# --------------------------------------------------------------------------- #
async def test_temporal_reversal_rejects_hypothesis(make_agent):
    bundle = latency_bundle()
    # deploy well AFTER the conversion decline (beyond the 90-min tolerance)
    for row in bundle:
        if row.get("metric_or_fact") == "event.frontend_deployment":
            row["value"] = "2026-09-01T15:00:00+05:30"
        if row.get("metric_id") == "metric.mobile_lcp_seconds":
            row["start_time"] = "2026-09-01T15:05:00+05:30"
    agent = make_agent(bundle, [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")])
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    latency_h = next(h for h in r.hypotheses if h.treatment_metric == "metric.mobile_lcp_seconds")
    assert latency_h.status == "rejected"
    assert "hard gate" in (latency_h.rejection_reason or "")
    blocking = [c for c in r.contradictions if c.hypothesis_id == latency_h.hypothesis_id]
    assert blocking and blocking[0].category == "temporal" and blocking[0].severity == "blocking"


# --------------------------------------------------------------------------- #
# 2b. no anomaly evidence and no metric hint -> NO_CONFIRMED_ANOMALY, no
#     fabricated root cause against a hardcoded default metric
# --------------------------------------------------------------------------- #
async def test_no_anomaly_does_not_fabricate_root_cause(make_agent):
    agent = make_agent([], [])
    r = await agent.diagnose(_req())
    assert r.hypotheses == []
    assert r.finding is None
    assert r.claims == []
    assert any("NO_CONFIRMED_ANOMALY" in lim for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 2b2. unrecognized metric -> INSUFFICIENT_EVIDENCE, not a silent empty result
# --------------------------------------------------------------------------- #
async def test_unknown_metric_reports_insufficient_evidence(make_agent):
    agent = make_agent([], [])
    r = await agent.diagnose(_req(outcome_metric="metric.totally_unrecognized_thing"))
    assert r.hypotheses == []
    assert r.finding is None
    assert any("INSUFFICIENT_EVIDENCE" in lim for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 2c. runtime budget exhaustion returns a partial result instead of hanging
# --------------------------------------------------------------------------- #
async def test_runtime_budget_exhaustion_returns_partial_result(make_agent):
    import asyncio

    from seleric_swarm.agents.diagnostic import DiagnosticDeps
    from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent
    from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies
    from seleric_swarm.agents.diagnostic.registries import (
        InMemoryAnomalyRepository,
        InMemoryEvidenceRepository,
        TemplateCausalEstimationService,
    )
    from tests.diagnostic.conftest import CAC_TRUTH

    class _SlowReasoningModel:
        async def generate_structured(self, *, system, user, schema, tags=None):
            await asyncio.sleep(5)
            raise AssertionError("should have timed out before returning")

    deps = DiagnosticDeps(
        evidence_repo=InMemoryEvidenceRepository(latency_bundle()),
        anomaly_repo=InMemoryAnomalyRepository(
            [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")]
        ),
        causal_service=TemplateCausalEstimationService(CAC_TRUTH),
        reasoning=_SlowReasoningModel(),
    )
    policies = DiagnosticPolicies(raw={"budgets": {"max_runtime_seconds": 1, "max_hypotheses": 20}})
    r = await DiagnosticAgent(deps=deps, policies=policies).diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
    ))
    assert r.methodology == "budget_exhausted"
    assert any("runtime budget" in lim for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 3. no causal artifact match -> metadata-only, capped, inconclusive
# --------------------------------------------------------------------------- #
async def test_metadata_only_is_capped_inconclusive(make_agent):
    agent = make_agent(
        latency_bundle(),
        [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")],
        causal_truth={},  # estimator returns passed=False
    )
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        # NOTE: no trust_metadata_causal -> ceiling applies
    ))
    assert r.retained() == []
    assert r.finding is None or r.finding.retained_hypothesis_id is None
    assert any("metadata-only" in lim.lower() for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 4. outcome pivots to the downstream frontier when leadership moved
# --------------------------------------------------------------------------- #
async def test_outcome_pivots_to_downstream_frontier(make_agent):
    agent = make_agent(
        latency_bundle() + [ev("EV-cac", "metric.cac", 782.0, change_pct=29.5)],
        [anomaly("AN-cac", "metric.cac", 29.5, direction="up"),
         anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")],
    )
    r = await agent.diagnose(_req(
        primary_metric="metric.cac",         # mission is about CAC
        lead_domain="technical_agent",       # but leadership moved downstream
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    assert r.outcome_metric == "metric.purchase_cvr"
    assert r.finding is not None and r.finding.causal_confidence != "REJECTED"


# --------------------------------------------------------------------------- #
# 5. control divergence: a common shock (control moved too) weakens the hypothesis
# --------------------------------------------------------------------------- #
async def test_control_divergence_flags_common_shock(make_agent):
    bundle = latency_bundle()
    for row in bundle:
        if row.get("dimensions", {}).get("device") == "desktop":
            row["change_pct"] = -22.0   # control moved almost as much
    agent = make_agent(bundle, [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")])
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    latency_h = next(h for h in r.hypotheses if h.treatment_metric == "metric.mobile_lcp_seconds")
    cd = next(t for t in latency_h.test_results if t.kind == "control_divergence")
    assert cd.passed is False


# --------------------------------------------------------------------------- #
# 6. diagnostic artifact + claim shapes are what the Skeptic consumes
# --------------------------------------------------------------------------- #
async def test_emits_skeptic_ready_contracts(make_agent):
    agent = make_agent(
        latency_bundle(),
        [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")],
    )
    r = await agent.diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    da = r.diagnostic_artifact
    assert da is not None
    assert da.retained_hypotheses and da.causal_ref == r.causal_artifact.causal_id
    assert da.methodology
    ca = r.causal_artifact
    assert ca.treatment == "metric.mobile_lcp_seconds" and ca.outcome == "metric.purchase_cvr"
    assert ca.treatment_started_at and ca.outcome_started_at
    claim = r.claims[0]
    assert claim.claim_type == "causal"
    assert claim.causal_refs == [ca.causal_id]
    assert claim.diagnostic_refs == [r.diagnostic_run_id]
    assert claim.metadata["alternatives_ruled_out"] is True


# --------------------------------------------------------------------------- #
# 7. determinism (no reasoning model)
# --------------------------------------------------------------------------- #
async def test_deterministic(make_agent):
    reqk = {
        "outcome_metric": "metric.purchase_cvr",
        "degradation_started_at": "2026-09-01T12:05:00+05:30",
        "context": {"trust_metadata_causal": True},
    }
    an = [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")]
    r1 = await make_agent(latency_bundle(), an).diagnose(_req(**reqk))
    r2 = await make_agent(latency_bundle(), an).diagnose(_req(**reqk))
    assert [h.statement for h in r1.hypotheses] == [h.statement for h in r2.hypotheses]
    assert [h.status for h in r1.hypotheses] == [h.status for h in r2.hypotheses]
    assert (r1.finding.causal_confidence if r1.finding else None) == (
        r2.finding.causal_confidence if r2.finding else None
    )


# --------------------------------------------------------------------------- #
# 8. constrained LLM enrichment stays bounded to known metrics
# --------------------------------------------------------------------------- #
async def test_llm_enrichment_is_bounded(make_agent, graphs):
    from seleric_swarm.agents.diagnostic import DiagnosticDeps
    from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent
    from seleric_swarm.agents.diagnostic.hypotheses.generator import _LLMHypo, _LLMHypoList
    from seleric_swarm.agents.diagnostic.reasoning import ScriptedReasoningModel
    from seleric_swarm.agents.diagnostic.registries import (
        InMemoryAnomalyRepository,
        InMemoryEvidenceRepository,
        TemplateCausalEstimationService,
    )
    from tests.diagnostic.conftest import CAC_TRUTH

    scripted = ScriptedReasoningModel(
        structured=[
            _LLMHypoList(hypotheses=[
                _LLMHypo(statement="Aliens changed the weather", treatment_metric="metric.cosmic_rays"),  # rejected: unknown metric
                _LLMHypo(statement="Payment failures rose sharply", treatment_metric="metric.payment_failure_rate"),  # unknown metric here -> rejected
                _LLMHypo(statement="JS errors from the deploy", treatment_metric="metric.js_error_rate"),  # accepted: observed
            ])
        ]
    )
    deps = DiagnosticDeps(
        evidence_repo=InMemoryEvidenceRepository(latency_bundle()),
        anomaly_repo=InMemoryAnomalyRepository([anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")]),
        causal_graphs=graphs,
        causal_service=TemplateCausalEstimationService(CAC_TRUTH),
        reasoning=scripted,
    )
    r = await DiagnosticAgent(deps=deps).diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    llm_hyps = [h for h in r.hypotheses if h.llm_generated]
    assert all(h.treatment_metric in {"metric.js_error_rate"} for h in llm_hyps)
    assert not any("aliens" in h.statement.lower() for h in r.hypotheses)
    # the LLM's "JS errors from the deploy" describes the same mechanism as the
    # template's js_error_rate -> purchase_cvr hypothesis (same treatment/outcome,
    # different wording) - it must collapse to one hypothesis, not two.
    js_error_hyps = [h for h in r.hypotheses if h.treatment_metric == "metric.js_error_rate"]
    assert len(js_error_hyps) == 1


# --------------------------------------------------------------------------- #
# 8b. semantically-equivalent hypotheses (same mechanism, different wording)
#     dedupe to one hypothesis regardless of phrasing
# --------------------------------------------------------------------------- #
async def test_semantically_equivalent_hypotheses_are_deduplicated(make_agent, graphs):
    from seleric_swarm.agents.diagnostic import DiagnosticDeps
    from seleric_swarm.agents.diagnostic.agent import DiagnosticAgent
    from seleric_swarm.agents.diagnostic.hypotheses.generator import _LLMHypo, _LLMHypoList
    from seleric_swarm.agents.diagnostic.reasoning import ScriptedReasoningModel
    from seleric_swarm.agents.diagnostic.registries import (
        InMemoryAnomalyRepository,
        InMemoryEvidenceRepository,
        TemplateCausalEstimationService,
    )
    from tests.diagnostic.conftest import CAC_TRUTH

    scripted = ScriptedReasoningModel(
        structured=[
            _LLMHypoList(hypotheses=[
                _LLMHypo(
                    statement="Mobile frontend slowdown caused lower conversion",
                    treatment_metric="metric.mobile_lcp_seconds",
                ),
            ])
        ]
    )
    deps = DiagnosticDeps(
        evidence_repo=InMemoryEvidenceRepository(latency_bundle()),
        anomaly_repo=InMemoryAnomalyRepository(
            [anomaly("AN-cvr", "metric.purchase_cvr", -24.0, start_time="2026-09-01T12:05:00+05:30")]
        ),
        causal_graphs=graphs,
        causal_service=TemplateCausalEstimationService(CAC_TRUTH),
        reasoning=scripted,
    )
    r = await DiagnosticAgent(deps=deps).diagnose(_req(
        outcome_metric="metric.purchase_cvr",
        degradation_started_at="2026-09-01T12:05:00+05:30",
        context={"trust_metadata_causal": True},
    ))
    lcp_hyps = [h for h in r.hypotheses if h.treatment_metric == "metric.mobile_lcp_seconds"]
    assert len(lcp_hyps) == 1
