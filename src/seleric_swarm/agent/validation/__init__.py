"""``EvidenceValidator`` — structural gate + the Skeptic's two-signal content checks.

Sprint 2 shipped the orchestration: the bounded 1-revision retry loop
(non-negotiable rule 11, ``ExecutionLimits.max_validation_revisions``) plus
structural checks. Sprint 3 (this) adds the content half — ``score_trust`` and
``decide_verdict`` ported from ``agents/skeptic/scoring/`` — completing Profile
C's half of the validator.

Joint decision recorded with A1 acceptance (2026-09-18): keep
``max_validation_revisions = 1``. Causal escalation is ``search_breadth`` on
``estimate_effect`` (A1.1), not this counter — widening a causal search does not
consume a validation revision. Skeptic → validator is a **change in kind**
(cross-agent adversarial challenge becomes in-context self-review), not a
consolidation; see ``CONTRACTS.md`` A1 joint decisions and
``03_PROFILE_CAPABILITIES.md`` §4.

Two signals, never merged
-------------------------
``ValidationOutcome`` carries ``trust_score``/``trust_label`` and ``verdict`` as
separate fields, because ``docs/BUG_SHEET.md`` #12 records STRONG trust
alongside a REVISE verdict as intentional: "the evidence we have is solid, but
there's an unresolved competing explanation we haven't ruled out yet". Sprint
2's ``ValidationOutcome`` was binary (``ok``/``reason``) and had nowhere to put
that, so widening it was step one of this sprint.

REJECT vs REVISE (behavior decision, Sprint 3)
----------------------------------------------
``REVISE`` consumes a revision and re-prompts. ``REJECT`` fails closed
**immediately, without consuming a revision** — re-prompting a claim whose
evidence contradicts it just burns budget for the same answer. This matches
swarm_v2, where REJECT ended the mission and only REVISE triggered a
remediation round.

When revisions are exhausted mid-REVISE, the mission fails closed with
``INSUFFICIENT_EVIDENCE`` — including the STRONG-trust + REVISE case, per the A1
joint decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from seleric_swarm.agent.artifacts import CausalArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.limits import ExecutionBudgetTracker
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.progress import has_progress_sink, progress_handler
from seleric_swarm.agent.validation.signals import (
    AlternativeHypothesis,
    Challenge,
    CheckOutcome,
    EvidenceGap,
    run_checks,
)
from seleric_swarm.agent.validation.trust import TrustResult, score_trust
from seleric_swarm.agent.validation.verdict import decide_verdict

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from seleric_swarm.agent.validation.trust import TrustLabel
    from seleric_swarm.agent.validation.verdict import Verdict

__all__ = [
    "AlternativeHypothesis",
    "Challenge",
    "CheckOutcome",
    "EvidenceGap",
    "EvidenceValidator",
    "TrustResult",
    "ValidationOutcome",
    "decide_verdict",
    "run_validated_mission",
    "score_trust",
]


_PLACEHOLDER_ANSWERS = frozenset({"placeholder", "todo", "tbd", "n/a", "na", "none", "null", "answer"})


@dataclass
class ValidationOutcome:
    """Structural result plus, when content checks ran, both skeptic signals.

    ``ok`` stays the orchestration's single branch point, but it is now
    *derived* from the verdict rather than being the only thing computed.
    ``trust_label`` and ``verdict`` are independent — reading one tells you
    nothing about the other, which is the #12 property.
    """

    ok: bool
    reason: str | None = None
    verdict: Verdict | None = None
    trust_score: float | None = None
    trust_label: TrustLabel | None = None
    trust_components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        """REJECT is terminal; REVISE is retryable. See the module docstring."""
        return self.verdict == "REJECT"


class EvidenceValidator:
    def validate(
        self,
        result: MissionResult,
        *,
        deps: SelericDeps,
        alternatives: list[AlternativeHypothesis] | None = None,
    ) -> ValidationOutcome:
        # -- structural gates (Sprint 2, unchanged) ----------------------
        referenced = [*result.evidence_ids, *result.finding_ids]
        missing = [aid for aid in referenced if deps.artifact_store.get(aid) is None]
        if missing:
            return ValidationOutcome(ok=False, reason=f"unresolved artifact ids: {missing}")
        if not result.final_response.strip():
            # Any status: live, the model returned status="running" with an empty
            # answer via final_result, which slipped past a completed-only gate.
            return ValidationOutcome(
                ok=False,
                reason=f"mission returned an empty final_response (status={result.status}); write the answer",
            )
        core = result.final_response.strip().strip(".…").lower()
        if result.final_response.strip() and (not core or core in _PLACEHOLDER_ANSWERS):
            # Live: a run shipped the literal answer "placeholder" to the user.
            return ValidationOutcome(
                ok=False,
                reason="final_response is a placeholder, not an answer; write the real answer",
            )

        causal_check = self._validate_causal_classifications(deps)
        if not causal_check.ok:
            return causal_check

        # -- content checks: the two signals (Sprint 3) ------------------
        return self.score(deps, alternatives=alternatives or [])

    def score(
        self, deps: SelericDeps, *, alternatives: list[AlternativeHypothesis] | None = None
    ) -> ValidationOutcome:
        """Run the checks and both signals. Separated from ``validate`` so the
        two-signal behavior can be exercised without a ``MissionResult``."""
        alternatives = alternatives or []
        outcomes, gaps, claim_type = run_checks(deps)
        if not outcomes:
            # Nothing to check: the mission produced no durable artifacts, so
            # there is no claim for the two signals to reason about. Scoring it
            # would invent a trust number out of no evidence.
            return ValidationOutcome(ok=True, verdict="PASS")
        trust = score_trust(outcomes, alternatives, claim_type=claim_type)
        decision = decide_verdict(outcomes, gaps, alternatives, trust.score)
        return ValidationOutcome(
            ok=decision.verdict == "PASS",
            reason=None if decision.verdict == "PASS" else "; ".join(decision.reasons),
            verdict=decision.verdict,
            trust_score=trust.score,
            trust_label=trust.label,
            trust_components=trust.components,
            reasons=decision.reasons,
        )

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


def _usage_limits(limits: ExecutionLimits) -> UsageLimits:
    """Bound one mission. Tool calls use the execution cap; model requests get
    extra room for planning, retries, and the final answer."""
    tool_cap = max(1, limits.max_tool_calls)
    return UsageLimits(request_limit=tool_cap + 32, tool_calls_limit=tool_cap)


def _trunc(value: object, limit: int = 2000) -> object:
    """Keep step payloads debuggable but bounded; leave small dicts as-is."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    text = str(value)
    return value if len(text) <= limit else text[:limit] + "…[truncated]"


