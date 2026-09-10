# Accessibility

## Status is never colour-only

Every agent status is conveyed by **four** redundant channels:

1. ring colour (design token),
2. animation (`think`, `review`, `wait`, `blocked`, `celebrate`, `walk`, `sit_type`),
3. a role glyph on the nameplate,
4. a `!` badge for `waiting` / `failed`,

plus the hover card and inspector spell the status out in words.

## Reduced motion

`prefers-reduced-motion: reduce` is honoured in three places:
- `store.ts` seeds `reducedMotion` from the media query;
- `OfficeCanvas` skips bob / dash-offset / walk-cycle math and snaps characters to
  their destination;
- `index.css` near-zeros all CSS transitions/animations.

## Keyboard & focus

The DOM overlays (top bar, mission board, hover card, inspector, timeline, debug) are
semantic HTML: real `<button>` / `<select>` elements, visible focus styles inherited
from the UA plus token colours, `aria-label` on the canvas and the inspector. Timeline
rows are clickable and select the agent. The canvas itself is pointer-driven (pan /
zoom / hover / click) — a keyboard agent-cycle ( `[` / `]` to move selection through
`agentOrder`) is a small future addition.

## Contrast & theme

Light and dark palettes are both defined explicitly (`index.css` `:root` /
`[data-theme="dark"]`, mirrored in `render/palette.ts`). Theme follows
`prefers-color-scheme` on first load and is then user-togglable and persisted.

## Not yet automated

axe-core assertions and a keyboard-navigation test need a Playwright/headless harness
(see `16_PRODUCTION_READINESS.md`). The overlays are built to pass them.
