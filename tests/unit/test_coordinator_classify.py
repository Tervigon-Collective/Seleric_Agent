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
