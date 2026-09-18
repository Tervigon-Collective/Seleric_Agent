"""``EvidenceValidator`` — orchestration + minimal causal vocab check (Sprint 2).

Per ``docs/refactor/SPRINT_PLAN.md`` Sprint 2: this ships the bounded
1-revision retry loop (non-negotiable rule 11,
``ExecutionLimits.max_validation_revisions``). Joint decision with A1
acceptance (2026-09-18): keep ``max_validation_revisions = 1``; Causal
escalation is ``search_breadth`` on ``estimate_effect`` (A1.1), not this
counter. On STRONG-trust + REVISE when revisions are exhausted, fail closed
with ``INSUFFICIENT_EVIDENCE``. Skeptic → validator is a change in kind
(in-context self-review), not a consolidation — see ``CONTRACTS.md`` A1
joint decisions.

Sprint 2 C handoff (A1.4): every mission-scoped ``CausalArtifact`` must
carry a present, frozen-vocabulary ``evidence_classification``. Full
skeptic two-signal (``score_trust`` / ``decide_verdict``) and bug #12
remain Sprint 3.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import Agent

from seleric_swarm.agent.artifacts import CausalArtifact
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

        causal_check = self._validate_causal_classifications(deps)
        if not causal_check.ok:
            return causal_check
        return ValidationOutcome(ok=True)

    def _validate_causal_classifications(self, deps: SelericDeps) -> ValidationOutcome:
        """Minimal Profile C vocabulary gate — presence + membership only."""
        for artifact in deps.artifact_store.list_for_mission(deps.mission_id):
            if artifact.artifact_type != "causal":
                continue
            try:
                # CausalArtifact's Literal + model_validator enforce vocabulary
                # and CAUSALLY_SUPPORTED→refutation_checks; failure is enough.
                CausalArtifact.model_validate(artifact.payload)
            except Exception as exc:
                return ValidationOutcome(
                    ok=False,
                    reason=f"causal artifact {artifact.id} invalid: {exc}",
                )
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
