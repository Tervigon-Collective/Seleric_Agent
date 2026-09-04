"""Diagnostic policy loader. Wraps ``config/diagnostic_policies.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root

_DEFAULT_PATH = "config/diagnostic_policies.yaml"

_CONFIDENCE_ORDER = [
    "REJECTED",
    "ASSOCIATION_ONLY",
    "PLAUSIBLE_CAUSAL",
    "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS",
    "STRONGLY_SUPPORTED",
]


@dataclass
class DiagnosticPolicies:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path | None = None) -> DiagnosticPolicies:
        p = Path(path) if path else repo_root() / _DEFAULT_PATH
        data: dict[str, Any] = {}
        if p.exists():
            data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("diagnostic", {})
        return cls(raw=data)

    @classmethod
    def defaults(cls) -> DiagnosticPolicies:
        return cls(raw={})

    def _get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # -- budgets ------------------------------------------------------
    def budget(self, name: str) -> int:
        table = {
            "max_hypotheses": 6,
            "max_primary_candidates": 2,
            "max_tests_per_hypothesis": 6,
            "max_llm_calls": 3,
            "max_runtime_seconds": 90,
        }
        return int(self._get("budgets", name, default=table.get(name, 0)))

    # -- hypotheses -------------------------------------------------
    def min_supporting_evidence(self) -> int:
        return int(self._get("hypotheses", "min_supporting_evidence", default=1))

    def llm_enrichment(self) -> bool:
        return bool(self._get("hypotheses", "llm_enrichment", default=True))

    def prior_weights(self) -> dict[str, float]:
        default = {
            "evidence_overlap": 0.4,
            "incident_match": 0.3,
            "temporal_alignment": 0.2,
            "mechanism_specificity": 0.1,
        }
        return {**default, **(self._get("hypotheses", "prior_weights", default={}) or {})}

    # -- testing --------------------------------------------------
    def hard_gates(self) -> set[str]:
        return set(self._get("testing", "hard_gates", default=["temporal_precedence"]))

    def temporal_tolerance_minutes(self) -> int:
        return int(self._get("testing", "temporal_tolerance_minutes", default=90))

    def min_segment_divergence_pct(self) -> float:
        return float(self._get("testing", "min_segment_divergence_pct", default=10))

    def dose_response_min_pairs(self) -> int:
        return int(self._get("testing", "dose_response_min_pairs", default=2))

    # -- causal ------------------------------------------------
    def causal_flag(self, name: str) -> bool:
        default = {"require_graph": True, "require_temporal_check": True}
        return bool(self._get("causal", name, default=default.get(name, False)))

    def causal_min_refutations(self) -> int:
        return int(self._get("causal", "min_refutations", default=2))

    def estimator(self) -> str:
        return str(self._get("causal", "estimator", default="backdoor.linear_regression"))

    def refuters(self) -> list[str]:
        return list(
            self._get(
                "causal",
                "refuters",
                default=["placebo_treatment_refuter", "random_common_cause", "data_subset_refuter"],
            )
        )

    def retain_threshold(self) -> str:
        return str(self._get("causal", "retain_at_or_above", default="CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS"))

    def metadata_only_ceiling(self) -> str:
        return str(self._get("causal", "metadata_only_ceiling", default="PLAUSIBLE_CAUSAL"))

    # -- synthesis ---------------------------------------------
    def emit_inconclusive_finding(self) -> bool:
        return bool(self._get("synthesis", "emit_inconclusive_finding", default=True))

    def always_note_confounding(self) -> bool:
        return bool(self._get("synthesis", "always_note_confounding", default=True))

    # -- helpers ---------------------------------------------
    @staticmethod
    def confidence_rank(confidence: str) -> int:
        try:
            return _CONFIDENCE_ORDER.index(confidence)
        except ValueError:
            return 0

    def meets_retain(self, confidence: str) -> bool:
        return self.confidence_rank(confidence) >= self.confidence_rank(self.retain_threshold())

    def cap_metadata_confidence(self, confidence: str) -> str:
        ceiling = self.metadata_only_ceiling()
        if self.confidence_rank(confidence) > self.confidence_rank(ceiling):
            return ceiling
        return confidence
