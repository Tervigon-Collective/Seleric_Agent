"""Eligibility and temporal gates — every decision carries a reason code."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Literal

from seleric_swarm.forecasting.policies import ForecastPolicies
from seleric_swarm.forecasting.types import GateDecision
from seleric_swarm.services.catalogue_bootstrap import CatalogueSnapshot

Role = Literal["target", "co_target", "past_covariate", "known_future"]


def cutoff_for(
    *,
    as_of: date,
    maturity_days: int,
    last_data_day: date | None = None,
) -> date:
    """T_CUTOFF: context ends at min(as_of − 1, last mature day)."""
    last_complete = as_of - timedelta(days=1)
    mature_end = last_complete - timedelta(days=max(0, maturity_days))
    cutoff = mature_end
    if last_data_day is not None and last_data_day < cutoff:
        cutoff = last_data_day
    return cutoff


def apply_temporal_cutoff(
    values: list[float | None],
    index: list[date],
    *,
    cutoff: date,
    series: str,
) -> tuple[list[float | None], list[GateDecision]]:
    """Mask values after cutoff to null (T_CUTOFF / T_FEATURE_MATURITY)."""
    out: list[float | None] = []
    decisions: list[GateDecision] = []
    masked = 0
    for d, v in zip(index, values, strict=True):
        if d > cutoff:
            out.append(None)
            masked += 1
        else:
            out.append(v)
    if masked:
        decisions.append(
            GateDecision(
                code="T_CUTOFF",
                series=series,
                action="mask",
                detail=f"masked {masked} day(s) after cutoff {cutoff.isoformat()}",
            )
        )
    return out, decisions


def check_future_role(metric_id: str, role: Role) -> GateDecision | None:
    """T_FUTURE_ROLE: a metric can never be known-future."""
    if role == "known_future" and not metric_id.startswith("calendar."):
        return GateDecision(
            code="T_FUTURE_ROLE",
            series=metric_id,
            action="block",
            detail="only calendar.* features may be known-future",
        )
    return None


def check_identity(
    target_id: str,
    candidate_id: str,
    *,
    composition_ids: Iterable[str],
    role: Role,
) -> GateDecision | None:
    """T_IDENTITY: formula components may be co-targets, never covariates."""
    tree = set(composition_ids)
    if candidate_id in tree and role == "past_covariate":
        return GateDecision(
            code="T_IDENTITY",
            series=candidate_id,
            action="block",
            detail=f"{candidate_id} is in {target_id}'s composition/depends_on; co-target only",
        )
    return None


def check_date_basis(
    target_id: str,
    candidate_id: str,
    *,
    catalogue: CatalogueSnapshot,
    role: Role,
    policy_allows_cross_basis: bool = False,
) -> GateDecision | None:
    """T_DATE_BASIS: co-targets must share the target's date basis."""
    if role != "co_target":
        if role == "past_covariate" and not policy_allows_cross_basis:
            t_basis, _ = catalogue.date_basis_for(target_id)
            c_basis, _ = catalogue.date_basis_for(candidate_id)
            if t_basis and c_basis and t_basis != c_basis:
                return GateDecision(
                    code="T_DATE_BASIS",
                    series=candidate_id,
                    action="drop",
                    detail=f"covariate basis {c_basis} ≠ target basis {t_basis}; not policy-listed",
                )
        return None
    t_basis, _ = catalogue.date_basis_for(target_id)
    c_basis, _ = catalogue.date_basis_for(candidate_id)
    if t_basis and c_basis and t_basis != c_basis:
        return GateDecision(
            code="T_DATE_BASIS",
            series=candidate_id,
            action="block",
            detail=f"co-target basis {c_basis} ≠ target basis {t_basis}",
        )
    return None


def check_grain(metric_id: str, *, grain_ok: bool = True) -> GateDecision | None:
    """T_GRAIN: metric must be queryable at day grain."""
    if not grain_ok:
        return GateDecision(
            code="T_GRAIN",
            series=metric_id,
            action="block",
            detail="metric is not queryable at day grain",
        )
    return None


def check_unit(
    target_id: str,
    candidate_id: str,
    *,
    catalogue: CatalogueSnapshot,
    role: Role,
) -> GateDecision | None:
    """T_UNIT: soft consistency check for co-targets (warn only)."""
    if role != "co_target":
        return None
    t_unit = catalogue.unit_for(target_id)
    c_unit = catalogue.unit_for(candidate_id)
    if t_unit and c_unit and t_unit != c_unit:
        return GateDecision(
            code="T_UNIT",
            series=candidate_id,
            action="warn",
            detail=f"unit {c_unit} differs from target unit {t_unit}",
        )
    return None


