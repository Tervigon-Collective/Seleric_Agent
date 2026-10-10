"""Deterministic forecast pipeline — policies → frame → engines → artifact."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from seleric_swarm.agent.artifacts import Finding, ForecastArtifact, PredictionArtifact
from seleric_swarm.agent.forecast_plan import render_forecast_plan
from seleric_swarm.agent.progress import emit_progress
from seleric_swarm.analytics.grain import CALCULATION_VERSION
from seleric_swarm.conversations.contracts import Artifact, ArtifactProvenance
from seleric_swarm.forecasting.assembler import assemble_feature_frame, compute_cutoff
from seleric_swarm.forecasting.bakeoff import run_bakeoff
from seleric_swarm.forecasting.dataset import describe_dataset
from seleric_swarm.forecasting.driver import apply_driver_level, fetch_candidates
from seleric_swarm.forecasting.entities import entity_filter, select_entities, sum_mismatch_warning
from seleric_swarm.forecasting.scope import brand_filters, principal_brand_id
from seleric_swarm.forecasting.selection import select_driver, selection_facts
from seleric_swarm.forecasting.chart import build_forecast_chart_spec
from seleric_swarm.forecasting.insights import build_insight_facts, render_insight_facts
from seleric_swarm.forecasting.derive import horizon_totals, ratio_from_components
from seleric_swarm.forecasting.engines import route_and_forecast
from seleric_swarm.forecasting.features import candidate_features
from seleric_swarm.forecasting.policies import ForecastPolicies, default_policies
from seleric_swarm.forecasting.quality import is_blocking
from seleric_swarm.forecasting.types import (
    ForecastPlan,
    ForecastPlanTarget,
    TargetForecast,
)
from seleric_swarm.models.service import ForecastUnavailable, model_registry_from_yaml

_log = logging.getLogger("seleric.forecasting.pipeline")


@dataclass
class ForecastOutcome:
    plan: ForecastPlan
    artifact: ForecastArtifact | None = None
    artifact_id: str | None = None
    prediction_ids: list[str] = field(default_factory=list)
    skeleton: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    refused: bool = False
    warnings: list[str] = field(default_factory=list)


async def run_forecast(
    deps: Any,
    plan: ForecastPlan,
    *,
    policies: ForecastPolicies | None = None,
    chronos_url: str | None = None,
    stage_budget_s: float = 25.0,
) -> ForecastOutcome:
    """Run §3–§7 of the forecasting plan. Fail-closed: refuse rather than guess."""
    policies = policies or default_policies()
    started = time.perf_counter()
    stats: dict[str, Any] = {"stages": {}}
    warnings: list[str] = []
    mission_id = getattr(deps, "mission_id", "") or ""

    def _stage(name: str, t0: float) -> None:
        stats["stages"][name] = round((time.perf_counter() - t0) * 1000)

    emit_progress(mission_id, "agent.stage", "Building forecast frame", {"stage": "forecast"})

    active = [t for t in plan.targets if t.status != "refused" and not t.is_derived]
    derived = [t for t in plan.targets if t.is_derived]
    if not active and not derived:
        skeleton = _refuse_skeleton(plan, "no eligible targets")
        return ForecastOutcome(
            plan=plan, skeleton=skeleton, refused=True, stats=_finish(stats, started), warnings=["no_eligible_targets"]
        )

    # Use the max maturity among active targets for a shared cutoff.
    maturity = max((t.maturity_days for t in active), default=0)
    cutoff = compute_cutoff(as_of=plan.as_of, maturity_days=maturity)
    plan = plan.model_copy(update={"cutoff": cutoff})

    # P0: univariate + calendar; pull candidates for trace but do not fetch extras
    # unless an approved bundle lists them.
    target_ids = [t.metric_id for t in active]
    past_cov: list[str] = []
    known_future: list[str] = []
    for t in active:
        cands = candidate_features(t.metric_id, catalogue=deps.catalogue, policies=policies)
        past_cov.extend(t.past_covariates or cands.get("past_covariates") or [])
        known_future.extend(t.known_future or cands.get("known_future") or [])
    for t in active:
        past_cov.extend(_candidate_pool(t, deps, policies))
    past_cov = [m for m in dict.fromkeys(past_cov) if m not in target_ids]
    known_future = list(dict.fromkeys(known_future)) or [
        "calendar.dow", "calendar.festival", "calendar.payday"
    ]

    # Entity slices: account-level (None) or one filter per selected entity value.
    brand_id = principal_brand_id(deps)
    slices: list[tuple[str | None, list[dict[str, Any]] | None]] = [(None, None)]
    if plan.entity_dimension:
        emit_progress(
            mission_id, "agent.stage", f"Selecting {plan.entity_dimension} entities",
            {"stage": "forecast"},
        )
        volume_metric = active[0].metric_id
        selection = await select_entities(
            mcp_client=deps.mcp_client,
            metric_id=volume_metric,
            dimension=plan.entity_dimension,
            as_of=plan.as_of,
            policies=policies,
            requested_values=plan.entity_values or None,
            filters=brand_filters(brand_id),
        )
        warnings.extend(selection.warnings)
        for value, reason in selection.refused:
            warnings.append(f"{value}:{reason}")
        if not selection.values:
            reason = "Q_SPARSE_ENTITY" if selection.refused else "T_ENTITY_EMPTY"
            return ForecastOutcome(
                plan=plan,
                skeleton=_refuse_skeleton(
                    plan,
                    f"{reason}: no entities passed volume gates for {plan.entity_dimension}; "
                    "try the account-level forecast",
                ),
                refused=True,
                stats=_finish(stats, started),
                warnings=warnings or [reason],
            )
        slices = [
            (v, entity_filter(plan.entity_dimension, v)) for v in selection.values
        ]
        plan = plan.model_copy(update={"entity_values": list(selection.values)})
        stats["entities"] = {
            "dimension": plan.entity_dimension,
            "selected": selection.values,
            "refused": selection.refused,
            "account_total": selection.account_total,
        }

    chronos_url = chronos_url or _chronos_url(deps)
    registry = model_registry_from_yaml()
    forecasts: dict[str, TargetForecast] = {}
    engine_meta: dict[str, Any] = {}
    evidence_ids: list[str] = []
    driver_info: dict[str, dict[str, Any]] = {}
    dataset_lines: dict[str, list[str]] = {}
    bakeoff_lines: dict[str, str] = {}
    quality_all: list[dict[str, Any]] = []
    frame_hashes: list[str] = []
    primary_frame = None
    input_id: str | None = None
    backtest_summary: dict[str, Any] = {}

    t0 = time.perf_counter()
    assemble_ms = 0.0
    engines_ms = 0.0
    for entity_value, filters in slices:
        t_assemble = time.perf_counter()
        try:
            frame = await assemble_feature_frame(
                mcp_client=deps.mcp_client,
                target_ids=target_ids,
                past_covariate_ids=past_cov,
                known_future_names=known_future,
                cutoff=cutoff,
                context_days=policies.defaults.context_days,
                horizon_start=plan.horizon.start,
                horizon_end=plan.horizon.end,
                maturity_days=maturity,
                nonnegative_targets={t.metric_id for t in active if t.nonnegative},
                min_history_days=policies.defaults.min_history_days,
                filters=brand_filters(brand_id, filters),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("forecast_assemble_failed entity=%s", entity_value, exc_info=True)
            if entity_value is None and len(slices) == 1:
                return ForecastOutcome(
                    plan=plan,
                    skeleton=_refuse_skeleton(plan, f"assemble failed: {type(exc).__name__}"),
                    refused=True,
                    stats=_finish(stats, started),
                    warnings=["assemble_failed"],
                )
            warnings.append(f"{entity_value or 'account'}:assemble_failed")
            continue
        assemble_ms += (time.perf_counter() - t_assemble) * 1000
        quality_all.extend(v.model_dump() for v in frame.quality_verdicts)
        frame_hashes.append(frame.content_hash)
        if primary_frame is None:
            primary_frame = frame
            input_id = _store_input(deps, frame, plan)
            if input_id:
                evidence_ids.append(input_id)

        if is_blocking(frame.quality_verdicts):
            blocked = [v for v in frame.quality_verdicts if v.action in {"block", "refuse"}]
            reason = "; ".join(
                f"{v.series}:{v.code}" + (f" ({v.detail})" if v.detail else "") for v in blocked
            )
            if entity_value is None and len(slices) == 1:
                return ForecastOutcome(
                    plan=plan,
                    skeleton=_refuse_skeleton(plan, reason),
                    refused=True,
                    stats=_finish(stats, started),
                    warnings=[v.code for v in blocked],
                )
            warnings.append(f"{entity_value}:{reason}")
            for t in active:
                key = _forecast_key(t.metric_id, entity_value)
                forecasts[key] = TargetForecast(
                    metric_id=t.metric_id,
                    label=_entity_label(t.label or t.metric_id, plan.entity_dimension, entity_value),
                    status="refused",
                    entity_dimension=plan.entity_dimension,
                    entity_value=entity_value,
                    reason_codes=[*[v.code for v in blocked], *t.reason_codes],
                )
            continue

        t_eng = time.perf_counter()
        for t in active:
            slice_label = _entity_label(t.label or t.metric_id, plan.entity_dimension, entity_value)
            emit_progress(
                mission_id, "agent.stage", f"Forecasting {slice_label}", {"stage": "forecast"}
            )
            key = _forecast_key(t.metric_id, entity_value)
            ets_id = _ets_model_id(registry, t.metric_id)
            provisional = t.status == "provisional" or t.bundle_id is None
            label_of = lambda m: deps.catalogue.label_for(m) or m  # noqa: E731
            pool = _candidate_pool(t, deps, policies)
            lag_days = max(1, (plan.as_of - plan.cutoff).days)
            dataset_lines[key] = describe_dataset(
                frame, target_id=t.metric_id, label=slice_label,
                candidates=pool, lag_days=lag_days,
            )
            choice = None
            if chronos_url:
                emit_progress(
                    mission_id, "agent.stage", f"Validating features for {slice_label}",
                    {"stage": "forecast"},
                )
                try:
                    bake = await run_bakeoff(
                        frame, t.metric_id, pool, chronos_url=chronos_url,
                        known_names=list(frame.future_covariates),
                    )
                    choice = bake.choice
                    bakeoff_lines[key] = bake.summary_line(label_of)
                    bake_stats = {
                        "chosen": bake.chosen_name,
                        "scores": {k: round(v, 4) for k, v in bake.scores.items()},
                        "origins": bake.n_origins,
                        "seconds": round(bake.seconds, 1),
                    }
                    stats.setdefault("feature_validation", {})[key] = bake_stats
                    if provisional:
                        backtest_summary[key] = {
                            "kind": "provisional_bakeoff",
                            "folds": bake.n_origins,
                            **bake_stats,
                        }
                except ForecastUnavailable as exc:
                    warnings.append(f"{key}:{exc.warning}")
                    _log.info("forecast_bakeoff_skipped metric=%s reason=%s", key, exc.warning)
            try:
                eng, decision = await route_and_forecast(
                    frame,
                    target_id=t.metric_id,
                    preferred_engine=t.engine,
                    chronos_url=chronos_url,
                    ets_model_id=ets_id,
                    nonnegative=t.nonnegative,
                    provisional=provisional,
                    features=choice,
                )
            except ForecastUnavailable as exc:
                forecasts[key] = TargetForecast(
                    metric_id=t.metric_id,
                    label=slice_label,
                    status="refused",
                    entity_dimension=plan.entity_dimension,
                    entity_value=entity_value,
                    reason_codes=[*t.reason_codes, exc.warning],
                )
                warnings.append(f"{key}:{exc.warning}")
                continue

            if pool and eng.engine == "ets":
                try:
                    fetched = await fetch_candidates(
                        deps.mcp_client, pool, frame=frame, as_of=plan.as_of
                    )
                    f_dates = [date.fromisoformat(i[:10]) for i in frame.index]
                    tvals = frame.targets.get(t.metric_id) or []
                    sel = select_driver(
                        tvals, f_dates, fetched, as_of=plan.as_of,
                        lag=max(1, (plan.as_of - plan.cutoff).days),
                    )
                    info: dict[str, Any] = {"selection_line": selection_facts(sel, label_of)}
                    stats.setdefault("feature_selection", {})[key] = {
                        "chosen": sel.chosen,
                        "scores": [
                            {"metric": c.metric_id, "median_error": round(c.median_error, 3),
                             "baseline_error": round(c.baseline_error, 3), "origins": c.origins}
                            for c in sel.scores
                        ],
                        "note": sel.note,
                    }
                    if sel.chosen:
                        adj = apply_driver_level(
                            eng.days, target=tvals, frame_dates=f_dates,
                            driver=fetched[sel.chosen], as_of=plan.as_of,
                        )
                        if adj is not None:
                            eng = replace(eng, days=adj[0])
                            info.update(
                                driver_id=sel.chosen,
                                driver_label=label_of(sel.chosen),
                                **adj[1],
                            )
                    driver_info[key] = info
                except Exception:  # noqa: BLE001
                    _log.warning("forecast_feature_selection_failed metric=%s", key, exc_info=True)
                    warnings.append(f"{key}:feature_selection_failed")

            total_mean, total_p10, total_p90, tw = horizon_totals(
                eng.days,
                total_error_quantiles=_error_quantiles(policies, t.metric_id),
                horizon_key=f"h{len(eng.days)}",
            )
            warnings.extend(tw)
            status = t.status if t.status != "refused" else "provisional"
            if provisional and status == "validated":
                status = "provisional"
                warnings.append(f"{key}:provisional_no_approved_bundle")
            forecasts[key] = TargetForecast(
                metric_id=t.metric_id,
                label=slice_label,
                unit=deps.catalogue.unit_for(t.metric_id),
                date_basis=t.date_basis,
                status=status,  # type: ignore[arg-type]
                entity_dimension=plan.entity_dimension if entity_value else None,
                entity_value=entity_value,
                days=eng.days,
                total_mean=total_mean,
                total_p10=total_p10,
                total_p90=total_p90,
                reason_codes=list(t.reason_codes),
            )
            engine_meta[key] = {
                "engine": eng.engine,
                "model_id": eng.model_id,
                "model_version": eng.model_version,
                "revision": eng.revision,
                "router": decision.reason,
                "tried": decision.tried,
                "inference_seconds": eng.inference_seconds,
                "entity_value": entity_value,
            }
        engines_ms += (time.perf_counter() - t_eng) * 1000

    stats["stages"]["assemble_ms"] = round(assemble_ms)
    stats["stages"]["engines_ms"] = round(engines_ms)
    _ = t0  # started wall for overall latency via _finish

    # Derived recomposition (account keys and per-entity keys)
    entity_vals = sorted({f.entity_value for f in forecasts.values() if f.entity_value})
    for d in derived:
        parts = d.derived_of
        if len(parts) != 2:
            continue
        groups: list[str | None] = [None, *entity_vals] if entity_vals else [None]
        for ent in groups:
            n_key = _forecast_key(parts[0], ent)
            d_key = _forecast_key(parts[1], ent)
            out_key = _forecast_key(d.metric_id, ent)
            if n_key not in forecasts or d_key not in forecasts:
                continue
            num, den = forecasts[n_key], forecasts[d_key]
            if num.status == "refused" or den.status == "refused":
                forecasts[out_key] = TargetForecast(
                    metric_id=d.metric_id,
                    label=_entity_label(d.label or d.metric_id, plan.entity_dimension, ent),
                    status="refused",
                    entity_dimension=plan.entity_dimension if ent else None,
                    entity_value=ent,
                    reason_codes=["T_DERIVED_COMPONENT_REFUSED"],
                )
            else:
                derived_fc = ratio_from_components(
                    num, den, metric_id=d.metric_id,
                    label=_entity_label(d.label or d.metric_id, plan.entity_dimension, ent),
                )
                forecasts[out_key] = derived_fc.model_copy(
                    update={
                        "entity_dimension": plan.entity_dimension if ent else None,
                        "entity_value": ent,
                    }
                )

    if plan.entity_dimension and entity_vals:
        for mid in {t.metric_id for t in active}:
            ent_totals = [
                float(forecasts[k].total_mean)
                for v in entity_vals
                for k in [_forecast_key(mid, v)]
                if k in forecasts and forecasts[k].total_mean is not None and forecasts[k].status != "refused"
            ]
            # Compare entity sum to itself only when we also have an account slice;
            # with entity-only slices, warn if coverage from selection was incomplete.
            mismatch = sum_mismatch_warning(None, ent_totals)
            if mismatch:
                warnings.append(f"{mid}:{mismatch}")

    if not any(f.status != "refused" and f.days for f in forecasts.values()):
        return ForecastOutcome(
            plan=plan,
            skeleton=_refuse_skeleton(plan, "all targets refused"),
            refused=True,
            stats=_finish(stats, started),
            warnings=warnings or ["all_refused"],
        )

    frame = primary_frame
    assert frame is not None
    artifact = _build_artifact(
        plan=plan,
        forecasts=forecasts,
        frame_hash=frame.content_hash,
        input_id=input_id,
        engine_meta=engine_meta,
        features=_feature_roles(active, known_future, past_cov),
        quality=quality_all,
        evidence_ids=evidence_ids,
        warnings=warnings,
        backtest_summary=backtest_summary,
    )
    artifact_id, prediction_ids = _store_artifacts(deps, artifact, forecasts, engine_meta, evidence_ids)
    _store_forecast_finding(deps, forecasts, evidence_ids, artifact_id)
    insights, chart_ids = _insights_and_charts(
        deps, frame, plan, forecasts, evidence_ids, input_id, driver_info,
        dataset_lines, bakeoff_lines,
    )
    skeleton = _answer_skeleton(plan, forecasts, artifact, engine_meta, insights, bool(chart_ids))

    stats.update(
        status="ok",
        targets=list(forecasts),
        engines={k: v.get("engine") for k, v in engine_meta.items()},
        input_hash=frame.content_hash,
        frame_hashes=frame_hashes,
    )
    _log.info("forecast_stats %s", _finish(stats, started))
    _store_trace(
        deps, plan, frame, stats, engine_meta, warnings, artifact_id, quality_all
    )
    return ForecastOutcome(
        plan=plan,
        artifact=artifact,
        artifact_id=artifact_id,
        prediction_ids=prediction_ids,
        skeleton=skeleton,
        stats=_finish(stats, started),
        warnings=warnings,
    )


def _forecast_key(metric_id: str, entity_value: str | None) -> str:
    return f"{metric_id}[{entity_value}]" if entity_value else metric_id


def _entity_label(base: str, dimension: str | None, entity_value: str | None) -> str:
    if not entity_value:
        return base
    dim = dimension or "entity"
    return f"{base} ({dim}={entity_value})"


def _feature_roles(
    targets: list[ForecastPlanTarget], known_future: list[str], past_cov: list[str]
) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for t in targets:
        roles.append(
            {
                "metric_id": t.metric_id,
                "role": "target",
                "bundle_id": t.bundle_id,
                "ablation_gain": None,
            }
        )
        for c in t.co_targets:
            roles.append({"metric_id": c, "role": "co_target", "bundle_id": t.bundle_id})
    for mid in past_cov:
        roles.append({"metric_id": mid, "role": "past_covariate", "bundle_id": None})
    for name in known_future:
        roles.append({"metric_id": name, "role": "known_future", "bundle_id": None})
    return roles


def _build_artifact(
    *,
    plan: ForecastPlan,
    forecasts: dict[str, TargetForecast],
    frame_hash: str,
    input_id: str | None,
    engine_meta: dict[str, Any],
    features: list[dict[str, Any]],
    quality: list[dict[str, Any]],
    evidence_ids: list[str],
    warnings: list[str],
    backtest_summary: dict[str, Any] | None = None,
) -> ForecastArtifact:
    # Prefer first non-refused for top-level engine fields (multi-target in payload.targets)
    primary_key, primary = next(
        ((k, f) for k, f in forecasts.items() if f.days),
        next(iter(forecasts.items())),
    )
    meta = engine_meta.get(primary_key, {}) or engine_meta.get(primary.metric_id, {})
    overall = "provisional"
    if all(f.status == "validated" for f in forecasts.values() if f.days):
        overall = "validated"
    elif any(f.status == "refused" for f in forecasts.values()) and not any(
        f.days for f in forecasts.values()
    ):
        overall = "refused"
    return ForecastArtifact(
        plan_spec=plan.model_dump(mode="json"),
        cutoff=plan.cutoff.isoformat(),
        maturity_cut=plan.cutoff.isoformat(),
        targets=[f.model_dump(mode="json") for f in forecasts.values()],
        entity={"dimension": plan.entity_dimension, "values": plan.entity_values},
        features=features,
        quality_verdicts=quality,
        engine=str(meta.get("engine") or "unknown"),
        model_id=str(meta.get("model_id") or ""),
        model_version=str(meta.get("model_version") or ""),
        model_revision=meta.get("revision"),
        status=overall,  # type: ignore[arg-type]
        warnings=list(warnings),
        input_artifact_id=input_id,
        input_hash=frame_hash,
        evidence_ids=list(evidence_ids) or ["forecast_input"],
        backtest_summary=dict(backtest_summary or {}),
    )


def _error_quantiles(policies: ForecastPolicies, metric_id: str) -> dict[str, list[float]] | None:
    """Relative-error quantiles from a scored bundle (approved, else candidate)."""
    policy = policies.target(metric_id)
    bundle = policy.scored_bundle() if policy else None
    if bundle is None or bundle.backtest is None:
        return None
    return dict(bundle.backtest.total_error_quantiles) or None


def _store_trace(
    deps: Any,
    plan: ForecastPlan,
    frame: Any,
    stats: dict[str, Any],
    engine_meta: dict[str, Any],
    warnings: list[str],
    forecast_id: str | None,
    quality: list[dict[str, Any]],
) -> str | None:
    """Persist stage timings, gate decisions and router choices."""
    try:
        ev = [forecast_id] if forecast_id else [f"forecast_frame:{frame.content_hash}"]
        payload = {
            "cutoff": plan.cutoff.isoformat(),
            "input_hash": frame.content_hash,
            "stages": stats.get("stages") or {},
            "latency_ms": stats.get("latency_ms"),
            "engines": engine_meta,
            "gate_decisions": [g.model_dump() for g in frame.gate_decisions],
            "quality_verdicts": quality,
            "warnings": list(warnings),
            "forecast_artifact_id": forecast_id,
        }
        return deps.artifact_store.put(
            Artifact(
                workspace_id=deps.principal.workspace_id,
                artifact_type="forecast_trace",
                payload=payload,
                classification="derived",
                evidence_ids=ev,
                provenance=ArtifactProvenance(
                    evidence_ids=ev,
                    calculation_version=CALCULATION_VERSION,
                    source_metadata={"input_hash": frame.content_hash},
                ),
                mission_id=deps.mission_id,
            )
        ).id
    except Exception:  # noqa: BLE001
        _log.warning("forecast_trace_store_failed", exc_info=True)
        return None


def _store_input(deps: Any, frame: Any, plan: ForecastPlan) -> str | None:
    try:
        # Synthetic evidence id: the frame itself is the factual source (MCP series
        # queries are recorded inside payload.frame.source_queries).
        source_id = f"forecast_frame:{frame.content_hash}"
        art = deps.artifact_store.put(
            Artifact(
                workspace_id=deps.principal.workspace_id,
                artifact_type="forecast_input",
                payload={
                    "frame": frame.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "content_hash": frame.content_hash,
                },
                classification="factual",
                evidence_ids=[source_id],
                provenance=ArtifactProvenance(
                    evidence_ids=[source_id],
                    query_version="forecast_assembler_v1",
                    source_metadata={"content_hash": frame.content_hash},
                ),
                mission_id=deps.mission_id,
            )
        )
        return art.id
    except Exception:  # noqa: BLE001
        _log.warning("forecast_input_store_failed", exc_info=True)
        return None


def _store_artifacts(
    deps: Any,
    artifact: ForecastArtifact,
    forecasts: dict[str, TargetForecast],
    engine_meta: dict[str, Any],
    evidence_ids: list[str],
) -> tuple[str | None, list[str]]:
    prediction_ids: list[str] = []
    forecast_id: str | None = None
    try:
        ev = list(evidence_ids) or ["forecast_input"]
        stored = deps.artifact_store.put(
            Artifact(
                workspace_id=deps.principal.workspace_id,
                artifact_type="forecast",
                payload=artifact.model_dump(mode="json"),
                classification="derived",
                evidence_ids=ev,
                provenance=ArtifactProvenance(
                    evidence_ids=ev,
                    calculation_version=CALCULATION_VERSION,
                    model_version=f"{artifact.model_id}@{artifact.model_version}",
                ),
                mission_id=deps.mission_id,
            )
        )
        forecast_id = stored.id
    except Exception:  # noqa: BLE001
        _log.warning("forecast_artifact_store_failed", exc_info=True)

    for mid, fc in forecasts.items():
        if not fc.days or fc.total_mean is None:
            continue
        meta = engine_meta.get(mid, {})
        try:
            pred = PredictionArtifact(
                model_id=str(meta.get("model_id") or artifact.model_id or "forecast"),
                model_version=str(meta.get("model_version") or artifact.model_version or "1"),
                prediction_type="forecast",
                value=float(fc.total_mean),
                confidence_interval=(
                    (float(fc.total_p10), float(fc.total_p90))
                    if fc.total_p10 is not None and fc.total_p90 is not None
                    else None
                ),
                evidence_ids=list(evidence_ids) or ["forecast_input"],
                feature_leakage_checked=True,
            )
            art = deps.artifact_store.put(
                Artifact(
                    workspace_id=deps.principal.workspace_id,
                    artifact_type="prediction",
                    payload=pred.model_dump(mode="json"),
                    classification="derived",
                    evidence_ids=list(evidence_ids) or ["forecast_input"],
                    provenance=ArtifactProvenance(
                        evidence_ids=list(evidence_ids) or ["forecast_input"],
                        calculation_version=CALCULATION_VERSION,
                        model_version=f"{pred.model_id}@{pred.model_version}",
                        source_metadata={"metric_id": mid, "forecast_artifact_id": forecast_id},
                    ),
                    mission_id=deps.mission_id,
                )
            )
            prediction_ids.append(art.id)
        except Exception:  # noqa: BLE001
            _log.warning("prediction_artifact_store_failed metric=%s", mid, exc_info=True)
    return forecast_id, prediction_ids


def _store_forecast_finding(
    deps: Any,
    forecasts: dict[str, TargetForecast],
    evidence_ids: list[str],
    forecast_id: str | None,
) -> str | None:
    """Record horizon totals so answer grounding accepts the skeleton figures.

    Live 2026-10-10 (MS3-66cc3bb5f2): the model copied mean/P10/P90 from the
    ANSWER SKELETON into final_response; validation treated them as unbacked
    because only prediction/forecast payloads held them, and those were not
    walked into the mission value pool (fixed there too). A Finding makes the
    same numbers citable as derived metrics.
    """
    metrics: dict[str, float] = {}
    for mid, fc in forecasts.items():
        if fc.status == "refused" or not fc.days:
            continue
        if fc.total_mean is not None:
            metrics[f"{mid}.total_mean"] = float(fc.total_mean)
        if fc.total_p10 is not None:
            metrics[f"{mid}.total_p10"] = float(fc.total_p10)
        if fc.total_p90 is not None:
            metrics[f"{mid}.total_p90"] = float(fc.total_p90)
        for d in fc.days:
            metrics[f"{mid}.{d.date}.p50"] = float(d.p50)
            metrics[f"{mid}.{d.date}.p10"] = float(d.p10)
            metrics[f"{mid}.{d.date}.p90"] = float(d.p90)
    if not metrics:
        return None
    ev = [e for e in evidence_ids if e] or ([forecast_id] if forecast_id else [])
    if not ev:
        ev = ["forecast_input"]
    try:
        finding = Finding(
            finding_type="forecast_horizon",
            statement="Forecast horizon totals (mean / P10 / P90) and daily P50 path",
            evidence_ids=list(ev),
            metrics=metrics,
        )
        return deps.artifact_store.put(
            Artifact(
                workspace_id=deps.principal.workspace_id,
                artifact_type="finding",
                payload=finding.model_dump(mode="json"),
                classification="derived",
                evidence_ids=list(ev),
                provenance=ArtifactProvenance(
                    evidence_ids=list(ev),
                    calculation_version=CALCULATION_VERSION,
                    source_metadata={"forecast_artifact_id": forecast_id},
                ),
                mission_id=deps.mission_id,
            )
        ).id
    except Exception:  # noqa: BLE001
        _log.warning("forecast_finding_store_failed", exc_info=True)
        return None


def _insights_and_charts(
    deps: Any,
    frame: Any,
    plan: ForecastPlan,
    forecasts: dict[str, TargetForecast],
    evidence_ids: list[str],
    input_id: str | None,
    driver_info: dict[str, dict[str, Any]] | None = None,
    dataset_lines: dict[str, list[str]] | None = None,
    bakeoff_lines: dict[str, str] | None = None,
) -> tuple[dict[str, list[str]], list[str]]:
    """Per-target insight lines and one standard ``chart_spec`` per forecast target."""
    from datetime import date as _date

    insights: dict[str, list[str]] = {}
    chart_ids: list[str] = []
    try:
        dates = [_date.fromisoformat(i[:10]) for i in frame.index]
    except Exception:  # noqa: BLE001
        dates = []
    nonneg = {t.metric_id: t.nonnegative for t in plan.targets}
    ev = list(evidence_ids) or ([f"forecast_frame:{frame.content_hash}"] if frame.content_hash else [])
    for key, fc in forecasts.items():
        if fc.status == "refused" or not fc.days:
            continue
        mid = fc.metric_id
        series = frame.targets.get(mid) or []
        pairs = [
            (d, v) for d, v in zip(dates, series, strict=False) if d < plan.horizon.start
        ]
        hist_dates = [d for d, _ in pairs]
        hist = [v for _, v in pairs]
        try:
            facts = build_insight_facts(
                history=hist, history_dates=hist_dates, days=fc.days,
                nonnegative=nonneg.get(mid, True),
            )
            if hist_dates and any(v is not None for v in hist):
                last_obs = max(d for d, v in pairs if v is not None)
                facts["latest_actual_date"] = last_obs.isoformat()
            lines = render_insight_facts(fc.label or mid, facts, unit=fc.unit)
            if facts.get("latest_actual_date"):
                lines.append(f"- latest mature actual used: {facts['latest_actual_date']}")
            lines = [*(dataset_lines or {}).get(key, []), *lines]
            if (bakeoff_lines or {}).get(key):
                lines.append(bakeoff_lines[key])
            di = (driver_info or {}).get(key)
            if di and di.get("selection_line"):
                lines.append(di["selection_line"])
            if di and di.get("driver_label"):
                lines.append(
                    f"- level anchored to {di['driver_label']}: its last {di['run_rate_days']} days "
                    f"averaged {di['driver_run_rate']:,.0f}/day; at the recent ratio of "
                    f"{di['efficiency']:.2f} per unit of it, that implies about "
                    f"{di['implied_daily_mean']:,.0f}/day (a purely statistical path said "
                    f"{di['statistical_daily_mean']:,.0f}/day). The forecast assumes that driver "
                    "continues at its recent pace; if it changes materially, so will the outcome."
                )
            insights[key] = lines
        except Exception:  # noqa: BLE001
            _log.warning("forecast_insights_failed metric=%s", key, exc_info=True)
        try:
            chart_metric = key if fc.entity_value else mid
            spec = build_forecast_chart_spec(
                metric_id=chart_metric, label=fc.label or None, unit=fc.unit,
                history=hist, history_dates=hist_dates, days=fc.days,
            )
            art = deps.artifact_store.put(
                Artifact(
                    workspace_id=deps.principal.workspace_id,
                    artifact_type="chart_spec",
                    payload=spec,
                    classification="derived",
                    evidence_ids=ev,
                    provenance=ArtifactProvenance(
                        evidence_ids=ev, calculation_version=CALCULATION_VERSION
                    ),
                    mission_id=deps.mission_id,
                )
            )
            chart_ids.append(art.id)
        except Exception:  # noqa: BLE001
            _log.warning("forecast_chart_store_failed metric=%s", key, exc_info=True)
    return insights, chart_ids


def _answer_skeleton(
    plan: ForecastPlan,
    forecasts: dict[str, TargetForecast],
    artifact: ForecastArtifact,
    engine_meta: dict[str, Any],
    insights: dict[str, list[str]] | None = None,
    charted: bool = False,
) -> str:
    lines = [
        "ANSWER SKELETON (use only these numbers; do not invent):",
        render_forecast_plan(plan),
        f"status: {artifact.status}",
        f"cutoff: {artifact.cutoff}  input_hash: {artifact.input_hash}",
    ]
    if artifact.warnings:
        lines.append("warnings: " + "; ".join(artifact.warnings))
    for key, fc in forecasts.items():
        meta = engine_meta.get(key, {})
        lines.append(
            f"\n## {fc.label or key} [{fc.status}] engine={meta.get('engine')} "
            f"model={meta.get('model_id')}"
        )
        if fc.status == "refused" or not fc.days:
            lines.append(f"refused: {', '.join(fc.reason_codes) or 'n/a'}")
            continue
        lines.append(
            f"horizon total: mean={fc.total_mean:.4g} "
            f"p10={fc.total_p10:.4g} p90={fc.total_p90:.4g}"
            if fc.total_p10 is not None and fc.total_p90 is not None
            else f"horizon total: mean={fc.total_mean:.4g}"
        )
        if not charted:  # no chart widget: fall back to a compact day list
            lines.append("date | p10 | p50 | p90")
            for d in fc.days[:14]:
                lines.append(f"{d.date} | {d.p10:.4g} | {d.p50:.4g} | {d.p90:.4g}")
        lines.extend((insights or {}).get(key, []))
    if charted:
        lines.append(
            "\nA forecast chart (history, median path, P10-P90 range) is already attached "
            "to the answer with a Table tab: do NOT paste a daily table, do NOT call "
            "generate_visualization for it, and do not list day-by-day values."
        )
    lines.append(
        "\nWRITE THE ANSWER AS AN ANALYST: lead with the headline (expected total and "
        "typical range, how it compares with recent actuals), then what shape to expect "
        "inside the window, what could push it outside the range, and how much weight "
        "to put on it (status/engine, history stability, data freshness). Briefly say how the "
        "data was prepared (window, cleaned outages) and which features were validated and "
        "kept or rejected by backtest, in plain words. Use the insight "
        "facts as evidence for a point of view; explain what they mean for a decision. "
        "Where the facts do not support a driver, say it is unknown rather than guessing. "
        "A forecast artifact and chart are already recorded for this mission — report the "
        "numbers above; do not say a forecast artifact is unavailable or offer to re-run "
        "the same forecast."
    )
    return "\n".join(lines)


def _refuse_skeleton(plan: ForecastPlan, reason: str) -> str:
    return (
        f"ANSWER SKELETON:\n{render_forecast_plan(plan)}\n"
        f"status: refused\nreason: {reason}\n"
        "Do not invent a numeric forecast. Explain the refusal using the reason codes."
    )


def _ets_model_id(registry: Any, metric_id: str) -> str | None:
    bare = metric_id[len("metric.") :] if metric_id.startswith("metric.") else metric_id
    for target in (f"metric.{bare}", bare, metric_id):
        for rec in registry.for_target(target):
            if rec.status == "approved" and rec.model_type == "forecast":
                return rec.model_id
    return None


def _chronos_url(deps: Any) -> str | None:
    settings = getattr(getattr(deps, "context", None), "settings", None)
    if settings is None:
        return None
    url = getattr(settings, "chronos_base_url", "") or ""
    return url.strip() or None


def _finish(stats: dict[str, Any], started: float) -> dict[str, Any]:
    stats["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return stats


def _candidate_pool(t: ForecastPlanTarget, deps: Any, policies: ForecastPolicies) -> list[str]:
    """Candidate features for a target: policy drivers + prior hints, else domain neighbours."""
    from seleric_swarm.forecasting.features import load_feature_priors, module_of

    priors = load_feature_priors()
    bare = lambda m: m.removeprefix("metric.")  # noqa: E731
    pool = [bare(m) for m in [*t.drivers, *priors.metric_hints.get(t.metric_id, [])]]
    if not pool:
        try:
            mod = module_of(t.metric_id, deps.catalogue) or module_of(f"metric.{t.metric_id}", deps.catalogue)
            neighbours = set(priors.drivers.get(mod or "", []))
            for meta in deps.catalogue.metrics:
                if module_of(meta.id, deps.catalogue) in neighbours and module_of(meta.id, deps.catalogue) != "calendar":
                    pool.append(bare(meta.id))
        except Exception:  # noqa: BLE001
            pass
    blocked = set(policies.blocked_features)
    out = [m for m in dict.fromkeys(pool) if m != bare(t.metric_id) and m not in blocked]
    return out[:10]
