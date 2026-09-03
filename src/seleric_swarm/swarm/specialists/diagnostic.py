"""Diagnostic - "Why did it change?" (architecture sec. 7-9).

Never Evidence -> LLM -> root cause. Always: hypotheses -> tests -> reject/retain
-> causal validation -> root-cause candidate. Hypotheses are explicit objects.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.swarm.artifacts import Causal, Hypothesis
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import CausalQuery
from seleric_swarm.swarm.specialists.base import SpecialistAgent

_GRAPH_ID = "causal.funnel_purchase.v1"


class DiagnosticAgent(SpecialistAgent):
    agent_id = "diagnostic_agent"
    capability = "causal_diagnosis"
    produces = "causal"

    def policy(self, blackboard: Blackboard, mission: SwarmMission) -> bool:
        return mission.wants("diagnostic") and bool(blackboard.by_type("anomaly"))

    # -- hypothesis generation (template rules; swap for an LLM component later)
    def _generate(self, blackboard: Blackboard) -> list[dict[str, Any]]:
        anomalies = {a["metric_id"]: a for a in blackboard.by_type("anomaly")}
        events = [e for e in blackboard.by_type("evidence") if str(e.get("metric_or_fact", "")).startswith("event.")]
        deploy = events[0] if events else None
        cvr = anomalies.get("metric.purchase_cvr")
        lcp = anomalies.get("metric.mobile_lcp_seconds")
        jserr = anomalies.get("metric.js_error_rate")

        hyps: list[dict[str, Any]] = []
        if cvr and (lcp or jserr) and deploy:
            hyps.append(
                {
                    "statement": (
                        f"Frontend deploy {deploy.get('provenance', {}).get('event_id')} raised mobile "
                        f"latency / JS errors, degrading mobile purchase conversion."
                    ),
                    "domains": ["technical", "funnel"],
                    "supporting_evidence": [x["artifact_id"] for x in (deploy, lcp, jserr, cvr) if x],
                    "required_tests": ["causal: mobile_lcp -> purchase_cvr", "temporal precedence vs deploy"],
                    "primary": True,
                }
            )
        # Alternative explanations kept explicit so the Skeptic can attack them.
        for stmt, dom in (
            ("Paid traffic quality deteriorated (worse-intent clicks).", ["performance"]),
            ("A price or discount change reduced mobile conversion.", ["commerce"]),
            ("Stock availability fell for mobile-heavy SKUs.", ["inventory"]),
            ("Conversion tracking broke, understating purchases.", ["technical"]),
        ):
            hyps.append({"statement": stmt, "domains": dom, "supporting_evidence": [], "required_tests": [], "primary": False})
        return hyps

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        raw = self._generate(blackboard)
        hyp_ids: list[str] = []
        primary_id: str | None = None
        for h in raw:
            art = Hypothesis.new(
                mission_id=blackboard.mission_id,
                created_by=self.agent_id,
                statement=h["statement"],
                domains=h["domains"],
                status="testing" if h["primary"] else "proposed",
                supporting_evidence=h["supporting_evidence"],
                required_tests=h["required_tests"],
                evidence_refs=h["supporting_evidence"],
            )
            # Hypothesis generation is template rules in the prototype, so every
            # hypothesis is synthetic. A real LLM generator would set this from
            # its own inputs / grounding.
            art.mark_synthetic()
            hid = blackboard.post(art)
            hyp_ids.append(hid)
            if h["primary"]:
                primary_id = hid

        posted = list(hyp_ids)
        if primary_id is None:
            blackboard.record_event("diagnostic_no_primary")
            return posted

        query = CausalQuery(
            treatment="metric.mobile_lcp_seconds",
            outcome="metric.purchase_cvr",
            common_causes=["metric.sessions", "device", "campaign", "metric.return_rate"],
            graph_id=_GRAPH_ID,
        )
        result = await self.providers.causal.estimate(query, context={"mission_id": blackboard.mission_id})
        causal = Causal.new(
            mission_id=blackboard.mission_id,
            created_by=self.agent_id,
            hypothesis_ref=primary_id,
            treatment=result.treatment,
            outcome=result.outcome,
            common_causes=query.common_causes,
            graph_id=result.graph_id or _GRAPH_ID,
            estimator=result.estimator or query.estimator,
            effect=result.effect,
            effect_ci=result.effect_ci,
            refutations=result.refutations,
            passed=result.passed,
            data_origin=result.data_origin,  # type: ignore[arg-type]
            evidence_refs=[primary_id],
        )
        if result.synthetic:
            causal.mark_synthetic()
        posted.append(blackboard.post(causal))

        # Retain / reject based on causal validation.
        for hid in hyp_ids:
            if hid == primary_id:
                blackboard.update(hid, {"status": "retained" if result.passed else "testing"})
            else:
                blackboard.update(hid, {"status": "rejected" if result.passed else "proposed"})
        blackboard.record_event("diagnostic_done", primary=primary_id, causal_passed=result.passed)
        return posted
