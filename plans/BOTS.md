# Bots — agent pool for Seleric_Agent (plan, D-244)

Status: interview done 2026-11-21 (D-244); plan approved pending; nothing built.
Source of truth: this file. Decisions logged as D-244 in /opt/seleric/ssot-docs/docs/DECISIONS.md.

---

## A. Requirement as given (owner, 2026-11-21)

Making an agent pool, called Bots, appear on the sidebar, where we can configure different agents like
marketing, e-commerce, website funnel, etc., for monitoring or other tasks, which then uses the monitoring
console or can act independently, allowing cron schedule, all the features that the Rakazo and Grok bot
provide, also keeping a track of all the actions it did in the right side bar. So, it may or may not take
signals from the monitoring module or act independently. Has specialised tools for executing actions
available through the Seleric MCP. Policy management should be carried out in the Seleric MCP server;
whenever a call is made through any agent, it needs to provide the scope it wants access for. For the
marketing agent, its scope would be ads management, actions, etc., from the live API through the MCP
server, and not through the Cube. Use Hindsight for agent instruction and other knowledge-base store for
the agent, in a very structured way for the whole Seleric_Agent platform, starting with Seleric_Agent as
folder. Rapid Application Development model. Best practices, modularity.

**Make Bots a top-level item in the left sidebar.** Each bot has its own instructions, knowledge, tools,
triggers, and run history. A monitoring signal, a cron schedule, or a manual request can start the same
bot. The trigger explains *why the bot ran*. Seleric MCP decides *what the bot can do* on every call.

### Reviewed requirements

| Area | Requirement |
|---|---|
| Bots sidebar | List bots with role, enabled state, last run, next scheduled run. Opening a bot shows config + recent runs. Start with Marketing, E-commerce, Website Funnel templates. |
| Triggers | Subscribe to selected Monitoring Console signals, run on a timezone-aware cron, or on demand; any combination. A scheduled bot works with zero monitoring subscriptions. |
| Tools and scope | Role-specific tool list. Every agent call to Seleric MCP declares requested scope + target resource. MCP authenticates the caller, resolves bot + workspace, checks the current grant and tool requirements, records the decision before execution. Missing scope or denial ⇒ zero provider calls. |
| Live marketing actions | Ad inspection/changes via Seleric MCP to live Meta / Google Ads API. A signal may come from Cube analytics. Before changing a campaign the bot re-verifies the condition on live data. |
| Right sidebar | Persistent activity timeline per bot, grouped by run: trigger, reason, tool, target, requested scope, policy decision, outcome; for changes previous value, requested value, confirmed result. Distinguish attempted / denied / failed / completed. |
| Reliable execution | Define cron-overlaps-signal, duplicate signal, provider timeout after write. Durable action ID; reconcile uncertain outcomes against live state before retrying. |

### Proposed scopes (Marketing)

| Operation | Scope | Example MCP tool |
|---|---|---|
| Read live Meta performance | `ads.insights.read` | `meta_insights_live` |
| Inspect a Google campaign | `ads.campaign.read` | `google_campaigns_get` |
| Pause a Meta campaign | `ads.campaign.status.write` | `meta_campaigns_set_status` |
| Change a Meta campaign budget | `ads.campaign.budget.write` | `meta_campaigns_update_budget` |

Grants bound to workspace + bot + provider + ad account. MCP validates requested values (e.g. `PAUSED`
only, budget-increase cap). Policy rechecked at execution time so revocation affects queued runs.

### Hindsight layout

```text
Seleric_Agent/
  platform/{instructions,tool_reference}/
  workspaces/<workspace_id>/
    shared_knowledge/
    bots/<bot_id>/{instructions,playbooks,knowledge,run_learnings}/
```

Version authored instructions; record active version on each run. Learned observations kept distinct from
operator-authored instructions. Memory never grants permission. Durable action log (not memory) feeds the
right sidebar.

### First acceptance test

Marketing bot, cron 09:00 Asia/Kolkata, optional spend-alert subscription, grant = live-read + pause on one
ad account:
1. Monitoring disconnected → cron starts the bot; sidebar shows schedule as trigger.
2. Delayed alert → bot checks live state; if cleared, records why it skipped.
3. Cron + alert want the same pause → one effective change; both runs traceable.
4. Budget change or other account → MCP denies before provider; denial in sidebar.

