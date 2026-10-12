"""Running eval cases against the mission pipeline (M0.4).

The runner is the only part of the harness that executes anything. It is
injectable (``run_suite(..., runner=...)``) so the scorer, cassettes and report
can be tested without a live MCP or a real model.

``LegacyMissionRunner`` is the current adapter: it calls the existing
``run_v3_mission`` and maps its ``LookupMissionResult`` payload into the
harness's typed :class:`ObservedCase`. When ``pipeline.run_mission`` (M0.2+)
lands, only this adapter changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from seleric_swarm.config.settings import Settings
    from seleric_swarm.evals._case import EvalCase


class ObservedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: str
    value: Any = None
    unit: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    window: dict[str, Any] = Field(default_factory=dict)


class ObservedCase(BaseModel):
    """The scored output of one case (the final turn)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: str
    question: str = ""
    spec: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[ObservedEvidence, ...] = ()
    response_text: str = ""
    revisions: int = 0
    latency_ms: float | None = None
    llm_calls: int | None = None
    tokens: int | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    # Which path actually produced the answer, and why it left the typed path.
    # ``route`` is never inferred from the mission status: a "completed" mission
    # can have been answered by the legacy loop, so the route is read from the
    # trace (AG-00).
    route: str = "unknown"
    fallback_reason: str | None = None


# Routes an answer can travel (AG-00): the typed v2 path, its shadow (observed
# beside the legacy answer), the business-state snapshot fast path, the legacy
# agent loop, a v2 clarification, or a failed run with no fallback.
_ROUTE_V2 = "v2"
_ROUTE_V2_CLARIFY = "v2_clarify"
_ROUTE_V2_SHADOW = "v2_shadow"
_ROUTE_BUSINESS_STATE = "business_state"
_ROUTE_LEGACY = "legacy"
_ROUTE_FAILED = "failed"
_ROUTE_UNKNOWN = "unknown"


def _route_from_trace(trace: dict[str, Any], status: str) -> tuple[str, str | None]:
    """``(route, fallback_reason)`` for a mission, read from its trace.

    A successful mission status is not evidence that the typed path answered:
    the trace is the only place that says v2 answered, fell back or clarified.
    """
    if isinstance(trace.get("lookup"), str) and trace["lookup"] == "business_state_fast_path":
        return _ROUTE_BUSINESS_STATE, None
    mission = trace.get("v2_mission")
    if isinstance(mission, dict):
        stage = str(mission.get("status") or "")
        reason = str(mission.get("reason") or "") or None
        if stage == "answered":
            return _ROUTE_V2, None
        if stage == "clarify" or mission.get("clarification") is not None:
            return _ROUTE_V2_CLARIFY, None
        if stage == "fallback":
            if status == "failed":
                return _ROUTE_FAILED, reason
            return _ROUTE_LEGACY, reason
    if isinstance(trace.get("v2_shadow"), dict):
        # Legacy answered; the typed path only observed.
        if status == "failed":
            return _ROUTE_FAILED, None
        return _ROUTE_V2_SHADOW, None
    if status == "failed":
        return _ROUTE_FAILED, None
    if trace:
        return _ROUTE_LEGACY, None
    return _ROUTE_UNKNOWN, None


@runtime_checkable
class CaseRunner(Protocol):
    async def __call__(self, case: EvalCase) -> ObservedCase: ...


def _revisions(trace: dict[str, Any]) -> int:
    for container in (trace.get("validation"), trace):
        if isinstance(container, dict):
            for key in ("revisions", "revision_count", "revision_loops"):
                value = container.get(key)
                if isinstance(value, int):
                    return value
    return 0


