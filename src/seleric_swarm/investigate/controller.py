"""The "why" investigation as a code state machine (M4.3).

premise → health → decompose → localise ⇄ recurse → context → drivers → stop.
All numbers come from the ``MetricReader``; the optional ``Selector`` only picks among typed
candidates at branch points and never changes a number.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence

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
)
from seleric_swarm.semantic import SemanticReadModel

_log = logging.getLogger("seleric.investigate")

# Aggregations whose slices do not sum to the total (machine values, not business words).
_NON_ADDITIVE = frozenset(
    {"avg", "average", "mean", "median", "ratio", "rate", "percent", "percentage",
     "count_distinct", "distinct", "unique", "min", "max", "last", "first"}
)


class _Budget(Exception):
    pass


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
        self._spend(st)
        return await self.reader.total(metric, window, filters)

    async def _breakdown(self, st: InvestigationState, metric: str, window: WindowSpec, dim: str, filters: tuple[FilterSpec, ...]) -> dict[str, float]:
        self._spend(st)
        return await self.reader.breakdown(metric, window, dim, filters)

    def _spend(self, st: InvestigationState) -> None:
        if st.reads >= self.policy.max_reads:
            raise _Budget
        st.reads += 1

    # ---------------------------------------------------------------- stages
    async def _premise(self, st: InvestigationState) -> bool:
        r = st.request
        st.baseline_total = await self._total(st, r.metric_id, r.baseline, r.filters)
        st.event_total = await self._total(st, r.metric_id, r.event, r.filters)
        change = st.change
        base = st.baseline_total
        material = change is not None and base is not None and abs(change) > self.policy.min_change_ratio * max(abs(base), 1e-9)
        wrong_way = change is not None and ((r.direction == "up" and change < 0) or (r.direction == "down" and change > 0))
        verdict = "confirmed" if material and not wrong_way else "opposite" if wrong_way and material else "not_material"
        st.findings.append(Finding(kind="premise", stage="premise", subject=r.metric_id, baseline=base,
                                   event=st.event_total, delta=change, verdict=verdict))
        if verdict != "confirmed":
            st.status, st.stage = "premise_false", "stop"
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
        if any(h.status == "incomplete" and h.metric_id == r.metric_id for h in st.health):
            st.status, st.stage = "unhealthy", "stop"
            st.limitations.append("data_incomplete")
            return False
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
            # r = n/d: Δln r = Δln n − Δln d; each side's share of Δr is its share of Δln r.
            lr = _log_ratio(st.baseline_total, st.event_total)
            for e in edges:
                if e.role in ("numerator", "denominator"):
                    lc = _log_ratio(*values[e.source])
                    if lr and lc is not None:
                        contributions[e.source] = change * (lc if e.role == "numerator" else -lc) / lr
        else:
            for e in edges:
                if e.role == "term":
                    b, ev = values[e.source]
                    if b is not None and ev is not None:
                        contributions[e.source] = (e.coefficient if e.coefficient is not None else 1.0) * (ev - b)
        for metric, contrib in sorted(contributions.items(), key=lambda kv: -abs(kv[1])):
            b, ev = values[metric]
            st.findings.append(Finding(kind="component", stage="decompose", subject=metric, baseline=b, event=ev,
                                       delta=contrib, share_of_change=_share(contrib, change)))
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
            dims = [d for d in self.model.supported_dimensions_for(r.metric_id)
                    if d not in used and not self.model.is_time_dimension(d)]
            filters = (*r.filters, *st.path)
            parent_b = await self._total(st, r.metric_id, r.baseline, filters) if st.path else st.baseline_total
            parent_e = await self._total(st, r.metric_id, r.event, filters) if st.path else st.event_total
            parent_delta = (parent_e or 0.0) - (parent_b or 0.0)
            best: list[Segment] = []
            for dim in dims:
                b = await self._breakdown(st, r.metric_id, r.baseline, dim, filters)
                e = await self._breakdown(st, r.metric_id, r.event, dim, filters)
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
                                       share_of_change=chosen.share_of_change, path=st.path))
            total_change = st.change or 0.0
            st.explained_share = max(st.explained_share, _share(chosen.delta, total_change) or 0.0)
            if (chosen.share_of_change < self.policy.recurse_share or st.depth + 1 - start >= self.policy.max_depth
                    or st.explained_share >= self.policy.explained_target and st.depth > 0):
                break
            st.stage = "recurse"
            st.path = (*st.path, FilterSpec(dimension=chosen.dimension, operator="in", values=(chosen.value,)))
            st.depth += 1
        st.stage = "context"

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
            verdict = "supported_cause" if agrees else "not_moved" if not moved else "inconsistent" if edge.sign else "moved_unsigned"
            st.findings.append(Finding(kind="driver", stage="drivers", subject=edge.source, baseline=b, event=e,
                                       delta=e - b, verdict=verdict, detail=f"lag_days={edge.lag_days}"))

        if self.attributor is not None:
            try:
                shares = await self.attributor(target=r.metric_id, baseline=r.baseline, event=r.event, filters=filters)
            except Exception:
                _log.warning("investigate_attribution_failed", exc_info=True)
                shares = {}
            for node, share in sorted(shares.items(), key=lambda kv: -abs(kv[1])):
                if node != r.metric_id:
                    st.findings.append(Finding(kind="driver", stage="drivers", subject=node, share_of_change=share,
                                               verdict="gcm_attribution"))

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
        aggregation = (self.model.aggregation_for(metric_id) or "").lower()
        roles = {e.role for e in self.graph.parents(metric_id, "identity")}
        return aggregation not in _NON_ADDITIVE and "denominator" not in roles


def _segment(dim: str, value: str, b: float, e: float, parent_delta: float) -> Segment:
    return Segment(dimension=dim, value=value, baseline=b, event=e, delta=e - b, share_of_change=_share(e - b, parent_delta) or 0.0)


def _share(part: float, whole: float) -> float | None:
    return None if not whole else part / whole


def _log_ratio(b: float | None, e: float | None) -> float | None:
    if b is None or e is None or b <= 0 or e <= 0:
        return None
    return math.log(e / b)
