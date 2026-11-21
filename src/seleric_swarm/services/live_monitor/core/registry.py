"""Registries: source adapters, rules and actions. Everything the config page and runner know comes from here."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from seleric_swarm.services.live_monitor.core.model import (
    CADENCES,
    LEVELS,
    SEVERITIES,
    Entity,
    FieldDef,
    Hit,
    Snapshot,
)

if TYPE_CHECKING:
    from seleric_swarm.services.live_monitor.core.context import RuleContext


class Params(BaseModel):
    """Base for rule params: unknown keys are rejected, so a typo in the config is an error, not a silent default."""

    model_config = ConfigDict(extra="forbid")


RuleFn = Callable[["RuleContext", Entity, Any], Hit | None]


@dataclass(frozen=True)
class RuleDef:
    id: str
    title: str
    description: str
    tier: Literal["performance", "compliance", "custom"]
    platforms: tuple[str, ...]
    levels: tuple[str, ...]
    cadence: str
    params: type[Params]
    fn: RuleFn
    actions: tuple[str, ...] = ()
    playbook_ref: str = ""
    # Efficiency rules (CPA, ROAS, no purchase) are capped at info while the entity is learning or young.
    efficiency: bool = False
    # Keep only the highest-level hit in a lineage (a campaign-wide problem is not repeated for every ad under it).
    rollup: bool = False
    # Rules this one restates on the same entity (ROAS below break-even is CPA above allowable): one finding, not two.
    supersedes: tuple[str, ...] = ()

    def applies(self, platform: str, level: str) -> bool:
        return platform in self.platforms and level in self.levels


RULES: dict[str, RuleDef] = {}


def register_rule(
    *, id: str, title: str, description: str, tier: Literal["performance", "compliance"], levels: tuple[str, ...],
    cadence: str, params: type[Params], platforms: tuple[str, ...] = ("meta",), actions: tuple[str, ...] = (),
    playbook_ref: str = "", efficiency: bool = False, rollup: bool = False, supersedes: tuple[str, ...] = (),
) -> Callable[[RuleFn], RuleFn]:
    if cadence not in CADENCES:
        raise ValueError(f"{id}: cadence must be one of {', '.join(CADENCES)}")
    if set(levels) - set(LEVELS):
        raise ValueError(f"{id}: unknown level in {levels}")

    def deco(fn: RuleFn) -> RuleFn:
        if id in RULES:
            raise ValueError(f"rule {id} registered twice")
        RULES[id] = RuleDef(id, title, description, tier, platforms, levels, cadence, params, fn, actions, playbook_ref,
                            efficiency, rollup, supersedes)
        return fn

    return deco


@dataclass(frozen=True)
class ActionInput:
    """A value the person may edit in the approve dialog (prefilled by ``prefill``)."""

    name: str
    label: str
    max_length: int = 200


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]
    # Arguments filled from an earlier step's result at execute time: {arg_name: (step_index, dotted path)}.
    from_steps: dict[str, tuple[int, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionPlan:
    summary: str
    calls: tuple[ToolCall, ...]
    current: dict[str, Any] = field(default_factory=dict)
    change: dict[str, Any] = field(default_factory=dict)


ActionBuilder = Callable[[Any, Entity, Any, dict[str, str]], Awaitable[ActionPlan]]
ActionPrefill = Callable[[Entity, Any], dict[str, str]]


@dataclass(frozen=True)
class ActionDef:
    id: str
    title: str
    platform: str
    levels: tuple[str, ...]
    risk: Literal["link", "low", "delivery", "spend"]
    # build(client, entity, tenant, inputs) -> the exact MCP calls. None = a link action (no MCP write).
    build: ActionBuilder | None = None
    inputs: tuple[ActionInput, ...] = ()
    prefill: ActionPrefill | None = None


ACTIONS: dict[tuple[str, str], ActionDef] = {}


def register_action(action: ActionDef) -> ActionDef:
    key = (action.platform, action.id)
    if key in ACTIONS:
        raise ValueError(f"action {key} registered twice")
    ACTIONS[key] = action
    return action


class SourceAdapter(Protocol):
    platform: str

    async def discover(self, tenant: Any) -> Snapshot: ...

    async def daily_history(self, tenant: Any, days: int) -> list[dict[str, Any]]:
        """One row per ad per day: ids, ``date``, and the additive metrics."""
        ...

    async def hourly_profile(self, tenant: Any, days: int) -> dict[int, dict[str, float]]:
        """Account totals per hour of day over ``days`` (additive metrics)."""
        ...

    def field_catalogue(self) -> list[FieldDef]: ...


ADAPTERS: dict[str, Callable[[Any], SourceAdapter]] = {}


def register_adapter(platform: str, factory: Callable[[Any], SourceAdapter]) -> None:
    ADAPTERS[platform] = factory


def ensure_loaded() -> None:
    """Import the built-in rules, actions and adapters so their decorators run."""
    from seleric_swarm.services.live_monitor.adapters import meta  # noqa: F401
    from seleric_swarm.services.live_monitor.rules import compliance, performance  # noqa: F401


__all__ = [
    "ACTIONS", "ADAPTERS", "RULES", "SEVERITIES", "ActionDef", "ActionInput", "ActionPlan", "Params", "RuleDef",
    "SourceAdapter", "ToolCall", "ensure_loaded", "register_action", "register_adapter", "register_rule",
]
