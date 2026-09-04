"""Validator ABC + small builders shared by every validator."""

from __future__ import annotations

import abc

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.contracts import (
    Challenge,
    ChallengeCategory,
    EvidenceGap,
    FollowUpTask,
    Severity,
)


class Validator(abc.ABC):
    """One focused, independently unit-testable check. No LLM, no I/O beyond
    injected repositories/services."""

    name: str = "validator"

    @abc.abstractmethod
    async def run(self, ctx: SkepticContext) -> ValidatorOutcome: ...

    # -- builders --------------------------------------------------------
    def outcome(self, status: str, **kw) -> ValidatorOutcome:
        return ValidatorOutcome(validator=self.name, status=status, **kw)


def challenge(
    category: ChallengeCategory,
    severity: Severity,
    description: str,
    *,
    evidence_refs: list[str] | None = None,
    remediation_hint: str | None = None,
    detail: dict | None = None,
) -> Challenge:
    return Challenge(
        category=category,
        severity=severity,
        description=description,
        evidence_refs=evidence_refs or [],
        remediation_hint=remediation_hint,
        detail=detail or {},
    )


def gap(
    description: str,
    reason_required: str,
    *,
    capability_required: str | None = None,
    blocking: bool = False,
    priority: int = 5,
) -> EvidenceGap:
    return EvidenceGap(
        description=description,
        reason_required=reason_required,
        capability_required=capability_required,
        blocking=blocking,
        priority=priority,
    )


def followup(
    requested_capability: str,
    objective: str,
    question: str,
    *,
    evidence_refs: list[str] | None = None,
    priority: int = 5,
    blocking: bool = False,
    preferred_domain: str | None = None,
) -> FollowUpTask:
    return FollowUpTask(
        requested_capability=requested_capability,
        objective=objective,
        question=question,
        evidence_refs=evidence_refs or [],
        priority=priority,
        blocking=blocking,
        preferred_domain=preferred_domain,
    )
