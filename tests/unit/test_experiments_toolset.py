"""ExperimentToolset — registry, power analysis, lift (Profile C, Sprint 4).

Two properties carry most of the weight here:

* **The registry is a control, not a lookup table.** `evaluate_experiment`
  refuses an unregistered id rather than scoring whatever evidence it was
  handed. Measuring "lift" against an undeclared control is how a system ends
  up asserting a result nobody designed, and the shipped
  `config/experiment_registry.yaml` is deliberately empty for the same reason
  `config/model_registry.yaml` has no LTV entry.
* **Significance is never asserted.** The evidence carries point values with
  no interval or sample count. Reporting "not significant" from data that
  cannot support either verdict is the more dangerous of the two errors, so
  it stays unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
    PrincipalAuthMethod,
)
from seleric_swarm.experiments.registry import registry_from_yaml
from seleric_swarm.experiments.stats import SampleSizeUnavailable
from seleric_swarm.experiments.stats import estimate_sample_size as raw_estimate
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import experiments
from seleric_swarm.toolsets import policy_config as policy

_MISSION = "MS4-experiments"

_REGISTRY_YAML = """
experiments:
  - id: exp.button_copy
    name: Checkout button copy
    status: complete
    metric_id: metric.purchase_cvr
    hypothesis: Clearer copy reduces abandonment.
    started_at: "2026-09-01"
    baseline_rate: 0.021
    mde: 0.004
    variants:
      - name: control
        label: buy_now
        control: true
      - name: treatment
        label: complete_order
  - id: exp.no_control
    name: Badly designed test
    status: running
    metric_id: metric.purchase_cvr
    variants:
      - name: a
        label: variant_a
