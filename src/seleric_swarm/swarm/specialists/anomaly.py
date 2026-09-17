"""Anomaly - "What changed in an unusual way?" (architecture sec. 6).

LLM decides *which* metric / window / detector class (not modelled here);
statistics decide expected vs observed. This prototype routes to
``AnomalyDetector`` (template = robust deviation band).
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.swarm.artifacts import Anomaly
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import MetricReading
from seleric_swarm.swarm.specialists.base import SpecialistAgent


class AnomalyAgent(SpecialistAgent):
    agent_id = "anomaly_agent"
    capability = "anomaly_analysis"
    produces = "anomaly"

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return bool(blackboard.by_type("evidence"))

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        evidence = blackboard.by_type("evidence")
        already = {
            (a["metric_id"], tuple(sorted((a.get("dimensions") or {}).items())))
            for a in blackboard.by_type("anomaly")
        }

        readings: list[MetricReading] = []
        for e in evidence:
            metric = e.get("metric_or_fact")
            if not metric or str(metric).startswith("event."):
                continue
            if e.get("value") is None:
                continue
            # No baseline requirement here: the template detector needs one
            # and skips readings without it (see TemplateAnomalyDetector's
            # own no_baseline handling); robust_zscore pulls its expected
            # band from BusinessStateService history and never reads
            # reading.baseline, so a missing baseline must not drop the row
            # before it reaches the detector.
            baseline = e.get("baseline")
            readings.append(
                MetricReading(
                    metric_id=metric,
                    value=float(e["value"]),
                    baseline=float(baseline) if baseline is not None else None,
                    unit=e.get("unit"),
                    dimensions=dict(e.get("dimensions") or {}),
                    direction_bad=(e.get("provenance") or {}).get("direction_bad", "up"),
                    data_origin=e.get("data_origin", "FIXTURE"),
                    synthetic=bool(e.get("synthetic")),
                )
            )

        detector = self.providers.anomaly
        if detector is None:
            blackboard.record_event("anomaly_provider_unavailable")
            return []

        # Build detector context from mission context (no scenario-specific glue)
        detect_ctx: dict[str, Any] = {}
        # mission.context["time_range"] is never actually populated anywhere
        # in the mission graph (Template doesn't need it, so this went
        # unnoticed) -- the real resolved window lives on mission.time_range
        # itself. A history-based detector (BusinessStateService's
        # robust_zscore) needs a real window, so fall back to that field.
        window = (mission.context or {}).get("time_range") or mission.time_range
        if window:
            detect_ctx["time_range"] = window
        # A "why" mission wants real BusinessStateService history for
        # whatever Observer fetched (the asked metric + its co-movers, not
        # the full catalogue) -- not just the commerce/spend/net_profit
        # subset config/provider_registry.yaml defaults to. Lookup/overview
        # missions never set this intent, so they keep the current default.
        if mission.wants("diagnostic") or mission.wants("executive_health"):
            detect_ctx["force_robust_zscore"] = True

        findings = await detector.detect(readings, context=detect_ctx)

        posted: list[str] = []
        for f in findings:
            key = (f.metric_id, tuple(sorted((f.dimensions or {}).items())))
            if key in already:
                continue
            try:
                art = Anomaly.new(
                    mission_id=blackboard.mission_id,
                    created_by=self.agent_id,
                    metric_id=f.metric_id,
                    observed=f.observed,
                    expected_range=f.expected_range,
                    deviation_pct=f.deviation_pct,
                    score=f.score,
                    magnitude_score=f.magnitude_score,
                    adversity_score=f.adversity_score,
                    direction_bad=f.direction_bad,  # type: ignore[arg-type]
                    adverse=f.adverse,
                    detector=f.detector,
                    dimensions=f.dimensions,
                    start_time=f.start_time,
                    direction=f.direction,  # type: ignore[arg-type]
                    data_origin=f.data_origin,  # type: ignore[arg-type]
                    evidence_refs=[
                        e["artifact_id"]
                        for e in evidence
                        if e.get("metric_or_fact") == f.metric_id
                    ],
                )
            except Exception as exc:
                blackboard.record_event("anomaly_creation_failed", metric_id=f.metric_id, error=str(exc))
                continue

            if f.synthetic:
                art.mark_synthetic()
            ok, problems = self.validate(art.model_dump())
            if not ok:
                blackboard.record_event("anomaly_rejected", problems=problems)
                continue
            posted.append(blackboard.post(art))

        blackboard.record_event("anomaly_done", found=len(posted))
        return posted
