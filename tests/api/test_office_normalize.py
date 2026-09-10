"""Office gateway normalization — event stream + snapshot derivation.

Uses a synthetic persisted mission payload shaped like the real swarm_v2
control-plane output (see seleric_swarm/coordinator/observability/events.py),
walking the full CAC investigation incl. a Skeptic REVISE + remediation round.
"""

from __future__ import annotations

from seleric_swarm.api.office.normalize import (
    OFFICE_AGENTS,
    build_office_snapshot,
    normalize_events,
)


def _cac_events() -> list[dict]:
    seq = iter(range(1, 999))

    def e(kind: str, **kw: object) -> dict:
        return {"kind": kind, "seq": next(seq), "ts": "2026-09-09T14:00:00Z",
                "mission_id": "MS-cac", "family": kind.split("_")[0], **kw}

    return [
        e("mission_created", intents=["diagnostic", "predictive", "prescriptive"]),
        e("decomposition_created", decomposition_id="DEC-1", version=1, template="cac"),
        e("task_plan_created", tasks=6, errors=[]),
        e("task_wave_executed", iteration=1, mission_lead="performance", ready_done=["observer_agent", "anomaly_agent"]),
        e("decomposition_refined", decomposition_id="DEC-2", version=2, reason="Purchase CVR down on mobile"),
        e("leadership_transfer", from_agent="performance_agent", to_agent="funnel_agent",
          reason="media stable; funnel conversion suspect", unresolved_question="Why did mobile CVR fall?", epoch=1),
        e("task_wave_executed", iteration=2, mission_lead="funnel", ready_done=["observer_agent"]),
        e("leadership_transfer", from_agent="funnel_agent", to_agent="technical_agent",
          reason="mobile-only JS errors spike", unresolved_question="What broke on mobile?", epoch=2),
        e("task_specialists_activated", activated=3, intents=["diagnostic", "predictive", "prescriptive"]),
        e("claim_proposed", claim_id="CLM-1", claim_type="causal"),
        e("skeptic_gate", claim_id="CLM-1", event="review"),
        e("skeptic_revise", verdict="REVISE", claim_id="CLM-1", reason="missing traffic-mix control"),
        e("remediation_planned", kinds=["evidence_gap"]),
        e("remediation_activated", agent="performance_agent", remediation_round=1),
        e("remediation_round_done", remediation_round=1),
        e("skeptic_gate", claim_id="CLM-1", event="re-review"),
        e("skeptic_pass", verdict="PASS", claim_id="CLM-1"),
        e("mission_completed"),
    ]


def _raw() -> dict:
    return {
        "route": "swarm",
        "mission_id": "MS-cac",
        "status": "completed",
        "query": "Why has CAC increased over the last three days?",
        "mission_lead": "technical",
        "initial_mission_lead": "performance",
        "leadership_epoch": 2,
        "handoff_history": [
            {"from_agent": "performance_agent", "to_agent": "funnel_agent", "reason": "media stable", "epoch": 1},
            {"from_agent": "funnel_agent", "to_agent": "technical_agent", "reason": "JS errors", "epoch": 2},
        ],
        "unresolved_questions": [],
        "final_response": "Frontend regression in the mobile checkout bundle drove CVR down, raising CAC.",
        "artifacts": {
            "evidence": [{"id": "EV-1"}, {"id": "EV-2"}],
            "anomaly": [{"id": "AN-1"}, {"id": "AN-2"}, {"id": "AN-3"}],
            "hypothesis": [{"id": "HYP-21"}],
            "causal": [{"id": "CAU-1"}],
            "prediction": [{"id": "PR-1"}],
            "strategy": [{"id": "ST-1"}, {"id": "ST-2"}],
            "skeptic": [{"id": "SK-1"}, {"id": "SK-2"}],
        },
        "events": _cac_events(),
    }


def test_normalize_events_shape_and_order() -> None:
    ui = normalize_events(_cac_events(), mission_id="MS-cac")
    assert [x["seq"] for x in ui] == sorted(x["seq"] for x in ui)
    assert ui[0]["eventType"] == "mission_started"
    kinds = {x["eventType"] for x in ui}
    assert {"leadership_transferred", "skeptic_revise", "skeptic_pass", "remediation_created"} <= kinds
    # every event carries a stable id + a human summary
    assert all(x["eventId"] and x["summary"] for x in ui)


def test_normalize_events_after_seq_is_incremental() -> None:
    all_ev = _cac_events()
    first = normalize_events(all_ev, mission_id="MS-cac", after_seq=0)
    tail = normalize_events(all_ev, mission_id="MS-cac", after_seq=first[4]["seq"])
    assert min(x["seq"] for x in tail) > first[4]["seq"]
    assert len(first) == len(tail) + 5


def test_leadership_transfer_maps_from_and_to_agents() -> None:
    ui = normalize_events(_cac_events(), mission_id="MS-cac")
    lt = [x for x in ui if x["eventType"] == "leadership_transferred"]
    assert lt[0]["agentId"] == "funnel_agent"
    assert lt[1]["agentId"] == "technical_agent"
    assert "performance_agent" in lt[0]["metadata"]["from_agent"]


