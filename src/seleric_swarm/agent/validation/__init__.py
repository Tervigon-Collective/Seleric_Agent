"""``EvidenceValidator`` — structural gate + the Skeptic's two-signal content checks.

Sprint 2 shipped the orchestration: the bounded revision retry loop
(non-negotiable rule 11, ``ExecutionLimits.max_validation_revisions``) plus
structural checks. Sprint 3 (this) adds the content half — ``score_trust`` and
``decide_verdict`` ported from ``agents/skeptic/scoring/`` (swarm_v2, since
deleted) — completing Profile C's half of the validator.

Joint decision recorded with A1 acceptance (2026-09-18): keep
``max_validation_revisions = 1`` — later revised: the live default is now
**3** (up to three revision passes after the initial run, i.e. 4 attempts; see
``ExecutionLimits`` in ``agent/dependencies.py``), raised to allow revisions for
scope/evidence gaps. Causal escalation is ``search_breadth`` on
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

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage, UsageLimits

from seleric_swarm.agent.agent import CONVERSATIONAL
from seleric_swarm.agent.artifacts import CausalArtifact
from seleric_swarm.agent.dependencies import ExecutionLimits, SelericDeps
from seleric_swarm.agent.limits import ExecutionBudgetTracker
from seleric_swarm.agent.output import MissionResult
from seleric_swarm.agent.progress import emit_progress, has_progress_sink, progress_handler
from seleric_swarm.agent.validation.signals import (
    AlternativeHypothesis,
    Challenge,
    CheckOutcome,
    EvidenceGap,
    run_checks,
)
from seleric_swarm.agent.validation.trust import TrustResult, score_trust
from seleric_swarm.agent.validation.answer_audit import (
    cut_off,
    ends_in_offer,
    leaked_metric_ids,
    total_mismatch,
)
from seleric_swarm.agent.validation.verdict import decide_verdict
from seleric_swarm.api.status import is_terminal_status

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


_log = logging.getLogger("seleric.agent.validation")

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
    # The answer states something its own content proves false (a headline total
    # its table does not sum to). Such a draft is never shipped, not even as the
    # partial answer of an exhausted revision loop.
    self_contradicting: bool = False

    @property
    def rejected(self) -> bool:
        """REJECT is terminal; REVISE is retryable. See the module docstring."""
        return self.verdict == "REJECT"


def _evidence_metric_ids(result: MissionResult, deps: SelericDeps) -> set[str]:
    """Metric ids this run actually touched, for the prose-leak check.

    Read from the artifacts the answer cites rather than the whole catalogue:
    the check only needs the handful of ids the model had in front of it, and
    a catalogue-wide scan would risk matching ordinary words.
    """
    ids: set[str] = set()
    for aid in [*result.evidence_ids, *result.finding_ids]:
        artifact = deps.artifact_store.get(aid)
        payload = getattr(artifact, "payload", None)
        if isinstance(payload, dict):
            metric_id = payload.get("metric_id")
            if isinstance(metric_id, str) and metric_id.strip():
                ids.add(metric_id.strip())
    return ids


_WORK_ARTIFACT_TYPES = frozenset({"evidence", "finding"})


def _mission_has_evidence(deps: SelericDeps) -> bool:
    """True once the mission holds fetched evidence or a derived finding."""
    return any(
        a.artifact_type in _WORK_ARTIFACT_TYPES
        for a in deps.artifact_store.list_for_mission(deps.mission_id)
    )


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
        if not is_terminal_status(result.status):
            # final_result is the TERMINAL output tool: the first call ends the
            # mission. Live, the model used it as a narration channel —
            # status="running" with "Let me pull the funnel data for the last 3
            # months." — and the loop stopped before a single metric tool ran,
            # shipping the preamble as the answer. A non-terminal status is the
            # model's own statement that it is not done, so treat it as REVISE
            # (retryable) and send it back to finish the work.
            counts = deps.call_counts or {}
            if counts.get(CONVERSATIONAL):
                # Live 2026-09-30: with every tool withdrawn, "do the remaining
                # tool work first" is impossible — the revision re-ran the same
                # tool-less agent until revisions exhausted. Give it a feasible
                # one instead: reply directly, terminal status.
                return ValidationOutcome(
                    ok=False,
                    reason=(
                        f"final_result was called with status={result.status!r}, but no "
                        "tools are available this turn (conversational mode) — there is "
                        "no tool work to do and 'running' is not a terminal state. "
                        "Reply directly: final_response = your conversational answer "
                        "to the user, status='completed' (or 'failed' only if the "
                        "message cannot be answered at all). Never 'running'."
                    ),
                )
            return ValidationOutcome(
                ok=False,
                reason=(
                    f"final_result was called with status={result.status!r}, which is not a "
                    "terminal state, so the answer is a preamble rather than a result. "
                    "final_result ENDS the mission: do the remaining tool work first, then "
                    "call it exactly once with completed/partial/failed and the real answer"
                ),
            )
        core = result.final_response.strip().strip(".…").lower()
        if result.final_response.strip() and (not core or core in _PLACEHOLDER_ANSWERS):
            # Live: a run shipped the literal answer "placeholder" to the user.
            return ValidationOutcome(
                ok=False,
                reason="final_response is a placeholder, not an answer; write the real answer",
            )
        if (dangling := cut_off(result.final_response)) is not None:
            return ValidationOutcome(
                ok=False,
                reason=(
                    f"final_response stops mid-sentence (ends on '{dangling}'): the answer was cut "
                    "off. Write the complete answer"
                ),
            )
        steps = result.trace.get("steps") if isinstance(result.trace, dict) else None
        if (
            steps
            and result.status in ("completed", "partial")
            and not result.evidence_ids
            and not result.finding_ids
            and not (deps.call_counts or {}).get(CONVERSATIONAL)
            and all(
                step.get("tool") == "final_result"
                for step in steps
                if step.get("kind") == "tool_call"
            )
            and not _mission_has_evidence(deps)
        ):
            # Live 2026-09-30 (MS3-96688ff682): the model called final_result
            # as its FIRST and only step — 0 tool calls, 0 evidence — with
            # status="completed" and a preamble ("…Let me resolve the metrics
            # now"). Every other gate was blind: the status is terminal, the
            # text is non-placeholder, the id-leak check has an empty id-set
            # with evidence_ids=[], and score() short-circuits to PASS when no
            # artifacts exist. A data mission that ran no tools has nothing to
            # report; a promise of future work is not an answer. REVISE so the
            # model does the work (conversational turns are exempt above —
            # zero evidence is legitimate there).
            #
            # "No work done" means no evidence in the MISSION, not no tool call
            # in this run: a revision (or a recovery retry of the same mission)
            # that answers from evidence an earlier run already fetched has
            # done the work. Live 2026-10-04 (MS3-34e7eb26aa) this gate failed
            # two correct-table revisions that needed no new fetch.
            return ValidationOutcome(
                ok=False,
                reason=(
                    "final_result was called before any other tool: 0 tool calls and "
                    "0 evidence, so the response is a plan or progress note, not an "
                    "answer. Do the tool work first — resolve the metric with "
                    "get_metric_definitions/search_semantics, fetch the values with "
                    "query_metrics — then call final_result once with the real answer "
                    "and its evidence_ids."
                ),
            )

        # -- deterministic prose audits (see validation/answer_audit) --------
        # The response contract forbids both of these in plain words; live runs
        # on v0.1.24 shipped them anyway. Checked, not asserted.
        arithmetic = total_mismatch(result.final_response)
        if arithmetic:
            return ValidationOutcome(ok=False, reason=arithmetic, self_contradicting=True)
        leaked = leaked_metric_ids(result.final_response, _evidence_metric_ids(result, deps))
        if leaked:
            return ValidationOutcome(
                ok=False,
                reason=(
                    f"final_response exposes internal metric id(s): {', '.join(leaked)}. "
                    "Describe the metric in plain business language instead; ids belong in "
                    "evidence_ids, never in the prose"
                ),
            )
        # An answer that offers to go and do the work is not an answer. Only
        # checked on ``completed``: a ``partial`` that names what it still needs
        # has already declared itself incomplete and must not be nagged.
        # Live 2026-10-06 (MS3-167d9f4838): the mission fetched one of two requested
        # windows, then closed with "Do you want me to (A) fetch today's metrics ...
        # or (B) run a diagnose?" and shipped as completed.
        if result.status == "completed" and (offer := ends_in_offer(result.final_response)):
            return ValidationOutcome(
                ok=False,
                reason=(
                    f"final_response ends by offering to run the remaining work instead of "
                    f"reporting the result: \"{offer}\". Answer the whole question you were "
                    f"asked with the tools you have — fetch whatever is still missing and "
                    f"report it. If a requirement genuinely cannot be met, use status='partial' "
                    f"and state plainly in limitations what is missing and why; if you truly "
                    f"cannot proceed without an answer only the user has, say exactly which "
                    f"value you need and set status='partial'."
                ),
            )

        causal_check = self._validate_causal_classifications(deps)
        if not causal_check.ok:
            return causal_check

        # -- content checks: the two signals (Sprint 3) ------------------
        return self.score(deps, alternatives=alternatives or [], result=result)

    def score(
        self,
        deps: SelericDeps,
        *,
        alternatives: list[AlternativeHypothesis] | None = None,
        result: MissionResult | None = None,
    ) -> ValidationOutcome:
        """Run the checks and both signals. Separated from ``validate`` so the
        two-signal behavior can be exercised without a ``MissionResult``."""
        alternatives = alternatives or []
        outcomes, gaps, claim_type = run_checks(deps, result)
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
    """Bound one mission across its agent runs.

    Tool calls use the execution cap; model requests get extra room for
    planning, retries, and the final answer.

    The limits are checked against ONE ``RunUsage`` shared by the initial run
    and every REVISE revision (``_run_agent(usage=...)``), so the mission's
    aggregate stays at the configured budget without splitting it up front.
    Before 2026-10-06 each attempt got a 1/(1+revisions) share: a lookup's 100
    tool calls became 25 for the FIRST run, and a broad "in-depth analysis"
    (32 calls) failed outright although no revision ever ran (MS3-486986e598).
    """
    tool_cap = max(1, limits.max_tool_calls)
    return UsageLimits(request_limit=tool_cap + 32, tool_calls_limit=tool_cap)


# Requests the wrap-up run may spend writing the answer (plus output retries).
_WRAP_UP_REQUESTS = 3
_WRAP_UP_PROMPT = (
    "The tool budget for this question is used up — you cannot call any more tools. "
    "Answer now with final_result, using only the evidence and artifact ids already in "
    "the tool results above. Set status to partial and list in limitations exactly which "
    "parts of the question the fetched evidence does not cover."
)


def _resumable(messages: list[ModelMessage]) -> list[ModelMessage]:
    """History a new run can continue: drop a trailing model response whose tool
    calls were refused by the usage limit (a tool call without a tool return is
    rejected by the provider)."""
    history = list(messages)
    while history and not isinstance(history[-1], ModelRequest):
        history.pop()
    return history


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


async def _run_agent(
    agent: Agent[SelericDeps, MissionResult],
    deps: SelericDeps,
    query: str,
    *,
    message_history: list[ModelMessage] | None = None,
    usage_limits: UsageLimits | None = None,
    usage: RunUsage | None = None,
) -> tuple[MissionResult, list[ModelMessage]]:
    """One agent run: the result plus every message of the conversation so far.

    ``usage`` is the mission's shared counter (see ``_usage_limits``). When the
    tool budget runs out the evidence already fetched is not thrown away: one
    tool-less wrap-up run answers from it as a partial that names what it does
    not cover. Only if that also fails does the mission fail.

    The messages (``message_history`` included) are returned so a revision can
    continue the same conversation. Before 2026-10-04 each revision was a fresh
    run that saw only "your previous answer was rejected" and the scratchpad —
    no tool results, so no evidence or artifact ids. It could not cite the
    evidence it had fetched or chart it, guessed ids, and was rejected again
    (live MS3-34e7eb26aa: 12 refused generate_visualization calls in one
    revision, then INSUFFICIENT_EVIDENCE on a correct table).

    Partial output is never streamed: an answer reaches the user only after it
    validates (see ``run_validated_mission``). The non-streamed path also keeps
    pydantic-ai's output-validation retries, which a streamed final_result
    cannot use.
    """
    handler = progress_handler(deps.mission_id) if has_progress_sink(deps.mission_id) else None
    budget = usage_limits or _usage_limits(deps.limits)
    usage = usage if usage is not None else RunUsage()
    # capture_run_messages populates `messages` even when the run raises
    # UsageLimitExceeded — the failing-budget case we most need to debug.
    with capture_run_messages() as messages:
        try:
            run = await agent.run(
                query,
                deps=deps,
                message_history=message_history,
                usage_limits=budget,
                usage=usage,
                retries=max(1, deps.limits.agent_retries),
                event_stream_handler=handler,
            )
        except UsageLimitExceeded as exc:
            _log.info("v3_tool_budget_exhausted mission=%s detail=%s", deps.mission_id, exc)
            return await _wrap_up(agent, deps, query, list(messages), usage, handler)
    history = run.all_messages()
    result = run.output
    return result.model_copy(update={"trace": {**result.trace, "steps": _summarize_steps(history)}}), history


async def _wrap_up(
    agent: Agent[SelericDeps, MissionResult],
    deps: SelericDeps,
    query: str,
    messages: list[ModelMessage],
    usage: RunUsage,
    handler: Any,
) -> tuple[MissionResult, list[ModelMessage]]:
    """Answer from the evidence already fetched once the tool budget is spent.

    No further tool call is allowed (``tool_calls_limit`` = calls already made);
    final_result is the output tool and does not count. The answer is at most a
    partial: it was cut short, so it cannot claim full coverage."""
    emit_progress(
        deps.mission_id,
        "agent.revising",
        "Tool budget reached — answering from the evidence already fetched",
        {"reason": "tool_budget"},
    )
    history = _resumable(messages)
    limits = UsageLimits(
        request_limit=usage.requests + _WRAP_UP_REQUESTS, tool_calls_limit=usage.tool_calls
    )
    with capture_run_messages() as wrap_messages:
        try:
            run = await agent.run(
                _WRAP_UP_PROMPT,
                deps=deps,
                message_history=history,
                usage_limits=limits,
                usage=usage,
                retries=max(1, deps.limits.agent_retries),
                event_stream_handler=handler,
            )
        except UsageLimitExceeded:
            run = None
    if run is None or not (run.output.final_response or "").strip():
        steps = list(wrap_messages) or messages
        return (
            MissionResult(
                mission_id=deps.mission_id,
                status="failed",
                query=query,
                as_of=deps.as_of,
                final_response=(
                    "This question needs more data lookups than one answer allows. Ask about a "
                    "narrower slice — one metric group, channel or period — and I can go deeper."
                ),
                error_code="EXECUTION_LIMIT_EXCEEDED",
                limitations=["EXECUTION_LIMIT_EXCEEDED"],
                trace={"steps": _summarize_steps(steps)},
            ),
            steps,
        )
    history = run.all_messages()
    result = run.output
    limitations = [*result.limitations]
    if "TOOL_BUDGET_EXHAUSTED" not in limitations:
        limitations.insert(0, "TOOL_BUDGET_EXHAUSTED")
    return (
        result.model_copy(
            update={
                "status": "partial" if result.status in ("completed", "running") else result.status,
                "limitations": limitations,
                "trace": {**result.trace, "steps": _summarize_steps(history)},
            }
        ),
        history,
    )


def _validation_trace(
    outcome: ValidationOutcome, deps: SelericDeps, revisions: list[dict[str, Any]] | None = None
) -> dict:
    """Diagnostics for a failed/partial validation so the response explains
    itself (D3): the verdict, the human reason, the trust score, the resolved
    RequiredScope that drove a coverage gap, and every earlier rejection.
    Without this a mission that failed on scope surfaced only
    ``INSUFFICIENT_EVIDENCE`` — undiagnosable from one request_id."""
    scope = getattr(deps, "required_scope", None)
    scope_repr = None
    if scope is not None and not scope.is_empty():
        scope_repr = {
            "breakdowns": [sorted(cs) for cs in scope.breakdowns],
            "value_filters": [
                {"term": vf.term, "dimensions": sorted(vf.dimensions), "values": list(vf.values)}
                for vf in scope.value_filters
            ],
            "temporal_grain": getattr(scope, "temporal_grain", None),
        }
    return {
        "validation": {
            "verdict": outcome.verdict,
            "reason": outcome.reason,
            "trust_score": outcome.trust_score,
            "required_scope": scope_repr,
            "revisions": list(revisions or []),
        }
    }


def _is_answer(result: MissionResult, outcome: ValidationOutcome) -> bool:
    """A rejected draft that is still a real answer the user could be shown.

    Rejected for a fixable reason (a citation, a coverage gap, wording), not
    because it is empty, a placeholder, a non-terminal preamble, or provably
    wrong about its own numbers.
    """
    text = (result.final_response or "").strip()
    core = text.strip(".…").lower()
    return bool(
        text
        and core
        and core not in _PLACEHOLDER_ANSWERS
        and is_terminal_status(result.status)
        and result.status != "failed"
        and not outcome.self_contradicting
        and not outcome.rejected
    )


_REASON_IN_PROGRESS_CHARS = 140


def _short(reason: str | None) -> str:
    text = " ".join((reason or "the answer did not pass validation").split())
    return text if len(text) <= _REASON_IN_PROGRESS_CHARS else text[: _REASON_IN_PROGRESS_CHARS - 1] + "…"


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

    Each revision continues the same conversation (``message_history``), so the
    model sees its own tool results and the draft that was rejected.

    ``on_stream(kind, text)`` — when supplied — receives the answer once, as
    ``("delta", full_text)``, after it has validated (or ships as a partial).
    A rejected draft is never sent. Before 2026-10-04 every draft streamed live
    and each rejection wiped it with ``("reset", "")``: MS3-34e7eb26aa showed the
    user seven answers appearing and vanishing. ``reset`` is no longer emitted
    here; callers may keep handling it.
    """
    result = await _validated(agent, deps, query, validator=validator, tracker=tracker)
    if (
        on_stream is not None
        and result.status in ("completed", "partial")
        and (result.final_response or "").strip()
    ):
        on_stream("delta", result.final_response)
    return result


