"""Unit + contract tests for toolsets/causal.py and causal/service.py (Sprint 2 C).

Headline regression: docs/BUG_SHEET.md #6 — wider ``search_breadth`` searches
a larger space (history_days + candidate_cap), not a byte-identical re-run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.causal.dowhy_service import DoWhyEstimate, RefuterOutcome
from seleric_swarm.causal.service import classify_from_refutations, widening_for
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import causal


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore | None = None) -> SelericDeps:
    return SelericDeps(
        mission_id="mission-causal",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        principal=Principal(principal_id="p1", workspace_id="ws1", user_id="u1"),
        thread_id="thread-1",
        run_id="run-1",
        trace_id="trace-1",
        context=ContextBundle(),
        mcp_client=NullMcpClient(),
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(),
    )


def _put_evidence(
    store: InMemoryArtifactStore,
    *,
    metric: str,
    day: str,
    value: float,
) -> str:
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    evidence = EvidenceArtifact(
        metric_id=metric,
        dimensions={},
        grain="day",
        as_of=datetime(2026, 9, 18, tzinfo=UTC),
        period_start=start,
        period_end=start,
        value=value,
        source_query={"measure": metric},
    )
    artifact = store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="evidence",
            payload=evidence.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:{metric}:{day}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id="mission-causal",
        )
    )
    return artifact.id


def _series(
    store: InMemoryArtifactStore,
    metric: str,
    *,
    n: int = 30,
    end: datetime | None = None,
    base: float = 100.0,
    slope: float = 1.0,
) -> list[str]:
    end = end or datetime(2026, 9, 17, tzinfo=UTC)
    ids: list[str] = []
    for i in range(n):
        day = (end - timedelta(days=n - 1 - i)).date().isoformat()
        ids.append(_put_evidence(store, metric=metric, day=day, value=base + slope * i))
    return ids


# ---- bug #6: search_breadth widens history + candidate_cap --------------------


def test_search_breadth_widens_history_and_candidate_cap() -> None:
    """docs/BUG_SHEET.md #6 — wider breadth searches a larger space."""
    b0 = widening_for(0)
    b1 = widening_for(1)
    b2 = widening_for(2)
    assert b0.history_days == 30
    assert b1.history_days == 60
    assert b2.history_days == 90
    assert b1.candidate_cap > b0.candidate_cap
    assert b2.candidate_cap > b1.candidate_cap
    assert b1.history_days > b0.history_days


