"""Monitoring panel API: what is watched per domain, how, and what the latest snapshot says.

``GET  /v1/monitoring/state``              latest snapshot per domain (what the Ops tab draws)
``GET  /v1/monitoring/config``             effective configuration per domain
``GET  /v1/monitoring/catalogue/{domain}`` metrics a user can add to a domain
``PUT  /v1/monitoring/config/{domain}``    save the selection and alert rules (validated, versioned)
``DELETE /v1/monitoring/config/{domain}``  back to the shipped defaults
``POST /v1/monitoring/refresh/{domain}``   resolve one domain now instead of waiting for the cron
``GET  /v1/monitoring/targets``            target monitoring: open findings, latest value per metric against its band
``GET  /v1/monitoring/live/schema``        live ads monitor: rules, params schemas, actions, fields (drives the form)
``GET|PUT|DELETE /v1/monitoring/live/config``  live ads monitor configuration (validated, versioned, 409 on conflict)
``POST /v1/monitoring/live/config/dry-run`` what an unsaved configuration would flag on the last snapshot
``GET  /v1/monitoring/live/baselines``     per-entity baselines, guardrails and the tracking reference

Everything is under ``/v1`` so the API-key middleware applies. Reads and writes need an authenticated principal.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from seleric_swarm.api.mission_access import request_principal
from seleric_swarm.services.domain_health.monitor_config import (
    MAX_METRICS_PER_DOMAIN,
    ConfigError,
    OverrideStore,
    VersionConflict,
    candidates,
    validate_domain,
)
from seleric_swarm.services.domain_health.resolver import DomainHealthProfiles, DomainStateResolver
from seleric_swarm.services.domain_health.scheduler import DEFAULT_MIN_INTERVAL_S, run_once
from seleric_swarm.services.domain_health.snapshot_store import SnapshotStore

router = APIRouter(prefix="/v1/monitoring")

_REFRESH_COOLDOWN_S = 20.0  # every metric is an MCP query; a hammered button must not become a load test
_locks: dict[str, asyncio.Lock] = {}
_last_refresh: dict[str, float] = {}


def _runtime(request: Request) -> Any:
    provider = getattr(request.app.state, "runtime_provider", None)
    if not callable(provider):
        raise HTTPException(status_code=503, detail="runtime is not configured")
    return provider()


def _profiles() -> tuple[DomainHealthProfiles, SnapshotStore]:
    store = SnapshotStore()
    return DomainHealthProfiles(overrides=OverrideStore(store.base_dir)), store


def _domain_or_404(profiles: DomainHealthProfiles, domain: str) -> None:
    if domain not in profiles.domain_ids():
        raise HTTPException(status_code=404, detail=f"unknown domain {domain}")


async def _registry(runtime: Any) -> Any:
    bootstrap = getattr(runtime, "bootstrap", None)
    if bootstrap is not None:
        try:
            await bootstrap.refresh_if_stale()
        except Exception:  # noqa: S110 - a cold catalogue is reported by the caller as "not loaded"
            pass
    return runtime.metrics


def _interval() -> float:
    return float(os.getenv("BUSINESS_STATE_MIN_INTERVAL_S", DEFAULT_MIN_INTERVAL_S) or DEFAULT_MIN_INTERVAL_S)


def _metric_view(m: Any) -> dict[str, Any]:
    return {
        "metric_id": m.metric_id, "name": m.name, "label": m.label, "unit": m.unit, "display": m.display, "shape": m.shape,
        "value": m.value, "observed_on": m.observed_on, "status": m.status, "polarity_known": m.polarity_known, "direction_bad": m.direction_bad,
        "rule": m.rule, "series": m.series, "judgement": m.judgement, "freshness": m.freshness,
        "quality_flags": list(m.quality_flags),
    }


def _config_view(profiles: DomainHealthProfiles, domain: str) -> dict[str, Any]:
    block = profiles.get(domain)
    in_headline = set(profiles.headline_metrics())
    return {
        "id": domain,
        "title": block.get("title") or domain,
        "description": block.get("description") or "",
        "enabled": block.get("enabled", True),
        "custom": block.get("custom", False),
        "version": block.get("version", 0),
        "updated_at": block.get("updated_at"),
        "updated_by": block.get("updated_by"),
        "catalogue_domains": block.get("catalogue_domains") or [domain],
        "metrics": [
            {
                "metric_id": e["metric_id"], "catalogue_metric": e.get("catalogue_metric") or e["metric_id"],
                "label": e.get("label"), "alert": e.get("alert"), "direction_bad": e.get("direction_bad"),
                "headline": e["metric_id"] in in_headline,
                "filters": e.get("filters") or {},
            }
            for e in block.get("metrics", [])
        ],
    }


async def _state_for(profiles: DomainHealthProfiles, store: SnapshotStore, domain: str) -> dict[str, Any]:
    config = _config_view(profiles, domain)
    snapshot = await store.aget_latest(domain)
    out: dict[str, Any] = {**config, "snapshot": None, "pending": False, "counts": {}}
    if snapshot is None or not snapshot.metrics:
        out["pending"] = bool(config["metrics"]) and config["enabled"]
        return out
    computed = datetime.fromisoformat(snapshot.computed_at)
    changed = profiles.override_updated_at(domain)
    selected = {e["metric_id"] for e in config["metrics"]}
    present = {m.metric_id for m in snapshot.metrics}
    # Pending: the user changed the selection after this snapshot, so what is drawn may not be what is selected.
    out["pending"] = bool(changed and datetime.fromisoformat(changed) > computed) or selected != present
    age = (datetime.now(UTC) - computed).total_seconds()
    metrics = [_metric_view(m) for m in snapshot.metrics if m.metric_id in selected or not out["pending"]]
    counts: dict[str, int] = {}
    for m in metrics:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    out["snapshot"] = {
        "status": snapshot.status, "as_of": snapshot.as_of, "computed_at": snapshot.computed_at,
        "age_s": age, "stale": age > 3 * _interval(), "window": snapshot.window,
        "signals": snapshot.headline_signals, "metrics": metrics,
    }
    out["counts"] = counts
    return out


@router.get("/state")
async def state(request: Request) -> dict[str, Any]:
    request_principal(request)
    profiles, store = _profiles()
    domains = [await _state_for(profiles, store, d) for d in profiles.domain_ids()]
    return {"interval_s": _interval(), "max_metrics_per_domain": MAX_METRICS_PER_DOMAIN, "domains": domains}


@router.get("/targets")
async def targets(request: Request, brand_id: int | None = None) -> dict[str, Any]:
    """Findings and latest evaluations from the ``monitor`` schema (Postgres), written hourly by the monitoring runner."""
    request_principal(request)
    from sqlalchemy import create_engine

    from seleric_swarm.config.settings import get_settings
    from seleric_swarm.exploration.space import default_brand_id
    from seleric_swarm.services.monitoring.read import read_state

    brand = brand_id if brand_id is not None else int(default_brand_id())

    def _read() -> dict[str, Any]:
        engine = create_engine(get_settings().database_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                return read_state(conn, brand)
        finally:
            engine.dispose()

    return {"brand_id": brand, **await asyncio.to_thread(_read)}


@router.get("/config")
async def config(request: Request) -> dict[str, Any]:
    request_principal(request)
    profiles, _ = _profiles()
    return {"max_metrics_per_domain": MAX_METRICS_PER_DOMAIN, "domains": [_config_view(profiles, d) for d in profiles.domain_ids()]}


@router.get("/catalogue/{domain}")
async def catalogue(domain: str, request: Request) -> dict[str, Any]:
    request_principal(request)
    profiles, _ = _profiles()
    _domain_or_404(profiles, domain)
    registry = await _registry(_runtime(request))
    block = profiles.get(domain)
    return {"domain": domain, "metrics": candidates(registry, block.get("catalogue_domains") or [domain])}


@router.put("/config/{domain}")
async def save_config(domain: str, request: Request) -> dict[str, Any]:
    principal = request_principal(request)
    profiles, store = _profiles()
    _domain_or_404(profiles, domain)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body must be JSON") from exc
    expected = body.pop("expected_version", None) if isinstance(body, dict) else None
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
        raise HTTPException(status_code=422, detail="expected_version must be an integer")
    registry = await _registry(_runtime(request))
    try:
        entry = validate_domain(domain, body, registry=registry, defaults=profiles.defaults(domain).get("metrics", []))
        OverrideStore(store.base_dir).save(domain, entry, expected_version=expected, updated_by=principal.user_id)
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    profiles, store = _profiles()
    return await _state_for(profiles, store, domain)


@router.delete("/config/{domain}")
async def reset_config(domain: str, request: Request) -> dict[str, Any]:
    request_principal(request)
    profiles, store = _profiles()
    _domain_or_404(profiles, domain)
    OverrideStore(store.base_dir).reset(domain)
    profiles, store = _profiles()
    return await _state_for(profiles, store, domain)


# ------------------------------------------------------------------------------------------- live ads monitor

def _live_view() -> dict[str, Any]:
    from seleric_swarm.services.live_monitor import config as live

    cfg, meta = live.load()
    return {"config": cfg.model_dump(mode="json"), "defaults": live.defaults(), **meta}


async def _live_body(request: Request) -> tuple[dict[str, Any], int | None]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body must be JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("config"), dict):
        raise HTTPException(status_code=422, detail="body must be {config, expected_version}")
    expected = body.get("expected_version")
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
        raise HTTPException(status_code=422, detail="expected_version must be an integer")
    return body["config"], expected


@router.get("/live/schema")
async def live_schema(request: Request) -> dict[str, Any]:
    request_principal(request)
    from seleric_swarm.services.live_monitor.core import schema

    return schema.build()


@router.get("/live/config")
async def live_config(request: Request) -> dict[str, Any]:
    request_principal(request)
    return await asyncio.to_thread(_live_view)


@router.put("/live/config")
async def save_live_config(request: Request) -> dict[str, Any]:
    principal = request_principal(request)
    from seleric_swarm.services.live_monitor import config as live

    payload, expected = await _live_body(request)
    try:
        await asyncio.to_thread(lambda: live.save(payload, expected_version=expected, updated_by=principal.user_id))
    except VersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(_live_view)


@router.delete("/live/config")
async def reset_live_config(request: Request) -> dict[str, Any]:
    request_principal(request)
    from seleric_swarm.services.live_monitor import config as live

    await asyncio.to_thread(live.reset)
    return await asyncio.to_thread(_live_view)


@router.post("/live/config/dry-run")
async def dry_run_live_config(request: Request) -> dict[str, Any]:
    """What the given (unsaved) configuration would flag on the last snapshot. Nothing is written."""
    request_principal(request)
    from seleric_swarm.services.live_monitor import config as live
    from seleric_swarm.services.live_monitor.runner import evaluate_stored

    payload, _ = await _live_body(request)
    try:
        cfg = live.validate(payload)
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tenants = [t for t in cfg.tenants if t.enabled]
    return {"tenants": [await asyncio.to_thread(evaluate_stored, cfg, t) for t in tenants]}


@router.get("/live/baselines")
async def live_baselines(request: Request, tenant_id: str, level: str | None = None, entity_id: str | None = None) -> dict[str, Any]:
    request_principal(request)
    from seleric_swarm.services.live_monitor import config as live
    from seleric_swarm.services.live_monitor import guardrails, store

    def _q() -> dict[str, Any]:
        cfg, _ = live.load()
        if cfg.tenant(tenant_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
        with store.engine().connect() as conn:
            return {
                "tenant_id": tenant_id, "rows": store.baselines_view(conn, tenant_id, level, entity_id),
                "guardrails": guardrails.effective(cfg.guardrails, store.get_state(conn, tenant_id, "guardrails")).to_json(),
                "tracking": store.get_state(conn, tenant_id, "tracking"), "run": store.get_state(conn, tenant_id, "baselines_meta"),
                "defaults": cfg.baselines.model_dump(mode="json")["defaults"],
            }

    return await asyncio.to_thread(_q)


@router.post("/refresh/{domain}")
async def refresh(domain: str, request: Request) -> dict[str, Any]:
    request_principal(request)
    profiles, store = _profiles()
    _domain_or_404(profiles, domain)
    runtime = _runtime(request)
    if getattr(runtime, "business_state", None) is None:
        raise HTTPException(status_code=503, detail="business state service is not available")
    if not profiles.get(domain).get("enabled", True):
        raise HTTPException(status_code=409, detail=f"{domain} is switched off")
    lock = _locks.setdefault(domain, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail=f"{domain} is already refreshing")
    wait = _REFRESH_COOLDOWN_S - (time.monotonic() - _last_refresh.get(domain, -1e9))
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"refreshed a moment ago; retry in {int(wait) + 1}s", headers={"Retry-After": str(int(wait) + 1)})
    async with lock:
        _last_refresh[domain] = time.monotonic()
        resolver = DomainStateResolver(runtime.business_state, profiles)
        snapshots = await run_once(resolver, store, domains=[domain])
    if not any(s.domain == domain for s in snapshots):
        raise HTTPException(status_code=502, detail=f"could not resolve {domain}; the last good snapshot is kept")
    return await _state_for(profiles, store, domain)
