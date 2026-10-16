"""The live dependency graph (plan D-55): built per question from the catalogue definitions the tool has just fetched plus
the lean reviewed priors (``data/priors.json``, written by ``scripts/vendor_priors.py`` from Agent Core).

What the catalogue states is read from it as it is now: a metric's composition, ratio components and inputs are the
arithmetic edges, so a metric Core renames or re-defines changes the graph with no file to re-vendor. What no formula can
state (which volume drives which, which metrics count the same events, funnel order) comes from the priors, and only for
metrics the live catalogue has. The result is the same ``Graph`` value the subgraph builder walks."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from seleric_swarm.diagnosis.domain.canonical import fingerprint
from seleric_swarm.diagnosis.domain.graph import (
    Direction,
    Edge,
    EdgeKind,
    Graph,
    GraphIssue,
    LoadedGraph,
    Node,
    Ratio,
    Tier,
)

PRIORS_PATH = Path(__file__).resolve().parents[1] / "data" / "priors.json"
UNKNOWN_TIER = 99  # a metric no prior places (it is on no mechanism edge) sorts after every placed one
_PAIR_EDGE_KINDS = {k.value: k for k in (EdgeKind.CO_MEASURE, EdgeKind.SUBSET, EdgeKind.ALLOCATION, EdgeKind.TWIN)}


@cache
def load_priors(path: Path = PRIORS_PATH) -> Mapping[str, Any]:
    priors: Mapping[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return priors


def _node(metric_id: str, d: Mapping[str, Any], tier: int, allocated: bool) -> Node:
    ratio: Mapping[str, Any] | None = d.get("ratio_components") or None
    return Node(
        id=metric_id, label=str(d.get("display_name") or metric_id), view=str((d.get("cube_mapping") or {}).get("view") or d.get("view") or ""),
        category=str(d.get("category") or ""), aggregation=str(d.get("aggregation") or ""), unit=str(d.get("unit") or ""),
        grain=str(d.get("grain") or ""), date_axis="", status=str(d.get("status") or ""), tier=tier, allocated=allocated,
        formula="", ratio=Ratio(str(ratio["numerator"]), str(ratio["denominator"])) if ratio else None, same_quantity_group=(),
    )


def _accounting_edges(metric_id: str, d: Mapping[str, Any], known: set[str]) -> list[Edge]:
    """The arithmetic the catalogue states for one metric: its composition, ratio components and other inputs."""
    formula: Mapping[str, Any] = d.get("formula") or {}
    out: list[Edge] = []
    seen: set[str] = set()
    for part in formula.get("composition") or []:
        if part["metric"] in known:
            out.append(Edge(part["metric"], metric_id, EdgeKind.COMPOSITION, "catalogue:formula.composition", "definitional", coefficient=int(part["sign"])))
            seen.add(part["metric"])
    ratio: Mapping[str, Any] = d.get("ratio_components") or {}
    for role in ("numerator", "denominator"):
        part = ratio.get(role)
        if part in known and part not in seen:
            out.append(Edge(part, metric_id, EdgeKind.RATIO_COMPONENT, "catalogue:ratio_components", "definitional", role=role))
            seen.add(part)
    for part in formula.get("depends_on") or []:
        if part in known and part not in seen and part != metric_id:
            out.append(Edge(part, metric_id, EdgeKind.DECLARED_INPUT, "catalogue:formula.depends_on", "definitional_partial"))
            seen.add(part)
    return out


def build_live_graph(definitions: Mapping[str, Mapping[str, Any]], priors: Mapping[str, Any] | None = None) -> LoadedGraph:
    """The graph for the catalogue as it is now. Prior edges and pairs that name a metric the catalogue lacks are left
    out and reported, never silently."""
    priors = priors if priors is not None else load_priors()
    known = {m for m, d in definitions.items() if isinstance(d, Mapping)}
    issues: list[GraphIssue] = []
    allocated = {p["b"] for p in priors["pairs"] if p["kind"] == "allocation"}
    tiers = {m: int(t) for m, t in priors["metric_tiers"].items()}
    nodes = {m: _node(m, definitions[m], tiers.get(m, UNKNOWN_TIER), m in allocated) for m in sorted(known)}
    edges: list[Edge] = []
    for m in sorted(known):
        edges += _accounting_edges(m, definitions[m], known)
    for e in priors["causal"]:
        if e["source"] not in known or e["target"] not in known:
            gone = [m for m in (e["source"], e["target"]) if m not in known]
            issues.append(GraphIssue("prior_edge_not_in_catalogue", f"{e['source']}->{e['target']}", f"the live catalogue has no {gone}"))
            continue
        kind = EdgeKind(e["kind"])
        edges.append(Edge(
            e["source"], e["target"], kind, "priors:" + e["kind"], e["confidence"],
            direction=Direction(e["sign"]), lag_days=(int(e["lag_days"][0]), int(e["lag_days"][1])),
        ))
    for p in priors["pairs"]:
        if p["a"] in known and p["b"] in known:
            edges.append(Edge(p["a"], p["b"], _PAIR_EDGE_KINDS[p["kind"]], "priors:" + p["kind"], "reviewed", expected=p["expected"] or None))
    for m, d in definitions.items():
        if isinstance(d, Mapping) and d.get("status") == "broken":
            issues.append(GraphIssue("node_status_broken", m, "the catalogue marks this metric broken"))
    version = fingerprint({
        "priors": priors["source"]["sha256"],
        "arithmetic": sorted(f"{e.source}>{e.target}:{e.kind.value}" for e in edges if e.basis.startswith("catalogue")),
    })
    graph = Graph(
        schema_version=int(priors["version"]), version=version, catalogue_version=fingerprint(sorted(known)), nodes=nodes,
        edges=tuple(edges), tiers={int(k): Tier(int(k), v, "") for k, v in priors["tiers"].items()}, hierarchies={}, edge_kind_notes={},
    )
    return LoadedGraph(graph, tuple(issues))