def _summarize_steps(messages: list) -> list[dict]:
    """Compact per-step trace from PydanticAI message history: every tool call
    (with args), tool return, retry, and model text — the minute-level record
    for debugging (e.g. which calls blew the tool-call budget)."""
    steps: list[dict] = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            kind = getattr(part, "part_kind", "")
            if kind == "tool-call":
                steps.append({"kind": "tool_call", "tool": part.tool_name, "args": _trunc(part.args)})
            elif kind == "tool-return":
                steps.append({"kind": "tool_return", "tool": part.tool_name, "result": _trunc(part.content)})
            elif kind == "retry-prompt":
                steps.append({"kind": "retry", "tool": getattr(part, "tool_name", None), "error": _trunc(part.content)})
            elif kind == "thinking":
                steps.append({"kind": "thinking", "text": _trunc(part.content)})
            elif kind == "text" and str(part.content).strip():
                steps.append({"kind": "model_text", "text": _trunc(part.content)})
    for i, step in enumerate(steps, 1):
        step["seq"] = i
    return steps


async def _run_agent_streamed(
    agent: Agent[SelericDeps, MissionResult],
    deps: SelericDeps,
    query: str,
    on_delta: Callable[[str], None],
) -> MissionResult:
    """Stream the final answer's tokens as they are generated.

    ``run_stream`` runs the full agent graph — all the tool calls — internally
    and only yields once the model starts emitting the final ``MissionResult``.
    We diff the ``final_response`` field across the partially-validated outputs
    and push each new suffix to ``on_delta``; everything else (evidence, status,
    trace) is read from the fully-validated output at the end, unchanged.
    """
    async with agent.run_stream(
        query,
        deps=deps,
        usage_limits=_usage_limits(deps.limits),
        retries=max(1, deps.limits.agent_retries),
    ) as stream:
        emitted = 0
        async for partial in stream.stream_output(debounce_by=0.05):
            text = getattr(partial, "final_response", None) or ""
            if len(text) > emitted:
                on_delta(text[emitted:])
                emitted = len(text)
        result = await stream.get_output()
        steps = _summarize_steps(stream.all_messages())
    return result.model_copy(update={"trace": {**result.trace, "steps": steps}})


