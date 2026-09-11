"""Diagnostic Agent behaviour suite."""

from __future__ import annotations

from seleric_swarm.agents.diagnostic import DiagnosticRequest
from tests.diagnostic.conftest import MISSION, anomaly, ev, matching_truth

_OUTCOME = "metric.purchase_cvr"
_TREATMENT = "metric.spend"  # registered, performance-owned
_ALT = "metric.ctr"  # registered, also observed — competing co-mover
_START = "2026-09-01T11:47:00+05:30"
_DEG = "2026-09-01T12:05:00+05:30"


def _req(**kw) -> DiagnosticRequest:
    kw.setdefault("mission_id", MISSION)
    kw.setdefault("question", "Why did purchase conversion drop?")
    return DiagnosticRequest(**kw)


def _cvr_evidence(*, treatment_start: str = _START, sample_size: int | None = None) -> list[dict]:
    """Observed co-movers for a CVR drop. Metrics come from the registry, not a story list."""
    return [
        ev("EV-cvr", _OUTCOME, 2.35, change_pct=-24.0),
        ev("EV-mcvr", _OUTCOME, 2.03, change_pct=-31.0, dims={"device": "mobile"}),
        ev("EV-dcvr", _OUTCOME, 3.30, change_pct=-3.0, dims={"device": "desktop", "segment": "control"}),
        ev("EV-treat", _TREATMENT, 50_000, change_pct=40.0, start_time=treatment_start, sample_size=sample_size),
        ev("EV-alt", _ALT, 0.8, change_pct=-35.0, start_time=treatment_start),
    ]


def _cvr_anoms(*, treatment_start: str = _START) -> list[dict]:
    return [
        anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG),
        anomaly("AN-t", _TREATMENT, 40.0, direction="up", start_time=treatment_start),
    ]


def _retain_truth() -> dict:
    return matching_truth(_TREATMENT, _OUTCOME)


# --------------------------------------------------------------------------- #
# 1. observed co-mover that the estimator matches is retained
# --------------------------------------------------------------------------- #
async def test_matching_observed_treatment_is_retained(make_agent):
    agent = make_agent(_cvr_evidence(), _cvr_anoms(), causal_truth=_retain_truth())
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    assert r.finding is not None
    assert r.finding.causal_confidence in {"CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS", "STRONGLY_SUPPORTED"}
    retained = r.retained()
    assert len(retained) == 1 and retained[0].treatment_metric == _TREATMENT
    assert "spend" in r.finding.statement.lower()
    treatments = {h.treatment_metric for h in r.hypotheses}
    assert _ALT in treatments
    assert r.claims and r.claims[0].claim_type == "causal"
    assert all(c.severity in {"info", "warning", "blocking"} for c in r.contradictions)
    assert any("confounding" in lim.lower() for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 1a. retained mechanism's domain differs from the current lead ->
#     leadership transfer is *recommended*, not executed; incident_type set
# --------------------------------------------------------------------------- #
async def test_leadership_transfer_recommended_and_incident_type_set(make_agent):
    agent = make_agent(_cvr_evidence(), _cvr_anoms(), causal_truth=_retain_truth())
    r = await agent.diagnose(_req(
        primary_metric="metric.cac",
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        lead_domain="funnel_agent",
        context={"trust_metadata_causal": True},
    ))
    assert r.leadership_transfer_recommended is True
    assert r.recommended_domain_lead == "performance_agent"
    assert r.leadership_transfer_reason and "funnel" in r.leadership_transfer_reason
    assert r.incident_type == "performance"


async def test_no_leadership_transfer_when_lead_already_owns_mechanism(make_agent):
    agent = make_agent(_cvr_evidence(), _cvr_anoms(), causal_truth=_retain_truth())
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        lead_domain="performance_agent",
        context={"trust_metadata_causal": True},
    ))
    assert r.leadership_transfer_recommended is False
    assert r.recommended_domain_lead is None


