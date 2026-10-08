"""toolsets/diagnosis.py against a fake catalogue + Cube.

The fake catalogue uses invented metric/dimension/view names, so the test
fails if the tool plans anything from a hardcoded name instead of metadata.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from seleric_swarm.agent.artifacts import CausalArtifact, EvidenceArtifact, Finding
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.conversations.contracts import ContextBundle, Principal
from seleric_swarm.services.catalogue_bootstrap import CatalogueMetricMeta, CatalogueSnapshot
from seleric_swarm.state.artifacts import InMemoryArtifactStore
from seleric_swarm.toolsets import diagnosis

pytestmark = pytest.mark.filterwarnings("ignore")
warnings.filterwarnings("ignore")
logging.getLogger("dowhy").setLevel(logging.ERROR)

AS_OF = datetime(2026, 10, 3, 12, tzinfo=UTC)
EVENT = date(2026, 10, 2)
DAYS = [EVENT - timedelta(days=i) for i in range(70, -2, -1)]  # includes a partial "today"

DEFS: dict[str, dict[str, Any]] = {
    "zz_revenue": {"aggregation": "additive", "grain": "purchase", "cube_mapping": {"view": "shop"}, "formula": {"depends_on": []}},
    "zz_purchases": {"aggregation": "additive", "grain": "purchase", "cube_mapping": {"view": "shop"}, "formula": {"depends_on": []}},
    "zz_basket": {"aggregation": "ratio", "grain": "purchase", "cube_mapping": {"view": "shop"}, "formula": {"depends_on": ["zz_revenue", "zz_purchases"]}},
    "zz_visits": {"aggregation": "additive", "grain": "visit", "cube_mapping": {"view": "site"}, "formula": {"depends_on": []}},
    "zz_buy_rate": {"aggregation": "ratio", "grain": "visit", "cube_mapping": {"view": "site"}, "formula": {"depends_on": ["zz_visits"]}},
    "zz_budget": {"aggregation": "additive", "grain": "placement_day", "cube_mapping": {"view": "media"}, "formula": {"depends_on": []}},
    "zz_cost_per_visit": {"aggregation": "ratio", "grain": "placement_day", "cube_mapping": {"view": "media"}, "formula": {"depends_on": ["zz_budget", "zz_visits"]}},
}
DIMS = {
    "shop": ["brand_id", "zz_source"],
    "site": ["brand_id", "zz_source"],
    "media": ["brand_id"],
}


def _data(seed: int = 1) -> dict[str, dict[date, float]]:
    rng = np.random.default_rng(seed)
    n = len(DAYS)
    budget = 1000 + 80 * rng.normal(size=n)
    visits = 3000 + 0.8 * budget + rng.normal(0, 60, n)
    rate = np.clip(0.012 + rng.normal(0, 0.0006, n), 0.001, 1)
    i_ev = DAYS.index(EVENT)
    rate[i_ev] = 0.006  # the buy rate halves on the event day
    purchases = rate * visits
    basket = 2000 + rng.normal(0, 40, n)
    revenue = purchases * basket
    S = lambda a: {d: float(v) for d, v in zip(DAYS, a, strict=True)}
    return {
        "zz_budget": S(budget), "zz_visits": S(visits), "zz_buy_rate": S(rate),
        "zz_purchases": S(purchases), "zz_basket": S(basket), "zz_revenue": S(revenue),
        "zz_cost_per_visit": S(budget / visits),
    }


class FakeMcp:
    def __init__(self) -> None:
        self.data = _data()
        self.queries: list[dict[str, Any]] = []

    async def call(self, *, agent_id: str, capability: str, arguments: dict[str, Any]) -> Any:
        if capability == "seleric.catalogue_get_metrics":
            return {"metrics": {m: {**DEFS[m], "id": m} for m in arguments["metric_ids"] if m in DEFS}}
        assert capability == "seleric.metrics_query", capability
        self.queries.append(arguments)
        m = arguments["measures"][0]
        start = date.fromisoformat(arguments["time_range"]["start"])
        end = date.fromisoformat(arguments["time_range"]["end"])
        dims = arguments.get("dimensions") or []
        rows = []
        for d, v in self.data[m].items():
            if not (start <= d <= end):
                continue
            stamp = f"{d.isoformat()}T00:00:00.000"
            if not dims:
                rows.append({"x.day.day": stamp, m: v})
                continue
            # two segments; the event's rate drop sits entirely in segment "a"
            share = 0.5
            for seg, w in (("a", share), ("b", 1 - share)):
                val = v * w
                if m == "zz_buy_rate":
                    val = v if seg == "a" else self.data["zz_buy_rate"][d - timedelta(days=7)] if d == EVENT else v
                rows.append({"x.day.day": stamp, f"x.{dims[0]}": seg, m: val})
        return {"rows": rows, "provenance": {"query_id": "q"}}


def _deps(mcp: FakeMcp) -> SelericDeps:
    metrics = tuple(
        CatalogueMetricMeta(id=m, view=d["cube_mapping"]["view"], supported_dimensions=list(DIMS[d["cube_mapping"]["view"]]), raw={"aggregation": d["aggregation"]})
        for m, d in DEFS.items()
    )
    return SelericDeps(
        mission_id="m-diag", as_of=AS_OF,
        principal=Principal(principal_id="p", workspace_id="ws", user_id="u"),
        thread_id="t", run_id="r", trace_id="tr", context=ContextBundle(), mcp_client=mcp,
        artifact_store=InMemoryArtifactStore(), limits=ExecutionLimits(),
        catalogue=CatalogueSnapshot(metrics=metrics, dimensions=("brand_id", "zz_source")),
    )


class Ctx:
    def __init__(self, deps: SelericDeps) -> None:
        self.deps = deps


@pytest.fixture(autouse=True)
def _fresh_definition_cache():
    diagnosis._definitions_cache.update({"at": 0.0, "ids": frozenset(), "defs": {}})


def test_tool_plans_from_metadata_and_grounds_every_number():
    mcp = FakeMcp()
    ctx = Ctx(_deps(mcp))
    res = asyncio.run(diagnosis.diagnose_metric_change(ctx, "zz_revenue", claimed_direction="down"))  # type: ignore[arg-type]
    assert res.success, res.summary
    report = res.provenance.source_metadata["diagnosis"]
    assert report["event"]["premise"] == "confirmed"
    # lineage-proposed, data-verified: revenue = basket x purchases; purchases ≈ buy_rate x visits
    assert report["identities"][0].startswith("zz_revenue = zz_basket × zz_purchases")
    assert {t["metric"] for t in report["chain"]} == {"zz_buy_rate", "zz_visits"}
    lead = max(report["chain"], key=lambda t: abs(t["contribution"]))
    assert lead["metric"] == "zz_buy_rate"
    # the scope key carried by every view is never used as a segment dimension
    assert all(q.get("dimensions") != ["brand_id"] for q in mcp.queries)
    # partial "today" never enters the history
    assert report["event_window"] == [EVENT.isoformat()]

    store = ctx.deps.artifact_store
    arts = {a.id: a for a in store.list_for_mission("m-diag")}
    finding = Finding.model_validate(arts[res.artifact_ids[0]].payload)
    assert finding.finding_type == "diagnosis"
    assert finding.metrics["event.actual"] == pytest.approx(report["event"]["actual"])
    assert "chain.zz_buy_rate.contribution" in finding.metrics
    for aid in res.artifact_ids[1:]:
        a = arts[aid]
        if a.artifact_type == "causal":
            c = CausalArtifact.model_validate(a.payload)
            assert c.query["graph_edges"] and c.query["adjustment_set"] is not None
            if c.evidence_classification == "CAUSALLY_SUPPORTED":
                assert c.refutation_checks
        else:
            EvidenceArtifact.model_validate(a.payload)
    assert "REPORTING RULES" in res.summary and "ANSWER SKELETON" in res.summary
    assert "zz_" not in res.summary.split("REPORTING RULES")[0]  # labels, never ids, reach the model


def test_tool_refuses_unknown_metric_and_partial_event():
    mcp = FakeMcp()
    ctx = Ctx(_deps(mcp))
    res = asyncio.run(diagnosis.diagnose_metric_change(ctx, "zz_revenue", event_start=AS_OF, event_end=AS_OF))  # type: ignore[arg-type]
    assert res.success
    assert res.provenance.source_metadata["diagnosis"]["verdict"] == "insufficient_data"
    from pydantic_ai import ModelRetry

    with pytest.raises(ModelRetry):  # a near-miss id is bounced back with the closest real ids
        asyncio.run(diagnosis.diagnose_metric_change(ctx, "zz_revenu"))  # type: ignore[arg-type]


def test_exclusive_midnight_end_and_partial_today_are_handled():
    """Live 2026-10-04: the model sent 'yesterday' as [D 00:00, D+1 00:00]; D+1 was
    today (in progress) and the whole diagnosis came back 'insufficient data'."""
    ctx = Ctx(_deps(FakeMcp()))
    start = datetime(2026, 10, 2, tzinfo=UTC)
    res = asyncio.run(diagnosis.diagnose_metric_change(ctx, "zz_revenue", event_start=start, event_end=start + timedelta(days=1)))  # type: ignore[arg-type]
    assert res.provenance.source_metadata["diagnosis"]["event_window"] == ["2026-10-02"]
    # an explicit two-day window that runs into today keeps only the complete day
    res = asyncio.run(diagnosis.diagnose_metric_change(ctx, "zz_revenue", event_start=start, event_end=AS_OF))  # type: ignore[arg-type]
    report = res.provenance.source_metadata["diagnosis"]
    assert report["event_window"] == ["2026-10-02"]
    assert any("in progress" in n for n in report["data_quality"])


def test_any_window_length_is_diagnosed_as_a_period_comparison():
    """Live: "event window ... is 30 days; diagnose at most 14 days at a time" failed
    a why-question. No length is refused; a long window is compared with the
    equal-length period just before it."""
    mcp = FakeMcp()
    ctx = Ctx(_deps(mcp))
    start = datetime(2026, 9, 3, tzinfo=UTC)
    end = datetime(2026, 10, 2, 23, 59, tzinfo=UTC)
    res = asyncio.run(diagnosis.diagnose_metric_change(ctx, "zz_revenue", event_start=start, event_end=end))  # type: ignore[arg-type]
    assert res.success, res.summary
    event = res.provenance.source_metadata["diagnosis"]["event"]
    assert event["reference_kind"] == "the comparison period"
    refs = sorted({r for v in event["reference_days"].values() for r in v})
    assert refs[0] == "2026-08-04" and refs[-1] == "2026-09-02"
