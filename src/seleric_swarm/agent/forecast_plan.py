"""Compile a typed ForecastPlan from the tool call's metric ids and horizon — code only, no LLM."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from seleric_swarm.forecasting.gates import check_certified
from seleric_swarm.forecasting.horizon import resolve_horizon
from seleric_swarm.forecasting.policies import (
    ForecastPolicies,
    assert_catalogue_dimensions,
    catalogue_forecast_block,
    default_policies,
)
from seleric_swarm.forecasting.types import ForecastPlan, ForecastPlanTarget
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot


async def compile_forecast_plan(
    targets: Sequence[str],
    *,
    catalogue: CatalogueSnapshot,
    as_of: date | datetime,
    horizon_days: int = 14,
    entity_dimension: str | None = None,
    entity_values: Sequence[str] = (),
    combine_entities: bool = False,
    threshold: float | None = None,
    period_start: str | date | None = None,
    period_end: str | date | None = None,
    policies: ForecastPolicies | None = None,
    timezone: str = "Asia/Kolkata",
    canonical: Any | None = None,
) -> ForecastPlan:
    """Code decides the rest: eligibility, horizon clamps, derived-ratio expansion."""
    policies = policies or default_policies()
    as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of

    known = catalogue.metric_ids()
    plan_notes: list[str] = []
    resolved_ids: list[str] = []
    for raw in targets:
        mid = _bare(str(raw))
        if not mid:
            continue
        if canonical is not None and callable(canonical):
            mid = _bare(canonical(mid))
        # Derived policy targets (aov, net_roas, ...) may not be catalogue metrics.
        if (known and mid not in known) and mid not in policies.derived:
            plan_notes.append(f"no catalogue metric for '{raw}'")
            continue
        if mid not in resolved_ids:
            resolved_ids.append(mid)

    p_end = _iso_date(period_end)
    if p_end is not None and p_end < as_of_d:
        raise ValueError(
            f"period ended {p_end.isoformat()}, before today {as_of_d.isoformat()}: "
            "a forecast only looks forward, so report the actuals for it instead"
        )
    asked_days = horizon_days
    if p_end is not None:
        # forecast through the end of the period asked about (calendar month, quarter, ...)
        horizon_days = (p_end - as_of_d).days + 1
    horizon = resolve_horizon(
        {"n": horizon_days, "unit": "day", "period_word": "next_n"},
        as_of=as_of_d,
        timezone=timezone,
        max_horizon_days=policies.defaults.max_horizon_days,
    )

    entity_dimension = (entity_dimension or "").strip() or None
    entity_values = [str(v) for v in entity_values if str(v).strip()]

    targets_out: list[ForecastPlanTarget] = []

    # Validate entity dimensions against catalogue, dropping deprecated/unknown ones
    catalogue_dim_ids = set(catalogue.dimensions)
    policies, dim_warnings = assert_catalogue_dimensions(policies, catalogue_dim_ids, catalogue)
    plan_notes.extend(dim_warnings)

    # Expand derived ratios into component targets + recomposition metadata.
    expanded: list[str] = []
    derived_meta: dict[str, list[str]] = {}
    for mid in resolved_ids:
        if mid in policies.derived:
            spec = policies.derived[mid]
            derived_meta[mid] = [spec.numerator, spec.denominator]
            for part in (spec.numerator, spec.denominator):
                if part not in expanded:
                    expanded.append(part)
            plan_notes.append(f"derived:{mid}->{spec.numerator}/{spec.denominator}")
        else:
            if mid not in expanded:
                expanded.append(mid)

    if p_end is None and horizon.n_days < asked_days:
        plan_notes.append(
            f"horizon_clamped: asked {asked_days} days, policy maximum is {horizon.n_days}; "
            f"only {horizon.n_days} days are forecast"
        )

    # Entity policy may clamp horizon (platform/channel first).
    entity_max_h: int | None = None
    if entity_dimension:
        for mid in expanded:
            policy = policies.target(mid)
            if policy and entity_dimension in policy.entities:
                ep = policy.entities[entity_dimension]
                entity_max_h = (
                    ep.max_horizon_days
                    if entity_max_h is None
                    else min(entity_max_h, ep.max_horizon_days)
                )
        if entity_max_h is not None and horizon.n_days > entity_max_h:
            new_end = horizon.start + timedelta(days=entity_max_h - 1)
            horizon = horizon.model_copy(
                update={"end": new_end, "n_days": entity_max_h}
            )
            plan_notes.append(f"entity_horizon_clamped:{entity_max_h}")

    max_maturity = 0
    for mid in expanded:
        block = catalogue_forecast_block(catalogue, mid)
        status, reasons = policies.eligibility(
            mid, entity_dimension, catalogue_forecast=block
        )
        policy = policies.target(mid)
        lag = policies.defaults.cutoff_lag_days
        if lag is not None:
            maturity = max(0, lag - 1)  # cutoff = as_of - 1 - maturity = as_of - lag
        else:
            maturity = policy.eligibility.maturity_days if policy else 0
        max_maturity = max(max_maturity, maturity)

        # Empty catalogue snapshot (tests) cannot prove support — only refuse
        # when the catalogue actually lists dimensions for the metric.
        if (
            entity_dimension
            and not catalogue.carries(mid, entity_dimension)
            and catalogue.supported_dimensions_for(mid)
        ):
            status = "refused"
            reasons = [*reasons, "T_ENTITY_UNSUPPORTED"]

        if status == "refused" and "T_POLICY_MISSING" in reasons:
            # Eligible certified metric not yet in policy → provisional (P2).
            cert = check_certified(mid, catalogue=catalogue)
            if cert is not None and cert.action == "block":
                reasons = [r for r in reasons if r != "T_POLICY_MISSING"] + ["T_CERTIFIED"]
                if cert.detail:
                    plan_notes.append(f"T_CERTIFIED:{mid}:{cert.detail}")
            elif catalogue.has_metric(mid) or not catalogue.metrics:
                status = "provisional"
                reasons = [r for r in reasons if r != "T_POLICY_MISSING"] + ["T_PROVISIONAL"]

        if status == "provisional" and policy is None:
            # Still require certified when the catalogue can say.
            cert = check_certified(mid, catalogue=catalogue)
            if cert is not None and cert.action == "block" and catalogue.metrics:
                status = "refused"
                reasons = [*reasons, "T_CERTIFIED"]

        bundle = policy.approved_bundle() if policy else None
        targets_out.append(
            ForecastPlanTarget(
                metric_id=mid,
                label=catalogue.label_for(mid) or mid,
                status=status,  # type: ignore[arg-type]
                reason_codes=reasons,
                maturity_days=maturity,
                nonnegative=bool(policy.eligibility.nonnegative) if policy else True,
                date_basis=(
                    policy.eligibility.date_basis
                    if policy and policy.eligibility.date_basis
                    else (catalogue.date_basis_for(mid)[0])
                ),
                bundle_id=bundle.id if bundle else None,
                engine=bundle.engine if bundle else (
                    policies.defaults.provisional.engine if status == "provisional" else None
                ),
                drivers=list(policy.drivers) if policy else [],
                co_targets=list(bundle.co_targets) if bundle else [],
                past_covariates=list(bundle.past_covariates) if bundle else [],
                known_future=list(bundle.known_future) if bundle else [
                    "calendar.dow", "calendar.festival", "calendar.payday"
                ],
            )
        )

    for mid, parts in derived_meta.items():
        targets_out.append(
            ForecastPlanTarget(
                metric_id=mid,
                label=catalogue.label_for(mid) or mid,
                status="provisional",
                reason_codes=["T_DERIVED"],
                is_derived=True,
                derived_of=parts,
                known_future=[],
            )
        )

    from seleric_swarm.forecasting.assembler import compute_cutoff

    cutoff = compute_cutoff(as_of=as_of_d, maturity_days=max_maturity)
    w_start = max(_iso_date(period_start) or horizon.start, horizon.start)
    w_end = min(p_end or horizon.end, horizon.end)
    windowed = (w_start, w_end) != (horizon.start, horizon.end) and w_start <= w_end
    return ForecastPlan(
        window_start=w_start if windowed else None,
        window_end=w_end if windowed else None,
        targets=targets_out,
        entity_dimension=entity_dimension,
        entity_values=entity_values,
        combine_entities=combine_entities and len(entity_values) > 1,
        threshold=threshold,
        horizon=horizon,
        as_of=as_of_d,
        cutoff=cutoff,
        policy_version=policies.version,
        notes=plan_notes,
    )


def render_forecast_plan(plan: ForecastPlan) -> str:
    """Advisory plan block for the mission prompt."""
    lines = [
        "Forecast plan:",
        f"- as_of: {plan.as_of.isoformat()}  cutoff: {plan.cutoff.isoformat()}",
        (f"- horizon: {plan.horizon.start.isoformat()}"
         f"{' (today, a partial day)' if plan.horizon.start == plan.as_of else ''} .. "
         f"{plan.horizon.end.isoformat()} ({plan.horizon.n_days} days, {plan.horizon.period_word})"),
    ]
    lines += [f"- LIMIT (tell the user): {n}" for n in plan.notes if n.startswith("horizon_clamped")]
    if plan.window_start and plan.window_end:
        lines.append(
            f"- totals window: {plan.window_start.isoformat()} .. {plan.window_end.isoformat()}"
        )
    lines.append("- grain: daily only (no hourly or sub-daily forecast exists)")
    if not plan.entity_dimension:
        lines.append("- scope: whole account (no entity filter applied)")
    if plan.entity_dimension:
        lines.append(
            f"- entity: {plan.entity_dimension}"
            + (f" in {plan.entity_values}" if plan.entity_values else " (top-N by volume)")
        )
    for t in plan.targets:
        extra = f" [{', '.join(t.reason_codes)}]" if t.reason_codes else ""
        derived = " (derived)" if t.is_derived else ""
        lines.append(
            f"- {t.metric_id}{derived}: {t.status} maturity={t.maturity_days}d"
            f" engine={t.engine or 'auto'}{extra}"
        )
    for note in plan.notes:
        if not note.startswith("horizon_clamped"):
            lines.append(f"- note: {note}")
    return "\n".join(lines)


def _iso_date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _bare(metric_id: str) -> str:
    mid = (metric_id or "").strip()
    if mid.startswith("metric."):
        return mid[len("metric.") :]
    return mid
