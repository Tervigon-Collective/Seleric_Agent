# Coordinator Specialist Roster

Reference for anyone editing the `prompts/coordinator/*` prompts. Every routing
token a coordinator prompt emits (`domain_lead`, `branch`, `purpose`) is matched
downstream against the values below. If this file and the code disagree, the code
wins — fix the drift, do not paper over it in the prompt.

Sources of truth:
- `config/agent_registry.yaml` — agents, `capabilities`, `enabled`, `seleric_module`, `ontology`
- `src/seleric_swarm/agents/*/prompts.py` — the intelligence agents' own system prompts
- `src/seleric_swarm/coordinator/decomposition/__init__.py` — `purpose -> capability` map, `branch` re-branching
- `src/seleric_swarm/services/metrics.py` — `catalog_prompt()` (emits `domain=`), `owner_agent_for()` (`<domain>_agent`)

---

## 1. Domain agents (data retrieval)

Domain agents have **no LLM system prompt**. They observe metrics from one
Seleric MCP module and emit evidence. `domain_lead` in `classify` / `classify_swarm`
is always `"<domain>_agent"`, where `<domain>` is the `domain=` field on the
primary resolved metric's row in the registry catalogue.

| agent | enabled | seleric_module (tool) | capabilities (skills) | ontology (entities it can reason over) |
|---|---|---|---|---|
| `commerce_agent` | yes | `commerce` | orders, products, pricing, returns, ecommerce_analysis | order, sku, product, pricing, discount, return, marketplace |
| `performance_agent` | yes | `paidmedia` | campaign_analysis, cac_analysis, roas_analysis, attribution_analysis | campaign, adset, ad, creative, audience, attribution, delivery |
| `attribution_agent` | yes | `attribution` | attribution_analysis, channel_attribution | channel, last_touch, first_touch, campaign, placement, organic |
| `funnel_agent` | yes | `webanalytics` | funnel_analysis, journey_analysis, device_analysis | session, landing_page, pdp, atc, checkout, payment, device, journey |
| `finance_agent` | yes | `finance` | profitability, cogs, margin, cash | gross_profit, net_profit, margin, cogs, rto, cash |
| `product_agent` | yes | `product` | product_performance, sku_analysis, margin_analysis | sku, variant, collection, margin, velocity, assortment |
| `customer_agent` | yes | `customer` | ltv_analysis, repeat_purchase_analysis, cohort_analysis | cohort, ltv, repeat, acquisition, retention |
| `operations_agent` | yes | `operations` | returns_analysis, refund_analysis | refund, return, fulfillment, sla, ops |
| `inventory_agent` | **no** (no module) | — | stock_cover, stockout_risk, ageing | stock_cover, reorder_point, ageing, velocity, dead_stock |
| `procurement_agent` | **no** (no module) | — | vendor, po, moq, lead_time | vendor, po, moq, lead_time, capacity, inbound |
| `technical_agent` | **no** (no module) | — | latency, errors, deployments, incident_analysis | latency, web_vitals, javascript_error, deployment, incident, api |

