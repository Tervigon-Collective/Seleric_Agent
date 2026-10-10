"""Two-signal validator tests — docs/BUG_SHEET.md #12 ported forward (Sprint 3, C).

#12 is filed as **"INVESTIGATED, NOT A BUG"**: ``trust_label: STRONG`` alongside
``verdict: REVISE`` is intentional, coherent design — "the evidence we have is
solid, but there's an unresolved competing explanation we haven't ruled out
yet". ``03_PROFILE_CAPABILITIES.md`` §9 criterion (c)5 makes reproducing that
structure an exit criterion for this profile, because the obvious way to port
two functions is to collapse them into one score, which would destroy it.

Why the combination is *possible* at all, in numbers: the STRONG floor is 0.72
and ``revise_below`` is 0.55 (`toolsets/policy_config.py`, copied from
`config/skeptic_policies.yaml`). Anything clearing STRONG clears the verdict's
trust gate with room to spare, so a REVISE on a STRONG claim can only come from
a gap, an alternative, or a warning — never from the trust score. These tests
pin each of those three routes separately.

Offline: no `runtime` fixture, no MCP, no LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact, PredictionArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.agent.validation import AlternativeHypothesis, EvidenceValidator
from seleric_swarm.agent.scope import RequiredScope
from seleric_swarm.agent.validation.signals import (
    Challenge,
    CheckOutcome,
    EvidenceGap,
    check_contradiction,
    check_evidence,
    check_scope_coverage,
)
from seleric_swarm.agent.validation.trust import score_trust
from seleric_swarm.agent.validation.verdict import decide_verdict
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import policy_config as policy

_MISSION = "MS3-signals"


def _deps(store: InMemoryArtifactStore) -> SelericDeps:
    return SelericDeps(
        mission_id=_MISSION,
        as_of=datetime.now(UTC),
        principal=Principal(
            principal_id="p1",
            workspace_id="ws-1",
            user_id="user-1",
            authenticated=False,
            auth_method=PrincipalAuthMethod.ANONYMOUS,
        ),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store,
        limits=ExecutionLimits(),
    )


def _evidence(
    store: InMemoryArtifactStore,
    *,
    day: int,
    value: float | None = 100.0,
    metric: str = "metric.net_sales",
) -> str:
    stamp = datetime(2026, 9, day, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id=metric,
        grain="day",
        as_of=datetime.now(UTC),
        period_start=stamp,
        period_end=stamp,
        value=value,
        source_query={"measure": metric},
    )
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric}:{day}:{value}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    ).id


def _clean_series(store: InMemoryArtifactStore, *, count: int = 10) -> list[str]:
    """A series with no quality problems of its own, so any REVISE in a test
    below is unambiguously caused by the thing that test is about."""
    return [_evidence(store, day=d, value=100.0 + d) for d in range(1, count + 1)]


def _derived(
    store: InMemoryArtifactStore, artifact_type: str, payload: dict[str, Any], evidence_ids: list[str]
) -> str:
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type=artifact_type,
            payload=payload,
            classification="derived",
            evidence_ids=evidence_ids,
            provenance=ArtifactProvenance(
                evidence_ids=evidence_ids, calculation_version=f"{artifact_type}.v0"
            ),
            mission_id=_MISSION,
        )
    ).id


# ---- the headline: bug #12's exact shape, all three routes -------------------


# 0.8 lands inside the STRONG band [0.72, 0.9) once the default profile
# renormalizes onto evidence_quality alone (its only feeding dimension here).
# A perfect 1.0 would score VERIFIED, which is a *different* label and would not
# reproduce #12's reported shape.
_STRONG_BAND_SIGNAL = 0.8


def test_strong_trust_with_revise_verdict_via_unresolved_alternative():
    """#12's own trace shape (condition b): a solid claim with a competing
    explanation nobody ruled out. STRONG and REVISE, together, as separate
    fields.

    The evidence is deliberately imperfect-but-good: 5 rows (below the 8-row
    floor) with one null. Both cost trust — landing it in the STRONG band
    rather than VERIFIED — and both raise only *non-blocking* gaps, so the
    REVISE below is unambiguously caused by the alternative and nothing else.
    """
    store = InMemoryArtifactStore()
    for day in range(1, 5):
        _evidence(store, day=day, value=100.0 + day)
    _evidence(store, day=5, value=None)
    deps = _deps(store)

    outcome = EvidenceValidator().score(
        deps,
        alternatives=[
            AlternativeHypothesis(
                hypothesis="a pricing change, not demand, explains the move",
                status="open",
                priority=policy.ALT_PRIORITY_REVISE_FLOOR,
            )
        ],
    )

    assert outcome.trust_label == "STRONG"
    assert outcome.verdict == "REVISE"
    assert outcome.trust_score >= policy.TRUST_LABEL_THRESHOLDS["STRONG"]
    assert outcome.trust_score < policy.TRUST_LABEL_THRESHOLDS["VERIFIED"]
    assert any("unresolved alternative" in r for r in outcome.reasons)


def test_strong_trust_with_revise_verdict_via_blocking_gap():
    """Condition (a): a blocking evidence gap forces REVISE regardless of trust."""
    outcomes = [
        CheckOutcome(check="evidence", score_signals={"evidence_quality": _STRONG_BAND_SIGNAL})
    ]
    trust = score_trust(outcomes, [], claim_type="default")
    decision = decide_verdict(
        outcomes, [EvidenceGap(description="no control segment", blocking=True)], [], trust.score
    )

    assert trust.label == "STRONG"
    assert decision.verdict == "REVISE"


def test_strong_trust_with_revise_verdict_via_source_conflict_warning():
    """Condition (c): a warning in REVISE_CATEGORIES. 'source' is in the set."""
    outcomes = [
        CheckOutcome(
            check="contradiction",
            score_signals={"evidence_quality": _STRONG_BAND_SIGNAL},
            challenges=[
                Challenge(
                    category="source",
                    severity="warning",
                    description="two sources disagree by 12%",
                    contradiction_type="source_conflict",
                )
            ],
        )
    ]
    trust = score_trust(outcomes, [], claim_type="default")
    decision = decide_verdict(outcomes, [], [], trust.score)

    assert trust.label == "STRONG"
    assert decision.verdict == "REVISE"


def test_the_two_signals_are_structurally_independent():
    """score_trust's signature cannot see a verdict, and decide_verdict sees
    trust only as a float — never the label or components. This is what keeps
    #12 possible; a merged score would make it unrepresentable."""
    import inspect

    trust_params = set(inspect.signature(score_trust).parameters)
    assert not trust_params & {"verdict", "decision", "gaps"}

    verdict_params = inspect.signature(decide_verdict).parameters
    # String compare: `from __future__ import annotations` keeps annotations lazy.
    assert verdict_params["trust_score"].annotation == "float"
    assert "trust_label" not in verdict_params
    assert "trust_components" not in verdict_params


