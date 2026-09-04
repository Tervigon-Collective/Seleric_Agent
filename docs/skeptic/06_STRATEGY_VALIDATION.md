# 06 - Strategy Validation

`validators/strategy_validator.py` + `registries.BusinessRuleService`.

## Mechanism fit

The core question: **does the action attack the diagnosed mechanism, or a
symptom?**

- Diagnosed mechanism comes from `claim.metadata["diagnosed_mechanism"]`, else
  the attached `CausalAnalysisArtifact` (`treatment -> outcome`), else a
  `DiagnosticArtifact.retained_hypotheses[0]`.
- If there is a diagnosed mechanism, the action does **not** address it
  (keyword-overlap heuristic + rollback/hotfix special case), and the declared
  `mechanism_fit` is `low`/absent or the action is a known symptom action
  (reduce spend, increase discount, shift campaigns, lower bids) ->
  **blocking `strategy` challenge -> REJECT** + `intervention_design`
  follow-up.

Example: diagnosis "checkout bug"; strategy "reduce Meta budget 30%" -> REJECT.

## Business-rule interface

```python
class BusinessRuleService(Protocol):
    async def validate_strategy(self, strategy: StrategyArtifact,
                                *, context: dict) -> list[RuleViolation]: ...
```

`InMemoryBusinessRuleService` ships two deterministic guardrails; the real
implementation reads finance / inventory / procurement / technical constraint
stores.

- `inventory.no_scale_when_stock_critical` - action scales acquisition
  (`increase/scale/raise` + `spend/budget/acquisition`) while
  `context["stock_cover_days"] < critical_stock_cover_days` -> **blocking** ->
  REJECT + follow-up `preferred_domain="inventory"`.
- `finance.margin_floor` - discount action with `context["margin_floor_violation"]`
  -> blocking.

`strategy.reject_on_blocking_rule_violation` (policy) gates whether a blocking
violation forces REJECT (default true).

## Reversibility / prerequisites

Low/none reversibility -> methodological issue "require a rollback plan".
Unmet prerequisites and missing measurement plan surface as warnings.

## The Skeptic never becomes the domain agent

A rule violation produces a `FollowUpTask` with `preferred_domain` set; the
**Coordinator** decides leadership. The Skeptic does not impersonate the
Inventory / Finance agent.