async def _run_agent(
    agent: Agent[SelericDeps, MissionResult],
    deps: SelericDeps,
    query: str,
    *,
    on_delta: Callable[[str], None] | None = None,
) -> MissionResult:
    # capture_run_messages populates `messages` even when the run raises
    # UsageLimitExceeded — the failing-budget case we most need to debug.
    # Only stream when something is listening: streaming changes the request path
    # (request_stream), so runs with no UI keep the plain request path.
    handler = progress_handler(deps.mission_id) if has_progress_sink(deps.mission_id) else None
    with capture_run_messages() as messages:
        try:
            if on_delta is not None:
                return await _run_agent_streamed(agent, deps, query, on_delta)
            result = (
                await agent.run(
                    query,
                    deps=deps,
                    usage_limits=_usage_limits(deps.limits),
                    retries=max(1, deps.limits.agent_retries),
                    event_stream_handler=handler,
                )
            ).output
            return result.model_copy(update={"trace": {**result.trace, "steps": _summarize_steps(messages)}})
        except UsageLimitExceeded:
            return MissionResult(
                mission_id=deps.mission_id,
                status="failed",
                query=query,
                as_of=deps.as_of,
                final_response="This question took too many steps. Please retry with a more specific metric name.",
                error_code="EXECUTION_LIMIT_EXCEEDED",
                limitations=["EXECUTION_LIMIT_EXCEEDED"],
                trace={"steps": _summarize_steps(messages)},
            )


async def run_validated_mission(
    agent: Agent[SelericDeps, MissionResult],
    deps: SelericDeps,
    query: str,
    *,
    validator: EvidenceValidator | None = None,
    tracker: ExecutionBudgetTracker | None = None,
    on_stream: Callable[[str, str], None] | None = None,
) -> MissionResult:
    """Run the agent once; on a REVISE, revise up to the bounded limit.

    ``deps.limits.max_validation_revisions`` is tracked through
    ``ExecutionBudgetTracker`` like every other execution limit, not a separate
    ad hoc counter. A REJECT verdict short-circuits without spending one.

    ``on_stream(kind, text)`` — when supplied — streams the final answer live:
    ``("delta", suffix)`` for each new chunk, ``("reset", "")`` when a REVISE
    scraps the streamed-but-rejected answer so the caller can clear it before
    the revised answer streams in.
    """
    validator = validator or EvidenceValidator()
    tracker = tracker or ExecutionBudgetTracker(limits=deps.limits)
    on_delta = (lambda text: on_stream("delta", text)) if on_stream else None

    result = await _run_agent(agent, deps, query, on_delta=on_delta)
    if result.error_code == "EXECUTION_LIMIT_EXCEEDED":
        return result
    outcome = validator.validate(result, deps=deps)
    while not outcome.ok:
        if outcome.rejected:
            # Terminal: the evidence contradicts the claim. Re-prompting spends
            # a revision to get the same rejection.
            resp = (
                result.final_response.strip()
                if (result.final_response and result.final_response.strip())
                else "I could not back this answer with live metric evidence. Please retry."
            )
            return result.model_copy(
                update={
                    "status": "failed",
                    "error_code": "INSUFFICIENT_EVIDENCE",
                    "final_response": resp,
                    "limitations": ["INSUFFICIENT_EVIDENCE"],
                }
            )
        verdict = tracker.consume("validation_revisions")
        if not verdict.ok:
            resp = (
                result.final_response.strip()
                if (result.final_response and result.final_response.strip())
                else "I could not back this answer with live metric evidence. Please retry."
            )
            return result.model_copy(
                update={
                    "status": "failed",
                    "error_code": "INSUFFICIENT_EVIDENCE",
                    "final_response": resp,
                    "limitations": ["INSUFFICIENT_EVIDENCE"],
                }
            )
        if on_stream is not None:
            # The streamed-but-rejected answer is now stale; tell the caller to
            # clear it before the revised answer streams in over the top.
            on_stream("reset", "")
        revision_prompt = (
            f"{query}\n\nYour previous answer was rejected: {outcome.reason}. Revise it."
        )
        result = await _run_agent(agent, deps, revision_prompt, on_delta=on_delta)
        if result.error_code == "EXECUTION_LIMIT_EXCEEDED":
            return result
        outcome = validator.validate(result, deps=deps)
    return result
