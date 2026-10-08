"""Regressions from the 2026-10-07/08 run-failure audit (162 missions, 6 failed, 16 partial).

The four Pawveralls channel-reconciliation failures shared one chain: the model was
told to roll up rows with run_python, run_python refused the prefetch finding that held
them (artifact-type misuse, 20 of 33 tool failures), so it summed in prose and the
unrecorded totals failed grounding until the mission did.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai import ModelRetry

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import ExecutionLimits, NullMcpClient, SelericDeps
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import analytics, sandbox, semantic

AS_OF = datetime(2026, 10, 8, tzinfo=UTC)


class Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


def _deps(store: InMemoryArtifactStore, catalogue: CatalogueSnapshot | None = None) -> SelericDeps:
    return SelericDeps(
        mission_id="m1", as_of=AS_OF,
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t", run_id="r", trace_id="tr", context=ContextBundle(), mcp_client=NullMcpClient(),
        artifact_store=store, limits=ExecutionLimits(), catalogue=catalogue or CatalogueSnapshot(),
    )


def _row(store: InMemoryArtifactStore, sub: str, value: float, metric: str = "product_gross_sales") -> str:
    ev = EvidenceArtifact(
        metric_id=metric, dimensions={"sub_channel": sub}, grain="none", as_of=AS_OF,
        period_start=AS_OF, period_end=AS_OF, value=value, unit="INR", source_query={"measure": metric},
    )
    return store.put(Artifact(
        workspace_id="w", artifact_type="evidence", payload=ev.model_dump(mode="json"), classification="factual",
        evidence_ids=[f"raw:{metric}:{sub}"], provenance=ArtifactProvenance(query_version="q"), mission_id="m1",
    )).id


def _finding(store: InMemoryArtifactStore, evidence_ids: list[str], artifact_type: str = "finding") -> str:
    payload = (
        Finding(finding_type="prefetched_lookup", statement="table", evidence_ids=evidence_ids).model_dump(mode="json")
        if artifact_type == "finding" else {"chart": "bar"}
    )
    return store.put(Artifact(
        workspace_id="w", artifact_type=artifact_type, payload=payload, classification="derived",
        evidence_ids=list(evidence_ids),
        provenance=ArtifactProvenance(evidence_ids=list(evidence_ids), calculation_version="test.v1"),
        mission_id="m1",
    )).id


@pytest.mark.parametrize("artifact_type", ["finding", "chart_spec"])
async def test_run_python_reads_a_derived_artifact_as_the_evidence_it_cites(artifact_type: str) -> None:
    store = InMemoryArtifactStore()
    rows = [_row(store, "ig_feed", 938112.60), _row(store, "fb_feed", 300370.76), _row(store, "google_pmax", 224491.98)]
    derived = _finding(store, rows, artifact_type)
    res = await sandbox.run_python(
        Ctx(_deps(store)),  # type: ignore[arg-type]
        "result = {'meta': sum(e['value'] for e in evidence if e['dimensions']['sub_channel'] in ('ig_feed', 'fb_feed'))}",
        [derived],
    )
    assert res.success, res.summary
    out = store.get(res.artifact_ids[0]).payload
    assert out["metrics"]["meta"] == pytest.approx(1238483.36)
    assert set(out["evidence_ids"]) == set(rows)  # cites the measurements, never the summary


async def test_scoring_tools_still_refuse_a_finding() -> None:
    """The analytics owner's rule stands: scoring a summary as if it were a measurement."""
    store = InMemoryArtifactStore()
    rows = [_row(store, "a", 1.0), _row(store, "b", 2.0)]
    _ev, _ids, refusal = analytics._load_evidence(Ctx(_deps(store)), [_finding(store, rows)])  # type: ignore[arg-type]
    assert refusal is not None and refusal.retryable and rows[0] in refusal.summary


def test_a_breakdowns_row_count_is_a_fetched_fact() -> None:
    """MS3-8c381f3641: "all 173 products" was rejected as a total against the revenue column."""
    from seleric_swarm.agent.validation import _mission_values

    store = InMemoryArtifactStore()
    for i in range(5):
        _row(store, f"p{i}", 10.0 + i)
    values = _mission_values(_deps(store))
    assert 5.0 in values
    assert 60.0 in values  # the column total is still there


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(metrics=(
        CatalogueMetricMeta(id="net_profit", view="order_pnl", supported_dimensions=["finance_channel"]),
        CatalogueMetricMeta(id="gross_profit", view="order_pnl", supported_dimensions=["finance_channel"]),
        CatalogueMetricMeta(id="ad_spend", view="ad_delivery", supported_dimensions=["campaign_name"]),
    ))


def test_another_views_metric_is_not_a_dimension() -> None:
    """Live: dimensions={"ad_spend": ...} on net_profit reached Cube as an invalid filter (x4)."""
    with pytest.raises(ModelRetry, match="'ad_spend' is a metric"):
        semantic._reject_metric_as_dimension(_catalogue(), "net_profit", ["finance_channel", "ad_spend"])
    # same view: a measure filter Cube supports; own id and real dimensions pass too
    semantic._reject_metric_as_dimension(_catalogue(), "net_profit", ["gross_profit", "net_profit", "finance_channel"])
    semantic._reject_metric_as_dimension(CatalogueSnapshot(), "net_profit", ["ad_spend"])  # empty snapshot: fail-open


def test_failure_detail_keeps_types_and_status_never_bodies() -> None:
    from pydantic_ai.exceptions import FallbackExceptionGroup, ModelHTTPError

    from seleric_swarm.agent.runner import _failure_detail

    exc = FallbackExceptionGroup("all failed", [
        ModelHTTPError(429, "gpt-5-mini", body={"error": "secret provider body"}),
        TimeoutError("read timed out"),
    ])
    detail = _failure_detail(exc)
    assert detail == ["ModelHTTPError 429", "TimeoutError"]
    assert "secret" not in str(detail)
