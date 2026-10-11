"""``investigate.Attributor`` over DoWhy-GCM: daily series via the plan executor, fit in a thread."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from seleric_swarm.adapters.gcm import MIN_DAYS, gcm_distribution_change
from seleric_swarm.contracts.pipeline import FilterSpec, WindowSpec
from seleric_swarm.graph import MetricGraph
from seleric_swarm.pipeline._engines import _daily
from seleric_swarm.semantic import SemanticPort, SemanticReadModel

HISTORY_DAYS = 28


class GcmAttributor:
    def __init__(self, port: SemanticPort | None, read_model: SemanticReadModel, graph: MetricGraph, as_of: datetime) -> None:
        self.port, self.model, self.graph, self.as_of = port, read_model, graph, as_of

    async def __call__(
        self, *, target: str, baseline: WindowSpec, event: WindowSpec, filters: tuple[FilterSpec, ...]
    ) -> dict[str, float]:
        nodes = [n for n in (target, *self.graph.ancestors(target)) if self.model.has_metric(n)]
        if len(nodes) < 2:
            return {}
        start = event.start - timedelta(days=max(HISTORY_DAYS, MIN_DAYS))
        span = WindowSpec(role="context", start=start, end=event.end)
        series = {}
        for node in nodes:
            carried = tuple(f for f in filters if self.model.carries(node, f.dimension))
            _, parsed = await _daily(self.port, self.model, self.as_of, node, span, None, carried)
            if parsed.get(""):
                series[node] = parsed[""]
        old_days = [start + timedelta(days=i) for i in range((event.start - start).days)]
        new_days = [event.start + timedelta(days=i) for i in range((event.end - event.start).days + 1)]
        edges = [(e.source, e.target) for e in self.graph.edges]
        del baseline  # GCM learns "normal" from the history before the event, not the short baseline
        return await asyncio.to_thread(gcm_distribution_change, edges, series, target, old_days, new_days)