Open: exact Rakazo / Grok-bot feature list; E-commerce + Website Funnel action inventories.

---

## B. Validation against current code (2026-11-21)

| Requirement | What exists today | Verdict |
|---|---|---|
| Bots in left sidebar | office-ui nav is hard-coded `NavItem`s in `office-ui/src/components/AppFrame.tsx`; workspaces union in `src/stores/shell.ts`; routes in `src/routing.ts`; lazy pages in `src/App.tsx`. Ops/Monitoring pages are the pattern (`components/ops/OpsWorkspace.tsx`). | Easy; copy Ops pattern. |
| Right-sidebar timeline | `ResponsivePanel side="right"`, `FindingTimeline`/`ActivityList` in `components/notifications/NotificationCenter.tsx`, `MissionTimeline.tsx`. | Reuse components; data source is new. |
| Bot run engine | One agent: `agent/runner.py::run_v3_mission` → `agent/loop.py::run_turn` (PydanticAI). Durable queue `runs`/`run_attempts` + `recovery` worker. Prompt modules in `agent/prompt_library/`. No personas. | Reuse; a bot = prompt module + tool allowlist + MCP key passed into the same runner. |
| Cron | No scheduler lib; only compose `while/sleep` loops. Rakazo has a proper one (routine reconciler + coalescing + backoff) but in TypeScript. | New (small): one scheduler loop + `croniter`-style next-run. |
| Monitoring signals | `monitor.findings` / `finding_events` (monitoring-cron hourly, live-monitor-cron 5 min) in Postgres. No pub/sub on findings. | Bot trigger = cursor poll over `finding_events` (or add NOTIFY). |
| Live ads actions from findings | `services/live_monitor/actions.py` already does preview→approve against D-238 tools, logged in `monitor.live_action_requests`. | Overlaps Marketing bot → merge, don't duplicate. |
| MCP scope per call | Live MCP checks scopes per call (`gateway/auth.py`, `ads/envelope.py`), but **every caller is the same identity** (`actor="service-token"`); OAuth strips writes. Audit tables `audit_log`/`ads_operations` have no caller or scope. | Gap. **D-239 (branch `ads-v2`, ~/work/ads_v2, not deployed)** already builds per-agent API keys, policy brand × role, per-action approval auto/token/human, exactly-once execute, audit with caller, kill switch, freeze, dry-run, /ads/admin. |
| Your tool names | `meta_insights_live`, `google_campaigns_get`, `meta_campaigns_set_status`, `meta_campaigns_update_budget` exist **live** (D-238). D-239 deletes them and renames to `ads_meta_*` / `ads_google_*`. | Pick the base: D-239. |
| Live API, not Cube | All `meta_*` except `meta_insights_query` hit Graph API live; all `google_*` hit Google Ads live. | OK. But prod grants no `google_ads:*` scope → every Google call denied today. |
| Hindsight | Not installed anywhere on the host; no config. Rakazo supports it (banks `rakazo-<sha(space)>[-<sha(bot)>]`, retain/recall/reflect). Seleric_Agent has its own `memories` pgvector table; `knowledge_corpus/` empty. | New service to deploy. |
| E-commerce bot actions | Seleric MCP has **no Shopify tools** (Shopify only as Cube metrics). A separate `multiagent-mcp-shopify` exists in seleric_systems. | Needs new MCP tools or read-only bot. |
| Reliable execution | Meta client retries POSTs after timeouts (duplicate-create risk); optional idempotency key; no reconciliation job. D-239 adds atomic `approved→executing`. | Partly covered by D-239; reconcile-before-retry still new. |

## C. Gaps / things you may be overlooking

