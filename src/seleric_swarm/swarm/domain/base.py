"""Domain Agent base (architecture sec. 16-17).

A Domain Agent is: business-semantic authority + capability router + governed
data-access boundary + leadership node. All seven domains share this class; each
subclass is mostly declarative ``DomainConfig``. Improve this file once and every
domain improves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seleric_swarm.swarm.artifacts import Evidence
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.envelope import HandoffProposal
from seleric_swarm.swarm.providers.base import DataProvider


@dataclass
class DomainConfig:
    agent_id: str
    domain: str  # data-provider key
    owned_metrics: list[str] = field(default_factory=list)
    frontier_metrics: list[str] = field(default_factory=list)  # if quiet -> cause is downstream
    probe_metrics: list[str] = field(default_factory=list)
    probe_dimensions: list[dict[str, str]] = field(default_factory=lambda: [{}])
    # Sentinel metrics this domain checks as part of its own decomposition, even
    # though another domain owns them: metric_id -> owning *domain* key.
    sentinels: dict[str, str] = field(default_factory=dict)
    downstream: dict[str, str] = field(default_factory=dict)  # metric_id -> agent_id owning it
    handoff_targets: list[str] = field(default_factory=list)
    ontology: list[str] = field(default_factory=list)


@dataclass
class DomainAnalysis:
    agent_id: str
    summary: str
    owns_frontier: bool
    evidence_refs: list[str]


class DomainAgent:
    agent_class = "domain"

    def __init__(
        self,
        config: DomainConfig,
        data_provider: DataProvider | None,
        peers: dict[str, DataProvider] | None = None,
    ) -> None:
        self.config = config
        self.agent_id = config.agent_id
        self.data = data_provider
        self.peers = peers or {}

    # -- context / semantics --------------------------------------------------
    def resolve_metrics(self, hints: list[str]) -> list[str]:
        owned = [h for h in hints if h in self.config.owned_metrics]
        return owned or list(self.config.probe_metrics)

    # -- Observer delegates data retrieval here (only domains + Observer hold data access)
    async def observe(self, blackboard: Blackboard, *, time_range: dict[str, Any]) -> list[str]:
        if self.data is None:
            blackboard.record_event("observe_skipped", agent=self.agent_id, reason="no data provider")
            return []
        posted: list[str] = []
        for dims in self.config.probe_dimensions or [{}]:
            result = await self.data.fetch(
                metric_ids=self.config.probe_metrics,
                time_range=time_range,
                dimensions=dims or None,
            )
            for r in result.readings:
                ev = Evidence.new(
                    mission_id=blackboard.mission_id,
                    created_by=f"observer_agent@{self.agent_id}",
                    metric_or_fact=r.metric_id,
                    value=r.value,
                    baseline=r.baseline,
                    change_pct=r.change_pct,
                    unit=r.unit,
                    dimensions=r.dimensions,
                    time_range=time_range,
                    source=r.source,
                    data_origin=r.data_origin,  # type: ignore[arg-type]
                    provenance={"direction_bad": r.direction_bad, "domain": self.config.domain},
                )
                if r.synthetic:
                    ev.mark_synthetic()
                posted.append(blackboard.post(ev))
            for e in result.events:
                ev = Evidence.new(
                    mission_id=blackboard.mission_id,
                    created_by=f"observer_agent@{self.agent_id}",
                    metric_or_fact=f"event.{e.type}",
                    value=e.at,
                    unit=None,
                    time_range=time_range,
                    source=f"fixture.event.{e.event_id}",
                    data_origin=e.data_origin,  # type: ignore[arg-type]
                    provenance={"event_id": e.event_id, "description": e.description, "domain": e.domain},
                )
                if e.synthetic:
                    ev.mark_synthetic()
                posted.append(blackboard.post(ev))

        # Sentinel scan: a shallow read of downstream metrics this domain checks
        # as part of its own decomposition (architecture sec. 18). This is what
        # lets Performance *see* the post-click CVR problem and hand off.
        posted += await self._observe_sentinels(blackboard, time_range=time_range)

        blackboard.record_event("observed", agent=self.agent_id, evidence=len(posted))
        return posted

    async def _observe_sentinels(self, blackboard: Blackboard, *, time_range: dict[str, Any]) -> list[str]:
        posted: list[str] = []
        by_domain: dict[str, list[str]] = {}
        for metric_id, domain_key in self.config.sentinels.items():
            by_domain.setdefault(domain_key, []).append(metric_id)
        for domain_key, metric_ids in by_domain.items():
            provider = self.peers.get(domain_key)
            if provider is None:
                continue
            result = await provider.fetch(metric_ids=metric_ids, time_range=time_range)
            for r in result.readings:
                ev = Evidence.new(
                    mission_id=blackboard.mission_id,
                    created_by=f"observer_agent@{self.agent_id}",
                    prefix="EV-SENT",
                    metric_or_fact=r.metric_id,
                    value=r.value,
                    baseline=r.baseline,
                    change_pct=r.change_pct,
                    unit=r.unit,
                    dimensions=r.dimensions,
                    time_range=time_range,
                    source=r.source,
                    data_origin=r.data_origin,  # type: ignore[arg-type]
                    provenance={"direction_bad": r.direction_bad, "domain": domain_key, "sentinel": True},
                )
                if r.synthetic:
                    ev.mark_synthetic()
                posted.append(blackboard.post(ev))
        return posted

    # -- domain reasoning ---------------------------------------------------
    def reason(self, blackboard: Blackboard) -> DomainAnalysis:
        anomalies = blackboard.by_type("anomaly")
        owns_frontier = self._owns_frontier(anomalies)
        my_anoms = [a for a in anomalies if a.get("metric_id") in self.config.owned_metrics]
        if my_anoms:
            worst = max(my_anoms, key=lambda a: abs(a.get("deviation_pct") or 0))
            summary = (
                f"{self.agent_id}: {worst['metric_id']} moved {worst.get('deviation_pct')}% "
                f"({worst.get('direction')})."
            )
        else:
            summary = f"{self.agent_id}: owned metrics within expected band."
        return DomainAnalysis(
            agent_id=self.agent_id,
            summary=summary,
            owns_frontier=owns_frontier,
            evidence_refs=blackboard.evidence_ledger[-8:],
        )

    def _owns_frontier(self, anomalies: list[dict[str, Any]]) -> bool:
        frontier = set(self.config.frontier_metrics)
        if not frontier:
            return True  # terminal domain (e.g. technical): it diagnoses, never hands off
        return any(a.get("metric_id") in frontier for a in anomalies)

    # -- leadership handoff (architecture sec. 18-19, 32) -------------------
    def evaluate_handoff(self, blackboard: Blackboard) -> HandoffProposal | None:
        anomalies = blackboard.by_type("anomaly")
        if self._owns_frontier(anomalies):
            return None  # the cause is in my domain; keep leading
        foreign = [a for a in anomalies if a.get("metric_id") in self.config.downstream]
        if not foreign:
            return None
        target_anom = max(foreign, key=lambda a: abs(a.get("deviation_pct") or 0))
        to_agent = self.config.downstream[target_anom["metric_id"]]

        frontier_evidence = [
            ev["artifact_id"]
            for ev in blackboard.by_type("evidence")
            if ev.get("metric_or_fact") in self.config.frontier_metrics
        ]
        evidence_refs = frontier_evidence + [target_anom["artifact_id"]]
        if not evidence_refs:
            evidence_refs = blackboard.evidence_ledger[-3:]
        return HandoffProposal(
            from_agent=self.agent_id,
            to_agent=to_agent,
            reason=(
                f"{self.agent_id} frontier metrics {self.config.frontier_metrics} are within band "
                f"while {target_anom['metric_id']} degraded {target_anom.get('deviation_pct')}% - "
                f"the causal frontier is now in {to_agent}'s domain."
            ),
            evidence_refs=evidence_refs or ["EV-none"],
            unresolved_question=(
                f"Which {to_agent} factor drove {target_anom['metric_id']} "
                f"{target_anom.get('direction')} {target_anom.get('deviation_pct')}%?"
            ),
            requested_output="EvidenceBundle + AnomalyArtifact for the downstream domain",
            confidence=0.9,
        )
