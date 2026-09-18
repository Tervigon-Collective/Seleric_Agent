"""Causal-graph-first candidate discovery.

Finds every node with a directed path to the outcome metric on the registered
causal graph -- the full "responsible node" set DoWhy will test -- instead of
asking an LLM to guess candidates. An LLM never proposes a mechanism here;
observed deviation magnitude (anomalies/evidence) only decides *ranking*, i.e.
which ancestors get a DoWhy run first when the ancestor set exceeds budget.
"""

from __future__ import annotations

from typing import Any

import structlog

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis
from seleric_swarm.agents.diagnostic.ontology import (
    graph_id_for_outcome,
    graph_identity,
    node_for_metric,
)
from seleric_swarm.services.metrics import MetricRegistry

_log = structlog.get_logger("seleric_swarm.agents.diagnostic")
_METRICS: MetricRegistry | None = None


def _registry(ctx: DiagnosticContext) -> MetricRegistry:
    if ctx.deps.metrics is not None:
        return ctx.deps.metrics
    global _METRICS
    if _METRICS is None:
        _METRICS = MetricRegistry("config/metric_registry.yaml")
    return _METRICS


def _label(metric_id: str) -> str:
    return metric_id.removeprefix("metric.").replace("_", " ")


def _movement_score(ctx: DiagnosticContext, metric_id: str) -> tuple[float, str, list[str]]:
    """Observed deviation magnitude for a candidate metric, from anomalies
    first (they carry a computed deviation_pct), else evidence change_pct,
    else calculated from the observations DataFrame if present."""
    for a in ctx.anomalies:
        if a.metric_id == metric_id:
            return abs(a.deviation_pct or 0.0), a.direction, list(a.evidence_refs or [])
    score = 0.0
    direction = ""
    refs: list[str] = []
    for e in ctx.evidence_for_metric(metric_id):
        eid = str(e.get("evidence_id") or e.get("artifact_id") or "")
        if eid and eid not in refs:
            refs.append(eid)
        try:
            score = max(score, abs(float(e.get("change_pct") or 0.0)))
        except (TypeError, ValueError):
            pass
        direction = str(e.get("direction") or direction or "")
    if score > 0.0:
        return score, direction, refs

    # Calculate movement from the fetched observations frame if available
    obs = ctx.request.observations
    if obs is not None and hasattr(obs, "columns") and metric_id in obs.columns:
        try:
            col = obs[metric_id].dropna()
            if len(col) >= 4:
                n_mission = min(3, len(col) // 2)
                baseline = col.iloc[:-n_mission]
                mission = col.iloc[-n_mission:]
                b_mean = float(baseline.mean())
                m_mean = float(mission.mean())
                if abs(b_mean) > 1e-9:
                    change_pct = (m_mean - b_mean) / abs(b_mean) * 100.0
                    dir_str = "up" if change_pct > 1.0 else ("down" if change_pct < -1.0 else "")
                    if abs(change_pct) >= 1.0:
                        return abs(change_pct), dir_str, refs
        except Exception as exc:
            _log.debug("diagnostic.discovery.observation_movement_skipped", metric_id=metric_id, error=str(exc))

    return score, direction, refs


def _domains_for(metric_id: str, ctx: DiagnosticContext) -> list[str]:
    owner = _registry(ctx).owner_agent_for(metric_id)
    if owner:
        return [owner.removesuffix("_agent")]
    for e in ctx.evidence_for_metric(metric_id):
        domain = (e.get("provenance") or {}).get("domain")
        if domain:
            return [str(domain).removesuffix("_agent")]
    return []


def _node_for(metric_id: str, registry: MetricRegistry) -> str:
    """Graph node label for a metric, resolving to its legacy ``metric.*``
    spelling first so an explicit ``node_by_metric`` override always applies
    regardless of whether the caller has the live-catalogue bare id or the
    legacy id in hand (``cac`` / ``metric.cac`` are the same real metric)."""
    return node_for_metric(graph_identity(metric_id, registry))


def _metric_for_node(node: str, registry: MetricRegistry, outcome: str) -> str | None:
    if node != outcome and registry.get(node) is not None:
        return node
    for m in registry.all():
        if m.id != outcome and _node_for(m.id, registry) == node:
            return m.id
    return None


def graph_candidate_metric_ids(graph: Any, outcome: str, registry: MetricRegistry) -> list[str]:
    """Fetchable metric ids for every ancestor of ``outcome`` on ``graph``, in
    graph order (no ranking) -- shared by ``identify_candidate_nodes`` and by
    ``swarm_bridge.py`` to know what series to pre-fetch before the graph runs.
    """
    if graph is None or not outcome:
        return []
    outcome_node = _node_for(outcome, registry)
    out: list[str] = []
    seen: set[str] = set()
    for node in graph.ancestors(outcome_node):
        metric_id = _metric_for_node(node, registry, outcome)
        if metric_id and metric_id not in seen:
            seen.add(metric_id)
            out.append(metric_id)
    return out


async def identify_candidate_nodes(ctx: DiagnosticContext) -> list[DiagnosticHypothesis]:
    outcome = ctx.outcome_metric

    # Semantic neighbors from the OM entity cluster are context, not causal
    # candidates -- recorded for the scenario-narration step, never turned
    # into a treatment_metric. An ontology-port failure must never break
    # discovery, which does not depend on it.
    if ctx.deps.ontology is not None:
        try:
            related = await ctx.deps.ontology.related_metrics(outcome)
            ctx.scratch["semantic_neighbors"] = list(related.get("related_metrics") or [])
            ctx.scratch["entity_cluster"] = related.get("entity_cluster")
            ctx.scratch["om_data_product"] = related.get("data_product")
        except Exception as exc:
            _log.debug("diagnostic.discovery.ontology_skipped", error=str(exc))

    graph_id = ctx.request.context.get("graph_id") or graph_id_for_outcome(outcome)
    graph = ctx.deps.causal_graphs.get(graph_id)
    cap = ctx.policies.budget("max_causal_candidates")
    if graph is None or not outcome:
        return []

    registry = _registry(ctx)

    formula_deps: set[str] = set()
    outcome_def = registry.get(outcome)
    if outcome_def:
        for dep in outcome_def.raw.get("depends_on") or []:
            dep_str = str(dep)
            dep_id = f"metric.{dep_str}" if not dep_str.startswith("metric.") else dep_str
            if registry.get(dep_id) is not None:
                formula_deps.add(dep_id)
            else:
                for m in registry.all():
                    if dep_str in m.aliases or m.catalogue_metric == dep_str:
                        formula_deps.add(m.id)

    candidates: dict[str, tuple[float, str, list[str]]] = {}
    for metric_id in graph_candidate_metric_ids(graph, outcome, registry):
        score, direction, refs = _movement_score(ctx, metric_id)
        # Prioritize direct formula components while preserving observed movement ranking
        is_formula = metric_id in formula_deps
        rank_score = (score + 50.0) if is_formula else score
        prev = candidates.get(metric_id)
        if prev is None or rank_score > prev[0]:
            candidates[metric_id] = (rank_score, direction, refs)

    # caller-declared alternatives are still honored, ranked alongside graph candidates
    for alt in ctx.request.context.get("alternatives_to_test", []) or []:
        candidates.setdefault(str(alt), (0.0, "", []))

    ranked = sorted(candidates.items(), key=lambda kv: -kv[1][0])[: cap if cap > 0 else None]

    # When no time-series observation frame is available, DoWhy has no empirical
    # data to fit a causal model against -- it can only echo the graph metadata.
    # Running it on a metric with zero observed movement (no anomaly, no evidence
    # change_pct, no DataFrame column) produces "ASSOCIATION" at best and a
    # vague narrative at worst. Skip those unless they are direct formula
    # components (formula deps are always structurally relevant) or the caller
    # explicitly requested them via `alternatives_to_test`.
    no_obs_frame = ctx.request.observations is None
    explicit_alts = {str(a) for a in ctx.request.context.get("alternatives_to_test", []) or []}

    def _build(metric_id: str, direction: str, refs: list[str], *, is_formula: bool) -> DiagnosticHypothesis:
        if direction in {"up", "down"}:
            moved = f"moved {direction}"
        elif is_formula:
            moved = "is a direct formula component"
        else:
            moved = "is an unobserved upstream candidate on the causal graph"
        return DiagnosticHypothesis(
            statement=f"{_label(metric_id)} {moved} of {_label(outcome)}.",
            treatment_metric=metric_id,
            outcome_metric=outcome,
            domains=_domains_for(metric_id, ctx),
            supporting_evidence=refs,
            synthetic=ctx.synthetic_inputs(),
        )

    out: list[DiagnosticHypothesis] = []
    for metric_id, (rank_score, direction, refs) in ranked:
        is_formula = metric_id in formula_deps
        has_observed_signal = direction in {"up", "down"} or rank_score > 0.0
        if no_obs_frame and not has_observed_signal and not is_formula and metric_id not in explicit_alts:
            _log.debug(
                "diagnostic.discovery.skipped_unobserved_no_frame",
                metric_id=metric_id,
                outcome=outcome,
            )
            continue
        out.append(_build(metric_id, direction, refs, is_formula=is_formula))

    if not out and ranked:
        # Every graph ancestor got filtered for lacking observed movement --
        # that heuristic exists to deprioritize noise when there's at least
        # one real signal, not to make a structurally-connected outcome (one
        # with real ancestors on the causal graph) report zero candidates.
        # Fall back to the graph's own ranking so DoWhy still gets a shot at
        # metadata-only evidence instead of an empty, silently-dropped result.
        _log.debug("diagnostic.discovery.fallback_to_unfiltered_candidates", outcome=outcome)
        for metric_id, (_rank_score, direction, refs) in ranked:
            out.append(_build(metric_id, direction, refs, is_formula=metric_id in formula_deps))

    return out
