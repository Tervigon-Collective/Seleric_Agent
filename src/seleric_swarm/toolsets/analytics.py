"""AnalyticsToolset v0 — calculations over already-fetched evidence (Profile C).

Frozen signatures: ``docs/refactor/CONTRACTS.md`` §4. Sprint 1 ships the two
*ported* functions, ``compare_periods`` and ``detect_anomalies``; the other
four frozen analytics functions (``contribution_analysis``,
``segment_decomposition``, ``funnel_decomposition``, ``cohort_analysis``)
have no implementation anywhere in ``src/`` to port and are Sprint 4
greenfield — deliberately absent here rather than stubbed, so a caller gets
an import error rather than a silently empty result.

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

from typing import TYPE_CHECKING, Literal

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.output import ToolResult
from seleric_swarm.analytics.comparison import MetricPoint, period_deltas
from seleric_swarm.analytics.grain import CALCULATION_VERSION, validate_grain_set
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.services.business_state.detectors import robust_zscore

if TYPE_CHECKING:
    from datetime import datetime

    from pydantic_ai import RunContext

    from seleric_swarm.agent.dependencies import SelericDeps

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
        except Exception as exc:  # noqa: BLE001 - never raise across the tool boundary
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
        return _refuse(mismatch, error_code="EVIDENCE_GRAIN_MISMATCH")

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
        base = float(delta.b.value) if delta.b.value else None
        metrics = {"delta": delta.delta, "value_a": float(delta.a.value), "value_b": float(delta.b.value)}
        if base:
            metrics["delta_pct"] = delta.delta / base * 100
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

        *history, (observed_id, observed) = usable
        result = robust_zscore([float(item.value) for _, item in history], float(observed.value))
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
