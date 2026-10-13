"""Build a ``causal_graph`` chart_spec from a diagnosis report's DoWhy DAGs.

Each implicated / ruled-out driver carries the explicit DAG DoWhy identified the
effect on (``graph_edges``). This module unions those edges into one view so the
UI can show the causal structure the engine actually used — not a separate
hand-drawn ontology.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.causal import diagnosis as engine

_ROLE_OUTCOME = "outcome"
_ROLE_DRIVER = "driver"
_ROLE_CONFOUNDER = "confounder"

# DoWhy calendar dummies use Python weekday() (Mon=0 … Sun=6); Mon is the
# omitted reference level, so nodes are dow_1…dow_6 (Tue…Sun).
_DOW_LABELS = {1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}


def _label(metric_id: str) -> str:
    raw = (metric_id or "").strip()
    if raw.startswith("dow_"):
        try:
            k = int(raw.split("_", 1)[1])
        except ValueError:
            k = -1
        if k in _DOW_LABELS:
            return f"{_DOW_LABELS[k]} (day of week)"
    if raw == "trend":
        return "trend"
    return raw.replace("_", " ").strip() or metric_id


def _add_identity_term(
    nodes: dict[str, dict[str, Any]], links: dict[tuple[str, str], dict[str, Any]],
    metric: str, parent: str, share: float | None,
) -> None:
    if not metric or metric == parent:
        return
    nodes.setdefault(parent, {"id": parent, "name": _label(parent), "role": _ROLE_DRIVER, "classification": None,
                              "status": "accounting identity", "share_of_change": None})
    nodes.setdefault(metric, {"id": metric, "name": _label(metric), "role": _ROLE_DRIVER,
                              "classification": "identity", "status": "accounting identity",
                              "share_of_change": share})
    links[(metric, parent)] = {"source": metric, "target": parent, "kind": "identity", "classification": "identity"}


def build_causal_graph_spec(
    report: engine.DiagnosisReport,
    *,
    outcome: str | None = None,
) -> dict[str, Any] | None:
    """A chart_spec for the union of driver DAGs, or ``None`` when nothing to draw."""
    target = outcome or report.outcome
    nodes: dict[str, dict[str, Any]] = {
        target: {
            "id": target,
            "name": _label(target),
            "role": _ROLE_OUTCOME,
            "classification": None,
            "status": None,
            "share_of_change": None,
        }
    }
    links: dict[tuple[str, str], dict[str, Any]] = {}

    def add_link(source: str, dest: str, *, kind: str, classification: str | None = None) -> None:
        if not source or not dest or source == dest:
            return
        key = (source, dest)
        prior = links.get(key)
        # Prefer a treatment edge over a generic adjustment when both appear.
        if prior is not None and prior.get("kind") == "treatment" and kind != "treatment":
            return
        links[key] = {
            "source": source,
            "target": dest,
            "kind": kind,
            "classification": classification,
        }

    drawn = 0
    for finding in report.drivers:
        if finding.status not in ("implicated", "ruled_out"):
            continue
        edges = [tuple(e) for e in (finding.graph_edges or []) if isinstance(e, (list, tuple)) and len(e) >= 2]
        if not edges and finding.driver:
            # Fall back to the estimand path X → Y when DoWhy left no edges.
            edges = [(finding.driver, target)]
        if not edges:
            continue
        drawn += 1
        driver = finding.driver
        if driver and driver not in nodes:
            nodes[driver] = {
                "id": driver,
                "name": _label(driver),
                "role": _ROLE_DRIVER,
                "classification": finding.classification,
                "status": finding.status,
                "share_of_change": finding.share_of_change,
            }
        elif driver in nodes and nodes[driver]["role"] != _ROLE_OUTCOME:
            nodes[driver]["classification"] = finding.classification or nodes[driver].get("classification")
            nodes[driver]["status"] = finding.status or nodes[driver].get("status")
            if finding.share_of_change is not None:
                nodes[driver]["share_of_change"] = finding.share_of_change
            nodes[driver]["role"] = _ROLE_DRIVER
        for src, dst, *_ in edges:
            src_s, dst_s = str(src), str(dst)
            for node_id in (src_s, dst_s):
                if node_id not in nodes:
                    nodes[node_id] = {
                        "id": node_id,
                        "name": _label(node_id),
                        "role": _ROLE_CONFOUNDER,
                        "classification": None,
                        "status": None,
                        "share_of_change": None,
                    }
            kind = "treatment" if src_s == driver and dst_s == target else "adjustment"
            add_link(src_s, dst_s, kind=kind, classification=finding.classification)

    identity_only = False
    if drawn == 0 or len(links) == 0:
        # No DoWhy DAG was estimated (the engine found the exact accounting split but no upstream driver): draw that
        # split, which is the structure the diagnosis did establish, instead of an empty tab.
        nodes = {target: nodes[target]}
        links = {}
        for term in report.bridge:
            _add_identity_term(nodes, links, term.metric, term.parent or target, term.share_of_change)
        if not links:
            for dec_term in report.decomposition:
                if dec_term.basis == "identity":
                    _add_identity_term(nodes, links, dec_term.metric, target, dec_term.share_of_change)
        if not links:
            return None
        identity_only = True

    # Table tab: one row per directed edge so CSV export still works.
    data = [
        {
            "source": link["source"],
            "target": link["target"],
            "kind": link["kind"],
            "classification": link.get("classification") or "",
        }
        for link in links.values()
    ]
    return {
        "chart_type": "causal_graph",
        "title": f"Causal graph · {_label(target)}",
        "metrics": [target, *[n for n in nodes if n != target]],
        "dimensions": [],
        "series": [{"key": "kind", "name": "Edge"}],
        "xAxis": {"type": "category", "key": "source"},
        "yAxis": [{"type": "value", "name": ""}],
        "data": data,
        "nodes": list(nodes.values()),
        "links": list(links.values()),
        "warnings": [
            ("No upstream driver was causally identified. The arrows are the exact accounting identity the change "
             "splits into (term → what it adds up to), not estimated causal effects."
             if identity_only else
             "Edges are the explicit DAGs DoWhy used for each tested driver "
             "(treatment → outcome plus the backdoor adjustment set)."),
        ],
    }
