"""LLM scenario narration for DoWhy-confirmed causal candidates.

By the time this runs, the causal step has already decided WHICH nodes are
statistically responsible (retained/inconclusive) and WHAT their estimated
effect is. This step's only job is turning that into a readable "here is what
likely happened" explanation for the user's actual question -- it may not
propose a mechanism DoWhy didn't confirm, and may not change a confidence
tier. Same LLM-boundary discipline as the rest of this package
(``reasoning.py``): bounded to an allowed id set, gracefully degrades to a
templated sentence if no reasoning model is configured or the call fails.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from seleric_swarm.agents.diagnostic.context import DiagnosticContext
from seleric_swarm.agents.diagnostic.contracts import CausalAnalysisArtifact, DiagnosticHypothesis

_log = structlog.get_logger("seleric_swarm.agents.diagnostic")

CandidateResult = tuple[DiagnosticHypothesis, str, CausalAnalysisArtifact | None]

SCENARIO_SYSTEM_PROMPT = """\
You are the Diagnostic Agent of the Seleric Intelligence Swarm.

A causal engine (DoWhy) has tested candidate drivers against the outcome metric and produced estimated effects and confidence tiers. Your job is to explain, in plain, natural business language, what likely happened for the user's specific question.

Writing & Style Rules:
- Write continuous, fluent executive business prose or clean unnumbered bullet points. NEVER output numbered section titles like "1. Primary Outcome:" or "2. Full-Funnel Causal Drill-Down:".
- NEVER cite internal schema names, JSON payload keys, or prompt variables (do NOT write "ANOMALIES_JSON", "CLAIMS_JSON", "HYPOTHESES_JSON", "SKEPTIC_VERDICT", or "STRONGLY_SUPPORTED").
- Format metric names for human readers: remove "metric." prefixes and convert snake_case to Title Case (e.g. "Net Sales" instead of "metric.net_sales", "Gross Sales" instead of "gross_sales", "Session Conversion Rate" instead of "purchase_cvr", "Web Sessions" instead of "sessions" or "web_sessions").
- Format numeric figures cleanly: round percentages to 1 or 2 decimal places (e.g. "-98.3%"), format numbers with commas or currency ($1,355), and NEVER print raw 15-digit float strings.
- Reference ONLY the nodes given to you. Never invent metrics or change confidence tiers.
- Explain the multi-level causal chain step-by-step:
  - Top-Level Outcome: What metric moved and by how much.
  - Intermediate Driver: How intermediate funnel metrics (like Gross Sales) were impacted.
  - Upstream Root Cause: How root drivers (like conversion rate or web sessions) initiated the drop.
- Explain clearly to the user how each component in the chain is responsible for the overall outcome.
- If no observed movement is given for a node, state clearly that data for that metric was unrecorded or missing this period.
"""


class _Scenario(BaseModel):
    node: str
    narrative: str
    confidence: str = ""


class _ScenarioList(BaseModel):
    scenarios: list[_Scenario] = []


def _humanize_metric(mid: str) -> str:
    return mid.removeprefix("metric.").replace("_", " ").title()


def _fallback_narrative(h: DiagnosticHypothesis, artifact: CausalAnalysisArtifact | None, confidence: str) -> str:
    treatment_label = _humanize_metric(h.treatment_metric)
    outcome_label = _humanize_metric(h.outcome_metric)
    conf_label = confidence.lower().replace("_", " ")
    if artifact is not None and artifact.estimated_effect is not None:
        return (
            f"{treatment_label} was identified as a key driver of {outcome_label}, with a modeled "
            f"causal effect of {artifact.estimated_effect:+.4g} ({conf_label})."
        )
    return f"{treatment_label} is a causally {conf_label} candidate driver of {outcome_label}."


def _observed_movement(ctx: DiagnosticContext, metric_id: str) -> str | None:
    """The real, observed direction/magnitude for a metric this period --
    never inferred from a DoWhy coefficient sign, which describes a modeled
    relationship, not an empirical fact about this specific window."""
    for a in ctx.anomalies:
        if a.metric_id == metric_id:
            if a.deviation_pct is not None:
                return f"{a.direction} {a.deviation_pct:+.1f}%"
            return a.direction or None
    for e in ctx.evidence_for_metric(metric_id):
        direction = e.get("direction")
        change = e.get("change_pct")
        if change is not None:
            try:
                return f"{direction or ''} {float(change):+.1f}%".strip()
            except (TypeError, ValueError):
                pass
        if direction:
            return str(direction)
    return None


def _candidate_line(ctx: DiagnosticContext, h: DiagnosticHypothesis, artifact: CausalAnalysisArtifact | None, confidence: str) -> str:
    treatment_label = _humanize_metric(h.treatment_metric)
    parts = [f"node={h.treatment_metric} ({treatment_label})", f"confidence={confidence}"]
    movement = _observed_movement(ctx, h.treatment_metric)
    parts.append(f"observed_movement={movement}" if movement else "observed_movement=not recorded this period")
    if artifact is not None:
        if artifact.estimated_effect is not None:
            parts.append(f"estimated_effect={artifact.estimated_effect:+.4g}")
        if artifact.common_causes:
            parts.append(f"common_causes={artifact.common_causes}")
        passed = sum(1 for r in artifact.refutation_results if r.get("passed"))
        parts.append(f"refutations_passed={passed}/{len(artifact.refutation_results)}")
    return "; ".join(parts)


async def generate_scenarios(ctx: DiagnosticContext, candidates: list[CandidateResult]) -> dict[str, str]:
    """Returns ``{hypothesis_id: narrative}`` for every causally-confirmed
    candidate passed in (never for rejected or untested ones -- filtering
    that happens before this is called)."""
    if not candidates:
        return {}
    if not ctx.policies.llm_enrichment():
        return {h.hypothesis_id: _fallback_narrative(h, art, conf) for h, conf, art in candidates}

    by_node = {h.treatment_metric: h for h, _, _ in candidates}
    lines = [_candidate_line(ctx, h, art, conf) for h, conf, art in candidates]
    outcome_movement = _observed_movement(ctx, ctx.outcome_metric)
    user = "\n".join(
        [
            f"Question: {ctx.request.question}",
            f"Outcome metric: {ctx.outcome_metric}",
            f"Observed outcome movement this period: {outcome_movement or 'not recorded'}",
            "Confirmed candidates (from DoWhy, not guesses):",
            *[f"- {line}" for line in lines],
        ]
    )

    narratives: dict[str, str] = {}
    try:
        result = await ctx.deps.reasoning.generate_structured(
            system=SCENARIO_SYSTEM_PROMPT,
            user=user,
            schema=_ScenarioList,
            tags=["diagnostic", "scenarios"],
        )
        for s in result.scenarios:
            h = by_node.get(s.node)
            if h is None:
                _log.debug("diagnostic.scenarios.dropped_unallowed_node", node=s.node)
                continue
            narratives[h.hypothesis_id] = s.narrative
    except Exception as exc:  # LLM failure must never break diagnosis
        _log.debug("diagnostic.scenarios.llm_skipped", error=str(exc))

    for h, conf, art in candidates:
        narratives.setdefault(h.hypothesis_id, _fallback_narrative(h, art, conf))
    return narratives
