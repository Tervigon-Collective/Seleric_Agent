---
version: 1
slug: "src-components-bots-botsworkspace-tsx"
primary_target: "src/components/bots/BotsWorkspace.tsx"
related_targets: ["src/components/AppFrame.tsx","src/index.css"]
---

# Bots page

Mode: Operate. Visitor: the brand's marketer or owner, checking in a few times a day, often on a phone. Job: see what the bots flagged, decide on each proposed change, and get back to work. Action: open a decision, read the proof, then Open report / Ask about this / Dismiss. Proof: code-computed normal ranges (bot_eda p10/p50/p90 for the same hours over 56 days), the bot's movers, and an honest unverified note. Constraints: the bot only proposes; nothing writes to the ad account from here (Ads approvals is a later phase). Settings, Chat, Activity, on/off, create and delete keep a home on the page. Data comes from `runs.report` (D-253); without it the page falls back to the Markdown answer. One screen per bot: the list, decision and rail show only the open bot's runs; the bot picker switches screens.

## Direction contract

THESIS: A decision inbox, not a bot console. One row per proposed change, newest first; the bot's prose becomes a verdict, a struck old value and a big new value. Refuses the default "bot list → run log → wall of Markdown".

OWN-WORLD: The app's own look (DESIGN.md): Inter, fe-dashboard light (#f4f4f2, purple #7f33cc) / Violet Bloom dark (#1a1b1e, #7a46e9). Red only for what needs a person, as a small dot. The Metro Line theme (D-253: Overpass, navy/scarlet, ring stops, line timeline) was removed by the owner on 2026-11-27. Range bars are a green band with a median tick and a marker for today.

STORY: The marketer sees how many decisions wait, opens the top one, sees old → new and why, checks that the numbers sit inside or outside their normal range, then acts or dismisses.

FIRST VIEWPORT: Icon rail; 340px list with Run now, bot bar, "Needs you · N / All runs · M", groups. Centre: verdict pill + meta line, 24px entity heading, 40px proposed value, reason, three actions, two-column checks. Right rail: "This campaign today" as a plain divided list with status dots. Phone: list first, detail on tap with Back.

FORM: seed: none (pinned) — layout from "'/opt/seleric/tmp/bots-mocks/v3/2-panes-desktop.png'"; look: "use the previous color pallate", "remove the metro line theme, and per bot one screen".

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
