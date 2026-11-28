# Bots — agent pool for Seleric_Agent (plan, D-244)

Status: **P1 + P2 built and verified on a local preview 2026-11-21..23** (branch `bots` in ~/work/bots, 19 commits on 420a7a0; not pushed, not deployed). See "P1 as built" and "P2 as built" at the end.
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
| B16 | After P1 | "Continue to Phase 2 (Recommended)": build P2 on the branch, deploy P1+P2 together later. |
| B17 | Can't verify | "New 'unverified' outcome (Recommended)": grey, not an alert; P2 notifications skip it unless it repeats. |
| B18 | Hindsight layout | "Yes, as described (Recommended)": confirms B13 reading. |
| B19 | Email recipients | "Set per bot (Recommended)": per-bot list from email contacts (allowed domains); template list empty. |
| B20 | When to send | "Alerts + daily summary (Recommended)": every cron run, any action_needed, unverified after 2 in a row; chat/manual never. Same rule for Notification Center. |
| B21 | Partial today (2026-11-23) | "Compute in code (Recommended)": day-level live insights reaching today get a `same_hours` block (today vs yesterday over the same complete hours) computed in code from one extra hourly call. |
| B22 | Memory retain (2026-11-23) | "Calm verdicts + notes (Recommended)": only no_action/cleared verdicts and operator-written notes/chat questions are saved; alerts and unverified are not. |
| B23 | Comparison basis (2026-11-23) | Owner, on "yesterday only": "why just yesterday , for that the agent should run rpoper statictsics right"; asked baseline choice, answered "can it perform EDA , rather than just hese two set of insigts". |
| B24 | Who decides the outcome | "Bot decides, range as input" (not a code-enforced p10/p90 gate). |
| B25 | EDA scope | All four: trend + where today sits; drill-down + outliers; funnel steps; fatigue + concentration. |
| B26 | History source | "Warehouse + live today (Recommended)": history from ClickHouse/Cube, today from the live Meta API. |
| B27 | Who runs the EDA | "Code, bot interprets (Recommended)": fixed EDA computed in code each run; bot reads it, may dig further with run_python, decides. |
| B28 | Funnel/outcome source | "Shopify-attributed orders": outcomes (orders, sales, CPA, ROAS) from `ssot_ad_pnl` (our attribution, campaign/adset/ad × day, refreshed through the day); Meta-reported purchases not used. My defaults (owner may override): funnel = impressions → link clicks → landing views (Meta) → attributed orders (no add-to-cart: no campaign-level ATC basis); fatigue = CTR and orders-per-spend trend per ad over 14 days (no frequency: reach not in Cube). |
| B29 | One basis (2026-11-23) | Owner, on "is moinitoring and bots , providing the same functionalinity , information and analysis is scattered": **"Our attribution everywhere"** — live alert rules move from Meta-reported purchases to our Shopify attribution too. |
| B30 | Alerts → bot | **"Every finding, one run each"** — each new warning/critical live finding gets its own bot run. |
| B31 | Where people read | **"Findings are the record (Recommended)"** — Notification Center + Monitoring page show each finding with the bot's analysis and approve button inside it; diagnose.py's LLM cause replaced by the bot; Bots page keeps config, chat and the daily brief. **As built:** `live_monitor.diagnose` is gates-only (`pending_bot`); `bots.finding_handoff` writes cause/bullets/`bot_run_id` onto `monitor.findings.diagnosis` after each signal run; cap skips leave `not investigated (cap)`; Approve still uses `live_monitor.actions` (P3/D-239 merge still parked). |
| B32 | Alert run cap | "Raise cap to 60/day (Recommended)": alert runs get their own 60/day cap; 24/day stays for schedule/watch/webhook; a finding over the cap is marked "not investigated (cap)". |
| B33 | Today's attributed orders | **"Real-time matcher"**: live orders' UTMs / fbclid → Meta campaign and ad ids every 30 s, so today-rules stay instant (owner accepts it is a second copy of the warehouse matching rules; needs a parity check against the mart). |
| B34 | Verdict in code where the EDA has a band (2026-11-25) | Owner "apply fixes" to: one bot, one run per alert, verdict in code. **Narrows B24 ("Bot decides, range as input") for one case:** a signal run on an account-level `live.meta.spend_pacing` finding whose ad_spend is inside the EDA p10-p90 ends `cleared` in code; the bot's own verdict is kept as a note. Every other finding stays with the bot (campaign pacing has no band). Replay: settles 3 of 7 split alerts seen 11-23..25. |
| B35 | Bot CRUD (2026-11-25) | Owner: "remove the duplicate bot and also handle bot CRUD". Delete endpoint + button (a0dcc6f, other session) deployed; the two extra "Marketing" bots deleted (rows backed up in ~/work/bots-deleted-20261125). |
| B36 | Answer layout + page (2026-11-26) | Owner: "format the answer properly, enhance the UI UX and layout for the end user. use design skills"; deploy: "Deploy both". Standing runs (not chat, not Run now with a question) end in a fixed layout: bold one-line verdict, a Check / Now / Normal range / Read table, Movers, What to do, OUTCOME line (`worker.ANSWER_FORMAT`). Email turns the table into a real table. Bots page: latest run first with a coloured verdict band, earlier runs as one-line rows, plain-language verdicts (Needs action / All in order / Alert cleared / Couldn't check). |
| B37 | Redesign scope (2026-11-26) | Owner: "redeisgn the bot moudle"; Impeccable interview answers: scope **"New look for whole app"** (replace office-ui's visual identity, starting from Bots), main user **"Owner / marketing lead"** (checks what bots found, decides budget/campaign changes, rarely edits settings), first view **"What needs action now"** (inbox across bots, decisions first, calm runs collapsed). PRODUCT.md written in office-ui. Look chosen on the Impeccable decision page (B38). Not deployed until owner says. Mock rounds 2026-11-26: owner rejected Airway Bill, Scorecard, Metro Line and Standard ("i reject all excpet , standard metro"); kept the Standard + Metro Line mix (/opt/seleric/tmp/bots-mocks/v2/5-standard-metro.html); asked for "new ideas and layouts , not the previous one enhanced" (v3 layouts in /opt/seleric/tmp/bots-mocks/v3). |

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


---

## P1 as built (2026-11-21)

Branch `bots` (~/work/bots). Commit 420a7a0 is a snapshot of the live checkout's uncommitted WIP (not Bots); the Bots diff is `git diff 420a7a0..bots` (30 files).

| Piece | Where |
|---|---|
| Schema `bots` (bots, instruction_versions, triggers, runs, activity) | `migrations/022_bots.sql` |
| Registry, versioning (409 on stale `expected_version`), dedupe/merge, claim, limits | `services/bots/store.py` |
| Cron (croniter, timezone-aware) + signal (cursor over `monitor.finding_events`) | `services/bots/schedule.py` |
| Worker: brief, run via `run_v3_mission(bot_brief, extra_tools)`, activity from trace, OUTCOME line | `services/bots/worker.py`, compose service `bots-cron`, kill switch `BOTS_ENABLED` |
| API `/v1/bots` (11 routes) | `api/bots.py` |
| Live ad reads for bots only (chat never sees them) | `ADS_READ_TOOLS` in `protocols/mcp/servers/seleric_remote.py`; gateway allowlist; `tool_defs(extra)` |
| Live rows usable by run_python/grounding (paise to rupees for budgets) | `agent/live_rows.py`, hooked in `agent/mcp_toolset.py` |
| Marketing template | `config/bots/marketing.yaml` |
| Bots page + right-sidebar Activity (Activity tab on narrow screens) | `office-ui/src/components/bots/BotsWorkspace.tsx`, `stores/bots.ts`, `api/bots.ts` |

Limits enforced in P1: one run at a time per bot, overlapping cron/signal merge into a queued run, `max_scheduled_runs_per_day` (a skipped run is recorded), stale runs failed after 15 min, global kill switch. Tool calls per run are capped by the agent itself (`AGENT_MAX_TOOL_CALLS`, 20). Pending-proposal limit waits for P3.

### Verification (preview :8095 with its own Postgres; live MCP reads; nothing written anywhere)
- Unit: 26 bot tests (cron across IST midnight, triggers, write tools refused, outcome line, activity rows, live-row adapter on real captured Meta envelopes). SQL paths checked on a throwaway Postgres. UI: 7 vitest. Pre-existing failures at 420a7a0, unrelated: `test_auto_chart` (py), `notifications.test.tsx` (ui).
- Acceptance 1, cron with monitoring off: forced the 09:00 slot due; the run fired with the schedule as reason; next slot went back to 09:00 IST next day.
- Acceptance 2, delayed alert: a 6-hour-old "10:00 IST spend 3x yesterday" finding; the bot re-checked live hourly rows and answered **cleared**: today ₹2,915.83 vs yesterday ₹3,229.97. Truth: live Meta ₹2,937.94 / ₹3,230.01; ClickHouse `serve.meta_ads_hourly` ₹2,904.98 / ₹3,230.07.
- Duplicate signal: two open events for one finding gave one run with one merged entry.
- Chat: TH-501 PENTAGONBED ₹2,600/day and DOG HARNESS ₹4,000/day (raw 260000/400000 paise); spend 2026-11-20 ₹43,350.67. Live Meta ₹43,350.55, ClickHouse `serve.meta_ads_daily` ₹43,350.07. Run `completed`, no grounding footer, 3 tool calls, 16 s.
- Budgets are paise: on brand 20, as paise, campaign-day spend is p50 0.35x / p90 1.06x of daily_budget (60 days, 1,575 campaign-days).
- Write tools refused when a bot is saved (`meta_campaigns_set_status`); API without key returns 401.

### Found while building
- First run: every Meta call was denied by our own gateway (ads tools not registered), and `run_python` could not read live rows, so a pacing check summed zeros and called a real alert "cleared". Both fixed (adapter + precise run_python note).
- When a bot cannot verify, it picks `action_needed`: noisy. Owner question below.

### Deploy steps
Superseded by "Deploy steps (P1 + P2)" below.

## P2 as built (2026-11-21..23)

Commits fbac967 … 1fea9c8 on `bots`. Migration `migrations/023_bots_phase2.sql` (additive: wider CHECKs, `triggers.secret_hash`, `triggers.state`, `bots.notify`, table `bots.skills`).

| Piece | Where |
|---|---|
| Watch trigger: claim due rows, call the read tool outside any transaction, fingerprint only the chosen fields per row id, fire with the change in words ("TH-487… : daily_budget 3000.0 -> 3500.0", "new", "no longer in the result"). First check = baseline, a failed check keeps the last good state, a result with more pages is refused (rows moving between pages would look like changes). Budgets in rupees. Interval ≥ 5 min. | `services/bots/schedule.py` (`claim_watches`, `record_watch`), `worker.check_watches` |
| Webhook trigger: `POST /hooks/bots/{trigger_id}` + `X-Bot-Secret` (shown once, sha256 stored, constant-time compare, any failure = 404), 64 KB body, optional `Idempotency-Key`; body goes into the brief inside `<webhook_data>` marked untrusted | `api/bots.py` `hooks_router`, `api/security.py` exemption |
| Skills (named how-tos added to every run while on) | `bots.skills`, `PUT/DELETE /v1/bots/{id}/skills` |
| Hindsight Cloud memory: recall from bot + brand-shared + platform banks at start, retain the verdict (not figures) at end, both best-effort; Memory tab lists/searches/adds notes/forgets | `services/bots/hindsight.py`, `/v1/bots/{id}/memory`; banks `<HINDSIGHT_BANK_PREFIX>-platform`, `-b<brand>-shared`, `-b<brand>-bot-<id>` |
| Delivery (B20): cron always; any automatic action_needed; unverified only after a 2nd unverified in a row; chat/manual never. Activity row `delivery` → Notification Center "Bots" tab; email via MailService to the bot's recipients (template ships none) | `services/bots/delivery.py`, `api/notifications.py` (`bots`, 48 h) |
| UI: Triggers form for watch/webhook (secret shown once with URL + header, watch last check/error), Skills tab, Memory tab, Email recipients, memory/delivery steps in Activity, Notification Center "Bots" tab (click opens the bot) | `BotsWorkspace.tsx`, `NotificationCenter.tsx` |

### Verification (preview :8095, live Meta reads, Hindsight banks `seleric-preview-*`, no email recipients)
- Unit: 52 bot tests (py) + 9 UI; throwaway-Postgres checks for P1 and P2 SQL paths pass. Full UI suite 276/277; the 1 failure (`notifications.test.tsx` "disables write actions") fails identically at 420a7a0.
- Webhook: no secret 404, wrong secret 404, right secret queued, same Idempotency-Key → duplicate. Run answered with the body as context; memory recall/retain + delivery rows written; it appeared in Notification Center.
- Watch on `meta_campaigns_list` (ACTIVE, limit 500 → 198 rows, one page): baseline without a run; a simulated stored change fired one run whose reason named the campaign and field; the bot confirmed ₹3,000 live. Left on over the weekend it fired 21 times in 43 h on **real** team edits (budget 3000→3500, new campaigns) — correct detection, but noisy: the template keeps its watch **off**.
- Cron fired by itself 2026-11-23 09:00 IST.
- Figures: spend 2026-11-20 ₹43,350.67 in the bot answer = ClickHouse `serve.meta_ads_hourly`.

### Found while building
- **Partial today vs full yesterday — FIXED by B21 (d0be664).** The bot keeps calling "spend down 35 %" by comparing today-so-far with all of yesterday; on 2026-11-21 the same hours were down 11 % (CH: ₹30,918.72 → ₹27,482.40 to 16:00). A note on live rows, the template and the outcome rule did not stop it; the clean run (memory wiped) ended `unverified` instead of comparing same hours. Needs a deterministic fix. Same gap as today-answers 2026-10-28 / UAT 10-29.
- **Memory reinforces wrong verdicts — FIXED by B22 (d0be664).** Retained verdicts ("large drops … need someone to check") were recalled into later runs (23 memories) and repeated. Preview banks wiped 2026-11-23.
- `partial` run status comes from the agent runner's own mission status, not from Bots.
- Template watch without `status` hit >500 campaigns (paging) → refused by the truncation guard; now `status: [ACTIVE]`.

### After B21/B22 (2026-11-23 13:46 IST, memory wiped, all preview triggers off)
- Manual run: the bot quoted the same-hours block: spend ₹15,818 today vs ₹16,229 yesterday (00:00–12:59), purchases 24 vs 23, outcome **no_action** (was a false action_needed on every run before). ClickHouse `serve.meta_ads_hourly` hours 0–12: ₹16,229.43 yesterday (exact), ₹15,772.68 today (CH as of 13:34 IST, live is ahead). The no_action verdict was saved to memory; alerts no longer are.
- Shortcut: same-hours covers the called object's totals (account), not each campaign; campaign movers in the answer are still today-so-far.

### Polish logged, not done
- Notification Center Bots rows say "action needed" twice (subject and reason both carry it).
- Webhook URL in the UI is `window.location.origin` + `/hooks/bots/<id>`; check in prod that this path reaches the API (deploy step 6).

### Deploy steps (P1 + P2, only when the owner says)
1. Rollback tags for api + recovery images.
2. Apply `migrations/022_bots.sql` then `migrations/023_bots_phase2.sql` to seleric_swarm.
3. Apply the Bots diff (`git diff 420a7a0..bots`) to the live checkout (it carries peer WIP).
4. `docker-compose.override.yml`: give `bots-cron` `env_file: .env` (it `extends: recovery` from the base file, so the override's env_file for recovery does not reach it). `.env` needs `HINDSIGHT_API_KEY` (present) and, if a non-default prefix is wanted, `HINDSIGHT_BANK_PREFIX` (default `seleric`).
5. Rebuild api + recovery, start `bots-cron`.
6. Smoke: page loads, create Marketing bot, Run now, Activity shows memory + answer rows; curl the webhook URL exactly as the UI shows it: 404 without secret (proves `/hooks/bots/` reaches the API).

## EDA for bots (B23–B28) — BUILT a1c379c on branch bots, verified on preview (2026-11-23); not deployed

**Shape.** The runner has no way to pre-load evidence (only `bot_brief` text + `extra_tools`), so the EDA is a bot-only read tool `bot_eda` (registered beside the live ad reads; its tables go through `write_query_evidence` like `same_hours`). The run question requires calling it first; the worker records a run that skipped it. Statistics reuse `services/live_monitor` (`core/stats`, `baselines`, `adapters/meta.normalise_insight`) — no new statistics code, so the bot and the `live.meta.*` alerts cannot disagree. Sources fetched concurrently with timeouts; a failed source gives a labelled partial EDA, not none.

| Table (query-result shaped, top-K + "and N more") | Today | History | Source of history |
|---|---|---|---|
| Where today sits: account spend/impressions/clicks/LPV by now vs expected-by-now band (p10/p50/p90 × hour-of-day share), percentile; 28-day trend (last 7 vs prior 7); weekday effect over 8 weeks | live insights (account, hourly) | daily 56 days | Cube `paid_media` (warehouse) + `monitor.live_baselines` hour curve (fresh nightly, last 2026-11-22 21:30 UTC) |
| Drivers + outliers: campaigns' share of the account's gap to expected; each scored against its own band; ad sets/ads only under the top 5 | live campaign level | per-entity bands | `monitor.live_baselines` (596 campaigns, 1,101 ad sets, 1,109 ads) |
| Funnel: CTR → LPV/click → attributed orders/LPV, CPA/ROAS vs band | live delivery + warehouse orders | 28 days | B28: `ssot_ad_pnl` (Shopify-attributed) + `paid_media` |
| Fatigue: ads running ≥7 days with CTR and orders-per-spend falling over 14 days | — | 14 days | warehouse (`paid_media` ad-day + `ssot_ad_pnl`) |
| Concentration + edits: top-5 spend share; new/edited campaigns since yesterday | live | `paid_media_changes` | warehouse |

Yesterday same-hours (B21) becomes one context line. Rule line (B24, bot decides): a value inside its normal-by-now range is not a finding on its own.

Verification plan: unit tests on captured envelopes; one preview run (worker started briefly, all triggers off); ClickHouse check of 28-day daily spend vs `serve.meta_ads_daily`, and one campaign's percentile recomputed by hand from `live_baselines` × hour curve.

### As built (a1c379c)
- `services/bots/eda.py` + bot-only tool `bot_eda` (args brand_id, account_id), computed inside the agent's tool layer from 9 MCP calls run concurrently (3-4 s live). Evidence gets every table; the model reads a compact view (columns once, rows as lists, ~7.5k chars) because the full pack (18k) went over the 14k tool-result cap and the generic compaction kept only 3 tables.
- Tables: where_today_sits, trend, funnel, drivers (expected_by_now, spend_vs_expected_pct, expected_orders_by_now, p_zero_orders_at_28d_rate via live_monitor `prob_zero`, spend shares → concentration), ads_under_top_movers, fatigue, changes.
- Landing views: Meta-reported (`platform_reported_landing_page_views`) on both sides; `landing_page_views` is our own sessions (D-13r) and would mix bases with live today.
- Hourly holes: a history day is used for "same hours" only when its hourly spend is within 2 % of its daily spend. **Found:** warehouse Meta hourly (serve.meta_ads_hourly) holds 0-17 % of the day for 2026-11-15/16/17 (Nov 18 97.7 %); daily is complete. Not fixed (warehouse data, owner's call).
- Template: step 1 = call bot_eda; orders/sales only from it (Meta purchase actions double count: a run read 30 purchases where we attribute 3); report each EDA part in one line; zero orders is a finding only when p_zero is small. Worker writes a `note/skipped` activity row when a standing run did not complete bot_eda. Watch triggers cannot use bot_eda (not an MCP tool).

### Verification (2026-11-23, preview, worker started per run and stopped after)
- ClickHouse: spend-by-14:00 band over 52 usable days p10/p50/p90 ₹15,258.5 / ₹21,131.9 / ₹27,755.1 and today's 21.2th percentile = EDA exactly; 28-day CPA p50 ₹1,520.48 = EDA; 7-day attributed spend ₹287,580 = serve.meta_ads_daily; today's attributed orders 13 = EDA.
- 4 preview runs: (1) before fixes, Meta purchases + "zero orders on ₹1k" → action_needed (wrong basis); (2) no_action but skipped drivers/fatigue; (3) every part reported but fatigue/concentration called "normal" unseen → found the 14k cap cut; (4) all parts read correctly (concentration 54 % vs 27 %, TH-494 ads CTR −27..−29 %), action_needed on fatigue + concentration; still misread one campaign as "high spend" (it was 70 % below expected) → `spend_vs_expected_pct` added (d265758). Run 5 on d265758: action_needed on fatigue (TH-494 ad CTR −28 %, high spend) + concentration (54 % vs 28 %), all parts read correctly, CRABDASH figures right (₹1,061 vs ₹3,593 expected) but still worded "high spend … zero orders" without citing p_zero (0.30): interpretation is model-dependent (agent role gpt-5-mini); statistics verified.
- Tests: bots suite 62 passed; full unit 1227 passed, 1 pre-existing failure (test_auto_chart).
- **P3 cut-over note:** the D-238 → D-239 rename (`meta_*` → `ads_meta_*`) is no longer template-only: `services/bots/eda.py` (`run`: meta_insights_live) and `agent/live_rows.same_hours_args` (B21) name the tool in code. Without the edit, bot_eda lists the live sources as unavailable and same_hours silently stops.

## DEPLOYED to production 2026-11-23 (owner: "deploy and give me a real test guide")
- Live checkout /opt/seleric/Seleric_Agent (branch `monitoring`, uncommitted as before): applied `git diff 420a7a0..bots` (minus plans/BOTS.md; live tree = base snapshot exactly, checked file by file) + grounding fix f36547a. Patches kept: ~/work/bots-deploy-20261123.patch, ~/work/bots-deploy-20261123-grounding.patch.
- Migrations 022 + 023 applied to seleric_swarm (schema `bots`). Override: `bots-cron` env_file .env + networks (backup ~/work/docker-compose.override.yml.pre-bots-20261123). Rebuilt api, recovery, bots-cron; rollback images `seleric_agent-{api,recovery}:rollback-20261123-bots`.
- Prod bot `bot_84a9aa280a13` (Marketing, from template): schedule 09:00 IST **on**, signal **off** (20-40 live.meta warning/critical alerts/day would hit the 24-run cap), watch off, no email.
- Smoke: UI + webhook path 404 via https://agent.seleric.com; run 1 completed-as-**partial**: grounding read "1.44%–1.81%" as −1.81 % (sign mismatch) and the revision note leaked into the answer → fixed in `grounding.answer_numbers` (`_range_end`: a dash after `%` is a range), f36547a; run 2 **completed**, action_needed (fatigue TH-494 CTR −28 %, concentration 54 % vs 27 %).
- Test guide: plans/BOTS_TEST_GUIDE.md.

## Deployed 2026-11-25 (dbd450e + a0dcc6f)
- Fixes: signal cursor skips Monitoring's diagnosis events (each reopened alert had started 2 runs per bot); B34 code verdict; bot delete + numbered names (a0dcc6f).
- Extra bots bot_1b6bc8735c3a + bot_e3e3a87106a6 paused, backed up, deleted via DELETE /v1/bots. One bot left: bot_84a9aa280a13 (cron + signal + watch on, as the owner left them).
- api, recovery, bots-cron rebuilt; rollback tags `seleric_agent-{api,recovery,bots-cron}:rollback-20261125-crud`. Live checkout source = worktree files (copied per file).
- Open: shared 24-run daily cap (dropped a critical alert 11-24 17:10 UTC); fact-check makes ~1/3 runs partial (bot computes figures in prose); campaign-level pacing still model-judged.

## Deployed 2026-11-26 (d5e320a + bd52e20)
- B36 answer layout + Bots page redesign. api, recovery, bots-cron rebuilt; rollback tags `seleric_agent-{api,recovery,bots-cron}:rollback-20261126-layout`.
- c497284 (owner: "Hide the seeting in a setting panel of the bot, rather than showing every tab below the bot name"): Settings side panel; page = Reports + Chat; api rebuilt, rollback `seleric_agent-api:rollback-20261126-settings`.
- 5b3b585 impeccable polish (owner "use impecable"): DESIGN.md radius/heading rules, shared Checkbox/Button, focus + chevron on earlier reports, full-width answer tables, verdict split when the fact-check note follows OUTCOME; api rebuilt, rollback `seleric_agent-api:rollback-20261126-polish`.
- f5269be responsive (owner "also responsive design"): Activity side panel from 1280px wide (tab below), 44px touch targets on touch screens, settings tabs as a grid on phones; api rebuilt, rollback `seleric_agent-api:rollback-20261126-responsive`.
