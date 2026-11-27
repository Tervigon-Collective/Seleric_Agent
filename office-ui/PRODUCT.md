# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: the brand owner / marketing lead of an Indian D2C brand (brand 20 today). They open the app to see what changed in sales, ads and the website, decide budget and campaign changes, and ask follow-up questions. They rarely edit bot settings; when they do, it is occasional configuration, not daily work.

Secondary: the in-house data/marketing team, who configure bots, monitoring rules and check run detail when an answer looks wrong.

## Product Purpose

Seleric is an analytics agent for a Shopify + Amazon + Meta/Google Ads brand. Its web app (office-ui) has four areas: Chat (ask the agent a question), Monitoring (live and hourly checks that raise findings), Bots (named agents that run on a schedule, on a monitoring alert, on a watch or webhook, or on demand, and report what they found), and Office (a spatial view of agent runs).

Success: the owner sees in seconds what needs a decision now, trusts the numbers enough to act, and can approve or dismiss a proposed change without reading a run log.

## Positioning

Numbers come from the brand's own single-source-of-truth marts (Shopify orders with COGS, returns/RTO, ad spend from the live Meta/Google APIs), not platform-reported attribution. Bots compute statistics in code (normal ranges, same-hour comparisons) and re-check live data before proposing a change; the answer says when it could not verify a figure.

## Operating Context

- Checked on a laptop during the working day and on a phone between meetings; India timezone (Asia/Kolkata), INR amounts.
- A bot answer has a fixed shape: one-line verdict, a checks table (now vs normal range), up to five movers, and "what to do" proposals (entity: current → proposed, reason), plus an outcome (needs action / couldn't check / alert cleared / all in order).
- Bot runs also go out by email; Monitoring has its own findings list and badge in the sidebar.

## Capabilities and Constraints

- Bots are read-only today: they propose, a person approves. Ad writes go through Seleric MCP (D-238 live tools; per-bot keys and approval via D-239 later). Every write needs a human (D-244 B2).
- Brand 20 only (workspace = brand). No per-user login yet; the UI is open (auth later).
- Limits per bot: one run at a time, ≤24 scheduled runs/day, ≤40 tool calls/run.
- Stack: React + Vite + Tailwind 4, shadcn `ui/*` components, light and dark themes.
- Open: in-app approve/reject of proposals is not built (approval opens the D-239 page once it exists).

## Brand Commitments

Name: Seleric. No logo or brand palette has been made binding. Voice: plain business English, the owner's terms (net sales, ad spend, ROAS, CPO, RTO), no jargon about tools or pipelines.

## Evidence on Hand

Real bot runs, activity and findings in the live Postgres `bots` / `monitor` schemas (brand 20); verified figures cross-checked against ClickHouse. No customer testimonials or external benchmarks exist; do not invent them.

## Product Principles

1. Decision first: lead with what needs action now; calm runs stay available but out of the way.
2. Show the number and its normal range, never a bare figure.
3. Say what the system could not check rather than smoothing it over.
4. A proposal names the exact change (entity, current, proposed, reason); approval is a person's act.
5. Settings are secondary; reading and deciding is the main job.

## Accessibility & Inclusion

WCAG 2.1 AA contrast, keyboard focus on every control, 44px touch targets on touch screens, works from 360px wide.
