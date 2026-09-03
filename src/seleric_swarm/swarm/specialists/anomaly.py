"""Anomaly - "What changed in an unusual way?" (architecture sec. 6).

LLM decides *which* metric / window / detector class (not modelled here);
statistics decide expected vs observed. This prototype routes to
``AnomalyDetector`` (template = robust deviation band).
"""

from __future__ import annotations

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
        already = {(a["metric_id"], tuple(sorted((a.get("dimensions") or {}).items()))) for a in blackboard.by_type("anomaly")}

        deploy_at = next(
            (e["value"] for e in evidence if str(e.get("metric_or_fact", "")).startswith("event.")),
            None,
        )
        readings: list[MetricReading] = []
        for e in evidence:
            metric = e.get("metric_or_fact")
            if not metric or str(metric).startswith("event."):
                continue
            if e.get("value") is None or e.get("baseline") is None:
                continue
            readings.append(
                MetricReading(
                    metric_id=metric,
                    value=float(e["value"]),
                    baseline=float(e["baseline"]),
                    unit=e.get("unit"),
                    dimensions=dict(e.get("dimensions") or {}),
                    direction_bad=(e.get("provenance") or {}).get("direction_bad", "up"),
                    data_origin=e.get("data_origin", "FIXTURE"),
                    synthetic=bool(e.get("synthetic")),
                )
            )

        findings = await self.providers.anomaly.detect(
            readings, context={"degradation_started_at": deploy_at}
        )
        posted: list[str] = []
        for f in findings:
            key = (f.metric_id, tuple(sorted((f.dimensions or {}).items())))
            if key in already:
                continue
            art = Anomaly.new(
                mission_id=blackboard.mission_id,
                created_by=self.agent_id,
                metric_id=f.metric_id,
                observed=f.observed,
                expected_range=f.expected_range,
                deviation_pct=f.deviation_pct,
                score=f.score,
                detector=f.detector,
                dimensions=f.dimensions,
                start_time=f.start_time,
                direction=f.direction,  # type: ignore[arg-type]
                data_origin=f.data_origin,  # type: ignore[arg-type]
                evidence_refs=[e["artifact_id"] for e in evidence if e.get("metric_or_fact") == f.metric_id],
            )
            if f.synthetic:
                art.mark_synthetic()
            ok, problems = self.validate(art.model_dump())
            if not ok:
                blackboard.record_event("anomaly_rejected", problems=problems)
                continue
            posted.append(blackboard.post(art))
        blackboard.record_event("anomaly_done", found=len(posted))
        return posted
