"""DoWhy-backed ``CausalValidationService``.

Wraps ``seleric_swarm.causal.dowhy_service.DoWhyService``. Two modes:

* **audit-only** (default) - the upstream ``CausalAnalysisArtifact`` already
  carries an effect + refutation_results; we validate its metadata + refutation
  robustness exactly as ``BasicCausalValidationService`` does, but we can also
  *re-run* refuters when an observation frame is supplied.
* **re-estimate** - when ``context["observations"]`` is a DataFrame, actually run
  DoWhy (identify -> estimate -> refute) and fold the fresh refutation outcomes
  into the confidence decision.

If DoWhy is unavailable or estimation fails, it degrades to the metadata audit
and records the reason -- it never fabricates a causal pass.
"""

from __future__ import annotations

from typing import Any

import structlog

from seleric_swarm.agents.skeptic.contracts import CausalAnalysisArtifact
from seleric_swarm.agents.skeptic.registries import (
    BasicCausalValidationService,
    CausalGraphRegistry,
    CausalValidationResult,
)
from seleric_swarm.causal.dowhy_service import CausalRequest, DoWhyService, DoWhyUnavailable

_log = structlog.get_logger("seleric_swarm.agents.skeptic.services.dowhy")

_CONF_ORDER = [
    "REJECTED",
    "ASSOCIATION_ONLY",
    "PLAUSIBLE_CAUSAL",
    "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS",
    "STRONGLY_SUPPORTED",
]


class DoWhyCausalValidationService:
    def __init__(
        self,
        graphs: CausalGraphRegistry | None = None,
        *,
        dowhy: DoWhyService | None = None,
        min_refutations: int = 2,
    ) -> None:
        self._audit = BasicCausalValidationService(graphs)
        self._dowhy = dowhy or DoWhyService()
        self._min_refutations = min_refutations

    async def validate(
        self, artifact: CausalAnalysisArtifact, *, context: dict[str, Any]
    ) -> CausalValidationResult:
        base = await self._audit.validate(artifact, context=context)

        observations = context.get("observations")
        if observations is None:
            base.issues.append("DoWhy re-estimation skipped: no observation frame supplied (metadata audit only).")
            return base

        try:
            estimate = self._dowhy.estimate(
                CausalRequest(
                    treatment=artifact.treatment,
                    outcome=artifact.outcome,
                    common_causes=list(artifact.common_causes),
                    graph_id=artifact.graph_id,
                    estimator=artifact.estimator or "backdoor.linear_regression",
                    refuters=list(context.get("refuters") or [])
                    or ["placebo_treatment_refuter", "random_common_cause", "data_subset_refuter"],
                ),
                observations,
            )
        except DoWhyUnavailable as exc:
            _log.warning("skeptic.dowhy.unavailable", error=str(exc))
            base.issues.append(f"DoWhy re-estimation unavailable: {exc}. Fell back to metadata audit.")
            return base

        passed = estimate.refutations_passed
        total = len(estimate.refutations)
        base.refutations_passed = passed
        base.refutations_total = total
        base.detail = {
            **base.detail,
            "dowhy_effect": estimate.effect,
            "dowhy_refutations": estimate.as_dict()["refutations"],
            "dowhy_n_rows": estimate.n_rows,
        }

        # recompute confidence with the fresh refutation evidence
        confidence = base.confidence
        if not base.temporal_ok:
            confidence = "REJECTED"
        elif base.graph_ok and total >= self._min_refutations and passed == total and base.confounders_ok:
            confidence = "STRONGLY_SUPPORTED"
        elif base.graph_ok and total >= 1 and passed == total:
            confidence = "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS"
        elif passed < total:
            confidence = _downgrade(confidence)
        base.confidence = confidence

        sign_flip = (
            artifact.estimated_effect is not None
            and estimate.effect * artifact.estimated_effect < 0
        )
        if sign_flip:
            base.confidence = "REJECTED"
            base.issues.append(
                f"DoWhy effect ({estimate.effect:.3f}) has the opposite sign to the reported "
                f"effect ({artifact.estimated_effect:.3f})."
            )
        return base


def _downgrade(confidence: str) -> str:
    try:
        idx = _CONF_ORDER.index(confidence)
    except ValueError:
        return "PLAUSIBLE_CAUSAL"
    return _CONF_ORDER[max(0, idx - 1)]
