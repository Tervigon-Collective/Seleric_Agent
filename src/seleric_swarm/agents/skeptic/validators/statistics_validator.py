"""Statistical validator (spec sec. 24).

Delegates every quantitative test to the injected
:class:`StatisticalValidatorService`; the Skeptic (and any LLM) never compute a
p-value or interval here. It only decides *which* checks a claim needs and how
their pass/fail maps to challenges and score signals.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, gap


class StatisticsValidator(Validator):
    name = "statistical"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        stats = ctx.deps.stats
        checks: list[tuple[str, dict]] = []

        primary = _primary_evidence(ctx)
        sample = primary.sample_size if primary else ctx.claim.metadata.get("sample_size")
        change = primary.change_pct if primary else ctx.claim.metadata.get("change_pct")

        checks.append(("sample_size", {"sample_size": sample, "min_sample": ctx.policies.min_sample_size()}))
        if change is not None:
            checks.append(("effect_size", {"change_pct": change}))
        if ctx.claim.claim_type in {"causal", "correlation"} and ctx.causal:
            checks.append(("confidence_interval_excludes_zero", {"interval": ctx.causal[0].confidence_interval}))
        segs = ctx.claim.metadata.get("segment_effects")
        if segs:
            checks.append(("segment_robustness", {"segments": segs}))

        results: dict[str, bool] = {}
        for name, data in checks:
            res = await stats.check(name=name, data=data)
            results[name] = res.passed
            if not res.passed:
                if name == "sample_size":
                    out.status = "WEAK"
                    out.evidence_gaps.append(
                        gap(
                            "Sample size is below the minimum for a reliable estimate.",
                            "Small samples make the observed change indistinguishable from noise.",
                            capability_required="metric_observation",
                            blocking=False,
                            priority=6,
                        )
                    )
                    out.challenges.append(challenge("statistical", "warning", f"sample_size check failed: {res.detail}"))
                else:
                    out.status = "WEAK"
                    out.challenges.append(challenge("statistical", "warning", f"{name} check failed: {res.detail}"))

        passed = sum(1 for v in results.values() if v)
        total = max(1, len(results))
        out.score_signals["statistical_strength"] = passed / total
        out.score_signals.setdefault("evidence_quality", 0.4 + 0.4 * (passed / total))
        out.detail = {"checks": results}
        return out


def _primary_evidence(ctx: SkepticContext):
    for ev in ctx.evidence:
        if ev.metric_id and ev.change_pct is not None:
            return ev
    return ctx.evidence[0] if ctx.evidence else None
