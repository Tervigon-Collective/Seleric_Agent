"""Challenge planning.

Turns the claim + risk class into an ordered list of *challenge intents* -- the
questions the Skeptic commits to answering. Deterministic; the reasoning model
may later enrich phrasing but never removes a required challenge.
"""

from __future__ import annotations

from seleric_swarm.agents.skeptic.context import SkepticContext

_ALWAYS = [
    "Is every material number backed by a referenced evidence artifact?",
    "Is the provenance (source, query hash, calc version, freshness) complete?",
    "Do any Blackboard artifacts contradict this claim?",
    "Is a similarly-named metric being used with a different definition?",
]

_BY_TYPE: dict[str, list[str]] = {
    "numeric": ["Is the sample size adequate and the effect outside noise?"],
    "comparison": [
        "Are the two periods/entities comparable (seasonality, mix, definition)?",
        "Is the difference statistically meaningful?",
    ],
    "anomaly": [
        "Is the detector valid for this metric/context and is there enough history?",
        "Could this be late-arriving data or a seasonal effect?",
        "Is the anomaly consistent across segments?",
    ],
    "correlation": [
        "Is this association being presented as causation?",
        "What confounder could produce both series?",
    ],
    "causal": [
        "Does the treatment precede the outcome in time?",
        "Is the causal graph registered and does it support treatment -> outcome?",
        "Are the expected confounders adjusted for?",
        "Do the refutation tests pass?",
        "What alternative mechanism remains plausible?",
    ],
    "forecast": [
        "Is there a registered, approved model with current metadata?",
        "Is model drift within tolerance and is the current regime in-domain?",
        "Does the prediction carry an interval and lineage?",
    ],
    "recommendation": [
        "Does the action address the diagnosed mechanism?",
        "Do finance/inventory/procurement/technical constraints invalidate it?",
        "Is there a lower-risk, more reversible alternative?",
        "What happens if the diagnosis is wrong?",
    ],
    "action": [
        "Does the action address the diagnosed mechanism?",
        "Is the action reversible and are its prerequisites met?",
        "Do business constraints block it?",
    ],
}


def build_challenge_plan(ctx: SkepticContext) -> list[str]:
    plan = list(_ALWAYS) + list(_BY_TYPE.get(ctx.claim.claim_type, []))
    if ctx.risk_class in {"R4", "R5"}:
        plan.append("Stress-test: how does the conclusion change under the pessimistic assumption?")
    cap = ctx.policies.budget("max_challenges")
    return plan[:cap] if cap else plan
