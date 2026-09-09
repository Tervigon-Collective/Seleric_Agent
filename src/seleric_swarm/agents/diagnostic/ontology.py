"""Domain ontology: candidate mechanisms per outcome metric.

Deterministic seed set the hypothesis generator draws from before (optionally)
asking the LLM for more. Backed by ``config/diagnostic_ontology.yaml`` so that
adding a new outcome metric's candidate mechanisms is a config change, not a
Python code change (docs/44 tickets SCL-001 / ADP-001).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from seleric_swarm.paths import repo_root

_DEFAULT_PATH = "config/diagnostic_ontology.yaml"


@dataclass(frozen=True)
class MechanismTemplate:
    key: str
    statement: str
    mechanism: str
    treatment_metric: str
    outcome_metric: str
    domains: tuple[str, ...]
    evidence_hints: tuple[str, ...] = field(default_factory=tuple)
    is_symptom_only: bool = False


@dataclass
class _Ontology:
    mechanisms_by_outcome: dict[str, tuple[MechanismTemplate, ...]]
    incident_type_by_key: dict[str, str]
    graph_id_by_outcome: dict[str, str]
    default_graph_id: str
    node_by_metric: dict[str, str]
    treatment_events: dict[str, tuple[str, ...]]
    base_common_causes: tuple[str, ...]
    extra_common_causes_by_outcome: dict[str, tuple[str, ...]]


@lru_cache(maxsize=1)
def _load(path: str | None = None) -> _Ontology:
    p = Path(path) if path else repo_root() / _DEFAULT_PATH
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    mechanisms_by_outcome: dict[str, tuple[MechanismTemplate, ...]] = {}
    incident_type_by_key: dict[str, str] = {}
    graph_id_by_outcome: dict[str, str] = {}
    extra_common_causes_by_outcome: dict[str, tuple[str, ...]] = {}

    for outcome_metric, entry in (raw.get("outcomes") or {}).items():
        templates = []
        for m in entry.get("mechanisms") or []:
            templates.append(
                MechanismTemplate(
                    key=m["key"],
                    statement=m["statement"],
                    mechanism=m["mechanism"],
                    treatment_metric=m["treatment_metric"],
                    outcome_metric=outcome_metric,
                    domains=tuple(m.get("domains") or ()),
                    evidence_hints=tuple(m.get("evidence_hints") or ()),
                    is_symptom_only=bool(m.get("is_symptom_only", False)),
                )
            )
            if m.get("incident_type"):
                incident_type_by_key[m["key"]] = m["incident_type"]
        mechanisms_by_outcome[outcome_metric] = tuple(templates)
        if entry.get("graph_id"):
            graph_id_by_outcome[outcome_metric] = entry["graph_id"]
        if entry.get("extra_common_causes"):
            extra_common_causes_by_outcome[outcome_metric] = tuple(entry["extra_common_causes"])

    return _Ontology(
        mechanisms_by_outcome=mechanisms_by_outcome,
        incident_type_by_key=incident_type_by_key,
        graph_id_by_outcome=graph_id_by_outcome,
        default_graph_id=raw.get("default_graph_id", "causal.funnel_purchase.v1"),
        node_by_metric=dict(raw.get("node_by_metric") or {}),
        treatment_events={k: tuple(v) for k, v in (raw.get("treatment_events") or {}).items()},
        base_common_causes=tuple(raw.get("base_common_causes") or ()),
        extra_common_causes_by_outcome=extra_common_causes_by_outcome,
    )


def mechanisms_for(outcome_metric: str) -> tuple[MechanismTemplate, ...]:
    return _load().mechanisms_by_outcome.get(outcome_metric, ())


def known_outcomes() -> list[str]:
    return list(_load().mechanisms_by_outcome)


def incident_type_for_key(mechanism_key: str) -> str | None:
    return _load().incident_type_by_key.get(mechanism_key)


def incident_type_for_treatment(outcome_metric: str, treatment_metric: str) -> str | None:
    """Resolve incident_type from the ontology template a hypothesis came from.

    Hypotheses don't retain the template ``key`` (only the (treatment, outcome)
    pair), so this matches back on that pair — stable because each outcome's
    template set uses a unique treatment metric per mechanism.
    """
    for tmpl in mechanisms_for(outcome_metric):
        if tmpl.treatment_metric == treatment_metric:
            return incident_type_for_key(tmpl.key)
    return None


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
