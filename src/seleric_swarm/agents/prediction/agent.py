"""PredictionAgent - the stable boundary for the Coordinator / swarm.

    result = await PredictionAgent(...).predict(request)

Runs the LangGraph workflow and returns a :class:`PredictionResult` carrying the
``ForecastArtifact`` and forecast ``Claim[]`` - the contracts the Skeptic's model
+ forecast validators consume. Deterministic; the optional reasoning model only
writes a narrative string and never a number.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from seleric_swarm.agents.prediction.context import PredictionContext, PredictionDeps
from seleric_swarm.agents.prediction.contracts import PredictionRequest, PredictionResult
from seleric_swarm.agents.prediction.graph import build_prediction_graph
from seleric_swarm.agents.prediction.policies import PredictionPolicies
from seleric_swarm.agents.prediction.prompts import PREDICTION_SYSTEM_PROMPT, narrative_user

_log = structlog.get_logger("seleric_swarm.agents.prediction")

_COMPILED_GRAPH = None


def _graph():
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_prediction_graph()
    return _COMPILED_GRAPH


class PredictionAgent:
    agent_id = "prediction_agent"
    agent_version = "1.0.0"

    def __init__(
        self,
        *,
        deps: PredictionDeps | None = None,
        policies: PredictionPolicies | None = None,
    ) -> None:
        self.deps = deps or PredictionDeps()
        self.policies = policies or PredictionPolicies.load()

    async def predict(self, request: PredictionRequest) -> PredictionResult:
        started = time.perf_counter()
        ctx = PredictionContext(request=request, policies=self.policies, deps=self.deps)
        final_state = await _graph().ainvoke(
            {
                "mission_id": request.mission_id,
                "prediction_run_id": f"PREDRUN-{int(started * 1000) % 10_000_000}",
                "request": request.model_dump(exclude={"observations"}),
                "_context": ctx,
            }
        )
        result: PredictionResult = final_state["_result"]

        # optional narrative (LLM; failure is non-fatal, numbers unaffected)
        fa = result.forecast_artifact
        if result.has_forecast() and fa is not None:
            try:
                text = await self.deps.reasoning.generate_text(
                    system=PREDICTION_SYSTEM_PROMPT,
                    user=narrative_user(
                        result.target_metric,
                        result.horizon,
                        fa.prediction,
                        fa.interval,
                        [s.model_dump() for s in result.scenarios],
                        result.confidence,
                    ),
                    tags=["prediction", "narrative"],
                )
                if text:
                    result.audit["narrative"] = text.strip()
            except Exception as exc:
                _log.warning("prediction.narrative_failed", error=str(exc))

        self._emit(result, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        return result

    def _emit(self, result: PredictionResult, *, elapsed_ms: float) -> None:
        _log.info(
            "prediction.run",
            mission_id=result.mission_id,
            prediction_run_id=result.prediction_run_id,
            target_metric=result.target_metric,
            horizon=result.horizon,
            source=result.source,
            applicability=result.applicability,
            confidence=result.confidence,
            has_forecast=result.has_forecast(),
            scenarios=len(result.scenarios),
            claims=len(result.claims),
            synthetic=result.synthetic,
            elapsed_ms=elapsed_ms,
        )


def prediction_deps_from_blackboard(blackboard: Any, *, base: PredictionDeps | None = None) -> PredictionDeps:
    from seleric_swarm.agents.prediction.registries import repositories_from_blackboard

    base = base or PredictionDeps()
    evidence_repo, artifact_repo = repositories_from_blackboard(blackboard)
    return PredictionDeps(
        evidence_repo=evidence_repo,
        artifact_repo=artifact_repo,
        model_registry=base.model_registry,
        feature_store=base.feature_store,
        model_service=base.model_service,
        baseline=base.baseline,
        drift_monitor=base.drift_monitor,
        reasoning=base.reasoning,
    )
