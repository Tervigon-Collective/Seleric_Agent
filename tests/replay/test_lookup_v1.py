import pytest

from seleric_swarm.eval.evaluators import (
    evidence_on_numeric_claims,
    load_jsonl,
    mcp_not_called_for_unsupported,
    routing_exact_match,
    schema_valid,
)
from seleric_swarm.orchestration.runner import run_mission


def _has_metric_evidence(result, metric_id: str | None) -> bool:
    if not metric_id:
        return True
    return any(
        row.metric_or_fact == metric_id and isinstance(row.value, (int, float))
        for row in result.evidence
    )


@pytest.mark.asyncio
async def test_lookup_v1_gold(runtime):
    rows = load_jsonl("eval/datasets/lookup_commerce.jsonl")
    assert len(rows) >= 5
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
        assert result.status == expected["status"], (row["id"], result.status, result.error)
        if expected.get("status") == "completed":
            assert _has_metric_evidence(result, expected.get("metric_id")), (row["id"], result.evidence)
            assert evidence_on_numeric_claims(result), row["id"]
            assert any(c.startswith("seleric.") for c in [x["capability"] for x in runtime.mcp.invocations])
        if expected.get("error_code") == "INSUFFICIENT_EVIDENCE":
            assert result.error and result.error.code == "INSUFFICIENT_EVIDENCE"
            assert 0 not in [row.value for row in result.evidence]
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
    assert any(
        c.claim_type == "numeric" and c.support_refs and c.trust_label in {"VERIFIED", "STRONG"}
        for c in result.claims
    )
    assert any(e.metric_or_fact == "metric.net_sales" and isinstance(e.value, float) for e in result.evidence)
    assert result.trace.request_id
    assert "seleric.metrics_query" in [c["capability"] for c in runtime.mcp.invocations]
