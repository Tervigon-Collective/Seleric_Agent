"""Causal-graph wiring for diagnosis.

``config/diagnostic_ontology.yaml`` maps metrics onto the registered causal
graph (node labels, treatment events, common causes). It does **not** list
canned RCA stories — hypotheses are seeded from observed evidence and the
metric registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root
from seleric_swarm.services.metrics import MetricRegistry

_DEFAULT_PATH = "config/diagnostic_ontology.yaml"


@dataclass
class _Ontology:
    default_graph_id: str
    graph_id_by_outcome: dict[str, str]
    node_by_metric: dict[str, str]
    treatment_events: dict[str, tuple[str, ...]]
    base_common_causes: tuple[str, ...]
    extra_common_causes_by_outcome: dict[str, tuple[str, ...]]


@lru_cache(maxsize=1)
def _load(path: str | None = None) -> _Ontology:
    p = Path(path) if path else repo_root() / _DEFAULT_PATH
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    extra = {
        str(k): tuple(v or ())
        for k, v in (raw.get("extra_common_causes") or {}).items()
    }
    graph_ids = {
        str(k): str(v)
        for k, v in (raw.get("graph_id_by_outcome") or {}).items()
    }
    return _Ontology(
        default_graph_id=raw.get("default_graph_id", "causal.funnel_purchase.v1"),
        graph_id_by_outcome=graph_ids,
        node_by_metric=dict(raw.get("node_by_metric") or {}),
        treatment_events={k: tuple(v) for k, v in (raw.get("treatment_events") or {}).items()},
        base_common_causes=tuple(raw.get("base_common_causes") or ()),
        extra_common_causes_by_outcome=extra,
    )


def graph_id_for_outcome(outcome_metric: str) -> str:
    o = _load()
    return o.graph_id_by_outcome.get(outcome_metric, o.default_graph_id)


def node_for_metric(metric_id: str) -> str:
    o = _load()
    mapped = o.node_by_metric.get(metric_id)
    if mapped:
        return mapped
    return metric_id.removeprefix("metric.") or metric_id


def treatment_events(treatment_metric: str) -> tuple[str, ...]:
    return _load().treatment_events.get(treatment_metric, ())


def common_causes_for_outcome(outcome_metric: str) -> list[str]:
    """YAML confounder overlay. Prefer ``confounders_from_graph`` at runtime."""
    o = _load()
    return [*o.base_common_causes, *o.extra_common_causes_by_outcome.get(outcome_metric, ())]


def _idents_for_node(node: str) -> list[str]:
    o = _load()
    idents = [mid for mid, mapped in o.node_by_metric.items() if mapped == node]
    if node.startswith("metric."):
        idents.append(node)
    else:
        idents.append(f"metric.{node}")
        idents.append(node)
    out: list[str] = []
    seen: set[str] = set()
    for ident in idents:
        if ident and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out


def confounders_from_graph(graph: Any, treatment_metric: str, outcome_metric: str) -> list[str]:
    """Common ancestors of treatment and outcome on the registered causal graph.

    Returns catalogue metric ids where we have a mapping, plus raw node labels
    (campaign, device) so DoWhy can still adjust for dimensions present in the
    observation frame. Not an RCA story list.
    """
    if graph is None or not treatment_metric or not outcome_metric:
        return []
    ancestors = getattr(graph, "ancestors", None)
    if ancestors is None:
        return []
    common = ancestors(node_for_metric(treatment_metric)) & ancestors(node_for_metric(outcome_metric))
    out: list[str] = []
    seen: set[str] = set()
    for node in sorted(common):
        for ident in _idents_for_node(node):
            if ident not in seen:
                seen.add(ident)
                out.append(ident)
    return out


def metric_confounders_to_fetch(
    graph: Any,
    *,
    outcome: str,
    treatments: list[str],
    metrics: MetricRegistry | None = None,
) -> list[str]:
    """Fetchable ``metric.*`` confounders for each treatment→outcome pair."""
    registry = metrics or MetricRegistry("config/metric_registry.yaml")
    found: list[str] = []
    seen: set[str] = set()
    for treatment in treatments:
        for ident in confounders_from_graph(graph, treatment, outcome):
            if not ident.startswith("metric.") or ident in seen:
                continue
            if registry.get(ident) is None:
                continue
            seen.add(ident)
            found.append(ident)
    return found
