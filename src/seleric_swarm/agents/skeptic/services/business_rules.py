"""``BusinessRuleService`` backed by a pluggable constraint store.

`ConstraintStore` is the seam onto real finance / inventory / procurement /
technical constraint systems: given a mission + domain it returns the current
constraint snapshot (stock cover days, contribution-margin floor, open-PO risk,
change-freeze windows, ...). `ConstraintStoreBusinessRuleService` turns that
snapshot into `RuleViolation`s for a `StrategyArtifact`.

`InMemoryConstraintStore` lets a test or an offline run supply the snapshot
directly; production implements `ConstraintStore` against the live systems.

Whether the action "scales spend" / "cuts spend" / "discounts" is decided by an
optional reasoning model when one is injected (real sentence meaning, not
keyword hits); the keyword lists below are the always-available deterministic
fallback, same pattern as the Diagnostic/Skeptic LLM-enrichment modules.
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from seleric_swarm.agents.skeptic.contracts import StrategyArtifact
from seleric_swarm.agents.skeptic.reasoning import NullReasoningModel, ReasoningModel
from seleric_swarm.agents.skeptic.registries import RuleViolation

_log = structlog.get_logger("seleric_swarm.agents.skeptic")

_SCALE_WORDS = ("increase", "scale", "raise", "boost", "ramp", "push")
_SPEND_WORDS = ("spend", "budget", "acquisition", "bid", "paid media")
_CUT_WORDS = ("reduce", "cut", "decrease", "pause", "lower")


class _ActionClassification(BaseModel):
    scales_spend: bool
    cuts_spend: bool
    discounts: bool


@dataclass
class ConstraintSnapshot:
    mission_id: str = ""
    stock_cover_days: float | None = None
    critical_stock_cover_days: float = 7.0
    contribution_margin_floor: float | None = None
    current_contribution_margin: float | None = None
    open_po_risk: str | None = None            # "low" | "elevated" | "high"
    change_freeze_active: bool = False
    max_budget_delta_pct: float | None = None  # e.g. 20.0 -> +/-20% allowed per cycle
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ConstraintStore(Protocol):
    async def snapshot(self, *, mission_id: str, domains: list[str]) -> ConstraintSnapshot: ...


class InMemoryConstraintStore:
    def __init__(self, snapshot: ConstraintSnapshot | dict[str, Any] | None = None) -> None:
        if isinstance(snapshot, dict):
            snapshot = ConstraintSnapshot(**snapshot)
        self._snapshot = snapshot or ConstraintSnapshot()

    async def snapshot(self, *, mission_id: str, domains: list[str]) -> ConstraintSnapshot:
        return self._snapshot


class ConstraintStoreBusinessRuleService:
    """Implements ``registries.BusinessRuleService``."""

    def __init__(self, store: ConstraintStore, *, reasoning: ReasoningModel | None = None) -> None:
        self._store = store
        self._reasoning = reasoning

    async def _classify_action(self, action: str) -> _ActionClassification:
        if self._reasoning is not None and not isinstance(self._reasoning, NullReasoningModel):
            try:
                from seleric_swarm.agents.skeptic.prompts import BUSINESS_ACTION_SYSTEM, business_action_user

                return await self._reasoning.generate_structured(
                    system=BUSINESS_ACTION_SYSTEM,
                    user=business_action_user(action),
                    schema=_ActionClassification,
                    tags=["skeptic", "business_rules"],
                )
            except Exception as exc:  # LLM failure must never break rule checks
                _log.debug("skeptic.business_rules.llm_skipped", error=str(exc))
        return _ActionClassification(
            scales_spend=_has(action, _SCALE_WORDS) and _has(action, _SPEND_WORDS),
            cuts_spend=_has(action, _CUT_WORDS) and _has(action, _SPEND_WORDS),
            discounts="discount" in action or "promo" in action or "price cut" in action,
        )

    async def validate_strategy(
        self, strategy: StrategyArtifact, *, context: dict[str, Any]
    ) -> list[RuleViolation]:
        domains = _domains_for(strategy, context)
        snap = await self._store.snapshot(
            mission_id=context.get("mission_id", strategy.mission_id), domains=domains
        )
        action = (strategy.action or "").lower()
        out: list[RuleViolation] = []

        classification = await self._classify_action(action)
        scales_spend = classification.scales_spend
        cuts_spend = classification.cuts_spend
        discounts = classification.discounts

        # -- inventory: don't scale demand when cover is critical -------------
        if scales_spend and snap.stock_cover_days is not None and (
            snap.stock_cover_days < snap.critical_stock_cover_days
        ):
            out.append(
                RuleViolation(
                    rule_id="inventory.no_scale_when_stock_critical",
                    description=(
                        f"Action scales acquisition while stock cover is "
                        f"{snap.stock_cover_days:g}d (< critical {snap.critical_stock_cover_days:g}d)."
                    ),
                    severity="blocking",
                    domain="inventory",
                    remediation_capability="stock_cover_analysis",
                )
            )

        # -- finance: margin floor -----------------------------------------
        if discounts and snap.contribution_margin_floor is not None:
            proj = context.get("projected_contribution_margin")
            if proj is None:
                proj = snap.current_contribution_margin
            if proj is not None and proj < snap.contribution_margin_floor:
                out.append(
                    RuleViolation(
                        rule_id="finance.margin_floor",
                        description=(
                            f"Discount action drives contribution margin to {proj:.1%}, "
                            f"below the {snap.contribution_margin_floor:.1%} floor."
                        ),
                        severity="blocking",
                        domain="finance",
                        remediation_capability="margin_analysis",
                    )
                )

        # -- finance/governance: budget delta cap ---------------------------
        delta = _abs_pct(strategy) or context.get("budget_delta_pct")
        if (scales_spend or cuts_spend) and snap.max_budget_delta_pct is not None and delta is not None and (
            delta > snap.max_budget_delta_pct
        ):
            out.append(
                RuleViolation(
                    rule_id="finance.budget_delta_cap",
                    description=(
                        f"Requested budget change {delta:g}% exceeds the per-cycle cap "
                        f"of {snap.max_budget_delta_pct:g}%."
                    ),
                    severity="warning",
                    domain="finance",
                    remediation_capability="budget_governance",
                )
            )

        # -- procurement risk -----------------------------------------------
        if scales_spend and (snap.open_po_risk or "").lower() == "high":
            out.append(
                RuleViolation(
                    rule_id="procurement.open_po_risk_high",
                    description="Scaling demand while open-PO risk is high may create unfulfillable orders.",
                    severity="warning",
                    domain="procurement",
                    remediation_capability="po_risk_review",
                )
            )

        # -- technical: change freeze -------------------------------------
        if snap.change_freeze_active and _has(action, ("deploy", "roll back", "rollback", "hotfix", "release")):
            out.append(
                RuleViolation(
                    rule_id="technical.change_freeze",
                    description="A change-freeze window is active; deploy/rollback actions need an exception.",
                    severity="warning",
                    domain="technical",
                    remediation_capability="change_management",
                )
            )

        return out


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _abs_pct(strategy: StrategyArtifact) -> float | None:
    import re

    m = re.search(r"(\d+(?:\.\d+)?)\s*%", strategy.action or "")
    return float(m.group(1)) if m else None


def _domains_for(strategy: StrategyArtifact, context: dict[str, Any]) -> list[str]:
    doms = {strategy.owner_domain} if strategy.owner_domain else set()
    doms.update(context.get("constraint_domains", []) or [])
    doms.update(["inventory", "finance", "procurement", "technical"])
    return sorted(d for d in doms if d)
