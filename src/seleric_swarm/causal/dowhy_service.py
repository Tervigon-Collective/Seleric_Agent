"""DoWhy boundary.

Do not let agents call arbitrary causal estimators without a registered causal
question / graph. ``DoWhyService.estimate`` builds a ``CausalModel``, identifies
the estimand, estimates the effect and runs the configured refuters. It is
import-lazy and defensive: any failure raises ``DoWhyUnavailable`` so callers
can fall back to a metadata-only audit rather than fake a causal result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DEFAULT_REFUTERS = ("placebo_treatment_refuter", "random_common_cause", "data_subset_refuter")


class DoWhyUnavailable(RuntimeError):
    """DoWhy is not installed or the estimation pipeline failed."""


@dataclass
class CausalRequest:
    treatment: str
    outcome: str
    common_causes: list[str]
    graph_id: str = ""
    estimator: str = "backdoor.linear_regression"
    refuters: list[str] = field(default_factory=lambda: list(_DEFAULT_REFUTERS))


@dataclass
class RefuterOutcome:
    name: str
    estimated_effect: float
    new_effect: float
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DoWhyEstimate:
    treatment: str
    outcome: str
    effect: float
    estimator: str
    common_causes: list[str]
    refutations: list[RefuterOutcome]
    n_rows: int

    @property
    def refutations_passed(self) -> int:
        return sum(1 for r in self.refutations if r.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "treatment": self.treatment,
            "outcome": self.outcome,
            "effect": self.effect,
            "estimator": self.estimator,
            "common_causes": list(self.common_causes),
            "n_rows": self.n_rows,
            "refutations": [
                {"name": r.name, "passed": r.passed, "estimated_effect": r.estimated_effect,
                 "new_effect": r.new_effect, **r.detail}
                for r in self.refutations
            ],
        }


class DoWhyService:
    """Thin wrapper over ``dowhy.CausalModel``. Sync internals; call from a thread
    if you need to keep an event loop free (estimation is CPU-bound)."""

    def __init__(self, *, relative_tolerance: float = 0.25) -> None:
        self._tol = relative_tolerance

    def estimate(self, request: CausalRequest, data: Any) -> DoWhyEstimate:
        try:
            import pandas as pd
            from dowhy import CausalModel
        except Exception as exc:  # pragma: no cover - env without dowhy
            raise DoWhyUnavailable(f"dowhy/pandas import failed: {exc}") from exc

        if not isinstance(data, pd.DataFrame):
            raise DoWhyUnavailable("estimate() requires a pandas DataFrame of observations")

        needed = [request.treatment, request.outcome, *request.common_causes]
        missing = [c for c in needed if c not in data.columns]
        if missing:
            raise DoWhyUnavailable(f"dataset missing columns: {missing}")

        try:
            model = CausalModel(
                data=data,
                treatment=request.treatment,
                outcome=request.outcome,
                common_causes=list(request.common_causes),
            )
            identified = model.identify_effect(proceed_when_unidentifiable=True)
            estimate = model.estimate_effect(identified, method_name=request.estimator)
            base_effect = float(estimate.value)

            refutations: list[RefuterOutcome] = []
            for name in request.refuters:
                refutations.append(self._refute(model, identified, estimate, base_effect, name))
        except DoWhyUnavailable:
            raise
        except Exception as exc:
            raise DoWhyUnavailable(f"dowhy estimation failed: {exc}") from exc

        return DoWhyEstimate(
            treatment=request.treatment,
            outcome=request.outcome,
            effect=base_effect,
            estimator=request.estimator,
            common_causes=list(request.common_causes),
            refutations=refutations,
            n_rows=len(data),
        )

    # -- refuter interpretation ------------------------------------------
    def _refute(self, model, identified, estimate, base_effect: float, name: str) -> RefuterOutcome:
        kwargs: dict[str, Any] = {}
        if name == "placebo_treatment_refuter":
            kwargs = {"placebo_type": "permute"}
        elif name == "data_subset_refuter":
            kwargs = {"subset_fraction": 0.8}
        try:
            result = model.refute_estimate(identified, estimate, method_name=name, **kwargs)
            new_effect = float(getattr(result, "new_effect", 0.0))
        except Exception as exc:  # a refuter that errors counts as not-passed, not fatal
            return RefuterOutcome(name, base_effect, float("nan"), passed=False, detail={"error": str(exc)})

        denom = abs(base_effect) if abs(base_effect) > 1e-9 else 1.0
        if name == "placebo_treatment_refuter":
            # a valid effect should collapse toward zero under a placebo treatment
            passed = abs(new_effect) <= 0.5 * denom
        else:
            # effect should be stable under a random common cause / data subset
            passed = abs(new_effect - base_effect) / denom <= self._tol
        return RefuterOutcome(name, base_effect, new_effect, passed=passed)
