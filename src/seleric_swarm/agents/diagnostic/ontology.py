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
    return _load().node_by_metric.get(metric_id, metric_id)


def treatment_events(treatment_metric: str) -> tuple[str, ...]:
    return _load().treatment_events.get(treatment_metric, ())


def common_causes_for_outcome(outcome_metric: str) -> list[str]:
    o = _load()
    return [*o.base_common_causes, *o.extra_common_causes_by_outcome.get(outcome_metric, ())]
