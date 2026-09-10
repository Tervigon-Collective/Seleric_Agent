"""DoWhy-backed ``CausalEstimationService``.

When ``observations`` is a pandas DataFrame it runs
``seleric_swarm.causal.dowhy_service.DoWhyService`` (identify -> estimate ->
refute) and returns a fully-populated ``CausalAnalysisArtifact``. Without
observations it falls back to an optional template service (or a bare
metadata artifact) and records that the estimate is metadata-only.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from seleric_swarm.agents.diagnostic.contracts import CausalAnalysisArtifact
from seleric_swarm.agents.diagnostic.registries import (
    CausalEstimationQuery,
    CausalEstimationService,
    TemplateCausalEstimationService,
    _stable_id,
)
from seleric_swarm.causal.dowhy_service import CausalRequest, DoWhyService, DoWhyUnavailable

_log = structlog.get_logger("seleric_swarm.agents.diagnostic.services.dowhy")


class DoWhyCausalEstimationService:
    def __init__(
        self,
        *,
        dowhy: DoWhyService | None = None,
        fallback: CausalEstimationService | None = None,
    ) -> None:
        self._dowhy = dowhy or DoWhyService()
        self._fallback = fallback or TemplateCausalEstimationService()

    async def estimate(
        self, query: CausalEstimationQuery, *, observations: Any = None
    ) -> CausalAnalysisArtifact:
        if observations is None:
            art = await self._fallback.estimate(query, observations=None)
            art.limitations.append("DoWhy estimation skipped: no observation frame supplied.")
            return art

        try:
            # DoWhy estimation is synchronous and CPU-bound (identify -> estimate
            # -> one re-estimate per refuter). Run it off the event loop so the
            # loop stays responsive AND the caller's asyncio.wait_for runtime
            # budget can actually cancel/observe it.
            est = await asyncio.to_thread(
                self._dowhy.estimate,
                CausalRequest(
                    treatment=query.treatment,
                    outcome=query.outcome,
                    common_causes=list(query.common_causes),
                    graph_id=query.graph_id,
                    estimator=query.estimator or "backdoor.linear_regression",
                    refuters=query.refuters
                    or ["placebo_treatment_refuter", "random_common_cause", "data_subset_refuter"],
                ),
                observations,
            )
        except DoWhyUnavailable as exc:
            _log.warning("diagnostic.dowhy.unavailable", error=str(exc))
            art = await self._fallback.estimate(query, observations=None)
            art.limitations.append(f"DoWhy estimation unavailable ({exc}); used fallback.")
            return art

        refutations = est.as_dict()["refutations"]
        # A refuter that *errored* (an environment / data-shape quirk) is
        # tolerated; a refuter that ran and *contradicted* the effect is not.
        # Require >= 2 refuters configured, no active contradiction, and at
        # least 2 refuters that actually completed. This keeps a single flaky
        # (e.g. stochastic placebo permutation) run from silently demoting a
        # real causal finding to "inconclusive".
        errored = sum(1 for r in refutations if "error" in r)
        contradicted = sum(1 for r in refutations if not r.get("passed") and "error" not in r)
        passed = len(refutations) >= 2 and contradicted == 0 and (len(refutations) - errored) >= 2
        return CausalAnalysisArtifact(
            causal_id=_stable_id("CAUS", query.mission_id, query.treatment, query.outcome, str(est.n_rows)),
            mission_id=query.mission_id,
            treatment=query.treatment,
            outcome=query.outcome,
            graph_id=query.graph_id,
            common_causes=list(query.common_causes),
            estimator=est.estimator,
            estimator_parameters={"n_rows": est.n_rows},
            estimated_effect=est.effect,
            confidence_interval=[],
            sample_size=est.n_rows,
            refutation_results=refutations,
            assumptions=[
                "backdoor adjustment for the listed common causes",
                "no unmeasured confounding beyond the adjustment set",
            ],
            limitations=["Unmeasured confounding cannot be completely excluded."],
            treatment_started_at=query.treatment_started_at,
            outcome_started_at=query.outcome_started_at,
            passed=bool(passed),
            synthetic=False,
        )
