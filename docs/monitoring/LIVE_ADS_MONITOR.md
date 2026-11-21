# Live ads monitor

How the 5‑minute Meta ads monitor works: what it watches, how it judges, default thresholds, and what you can do from a notification.

Code: `src/seleric_swarm/services/live_monitor/`  
Defaults: `config/live_monitor.yaml`  
Edit in product: Monitoring → **Live ads** tab (brand **Configure** modal for account / pixel / attribution / budget caps)  
Alerts: Monitoring → **Notifications** tab (`?ops=notifications`)

---

## 1. What it is

A long‑running process (`live-monitor-cron`) that every **5 minutes**:

1. Discovers **ACTIVE** Meta campaigns / ad sets / ads for each configured tenant (via Seleric MCP).
2. Pulls live insights (today; rolling 3d / 7d / prior‑7d on the hour).
3. Runs **due** rules (by cadence: 5m, 1h, or daily).
4. Opens / escalates / resolves findings in `monitor.findings`.
5. On **open / reopen / escalate**, runs a structured **diagnosis** once (gates + artifacts + recommended action; LLM cause with rule fallback). Eased / resolved still get a short narration.
6. Surfaces cards in **Notifications** with a primary Approve CTA, evidence / gates, and an activity trail (gated by `ADS_ACTIONS_ENABLED`).

Baselines and finance guardrails refresh **nightly** (after 03:00 local). Daily rules run after **10:00** local.

Only entities that can actually spend are judged for performance: an ACTIVE campaign whose ads are all paused is ignored (except status rules).

---

## 2. Analysis types

| Type | How it works | Used for |
|------|----------------|----------|
| **Pace vs hour‑of‑day curve** | Expected spend = daily budget × share of a normal day’s spend done by now (28‑day hourly curve; linear if missing). Launch‑day campaigns only count time since go‑live. | Spend pacing |
| **Gamma–Poisson rate** | Probability that the true purchase rate is worse than the guardrail, with a weak prior from baselines (`prior_events=2`). | CPA, spend with no purchase, scale |
| **ROAS probability** | Same family: P(true Meta ROAS &lt; target or break‑even) given purchases, spend, value, and AOV prior. | ROAS below |
| **Beta–Binomial** | P(true CTR or conversion rate is below a line), with CTR/CVR priors. | CTR drop, conversion drop |
| **Baseline band** | Entity (then parent, then account) p10 / p50 / p90 from trailing 14 days; weekday‑aware where configured. | CPM spike, CTR drop, conversion drop, stall |
| **Hard thresholds** | Simple comparisons (frequency, LPV/click, learning stage, attribution fields). | Fatigue, landing, learning, compliance |
| **Tracking reference** | Pixel purchases vs Shopify Meta‑attributed orders; median ratio over days with enough store volume. | Tracking gap |

**Confidence gate:** efficiency findings only fire when the probability is ≥ `stats.confidence` (**0.8** by default). Raw CPA/ROAS on a tiny sample never pages.

**Learning guard:** entities younger than 3 days, or with learning stage `LEARNING`, have **efficiency** findings capped at **info** (delivery / tracking / compliance still fire).

**Rollup:** if a campaign and its ad set both hit the same rule, only the highest ancestor is kept; children are listed in evidence.

**Spam control:** notify on open / severity change / resolve; `cooldown_ticks` (default **2**) before a miss resolves; quiet hours send criticals only.

### Trust / gates

Diagnosis never re-judges ROAS/CPA. When a finding opens (or reopens / escalates), the monitor writes a structured diagnosis once: deterministic **gates** and **artifacts** from the hit evidence, plus an optional LLM sentence for cause / bullets / recommended action.

| Gate chip | Meaning |
|-----------|---------|
| **confidence** | The probability the rule already computed (e.g. P(CPA above) or P(ROAS below)). Must be ≥ `stats.confidence` for efficiency rules to fire. |
| **volume** | Min spend / impressions / clicks for that rule were met (`min_spend_ok`, `min_impressions_ok`, …). Under the floor → no finding. |
| **learning** | Entity still learning → efficiency severity capped at info. |
| **window** | Which metric window the rule used (`today` / `3d`). |

The card’s primary CTA is `recommended_action_id`, always chosen from the finding’s allow-listed actions. Preview → Approve still goes through Seleric MCP; every lifecycle / diagnosis / preview / approve / label is on `monitor.finding_events` with actor.

