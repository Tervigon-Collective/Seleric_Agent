# Bots — test guide (production, deployed 2026-11-23; layout 2026-11-26)

Open **https://agent.seleric.com/ui/** → **Bots** in the left sidebar → **Marketing**. The page shows **Reports** and **Chat**; everything that configures the bot is under **Settings** (button next to Run now): General (name, role, instructions, delete), When it runs, Skills, Memory, Tools & email (bot `bot_84a9aa280a13`, brand 20,
Meta account act_1685189008684458). The right sidebar is the bot's **Activity**: one group per run.

The bot only reads. It can propose a change in words; nothing it says changes an ad (approvals come in Phase 3).

State at hand-over:

| Trigger | State | What it does |
|---|---|---|
| Schedule `0 9 * * *` Asia/Kolkata | **on** | standing run every day 09:00 IST, first one 2026-11-24 |
| Monitoring signal `live.meta.*` (warning, critical) | **on** (since 11-23) | a run per new live-Meta alert; ~20-40 alerts/day now, capped at 24 runs/day |
| Watch `meta_campaigns_list` (status, budgets, every 15 min) | **on** (since 11-23) | a run each time anyone pauses/activates a campaign or changes a budget |
| Webhook | none | add one in Triggers (test 6) |

Email recipients: none. Two production runs were made during deploy (both on Activity).

---

## 1. Standing run (the main test)

1. Open Marketing → **Run now** (no message).
2. Wait 1-2 minutes. Activity should show, in order: *Manual*, *Memory* (recalled N), **bot_eda**, maybe 1-3 more
   live tools / run_python, the **answer**, *Saved to memory* (only when the outcome is no_action or cleared).
