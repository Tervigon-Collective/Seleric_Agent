from __future__ import annotations

from typing import Any


class ModelRouter:
    def select_anomaly_model(self, metric_context: dict[str, Any]) -> str:
        # TODO: use registry metadata: seasonality, volume, history length, multivariate needs.
        return "seasonal_robust_baseline"

    def select_prediction_model(self, target: str, context: dict[str, Any]) -> str | None:
        # TODO: return only a validated model applicable to current data/domain.
        return None
