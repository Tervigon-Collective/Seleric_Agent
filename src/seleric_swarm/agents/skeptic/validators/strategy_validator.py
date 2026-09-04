"""Strategy validator (spec sec. 31-32).

Checks a recommendation/action against (a) the diagnosed mechanism -- does the
action attack the retained cause or merely a symptom? -- and (b) the injected
:class:`BusinessRuleService` for finance/inventory/procurement/technical
constraints. A mechanism mismatch or a blocking rule violation -> REJECT with a
domain-routed follow-up (the Skeptic never impersonates the domain agent).
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext, ValidatorOutcome
from seleric_swarm.agents.skeptic.validators.base import Validator, challenge, followup

_SYMPTOM_ACTIONS = ("reduce spend", "reduce budget", "cut spend", "reduce paid", "increase discount", "shift campaigns", "lower bids")
_MECHANISM_FIT = {"very_high": 1.0, "high": 0.8, "medium": 0.5, "low": 0.2}


class StrategyValidator(Validator):
    name = "strategy"

    async def run(self, ctx: SkepticContext) -> ValidatorOutcome:
        out = self.outcome("OK")
        if not ctx.strategies:
            out.status = "NOT_APPLICABLE"
            return out

        strat = ctx.strategies[0]
        action = (strat.action or "").lower()

        # -- mechanism fit --------------------------------------------------
        diagnosed = _diagnosed_mechanism(ctx)
        fit_label = _declared_fit(strat)
        addresses = _addresses(action, diagnosed)
        mechanism_mismatch = (
            ctx.policies.strategy_require_mechanism_fit()
            and diagnosed
            and not addresses
            and (fit_label in {"low", None} or any(s in action for s in _SYMPTOM_ACTIONS))
        )
        if mechanism_mismatch:
            out.status = "REJECTED"
            out.challenges.append(
                challenge(
                    "strategy",
                    "blocking",
                    f"Recommended action ('{strat.action}') does not address the diagnosed mechanism "
                    f"('{diagnosed}'); it treats a symptom.",
                    evidence_refs=[strat.strategy_id],
                    remediation_hint="Choose an intervention that attacks the retained cause.",
                )
            )
            out.followups.append(
                followup(
                    "intervention_design",
                    "Design a mechanism-fit intervention.",
                    f"What action directly remediates '{diagnosed}'?",
                    priority=8,
                    preferred_domain=strat.owner_domain,
                )
            )

        # -- business rules ----------------------------------------------
        violations = await ctx.deps.rules.validate_strategy(
            strat, context={**ctx.risk_context, **ctx.claim.metadata}
        )
        for v in violations:
            sev = "blocking" if v.severity == "blocking" else "warning"
            if sev == "blocking" and ctx.policies.strategy_reject_on_blocking_rule():
                out.status = "REJECTED"
            elif out.status != "REJECTED":
                out.status = "WEAK"
            out.challenges.append(
                challenge("strategy", sev, f"Business rule '{v.rule_id}' violated: {v.description}", evidence_refs=[strat.strategy_id])  # type: ignore[arg-type]
            )
            out.followups.append(
                followup(
                    v.remediation_capability or "constraint_check",
                    f"Confirm the {v.domain} constraint that blocks this action.",
                    v.description,
                    priority=9 if sev == "blocking" else 6,
                    blocking=sev == "blocking",
                    preferred_domain=v.domain,
                )
            )

        # -- reversibility / prerequisites ---------------------------
        if (strat.reversibility or "").lower() in {"low", "none"}:
            out.status = _weaken(out.status)
            out.methodological_issues.append("Recommended action has low reversibility; require a rollback plan.")

        fit_score = _MECHANISM_FIT.get(fit_label or "", 0.5)
        if addresses:
            fit_score = max(fit_score, 0.8)
        out.score_signals["strategy_fit"] = 0.0 if out.status == "REJECTED" else fit_score
        out.detail = {
            "diagnosed_mechanism": diagnosed,
            "declared_fit": fit_label,
            "addresses_mechanism": addresses,
            "rule_violations": [v.rule_id for v in violations],
        }
        return out


def _diagnosed_mechanism(ctx: SkepticContext) -> str | None:
    if ctx.claim.metadata.get("diagnosed_mechanism"):
        return str(ctx.claim.metadata["diagnosed_mechanism"])
    if ctx.causal:
        return f"{ctx.causal[0].treatment} -> {ctx.causal[0].outcome}"
    for d in ctx.diagnostics:
        if d.retained_hypotheses:
            return d.retained_hypotheses[0]
    return None


def _declared_fit(strat) -> str | None:
    recommended = None
    for opt in strat.options:
        if opt.get("action") == strat.action:
            recommended = opt
            break
    if recommended and recommended.get("mechanism_fit"):
        return str(recommended["mechanism_fit"]).lower()
    return None


def _addresses(action: str, diagnosed: str | None) -> bool:
    if not diagnosed:
        return False
    d = diagnosed.lower()
    pool = ("latency", "lcp", "deploy", "checkout", "payment", "stock", "price", "bug", "regression", "frontend", "js error")
    if "roll back" in action or "rollback" in action or "hotfix" in action:
        return any(w in d for w in ("deploy", "latency", "lcp", "regression", "frontend", "bug", "checkout", "js error"))
    # a real fix names the mechanism: keyword must appear in BOTH the action and the diagnosis
    return any(w in d and w in action for w in pool)


def _weaken(status: str) -> str:
    if status == "REJECTED":
        return status
    return "WEAK" if status == "OK" else status
