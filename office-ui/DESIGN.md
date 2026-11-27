---
name: Seleric office-ui
description: A transit-map decision inbox for marketers. Navy ink on cool paper, a scarlet line for what needs a person.
colors:
  navy-ink: "#0b1230"
  navy-hover: "#1b2550"
  text-ink: "#0e1430"
  text-dim: "#566079"
  text-faint: "#646d86"
  scarlet-signal: "#e21d2d"
  scarlet-soft: "#fdecee"
  scarlet-ink: "#b3121f"
  cobalt-link: "#1e5bff"
  selected-wash: "#f1f4ff"
  selected-line: "#c9d6ff"
  range-green: "#16753a"
  range-green-soft: "#e7f6ec"
  range-green-line: "#a9d9b8"
  amber-ink: "#6b5200"
  amber-soft: "#fff6da"
  cool-paper: "#f6f7f9"
  card-white: "#ffffff"
  rail-grey: "#eef0f4"
  hover-grey: "#e3e6ec"
  hairline: "#e1e5ec"
  hairline-strong: "#c5cbd8"
  night-ground: "#0a0f24"
  night-card: "#111833"
  night-primary: "#2f5bea"
  night-signal: "#ff4d5a"
typography:
  proposed-value:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "40px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.025em"
    fontFeature: "tnum"
  review-value:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 800
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 800
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 800
    lineHeight: 1.4
    letterSpacing: "-0.025em"
  figure:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 800
    lineHeight: 1.55
    fontFeature: "tnum"
  body:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  row-title:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13.5px"
    fontWeight: 700
    lineHeight: 1.55
  label:
    fontFamily: "Overpass Variable, Overpass, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.33
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  xs: "4px"
  control-legacy: "7px"
  md: "8px"
  lg: "10px"
  xl: "22px"
  full: "9999px"
spacing:
  row-gap: "2px"
  stack-sm: "8px"
  row-x: "12px"
  gutter: "16px"
  rail-pad: "18px"
  column-gap: "24px"
  decision-x: "28px"
components:
  button-primary:
    backgroundColor: "{colors.navy-ink}"
    textColor: "{colors.card-white}"
    rounded: "{rounded.lg}"
    padding: "0 10px"
    height: "32px"
  button-outline:
    backgroundColor: "{colors.cool-paper}"
    textColor: "{colors.text-ink}"
    rounded: "{rounded.lg}"
    height: "32px"
  button-ghost:
    textColor: "{colors.text-ink}"
    rounded: "{rounded.lg}"
    height: "32px"
  inbox-row:
    textColor: "{colors.text-ink}"
    typography: "{typography.row-title}"
    rounded: "{rounded.lg}"
    padding: "10px 12px"
  inbox-row-selected:
    backgroundColor: "{colors.selected-wash}"
    rounded: "{rounded.lg}"
  pill-action:
    backgroundColor: "{colors.scarlet-soft}"
    textColor: "{colors.scarlet-ink}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
  pill-normal:
    backgroundColor: "{colors.range-green-soft}"
    textColor: "{colors.range-green}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
  pill-unverified:
    backgroundColor: "{colors.amber-soft}"
    textColor: "{colors.amber-ink}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
  badge-count:
    backgroundColor: "{colors.scarlet-signal}"
    textColor: "{colors.card-white}"
    rounded: "{rounded.full}"
    padding: "0 6px"
    height: "18px"
  card:
    backgroundColor: "{colors.card-white}"
    rounded: "{rounded.lg}"
    padding: "12px"
---

# Design System: Seleric office-ui

## Overview

**Creative North Star: "The Metro Line"**

The app reads like a transit diagram printed on cool paper. Navy is the ink everything is drawn in; one scarlet line runs through it and marks only the stops where a person has to get off and act. Records are rows, events are ring stops on a line, and a number's normal range is a short green band you can read at a glance. There is one face, Overpass, set heavy for figures and names and light for everything else.