def test_thresholds_leave_room_for_strong_plus_revise():
    """The mechanism itself. If STRONG ever dropped below revise_below, every
    STRONG claim would auto-PASS the trust gate for a different reason and #12's
    shape would silently become unreachable."""
    assert policy.TRUST_LABEL_THRESHOLDS["STRONG"] > policy.TRUST_REVISE_BELOW


# ---- verdict precedence and the REJECT path ---------------------------------


def test_blocking_challenge_rejects_and_does_not_merely_revise():
    outcomes = [
        CheckOutcome(
            check="evidence",
            status="REJECTED",
            challenges=[
                Challenge(category="evidence", severity="blocking", description="payload invalid")
            ],
        )
    ]
    assert decide_verdict(outcomes, [], [], 0.99).verdict == "REJECT"


def test_blocking_failure_caps_trust_no_matter_how_good_the_signals():
    """trust_score.py:79-82 — the one place verdict-shaped information touches
    trust, and it is one-directional."""
    outcomes = [
        CheckOutcome(
            check="evidence",
            score_signals={"evidence_quality": 1.0, "provenance_completeness": 1.0},
            challenges=[
                Challenge(category="evidence", severity="blocking", description="contradicted")
            ],
        )
    ]
    trust = score_trust(outcomes, [], claim_type="default")
    assert trust.score == policy.TRUST_BLOCKING_CAP