def check_certified(metric_id: str, *, catalogue: CatalogueSnapshot) -> GateDecision | None:
    """T_CERTIFIED: status certified required when catalogue carries it."""
    for meta in catalogue.metrics:
        if meta.id == metric_id:
            status = str((meta.raw or {}).get("status") or "").strip().lower()
            if status and status not in {"certified", "approved", "active", ""}:
                return GateDecision(
                    code="T_CERTIFIED",
                    series=metric_id,
                    action="block",
                    detail=f"status={status!r} is not certified",
                )
            return None
    # Unknown to snapshot: block (cannot prove certified).
    return GateDecision(
        code="T_CERTIFIED",
        series=metric_id,
        action="block",
        detail="metric not in catalogue snapshot",
    )


def check_policy_blocked(
    metric_id: str, *, policies: ForecastPolicies
) -> GateDecision | None:
    reason = policies.is_blocked(metric_id)
    if reason:
        return GateDecision(
            code="T_POLICY_BLOCKED",
            series=metric_id,
            action="block",
            detail=reason,
        )
    return None


def gate_candidate(
    *,
    target_id: str,
    candidate_id: str,
    role: Role,
    catalogue: CatalogueSnapshot,
    policies: ForecastPolicies,
    composition_ids: Iterable[str] = (),
    grain_ok: bool = True,
    policy_allows_cross_basis: bool = False,
) -> list[GateDecision]:
    """Run all eligibility gates for one candidate feature; empty = pass."""
    decisions: list[GateDecision] = []
    for check in (
        check_future_role(candidate_id, role),
        check_policy_blocked(candidate_id, policies=policies),
        check_certified(candidate_id, catalogue=catalogue)
        if not candidate_id.startswith("calendar.")
        else None,
        check_grain(candidate_id, grain_ok=grain_ok)
        if not candidate_id.startswith("calendar.")
        else None,
        check_identity(
            target_id, candidate_id, composition_ids=composition_ids, role=role
        ),
        check_date_basis(
            target_id,
            candidate_id,
            catalogue=catalogue,
            role=role,
            policy_allows_cross_basis=policy_allows_cross_basis,
        ),
        check_unit(target_id, candidate_id, catalogue=catalogue, role=role),
    ):
        if check is not None:
            decisions.append(check)
    return decisions


def leakage_canary_mask(
    target: list[float | None],
    covariate: list[float | None],
    index: list[date],
    *,
    cutoff: date,
    lag_days: int,
) -> tuple[list[float | None], list[GateDecision]]:
    """Leakage canary: a covariate equal to target shifted −k must be masked after cutoff.

    Used in tests to prove T_CUTOFF + maturity masking blocks look-ahead.
    """
    masked, decisions = apply_temporal_cutoff(
        covariate, index, cutoff=cutoff, series=f"lag_{lag_days}"
    )
    # Also null any point where the covariate's implied target day is after cutoff.
    for i, d in enumerate(index):
        implied = d + timedelta(days=lag_days)
        if implied > cutoff:
            masked[i] = None
    if not any(d.code == "T_CUTOFF" for d in decisions):
        decisions.append(
            GateDecision(
                code="T_FEATURE_MATURITY",
                series=f"lag_{lag_days}",
                action="mask",
                detail="masked look-ahead lag points past cutoff",
            )
        )
    return masked, decisions


def composition_ids_from_definition(definition: dict[str, Any] | None) -> set[str]:
    """Walk formula.composition / depends_on like composition.py does."""
    if not definition:
        return set()
    out: set[str] = set()
    raw = definition

    def walk(node: Any) -> None:
        if isinstance(node, str):
            out.add(node)
            return
        if isinstance(node, dict):
            for key in ("depends_on", "composition", "components", "parts"):
                val = node.get(key)
                if isinstance(val, list):
                    for item in val:
                        walk(item)
                elif isinstance(val, dict):
                    walk(val)
            for key in ("metric_id", "id", "numerator", "denominator"):
                if isinstance(node.get(key), str):
                    out.add(node[key])
            formula = node.get("formula")
            if isinstance(formula, dict):
                walk(formula)
            elif isinstance(formula, str):
                # crude token harvest for "a / b" style
                for tok in formula.replace("*", " ").replace("/", " ").replace("+", " ").replace("-", " ").split():
                    if tok.isidentifier() or "_" in tok:
                        out.add(tok)

    walk(raw)
    return out
