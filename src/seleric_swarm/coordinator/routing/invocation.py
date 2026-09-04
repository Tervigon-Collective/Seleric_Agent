"""Capability resolution, team assembly, and agent invocation."""

from __future__ import annotations

from typing import Any, Protocol

from seleric_swarm.coordinator.contracts import AgentContext, AgentExecutionResult, TaskSpec
from seleric_swarm.registry.agent_registry import AgentRegistry
from seleric_swarm.swarm.envelope import Intent, SwarmMessage
from seleric_swarm.swarm.transport import AgentTransport


class AgentInvoker(Protocol):
    async def invoke(
        self,
        agent_id: str,
        task: TaskSpec,
        context: AgentContext,
    ) -> AgentExecutionResult: ...


def assemble_team(
    *,
    agents: AgentRegistry,
    required_specialists: list[str],
    optional_specialists: list[str],
    domain_lead: str,
) -> list[dict[str, Any]]:
    team: list[dict[str, Any]] = []
    seen: set[str] = set()
    for aid in [domain_lead, *required_specialists, *optional_specialists]:
        if not aid or aid in seen:
            continue
        seen.add(aid)
        rec = agents.get(aid) or {}
        team.append(
            {
                "agent_id": aid,
                "axis": "domain" if aid.endswith("_agent") and aid not in {
                    "observer_agent",
                    "anomaly_agent",
                    "diagnostic_agent",
                    "prediction_agent",
                    "strategy_agent",
                    "skeptic_agent",
                    "coordinator_agent",
                } and "observer" not in aid else _axis(aid),
                "version": rec.get("version") or agents.version(aid),
                "capabilities": list(rec.get("capabilities") or agents.capabilities_of(aid)),
                "enabled": rec.get("enabled", True),
            }
        )
    return team


def _axis(agent_id: str) -> str:
    if agent_id in {
        "observer_agent",
        "anomaly_agent",
        "diagnostic_agent",
        "prediction_agent",
        "strategy_agent",
        "skeptic_agent",
    }:
        return "specialist"
    return "domain"


class LocalAgentInvoker:
    """Invokes in-process swarm specialists via a callable registry."""

    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers

    async def invoke(
        self,
        agent_id: str,
        task: TaskSpec,
        context: AgentContext,
    ) -> AgentExecutionResult:
        handler = self.handlers.get(agent_id)
        if handler is None:
            return AgentExecutionResult(
                agent_id=agent_id,
                task_id=task.task_id,
                status="blocking_failure",
                error_code="AGENT_UNAVAILABLE",
                error_message=f"No local handler for {agent_id}",
            )
        try:
            result = await handler(task, context)
            refs = list(result.get("artifact_refs") or [])
            return AgentExecutionResult(
                agent_id=agent_id,
                task_id=task.task_id,
                status="success",
                artifact_refs=refs,
                metadata=dict(result),
            )
        except Exception as exc:
            return AgentExecutionResult(
                agent_id=agent_id,
                task_id=task.task_id,
                status="retryable_failure",
                error_code="INVOKE_ERROR",
                error_message=str(exc),
            )


class A2AAgentInvoker:
    def __init__(self, transport: AgentTransport, *, from_agent: str = "coordinator_agent") -> None:
        self.transport = transport
        self.from_agent = from_agent

    async def invoke(
        self,
        agent_id: str,
        task: TaskSpec,
        context: AgentContext,
    ) -> AgentExecutionResult:
        msg = SwarmMessage(
            mission_id=context.mission_id,
            from_agent=self.from_agent,
            to_agent=agent_id,
            intent=Intent.TASK_REQUEST if "skeptic" not in agent_id else Intent.CHALLENGE,
            payload={
                "task_id": task.task_id,
                "objective": task.objective,
                "capabilities": task.requested_capabilities,
                "input_refs": context.input_refs,
                "idempotency_key": task.idempotency_key,
                "context": context.model_dump(),
            },
        )
        try:
            reply = await self.transport.send(msg)
            refs = list((reply or {}).get("artifact_refs") or [])
            return AgentExecutionResult(
                agent_id=agent_id,
                task_id=task.task_id,
                status="success",
                artifact_refs=refs,
                metadata=dict(reply or {}),
            )
        except Exception as exc:
            return AgentExecutionResult(
                agent_id=agent_id,
                task_id=task.task_id,
                status="retryable_failure",
                error_code="A2A_ERROR",
                error_message=str(exc),
            )


def build_context(task: TaskSpec, state: dict[str, Any]) -> AgentContext:
    """Send only relevant refs — not the entire mission history."""
    return AgentContext(
        mission_id=str(state.get("mission_id") or task.mission_id),
        task_id=task.task_id,
        question=task.objective,
        mission_lead=state.get("mission_lead"),
        active_specialist=state.get("active_specialist"),
        evidence_refs=list(state.get("evidence_refs") or [])[-12:],
        anomaly_refs=list(state.get("anomaly_refs") or [])[-8:],
        input_refs=list(task.input_refs),
        payload={
            "preferred_domain": task.preferred_domain,
            "subquestion_id": task.subquestion_id,
            "task_type": task.task_type,
            "capabilities": task.requested_capabilities,
            "frontier": state.get("current_frontier"),
        },
    )
