# Shekel Design Language

The written design commitment for the Fable 5 UI/UX overhaul. Every screen rebuild reads this first
and is checked against it afterward. The companion `shekel-design` skill
(`.claude/skills/shekel-design/`) loads this document so the constraints reload in every design
session and screen 1 stays coherent with screen 50.

Last evaluated: 2026-06-12.

## Purpose

Shekel is a personal budget app organized around pay periods, not calendar months. Every transaction
maps to a specific paycheck, with roughly two years of forward projection. The product exists so one
person can answer, at a glance and with confidence: do I have enough money, what is due before my
next paycheck, and where is my projection heading. The design language serves that question. Clarity
about money beats decoration every time.

## Audience and tone

- **Audience:** a single financially literate operator managing their own household budget. Not a
  team, not a client. They open the app often and know their own data.
- **Tone:** calm, precise, trustworthy. The interface should feel like a well-kept ledger, not a
  consumer fintech dashboard competing for attention. No celebratory confetti, no growth-hacking
  nudges, no manufactured urgency. Urgency is reserved for real financial signals (a projected
  negative balance), and it is proportionate.
- **Voice in copy:** plain, short, specific. "Projected balance goes negative on May 14" beats "Uh
  oh, trouble ahead." Numbers are the headline; words are the caption.

## Design principles

1. **The number is the hero.** Every card answers one money question. The figure that answers it is
   the largest, highest-contrast element on the card. Everything else supports it.
2. **A figure and its caption never disagree.** If a number is a projection, say so; if it is "as
   of" a date, the number must be the value on that date. (This principle exists because the current
   balance card violates it; see `dashboard_card_audit.md`.)
3. **As simple as possible without losing functionality.** Consolidate redundant surfaces, remove
   cards that are not helpful, and reduce clicks, but never drop a number the user relies on.
   Removal is a product decision, made explicitly, not a side effect of a redesign.
4. **Every call to action goes somewhere useful.** A link or button resolves the thing it sits next
   to. No placeholder links to the home page.
5. **Tabular money.** Monetary values use tabular numerals and right alignment so columns of figures
   scan cleanly. The existing `font-mono` / tabular-nums treatment is the baseline.
6. **Density with breathing room.** This is a data-dense app for a power user; prefer compact,
   information-rich layouts over large empty hero space, but keep enough whitespace that figures do
   not collide.
7. **Consistent across screens.** A card, a progress bar, a money figure, an alert, and an empty
   state look and behave the same everywhere. The token layer and shared component patterns are how
   that consistency is enforced, not by convention alone.

## Hard constraints (non-negotiable)

These come from the codebase and its rules (CLAUDE.md, `.claude/rules/coding.md`), not from taste. A
direction that violates one of these is out of bounds regardless of how it looks.

- **Stack:** Bootstrap 5 + the design-token layer + HTMX + vanilla JS. No frontend framework, no
  SPA, unless the stack ROI gate explicitly decides otherwise.
- **Content Security Policy** (`app/__init__.py`, `_CSP_DIRECTIVES`): `script-src 'self'`,
  `style-src 'self'`. No inline `<style>` and no inline `<script>`. All CSS lives under
  `app/static/css/` in the file matching its concern (theme tokens / base / components / per-screen
  / utilities; layout and load-order contract in `css_architecture_audit.md`); all JS lives under
  `app/static/js/` and is loaded with `<script src>`. Pass data to JS via `data-*` attributes read
  with `element.dataset`.
- **Templates display, never compute.** All money math happens in the service or route with
  `Decimal` and is passed in. `float()` appears only at a serialization boundary (Chart.js JSON),
  never in a calculation.
- **Reference tables by id or enum, never by name string.** No comparing a `.name` against a string
  literal in Python or Jinja (gates `shekel-refname-compare`, plus the template hook).
- **Security and forms:** CSRF token on every form; HTMX mutations use POST; HTMX responses are
  partial templates (prefixed `_`) with an explicit `hx-target`; never `|safe` on user data; 404 for
  both "not found" and "not yours."
- **CSS hygiene:** Bootstrap utility classes first, custom CSS (in the matching file under
  `app/static/css/`) only as a last resort, no `!important` in new rules.
