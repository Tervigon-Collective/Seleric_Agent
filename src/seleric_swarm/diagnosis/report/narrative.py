"""The plain-language answer skeleton: every required part, catalogue labels, no ids (moved from
``causal/diagnosis.py``, plan P2.2)."""

from __future__ import annotations

from seleric_swarm.diagnosis.detect.seasonal import _PERIOD_KIND
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import _sign
from seleric_swarm.diagnosis.domain.report import DiagnosisReport, DimensionFinding, SegmentMove
from seleric_swarm.diagnosis.operators.where_dimensions import _covers_whole
from seleric_swarm.diagnosis.ports.formatting import ValueFormatting
from seleric_swarm.diagnosis.report.format import _chg, _effect, _label, _offsetting, _share, _val
from seleric_swarm.diagnosis.report.headline import (
    _bridge_lines,
    _opposite,
    _previous_label,
    _reference_words,
    _usual_blend,
    _usual_for,
)


def _rate_segment_text(s: SegmentMove, metric: str, lineage: dict[str, MetricMeta], *, fmt: ValueFormatting) -> str:
    """One segment of a rate split: its own rate, plus its volume share when the mix carried it."""
    txt = (
        f"the {_label(metric, lineage).lower()} for {s.segment} went from {_val(s.rate_reference, metric, lineage, fmt)} "
        f"to {_val(s.rate_event, metric, lineage, fmt)}" + (" — beyond its own normal range" if s.significant else "")
    )
    if (
        s.volume_share_reference is not None and s.volume_share_event is not None
        and abs(s.mix_effect or 0.0) > abs(s.rate_effect or 0.0)
    ):
        txt += (
            f", while its share of the volume went from {s.volume_share_reference:.0%} to {s.volume_share_event:.0%}"
        )
        if s.reference <= 0.01 * s.event:
            txt += " (it had almost no volume on the usual days, so it is new in this period)"
        elif s.event <= 0.01 * s.reference:
            txt += " (it stopped in this period)"
    return txt


def _ratio_segment_text(s: SegmentMove, d: DimensionFinding, metric: str, lineage: dict[str, MetricMeta], *, fmt: ValueFormatting) -> str:
    num, den = d.numerator or "", d.denominator or ""
    txt = (
        f"{s.segment}: {_label(num, lineage)} {_val(s.numerator_reference, num, lineage, fmt)} → "
        f"{_val(s.numerator_event, num, lineage, fmt)}, {_label(den, lineage)} {_val(s.reference, den, lineage, fmt)} → "
        f"{_val(s.event, den, lineage, fmt)}"
    )
    if s.rate_reference is not None and s.rate_event is not None:
        txt += f" ({_label(metric, lineage)} {_val(s.rate_reference, metric, lineage, fmt)} → {_val(s.rate_event, metric, lineage, fmt)})"
    share = s.share_of_change
    if share is not None and share < 0:
        return txt + f", offsetting about {_share(-share)} of the change"
    return txt + f", about {_share(share)} of the change"