def test_snapshot_has_every_office_character() -> None:
    snap = build_office_snapshot(_raw(), mission_id="MS-cac")
    got = {a["agentId"] for a in snap["agents"]}
    assert got == {a["agentId"] for a in OFFICE_AGENTS}


def test_snapshot_lead_ring_follows_mission_lead() -> None:
    snap = build_office_snapshot(_raw(), mission_id="MS-cac")
    leads = [a["agentId"] for a in snap["agents"] if a["missionLead"]]
    assert leads == ["technical_agent"]
    assert snap["leadAgentId"] == "technical_agent"


def test_snapshot_board_and_stage_on_completion() -> None:
    snap = build_office_snapshot(_raw(), mission_id="MS-cac")
    assert snap["stage"] == "complete"
    assert all(s["state"] == "done" for s in snap["board"]["steps"])
    assert snap["finalResponse"]
    assert snap["artifacts"]["anomaly"] == 3


def test_snapshot_running_mission_keeps_active_agents() -> None:
    raw = _raw()
    raw["status"] = "running"
    raw["events"] = raw["events"][:7]  # stop mid funnel investigation
    raw["mission_lead"] = "funnel"
    snap = build_office_snapshot(raw, mission_id="MS-cac")
    by_id = {a["agentId"]: a for a in snap["agents"]}
    assert by_id["funnel_agent"]["missionLead"] is True
    assert by_id["coordinator"]["status"] in {"planning", "thinking", "working"}
    assert snap["stage"] in {"investigating", "decomposing", "planning"}


def test_snapshot_handoffs_from_history() -> None:
    snap = build_office_snapshot(_raw(), mission_id="MS-cac")
    assert [h["to"] for h in snap["handoffs"]] == ["funnel_agent", "technical_agent"]


def test_empty_mission_is_safe() -> None:
    snap = build_office_snapshot({}, mission_id="MS-x")
    assert snap["agents"] and snap["stage"] == "intake"
    assert normalize_events(None, mission_id="MS-x") == []


def test_snapshot_parallel_tasks_group_by_agent() -> None:
    raw = _raw()
    raw["tasks"] = [
        {"task_id": "T1", "assigned_agent": "performance_agent", "objective": "Meta spend", "status": "running"},
        {"task_id": "T2", "assigned_agent": "performance_agent", "objective": "Google spend", "status": "running"},
        {"task_id": "T3", "assigned_agent": "performance_agent", "objective": "Attribution", "status": "done"},
        {"task_id": "T4", "assigned_agent": "funnel_agent", "objective": "Solo task", "status": "running"},
    ]
    snap = build_office_snapshot(raw, mission_id="MS-cac")
    pt = snap["parallelTasks"]
    # performance has >1 task with in-flight work -> shown; funnel has only 1 -> not
    assert set(pt) == {"performance_agent"}
    assert len(pt["performance_agent"]) == 3
    assert {t["label"] for t in pt["performance_agent"]} == {"Meta spend", "Google spend", "Attribution"}


def test_snapshot_trace_passthrough_and_langsmith_url(monkeypatch) -> None:
    import seleric_swarm.api.office.normalize as nz

    raw = _raw()
    raw["trace"] = {"request_id": "req-123", "session_id": "sess-9"}

    # With a project configured, a deep link is built and carries the request id.
    monkeypatch.setattr(nz, "_langsmith_trace_url", lambda rid: f"https://smith/x?search={rid}" if rid else None)
    snap = build_office_snapshot(raw, mission_id="MS-cac")
    assert snap["trace"] == {"requestId": "req-123", "sessionId": "sess-9"}
    assert snap["traceUrl"] == "https://smith/x?search=req-123"

    # No trace at all -> no url.
    raw.pop("trace")
    snap2 = build_office_snapshot(raw, mission_id="MS-cac")
    assert snap2["trace"] == {"requestId": None, "sessionId": None}
    assert snap2["traceUrl"] is None


def test_evidence_a2a_events_normalize() -> None:
    ev = [
        {"kind": "evidence_requested", "seq": 1, "ts": "2026-09-09T14:00:00Z", "agent": "diagnostic_agent", "key": "technical.mobile_latency"},
        {"kind": "evidence_received", "seq": 2, "ts": "2026-09-09T14:00:05Z", "agent": "diagnostic_agent", "key": "technical.mobile_latency"},
    ]
    ui = normalize_events(ev, mission_id="MS-cac")
    assert ui[0]["eventType"] == "evidence_requested"
    assert ui[0]["status"] == "waiting_for_evidence"
    assert ui[0]["agentId"] == "diagnostic_agent"
    assert "mobile_latency" in ui[0]["summary"]
    assert ui[1]["eventType"] == "evidence_received" and ui[1]["status"] == "working"