---

## 3. Guardrails (money lines)

Recomputed nightly from finance (contribution margin, net AOV) and Meta pixel AOV, unless `guardrails.mode: manual`.

| Guardrail | Meaning | Typical brand‑20 seed |
|-----------|---------|------------------------|
| `be_roas_net` | Break‑even on net sales | ~1.71 |
| `be_roas_platform` | Same line on Meta’s reported scale: `be_net × meta_aov / net_aov` | ~2.2 |
| `target_roas` | `be_roas_platform × target_roas_multiple` (default **1.3×**) | ~2.9 |
| `allowable_cpa` | `net_aov × contribution_margin` | ~₹1,200 |

Shown on each tenant’s status strip and in finding evidence.

---

## 4. Performance rules (P0)

Cadence · levels · default thresholds · suggested actions.

| Rule id | Cadence | Levels | Fires when | Default thresholds | Actions |
|---------|---------|--------|------------|--------------------|---------|
| `spend_pacing` | 5m | account, campaign | Today’s spend vs expected‑by‑now | Over ≥ **1.5×** warning, ≥ **1.75×** critical; under ≤ **0.4×** after **12:00** local; skip if expected &lt; ₹500 | Pause (critical, non‑account), Open agent |
| `spend_no_purchase` | 5m | campaign, adset, ad | Spend today, 0 purchases, unlikely at usual rate | Warn at **1.5×** allowable CPA; critical at **3×**; P(0) &lt; **0.1** | Pause, Open agent |
| `cpa_above_allowable` | 1h | campaign, adset | Rolling 3d CPA likely above allowable | Spend ≥ **2×** allowable; P ≥ **0.8**; critical if 7d agrees | Lower budget, Pause, Open agent |
| `roas_below` | 1h | campaign, adset | 3d Meta ROAS likely below target / BE | Min spend **₹5,000**; warn if P(ROAS &lt; target) ≥ 0.8; critical if P(ROAS &lt; BE) ≥ 0.8 and 7d agrees. Supersedes CPA on the same entity | Lower budget, Pause, Open agent |
| `cpm_spike` | 1h | campaign, adset | Today’s CPM above usual high | ≥ **1.2×** entity p90; ≥ **5,000** impressions | Open agent |
| `ctr_drop` | 1h | ad | Link CTR likely below usual low | ≥ **5,000** impressions; P(CTR &lt; p10) ≥ 0.8 | Open agent, Pause |
| `creative_fatigue` | daily | ad | High frequency + CTR falling | Freq ≥ **2.0** warn / **2.5** critical; 3d CTR ≥ **20%** below prior 7d; ≥ 3k impressions | Open agent, Pause |
| `landing_break` | 5m | campaign, ad | LPV per link click collapsed | ≥ **200** clicks; warn &lt; **0.80**, critical &lt; **0.50**; card includes URL check | Open agent |
| `conversion_drop` | 1h | account, campaign | Purchases / LPV down while CTR looks normal | ≥ **300** LPV; rate ≤ **50%** of baseline (critical ≤ **25%**); skipped if CTR is already below p10 | Open agent |
| `tracking_gap` | daily | account | Pixel vs store, or ATC under‑firing | Ratio &gt; **40%** off usual median (warn/critical); ATC‑only under‑fire → **info** | Open agent |
| `delivery_stall` | 5m | campaign, adset | Active, deliverable, no impressions in the last hour when history expects them | Window **08–23** local; expected ≥ **200** (critical ≥ **1000**); entity age ≥ **2h**; skipped if budget already spent | Open agent |
| `learning_limited` | daily | adset | Meta learning state | `LEARNING_LIMITED` / FAIL → warning; still `LEARNING` after **7** days → info | Open agent |
| `scale_candidate` | daily | campaign, adset | Winner limited by budget (info) | P(3d ROAS ≥ **1.3×** BE) ≥ 0.8; spend ≥ **₹3,000**; last 2 full days ≥ **90%** of budget | Raise budget, Open agent |

---

## 5. Compliance rules (P1)

Most run every **5 minutes**. Playbook naming (`TH-{sku}-…`) scopes label / IG / lifecycle / attribution / creative checks via `expectations.compliance_name_pattern`.

