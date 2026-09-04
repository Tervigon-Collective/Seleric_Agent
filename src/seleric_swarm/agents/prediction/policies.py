"""Prediction policy loader. Wraps ``config/prediction_policies.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root

_DEFAULT_PATH = "config/prediction_policies.yaml"


@dataclass
class PredictionPolicies:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path | None = None) -> PredictionPolicies:
        p = Path(path) if path else repo_root() / _DEFAULT_PATH
        data: dict[str, Any] = {}
        if p.exists():
            data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("prediction", {})
        return cls(raw=data)

    @classmethod
    def defaults(cls) -> PredictionPolicies:
        return cls(raw={})

    def _get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # -- budgets -----------------------------------------------------
    def budget(self, name: str) -> int:
        table = {"max_llm_calls": 2, "max_runtime_seconds": 45}
        return int(self._get("budgets", name, default=table.get(name, 0)))

    # -- horizon ---------------------------------------------------
    def default_horizon(self) -> str:
        return str(self._get("horizon", "default", default="7d"))

    def max_horizon_days(self) -> int:
        return int(self._get("horizon", "max_days", default=30))

    # -- fallback ladder --------------------------------------
    def fallback_order(self) -> list[str]:
        return list(self._get("fallback_order", default=["registered_model", "statistical_baseline", "insufficient"]))

    def allow_llm_numeric_fallback(self) -> bool:
        return bool(self._get("allow_llm_numeric_fallback", default=False))

    # -- model gates ----------------------------------------
    def model_require_status(self) -> set[str]:
        return {s.lower() for s in self._get("model", "require_status", default=["approved", "production"])}

    def model_require_backtest(self) -> bool:
        return bool(self._get("model", "require_backtest", default=True))

    def model_recent_validation_days(self) -> int:
        return int(self._get("model", "require_recent_validation_days", default=90))

    def model_require_feature_set(self) -> bool:
        return bool(self._get("model", "require_feature_set", default=True))

    # -- baseline -------------------------------------------
    def baseline_method(self) -> str:
        return str(self._get("baseline", "method", default="drift_projection"))

    def baseline_min_history(self) -> int:
        return int(self._get("baseline", "min_history_points", default=8))

    def baseline_interval_z(self) -> float:
        return float(self._get("baseline", "interval_z", default=1.28))

    def baseline_approved(self) -> bool:
        return bool(self._get("baseline", "approved", default=True))

    # -- drift ---------------------------------------------
    def drift_reject_statuses(self) -> set[str]:
        return {s.lower() for s in self._get("drift", "reject_statuses", default=["red", "drifted"])}

    def drift_unknown_is(self) -> str:
        return str(self._get("drift", "unknown_is", default="warning"))

    # -- applicability -----------------------------------
    def applic_min_history_days(self) -> int:
        return int(self._get("applicability", "min_history_days", default=28))

    def applic_reject_statuses(self) -> set[str]:
        return {s.lower() for s in self._get("applicability", "reject_statuses", default=["out_of_domain", "regime_shift", "not_applicable"])}

    # -- interval ----------------------------------------
    def interval_required(self) -> bool:
        return bool(self._get("interval", "required", default=True))

    def interval_max_relative_width(self) -> float:
        return float(self._get("interval", "max_relative_width", default=0.6))

    # -- scenarios --------------------------------------
    def build_scenarios(self) -> bool:
        return bool(self._get("scenarios", "build", default=True))

    def cause_persistence(self, level: str) -> float:
        key = "cause_persistence_high" if level == "high" else "cause_persistence_low"
        return float(self._get("scenarios", key, default=1.0 if level == "high" else 0.4))

    # -- confidence -----------------------------------
    def strong_backtest_mape_below(self) -> float:
        return float(self._get("confidence", "strong_requires_backtest_mape_below", default=0.15))