Density is an operator's: a 14px body, 13.5px row titles, a 300px list beside a reading column capped at 760px, and a narrow history rail. Hierarchy comes from weight and size of the figure (the proposed value is the largest thing on screen), never from colour fills or decoration. Surfaces are flat white cards on a grey canvas, separated by hairlines.

Dark mode is the same line at night: a deep navy ground, cobalt taking over as primary because navy would vanish, scarlet kept for the line.

**Key Characteristics:**
- One typeface (Overpass Variable, self-hosted), weights 400 to 800.
- Navy ink and primary; scarlet only for what needs a person; green band for normal; cobalt for links and focus.
- Ring stops instead of dots, icons or avatars to mark rows and events.
- Tabular numerals on every figure.
- Flat, hairline-separated surfaces; shadows only for floating layers.
- Phone shows the list first, the detail on tap with Back.

## Colors

A cool, nearly monochrome navy-on-grey palette with one loud signal colour and one quiet range colour.

### Primary
- **Navy Ink**: primary buttons, ring stops, the brand ring, the "today" ring on an in-range bar. Stock buttons fade the fill to 80% on hover; brand-filled controls outside the stock button (composer send, "Ask Seleric") deepen to **Navy Hover**. In dark mode the primary becomes **Night Primary** (cobalt-navy) and stops draw in the foreground colour.
- **Text Ink**: all body and heading text; a hair lighter than Navy Ink so long text is not harsher than the buttons.

### Secondary
- **Scarlet Signal**: the metro line on the history rail, the proposed stop, the "today" ring when a check is out of range, count badges, the brand dot. Its soft tint **Scarlet Soft** backs "Needs action" and High/Low pills, whose text is the darker **Scarlet Ink** for contrast. Dark mode uses **Night Signal**.
- **Cobalt Link**: links and the keyboard focus ring. Never a fill on a primary action in light mode.

### Tertiary
- **Range Green** / **Range Green Soft** / **Range Green Line**: the normal band (p10 to p90) on range bars, the Normal pill, "All in order".
- **Amber Ink** / **Amber Soft**: "Couldn't check" pills, the unverified note under a decision, warning badges.

### Neutral
- **Cool Paper**: page canvas.
- **Card White**: list pane, history rail, cards, popovers.
- **Rail Grey**: sidebar and secondary surfaces; **Hover Grey** for hover on rail items.
- **Selected Wash** with **Selected Line**: the selected inbox row. (The current nav item is instead a Card White tile with the resting lift and a hairline ring.)
- **Hairline** / **Hairline Strong**: dividers, check-row rules, input borders, scrollbar thumbs.
- **Text Dim** / **Text Faint**: meta lines, timestamps, "normal ..." captions, group heads.
- **Night Ground** / **Night Card**: dark canvas and card.

### Named Rules
**The Scarlet Stop Rule.** The scarlet line itself (the history rail's route and the brand mark's dot) is the world's route and always draws in scarlet. Anywhere else, scarlet on a stop, ring, value, pill or badge means a person must act: an action-needed run, an out-of-range reading, the proposed stop, the count badge. A failed job, an error banner or a destructive button uses Scarlet Ink or the danger tone unless a person must act on it.

**The Navy Ink Rule.** Navy is both the ink and the primary action. There is no second brand hue; a "branded" control is simply a navy one.

**The Green Band Rule.** Green means healthy: inside the normal range, all in order, resolved. It never marks a button or a brand moment.

## Typography

**Body Font:** Overpass Variable (with Overpass, ui-sans-serif, system-ui)
**Label/Mono Font:** JetBrains Mono, for code and raw payloads only

**Character:** A transit-signage grotesque used at one family. Heavy weights (700 to 800, tight tracking) carry names and figures; 400 carries prose. Headings share the body face; there is no display or serif face.

