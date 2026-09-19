import pytest

from seleric_swarm.agents.coordinator import Agent as CoordinatorAgent
from seleric_swarm.contracts.lookup import CoordinatorClassificationV1, TimeRangeV1
from seleric_swarm.eval.evaluators import classify_exact_match, load_jsonl


@pytest.mark.asyncio
async def test_coordinator_classify_gold(runtime):
    agent = CoordinatorAgent(runtime)
    rows = load_jsonl("eval/datasets/coordinator_classify.jsonl")
    assert len(rows) >= 20
    hits = 0
    for row in rows:
        payload = await agent.classify(
            query=row["query"],
            timezone=row.get("scope", {}).get("timezone", "Asia/Kolkata"),
            as_of=row.get("scope", {}).get("as_of"),
            mission_id="eval",
            request_id="eval",
            session_id="eval",
            task_id="eval",
        )
        actual = CoordinatorClassificationV1(
            query_class=payload["query_class"],
            domain_lead=payload.get("mission_lead") or "coordinator_agent",
            time_range=TimeRangeV1.model_validate(payload.get("time_range") or {"kind": "none"}),
            metric_hints=payload.get("metric_hints") or [],
            unsupported_reason=payload.get("unsupported_reason"),
        )
        if classify_exact_match(actual, row["expected"]):
            hits += 1
        else:
            raise AssertionError((row["id"], payload, row["expected"]))
    assert hits / len(rows) >= 0.95


@pytest.mark.asyncio
async def test_classify_resolves_grain_via_live_catalogue_not_left_empty(runtime):
    """Regression for the gap found in the catalogue_grounding.py heuristic
    deletion (docs/refactor/TASK_SHEET.md, Sprint 3 Profile B): classify()
    must not silently return resolved_dimensions=[] for every grain-shaped
    question just because the deleted apply_catalogue_grain() is gone — it
    resolves grain via the live seleric-mcp catalogue resolver instead
    (resolve_catalogue_dimension), corroborated against the canonical
    metric's own declared supported_dimensions."""
    agent = CoordinatorAgent(runtime)
    payload = await agent.classify(
        # metric.units_sold's catalogue entry declares product_title/sku
        # (config/metric_registry.yaml) -- a real, live-confirmed breakdown,
        # unlike net_sales (canonical_pnl-scoped, no channel dimension).
        query="units sold by product",
        timezone="Asia/Kolkata",
        as_of=None,
        mission_id="grain-regression",
        request_id="grain-regression",
        session_id="grain-regression",
        task_id="grain-regression",
    )
    if payload.get("metric_hints") != ["metric.units_sold"]:
        pytest.skip(
            f"classifier did not resolve metric.units_sold for this query in this "
            f"environment (got {payload.get('metric_hints')}) -- cannot assert grain "
            "resolution without the expected metric"
        )
    assert payload.get("resolved_dimensions"), (
        f"grain-shaped query resolved no dimensions at all: {payload}"
    )


@pytest.mark.asyncio
async def test_classify_leaves_dimensions_empty_for_plain_lookup(runtime):
    """No grain-intent language -> no dimension resolution attempted at all
    (query_has_grain_intent gates the live resolver call)."""
    agent = CoordinatorAgent(runtime)
    payload = await agent.classify(
        query="what were net sales yesterday",
        timezone="Asia/Kolkata",
        as_of=None,
        mission_id="grain-regression-2",
        request_id="grain-regression-2",
        session_id="grain-regression-2",
        task_id="grain-regression-2",
    )
    assert not payload.get("resolved_dimensions")