- **Both themes.** Every screen must render correctly in light and dark, driven by `data-bs-theme`.
  Use tokens, never hardcoded hex, so a theme is a single block of variable values.

## Design tokens

The token vocabulary lives in `app/static/css/theme-steel-ink.css` as CSS custom properties defined
per `[data-bs-theme="dark"]` and `[data-bs-theme="light"]` block (one file per palette; Steel Ink is
the app default and currently the only palette). New work references these variables; it does not
introduce new raw hex. The Step 2a refactor consolidates the remaining inlined hex onto these names.

| Token | Role |
| ----- | ---- |
| `--bs-primary` / `--shekel-accent` | brand accent (Steel Blue, `#4A9ECC`) |
| `--shekel-accent-hover` | accent pressed / hover (`#2878A8`) |
| `--shekel-accent-light` | accent highlight (`#6BB8E0`) |
| `--shekel-surface` | card / panel background |
| `--shekel-surface-raised` | raised surface (card header, raised cell) |
| `--shekel-text-primary` | primary body text |
| `--shekel-text-secondary` | secondary text |
| `--shekel-text-muted` | muted / caption text |
| `--shekel-border-strong` / `--shekel-border-subtle` | strong vs subtle borders |
| `--shekel-row-hover` | table row hover background |
| `--shekel-sticky-bg` | sticky column / header background |
| `--shekel-header-bg` | grid header background |
| `--shekel-summary-bg` | grid summary / total row background |
| `--shekel-done` | settled / done / positive (values in the Steel Ink table) |
| `--shekel-credit` | credit money state (violet; values in the Steel Ink table) |
| `--shekel-warning` | caution / low-balance warning -- a non-money state (amber) |
| `--shekel-danger` | danger / negative (values in the Steel Ink table) |
| `--shekel-number-ink` | money figures -- one step above body text, paired with mono 500 |
| `--shekel-section-income-bg` / `--shekel-section-income-text` | income section banners |
| `--shekel-section-expense-bg` / `--shekel-section-expense-text` | expense section banners |

Semantic mapping for money state: positive / settled uses `--shekel-done`, negative / over-budget
uses `--shekel-danger`, credit uses `--shekel-credit`. These already match the grid; the rebuild
keeps them consistent so a color means the same thing on every screen. Money-state tokens are used
ONLY when the state is true (D11): cautions that are not money states -- a low-but-positive balance,
the threshold line, a scheduled future change, a stale anchor, the ARM tag -- use
`--shekel-warning`, and structural markers (a chart landmark, a threshold) use accent or muted with
a text label.

### Committed theme: Steel Ink (decided 2026-06-11)

Chosen through the Loop A theme exploration on the rebuilt grid canvas (matrix T1-T4, wildcards
U1-U3, merges M1-M2; the developer selected M1). Steel Ink pairs an achromatic carbon base with the
Steel Blue signature accent: the accent is the only non-money chroma on screen, so the money state
colors carry the contrast ("the number is the hero," applied to color). Dark mode is the first-class
theme; light mode is an e-ink paper derivation. These values landed app-wide in Loop B phase 1 and
now live in `app/static/css/theme-steel-ink.css`.

