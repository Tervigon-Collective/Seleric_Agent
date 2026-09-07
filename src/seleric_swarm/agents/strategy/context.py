"""Shared runtime context + deps for the Strategy workflow."""

from __future__ import annotations

from dataclasses import dataclass, field

from seleric_swarm.agents.strategy.contracts import StrategyRequest
from seleric_swarm.agents.strategy.policies import StrategyPolicies
from seleric_swarm.agents.strategy.reasoning import NullReasoningModel, ReasoningModel


@dataclass(frozen=True)
class StrategyDeps:
    reasoning: ReasoningModel = field(default_factory=NullReasoningModel)


@dataclass
class StrategyContext:
    request: StrategyRequest
    policies: StrategyPolicies
    deps: StrategyDeps