1. **Build vs reuse Rakazo.** Rakazo already does named bots, routines (cron/watch/webhook/email/Slack), approvals (Ask/Auto/Never + spend limit), audit log, per-bot tools/models, Hindsight memory; D-239 even ships a Rakazo plugin. But it is upstream-tracked (hourly auto-update), can't be forked, and its UI is not the Seleric sidebar. Decision needed.
2. **Autonomy vs earlier decisions.** D-239 seed = *every write needs human approval*; unified-anomaly interview (2026-11-02) = "human decides, no auto-act". "Act independently" contradicts both unless you change it per bot/action.
3. **D-239 is a hard prerequisite** (per-caller identity). Its cut-over is blocked by a teammate's live-only edits in Agent_Core, and a push to Agent_Core `main` = deploy (Jenkins rsync --delete).
4. **Scope granularity.** D-239 has coarse scopes (`ads:read/write/admin`) + policy rules per brand × role. Your finer scopes (`ads.campaign.status.write`…) and binding to bot + account need policy keyed by *caller*, not only role.
5. **Hindsight is memory, not a document store** (verified 2026-11-21 against docs.hindsight.vectorize.io + GitHub README). `retain` = LLM-extracted facts (world / experience / observations), not verbatim text. Banks are **flat ids** (no folders; the `Seleric_Agent/...` tree must be encoded in bank ids + metadata). Operator-authored inputs that exist: bank **Background** text, **disposition** traits, **mental models** (standing queries refreshed by reflect) — no always-on directives. Self-host: `ghcr.io/vectorize-io/hindsight` (API 8888, UI 9999), Postgres 14/15+ with pgvector, **LLM key required** (retain/reflect), MIT. ⇒ Authored instructions/playbooks versioned in git (prompt modules) + Postgres; Hindsight holds learned memory + background.
6. **Who may configure bots.** office-ui serves the shared API key publicly via `/ui/config.js` (open since 11-17). A Bots page that can grant write power must sit behind real per-user auth/roles.
7. **Where bot output goes.** Timeline only, or also email / Slack / Notification Center? Nothing pushes today except email.
8. **Run cost + safety limits per bot.** LLM token budget, max runs/day, max tool calls/run, per-bot pause/kill switch, one-run-at-a-time per bot.
9. **Brand/workspace mapping.** "Workspace" isn't a concept in MCP; brand is. Is workspace = brand (20 now)?
10. **Signals carry Cube numbers that lag 40-60 min.** Live re-verify before writes (you have this) — also define the threshold the bot re-checks.
11. **Website Funnel bot has no actuator.** Tracker/theme changes are not API-safe (live-site pollution incident 10-27). Likely read-only + diagnose + notify.
12. **Testing writes.** Live write tests need owner OK + team warning (11-18 test campaign deleted by a teammate). Need a dry-run/sandbox mode per bot.

## D. Decisions (owner interview 2026-11-21; to be logged as next D-number in ssot-docs/docs/DECISIONS.md)

| # | Question | Owner's pick |
|---|---|---|
| B1 | Engine | Native in Seleric_Agent (Bots page in office-ui, existing runner + durable queue, Rakazo routine design copied) |
| B2 | Autonomy for ad writes | **Every write needs human** (keeps D-239 seed + 11-02 "no auto-act"). Bots read, diagnose, propose; a person approves. |
| B3 | MCP base | Build on D-239 (`ads_meta_*`/`ads_google_*`, per-agent keys, policy, audit). Read-only bots can start before D-239 cut-over. |
| B4 | V1 bots | Marketing first (read + propose pause). E-commerce / Funnel later as read-only once action lists agreed. |
| B5 | Hindsight | **Skip Hindsight.** Use the agent's existing pgvector `memories` table. (Overrides the requirement's "use Hindsight".) Instructions/playbooks versioned in git + Postgres. |
| B6 | Permission grain | Fine scopes per bot key: each bot = own D-239 key with per-operation scopes + allowed ad accounts; MCP maps every tool → one scope, denies before any provider call; D-239 policy (caps, human approval) on top. |
| B7 | Delivery | Right sidebar + Bots page (approve/reject inline), Email (D-239 signed links), Notification Center. Not Slack/Telegram. |
| B8 | V1 Rakazo/Grok features | Chat with a bot; Watch + webhook triggers; Skills + editable memory. (Plus cron, manual, monitoring-signal.) Not: bots managing bots / group chats. |
| B9 | Access | First pick "Everyone signed in"; after being told the UI key is public: **"authentication will be handled later, now open for all"**. ⇒ Phase 1-2 open UI. Open item: writes (Phase 3) must not go live while approve is open to anyone — approval stays in D-239 (named approvers) until auth exists. |
| B10 | Limits | Safe defaults: one run at a time per bot (overlapping triggers merge), ≤24 scheduled runs/day, ≤40 tool calls/run, ≤5 pending proposals/bot, per-bot pause + global bots kill switch; editable per bot. |
| B11 | D-239 order | Read-only bots first; D-239 cut-over is a separate step after owner + teammate settle the live-only Agent_Core edits; then proposals switch on. |
| B12 | Brands | Brand 20 only in V1; workspace = brand; `brand_id` kept on every row. |
| B13 | Hindsight (reverses B5) | Owner, asked whether the knowledge layout is still wanted: **"https://api.hindsight.vectorize.io , HINDSIGHT_API_KEY= in env"** ⇒ use **Hindsight Cloud** (key in Seleric_Agent/.env, verified 200 on banks list 2026-11-21). Bot memory/knowledge leaves the server to Vectorize (owner accepts by choosing Cloud). *My reading, owner to confirm:* the knowledge tree lives in Hindsight banks; authored instructions stay versioned verbatim in git/Postgres because retain only keeps extracted facts. |
| B14 | Approve | Owner: "Use D-239 page for now". Timeline shows the pending change; Approve opens D-239's login + named-approver page until auth exists. |
| B15 | Go | Owner: "Start Phase 1". |