| Token | Dark | Light |
| ----- | ---- | ----- |
| `--shekel-page-bg` (page behind surfaces) | `#101216` | `#EFEDE8` |
| `--shekel-surface` | `#1B1F26` | `#FBFAF7` |
| `--shekel-surface-raised` | `#282E38` | `#EDEBE5` |
| `--shekel-header-bg` | `#2C323E` | `#22242A` |
| `--shekel-header-text` (header stays dark in light mode) | `#F0F1F3` | `#F0F1F3` |
| `--shekel-sticky-bg` | `#161920` | `#EAE8E1` |
| `--shekel-row-hover` | `#374049` | `#E6E3DC` |
| `--shekel-group-header-bg` | `#222731` | `#F0EEE8` |
| `--shekel-summary-bg` | `#161920` | `#ECEAE3` |
| `--shekel-border-strong` | `#525A69` | `#C8C5BD` |
| `--shekel-border-subtle` | `#323945` | `#DDDAD2` |
| `--shekel-text-primary` | `#EDEFF2` | `#1B1D22` |
| `--shekel-number-ink` (money figures) | `#F5F7FA` | `#0D0F13` |
| `--shekel-text-secondary` | `#B9BFC9` | `#4A4E57` |
| `--shekel-text-muted` | `#98A1AF` | `#63686F` |
| `--shekel-accent` | `#4A9ECC` | `#25719F` |
| `--shekel-accent-hover` | `#2878A8` | `#1C5E86` |
| `--shekel-accent-light` | `#6BB8E0` | `#4A9ECC` |
| `--shekel-accent-rgb` | `74, 158, 204` | `37, 113, 159` |
| `--shekel-done` | `#3FB950` | `#1A7F37` |
| `--shekel-credit` | `#B190F8` | `#7443CC` |
| `--shekel-warning` | `#D29922` | `#855800` |
| `--shekel-danger` | `#FB6D63` | `#CF222E` |
| `--shekel-section-income-bg` | `#1B2A20` | `#DCEBDD` |
| `--shekel-section-income-text` | `#4CC368` | `#185C2C` |
| `--shekel-section-expense-bg` | `#2E1F23` | `#F3DCDF` |
| `--shekel-section-expense-text` | `#E5697E` | `#8E2336` |

Notes: the accent now differs between modes (`#4A9ECC` dark, `#25719F` light) for contrast on the
paper background, so accent tints should use `color-mix` with `--shekel-accent` (or the per-theme
`--shekel-accent-rgb`) rather than hardcoded rgba values. The state trio is the vivid set; the soft
Tokyo Night trio (M2) was considered and rejected because the achromatic base exists precisely to
let the state colors carry maximum contrast.

Contrast revision 2026-07-09 (polish audit RC4): `--shekel-text-muted` was under WCAG AA at the
caption sizes it is used at (dark 4.31:1 on surface, light 4.07:1 on the page bg); both values were
lifted one step (`#757C88 -> #848B97` dark, `#6E737D -> #63686F` light) and now clear 4.5:1 on every
surface tier while staying visibly quieter than the secondary tier. The light accent deepened
`#2878A8 -> #25719F` for the same reason (links on the bare page bg were 4.13:1). A type floor
accompanies this: no text below `0.6875rem` (11px) anywhere in the app.

### Dark neutral revision: "Graphite" (ratified 2026-07-09, S16 token round)

The original dark ladder packed seven surface grays into a 1.28:1 total span; adjacent tiers
differed by 1.02-1.07:1, below reliable perception for large fills, so the tiers read as one mud
(polish audit D15, raised by the developer during S12). The ratified Graphite ladder (chosen over
Carbon, Deep Field, and Warm Ink candidates on the live grid and dashboard) lifts the whole dark UI
a step and spreads the working pairs: page-vs-surface 1.13:1, hover 1.57:1 above the surface, subtle
border 1.42:1, total span 1.78:1. Light mode neutrals are unchanged. Two riders, both AA-forced by
the new raised tier and verified with the S1 chip-tint method:

- Dark `--shekel-danger` lifted `#F85149 -> #FB6D63` (the old red was already 4.51:1 on the old
  hover tint) and dark `--shekel-credit` lifted `#A87BF7 -> #B190F8`.
- **Ink hierarchy** ("the number is the hero" applied to ink): money figures render in
  `--shekel-number-ink` (one step beyond body text in both themes) at JetBrains Mono 500
  (`.font-mono` app-wide; the 500 face is a new weight stop on the existing variable font). Grid row
  labels drop from th-default bold to 600, and data captions (the grid's Due dates) use secondary
  ink, not muted (`.cell-caption`). Muted is for true de-emphasis only, never load-bearing data.
- The grid's current-period column highlight was ruled REMOVED in the same session (redundant at
  default view where the current period is the first column); the removal builds with S12 Loop B.

### Credit / warning split (ratified 2026-07-09, S1 token round)

The credit money state and the caution role shared amber, making an informational state (paid by
credit card) indistinguishable from a warning (low balance). The developer's D5+D11 ruling split
them:

