"""The one-sentence headline and the bridge lines of the report (moved from ``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

import math

from seleric_swarm.diagnosis.detect.seasonal import _PERIOD_KIND
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import _rel
from seleric_swarm.diagnosis.domain.report import BridgeTerm, DiagnosisReport, EventSummary
from seleric_swarm.diagnosis.operators.where_dimensions import _covers_whole
from seleric_swarm.diagnosis.ports.formatting import ValueFormatting
from seleric_swarm.diagnosis.report.format import _fmt, _label, _offsetting, _pct, _share, _val


def _opposite(direction: str | None) -> str:
    return "down" if direction == "up" else "up"


def _headline(r: DiagnosisReport, *, cfg: DiagnosisConfig) -> str:
    e = r.event
    if e is None:
        return r.headline
    vs = "vs the comparison period's" if e.reference_kind == _PERIOD_KIND else "vs a usual"
    move = f"{r.outcome} was {_fmt(e.actual)} {vs} {_fmt(e.reference)} ({_pct(e.delta_pct)}, z={_fmt(e.z_score)})"
    if r.verdict == "no_unusual_change":
        claim = ""
        if e.premise == "contradicted":
            claim = f" Note it moved {e.direction}, the opposite of the direction asked about."
        elif e.premise == "vs_previous_only":
            claim = (
                f" It did go {_opposite(e.direction)} versus the day before ({_pct(e.previous_period.get('delta_pct'))}),"
                " but that was a move back toward its usual level, not an unusual change."
            )
        hot = next(
            (d for d in (*r.dimensions, *r.chain_dimensions) if _covers_whole(d, cfg=cfg) and d.localised and d.top and d.top[0].significant),
            None,
        )
        note = ""
        if hot is not None:
            s0 = hot.top[0]
            note = (
                f" One segment did move beyond its own normal range: {hot.dimension}={s0.segment} (z={_fmt(s0.z_score)})"
                " — worth watching, but it did not make the total unusual."
            )
        return f"{move}: within normal day-to-day variation, so there is no unusual change to explain.{claim}{note}"
    parts = [move + "."]
    if e.premise == "contradicted":
        parts.insert(0, f"Contrary to the question, {r.outcome} did not go {'down' if e.direction == 'up' else 'up'} — it went {e.direction}.")
    elif e.premise == "vs_previous_only":
        parts.insert(0, (
            f"{r.outcome} went {_opposite(e.direction)} versus the day before "
            f"({_pct(e.previous_period.get('delta_pct'))}) but {e.direction} versus its usual level{_usual_for(e)}."
        ))
    if e.strength == "moderate":
        pct = e.sampling.get("percentile_vs_history", math.nan)
        tail = pct if e.direction == "down" else 1 - pct
        parts.append(
            f"That is a bigger swing than about {1 - tail:.0%} of normal days but not extreme, so treat it as a "
            "notable change rather than a clear-cut anomaly."
        )
    if r.decomposition:
        lead = max(r.decomposition, key=lambda t: abs(t.contribution))
        if _offsetting([t.share_of_change for t in r.decomposition]):
            parts.append(
                "Arithmetically, " + ", ".join(f"{t.metric} {t.contribution:+.4g}" for t in r.decomposition)
                + f" (in {r.outcome}'s units; the parts offset each other)."
            )
        else:
            parts.append(f"Arithmetically, {lead.metric} accounts for {_pct(lead.share_of_change).lstrip('+')} of the change.")
    if r.bridge:
        parents = {t.parent for t in r.bridge}
        leaves = sorted((t for t in r.bridge if t.metric not in parents), key=lambda t: -abs(t.share_of_change or 0.0))
        parts.append(
            "Exactly (accounting identity " + "; ".join(r.bridge_identity) + "): "
            + ", ".join(f"{t.metric} {_pct(t.share_of_change)}" for t in leaves[:4])
            + " of the change."
        )
    if r.chain:
        lead_c = max(r.chain, key=lambda t: abs(t.contribution))
        other = next(t for t in r.chain if t is not lead_c)
        parts.append(
            f"One level down, {r.chain_identity.split(' ')[0]} ≈ {lead_c.metric} × {other.metric}: {other.metric} moved "
            f"{_pct(_rel(other.event - other.reference, other.reference))}, {lead_c.metric} moved "
            f"{_pct(_rel(lead_c.event - lead_c.reference, lead_c.reference))}, so {lead_c.metric} carries most of it."
        )
        cdim = next((d for d in r.chain_dimensions if _covers_whole(d, cfg=cfg) and (d.simpsons_paradox or d.localised)), None)
        if cdim and cdim.top:
            parts.append(f"The {lead_c.metric} drop is concentrated in {cdim.dimension}={cdim.top[0].segment}." if (lead_c.contribution < 0) else
                         f"The {lead_c.metric} rise is concentrated in {cdim.dimension}={cdim.top[0].segment}.")
    top_dim = next((d for d in r.dimensions if _covers_whole(d, cfg=cfg) and (d.simpsons_paradox or d.localised)), None)
    if top_dim and top_dim.top:
        s = top_dim.top[0]
        parts.append(f"It is concentrated in {top_dim.dimension}={s.segment}.")
    elif r.dimensions and all(d.broad_based for d in r.dimensions):
        parts.append("It is broad-based across every segment checked.")
    for m in (f for f in r.measurement if f.status == "diverged" and cfg.measurement_acts):
        parts.append(
            f"{m.metric} disagrees with {m.peer}, which counts the same events: check how {m.metric} is recorded before "
            "looking for a business cause."
        )
    causes = [f for f in r.drivers if f.status == "implicated" and f.classification in ("supported_cause", "likely_contributor")]
    if causes:
        parts.append("; ".join(
            f"{f.driver} is a {(f.classification or '').replace('_', ' ')} (~{_pct(f.share_of_change).lstrip('+')} of the change)"
            for f in causes[:3]
        ) + ".")
    else:
        parts.append(
            "No upstream driver could be causally identified from the available data; the figures above say what "
            "and where it changed, not why."
            if (r.decomposition or r.chain or r.dimensions) else
            "No upstream driver could be causally identified from the available data."
        )
    return " ".join(parts)


def _bridge_lines(r: DiagnosisReport, lineage: dict[str, MetricMeta], *, fmt: ValueFormatting) -> list[str]:
    """The exact accounting bridge, both comparisons, reconciled to the total."""
    e = r.event
    assert e is not None
    name = _label(r.outcome, lineage)
    prev_total = e.previous_period.get("delta")

    def term(t: BridgeTerm) -> str:
        indent = "  " * (t.depth - 1)
        verb = "adds" if t.sign > 0 else "subtracts"
        vs_prev = (
            f"; vs the day before {_val(t.previous, t.metric, lineage, fmt)} → {_val(t.contribution_vs_previous, t.metric, lineage, fmt)} "
            "effect on " + _label(t.parent, lineage)
            if t.previous is not None and t.contribution_vs_previous is not None else ""
        )
        return (
            f"{indent}- {_label(t.metric, lineage)} ({verb}; part of {_label(t.parent, lineage)}): usual "
            f"{_val(t.reference, t.metric, lineage, fmt)} → {_val(t.event, t.metric, lineage, fmt)}, effect "
            f"{_val(t.contribution, t.metric, lineage, fmt)} on {_label(t.parent, lineage)} "
            f"({_share(t.share_of_change)} of the {name} change){vs_prev}"
        )

    by_parent: dict[str, list[BridgeTerm]] = {}
    for t in r.bridge:
        by_parent.setdefault(t.parent, []).append(t)
    ordered: list[str] = []

    def walk(node: str) -> None:
        for t in by_parent.get(node, []):
            ordered.append(term(t))
            walk(t.metric)

    walk(r.outcome)
    recon = f"Reconciles to the {name} change within {_val(abs(r.bridge_residual or 0.0), r.outcome, lineage, fmt)}"
    if prev_total is not None and r.bridge_residual_vs_previous is not None:
        recon += (
            f" (vs the usual) and within {_val(abs(r.bridge_residual_vs_previous), r.outcome, lineage, fmt)} "
            f"(vs the day before, total change {_val(prev_total, r.outcome, lineage, fmt)})"
        )
    return [
        "WHAT CHANGED — EXACT BRIDGE (accounting identity verified on every history day; arithmetic, not a "
        "cause — report these as the components of the change, never as 'not explaining' it): "
        + "; ".join(r.bridge_identity) + ".",
        *ordered,
        recon + ".",
    ]


def _same_weekday(e: EventSummary) -> bool:
    return e.reference_kind.startswith("same weekday")


def _usual_for(e: EventSummary) -> str:
    return " for that weekday" if _same_weekday(e) else ""


def _usual_blend(e: EventSummary) -> str:
    return (
        "recent same weekdays, scaled to the recent overall level (so a recent surge or slump moves it)"
        if _same_weekday(e) else "the most recent normal days, scaled to the recent overall level"
    )


def _reference_words(e: EventSummary) -> str:
    return "the comparison period" if e.reference_kind == _PERIOD_KIND else "the usual level"


def _previous_label(r: DiagnosisReport) -> str:
    n = len(r.event_window)
    return "the day before" if n == 1 else f"the previous {n} days"