def _narrative(r: DiagnosisReport, lineage: dict[str, MetricMeta], *, cfg: DiagnosisConfig, fmt: ValueFormatting) -> list[str]:
    e = r.event
    if e is None:
        return [r.headline]
    name = _label(r.outcome, lineage)
    when = r.event_window[0] if len(r.event_window) == 1 else f"{r.event_window[0]} to {r.event_window[-1]}"
    out: list[str] = []
    if e.reference_kind == _PERIOD_KIND:
        base = sorted({x for v in e.reference_days.values() for x in v})
        happened = (
            f"WHAT HAPPENED: {name} was {_val(e.actual, r.outcome, lineage, fmt)} on {when}, against "
            f"{_val(e.reference, r.outcome, lineage, fmt)} in the comparison period {base[0]} to {base[-1]} "
            f"({_chg(e.actual, e.reference)})."
        )
    else:
        happened = (
            f"WHAT HAPPENED: {name} was {_val(e.actual, r.outcome, lineage, fmt)} on {when}, against a usual "
            f"{_val(e.reference, r.outcome, lineage, fmt)}{_usual_for(e)} ({_chg(e.actual, e.reference)}; 'usual' blends "
            f"{_usual_blend(e)} and is not any single day's value)."
        )
        if e.previous_period.get("value") is not None:
            happened += f" Versus {_previous_label(r)} it was {_chg(e.actual, e.previous_period['value'])}."
    if e.premise == "contradicted":
        happened += f" Note: it went {e.direction}, not {'down' if e.direction == 'up' else 'up'} as the question assumes."
    elif e.premise == "vs_previous_only":
        happened += (
            f" So it did go {_opposite(e.direction)} versus the day before, as the question says, but against its "
            f"usual level it went {e.direction}: LEAD with both comparisons — never say it did not go "
            f"{_opposite(e.direction)}."
        )
    happened += ({
        "strong": " The daily values of the two periods clearly differ, so this is a real shift.",
        "moderate": " The daily values of the two periods differ somewhat — a notable shift, not a clear one.",
        "none": " The daily values of the two periods overlap a lot, so the shift is within normal day-to-day "
                "variation: LEAD with that, and present the parts below as what moved, never as the reason it "
                "rose or fell.",
    } if e.reference_kind == _PERIOD_KIND else {
        "strong": " That is well outside its normal day-to-day range.",
        "moderate": " That is a larger swing than most normal days, but not extreme — a notable change, not a clear anomaly.",
        "none": " That is within its normal day-to-day range, so there is no unusual change to explain.",
    })[e.strength]
    if e.premise == "not_unusual" and abs(e.delta_pct or 0.0) < 0.10:
        happened += " Against its usual level it was essentially flat."
    out.append(happened)
    if r.bridge:
        out.extend(_bridge_lines(r, lineage, fmt=fmt))
    if r.decomposition and e.strength == "none" and e.reference_kind != _PERIOD_KIND:
        # A period comparison is exact accounting, so its shares stay meaningful.
        # Shares of a change that is within noise are meaningless (they explode as
        # the total nears zero); state the component moves only.
        out.append(
            "COMPONENTS, each from its usual level (not the previous period) to the period asked about (for context; the total did not move unusually): "
            + "; ".join(
                f"{_label(t.metric, lineage)} {_val(t.reference, t.metric, lineage, fmt)} → {_val(t.event, t.metric, lineage, fmt)} "
                f"({_chg(t.event, t.reference)})"
                for t in r.decomposition
            )
            + "."
        )
    elif r.decomposition:
        terms = sorted(r.decomposition, key=lambda t: -abs(t.contribution))
        if _offsetting([t.share_of_change for t in terms]):
            out.append(
                f"WHAT CHANGED (arithmetic, not a cause; the parts pull in opposite directions, so state each "
                f"one's effect on {name}, never a percent share): "
                + "; ".join(
                    f"{_label(t.metric, lineage)} went from {_val(t.reference, t.metric, lineage, fmt)} to "
                    f"{_val(t.event, t.metric, lineage, fmt)} ({_chg(t.event, t.reference)}), moving {name} by "
                    f"{_effect(t.contribution, r.outcome, lineage, fmt=fmt)}"
                    for t in terms
                )
                + f"; net {_effect(sum(t.contribution for t in terms), r.outcome, lineage, fmt=fmt)}."
            )
        else:
            out.append(
                "WHAT CHANGED (arithmetic, not a cause): "
                + "; ".join(
                    f"{_label(t.metric, lineage)} went from {_val(t.reference, t.metric, lineage, fmt)} to "
                    f"{_val(t.event, t.metric, lineage, fmt)} ({_chg(t.event, t.reference)}), "
                    f"{'accounting for' if _sign(t.contribution) == _sign(e.delta or 0) else 'offsetting'} about "
                    f"{_share(t.share_of_change)} of the change"
                    for t in terms
                )
                + "."
            )
    if r.chain and e.strength != "none":
        target = r.chain_identity.split(" ")[0]
        lead_c = max(r.chain, key=lambda t: abs(t.contribution))
        out.append(
            f"ONE LEVEL DOWN: {_label(target, lineage)} ≈ "
            + " × ".join(_label(t.metric, lineage) for t in r.chain)
            + f" (approximately{'; ' + r.chain_identity.split('(')[-1].rstrip(')') if '(' in r.chain_identity else ''}): "
            + "; ".join(
                f"{_label(t.metric, lineage)} {_val(t.reference, t.metric, lineage, fmt)} → {_val(t.event, t.metric, lineage, fmt)} "
                f"({_chg(t.event, t.reference)})"
                for t in r.chain
            )
            + (
                f", so {_label(lead_c.metric, lineage)} moved it by {_effect(lead_c.contribution, target, lineage, fmt=fmt)}."
                if _offsetting([t.share_of_change for t in r.chain])
                else f", so {_label(lead_c.metric, lineage)} carries about {_share(lead_c.share_of_change)} of it."
            )
        )
    if r.funnel and r.chain and e.strength != "none":
        base = _label(r.chain[-1].metric, lineage).lower()
        lead_f = max(r.funnel, key=lambda t: abs(t.share_of_change or 0.0))
        out.append(
            f"FUNNEL STAGES (arithmetic, not a cause): {_label(r.funnel[-1].metric, lineage)} is the product of the steps "
            f"of the funnel; each stage as a share of {base}, and in brackets the share of the stage before it that "
            "reached it: "
            + "; ".join(
                f"{_label(t.metric, lineage)} {_val(t.reference, t.metric, lineage, fmt)} → {_val(t.event, t.metric, lineage, fmt)}"
                + ("" if i == 0 else f" (step {t.step_reference:.1%} → {t.step_event:.1%}, {_chg(t.step_event, t.step_reference)})")
                for i, t in enumerate(r.funnel)
            )
            + f". The largest move was at {_label(lead_f.metric, lineage).lower()}"
            + (f" (about {_share(lead_f.share_of_change)} of the {_label(r.funnel[-1].metric, lineage).lower()} change)."
               if not _offsetting([t.share_of_change for t in r.funnel]) else ".")
        )
    period = e.reference_kind == _PERIOD_KIND
    where = next((d for d in r.chain_dimensions if _covers_whole(d, cfg=cfg) and (d.localised or d.simpsons_paradox)), None)
    where_metric = r.chain[0].metric if where is not None and r.chain else None
    own = next((d for d in r.dimensions if d.kind == "additive" and _covers_whole(d, cfg=cfg) and d.localised and d.top), None)
    if own is not None and not period:
        # An additive outcome answers "where" with its own split (which campaign took the spend); a rate beside it is
        # read after (live 2026-10-29: Meta spend was explained by the product-view rate of its landing pages).
        where, where_metric = own, r.outcome
    if period:
        where = next((d for d in r.dimensions if _covers_whole(d, cfg=cfg) and d.top and d.n_segments >= 2), None)
        where_metric = r.outcome if where is not None else None
    if where is None:
        where = next((d for d in r.dimensions if _covers_whole(d, cfg=cfg) and (d.localised or d.simpsons_paradox)), None)
        where_metric = r.outcome if where is not None else None
    if where is not None and where.top and where_metric is not None:
        s0 = where.top[0]
        if where.kind == "rate":
            txt = f"WHERE: by {where.dimension.replace('_', ' ')}, {_rate_segment_text(s0, where_metric, lineage, fmt=fmt)}."
            if where.simpsons_paradox:
                txt += (
                    " The overall rate moved because the mix shifted, while rates within each group moved "
                    "the other way (Simpson's paradox)."
                )
            elif abs(where.mix_effect or 0.0) > abs(where.rate_effect or 0.0):
                # mix + rate = the overall move: both are stated, so "rates held" is never claimed when they did not
                txt += (
                    f" The overall rate moved mainly because the mix shifted ({_effect(where.mix_effect or 0.0, where_metric, lineage, fmt=fmt)}); "
                    f"rates within the groups moved it {_effect(where.rate_effect or 0.0, where_metric, lineage, fmt=fmt)}."
                )
            also = [
                d for d in (*r.chain_dimensions, *r.dimensions)
                if d is not where and d.kind == "rate" and d.top and _covers_whole(d, cfg=cfg) and (d.localised or d.simpsons_paradox)
            ][:2]
            if also:
                txt += " The same shift shows " + "; ".join(
                    f"by {d.dimension.replace('_', ' ')}: {_rate_segment_text(d.top[0], where_metric, lineage, fmt=fmt)}" for d in also
                ) + "."
        elif where.kind == "ratio" and where.numerator and where.denominator:
            txt = f"WHERE: by {where.dimension.replace('_', ' ')}, " + "; ".join(
                _ratio_segment_text(s, where, where_metric, lineage, fmt=fmt)
                for s in where.top[:3] if period or _sign(s.share_of_change or 0.0) > 0
            ) + (
                f" (shares are of the change versus {_reference_words(e)}; each is what the segment added to "
                f"{_label(where.numerator, lineage).lower()} beyond the usual return on what it added to "
                f"{_label(where.denominator, lineage).lower()})."
            )
            # A coarser level of the same split names the parent (campaign above an
            # ad); a period comparison walks one level down instead.
            also = [
                d for d in r.dimensions
                if d is not where and d.kind == "ratio" and d.top and _covers_whole(d, cfg=cfg)
                and ((d.n_segments > where.n_segments) if period else (d.n_segments < where.n_segments and d.localised))
            ][:1]
            if also:
                shown = also[0].top[:3] if period else also[0].top[:1]
                txt += f" By {also[0].dimension.replace('_', ' ')}: " + "; ".join(
                    _ratio_segment_text(x, also[0], where_metric, lineage, fmt=fmt) for x in shown
                ) + "."
        else:
            # a localised split names the few segments that carried it, not only the largest
            def _lead(d: DimensionFinding) -> str:
                top = d.top[0]
                same = [x for x in d.top if _sign(x.share_of_change or 0.0) == _sign(top.share_of_change or 0.0)][:3] if d.localised else [top]
                return "; ".join(
                    f"{x.segment} went from {_val(x.reference, where_metric, lineage, fmt)} to {_val(x.event, where_metric, lineage, fmt)}, "
                    f"about {_share(x.share_of_change)} of the change"
                    for x in same
                )

            txt = (
                f"WHERE: by {where.dimension.replace('_', ' ')}, {_lead(where)}"
                f" versus {_reference_words(e)} (not the day before — never apply this share to the day-before change)."
            )
            # One level finer, from the same data: what inside it carried the change (a different split, not an id of the same one).
            finer = next((
                d for d in r.dimensions
                if d is not where and d.kind == "additive" and d.localised and d.top and _covers_whole(d, cfg=cfg)
                and d.n_segments > where.n_segments and (d.top[0].event, d.top[0].reference) != (s0.event, s0.reference)
            ), None)
            if finer is not None and where.localised:
                txt += f" By {finer.dimension.replace('_', ' ')}: {_lead(finer)}."
        out.append(txt)
    elif (full := [d for d in r.dimensions if _covers_whole(d, cfg=cfg)][:3]) and all(d.broad_based for d in full):
        out.append(
            "WHERE: the change was broad-based — every "
            + ", ".join(d.dimension.replace("_", " ") for d in full)
            + " moved roughly in proportion, so no single segment explains it."
        )
    # Segments far outside their own normal range are worth naming even when they are not the largest share
    # (live 2026-10-09 MS3-dce7d3104e: exchange and unattributed rows at z −6.2 / −5.3 went unmentioned).
    said = " ".join(out)
    unusual = sorted(
        (
            (abs(sm.z_score or 0.0), d, sm)
            for d in (*r.chain_dimensions, *r.dimensions) if _covers_whole(d, cfg=cfg)
            for sm in d.top if sm.significant and sm.z_score is not None
        ),
        key=lambda x: -x[0],
    )
    named: list[str] = []
    for _, d, sm in unusual:
        if len(named) >= 3 or sm.segment in said or any(sm.segment in n for n in named):
            continue
        if sm.rate_reference is not None and sm.rate_event is not None:
            move = f"{_val(sm.rate_reference, r.outcome, lineage, fmt)} → {_val(sm.rate_event, r.outcome, lineage, fmt)}"
        else:
            move = f"{_val(sm.reference, r.outcome, lineage, fmt)} → {_val(sm.event, r.outcome, lineage, fmt)}"
        named.append(f"{d.dimension.replace('_', ' ')} {sm.segment}: {move}")
    if named:
        out.append(
            "ALSO OUTSIDE ITS OWN NORMAL RANGE (places to look, not causes): " + "; ".join(named) + "."
        )
    if len(r.previous_values) > 1:
        prev_label = r.event.previous_period.get("days", ["the day before"]) if r.event else ["the day before"]
        span = prev_label[0] if len(prev_label) == 1 else f"{prev_label[0]}..{prev_label[-1]}"
        out.append(
            f"{'PREVIOUS DAY' if len(prev_label) == 1 else 'PREVIOUS PERIOD, the days just before the window'} ({span}), "
            "if a comparison table needs it (these totals, not the usual level, are the previous period): "
            + "; ".join(f"{_label(m, lineage)} {_val(v, m, lineage, fmt)}" for m, v in r.previous_values.items())
            + "."
        )
        if r.event_values:
            out.append(
                f"{'EVENT DAY' if len(r.event_window) == 1 else 'EVENT WINDOW'} "
                f"({r.event_window[0] if len(r.event_window) == 1 else f'{r.event_window[0]}..{r.event_window[-1]}'}), "
                "the same metrics: "
                + "; ".join(f"{_label(m, lineage)} {_val(v, m, lineage, fmt)}" for m, v in r.event_values.items())
                + "."
            )
    causes = [f for f in r.drivers if f.status == "implicated" and f.classification in ("supported_cause", "likely_contributor")]
    assoc = [f for f in r.drivers if f.status == "implicated" and f.classification == "correlation"]
    for m in (f for f in r.measurement if f.status == "diverged" and cfg.measurement_acts):
        out.append(
            f"MEASUREMENT: {_label(m.metric, lineage)} and {_label(m.peer, lineage)} count the same events in two systems and "
            f"they disagree on the event days ({m.reason}). Check how {_label(m.metric, lineage)} is recorded before looking for "
            "a business cause."
        )
    if causes:
        for f in causes:
            kind = "a cause" if f.classification == "supported_cause" else "a likely contributor"
            lo, hi = sorted(f.contribution_ci) if len(f.contribution_ci) == 2 else (None, None)
            out.append(
                f"WHY: {_label(f.driver, lineage)} is {kind}. It moved {_chg(f.driver_event, f.driver_reference)} "
                f"({_val(f.driver_reference, f.driver, lineage, fmt)} → {_val(f.driver_event, f.driver, lineage, fmt)}); from the "
                f"past {f.n_rows} days that is worth about {_val(f.contribution, r.outcome, lineage, fmt)} of {name.lower()} "
                f"(95% range {_val(lo, r.outcome, lineage, fmt)} to {_val(hi, r.outcome, lineage, fmt)}), roughly "
                f"{_share(f.share_of_change)} of the change. Basis: {f.reason}."
            )
    elif e.reference_kind == _PERIOD_KIND:
        out.append(
            "WHY: this compares two periods, so the answer is the parts above — which components and which "
            "segments account for the difference. Upstream causes are not estimated across whole periods; "
            "diagnosing a short window inside the period tests those."
        )
    else:
        out.append(
            "WHY: no upstream cause could be identified from the data — the figures above show what and where it "
            "changed, not why."
        )
    if assoc:
        out.append(
            "MOVED TOGETHER (not shown to be causes): "
            + ", ".join(_label(f.driver, lineage) for f in assoc)
            + " — the data cannot tell whether they drove the change or reacted to it."
        )
    groups = {  # (one, many)
        "did_not_move": ("did not change unusually, so it does not explain it", "did not change unusually, so they do not explain it"),
        "wrong_direction": ("moved the other way, so it would have pushed it the opposite direction",
                            "moved the other way, so they would have pushed it the opposite direction"),
        "no_effect": ("shows no effect on it once weekday, trend and the previous day are accounted for",
                      "show no effect on it once weekday, trend and the previous day are accounted for"),
        "path_did_not_move": ("would act on it through a metric that did not change unusually, so it does not explain it",
                              "would act on it through a metric that did not change unusually, so they do not explain it"),
    }
    parts = []
    for code, (one, many) in groups.items():
        names = [_label(f.driver, lineage) for f in r.drivers if f.status == "ruled_out" and f.ruled_out_because == code]
        if names:
            parts.append(", ".join(names) + " " + (one if len(names) == 1 else many))
    if parts:
        out.append("RULED OUT: " + "; ".join(parts) + ".")
    refuted = any(not rr.get("passed") for f in causes for rr in f.refutations)
    if e.reference_kind == _PERIOD_KIND:
        out.append(
            "CONFIDENCE: the period totals and their split are exact accounting from the data; "
            + {"strong": "the daily values of the two periods clearly differ",
               "moderate": "the daily values of the two periods differ somewhat",
               "none": "the daily values of the two periods overlap a lot"}[e.strength]
            + "."
        )
        return out
    out.append(
        "CONFIDENCE: "
        + {"strong": "the change itself is clearly real", "moderate": "the change is notable but within the range of rare normal days", "none": "the change is within normal variation"}[e.strength]
        + "; "
        + (
            "cause estimates control for day-of-week, trend and the previous day, and passed placebo checks"
            + (" (one robustness check was weaker)" if refuted else "")
            if causes else "no causal estimate met the bar for a cause"
        )
        + ". Key assumption: no unmeasured same-day factor moved both."
    )
    return out