def _v2_costs(trace: dict[str, Any]) -> tuple[float | None, int | None, int | None]:
    """``(latency_ms, tokens, llm_calls)`` from the v2 stage trace.

    The v2 stages record wall-clock (``latency_ms``) and per-stage token usage; the
    legacy path records none of them. Prefer the enforce mission trace, else shadow.
    """
    stage = trace.get("v2_mission")
    if not isinstance(stage, dict):
        stage = trace.get("v2_shadow")
    if not isinstance(stage, dict):
        return None, None, None
    raw_latency = stage.get("latency_ms")
    latency = float(raw_latency) if isinstance(raw_latency, (int, float)) else None
    understand = stage.get("understand") if isinstance(stage.get("understand"), dict) else {}
    answer = stage.get("answer") if isinstance(stage.get("answer"), dict) else {}
    token_parts: list[float] = []
    for container in (understand, answer):
        for key in ("input_tokens", "output_tokens"):
            value = container.get(key) if isinstance(container, dict) else None
            if isinstance(value, (int, float)):
                token_parts.append(float(value))
    tokens = int(sum(token_parts)) if token_parts else None
    calls = 0
    counted = False
    for container in (understand, answer):
        for key in ("attempt", "attempts"):
            value = container.get(key) if isinstance(container, dict) else None
            if isinstance(value, int):
                calls += value
                counted = True
                break
    return latency, tokens, (calls if counted else None)


def _observed_from_payload(case: EvalCase, question: str, payload: dict[str, Any]) -> ObservedCase:
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    assert isinstance(trace, dict)
    error = payload.get("error")
    error_code = error.get("code") if isinstance(error, dict) else (error or None)

    evidence: list[ObservedEvidence] = []
    for row in payload.get("evidence") or []:
        if not isinstance(row, dict):
            continue
        dimensions = row.get("dimensions")
        window = row.get("time_range")
        evidence.append(
            ObservedEvidence(
                metric_id=str(row.get("metric_or_fact") or row.get("metric_id") or ""),
                value=row.get("value"),
                unit=row.get("unit"),
                dimensions={str(k): str(v) for k, v in (dimensions or {}).items()}
                if isinstance(dimensions, dict)
                else {},
                window=window if isinstance(window, dict) else {},
            )
        )

    elapsed = trace.get("elapsed_seconds")
    v2_latency, v2_tokens, v2_calls = _v2_costs(trace)
    if v2_latency is None and isinstance(elapsed, (int, float)):
        v2_latency = float(elapsed) * 1000
    status = str(payload.get("status") or "unknown")
    route, fallback_reason = _route_from_trace(trace, status)
    spec = trace.get("spec")
    diagnostics: dict[str, Any] = {
        "spec_source": "trace.spec" if isinstance(spec, dict) and spec else None,
        "route": route,
        "fallback_reason": fallback_reason,
    }
    for key in ("v2_mission", "v2_shadow"):
        stage = trace.get(key)
        if isinstance(stage, dict):
            diagnostics[key] = {k: stage.get(k) for k in ("status", "reason", "clarification_text", "disagree", "clarification", "plan", "run", "answer", "coverage", "report_spec")
                                if stage.get(k) is not None}
            if key == "v2_mission" and isinstance(stage.get("coverage"), dict):
                diagnostics["coverage"] = stage["coverage"]
            if key == "v2_mission" and isinstance(stage.get("run"), dict):
                # Surface query_calls for AG-08 scoring even when nested.
                diagnostics.setdefault("query_calls", stage["run"].get("query_calls"))
        elif key == "v2_shadow":
            diagnostics[key] = "absent (UNDERSTAND_V2_MODE/PLANNER_V2_MODE not set in this process?)"
    mission = trace.get("v2_mission")
    if isinstance(mission, dict) and isinstance(mission.get("spec"), dict):
        sp = mission["spec"]
        diagnostics["v2_spec"] = {
            "shape": sp.get("shape"),
            "measures": [m.get("metric_id") for m in sp.get("measures") or []],
            "entities": sp.get("entities"),
            "windows": [f"{w.get('role')} {w.get('start')}..{w.get('end')}" for w in sp.get("windows") or []],
        }
    if not isinstance(spec, dict) or not spec:
        # The legacy path writes no spec; the v2 stages do (enforce first, then shadow).
        for key in ("v2_mission", "v2_shadow"):
            stage = trace.get(key)
            if isinstance(stage, dict) and isinstance(stage.get("spec"), dict):
                spec = stage["spec"]
                diagnostics["spec_source"] = key
                break
    return ObservedCase(
        id=case.id,
        question=question,
        status=status,
        spec=spec if isinstance(spec, dict) else {},
        evidence=tuple(evidence),
        response_text=str(payload.get("final_response") or ""),
        revisions=_revisions(trace),
        latency_ms=v2_latency,
        llm_calls=v2_calls,
        tokens=v2_tokens,
        diagnostics={**diagnostics, "evidence_metrics": sorted({e.metric_id for e in evidence})[:10]},
        error=str(error_code) if error_code else None,
        route=route,
        fallback_reason=fallback_reason,
    )


