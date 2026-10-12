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

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from seleric_swarm.evals._case import EvalCase
    from seleric_swarm.evals._runner import ObservedCase

HIGHER_IS_BETTER = (
    "spec_accuracy",
    "status_ok",
    "value_correctness",
    "sections_covered",
    "chart_reconciles",
    "numbers_grounded",
    "mentions_ok",
    "forbid_clean",
)
LOWER_IS_BETTER = ("revisions", "latency_ms", "llm_calls", "tokens", "query_calls", "cost_usd")
SCORE_METRICS = HIGHER_IS_BETTER + LOWER_IS_BETTER

# A value and its truth can be the *same* number carried through different
# representations: the truth is read from a live query, the evidence value is a
# JSON/JSONB round-trip, so the last bit can differ (~1e-16 relative). This floor
# absorbs that representation error while staying 7 orders of magnitude below any
# real numeric mistake, so a wrong value or window still fails (AG-00).
REPRESENTATION_EPS = 1e-9


def _within(value: float, expected: float, tolerance: float) -> bool:
    """Compare two numbers allowing for float representation, not for error."""
    floor = REPRESENTATION_EPS * max(abs(expected), 1.0)
    return abs(value - expected) <= max(tolerance, floor)


_FIGURE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%|k|K|m|M|L|lakh|Cr|cr|crore)?(?![\w])")
_SCALES = {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "L": 1e5, "lakh": 1e5, "Cr": 1e7, "cr": 1e7, "crore": 1e7}


def response_figures(text: str, *, ignore_text: str = "") -> list[tuple[str, list[float], int]]:
    """Figures a reader would take as claims: ``(raw, candidate values, decimals shown)``.

    Candidates cover the ways one evidence value is printed: as is, x100 for a
    share shown as a percent, and expanded K/M/L/Cr suffixes. Years, day-of-month
    style small integers and anything the user typed themselves are not claims.
    """
    typed = {m.group(0).strip() for m in _FIGURE.finditer(ignore_text)}
    out: list[tuple[str, list[float], int]] = []
    for m in _FIGURE.finditer(text):
        raw = m.group(0).strip()
        whole, frac, suffix = m.group(1).replace(",", ""), m.group(2), m.group(3)
        if raw in typed:
            continue
        number = float(f"{whole}.{frac}" if frac else whole)
        if frac is None and suffix is None and ("," not in m.group(1)) and (number < 100 or 1900 <= number <= 2100):
            continue
        decimals = len(frac) if frac else 0
        if suffix == "%":
            candidates = [number, number / 100]
        elif suffix in _SCALES:
            candidates = [number * _SCALES[suffix]]
        else:
            candidates = [number]
        out.append((raw, candidates, decimals))
    return out


def _print_match(figure: float, decimals: int, value: float) -> bool:
    """True when ``value`` prints as ``figure`` at the shown precision."""
    return abs(figure - value) <= 0.5 * 10.0 ** (-decimals) + REPRESENTATION_EPS * max(abs(value), 1.0)


def figures_grounded(observed: ObservedCase, question: str = "") -> tuple[float | None, list[str]]:
    """Fraction of the answer's figures that equal an evidence value as printed.

    ``None`` when the answer states no figures, so a narrative answer is not
    scored as grounded or ungrounded. Returns the ungrounded figures too.
    """
    figures = response_figures(observed.response_text, ignore_text=question)
    if not figures:
        return None, []
    values = [float(e.value) for e in observed.evidence if isinstance(e.value, (int, float)) and not isinstance(e.value, bool)]
    totals = [sum(values)] if values else []
    pool = values + totals
    ungrounded: list[str] = []
    for raw, candidates, decimals in figures:
        hit = any(
            _print_match(c, decimals, v) or _print_match(c, decimals, v * 100)
            for c in candidates
            for v in pool
        )
        if not hit:
            ungrounded.append(raw)
    return 1.0 - len(ungrounded) / len(figures), ungrounded


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


def _iso_day(value: Any) -> str | None:
    """The calendar day of a date/datetime string, ignoring any time component."""
    if isinstance(value, str) and value:
        return value[:10]
    if hasattr(value, "isoformat"):
        return str(value.isoformat())[:10]
    return None


def _window_matches(row_window: dict[str, Any], match: dict[str, Any]) -> bool:
    """The truth names its window; an evidence row on another window is not it.

    A truth with no window matches any window; a truth with a window requires the
    row to carry the same one, so a right number on the wrong day fails (AG-00-T06).
    """
    start, end = match.get("start"), match.get("end")
    if start is None and end is None:
        return True
    want_start = _iso_day(start) or _iso_day(end)
    want_end = _iso_day(end) or _iso_day(start)
    row_start = _iso_day((row_window or {}).get("start"))
    row_end = _iso_day((row_window or {}).get("end"))
    return row_start == want_start and row_end == want_end


def _row_matches(row: Any, match: dict[str, Any]) -> bool:
    metric = match.get("metric_id", match.get("metric"))
    if metric is not None and _bare(getattr(row, "metric_id", None)) != _bare(metric):
        return False
    wanted_dims = match.get("dimensions") or match.get("dimension_values") or {}
    row_dims = getattr(row, "dimensions", {}) or {}
    if not all(row_dims.get(k) == v for k, v in wanted_dims.items()):
        return False
    return _window_matches(getattr(row, "window", {}) or {}, match)


