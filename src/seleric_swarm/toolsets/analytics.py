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

import contextlib
from collections.abc import Callable, Sequence
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
from seleric_swarm.analytics.visualization import generate_visualization_spec
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


def _ratio_lookup(ctx: RunContext[SelericDeps]) -> funnel_math.RatioLookup:
    """Type a metric as a rate or a count, from the catalogue's ``aggregation``.

    The catalogue is the only authority on what a metric *is*, so a funnel's
    base stage and its steps are read off it rather than named here. A metric
    the snapshot does not carry returns ``None`` — unknown, and left unplaced
    instead of guessed at. Ids are canonicalised first because evidence is
    stamped with whichever spelling it was fetched under.
    """
    snapshot = ctx.deps.catalogue
    canonical = ctx.deps.canonical_metric_id

    def is_ratio(metric_id: str) -> bool | None:
        for candidate in (metric_id, canonical(metric_id)):
            if not snapshot.has_metric(candidate):
                continue
            aggregation = snapshot.aggregation_for(candidate)
            return aggregation == "ratio" if aggregation else None
        return None

    return is_ratio


def _provenance(evidence_ids: list[str]) -> ArtifactProvenance:
    return ArtifactProvenance(evidence_ids=list(evidence_ids), calculation_version=CALCULATION_VERSION)


def _refuse(
    summary: str, *, error_code: str, retryable: bool = False, warnings: Sequence[str] = ()
) -> ToolResult:
    return ToolResult(
        success=False,
        summary=summary,
        error_code=error_code,
        retryable=retryable,
        warnings=list(warnings),
    )


_AVAILABLE_EVIDENCE_SHOWN = 60
# Shortest id fragment accepted as a repair candidate. Long enough that a
# coincidental prefix collision across a mission's evidence is implausible,
# short enough to catch the dropped-trailing-character transcription.
_MIN_ID_PREFIX_CHARS = 8


def _repair_ids(ctx: RunContext[SelericDeps], requested: list[str]) -> tuple[list[str], dict[str, str]]:
    """Repair mistyped artifact ids, and say which id each request resolved to.

    ``evidence_ids`` is an opaque-40-char-token channel: the model must echo back
    the exact ids a previous tool returned, and at real cardinality it
    mis-transcribes them (live 2026-10-06 MS3-167d9f4838: 47 ids copied into
    ``run_python``, one character dropped from one of them, and the whole
    computation lost). ``instructions.py`` already forbids this pattern and the
    model did it anyway, because the signature *mandates* it — so the repair
    belongs here, where a dropped character is recoverable by construction.

    A repair is applied only when it is **unambiguous**: exactly one evidence
    artifact in this mission has the requested id as a prefix (or is a prefix of
    it, for a spurious trailing character). Two candidates resolve to nothing —
    guessing between them is how a finding ends up citing the wrong evidence
    (non-negotiable rule 6).
    """
    resolved = list(requested)
    known = ctx.deps.artifact_store.get_many(list(requested))
    found = {a.id for a in known}
    repairs: dict[str, str] = {}
    missing = [aid for aid in requested if aid not in found]
    if not missing:
        return resolved, repairs

    evidence_ids_in_mission = [
        a.id
        for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)
        if a.artifact_type == "evidence"
    ]
    for aid in missing:
        if len(aid) < _MIN_ID_PREFIX_CHARS:
            continue
        candidates = [
            candidate
            for candidate in evidence_ids_in_mission
            if candidate.startswith(aid) or aid.startswith(candidate)
        ]
        if len(candidates) == 1:
            repairs[aid] = candidates[0]
    if not repairs:
        return resolved, repairs
    resolved = [repairs.get(aid, aid) for aid in requested]
    return resolved, repairs


