"""Skeptic policy loader.

Wraps ``config/skeptic_policies.yaml`` in a small typed accessor so modules ask
``policies.risk_weights()`` instead of digging through nested dicts. Unknown keys
fall back to conservative built-in defaults, so a missing config file still
produces a working (strict) Skeptic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root

_DEFAULT_PATH = "config/skeptic_policies.yaml"


@dataclass
class SkepticPolicies:
    raw: dict[str, Any]

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path | None = None) -> SkepticPolicies:
        p = Path(path) if path else repo_root() / _DEFAULT_PATH
        data: dict[str, Any] = {}
        if p.exists():
            data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("skeptic", {})
        return cls(raw=data)

    @classmethod
    def defaults(cls) -> SkepticPolicies:
        return cls(raw={})

    # -- generic getter -------------------------------------------------
    def _get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # -- budgets ---------------------------------------------------------
    def budget(self, name: str) -> int:
        table = {
            "max_alternative_hypotheses": 5,
            "max_challenges": 8,
            "max_followup_rounds": 3,
            "max_parallel_checks": 5,
            "max_validator_calls": 24,
            "max_llm_calls": 4,
            "max_runtime_seconds": 60,
        }
        return int(self._get("budgets", name, default=table.get(name, 0)))

    # -- activation ----------------------------------------------------
    def skeptic_required(self, claim_type: str, risk_score: float) -> bool:
        node = self._get("activation", claim_type, default={}) or {}
        if node.get("skeptic_required") is True:
            return True
        threshold = node.get("skeptic_required_if_risk_above")
        if threshold is not None:
            return risk_score > float(threshold)
        return bool(node.get("skeptic_required", False))

    # -- risk --------------------------------------------------------
    def risk_weights(self) -> dict[str, float]:
        default = {
            "claim_impact": 0.20,
            "claim_type_risk": 0.15,
            "evidence_weakness": 0.15,
            "causal_complexity": 0.15,
            "model_dependency": 0.10,
            "irreversibility": 0.10,
            "financial_magnitude": 0.10,
            "operational_risk": 0.05,
        }
        return {**default, **(self._get("risk", "weights", default={}) or {})}

    def claim_type_risk(self, claim_type: str) -> float:
        default = {
            "numeric": 0.15, "comparison": 0.20, "anomaly": 0.40, "correlation": 0.50,
            "causal": 0.80, "forecast": 0.75, "recommendation": 0.80, "action": 0.95,
            "qualitative": 0.10,
        }
        table = {**default, **(self._get("risk", "claim_type_risk", default={}) or {})}
        return float(table.get(claim_type, 0.3))

    def risk_class_thresholds(self) -> dict[str, float]:
        default = {"R1": 0.15, "R2": 0.35, "R3": 0.55, "R4": 0.70, "R5": 0.85}
        return {**default, **(self._get("risk", "class_thresholds", default={}) or {})}

    def min_class_by_type(self, claim_type: str) -> str | None:
        return (self._get("risk", "min_class_by_type", default={}) or {}).get(claim_type)

    # -- evidence / provenance -------------------------------------
    def require_evidence_for(self) -> set[str]:
        return set(
            self._get(
                "evidence", "require_evidence_for",
                default=["numeric", "comparison", "causal", "forecast", "recommendation", "action"],
            )
        )

    def max_freshness_hours(self) -> float:
        return float(self._get("evidence", "max_freshness_hours", default=72))

    def min_sample_size(self) -> int:
        return int(self._get("evidence", "min_sample_size", default=100))

    def require_query_hash_for(self) -> set[str]:
        return set(self._get("provenance", "require_query_hash_for", default=["numeric", "comparison"]))

    def require_calculation_version_for(self) -> set[str]:
        return set(self._get("provenance", "require_calculation_version_for", default=["numeric", "causal"]))

    # -- causal ---------------------------------------------------
    def causal_flag(self, name: str) -> bool:
        default = {"require_temporal_check": True, "require_graph": True, "require_refutation": True}
        return bool(self._get("causal", name, default=default.get(name, False)))

    def causal_min_refutations(self) -> int:
        return int(self._get("causal", "min_refutations", default=2))

    # -- forecast / model --------------------------------------
    def forecast_flag(self, name: str) -> bool:
        default = {"require_model_metadata": True, "allow_llm_numeric_fallback": False}
        return bool(self._get("forecast", name, default=default.get(name, False)))

    def drift_reject_statuses(self) -> set[str]:
        return {s.lower() for s in self._get("forecast", "drift_reject_statuses", default=["red", "drifted"])}

    def model_recent_validation_days(self) -> int:
        return int(self._get("model", "require_recent_validation_days", default=90))

    def model_require_backtest(self) -> bool:
        return bool(self._get("model", "require_backtest", default=True))

    # -- strategy ----------------------------------------------
    def strategy_require_mechanism_fit(self) -> bool:
        return bool(self._get("strategy", "require_mechanism_fit", default=True))

    def strategy_reject_on_blocking_rule(self) -> bool:
        return bool(self._get("strategy", "reject_on_blocking_rule_violation", default=True))

    # -- blind review ----------------------------------------
    def blind_review_threshold(self) -> float:
        return float(self._get("blind_review", "enabled_if_risk_above", default=0.75))

    # -- trust -------------------------------------------------
    def trust_label_thresholds(self) -> dict[str, float]:
        default = {"INSUFFICIENT": 0.0, "WEAK": 0.35, "PROBABLE": 0.55, "STRONG": 0.72, "VERIFIED": 0.9}
        return {**default, **(self._get("trust", "label_thresholds", default={}) or {})}

    def trust_revise_below(self) -> float:
        return float(self._get("trust", "verdict_thresholds", "revise_below", default=0.55))

    def trust_profile(self, claim_type: str) -> dict[str, float]:
        profiles = self._get("trust", "profiles", default={}) or {}
        if claim_type in profiles:
            return dict(profiles[claim_type])
        if "default" in profiles:
            return dict(profiles["default"])
        return {"evidence_quality": 0.4, "provenance_completeness": 0.3, "cross_source_agreement": 0.3}
