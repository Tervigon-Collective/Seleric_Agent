"""DoWhy boundary.

Do not let agents call arbitrary causal estimators without a registered causal
question / graph. ``DoWhyService.estimate`` builds a ``CausalModel``, identifies
the estimand, estimates the effect and runs the configured refuters. It is
import-lazy and defensive: any failure raises ``DoWhyUnavailable`` so callers
can fall back to a metadata-only audit rather than fake a causal result.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

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
    confidence_interval: list[float] = field(default_factory=list)
    dropped_collinear_common_causes: list[str] = field(default_factory=list)

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
            "confidence_interval": list(self.confidence_interval),
            "dropped_collinear_common_causes": list(self.dropped_collinear_common_causes),
            "refutations": [
                {"name": r.name, "passed": r.passed, "estimated_effect": r.estimated_effect,
                 "new_effect": r.new_effect, **r.detail}
                for r in self.refutations
            ],
        }


def _normalize_confidence_interval(raw: Any) -> list[float]:
    """Normalize DoWhy CI output to ``[lo, hi]`` or ``[]`` if unavailable."""
    if raw is None:
        return []
    try:
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        if isinstance(raw, dict):
            for key in ("default", "bootstrap", "ci", "interval"):
                if key in raw:
                    return _normalize_confidence_interval(raw[key])
            vals = list(raw.values())
            return _normalize_confidence_interval(vals[0] if len(vals) == 1 else vals)
        if isinstance(raw, (list, tuple)):
            if len(raw) == 2 and all(isinstance(x, (int, float)) for x in raw):
                lo, hi = float(raw[0]), float(raw[1])
                return [lo, hi] if lo <= hi else [hi, lo]
            # Nested [[lo, hi], ...] from some DoWhy versions
            if len(raw) == 1:
                return _normalize_confidence_interval(raw[0])
            if len(raw) >= 2 and isinstance(raw[0], (list, tuple)):
                return _normalize_confidence_interval(raw[0])
    except (TypeError, ValueError):
        return []
    return []


_COLLINEARITY_THRESHOLD = 0.98


def _drop_collinear_common_causes(
    data: Any, treatment: str, common_causes: list[str]
) -> tuple[list[str], list[str]]:
    """Drop common causes that are near-perfectly correlated with the treatment.

    Ad-platform metrics routinely move together (clicks/impressions/spend all
    spike from the same tracking or scale change), which makes the design
    matrix for ``backdoor.linear_regression`` singular or numerically unstable
    and used to blow the whole estimate up into a ``DoWhyUnavailable`` ->
    template-fallback (a real, sufficiently-sized observation frame producing
    no real causal estimate at all). Dropping the offending covariate keeps
    the estimate real; the caller is told what was dropped so it isn't silent.
    """
    if not common_causes:
        return [], []
    kept: list[str] = []
    dropped: list[str] = []
    treatment_col = data[treatment]
    for cause in common_causes:
        try:
            corr = float(treatment_col.corr(data[cause]))
        except Exception:
            _log.warning("collinearity_check_failed", exc_info=True, extra={"cause": cause})
            kept.append(cause)
            continue
        if not math.isnan(corr) and abs(corr) >= _COLLINEARITY_THRESHOLD:
            dropped.append(cause)
        else:
            kept.append(cause)
    return kept, dropped


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

        common_causes, dropped = _drop_collinear_common_causes(data, request.treatment, request.common_causes)
        if dropped:
            _log.warning(
                "dowhy: dropped near-collinear common causes %s (|corr| with treatment %s > %.2f) "
                "to avoid a singular/unstable regression",
                dropped,
                request.treatment,
                _COLLINEARITY_THRESHOLD,
            )

        try:
            model = CausalModel(
                data=data,
                treatment=request.treatment,
                outcome=request.outcome,
                common_causes=common_causes,
            )
            identified = model.identify_effect(proceed_when_unidentifiable=True)
            estimate = model.estimate_effect(identified, method_name=request.estimator)
            base_effect = float(estimate.value)
            ci = _extract_confidence_interval(estimate)

            refutations: list[RefuterOutcome] = []
            for name in request.refuters:
                refutations.append(self._refute(model, identified, estimate, base_effect, name))
        except DoWhyUnavailable:
            raise
        except Exception as exc:
            # Surface *why* with full context and a traceback -- a bare
            # DoWhyUnavailable(str(exc)) upstream loses both, which made every
            # real DoWhy failure (singular design matrix from collinear common
            # causes, near-zero-variance treatment column, etc.) indistinguishable
            # from "dowhy not installed" in the logs.
            _log.warning(
                "dowhy estimation failed: treatment=%s outcome=%s common_causes=%s n_rows=%s estimator=%s",
                request.treatment,
                request.outcome,
                request.common_causes,
                len(data),
                request.estimator,
                exc_info=True,
            )
            raise DoWhyUnavailable(f"dowhy estimation failed: {exc}") from exc

        return DoWhyEstimate(
            treatment=request.treatment,
            outcome=request.outcome,
            effect=base_effect,
            estimator=request.estimator,
            common_causes=common_causes,
            refutations=refutations,
            n_rows=len(data),
            confidence_interval=ci,
            dropped_collinear_common_causes=dropped,
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


def _extract_confidence_interval(estimate: Any) -> list[float]:
    """Best-effort CI from a DoWhy ``CausalEstimate``; never raises."""
    try:
        getter = getattr(estimate, "get_confidence_intervals", None)
        if callable(getter):
            return _normalize_confidence_interval(getter())
    except Exception:  # noqa: S110 - DoWhy versions expose CI through different optional APIs
        pass
    for attr in ("confidence_intervals", "confidence_interval"):
        try:
            raw = getattr(estimate, attr, None)
            if raw is not None:
                return _normalize_confidence_interval(raw)
        except Exception:  # noqa: S112 - malformed optional CI attributes are tried in fallback order
            continue
    return []
