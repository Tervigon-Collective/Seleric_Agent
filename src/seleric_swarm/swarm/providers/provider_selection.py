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

    def _strategy_for(self, reading: MetricReading, *, force_robust: bool = False) -> str:
        definition = self._metrics.get(reading.metric_id)
        # Live intake emits catalogue ids (total_ad_spend); provider_registry.yaml
        # is keyed on YAML overlay ids (metric.spend). Try both spellings, and
        # the overlay's domain (performance), not the live category (finance).
        overlay = self._metrics._overlay_for(reading.metric_id)
        domain = (overlay.domain if overlay else None) or (definition.domain if definition else None)
        strategy = "template"
        for mid in dict.fromkeys(
            mid
            for mid in (
                getattr(overlay, "id", None),
                reading.metric_id,
                getattr(definition, "id", None),
                getattr(definition, "catalogue_metric", None),
            )
            if mid
        ):
            strategy = self._registry.anomaly_strategy_for(metric_id=mid, domain=domain)
            if strategy != "template":
                break
        if strategy == "template" and force_robust:
            strategy = "robust_zscore"
        if strategy == "robust_zscore" and self._robust_zscore is None:
            # Config asked for BusinessStateService's detector but none was
            # wired in (e.g. runtime.business_state wasn't passed to
            # build_hybrid_bundle) -- degrade to template rather than crash.
            log.warning("provider_registry selected robust_zscore for %s with no BusinessStateService wired; using template", reading.metric_id)
            return "template"
        return strategy

    async def detect(self, readings: list[MetricReading], *, context: dict[str, Any]) -> list[AnomalyFinding]:
        # On diagnostic/executive_health missions the "why" investigation
        # wants real history for the asked metric and its co-movers, not
        # just the commerce/spend/net_profit subset config.yaml ships by
        # default (05_SPRINT_PLAN.md Sprint 2.5 widening). AnomalyAgent sets
        # this from mission.wants("diagnostic") / mission.wants("executive_health").
        force_robust = bool(context.get("force_robust_zscore")) and self._robust_zscore is not None
        groups: dict[str, list[MetricReading]] = {}
        for reading in readings:
            groups.setdefault(self._strategy_for(reading, force_robust=force_robust), []).append(reading)

        findings: list[AnomalyFinding] = []
        template_group = groups.get("template", [])
        if "robust_zscore" in groups:
            detector = self._robust_zscore
            robust_findings = await detector.detect(groups["robust_zscore"], context=context)
            findings.extend(robust_findings)
            if force_robust:
                # BusinessStateService returns nothing for a reading on
                # SPARSE_HISTORY/UNAVAILABLE (03 SS5) rather than an error --
                # fall those specific readings back to the template detector
                # instead of silently losing the metric from this mission.
                found_ids = {f.metric_id for f in robust_findings}
                template_group = template_group + [
                    r for r in groups["robust_zscore"] if r.metric_id not in found_ids
                ]
        if template_group:
            findings.extend(await self._template.detect(template_group, context=context))
        return findings
