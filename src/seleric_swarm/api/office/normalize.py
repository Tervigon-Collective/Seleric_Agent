"""Pure normalization: persisted mission payload -> office snapshot + UI events.

Everything here is a pure function of the persisted mission ``raw`` dict
(``store.get_raw``). No I/O, no orchestration. The front-end hydrates from
:func:`build_office_snapshot` and then applies the incremental
:func:`normalize_events` stream.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------- #
# Agent roster — the persistent "characters" in the office.                   #
# --------------------------------------------------------------------------- #

# role: coordinator | specialist | domain
OFFICE_AGENTS: list[dict[str, str]] = [
    {"agentId": "coordinator", "name": "Coordinator", "role": "coordinator"},
    {"agentId": "observer_agent", "name": "Observer", "role": "specialist"},
    {"agentId": "anomaly_agent", "name": "Anomaly", "role": "specialist"},
    {"agentId": "diagnostic_agent", "name": "Diagnostic", "role": "specialist"},
    {"agentId": "prediction_agent", "name": "Prediction", "role": "specialist"},
    {"agentId": "strategy_agent", "name": "Strategy", "role": "specialist"},
    {"agentId": "skeptic_agent", "name": "Skeptic", "role": "specialist"},
    {"agentId": "performance_agent", "name": "Performance", "role": "domain", "domain": "performance"},
    {"agentId": "commerce_agent", "name": "Commerce", "role": "domain", "domain": "commerce"},
    {"agentId": "funnel_agent", "name": "Funnel", "role": "domain", "domain": "funnel"},
    {"agentId": "finance_agent", "name": "Finance", "role": "domain", "domain": "finance"},
    {"agentId": "inventory_agent", "name": "Inventory", "role": "domain", "domain": "inventory"},
    {"agentId": "procurement_agent", "name": "Procurement", "role": "domain", "domain": "procurement"},
    {"agentId": "technical_agent", "name": "Technical", "role": "domain", "domain": "technical"},
]
_AGENT_IDS = {a["agentId"] for a in OFFICE_AGENTS}
_BY_ID = {a["agentId"]: a for a in OFFICE_AGENTS}

# Persisted control-plane kind -> normalized UI event type.
KIND_TO_UI: dict[str, str] = {
    "mission_created": "mission_started",
    "mission_completed": "mission_completed",
    "mission_partial": "mission_partial",
    "mission_budget_exhausted": "mission_blocked",
    "mission_cancelled": "mission_completed",
    "mission_failed": "error",
    "mission_accepted": "mission_created",
    "decomposition_created": "decomposition_created",
    "decomposition_refined": "decomposition_refined",
    "task_plan_created": "task_created",
    "task_wave_executed": "task_started",
    "task_specialists_activated": "agent_started",
    "leadership_transfer": "leadership_transferred",
    "leadership_rejected": "leadership_transfer_rejected",
    "claim_proposed": "artifact_created",
    "claim_validated": "skeptic_pass",
    "claim_challenged": "skeptic_revise",
    "claim_rejected": "skeptic_reject",
    "skeptic_gate": "skeptic_review_started",
    "skeptic_pass": "skeptic_pass",
    "skeptic_revise": "skeptic_revise",
    "skeptic_reject": "skeptic_reject",
    "remediation_planned": "remediation_created",
    "remediation_activated": "task_assigned",
    "remediation_round_done": "task_completed",
    "specialist_error": "error",
    # A2A evidence exchange — reserved; rendered as a travelling document when
    # the backend emits them as discrete events.
    "evidence_requested": "evidence_requested",
    "evidence_received": "evidence_received",
}

_ARTIFACT_BUCKET_OWNER = {
    "evidence": "observer_agent",
    "anomaly": "anomaly_agent",
    "hypothesis": "diagnostic_agent",
    "causal": "diagnostic_agent",
    "prediction": "prediction_agent",
    "strategy": "strategy_agent",
    "skeptic": "skeptic_agent",
}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _blank_agent(spec: dict[str, str]) -> dict[str, Any]:
    a: dict[str, Any] = {
        "agentId": spec["agentId"],
        "name": spec["name"],
        "role": spec["role"],
        "status": "idle",
        "missionLead": False,
        "waitingOn": [],
    }
    if spec.get("domain"):
        a["domain"] = spec["domain"]
    return a


def _lead_to_agent(lead: str | None) -> str | None:
    if not lead:
        return None
    if lead in _AGENT_IDS:
        return lead
    cand = f"{lead}_agent"
    return cand if cand in _AGENT_IDS else None


# --------------------------------------------------------------------------- #
# Event stream                                                               #
# --------------------------------------------------------------------------- #

def normalize_events(
    raw_events: list[dict[str, Any]] | None,
    *,
    mission_id: str,
    after_seq: int = 0,
) -> list[dict[str, Any]]:
    """Map persisted control-plane events -> ``SwarmUIEvent`` dicts (seq order)."""
    out: list[dict[str, Any]] = []
    for ev in sorted(raw_events or [], key=lambda e: int(e.get("seq") or 0)):
        seq = int(ev.get("seq") or 0)
        if seq <= after_seq:
            continue
        kind = str(ev.get("kind") or ev.get("legacy_kind") or "")
        ui_type = KIND_TO_UI.get(kind, kind or "unknown")
        agent_id, status = _event_agent_and_status(kind, ev)
        out.append(
            {
                "eventId": f"{mission_id}:{seq}",
                "seq": seq,
                "timestamp": ev.get("ts") or _now_iso(),
                "missionId": mission_id,
                "taskId": ev.get("task_id"),
                "agentId": agent_id,
                "eventType": ui_type,
                "status": status,
                "summary": _event_summary(kind, ev),
                "artifactRefs": ev.get("claim_refs") or ev.get("artifact_refs") or [],
                "metadata": {
                    k: v
                    for k, v in ev.items()
                    if k
                    not in {
                        "kind",
                        "ts",
                        "seq",
                        "mission_id",
                        "family",
                        "workflow_name",
                        "workflow_version",
                    }
                },
            }
        )
    return out


def _event_agent_and_status(kind: str, ev: dict[str, Any]) -> tuple[str | None, str | None]:
    if kind == "mission_created":
        return "coordinator", "planning"
    if kind in {"decomposition_created", "decomposition_refined", "task_plan_created"}:
        return "coordinator", "planning"
    if kind == "task_wave_executed":
        return _lead_to_agent(ev.get("mission_lead")), "working"
    if kind == "task_specialists_activated":
        return "diagnostic_agent", "working"
    if kind == "leadership_transfer":
        return _lead_to_agent(ev.get("to_agent") or ev.get("requested_target")), "working"
    if kind == "skeptic_gate":
        return "skeptic_agent", "reviewing"
    if kind == "skeptic_pass":
        return "skeptic_agent", "completed"
    if kind in {"skeptic_revise", "skeptic_reject"}:
        return "skeptic_agent", "reviewing"
    if kind in {"remediation_planned"}:
        return "coordinator", "working"
    if kind == "remediation_activated":
        return _lead_to_agent(ev.get("agent")) or ev.get("agent"), "working"
    if kind == "specialist_error":
        return _lead_to_agent(ev.get("agent")) or ev.get("agent"), "failed"
    if kind == "evidence_requested":
        return _lead_to_agent(ev.get("agent") or ev.get("from_agent")) or ev.get("agent"), "waiting_for_evidence"
    if kind == "evidence_received":
        return _lead_to_agent(ev.get("agent") or ev.get("to_agent")) or ev.get("agent"), "working"
    if kind in {"mission_completed", "mission_partial", "mission_cancelled"}:
        return "coordinator", "completed"
    if kind == "mission_budget_exhausted":
        return "coordinator", "blocked"
    return None, None


def _event_summary(kind: str, ev: dict[str, Any]) -> str:
    if kind == "mission_created":
        return "Mission received — planning investigation"
    if kind == "decomposition_created":
        return "Decomposed the question into sub-questions"
    if kind == "decomposition_refined":
        return str(ev.get("reason") or "Refined the decomposition with new evidence")
    if kind == "task_plan_created":
        n = ev.get("tasks")
        return f"Task plan created ({n} tasks)" if n is not None else "Task plan created"
    if kind == "task_wave_executed":
        return f"Investigation wave — lead: {ev.get('mission_lead') or 'n/a'}"
    if kind == "task_specialists_activated":
        return "Specialists activated: " + ", ".join(ev.get("intents") or []) or "Specialists activated"
    if kind == "leadership_transfer":
        frm = ev.get("from_agent") or "?"
        to = ev.get("to_agent") or ev.get("requested_target") or "?"
        why = ev.get("reason") or ev.get("unresolved_question")
        base = f"Leadership {frm} → {to}"
        return f"{base}: {why}" if why else base
    if kind == "leadership_rejected":
        return f"Leadership transfer rejected: {ev.get('reason') or 'policy'}"
    if kind == "skeptic_gate":
        return "Skeptic review started"
    if kind == "skeptic_pass":
        return "Skeptic: PASS"
    if kind == "skeptic_revise":
        return f"Skeptic: REVISE — {ev.get('reason') or 'missing checks'}"
    if kind == "skeptic_reject":
        return f"Skeptic: REJECT — {ev.get('reason') or 'claim not supported'}"
    if kind == "remediation_planned":
        kinds = ev.get("kinds") or ev.get("kind")
        return f"Remediation planned: {kinds}" if kinds else "Remediation planned"
    if kind == "remediation_activated":
        return f"Remediation task → {ev.get('agent') or '?'}"
    if kind == "remediation_round_done":
        return f"Remediation round {ev.get('remediation_round') or ''} complete".strip()
    if kind == "mission_completed":
        return "Mission complete"
    if kind == "mission_partial":
        return f"Mission partial: {ev.get('reason') or ev.get('status_reason') or ''}".strip()
    if kind == "mission_budget_exhausted":
        return f"Budget exhausted: {ev.get('reason') or ''}".strip()
    if kind == "specialist_error":
        return f"Specialist error: {ev.get('agent') or ''} {ev.get('error') or ''}".strip()
    if kind == "evidence_requested":
        return f"Evidence request: {ev.get('key') or ev.get('metric') or 'data'}".strip()
    if kind == "evidence_received":
        return f"Evidence received: {ev.get('key') or ev.get('metric') or 'data'}".strip()
    return kind.replace("_", " ")


# --------------------------------------------------------------------------- #
# Snapshot                                                                   #
# --------------------------------------------------------------------------- #

_BOARD_STEPS = [
    ("verify", "Verify issue"),
    ("frontier", "Find causal frontier"),
    ("diagnose", "Diagnose cause"),
    ("forecast", "Forecast impact"),
    ("strategy", "Design strategy"),
    ("skeptic", "Skeptic review"),
]


def _langsmith_trace_url(request_id: str | None) -> str | None:
    """Best-effort deep link into LangSmith for this mission's traces.

    LangSmith runs are tagged with the mission ``request_id`` (see
    ``observability/tracing.py``), so a project-scoped search by that tag lands
    on the mission. Returns ``None`` unless LangSmith is configured.
    """
    if not request_id:
        return None
    try:
        from seleric_swarm.config.settings import get_settings

        s = get_settings()
    except Exception:
        return None
    project = getattr(s, "langsmith_project", "") or ""
    org = getattr(s, "langsmith_org", "") or ""
    if not project:
        return None
    base = "https://smith.langchain.com"
    if org:
        return f"{base}/o/{org}/projects/p/{project}?searchModel=%7B%22filter%22%3A%22{request_id}%22%7D"
    return f"{base}/projects/p/{project}?search={request_id}"


def build_office_snapshot(raw: dict[str, Any] | None, *, mission_id: str) -> dict[str, Any]:
    raw = raw or {}
    events = [e for e in (raw.get("events") or []) if isinstance(e, dict)]
    events.sort(key=lambda e: int(e.get("seq") or 0))
    kinds = [str(e.get("kind") or e.get("legacy_kind") or "") for e in events]

    agents = {a["agentId"]: _blank_agent(a) for a in OFFICE_AGENTS}
    status = str(raw.get("status") or "running")
    mission_lead = raw.get("mission_lead")
    initial_lead = raw.get("initial_mission_lead") or raw.get("initial_lead")

    # Fold the event stream.
    for ev in events:
        _apply_event_to_agents(agents, str(ev.get("kind") or ""), ev)

    # Overlay artifact-bucket ownership (counts + "has produced work").
    artifacts = raw.get("artifacts") or {}
    art_counts: dict[str, int] = {}
    for bucket, items in artifacts.items():
        n = len(items) if isinstance(items, list) else 0
        art_counts[bucket] = n
        owner = _ARTIFACT_BUCKET_OWNER.get(bucket)
        if owner and n and owner in agents and agents[owner]["status"] == "idle":
            agents[owner]["status"] = "completed"

    # Sub-agent / parallel-task fan-out: group planned tasks by their assigned
    # agent so the office can render helper workstations around a busy lead.
    parallel_tasks: dict[str, list[dict[str, Any]]] = {}
    for t in raw.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        agent = _lead_to_agent(t.get("assigned_agent")) or t.get("assigned_agent")
        if not agent or agent not in agents:
            continue
        parallel_tasks.setdefault(agent, []).append(
            {
                "taskId": t.get("task_id"),
                "label": t.get("objective") or t.get("task_type") or "task",
                "status": t.get("status") or "pending",
            }
        )
    # Only keep agents that genuinely have more than one task in flight.
    parallel_tasks = {
        a: ts
        for a, ts in parallel_tasks.items()
        if len(ts) > 1 and any(x["status"] not in {"done", "cancelled"} for x in ts)
    }

    # Current lead ring.
    lead_agent = _lead_to_agent(mission_lead)
    for a in agents.values():
        a["missionLead"] = a["agentId"] == lead_agent
        if a["status"] not in {"idle", "offline"}:
            a.setdefault("missionId", mission_id)

    # Terminal mission -> settle everyone.
    terminal = status in {
        "completed",
        "prototype_completed",
        "partial",
        "blocked",
        "failed",
        "cancelled",
    }
    if terminal:
        for a in agents.values():
            if a["status"] in {"working", "thinking", "planning", "reviewing", "handoff"}:
                a["status"] = "completed" if status.endswith("completed") else "idle"

    last_ev = events[-1] if events else None
    stage = _derive_stage(kinds, status)

    trace = raw.get("trace") if isinstance(raw.get("trace"), dict) else {}
    request_id = trace.get("request_id") or trace.get("requestId")

    return {
        "missionId": mission_id,
        "query": raw.get("query") or "",
        "status": status,
        "route": raw.get("route"),
        "stage": stage,
        "trace": {
            "requestId": request_id,
            "sessionId": trace.get("session_id") or trace.get("sessionId"),
        },
        "traceUrl": _langsmith_trace_url(request_id),
        "missionLead": mission_lead,
        "leadAgentId": lead_agent,
        "initialLead": initial_lead,
        "leadershipEpoch": int(raw.get("leadership_epoch") or 0),
        "startedAt": events[0].get("ts") if events else None,
        "lastEventAt": last_ev.get("ts") if last_ev else None,
        "lastSeq": int(last_ev.get("seq") or 0) if last_ev else 0,
        "agents": list(agents.values()),
        "board": {"steps": _board_state(kinds, art_counts, status)},
        "handoffs": _handoffs(raw, events),
        "parallelTasks": parallel_tasks,
        "artifacts": art_counts,
        "unresolvedQuestions": raw.get("unresolved_questions") or [],
        "limitations": raw.get("limitations") or [],
        "finalResponse": raw.get("final_response"),
        "timeline": normalize_events(events, mission_id=mission_id),
    }


def _apply_event_to_agents(agents: dict[str, dict[str, Any]], kind: str, ev: dict[str, Any]) -> None:
    ts = ev.get("ts")

    def touch(agent_id: str | None, **patch: Any) -> None:
        if not agent_id or agent_id not in agents:
            return
        agents[agent_id].update(patch)
        agents[agent_id]["lastEventAt"] = ts

    if kind == "mission_created":
        touch("coordinator", status="planning", currentAction="Planning the investigation")
    elif kind == "decomposition_created":
        touch("coordinator", status="planning", currentAction="Decomposing the question")
    elif kind == "decomposition_refined":
        touch(
            "coordinator",
            status="thinking",
            currentAction="Refining decomposition",
            currentArtifact=ev.get("decomposition_id"),
        )
    elif kind == "task_plan_created":
        touch("coordinator", status="working", currentAction="Building the task plan")
    elif kind == "task_wave_executed":
        lead = _lead_to_agent(ev.get("mission_lead"))
        touch("observer_agent", status="retrieving_evidence", currentAction="Gathering metrics")
        touch("anomaly_agent", status="working", currentAction="Scanning for anomalies")
        touch(lead, status="working", currentAction="Leading the investigation wave")
    elif kind == "task_specialists_activated":
        for want, agent_id in (
            ("diagnostic", "diagnostic_agent"),
            ("predictive", "prediction_agent"),
            ("prescriptive", "strategy_agent"),
        ):
            if want in (ev.get("intents") or []):
                touch(agent_id, status="working", currentAction="Specialist analysis")
    elif kind == "leadership_transfer":
        frm = _lead_to_agent(ev.get("from_agent"))
        to = _lead_to_agent(ev.get("to_agent") or ev.get("requested_target"))
        touch(frm, status="handoff", currentAction="Handing off leadership")
        touch(
            to,
            status="working",
            currentAction=ev.get("unresolved_question") or "Taking mission leadership",
            subquestion=ev.get("unresolved_question"),
        )
    elif kind == "skeptic_gate":
        touch("skeptic_agent", status="reviewing", currentAction="Reviewing the claim")
    elif kind == "skeptic_pass":
        touch("skeptic_agent", status="completed", currentAction="Verdict: PASS")
    elif kind in {"skeptic_revise", "skeptic_reject"}:
        verdict = "REVISE" if kind == "skeptic_revise" else "REJECT"
        touch("skeptic_agent", status="reviewing", currentAction=f"Verdict: {verdict}")
        touch("coordinator", status="working", currentAction="Planning remediation")
    elif kind == "remediation_planned":
        touch("coordinator", status="working", currentAction="Remediation planned")
    elif kind == "remediation_activated":
        touch(
            _lead_to_agent(ev.get("agent")) or ev.get("agent"),
            status="working",
            currentAction="Working the remediation task",
        )
    elif kind == "specialist_error":
        touch(
            _lead_to_agent(ev.get("agent")) or ev.get("agent"),
            status="failed",
            error=str(ev.get("error") or "specialist error"),
        )
    elif kind == "mission_budget_exhausted":
        touch("coordinator", status="blocked", error=str(ev.get("reason") or "budget exhausted"))


def _derive_stage(kinds: list[str], status: str) -> str:
    if status in {"completed", "prototype_completed"}:
        return "complete"
    if status in {"failed"}:
        return "failed"
    if status in {"blocked", "partial"}:
        return "blocked"
    order = [
        ("skeptic_gate", "review"),
        ("task_specialists_activated", "specialists"),
        ("leadership_transfer", "investigating"),
        ("task_wave_executed", "investigating"),
        ("task_plan_created", "planning"),
        ("decomposition_created", "decomposing"),
        ("mission_created", "intake"),
    ]
    for kind, stage in order:
        if kind in kinds:
            return stage
    return "intake"


def _board_state(
    kinds: list[str], art_counts: dict[str, int], status: str
) -> list[dict[str, str]]:
    done: set[str] = set()
    active: set[str] = set()
    if "task_wave_executed" in kinds or art_counts.get("evidence"):
        done.add("verify")
    if art_counts.get("anomaly"):
        done.add("verify")
    if "leadership_transfer" in kinds:
        done.add("frontier")
    elif "task_wave_executed" in kinds:
        active.add("frontier")
    if art_counts.get("hypothesis") or art_counts.get("causal"):
        active.add("diagnose")
    if "skeptic_gate" in kinds and (art_counts.get("causal") or art_counts.get("hypothesis")):
        done.add("diagnose")
    if art_counts.get("prediction"):
        done.add("forecast")
    if art_counts.get("strategy"):
        done.add("strategy")
    if "skeptic_pass" in kinds:
        done.add("skeptic")
    elif "skeptic_gate" in kinds:
        active.add("skeptic")

    if status in {"completed", "prototype_completed"}:
        done = {s for s, _ in _BOARD_STEPS}
        active = set()

    steps: list[dict[str, str]] = []
    for sid, label in _BOARD_STEPS:
        state = "done" if sid in done else "active" if sid in active else "pending"
        steps.append({"id": sid, "label": label, "state": state})
    # First non-done becomes active if nothing is explicitly active yet.
    if not any(s["state"] == "active" for s in steps):
        for s in steps:
            if s["state"] == "pending":
                s["state"] = "active"
                break
    return steps


def _handoffs(raw: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hist = raw.get("handoff_history")
    if isinstance(hist, list) and hist:
        return [
            {
                "from": h.get("from_agent"),
                "to": h.get("to_agent") or h.get("requested_target"),
                "reason": h.get("reason") or h.get("unresolved_question"),
                "epoch": h.get("epoch"),
            }
            for h in hist
            if isinstance(h, dict)
        ]
    out: list[dict[str, Any]] = []
    for ev in events:
        if str(ev.get("kind")) == "leadership_transfer":
            out.append(
                {
                    "from": ev.get("from_agent"),
                    "to": ev.get("to_agent") or ev.get("requested_target"),
                    "reason": ev.get("reason") or ev.get("unresolved_question"),
                    "epoch": ev.get("epoch"),
                }
            )
    return out
