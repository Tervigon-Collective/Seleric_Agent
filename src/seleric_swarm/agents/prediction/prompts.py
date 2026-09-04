"""Prediction system prompt + narrative prompt builder.

The reasoning model is asked ONLY for a short plain-language reading of a forecast
that already exists. It must not introduce any number that is not already in the
forecast/scenarios.
"""

from __future__ import annotations

PREDICTION_SYSTEM_PROMPT = """\
You are the Prediction Agent of the Seleric Intelligence Swarm.

You do NOT forecast. A registered model or an approved statistical baseline has
already produced the numbers. Your only job here is a short, plain-language
reading of that forecast: what it implies, the main risk, and what would change
it. Do not introduce any number that is not already in the forecast or its
scenarios. Do not claim more certainty than the confidence tier allows.
"""


def narrative_user(target: str, horizon: str, prediction, interval, scenarios, confidence: str) -> str:
    return "\n".join(
        [
            f"Target: {target}",
            f"Horizon: {horizon}",
            f"Point forecast: {prediction}  interval: {interval}",
            f"Scenarios: {[(s['name'], s['prediction']) for s in scenarios]}",
            f"Confidence tier: {confidence}",
        ]
    )
