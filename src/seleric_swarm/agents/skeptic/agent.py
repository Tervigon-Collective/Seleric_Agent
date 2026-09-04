"""SkepticAgent - the stable integration boundary for the Coordinator.

    verdict = await SkepticAgent(...).validate_claim(request)

The agent resolves the claim into a :class:`SkepticContext`, runs the LangGraph
workflow, optionally asks the reasoning model for a plain-language explanation
(never for a verdict or a number), emits an observability record and returns a
:class:`SkepticVerdict`. Deterministic end to end when no reasoning model is
supplied.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from seleric_swarm.agents.skeptic.context import SkepticDeps
from seleric_swarm.agents.skeptic.contracts import (
    Claim,
    SkepticValidationRequest,
    SkepticVerdict,
)
from seleric_swarm.agents.skeptic.graph import build_skeptic_graph
from seleric_swarm.agents.skeptic.intake.claim_parser import parse_claim
from seleric_swarm.agents.skeptic.policies import SkepticPolicies
from seleric_swarm.agents.skeptic.prompts import EXPLANATION_SYSTEM, explanation_user
from seleric_swarm.agents.skeptic.registries import (
    ArtifactRepository,
    EvidenceRepository,
    InMemoryArtifactRepository,
    InMemoryEvidenceRepository,
)
from seleric_swarm.agents.skeptic.resolver import resolve_context

_log = structlog.get_logger("seleric_swarm.agents.skeptic")

_COMPILED_GRAPH = None


def _graph():
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_skeptic_graph()
    return _COMPILED_GRAPH


class SkepticAgent:
    agent_id = "skeptic_agent"
    agent_version = "1.0.0"

    def __init__(
        self,
        *,
        evidence_repo: EvidenceRepository | None = None,
        artifact_repo: ArtifactRepository | None = None,
        deps: SkepticDeps | None = None,
        policies: SkepticPolicies | None = None,
    ) -> None:
        self.evidence_repo = evidence_repo or InMemoryEvidenceRepository()
        self.artifact_repo = artifact_repo or InMemoryArtifactRepository()
        self.deps = deps or SkepticDeps()
        self.policies = policies or SkepticPolicies.load()

    # -- primary entrypoint ------------------------------------------------
    async def validate_claim(self, request: SkepticValidationRequest) -> SkepticVerdict:
        started = time.perf_counter()
        request = request.model_copy(deep=True)
        request.claim = parse_claim(request.claim, mission_id=request.mission_id)

        ctx = await resolve_context(
            request,
            evidence_repo=self.evidence_repo,
            artifact_repo=self.artifact_repo,
            deps=self.deps,
            policies=self.policies,
        )

        if request.blind_review is None:
            request.blind_review = ctx.risk_context.get("risk_score", 0.0) >= self.policies.blind_review_threshold()
        if request.blind_review:
            ctx.risk_context["blind_review"] = True

        final_state = await _graph().ainvoke(
            {
                "mission_id": request.mission_id,
                "skeptic_run_id": f"SK-{int(started * 1000) % 10_000_000}",
                "request": request.model_dump(),
                "_context": ctx,
                "_outcomes": [],
            }
        )
        verdict: SkepticVerdict = final_state["_verdict_model"]

        # explanation (LLM optional; failure is non-fatal)
        try:
            text = await self.deps.reasoning.generate_text(
                system=EXPLANATION_SYSTEM,
                user=explanation_user(
                    ctx.claim,
                    verdict.verdict,
                    [c.description for c in verdict.challenges],
                    [g.description for g in verdict.evidence_gaps],
                ),
                tags=["skeptic", "explanation"],
            )
            if text:
                verdict.explanation = text.strip()
        except Exception as exc:
            verdict.limitations.append("Explanation model unavailable; see challenges/validator_results.")
            _log.warning("skeptic.explanation_failed", error=str(exc))

        if not verdict.explanation:
            verdict.explanation = _fallback_explanation(verdict)

        self._emit(verdict, ctx, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
        return verdict

    # -- observability (spec sec. 51-52) --------------------------------
    def _emit(self, verdict: SkepticVerdict, ctx: Any, *, elapsed_ms: float) -> None:
        _log.info(
            "skeptic.run",
            mission_id=verdict.mission_id,
            skeptic_run_id=verdict.skeptic_run_id,
            claim_id=verdict.claim_id,
            claim_type=verdict.claim_type,
            risk_score=verdict.risk_score,
            risk_class=verdict.risk_class,
            validators=list(verdict.validator_results),
            contradictions=[
                c.description for c in verdict.challenges if c.category in {"contradiction", "source", "metric"}
            ],
            alternative_hypotheses=len(verdict.alternative_hypotheses),
            evidence_gaps=len(verdict.evidence_gaps),
            followups=len(verdict.required_followups),
            trust_score=verdict.trust_score,
            trust_label=verdict.trust_label,
            verdict=verdict.verdict,
            blind_review=ctx.risk_context.get("blind_review", False),
            elapsed_ms=elapsed_ms,
        )


def _fallback_explanation(v: SkepticVerdict) -> str:
    blockers = [c.description for c in v.challenges if c.severity == "blocking"]
    head = f"Verdict {v.verdict} (trust {v.trust_label}, score {v.trust_score:.2f})."
    if v.verdict == "PASS":
        return head + " Required evidence is present, no blocking contradiction, methodology acceptable."
    if blockers:
        return head + " Blocking issues: " + "; ".join(blockers[:3])
    return head + " Incomplete support: " + "; ".join(
        [g.description for g in v.evidence_gaps[:2]] + [c.description for c in v.challenges[:2]]
    )


# -- convenience constructors -------------------------------------------------


def skeptic_from_blackboard(blackboard: Any, *, deps: SkepticDeps | None = None,
                            policies: SkepticPolicies | None = None) -> SkepticAgent:
    """Build a SkepticAgent whose repositories read a live swarm Blackboard."""

    from seleric_swarm.agents.skeptic.registries import repositories_from_blackboard

    evidence_repo, artifact_repo = repositories_from_blackboard(blackboard)
    return SkepticAgent(
        evidence_repo=evidence_repo,
        artifact_repo=artifact_repo,
        deps=deps,
        policies=policies,
    )


def make_claim(**kw: Any) -> Claim:
    """Thin helper so callers don't import contracts directly for simple cases."""

    return Claim(**kw)
