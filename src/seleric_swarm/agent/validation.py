"""``EvidenceValidator`` — orchestration slot only (Sprint 2 Profile A).

Per ``docs/refactor/SPRINT_PLAN.md`` Sprint 2: this ships the bounded
1-revision retry loop (non-negotiable rule 11,
``ExecutionLimits.max_validation_revisions``). Content checks — the real
"does this causal claim carry a valid evidence classification" logic —
land once Profile C hands off its evidence-classification vocabulary (end
of Sprint 2, per the sprint gate); until then ``EvidenceValidator.validate``
only checks what's mechanically verifiable without that vocabulary:

- every ``evidence_id``/``finding_id`` the ``MissionResult`` references
  actually resolves in the ``ArtifactStore`` (the structural half of rule 6
  — "every numerical claim maps to an EvidenceArtifact"; the semantic half,
  matching specific numbers in ``final_response`` to specific artifacts,
  needs Profile C's classification work to do meaningfully).
- a "completed" mission has a non-empty ``final_response``.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent

from seleric_swarm.agent.dependencies import SelericDeps
from seleric_swarm.agent.limits import ExecutionBudgetTracker
from seleric_swarm.agent.output import MissionResult


@dataclass
class ValidationOutcome:
    ok: bool
    reason: str | None = None


class EvidenceValidator:
    def validate(self, result: MissionResult, *, deps: SelericDeps) -> ValidationOutcome:
        referenced = [*result.evidence_ids, *result.finding_ids]
        missing = [aid for aid in referenced if deps.artifact_store.get(aid) is None]
        if missing:
            return ValidationOutcome(ok=False, reason=f"unresolved artifact ids: {missing}")
        if result.status == "completed" and not result.final_response.strip():
            return ValidationOutcome(ok=False, reason="completed mission has an empty final_response")
        return ValidationOutcome(ok=True)


async def run_validated_mission(
    agent: Agent[SelericDeps, MissionResult],
    deps: SelericDeps,
    query: str,
    *,
    validator: EvidenceValidator | None = None,
    tracker: ExecutionBudgetTracker | None = None,
) -> MissionResult:
    """Run the agent once; on a failed validation, revise up to the bounded
    limit (``deps.limits.max_validation_revisions``, tracked the same way
    every other execution limit is — via ``ExecutionBudgetTracker``, not a
    separate ad hoc counter)."""
    validator = validator or EvidenceValidator()
    tracker = tracker or ExecutionBudgetTracker(limits=deps.limits)

    result = (await agent.run(query, deps=deps)).output
    outcome = validator.validate(result, deps=deps)
    while not outcome.ok:
        verdict = tracker.consume("validation_revisions")
        if not verdict.ok:
            return result.model_copy(
                update={"status": "failed", "error_code": "INSUFFICIENT_EVIDENCE"}
            )
        revision_prompt = (
            f"{query}\n\nYour previous answer was rejected: {outcome.reason}. "
            "Revise it."
        )
        result = (await agent.run(revision_prompt, deps=deps)).output
        outcome = validator.validate(result, deps=deps)
    return result
