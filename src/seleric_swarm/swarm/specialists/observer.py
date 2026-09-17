"""Observer - "What is actually happening?" (architecture sec. 4).

The grounding agent. It does not interpret; it delegates governed data retrieval
to the Domain Agent that currently leads and posts EvidenceArtifacts.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.swarm.artifacts import Evidence
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.domain.base import DomainAgent
from seleric_swarm.swarm.mission import SwarmMission
from seleric_swarm.swarm.providers.base import ProviderBundle
from seleric_swarm.swarm.specialists.base import SpecialistAgent


class ObserverAgent(SpecialistAgent):
    agent_id = "observer_agent"
    capability = "metric_observation"
    produces = "evidence"

    def __init__(self, providers: ProviderBundle, domains: dict[str, DomainAgent]) -> None:
        super().__init__(providers)
        self._domains = domains

    async def run(self, blackboard: Blackboard, mission: SwarmMission) -> list[str]:
        lead = blackboard.mission_lead or mission.initial_lead
        domain = self._domains.get(lead)
        if domain is None:
            blackboard.record_event("observe_no_domain", lead=lead)
            return []
        extra_metrics = _asked_metrics(mission, lead=lead)
        grain = _asked_grain(mission, lead=lead)

        tr = mission.time_range or {}
        if tr.get("kind") != "comparison" or not tr.get("start_b") or not tr.get("end_b"):
            return await domain.observe(
                blackboard, time_range=tr, extra_metrics=extra_metrics, grain=grain
            )

        # "June vs August" etc: fetch both periods, then post one delta
        # Evidence per (metric, dims) pair found in both. domain.observe()
        # already knows how to fetch a real range (a month, a week, ...),
        # not just a single day — comparison just calls it twice.
        period_a_ids = await domain.observe(
            blackboard,
            time_range={"start": tr.get("start"), "end": tr.get("end")},
            extra_metrics=extra_metrics,
            grain=grain,
        )
        period_b_ids = await domain.observe(
            blackboard,
            time_range={"start": tr.get("start_b"), "end": tr.get("end_b")},
            extra_metrics=extra_metrics,
            grain=grain,
        )
        delta_ids = _post_comparison_deltas(
            blackboard, agent_id=domain.agent_id, period_a_ids=period_a_ids, period_b_ids=period_b_ids
        )
        return period_a_ids + period_b_ids + delta_ids


def _post_comparison_deltas(
    blackboard: Blackboard,
    *,
    agent_id: str,
    period_a_ids: list[str],
    period_b_ids: list[str],
) -> list[str]:
    """One delta Evidence per (metric, dims) pair present in both periods.

    Delta is always period_a - period_b (the order the question named them
    in) so a decline reads negative regardless of which period is
    chronologically earlier — matches the lookup pipeline's convention
    (services/intelligence/observer.py's _comparison_deltas).
    """

    def _by_metric(ids: list[str]) -> dict[tuple[str, tuple[tuple[str, Any], ...]], dict[str, Any]]:
        out: dict[tuple[str, tuple[tuple[str, Any], ...]], dict[str, Any]] = {}
        for aid in ids:
            row = blackboard.get(aid)
            if not row or row.get("metric_or_fact", "").endswith(".delta"):
                continue
            key = (row["metric_or_fact"], tuple(sorted((row.get("dimensions") or {}).items())))
            out[key] = row
        return out

    a_by_metric = _by_metric(period_a_ids)
    b_by_metric = _by_metric(period_b_ids)
    posted: list[str] = []
    for key, a_row in a_by_metric.items():
        b_row = b_by_metric.get(key)
        if b_row is None or a_row.get("value") is None or b_row.get("value") is None:
            continue
        a_range = a_row.get("time_range") or {}
        b_range = b_row.get("time_range") or {}
        starts = [d for d in (a_range.get("start"), b_range.get("start")) if d]
        ends = [d for d in (a_range.get("end"), b_range.get("end")) if d]
        ev = Evidence.new(
            mission_id=a_row["mission_id"],
            created_by=f"observer_agent@{agent_id}",
            metric_or_fact=f"{key[0]}.delta",
            value=float(a_row["value"]) - float(b_row["value"]),
            unit=a_row.get("unit"),
            dimensions=dict(a_row.get("dimensions") or {}),
            time_range={"start": min(starts) if starts else None, "end": max(ends) if ends else None},
            source="deterministic.metrics",
            provenance={
                "calculation": "period_a - period_b",
                "period_a_evidence_id": a_row["artifact_id"],
                "period_b_evidence_id": b_row["artifact_id"],
            },
        )
        posted.append(blackboard.post(ev))
    return posted


def _asked_metrics(mission: SwarmMission, *, lead: str | None = None) -> list[str]:
    ctx = mission.context or {}
    domain = (lead or "").removesuffix("_agent")
    for dq in ctx.get("domain_questions") or []:
        if not isinstance(dq, dict):
            continue
        if dq.get("domain") == domain and dq.get("metrics"):
            return [str(m) for m in dq["metrics"] if m]
    out: list[str] = []
    for key in ("primary_metric", "resolved_metric"):
        value = ctx.get(key)
        if value and str(value) not in out:
            out.append(str(value))
    for hint in ctx.get("metric_hints") or []:
        if hint and str(hint) not in out:
            out.append(str(hint))
    return out


def _asked_grain(mission: SwarmMission, *, lead: str | None = None) -> list[str]:
    ctx = mission.context or {}
    domain = (lead or "").removesuffix("_agent")
    for dq in ctx.get("domain_questions") or []:
        if not isinstance(dq, dict):
            continue
        if dq.get("domain") == domain:
            return [str(g) for g in (dq.get("grain") or []) if g]
    return []