### Hierarchy
- **Proposed value** (800, 40px, line-height 1; 34px under 640px): the new value of a proposed change, preceded by the struck old value at 18px in Text Dim.
- **Review value** (800, 28px, 1.25): "Review {field}" when a proposal carries no number.
- **Headline** (800, 24px, 1.25): the entity name on a decision.
- **Title** (800, 20px): page titles in a list pane ("Bots"); the top bar uses 17px/800.
- **Figure** (800, 15px): a check's current value. Text-valued checks longer than a short figure drop to 13.5px/600 and stack under their label.
- **Body** (400, 14px, 1.55): prose; the decision reason is 15px at 85% ink, capped at 66ch.
- **Row title** (700, 13.5px): inbox rows, check labels sit at 13.5px/400.
- **Label** (600, 12px, Text Dim): group heads ("Needs your decision"), table headers, meta lines (12 to 13px). Sentence case.

### Named Rules
**The Tabular Figure Rule.** Every number that can change (money, counts, percentages, times in lists, badges) is set with tabular numerals so columns and updates do not jitter.

**The One Face Rule.** Overpass is the only text face. Hierarchy is weight and size, never a second family.

## Layout

The Bots surface is a three-pane grid inside the app frame. At 80rem and wider: list 300px, decision column fluid, history rail 280px. From 768px to 80rem: list `minmax(280px, 320px)` and decision; the campaign history moves into the decision column under a top rule. Below 768px: one pane at a time, the list first; opening an item replaces it with the detail and a Back button labelled with where it returns ("Needs you" / "All runs"), backed by browser history.

Breakpoints that mix units are written in rem (`min-[80rem]`), because Tailwind 4 sorts arbitrary px breakpoints before rem ones and a px value would be overridden by the md rule.

The decision column is capped at 760px with 28px side padding (16px on phones). Checks sit in a two-column grid with a 24px column gap, one column on phones. Lists use 2px between rows, 12px row padding, and a 16px gutter for headers and group heads. On phones the three decision actions become a two-column grid with the primary spanning both.

The app frame shows a 64px icon rail on Bots (`rail`), a 272px labelled sidebar elsewhere at desktop, and a slide-in panel below desktop.

### Named Rules
**The 44 Rule.** On coarse pointers every tap target in the Bots surface grows to at least 44px (buttons `h-11`, icon buttons `size-11`, tabs 48px, rows 14px vertical padding) without moving anything at fine pointers.

**The List-First Rule.** Phones never open on a detail; they show the list, then the detail with Back.

## Elevation & Depth

Flat by default. Depth comes from tone (grey canvas, white cards, grey sidebar) and 1px hairlines. The only resting shadow is the current nav item's `0 1px 2px rgba(14,20,48,0.08)` with a hairline ring; dark mode drops it. Floating layers (sheets, menus, dialogs) take the large shadow.

### Shadow Vocabulary
- **Resting lift** (`box-shadow: 0 1px 2px rgba(14, 20, 48, 0.08)`): current nav item, composer and voice dock.
- **Floating** (`box-shadow: 0 12px 32px rgba(14, 20, 48, 0.16)`; dark `0 12px 32px rgba(0,0,0,0.55)`): sheets, menus, dialogs, hover cards.

### Named Rules
**The Hairline Rule.** Separate with a 1px Hairline or whitespace; a status frame is a full 1px border in the status colour, never a thick side stripe.

## Shapes

Gently rounded rectangles for anything you click or read in (10px), 8px for tab triggers and inline notes, full circles for everything that is a stop, a pill or a badge. The ring stop is the system's silhouette: a white-filled circle with a heavy border (3px at 14px, 4px at 18px, 2.5px at 10px), dashed when only proposed. The range bar is a 2px hairline track with a 10px-tall green band (3px radius), a 2px median tick and a 14px ring for today. Older native selects and scrollbars keep a 7px radius; the composer and voice dock use 22px.

## Components

