"""Domain Agent base (architecture sec. 16-17).

A Domain Agent is: business-semantic authority + capability router + governed
data-access boundary + leadership node. All seven domains share this class; each
subclass is mostly declarative ``DomainConfig``. Improve this file once and every
domain improves.
"""

from __future__ import annotations

import asyncio
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
    frontier_metrics: list[str] = field(default_factory=list)  # if quiet -> cause may be elsewhere
    probe_metrics: list[str] = field(default_factory=list)
    probe_dimensions: list[dict[str, str]] = field(default_factory=lambda: [{}])
    # Dynamic peer set (all other domain agents). Target chosen by metric ownership.
    handoff_targets: list[str] = field(default_factory=list)
    ontology: list[str] = field(default_factory=list)  # keyword fallback when MCP is offline
    seleric_module: str | None = None
    # Terminal domains diagnose in place and never propose handoffs.
    terminal: bool = False


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
        metrics: Any | None = None,
    ) -> None:
        self.config = config
        self.agent_id = config.agent_id
        self.data = data_provider
        self.peers = peers or {}
        self.metrics = metrics  # MetricRegistry — owner resolution + peer probes
        self._live_ontology: dict[str, Any] = {}

    def attach_ontology(self, snapshot: dict[str, Any]) -> None:
        """Replace keyword fallback with a live catalogue ontology slice."""
        self._live_ontology = dict(snapshot or {})

    def ontology_terms(self) -> list[str]:
        """Entity/cluster names for this domain: live OM snapshot, else keywords."""
        live = self._live_ontology
        if live:
            terms = [d.get("name") for d in live.get("domains") or [] if d.get("name")]
            terms += [c.get("id") for c in live.get("entity_clusters") or [] if c.get("id")]
            terms += [dp.get("name") for dp in live.get("data_products") or [] if dp.get("name")]
            return [str(t) for t in terms]
        return list(self.config.ontology)

    # -- context / semantics --------------------------------------------------
    def resolve_metrics(self, hints: list[str]) -> list[str]:
        owned = [h for h in hints if h in self.config.owned_metrics]
        return owned or list(self.config.probe_metrics)

    def owner_agent_for(self, metric_id: str, blackboard: Blackboard | None = None) -> str | None:
        """Which domain agent owns this metric (registry first, else evidence domain)."""
        if self.metrics is not None:
            agent = self.metrics.owner_agent_for(metric_id)
            if agent:
                return agent
        if blackboard is not None:
            for e in blackboard.by_type("evidence"):
                if e.get("metric_or_fact") != metric_id:
                    continue
                domain = (e.get("provenance") or {}).get("domain")
                if domain:
                    return domain if str(domain).endswith("_agent") else f"{domain}_agent"
        return None

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

        # Shallow read of every peer domain so RCA can hand off wherever data points.
        posted += await self._observe_peers(blackboard, time_range=time_range)

        blackboard.record_event("observed", agent=self.agent_id, evidence=len(posted))
        return posted

    async def _observe_peers(self, blackboard: Blackboard, *, time_range: dict[str, Any]) -> list[str]:
        peers = [
            (domain_key, provider)
            for domain_key, provider in self.peers.items()
            if domain_key != self.config.domain and provider is not None
        ]

        async def _fetch(domain_key: str, provider: Any) -> Any:
            metric_ids: list[str] = []
            if self.metrics is not None:
                metric_ids = self.metrics.ids_for_domain(domain_key)
            return await provider.fetch(metric_ids=metric_ids, time_range=time_range)

        results = await asyncio.gather(*(_fetch(k, p) for k, p in peers))

        posted: list[str] = []
        for (domain_key, _provider), result in zip(peers, results):
            for r in result.readings:
                ev = Evidence.new(
                    mission_id=blackboard.mission_id,
                    created_by=f"observer_agent@{self.agent_id}",
                    prefix="EV-PEER",
                    metric_or_fact=r.metric_id,
                    value=r.value,
                    baseline=r.baseline,
                    change_pct=r.change_pct,
                    unit=r.unit,
                    dimensions=r.dimensions,
                    time_range=time_range,
                    source=r.source,
                    data_origin=r.data_origin,  # type: ignore[arg-type]
                    provenance={"direction_bad": r.direction_bad, "domain": domain_key, "peer_probe": True},
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
        if self.config.terminal:
            return True  # terminal domain diagnoses; never hands off
        frontier = set(self.config.frontier_metrics)
        if not frontier:
            # Non-terminal with no frontier metrics configured — do not claim ownership
            # (empty frontier must not block handoffs the way terminals do).
            return False
        return any(a.get("metric_id") in frontier for a in anomalies)

    def _metric_is_frontier(self, metric_id: str) -> bool:
        if metric_id in self.config.frontier_metrics:
            return True
        if self.metrics is None:
            return True
        m = self.metrics.get(metric_id)
        if m is None:
            return True
        raw = getattr(m, "raw", None) or {}
        if "frontier" in raw:
            return bool(raw["frontier"])
        return True

    def _target_is_terminal(self, agent_id: str) -> bool:
        """Terminal domains diagnose in place and should not be preferred as mid-chain hops."""
        from seleric_swarm.swarm.domain.configs import ALL_DOMAIN_CONFIGS

        cfg = ALL_DOMAIN_CONFIGS.get(agent_id)
        return bool(cfg is not None and cfg.terminal)

    # -- leadership handoff (architecture sec. 18-19, 32) -------------------
    def evaluate_handoff(
        self, blackboard: Blackboard, *, topology_neighbors: list[str] | None = None
    ) -> HandoffProposal | None:
        anomalies = blackboard.by_type("anomaly")
        if self._owns_frontier(anomalies):
            return None  # the cause is in my domain; keep leading

        peers = set(self.config.handoff_targets)
        if topology_neighbors is not None:
            # Restrict to the mesh's actual adjacency (config/coordinator_policies.yaml)
            # so RCA walks intermediate domains (e.g. Funnel) instead of jumping
            # straight to a distant domain just because it has the biggest anomaly.
            peers &= {f"{d}_agent" for d in topology_neighbors}
        visited = {self.agent_id}
        for h in blackboard.handoff_history or []:
            if h.get("from_agent"):
                visited.add(h["from_agent"])
            if h.get("to_agent"):
                visited.add(h["to_agent"])
        candidates: list[tuple[dict[str, Any], str]] = []
        for a in anomalies:
            mid = a.get("metric_id")
            if not mid or mid in self.config.owned_metrics:
                continue
            to_agent = self.owner_agent_for(str(mid), blackboard)
            if to_agent and to_agent not in visited and to_agent in peers:
                candidates.append((a, to_agent))
        if not candidates:
            return None
        # Prefer outcome / non-frontier foreign metrics (bridge symptoms) so RCA
        # walks intermediate domains before terminal ones (e.g. Funnel before Technical).
        outcomes = [
            (a, owner)
            for a, owner in candidates
            if not self._metric_is_frontier(str(a.get("metric_id") or ""))
        ]
        pool = outcomes or candidates
        # From upstream media domains, prefer non-terminal bridge owners when they
        # carry a material signal (purchase_cvr→funnel) so we do not skip to
        # technical. Mid-chain domains (funnel) keep max-deviation selection so
        # root-cause terminal metrics (js_error) still win over soft commerce dips.
        if self.config.domain in {"performance", "attribution"}:
            bridge = [(a, owner) for a, owner in pool if not self._target_is_terminal(owner)]
            if bridge:
                best_bridge = max(abs(a.get("deviation_pct") or 0) for a, _ in bridge)
                if best_bridge >= 10:
                    pool = bridge
        target_anom, to_agent = max(pool, key=lambda pair: abs(pair[0].get("deviation_pct") or 0))

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
