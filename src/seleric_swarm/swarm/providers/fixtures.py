"""Fixture-backed provider implementations (deterministic, offline, SYNTHETIC).

They read ``data/fixtures/scenarios/<id>.json`` so the reference mission produces
meaningful numbers - real enough to exercise anomaly detection, handoffs,
diagnosis, forecast, strategy and skeptic behaviour - but every value is stamped
``data_origin="FIXTURE"`` / ``synthetic=true`` and never presented as real data.

To go live: implement the same Protocols against MCP / your model registry and
pass a different ``ProviderBundle`` to ``run_swarm_mission``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from seleric_swarm.paths import repo_root
from seleric_swarm.swarm.providers.base import (
    AnomalyFinding,
    CausalQuery,
    CausalResult,
    DataResult,
    DomainEvent,
    ForecastResult,
    MetricReading,
    ProviderBundle,
    RankedInterventions,
    StatResult,
)

DEFAULT_SCENARIO = "cac_regression"


def load_scenario(scenario_id: str = DEFAULT_SCENARIO) -> dict[str, Any]:
    path = repo_root() / "data" / "fixtures" / "scenarios" / f"{scenario_id}.json"
    return json.loads(Path(path).read_text(encoding="utf-8"))


class FixtureDataProvider:
    """One per business domain; serves that domain's slice of the scenario."""

    def __init__(self, domain: str, scenario: dict[str, Any]) -> None:
        self.domain = domain
        self._scenario = scenario
        self._slice = scenario.get("domains", {}).get(domain, {})

    async def fetch(
        self,
        *,
        metric_ids: list[str],
        time_range: dict[str, Any],
        dimensions: dict[str, Any] | None = None,
    ) -> DataResult:
        metrics = self._slice.get("metrics", {})
        segments = self._slice.get("segments", {})
        readings: list[MetricReading] = []
        missing: list[str] = []
        want = metric_ids or list(metrics)
        for mid in want:
            spec = metrics.get(mid)
            if spec is None:
                missing.append(mid)
                continue
            readings.append(
                MetricReading(
                    metric_id=mid,
                    value=float(spec["current"]),
                    baseline=float(spec["baseline"]),
                    unit=spec.get("unit"),
                    direction_bad=spec.get("direction_bad", "up"),
                    dimensions={},
                    data_origin="FIXTURE",
                    synthetic=True,
                    source=f"fixture.scenario.{self._scenario.get('scenario_id')}",
                )
            )
        # Segment readings (e.g. device=mobile) when asked.
        dim = (dimensions or {})
        for dim_name, dim_value in dim.items():
            seg = segments.get(dim_name, {}).get(dim_value, {})
            for mid, spec in seg.items():
                readings.append(
                    MetricReading(
                        metric_id=mid,
                        value=float(spec["current"]),
                        baseline=float(spec["baseline"]),
                        unit=metrics.get(mid, {}).get("unit"),
                        direction_bad=metrics.get(mid, {}).get("direction_bad", "up"),
                        dimensions={dim_name: dim_value},
                        data_origin="FIXTURE",
                        synthetic=True,
                        source=f"fixture.scenario.{self._scenario.get('scenario_id')}",
                    )
                )
        return DataResult(
            readings=readings,
            events=await self.events(time_range=time_range),
            missing=missing,
            data_origin="FIXTURE",
            synthetic=True,
        )

    async def events(self, *, time_range: dict[str, Any]) -> list[DomainEvent]:
        out: list[DomainEvent] = []
        for ev in self._scenario.get("events", []):
            if ev.get("domain") == self.domain:
                out.append(
                    DomainEvent(
                        event_id=ev["event_id"],
                        type=ev["type"],
                        domain=ev["domain"],
                        at=ev["at"],
                        description=ev.get("description", ""),
                        data_origin="FIXTURE",
                        synthetic=True,
                    )
                )
        return out


