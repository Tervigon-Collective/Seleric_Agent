"""Coordinator policy loader — activation matrix, topology, budgets, synthesis guards."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from seleric_swarm.coordinator.contracts import MissionBudget


class SpecialistActivation(BaseModel):
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class LeadershipPolicy(BaseModel):
    min_score_delta: float = 0.15
    require_new_evidence: bool = True
    max_transfers: int = 6
    loop_window: int = 4


class DecompositionPolicy(BaseModel):
    eig_stop_threshold: float = 0.12
    max_versions: int = 8
    max_open_subquestions: int = 24


class SynthesisPolicy(BaseModel):
    forbidden_phrases_when_challenged: list[str] = Field(
        default_factory=lambda: [
            "validated mechanism",
            "confirmed root cause",
            "proven cause",
            "established root cause",
            "validated and confirmed",
        ]
    )
    prototype_banner: str = (
        "PROTOTYPE / FIXTURE RESULT — This result uses synthetic evidence "
        "and must not be treated as production intelligence."
    )


class CoordinatorPolicies(BaseModel):
    activation: dict[str, SpecialistActivation] = Field(default_factory=dict)
    domain_topology: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    leadership: LeadershipPolicy = Field(default_factory=LeadershipPolicy)
    decomposition: DecompositionPolicy = Field(default_factory=DecompositionPolicy)
    budgets: MissionBudget = Field(default_factory=MissionBudget)
    synthesis: SynthesisPolicy = Field(default_factory=SynthesisPolicy)
    business_timezone: str = "Asia/Kolkata"

    def specialists_for(self, intent_band: str) -> SpecialistActivation:
        key = intent_band.upper()
        return self.activation.get(key) or self.activation.get(intent_band) or SpecialistActivation()


def _default_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "coordinator_policies.yaml"


def load_coordinator_policies(path: str | None = None) -> CoordinatorPolicies:
    """Return an **isolated** policy set.

    The parsed file is cached, but each caller gets a deep copy so a mission (or
    test) that mutates ``policies.leadership``/``.budgets`` in place cannot poison
    every later mission sharing the process.
    """
    return _load_coordinator_policies_cached(path).model_copy(deep=True)


@lru_cache
def _load_coordinator_policies_cached(path: str | None = None) -> CoordinatorPolicies:
    cfg_path = Path(path) if path else _default_path()
    if not cfg_path.exists():
        return CoordinatorPolicies(
            activation={
                "LOOKUP": SpecialistActivation(required=["observer_agent"]),
                "COMPARISON": SpecialistActivation(required=["observer_agent"]),
                "ANOMALY": SpecialistActivation(required=["observer_agent", "anomaly_agent"]),
                "DIAGNOSTIC": SpecialistActivation(
                    required=["observer_agent", "anomaly_agent", "diagnostic_agent", "skeptic_agent"]
                ),
                "PREDICTIVE": SpecialistActivation(
                    required=["observer_agent", "prediction_agent", "skeptic_agent"]
                ),
                "PRESCRIPTIVE": SpecialistActivation(
                    required=["strategy_agent", "skeptic_agent"],
                    optional=["diagnostic_agent", "prediction_agent"],
                ),
            },
            domain_topology={
                "performance": {"downstream": ["funnel", "commerce", "finance"]},
                "funnel": {"related": ["technical", "commerce"]},
                "commerce": {"related": ["inventory", "finance", "funnel"]},
                "inventory": {"related": ["procurement", "commerce"]},
                "procurement": {"related": ["inventory", "finance"]},
                "technical": {"related": ["funnel"]},
                "finance": {"related": ["commerce", "performance"]},
            },
        )
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    activation = {
        k: SpecialistActivation(**v) for k, v in (raw.get("activation") or {}).items()
    }
    return CoordinatorPolicies(
        activation=activation,
        domain_topology=dict(raw.get("domain_topology") or {}),
        leadership=LeadershipPolicy(**(raw.get("leadership") or {})),
        decomposition=DecompositionPolicy(**(raw.get("decomposition") or {})),
        budgets=MissionBudget(**(raw.get("budgets") or {})),
        synthesis=SynthesisPolicy(**(raw.get("synthesis") or {})),
        business_timezone=str(raw.get("business_timezone") or "Asia/Kolkata"),
    )
