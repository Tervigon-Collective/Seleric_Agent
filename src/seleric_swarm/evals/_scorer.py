"""Scoring for the eval suite (M0.4).

Scoring is pure: it takes a case, the observed run, and the resolved truth
values, and returns numbers. It never runs anything and never reads the
catalogue. That is what makes a scorer regression explainable — the only
inputs are two typed records.

Metrics per case (a metric is ``None`` when the case does not ask about it, so
a missing expectation is never scored as a pass):

- ``spec_accuracy``  field-level fraction of the expected partial spec matched.
- ``status_ok``      1.0 when the observed status equals the expected one.
- ``value_correctness`` fraction of expected values within tolerance.
- ``revisions``      revision-loop count (lower is better; reported, not scored).
- ``latency_ms``     wall-clock of the final turn (lower is better).
- ``llm_calls``      LLM calls for the final turn (lower is better).
- ``tokens``         total tokens for the final turn (lower is better).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from seleric_swarm.evals._case import EvalCase
    from seleric_swarm.evals._runner import ObservedCase

HIGHER_IS_BETTER = ("spec_accuracy", "status_ok", "value_correctness")
LOWER_IS_BETTER = ("revisions", "latency_ms", "llm_calls", "tokens")
SCORE_METRICS = HIGHER_IS_BETTER + LOWER_IS_BETTER


@dataclass(frozen=True)
class ResolvedTruth:
    """One expected value, with its catalogue match key resolved."""

    label: str
    expected: float | None
    tolerance: float
    match: dict[str, Any]


class CaseScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tags: tuple[str, ...]
    metrics: dict[str, float | None]
    details: dict[str, Any] = Field(default_factory=dict)


class SuiteScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    overall: dict[str, float | None]
    by_tag: dict[str, dict[str, float | None]]
    cases: tuple[CaseScore, ...]


def _leaf_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_leaf_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_leaf_count(item) for item in value)
    return 1


def spec_matches(expected: Any, actual: Any) -> tuple[int, int]:
    """Count (matched, total) leaf expectations of a partial spec.

    A dict expectation is a recursive subset; a list must match element-wise
    and in order (specs are ordered tuples of measures/filters/windows).
    """

    if isinstance(expected, dict):
        matched = total = 0
        for key, value in expected.items():
            if not isinstance(actual, dict) or key not in actual:
                total += _leaf_count(value)
                continue
            sub_matched, sub_total = spec_matches(value, actual[key])
            matched += sub_matched
            total += sub_total
        return matched, total
    if isinstance(expected, list):
        matched = total = 0
        for index, value in enumerate(expected):
            if not isinstance(actual, list) or index >= len(actual):
                total += _leaf_count(value)
                continue
            sub_matched, sub_total = spec_matches(value, actual[index])
            matched += sub_matched
            total += sub_total
        return matched, total
    return (1 if expected == actual else 0), 1


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
    return None


def _bare(metric_id: Any) -> str:
    """Legacy evidence names metrics ``metric.<id>`` or ``<view>.<id>``; compare the bare id."""
    return str(metric_id or "").rsplit(".", 1)[-1]


def _row_matches(row: Any, match: dict[str, Any]) -> bool:
    metric = match.get("metric_id", match.get("metric"))
    if metric is not None and _bare(getattr(row, "metric_id", None)) != _bare(metric):
        return False
    wanted_dims = match.get("dimensions") or match.get("dimension_values") or {}
    row_dims = getattr(row, "dimensions", {}) or {}
    return all(row_dims.get(k) == v for k, v in wanted_dims.items())


def value_correctness(observed: ObservedCase, truths: list[ResolvedTruth]) -> float | None:
    scored = [truth for truth in truths if truth.expected is not None]
    if not scored:
        return None
    correct = 0
    for truth in scored:
        expected = truth.expected
        if expected is None:
            continue
        candidates = [row for row in observed.evidence if _row_matches(row, truth.match)]
        for row in candidates:
            value = _numeric(row.value)
            if value is not None and abs(value - expected) <= truth.tolerance:
                correct += 1
                break
    return correct / len(scored)


def score_case(
    case: EvalCase, observed: ObservedCase, truths: list[ResolvedTruth]
) -> CaseScore:
    metrics: dict[str, float | None] = {}
    details: dict[str, Any] = {}

    details["observed"] = observed.diagnostics
    details["status"] = observed.status if not observed.error else f"{observed.status} ({observed.error})"
    details["truths"] = [
        {"match": t.match, "expected": t.expected,
         # what the run produced for that match, so a miss shows the observed value
         "observed": [getattr(r, "value", None) for r in observed.evidence if _row_matches(r, t.match)][:5]}
        for t in truths
    ]
    if case.expect.spec:
        matched, total = spec_matches(case.expect.spec, observed.spec)
        metrics["spec_accuracy"] = matched / total if total else None
        details["spec_matched"] = matched
        details["spec_total"] = total
    else:
        metrics["spec_accuracy"] = None

    if case.expect.status is not None:
        metrics["status_ok"] = 1.0 if observed.status == case.expect.status else 0.0
        details["status"] = observed.status
    else:
        metrics["status_ok"] = None

    metrics["value_correctness"] = value_correctness(observed, truths)

    metrics["revisions"] = float(observed.revisions)
    metrics["latency_ms"] = observed.latency_ms
    metrics["llm_calls"] = None if observed.llm_calls is None else float(observed.llm_calls)
    metrics["tokens"] = None if observed.tokens is None else float(observed.tokens)

    missing_mentions = [m for m in case.expect.must_mention if m not in observed.response_text]
    forbidden_hits = [f for f in case.expect.forbid if f in observed.response_text]
    if missing_mentions:
        details["missing_mentions"] = missing_mentions
    if forbidden_hits:
        details["forbidden_hits"] = forbidden_hits
    if observed.error:
        details["error"] = observed.error

    return CaseScore(id=case.id, tags=case.tags, metrics=metrics, details=details)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(cases: list[EvalCase], scores: list[CaseScore]) -> SuiteScore:
    def _aggregate_for(subset: list[CaseScore]) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for metric in SCORE_METRICS:
            present = [s.metrics[metric] for s in subset if s.metrics.get(metric) is not None]
            result[metric] = _mean([v for v in present if v is not None])
        return result

    by_tag: dict[str, dict[str, float | None]] = {}
    tags = sorted({tag for case in cases for tag in case.tags})
    for tag in tags:
        tagged = [s for s in scores if tag in s.tags]
        by_tag[tag] = _aggregate_for(tagged)

    return SuiteScore(
        overall=_aggregate_for(scores),
        by_tag=by_tag,
        cases=tuple(scores),
    )
