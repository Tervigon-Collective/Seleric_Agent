# office-ui design rules

Stock shadcn components, Apple-HIG palette, minimal and subtle. Tokens live in `src/index.css`; `src/styles/shadcn.css` only aliases them.

## Rules
- Compose from `src/components/ui/*` plus Tailwind utilities. No new CSS component classes; don't edit stock `ui/*` for style.
- Colour: only tokens (`bg-background`, `text-muted-foreground`, `border`, `--brand`, `--st-*` for agent status). No hex literals in components.
- Radius: `rounded-md` controls, `rounded-lg` cards/panels/composer, `rounded-full` pills/avatars. No `xl`/`2xl`.
- Elevation: flat with a 1px border by default. `shadow-lg` only for floating layers (inspector, hover card, menus, dialogs).
- Width: chat column (transcript and composer) is `max-w-[880px]`; dialogs `sm:max-w-2xl`, wide chart dialog `sm:max-w-5xl`.
- States: every interactive control needs focus-visible, disabled and (if async) loading. Use `Button`, which already has them; no raw `<button>`/`<input>`.
- Motion: 120ms fast / 180ms base (`--motion-fast`, `--motion`). Fade/slide only.
- Separate with whitespace before adding borders or shadows.

## Anti-patterns
Serif or editorial headings, extra web fonts, gradients, decorative effects, off-token colours, ad-hoc radii/shadows, per-page container widths.
