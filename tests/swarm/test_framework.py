"""Framework-level checks: artifacts, blackboard, envelope, transport, and the
complexity-based dispatch fold-in."""

from __future__ import annotations

import pytest

from seleric_swarm.swarm.artifacts import ARTIFACT_MODELS, Anomaly, Evidence
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.envelope import PROTOCOL, HandoffProposal, Intent, SwarmMessage
from seleric_swarm.swarm.transport import InProcessTransport


def test_artifact_registry_covers_seven_types():
    assert set(ARTIFACT_MODELS) == {
        "evidence", "anomaly", "hypothesis", "causal", "prediction", "strategy", "skeptic"
    }


def test_blackboard_tracks_evidence_ledger_and_synthetic_inputs():
    bb = Blackboard("MS-1")
    ev = Evidence.new(mission_id="MS-1", created_by="observer", metric_or_fact="metric.cac", value=782, baseline=604)
    ev.mark_synthetic()
    eid = bb.post(ev)
    assert bb.evidence_ledger == [eid]
    assert bb.has_synthetic_inputs([eid]) is True
    an = Anomaly.new(mission_id="MS-1", created_by="anomaly", metric_id="metric.cac", deviation_pct=29.5)
    aid = bb.post(an)
    assert bb.refs_by_type("anomaly") == [aid]
    bb.update(aid, {"score": 0.9})
    assert bb.get(aid)["score"] == 0.9


def test_swarm_message_uses_seleric_protocol():
    m = SwarmMessage.request(
        mission_id="MS-1", from_agent="performance_agent", to_agent="funnel_agent",
        intent=Intent.HANDOFF_PROPOSAL, objective="investigate cvr",
    )
    assert m.protocol == PROTOCOL
    assert m.intent is Intent.HANDOFF_PROPOSAL


def test_handoff_proposal_requires_evidence():
    with pytest.raises(ValueError):
        HandoffProposal(
            from_agent="a", to_agent="b", reason="x", evidence_refs=[], unresolved_question="q"
        )


@pytest.mark.asyncio
async def test_in_process_transport_routes_and_logs():
    t = InProcessTransport()

    async def handler(msg: SwarmMessage) -> dict:
        return {"ok": True, "echo": msg.objective}

    t.register("funnel_agent", handler)
    out = await t.send(SwarmMessage.request(
        mission_id="MS-1", from_agent="c", to_agent="funnel_agent",
        intent=Intent.TASK_REQUEST, objective="hi",
    ))
    assert out == {"ok": True, "echo": "hi"}
    assert t.log[0]["to"] == "funnel_agent"
    miss = await t.send(SwarmMessage.request(
        mission_id="MS-1", from_agent="c", to_agent="nobody", intent=Intent.TASK_REQUEST,
    ))
    assert miss["ok"] is False


@pytest.mark.asyncio
async def test_dispatch_routes_lookup_vs_swarm(runtime):
    from seleric_swarm.orchestration.dispatch import route_for

    assert await route_for(runtime, query="What were net sales on 2026-08-01?") == "lookup"
    assert await route_for(runtime, query="Why did CAC increase and what should we do?") == "swarm"
    assert await route_for(runtime, query="Compare net sales on 2026-08-01 and 2026-08-02") == "lookup"
