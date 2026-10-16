"""The report: verdict, competing hypotheses, the values the answer may cite, headline and narrative (plan P2.2).
Assembled from the stages' results in the order the engine always built it; nothing here computes new evidence."""

from __future__ import annotations

from seleric_swarm.diagnosis.domain.analysis import EventAnalysis, WhatResult, WhyResult
from seleric_swarm.diagnosis.domain.config import DiagnosisConfig
from seleric_swarm.diagnosis.domain.lineage import MetricMeta
from seleric_swarm.diagnosis.domain.numeric import _finite, _mean
from seleric_swarm.diagnosis.domain.report import DiagnosisReport, DimensionFinding, DriverFinding
from seleric_swarm.diagnosis.ports.formatting import ValueFormatting
from seleric_swarm.diagnosis.report.format import _display_lineage
from seleric_swarm.diagnosis.report.headline import _headline
from seleric_swarm.diagnosis.report.narrative import _narrative


def assemble_report(
    ana: EventAnalysis, what: WhatResult, dims: list[DimensionFinding], why: WhyResult, *, cfg: DiagnosisConfig, fmt: ValueFormatting
) -> DiagnosisReport:
    inp, y, event_days, baseline, delta = ana.inp, ana.y, ana.event_days, ana.baseline, ana.delta
    unusual, notable, prev_days = ana.unusual, ana.notable, ana.prev_days
    window_label, quality, implicated = ana.window_label, ana.quality, why.implicated
    report = DiagnosisReport(
        outcome=y, event_window=window_label, verdict="root_cause_not_identified", headline="",
        event=ana.event, identities=[i.describe() for i in ana.identities], data_quality=quality,
    )
    report.assumptions = [*ana.assumptions, *why.assumptions]
    report.decomposition, report.bridge, report.bridge_identity = what.decomposition, what.bridge, what.bridge_identity
    report.bridge_residual, report.bridge_residual_vs_previous = what.bridge_residual, what.bridge_residual_vs_previous
    report.chain_identity, report.chain, report.chain_dimensions = what.chain_identity, what.chain, what.chain_dimensions
    report.dimensions, report.drivers = dims, why.drivers
    # ---- competing hypotheses & verdict ---------------------------------------------
    for drv in report.drivers:
        if drv.status != "implicated" or drv.classification not in ("supported_cause", "likely_contributor"):
            report.competing_hypotheses.append({
                "hypothesis": f"{drv.driver} drove the change", "status": drv.status,
                "classification": drv.classification, "reason": drv.reason,
            })
    for dim_finding in dims:
        if dim_finding.broad_based:
            report.competing_hypotheses.append({
                "hypothesis": f"the change is specific to some {dim_finding.dimension} values",
                "status": "ruled_out",
                "reason": f"every {dim_finding.dimension} moved roughly in proportion (specificity {dim_finding.specificity:.2f})",
            })
    if implicated:
        # One estimate per inseparable cluster: collinear drivers carry the same signal.
        counted: list[DriverFinding] = []
        for drv in sorted(implicated, key=lambda drv: -abs(drv.contribution or 0.0)):
            if not any(drv.driver in g.inseparable_from for g in counted):
                counted.append(drv)
        explained = sum(drv.contribution or 0.0 for drv in counted)
        report.unexplained_share = 1.0 - explained / delta if abs(delta) > 1e-12 else None
        if len(implicated) > 1:
            report.assumptions.append(
                "Driver contributions are estimated one driver at a time; when drivers are linked they overlap "
                "and should not be summed."
            )
    # A period comparison asks what made the two totals differ: the accounting
    # below answers that even when the daily values overlap.
    if not (unusual or notable) and not baseline:
        report.verdict = "no_unusual_change"
    elif any(drv.classification == "supported_cause" for drv in implicated):
        report.verdict = "explained" if (report.unexplained_share is not None and abs(report.unexplained_share) < 0.5) else "partially_explained"
    elif implicated:
        report.verdict = "partially_explained"
    elif report.decomposition or report.bridge or report.chain or any(d.localised or d.simpsons_paradox for d in (*dims, *report.chain_dimensions)):
        report.verdict = "located_cause_not_identified"
    else:
        report.verdict = "root_cause_not_identified"
    cited = [y, *(t.metric for t in report.decomposition), *(t.metric for t in report.bridge), *(t.metric for t in report.chain),
             *(drv.driver for drv in report.drivers if drv.status in ("implicated", "ruled_out"))]
    for cm in dict.fromkeys(cited):
        add = inp.lineage.get(cm, MetricMeta(cm)).additive
        for days, into in ((prev_days, report.previous_values), (event_days, report.event_values)):
            vals = [inp.series.get(cm, {}).get(d) for d in days]
            finite_vals = [v for v in vals if _finite(v)]
            if len(finite_vals) == len(vals):
                into[cm] = sum(finite_vals) if add else _mean(finite_vals)
    report.headline = _headline(report, cfg=cfg)
    report.narrative = _narrative(report, _display_lineage(inp.lineage, inp.series), cfg=cfg, fmt=fmt)
    return report