async def _validated(
    agent: Agent[SelericDeps, MissionResult],
    deps: SelericDeps,
    query: str,
    *,
    validator: EvidenceValidator | None,
    tracker: ExecutionBudgetTracker | None,
) -> MissionResult:
    validator = validator or EvidenceValidator()
    tracker = tracker or ExecutionBudgetTracker(limits=deps.limits)

    # pydantic-ai checks UsageLimits against the RunUsage it is handed; one
    # counter for the initial run + revisions keeps the mission at one budget
    # without starving the first run.
    mission_budget = _usage_limits(deps.limits)
    mission_usage = RunUsage()

    result, history = await _run_agent(
        agent, deps, query, usage_limits=mission_budget, usage=mission_usage
    )
    if result.error_code == "EXECUTION_LIMIT_EXCEEDED":
        return result
    outcome = validator.validate(result, deps=deps)
    revisions: list[dict[str, Any]] = []
    # The latest rejected draft that is still a real answer, with its own
    # rejection — what an exhausted loop ships as a partial. Not simply the
    # last draft: live MS3-34e7eb26aa ended on a draft whose headline total
    # contradicted its own table, and that is what the user was shown.
    best: tuple[MissionResult, ValidationOutcome] | None = None
    while not outcome.ok:
        revisions.append({"revision": len(revisions), "verdict": outcome.verdict, "reason": outcome.reason})
        _log.info(
            "v3_validation_rejected mission=%s revision=%d verdict=%s reason=%s",
            deps.mission_id,
            len(revisions) - 1,
            outcome.verdict,
            outcome.reason,
        )
        if _is_answer(result, outcome):
            best = (result, outcome)
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
                    "limitations": ["INSUFFICIENT_EVIDENCE", *([outcome.reason] if outcome.reason else [])],
                    "trace": {**result.trace, **_validation_trace(outcome, deps, revisions)},
                }
            )
        verdict = tracker.consume("validation_revisions")
        if not verdict.ok:
            return _exhausted(result, outcome, best, deps, revisions)
        emit_progress(
            deps.mission_id,
            "agent.revising",
            f"Revising the answer — {_short(outcome.reason)}",
            {"revision": len(revisions), "verdict": outcome.verdict},
        )
        revision_prompt = (
            f"Your previous answer was rejected: {outcome.reason}. Revise it: fix exactly "
            "that problem, reuse the evidence and artifacts you already fetched (their ids "
            "are in the tool results above), and call final_result again."
        )
        result, history = await _run_agent(
            agent,
            deps,
            revision_prompt,
            message_history=history,
            usage_limits=mission_budget,
            usage=mission_usage,
        )
        if result.error_code == "EXECUTION_LIMIT_EXCEEDED":
            return result
        outcome = validator.validate(result, deps=deps)

    # Attach validation telemetry to trace for observability
    validation_trace = {
        "verdict": outcome.verdict,
        "trust_score": outcome.trust_score,
        "trust_label": outcome.trust_label,
        "reasons": outcome.reasons,
        "revisions_used": len(revisions),
        "revisions": revisions,
    }
    existing_trace = result.trace or {}
    return result.model_copy(update={"trace": {**existing_trace, "validation": validation_trace}})


