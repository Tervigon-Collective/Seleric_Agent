"""Strategy policy loader. Wraps ``config/strategy_policies.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root

_DEFAULT_PATH = "config/strategy_policies.yaml"

_FIT_RANK = {"low": 0, "medium": 1, "high": 2, "very_high": 3}


@dataclass
class StrategyPolicies:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path | None = None) -> StrategyPolicies:
        p = Path(path) if path else repo_root() / _DEFAULT_PATH
        data: dict[str, Any] = {}
        if p.exists():
            data = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("strategy", {})
        return cls(raw=data)

    @classmethod
    def defaults(cls) -> StrategyPolicies:
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
        table = {"max_options": 5, "max_llm_calls": 2, "max_runtime_seconds": 45}
        return int(self._get("budgets", name, default=table.get(name, 0)))

    # -- generation ---------------------------------------------------
    def min_mechanism_fit_to_recommend(self) -> str:
        return str(self._get("generation", "min_mechanism_fit_to_recommend", default="high"))

    def fit_rank(self, fit: str) -> int:
        return _FIT_RANK.get(fit, -1)

    def meets_min_fit(self, fit: str) -> bool:
        return self.fit_rank(fit) >= self.fit_rank(self.min_mechanism_fit_to_recommend())
