"""Causal estimation service — DoWhy extract-wrap for the V3 CausalToolset.

Rules 4+5: consumes already-fetched ``EvidenceArtifact``s; never fetches and
never calls another tool. ``search_breadth`` (A1.1) replaces hidden
``remediation_round`` widening:

    history_days = HISTORY_DAYS_BASE * (1 + search_breadth)
    candidate_cap = BASE_CAUSAL_CANDIDATE_CAP + search_breadth
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.causal.dowhy_service import CausalRequest, DoWhyService, DoWhyUnavailable
from seleric_swarm.toolsets.policy_config import (
    BASE_CAUSAL_CANDIDATE_CAP,
    DEFAULT_CAUSAL_ESTIMATOR,
    DEFAULT_REFUTERS,
    HISTORY_DAYS_BASE,
    MIN_OBSERVATION_ROWS,
    MIN_REFUTATIONS,
)

SearchBreadth = Literal[0, 1, 2]

EvidenceClassification = Literal[
    "OBSERVATION",
    "ASSOCIATION",
    "HYPOTHESIS",
    "CAUSALLY_SUPPORTED",
    "EXPERIMENTALLY_VALIDATED",
]


@dataclass(frozen=True)
class Widening:
    history_days: int
    candidate_cap: int
    search_breadth: int


@dataclass
class EstimateOutcome:
    evidence_classification: EvidenceClassification
    effect_estimate: float | None
    refutation_checks: list[dict[str, Any]]
    method: str
    query: dict[str, Any]
    warnings: list[str]
    common_causes: list[str]
    n_rows: int


def widening_for(
    search_breadth: int,
    *,
    base_cap: int = BASE_CAUSAL_CANDIDATE_CAP,
    history_base: int = HISTORY_DAYS_BASE,
) -> Widening:
    """Map ``search_breadth`` to history/candidate caps (bug #6 arithmetic)."""
    b = int(search_breadth)
    if b not in (0, 1, 2):
        raise ValueError(f"search_breadth must be 0, 1, or 2; got {b}")
    return Widening(
        history_days=history_base * (1 + b),
        candidate_cap=base_cap + b,
        search_breadth=b,
    )


def build_observation_frame(
    evidence: list[EvidenceArtifact],
    *,
    treatment: str,
    outcome: str,
    history_days: int,
    candidate_cap: int,
    as_of: datetime | None = None,
) -> tuple[Any, list[str], list[str]]:
    """Pivot evidence into a DoWhy observation frame.

    Returns ``(dataframe, common_causes, warnings)``. Common causes are other
    metric series present in the evidence, capped by ``candidate_cap``
    (treatment itself is excluded from the cap count of confounders; the cap
    bounds how many non-outcome metrics participate as treatment+confounders
    when discovering, and here bounds confounders taken alongside the named
    treatment).
    """
    import pandas as pd

    warnings: list[str] = []
    if as_of is None:
        as_of = max((e.period_end for e in evidence), default=datetime.now().astimezone())
    window_start = as_of - timedelta(days=history_days)

    by_metric: dict[str, dict[datetime, float]] = {}
    for e in evidence:
        if e.value is None:
            continue
        if e.period_end < window_start or e.period_start > as_of:
            continue
        day = e.period_start.replace(hour=0, minute=0, second=0, microsecond=0)
        by_metric.setdefault(e.metric_id, {})[day] = float(e.value)

    if treatment not in by_metric or outcome not in by_metric:
        return None, [], warnings

    confounder_candidates = [
        m for m in by_metric if m not in {treatment, outcome}
    ][: max(0, candidate_cap - 1)]
    # candidate_cap bounds treatment + confounders considered in a discovery
    # pass; with a named treatment we still honour the same cap on how many
    # extra series ride along.
    if len(by_metric) - 1 > candidate_cap:
        warnings.append(
            f"candidate_cap={candidate_cap} truncated confounders to {len(confounder_candidates)}"
        )

    columns = [treatment, outcome, *confounder_candidates]
    all_days = sorted({d for m in columns for d in by_metric[m]})
    rows: list[dict[str, float]] = []
    for day in all_days:
        if any(day not in by_metric[m] for m in columns):
            continue
        rows.append({m: by_metric[m][day] for m in columns})

    if not rows:
        return None, confounder_candidates, warnings

    return pd.DataFrame(rows), confounder_candidates, warnings


def classify_from_refutations(
    *,
    effect: float | None,
    refutations: list[dict[str, Any]],
    common_causes: list[str],
    min_refutations: int = MIN_REFUTATIONS,
) -> EvidenceClassification:
    """Map DoWhy refuter outcomes onto the frozen V3 classification vocabulary."""
    _ = common_causes  # retained for call-site compatibility / future STRONG tier
    if effect is None:
        return "OBSERVATION"
    if not refutations:
        return "ASSOCIATION"

    errored = sum(1 for r in refutations if "error" in r)
    contradicted = sum(1 for r in refutations if not r.get("passed") and "error" not in r)
    completed = len(refutations) - errored
    if contradicted > 0 or completed < min_refutations:
        return "ASSOCIATION"
    return "CAUSALLY_SUPPORTED"


def estimate_from_evidence(
    evidence: list[EvidenceArtifact],
    *,
    treatment: str,
    outcome: str,
    method: str = DEFAULT_CAUSAL_ESTIMATOR,
    search_breadth: SearchBreadth = 0,
    as_of: datetime | None = None,
    dowhy: DoWhyService | None = None,
) -> EstimateOutcome:
    """Run DoWhy on an evidence-built frame. Does not fetch."""
    widen = widening_for(search_breadth)
    frame, common_causes, warnings = build_observation_frame(
        evidence,
        treatment=treatment,
        outcome=outcome,
        history_days=widen.history_days,
        candidate_cap=widen.candidate_cap,
        as_of=as_of,
    )
    query = {
        "treatment": treatment,
        "outcome": outcome,
        "method": method,
        "search_breadth": widen.search_breadth,
        "history_days": widen.history_days,
        "candidate_cap": widen.candidate_cap,
        "common_causes": list(common_causes),
    }

    if frame is None or len(frame) < MIN_OBSERVATION_ROWS:
        n = 0 if frame is None else len(frame)
        return EstimateOutcome(
            evidence_classification="ASSOCIATION",
            effect_estimate=None,
            refutation_checks=[],
            method=method,
            query=query,
            warnings=[*warnings, f"observation rows={n} below min={MIN_OBSERVATION_ROWS}"],
            common_causes=common_causes,
            n_rows=n,
        )

    service = dowhy or DoWhyService()
    try:
        est = service.estimate(
            CausalRequest(
                treatment=treatment,
                outcome=outcome,
                common_causes=list(common_causes),
                estimator=method,
                refuters=list(DEFAULT_REFUTERS),
            ),
            frame,
        )
    except DoWhyUnavailable as exc:
        return EstimateOutcome(
            evidence_classification="ASSOCIATION",
            effect_estimate=None,
            refutation_checks=[],
            method=method,
            query=query,
            warnings=[*warnings, f"DoWhy unavailable: {exc}"],
            common_causes=common_causes,
            n_rows=len(frame),
        )

    ref_checks = [
        {
            "name": r.name,
            "passed": r.passed,
            "estimated_effect": r.estimated_effect,
            "new_effect": r.new_effect,
            **r.detail,
        }
        for r in est.refutations
    ]
    classification = classify_from_refutations(
        effect=est.effect,
        refutations=ref_checks,
        common_causes=list(est.common_causes),
    )
    if classification == "CAUSALLY_SUPPORTED" and not ref_checks:
        # Invariant of CausalArtifact — never emit the tier without checks.
        classification = "ASSOCIATION"
        warnings.append("CAUSALLY_SUPPORTED demoted: empty refutation_checks")

    query["common_causes"] = list(est.common_causes)
    if est.dropped_collinear_common_causes:
        warnings.append(
            f"dropped collinear common causes: {est.dropped_collinear_common_causes}"
        )
    return EstimateOutcome(
        evidence_classification=classification,
        effect_estimate=float(est.effect),
        refutation_checks=ref_checks,
        method=est.estimator,
        query=query,
        warnings=warnings,
        common_causes=list(est.common_causes),
        n_rows=est.n_rows,
    )
