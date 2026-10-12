"""The "why" investigation as a code state machine (M4.3).

premise → health → decompose → localise ⇄ recurse → context → drivers → stop.
All numbers come from the ``MetricReader``; the optional ``Selector`` only picks among typed
candidates at branch points and never changes a number.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Literal

from seleric_swarm.context import (
    AsyncContextProvider,
    ContextProvider,
    ContextWindow,
    HealthSource,
    agather_events,
    gather_health,
)
from seleric_swarm.contracts.pipeline import FilterSpec, WindowSpec
from seleric_swarm.graph import MetricGraph
from seleric_swarm.investigate.state import (
    Attributor,
    Candidate,
    Finding,
    InvestigatePolicy,
    InvestigateRequest,
    InvestigationState,
    MetricReader,
    Segment,
    Selector,
    aggregation_is_additive,
)

# Type alias for finding qualification (from Finding.qualification)
FindingQualification = Literal[
    "exact_accounting",
    "observed_segment_contribution",
    "directionally_consistent_hypothesis",
    "untested_hypothesis",
    "contradicted_hypothesis",
    "causally_supported",
]
from seleric_swarm.semantic import SemanticReadModel

_log = logging.getLogger("seleric.investigate")


class _Budget(Exception):
    pass


_PremiseStatus = Literal["premise_false", "not_material", "insufficient_evidence"]


class Investigation:
    def __init__(
        self,
        reader: MetricReader,
        read_model: SemanticReadModel,
        graph: MetricGraph,
        *,
        providers: Sequence[ContextProvider | AsyncContextProvider] = (),
        health_sources: Sequence[HealthSource] = (),
        selector: Selector | None = None,
        policy: InvestigatePolicy | None = None,
        attributor: Attributor | None = None,
    ) -> None:
        self.reader, self.model, self.graph = reader, read_model, graph
        self.providers, self.health_sources = providers, health_sources
        self.selector, self.policy = selector, policy or InvestigatePolicy()
        self.attributor = attributor
        # Per-mission query deduplication: identical (metric, window, filters)
        # reads are served from cache, never repeated (AG-02).
        self._cache: dict[tuple, float | None | dict[str, float]] = {}

    # ---------------------------------------------------------------- entry
    async def run(self, request: InvestigateRequest, state: InvestigationState | None = None) -> InvestigationState:
        """Run (or resume ``state``, e.g. "dig deeper") to a stop. Never raises."""
        st = state or InvestigationState(request=request)
        if state is not None and st.status == "done":
            self._resume(st)
        try:
            if st.stage == "premise" and not await self._premise(st):
                return st
            if st.stage == "health" and not self._health(st):
                return st
            if st.stage == "decompose":
                await self._decompose(st)
            if st.stage in ("localise", "recurse"):
                await self._localise(st)
            if st.stage == "context":
                await self._context(st)
            if st.stage == "drivers":
                await self._drivers(st)
            st.stage, st.status = "stop", "done"
        except _Budget:
            st.status, st.stage = "budget", "stop"
            st.limitations.append("read_budget_spent")
        except Exception as exc:
            _log.warning("investigate_failed", exc_info=True)
            st.status, st.stage = "done", "stop"
            st.limitations.append(f"investigation_error:{type(exc).__name__}")
        return st

    def _resume(self, st: InvestigationState) -> None:
        """"Dig deeper": drill into the last segment found, then localise again below it."""
        last = next((f for f in reversed(st.findings) if f.kind == "segment" and f.path == st.path), None)
        if last is None:
            return
        dim, _, value = last.subject.partition("=")
        st.path = (*st.path, FilterSpec(dimension=dim, operator="in", values=(value,)))
        st.depth += 1
        st.status, st.stage = "running", "localise"
        st.findings = [f for f in st.findings if f.kind not in ("context", "driver")]

    # ---------------------------------------------------------------- reads
    async def _total(self, st: InvestigationState, metric: str, window: WindowSpec, filters: tuple[FilterSpec, ...]) -> float | None:
        key = ("total", metric, window, filters)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        self._spend(st)
        value = await self.reader.total(metric, window, filters)
        self._cache[key] = value
        return value

    async def _breakdown(self, st: InvestigationState, metric: str, window: WindowSpec, dim: str, filters: tuple[FilterSpec, ...]) -> dict[str, float]:
        key = ("breakdown", metric, window, dim, filters)
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        self._spend(st)
        value = await self.reader.breakdown(metric, window, dim, filters)
        self._cache[key] = value
        return value

    def _spend(self, st: InvestigationState) -> None:
        if st.reads >= self.policy.max_reads:
            raise _Budget
        st.reads += 1

    # ---------------------------------------------------------------- stages
    # Premise outcomes that end the run, each with its own status. ``opposite`` is
    # a *refuted* premise; ``not_material`` is one that did not move; a window we
    # could not read is *insufficient evidence*, never "false" (AG-01).
    _PREMISE_STOP: dict[str, _PremiseStatus] = {
        "opposite": "premise_false",
        "not_material": "not_material",
        "insufficient_evidence": "insufficient_evidence",
    }

    async def _premise(self, st: InvestigationState) -> bool:
        r = st.request
        st.baseline_total = await self._total(st, r.metric_id, r.baseline, r.filters)
        st.event_total = await self._total(st, r.metric_id, r.event, r.filters)
        base = st.baseline_total
        event = st.event_total
        if base is None or event is None:
            # A missing read is not evidence that the premise is false.
            verdict = "insufficient_evidence"
            change = None
        else:
            change = event - base
            material = abs(change) > self.policy.min_change_ratio * max(abs(base), 1e-9)
            wrong_way = (r.direction == "up" and change < 0) or (r.direction == "down" and change > 0)
            verdict = "opposite" if material and wrong_way else "confirmed" if material else "not_material"
        st.findings.append(Finding(kind="premise", stage="premise", subject=r.metric_id, baseline=base,
                                   event=event, delta=change, verdict=verdict))
        if verdict != "confirmed":
            st.status, st.stage = self._PREMISE_STOP[verdict], "stop"
            return False
        st.stage = "health"
        return True

    def _health(self, st: InvestigationState) -> bool:
        r = st.request
        window = ContextWindow(start=min(r.baseline.start, r.event.start), end=max(r.baseline.end, r.event.end))
        metrics = (r.metric_id, *self.graph.components(r.metric_id))
        st.health = gather_health(self.health_sources, window, metrics, self.model)
        for h in st.health:
            st.findings.append(Finding(kind="health", stage="health", subject=h.metric_id, verdict=h.status, detail=h.detail))
        target = [h for h in st.health if h.metric_id == r.metric_id]
        # Incomplete or unverifiable data about the metric under investigation is a
        # stop, with the reason kept distinct; caveats below that are recorded, not fatal.
        for status, limitation in (("incomplete", "data_incomplete"), ("unknown", "data_unknown")):
            if any(h.status == status for h in target):
                st.status, st.stage = "unhealthy", "stop"
                st.limitations.append(limitation)
                return False
        if any(h.status == "provisional" for h in st.health):
            st.limitations.append("data_provisional")
        if st.health:
            st.limitations.append("data_health_issues")
        st.stage = "decompose"
        return True

    async def _decompose(self, st: InvestigationState) -> None:
        r, change = st.request, st.change or 0.0
        edges = self.graph.parents(r.metric_id, "identity")
        roles = {e.role for e in edges}
        values: dict[str, tuple[float | None, float | None]] = {}
        for e in edges:
            if e.source not in values:
                values[e.source] = (await self._total(st, e.source, r.baseline, r.filters),
                                    await self._total(st, e.source, r.event, r.filters))
        contributions: dict[str, float] = {}
        if {"numerator", "denominator"} <= roles:
            # r = n/d: exact decomposition — numerator effect (n1-n0)/d0,
            # denominator effect n1*(d0-d1)/(d0*d1); these sum exactly to Δr.
            n0 = n1 = d0 = d1 = None
            for edge in edges:
                if edge.role == "numerator":
                    n0, n1 = values.get(edge.source, (None, None))
                elif edge.role == "denominator":
                    d0, d1 = values.get(edge.source, (None, None))
            if n0 is not None and n1 is not None and d0 is not None and d1 is not None:
                num_effect: float = (n1 - n0) / d0
                den_effect: float = n1 * (d0 - d1) / (d0 * d1)
                contributions = {edge.source: (num_effect if edge.role == "numerator" else den_effect)
                                 for edge in edges if edge.role in ("numerator", "denominator")}
        else:
            for e in edges:
                if e.role == "term":
                    b, ev = values[e.source]
                    if b is not None and ev is not None:
                        contributions[e.source] = (e.coefficient if e.coefficient is not None else 1.0) * (ev - b)
        explained = 0.0
        for metric, contrib in sorted(contributions.items(), key=lambda kv: -abs(kv[1])):
            b, ev = values[metric]
            st.findings.append(Finding(kind="component", stage="decompose", subject=metric, baseline=b, event=ev,
                                       delta=contrib, share_of_change=_share(contrib, change),
                                       qualification="exact_accounting"))
            explained += contrib
        st.unexplained_residual = change - explained
        if st.unexplained_residual is not None and abs(st.unexplained_residual) > 1e-9 * max(abs(change), 1.0):
            st.findings.append(Finding(kind="limitation", stage="decompose", subject=r.metric_id,
                                       delta=st.unexplained_residual, verdict="unexplained_residual",
                                       detail=f"{st.unexplained_residual:g} of {change:g} not accounted for"))
        missing = [e.source for e in edges if values.get(e.source, (None, None))[0] is None
                   or values.get(e.source, (None, None))[1] is None]
        if missing:
            st.limitations.append("decomposition_incomplete")
            st.findings.append(Finding(kind="limitation", stage="decompose", subject=",".join(missing),
                                       verdict="missing_components", qualification="untested_hypothesis"))
        st.stage = "localise"

    async def _localise(self, st: InvestigationState) -> None:
        r = st.request
        if not self._additive(r.metric_id):
            st.limitations.append("localise_skipped_non_additive")
            st.stage = "context"
            return
        start = st.depth  # max_depth counts per run, so "dig deeper" can go further
        while True:
            used = {f.dimension for f in (*r.filters, *st.path)}
            dims = self._prioritise_dimensions(r.metric_id, used)
            filters = (*r.filters, *st.path)
            parent_b = await self._total(st, r.metric_id, r.baseline, filters) if st.path else st.baseline_total
            parent_e = await self._total(st, r.metric_id, r.event, filters) if st.path else st.event_total
            parent_delta = (parent_e or 0.0) - (parent_b or 0.0)
            best: list[Segment] = []
            for dim in dims:
                b = await self._breakdown(st, r.metric_id, r.baseline, dim, filters)
                e = await self._breakdown(st, r.metric_id, r.event, dim, filters)
                self._note_entity_changes(st, dim, b, e)
                segs = sorted((_segment(dim, k, b.get(k, 0.0), e.get(k, 0.0), parent_delta) for k in set(b) | set(e)),
                              key=lambda s: -abs(s.delta))
                if segs:
                    best.append(segs[0])
            if not best:
                break
            # Same-direction segments only: a slice moving against the change does not explain it.
            best = [s for s in best if s.share_of_change > 0] or best
            best.sort(key=lambda s: -s.share_of_change)
            chosen = await self._choose(st, best)
            st.findings.append(Finding(kind="segment", stage="localise", depth=st.depth, subject=f"{chosen.dimension}={chosen.value}",
                                       baseline=chosen.baseline, event=chosen.event, delta=chosen.delta,
                                       share_of_change=chosen.share_of_change, path=st.path,
                                       qualification="observed_segment_contribution"))
            total_change = st.change or 0.0
            st.explained_share = max(st.explained_share, _share(chosen.delta, total_change) or 0.0)
            if (chosen.share_of_change < self.policy.recurse_share or st.depth + 1 - start >= self.policy.max_depth
                    or st.explained_share >= self.policy.explained_target and st.depth > 0):
                break
            st.stage = "recurse"
            st.path = (*st.path, FilterSpec(dimension=chosen.dimension, operator="in", values=(chosen.value,)))
            st.depth += 1
        st.stage = "context"

    def _prioritise_dimensions(self, metric_id: str, used: set[str]) -> list[str]:
        """Order candidate dimensions by declared hierarchy level, then cap breadth.

        Business hierarchies (campaign → ad set → ad) come before unsupported or
        unranked dimensions. Within the same level, preserve the catalogue's
        declared order so stable tests aren't affected. Only the top
        ``max_dimensions_per_level`` are queried, so a level never fans out
        across every dimension (AG-02).
        """
        dims = [d for d in self.model.supported_dimensions_for(metric_id)
                if d not in used and not self.model.is_time_dimension(d)]

        def level(dim: str) -> int | None:
            return self.model.dimension_hierarchy_level(dim)

        # Tie-break by original index to preserve declared order for equal levels
        original_index = {d: i for i, d in enumerate(dims)}
        ranked = sorted(dims, key=lambda d: (level(d) if level(d) is not None else 999, original_index[d]))
        return ranked[: self.policy.max_dimensions_per_level]

    def _note_entity_changes(self, st: InvestigationState, dim: str, baseline: dict[str, float], event: dict[str, float]) -> None:
        """Flag segments that appeared or disappeared between windows.

        A renamed entity shows up as one disappearance plus one appearance; both
        are recorded as observations, not merged, because identity resolution is
        Core's call (AG-02 / AG03-T04).
        """
        gone = sorted(set(baseline) - set(event))
        new = sorted(set(event) - set(baseline))
        for value in gone:
            st.findings.append(Finding(kind="limitation", stage="localise", subject=f"{dim}={value}",
                                       verdict="entity_disappeared", baseline=baseline[value],
                                       qualification="observed_segment_contribution"))
        for value in new:
            st.findings.append(Finding(kind="limitation", stage="localise", subject=f"{dim}={value}",
                                       verdict="entity_appeared", event=event[value],
                                       qualification="observed_segment_contribution"))

    async def _context(self, st: InvestigationState) -> None:
        r = st.request
        scope = {f.dimension: f.values[0] for f in (*r.filters, *st.path) if f.operator == "in" and len(f.values) == 1}
        st.events = await agather_events(self.providers, ContextWindow(start=r.event.start, end=r.event.end), scope, self.model)
        for ev in st.events:
            if ev.metric_ids and r.metric_id not in ev.metric_ids:
                continue
            st.findings.append(Finding(kind="context", stage="context", subject=ev.title, verdict=ev.kind,
                                       detail=f"{ev.start}..{ev.end} {ev.detail}".strip()))
        st.stage = "drivers"

    async def _drivers(self, st: InvestigationState) -> None:
        r, change = st.request, st.change or 0.0
        filters = (*r.filters, *st.path)
        for edge in self.graph.causes(r.metric_id):
            if not self.model.has_metric(edge.source):
                continue
            b = await self._total(st, edge.source, r.baseline, filters)
            e = await self._total(st, edge.source, r.event, filters)
            if b is None or e is None:
                continue
            moved = abs(e - b) > self.policy.min_change_ratio * max(abs(b), 1e-9)
            expected = edge.sign * (1 if change > 0 else -1)
            agrees = moved and expected != 0 and (e - b) * expected > 0
            # Direction agreement is a hypothesis, not causal proof. Only the GCM
            # attributor — a separate identification and testing process — earns
            # "causally_supported" (AG-02).
            if agrees:
                verdict = "directionally_consistent"
                qualification: FindingQualification = "directionally_consistent_hypothesis"
            elif not moved:
                verdict, qualification = "not_moved", "untested_hypothesis"
            else:
                verdict, qualification = "inconsistent" if edge.sign else "moved_unsigned", "contradicted_hypothesis"
            st.findings.append(Finding(kind="driver", stage="drivers", subject=edge.source, baseline=b, event=e,
                                       delta=e - b, verdict=verdict, detail=f"lag_days={edge.lag_days}",
                                       qualification=qualification))

        if self.attributor is not None:
            try:
                shares = await self.attributor(target=r.metric_id, baseline=r.baseline, event=r.event, filters=filters)
            except Exception:
                _log.warning("investigate_attribution_failed", exc_info=True)
                shares = {}
            for node, share in sorted(shares.items(), key=lambda kv: -abs(kv[1])):
                if node != r.metric_id:
                    st.findings.append(Finding(kind="driver", stage="drivers", subject=node, share_of_change=share,
                                               verdict="gcm_attribution", qualification="causally_supported"))

    # ---------------------------------------------------------------- helpers
    async def _choose(self, st: InvestigationState, segs: list[Segment]) -> Segment:
        if self.selector is None or len(segs) < 2:
            return segs[0]
        cands = [Candidate(id=f"{s.dimension}={s.value}", share_of_change=s.share_of_change,
                           summary=f"{s.dimension}={s.value}: {s.baseline:g} -> {s.event:g}") for s in segs]
        try:
            picked, rationale = await self.selector(stage="localise", candidates=cands)
        except Exception:
            _log.warning("investigate_select_failed", exc_info=True)
            return segs[0]
        for s, c in zip(segs, cands, strict=True):
            if c.id == picked:
                if rationale:
                    st.findings.append(Finding(kind="limitation", stage="localise", subject=c.id, verdict="selected", detail=rationale[:200]))
                return s
        return segs[0]

    def _additive(self, metric_id: str) -> bool:
        roles = {e.role for e in self.graph.parents(metric_id, "identity")}
        return aggregation_is_additive(self.model.aggregation_for(metric_id)) and "denominator" not in roles


def _segment(dim: str, value: str, b: float, e: float, parent_delta: float) -> Segment:
    return Segment(dimension=dim, value=value, baseline=b, event=e, delta=e - b, share_of_change=_share(e - b, parent_delta) or 0.0)


def _share(part: float, whole: float) -> float | None:
    return None if not whole else part / whole


def _log_ratio(b: float | None, e: float | None) -> float | None:
    if b is None or e is None or b <= 0 or e <= 0:
        return None
    return math.log(e / b)