"""


class FakeRunContext:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _ctx(store: InMemoryArtifactStore | None = None) -> FakeRunContext:
    return FakeRunContext(
        SelericDeps(
            mission_id=_MISSION,
            as_of=datetime(2026, 9, 19, tzinfo=UTC),
            principal=Principal(
                principal_id="p1",
                workspace_id="ws-1",
                user_id="user-1",
                authenticated=False,
                auth_method=PrincipalAuthMethod.ANONYMOUS,
            ),
            thread_id="t",
            run_id="r",
            trace_id="x",
            context=ContextBundle(),
            mcp_client=NullMcpClient(),
            artifact_store=store or InMemoryArtifactStore(),
            limits=ExecutionLimits(),
        )
    )


@pytest.fixture
def loaded_registry(monkeypatch, tmp_path: Path):
    path = tmp_path / "experiment_registry.yaml"
    path.write_text(_REGISTRY_YAML, encoding="utf-8")
    registry = registry_from_yaml(path)
    monkeypatch.setattr(experiments, "_registry", lambda: registry)
    return registry


def _put_evidence(store: InMemoryArtifactStore, *, label: str, value: float | None) -> str:
    payload = EvidenceArtifact(
        metric_id="metric.purchase_cvr",
        dimensions={"experiment_variant": label},
        grain="none",
        as_of=datetime(2026, 9, 19, tzinfo=UTC),
        period_start=datetime(2026, 9, 1, tzinfo=UTC),
        period_end=datetime(2026, 9, 30, tzinfo=UTC),
        value=value,
        source_query={"measure": "metric.purchase_cvr"},
    )
    return store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=[f"raw:cvr:{label}"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    ).id


# ---- sample size: real power analysis ---------------------------------------


def test_sample_size_matches_a_standard_two_proportion_calculation():
    """2.0% -> 2.5% at 80% power / 5% alpha is ~13.8k per variant. Pinned
    against a known figure so a silent statsmodels behavior change is caught."""
    result = raw_estimate(0.02, 0.005)
    assert 13_000 < result.per_variant < 14_500
    assert result.total == result.per_variant * 2


def test_smaller_effects_need_larger_samples():
    assert raw_estimate(0.02, 0.002).per_variant > raw_estimate(0.02, 0.01).per_variant


def test_higher_power_needs_a_larger_sample():
    assert raw_estimate(0.02, 0.005, power=0.95).per_variant > raw_estimate(
        0.02, 0.005, power=0.8
    ).per_variant


@pytest.mark.parametrize("baseline", [0.0, 1.0, -0.1, 1.5])
def test_non_proportion_baselines_are_refused(baseline: float):
    with pytest.raises(SampleSizeUnavailable) as excinfo:
        raw_estimate(baseline, 0.005)
    assert excinfo.value.warning == policy.WARN_INVALID_RATE


def test_an_mde_pushing_the_rate_past_one_is_refused():
    """No proportion test can detect a rate above 100%."""
    with pytest.raises(SampleSizeUnavailable):
        raw_estimate(0.98, 0.05)


def test_a_vanishing_mde_is_refused_rather_than_returning_a_huge_number():
    with pytest.raises(SampleSizeUnavailable):
        raw_estimate(0.02, 0.0)


@pytest.mark.asyncio
async def test_sample_size_tool_states_that_mde_is_absolute():
    """Relative-vs-absolute MDE is the usual way this number ends up several
    times wrong, so the summary spells out the rates it sized for."""
    result = await experiments.estimate_sample_size(_ctx(), 0.02, 0.005)

    assert result.success is True
    assert "2.000%" in result.summary and "2.500%" in result.summary
    assert result.provenance.source_metadata["mde_is_absolute"] is True


@pytest.mark.asyncio
async def test_sample_size_tool_refuses_a_bad_rate():
    result = await experiments.estimate_sample_size(_ctx(), 1.5, 0.005)
    assert result.success is False
    assert policy.WARN_INVALID_RATE in result.warnings


# ---- history ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_registry_succeeds_and_says_nothing_has_run(monkeypatch):
    """The shipped registry is empty on purpose. "Nothing has been run" is an
    answer, not a lookup failure."""
    from seleric_swarm.experiments.registry import ExperimentRegistry

    monkeypatch.setattr(experiments, "_registry", lambda: ExperimentRegistry())
    result = await experiments.get_experiment_history(_ctx())

    assert result.success is True
    assert policy.WARN_NO_EXPERIMENT_RECORDS in result.warnings


@pytest.mark.asyncio
async def test_history_lists_registered_experiments(loaded_registry):
    result = await experiments.get_experiment_history(_ctx())
    assert result.success is True
    assert "exp.button_copy" in result.summary


@pytest.mark.asyncio
async def test_history_flags_a_design_with_no_control(loaded_registry):
    result = await experiments.get_experiment_history(_ctx(), "exp.no_control")
    assert result.success is True
    assert "NO CONTROL DECLARED" in result.summary


@pytest.mark.asyncio
async def test_history_refuses_an_unknown_id(loaded_registry):
    result = await experiments.get_experiment_history(_ctx(), "exp.nope")
    assert result.success is False
    assert policy.WARN_UNKNOWN_EXPERIMENT in result.warnings


# ---- evaluation: the registry as a control ----------------------------------


@pytest.mark.asyncio
async def test_evaluate_computes_lift_against_the_declared_control(loaded_registry):
    store = InMemoryArtifactStore()
    ids = [
        _put_evidence(store, label="buy_now", value=0.020),
        _put_evidence(store, label="complete_order", value=0.025),
    ]
    result = await experiments.evaluate_experiment(_ctx(store), "exp.button_copy", ids)

    assert result.success is True, result.summary
    payload = store.get(result.artifact_ids[0]).payload
    assert payload["metrics"]["absolute_lift"] == pytest.approx(0.005)
    assert payload["metrics"]["relative_lift"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_evaluate_never_asserts_significance(loaded_registry):
    """The evidence has point values, no interval and no sample count.
    Claiming "not significant" would be a verdict the data cannot support."""
    store = InMemoryArtifactStore()
    ids = [
        _put_evidence(store, label="buy_now", value=0.020),
        _put_evidence(store, label="complete_order", value=0.025),
    ]
    result = await experiments.evaluate_experiment(_ctx(store), "exp.button_copy", ids)

    assert any("significance not assessed" in w for w in result.warnings)
    payload = store.get(result.artifact_ids[0]).payload
    assert "significance unknown" in payload["statement"]
    assert "significant" not in payload["metrics"]


@pytest.mark.asyncio
async def test_evaluate_refuses_an_unregistered_experiment(loaded_registry):
    """The headline control. Scoring evidence against an undeclared design is
    how a system asserts a result nobody planned."""
    store = InMemoryArtifactStore()
    ids = [_put_evidence(store, label="buy_now", value=0.02)]
    result = await experiments.evaluate_experiment(_ctx(store), "exp.never_registered", ids)

    assert result.success is False
    assert policy.WARN_UNKNOWN_EXPERIMENT in result.warnings
    assert result.artifact_ids == []


@pytest.mark.asyncio
async def test_evaluate_refuses_a_design_with_no_control(loaded_registry):
    store = InMemoryArtifactStore()
    ids = [_put_evidence(store, label="variant_a", value=0.02)]
    result = await experiments.evaluate_experiment(_ctx(store), "exp.no_control", ids)

    assert result.success is False
    assert policy.WARN_MISSING_VARIANT_EVIDENCE in result.warnings


@pytest.mark.asyncio
async def test_evaluate_refuses_pooled_evidence_with_no_variant_label(loaded_registry):
    """A query_metrics breakdown leaves dimensions empty, so the arms cannot be
    told apart -- averaging them would produce a lift number nobody can trace."""
    store = InMemoryArtifactStore()
    payload = EvidenceArtifact(
        metric_id="metric.purchase_cvr",
        grain="none",
        as_of=datetime(2026, 9, 19, tzinfo=UTC),
        period_start=datetime(2026, 9, 1, tzinfo=UTC),
        period_end=datetime(2026, 9, 30, tzinfo=UTC),
        value=0.02,
        source_query={},
    )
    aid = store.put(
        Artifact(
            workspace_id="ws-1",
            artifact_type="evidence",
            payload=payload.model_dump(mode="json"),
            classification="factual",
            evidence_ids=["raw:pooled"],
            provenance=ArtifactProvenance(query_version="q1"),
            mission_id=_MISSION,
        )
    ).id

    result = await experiments.evaluate_experiment(_ctx(store), "exp.button_copy", [aid])

    assert result.success is False
    assert policy.WARN_MISSING_VARIANT_EVIDENCE in result.warnings


@pytest.mark.asyncio
async def test_evaluate_names_a_treatment_with_no_evidence(loaded_registry):
    store = InMemoryArtifactStore()
    ids = [_put_evidence(store, label="buy_now", value=0.02)]
    result = await experiments.evaluate_experiment(_ctx(store), "exp.button_copy", ids)

    assert result.success is False
    assert any("treatment" in w or "variant" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_evaluate_reports_missing_evidence_ids(loaded_registry):
    result = await experiments.evaluate_experiment(
        _ctx(), "exp.button_copy", ["artifact-does-not-exist"]
    )
    assert result.success is False
    assert "artifact-does-not-exist" in result.summary


# ---- the shipped registry ---------------------------------------------------


def test_shipped_registry_is_empty_on_purpose():
    """Seeding plausible entries would fabricate experimental history, and
    evaluate_experiment scores evidence against whatever is declared — a fake
    entry would yield a real-looking lift finding for a test that never ran."""
    assert len(registry_from_yaml()) == 0
