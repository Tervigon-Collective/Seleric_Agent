"""Intent classification edge cases for health + forecast + action queries."""

from __future__ import annotations

from seleric_swarm.coordinator.intake import apply_full_flags
from seleric_swarm.coordinator.intake import classify_intents as intake_intents


def test_health_plus_forecast_action_includes_diagnostic():
    q = "how are we doing today, what happens if this continues, and what should we do?"
    intake = set(intake_intents(q))
    assert "executive_health" in intake
    assert "diagnostic" in intake
    assert "predictive" in intake
    assert "prescriptive" in intake


def test_full_flags_force_specialist_intents():
    why = set(intake_intents("Why has CAC increased over the last three days?"))
    assert "diagnostic" in why
    assert "predictive" not in why
    forced = apply_full_flags(why, full_diagnostic=True, full_prediction=True, full_skeptic=True)
    assert "diagnostic" in forced
    assert "predictive" in forced


def test_full_skeptic_alone_adds_diagnostic_floor():
    forced = apply_full_flags({"lookup"}, full_skeptic=True)
    assert "diagnostic" in forced