async def test_no_leadership_transfer_when_lead_owns_asked_metric(make_agent):
    """A funnel co-mover driving ROAS is a finding; performance keeps the question."""
    agent = make_agent(
        [ev("EV-roas", "metric.gross_roas", 2.4, change_pct=15.0),
         ev("EV-co", "metric.checkout_rate", 0.4, change_pct=37.0, start_time=_START)],
        [anomaly("AN-roas", "metric.gross_roas", 15.0, direction="up"),
         anomaly("AN-co", "metric.checkout_rate", 37.0, direction="up", start_time=_START)],
        causal_truth=matching_truth("metric.checkout_rate", "metric.gross_roas"),
    )
    r = await agent.diagnose(_req(
        primary_metric="metric.gross_roas",
        outcome_metric="metric.gross_roas",
        degradation_started_at=_DEG,
        lead_domain="performance_agent",
        context={"trust_metadata_causal": True},
    ))
    retained = r.retained()
    assert retained and retained[0].treatment_metric == "metric.checkout_rate"
    assert r.incident_type == "funnel"
    assert r.leadership_transfer_recommended is False


# --------------------------------------------------------------------------- #
# 1b. a reported (not merely absent) small sample size downgrades support via
#     the shared deterministic statistics service - never treated as zero
# --------------------------------------------------------------------------- #
async def test_reported_small_sample_size_downgrades_evidence_sufficiency(make_agent):
    agent = make_agent(
        _cvr_evidence(sample_size=5),
        _cvr_anoms(),
        causal_truth=_retain_truth(),
    )
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    treat_h = next(h for h in r.hypotheses if h.treatment_metric == _TREATMENT)
    ev_check = next(t for t in treat_h.test_results if t.kind == "evidence_sufficiency")
    assert ev_check.passed is False
    assert "sample_size_check" in ev_check.detail
    assert ev_check.detail["sample_size_check"]["sample_size"] == 5


# --------------------------------------------------------------------------- #
# 2. impossible temporal ordering -> hypothesis rejected on the hard gate
# --------------------------------------------------------------------------- #
async def test_temporal_reversal_rejects_hypothesis(make_agent):
    late = "2026-09-01T15:05:00+05:30"
    agent = make_agent(
        _cvr_evidence(treatment_start=late),
        [anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)],
        causal_truth=_retain_truth(),
    )
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    treat_h = next(h for h in r.hypotheses if h.treatment_metric == _TREATMENT)
    assert treat_h.status == "rejected"
    assert "hard gate" in (treat_h.rejection_reason or "")
    blocking = [c for c in r.contradictions if c.hypothesis_id == treat_h.hypothesis_id]
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

    class _SlowReasoningModel:
        async def generate_structured(self, *, system, user, schema, tags=None):
            await asyncio.sleep(5)
            raise AssertionError("should have timed out before returning")

    deps = DiagnosticDeps(
        evidence_repo=InMemoryEvidenceRepository(_cvr_evidence()),
        anomaly_repo=InMemoryAnomalyRepository(_cvr_anoms()),
        causal_service=TemplateCausalEstimationService(_retain_truth()),
        reasoning=_SlowReasoningModel(),
    )
    policies = DiagnosticPolicies(raw={"budgets": {"max_runtime_seconds": 1, "max_hypotheses": 20}})
    r = await DiagnosticAgent(deps=deps, policies=policies).diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
    ))
    assert r.methodology == "budget_exhausted"
    assert any("runtime budget" in lim for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 3. no causal artifact match -> metadata-only, capped, inconclusive
# --------------------------------------------------------------------------- #
async def test_metadata_only_is_capped_inconclusive(make_agent):
    agent = make_agent(_cvr_evidence(), [anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)])
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        # NOTE: no trust_metadata_causal and no matching_truth -> ceiling applies
    ))
    assert r.retained() == []
    assert r.finding is None or r.finding.retained_hypothesis_id is None
    assert r.leadership_transfer_recommended is False
    assert any("metadata-only" in lim.lower() for lim in r.limitations)


