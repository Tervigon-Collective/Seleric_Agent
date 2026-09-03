import pytest

from seleric_swarm.eval.evaluators import (
    evidence_on_numeric_claims,
    load_jsonl,
    mcp_not_called_for_unsupported,
    numeric_exact_match,
    routing_exact_match,
    schema_valid,
)
from seleric_swarm.orchestration.runner import run_mission


@pytest.mark.asyncio
async def test_lookup_v1_gold(runtime):
    rows = load_jsonl("eval/datasets/lookup_commerce.jsonl")
    assert len(rows) >= 15
    for row in rows:
        expected = row["expected"]
        result = await run_mission(
            runtime,
            query=row["query"],
            timezone=row.get("scope", {}).get("timezone", "Asia/Kolkata"),
            as_of=row.get("scope", {}).get("as_of"),
        )
        raw = runtime.store.get_raw(result.mission_id)
        assert schema_valid(result), row["id"]
        assert routing_exact_match(result.query_class, result.mission_lead, expected), (
            row["id"],
            result.query_class,
            result.mission_lead,
            result.error,
        )
        assert numeric_exact_match(result, expected), (row["id"], result.model_dump())
        assert evidence_on_numeric_claims(result), row["id"]
        assert mcp_not_called_for_unsupported(raw or {}, expected), (row["id"], raw)


@pytest.mark.asyncio
async def test_canonical_net_sales_question(runtime):
    result = await run_mission(
        runtime,
        query="What were net sales yesterday?",
        timezone="Asia/Kolkata",
        as_of="2026-09-03",
    )
    assert result.status == "completed"
    assert result.query_class == "lookup"
    assert result.mission_lead == "commerce_agent"
    assert result.active_specialist == "observer_agent"
    assert any(c.claim_type == "numeric" and c.support_refs and c.trust_label in {"VERIFIED", "STRONG"} for c in result.claims)
    assert any(e.metric_or_fact == "metric.net_sales" and e.value == 98000.0 for e in result.evidence)
    assert result.trace.request_id