- `--shekel-credit` is violet (`#B190F8` dark / `#7443CC` light; the dark value lifted from S1's
  `#A87BF7` in the S16 Graphite revision, see above) and means EXACTLY the credit money state:
  credit chips and glyphs, the mark-credit / undo-credit actions, the command palette's credit
  action icon.
- `--shekel-warning` keeps the amber caution role (`#D29922` dark / `#855800` light): the grid's
  low-but-positive balance, the low-balance threshold line, due-soon markers, stale-anchor captions,
  scheduled escrow changes, the ARM tag. The light value deepened from credit's old `#9A6700`, which
  was under AA on two of three light tiers.
- Every value clears WCAG AA (>= 4.5:1) on all three surface tiers per theme AND as chip text on its
  own 16% `color-mix` tint.

**Archive / delete button convention (D5):** archive and deactivate controls are `--shekel-warning`
amber outline buttons; true deletes are `--shekel-danger` outline buttons; restore / unarchive /
reactivate controls are neutral `btn-outline-secondary` outline buttons (a benign, reversible action
in the same quiet tier as Edit, so color stays reserved for the caution and danger controls -- money
green is never used on a non-money control). Amber is never used on a control for any other reason,
and raw Bootstrap `btn-outline-warning` (`#FFC107`) and `btn-outline-success` (money green) are
retired on controls. The `btn-outline-warning` / `btn-outline-danger` skins live app-wide in
`base.css`; the restore sweep replaced `btn-outline-success` with `btn-outline-secondary` in the
five archived-list / reactivate controls.

## Accessibility

- Color is never the only signal. Pair every color-coded state with an icon or text (the bill row
  already pairs over-budget red with bold weight; keep that pattern).
- Maintain WCAG AA contrast in both themes. The token values are the contract; do not introduce
  one-off colors that have not been contrast-checked.
- Interactive elements are real controls with roles and keyboard access (the balance card already
  uses `role="button" tabindex="0"`); keep that for any click-to-edit affordance.
- Every progress bar and alert keeps its ARIA attributes and screen-reader text.

## Differentiation

Shekel's distinguishing idea is the **pay-period ledger**: money is organized by paycheck, projected
forward, and presented with precision. The "quiet, dense, trustworthy ledger" aesthetic is the
GRID's identity -- that screen grew out of the developer's long-lived spreadsheet workflow and keeps
its spreadsheet soul. It is not an app-wide prescription. (Amended 2026-06-12: the earlier app-wide
wording described the app's then-current state, not the destination; the developer's intent is a
total overhaul.) Other screens use whatever presentation serves their job best -- charts first where
a trend IS the answer (the dashboard's projected end-balance chart is the canonical example) --
while staying calm and precise: no confetti, no manufactured urgency, no decoration that outranks a
number. The Steel Blue palette, the design tokens, and tabular figures remain the signature
everywhere, and the principles above (the number is the hero, a figure and its caption never
disagree, tabular money, both themes) bind every screen regardless of form.

## Standalone form idiom (added 2026-07-05)

Every standalone create / edit / setup form is presented in one shared Steel Ink card via the
`form_card(title, icon)` macro (`app/templates/_form_macros.html`): a themed Bootstrap `.card` with
a titled `.card-header` over a `.card-body`, wrapped around the form body with `{% call %}`. Grouped
fields inside a form use the `.form-section` (hairline-separated) and `.form-section--accent` (a
tinted, accent-left-ruled emphasis box, for a form's centre of gravity such as a recurrence picker)
vocabulary in `components.css`. New forms adopt this rather than a bare `<h4>` + form, so the create
/ edit surfaces stay consistent (principle 7). Exceptions, by decision: pre-login auth pages keep
their narrow logo-gate card; multi-part forms already built as stacked cards (e.g.
`salary/form.html`) are left intact rather than rewritten; inline HTMX edit forms (grid cells, quick
/ full edits) belong to their host surface and are not carded.

## How a screen uses this document

1. Read this brief and the relevant per-screen audit (for the dashboard, `dashboard_card_audit.md`).
2. Confirm the screen's job and its one-question-per-card breakdown.
3. Build with tokens, Bootstrap utilities, and the constraints above.
4. Verify against the principles and constraints, in both themes, before calling the screen done.
