"""toolsets/exploration.py against a fake catalogue + Cube.

Invented metric/dimension/view names: the tool must plan everything from
catalogue metadata (lineage hub score, supported dimensions, scope keys).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from seleric_swarm.agent.artifacts import EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import diagnosis, exploration

AS_OF = datetime(2026, 10, 7, 12, tzinfo=UTC)
YESTERDAY = date(2026, 10, 6)
DAYS = [YESTERDAY - timedelta(days=i) for i in range(70, -2, -1)]  # includes a partial "today"
WINDOW = [YESTERDAY - timedelta(days=i) for i in range(6, -1, -1)]

DEFS: dict[str, dict[str, Any]] = {
    "yy_takings": {"aggregation": "additive", "grain": "sale", "cube_mapping": {"view": "till"}, "formula": {"depends_on": []}},
    "yy_baskets": {"aggregation": "additive", "grain": "sale", "cube_mapping": {"view": "till"}, "formula": {"depends_on": []}},
    "yy_basket_size": {"aggregation": "ratio", "grain": "sale", "cube_mapping": {"view": "till"},
                       "formula": {"depends_on": ["yy_takings", "yy_baskets"]}},
    "yy_footfall": {"aggregation": "additive", "grain": "entry", "cube_mapping": {"view": "door"}, "formula": {"depends_on": []}},
    "yy_conversion": {"aggregation": "ratio", "grain": "entry", "cube_mapping": {"view": "door"},
                      "formula": {"depends_on": ["yy_baskets", "yy_footfall"]}},
    # a view without the zone axis: brand_id is on every view (a scope key), yy_zone is not
    "yy_rent": {"aggregation": "additive", "grain": "lease_day", "cube_mapping": {"view": "ledger"}, "formula": {"depends_on": []}},
}
DIMS = {"till": ["brand_id", "yy_zone"], "door": ["brand_id", "yy_zone"], "ledger": ["brand_id"]}
ZONES = {"north": 0.55, "south": 0.3, "east": 0.15}


class FakeMcp:
    def __init__(self) -> None:
        rng = np.random.default_rng(5)
        n = len(DAYS)
        self.data = {
            "yy_takings": {d: 10000 * (1 + 0.03 * rng.normal()) for d in DAYS},
            "yy_baskets": {d: 400 * (1 + 0.03 * rng.normal()) for d in DAYS},
            "yy_footfall": {d: 9000 * (1 + 0.03 * rng.normal()) for d in DAYS},
            "yy_rent": {d: 2000.0 for d in DAYS},
        }
        self.data["yy_basket_size"] = {d: self.data["yy_takings"][d] / self.data["yy_baskets"][d] for d in DAYS}
        self.data["yy_conversion"] = {d: self.data["yy_baskets"][d] / self.data["yy_footfall"][d] for d in DAYS}
        self.noise = rng.normal(0, 0.02, (n, len(ZONES)))
        self.queries: list[dict[str, Any]] = []

    def _zone_value(self, m: str, d: date, zone: str) -> float:
        share = ZONES[zone]
        v = self.data[m][d] * share * (1 + self.noise[DAYS.index(d), list(ZONES).index(zone)])
        if m == "yy_takings" and zone == "south" and d in WINDOW:
            v *= 0.3  # planted: south's takings collapse in the window
        return v

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        if capability == "seleric.catalogue_get_metrics":
            return {"metrics": {m: {**DEFS[m], "id": m} for m in arguments["metric_ids"] if m in DEFS}}
        assert capability == "seleric.metrics_query", capability
        self.queries.append(arguments)
        m = arguments["measures"][0]
        start = date.fromisoformat(arguments["time_range"]["start"])
        end = date.fromisoformat(arguments["time_range"]["end"])
        dims = arguments.get("dimensions") or []
        zone_filter = next((f["values"] for f in arguments.get("filters") or [] if f["dimension"] == "yy_zone"), None)
        rows = []
        for d in DAYS:
            if not (start <= d <= end):
                continue
            stamp = f"{d.isoformat()}T00:00:00.000"
            zones = zone_filter or list(ZONES)
            if dims:
                rows += [{"x.day.day": stamp, f"x.{dims[0]}": z, m: self._zone_value(m, d, z)} for z in zones]
            else:
                total = sum(self._zone_value(m, d, z) for z in zones) if (zone_filter or m == "yy_takings") else self.data[m][d]
                rows.append({"x.day.day": stamp, m: total})
        return {"rows": rows, "provenance": {"query_id": "q"}}


def _deps(mcp: FakeMcp) -> SelericDeps:
    metrics = tuple(
        CatalogueMetricMeta(id=m, view=d["cube_mapping"]["view"], supported_dimensions=list(DIMS[d["cube_mapping"]["view"]]),
                            raw={"aggregation": d["aggregation"]})
        for m, d in DEFS.items()
    )
    return SelericDeps(
        mission_id="m-explore", as_of=AS_OF,
        principal=Principal(principal_id="p", workspace_id="ws", user_id="u"),
        thread_id="t", run_id="r", trace_id="tr", context=ContextBundle(), mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=metrics, dimensions=("brand_id", "yy_zone")),
    )


class Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


@pytest.fixture(autouse=True)
def _fresh_definition_cache():
    diagnosis._definitions_cache.update({"at": 0.0, "ids": frozenset(), "defs": {}})


def test_open_exploration_plans_from_metadata_and_grounds_every_finding():
    mcp = FakeMcp()
    ctx = Ctx(_deps(mcp))
    res = asyncio.run(exploration.explore_data(ctx))  # type: ignore[arg-type]
    assert res.success, res.summary
    report = res.provenance.source_metadata["exploration"]
    # headline metrics are the additive hubs, from lineage — no name was given
    explored = res.provenance.source_metadata["metrics"]
    assert set(explored) == {"yy_takings", "yy_baskets", "yy_footfall", "yy_rent"}
    # the most-depended-on base metric first (two ratios use it), then round-robin across views
    assert explored[:2] == ["yy_baskets", "yy_footfall"]
    assert report["window"][0] == WINDOW[0].isoformat() and report["window"][-1] == YESTERDAY.isoformat()
    # the scope key is never screened as a breakdown; today never enters a window
    assert all(q.get("dimensions") != ["brand_id"] for q in mcp.queries)
    assert all(q["time_range"]["end"] <= YESTERDAY.isoformat() for q in mcp.queries)
    kinds = {(i["kind"], i["metric"]) for i in report["insights"]}
    assert ("period_change", "yy_takings") in kinds
    shift = next(i for i in report["insights"] if i["kind"] == "distribution_shift")
    assert shift["segments"][0] == "south"

    arts = {a.id: a for a in ctx.deps.artifact_store.list_for_mission("m-explore")}
    finding_ids = res.provenance.source_metadata["finding_ids"]
    assert finding_ids == res.artifact_ids[: len(finding_ids)]
    for fid in finding_ids:
        f = Finding.model_validate(arts[fid].payload)
        assert f.finding_type.startswith("exploration.")
        assert all(eid in arts for eid in f.evidence_ids)
    for eid in res.artifact_ids[len(finding_ids):]:
        EvidenceArtifact.model_validate(arts[eid].payload)
    head = res.summary.split("NEXT PROBES")[0]
    assert "yy_" not in head  # labels, never ids, in the findings the model paraphrases
    assert "REPORTING RULES" in res.summary and "explore_data(" in res.summary


def test_drill_down_follow_up_scopes_the_exploration_and_reports_its_share():
    mcp = FakeMcp()
    ctx = Ctx(_deps(mcp))
    res = asyncio.run(exploration.explore_data(ctx, metric_ids=["yy_takings"], filters={"yy_zone": "north"}))  # type: ignore[arg-type]
    assert res.success, res.summary
    seg_queries = [q for q in mcp.queries if q.get("dimensions")]
    assert not any(q["dimensions"] == ["yy_zone"] for q in seg_queries)  # the filtered axis is not re-split
    # north is about half of takings: impact (and so the score) is scaled by that share
    for i in res.provenance.source_metadata["exploration"]["insights"]:
        assert 0.4 < i["impact"] < 0.9


def test_refusals():
    ctx = Ctx(_deps(FakeMcp()))
    res = asyncio.run(exploration.explore_data(ctx, period_start=AS_OF, period_end=AS_OF))  # type: ignore[arg-type]
    assert not res.success and res.error_code == "UNSUPPORTED_QUERY"
    long = asyncio.run(exploration.explore_data(  # type: ignore[arg-type]
        ctx, period_start=datetime(2026, 1, 1, tzinfo=UTC), period_end=datetime(2026, 9, 30, tzinfo=UTC)
    ))
    assert not long.success and "at most" in long.summary
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry):  # a near-miss id is bounced back with the closest real ids
        asyncio.run(exploration.explore_data(ctx, metric_ids=["yy_taking"]))  # type: ignore[arg-type]