def test_estimate_effect_records_widened_caps_in_query(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryArtifactStore()
    eids = [
        *_series(store, "metric.spend", n=35, slope=2.0),
        *_series(store, "metric.net_sales", n=35, slope=1.5),
        *_series(store, "metric.sessions", n=35, slope=0.5),
    ]
    fake_est = DoWhyEstimate(
        treatment="metric.spend",
        outcome="metric.net_sales",
        effect=1.25,
        estimator="backdoor.linear_regression",
        common_causes=["metric.sessions"],
        refutations=[
            RefuterOutcome("placebo_treatment_refuter", 1.25, 0.01, True),
            RefuterOutcome("random_common_cause", 1.25, 1.20, True),
        ],
        n_rows=35,
    )
    mock_svc = MagicMock()
    mock_svc.estimate.return_value = fake_est
    monkeypatch.setattr("seleric_swarm.causal.service.DoWhyService", lambda: mock_svc)

    ctx = FakeRunContext(_deps(store))
    r0 = causal.estimate_effect(
        ctx, eids, "metric.spend", "metric.net_sales", search_breadth=0  # type: ignore[arg-type]
    )
    r1 = causal.estimate_effect(
        ctx, eids, "metric.spend", "metric.net_sales", search_breadth=1  # type: ignore[arg-type]
    )
    assert r0.success and r1.success
    q0 = CausalArtifact.model_validate(store.get(r0.artifact_ids[0]).payload).query
    q1 = CausalArtifact.model_validate(store.get(r1.artifact_ids[0]).payload).query
    assert q1["history_days"] > q0["history_days"]
    assert q1["candidate_cap"] > q0["candidate_cap"]


def test_search_breadth_does_not_tighten_min_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bug #6/#7: escalating breadth must not refuse evidence that passed at 0."""
    store = InMemoryArtifactStore()
    eids = [
        *_series(store, "metric.spend", n=30, slope=2.0),
        *_series(store, "metric.net_sales", n=30, slope=1.5),
        *_series(store, "metric.sessions", n=30, slope=0.5),
    ]
    fake_est = DoWhyEstimate(
        treatment="metric.spend",
        outcome="metric.net_sales",
        effect=1.0,
        estimator="backdoor.linear_regression",
        common_causes=["metric.sessions"],
        refutations=[
            RefuterOutcome("placebo_treatment_refuter", 1.0, 0.0, True),
            RefuterOutcome("random_common_cause", 1.0, 0.9, True),
        ],
        n_rows=30,
    )
    mock_svc = MagicMock()
    mock_svc.estimate.return_value = fake_est
    monkeypatch.setattr("seleric_swarm.causal.service.DoWhyService", lambda: mock_svc)

    ctx = FakeRunContext(_deps(store))
    r0 = causal.estimate_effect(
        ctx, eids, "metric.spend", "metric.net_sales", search_breadth=0  # type: ignore[arg-type]
    )
    r2 = causal.estimate_effect(
        ctx, eids, "metric.spend", "metric.net_sales", search_breadth=2  # type: ignore[arg-type]
    )
    assert r0.success and r2.success
    q0 = CausalArtifact.model_validate(store.get(r0.artifact_ids[0]).payload).query
    q2 = CausalArtifact.model_validate(store.get(r2.artifact_ids[0]).payload).query
    assert q2["history_days"] > q0["history_days"]
    assert q2["candidate_cap"] > q0["candidate_cap"]


# ---- envelope / preconditions -----------------------------------------------


def test_missing_evidence_returns_insufficient() -> None:
    ctx = FakeRunContext(_deps())
    result = causal.estimate_effect(
        ctx, [], "metric.spend", "metric.net_sales"  # type: ignore[arg-type]
    )
    assert not result.success
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.artifact_ids == []


def test_thin_history_returns_insufficient() -> None:
    store = InMemoryArtifactStore()
    eids = [
        *_series(store, "metric.spend", n=5),
        *_series(store, "metric.net_sales", n=5),
    ]
    ctx = FakeRunContext(_deps(store))
    result = causal.estimate_effect(
        ctx, eids, "metric.spend", "metric.net_sales"  # type: ignore[arg-type]
    )
    assert not result.success
    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert any("thin" in w or "policy:" in w for w in result.warnings)


def test_estimate_writes_causal_artifact_with_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryArtifactStore()
    eids = [
        *_series(store, "metric.spend", n=30, slope=2.0),
        *_series(store, "metric.net_sales", n=30, slope=1.5),
        *_series(store, "metric.sessions", n=30, slope=0.3),
    ]
    fake_est = DoWhyEstimate(
        treatment="metric.spend",
        outcome="metric.net_sales",
        effect=2.0,
        estimator="backdoor.linear_regression",
        common_causes=["metric.sessions"],
        refutations=[
            RefuterOutcome("placebo_treatment_refuter", 2.0, 0.0, True),
            RefuterOutcome("random_common_cause", 2.0, 1.9, True),
        ],
        n_rows=30,
    )
    mock_svc = MagicMock()
    mock_svc.estimate.return_value = fake_est
    monkeypatch.setattr("seleric_swarm.causal.service.DoWhyService", lambda: mock_svc)

    ctx = FakeRunContext(_deps(store))
    result = causal.estimate_effect(
        ctx, eids, "metric.spend", "metric.net_sales"  # type: ignore[arg-type]
    )
    assert result.success
    payload = CausalArtifact.model_validate(store.get(result.artifact_ids[0]).payload)
    assert payload.evidence_classification == "CAUSALLY_SUPPORTED"
    assert payload.refutation_checks
    assert payload.effect_estimate == 2.0


def test_classify_requires_refutations_for_causally_supported() -> None:
    assert (
        classify_from_refutations(effect=1.0, refutations=[], common_causes=["x"])
        == "ASSOCIATION"
    )
    assert (
        classify_from_refutations(
            effect=1.0,
            refutations=[{"name": "a", "passed": True}, {"name": "b", "passed": True}],
            common_causes=["x"],
        )
        == "CAUSALLY_SUPPORTED"
    )


def test_refute_estimate_rewrites_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryArtifactStore()
    eids = [
        *_series(store, "metric.spend", n=30),
        *_series(store, "metric.net_sales", n=30),
        *_series(store, "metric.sessions", n=30),
    ]
    prior = CausalArtifact(
        query={
            "treatment": "metric.spend",
            "outcome": "metric.net_sales",
            "search_breadth": 0,
            "method": "backdoor.linear_regression",
        },
        evidence_classification="ASSOCIATION",
        effect_estimate=None,
        refutation_checks=[],
        evidence_ids=eids,
        method="backdoor.linear_regression",
    )
    prior_id = store.put(
        Artifact(
            workspace_id="ws1",
            artifact_type="causal",
            payload=prior.model_dump(mode="json"),
            classification="derived",
            evidence_ids=eids,
            provenance=ArtifactProvenance(calculation_version="causal.v0"),
            mission_id="mission-causal",
        )
    ).id

    fake_est = DoWhyEstimate(
        treatment="metric.spend",
        outcome="metric.net_sales",
        effect=0.5,
        estimator="backdoor.linear_regression",
        common_causes=["metric.sessions"],
        refutations=[
            RefuterOutcome("placebo_treatment_refuter", 0.5, 0.01, True),
            RefuterOutcome("random_common_cause", 0.5, 0.48, True),
        ],
        n_rows=30,
    )
    mock_svc = MagicMock()
    mock_svc.estimate.return_value = fake_est
    monkeypatch.setattr("seleric_swarm.causal.service.DoWhyService", lambda: mock_svc)

    ctx = FakeRunContext(_deps(store))
    result = causal.refute_estimate(ctx, prior_id)  # type: ignore[arg-type]
    assert result.success
    new_payload = CausalArtifact.model_validate(store.get(result.artifact_ids[0]).payload)
    assert new_payload.query.get("refute_of") == prior_id
    assert new_payload.refutation_checks