class TemplateAnomalyDetector:
    """Robust deviation band: |current - baseline| / baseline beyond a threshold.

    Replace with STL residual / IsolationForest / change-point per the Anomaly ML
    contract; keep the ``AnomalyFinding`` return shape.
    """

    def __init__(self, rel_threshold: float = 0.10) -> None:
        self._t = rel_threshold

    async def detect(
        self, readings: list[MetricReading], *, context: dict[str, Any]
    ) -> list[AnomalyFinding]:
        out: list[AnomalyFinding] = []
        for r in readings:
            if r.value is None or not r.baseline:
                continue
            rel = (r.value - r.baseline) / r.baseline
            if abs(rel) < self._t:
                continue
            lo, hi = sorted((r.baseline * (1 - self._t), r.baseline * (1 + self._t)))
            out.append(
                AnomalyFinding(
                    metric_id=r.metric_id,
                    observed=r.value,
                    expected_range=[round(lo, 4), round(hi, 4)],
                    deviation_pct=round(rel * 100.0, 2),
                    score=min(0.99, round(abs(rel) / self._t * 0.25, 2)),
                    direction="up" if rel > 0 else "down",
                    detector={"id": "template.robust_band.v0", "rel_threshold": self._t},
                    dimensions=dict(r.dimensions),
                    start_time=context.get("degradation_started_at"),
                    data_origin="STATS",
                    synthetic=True,
                )
            )
        return out


class TemplateCausalEngine:
    """Returns the scenario's declared causal truth. Replace with DoWhy/EconML."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self._truth = scenario.get("causal_truth", {})

    async def estimate(self, query: CausalQuery, *, context: dict[str, Any]) -> CausalResult:
        t = self._truth
        matches = t.get("treatment") == query.treatment and t.get("outcome") == query.outcome
        if not matches:
            return CausalResult(
                treatment=query.treatment,
                outcome=query.outcome,
                effect=None,
                effect_ci=[],
                refutations=[],
                passed=False,
                estimator=query.estimator,
                graph_id=query.graph_id,
                data_origin="TEMPLATE",
                synthetic=True,
            )
        return CausalResult(
            treatment=query.treatment,
            outcome=query.outcome,
            effect=float(t["effect"]),
            effect_ci=list(t.get("effect_ci", [])),
            refutations=list(t.get("refutations", [])),
            passed=bool(t.get("passed", False)),
            estimator=query.estimator,
            graph_id=t.get("graph_id", query.graph_id),
            data_origin="TEMPLATE",
            synthetic=True,
        )


class TemplateForecaster:
    """Returns the scenario's declared forecast truth. Replace with model registry."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self._truth = scenario.get("forecast_truth", {})

    async def forecast(
        self, *, target: str, horizon: str, features: dict[str, Any]
    ) -> ForecastResult | None:
        t = self._truth
        if t.get("target") != target:
            return None
        return ForecastResult(
            target=target,
            horizon=t.get("horizon", horizon),
            prediction=t.get("prediction"),
            interval=list(t.get("interval", [])),
            model=dict(t.get("model", {})),
            drift_status=t.get("model", {}).get("drift_status"),
            secondary=dict(t.get("secondary", {})),
            data_origin="MODEL",
            synthetic=True,
        )


class TemplateOptimizer:
    """Ranks interventions by mechanism-fit first (architecture sec. 12)."""

    _RANK: ClassVar[dict[str, int]] = {"very_high": 4, "high": 3, "medium": 2, "low": 1}

    async def rank(
        self, *, problem: dict[str, Any], options: list[dict[str, Any]]
    ) -> RankedInterventions:
        scored = sorted(
            options,
            key=lambda o: (
                self._RANK.get(str(o.get("mechanism_fit", "low")), 1),
                self._RANK.get(str(o.get("expected_impact", "low")), 1),
                -self._RANK.get(str(o.get("risk", "high")), 3),
            ),
            reverse=True,
        )
        recommended = [o["action"] for o in scored[:2]] if scored else []
        return RankedInterventions(options=scored, recommended=recommended, data_origin="TEMPLATE", synthetic=True)


class TemplateStatsEngine:
    """Deterministic pass-through for refutation-style checks. Replace with scipy/statsmodels."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self._scenario = scenario

    async def check(self, *, name: str, data: dict[str, Any]) -> StatResult:
        # Prototype: alternative-explanation probes all "pass" (no confounder found)
        # because the fixture encodes a single clean mechanism.
        return StatResult(name=name, passed=True, detail={"note": "template check", **data}, data_origin="STATS", synthetic=True)


def build_fixture_bundle(scenario_id: str = DEFAULT_SCENARIO) -> ProviderBundle:
    scenario = load_scenario(scenario_id)
    domains = list(scenario.get("domains", {}))
    return ProviderBundle(
        data={d: FixtureDataProvider(d, scenario) for d in domains},
        anomaly=TemplateAnomalyDetector(),
        causal=TemplateCausalEngine(scenario),
        forecaster=TemplateForecaster(scenario),
        optimizer=TemplateOptimizer(),
        stats=TemplateStatsEngine(scenario),
    )
