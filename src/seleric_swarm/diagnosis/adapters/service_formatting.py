"""``ValueFormatting`` over the application's metric formatting rules (``services/metrics.py``)."""

from __future__ import annotations

from seleric_swarm.services import metrics


class ServiceFormatting:
    def is_percent_share(self, metric_id: str, unit: str | None) -> bool:
        return metrics.is_percent_share_metric(metric_id, unit)

    def percent_points(self, value: float) -> float:
        return metrics.percent_points(value)
