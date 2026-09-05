"""Deterministic test execution.

Each runner returns a ``TestResult``. No LLM. A failed *hard-gate* test
(``policies.hard_gates()``) rejects the hypothesis regardless of any other
signal. Statistical sub-checks delegate to the injected stats service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import (
    DiagnosticHypothesis,
    HypothesisTest,
    TestResult,
)


async def run_tests(
    ctx: DiagnosticContext, h: DiagnosticHypothesis, tests: list[HypothesisTest]
) -> list[TestResult]:
    hard = ctx.policies.hard_gates()
    results: list[TestResult] = []
    for t in tests:
        fn = _RUNNERS.get(t.kind)
        if fn is None:
            continue
        res = await fn(ctx, h, t)
        res.hard_gate = t.kind in hard
        results.append(res)
    return results


# --------------------------------------------------------------------------- #
# individual runners
# --------------------------------------------------------------------------- #


async def _evidence_sufficiency(ctx: DiagnosticContext, h: DiagnosticHypothesis, t: HypothesisTest) -> TestResult:
    tm = t.params.get("treatment_metric") or h.treatment_metric
    rows = ctx.evidence_for_metric(tm) if tm else []
    rows += [e for e in ctx.evidence if (e.get("metric_id") or e.get("metric_or_fact")) in _events_for(tm)]
    passed = len(rows) >= int(t.params.get("min_supporting", 1)) or bool(h.supporting_evidence)
    detail: dict[str, Any] = {"rows": len(rows)}
    note = "direct evidence present" if passed else "no direct evidence for treatment"

    # Deterministic sample-size gate — reuses the same statistical service the
    # Skeptic validates against. Missing sample_size is NOT treated as zero
    # (evidence that never reports a count is neither underpowered nor
    # sufficiently powered); only a *reported* small sample downgrades support.
    if passed:
        sizes = [r["sample_size"] for r in rows if r.get("sample_size") is not None]
        if sizes:
            check = await ctx.deps.stats.check(name="sample_size", data={"sample_size": min(sizes)})
            detail["sample_size_check"] = check.detail
            if not check.passed:
                passed = False
                note = f"reported sample size too small for statistical power: {check.detail}"

    return TestResult(
        test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=passed,
        detail=detail, note=note,
    )


async def _temporal_precedence(ctx: DiagnosticContext, h: DiagnosticHypothesis, t: HypothesisTest) -> TestResult:
    deg = t.params.get("degradation_started_at") or ctx.degradation_started_at
    tol = int(t.params.get("tolerance_minutes", 90))
    tm = t.params.get("treatment_metric") or h.treatment_metric

    if not deg:
        return TestResult(
            test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=True,
            detail={"skipped": "no degradation timestamp"}, note="cannot test precedence; not failing on absence",
        )

    change_times = _treatment_change_times(ctx, tm)
    if not change_times:
        return TestResult(
            test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=True,
            detail={"skipped": "no treatment change timestamp"}, note="no timestamp to test; not failing on absence",
        )

    deg_dt = _parse(deg)
    ok = any(ct is not None and deg_dt is not None and ct <= deg_dt + timedelta(minutes=tol) for ct in change_times)
    # explicit reversal: outcome moved strictly before the treatment -> hard fail
    reversed_order = all(
        ct is not None and deg_dt is not None and ct > deg_dt + timedelta(minutes=tol) for ct in change_times
    )
    return TestResult(
        test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=ok and not reversed_order,
        detail={"degradation": deg, "treatment_changes": [str(c) for c in change_times]},
        note="treatment precedes / coincides with degradation" if ok else "treatment change is after the outcome change",
    )


async def _segment_specificity(ctx: DiagnosticContext, h: DiagnosticHypothesis, t: HypothesisTest) -> TestResult:
    outcome = t.params.get("outcome_metric", ctx.outcome_metric)
    min_div = float(t.params.get("min_divergence_pct", 10))
    seg_rows = [
        e for e in ctx.evidence
        if (e.get("metric_id") or e.get("metric_or_fact")) == outcome and (e.get("dimensions") or {})
    ]
    changes: dict[str, float] = {
        _seg_key(e): float(e["change_pct"])
        for e in seg_rows
        if e.get("change_pct") is not None
    }
    if len(changes) < 2:
        return TestResult(
            test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=True,
            detail={"skipped": "need >=2 segments"}, note="insufficient segmentation; not failing",
        )
    worst = min(changes.values())
    best = max(changes.values())
    diverges = abs(worst - best) >= min_div
    predicted = _predicted_segment(h)
    predicted_is_worst = predicted is None or any(
        predicted in str(k) and v == worst for k, v in changes.items()
    )
    passed = diverges and predicted_is_worst
    return TestResult(
        test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=passed,
        detail={"segment_changes": changes, "predicted_segment": predicted},
        note="drop concentrated in the predicted segment" if passed else "drop is not segment-specific as predicted",
    )


async def _control_divergence(ctx: DiagnosticContext, h: DiagnosticHypothesis, t: HypothesisTest) -> TestResult:
    outcome = t.params.get("outcome_metric", ctx.outcome_metric)
    rows = [
        e for e in ctx.evidence
        if (e.get("metric_id") or e.get("metric_or_fact")) == outcome and e.get("change_pct") is not None
    ]
    control = next(
        (e for e in rows if "control" in str(e.get("dimensions") or {}).lower() or (e.get("dimensions") or {}).get("device") == "desktop"),
        None,
    )
    affected = max(rows, key=lambda e: abs(e.get("change_pct") or 0), default=None)
    if control is None or affected is None:
        return TestResult(
            test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=True,
            detail={"skipped": "no control segment"}, note="no control to compare; not failing",
        )
    passed = abs(control.get("change_pct") or 0) * 2 < abs(affected.get("change_pct") or 0)
    return TestResult(
        test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=passed,
        detail={"control_change_pct": control.get("change_pct"), "affected_change_pct": affected.get("change_pct")},
        note="control moved far less than the affected segment" if passed else "control moved similarly - a common shock is likely",
    )


async def _dose_response(ctx: DiagnosticContext, h: DiagnosticHypothesis, t: HypothesisTest) -> TestResult:
    pairs = ctx.request.context.get("dose_pairs") or []
    min_pairs = int(t.params.get("min_pairs", 2))
    if len(pairs) < min_pairs:
        return TestResult(
            test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=True,
            detail={"skipped": f"need >= {min_pairs} dose pairs"}, note="no dose-response data; not failing",
        )
    # pairs: [{"treatment": x, "outcome": y}, ...] - expect monotone negative assoc
    xs = [float(p["treatment"]) for p in pairs]
    ys = [float(p["outcome"]) for p in pairs]
    monotone = all((xs[i] - xs[i - 1]) * (ys[i] - ys[i - 1]) <= 0 for i in range(1, len(xs)))
    return TestResult(
        test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=monotone,
        detail={"pairs": pairs}, note="worse treatment tracks worse outcome" if monotone else "no dose-response relationship",
    )


async def _mechanism_consistency(ctx: DiagnosticContext, h: DiagnosticHypothesis, t: HypothesisTest) -> TestResult:
    tm = t.params.get("treatment_metric") or h.treatment_metric
    tm_row = next((e for e in ctx.evidence_for_metric(tm)), None) if tm else None
    out_row = next((e for e in ctx.evidence_for_metric(ctx.outcome_metric)), None)
    if not tm_row or not out_row or tm_row.get("change_pct") is None or out_row.get("change_pct") is None:
        return TestResult(
            test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=True,
            detail={"skipped": "insufficient change data"}, note="cannot check direction; not failing",
        )
    # a degrading mechanism: treatment worsened and outcome worsened (opposite signs OK per metric polarity)
    consistent = abs(tm_row["change_pct"]) >= 5 and abs(out_row["change_pct"]) >= 3
    return TestResult(
        test_id=t.test_id, hypothesis_id=h.hypothesis_id, kind=t.kind, passed=consistent,
        detail={"treatment_change_pct": tm_row["change_pct"], "outcome_change_pct": out_row["change_pct"]},
        note="both treatment and outcome moved materially" if consistent else "movement too small to support the mechanism",
    )


_RUNNERS = {
    "evidence_sufficiency": _evidence_sufficiency,
    "temporal_precedence": _temporal_precedence,
    "segment_specificity": _segment_specificity,
    "control_divergence": _control_divergence,
    "dose_response": _dose_response,
    "mechanism_consistency": _mechanism_consistency,
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


_TREATMENT_EVENTS: dict[str, tuple[str, ...]] = {
    "metric.mobile_lcp_seconds": ("event.frontend_deployment",),
    "metric.js_error_rate": ("event.frontend_deployment", "event.tag_change"),
    "metric.avg_price": ("event.price_change",),
    "metric.attributed_orders": ("event.attribution_change", "event.tag_change"),
}


def _events_for(treatment_metric: str) -> set[str]:
    return set(_TREATMENT_EVENTS.get(treatment_metric, ()))


def _treatment_change_times(ctx: DiagnosticContext, treatment_metric: str) -> list[datetime | None]:
    times: list[datetime | None] = []
    for ev_key in _events_for(treatment_metric):
        if ev_key in ctx.event_times:
            times.append(_parse(ctx.event_times[ev_key]))
    for e in ctx.evidence_for_metric(treatment_metric):
        ts = e.get("start_time") or (e.get("time_range") or {}).get("start")
        if ts:
            times.append(_parse(str(ts)))
    return [t for t in times if t is not None]


def _seg_key(e: dict[str, Any]) -> str:
    dims = e.get("dimensions") or {}
    return ",".join(f"{k}={v}" for k, v in sorted(dims.items())) or "all"


def _predicted_segment(h: DiagnosticHypothesis) -> str | None:
    s = h.statement.lower()
    if "mobile" in s:
        return "mobile"
    if "desktop" in s:
        return "desktop"
    return None


def _parse(value: str) -> datetime | None:
    v = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
