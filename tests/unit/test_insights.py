"""Signals the user did not ask about, from the hourly business-health snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services import insights
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.services.domain_health.models import DomainStateSnapshot, ResolvedMetric
from seleric_swarm.state.artifacts import InMemoryArtifactStore


def _deps() -> SelericDeps:
    return SelericDeps(
        mission_id="MS3-i",
        as_of=datetime(2026, 10, 7, tzinfo=UTC),
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=None,
        artifact_store=InMemoryArtifactStore(),
        limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(
            metrics=(
                CatalogueMetricMeta(id="net_sales", view="commerce"),
                CatalogueMetricMeta(id="discounts", view="commerce"),
                CatalogueMetricMeta(id="shipping_cost", view="pnl"),
            )
        ),
    )


def _snap(domain: str, metrics: list[ResolvedMetric], *, age_h: float = 0.5) -> DomainStateSnapshot:
    return DomainStateSnapshot(
        domain=domain,
        brand_id="1",
        as_of="2026-10-06",
        computed_at=(datetime.now(UTC) - timedelta(hours=age_h)).isoformat(),
        window={"start": "2026-09-30", "end": "2026-10-06"},
        status="OK",
        metrics=metrics,
        provenance={"query_ids": ["q1"]},
    )


class _Store:
    def __init__(self, snaps: dict[str, DomainStateSnapshot]) -> None:
        self.snaps = snaps

    async def aget_latest(self, domain: str):
        return self.snaps.get(domain)


def _anomaly(score: float, direction: str) -> dict:
    return {"is_anomaly": True, "score": score, "direction": direction, "expected": 100.0}


async def test_related_signals_rank_first_and_quiet_metrics_are_ignored():
    store = _Store(
        {
            "commerce": _snap(
                "commerce",
                [
                    ResolvedMetric(metric_id="metric.net_sales", value=90.0, period_delta_pct=-3.0),  # quiet
                    ResolvedMetric(metric_id="metric.discounts", value=40.0, period_delta_pct=60.0, direction_bad="up"),
                ],
            ),
            "finance": _snap(
                "finance",
                [ResolvedMetric(metric_id="metric.shipping_cost", value=9.0, anomaly=_anomaly(6.0, "up"), direction_bad="up")],
            ),
        }
    )
    signals = await insights.gather_signals(_deps(), ["net_sales"], store=store)
    assert [s.metric_id for s in signals] == ["discounts", "shipping_cost"]
    assert signals[0].related and signals[0].kind == "risk"
    assert not signals[1].related and "outside its normal range" in signals[1].text


async def test_an_improvement_is_an_opportunity_and_stale_snapshots_are_skipped():
    good = ResolvedMetric(metric_id="metric.net_sales", value=180.0, anomaly=_anomaly(4.0, "up"), direction_bad="down")
    signals = await insights.gather_signals(_deps(), ["net_sales"], store=_Store({"commerce": _snap("commerce", [good])}))
    assert signals[0].kind == "opportunity"
    stale = _Store({"commerce": _snap("commerce", [good], age_h=12)})
    assert await insights.gather_signals(_deps(), ["net_sales"], store=stale) == []


async def test_signals_are_recorded_as_one_citable_signal_artifact():
    deps = _deps()
    m = ResolvedMetric(metric_id="metric.discounts", value=40.0, period_delta_pct=60.0, direction_bad="up")
    signals = await insights.gather_signals(deps, ["net_sales"], store=_Store({"commerce": _snap("commerce", [m])}))
    artifact_id = insights.record_signals(deps, signals)
    artifact = deps.artifact_store.get(artifact_id)
    # Not "evidence": the claim checks (contradiction, scope) judge only what the
    # mission fetched; the validator still counts these numbers as backed.
    assert artifact.artifact_type == "signal"
    assert artifact.payload["signals"][0] == {
        "metric": "discounts", "domain": "commerce", "kind": "risk", "as_of": "2026-10-06",
        "value": 40.0, "change_pct": 60.0, "robust_z": None, "typical": None,
    }
    from seleric_swarm.agent.validation import _mission_values

    assert 60.0 in _mission_values(deps) and 40.0 in _mission_values(deps)
    block = insights.signals_block(signals, artifact_id)
    assert "Worth a look" in block and artifact_id in block


async def test_the_insight_step_fails_open_and_has_a_kill_switch(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("disk")

    monkeypatch.setattr(insights, "gather_signals", _boom)
    assert await insights.insight_block(_deps(), ["net_sales"]) == ("", {"status": "failed"})
    monkeypatch.setenv("INSIGHT_SIGNALS", "0")
    assert await insights.insight_block(_deps(), ["net_sales"]) == ("", {"status": "disabled"})
