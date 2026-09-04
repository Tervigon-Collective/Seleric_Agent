"""Task scheduler — ready-set selection from TaskSpec DAGs."""

from __future__ import annotations

from seleric_swarm.coordinator.contracts import TaskSpec


def get_ready_tasks(tasks: list[TaskSpec]) -> list[TaskSpec]:
    """Pending/ready tasks whose dependencies are all done."""
    done = {t.task_id for t in tasks if t.status == "done"}
    ready: list[TaskSpec] = []
    for task in tasks:
        if task.status not in {"pending", "ready"}:
            continue
        if all(dep in done for dep in task.dependencies):
            ready.append(task)
    return sorted(ready, key=lambda t: (-t.priority, t.task_id))


def mark_running(tasks: list[TaskSpec], selected_ids: set[str]) -> list[TaskSpec]:
    out: list[TaskSpec] = []
    for t in tasks:
        if t.task_id in selected_ids:
            out.append(t.model_copy(update={"status": "running"}))
        else:
            out.append(t)
    return out


def mark_done(tasks: list[TaskSpec], task_id: str, *, failed: bool = False) -> list[TaskSpec]:
    out: list[TaskSpec] = []
    for t in tasks:
        if t.task_id == task_id:
            out.append(t.model_copy(update={"status": "failed" if failed else "done"}))
        else:
            out.append(t)
    return out
