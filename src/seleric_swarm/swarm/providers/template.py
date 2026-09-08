"""Baseline quantitative providers for the swarm.

None of these are placeholders — they are the honest, minimum-assumption
implementations for the seams whose Protocol contracts live in
``providers.base``. Real ML/DoWhy/forecaster implementations replace them by
being registered in the ``ProviderBundle``; they never call the network or an
LLM here.

Design rules:

* No hardcoded thresholds, magic constants, metric-specific rules, or scenario
  glue. Every constant is either a mathematical property (e.g. the logistic
  denominator, the MAD→σ constant), or supplied by the caller as data.
* Every result carries ``data_origin`` / ``synthetic`` from its inputs so
  downstream governance (claim gate, skeptic) can enforce provenance rules.
* Missing inputs are reported (via quality flags / empty results / explicit
  ``no_baseline`` counters), never fabricated.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.swarm.providers.base import (
    AnomalyFinding,
    CausalQuery,
    CausalResult,
    ForecastResult,
    MetricReading,
    RankedInterventions,
    StatResult,
)


class RelativeEffectAnomalyDetector:
    """Rank readings by relative effect vs the baseline the provider supplied.

    The DataProvider is contractually responsible for producing a baseline
    (``compare_period`` in the MCP data path). This detector's job is to
    quantify the deviation and score it — not to invent a "normal band". It
    therefore does not gate on a threshold: every reading with a valid
    baseline becomes an ``AnomalyFinding``, ranked by score. Downstream policy
    (Skeptic magnitude validators, Coordinator leadership frontier, Diagnostic
    hypothesis ranking) decides what is material — the correct architectural
    place for that decision.

    Readings without a numeric baseline are skipped and counted, so the
    caller can surface ``NO_BASELINE`` as a mission limitation rather than
    fabricating an "anomaly at zero".
    """

    method = "relative_effect_size"

    def __init__(self, *, name: str = "relative_effect_v1") -> None:
        self._name = name

    @staticmethod
    def _score(relative_change: float) -> float:
        """Monotone logistic map |x| → [0, 1) with no clipping.

        s(0) = 0, s(1) = 0.5, s(9) = 0.9, s(∞) → 1. Preserves ordering by
        magnitude and needs no calibration constant.
        """

        x = abs(relative_change)
        return round(x / (1.0 + x), 6)

    async def detect(
        self, readings: list[MetricReading], *, context: dict[str, Any]
    ) -> list[AnomalyFinding]:
        start = context.get("degradation_started_at") if context else None
        findings: list[AnomalyFinding] = []
        no_baseline: list[str] = []

        for reading in readings:
            if reading.value is None:
                continue
            if reading.baseline is None or reading.baseline == 0:
                no_baseline.append(reading.metric_id)
                continue

            baseline = float(reading.baseline)
            relative_change = (float(reading.value) - baseline) / abs(baseline)

            # Direction of actual move
            if relative_change > 0:
                direction = "up"
            elif relative_change < 0:
                direction = "down"
            else:
                direction = "unknown"

            # direction_bad from the reading tells us which direction is adverse
            direction_bad = reading.direction_bad or "up"
            adverse = (direction == direction_bad) if direction != "unknown" else False

            magnitude_score = self._score(relative_change)
            # Adversity score: magnitude when moving in the bad direction, else 0
            adversity_score = magnitude_score if adverse else 0.0

            findings.append(
                AnomalyFinding(
                    metric_id=reading.metric_id,
                    observed=float(reading.value),
                    # Empty expected_range signals "no variance information from
                    # provider"; callers needing a band use the point baseline.
                    expected_range=[],
                    deviation_pct=round(relative_change * 100.0, 2),
                    score=magnitude_score,  # back-compat: magnitude
                    magnitude_score=magnitude_score,
                    adversity_score=adversity_score,
                    direction=direction,
                    direction_bad=direction_bad,
                    adverse=adverse,
                    detector={
                        "name": self._name,
                        "method": self.method,
                        "baseline_source": "provider_compare_period",
                    },
                    dimensions=dict(reading.dimensions or {}),
                    start_time=start,
                    data_origin=reading.data_origin or "MCP",
                    synthetic=bool(reading.synthetic),
                )
            )

        # Sort by adversity first (bad moves up front), then by magnitude.
        # Downstream consumers (leadership frontier, skeptic) apply their own
        # materiality thresholds; we don't filter here to avoid threshold drift.
        findings.sort(key=lambda f: (f.adversity_score, f.magnitude_score), reverse=True)

        if no_baseline and context is not None:
            context.setdefault("no_baseline_metrics", []).extend(sorted(set(no_baseline)))

        return findings


class TemplateCausalEngine:
    """Association-only causal seam.

    Real causal estimation lives behind ``SwarmDiagnosticSpecialist``, which
    routes to ``agents/diagnostic`` (DoWhy + registered causal graphs). This
    seam is a *contract-honest* placeholder for callers that have not enabled
    the full diagnostic subsystem: it returns an ``ASSOCIATION_ONLY``-tier
    result with every declared refuter marked failed so no consumer can mistake
    its output for a validated causal claim.
    """

    async def estimate(self, query: CausalQuery, *, context: dict[str, Any]) -> CausalResult:
        del context
        return CausalResult(
            treatment=query.treatment,
            outcome=query.outcome,
            effect=None,
            effect_ci=[],
            refutations=[
                {"name": name, "passed": False, "note": "template_engine_association_only"}
                for name in query.refuters
            ],
            passed=False,
            estimator=query.estimator,
            graph_id=query.graph_id,
            data_origin="TEMPLATE",
            synthetic=True,
        )


class TemplateForecaster:
    """Insufficient-evidence forecaster.

    Per ``03-ml-causal.mdc`` #2 the Prediction Agent must use only registered
    / validated models. This seam returns ``None`` so a mission with no
    registered model routes to ``INSUFFICIENT_EVIDENCE`` instead of emitting
    a fabricated forecast.
    """

    async def forecast(
        self, *, target: str, horizon: str, features: dict[str, Any]
    ) -> ForecastResult | None:
        del target, horizon, features
        return None


class TemplateOptimizer:
    """Ranking pass-through.

    Preserves the caller's option order and marks the first entry as the
    recommendation; no scoring model. Real optimizers (bandit / MILP / rule
    engine) plug in via ``ProviderBundle.optimizer``.
    """

    async def rank(
        self, *, problem: dict[str, Any], options: list[dict[str, Any]]
    ) -> RankedInterventions:
        del problem
        recommended = [str(o["action"]) for o in options[:1] if o.get("action")]
        return RankedInterventions(
            options=list(options),
            recommended=recommended,
            data_origin="TEMPLATE",
            synthetic=True,
        )


class TemplateStatsEngine:
    """Report-only stats seam for missions without a registered stats engine.

    Every ``check`` returns ``passed=False`` with an explicit ``no_engine``
    reason. The Skeptic then records this as an unresolved attack rather than
    treating a missing stats engine as an implicit pass — the honest failure
    mode.
    """

    async def check(self, *, name: str, data: dict[str, Any]) -> StatResult:
        return StatResult(
            name=name,
            passed=False,
            detail={"reason": "no_stats_engine_registered", "inputs": sorted(data.keys())},
            data_origin="TEMPLATE",
            synthetic=True,
        )


# Legacy import alias (v1.11 provider bundle). New code should import
# ``RelativeEffectAnomalyDetector`` directly.
TemplateAnomalyDetector = RelativeEffectAnomalyDetector
