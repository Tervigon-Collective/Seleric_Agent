"""Sprint 2.5 checklist: config selects a non-default strategy ->
build_hybrid_bundle returns that implementation, not the Template one.

Uses the live-MCP ``runtime`` fixture (conftest.py) since ConfiguredAnomalyDetector's
whole point is dispatching real BusinessStateService calls for overridden
metrics -- a fake gateway would just prove the dispatch, not that the wiring
survives a real build_hybrid_bundle() call.
"""

from __future__ import annotations

import pytest

from seleric_swarm.registry.provider_registry import ProviderRegistry
from seleric_swarm.services.business_state.detectors import RobustZScoreDetector
from seleric_swarm.services.metrics import MetricRegistry
from seleric_swarm.swarm.providers.base import AnomalyFinding, MetricReading
from seleric_swarm.swarm.providers.mcp_data import build_hybrid_bundle
from seleric_swarm.swarm.providers.provider_selection import ConfiguredAnomalyDetector
from seleric_swarm.swarm.providers.template import TemplateAnomalyDetector


class _FakeRegistry(ProviderRegistry):
    """Override just metric.spend -> robust_zscore, everything else default."""

    def __init__(self) -> None:  # no super().__init__: skip the YAML read entirely
        self._default = {"anomaly_strategy": "template", "forecast_strategy": "template"}
        self._domain = {}
        self._metric = {"metric.spend": {"anomaly_strategy": "robust_zscore"}}


def test_default_registry_resolves_template():
    registry = ProviderRegistry()
    assert registry.anomaly_strategy_for(metric_id="metric.units_sold", domain="product") == "template"


def test_provider_registry_yaml_has_a_real_override():
    """config/provider_registry.yaml ships with at least one non-default
    override (metric.spend -> robust_zscore) so the mechanism is exercised
    by default, not just in tests."""
    registry = ProviderRegistry()
    assert registry.anomaly_strategy_for(metric_id="metric.spend") == "robust_zscore"


def test_catalogue_id_resolves_to_yaml_metric_override():
    """Live classifier emits catalogue ids; registry keys are YAML metric.* ids."""
    metrics = MetricRegistry("config/metric_registry.yaml")
    # stand-in so the no-BSS degrade path isn't taken; we only assert dispatch.
    detector = ConfiguredAnomalyDetector(
        registry=_FakeRegistry(),
        metrics=metrics,
        template=TemplateAnomalyDetector(),
        robust_zscore=TemplateAnomalyDetector(),
    )
    assert detector._strategy_for(MetricReading(metric_id="total_ad_spend", value=1, baseline=1)) == "robust_zscore"
    assert detector._strategy_for(MetricReading(metric_id="metric.spend", value=1, baseline=1)) == "robust_zscore"
    assert detector._strategy_for(MetricReading(metric_id="metric.net_sales", value=1, baseline=1)) == "template"

    shipped = ConfiguredAnomalyDetector(
        registry=ProviderRegistry(),
        metrics=metrics,
        template=TemplateAnomalyDetector(),
        robust_zscore=TemplateAnomalyDetector(),
    )
    assert shipped._strategy_for(MetricReading(metric_id="commerce_net_revenue_daily", value=1, baseline=1)) == "robust_zscore"


@pytest.mark.asyncio
async def test_build_hybrid_bundle_returns_configured_detector_not_bare_template(runtime):
    bundle, _stats = build_hybrid_bundle(
        mcp=runtime.mcp,
        execution_mode="staging",
        metrics=runtime.metrics,
        agents=runtime.agents,
        business_state=runtime.business_state,
    )
    assert isinstance(bundle.anomaly, ConfiguredAnomalyDetector)
    assert not isinstance(bundle.anomaly, TemplateAnomalyDetector)