### Buttons
- **Shape:** gently rounded (10px), 32px tall, 14px/500 label with a 16px lucide icon.
- **Primary:** Navy Ink fill, white text; hover lightens to 80% opacity. Dark: Night Primary.
- **Outline:** hairline border on the canvas colour; hover goes to the muted grey.
- **Ghost:** no fill until hover. Used for Dismiss, Back and row buttons.
- **Focus:** a 3px cobalt ring at 50% plus a cobalt border. Pressing nudges 1px down.

### Pills and badges
- **Verdict pill:** full-round, 12px/700, 2px by 10px padding. Needs action: Scarlet Soft with Scarlet Ink and an alert icon. All in order: green soft. Couldn't check: amber soft. Alert cleared: muted grey.
- **Read pill:** Normal on green soft; High and Low on Scarlet Soft with Scarlet Ink.
- **Count badge:** Scarlet Signal fill, white 11 to 11.5px/700 tabular figure, 18px line, full-round. On the collapsed rail it sits at the icon's top right at 16px. A warning-only count uses amber soft instead.

### Inbox rows
- **Grid:** an 18px stop column, the text, then the value column.
- **Text:** entity at 13.5px/700, a 12px Text Dim line "time · condition" clamped to two lines.
- **Value:** the proposed value at 14px/800 with the struck current value above it at 11.5px.
- **Selected:** Selected Wash fill with a Selected Line border; unselected rows carry a transparent border so nothing shifts.
- **Stops:** large navy ring for decisions, scarlet ring for action-needed in All runs, small muted ring for the calm summary.

### Decision
Verdict pill and a dot-separated meta line, the 24px entity, old value struck beside the 40px new value, the reason, an optional "Change n of m" line, then Open report (primary), Ask about this (outline), Dismiss (ghost). Below: "Checks at hh:mm" as range-bar rows, "What moved today" as a plain table (7px cell padding, 12px/600 grey header, 13px tabular body, hairline rows), and an amber note stating what the bot did not verify.

### Range bar (check row)
Label left, Figure right, the bar, then "normal lo–hi" caption and the read pill. Today's ring is navy when Normal and scarlet when High or Low. Without a numeric range the row says "no normal range" and drops the bar.

### Metro timeline (history rail)
A 4px scarlet line at 9px from the left, events 18px apart. Each event: 12px Text Dim time, 14px/700 title, 12.5px note. The proposed change is the top event as a large dashed scarlet stop; other events are navy rings, large when from the open run.

### Navigation
Icon rail and sidebar on Rail Grey. Items are 36px, 10px radius, Text Dim icon; the current item becomes a white card with the resting lift and a hairline ring, 600 weight. The brand mark is a 16px navy ring (3.5px border) with a scarlet dot offset to its right, beside "Seleric" at 18px/800.

### Notifications
Cards are 10px radius, hairline, white, 12px padding. An open critical or warning item is framed by a full 1px border in its severity colour (danger or amber); info keeps the hairline; resolved drops to 70% opacity. Severity pills are 11px/500 full-round.

## Do's and Don'ts

### Do:
- **Do** keep scarlet for the line itself and, on stops, rings, values, pills and badges, only for what needs a person: action-needed, out-of-range, the proposed stop, the count badge.
- **Do** use Navy Ink for primary actions and ring stops; cobalt only for links and focus.
- **Do** show a normal range as a green band with a median tick and a ring for today.
- **Do** set every figure in tabular numerals.
- **Do** give every tap target 44px on coarse pointers.
- **Do** show phones the list first and the detail with Back.
- **Do** mark events and rows with ring stops, dashed for a change that is only proposed.
- **Do** write mixed-unit arbitrary breakpoints in rem (`min-[80rem]`).

### Don't:
- **Don't** use scarlet for decoration, headings, failed jobs or generic errors.
- **Don't** add a second typeface; headings use Overpass.
- **Don't** mark status with a thick coloured side stripe; use a full 1px border or a pill.
- **Don't** add uppercase tracked labels above headings; group heads are 12px sentence case.
- **Don't** add resting shadows to cards; flat with a hairline.
- **Don't** use glyph or emoji icons; icons are lucide line icons at 16px.
