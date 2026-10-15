"""Offline runner (plan P0.7): run the real diagnosis tool against a case's world, no network and no LLM.

The world is the fixture with the case's injections applied. ``WorldMcp`` answers the two capabilities the tool
uses (``catalogue_get_metrics``, ``metrics_query``) from it, in the shape the live semantic layer returns, so
``toolsets/diagnosis.py::diagnose_metric_change`` plans, fetches and analyses exactly as in production. The catalogue
the tool plans over is the fixture's metric set (the graph-chosen subset), not the full catalogue: scores say how the
engine does inside that universe.

A result keeps the tool's structured report, its summary and every query it issued. ``tree_state()`` reports the
git state of the engine so a score is never filed against code that differs from the recorded commit.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

import case_format as C
import inject
import world_fixture as W

GRAPH_PATH = ROOT / "src" / "seleric_swarm" / "causal" / "data" / "dependency_graph.json"
ENGINE_PATHS = ("src/seleric_swarm/causal", "src/seleric_swarm/toolsets/diagnosis.py", "src/seleric_swarm/toolsets/policy_config.py")
SCOPE_DIMENSION = "brand_id"


class WorldMcp:
    """Minimal semantic layer over an injected world."""

    def __init__(self, world: inject.InjectedWorld, definitions: dict[str, Any]) -> None:
        self.world = world
        self.definitions = definitions
        self.queries: list[dict[str, Any]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        if capability == "seleric.catalogue_get_metrics":
            return {"metrics": {m: self.definitions[m] for m in arguments["metric_ids"] if m in self.definitions}}
        if capability != "seleric.metrics_query":
            raise AssertionError(f"unexpected capability {capability}")
        self.queries.append(arguments)
        return self._query(arguments)

    def _query(self, args: dict[str, Any]) -> dict[str, Any]:
        measures = list(args["measures"])
        start, end = (date.fromisoformat(args["time_range"][k]) for k in ("start", "end"))
        dims = list(args.get("dimensions") or [])
        eq = {f["dimension"]: [str(v) for v in f["values"]] for f in args.get("filters") or []
              if f.get("operator") == "equals" and f["dimension"] != SCOPE_DIMENSION}
        if len(dims) > 1 or len(eq) > 1 or (dims and eq):
            return {"error": "eval world serves one dimension or one equals filter per query", "rows": [], "provenance": {}}
        rows: dict[tuple[date, str], dict[str, Any]] = {}
        for m in measures:
            if m not in self.world.series:
                return {"error": f"Metric '{m}' is not in the eval world", "rows": [], "provenance": {}}
            if dims:
                by_seg = self.world.segments.get(m, {}).get(dims[0])
                if by_seg is None:
                    return {"error": f"{m} cannot be split by {dims[0]} in the eval world", "rows": [], "provenance": {}}
                source = [(seg, s) for seg, s in by_seg.items()]
            elif eq:
                (dim, values), = eq.items()
                by_seg = self.world.segments.get(m, {}).get(dim)
                if by_seg is None:
                    return {"error": f"{m} cannot be filtered by {dim} in the eval world", "rows": [], "provenance": {}}
                pooled: dict[date, float] = {}
                for v in values:
                    for d, x in by_seg.get(v, {}).items():
                        pooled[d] = pooled.get(d, 0.0) + x
                source = [("", pooled)]
            else:
                source = [("", self.world.series[m])]
            for seg, series in source:
                for d, v in series.items():
                    if start <= d <= end:
                        row = rows.setdefault((d, seg), {"x.day": f"{d.isoformat()}T00:00:00.000"})
                        if dims:
                            row[dims[0]] = seg
                        row[m] = v
        ordered = [rows[k] for k in sorted(rows)]
        return {"columns": [*measures], "rows": ordered, "warnings": [], "provenance": {"query_id": f"q{len(self.queries)}"}}


@dataclass
class RunResult:
    case_id: str
    success: bool
    summary: str
    report: dict[str, Any]
    queries: int
    planted: list[inject.Planted] = field(default_factory=list)
    error: str = ""


def tree_state() -> dict[str, Any]:
    """HEAD and dirty engine paths. A tree exported with ``git archive <commit>`` has no .git: it carries ``.eval_commit``
    (the commit it was exported from) and is clean by construction, which is how a score is made independent of a shared
    working tree that teammates edit."""
    marker = ROOT / ".eval_commit"
    if not (ROOT / ".git").exists() and marker.exists():
        return {"head": marker.read_text().strip(), "engine_dirty": [], "exported": True}

    def git(*a: str) -> str:
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, check=True).stdout.strip()

    return {"head": git("rev-parse", "HEAD"), "engine_dirty": git("status", "--porcelain", "--", *ENGINE_PATHS).splitlines()}


def build_world(case: C.Case) -> tuple[W.Fixture, inject.InjectedWorld]:
    fx = W.load_fixture(case.world.fixture, pinned_sha256=case.world.sha256)
    graph = json.loads(GRAPH_PATH.read_text())
    return fx, inject.apply_injections(fx, case.injections, graph)


def check_reaches_outcome(case: C.Case, world: inject.InjectedWorld) -> None:
    """A planted real cause must reach the outcome through the graph; otherwise the case tests nothing."""
    if case.kind == "injection" and case.truth.cause_class == "findable" and case.request.outcome not in world.changed:
        raise inject.InjectionError(f"{case.id}: the injection never reaches the outcome {case.request.outcome!r}; add the effects that connect them")


def run_case(case: C.Case) -> RunResult:
    from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
    from seleric_swarm.conversations.contracts import ContextBundle, Principal
    from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
    from seleric_swarm.state.artifacts import InMemoryArtifactStore
    from seleric_swarm.toolsets import diagnosis

    fx, world = build_world(case)
    check_reaches_outcome(case, world)
    dims_of = {m: [SCOPE_DIMENSION, *sorted(world.segments.get(m, {}))] for m in fx.definitions}
    metrics = tuple(
        CatalogueMetricMeta(id=m, label=str(d.get("display_name") or m), view=str((d.get("cube_mapping") or {}).get("view") or ""),
                            supported_dimensions=dims_of[m], raw=d)
        for m, d in sorted(fx.definitions.items()) if m in world.series
    )
    all_dims = tuple(sorted({x for v in dims_of.values() for x in v}))
    mcp = WorldMcp(world, fx.definitions)
    rq = case.request
    deps = SelericDeps(
        mission_id=f"eval-{case.id}", as_of=datetime.combine(max(rq.event_days) + timedelta(days=1), datetime.min.time(), tzinfo=UTC).replace(hour=12),
        principal=Principal(principal_id="eval", workspace_id="eval", user_id="eval"), thread_id="t", run_id="r", trace_id="tr",
        context=ContextBundle(), mcp_client=mcp, artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=metrics, dimensions=all_dims),
    )

    class Ctx:
        def __init__(self) -> None:
            self.deps = deps

    def at(d: date) -> datetime:
        return datetime.combine(d, datetime.min.time(), tzinfo=UTC)

    diagnosis._definitions_cache.update({"at": 0.0, "ids": frozenset(), "defs": {}})
    kwargs: dict[str, Any] = {"event_start": at(min(rq.event_days)), "event_end": at(max(rq.event_days)),
                              "claimed_direction": rq.claimed_direction, "filters": dict(rq.scope) or None}
    if rq.baseline_days:
        kwargs.update(compare_start=at(min(rq.baseline_days)), compare_end=at(max(rq.baseline_days)))
    try:
        res = asyncio.run(diagnosis.diagnose_metric_change(Ctx(), rq.outcome, **kwargs))  # type: ignore[arg-type]
    except Exception as e:  # a crash is a result: the scorer counts it, never hides it
        return RunResult(case.id, False, "", {}, len(mcp.queries), world.planted, f"{type(e).__name__}: {e}")
    report = dict((res.provenance.source_metadata or {}).get("diagnosis") or {}) if res.success else {}
    return RunResult(case.id, bool(res.success), str(res.summary), report, len(mcp.queries), world.planted)
