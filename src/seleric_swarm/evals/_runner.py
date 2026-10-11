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
    spec = trace.get("spec")
    diagnostics: dict[str, Any] = {"spec_source": "trace.spec" if isinstance(spec, dict) and spec else None}
    for key in ("v2_mission", "v2_shadow"):
        stage = trace.get(key)
        if isinstance(stage, dict):
            diagnostics[key] = {k: stage.get(k) for k in ("status", "reason", "clarification_text", "disagree", "clarification", "plan", "run", "answer")
                                if stage.get(k) is not None}
        elif key == "v2_shadow":
            diagnostics[key] = "absent (UNDERSTAND_V2_MODE/PLANNER_V2_MODE not set in this process?)"
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
        status=str(payload.get("status") or "unknown"),
        spec=spec if isinstance(spec, dict) else {},
        evidence=tuple(evidence),
        response_text=str(payload.get("final_response") or ""),
        revisions=_revisions(trace),
        latency_ms=float(elapsed) * 1000 if isinstance(elapsed, (int, float)) else None,
        diagnostics={**diagnostics, "evidence_metrics": sorted({e.metric_id for e in evidence})[:10]},
        error=str(error_code) if error_code else None,
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