# --------------------------------------------------------------------------- #
# 4. outcome pivots to the downstream frontier when leadership moved
# --------------------------------------------------------------------------- #
async def test_outcome_pivots_to_downstream_frontier(make_agent):
    agent = make_agent(
        _cvr_evidence() + [ev("EV-cac", "metric.cac", 782.0, change_pct=29.5)],
        [anomaly("AN-cac", "metric.cac", 29.5, direction="up"),
         anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)],
        causal_truth=_retain_truth(),
    )
    r = await agent.diagnose(_req(
        primary_metric="metric.cac",
        lead_domain="technical_agent",
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    assert r.outcome_metric == _OUTCOME
    assert r.finding is not None and r.finding.causal_confidence != "REJECTED"


async def test_commerce_keeps_asked_gross_sales_not_net_sales_sibling(make_agent):
    """Asked metric stays even when a louder sibling KPI (net_sales) is also anomalous.

    Candidates come from observed co-movers, not a per-metric YAML story list.
    """
    agent = make_agent(
        [ev("EV-gs", "metric.gross_sales", 120.0, change_pct=20.0),
         ev("EV-ns", "metric.net_sales", 40.0, change_pct=-60.0),
         ev("EV-cvr", _OUTCOME, 0.03, change_pct=11.0)],
        [anomaly("AN-gs", "metric.gross_sales", 20.0, direction="up"),
         anomaly("AN-ns", "metric.net_sales", -60.0, direction="down"),
         anomaly("AN-cvr", _OUTCOME, 11.0, direction="up")],
    )
    r = await agent.diagnose(_req(
        primary_metric="metric.gross_sales",
        lead_domain="commerce_agent",
        question="Why has gs increased over the last three days?",
    ))
    assert r.outcome_metric == "metric.gross_sales"
    treatments = {h.treatment_metric for h in r.hypotheses}
    assert "metric.net_sales" in treatments
    assert _OUTCOME in treatments
    assert r.hypotheses, "observed co-movers must seed hypotheses without a YAML outcome entry"


# --------------------------------------------------------------------------- #
# 5. control divergence: a common shock (control moved too) weakens the hypothesis
# --------------------------------------------------------------------------- #
async def test_control_divergence_flags_common_shock(make_agent):
    bundle = _cvr_evidence()
    for row in bundle:
        if row.get("dimensions", {}).get("device") == "desktop":
            row["change_pct"] = -22.0
    agent = make_agent(
        bundle,
        [anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)],
        causal_truth=_retain_truth(),
    )
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    treat_h = next(h for h in r.hypotheses if h.treatment_metric == _TREATMENT)
    cd = next(t for t in treat_h.test_results if t.kind == "control_divergence")
    assert cd.passed is False


# --------------------------------------------------------------------------- #
# 6. diagnostic artifact + claim shapes are what the Skeptic consumes
# --------------------------------------------------------------------------- #
async def test_emits_skeptic_ready_contracts(make_agent):
    agent = make_agent(
        _cvr_evidence(),
        [anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)],
        causal_truth=_retain_truth(),
    )
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    da = r.diagnostic_artifact
    assert da is not None
    assert da.retained_hypotheses and da.causal_ref == r.causal_artifact.causal_id
    assert da.methodology
    ca = r.causal_artifact
    assert ca.treatment == _TREATMENT and ca.outcome == _OUTCOME
    assert ca.treatment_started_at and ca.outcome_started_at
    claim = r.claims[0]
    assert claim.claim_type == "causal"
    assert claim.causal_refs == [ca.causal_id]
    assert claim.diagnostic_refs == [r.diagnostic_run_id]
    assert "alternatives_ruled_out" in claim.metadata


