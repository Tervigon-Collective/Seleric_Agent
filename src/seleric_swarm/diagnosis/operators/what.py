"""WHAT: what changed, by exact accounting (plan P2.2). Candidates come from catalogue lineage and are accepted only when
the data verify them day by day; the change is split with Shapley values. Evidence basis: identity (arithmetic, not a
causal claim). Failure modes: no verified identity (the layer is empty, not guessed)."""

from __future__ import annotations

from collections.abc import Callable

from seleric_swarm.diagnosis.domain.analysis import EventAnalysis, WhatResult
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.report import BridgeTerm, DecompositionTerm, DimensionFinding
from seleric_swarm.diagnosis.operators.identities import discover_rate_chains
from seleric_swarm.diagnosis.operators.what_terms import (
    _bridge,
    _chain_step,
    _decompose,
    _own_window,
)
from seleric_swarm.diagnosis.operators.where_rate import _analyse_rate_dimension


def run_what(ana: EventAnalysis, *, cfg: DiagnosisConfig, clock: Callable[[], float]) -> WhatResult:
    inp, y, event_days, history, baseline = ana.inp, ana.y, ana.event_days, ana.history, ana.baseline
    meta_y, window, ref_kind, identity, y_ev = ana.meta_y, ana.window, ana.ref_kind, ana.identity, ana.y_ev
    y_rf, delta, prev_days, previous = ana.y_rf, ana.delta, ana.prev_days, ana.previous
    decomposition: list[DecompositionTerm] = []
    bridge: list[BridgeTerm] = []
    bridge_identity: list[str] = []
    bridge_residual: float | None = None
    bridge_residual_vs_previous: float | None = None
    chain_identity = ""
    chain: list[DecompositionTerm] = []
    chain_dimensions: list[DimensionFinding] = []
    # ---- layer 2: decomposition ---------------------------------------------------
    if identity is not None:
        decomposition = _decompose(inp, window, identity, y_ev, y_rf, cfg=cfg)
    # ---- layer 2a: exact additive bridge (accounting identity) ---------------------
    if meta_y.additive and inp.bridge_candidates:
        bridge, bridge_identity, bridge_residual, bridge_residual_vs_previous = _bridge(
            inp, window, history, prev_days, delta, previous.get("delta"), cfg=cfg, clock=clock
        )

    # ---- layer 2b: next link — dominant additive component as rate x base ----------
    chain_target, t_ev, t_rf = (y, y_ev, y_rf) if meta_y.additive else (None, None, None)
    additive_terms = [t for t in decomposition if inp.lineage.get(t.metric, MetricMeta(t.metric)).additive]
    if not additive_terms and bridge:
        # The bridge term (at any level) that moved the outcome most, never one it splits further.
        parents = {t.parent for t in bridge}
        leaves = [t for t in bridge if t.metric not in parents]
        lead_b = max(leaves, key=lambda t: abs(t.share_of_change or 0.0))
        chain_target, t_ev, t_rf = lead_b.metric, lead_b.event, lead_b.reference
    if additive_terms:
        lead = max(additive_terms, key=lambda t: abs(t.contribution))
        chain_target, t_ev, t_rf = lead.metric, lead.event, lead.reference
    if chain_target is not None and t_ev is not None and t_rf is not None:
        chains = discover_rate_chains(chain_target, inp.lineage, inp.series, history, cfg=cfg)
        if chains:
            ident_c = chains[0]
            chain_identity = ident_c.describe() + f" (agrees within {ident_c.dispersion:.0%} on a typical day)"
            chain = _chain_step(inp, window, ident_c, t_ev, t_rf, cfg=cfg)
            rate_m, base_m = ident_c.factors[0][0], ident_c.factors[1][0]
            r_window = window if baseline else _own_window(inp.series[rate_m], history, event_days, additive=False, kind=ref_kind, cfg=cfg)[0]
            for dim, seg in (inp.segments.get(rate_m) or {}).items():
                if r_window is None or dim not in (inp.segments.get(base_m) or {}):
                    continue
                rate_finding = _analyse_rate_dimension(dim, seg, inp.segments[base_m][dim], r_window, inp.series[rate_m], inp.series.get(base_m), cfg=cfg)
                if rate_finding is not None:
                    chain_dimensions.append(rate_finding)
            chain_dimensions.sort(key=lambda f: (not f.simpsons_paradox, not f.localised, -abs(f.hot_share or 0.0)))
    return WhatResult(
        decomposition=decomposition, bridge=bridge, bridge_identity=bridge_identity, bridge_residual=bridge_residual,
        bridge_residual_vs_previous=bridge_residual_vs_previous, chain_identity=chain_identity, chain=chain,
        chain_dimensions=chain_dimensions,
    )
