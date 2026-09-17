"""Config-driven AnomalyDetector selection (Sprint 2.5,
docs/features/business-state-service/05_SPRINT_PLAN.md).

``build_hybrid_bundle()`` instantiates one ``ConfiguredAnomalyDetector``
instead of hardcoding ``TemplateAnomalyDetector`` directly. Internally it
dispatches each reading to Template or BusinessStateService's
``RobustZScoreDetector`` per ``config/provider_registry.yaml`` -- the live
specialists (``swarm/specialists/anomaly.py``) still just call
``self.providers.anomaly.detect(...)`` once, unaware of the split.
"""

from __future__ import annotations

import logging
from typing import Any

from seleric_swarm.registry.provider_registry import ProviderRegistry
from seleric_swarm.services.metrics import MetricRegistry
from seleric_swarm.swarm.providers.base import AnomalyDetector, AnomalyFinding, MetricReading

log = logging.getLogger(__name__)


class ConfiguredAnomalyDetector:
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        metrics: MetricRegistry,
        template: AnomalyDetector,
        robust_zscore: AnomalyDetector | None,
    ) -> None:
        self._registry = registry
        self._metrics = metrics
        self._template = template
        self._robust_zscore = robust_zscore

    def _strategy_for(self, reading: MetricReading) -> str:
        definition = self._metrics.get(reading.metric_id)
        domain = definition.domain if definition else None
        strategy = self._registry.anomaly_strategy_for(metric_id=reading.metric_id, domain=domain)
        if strategy == "robust_zscore" and self._robust_zscore is None:
            # Config asked for BusinessStateService's detector but none was
            # wired in (e.g. runtime.business_state wasn't passed to
            # build_hybrid_bundle) -- degrade to template rather than crash.
            log.warning("provider_registry selected robust_zscore for %s with no BusinessStateService wired; using template", reading.metric_id)
            return "template"
        return strategy

    async def detect(self, readings: list[MetricReading], *, context: dict[str, Any]) -> list[AnomalyFinding]:
        groups: dict[str, list[MetricReading]] = {}
        for reading in readings:
            groups.setdefault(self._strategy_for(reading), []).append(reading)

        findings: list[AnomalyFinding] = []
        for strategy, group in groups.items():
            detector = self._robust_zscore if strategy == "robust_zscore" else self._template
            findings.extend(await detector.detect(group, context=context))
        return findings
