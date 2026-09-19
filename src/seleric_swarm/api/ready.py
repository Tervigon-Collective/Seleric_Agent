"""Readiness checks for /readyz — dependency surface without secrets."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from sqlalchemy import text

from seleric_swarm.conversations.blobs import (
    ClamAVMalwareScanner,
    MinioBlobStore,
    UnavailableMalwareScanner,
)
from seleric_swarm.runtime import SwarmRuntime


def _bounded(call: Any, timeout_s: float) -> tuple[bool, str | None]:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="readyz")
    future = executor.submit(call)
    try:
        result = future.result(timeout=timeout_s)
        return bool(result is not False), None
    except FutureTimeoutError:
        future.cancel()
        return False, "timeout"
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _database_probe(runtime: SwarmRuntime) -> bool:
    engines: list[Any] = []
    store_engine = getattr(runtime.store, "_engine", None)
    if store_engine is not None:
        engines.append(store_engine)
    repositories = runtime.conversations
    if repositories is not None:
        repository_engine = getattr(repositories.runs, "engine", None)
        if repository_engine is not None and repository_engine not in engines:
            engines.append(repository_engine)
    if not engines:
        return runtime.settings.persistence_backend in {"memory", "file"}
    for engine in engines:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    return True


def _mcp_probe(runtime: SwarmRuntime) -> bool:
    capabilities = runtime.mcp.capabilities
    if not capabilities:
        return False
    capability = "seleric.catalogue_list_metrics"
    if capability not in capabilities:
        return False

    async def call() -> None:
        await asyncio.wait_for(
            runtime.mcp.call(
                agent_id="v3_agent",
                capability=capability,
                arguments={},
            ),
            timeout=runtime.settings.readiness_timeout_s,
        )

    asyncio.run(call())
    return True


def _check(
    checks: dict[str, Any],
    name: str,
    call: Any,
    *,
    timeout_s: float,
    required: bool,
    backend: str,
) -> bool:
    ok, error = _bounded(call, timeout_s)
    checks[name] = {"ok": ok, "required": required, "backend": backend}
    if error:
        checks[name]["error"] = error
    return ok or not required


def check_readiness(runtime: SwarmRuntime, *, timeout_s: float | None = None) -> dict[str, Any]:
    """Run bounded, effective dependency probes and return a secret-free payload."""
    settings = runtime.settings
    timeout = timeout_s or settings.readiness_timeout_s
    production = not settings.is_dev_surface()
    checks: dict[str, Any] = {}
    ready = _check(
        checks,
        "database",
        lambda: _database_probe(runtime),
        timeout_s=timeout,
        required=True,
        backend=settings.persistence_backend,
    )
    checks["store"] = dict(checks["database"])

    redis_clients: list[Any] = []
    cancellation_client = getattr(runtime.cancellation, "_client", None)
    notifier_client = getattr(getattr(runtime.activity_events, "notifier", None), "client", None)
    for client in (cancellation_client, notifier_client):
        if client is not None and client not in redis_clients:
            redis_clients.append(client)
    redis_required = (
        settings.cancellation_backend == "redis"
        or settings.resolved_event_notifier_backend() == "redis"
    )
    ready &= _check(
        checks,
        "redis",
        lambda: bool(redis_clients) and all(client.ping() for client in redis_clients),
        timeout_s=timeout,
        required=redis_required,
        backend="redis" if redis_clients else "memory",
    )

    blob_store = runtime.blob_store
    if isinstance(blob_store, MinioBlobStore):
        blob_probe = lambda: blob_store.client.bucket_exists(blob_store.bucket)
    else:
        blob_probe = lambda: (
            blob_store is not None and getattr(blob_store, "root", None) is not None
        )
    ready &= _check(
        checks,
        "blob",
        blob_probe,
        timeout_s=timeout,
        required=True,
        backend=settings.blob_backend,
    )

    scanner = getattr(blob_store, "scanner", None)
    scanner_probe = (
        scanner.ping
        if isinstance(scanner, ClamAVMalwareScanner)
        else lambda: scanner is not None
        and not isinstance(scanner, UnavailableMalwareScanner)
    )
    ready &= _check(
        checks,
        "scanner",
        scanner_probe,
        timeout_s=timeout,
        required=production,
        backend=settings.malware_scanner_backend,
    )

    queue = runtime.run_queue
    ready &= _check(
        checks,
        "queue",
        lambda: queue is not None and callable(getattr(queue, "enqueue", None)),
        timeout_s=timeout,
        required=True,
        backend=type(queue).__name__ if queue is not None else "unavailable",
    )

    caps = sorted(runtime.mcp.capabilities)
    ready &= _check(
        checks,
        "provider",
        lambda: _mcp_probe(runtime) if production else bool(caps),
        timeout_s=timeout,
        required=production,
        backend="mcp",
    )
    checks["provider"]["capabilities"] = len(caps)
    checks["provider"]["sample"] = caps[:5]
    checks["mcp"] = dict(checks["provider"])

    llm_configured = settings.llm_provider == "fake" or bool(
        settings.azure_openai_endpoint
        and settings.azure_openai_api_key
        and settings.primary_model()
    )
    checks["llm"] = {
        "ok": llm_configured,
        "required": True,
        "backend": settings.llm_provider,
    }
    ready &= llm_configured

    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "app_env": runtime.settings.app_env,
        "swarm_workflow": getattr(runtime.settings, "swarm_workflow", None),
        "checks": checks,
    }


def effective_capabilities(
    runtime: SwarmRuntime, readiness: dict[str, Any] | None = None
) -> dict[str, bool]:
    """Report only capabilities backed by configured, currently healthy adapters."""
    readiness = readiness or check_readiness(runtime)
    checks = readiness["checks"]
    repositories = runtime.conversations
    action_execution = runtime.action_execution
    executors = getattr(action_execution, "executors", {}) if action_execution else {}
    embedding_ready = bool(
        runtime.settings.search_embedding_model
        and runtime.settings.azure_openai_endpoint
        and runtime.settings.azure_openai_api_key
    )
    return {
        "hybrid_search": repositories is not None and bool(checks["database"]["ok"]),
        "vector_search": repositories is not None
        and embedding_ready
        and bool(checks["database"]["ok"]),
        "approval_audit": repositories is not None and bool(checks["database"]["ok"]),
        "scheduled_expiry": runtime.run_queue is not None and bool(checks["queue"]["ok"]),
        "action_execution": bool(executors),
        "write_actions": bool(runtime.settings.allow_write_actions and executors),
        "immutable_audit": repositories is not None and bool(checks["database"]["ok"]),
        "replay_execution": False,
    }