Consequence of B2: a bot run ends with a *pending action* in MCP; approval executes it later. Acceptance test 3 = two runs propose the same pause → one pending action (dedupe on entity+op), one execution, both runs linked.

## E. Plan (RAD: each phase ships a working, demoable slice)

### Shape
A bot = **row in Postgres** (instructions + version, tool allow-list, scopes, triggers, limits) that runs on the **existing agent runner** (`agent/runner.py::run_v3_mission` via `api/async_missions.py::enqueue_durable_mission`). The trigger says why it ran; Seleric MCP (D-239, Phase 3) says what it may do. Nothing new where something exists.

### Data (migration `migrations/022_bots.sql`, db seleric_swarm, schema `bots`)
- `bots.bots` — id, brand_id, name, template, enabled, instructions_md, instructions_version, tools jsonb (allow-list), scopes jsonb, ad_accounts jsonb, model_role, limits jsonb, version, updated_by/at.
- `bots.instruction_versions` — bot_id, version, body, author, created_at (each run records the version it used).
- `bots.triggers` — bot_id, kind (`manual|cron|signal|watch|webhook`), config jsonb (cron+tz / detector+metric filter / watch tool+args+interval / webhook secret hash), enabled, next_run_at, cursor, last_fired_at.
- `bots.runs` — bot_id, trigger_id, trigger_kind, reason, dedupe_key, run_id → `runs`, status, merged_triggers jsonb, tool_calls, tokens.
- `bots.activity` — run, ts, kind (`tool|proposal|denied|failed|completed|skipped|note`), tool, target, scope, decision, before, requested, result, mcp_action_id. **Source of the right sidebar.**
- `bots.skills` — bot_id, name, body_md, author (bot|human).