def _available_evidence(ctx: RunContext[SelericDeps]) -> str:
    """The evidence this mission already holds, so a refused call can be fixed.

    Live 2026-10-04 (MS3-34e7eb26aa): a call with ids that did not exist was
    refused with only the bad ids, and the model guessed again — twelve times.
    Naming the real ids (with what each one is) turns the next call into a fix.
    """
    rows: list[str] = []
    for artifact in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id):
        if artifact.artifact_type != "evidence":
            continue
        try:
            ev = EvidenceArtifact.model_validate(artifact.payload)
        except Exception:
            continue
        period = f"{ev.period_start.date()}" + (
            "" if ev.period_end.date() == ev.period_start.date() else f"..{ev.period_end.date()}"
        )
        dims = ", ".join(f"{k}={v}" for k, v in sorted(ev.dimensions.items()))
        rows.append(f"{artifact.id} ({ev.metric_id} {period}{' ' + dims if dims else ''})")
    if not rows:
        return " This mission holds no evidence yet: fetch it with query_metrics first."
    more = "" if len(rows) <= _AVAILABLE_EVIDENCE_SHOWN else f" …(+{len(rows) - _AVAILABLE_EVIDENCE_SHOWN} more)"
    return " Evidence available in this mission: " + "; ".join(rows[:_AVAILABLE_EVIDENCE_SHOWN]) + more


def _load_evidence(
    ctx: RunContext[SelericDeps], evidence_ids: list[str], *, warnings: Sequence[str] = ()
) -> tuple[list[EvidenceArtifact], list[str], ToolResult | None]:
    """Resolve artifact ids to typed evidence, or a refusal explaining why not.

    Returns ``(evidence, resolved_ids, refusal)``. ``resolved_ids`` is what the
    request actually became — the same length and order as the input, so callers
    can rebind over ``evidence_ids`` and have every downstream ``zip`` and
    ``_write_finding`` cite the artifact that was really read. Passing the
    *original* list back would cite an id that does not exist the moment a
    mistyped one is repaired.

    A missing id is reported rather than skipped: silently computing over a
    subset of what the agent asked for is how a finding ends up citing
    evidence that didn't back it (non-negotiable rule 6).

    ``retryable=True`` on an input-shaped refusal is deliberate and load-bearing.
    A wrong or mistyped id is the most recoverable error class there is — the
    fix is a corrected call, not a replay — but ``ToolResult.retryable``
    defaults to ``False`` and ``instructions.py`` tells the model "a
    non-retryable error does not justify replaying the same call". Live
    2026-10-06 (MS3-167d9f4838): one dropped character produced
    ``retryable=False``, the model correctly declined to re-call, abandoned the
    mission, and shipped half the question as ``completed``. ``repeat_guard``
    keys off this same flag to re-execute rather than replay, so a corrected id
    is a real retry, not a loop.
    """
    if not evidence_ids:
        return (
            [],
            [],
            _refuse(
                "no evidence_ids supplied",
                error_code="INSUFFICIENT_EVIDENCE",
                retryable=True,
                warnings=warnings,
            ),
        )

    resolved_ids, repairs = _repair_ids(ctx, list(evidence_ids))
    artifacts = ctx.deps.artifact_store.get_many(resolved_ids)
    found = {a.id for a in artifacts}
    missing = [aid for aid in resolved_ids if aid not in found]
    if missing:
        return (
            [],
            [],
            _refuse(
                f"evidence not found in store: {', '.join(missing)}." + _available_evidence(ctx),
                error_code="INSUFFICIENT_EVIDENCE",
                retryable=True,
                warnings=warnings,
            ),
        )

    evidence: list[EvidenceArtifact] = []
    for artifact in artifacts:
        if artifact.artifact_type != "evidence":
            return (
                [],
                [],
                _refuse(
                    f"artifact {artifact.id} is artifact_type={artifact.artifact_type!r}, not evidence",
                    error_code="INSUFFICIENT_EVIDENCE",
                    warnings=warnings,
                ),
            )
        try:
            evidence.append(EvidenceArtifact.model_validate(artifact.payload))
        except Exception as exc:  # never raise across the tool boundary
            return (
                [],
                [],
                _refuse(
                    f"artifact {artifact.id} payload is not a valid EvidenceArtifact: {exc}",
                    error_code="INSUFFICIENT_EVIDENCE",
                    warnings=warnings,
                ),
            )
    if repairs:
        # Say what was repaired: a silent substitution would leave the model
        # reasoning about ids that were never in its hands.
        _note_repaired_ids(
            ctx, "; ".join(f"{bad} -> {good}" for bad, good in sorted(repairs.items()))
        )
    return evidence, resolved_ids, None