def _truth_detail(observed: ObservedCase, truth: ResolvedTruth) -> dict[str, Any]:
    """What the run produced for one truth, and whether it matched.

    Distinguishes *why* a truth missed: no evidence row at all ("missing"), a row
    on a different window ("wrong_window"), a row with no numeric value
    ("non_numeric"), or a numeric value that disagrees ("value_mismatch").
    """
    candidates = [row for row in observed.evidence if _row_matches(row, truth.match)]
    observed_values = [getattr(row, "value", None) for row in candidates][:5]
    matched = False
    reason = "missing"
    for row in candidates:
        value = _numeric(row.value)
        if value is None:
            reason = "non_numeric"
            continue
        if truth.expected is not None and _within(value, truth.expected, truth.tolerance):
            matched, reason = True, "matched"
            break
        reason = "value_mismatch"
    return {
        "match": truth.match,
        "expected": truth.expected,
        "observed": observed_values,
        "matched": matched,
        "reason": reason,
    }


def value_correctness(observed: ObservedCase, truths: list[ResolvedTruth]) -> float | None:
    scored = [truth for truth in truths if truth.expected is not None]
    if not scored:
        return None
    correct = sum(1 for truth in scored if _truth_detail(observed, truth)["matched"])
    return correct / len(scored)


def score_case(
    case: EvalCase, observed: ObservedCase, truths: list[ResolvedTruth]
) -> CaseScore:
    metrics: dict[str, float | None] = {}
    details: dict[str, Any] = {}

    details["observed"] = observed.diagnostics
    details["status"] = observed.status if not observed.error else f"{observed.status} ({observed.error})"
    details["route"] = observed.route
    details["fallback_reason"] = observed.fallback_reason
    # Per-truth outcome and *why* it missed, so a numerical failure is explainable
    # from the report alone (AG-00).
    details["truths"] = [_truth_detail(observed, t) for t in truths]
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

    # AG-08 report metrics (read from v2_mission diagnostics when present).
    mission = (observed.diagnostics or {}).get("v2_mission") or {}
    coverage = (observed.diagnostics or {}).get("coverage") or mission.get("coverage") or {}
    run = mission.get("run") or {}
    sections = coverage.get("sections") if isinstance(coverage, dict) else None
    if isinstance(sections, list) and sections:
        covered = sum(
            1
            for s in sections
            if isinstance(s, dict) and s.get("status") in ("complete", "partial", "provisional")
        )
        metrics["sections_covered"] = covered / len(sections)
    else:
        metrics["sections_covered"] = None
    # Charts reconcile when the run produced result sets without step errors on
    # charted metrics, or when no charts were expected.
    step_errors = run.get("step_errors") if isinstance(run, dict) else None
    if isinstance(step_errors, list):
        metrics["chart_reconciles"] = 1.0 if not step_errors else 0.0
    else:
        metrics["chart_reconciles"] = None
    # Numbers are grounded when each figure the answer states equals an evidence
    # value (or the sum of them) at the precision it prints. Narrative answers
    # with no figures are unscored (None), never a pass.
    grounded, ungrounded = figures_grounded(observed, question=observed.question)
    if grounded is not None:
        metrics["numbers_grounded"] = grounded
        if ungrounded:
            details["ungrounded_figures"] = ungrounded
    elif observed.status in ("completed", "partial") and observed.response_text:
        metrics["numbers_grounded"] = None
    else:
        metrics["numbers_grounded"] = 0.0 if "report" in case.tags else None
    query_calls = run.get("query_calls") if isinstance(run, dict) else None
    metrics["query_calls"] = float(query_calls) if isinstance(query_calls, (int, float)) else None

    missing_mentions = [m for m in case.expect.must_mention if m not in observed.response_text]
    forbidden_hits = [f for f in case.expect.forbid if f in observed.response_text]
    must = case.expect.must_mention
    metrics["mentions_ok"] = (1.0 - len(missing_mentions) / len(must)) if must else None
    metrics["forbid_clean"] = (0.0 if forbidden_hits else 1.0) if case.expect.forbid else None
    metrics["cost_usd"] = observed.cost_usd
    if missing_mentions:
        details["missing_mentions"] = missing_mentions
    if forbidden_hits:
        details["forbidden_hits"] = forbidden_hits
    if observed.error:
        details["error"] = observed.error

    return CaseScore(id=case.id, tags=case.tags, metrics=metrics, details=details)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; ``None`` for no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def aggregate(cases: list[EvalCase], scores: list[CaseScore]) -> SuiteScore:
    def _aggregate_for(subset: list[CaseScore]) -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        for metric in SCORE_METRICS:
            present = [s.metrics[metric] for s in subset if s.metrics.get(metric) is not None]
            result[metric] = _mean([v for v in present if v is not None])
        latencies = [s.metrics["latency_ms"] for s in subset if s.metrics.get("latency_ms") is not None]
        result["latency_p50_ms"] = percentile([v for v in latencies if v is not None], 0.50)
        result["latency_p95_ms"] = percentile([v for v in latencies if v is not None], 0.95)
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