def test_low_priority_alternative_does_not_force_revise():
    """AlternativeHypothesis defaults to priority 5, below the >=6 floor —
    matching agents/skeptic/contracts.py. Only a deliberately raised priority
    triggers REVISE."""
    outcomes = [CheckOutcome(check="evidence", score_signals={"evidence_quality": 1.0})]
    trust = score_trust(outcomes, [], claim_type="default")
    decision = decide_verdict(
        outcomes, [], [AlternativeHypothesis(hypothesis="a long shot", status="open")], trust.score
    )
    assert decision.verdict == "PASS"


def test_excluded_warning_categories_do_not_force_revise():
    """REVISE_CATEGORIES deliberately omits evidence/provenance/strategy.
    Widening it would silently convert PASSes into REVISEs."""
    for category in ("evidence", "provenance", "alternative_hypothesis", "strategy"):
        outcomes = [
            CheckOutcome(
                check="x",
                score_signals={"evidence_quality": 1.0},
                challenges=[
                    Challenge(category=category, severity="warning", description="minor note")
                ],
            )
        ]
        trust = score_trust(outcomes, [], claim_type="default")
        assert decide_verdict(outcomes, [], [], trust.score).verdict == "PASS", category


# ---- ported trust arithmetic ------------------------------------------------


def test_duplicate_signals_merge_by_min_not_mean():
    """Deliberately skeptical: one check's low reading is not averaged away."""
    outcomes = [
        CheckOutcome(check="a", score_signals={"evidence_quality": 1.0}),
        CheckOutcome(check="b", score_signals={"evidence_quality": 0.2}),
    ]
    trust = score_trust(outcomes, [], claim_type="default")
    assert trust.components["evidence_quality"] == pytest.approx(0.2)


def test_dimension_with_no_feeding_signal_is_dropped_not_zeroed():
    """The causal profile weights temporal_validity and graph_plausibility at
    0.15 each, and V3 has no check that computes them. They must drop out of
    the weight total — scoring them 0 would punish a claim for a check this
    system cannot perform."""
    outcomes = [
        CheckOutcome(
            check="causal",
            score_signals={"refutation_robustness": 1.0, "estimator_validity": 1.0},
        )
    ]
    trust = score_trust(outcomes, [], claim_type="causal")

    assert "temporal_validity" not in trust.components
    assert "graph_plausibility" not in trust.components
    # Renormalized over the dimensions that did feed, so a perfect score on
    # every computable dimension is still 1.0 -- not 0.35.
    assert trust.score == pytest.approx(1.0)


def test_alt_elimination_is_one_when_there_are_no_alternatives():
    outcomes = [CheckOutcome(check="causal", score_signals={"refutation_robustness": 1.0})]
    assert score_trust(outcomes, [], claim_type="causal").components["alternative_elimination"] == 1.0


def test_alt_elimination_scales_with_open_fraction():
    outcomes = [CheckOutcome(check="causal", score_signals={"refutation_robustness": 1.0})]
    alts = [
        AlternativeHypothesis(hypothesis="a", status="open"),
        AlternativeHypothesis(hypothesis="b", status="eliminated"),
    ]
    trust = score_trust(outcomes, alts, claim_type="causal")
    assert trust.components["alternative_elimination"] == pytest.approx(0.5)


# ---- V3-native checks -------------------------------------------------------


def test_mission_with_no_artifacts_has_nothing_to_validate():
    """Rule 6 binds numerical claims to evidence; a mission that made no claim
    owes none. NOT_APPLICABLE, not a failure."""
    assert check_evidence([]).status == "NOT_APPLICABLE"


def test_derived_artifact_with_no_backing_evidence_is_a_blocking_gap():
    """A finding citing evidence that isn't in the store is a rule-6 problem,
    but REVISE rather than REJECT — the claim might be true, the evidence is
    incomplete."""
    store = InMemoryArtifactStore()
    _derived(store, "finding", {"finding_type": "anomaly"}, ["ev-missing"])
    outcome = EvidenceValidator().score(_deps(store))

    assert outcome.verdict == "REVISE"
    assert any("no evidence artifacts" in r for r in outcome.reasons)