3. **Reports** tab: the newest run sits on top under a coloured band (red *Needs action*, green *All in order* /
   *Alert cleared*, amber *Couldn't check*) with the bot's one-line reason. The answer below it has a bold one-line
   verdict, a **Check / Now / Normal range / Read** table (one row per EDA part: where today sits, 7-day trend,
   drivers, ads under the movers, funnel, fatigue, concentration and edits), then **Movers** and **What to do**.
   Older runs are one line each; click one to open it. (Run now with your own question, and Chat, answer freely.)

Check the answer against these rules (each one was broken before the EDA and is what to watch for):

| Rule | Wrong looks like |
|---|---|
| Today is compared over the **same hours** (00:00 to the last complete hour), never against a whole earlier day | "spend down 40 % vs yesterday" at 2 pm |
| A figure **inside p10-p90 is normal** — not a finding by itself | "spend is low" while it is inside its range |
| Orders, CPA, ROAS are **our Shopify attribution**, never Meta's purchase counts | a campaign with 30 "purchases" on ₹2,000 |
| Zero orders is a finding only when its chance is small (`p_zero_orders_at_28d_rate`) | "₹1,000 spent, 0 orders → pause" when ~1 order was expected |
| Every number has a source; no "⚠ Not verified against the data" footer | the footer, or run status **partial** |

Cross-check one figure yourself (ClickHouse, brand 20). "Spend by hour H" band, H = the answer's last complete hour:

```sql
SELECT quantileExactInclusive(0.1)(s) p10, quantileExactInclusive(0.5)(s) p50, quantileExactInclusive(0.9)(s) p90
FROM (SELECT report_date, sum(meta_spend) s FROM serve.meta_ads_hourly
      WHERE brand_id = 20 AND report_date BETWEEN today() - 56 AND today() - 1 AND hour_of_day < 14   -- H
        AND report_date NOT IN ('2026-11-15','2026-11-16','2026-11-17','2026-11-18')                 -- hourly holes
      GROUP BY 1)
```
On 2026-11-23 at H=14 this gave ₹15,258.5 / ₹21,131.9 / ₹27,755.1, the same as the bot's EDA.

## 2. Chat with the bot

**Chat** tab → ask something specific, e.g. *"How is DOG SHOES-CTL-21NOV doing against its usual?"*
- Expect an answer to that question only (no EDA-skipped note, no report in Notification Center: chat and
  Run-now never send reports).
- Ask a follow-up (*"and its ads?"*): it remembers the previous turn.

## 3. Schedule and reports (tomorrow 09:00 IST, or force it now)

- The bot header shows **Next report 09:00**. After it runs, the bell → **Notification Center → Bots** tab shows the
  report (scheduled runs always report); clicking it opens the bot.
- To see it today: in Settings → When it runs add a **schedule** for ~10 minutes from now (e.g. `50 15 * * *`, Asia/Kolkata),
  wait, check the Bots tab, then delete that trigger.
- Email: Settings → Tools & email → **Email** → add your address → repeat the forced schedule. Reports go only to the
  listed addresses.

## 4. Monitoring signal (auto run on an alert)

1. Settings → When it runs → switch the monitoring alert trigger on.
2. On the next new live-Meta warning/critical alert, a run starts with *why* = the finding (detector, campaign).
3. Expect it to re-check the alert on live data first: `OUTCOME: cleared` (with the reason) when it is gone,
   otherwise its standing task for that entity. A report reaches Notification Center only on `action_needed`.
4. Volume today is ~20-40 alerts/day → up to 24 runs/day. Switch it off after the test unless you want that.

## 5. Watch (auto run on a team edit)

Settings → When it runs → switch the campaign watch on. The first check only records the baseline (no run). The next time someone pauses a
campaign or changes a budget, a run starts within 15 minutes and *why* names the campaign and field
(e.g. `daily_budget 3000.0 -> 3500.0`). It fires on **every** edit; switch off after the test.

## 6. Webhook (another system starts a run)

1. Settings → When it runs → add **Webhook** → copy the URL and the secret (the secret is shown **once**).
2. Send:
   ```sh
   curl -X POST '<URL from the UI>' -H 'X-Bot-Secret: <secret>' -H 'Content-Type: application/json' \
        -H 'Idempotency-Key: test-1' -d '{"event": "test", "note": "check TH-494 spend"}'
   ```
   → replies queued; a run starts and the body is in its *why*.
3. Same command again (same Idempotency-Key) → duplicate, no second run. Wrong or missing secret → 404.

## 7. Memory, skills, instructions

- Settings → **Memory**, *This bot*: after a no_action run there is a saved line; action_needed verdicts are **not**
  saved (so a wrong alarm is not repeated). **Add note**, e.g. *"DIWALI campaigns are seasonal; ignore that they
  stopped."* → next Run now: Activity *Memory: recalled …* and the answer respects it. **Forget** removes it.
- Settings → **Skills**: add one, e.g. *"End with the 3 ads with the most spend today and their CTR."* → next run
  follows it. Switch it off → it stops.
- Settings → **General**: edit the instructions and save → version goes up; the next run uses the new version.
- **Delete bot** (Settings → General, at the bottom) removes a bot with its runs, activity, triggers and skills after a confirm; refused while a run is in progress. A second bot from the same template is named "Marketing 2".
- The **On / Paused** switch next to Run now: Paused → no scheduled or automatic runs and Run now is greyed out.

---

## Known limits (tell me if any of these bite)

- The statistics are fixed in code and match ClickHouse; the **reading** of them is the model's. In tests it once
  called a campaign "high spend" when it was 70 % below expected. Flag such lines.
- Warehouse hourly Meta data is missing for **2026-11-15/16/17** (daily is complete). The EDA leaves those days out
  and says so; other hourly reports are affected — open question for you (backfill?).
- Funnel has no add-to-cart step and fatigue has no frequency (not in the warehouse with our attribution).
- Notification Center rows say "action needed" twice.
- Proposals are text only until Phase 3 (D-239 approvals).

## Rollback (server side)

`seleric_agent-api` / `seleric_agent-recovery` images tagged `rollback-20261123-bots`; code =
`git apply -R ~/work/bots-deploy-20261123-grounding.patch ~/work/bots-deploy-20261123.patch` in
/opt/seleric/Seleric_Agent; override backup `~/work/docker-compose.override.yml.pre-bots-20261123`; stop
`bots-cron`. Migrations 022/023 are additive (schema `bots`) and can stay. Quick off switch: `BOTS_ENABLED=false` in
`.env` + recreate `bots-cron`.
