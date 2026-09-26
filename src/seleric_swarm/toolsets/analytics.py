"""AnalyticsToolset v0 — calculations over already-fetched evidence (Profile C).

Frozen signatures: ``docs/refactor/CONTRACTS.md`` §4. Sprint 1 shipped the
two *ported* functions, ``compare_periods`` and ``detect_anomalies``. Sprint 4
adds the other four — ``contribution_analysis``, ``segment_decomposition``,
``funnel_decomposition``, ``cohort_analysis`` — which had no implementation
anywhere in ``src/`` to port and are genuinely new capability. Their
arithmetic lives in ``analytics/{breakdown,funnel,cohort}.py`` so it can be
unit tested without a ``RunContext``; this module is the thin agent-facing
wrapper.

Non-negotiable rule 5: these tools calculate, they never fetch. Evidence
arrives as ``evidence_ids`` that ``toolsets/semantic.py`` already wrote to
the ``ArtifactStore``. Rule 4: they never call another tool.

Those two rules have a consequence this module exists to handle. Because a
tool can neither fetch evidence nor ask for it, the *caller* — the LLM —
chooses the grain, the window and which rows get compared. In swarm_v2 that
choice was deterministic code (a typed ``granularity`` threaded from the
classifier into both the fetcher and the detector, which is what makes
``docs/BUG_SHEET.md`` #14's fix hold). Here it is a per-call model decision.
So every entry point validates its inputs before computing and returns a
structured refusal rather than a number it cannot stand behind — see
``analytics/grain.py`` and ``CONTRACTS.md`` A1.2 (ACCEPTED 2026-09-18).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from pydantic_ai import RunContext

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.analytics import cohort as cohort_math
from seleric_swarm.analytics import funnel as funnel_math
from seleric_swarm.analytics.breakdown import Segment, contributions, shares
from seleric_swarm.analytics.comparison import MetricPoint, period_deltas
from seleric_swarm.analytics.grain import CALCULATION_VERSION, validate_grain_set
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.business_state.detectors import robust_zscore
from seleric_swarm.toolsets import policy_config as policy

# Median+MAD. "mad" and "robust_zscore" name the same estimator in this
# codebase — services/business_state/detectors.py::robust_zscore *is* the
# median/MAD one — so both map to it rather than pretending there are two
# implementations. "seasonal" is in the frozen signature but has no
# implementation in src/; it refuses explicitly instead of quietly running a
# different detector than the caller asked for.
_ZSCORE_METHODS = frozenset({"robust_zscore", "mad"})


def _provenance(evidence_ids: list[str]) -> ArtifactProvenance:
    return ArtifactProvenance(evidence_ids=list(evidence_ids), calculation_version=CALCULATION_VERSION)


def _refuse(summary: str, *, error_code: str, retryable: bool = False) -> ToolResult:
    return ToolResult(success=False, summary=summary, error_code=error_code, retryable=retryable)


def _load_evidence(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> tuple[list[EvidenceArtifact], ToolResult | None]:
    """Resolve artifact ids to typed evidence, or a refusal explaining why not.

    A missing id is reported rather than skipped: silently computing over a
    subset of what the agent asked for is how a finding ends up citing
    evidence that didn't back it (non-negotiable rule 6).
    """
    if not evidence_ids:
        return [], _refuse("no evidence_ids supplied", error_code="INSUFFICIENT_EVIDENCE")

    artifacts = ctx.deps.artifact_store.get_many(list(evidence_ids))
    found = {a.id for a in artifacts}
    missing = [aid for aid in evidence_ids if aid not in found]
    if missing:
        return [], _refuse(
            f"evidence not found in store: {', '.join(missing)}",
            error_code="INSUFFICIENT_EVIDENCE",
        )

    evidence: list[EvidenceArtifact] = []
    for artifact in artifacts:
        if artifact.artifact_type != "evidence":
            return [], _refuse(
                f"artifact {artifact.id} is artifact_type={artifact.artifact_type!r}, not evidence",
                error_code="INSUFFICIENT_EVIDENCE",
            )
        try:
            evidence.append(EvidenceArtifact.model_validate(artifact.payload))
        except Exception as exc:  # never raise across the tool boundary
            return [], _refuse(
                f"artifact {artifact.id} payload is not a valid EvidenceArtifact: {exc}",
                error_code="INSUFFICIENT_EVIDENCE",
            )
    return evidence, None


def _write_finding(
    ctx: RunContext[SelericDeps],
    *,
    finding_type: str,
    statement: str,
    evidence_ids: list[str],
    metrics: dict[str, float],
) -> str:
    finding = Finding(
        finding_type=finding_type,
        statement=statement,
        evidence_ids=list(evidence_ids),
        metrics=metrics,
    )
    artifact = ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="finding",
            payload=finding.model_dump(mode="json"),
            classification="derived",
            evidence_ids=list(evidence_ids),
            provenance=_provenance(evidence_ids),
            mission_id=ctx.deps.mission_id,
        )
    )
    return artifact.id


def _group_by_series(
    evidence: list[EvidenceArtifact], evidence_ids: list[str]
) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[tuple[str, EvidenceArtifact]]]:
    """Split a mixed evidence set into one series per (metric, dimension slice).

    A single call may carry several metrics, or one metric broken down by a
    dimension; each is its own time series and must be scored against its own
    history, never pooled.
    """
    grouped: dict[tuple[str, tuple[tuple[str, str], ...]], list[tuple[str, EvidenceArtifact]]] = {}
    for aid, item in zip(evidence_ids, evidence, strict=True):
        key = (item.metric_id, tuple(sorted(item.dimensions.items())))
        grouped.setdefault(key, []).append((aid, item))
    return grouped


async def compare_periods(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult:
    """Period-over-period change for every metric present in both periods.

    ``evidence_ids`` must cover exactly two distinct periods. **Order is
    meaningful**: the period of the first id is period A and the delta is
    ``A - B``, so a decline reads negative — the same convention
    ``swarm/specialists/observer.py::_post_comparison_deltas`` and
    ``services/intelligence/observer.py::_comparison_deltas`` already use.
    The frozen signature carries no explicit period arguments, so the id
    order is the only channel the caller's intended direction survives on.

    Metrics present in only one period are skipped, not zero-filled — an
    absent measurement is not a measurement of zero.
    """
    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        # Teach the recovery path instead of leaving the model to retry the same
        # ids (live L3: it re-called compare_periods on a 25-day vs 31-day pair
        # instead of normalizing). Non-retryable — recovery needs different
        # evidence, so the fix is a new fetch, not a repeat of this call.
        return _refuse(
            f"{mismatch}. To compare these, re-fetch both periods over the same number "
            f"of days (e.g. the first N days of each), or fetch each period as a "
            f"daily-grain series and compare their daily averages.",
            error_code="EVIDENCE_GRAIN_MISMATCH",
        )

    periods = list(dict.fromkeys((e.period_start, e.period_end) for e in evidence))
    if len(periods) != 2:
        return _refuse(
            f"compare_periods needs evidence from exactly 2 distinct periods, got {len(periods)}",
            error_code="INSUFFICIENT_EVIDENCE",
        )

    period_a = periods[0]
    points: dict[tuple[datetime, datetime], list[MetricPoint]] = {periods[0]: [], periods[1]: []}
    for aid, item in zip(evidence_ids, evidence, strict=True):
        points[(item.period_start, item.period_end)].append(
            MetricPoint(metric=item.metric_id, dimensions=dict(item.dimensions), value=item.value, ref=aid)
        )

    deltas = period_deltas(points[period_a], points[periods[1]])
    if not deltas:
        return _refuse(
            "no metric appears in both periods with a usable value",
            error_code="INSUFFICIENT_EVIDENCE",
        )

    artifact_ids: list[str] = []
    for delta in deltas:
        a_value = delta.a.value
        b_value = delta.b.value
        if a_value is None or b_value is None:
            continue
        metrics = {"delta": delta.delta, "value_a": a_value, "value_b": b_value}
        if b_value:
            metrics["delta_pct"] = delta.delta / b_value * 100
        dims = f" [{', '.join(f'{k}={v}' for k, v in sorted(delta.dimensions.items()))}]" if delta.dimensions else ""
        artifact_ids.append(
            _write_finding(
                ctx,
                finding_type="period_comparison",
                statement=f"{delta.metric}{dims} changed by {delta.delta:+.4g} between the two periods",
                evidence_ids=[delta.a.ref, delta.b.ref],
                metrics=metrics,
            )
        )

    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=f"{len(artifact_ids)} metric(s) compared across 2 periods",
        provenance=_provenance(evidence_ids),
    )


async def detect_anomalies(
    ctx: RunContext[SelericDeps],
    evidence_ids: list[str],
    method: Literal["robust_zscore", "mad", "seasonal"] = "robust_zscore",
) -> ToolResult:
    """Score the most recent point of each series against its own history.

    The evidence set *is* the series: sorted by ``period_start``, the last
    point is the observation and everything before it is the baseline. That
    is what makes this rule-5 compliant where the old
    ``RobustZScoreDetector`` was not — that class fetched its own history via
    ``BusinessStateService.get_metric_state()`` mid-detection, which an
    analytics tool may not do. The detection math itself
    (``detectors.py::robust_zscore``, median + MAD) is reused unchanged, not
    re-derived.

    ``docs/BUG_SHEET.md`` #14 lives or dies on the grain check above this
    line. A window aggregate labelled ``grain="day"`` is refused outright;
    ``swarm/specialists/anomaly.py``'s sum/normalize fallback is *not*
    ported, because dividing a 5-day sum by 5 is not the same number as a
    real daily series and pretending otherwise is what produced the original
    false "+208% spike".
    """
    if method not in _ZSCORE_METHODS:
        return _refuse(
            f"method={method!r} has no implementation in this repo yet; "
            f"available: {', '.join(sorted(_ZSCORE_METHODS))}",
            error_code="METHOD_NOT_AVAILABLE",
        )

    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    grouped = _group_by_series(evidence, list(evidence_ids))
    artifact_ids: list[str] = []
    warnings: list[str] = []

    for (metric_id, dims), series in grouped.items():
        ordered = sorted(series, key=lambda pair: pair[1].period_start)
        usable = [(aid, item) for aid, item in ordered if item.value is not None]
        if len(usable) < 2:
            # One point is an observation with no baseline. The old pipeline
            # expressed this as a policy() gate that skipped the specialist
            # (docs/BUG_SHEET.md #8 records those gates working correctly);
            # here it is a per-series warning rather than a whole-call
            # failure, so one thin series doesn't discard the others.
            warnings.append(
                f"{metric_id}: {len(usable)} usable point(s), need >=2 (1 observation + >=1 baseline)"
            )
            continue

        *history, (_observed_id, observed) = usable
        # `usable` already filtered out `value is None` above; mypy can't
        # carry that narrowing through the slice/unpack, so tell it what's
        # already proven rather than re-filtering (which would silently
        # change `history`'s length if the invariant ever broke).
        result = robust_zscore(
            [cast(float, item.value) for _, item in history], cast(float, observed.value)
        )
        if not result.is_anomaly:
            continue

        metrics = {
            "z_score": result.score,
            "observed": result.observed,
            "expected": result.expected,
            "expected_low": result.expected_range[0],
            "expected_high": result.expected_range[1],
        }
        if result.deviation_pct is not None:
            metrics["deviation_pct"] = result.deviation_pct
        dims_text = f" [{', '.join(f'{k}={v}' for k, v in dims)}]" if dims else ""
        artifact_ids.append(
            _write_finding(
                ctx,
                finding_type="anomaly",
                statement=(
                    f"{metric_id}{dims_text} was {result.observed:.4g} on "
                    f"{observed.period_start.date()}, {result.direction} vs an expected "
                    f"{result.expected:.4g} (z={result.score:.2f} over {len(history)} "
                    f"{observed.grain}-grain baseline point(s))"
                ),
                evidence_ids=[aid for aid, _ in usable],
                metrics=metrics,
            )
        )

    if not artifact_ids and warnings:
        return _refuse("; ".join(warnings), error_code="INSUFFICIENT_EVIDENCE")

    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=f"{len(artifact_ids)} anomaly(ies) across {len(grouped)} series",
        provenance=_provenance(evidence_ids),
        warnings=warnings,
    )


# ---- Sprint 4: breakdowns ---------------------------------------------------
#
# All four apply the A1.2 grain precondition. CONTRACTS.md §4 binds that rule
# textually to compare_periods/detect_anomalies only, but A1's standing rule is
# "if the caller picks the inputs, the tool must validate them", and these four
# take caller-chosen evidence_ids like the others. Verified safe for drilldown
# output: validate_grain_set skips its span-vs-grain check for grain="none",
# which is what drilldown stamps.


def _segments_for_period(
    evidence: list[EvidenceArtifact], evidence_ids: list[str], dimension: str
) -> dict[tuple[datetime, datetime], list[Segment]]:
    """Group dimension-stamped evidence into one segment list per period."""
    out: dict[tuple[datetime, datetime], list[Segment]] = {}
    for aid, item in zip(evidence_ids, evidence, strict=True):
        label = item.dimensions.get(dimension)
        if not label:
            continue
        key = (item.period_start, item.period_end)
        out.setdefault(key, []).append(Segment(label=label, value=item.value, ref=aid))
    return out


async def contribution_analysis(
    ctx: RunContext[SelericDeps], evidence_ids: list[str], dimension: str
) -> ToolResult:
    """How much each value of ``dimension`` makes up — or moved.

    With one period: each segment's share of the total. With two: each
    segment's contribution to the overall change, which is what the question
    "why did it move?" usually means.

    **The denominator is the sum of the supplied segments, not the metric's
    true total.** ``toolsets/semantic.py::drilldown`` computes a parent total
    and discards it, and it skips null-valued rows, so a server-side residual
    bucket never reaches us. Shares are therefore shares *of the observed
    parts*; a warning says so rather than letting them read as exact.
    """
    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    by_period = _segments_for_period(evidence, list(evidence_ids), dimension)
    if not by_period:
        return ToolResult(
            success=False,
            summary=(
                f"no evidence carries a {dimension!r} dimension value; "
                "contribution_analysis needs drilldown output, not a plain query_metrics "
                "breakdown (which leaves dimensions empty on every row)"
            ),
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[policy.WARN_NO_DIMENSION_EVIDENCE],
        )
    if len(by_period) > 2:
        return _refuse(
            f"contribution_analysis handles 1 or 2 periods, got {len(by_period)}",
            error_code="INSUFFICIENT_EVIDENCE",
        )

    metric_id = evidence[0].metric_id
    periods = list(by_period)
    warnings: list[str] = []
    artifact_ids: list[str] = []

    if len(periods) == 2:
        rows, total_delta = contributions(by_period[periods[0]], by_period[periods[1]])
        if not rows:
            return _refuse(
                f"no {dimension!r} value appears in both periods with a usable number",
                error_code="INSUFFICIENT_EVIDENCE",
            )
        if total_delta == 0:
            warnings.append(
                "segments exactly offset (total change is zero); per-segment deltas are "
                "reported but share-of-change is undefined"
            )
        for row in rows:
            artifact_ids.append(
                _write_finding(
                    ctx,
                    finding_type="contribution",
                    statement=(
                        f"{metric_id} [{dimension}={row.label}] changed by {row.delta:+.4g}, "
                        f"{row.share_of_change:+.1%} of the total change"
                    ),
                    evidence_ids=[row.ref_a, row.ref_b],
                    metrics={
                        "delta": row.delta,
                        "value_a": row.value_a,
                        "value_b": row.value_b,
                        "share_of_change": row.share_of_change,
                    },
                )
            )
        summary = (
            f"{len(rows)} {dimension} segment(s) contributing to a {total_delta:+.4g} change"
        )
    else:
        result = shares(by_period[periods[0]])
        if result is None or not result.shares:
            return _refuse(
                f"no usable {dimension!r} segment values (or they sum to zero)",
                error_code="INSUFFICIENT_EVIDENCE",
            )
        warnings.append(
            "shares are of the summed segments, not an independently fetched total — "
            "drilldown discards the parent total and skips null rows"
        )
        if result.other_count:
            warnings.append(
                f"{result.other_count} segment(s) below {policy.MIN_SEGMENT_SHARE:.0%} "
                f"folded into 'other' ({result.other_value:+.4g}); they remain in the "
                "denominator"
            )
        for share in result.shares:
            artifact_ids.append(
                _write_finding(
                    ctx,
                    finding_type="contribution",
                    statement=(
                        f"{metric_id} [{dimension}={share.label}] is {share.value:.4g}, "
                        f"{share.share:.1%} of the observed total"
                    ),
                    evidence_ids=[share.ref],
                    metrics={
                        "value": share.value,
                        "share": share.share,
                        "total": result.total,
                    },
                )
            )
        summary = f"{len(result.shares)} {dimension} segment(s) of a {result.total:.4g} total"

    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=summary,
        provenance=_provenance(evidence_ids),
        warnings=warnings,
    )


async def segment_decomposition(
    ctx: RunContext[SelericDeps], evidence_ids: list[str], dimensions: list[str]
) -> ToolResult:
    """Break a metric down across several dimensions at once.

    One ``Finding`` per dimension, each carrying that dimension's segments.
    Dimensions with no stamped evidence are named in ``warnings`` rather than
    silently producing nothing.

    Requires **dimension-stamped** evidence, i.e. ``drilldown`` output. A
    ``query_metrics`` breakdown call writes N rows that all carry
    ``dimensions={}`` (``semantic.py`` keeps only *filtered* dims on the
    artifact), which would pool into one indistinguishable series — this
    refuses that rather than averaging it into a number nobody can trace.
    """
    if not dimensions:
        return _refuse("no dimensions supplied", error_code="INSUFFICIENT_EVIDENCE")

    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    if not any(item.dimensions for item in evidence):
        return ToolResult(
            success=False,
            summary=(
                "every evidence row has empty dimensions — these are pooled rows from a "
                "query_metrics breakdown, not per-segment drilldown output, and cannot be "
                "told apart"
            ),
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[policy.WARN_POOLED_SEGMENTS],
        )

    metric_id = evidence[0].metric_id
    artifact_ids: list[str] = []
    warnings: list[str] = []

    for dimension in dimensions:
        by_period = _segments_for_period(evidence, list(evidence_ids), dimension)
        if not by_period:
            warnings.append(f"{policy.WARN_NO_DIMENSION_EVIDENCE}:{dimension}")
            continue
        if len(by_period) > 1:
            # This tool renders ONE decomposition; pooling several periods into
            # one series would silently average across time, which is
            # contribution_analysis's job — refuse instead of conflating.
            warnings.append(f"{policy.WARN_POOLED_SEGMENTS}:{dimension}")
            continue
        segments = list(next(iter(by_period.values())))
        result = shares(segments)
        if result is None or not result.shares:
            warnings.append(f"{policy.WARN_NO_DIMENSION_EVIDENCE}:{dimension}")
            continue
        top = result.shares[0]
        artifact_ids.append(
            _write_finding(
                ctx,
                finding_type="segment_decomposition",
                statement=(
                    f"{metric_id} by {dimension}: {len(result.shares)} segment(s), "
                    f"largest is {top.label} at {top.share:.1%} of {result.total:.4g}"
                ),
                evidence_ids=[s.ref for s in result.shares],
                metrics={
                    "segments": float(len(result.shares)),
                    "total": result.total,
                    "top_share": top.share,
                    "top_value": top.value,
                    "other_count": float(result.other_count),
                },
            )
        )

    if not artifact_ids:
        return _refuse(
            f"none of {dimensions} had usable stamped evidence",
            error_code="INSUFFICIENT_EVIDENCE",
        )

    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=(
            f"{metric_id} decomposed across {len(artifact_ids)} of "
            f"{len(dimensions)} dimension(s)"
        ),
        provenance=_provenance(evidence_ids),
        warnings=warnings,
    )


async def funnel_decomposition(
    ctx: RunContext[SelericDeps], evidence_ids: list[str]
) -> ToolResult:
    """Step-to-step conversion and drop-off across the website funnel.

    Steps and their order come from ``policy_config.FUNNEL_STEPS``, which is
    declared rather than inferred — see that constant's comment for why
    parsing ``formula`` strings to order them would be a regression.

    Every step in that list is session-anchored (``X_sessions / sessions``),
    so conversion between consecutive steps is ``rate[i+1] / rate[i]`` and no
    cross-axis division is involved. Metric ids outside the declared funnel
    are dropped and named in ``warnings``, never positioned by guesswork.
    """
    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    readings = [
        funnel_math.StepReading(metric_id=item.metric_id, value=item.value, ref=aid)
        for aid, item in zip(evidence_ids, evidence, strict=True)
    ]
    steps = funnel_math.ordered_steps(readings)
    warnings: list[str] = []

    unknown = funnel_math.unknown_steps(readings)
    if unknown:
        warnings.append(f"{policy.WARN_UNKNOWN_FUNNEL_STEP}:{','.join(unknown)}")

    if len(steps) < 2:
        return ToolResult(
            success=False,
            summary=(
                f"{len(steps)} recognized funnel step(s); need at least 2 to measure a "
                f"conversion. Declared funnel: {', '.join(policy.FUNNEL_STEPS)}"
            ),
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[policy.WARN_NO_FUNNEL_STEPS, *warnings],
        )

    moves = funnel_math.transitions(steps)
    if not moves:
        return _refuse(
            "no measurable transition (an upstream step is zero)",
            error_code="INSUFFICIENT_EVIDENCE",
        )

    artifact_ids = [
        _write_finding(
            ctx,
            finding_type="funnel_decomposition",
            statement=(
                f"{move.from_step} -> {move.to_step}: {move.conversion:.1%} converted, "
                f"{move.drop_off:.1%} dropped off"
            ),
            evidence_ids=[s.ref for s in steps],
            metrics={"conversion": move.conversion, "drop_off": move.drop_off},
        )
        for move in moves
    ]

    worst = funnel_math.worst_transition(moves)
    worst_text = (
        f"; largest drop-off {worst.drop_off:.1%} at {worst.from_step} -> {worst.to_step}"
        if worst is not None
        else ""
    )
    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=(
            f"{len(moves)} funnel transition(s) across {len(steps)} step(s){worst_text}"
        ),
        provenance=_provenance(evidence_ids),
        warnings=warnings,
    )


async def cohort_analysis(ctx: RunContext[SelericDeps], evidence_ids: list[str]) -> ToolResult:
    """Compare cohorts against their own median.

    **Does not assume a time series.** The customer-domain retention metrics
    have no daily grain — ``repeat_rate`` slices by ``brand_id`` only, and a
    windowed query returns a single row for the whole window
    (``feature_class: windowed_point``). So a cohort here is a dimension
    value, or, when the evidence carries no dimension, a measurement window.
    The summary says which of the two it did, because they answer different
    questions.
    """
    evidence, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    readings = [
        cohort_math.CohortReading(
            # One dimension value identifies the cohort; with several stamped,
            # join them so two cohorts never collapse onto one label.
            label="/".join(str(v) for _, v in sorted(item.dimensions.items())),
            value=item.value,
            period_start=item.period_start,
            ref=aid,
        )
        for aid, item in zip(evidence_ids, evidence, strict=True)
    ]
    readings, by_period = cohort_math.label_readings(readings)

    spread = cohort_math.cohort_spread(readings)
    if spread is None:
        return ToolResult(
            success=False,
            summary="fewer than 2 cohorts carry a value; nothing to compare",
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[policy.WARN_SINGLE_COHORT],
        )

    metric_id = evidence[0].metric_id
    basis = "measurement window" if by_period else "dimension value"
    artifact_ids = [
        _write_finding(
            ctx,
            finding_type="cohort_analysis",
            statement=(
                f"{metric_id} cohort {c.label} is {c.value:.4g} "
                f"({c.delta_from_median:+.4g} vs the cohort median {spread.median:.4g})"
            ),
            evidence_ids=[c.ref],
            metrics={
                "value": c.value,
                "delta_from_median": c.delta_from_median,
                "cohort_median": spread.median,
            },
        )
        for c in spread.cohorts
    ]

    return ToolResult(
        success=True,
        artifact_ids=artifact_ids,
        summary=(
            f"{len(spread.cohorts)} cohort(s) of {metric_id} by {basis}; "
            f"best {spread.best.label} {spread.best.value:.4g}, "
            f"worst {spread.worst.label} {spread.worst.value:.4g}, "
            f"spread {spread.spread:.4g}"
        ),
        provenance=_provenance(evidence_ids),
        warnings=[f"cohorts identified by {basis}"] if by_period else [],
    )
