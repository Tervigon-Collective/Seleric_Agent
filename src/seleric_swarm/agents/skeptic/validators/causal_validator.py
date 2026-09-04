"""Causal validator (spec sec. 26-27).

Audits a :class:`CausalAnalysisArtifact` via the injected
:class:`CausalValidationService` (temporal order, graph support, confounder
coverage, estimator sanity, refutation robustness). Never emits "proved causal".
A missing artifact is a blocking evidence gap (-> REVISE); an impossible causal
direction is a blocking challenge (-> REJECT).
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, followup, gap

_CONF_SCORE = {
    "REJECTED": 0.0,
    "ASSOCIATION_ONLY": 0.25,
    "PLAUSIBLE_CAUSAL": 0.45,
    "CAUSALLY_SUPPORTED_UNDER_ASSUMPTIONS": 0.72,
    "STRONGLY_SUPPORTED": 0.9,
}


class CausalValidator(Validator):
    name = "causal"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        if not ctx.causal:
            out.status = "INSUFFICIENT"
            out.evidence_gaps.append(
                gap(
                    "Causal claim has no CausalAnalysisArtifact.",
                    "Association is not causation; a causal claim needs a graph, estimator and refutations.",
                    capability_required="causal_diagnosis",
                    blocking=True,
                    priority=9,
                )
            )
            out.followups.append(
                followup(
                    "causal_diagnosis",
                    "Produce a causal analysis for the claimed mechanism.",
                    f"Estimate the effect of the claimed treatment on the outcome for: {ctx.claim.statement}",
                    priority=9,
                    blocking=True,
                    preferred_domain=ctx.claim.metadata.get("preferred_domain"),
                )
            )
            out.score_signals["causal_confidence"] = 0.15
            return out

        artifact = ctx.causal[0]
        service = ctx.deps.resolved_causal_service()
        result = await service.validate(artifact, context={**ctx.risk_context, **ctx.claim.metadata})

        if not result.available:
            out.status = "UNAVAILABLE"
            out.methodological_issues.append("Causal validation service unavailable; causal support unconfirmed.")
            out.challenges.append(
                challenge("causal", "warning", "Causal service unavailable - claim downgraded to association.", evidence_refs=[artifact.causal_id])
            )
            out.score_signals["causal_confidence"] = 0.2
            out.detail = {"confidence": "ASSOCIATION_ONLY", "service": "unavailable"}
            return out

        if not result.temporal_ok and ctx.policies.causal_flag("require_temporal_check"):
            out.status = "REJECTED"
            out.challenges.append(
                challenge(
                    "temporal",
                    "blocking",
                    "Causal direction is impossible: outcome change precedes the treatment.",
                    evidence_refs=[artifact.causal_id],
                    detail={"issues": result.issues},
                )
            )

        if not result.graph_ok and ctx.policies.causal_flag("require_graph"):
            out.status = _weaken(out.status)
            out.challenges.append(
                challenge("causal", "warning", f"Causal graph problem: {result.issues}", evidence_refs=[artifact.causal_id])
            )

        if not result.confounders_ok:
            out.status = _weaken(out.status)
            out.methodological_issues.append("Expected confounders were not adjusted for.")

        if ctx.policies.causal_flag("require_refutation"):
            need = ctx.policies.causal_min_refutations()
            if result.refutations_total < need:
                out.status = _weaken(out.status)
                out.challenges.append(
                    challenge(
                        "causal",
                        "warning",
                        f"Only {result.refutations_total}/{need} refutation tests were run.",
                        evidence_refs=[artifact.causal_id],
                    )
                )
                out.followups.append(
                    followup(
                        "causal_refutation",
                        "Run the remaining refutation tests.",
                        "Run placebo-treatment, random-common-cause and data-subset refuters and report results.",
                        evidence_refs=[artifact.causal_id],
                        priority=6,
                    )
                )
            elif result.refutations_passed < result.refutations_total:
                out.status = _weaken(out.status)
                out.challenges.append(
                    challenge("causal", "warning", "One or more refutation tests failed.", evidence_refs=[artifact.causal_id])
                )

        confidence = result.confidence
        if out.status == "REJECTED":
            confidence = "REJECTED"
        out.score_signals["causal_confidence"] = _CONF_SCORE.get(confidence, 0.4)
        out.score_signals["temporal_validity"] = 1.0 if result.temporal_ok else 0.0
        out.score_signals["graph_plausibility"] = 1.0 if result.graph_ok else 0.3
        out.score_signals["confounder_coverage"] = 1.0 if result.confounders_ok else 0.3
        out.score_signals["estimator_validity"] = 1.0 if result.estimator_ok else 0.2
        out.score_signals["refutation_robustness"] = (
            result.refutations_passed / result.refutations_total if result.refutations_total else 0.2
        )
        out.detail = {
            "confidence": confidence,
            "temporal_ok": result.temporal_ok,
            "graph_ok": result.graph_ok,
            "confounders_ok": result.confounders_ok,
            "refutations": [result.refutations_passed, result.refutations_total],
            "issues": result.issues,
            **(result.detail or {}),
        }
        if confidence not in {"REJECTED"}:
            out.methodological_issues.append("Unmeasured confounding cannot be completely excluded.")
        return out


def _weaken(status: str) -> str:
    if status == "REJECTED":
        return status
    return "WEAK" if status == "OK" else status