# --------------------------------------------------------------------------- #
# 7. determinism (no reasoning model)
# --------------------------------------------------------------------------- #
async def test_deterministic(make_agent):
    reqk = {
        "outcome_metric": _OUTCOME,
        "degradation_started_at": _DEG,
        "context": {"trust_metadata_causal": True},
    }
    an = [anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)]
    r1 = await make_agent(_cvr_evidence(), an, causal_truth=_retain_truth()).diagnose(_req(**reqk))
    r2 = await make_agent(_cvr_evidence(), an, causal_truth=_retain_truth()).diagnose(_req(**reqk))
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

    scripted = ScriptedReasoningModel(
        structured=[
            _LLMHypoList(hypotheses=[
                _LLMHypo(statement="Aliens changed the weather", treatment_metric="metric.cosmic_rays"),
                _LLMHypo(statement="Orders moved on their own", treatment_metric="metric.orders"),
                _LLMHypo(statement="CTR declined alongside conversion", treatment_metric=_ALT),
            ])
        ]
    )
    deps = DiagnosticDeps(
        evidence_repo=InMemoryEvidenceRepository(_cvr_evidence()),
        anomaly_repo=InMemoryAnomalyRepository([anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)]),
        causal_graphs=graphs,
        causal_service=TemplateCausalEstimationService(_retain_truth()),
        reasoning=scripted,
    )
    r = await DiagnosticAgent(deps=deps).diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    llm_hyps = [h for h in r.hypotheses if h.llm_generated]
    assert all(h.treatment_metric in {_ALT} for h in llm_hyps)
    assert not any("aliens" in h.statement.lower() for h in r.hypotheses)
    alt_hyps = [h for h in r.hypotheses if h.treatment_metric == _ALT]
    assert len(alt_hyps) == 1


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

    scripted = ScriptedReasoningModel(
        structured=[
            _LLMHypoList(hypotheses=[
                _LLMHypo(
                    statement="Rising media spend inflated acquisition cost and cut conversion",
                    treatment_metric=_TREATMENT,
                ),
            ])
        ]
    )
    deps = DiagnosticDeps(
        evidence_repo=InMemoryEvidenceRepository(_cvr_evidence()),
        anomaly_repo=InMemoryAnomalyRepository(
            [anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)]
        ),
        causal_graphs=graphs,
        causal_service=TemplateCausalEstimationService(_retain_truth()),
        reasoning=scripted,
    )
    r = await DiagnosticAgent(deps=deps).diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    treat_hyps = [h for h in r.hypotheses if h.treatment_metric == _TREATMENT]
    assert len(treat_hyps) == 1


# --------------------------------------------------------------------------- #
# 9. multiple contributors: a real-but-weaker mechanism is reported alongside
#    the primary, ranked below it, and never fabricated into a validated claim
# --------------------------------------------------------------------------- #
async def test_multiple_contributors_ranked_not_forced_to_one(make_agent):
    from seleric_swarm.agents.diagnostic.policies import DiagnosticPolicies

    bundle = _cvr_evidence() + [
        ev("EV-cpc", "metric.cpc", 12.0, change_pct=80.0, start_time=_START),
    ]
    agent = make_agent(
        bundle,
        [anomaly("AN-cvr", _OUTCOME, -24.0, start_time=_DEG)],
        causal_truth=_retain_truth(),
    )
    agent.policies = DiagnosticPolicies(raw={"budgets": {"max_primary_candidates": 4}})
    r = await agent.diagnose(_req(
        outcome_metric=_OUTCOME,
        degradation_started_at=_DEG,
        context={"trust_metadata_causal": True},
    ))
    assert len(r.findings) > 1
    assert r.findings[0].role == "primary"
    assert r.findings[0].retained_hypothesis_id is not None
    assert all(f.role != "primary" for f in r.findings[1:])
    assert all(
        f.retained_hypothesis_id is None
        for f in r.findings
        if f.role != "primary"
    )
    assert len(r.claims) == 1
    assert r.residual_unexplained is True
    assert r.finding is r.findings[0]