def test_forecast_input_backs_derived_forecast_artifacts():
    """Live 2026-10-10 (MS3-66cc3bb5f2): Chronos stored forecast_input + prediction
    with no Cube evidence rows; check_evidence treated that as a blocking gap and
    forced a revision that abandoned the forecast."""
    store = InMemoryArtifactStore()
    store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="forecast_input",
            payload={"frame": {"content_hash": "abc"}, "content_hash": "abc"},
            classification="factual",
            evidence_ids=["forecast_frame:abc"],
            provenance=ArtifactProvenance(evidence_ids=["forecast_frame:abc"]),
            mission_id=_MISSION,
        )
    )
    _derived(
        store,
        "prediction",
        {
            "model_id": "chronos",
            "model_version": "1",
            "prediction_type": "forecast",
            "value": 312500.0,
            "confidence_interval": [198500.0, 500100.0],
            "evidence_ids": ["forecast_input"],
            "feature_leakage_checked": True,
        },
        ["forecast_input"],
    )
    outcome = check_evidence(store.list_for_mission(_MISSION))
    assert outcome.status == "OK"
    scored = EvidenceValidator().score(_deps(store))
    assert scored.verdict != "REVISE" or not any("no evidence artifacts" in r for r in scored.reasons)


def test_a_plan_artifact_alone_is_not_a_claim_and_does_not_fail_the_mission():
    """Live bug: a planned mission that answered without fetching data (e.g.
    "thanks!") carried a `plan` artifact, which was scored as a derived claim
    with no evidence and failed closed as INSUFFICIENT_EVIDENCE."""
    store = InMemoryArtifactStore()
    store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="plan",
            payload={"plan": "1. greet", "intent": "chat"},
            classification="ui",
            mission_id=_MISSION,
        )
    )
    outcome = EvidenceValidator().score(_deps(store))

    assert outcome.ok
    assert outcome.verdict == "PASS"


def test_contradiction_between_two_sources_for_the_same_day():
    store = InMemoryArtifactStore()
    _evidence(store, day=1, value=100.0)
    _evidence(store, day=1, value=140.0)
    artifacts = store.list_for_mission(_MISSION)

    outcome = check_contradiction(artifacts)

    assert outcome.challenges
    challenge = outcome.challenges[0]
    assert challenge.category == "source"
    assert challenge.severity == "warning"
    # Explicit field, not smuggled through detail["contradiction_type"] the way
    # agents/skeptic/contracts.py does it.
    assert challenge.contradiction_type == "source_conflict"


def test_agreeing_sources_are_not_flagged():
    store = InMemoryArtifactStore()
    _evidence(store, day=1, value=100.0)
    _evidence(store, day=1, value=101.0)  # 1%, under the 5% tolerance
    assert not check_contradiction(store.list_for_mission(_MISSION)).challenges


def test_causal_claim_short_on_refutations_warns_and_revises():
    """Rule 19: CAUSALLY_SUPPORTED needs refutation. The schema requires a
    non-empty list; this checks the count floor on top of it."""
    store = InMemoryArtifactStore()
    evidence_ids = _clean_series(store)
    causal = CausalArtifact(
        query={"treatment": "t", "outcome": "o"},
        evidence_classification="CAUSALLY_SUPPORTED",
        effect_estimate=1.0,
        refutation_checks=[{"name": "placebo", "passed": True}],  # 1 < MIN_REFUTATIONS
        evidence_ids=evidence_ids,
        method="backdoor.linear_regression",
    )
    _derived(store, "causal", causal.model_dump(mode="json"), evidence_ids)

    outcome = EvidenceValidator().score(_deps(store))

    assert outcome.verdict == "REVISE"
    assert any("refutation" in r for r in outcome.reasons)


def test_prediction_without_leakage_check_warns():
    store = InMemoryArtifactStore()
    evidence_ids = _clean_series(store)
    prediction = PredictionArtifact(
        model_id="forecast.orders.daily",
        model_version="1",
        prediction_type="forecast",
        value=123.0,
        confidence_interval=(100.0, 150.0),
        evidence_ids=evidence_ids,
        feature_leakage_checked=False,
    )
    _derived(store, "prediction", prediction.model_dump(mode="json"), evidence_ids)

    outcome = EvidenceValidator().score(_deps(store))

    assert outcome.verdict == "REVISE"
    assert any("feature-leakage" in r for r in outcome.reasons)


# -- requested time windows -----------------------------------------------------
#
# Live 2026-10-06 (MS3-167d9f4838): the question compared "the last 3 days versus
# today"; the resolver dropped the second window, nothing reconciled the periods,
# and the mission shipped the 3-day half as `completed`. `RequiredScope` had no
# field to hold a second window, so the gate below is what closes it.


