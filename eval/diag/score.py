"""Scorer (plan P0.6): compare an engine answer with a case's truth. No engine imports.

Reads the structured report the diagnosis tool returns (``DiagnosisReport.as_dict()``). Two kinds of result are kept
apart on purpose (plan section 7): how often a planted cause is found, and how often a cause is named that is not there.

For every case:
- ``named``   metrics the engine calls a cause: classification ``supported_cause`` or ``likely_contributor``
- ``listed``  metrics it lists as implicated at all (includes ``correlation``)
- a planted cause counts as found when it, or a member of its ``same_quantity_group`` in the graph, is named
  (``named``), only listed (``listed``) or absent (``missed``); ``rank`` is its place among the named causes in the order
  the report gives them. A metric on the planted path (the intermediate steps) is a proximate cause, not a false one.
- ``false_attribution`` is a named cause that is neither planted, nor on the planted path, nor in a planted group, or any
  named cause in a case whose truth has no findable cause (hidden, measurement, none) or that lists it in ``must_not_attribute``.
- ``correct_verdict`` follows the truth: found/located/no change/premise (measurement artifacts are not expressible by the
  engine today, so that class is judged on false attribution and on admitting the cause is not identified).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import case_format as C

NAMED = ("supported_cause", "likely_contributor")
NOT_IDENTIFIED = ("located_cause_not_identified", "root_cause_not_identified", "insufficient_data")
FOUND = ("explained", "partially_explained")
DETECTABLE_Z = 2.0  # below this a planted change cannot be told from ordinary variation: "no unusual change" is then a fair answer


@dataclass
class CaseScore:
    case_id: str
    kind: str
    cause_class: str
    tags: tuple[str, ...]
    ran: bool
    verdict: str = ""
    named: list[str] = field(default_factory=list)
    listed: list[str] = field(default_factory=list)
    found: dict[str, str] = field(default_factory=dict)  # planted cause -> named | listed | missed
    rank: dict[str, int | None] = field(default_factory=dict)
    top1: bool | None = None
    top3: bool | None = None
    localised: bool | None = None  # the planted scope is the top segment of some dimension finding
    false_attributions: list[str] = field(default_factory=list)
    correct_verdict: bool | None = None
    unexplained_share: float | None = None
    unexplained_in_range: bool | None = None
    detectable: bool = True  # the planted change is at least DETECTABLE_Z sigmas of the outcome's own variability
    decomposition_ok: bool | None = None  # the engine's split puts the change where the truth says (and the right effect dominates)
    queries: int = 0
    error: str = ""


def _groups(graph: dict[str, Any]) -> dict[str, set[str]]:
    return {n["id"]: set(n.get("same_quantity_group") or []) | {n["id"]} for n in graph["nodes"]}


def _downstream(graph: dict[str, Any], metric: str) -> set[str]:
    """Everything the graph says the metric feeds: mechanism edges and the accounting edges that carry it onward.

    These are proximate effects of a cause, so naming one of them as the cause is not a false attribution.
    """
    forward: dict[str, set[str]] = {}
    for e in graph["edges"]:
        if e["kind"] in ("mechanism", "composition", "ratio_component", "declared_input"):
            forward.setdefault(e["from"], set()).add(e["to"])
    seen: set[str] = set()
    todo = [metric]
    while todo:
        for nxt in forward.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def _path_nodes(planted: list[Any]) -> set[str]:
    nodes: set[str] = set()
    for p in planted:
        nodes.add(p.metric)
        for a, b in p.path:
            nodes |= {a, b}
    return nodes


def score_case(case: C.Case, report: dict[str, Any], ran: bool, error: str, queries: int, planted: list[Any],
               graph: dict[str, Any]) -> CaseScore:
    s = CaseScore(case.id, case.kind, case.truth.cause_class, case.tags, ran, queries=queries, error=error)
    s.detectable = case.truth.signal_z is None or case.truth.signal_z >= DETECTABLE_Z
    if not ran:
        s.false_attributions = []
        return s
    groups = _groups(graph)
    drivers = [d for d in report.get("drivers") or [] if d.get("status") == "implicated"]
    s.verdict = str(report.get("verdict") or "")
    s.named = [d["driver"] for d in drivers if d.get("classification") in NAMED]
    s.listed = [d["driver"] for d in drivers]
    s.unexplained_share = report.get("unexplained_share")
    truth = case.truth

    for cause in truth.causes:
        family = groups.get(cause.metric, {cause.metric})
        hits = [i for i, m in enumerate(s.named) if m in family]
        s.found[cause.metric] = "named" if hits else "listed" if any(m in family for m in s.listed) else "missed"
        s.rank[cause.metric] = hits[0] + 1 if hits else None
    if truth.causes:
        ranks = [r for r in s.rank.values() if r is not None]
        s.top1 = all(r == 1 for r in s.rank.values()) if len(truth.causes) == 1 else bool(ranks) and min(ranks) == 1
        s.top3 = all(r is not None and r <= 3 for r in s.rank.values()) if len(truth.causes) == 1 else bool(ranks) and min(ranks) <= 3
        scope_values = {v for p in planted for v in p.scope.values()}
        s.localised = any(
            (t := (dim.get("top") or [])) and t[0].get("segment") in scope_values
            for dim in report.get("dimensions") or []
        ) if scope_values else None

    allowed: set[str] = set()
    for cause in truth.causes:
        allowed |= groups.get(cause.metric, {cause.metric})
    allowed |= _path_nodes(planted)
    for cause in truth.causes:
        allowed |= _downstream(graph, cause.metric)
    for p in planted:
        allowed |= _downstream(graph, p.metric)
    # A named cause is false unless it is the planted cause, a member of its group, or something it feeds; a metric the
    # case forbids is false whatever else is true of it. Non-findable worlds have no cause to name, only the planted
    # metric's own path (the hidden cause's visible footprint) is tolerated.
    s.false_attributions = [m for m in s.named if m not in allowed or m in truth.must_not_attribute]

    event = report.get("event") or {}
    expected = truth.expected
    if expected == "cause_found":
        s.correct_verdict = s.verdict in FOUND or (s.verdict in NOT_IDENTIFIED and bool(s.named))
        if not s.detectable and s.verdict == "no_unusual_change":
            s.correct_verdict = True  # below the noise floor, saying nothing unusual happened is right
    elif expected == "located_not_identified" or expected == "measurement_artifact":
        s.correct_verdict = s.verdict in NOT_IDENTIFIED and not s.named
    elif expected == "no_change":
        s.correct_verdict = s.verdict == "no_unusual_change"
    elif expected == "premise_false":
        s.correct_verdict = event.get("premise") in ("contradicted", "not_unusual") or s.verdict == "no_unusual_change"
    if truth.decomposition is not None:
        dec = truth.decomposition
        dim = next((d for d in report.get("dimensions") or [] if d.get("dimension") == dec.dimension), None)
        top = (dim.get("top") or []) if dim else []
        ok = bool(top) and top[0].get("segment") in dec.top_segments
        if ok and dec.dominant:
            mix, rate = abs(dim.get("mix_effect") or 0.0), abs(dim.get("rate_effect") or 0.0)
            ok = mix > rate if dec.dominant == "mix" else rate > mix
        s.decomposition_ok = ok
    if truth.unexplained_share is not None and s.unexplained_share is not None:
        lo, hi = truth.unexplained_share
        s.unexplained_in_range = lo <= float(s.unexplained_share) <= hi
    return s


def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def aggregate(scores: list[CaseScore]) -> dict[str, Any]:
    """Rates with their denominators. Findable and non-findable worlds are never pooled into one number."""
    ran = [s for s in scores if s.ran and s.kind != "stability"]
    stability = [s for s in scores if s.ran and s.kind == "stability"]
    findable = [s for s in ran if s.cause_class == "findable" and s.detectable]
    below_noise = [s for s in ran if s.cause_class == "findable" and not s.detectable]
    other = [s for s in ran if s.cause_class != "findable"]
    return {
        "cases": len(scores), "crashed": sum(1 for s in scores if not s.ran),
        "findable": {
            "n": len(findable),
            "top1": _rate([bool(s.top1) for s in findable]), "top3": _rate([bool(s.top3) for s in findable]),
            "named": _rate([all(v == "named" for v in s.found.values()) for s in findable]),
            "listed_or_named": _rate([all(v != "missed" for v in s.found.values()) for s in findable]),
            "localised": _rate([s.localised for s in findable if s.localised is not None]),
            "false_attribution": _rate([bool(s.false_attributions) for s in findable]),
            "correct_verdict": _rate([bool(s.correct_verdict) for s in findable]),
        },
        "below_noise": {"n": len(below_noise), "no_unusual_change": _rate([s.verdict == "no_unusual_change" for s in below_noise]),
                        "false_attribution": _rate([bool(s.false_attributions) for s in below_noise])},
        "not_findable": {
            "n": len(other),
            "false_attribution": _rate([bool(s.false_attributions) for s in other]),
            "correct_verdict": _rate([bool(s.correct_verdict) for s in other]),
        },
        # Real incidents with an unknown cause: no accuracy claim, only what the engine said (checked for stability later).
        "stability": [{"case": s.case_id, "verdict": s.verdict, "named": s.named, "unexplained_share": s.unexplained_share} for s in stability],
        "decomposition_ok": _rate([s.decomposition_ok for s in ran if s.decomposition_ok is not None]),
        "unexplained_in_range": _rate([s.unexplained_in_range for s in ran if s.unexplained_in_range is not None]),
        "by_tag": {
            tag: {"n": len(g), "false_attribution": _rate([bool(s.false_attributions) for s in g]),
                  "correct_verdict": _rate([bool(s.correct_verdict) for s in g])}
            for tag in sorted({t for s in ran for t in s.tags}) for g in [[s for s in ran if tag in s.tags]]
        },
    }
