"""swarm_v2 LangGraph mission control — live DECIDE → EXECUTE cycle.

LangGraph owns routing/loops. A closed-over ``SwarmV2Context`` holds live
clients (blackboard, transport, agents) that must never enter MissionState.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from seleric_swarm.coordinator.artifacts.claims import ClaimManager
from seleric_swarm.coordinator.artifacts.manager import ArtifactManager
from seleric_swarm.coordinator.contracts import (
    MissionPlan,
    MissionRequest,
    ProblemDecomposition,
    TaskSpec,
)
from seleric_swarm.coordinator.decomposition import (
    initial_decomposition,
    refine_from_evidence,
    refine_from_skeptic_followups,
)
from seleric_swarm.coordinator.execution.execution_engine import ExecutionEngine
from seleric_swarm.coordinator.governance.budget import check_swarm_budget
from seleric_swarm.coordinator.governance.completion_gate import decide_completion
from seleric_swarm.coordinator.governance.conflicts import (
    arbitrate_conflicts,
    conflict_limitations,
    detect_conflicts,
)
from seleric_swarm.coordinator.governance.remediation import (
    execute_targeted_remediation,
    targeted_remediation_plan,
)
from seleric_swarm.coordinator.governance.skeptic_gate import apply_skeptic_gate
from seleric_swarm.coordinator.governance.synthetic_guard import mission_synthetic_status
from seleric_swarm.coordinator.intake import (
    apply_full_flags,
    has_analytical_signal,
    normalize_query,
    resolve_mission_time_range,
)
from seleric_swarm.coordinator.leadership.frontier import LeadershipController, evaluate_frontier
from seleric_swarm.coordinator.observability.events import (
    CLAIM_CHALLENGED,
    CLAIM_PROPOSED,
    CLAIM_REJECTED,
    CLAIM_VALIDATED,
    DECOMPOSITION_CREATED,
    DECOMPOSITION_REFINED,
    LEADERSHIP_REJECTED,
    LEADERSHIP_TRANSFER,
    MISSION_BUDGET_EXHAUSTED,
    MISSION_COMPLETED,
    MISSION_CONTROL_PLANE,
    MISSION_CREATED,
    MISSION_PARTIAL,
    REMEDIATION_ACTIVATED,
    REMEDIATION_PLANNED,
    REMEDIATION_ROUND_DONE,
    SKEPTIC_GATE,
    SKEPTIC_PASS,
    SKEPTIC_REJECT,
    SKEPTIC_REVISE,
    TASK_PLAN_CREATED,
    TASK_SPECIALISTS_ACTIVATED,
    TASK_WAVE_EXECUTED,
    MissionEventEmitter,
    summarize_event_families,
)
from seleric_swarm.coordinator.planning.mission_planner import (
    build_mission_plan,
    tasks_from_subquestions,
    validate_plan,
)
from seleric_swarm.coordinator.policies import CoordinatorPolicies, load_coordinator_policies
from seleric_swarm.coordinator.routing.invocation import A2AAgentInvoker, assemble_team
from seleric_swarm.coordinator.state import empty_mission_extensions
from seleric_swarm.coordinator.synthesis.provenance_builder import build_provenance_summary
from seleric_swarm.coordinator.synthesis.response_builder import build_claim_aware_response
from seleric_swarm.leadership.manager import LeadershipManager
from seleric_swarm.observability.tracing import coordinator_task_metadata, traced_span
from seleric_swarm.orchestration.state import MissionState
from seleric_swarm.runtime import SwarmRuntime
from seleric_swarm.swarm.blackboard import Blackboard
from seleric_swarm.swarm.domain.base import DomainAgent
from seleric_swarm.swarm.domain.configs import build_domain_configs
from seleric_swarm.swarm.envelope import Intent, SwarmMessage
from seleric_swarm.swarm.mission import SwarmMission, SwarmMissionResult, TeamMember
from seleric_swarm.swarm.orchestrator import _initial_lead
from seleric_swarm.swarm.providers.base import ProviderBundle
from seleric_swarm.swarm.providers.fixtures import (
    DEFAULT_SCENARIO,
    build_fixture_bundle,
    load_scenario,
)
from seleric_swarm.swarm.providers.mcp_data import McpFetchStats, build_hybrid_bundle
from seleric_swarm.swarm.specialists.anomaly import AnomalyAgent
from seleric_swarm.swarm.specialists.diagnostic import DiagnosticAgent
from seleric_swarm.swarm.specialists.observer import ObserverAgent
from seleric_swarm.swarm.specialists.prediction import PredictionAgent
from seleric_swarm.swarm.specialists.skeptic import SkepticAgent
from seleric_swarm.swarm.specialists.strategy import StrategyAgent
from seleric_swarm.swarm.transport import InProcessTransport

WorkflowVersion = Literal["swarm_v2"]
ActivateFn = Callable[..., Awaitable[dict[str, Any]]]

_CHALLENGED_LIM = "Primary claim remains CHALLENGED after Skeptic REVISE."
_REASON_MAX_ROUNDS = "Max Skeptic remediation rounds exhausted; primary claim remains unresolved."
_REASON_STALLED = (
    "No validated root cause could be established: the Skeptic's follow-ups require evidence the "
    "available data does not contain."
)


@dataclass
class SwarmV2Context:
    """Live dependencies for one mission — never serialized into LangGraph state."""

    runtime: SwarmRuntime
    policies: CoordinatorPolicies
    blackboard: Blackboard
    mission: SwarmMission
    domains: dict[str, DomainAgent]
    activate: ActivateFn
    leadership: LeadershipController
    claim_mgr: ClaimManager
    artifact_mgr: ArtifactManager
    transport: InProcessTransport
    invoker: A2AAgentInvoker
    engine: ExecutionEngine
    decomposition: ProblemDecomposition
    plan: MissionPlan
    decompositions: list[dict[str, Any]] = field(default_factory=list)
    remediation_round: int = 0
    last_followup_signature: str | None = None
    managed_claims: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    investigate_iterations: int = 0
    frontier_moved: bool = False
    claim_id: str | None = None
    final_response: str = ""
    completion_detail: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    team: list[TeamMember] = field(default_factory=list)
    request: MissionRequest | None = None
    budget_exhausted: bool = False
    budget_reason: str | None = None
    emitter: MissionEventEmitter | None = None
    mcp_stats: McpFetchStats | None = None

    def emit(self, kind: str, **data: Any) -> dict[str, Any]:
        if self.emitter is None:
            self.emitter = MissionEventEmitter(self.blackboard)
        return self.emitter.emit(kind, **data)


def build_swarm_v2_graph(ctx: SwarmV2Context) -> Any:
    """Compile LangGraph with real DECIDE→EXECUTE node handlers."""

    g: StateGraph = StateGraph(MissionState)
    g.add_node("intake", _make_intake(ctx))
    g.add_node("decompose", _make_decompose(ctx))
    g.add_node("plan", _make_plan(ctx))
    g.add_node("assemble", _make_assemble(ctx))
    g.add_node("execute", _make_execute(ctx))
    g.add_node("refine", _make_refine(ctx))
    g.add_node("specialists", _make_specialists(ctx))
    g.add_node("skeptic_gate", _make_skeptic_gate(ctx))
    g.add_node("remediate", _make_remediate(ctx))
    g.add_node("complete", _make_complete(ctx))
    g.add_node("synthesize", _make_synthesize(ctx))

    g.add_edge(START, "intake")
    g.add_edge("intake", "decompose")
    g.add_edge("decompose", "plan")
    g.add_edge("plan", "assemble")
    g.add_edge("assemble", "execute")
    g.add_edge("execute", "refine")
    g.add_conditional_edges(
        "refine",
        _route_after_refine(ctx),
        {"execute": "execute", "specialists": "specialists"},
    )
    g.add_edge("specialists", "skeptic_gate")
    g.add_conditional_edges(
        "skeptic_gate",
        _route_after_skeptic,
        {"remediate": "remediate", "complete": "complete", "synthesize": "synthesize"},
    )
    g.add_edge("remediate", "skeptic_gate")
    g.add_edge("complete", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()


def _route_after_refine(ctx: SwarmV2Context):
    def _route(state: MissionState) -> str:
        max_iter = ctx.policies.leadership.max_transfers + 1
        budget = check_swarm_budget(dict(state), ctx.policies.budgets, agent_calls_needed=2)
        if not budget.ok:
            ctx.budget_exhausted = True
            ctx.budget_reason = budget.reason
            ctx.frontier_moved = False
            return "specialists"
        if ctx.frontier_moved and ctx.investigate_iterations < max_iter:
            return "execute"
        return "specialists"

    return _route


def _route_after_skeptic(state: MissionState) -> str:
    if state.get("status") == "remediating" and state.get("remediation_tasks"):
        round_n = int(state.get("remediation_round") or 0)
        budgets = state.get("budgets") or {}
        max_r = int(budgets.get("max_remediation_rounds") or 3)
        if round_n <= max_r and not state.get("budget_exhausted"):
            return "remediate"
    if state.get("challenged_claim_refs") and state.get("status") != "remediating":
        return "complete"
    if state.get("status") in {"validating", "running"} and not state.get("challenged_claim_refs"):
        # PASS path
        return "complete"
    if state.get("validated_claim_refs") or state.get("challenged_claim_refs") or state.get("rejected_claim_refs"):
        return "complete"
    return "synthesize"


def _make_intake(ctx: SwarmV2Context):
    async def intake(state: MissionState) -> dict[str, Any]:
        nq = state.get("normalized_query") or {}
        ctx.emit(
            MISSION_CREATED,
            intents=sorted(ctx.mission.intents or []),
            query_class=state.get("query_class"),
        )
        return {
            "status": "normalizing",
            "workflow_name": "swarm_v2",
            "workflow_version": "1.4.0",
            **empty_mission_extensions(),
            "normalized_query": nq,
            "events": list(ctx.blackboard.events),
        }

    return intake


def _make_decompose(ctx: SwarmV2Context):
    async def decompose(state: MissionState) -> dict[str, Any]:
        ctx.decompositions = [ctx.decomposition.model_dump()]
        ctx.emit(
            DECOMPOSITION_CREATED,
            decomposition_id=ctx.decomposition.decomposition_id,
            version=ctx.decomposition.version,
            template=ctx.decomposition.template,
        )
        return {
            "status": "decomposing",
            "decomposition_refs": [ctx.decomposition.decomposition_id],
            "current_decomposition_ref": ctx.decomposition.decomposition_id,
            "decompositions": ctx.decompositions,
            "objectives": [o.model_dump() for o in ctx.decomposition.objectives],
            "events": list(ctx.blackboard.events),
        }

    return decompose


def _make_plan(ctx: SwarmV2Context):
    async def plan_node(state: MissionState) -> dict[str, Any]:
        errors = validate_plan(ctx.plan)
        ctx.emit(TASK_PLAN_CREATED, tasks=len(ctx.plan.tasks), errors=errors)
        return {
            "status": "planning",
            "complexity_label": ctx.plan.complexity,
            "tasks": [t.model_dump() for t in ctx.plan.tasks],
            "task_graph": {"tasks": [t.model_dump() for t in ctx.plan.tasks]},
            "budgets": ctx.policies.budgets.model_dump(),
            "plan_blocked_reasons": errors,
            "events": list(ctx.blackboard.events),
        }

    return plan_node


def _make_assemble(ctx: SwarmV2Context):
    async def assemble(state: MissionState) -> dict[str, Any]:
        return {
            "status": "assembled",
            "team": [t.__dict__ for t in ctx.team],
            "initial_mission_lead": ctx.mission.initial_lead,
            "mission_lead": ctx.blackboard.mission_lead or ctx.mission.initial_lead,
            "events": list(ctx.blackboard.events),
        }

    return assemble


def _make_execute(ctx: SwarmV2Context):
    async def execute(state: MissionState) -> dict[str, Any]:
        """One investigation wave: observe → anomaly (+ optional DAG wave)."""
        lead = ctx.blackboard.mission_lead or ctx.mission.initial_lead
        usage = dict(state.get("usage") or {})
        precheck = check_swarm_budget(
            {**dict(state), "usage": usage, "handoff_history": list(ctx.blackboard.handoff_history)},
            ctx.policies.budgets,
            agent_calls_needed=2,
        )
        if not precheck.ok:
            ctx.budget_exhausted = True
            ctx.budget_reason = precheck.reason
            ctx.frontier_moved = False
            if precheck.reason and precheck.reason not in ctx.limitations:
                ctx.limitations.append(precheck.reason)
            ctx.emit(
                MISSION_BUDGET_EXHAUSTED,
                key=precheck.exhausted_key,
                reason=precheck.reason,
                usage=usage,
                budgets=ctx.policies.budgets.model_dump(),
            )
            return {
                "status": "partial",
                "budget_exhausted": True,
                "status_reason": precheck.reason,
                "mission_lead": lead,
                "usage": usage,
                "agent_calls": int(usage.get("agent_calls") or 0),
                "limitations": list(ctx.limitations),
                "events": list(ctx.blackboard.events),
            }

        ctx.investigate_iterations += 1
        await ctx.activate("observer_agent", f"Observe {lead} metrics")
        await ctx.activate("anomaly_agent", "Detect anomalies", Intent.MODEL_REQUEST)

        # Drive ready TaskSpecs through ExecutionEngine (idempotent A2A activations).
        tasks = [TaskSpec.model_validate(t) for t in (state.get("tasks") or [])]
        if not tasks:
            tasks = list(ctx.plan.tasks)
        # Mark observe/anomaly-style tasks done after specialist activation
        for t in tasks:
            if t.status in {"pending", "ready"} and t.assigned_agent in {
                "observer_agent",
                "anomaly_agent",
            }:
                t.status = "done"
        usage["agent_calls"] = int(usage.get("agent_calls") or 0) + 2
        usage["investigate_iterations"] = ctx.investigate_iterations
        usage["leadership_transfers"] = len(ctx.blackboard.handoff_history)

        post = check_swarm_budget(
            {**dict(state), "usage": usage, "handoff_history": list(ctx.blackboard.handoff_history)},
            ctx.policies.budgets,
        )
        if not post.ok:
            ctx.budget_exhausted = True
            ctx.budget_reason = post.reason
            ctx.frontier_moved = False
            if post.reason and post.reason not in ctx.limitations:
                ctx.limitations.append(post.reason)
            ctx.emit(
                MISSION_BUDGET_EXHAUSTED,
                key=post.exhausted_key,
                reason=post.reason,
                usage=usage,
                budgets=ctx.policies.budgets.model_dump(),
            )

        ctx.emit(
            TASK_WAVE_EXECUTED,
            iteration=ctx.investigate_iterations,
            mission_lead=lead,
            ready_done=["observer_agent", "anomaly_agent"],
            budget_exhausted=ctx.budget_exhausted,
            decide_execute=True,
        )
        return {
            "status": "partial" if ctx.budget_exhausted else "running",
            "budget_exhausted": ctx.budget_exhausted,
            "status_reason": ctx.budget_reason,
            "mission_lead": lead,
            "active_specialist": ctx.blackboard.active_specialist,
            "evidence_refs": ctx.blackboard.refs_by_type("evidence"),
            "anomaly_refs": ctx.blackboard.refs_by_type("anomaly"),
            "tasks": [t.model_dump() for t in tasks],
            "usage": usage,
            "agent_calls": usage["agent_calls"],
            "coordinator_iterations": ctx.investigate_iterations,
            "limitations": list(ctx.limitations),
            "events": list(ctx.blackboard.events),
        }

    return execute


def _make_refine(ctx: SwarmV2Context):
    async def refine(state: MissionState) -> dict[str, Any]:
        ctx.frontier_moved = False
        lead = ctx.blackboard.mission_lead or ctx.mission.initial_lead
        refined = refine_from_evidence(
            ctx.decomposition,
            evidence=ctx.blackboard.by_type("evidence"),
            anomalies=ctx.blackboard.by_type("anomaly"),
            reason="evidence_driven_frontier",
            policies=ctx.policies,
        )
        patch: dict[str, Any] = {"status": "running"}
        if refined.decomposition_id != ctx.decomposition.decomposition_id:
            ctx.decomposition.status = "superseded"  # type: ignore[misc]
            ctx.decomposition = refined
            ctx.decompositions.append(refined.model_dump())
            ctx.frontier_moved = True
            # Append new ready tasks from refined subquestions
            new_tasks = tasks_from_subquestions(
                mission_id=ctx.mission.mission_id,
                decomposition=refined,
                subquestions=[sq for sq in refined.subquestions if sq.question_id in set(refined.questions_added)],
            )
            existing = [TaskSpec.model_validate(t) for t in (state.get("tasks") or [])]
            existing_ids = {t.task_id for t in existing}
            for t in new_tasks:
                if t.task_id not in existing_ids:
                    existing.append(t)
            patch["tasks"] = [t.model_dump() for t in existing]
            patch["decompositions"] = ctx.decompositions
            patch["current_decomposition_ref"] = refined.decomposition_id
            patch["decomposition_refs"] = [d.get("decomposition_id") for d in ctx.decompositions]
            ctx.emit(
                DECOMPOSITION_REFINED,
                decomposition_id=refined.decomposition_id,
                version=refined.version,
                reason=refined.reason_for_revision,
                questions_added=refined.questions_added,
                questions_retired=refined.questions_retired,
            )

        frontier = evaluate_frontier(
            anomalies=ctx.blackboard.by_type("anomaly"),
            evidence=ctx.blackboard.by_type("evidence"),
            current_lead=lead,
            topology=ctx.policies.domain_topology,
        )
        lead_domain = lead.removesuffix("_agent")
        topo = ctx.policies.domain_topology.get(lead_domain)
        neighbors = [d for vs in topo.values() for d in vs] if topo else None
        proposal = ctx.domains[lead].evaluate_handoff(ctx.blackboard, topology_neighbors=neighbors)
        if proposal is not None:
            decision = ctx.leadership.decide_transfer(
                ctx.blackboard.leadership_state(),
                proposal.to_leadership_proposal(ctx.mission.mission_id),
            )
            if decision.get("accepted"):
                ctx.blackboard.handoff_history = list(decision["handoff_history"])
                ctx.blackboard.leadership_epoch = int(decision["leadership_epoch"])
                ctx.blackboard.mission_lead = decision["mission_lead"]
                ctx.frontier_moved = True
                ctx.emit(
                    LEADERSHIP_TRANSFER,
                    **decision["handoff_history"][-1],
                    frontier=frontier,
                )
                patch["mission_lead"] = decision["mission_lead"]
                patch["leadership_epoch"] = decision["leadership_epoch"]
                patch["handoff_history"] = list(ctx.blackboard.handoff_history)
            else:
                ctx.emit(LEADERSHIP_REJECTED, reason=decision.get("error_message"))
                ctx.frontier_moved = False

        patch["events"] = list(ctx.blackboard.events)
        patch["unresolved_questions"] = [
            sq.question
            for sq in ctx.decomposition.subquestions
            if sq.status in {"pending", "ready"}
        ]
        return patch

    return refine


def _make_specialists(ctx: SwarmV2Context):
    async def specialists(state: MissionState) -> dict[str, Any]:
        intents = ctx.mission.intents
        usage = dict(state.get("usage") or {})
        to_run: list[tuple[str, str, Intent]] = []
        intent_set = set(intents or [])
        if "diagnostic" in intent_set or "executive_health" in intent_set:
            to_run.append(("diagnostic_agent", "Generate + causally validate hypotheses", Intent.MODEL_REQUEST))
        if "predictive" in intent_set:
            to_run.append(("prediction_agent", "Forecast impact if trend continues", Intent.MODEL_REQUEST))
        if "prescriptive" in intent_set:
            to_run.append(("strategy_agent", "Design mechanism-fit interventions", Intent.MODEL_REQUEST))

        activated = 0
        for agent_id, objective, intent in to_run:
            budget = check_swarm_budget(
                {
                    **dict(state),
                    "usage": usage,
                    "handoff_history": list(ctx.blackboard.handoff_history),
                },
                ctx.policies.budgets,
                agent_calls_needed=1,
            )
            if not budget.ok:
                ctx.budget_exhausted = True
                ctx.budget_reason = budget.reason
                if budget.reason and budget.reason not in ctx.limitations:
                    ctx.limitations.append(budget.reason)
                ctx.emit(
                    MISSION_BUDGET_EXHAUSTED,
                    key=budget.exhausted_key,
                    reason=budget.reason,
                    skipped_agent=agent_id,
                    usage=usage,
                )
                break
            await ctx.activate(agent_id, objective, intent)
            activated += 1
            usage["agent_calls"] = int(usage.get("agent_calls") or 0) + 1

        usage["leadership_transfers"] = len(ctx.blackboard.handoff_history)
        if activated:
            ctx.emit(
                TASK_SPECIALISTS_ACTIVATED,
                activated=activated,
                intents=sorted(intents),
            )
        return {
            "status": "partial" if ctx.budget_exhausted else "running",
            "budget_exhausted": ctx.budget_exhausted,
            "status_reason": ctx.budget_reason or state.get("status_reason"),
            "hypothesis_refs": ctx.blackboard.refs_by_type("hypothesis"),
            "causal_refs": ctx.blackboard.refs_by_type("causal"),
            "prediction_refs": ctx.blackboard.refs_by_type("prediction"),
            "strategy_refs": ctx.blackboard.refs_by_type("strategy"),
            "usage": usage,
            "agent_calls": int(usage.get("agent_calls") or 0),
            "limitations": list(ctx.limitations),
            "events": list(ctx.blackboard.events),
            "specialists_activated": activated,
        }

    return specialists


def _make_skeptic_gate(ctx: SwarmV2Context):
    async def skeptic_gate(state: MissionState) -> dict[str, Any]:
        intents = set(ctx.mission.intents or [])
        needs_skeptic = bool(intents & {"diagnostic", "predictive", "prescriptive", "executive_health"})
        if not needs_skeptic:
            return {"status": "validating", "events": list(ctx.blackboard.events)}

        # First entry vs re-check after remediation
        if ctx.remediation_round == 0 and not ctx.claim_id:
            await ctx.activate("skeptic_agent", "Attack candidate conclusion", Intent.CHALLENGE)
        skeptic_art = (ctx.blackboard.by_type("skeptic") or [{}])[-1]
        verdict = skeptic_art.get("verdict")
        followups = list(skeptic_art.get("required_followups") or [])

        if not ctx.claim_id:
            retained_hyps = [
                h for h in ctx.blackboard.by_type("hypothesis") if h.get("status") == "retained"
            ]
            claim = ctx.claim_mgr.propose(
                mission_id=ctx.mission.mission_id,
                statement=retained_hyps[0]["statement"] if retained_hyps else "candidate conclusion",
                claim_type="causal",
                support_refs=ctx.blackboard.refs_by_type("causal"),
                origin_agent="diagnostic_agent",
                synthetic=True,
            )
            ctx.claim_id = claim.claim_id
            ctx.emit(CLAIM_PROPOSED, claim_id=claim.claim_id, claim_type=claim.claim_type)

        gate = apply_skeptic_gate(
            claim_manager=ctx.claim_mgr,
            claim_id=ctx.claim_id,
            verdict=str(verdict or "PASS"),
            followups=followups,
            mission_id=ctx.mission.mission_id,
            remediation_round=ctx.remediation_round,
            max_remediation_rounds=ctx.policies.budgets.max_remediation_rounds,
            prev_followup_signature=ctx.last_followup_signature,
        )
        ctx.last_followup_signature = gate.get("followup_signature") or ctx.last_followup_signature
        ctx.managed_claims = ctx.claim_mgr.dump()
        buckets = ctx.claim_mgr.buckets()
        skeptic_kind = {
            "PASS": SKEPTIC_PASS,
            "REVISE": SKEPTIC_REVISE,
            "REJECT": SKEPTIC_REJECT,
        }.get(str(verdict or "").upper(), SKEPTIC_GATE)
        ctx.emit(skeptic_kind, verdict=verdict, claim_id=ctx.claim_id, gate_event=gate.get("event"))
        if buckets.get("validated_claim_refs"):
            ctx.emit(CLAIM_VALIDATED, claim_refs=buckets["validated_claim_refs"])
        if buckets.get("challenged_claim_refs"):
            ctx.emit(CLAIM_CHALLENGED, claim_refs=buckets["challenged_claim_refs"])
        if buckets.get("rejected_claim_refs"):
            ctx.emit(CLAIM_REJECTED, claim_refs=buckets["rejected_claim_refs"])

        rem_tasks = []
        status = str(gate.get("mission_status") or "validating")
        status_reason = gate.get("status_reason")
        _reason_text = {
            "max_remediation_rounds_exhausted": _REASON_MAX_ROUNDS,
            "remediation_stalled_no_new_information": _REASON_STALLED,
        }
        if status_reason:
            _line = _reason_text.get(status_reason, str(status_reason))
            if _line not in ctx.limitations:
                ctx.limitations.append(_line)
        if verdict == "REVISE" and gate.get("remediation"):
            status = "remediating"
            rem_tasks = list((gate.get("remediation") or {}).get("tasks") or [])
            ctx.emit(
                REMEDIATION_PLANNED,
                avoid_full_diagnostic=(gate.get("remediation") or {}).get("avoid_full_diagnostic"),
                kinds=(gate.get("remediation") or {}).get("kinds"),
            )
        elif verdict == "REJECT" and gate.get("remediation"):
            status = "remediating"
            rem_tasks = list((gate.get("remediation") or {}).get("tasks") or [])
            ctx.emit(REMEDIATION_PLANNED, kinds=(gate.get("remediation") or {}).get("kinds"))
        elif verdict == "PASS":
            status = "validating"
        return {
            "status": status,
            "status_reason": status_reason or state.get("status_reason"),
            "skeptic_refs": ctx.blackboard.refs_by_type("skeptic"),
            "skeptic_findings": [{"status": "passed" if verdict == "PASS" else "open", "verdict": verdict}],
            "managed_claims": ctx.managed_claims,
            "claim_refs": buckets["claim_refs"],
            "validated_claim_refs": buckets["validated_claim_refs"],
            "challenged_claim_refs": buckets["challenged_claim_refs"],
            "rejected_claim_refs": buckets["rejected_claim_refs"],
            "remediation_tasks": rem_tasks,
            "remediation_round": ctx.remediation_round,
            "budgets": ctx.policies.budgets.model_dump(),
            "limitations": list(ctx.limitations),
            "events": list(ctx.blackboard.events),
        }

    return skeptic_gate


def _make_remediate(ctx: SwarmV2Context):
    async def remediate(state: MissionState) -> dict[str, Any]:
        followups = []
        for t in state.get("remediation_tasks") or []:
            followups.append((t.get("metadata") or {}).get("followup") or t)
        if not followups:
            skeptic_art = (ctx.blackboard.by_type("skeptic") or [{}])[-1]
            followups = list(skeptic_art.get("required_followups") or [])

        # Extend decomposition from Skeptic follow-ups (avoid duplicate version spam)
        prev_id = ctx.decomposition.decomposition_id
        ctx.decomposition = refine_from_skeptic_followups(ctx.decomposition, followups, policies=ctx.policies)
        if ctx.decomposition.decomposition_id != prev_id or not any(d.get("decomposition_id") == prev_id for d in ctx.decompositions):
            ctx.decompositions.append(ctx.decomposition.model_dump())

        plan_rem = targeted_remediation_plan(
            mission_id=ctx.mission.mission_id, followups=followups
        )

        async def _activate(
            agent_id: str, objective: str, intent: str = "task_request", extra: dict | None = None
        ):
            intent_enum = {
                "task_request": Intent.TASK_REQUEST,
                "model_request": Intent.MODEL_REQUEST,
                "challenge": Intent.CHALLENGE,
            }.get(intent, Intent.TASK_REQUEST)
            if extra:
                ctx.emit(REMEDIATION_ACTIVATED, agent=agent_id, **extra)
            followup = (extra or {}).get("followup") if extra else None
            task_id = None
            subquestion_id = None
            if isinstance(followup, dict):
                task_id = followup.get("task_id")
                subquestion_id = followup.get("subquestion_id")
            return await ctx.activate(
                agent_id,
                objective,
                intent_enum,
                task_id=task_id,
                subquestion_id=subquestion_id,
            )

        await execute_targeted_remediation(plan=plan_rem, activate=_activate)
        ctx.remediation_round += 1
        await ctx.activate("skeptic_agent", "Re-check after targeted remediation", Intent.CHALLENGE)
        ctx.emit(REMEDIATION_ROUND_DONE, remediation_round=ctx.remediation_round)

        return {
            "status": "validating",
            "remediation_round": ctx.remediation_round,
            "remediation_tasks": [],  # cleared so route goes to complete after next gate
            "decompositions": ctx.decompositions,
            "current_decomposition_ref": ctx.decomposition.decomposition_id,
            "events": list(ctx.blackboard.events),
        }

    return remediate


def _make_complete(ctx: SwarmV2Context):
    async def complete(state: MissionState) -> dict[str, Any]:
        buckets = ctx.claim_mgr.buckets()
        prov = ctx.blackboard.synthetic_summary()
        conflicts = arbitrate_conflicts(
            detect_conflicts(
                {
                    "evidence": ctx.blackboard.by_type("evidence"),
                    "hypotheses": ctx.blackboard.by_type("hypothesis"),
                    "predictions": ctx.blackboard.by_type("prediction"),
                    "strategies": ctx.blackboard.by_type("strategy"),
                    "contradictions": [],
                    "normalized_query": {
                        "primary_metric": (ctx.mission.context or {}).get("resolved_metric")
                        or (ctx.mission.context or {}).get("primary_metric")
                    },
                }
            )
        )
        ctx.conflicts = conflicts
        for line in conflict_limitations(conflicts):
            if line not in ctx.limitations:
                ctx.limitations.append(line)
        state_for_completion: dict[str, Any] = {
            **empty_mission_extensions(),
            "objectives": [o.model_dump() for o in ctx.decomposition.objectives],
            "validated_claim_refs": buckets["validated_claim_refs"],
            "challenged_claim_refs": buckets["challenged_claim_refs"],
            "rejected_claim_refs": buckets["rejected_claim_refs"],
            "evidence_gaps": list(state.get("evidence_gaps") or []),
            "conflicts": conflicts,
            "tasks": list(state.get("tasks") or []),
            "remediation_tasks": list(state.get("remediation_tasks") or []),
            "synthetic": bool(prov.get("all_synthetic")),
            "decompositions": ctx.decompositions,
            "claims": [{"gate_status": "passed"} for _ in buckets["validated_claim_refs"]],
            "evidence": ctx.blackboard.by_type("evidence"),
            "status": "completed" if buckets["validated_claim_refs"] else "partial",
            "skeptic_findings": list(state.get("skeptic_findings") or []),
            "budgets": ctx.policies.budgets.model_dump(),
            "usage": {
                **dict(state.get("usage") or {}),
                "leadership_transfers": len(ctx.blackboard.handoff_history),
                "remediation_rounds": ctx.remediation_round,
            },
            "handoff_history": list(ctx.blackboard.handoff_history),
            "budget_exhausted": ctx.budget_exhausted or bool(state.get("budget_exhausted")),
            "status_reason": ctx.budget_reason or state.get("status_reason"),
        }
        if ctx.budget_exhausted and ctx.budget_reason and ctx.budget_reason not in ctx.limitations:
            ctx.limitations.append(ctx.budget_reason)
        if buckets["validated_claim_refs"] and state_for_completion["objectives"]:
            state_for_completion["objectives"][0]["status"] = "satisfied"

        completion = decide_completion(state_for_completion)
        ctx.completion_detail = completion.model_dump()
        internal = mission_synthetic_status(
            all_synthetic=bool(prov.get("all_synthetic")),
            mixed=bool(prov.get("mixed")),
            complete=completion.complete,
        )
        status = completion.status
        # Preserve prototype_completed for synthetic-complete missions (plan DoD).
        if internal == "prototype_completed" and status == "completed":
            status = "prototype_completed"
        if status == "prototype_completed" and "prototype_completed: synthetic evidence only" not in ctx.limitations:
            ctx.limitations.append("prototype_completed: synthetic evidence only")

        _final_reason_text = {
            "max_remediation_rounds_exhausted": _REASON_MAX_ROUNDS,
            "remediation_stalled_no_new_information": _REASON_STALLED,
        }
        _sr = state.get("status_reason") or ctx.budget_reason
        challenged = buckets["challenged_claim_refs"]
        if challenged:
            status = "partial" if status == "completed" else status
            # A specific status_reason already explains *why* the claim is
            # challenged; only add the generic line when nothing else does.
            explained = _sr in _final_reason_text
            if not explained and _CHALLENGED_LIM not in ctx.limitations:
                ctx.limitations.append(_CHALLENGED_LIM)
        if _sr in _final_reason_text and _final_reason_text[_sr] not in ctx.limitations:
            ctx.limitations.append(_final_reason_text[_sr])
        # final pass: unique, order-preserving
        ctx.limitations[:] = list(dict.fromkeys(ctx.limitations))

        if ctx.budget_exhausted and status == "completed":
            status = "partial"

        ctx.provenance = build_provenance_summary(
            artifact_refs={
                t: ctx.blackboard.refs_by_type(t)
                for t in ("evidence", "anomaly", "hypothesis", "causal", "prediction", "strategy", "skeptic")
            },
            synthetic_summary=prov,
            claim_refs=buckets.get("claim_refs"),
            decomposition_refs=[str(d.get("decomposition_id") or "") for d in ctx.decompositions],
        )
        event_kind = MISSION_COMPLETED if completion.complete and not ctx.budget_exhausted else MISSION_PARTIAL
        ctx.emit(
            event_kind,
            status=status,
            budget_exhausted=ctx.budget_exhausted,
            budget_reason=ctx.budget_reason,
        )
        return {
            "status": status,
            "budget_exhausted": ctx.budget_exhausted,
            "status_reason": ctx.budget_reason or state.get("status_reason"),
            "completion_detail": ctx.completion_detail,
            "completion_decision": completion.status,
            "conflicts": conflicts,
            "limitations": list(ctx.limitations),
            "synthetic": bool(prov.get("synthetic")),
            "events": list(ctx.blackboard.events),
        }

    return complete


def _make_synthesize(ctx: SwarmV2Context):
    async def synthesize(state: MissionState) -> dict[str, Any]:
        ctx.final_response = build_claim_aware_response(
            ctx.blackboard,
            ctx.mission,
            managed_claims=ctx.managed_claims,
            completion_status=str(state.get("completion_decision") or state.get("status")),
            policies=ctx.policies,
            conflicts=ctx.conflicts,
            extra_limitations=list(ctx.limitations),
        )
        return {
            "final_response": ctx.final_response,
            "events": list(ctx.blackboard.events),
        }

    return synthesize


def _unsupported_swarm_result(
    *,
    mission_id: str,
    query: str,
    request_id: str,
    session_id: str,
    runtime: SwarmRuntime,
    reason: str,
) -> SwarmMissionResult:
    """Terminal result for a query with no resolvable metric or analysis intent.

    Prevents the swarm from fabricating a synthetic diagnosis against fixture
    defaults for noise / off-topic input (see ``has_analytical_signal``).
    """
    result = SwarmMissionResult(
        mission_id=mission_id,
        status="failed",
        query=query,
        complexity="L0",
        initial_mission_lead="coordinator_agent",
        mission_lead="coordinator_agent",
        leadership_epoch=0,
        team=[],
        handoff_history=[],
        artifacts={
            t: []
            for t in (
                "evidence",
                "anomaly",
                "hypothesis",
                "causal",
                "prediction",
                "strategy",
                "skeptic",
            )
        },
        final_response=reason,
        limitations=[reason],
        synthetic=False,
        events=[],
        error_code="ROUTING_UNSUPPORTED",
    )
    try:
        from seleric_swarm.swarm.orchestrator import _swarm_mission_view

        runtime.store.put(
            _swarm_mission_view(result, request_id, session_id),
            {
                "route": "swarm",
                "workflow": "swarm_v2",
                "trace": {"request_id": request_id, "session_id": session_id},
                **result.as_dict(),
            },
        )
    except Exception:  # noqa: S110 - persistence must never fail the response
        pass
    return result


async def run_swarm_v2_mission(
    runtime: SwarmRuntime,
    *,
    query: str,
    timezone: str = "Asia/Kolkata",
    as_of: str | None = None,
    providers: ProviderBundle | None = None,
    scenario_id: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    full_skeptic: bool = False,
    full_diagnostic: bool = False,
    full_prediction: bool = False,
    execution_mode: str = "production",
    budget_overrides: dict[str, Any] | None = None,
    mission_id: str | None = None,
) -> SwarmMissionResult:
    """Execute Coordinator V1 via LangGraph DECIDE→EXECUTE cycle."""
    if not scenario_id:
        raise ValueError(
            "scenario_id is required for swarm missions "
            f"(e.g. '{DEFAULT_SCENARIO}' for the fixture pack)"
        )
    mid = mission_id or f"MS-{uuid4().hex[:10]}"
    mission_id = mid
    rid = request_id or uuid4().hex
    sid = session_id or uuid4().hex
    if not has_analytical_signal(query, runtime.metrics):
        return _unsupported_swarm_result(
            mission_id=mission_id,
            query=query,
            request_id=rid,
            session_id=sid,
            runtime=runtime,
            reason=(
                "Query does not name a known metric or a supported analysis "
                "(diagnose / forecast / compare / recommend / health check); "
                "rephrase with a metric and an intent, e.g. 'why did CAC increase?'."
            ),
        )
    policies = load_coordinator_policies(
        getattr(runtime.settings, "coordinator_policies_path", None)
    )
    if budget_overrides:
        policies = policies.model_copy(
            update={"budgets": policies.budgets.model_copy(update=budget_overrides)}
        )
    scenario = load_scenario(scenario_id)
    mode: Literal["production", "staging", "fixture"] = (
        execution_mode if execution_mode in {"production", "staging", "fixture"} else "production"  # type: ignore[assignment]
    )
    mcp_stats: McpFetchStats | None = None
    if providers is None:
        if mode != "fixture":
            providers, mcp_stats = build_hybrid_bundle(
                scenario_id,
                mcp=runtime.mcp,
                execution_mode=mode,
                metrics=runtime.metrics,
                agents=runtime.agents,
            )
        else:
            providers = build_fixture_bundle(scenario_id)
    request = MissionRequest(
        query=query,
        session_id=sid,
        execution_mode=mode,
    )
    normalized = await normalize_query(
        query,
        timezone=timezone,
        as_of=as_of,
        metrics=runtime.metrics,
        mcp=runtime.mcp if mode != "fixture" else None,
        agent_id="coordinator_agent",
        runtime=runtime if mode != "fixture" else None,
        mission_id=mid,
        request_id=rid,
        session_id=sid,
    )
    # normalized.candidate_domains is already LLM+catalogue grounded when a
    # live runtime was given (falls back to the regex domain guesser inside
    # normalize_query itself when not) — no separate keyword pass needed here.
    initial_lead = (
        f"{normalized.candidate_domains[0]}_agent" if normalized.candidate_domains else _initial_lead(query)
    )
    # Single source of truth: intake intents (LLM+catalogue classified, includes
    # executive_health → diagnostic), plus full_* flags that force specialist
    # activation. Fold into normalized so decomposition / plan see the same
    # intents the mission executes with.
    intents = apply_full_flags(
        set(normalized.intents),
        full_diagnostic=full_diagnostic,
        full_prediction=full_prediction,
        full_skeptic=full_skeptic,
    )
    if "executive_health" in intents:
        intents.add("diagnostic")
    normalized = normalized.model_copy(update={"intents": sorted(intents)})
    # Live MCP uses the query window; fixture mode keeps the scenario observation window
    # (and as_of extension) via resolve_mission_time_range.
    if mode != "fixture" and normalized.time_range is not None:
        time_range = {
            "start": normalized.time_range.start,
            "end": normalized.time_range.end,
            "timezone": normalized.time_range.timezone or timezone,
            "label": normalized.time_range.label,
        }
    else:
        time_range = resolve_mission_time_range(
            scenario, timezone=timezone, as_of=as_of, normalized=normalized
        )
    decomposition = initial_decomposition(mission_id=mission_id, normalized=normalized, policies=policies)
    plan = build_mission_plan(
        mission_id=mission_id,
        normalized=normalized,
        decomposition=decomposition,
        initial_lead=initial_lead,
        policies=policies,
    )
    plan_errors = validate_plan(plan)

    mission = SwarmMission(
        mission_id=mission_id,
        query=query,
        time_range=time_range,
        intents=intents,
        complexity=plan.complexity,
        initial_lead=initial_lead,
        context={
            # Only fall back to a domain default when intake couldn't resolve a
            # specific metric from the query — never clobber a correctly
            # resolved one (e.g. "why did ROAS drop" must diagnose ROAS, not CAC,
            # even though ROAS keywords route the initial lead to performance_agent).
            "primary_metric": (
                normalized.primary_metric
                or ("metric.cac" if initial_lead == "performance_agent" else "metric.net_sales")
            ),
            "resolved_metric": normalized.primary_metric,
            "decomposition_id": decomposition.decomposition_id,
            "plan_errors": plan_errors,
            "degradation_started_at": (scenario.get("domains", {}).get("technical", {}) or {}).get(
                "degradation_started_at"
            ),
        },
    )

    domains: dict[str, DomainAgent] = {
        cfg.agent_id: DomainAgent(
            cfg, providers.data_for(cfg.domain), peers=providers.data, metrics=runtime.metrics
        )
        for cfg in build_domain_configs(runtime.metrics, runtime.agents).values()
    }
    observer = ObserverAgent(providers, domains)
    anomaly = AnomalyAgent(providers)
    if full_diagnostic:
        from seleric_swarm.agents.diagnostic.swarm_bridge import SwarmDiagnosticSpecialist

        diagnostic: Any = SwarmDiagnosticSpecialist(providers, scenario=scenario)
    else:
        diagnostic = DiagnosticAgent(providers)
    if full_prediction:
        from seleric_swarm.agents.prediction.swarm_bridge import SwarmPredictionSpecialist

        prediction: Any = SwarmPredictionSpecialist(providers, scenario=scenario)
    else:
        prediction = PredictionAgent(providers)
    strategy = StrategyAgent(providers)
    if full_skeptic:
        from seleric_swarm.agents.skeptic.swarm_bridge import SwarmSkepticSpecialist

        skeptic: Any = SwarmSkepticSpecialist(providers)
    else:
        skeptic = SkepticAgent(providers)

    blackboard = Blackboard(mission_id)
    blackboard.mission_lead = initial_lead
    transport = InProcessTransport()
    for spec in (observer, anomaly, diagnostic, prediction, strategy, skeptic):

        async def _handler(msg: SwarmMessage, _spec=spec) -> dict[str, Any]:
            ids = await _spec.run(blackboard, mission)
            return {"ok": True, "artifact_refs": ids, "produced": _spec.produces}

        transport.register(spec.agent_id, _handler)

    # Mutable holder so activate spans see live remediation_round / decomposition.
    _live: dict[str, Any] = {"ctx": None}

    async def activate(
        agent_id: str,
        objective: str,
        intent: Intent = Intent.TASK_REQUEST,
        *,
        task_id: str | None = None,
        subquestion_id: str | None = None,
    ) -> dict[str, Any]:
        blackboard.active_specialist = agent_id
        live_ctx: SwarmV2Context | None = _live.get("ctx")
        decomp = live_ctx.decomposition if live_ctx is not None else decomposition
        rem_round = live_ctx.remediation_round if live_ctx is not None else 0
        meta = coordinator_task_metadata(
            request_id=rid,
            session_id=sid,
            mission_id=mission_id,
            workflow_name="swarm_v2",
            workflow_version="1.4.0",
            agent_name=agent_id,
            agent_version="1.4.0",
            task_id=task_id,
            subquestion_id=subquestion_id,
            active_specialist=agent_id,
            mission_lead=blackboard.mission_lead or initial_lead,
            remediation_round=rem_round,
            decomposition_id=decomp.decomposition_id,
            decomposition_version=decomp.version,
            leadership_epoch=blackboard.leadership_epoch,
            synthetic=(mode == "fixture"),
        )
        msg = SwarmMessage.request(
            mission_id=mission_id,
            from_agent="coordinator_agent",
            to_agent=agent_id,
            intent=intent,
            objective=objective,
            scope=time_range,
            mission_context={
                **blackboard.leadership_state(),
                "decomposition_id": decomp.decomposition_id,
                "decomposition_version": decomp.version,
                "workflow_version": "1.4.0",
                "task_id": task_id,
                "subquestion_id": subquestion_id,
                "remediation_round": rem_round,
            },
        )
        with traced_span(
            f"swarm.activate.{agent_id}",
            meta,
            runtime.settings.langsmith_tracing,
            inputs={"objective": objective, "intent": str(intent), "task_id": task_id},
            tags=["swarm_v2", "activate", agent_id],
        ) as span:
            reply = await transport.send(msg)
            span.set_outputs(
                {
                    "ok": bool((reply or {}).get("ok", True)),
                    "artifact_refs": list((reply or {}).get("artifact_refs") or []),
                    "mission_lead": blackboard.mission_lead,
                    "active_specialist": agent_id,
                    "remediation_round": rem_round,
                }
            )
            return reply or {"ok": True}

    team_rows = assemble_team(
        agents=runtime.agents,
        required_specialists=plan.required_specialists,
        optional_specialists=plan.optional_specialists,
        domain_lead=initial_lead,
    )
    team = [TeamMember(t["agent_id"], t.get("axis") or "specialist", "support") for t in team_rows]
    if team:
        team[0] = TeamMember(initial_lead, "domain", "lead")

    invoker = A2AAgentInvoker(transport, from_agent="coordinator_agent")
    engine = ExecutionEngine(invoker, budgets=policies.budgets)
    emitter = MissionEventEmitter(
        blackboard, workflow_name="swarm_v2", workflow_version="1.4.0"
    )
    ctx = SwarmV2Context(
        runtime=runtime,
        policies=policies,
        blackboard=blackboard,
        mission=mission,
        domains=domains,
        activate=activate,
        leadership=LeadershipController(LeadershipManager(), policies),
        claim_mgr=ClaimManager(),
        artifact_mgr=ArtifactManager(blackboard),
        transport=transport,
        invoker=invoker,
        engine=engine,
        decomposition=decomposition,
        plan=plan,
        team=team,
        request=request,
        emitter=emitter,
        mcp_stats=mcp_stats,
    )
    _live["ctx"] = ctx
    if mode != "fixture" and mcp_stats is not None and mcp_stats.mcp_hits == 0:
        # Staging/production asked for MCP path but nothing hit yet — note intent.
        line = (
            f"execution_mode={mode}: MCP-preferring providers active "
            f"(capabilities available: {sorted(runtime.mcp.capabilities) or ['none']})"
        )
        if line not in ctx.limitations:
            ctx.limitations.append(line)
    for reason in normalized.unresolved_semantics:
        if reason == "primary_metric_unresolved":
            line = "Primary metric could not be resolved from the query; results may use fixture defaults."
        else:
            line = reason
        if line not in ctx.limitations:
            ctx.limitations.append(line)

    initial_state: MissionState = {
        "mission_id": mission_id,
        "request_id": rid,
        "session_id": sid,
        "user_query": query,
        "timezone": timezone,
        "as_of": as_of,
        "status": "received",
        "workflow_name": "swarm_v2",
        "workflow_version": "1.4.0",
        "normalized_query": normalized.model_dump(),
        "mission_lead": initial_lead,
        "initial_mission_lead": initial_lead,
        "leadership_epoch": 0,
        "execution_mode": request.execution_mode,
        "budgets": policies.budgets.model_dump(),
        "usage": {},
        "events": [],
        "budget_exhausted": False,
        "langsmith_tracing": bool(runtime.settings.langsmith_tracing),
        "trace_base": {
            "request_id": rid,
            "session_id": sid,
            "workflow_name": "swarm_v2",
            "workflow_version": "1.4.0",
            "agent_version": "1.4.0",
        },
        "synthetic": mode == "fixture",
        "current_decomposition_ref": decomposition.decomposition_id,
        "decomposition_refs": [decomposition.decomposition_id],
    }

    graph = build_swarm_v2_graph(ctx)

    with traced_span(
        "mission.swarm_v2",
        {
            "request_id": rid,
            "session_id": sid,
            "mission_id": mission_id,
            "workflow_name": "swarm_v2",
            "workflow_version": "1.4.0",
            "agent_name": "coordinator_agent",
            "agent_version": "1.4.0",
            "decomposition_id": decomposition.decomposition_id,
            "decomposition_version": decomposition.version,
            "mission_lead": initial_lead,
            "synthetic": mode == "fixture",
            "execution_mode": mode,
            "decide_execute": True,
        },
        runtime.settings.langsmith_tracing,
        inputs={"query": query, "intents": sorted(intents), "complexity": plan.complexity, "execution_mode": mode},
    ) as span:
        final_state: dict[str, Any] = await graph.ainvoke(initial_state)
        artifacts = {
            t: blackboard.refs_by_type(t)
            for t in ("evidence", "anomaly", "hypothesis", "causal", "prediction", "strategy", "skeptic")
        }
        status = str(final_state.get("status") or "partial")
        # Parity with swarm_v1 success criteria for diagnostic fixtures
        retained = [h for h in blackboard.by_type("hypothesis") if h.get("status") == "retained"]
        skeptic_ok = (blackboard.by_type("skeptic") or [{}])[-1].get("verdict") == "PASS"
        challenged = any(c.get("state") == "CHALLENGED" for c in ctx.managed_claims)
        synth_summary = blackboard.synthetic_summary()
        all_synthetic = bool(synth_summary.get("all_synthetic"))
        # Matches SwarmMissionResult.synthetic's own "any synthetic artifact"
        # semantics (see `synthetic=bool(prov.get("synthetic"))` below) — a
        # mission mixing one synthetic artifact into otherwise-real evidence
        # must still never be labeled a bare "completed" success.
        any_synthetic = bool(synth_summary.get("synthetic"))
        # A health check legitimately has no single primary metric — it scans every
        # domain — so an unresolved metric only downgrades targeted investigations.
        metric_unresolved = (
            "primary_metric_unresolved" in normalized.unresolved_semantics
            and "executive_health" not in intents
        )
        if ctx.budget_exhausted or final_state.get("budget_exhausted"):
            status = "partial"
        elif challenged and status in {"prototype_completed", "completed"}:
            # A claim the Skeptic left CHALLENGED must never surface as a success,
            # even if an earlier gate stamped prototype_completed.
            status = "partial"
        elif metric_unresolved and status in {"prototype_completed", "completed"}:
            # We answered a fixture-default metric, not the user's question — the
            # investigation ran but the target could not be identified.
            status = "partial"
        elif status == "prototype_completed":
            pass  # preserve DoD status for synthetic-complete missions
        elif status == "completed" and any_synthetic and not metric_unresolved:
            # decide_completion's own "synthetic" reading can disagree with the
            # blackboard's authoritative synthetic_summary (e.g. it never saw a
            # retained claim to key off of) - never let a synthetic-touched
            # mission surface as a bare "completed" success.
            status = "prototype_completed"
            if "prototype_completed: synthetic evidence only" not in ctx.limitations:
                ctx.limitations.append("prototype_completed: synthetic evidence only")
        elif "diagnostic" in intents and retained and skeptic_ok and not challenged:
            status = "prototype_completed" if all_synthetic and not metric_unresolved else (
                "partial" if metric_unresolved else "completed"
            )
        elif challenged or status in {"running", "validating", "remediating", "assembled", "planning"}:
            status = "partial"

        span.set_outputs(
            {
                "status": status,
                "completion": ctx.completion_detail,
                "mission_lead": blackboard.mission_lead,
                "decomposition_versions": [d.get("version") for d in ctx.decompositions],
                "artifact_counts": {k: len(v) for k, v in artifacts.items()},
                "provenance": ctx.provenance,
                "conflicts": ctx.conflicts,
                "decide_execute_iterations": ctx.investigate_iterations,
                "budget_exhausted": ctx.budget_exhausted,
            }
        )

    prov = blackboard.synthetic_summary()
    if ctx.mcp_stats is not None:
        for line in ctx.mcp_stats.limitations():
            if line not in ctx.limitations:
                ctx.limitations.append(line)
    ctx.emit(
        MISSION_CONTROL_PLANE,
        decide_execute=True,
        decomposition_refs=[str(d.get("decomposition_id") or "") for d in ctx.decompositions],
        completion=ctx.completion_detail,
        request=request.model_dump(),
        iterations=ctx.investigate_iterations,
        budget_exhausted=ctx.budget_exhausted,
        budget_reason=ctx.budget_reason,
        usage=dict(final_state.get("usage") or {}),
        event_families=summarize_event_families(blackboard.events),
    )
    result = SwarmMissionResult(
        mission_id=mission_id,
        status=status,
        query=query,
        complexity=mission.complexity,
        initial_mission_lead=initial_lead,
        mission_lead=blackboard.mission_lead or initial_lead,
        leadership_epoch=blackboard.leadership_epoch,
        team=[t.__dict__ for t in team],
        handoff_history=blackboard.handoff_history,
        artifacts=artifacts,
        final_response=ctx.final_response or str(final_state.get("final_response") or ""),
        limitations=list(ctx.limitations) or list(final_state.get("limitations") or []),
        synthetic=bool(prov.get("synthetic")),
        events=list(blackboard.events),
    )
    try:
        from seleric_swarm.swarm.orchestrator import _swarm_mission_view

        runtime.store.put(
            _swarm_mission_view(result, rid, sid),
            {
                "route": "swarm",
                "workflow": "swarm_v2",
                "workflow_version": "1.4.0",
                "trace": {"request_id": rid, "session_id": sid},
                **result.as_dict(),
            },
        )
    except Exception:  # noqa: S110 - persistence must never fail a completed mission
        pass
    return result
