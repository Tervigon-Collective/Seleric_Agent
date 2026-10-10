"""Compile a typed ForecastPlan from Understanding slots — code only, no LLM."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from seleric_swarm.agent.plan import MetricSlot, TermResolver, _resolve_metrics
from seleric_swarm.forecasting.gates import check_certified
from seleric_swarm.forecasting.horizon import resolve_horizon
from seleric_swarm.forecasting.policies import (
    ForecastPolicies,
    catalogue_forecast_block,
    default_policies,
)
from seleric_swarm.forecasting.types import ForecastPlan, ForecastPlanTarget
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot


async def compile_forecast_plan(
    understanding: Any,
    *,
    catalogue: CatalogueSnapshot,
    resolver: TermResolver | None = None,
    as_of: date | datetime,
    scope: Any | None = None,
    policies: ForecastPolicies | None = None,
    timezone: str = "Asia/Kolkata",
    canonical: Any | None = None,
) -> ForecastPlan:
    """Follow the ``plan_from_slots`` pattern: LLM supplies slots, code decides the rest."""
    policies = policies or default_policies()
    as_of_d = as_of.date() if isinstance(as_of, datetime) else as_of

    slots = getattr(understanding, "forecast", None)
    metric_slots = _metric_slots(understanding, slots)
    resolved_ids, notes = await _resolve_metrics(metric_slots, resolver, catalogue)
    if canonical is not None:
        resolved_ids = [canonical(mid) if callable(canonical) else mid for mid in resolved_ids]
    # Prefer bare catalogue ids (strip metric. prefix when present)
    resolved_ids = [_bare(mid) for mid in resolved_ids]
    # Derived policy targets (aov, net_roas, …) may not be catalogue metrics —
    # accept the slot's metric_id when it is a known derived spec.
    for slot in metric_slots:
        bare = _bare(slot.metric_id.strip()) if slot.metric_id else ""
        if bare and bare in policies.derived and bare not in resolved_ids:
            resolved_ids.append(bare)
    resolved_ids = list(dict.fromkeys(resolved_ids))

    horizon = resolve_horizon(
        slots,
        as_of=as_of_d,
        timezone=timezone,
        max_horizon_days=policies.defaults.max_horizon_days,
    )

    entity_dimension = (
        (getattr(slots, "entity_dimension", None) if slots is not None else None)
        or getattr(understanding, "entity_dimension", None)
        or None
    )
    if entity_dimension == "":
        entity_dimension = None
    entity_values = _entity_values(scope)

    targets: list[ForecastPlanTarget] = []
    plan_notes = list(notes)

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

        if entity_dimension and not catalogue.carries(mid, entity_dimension):
            # Empty catalogue snapshot (tests) cannot prove support — only refuse
            # when the catalogue actually lists dimensions for the metric.
            if catalogue.supported_dimensions_for(mid):
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
        targets.append(
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
        targets.append(
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
    return ForecastPlan(
        targets=targets,
        entity_dimension=entity_dimension,
        entity_values=entity_values,
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
        f"- horizon: {plan.horizon.start.isoformat()} .. {plan.horizon.end.isoformat()} "
        f"({plan.horizon.n_days} days, {plan.horizon.period_word})",
    ]
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
        lines.append(f"- note: {note}")
    return "\n".join(lines)


def _metric_slots(understanding: Any, slots: Any | None) -> list[MetricSlot]:
    if slots is not None:
        targets = getattr(slots, "targets", None)
        if targets:
            return list(targets)
    return list(getattr(understanding, "metrics", None) or [])


def _entity_values(scope: Any | None) -> list[str]:
    if scope is None:
        return []
    filters = getattr(scope, "value_filters", None) or []
    values: list[str] = []
    for f in filters:
        if isinstance(f, dict):
            for v in f.get("values") or f.get("member_values") or []:
                values.append(str(v))
        else:
            for v in getattr(f, "values", None) or []:
                values.append(str(v))
    return values


def _bare(metric_id: str) -> str:
    mid = (metric_id or "").strip()
    if mid.startswith("metric."):
        return mid[len("metric.") :]
    return mid
