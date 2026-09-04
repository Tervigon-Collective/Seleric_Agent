"""Authoritative TaskGraph helpers for the lookup_v1 fast path.

The ControlPlane DAG is no longer advisory-only: routing and completion read
``TaskGraph`` readiness / dispatchability / status from MissionState.
"""

from __future__ import annotations

from typing import Any

from seleric_swarm.coordinator.models import TaskGraph

# Node route targets used by orchestration/graph.py
ROUTE_UNSUPPORTED = "finalize_unsupported"
ROUTE_ERROR = "finalize_error"


def load_task_graph(state: dict[str, Any]) -> TaskGraph:
    return TaskGraph.from_dict(state.get("task_graph"))


def dump_task_graph(graph: TaskGraph) -> dict[str, Any]:
    return graph.to_dict()


def mark_tasks(
    state: dict[str, Any],
    *,
    task_types: set[str] | None = None,
    task_ids: set[str] | None = None,
    status: str = "done",
    only_pending: bool = True,
) -> dict[str, Any]:
    """Return a MissionState patch that updates matching task statuses."""
    graph = load_task_graph(state)
    if not graph.tasks:
        return {}
    changed = False
    for task in graph.tasks:
        if task_ids is not None and task.id not in task_ids:
            continue
        if task_types is not None and task.type not in task_types:
            continue
        if only_pending and task.status not in {"pending", "ready", "running"}:
            continue
        if not task.dispatchable and status == "done":
            # Never mark blocked/non-dispatchable work as done
            continue
        task.status = status
        changed = True
    if not changed:
        return {}
    payload = dump_task_graph(graph)
    return {"task_graph": payload, "tasks": payload["tasks"]}


def mark_ready_as_running(state: dict[str, Any]) -> dict[str, Any]:
    graph = load_task_graph(state)
    ready = graph.ready()
    if not ready:
        return {}
    ids = {t.id for t in ready}
    for task in graph.tasks:
        if task.id in ids:
            task.status = "running"
    payload = dump_task_graph(graph)
    return {"task_graph": payload, "tasks": payload["tasks"]}


def next_domain_lead(state: dict[str, Any], *, fallback: str | None = None) -> str | None:
    """Authoritative live lead.

    Prefer ``mission_lead`` (updated by leadership transfer) over the planned
    ``lead_selection`` so post-handoff routing does not snap back to the
    initial domain and create a transfer loop.
    """
    lead_sel = state.get("lead_selection") or {}
    return state.get("mission_lead") or lead_sel.get("mission_lead") or fallback


def route_from_plan(
    state: dict[str, Any],
    *,
    supported_classes: set[str],
    domain_node_names: dict[str, str],
    error_codes: set[str] | None = None,
) -> str:
    """Decide the post-coordinator route from the authoritative DAG.

    Rules:
    1. Hard errors → finalize_error
    2. Unsupported query class → finalize_unsupported
    3. Plan not fully dispatchable → finalize_unsupported (blocked reasons already on state)
    4. Else route to the planned domain lead node
    """
    error_codes = error_codes or {"LLM_UNAVAILABLE", "BUDGET_EXCEEDED", "TIMEOUT"}
    if state.get("error_code") in error_codes:
        return ROUTE_ERROR

    query_class = state.get("query_class")
    if query_class not in supported_classes:
        return ROUTE_UNSUPPORTED

    # Authoritative: a non-dispatchable plan must not execute observer/domain work.
    if state.get("plan_dispatchable") is False:
        return ROUTE_UNSUPPORTED

    graph = load_task_graph(state)
    if graph.tasks and not any(t.dispatchable for t in graph.tasks):
        return ROUTE_UNSUPPORTED

    lead = next_domain_lead(state)
    if lead and lead in domain_node_names:
        return domain_node_names[lead]
    return ROUTE_UNSUPPORTED


def dag_progress_summary(state: dict[str, Any]) -> dict[str, Any]:
    graph = load_task_graph(state)
    return {
        "total": len(graph.tasks),
        "done": sum(1 for t in graph.tasks if t.status == "done"),
        "pending": sum(1 for t in graph.tasks if t.status in {"pending", "ready"}),
        "blocked": sum(1 for t in graph.tasks if not t.dispatchable),
        "dispatchable": graph.dispatchable,
        "ready_ids": [t.id for t in graph.ready()],
    }


def mark_ready_tasks_done(
    state: dict[str, Any],
    *,
    task_types: set[str] | None = None,
) -> dict[str, Any]:
    """Mark currently ready (deps satisfied) tasks of the given types as done."""
    graph = load_task_graph(state)
    ready_ids = {t.id for t in graph.ready()}
    # Also include tasks already running
    running_ids = {t.id for t in graph.tasks if t.status == "running"}
    target_ids = ready_ids | running_ids
    if task_types is not None:
        target_ids = {
            t.id
            for t in graph.tasks
            if t.id in target_ids and t.type in task_types and t.dispatchable
        }
    if not target_ids:
        # Fallback: first pending dispatchable observe with deps met
        done = {t.id for t in graph.tasks if t.status == "done"}
        for t in graph.tasks:
            if t.type in (task_types or set()) and t.status in {"pending", "ready", "running"}:
                if t.dispatchable and all(d in done for d in t.depends_on):
                    target_ids.add(t.id)
                    break
    return mark_tasks(state, task_ids=target_ids, status="done", only_pending=True)
