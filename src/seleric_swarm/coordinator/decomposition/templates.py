"""Progressive problem decomposition templates and helpers."""

from __future__ import annotations

from typing import Any

# Template names constrain LLM/template expansion — they do not replace reasoning.
TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "lookup": [
        {"purpose": "resolve_metric", "question": "Which metric answers the request?", "priority": 9},
        {"purpose": "resolve_scope", "question": "What scope and time range apply?", "priority": 8},
        {"purpose": "retrieve", "question": "Retrieve the metric value for the resolved scope.", "priority": 7},
        {"purpose": "validate", "question": "Validate evidence provenance.", "priority": 6},
        {"purpose": "answer", "question": "Answer the user with gated claims.", "priority": 5},
    ],
    "comparison": [
        {"purpose": "resolve_metric", "question": "Which metric is being compared?", "priority": 9},
        {"purpose": "resolve_period_a", "question": "Resolve comparison period A.", "priority": 8},
        {"purpose": "resolve_period_b", "question": "Resolve comparison period B.", "priority": 8},
        {"purpose": "compare", "question": "Compare the metric across periods.", "priority": 7},
        {"purpose": "validate", "question": "Validate comparability and provenance.", "priority": 6},
    ],
    "anomaly": [
        {"purpose": "identify_target", "question": "Identify the target metric.", "priority": 9},
        {"purpose": "establish_baseline", "question": "Establish the baseline regime.", "priority": 8},
        {"purpose": "retrieve_current", "question": "Retrieve current evidence.", "priority": 7},
        {"purpose": "detect_anomaly", "question": "Run anomaly detection.", "priority": 6},
        {"purpose": "validate_anomaly", "question": "Validate the anomaly finding.", "priority": 5},
    ],
    "diagnostic": [
        {"purpose": "verify_change", "question": "Did the target metric actually change?", "priority": 9},
        {"purpose": "decompose_drivers", "question": "Which major drivers moved?", "priority": 8},
        {"purpose": "identify_frontier", "question": "Which abnormal branch is the causal frontier?", "priority": 7},
        {"purpose": "generate_hypotheses", "question": "Generate candidate hypotheses.", "priority": 6},
        {"purpose": "test_hypotheses", "question": "Test hypotheses with available evidence.", "priority": 5},
        {"purpose": "causal_validation", "question": "Run causal validation where appropriate.", "priority": 4},
    ],
    "predictive": [
        {"purpose": "identify_target", "question": "Identify the prediction target.", "priority": 9},
        {"purpose": "establish_regime", "question": "Establish the current regime.", "priority": 8},
        {"purpose": "select_model", "question": "Select an applicable model.", "priority": 7},
        {"purpose": "infer", "question": "Produce forecast with interval.", "priority": 6},
        {"purpose": "validate_uncertainty", "question": "Validate uncertainty and applicability.", "priority": 5},
    ],
    "prescriptive": [
        {"purpose": "identify_problem", "question": "Identify the problem to act on.", "priority": 9},
        {"purpose": "establish_mechanism", "question": "Establish a supportable mechanism.", "priority": 8},
        {"purpose": "assess_impact", "question": "Assess future impact if unchanged.", "priority": 7},
        {"purpose": "generate_interventions", "question": "Generate interventions.", "priority": 6},
        {"purpose": "check_constraints", "question": "Check business constraints.", "priority": 5},
        {"purpose": "skeptic_validation", "question": "Validate recommendations with Skeptic.", "priority": 4},
    ],
    "executive_health": [
        {"purpose": "business_performance", "question": "How is revenue / orders performing today?", "priority": 9, "branch": "commerce"},
        {"purpose": "paid_acquisition", "question": "How is paid acquisition (CAC/ROAS) performing?", "priority": 8, "branch": "performance"},
        {"purpose": "funnel_health", "question": "Is the purchase funnel healthy?", "priority": 7, "branch": "funnel"},
        {"purpose": "profitability", "question": "Are margins/profitability within norms?", "priority": 6, "branch": "finance"},
        {"purpose": "operational_risk", "question": "Are there major operational incidents?", "priority": 5, "branch": "technical"},
    ],
    "cac_diagnostic": [
        {"purpose": "verify_cac", "question": "Did CAC actually increase?", "priority": 9, "branch": "cac"},
        {"purpose": "magnitude", "question": "How large is the CAC increase?", "priority": 8, "branch": "cac"},
        {"purpose": "onset", "question": "When did the CAC increase begin?", "priority": 7, "branch": "cac"},
        {"purpose": "channel_contrib", "question": "Which channels contributed?", "priority": 6, "branch": "media"},
        {"purpose": "driver_cpm", "question": "Did CPM move abnormally?", "priority": 5, "branch": "media"},
        {"purpose": "driver_ctr", "question": "Did CTR move abnormally?", "priority": 5, "branch": "media"},
        {"purpose": "driver_cpc", "question": "Did CPC move abnormally?", "priority": 5, "branch": "media"},
        {"purpose": "driver_cvr", "question": "Did conversion (purchase CVR) move abnormally?", "priority": 5, "branch": "conversion"},
    ],
}


def select_template(intents: list[str], primary_metric: str | None, query: str) -> str:
    q = query.lower()
    if "executive_health" in intents:
        return "executive_health"
    if primary_metric and "cac" in primary_metric and "diagnostic" in intents:
        return "cac_diagnostic"
    if "cac" in q and "diagnostic" in intents:
        return "cac_diagnostic"
    if "prescriptive" in intents:
        return "prescriptive"
    if "predictive" in intents and "diagnostic" not in intents:
        return "predictive"
    if "diagnostic" in intents:
        return "diagnostic"
    if "comparison" in intents:
        return "comparison"
    if "lookup" in intents:
        return "lookup"
    return "anomaly"