### Knowledge + memory (Hindsight Cloud, B13)
Banks are flat ids, so the tree is encoded in the id (brand 20 example):
- `seleric-platform` — platform instructions digest + tool reference (Background = platform rules).
- `seleric-b20-shared` — brand shared knowledge (campaign naming, known periods, past decisions).
- `seleric-b20-bot-<bot_id>` — bot knowledge, playbooks, run learnings (retain at run end with metadata `{kind: learning|knowledge|playbook, run_id, instructions_version}`).
Run start: recall from bot bank + shared bank (+ platform), inject as context labelled *learned, not instructions*. Authored instructions/skills stay verbatim in `bots.*` (git templates seed them). Memory never grants permission. Client: small `services/bots/hindsight.py` (retain / recall / reflect / list / delete — the calls Rakazo's `hindsight-client.ts` uses), env `HINDSIGHT_API_URL`, `HINDSIGHT_API_KEY`. Editable memory tab = list/delete documents in the bot bank.

### Backend (Seleric_Agent, Python)
- `services/bots/store.py` — CRUD + versioning (reuse `expected_version` → 409 pattern from `services/domain_health/monitor_config.py`).
- `services/bots/scheduler.py` — loop every 30 s: due triggers `FOR UPDATE SKIP LOCKED`; cron via **croniter** (new dep, tz-aware); signal = cursor over `monitor.finding_events` (no NOTIFY needed); watch = call tool, hash result, fire on change; coalesce if bot already running (append to `merged_triggers`); enforce B10 limits. Runs as compose service `bots-cron` on the api image (same pattern as `monitoring-cron`).
- `services/bots/run.py` — builds the run: platform behaviour + bot instructions@version + skills → prompt modules; tool set = `agent/mcp_toolset.py` `ALLOW` (catalogue/metrics only today) **plus the bot's template tools** (read-only ads tools) and `agent/local_tools.py::build_local_tools`, filtered to the bot's allow-list. Ads tool names live only in the template YAML, so the D-238 → D-239 rename (`meta_*` → `ads_meta_*`) at P3 is a config edit; tool-call hook writes `bots.activity`; max-tool-call guard.
- `api/bots.py` — `/v1/bots` CRUD, `/runs`, `/activity`, `POST /v1/bots/{id}/run`, `POST /v1/bots/{id}/hooks/{trigger}` (secret), chat = existing conversation API with `bot_id`.
- Templates: `config/bots/marketing.yaml` (instructions, read tools: catalogue/metrics, `meta_insights_live`, campaign/adset list/get, diagnose, business_snapshot; triggers: cron `0 9 * * *` Asia/Kolkata + signal `live.meta.*`).

### UI (office-ui, copy the Ops pattern)
`Workspace` += `"bots"` (`stores/shell.ts`), route `?bots=<id>` (`routing.ts`), lazy branch in `App.tsx`, `NavItem` "Bots" in `AppFrame.tsx`. New `components/bots/`: `BotsWorkspace` (list: role, enabled, last/next run), `BotDetail` tabs (Instructions · Triggers · Tools & scopes · Skills · Memory · Chat), `BotActivityPanel` in `ResponsivePanel side="right"` reusing `FindingTimeline`/`ActivityList` (grouped by run; attempted/denied/failed/completed). API client `api/bots.ts` + zustand store with 30 s polling (copy `stores/notifications.ts`).

### Phases
- **P0 (now):** save this doc as `Seleric_Agent/plans/BOTS.md`; log B1-B12 as **D-244** in `ssot-docs/docs/DECISIONS.md`.
- **P1 — read-only bots (demo):** migration, store, API, Marketing template, manual + cron + signal (`finding_events` cursor) triggers, runner hook, activity log, Bots page + right sidebar, chat. Built in worktree `~/work/bots` off `monitoring`; preview container like `ops-preview` (:8091). Acceptance 1 (cron with monitoring off) + 2 (delayed alert → live re-check → recorded skip).
- **P2 — triggers & output:** watch, webhook; coalescing + dedupe; B10 limits + kill switch; email run summary + Notification Center entry; skills; Hindsight banks (recall at start, retain learnings at end) + memory tab.
- **P3 — proposals (after D-239 cut-over, B11):** Agent_Core: per-bot D-239 key with fine scopes (tool → scope map, ad accounts), deny before provider call, caller+scope in audit. Agent: bot MCP transport uses its own key; proposals become D-239 pending actions (dedupe entity+op); sidebar shows pending/approved/executed by polling MCP; approve via D-239 page (*my default, owner to confirm*: B7 asked for approve inside the timeline, but with B9's open UI the approve click must carry a named approver, so it opens D-239's login-protected page until auth exists). Merge `services/live_monitor/actions.py` into this path. Reconcile-before-retry for uncertain writes. Acceptance 3 + 4.
- **P4:** E-commerce + Funnel templates, read-only, after their action inventories.

### Not doing (named)
Self-hosted Hindsight (B13 = Cloud), Slack/Telegram (B7), bots managing bots / group chat (B8), login system (B9), auto-execute writes (B2), multi-brand (B12).

### Verification
- pytest for store/scheduler/run (cron next-run across IST midnight, coalescing, dedupe, limits); vitest for routing/store/panel.
- P1 live check: Marketing bot on the preview, manual run + forced cron tick on brand 20; re-run the same question in chat and cross-check figures against ClickHouse (feedback-verify-live).
- Deploy only when the owner names it (api + recovery + new bots-cron rebuild, rollback tag, migration 022 first).