def install_mcp_client(runtime: Any, client: Any) -> None:
    """Point a built runtime at a different MCP client (cassette injection)."""

    from seleric_swarm.services.catalogue_bootstrap import CatalogueBootstrap
    from seleric_swarm.services.ontology import OntologyService

    runtime.mcp = client
    runtime.bootstrap = CatalogueBootstrap(client)
    runtime.ontology = OntologyService(client)
    runtime.metrics.bind_catalogue(runtime.bootstrap)


def build_mission_runtime(
    *, mcp_client: Any | None = None, settings: Settings | None = None
) -> Any:
    """Build a :class:`SwarmRuntime`, optionally with an injected MCP client."""

    from seleric_swarm.bootstrap import build_runtime

    runtime = build_runtime(settings)
    if mcp_client is not None:
        install_mcp_client(runtime, mcp_client)
    return runtime


class LegacyMissionRunner:
    """Adapter over ``run_v3_mission`` (the pre-M0.2 entry point)."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def __call__(self, case: EvalCase) -> ObservedCase:
        from seleric_swarm.agent.runner import run_v3_mission

        thread_id = f"eval-{case.id}"
        observed: ObservedCase | None = None
        for question in case.turns:
            result = await run_v3_mission(
                self._runtime,
                query=question,
                as_of=case.as_of,
                thread_id=thread_id,
            )
            payload = result.get("result", result) if isinstance(result, dict) else {}
            observed = _observed_from_payload(case, question, payload)
        assert observed is not None
        return observed


def _first_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("%", "").strip())
        except ValueError:
            return None
    return None


def truth_query(args: dict[str, Any]) -> dict[str, Any]:
    """Case truth (``metric_id``/``start``/``end``/``filters``) as ``metrics_query`` arguments."""
    if "measures" in args:
        return dict(args)
    out: dict[str, Any] = {k: v for k, v in args.items() if k not in ("metric_id", "start", "end")}
    if args.get("metric_id"):
        out["measures"] = [args["metric_id"]]
    if args.get("start") or args.get("end"):
        out["time_range"] = {"start": args.get("start") or args.get("end"), "end": args.get("end") or args.get("start")}
    return out


class McpTruthResolver:
    """Live truth lookup: run the case's ``metrics_query`` args and read the value.

    Used by the Jenkins (live) suite only. Best-effort — a truth query that
    fails returns ``None`` and the case's value metric is reported as ``n/a``
    rather than silently scored wrong.
    """

    def __init__(self, mcp: Any, *, agent_id: str = "v3_agent") -> None:
        self._mcp = mcp
        self._agent_id = agent_id

    async def __call__(self, args: dict[str, Any]) -> float | None:
        try:
            response = await self._mcp.call(
                agent_id=self._agent_id,
                capability="seleric.metrics_query",
                arguments=truth_query(args),
            )
        except Exception:
            return None
        if not isinstance(response, dict):
            return None
        rows = response.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        if isinstance(row, dict):
            metric = args.get("metric_id") or next(iter(args.get("measures") or []), None)
            for key in (metric, "value", "metric_value", "result"):
                if key is None:
                    continue
                number = _first_number(row.get(key))
                if number is not None:
                    return number
        return _first_number(row)