def _span(store: InMemoryArtifactStore, *, start: int, end: int, grain: str = "none") -> str:
    payload = EvidenceArtifact(
        metric_id="metric.net_sales",
        grain=grain,  # type: ignore[arg-type]
        as_of=datetime.now(UTC),
        period_start=datetime(2026, 10, start, tzinfo=UTC),
        period_end=datetime(2026, 10, end, tzinfo=UTC),
        value=100.0,
        source_query={"measure": "metric.net_sales"},
    )
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{start}:{end}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    ).id


def _incident_scope() -> Any:
    from seleric_swarm.agent.scope import required_windows_from_resolved
    from seleric_swarm.services.time_range import window_from_query

    return RequiredScope(
        windows=required_windows_from_resolved(
            window_from_query(
                "the last 3 days versus today", "Asia/Kolkata", "2026-10-06"
            )
        )
    )


def test_a_comparison_question_missing_one_window_is_a_blocking_gap() -> None:
    """The incident: 10-03..10-05 fetched, 10-06 never fetched."""
    store = InMemoryArtifactStore()
    _span(store, start=3, end=5)
    outcome = check_scope_coverage(store.list_for_mission(_MISSION), _incident_scope())
    assert outcome.status == "INSUFFICIENT"
    blocking = [g for g in outcome.gaps if g.blocking]
    assert len(blocking) == 1
    assert "2026-10-06" in blocking[0].description


@pytest.mark.parametrize(
    "spans",
    [
        pytest.param([(3, 5), (6, 6)], id="both_windows_fetched_separately"),
        pytest.param([(3, 6)], id="one_fetch_spanning_both"),
        pytest.param([(3, 3), (4, 4), (5, 5), (6, 6)], id="day_grained_series"),
    ],
)
def test_evidence_covering_both_windows_is_accepted(spans: list[tuple[int, int]]) -> None:
    store = InMemoryArtifactStore()
    for start, end in spans:
        _span(store, start=start, end=end, grain="day" if start != end else "none")
    outcome = check_scope_coverage(store.list_for_mission(_MISSION), _incident_scope())
    assert outcome.status == "OK", outcome.gaps
    assert not outcome.gaps


def test_a_hole_in_the_evidence_does_not_count_as_covering_a_window() -> None:
    """10-03 and 10-05 with no 10-04 is a gap, not a 3-day window.

    The gap names the window that is not covered, not the missing day inside it.
    """
    store = InMemoryArtifactStore()
    for day in (3, 5, 6):
        _span(store, start=day, end=day, grain="day")
    outcome = check_scope_coverage(store.list_for_mission(_MISSION), _incident_scope())
    assert outcome.status == "INSUFFICIENT"
    # Only the 3-day window is unsatisfied; 10-06 is covered by its own row.
    assert [g.description for g in outcome.gaps if g.blocking] == [
        g.description for g in outcome.gaps if g.blocking and "2026-10-03..2026-10-05" in g.description
    ]
    assert sum(1 for g in outcome.gaps if g.blocking) == 1


def test_a_single_window_question_is_not_gated_on_a_second_period() -> None:
    store = InMemoryArtifactStore()
    # "last 7 days" from as_of 2026-10-06 is 2026-09-29..2026-10-05: it starts in
    # SEPTEMBER, which is why this fixture cannot reuse the October-only _span.
    stamp_s, stamp_e = datetime(2026, 9, 29, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC)
    payload = EvidenceArtifact(
        metric_id="metric.net_sales",
        grain="none",
        as_of=datetime.now(UTC),
        period_start=stamp_s,
        period_end=stamp_e,
        value=100.0,
        source_query={"measure": "metric.net_sales"},
    )
    store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=["raw:7d"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    )
    from seleric_swarm.agent.scope import required_windows_from_resolved
    from seleric_swarm.services.time_range import window_from_query

    scope = RequiredScope(
        windows=required_windows_from_resolved(
            window_from_query("total ad spend in the last 7 days", "Asia/Kolkata", "2026-10-06")
        )
    )
    assert check_scope_coverage(store.list_for_mission(_MISSION), scope).status == "OK"
