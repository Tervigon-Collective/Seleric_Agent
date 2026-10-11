"""DoWhy-GCM attribution over the metric graph (M4.2 optional adapter, ``GRAPH_GCM_MODE``).

Fits a graphical causal model on daily series of the graph's nodes and attributes the
change in the target's distribution (baseline days → event days) to each upstream node
(``gcm.distribution_change``). Heavy and data-hungry: callers run it in a thread and treat
``{}`` as "no attribution".
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date

_log = logging.getLogger("seleric.adapters.gcm")

MIN_DAYS = 14


def gcm_distribution_change(
    edges: Sequence[tuple[str, str]],
    daily: Mapping[str, Mapping[date, float]],
    target: str,
    baseline_days: Sequence[date],
    event_days: Sequence[date],
    *,
    num_samples: int = 500,
) -> dict[str, float]:
    """Each node's share of the target's mean change; ``{}`` when the model cannot be fitted."""
    try:
        import networkx as nx  # type: ignore[import-untyped]
        import pandas as pd
        from dowhy import gcm
    except ImportError:
        return {}
    nodes = {n for e in edges for n in e if n in daily}
    graph = nx.DiGraph([(a, b) for a, b in edges if a in nodes and b in nodes])
    if target not in graph or not nx.is_directed_acyclic_graph(graph) or not list(graph.predecessors(target)):
        return {}
    keep = nx.ancestors(graph, target) | {target}
    graph = graph.subgraph(keep).copy()

    def frame(days: Sequence[date]) -> pd.DataFrame:
        rows = [{n: daily[n].get(d) for n in graph.nodes} for d in days]
        return pd.DataFrame(rows).dropna()

    old, new = frame(baseline_days), frame(event_days)
    if len(old) < MIN_DAYS or len(new) < 2:
        return {}
    try:
        model = gcm.StructuralCausalModel(graph)
        gcm.auto.assign_causal_mechanisms(model, old)
        raw = gcm.distribution_change(model, old, new, target, num_samples=num_samples,
                                      difference_estimation_func=lambda a, b: float(b.mean() - a.mean()))
    except Exception:
        _log.warning("gcm_attribution_failed", exc_info=True)
        return {}
    total = sum(raw.values())
    return {str(k): (float(v) / total if total else 0.0) for k, v in raw.items()}