def _note_repaired_ids(ctx: RunContext[SelericDeps], note: str) -> None:
    """Record a repaired-id substitution on the mission scratchpad.

    Best-effort: the scratchpad is a working note, not part of the evidence
    chain, so a failure here must never fail the load it was describing.
    """
    with contextlib.suppress(Exception):
        ctx.deps.scratchpad.note(f"[evidence ids auto-repaired] {note}")


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
    evidence: list[EvidenceArtifact],
    evidence_ids: list[str],
    canonical: Callable[[str], str] = lambda metric_id: metric_id,
) -> dict[tuple[str, tuple[tuple[str, str], ...]], list[tuple[str, EvidenceArtifact]]]:
    """Split a mixed evidence set into one series per (metric, dimension slice).

    A single call may carry several metrics, or one metric broken down by a
    dimension; each is its own time series and must be scored against its own
    history, never pooled.
    """
    grouped: dict[tuple[str, tuple[tuple[str, str], ...]], list[tuple[str, EvidenceArtifact]]] = {}
    for aid, item in zip(evidence_ids, evidence, strict=True):
        # Canonical id, not the stamped one: the same series fetched under two
        # spellings must land in one group, or each half is scored against half
        # its own history.
        key = (canonical(item.metric_id), tuple(sorted(item.dimensions.items())))
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
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
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
            f"compare needs evidence from exactly 2 distinct periods, got {len(periods)}",
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

    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    grouped = _group_by_series(evidence, list(evidence_ids), ctx.deps.canonical_metric_id)
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
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
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
                "contribution needs drilldown output, not a plain query_metrics "
                "breakdown (which leaves dimensions empty on every row)"
            ),
            error_code="INSUFFICIENT_EVIDENCE",
            warnings=[policy.WARN_NO_DIMENSION_EVIDENCE],
        )
    if len(by_period) > 2:
        return _refuse(
            f"contribution handles 1 or 2 periods, got {len(by_period)}",
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

    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
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
    """Step-to-step conversion and drop-off across a funnel.

    Supply one count metric for the base stage plus the catalogue rate metrics
    that divide by it, at one grain. Membership and order are derived, not
    declared: the catalogue types each metric, and because every step is a
    share of the same base, sorting the rates descending is the stage order.
    Conversion between consecutive stages is ``rate[i+1] / rate[i]``, so no
    cross-axis division is involved.

    Any funnel the catalogue can express works here, not only the website one.
    A reading that divides by the base without being a share of it — an average
    depth, a cost per unit — is named in ``warnings`` rather than positioned.
    """
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    readings = [
        funnel_math.StepReading(metric_id=item.metric_id, value=item.value, ref=aid)
        for aid, item in zip(evidence_ids, evidence, strict=True)
    ]
    is_ratio = _ratio_lookup(ctx)
    steps = funnel_math.ordered_steps(readings, is_ratio)
    warnings: list[str] = []

    unplaceable = funnel_math.unplaceable_steps(readings, is_ratio)
    if unplaceable:
        warnings.append(f"{policy.WARN_UNKNOWN_FUNNEL_STEP}:{','.join(unplaceable)}")

    if len(steps) < 2:
        return ToolResult(
            success=False,
            summary=(
                f"{len(steps)} placeable funnel stage(s); need at least 2 to measure a "
                "conversion. Supply one count metric for the base stage plus the "
                "catalogue rate metrics that divide by it."
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
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    mismatch = validate_grain_set(evidence)
    if mismatch is not None:
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

    readings = [
        cohort_math.CohortReading(
            # One dimension value identifies the cohort; with several stamped,
            # join them so two cohorts never collapse onto one label.
            label="/".join(v for _, v in sorted(item.dimensions.items())),
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

async def generate_visualization(
    ctx: RunContext[SelericDeps], evidence_ids: list[str], intent: str, title: str = "Visualization"
) -> ToolResult:
    """Generate a visualization specification for a given set of evidence.
    
    This tool should only be used when:
    - Comparing > 3 categories
    - Showing trends over time (time series)
    - Showing compositions (pie/donut)
    
    Do NOT use this tool for single numbers or simple KPI requests.
    """
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal
        
    spec = generate_visualization_spec(evidence, intent, title)
    
    if "error" in spec:
        return _refuse(spec["error"], error_code="VISUALIZATION_FAILED")

    # Idempotent: the same chart over the same evidence is one artifact. A
    # revision or a recovery retry of the mission re-asks for the chart it
    # already has; writing it again showed duplicate charts on the answer.
    wanted = set(evidence_ids)
    artifact = next(
        (
            a
            for a in ctx.deps.artifact_store.list_for_mission(ctx.deps.mission_id)
            if a.artifact_type == "chart_spec" and a.payload == spec and set(a.evidence_ids) == wanted
        ),
        None,
    ) or ctx.deps.artifact_store.put(
        Artifact(
            workspace_id=ctx.deps.principal.workspace_id,
            artifact_type="chart_spec",
            payload=spec,
            classification="derived",
            evidence_ids=list(evidence_ids),
            provenance=_provenance(evidence_ids),
            mission_id=ctx.deps.mission_id,
        )
    )

    return ToolResult(
        success=True,
        artifact_ids=[artifact.id],
        summary=(
            f"Generated {spec['chart_type']} chart visualization. It is attached to your answer "
            "automatically: do not link, embed or describe it as an image in final_response, and "
            "do not call generate_visualization again for the same evidence."
        ),
        provenance=_provenance(evidence_ids),
    )


# Join keys preferred when merging companion metric breakdowns (e4ad507: three
# independent top-10s for spend/sessions/orders never aligned on campaign_name).
_PREFERRED_JOIN_KEYS = (
    "campaign_name",
    "campaign_id",
    "ad_id",
    "ad_name",
    "adset_id",
    "adset_name",
    "product_title",
    "sku",
    "sub_channel",
    "channel",
    "finance_channel",
    "ad_platform",
    "platform",
)

# Derived ratios only when both source metric ids are present on a joined row.
# (label, numerator_metric_id, denominator_metric_id)
_MERGE_DERIVED: tuple[tuple[str, str, str], ...] = (
    ("cpa", "ad_spend", "orders"),
    ("roas", "net_sales", "ad_spend"),
    ("roas", "total_sales", "ad_spend"),
)

_MAX_MERGE_TABLE_ROWS = 40


def _infer_join_keys(
    evidence: list[EvidenceArtifact],
    requested: list[str],
) -> list[str] | None:
    """Pick join dimensions: caller list if every key appears on some row with a
    value; else the highest-preference key that appears on at least two different
    metrics (so companions can align)."""
    if requested:
        present = {k for item in evidence for k, v in item.dimensions.items() if k and v}
        missing = [k for k in requested if k not in present]
        if missing:
            return None
        return list(requested)

    # metric_id → keys that have a non-empty value on at least one of its rows
    by_metric: dict[str, set[str]] = {}
    for item in evidence:
        keys = {k for k, v in item.dimensions.items() if k and str(v).strip()}
        by_metric.setdefault(item.metric_id, set()).update(keys)
    if len(by_metric) < 2:
        return None
    shared = set.intersection(*by_metric.values()) if by_metric else set()
    if not shared:
        # Prefer a key that appears on ≥2 metrics even if not on every metric
        # (outer-join leaves holes for metrics that lack the entity).
        counts: dict[str, int] = {}
        for keys in by_metric.values():
            for k in keys:
                counts[k] = counts.get(k, 0) + 1
        shared = {k for k, n in counts.items() if n >= 2}
    if not shared:
        return None
    ordered = [k for k in _PREFERRED_JOIN_KEYS if k in shared]
    return ordered[:1] or sorted(shared)[:1]


def _fmt_merge_value(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 1e-9 and abs(value) >= 1:
        return f"{int(round(value)):,}"
    return f"{value:,.4g}"


async def merge_evidence_breakdowns(
    ctx: RunContext[SelericDeps],
    evidence_ids: list[str],
    dimensions: list[str] | None = None,
) -> ToolResult:
    """Outer-join companion metric breakdowns on shared entity dimensions.

    Turns separate ``query_metrics`` results (spend by campaign, orders by
    campaign, …) into one table so the answer can reason about efficiency
    instead of shipping independent top-N lists. Never fetches.
    """
    evidence, evidence_ids, refusal = _load_evidence(ctx, evidence_ids)
    if refusal is not None:
        return refusal

    join_keys = _infer_join_keys(evidence, [d for d in (dimensions or []) if d])
    if not join_keys:
        return _refuse(
            "merge needs a shared entity dimension across the evidence "
            f"(prefer one of: {', '.join(_PREFERRED_JOIN_KEYS[:6])}). "
            "Pass dimensions=[<join key>] after fetching breakdowns that stamp that key, "
            "or rank once and fetch companions for the same entity ids.",
            error_code="INVALID_ARGUMENT",
            retryable=True,
        )

    metrics = sorted({item.metric_id for item in evidence})
    if len(metrics) < 2:
        return _refuse(
            "merge needs evidence from at least two different metrics; "
            "fetch companion metrics for the same entities first",
            error_code="INVALID_ARGUMENT",
            retryable=True,
        )

    # (period, join_tuple) → metric_id → value; keep first evidence id per cell
    cells: dict[tuple[datetime, datetime, tuple[str, ...]], dict[str, float]] = {}
    cell_refs: dict[tuple[datetime, datetime, tuple[str, ...]], list[str]] = {}
    for aid, item in zip(evidence_ids, evidence, strict=True):
        if item.value is None:
            continue
        key_vals = tuple(str(item.dimensions.get(k) or "").strip() for k in join_keys)
        if any(not v for v in key_vals):
            continue
        slot = (item.period_start, item.period_end, key_vals)
        cells.setdefault(slot, {})
        # One value per metric per entity: keep the first (stable with prefetch order)
        if item.metric_id not in cells[slot]:
            cells[slot][item.metric_id] = float(item.value)
            cell_refs.setdefault(slot, []).append(aid)

    if not cells:
        return _refuse(
            f"no evidence rows carry all join keys {join_keys!r} with values; "
            "fetch breakdowns that stamp those dimensions",
            error_code="INSUFFICIENT_EVIDENCE",
            retryable=True,
        )

    # Stable sort: first join key label, then period start
    sorted_slots = sorted(cells.keys(), key=lambda s: (s[2], s[0], s[1]))

    derived_cols: list[str] = []
    for label, num_id, den_id in _MERGE_DERIVED:
        if num_id in metrics and den_id in metrics and label not in derived_cols:
            derived_cols.append(label)

    header = [*join_keys, *metrics, *derived_cols]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(join_keys) + [":---:"] * (len(metrics) + len(derived_cols))) + " |",
    ]
    finding_metrics: dict[str, float] = {}
    for slot in sorted_slots[:_MAX_MERGE_TABLE_ROWS]:
        _period_start, _period_end, key_vals = slot
        row_vals = cells[slot]
        derived: dict[str, float | None] = {label: None for label in derived_cols}
        for label, num_id, den_id in _MERGE_DERIVED:
            if label not in derived_cols or derived[label] is not None:
                continue
            num, den = row_vals.get(num_id), row_vals.get(den_id)
            if num is not None and den is not None and den != 0:
                derived[label] = num / den
        display = [
            *key_vals,
            *[_fmt_merge_value(row_vals.get(m)) for m in metrics],
            *[_fmt_merge_value(derived.get(c)) for c in derived_cols],
        ]
        lines.append("| " + " | ".join(display) + " |")
        # Index derived numbers for citation / unbacked checks
        entity = "|".join(key_vals)
        for m, v in row_vals.items():
            finding_metrics[f"{m}.{entity}"] = v
        for label, value in derived.items():
            if value is not None:
                finding_metrics[f"{label}.{entity}"] = value

    more = ""
    if len(sorted_slots) > _MAX_MERGE_TABLE_ROWS:
        more = f" (showing {_MAX_MERGE_TABLE_ROWS} of {len(sorted_slots)} entities)"

    table = "\n".join(lines)
    join_label = ", ".join(join_keys)
    statement = (
        f"Merged {len(metrics)} metrics on {join_label} across {len(sorted_slots)} entities"
        f"{more}.\n\n{table}"
    )
    finding_id = _write_finding(
        ctx,
        finding_type="merge",
        statement=statement,
        evidence_ids=list(evidence_ids),
        metrics=finding_metrics,
    )
    return ToolResult(
        success=True,
        artifact_ids=[finding_id],
        summary=(
            f"Merged metrics [{', '.join(metrics)}] on {join_label} "
            f"({len(sorted_slots)} entities{more}). "
            f"Use finding {finding_id} — one table, not separate top-N lists. "
            f"Derived columns ({', '.join(derived_cols) or 'none'}) only where both inputs exist.\n\n"
            f"{table}"
        ),
        provenance=_provenance(list(evidence_ids)),
    )


AnalysisMethod = Literal["compare", "anomaly", "contribution", "segments", "funnel", "cohort", "merge"]


async def analyze(
    ctx: RunContext[SelericDeps],
    evidence_ids: list[str],
    method: AnalysisMethod,
    dimensions: list[str] | None = None,
) -> ToolResult:
    """Calculate over evidence you already fetched (never fetches). Pick the method:

    - ``compare``: period-over-period change; ids must cover exactly two periods,
      the first id's period is A and the change is A - B.
    - ``anomaly``: score each series' latest point against its own history
      (robust z-score); the ids are the series.
    - ``contribution``: each value of ``dimensions[0]``'s share of the total (one
      period) or contribution to the change (two periods) — "what drove it".
    - ``segments``: the same metric broken down across several ``dimensions`` at once.
    - ``funnel``: step-to-step conversion and drop-off (one base count + its rates).
    - ``cohort``: compare cohorts (dimension values or windows) with their median.
    - ``merge``: outer-join companion metric breakdowns on shared entity dimensions
      (e.g. campaign_name) into one table with derived CPA/ROAS when both sides exist.
      Pass ``dimensions`` as the join key(s), or omit to infer (prefers campaign_name).

    Every result is a citable Finding."""
    dims = [d for d in (dimensions or []) if d]
    if method == "compare":
        return await compare_periods(ctx, evidence_ids)
    if method == "anomaly":
        return await detect_anomalies(ctx, evidence_ids)
    if method == "contribution":
        if not dims:
            return _refuse("contribution needs dimensions=[<the dimension to split by>]", error_code="INVALID_ARGUMENT")
        return await contribution_analysis(ctx, evidence_ids, dims[0])
    if method == "segments":
        if not dims:
            return _refuse("segments needs dimensions=[<dimension>, ...]", error_code="INVALID_ARGUMENT")
        return await segment_decomposition(ctx, evidence_ids, dims)
    if method == "funnel":
        return await funnel_decomposition(ctx, evidence_ids)
    if method == "cohort":
        return await cohort_analysis(ctx, evidence_ids)
    if method == "merge":
        return await merge_evidence_breakdowns(ctx, evidence_ids, dims or None)
    return _refuse(f"unknown method {method!r}", error_code="INVALID_ARGUMENT")