def _exhausted(
    result: MissionResult,
    outcome: ValidationOutcome,
    best: tuple[MissionResult, ValidationOutcome] | None,
    deps: SelericDeps,
    revisions: list[dict[str, Any]],
) -> MissionResult:
    """Revisions ran out. Ship the best real answer as partial, or fail cleanly.

    A real answer backed by mission evidence is a partial: the user gets it with
    the unresolved issue named, rather than a failed mission (which breaks UI
    trust and telemetry on a scope gap the agent cannot fix without new data).
    Evidence is read from the mission, not only ``evidence_ids`` — a correct
    answer that forgot to cite is still backed. Anything else fails with a
    generic message; a draft known to be wrong is never shown.
    """
    trace = {**result.trace, **_validation_trace(outcome, deps, revisions)}
    if best is not None and _mission_has_evidence(deps):
        answer, answer_outcome = best
        cited = [aid for aid in answer.evidence_ids if deps.artifact_store.get(aid) is not None]
        return answer.model_copy(
            update={
                "status": "partial",
                "error_code": None,
                "final_response": answer.final_response.strip(),
                "evidence_ids": cited,
                # Name the unresolved part of THIS answer alongside the code,
                # so a partial states what it does not cover (never merge
                # evidence with an unresolved scope difference silently).
                "limitations": [
                    "VALIDATION_REVISIONS_EXHAUSTED",
                    *([answer_outcome.reason] if answer_outcome.reason else []),
                ],
                "trace": {**answer.trace, **trace, "steps": result.trace.get("steps")},
            }
        )
    return result.model_copy(
        update={
            "status": "failed",
            "error_code": "INSUFFICIENT_EVIDENCE",
            "final_response": "I could not back this answer with live metric evidence. Please retry.",
            "limitations": ["INSUFFICIENT_EVIDENCE", *([outcome.reason] if outcome.reason else [])],
            "trace": trace,
        }
    )