Routing notes the prompts already encode:
- CAC / ROAS paired with commerce sales -> `performance_agent` (overrides the metric's own domain).
- `inventory` / `procurement` / `technical` are disabled; don't route a lead to them.

---

## 2. Intelligence agents (reasoning)

These carry LLM system prompts (in `agents/<name>/prompts.py`). The coordinator
reaches them through **`purpose` slugs** in `decompose_mission`, not through
`domain_lead`. Only these eight slugs map to a capability
(`decomposition/__init__.py::_caps_for_purpose`); every other slug falls through
to `metric_observation` and is just a human-readable label.

| purpose slug | capability required | agent | agent's job (from its system prompt) |
|---|---|---|---|
| `retrieve` | metric_observation, evidence_collection | `observer_agent` | Map a question to a canonical registry metric; collect evidence. Never invent a formula. |
| `detect_anomaly` | anomaly_analysis | `anomaly_agent` | Flag abnormal deviations against a baseline regime. |
| `generate_hypotheses` | hypothesis_generation | `diagnostic_agent` | Propose explicit, testable hypotheses for *why* a metric changed — concrete mechanism + treatment metric + owning domain. Does **not** decide truth, estimate effects, or state a root cause. Only mechanisms whose treatment metric is already observed or in the causal graph. |
| `test_hypotheses` | hypothesis_test | `diagnostic_agent` | Run deterministic tests on hypotheses using evidence already in hand. |
| `causal_validation` | causal_diagnosis | `diagnostic_agent` | Causal engine confirms/refutes the surviving mechanism. |
| `infer` | forecasting | `prediction_agent` | Forecast **orchestration**, not a forecasting LLM. Numbers come only from a registered production model or an approved statistical baseline, else `INSUFFICIENT_PREDICTIVE_EVIDENCE`. LLM only writes a plain-language reading of an existing forecast. |
| `generate_interventions` | intervention_design | `strategy_agent` | Propose concrete interventions that target a **specific diagnosed mechanism** — never a generic playbook. Labels `mechanism_fit` honestly; does not invent numbers or constraints. |
| `skeptic_validation` | challenge | `skeptic_agent` | Independent verification. Verdict `PASS` / `REVISE` / `REJECT`; a non-pass carries specific remediation tasks. Never invents evidence, never treats absence of evidence as evidence of absence, never claims causal certainty. Never becomes mission lead — routes domain issues back via `follow_up_task.preferred_domain`. |

Descriptive-only slugs used in the standard sequences (no capability wiring, safe
to keep for readability): `resolve_metric`, `resolve_scope`, `validate`,
`answer`, `resolve_period_a`, `resolve_period_b`, `compare`, `verify_change`,
`decompose_drivers`, `identify_frontier`, `identify_target`,
`establish_baseline`, `retrieve_current`, `validate_anomaly`,
`establish_regime`, `select_model`, `validate_uncertainty`, `identify_problem`,
`establish_mechanism`, `assess_impact`, `check_constraints`,
`business_performance`, `paid_acquisition`, `funnel_health`, `profitability`,
`operational_risk`.

---

## 3. `branch` tokens

`decompose_mission` sets `branch` per step. Only four are load-bearing — the
evidence re-brancher (`decomposition/__init__.py::refine_from_evidence` and the
domain filter around L429) matches them verbatim:

| branch | meaning | re-branch behaviour |
|---|---|---|
| `media` | paid-channel / CPM / CTR / CPC | retired when media drivers are stable and conversion is the frontier |
| `funnel` | funnel-stage questions | added when conversion is the frontier; filtered out if `funnel` not in candidate domains |
| `conversion` | purchase-CVR questions | signals the frontier moved to conversion |
| `technical` | page-speed / JS-error / deployment | added when mobile CVR is abnormal; filtered out if `technical` not in candidate domains |

Domain-wide labels (`commerce`, `performance`, `finance`, `operations`) are
passed through as `preferred_domain` routing hints only. `executive_health` uses
branches `commerce / performance / funnel / finance / operations`, one per step
— the `operational_risk` step routes to `operations_agent` (enabled), **not**
`technical_agent` (disabled). The offline template in
`decomposition/templates.py` still says `branch: "technical"` for that step; that
is a bug — see §5.

---

## 4. Skeptic follow-up -> remediation kind

`classify_followup` maps a Skeptic follow-up to one `RemediationKind`
(`coordinator/governance/remediation.py`): `missing_causal_graph`, `model_drift`,
`forecast_metadata`, `strategy_constraint`, `confounder`, `metric_semantics`,
`hypothesis_test`, `missing_evidence`, `generic`. Narrowest wins; check in that
order. `missing_causal_graph` lets the Coordinator skip a full diagnostic re-run;
`model_drift` / `forecast_metadata` scope the re-run to prediction only.

---

## 5. Known mismatches — fix in code, prompts alone can't

The prompts were tightened as far as prompt text allows. These remain and need
code changes:

1. **Descriptive `purpose` slugs fall back to Observer.**
   `decomposition/__init__.py::_caps_for_purpose` returns `["metric_observation"]`
   for any unknown slug, so `resolve_metric`, `resolve_scope`, `validate`,
   `answer`, `verify_change`, `identify_frontier`, `establish_mechanism`, … all
   become Observer tasks. A plain lookup can run Observer five times. Fix: split
   into `PURPOSE_CAPABILITIES` (the 8 wired slugs) + `COORDINATOR_LOCAL` (the
   descriptive slugs, executed by the Coordinator, no specialist call) + a
   validation error for anything else. Never use Observer as the generic
   fallback.

2. **Readiness is derived from priority.** `decomposition/__init__.py` L143 sets
   `status = "ready" if priority >= 7 else "pending"`. Readiness should be
   `all_dependencies_satisfied(task)`; priority should only order the already-
   ready set. As-is, `causal_validation` at priority 10 can be "ready" before
   its hypothesis tests exist. The decompose prompt no longer ties priority to
   readiness, but the code still does.

3. **`executive_health` operational branch.** `templates.py` L58 hardcodes
   `branch: "technical"` (disabled agent) for `operational_risk`; the LLM prompt
   now emits `branch: "operations"`. Align the offline template, and note
   `_domains_from_subs` only special-cases `technical`/`funnel` — add
   `operations` there or rely on the pass-through `preferred_domain`.

4. **No `primary_intent` passed to `decompose_mission`.** The prompt orders
   multi-intent frontiers itself (diagnostic → predictive → prescriptive →
   skeptic). Passing `primary_intent` (or the ordered intent list) from
   classification would make that deterministic rather than model-inferred.

5. **`executive_health` + `diagnostic` auto-pairing.** Removed from the
   `classify_swarm` prompt (discovery-first, escalate from evidence). The
   offline fallback in `llm/adapters/fake.py` L251-252 still adds `diagnostic`;
   update it and any planner code that assumes the pair.

---

## 6. Dynamic decomposition — what the prompt can and can't do

`decompose_mission.v1` was rewritten to plan **only the first investigation
cycle**: a small frontier of currently-answerable, high-information-gain
questions. Diagnostic missions now emit just `verify_change` + `detect_anomaly`;
hypothesis / test / causal steps are expected to come from a later cycle. This is
as far as the prompt can go, because:

1. **`decompose_mission` is called once.** `decomposition/__init__.py::initial_decomposition`
   invokes the LLM a single time with `query / intents / primary_metric /
   entities / candidate_domains` — **no evidence**. There is no LLM "re-plan
   given evidence" path. True evidence-driven replanning needs a new prompt
   (e.g. `coordinator.replan_mission`) plus a caller that passes current
   evidence, resolved facts, and open questions, wired into the refinement loop.

2. **Refinement is deterministic Python.** `refine_from_evidence` uses hardcoded
   ±10% deviation thresholds (`_media_drivers_stable`, `_conversion_abnormal`,
   `_mobile_cvr_abnormal`) and a fixed `media → funnel → technical` escalation
   order with canned subquestion lists. `refine_from_skeptic_followups` only
   appends. Emergent branch creation / pruning and evidence-driven leadership
   transfer are not possible until this becomes a reasoning step.

3. **The offline `TEMPLATES` fallback still emits full fixed pipelines.** When
   the LLM decomposition call fails, `initial_decomposition` falls back to
   `select_template` + `TEMPLATES`, which is exactly the deterministic
   stage-by-stage behaviour the first-cycle prompt is moving away from. The two
   paths now diverge in shape; align `TEMPLATES` (or accept the fallback is a
   degraded mode).

4. **Information gain is priority-only today.** Steps are built with
   `information_gain(priority=p)` and all other factors
   (`business_impact / resolve_prob / cost / latency`) left at defaults, so EIG
   is a monotone function of `priority`. `select_next_subquestions` ranks by EIG
   then priority with a 0.12 cutoff (≈ priority ≥ 5). For the prompt's
   "information gain per unit cost" framing to mean anything beyond ordering,
   the planner must pass real cost / resolve-probability estimates per step.
