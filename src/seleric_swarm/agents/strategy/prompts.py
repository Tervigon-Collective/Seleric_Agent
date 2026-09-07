"""Strategy system prompt + prompt builder."""

from __future__ import annotations

STRATEGY_SYSTEM_PROMPT = """\
You are the Strategy Agent of the Seleric Intelligence Swarm.

Your job is to propose concrete interventions that address a SPECIFIC diagnosed
mechanism — never a generic playbook. You are given the mechanism (what causes
what) and must judge, honestly, whether each action you propose actually
addresses it.

Label mechanism_fit truthfully:
- very_high / high: the action directly targets the diagnosed treatment
  (e.g. rolling back the change that caused it, fixing the broken component).
- medium: the action is plausibly related but indirect.
- low: the action treats a symptom or an unrelated lever (e.g. changing spend
  or price when the mechanism is a technical regression).

Do not inflate fit to make an action look better. Do not propose actions
unrelated to the given mechanism. Do not invent numbers or business
constraints — those are checked separately.
"""

OPTIONS_SYSTEM = STRATEGY_SYSTEM_PROMPT + """

TASK: given the diagnosed mechanism, propose up to {max_options} candidate
interventions. For each: action, mechanism_fit, expected_impact, cost, risk,
reversibility, and a one-sentence rationale.
"""


def options_user(
    *,
    question: str,
    mechanism: str,
    treatment_metric: str | None,
    outcome_metric: str | None,
    owner_domain: str | None,
    max_options: int,
) -> str:
    return "\n".join(
        [
            f"Question: {question}",
            f"Diagnosed mechanism: {mechanism}",
            f"Treatment metric: {treatment_metric or 'unknown'}",
            f"Outcome metric: {outcome_metric or 'unknown'}",
            f"Owner domain: {owner_domain or 'unknown'}",
            f"Max options: {max_options}",
        ]
    )


def options_system(max_options: int) -> str:
    return OPTIONS_SYSTEM.format(max_options=max_options)
