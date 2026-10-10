"""Explicit dataset-preparation and feature-engineering report.

Every forecast goes through the same ordered stages before any model runs. The
heavy lifting happens in the assembler/quality gates; this module records, per
stage, what was done so the run is auditable and the answer can say how much to
trust it.

1. Frame     target, daily grain, date basis, horizon.
2. Window    as much history as exists (up to the policy cap), ending at the
             cutoff (forecast date minus the policy lag): nothing later is read.
3. Regularise  reindex to a continuous daily calendar; missing days stay null
             (never zero-filled); the days between cutoff and horizon are
             present but unobserved so weekday/festival alignment is exact.
4. Clean     outages, listed incidents and zero-runs are masked (not smoothed);
             outliers are flagged, level shifts are reported.
5. Align     every candidate covariate goes through the same cutoff and gates
             (no look-ahead); only calendar features are known-future.
6. Engineer  calendar features (weekday, payday, month-end, festival and
             pre-festival windows) from the versioned calendar file. No manual
             lags or rolling features: Chronos-2 attends over the whole context.
7. Select    candidate features are validated by held-out backtest (bakeoff.py);
             only those that lower the error are used.
"""

from __future__ import annotations

from seleric_swarm.forecasting.types import FeatureFrame


def describe_dataset(
    frame: FeatureFrame,
    *,
    target_id: str,
    label: str,
    candidates: list[str],
    lag_days: int,
) -> list[str]:
    series = frame.targets.get(target_id) or []
    n = len(series)
    observed = sum(1 for v in series if v is not None)
    gap = max(0, (frame.horizon_start - frame.cutoff).days - 1)
    masked = [
        v for v in frame.quality_verdicts
        if v.series == target_id and v.action == "mask"
    ]
    masked_days = sum(len(v.dates) for v in masked)
    flagged = [v.code for v in frame.quality_verdicts if v.series == target_id and v.action == "warn"]
    kept = [c for c in candidates if c in frame.past_covariates]
    dropped = [c for c in candidates if c not in frame.past_covariates]
    lines = [
        f"DATASET PREPARATION for {label}:",
        f"- window: {frame.context_start.isoformat()} to {frame.cutoff.isoformat()} "
        f"({observed} observed days of {n - gap} calendar days; cutoff is {lag_days} day(s) before the forecast date)",
    ]
    if gap:
        lines.append(f"- {gap} unobserved day(s) between the cutoff and the forecast start are kept as gaps, not filled")
    if masked_days:
        lines.append(
            f"- cleaning: {masked_days} day(s) masked as outages/incidents ("
            + ", ".join(sorted({v.code for v in masked}))
            + ") so the model does not learn them as normal"
        )
    if flagged:
        lines.append("- flagged but kept: " + ", ".join(sorted(set(flagged))))
    lines.append(
        "- engineered known-future features: "
        + (", ".join(sorted(frame.future_covariates)) or "none")
    )
    if kept or dropped:
        lines.append(
            "- candidate covariates aligned to the same cutoff: "
            + (", ".join(kept) or "none")
            + (f" (dropped by data-quality gates: {', '.join(dropped)})" if dropped else "")
        )
    return lines
