"""Prior scoring for hypotheses (deterministic).

    prior = w_evidence * evidence_overlap
          + w_incident * incident_match
          + w_temporal * temporal_alignment
          + w_mechanism * mechanism_specificity

All four components are in [0, 1]. The result orders which hypotheses get a
causal estimate first; it is not a probability.
"""

from __future__ import annotations

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis


def rank_hypotheses(ctx: DiagnosticContext, hypotheses: list[DiagnosticHypothesis]) -> list[DiagnosticHypothesis]:
    w = ctx.policies.prior_weights()
    domain = (ctx.request.lead_domain or "").removesuffix("_agent") or None
    keywords = [t for t in ctx.request.question.lower().replace(",", " ").split() if len(t) > 3][:12]
    incidents = ctx.deps.incident_registry.match(domain=domain, keywords=keywords)
    incident_text = " ".join(f"{p.trigger} {p.typical_mechanism}".lower() for p in incidents)

    anomaly_metrics = {a.metric_id for a in ctx.anomalies}

    for h in hypotheses:
        evidence_overlap = min(1.0, len(h.supporting_evidence) / 3.0)
        # a hypothesis whose treatment metric itself moved anomalously scores higher
        if h.treatment_metric in anomaly_metrics:
            evidence_overlap = max(evidence_overlap, 0.6)

        incident_match = 0.0
        if incident_text:
            hit = sum(1 for tok in h.statement.lower().split() if len(tok) > 4 and tok in incident_text)
            incident_match = min(1.0, hit / 4.0)

        temporal_alignment = 0.0
        if ctx.degradation_started_at and h.treatment_metric:
            # any evidence/event for the treatment before the degradation start
            t_times = [
                str(e.get("value"))
                for e in ctx.evidence
                if (e.get("metric_id") or e.get("metric_or_fact")) in _treatment_events(h)
                and e.get("value")
            ]
            if any(t <= ctx.degradation_started_at for t in t_times):
                temporal_alignment = 1.0
            elif t_times:
                temporal_alignment = 0.3

        mechanism_specificity = 0.2
        if h.mechanism and len(h.mechanism.split()) >= 5:
            mechanism_specificity = 0.7
        if h.treatment_metric:
            mechanism_specificity = min(1.0, mechanism_specificity + 0.3)

        h.prior_score = round(
            w.get("evidence_overlap", 0.4) * evidence_overlap
            + w.get("incident_match", 0.3) * incident_match
            + w.get("temporal_alignment", 0.2) * temporal_alignment
            + w.get("mechanism_specificity", 0.1) * mechanism_specificity,
            4,
        )

    ordered = sorted(hypotheses, key=lambda h: (-h.prior_score, h.statement))
    if ordered:
        ordered[0].is_primary = True
    return ordered


def _treatment_events(h: DiagnosticHypothesis) -> set[str]:
    # crude mapping treatment metric -> the event fact that would precede it
    mapping = {
        "metric.mobile_lcp_seconds": {"event.frontend_deployment"},
        "metric.js_error_rate": {"event.frontend_deployment", "event.tag_change"},
        "metric.avg_price": {"event.price_change"},
        "metric.attributed_orders": {"event.attribution_change", "event.tag_change"},
    }
    return mapping.get(h.treatment_metric, set())