| Rule id | What | Severity | Actions |
|---------|------|----------|---------|
| `status_not_active` | Under an active parent: disapproved, with issues, billing pending, or review &gt; 24h | critical / warning | Open agent |
| `ad_review_issue` | ACTIVE ad still carries issues / review feedback | warning | Open agent |
| `missing_campaign_label` | No ad label for grouping | warning | Attach SKU label |
| `missing_ig_stream` | Instagram selected but Feed (`stream`) missing | warning | Add Instagram Feed |
| `wrong_pixel_or_event` | Optimises on another pixel / event than configured | warning | Open agent |
| `lifecycle_restricted` | Existing‑customer budget % set on prospecting | warning | Open agent |
| `attribution_mismatch` | Not click 7d / view 1d (or tenant expectation) | warning | Open agent |
| `missing_display_link_or_headline` | Link creative missing brand domain or headline | warning | Fix headline / display link |

---

## 6. Actions (one‑click)

Preview runs Seleric MCP with `validate_only` (shows current → after). **Approve** spends the MCP confirmation token server‑side. Browser never sees the token. Audited on the finding and in `monitor.live_action_requests`.

| Action id | Title | Risk | What it does | Caps |
|-----------|-------|------|--------------|------|
| `open_agent` | Open in Marketing Agent | link | Opens a new chat with a prompt about the finding | — |
| `pause` | Pause | delivery | Sets entity status PAUSED | — |
| `budget_down` | Lower budget | spend | Cut daily budget by **budget_step_pct** (default **20%**) | Floor `min_daily_budget_minor` (₹100) |
| `budget_up` | Raise budget | spend | Raise by step % | MCP raise policy (**+10%** max per call) and `max_daily_budget_minor` (₹1,000) |
| `set_label` | Attach SKU label | low | Adds campaign label from name pattern | — |
| `add_ig_stream` | Add Instagram Feed | low | Adds `stream` to Instagram positions | — |
| `swap_creative` | Fix headline / display link | low | Updates creative fields | — |

**Kill switch:** `ADS_ACTIONS_ENABLED=false` (default) → preview only; Approve is refused.

Clicking **Approve** on a budget raise counts as the management approval the playbook requires for that change.

---

## 7. Cadence and data windows

| Cadence | When | Typical data |
|---------|------|--------------|
| **5m** | Every tick | Structure (cached ~30 min), today insights, hourly slots for stall / pace |
| **1h** | Once per hour | Refreshed 3d / 7d windows |
| **daily** | After 10:00 local | Fatigue, tracking, learning, scale |
| **Nightly** | After 03:00 local | Baselines, guardrails, tracking median ratio |

Findings are ranked by severity, then rupee impact.

---

## 8. Where to change thresholds

1. **Product UI** — Ops → Monitoring → Live monitoring (versioned save; 409 on conflict; dry‑run against last snapshot).
2. **Defaults file** — `config/live_monitor.yaml` (`rules.{id}.params`, `stats`, `guardrails`, `notify`).
3. **Rule code** — param Field defaults in `rules/performance.py` / `rules/compliance.py` (what the schema form shows as defaults).

Custom rules can reference `metrics.*`, `baseline.*`, and `guardrail.*` fields from the same config page.

---

## 9. Runtime cheat sheet

```bash
# Loop (compose service)
python -m seleric_swarm.services.live_monitor.runner --loop

# One tick, no writes
python -m seleric_swarm.services.live_monitor.runner --once --dry-run --no-llm

# Force baseline refresh
python -m seleric_swarm.services.live_monitor.runner --once --baselines-now

# Replay last N days (performance rules; pacing skipped — no historical budgets)
python -m seleric_swarm.services.live_monitor.runner --backtest 28 --no-llm
```

Compose service: `live-monitor-cron` (own image, `seleric-mcp_default` network).  
Rollback: `docker compose -p seleric_agent rm -sf live-monitor-cron` (migration 020 is additive).

API: `GET /v1/notifications`, `GET|PUT /v1/monitoring/live/config`, `GET /v1/monitoring/live/schema`.

---

## 10. Quality loop

Each card can be labelled **useful / noise / wrong action** (`monitor.labels`). Use that to tune defaults; aim for roughly **≤5 actionable** (critical + warning) findings per day in steady state.
