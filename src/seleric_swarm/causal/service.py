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
from seleric_swarm.causal.dowhy_service import DoWhyService, estimate_time_series_effect
from seleric_swarm.toolsets import policy_config as P
from seleric_swarm.toolsets.policy_config import (
    BASE_CAUSAL_CANDIDATE_CAP,
    DEFAULT_CAUSAL_ESTIMATOR,
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


def _series_by_metric(
    evidence: list[EvidenceArtifact], *, window_start: datetime, as_of: datetime, warnings: list[str],
) -> dict[str, dict[Any, float]]:
    """One daily series per metric, never mixing dimension slices.

    Evidence for a metric can arrive as a total and as per-segment rows (a
    drilldown); writing both into one ``{day: value}`` map let whichever row
    came last overwrite the total. Per metric, keep the slice with the fewest
    dimension constraints (the total, or the one filtered scope) and say when
    others were set aside.
    """
    slices: dict[str, dict[tuple[tuple[str, str], ...], dict[Any, float]]] = {}
    for e in evidence:
        if e.value is None or e.grain != "day":
            continue
        if e.period_end < window_start or e.period_start > as_of:
            continue
        key = tuple(sorted((e.dimensions or {}).items()))
        slices.setdefault(e.metric_id, {}).setdefault(key, {})[e.period_start.date()] = float(e.value)
    out: dict[str, dict[Any, float]] = {}
    for metric, by_slice in slices.items():
        best = min(by_slice, key=lambda k: (len(k), -len(by_slice[k])))
        out[metric] = by_slice[best]
        if len(by_slice) > 1:
            warnings.append(
                f"{metric}: used the {'total' if not best else dict(best)} series; "
                f"{len(by_slice) - 1} other dimension slice(s) set aside"
            )
    return out


def build_observation_frame(
    evidence: list[EvidenceArtifact],
    *,
    treatment: str,
    outcome: str,
    history_days: int,
    candidate_cap: int,
    as_of: datetime | None = None,
) -> tuple[dict[str, dict[Any, float]] | None, list[str], list[str]]:
    """Daily series for the treatment, the outcome and up to ``candidate_cap - 1``
    competing candidates (which enter the DAG only as lagged pre-treatment terms).

    Returns ``(series_by_metric, competing_candidates, warnings)``; ``None`` when
    the treatment or outcome series is absent.
    """
    warnings: list[str] = []
    if as_of is None:
        as_of = max((e.period_end for e in evidence), default=datetime.now().astimezone())
    window_start = as_of - timedelta(days=history_days)
    by_metric = _series_by_metric(evidence, window_start=window_start, as_of=as_of, warnings=warnings)
    if treatment not in by_metric or outcome not in by_metric:
        return None, [], warnings
    competing = [m for m in by_metric if m not in {treatment, outcome}]
    if len(competing) > max(0, candidate_cap - 1):
        warnings.append(f"candidate_cap={candidate_cap} truncated competing candidates to {max(0, candidate_cap - 1)}")
    competing = competing[: max(0, candidate_cap - 1)]
    return by_metric, competing, warnings


def classify_from_refutations(
    *,
    effect: float | None,
    refutations: list[dict[str, Any]],
    common_causes: list[str],
    min_refutations: int = MIN_REFUTATIONS,
    confidence_interval: list[float] | None = None,
    direction: str | None = None,
) -> EvidenceClassification:
    """Map an estimate onto the frozen V3 classification vocabulary.

    ``CAUSALLY_SUPPORTED`` needs all of: an effect whose interval excludes 0,
    enough refuters run and none contradicting, and direction evidence beyond a
    same-day association (``temporal`` lead/lag or ``intervention`` shifts).
    Refuters passing on an unoriented same-day association is correlation that
    survived robustness checks — still ``ASSOCIATION``.
    """
    _ = common_causes  # retained for call-site compatibility
    if effect is None:
        return "OBSERVATION"
    if not refutations:
        return "ASSOCIATION"
    errored = sum(1 for r in refutations if "error" in r)
    contradicted = sum(1 for r in refutations if not r.get("passed") and "error" not in r)
    completed = len(refutations) - errored
    if contradicted > 0 or completed < min_refutations:
        return "ASSOCIATION"
    ci = confidence_interval or []
    if len(ci) == 2 and not (ci[0] > 0 or ci[1] < 0):
        return "ASSOCIATION"
    if direction not in ("temporal", "intervention"):
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
    """Run the time-series DAG estimator on evidence-built series. Does not fetch."""
    del dowhy  # legacy injection point; the DAG estimator is a pure function
    widen = widening_for(search_breadth)
    series, competing, warnings = build_observation_frame(
        evidence,
        treatment=treatment,
        outcome=outcome,
        history_days=widen.history_days,
        candidate_cap=widen.candidate_cap,
        as_of=as_of,
    )
    query: dict[str, Any] = {
        "treatment": treatment,
        "outcome": outcome,
        "method": method,
        "search_breadth": widen.search_breadth,
        "history_days": widen.history_days,
        "candidate_cap": widen.candidate_cap,
        "common_causes": [],
    }
    if series is None:
        return EstimateOutcome(
            evidence_classification="ASSOCIATION", effect_estimate=None, refutation_checks=[], method=method,
            query=query, warnings=[*warnings, f"observation rows=0 below min={MIN_OBSERVATION_ROWS}"],
            common_causes=[], n_rows=0,
        )
    days = sorted(set(series[treatment]) | set(series[outcome]))
    if method != DEFAULT_CAUSAL_ESTIMATOR:
        warnings.append(f"method {method!r} replaced by {DEFAULT_CAUSAL_ESTIMATOR} on the time-series DAG")
    est = estimate_time_series_effect(
        outcome=series[outcome], treatment=series[treatment],
        others={m: series[m] for m in competing}, days=days,
        treatment_name=treatment, outcome_name=outcome,
        alpha=P.DIAG_ALPHA, refuter_tolerance=P.DIAG_REFUTER_TOLERANCE,
        min_rows_per_covariate=P.DIAG_MIN_ROWS_PER_COVARIATE, min_rows=MIN_OBSERVATION_ROWS,
        placebo_shifts=P.DIAG_PLACEBO_SHIFTS,
    )
    query.update({
        "common_causes": list(est.adjustment_set),
        "adjustment_set": list(est.adjustment_set),
        "graph_edges": [list(e) for e in est.graph_edges],
        "identified_estimand": est.identified_estimand,
        "confidence_interval": list(est.confidence_interval),
        "uncertainty_method": est.uncertainty_method,
        "direction_evidence": est.temporal,
        "naive_association": est.naive_association,
    })
    if est.status != "estimated" or est.effect is None:
        return EstimateOutcome(
            evidence_classification="OBSERVATION" if est.n_rows else "ASSOCIATION",
            effect_estimate=None, refutation_checks=[], method=DEFAULT_CAUSAL_ESTIMATOR, query=query,
            warnings=[*warnings, est.reason], common_causes=list(est.adjustment_set), n_rows=est.n_rows,
        )
    checks = [dict(r) for r in est.refutations]
    classification = classify_from_refutations(
        effect=est.effect, refutations=checks, common_causes=list(est.adjustment_set),
        confidence_interval=list(est.confidence_interval), direction=est.temporal.get("direction"),
    )
    if classification != "CAUSALLY_SUPPORTED" and est.temporal.get("direction") not in ("temporal", "intervention"):
        warnings.append("same-day association: direction not identified, so this is not evidence of causation")
    return EstimateOutcome(
        evidence_classification=classification,
        effect_estimate=float(est.effect),
        refutation_checks=checks,
        method=DEFAULT_CAUSAL_ESTIMATOR,
        query=query,
        warnings=warnings,
        common_causes=list(est.adjustment_set),
        n_rows=est.n_rows,
    )
