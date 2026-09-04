"""Metric semantic consistency validator (spec sec. 20).

Detects when agents use a similarly-named metric with a *different definition*.
Such a disagreement is classified ``METRIC_SEMANTIC_CONFLICT`` -- never a factual
contradiction -- and produces a reconciliation follow-up rather than a REJECT.
"""

from __future__ import annotations

from itertools import combinations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, followup

_SEMANTIC_KEYS = (
    "calculation_version",
    "source_version",
    "attribution_basis",
    "unit",
    "timezone",
    "gross_or_net",
    "returns_treatment",
    "grain",
    "cohort_definition",
)


class MetricValidator(Validator):
    name = "metric"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        rows = ctx.all_evidence()
        by_metric: dict[str, list] = {}
        for ev in rows:
            if ev.metric_id:
                by_metric.setdefault(ev.metric_id, []).append(ev)

        conflicts: list[dict] = []
        for metric_id, group in by_metric.items():
            reg = ctx.deps.metric_registry.get(metric_id)
            for a, b in combinations(group, 2):
                diff = _semantic_diff(a, b, reg)
                if diff:
                    conflicts.append({"metric_id": metric_id, "rows": [a.evidence_id, b.evidence_id], "differs_on": diff})

        # explicit competing definitions carried on the claim
        for defn in ctx.claim.metadata.get("competing_definitions", []) or []:
            conflicts.append({"metric_id": defn.get("metric_id"), "rows": defn.get("refs", []), "differs_on": ["declared_formula"]})

        if conflicts:
            out.status = "WEAK"
            for c in conflicts:
                out.challenges.append(
                    challenge(
                        "metric",
                        "warning",
                        (
                            f"Metric '{c['metric_id']}' appears with inconsistent definitions "
                            f"(differs on {c['differs_on']}). This is a METRIC_SEMANTIC_CONFLICT, "
                            f"not a factual contradiction."
                        ),
                        evidence_refs=c["rows"],
                        detail={"contradiction_type": "metric_semantic_conflict", **c},
                        remediation_hint="Reconcile to one registered metric definition/version before comparing values.",
                    )
                )
                out.methodological_issues.append(
                    f"Unreconciled metric-semantic conflict on {c['metric_id']} ({c['differs_on']})."
                )
                out.followups.append(
                    followup(
                        "metric_definition_reconciliation",
                        f"Reconcile competing definitions of {c['metric_id']}.",
                        f"Which registered definition/version of {c['metric_id']} applies here, and do the "
                        f"values agree once aligned?",
                        evidence_refs=c["rows"],
                        priority=7,
                    )
                )
            out.detail = {"conflicts": conflicts, "contradiction_type": "metric_semantic_conflict"}
            out.score_signals["metric_validity"] = 0.4
        else:
            out.score_signals["metric_validity"] = 0.9 if by_metric else 0.6
        return out


def _semantic_diff(a, b, reg) -> list[str]:
    differs: list[str] = []
    a_meta = {**(a.dimensions or {}), "calculation_version": a.calculation_version, "source_version": a.source_version, "unit": a.unit, "timezone": a.timezone}
    b_meta = {**(b.dimensions or {}), "calculation_version": b.calculation_version, "source_version": b.source_version, "unit": b.unit, "timezone": b.timezone}
    for key in _SEMANTIC_KEYS:
        av, bv = a_meta.get(key), b_meta.get(key)
        if av is not None and bv is not None and av != bv:
            differs.append(key)
    # different origin source AND at least one semantic descriptor present but unequal
    return differs
