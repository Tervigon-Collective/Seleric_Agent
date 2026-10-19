"""Deterministic plan execution, context compaction and figure provenance (2026-10-07)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from seleric_swarm.agent.artifacts import EvidenceArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.config.settings import helper_chat_model
from seleric_swarm.conversations.contracts import (
    Artifact,
    ArtifactProvenance,
    ContextBundle,
    Principal,
)
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime(2026, 10, 7, tzinfo=IST)


def _catalogue() -> CatalogueSnapshot:
    return CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="spend", label="Spend", supported_dimensions=["camp"], raw={"aggregation": "additive"}),
            CatalogueMetricMeta(
                id="ret", label="Return", supported_dimensions=["camp"], raw={"aggregation": "ratio", "volume_metric": "spend"}
            ),
        ),
        dimensions=("camp",),
    )


def _deps(mcp=None, store=None) -> SelericDeps:
    return SelericDeps(
        mission_id="MS3-x",
        as_of=TODAY,
        principal=Principal(principal_id="p", workspace_id="w", user_id="u"),
        thread_id="t",
        run_id="r",
        trace_id="tr",
        context=ContextBundle(),
        mcp_client=mcp,
        artifact_store=store or InMemoryArtifactStore(),
        limits=ExecutionLimits(),
        catalogue=_catalogue(),
    )


class _Mcp:
    """Answers like Cube: hourly rows for granularity=hour, else one row per campaign."""

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        measure = arguments["measures"][0]
        start = arguments["time_range"]["start"]
        days = 3 if start == "2026-10-04" else 1
        camps = ["A", "B", "C"]
        for f in arguments.get("filters") or []:
            if f["dimension"] == "camp" and f["operator"] == "equals":
                camps = f["values"]
        if arguments.get("granularity") == "hour":
            rows = []
            for d in range(days):
                day = f"2026-10-0{4 + d if days == 3 else 7}"
                for hour in range(24):
                    rows += [{"camp": c, "x.hour": f"{day}T{hour:02d}:00:00.000", measure: "10"} for c in camps]
            return {"rows": rows, "provenance": {"query_id": "q"}}
        value = {"A": 3.0, "B": 2.0, "C": 1.0}
        rows = [{"camp": c, measure: str(value[c] if measure == "ret" else 100.0)} for c in camps]
        if arguments.get("sort"):
            rows.sort(key=lambda r: float(r[measure]), reverse=True)
        return {"rows": rows[: arguments.get("limit") or None], "provenance": {"query_id": "q"}}






class _RecordingMcp:
    def __init__(self) -> None:
        self.args: list[dict] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.args.append(arguments)
        measure = arguments["measures"][0]
        if arguments.get("dimensions"):
            rows = [{"camp": c, measure: "5"} for c in ("A", "B")]
        else:
            rows = [{measure: "7"}]
        return {"rows": rows, "provenance": {"query_id": "q"}}














def _evidence(store, value: float) -> str:
    payload = EvidenceArtifact(
        metric_id="spend", grain="none", as_of=TODAY, period_start=TODAY, period_end=TODAY, value=value, source_query={}
    ).model_dump(mode="json")
    return store.put(
        Artifact(workspace_id="w", artifact_type="evidence", payload=payload, classification="factual",
                 evidence_ids=["raw"], provenance=ArtifactProvenance(), mission_id="MS3-x")
    ).id




def test_each_role_resolves_its_own_deployment() -> None:
    from types import SimpleNamespace

    assert helper_chat_model(SimpleNamespace(azure_openai_helper_model="h", azure_openai_fast_model="f")) == "h"
    assert helper_chat_model(SimpleNamespace(azure_openai_helper_model="", azure_openai_fast_model="f")) == "f"
    assert helper_chat_model(SimpleNamespace(azure_openai_helper_model="", azure_openai_fast_model="")) == ""


def test_the_planner_gets_a_compact_catalogue() -> None:
    compact = _catalogue().render_compact()
    assert "- spend: Spend" in compact and "Dimensions: camp" in compact
    assert len(compact) < len(_catalogue().render())


class _WindowMcp:
    """One total per window: 300 over Sep 1-3, 450 over Oct 1-3."""

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        measure = arguments["measures"][0]
        value = 450.0 if arguments["time_range"]["start"].startswith("2026-10") else 300.0
        return {"rows": [{measure: str(value)}], "provenance": {"query_id": "q"}}




class _FilterMcp:
    """Records every query; no hourly series for the daily-only metric 'np'."""

    def __init__(self) -> None:
        self.args: list[dict] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict) -> dict:
        self.args.append(arguments)
        measure = arguments["measures"][0]
        if arguments.get("granularity") == "hour":
            if measure == "np":
                return {"error": "no hourly series"}
            day = arguments["time_range"]["start"][:8]
            rows = [{"x.hour": f"{day}{d:02d}T{h:02d}:00:00.000", measure: "1"} for d in range(1, 4) for h in range(24)]
            return {"rows": rows, "provenance": {"query_id": "q"}}
        return {"rows": [{measure: "30"}], "provenance": {"query_id": "q"}}






def dataclasses_replace_catalogue(deps: SelericDeps) -> SelericDeps:
    import dataclasses

    catalogue = CatalogueSnapshot(
        metrics=(
            CatalogueMetricMeta(id="spend", label="Spend", supported_dimensions=["camp", "plat"], raw={"aggregation": "additive"}),
            CatalogueMetricMeta(id="np", label="Profit", supported_dimensions=["camp", "plat"], raw={"aggregation": "additive"}),
        ),
        dimensions=("camp", "plat"),
    )
    return dataclasses.replace(deps, catalogue=catalogue)