@pytest.mark.asyncio
async def test_overridden_metric_uses_business_state_others_use_template(runtime):
    detector = ConfiguredAnomalyDetector(
        registry=_FakeRegistry(),
        metrics=runtime.metrics,
        template=TemplateAnomalyDetector(),
        robust_zscore=RobustZScoreDetector(runtime.business_state),
    )
    readings = [
        MetricReading(
            metric_id="metric.spend",  # overridden -> robust_zscore, real MCP history
            value=100.0,
            baseline=90.0,
            direction_bad="up",
            data_origin="MCP",
            synthetic=False,
        ),
        MetricReading(
            metric_id="metric.net_sales",  # not overridden -> template, value/baseline only
            value=100.0,
            baseline=80.0,
            direction_bad="down",
            data_origin="TEMPLATE_TEST",
            synthetic=False,
        ),
    ]
    findings = await detector.detect(readings, context={"time_range": {"kind": "relative", "relative_token": "last_30d"}})

    by_metric = {f.metric_id: f for f in findings}
    assert by_metric["metric.spend"].data_origin == "BUSINESS_STATE"
    assert by_metric["metric.spend"].detector.get("strategy") == "robust_zscore"
    assert by_metric["metric.net_sales"].data_origin == "TEMPLATE_TEST"
    assert by_metric["metric.net_sales"].detector.get("method") == "relative_effect_size"


def test_force_robust_zscore_overrides_template_default():
    metrics = MetricRegistry("config/metric_registry.yaml")
    detector = ConfiguredAnomalyDetector(
        registry=ProviderRegistry(),  # metric.units_sold isn't overridden -> template by default
        metrics=metrics,
        template=TemplateAnomalyDetector(),
        robust_zscore=TemplateAnomalyDetector(),  # stand-in; only dispatch is asserted
    )
    assert detector._strategy_for(MetricReading(metric_id="metric.units_sold", value=1, baseline=1)) == "template"
    assert (
        detector._strategy_for(MetricReading(metric_id="metric.units_sold", value=1, baseline=1), force_robust=True)
        == "robust_zscore"
    )


class _SparseHistoryOnSecondCall:
    """robust_zscore stand-in: no finding for one metric (SPARSE_HISTORY), a
    real finding for the other -- exercises the per-reading template fallback."""

    def __init__(self, sparse_metric_id: str) -> None:
        self._sparse = sparse_metric_id

    async def detect(self, readings, *, context):
        return [
            AnomalyFinding(
                metric_id=r.metric_id,
                observed=r.value,
                expected_range=[0, 1],
                deviation_pct=1.0,
                score=1.0,
                direction="up",
                data_origin="BUSINESS_STATE",
                synthetic=False,
            )
            for r in readings
            if r.metric_id != self._sparse
        ]


@pytest.mark.asyncio
async def test_force_robust_zscore_falls_back_to_template_on_sparse_history():
    metrics = MetricRegistry("config/metric_registry.yaml")
    detector = ConfiguredAnomalyDetector(
        registry=ProviderRegistry(),
        metrics=metrics,
        template=TemplateAnomalyDetector(),
        robust_zscore=_SparseHistoryOnSecondCall(sparse_metric_id="metric.net_sales"),
    )
    readings = [
        MetricReading(metric_id="metric.spend", value=100.0, baseline=90.0, direction_bad="up"),
        MetricReading(metric_id="metric.net_sales", value=100.0, baseline=80.0, direction_bad="down"),
    ]
    findings = await detector.detect(readings, context={"force_robust_zscore": True})

    by_metric = {f.metric_id: f for f in findings}
    assert by_metric["metric.spend"].data_origin == "BUSINESS_STATE"
    # metric.net_sales got no robust_zscore finding (SPARSE_HISTORY) -> fell
    # back to the template detector rather than silently dropping the metric.
    assert by_metric["metric.net_sales"].detector.get("method") == "relative_effect_size"


@pytest.mark.asyncio
async def test_registry_selects_robust_zscore_but_no_business_state_degrades_to_template(runtime):
    """If config says robust_zscore but build_hybrid_bundle wasn't given a
    business_state (e.g. an older call site), never crash -- fall back."""
    detector = ConfiguredAnomalyDetector(
        registry=_FakeRegistry(),
        metrics=runtime.metrics,
        template=TemplateAnomalyDetector(),
        robust_zscore=None,
    )
    readings = [
        MetricReading(metric_id="metric.spend", value=100.0, baseline=90.0, direction_bad="up"),
    ]
    findings = await detector.detect(readings, context={})
    assert findings[0].data_origin == "TEMPLATE"  # MetricReading's own default
    assert findings[0].detector.get("method") == "relative_effect_size"
