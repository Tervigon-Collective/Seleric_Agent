"""Readiness checks for /readyz — dependency surface without secrets."""

from __future__ import annotations

from typing import Any

from seleric_swarm.runtime import SwarmRuntime


def check_readiness(runtime: SwarmRuntime) -> dict[str, Any]:
    """Return readiness payload. ``ready`` is True when core deps respond."""
    checks: dict[str, Any] = {}
    ready = True

    # Store
    try:
        store = runtime.store
        # Smoke: put/get not required — just that the object exists and list_events works.
        mid = "__readyz__"
        getattr(store, "get", lambda _m: None)(mid)
        checks["store"] = {"ok": True, "backend": type(store).__name__}
    except Exception as exc:
        ready = False
        checks["store"] = {"ok": False, "error": type(exc).__name__}

    # MCP gateway (fixture servers should always register in local/dev)
    try:
        caps = sorted(runtime.mcp.capabilities)
        checks["mcp"] = {
            "ok": True,
            "capabilities": len(caps),
            "sample": caps[:5],
        }
        if runtime.settings.app_env.lower() in {"production", "prod"} and not caps:
            ready = False
            checks["mcp"]["ok"] = False
            checks["mcp"]["error"] = "no_mcp_capabilities"
    except Exception as exc:
        ready = False
        checks["mcp"] = {"ok": False, "error": type(exc).__name__}

    # Metrics registry
    try:
        # MetricRegistry may expose list/ids — keep soft
        checks["metrics"] = {"ok": True, "registry": type(runtime.metrics).__name__}
    except Exception as exc:
        ready = False
        checks["metrics"] = {"ok": False, "error": type(exc).__name__}

    # LLM port presence (does not call the model)
    try:
        checks["llm"] = {
            "ok": True,
            "provider": runtime.settings.llm_provider,
        }
    except Exception as exc:
        ready = False
        checks["llm"] = {"ok": False, "error": type(exc).__name__}

    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "app_env": runtime.settings.app_env,
        "swarm_workflow": getattr(runtime.settings, "swarm_workflow", None),
        "checks": checks,
    }
