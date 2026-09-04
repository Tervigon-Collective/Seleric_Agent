"""Remediation task builder (spec sec. 35).

Turns validator follow-ups, blocking gaps and unresolved alternatives into
machine-actionable :class:`FollowUpTask` objects the Coordinator can dispatch.
Task ids are stable (mission + claim + capability + question hash) so re-running
the Skeptic does not create duplicates.
"""

from __future__ import annotations

import hashlib

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.contracts import (
    AlternativeHypothesis,
    EvidenceGap,
    FollowUpTask,
)
from seleric_swarm.agents.skeptic.hypothesis.falsification import falsification_implications


def _stable_id(mission_id: str, claim_id: str, capability: str, question: str) -> str:
    digest = hashlib.sha1(f"{mission_id}|{claim_id}|{capability}|{question}".encode()).hexdigest()[:12]
    return f"FUP-{digest}"


def build_followups(
    ctx: SkepticContext,
    verdict: str,
    outcomes: list[ValidatorOutcome],
    gaps: list[EvidenceGap],
    alternatives: list[AlternativeHypothesis],
) -> list[FollowUpTask]:
    if verdict == "PASS":
        return []

    tasks: dict[str, FollowUpTask] = {}

    def add(task: FollowUpTask) -> None:
        task.task_id = _stable_id(ctx.claim.mission_id, ctx.claim.claim_id, task.requested_capability, task.question)
        tasks.setdefault(task.task_id, task)

    # 1. validator-authored follow-ups
    for oc in outcomes:
        for f in oc.followups:
            add(f)

    # 2. blocking / high-priority gaps -> tasks
    for g in gaps:
        if not g.blocking and g.priority < 6:
            continue
        add(
            FollowUpTask(
                requested_capability=g.capability_required or "metric_observation",
                objective=f"Close evidence gap: {g.description}",
                question=g.reason_required,
                priority=g.priority,
                blocking=g.blocking,
                preferred_domain=ctx.claim.metadata.get("preferred_domain"),
            )
        )

    # 3. unresolved alternatives -> falsification tasks
    for alt in alternatives:
        if alt.status != "open":
            continue
        add(
            FollowUpTask(
                requested_capability="hypothesis_test",
                objective=f"Falsify or confirm alternative: {alt.hypothesis}",
                question=alt.falsification_test or f"Design a test isolating: {alt.hypothesis}",
                priority=alt.priority,
                blocking=False,
                preferred_domain=ctx.claim.metadata.get("domain"),
            )
        )

    # 4. explicit falsification implication tests (only if nothing else covers them)
    if not tasks:
        fset = falsification_implications(ctx)
        for test in fset.tests[:3]:
            add(
                FollowUpTask(
                    requested_capability="hypothesis_test",
                    objective="Run falsification test implied by the claim.",
                    question=test,
                    priority=5,
                )
            )

    ordered = sorted(tasks.values(), key=lambda t: (-int(t.blocking), -t.priority))
    return ordered
