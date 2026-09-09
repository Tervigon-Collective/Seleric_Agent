"""Prior scoring for hypotheses (deterministic).

    prior = w_evidence * evidence_overlap
          + w_incident * incident_match
          + w_temporal * temporal_alignment
          + w_mechanism * mechanism_specificity

All four components are in [0, 1]. The result orders which hypotheses get a
causal estimate first; it is not a probability.
"""

from __future__ import annotations

from datetime import UTC, datetime

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import DiagnosticHypothesis
from seleric_swarm.agents.diagnostic.ontology import treatment_events as _ontology_treatment_events


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
        neighbors = set(ctx.scratch.get("semantic_neighbors") or [])
        bare = (h.treatment_metric or "").removeprefix("metric.")
        if h.treatment_metric in neighbors or (bare and bare in neighbors):
            evidence_overlap = max(evidence_overlap, 0.5)

        incident_match = 0.0
        if incident_text:
            hit = sum(1 for tok in h.statement.lower().split() if len(tok) > 4 and tok in incident_text)
            incident_match = min(1.0, hit / 4.0)

        temporal_alignment = 0.0
        deg_dt = _parse(ctx.degradation_started_at) if ctx.degradation_started_at else None
        if deg_dt and h.treatment_metric:
            # any event fact for the treatment that precedes the degradation start.
            # Compare parsed datetimes, not raw strings — "Z" vs "+00:00" and
            # differing UTC offsets do not sort lexically.
            t_times = [
                _parse(str(e.get("value")))
                for e in ctx.evidence
                if (e.get("metric_id") or e.get("metric_or_fact")) in _treatment_events(h)
                and e.get("value")
            ]
            t_times = [t for t in t_times if t is not None]
            if any(t <= deg_dt for t in t_times):
                temporal_alignment = 1.0
            elif t_times:
                temporal_alignment = 0.3

        spec_scores = ctx.policies.mechanism_specificity_scores()
        mechanism_specificity = spec_scores["base"]
        if h.mechanism and len(h.mechanism.split()) >= 5:
            mechanism_specificity = spec_scores["detailed_mechanism"]
        if h.treatment_metric:
            mechanism_specificity = min(1.0, mechanism_specificity + spec_scores["has_treatment_metric_bonus"])

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
    return set(_ontology_treatment_events(h.treatment_metric))


def _parse(value: str) -> datetime | None:
    v = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
