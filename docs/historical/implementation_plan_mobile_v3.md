> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Mobile-First v3 -- Implementation Plan

- Version: 1.0
- Date: 2026-05-23
- Author: prepared for the solo developer (SaltyReformed)
- Source plan: this document supersedes the deferred v2 plan
  (`docs/implementation_plan_mobile_v2.md`, deleted from the working
  tree 2026-05-23) and extends the v1 baseline shipped April 2026
  (`docs/implementation_plan_mobile.md`).
- Prerequisite reading: `docs/implementation_plan_mobile.md` (v1
  baseline; what shipped), `docs/mobile_friendliness_assessment.md`
  (the tier-1/2/3 audit that v1 acted on), `CLAUDE.md` (Rules,
  Transfer Invariants), `docs/coding-standards.md`,
  `docs/testing-standards.md`, the live state of the files cited
  in Section 6 in full before any code is edited.
- Standards: every commit follows CLAUDE.md and
  `docs/coding-standards.md` (Python, Jinja, JS, CSS, shell); every
  test pass follows `docs/testing-standards.md`. Test IDs in this
  plan use the form `C<commit>-<n>`.

---

## 0. Context

The v1 mobile pass (11 commits, complete April 2026) made the desktop app *functional* on small
screens: `table-responsive` wrappers, 44 px touch targets, a bottom-sheet popover at `<768 px`, a
card-based `_mobile_grid.html`, a 320 px breakpoint, a PWA manifest, and the `shekel-scroll-pills`
nav-pills convention. The user reports the result as "usable but clunky" and now wants the phone to
be the primary daily driver for both themselves (owner) and their spouse (companion role; uses
`/companion` exclusively).

The four daily workflows the user names (in their own words):

1. **Marking transactions as paid** -- the highest-frequency
   single-tap action. Today: tap card -> bottom sheet -> Mark
   Done button -> sheet dismisses. Three taps for one decision.
2. **Editing the actual amount** when the paid amount differs
   from the estimate. Today: same three-tap path through the
   bottom sheet, then the amount field needs the iOS numeric
   keypad (which has no decimal point under the default
   `type="number"` -- `inputmode="decimal"` fixes this; not yet
   applied to any of the 10 monetary inputs in the grid layer).
3. **Adding an ad-hoc transaction** (unplanned grocery run,
   one-off bill). Today: the "Add Transaction" button at
   `grid.html:74-76` opens a desktop-shaped Bootstrap modal that
   stacks form fields on mobile but does not take over the
   viewport; the save button can scroll out of reach above the
   keyboard.
4. **Reviewing upcoming bills and the projected end balance**
   for the current and next two pay periods. Today: scroll past
   the table on the desktop grid, or swipe through three single-
   period cards on `_mobile_grid.html`. Neither is set up for
   "answer the affordability question at a glance."

All four happen in the grid (or `/companion` for the spouse). The grid handles the work correctly;
the mobile *rendering* of the grid forces extra navigation for each, and the spouse-facing companion
view at `/companion` shares no card vocabulary with the owner mobile grid, so any visual change to
one drifts from the other.

This plan reframes mobile from "the desktop app fits on a phone" to "the high-frequency flows are
designed for the phone first, the canonical producers remain the only source of truth, and the
desktop layout stays unchanged." Phase 1 is a pure refactor (zero visible change) that eliminates a
transaction-matching duplication between the desktop and mobile templates so the subsequent phases
do not multiply the warning that already lives at `_mobile_grid.html:5-7`. Phases 2-5 ship the
mobile UX changes; each phase is independently mergeable to `dev` and then `dev -> main` via PR.

### Consequence of getting this wrong

Two distinct failure modes shape the rules in Section 1:

1. **A mobile-specific code path that re-implements logic the
   canonical producers already own.** The financial-calculation
   remediation (`docs/audits/financial_calculations/remediation_plan.md`)
   established `app/services/balance_resolver.py` and
   `app/services/loan_resolver.py` as the sole producers of
   balances, period subtotals, monthly payments, and amortization
   schedules. A mobile partial that does its own balance math, or
   reads `Account.current_anchor_*` or `LoanParams.current_principal`
   directly, recreates the bug class that audit closed. **Every
   displayed monetary value in this plan flows through the
   canonical producer; no exceptions.** This plan touches the
   producers in only one Python file (`app/routes/grid.py`,
   Phase 1, to pre-compute an existing Jinja matching loop) and
   the new code in that file mirrors the existing predicate
   text-for-text -- no new conditions are introduced.

2. **Service worker caching financial data.** A grid that serves
   yesterday's balances offline produces wrong numbers silently.
   The service worker in Phase 5 (Commit 25) caches static assets
   only; HTML and JSON responses are network-only pass-through
   with no stale fallback. A user offline sees an honest
   connection error, not stale money. The cache audit at
   `docs/audits/financial_calculations/remediation_plan.md`
   Section 8 stays valid by construction: `caches.open(...)`
   inspected at any point contains only `/static/...` URLs.

The cross-cutting rules in Section 1 are the load-bearing guarantees that make these two failure
modes impossible to introduce by accident.

---

## 1. Hard rules for executing this plan

These bind every commit. They restate CLAUDE.md, the coding standards, and the testing standards in
the context of this work.

1. **Read the entire file before editing it.** Line numbers in
   this plan are verified as of 2026-05-23 against the current
   `dev` branch (HEAD `207e31c`). They drift the moment any
   commit lands. Every commit's Section D says "re-grep current
   lines"; do that before any edit. Never edit by remembered line
   number.
2. **Canonical producers only for monetary values.** Every
   displayed dollar amount flows through `balance_resolver`
   (`balances_for`, `period_subtotal`, `balance_as_of_date`) or
   `loan_resolver`. No partial or JS file reads
   `Account.current_anchor_balance`, `Account.current_anchor_period_id`,
   `LoanParams.current_principal`, or `LoanParams.interest_rate`
   directly. The static guard the financial-calculation
   remediation added (commit `842d415`, finding F-6,
   `tests/test_static_guards.py`) is the model -- if this plan
   adds a new mobile partial, the guard's scope extends to it.
3. **Decimal-only on the server. JS is display-only for money.**
   JS never performs monetary arithmetic. The mobile cards
   receive pre-rounded strings from server-rendered partials
   (the existing `{{ "{:,.0f}".format(...) }}` convention in
   `_mobile_grid.html:103-127` continues; no JS-side number
   formatting).
4. **No new financial logic.** All math, status transitions,
   balance projections stay in `app/services/`. The Phase 1
   precomputation (Commit 2) moves an existing Jinja matching
   loop to Python with byte-identical conditions; the predicate
   is text-for-text identical to the Jinja loop it replaces.
   This is the only Python change to grid routing in the plan.
5. **HTMX patterns.** 200 for swap, 422 for validation, 204 for
   no-content. Mobile-specific responses return partials, never
   full pages. **No parallel `/m/...` routes.** Where a mobile
   partial differs from desktop, the same route returns
   different markup based on a context flag or media-query CSS;
   parallel route trees double the maintenance burden and are
   the v4A approach from `docs/mobile_friendliness_assessment.md`
   that the v1 plan explicitly rejected.
6. **CSRF.** Mobile forms use `{{ csrf_token() }}` or rely on
   the existing `htmx:configRequest` handler in
   `app/static/js/app.js`. Never bypass.
7. **Touch targets >= 44x44 CSS px** (WCAG 2.5.5 AA, Apple HIG).
   Already enforced on buttons inside `.txn-full-edit-popover`
   at `app/static/css/app.css:815-819` (per the v1 commit
   `6b75d10`). Every new component this plan adds (mobile card
   action bars, swipe-action buttons, offcanvas nav items, the
   "This Period" tab pills) inherits the same floor.
8. **`inputmode="decimal"` on every monetary input** plus
   `font-size: 16px` to suppress iOS auto-zoom. The 10 monetary
   `<input type="number" step="0.01">` sites listed in
   Section 6.4 each gain the attribute exactly once, in Commit 11.
9. **Firefox parity is a hard requirement.** Every commit's
   Section F runs on Firefox Desktop (Gecko at 1920x1080 and via
   responsive design mode at 375x812 + 430x932). The user's iPhones
   (XS and 16 Plus) run Firefox iOS which is WebKit per Apple's
   App Store rules; commits touching features WebKit handles
   differently from Gecko (swipe gesture passive-listener
   semantics, `visualViewport` API support, drag transforms)
   document the divergence explicitly in Section G rather than
   letting the user discover it later.
10. **Touch gestures must have a non-gesture equivalent.**
    Swipe-left-to-mark-paid (Commit 9) ships alongside the
    in-line action bar (Commit 7); the gesture is a shortcut,
    not the only path. Period-navigation swipe (already
    shipped in v1 at `mobile_grid.js:47-59`) keeps its
    `[<] [>]` arrow buttons. The grid stays usable on desktop
    keyboard and with assistive technology.
11. **No JS framework. No new CSS framework.** HTMX +
    vanilla JS + Bootstrap 5 only per `docs/coding-standards.md`.
    CSP forbids inline `<script>`; all JS is served from
    `app/static/js/*`. The service worker (Commit 25) is a
    static file at `app/static/sw.js`; registration happens in
    the existing `app/static/js/app.js`.
12. **Atomic commits, suite green after each.** Targeted tests
    (`./scripts/test.sh tests/path/test_file.py -v`) during
    edits; the full suite (`./scripts/test.sh`, ~65 s at
    `pytest.ini`'s `-n 12` default) as the final per-commit
    gate. `pylint app/ --fail-on=E,F` clean after every commit,
    no new warnings.
13. **Stay in scope.** Out-of-scope issues spotted during
    verification go to `J. OUT OF SCOPE -- flagged, not fixed`
    in the work summary with `file:line` + reason. If the issue
    is in a file this plan touches in a later commit, fold it in
    there; otherwise leave it.
14. **Do not push.** After green, present the work summary and
    ASK whether to commit and push to `dev` (this triggers CI;
    PR-to-`main` is the promotion path per CLAUDE.md "Git
    Workflow").
15. **Style.** No Unicode em/en dashes (use `--` or `-` per
    CLAUDE.md "Style"). Pythonic, type-hinted, substantive
    docstrings, specific exceptions, no broad `except Exception`.
    All Jinja files end at LF, no trailing whitespace.

---

## 2. Design decisions (made at plan time; confirm at review)

These resolve product / UX choices the implementation cannot make on its own. Each was explicitly
confirmed during planning on 2026-05-23 via the `AskUserQuestion` exchange logged in this session's
transcript.

- **D-A. Mobile grid layout is "This Period" / "Plan" tabs, not
  a chronological feed.** "This Period" defaults to today's pay
  period with `[<] [>]` arrows stepping to adjacent periods.
  "Plan" is a read-only multi-period summary accordion anchored
  at `current_period` and walking forward `PLAN_WINDOW_PERIODS`
  (13, ~6 months biweekly) periods, decoupled from the URL's
  `periods` / `offset` state.  Tabs share the `render_row_card`
  macro **only** for the interactive This Period view; Plan
  rows use the narrower `render_row_static` macro (no tap-to-
  expand, no envelope entries, no action bar -- entries are by
  design a current / past concept and Plan has no daily-driver
  affordances).  Rationale: the user picked this layout over a
  flat chronological feed because the affordability question
  ("can I pay this bill today?") is period-anchored, not
  date-anchored.

  **Plan tab supersedes the original "multi-period card-scroll
  view" design** (recorded here for the record because the
  original D-A description matched what shipped as Commit 7 of
  this plan).  The original Plan tab rendered N
  `<div class="mobile-period-panel">` siblings with prev/next
  buttons and a planPane swipe handler; only the first panel was
  visible by default, the rest hidden behind a Bootstrap `d-none`
  class.  The panel-swap JS in `mobile_grid.js::navigate()` set
  `style.display = ''` to reveal a sibling panel, which silently
  failed because `d-none`'s `display: none !important` outranked
  the inline-style clear.  After a single Next tap the user saw
  nothing, and the tab also offered no view that was meaningfully
  different from This Period when it did work.  The
  Plan-as-summary-accordion replacement collapses that whole
  subsystem (markup + JS + swipe handler + the matching-loop fan-
  out per panel) into a Bootstrap declarative accordion with
  `data-bs-parent` mutual exclusion.  See
  `app/routes/grid.py::_build_plan_view` for the parallel data
  slice and `app/templates/grid/_mobile_plan.html` for the new
  partial.

- **D-B. Companion view shares the "This Period" partial via a
  shared service-layer helper.** Rather than copy the card
  layout into `companion/index.html` or invent a thinner
  template shape, Commit 13 extracts `_build_row_keys`
  (currently at `grid.py:68-162`) plus the matching pre-computation
  into a new `app/services/grid_view_service.py`. Both
  `app/routes/grid.py::index` and `app/routes/companion.py::index`
  call into it. The mobile partial sees the same dict from
  either route. Rationale: keeps "Routes -> Services -> Models"
  clean per CLAUDE.md "Architecture"; eliminates the divergence
  risk between owner mobile and companion mobile that exists
  today.

- **D-C. Inline action bar saves a tap; the bottom sheet is for
  the full form.** Tapping a mobile transaction card slides an
  action bar in beneath it with `[Mark Paid]` `[Edit Amount]`
  `[Open Full]` buttons (Commit 7). Mark Paid posts to the
  existing `/transactions/<id>/mark-done` endpoint
  (`app/routes/transactions.py:491`). Edit Amount swaps the
  action bar for an inline amount form posting to
  `/transactions/inline` (`app/routes/transactions.py:909`).
  Open Full opens the bottom-sheet popover. The bottom sheet
  is reserved for "I need the full form." Rationale: 90 % of
  mobile interactions in the user's reported workflow are
  Mark Paid; the in-line bar makes that one tap (card -> bar ->
  Paid) instead of three (card -> sheet -> form button -> save).

- **D-D. Swipe-left on a card reveals a `[Mark Paid]` button;
  the gesture is the shortcut, the button is the commit.** No
  swipe-to-act-on-release. The card translates `-80 px` left
  on a horizontal swipe past the 50 px threshold; the user
  taps the revealed button to commit. Rationale: matches iOS
  Mail's "swipe left to reveal options" pattern (familiar to
  iPhone users) and avoids the false-positive risk of
  commit-on-release (a half-swipe still requires deliberate
  tap to commit). The right-swipe-as-primary convention v2
  proposed is dropped because (a) Firefox iOS uses the right
  edge for back-navigation gesture and (b) v2 has been deferred.

- **D-E. Period selector is a `<select>` jump-to dropdown
  on mobile, not a buttons row.** The current desktop period
  selector at `grid.html:24-49` (3P / 6P / 6M / 1Y / 2Y plus
  `[<] [>]` arrows) is hidden on mobile via `d-none d-md-flex`
  on its container. The mobile "This Period" header adds a
  jump-to `<select>` linking each option to
  `/grid?periods=N&offset=M`. Rationale: a buttons row that wide
  consumes critical vertical space on a 375x812 viewport; a
  native `<select>` opens the iOS picker wheel which is the
  right pattern for "pick one of five" on phone.

- **D-F. Add Transaction modal becomes `modal-fullscreen-sm-down`
  on mobile.** Bootstrap 5 native; no custom CSS. The modal
  takes over the viewport at `<576 px` with the save button
  pinned to the bottom of the modal-footer via `position:
  sticky; bottom: 0` so it remains reachable above the iOS
  keyboard. Rationale: the current modal at `grid.html:303-362`
  centers vertically and gets cut off at the bottom on a 375x812
  viewport when the iOS keyboard is open.

- **D-G. Drag-to-dismiss the bottom sheet, with iOS keyboard
  avoidance via `visualViewport`.** The existing static
  `position: fixed; bottom: 0` sheet at `app.css:821-843`
  gains a 32 x 4 px drag handle (CSS) plus touch handlers
  (`grid_edit.js`) that animate `transform: translateY()`
  while dragging. Drag past ~30 % of `popoverHeight` dismisses;
  release before that snaps back. `visualViewport.resize`
  listener adjusts the sheet's bottom offset so it floats above
  the iOS keyboard. Rationale: matches the iOS-system bottom-
  sheet convention; the no-keyboard-avoidance behavior is a
  reported pain point ("I have to scroll the sheet to find the
  save button when the keyboard is up").

- **D-H. Navbar becomes a Bootstrap offcanvas drawer at `<md`.**
  The existing `navbar-expand-md` collapsing navbar at
  `base.html:39-149` keeps `navbar-expand-md` (so desktop is
  unchanged) but the toggler at `base.html:44` switches from
  `data-bs-toggle="collapse"` to `data-bs-toggle="offcanvas"`
  with `data-bs-target="#mainOffcanvas"`. The nav `<ul>`s
  currently inside `<div class="collapse navbar-collapse"
  id="navMain">` (`base.html:48`) move into a new
  `<div class="offcanvas offcanvas-start" id="mainOffcanvas">`.
  Rationale: the slide-in pattern matches the iOS / Android
  native chrome the user is mentally indexing against; the
  collapse-and-push behavior of the current navbar is the v1
  Bootstrap default and "works" but does not feel mobile.

- **D-I. Service worker caches static assets only; no offline
  HTML, no offline JSON, no offline editing queue.** Phase 5
  Commit 25 adds `app/static/sw.js` and a `/sw.js` passthrough
  route. The fetch handler is **cache-first for URL prefixes
  matching `/static/vendor/*`, `/static/css/*`, `/static/js/*`,
  `/static/img/*`, `/static/fonts/*`, and `/static/manifest.json`**;
  **network-only pass-through (no `respondWith`) for everything
  else.** The `Cache` is named `shekel-static-v1`; the
  `activate` event purges old `shekel-static-*` versions.
  Rationale: any HTML or JSON in the cache is a vector for
  stale balances. The financial-correctness invariant the audit
  closed must not be reopened by a service worker. The
  user-visible benefit (faster load on subsequent visits;
  installability for "Add to Home Screen") is delivered
  without that risk.

- **D-J. PWA installability via manifest audit + maskable
  icons; no separate install-prompt banner.** The existing
  `manifest.json` already supports "Add to Home Screen" on
  iPhone. Commit 27 audits it: confirms `display: standalone`,
  valid 192 / 512 icons, `theme_color`, `name`, `short_name`,
  and adds `purpose: "any maskable"` to icon entries so iOS
  does not crop them. Apple-specific `apple-touch-icon` sizes
  (180 x 180 and 167 x 167) are generated if missing.
  Rationale: the install flow on iPhone is "Share -> Add to
  Home Screen" and the user already knows it; an in-app
  banner would be UI noise.

- **D-K. Settings sidebar becomes a `shekel-scroll-pills` row
  on mobile.** The current sidebar at `settings/dashboard.html`
  uses `col-md-3 col-md-9` split. On mobile the sidebar list
  group wraps in `d-none d-md-block`, and a sibling
  `d-md-none` block at the top renders the same section links
  as a horizontal `shekel-scroll-pills` row (the class is
  already established at `app.css:876-890` per v1 commit
  `463b188`). Rationale: matches the pattern already used by
  loan dashboard tabs and analytics tabs; reuses existing CSS.

- **D-L. Settings sidebar items become individual `dt-d-none
  d-md-block` vs `d-md-none` swaps per list page, not a
  global toggle.** Each list page (`accounts/list.html`,
  `salary/list.html`, `templates/list.html`,
  `transfers/list.html`) gets the same treatment in its own
  commit (Commits 17-20): the existing `<table>` wraps in
  `d-none d-md-block`; a sibling `d-md-none` block renders the
  same rows as Bootstrap cards. Rationale: a single commit
  touching four templates would be reviewable but not bisectable
  if a regression appears; the per-page commit lets the user
  revert one table-to-card conversion independently.

- **D-M. Dashboard mark-paid disposition is asked at Commit 22,
  not decided here.** Memory entry
  `project_dashboard_redesign_or_remove.md` flags the dashboard
  mark-paid feature as redesign-or-remove. Commit 22's
  Section D explicitly asks before editing. Rationale: the user
  has not yet committed to a direction for the dashboard
  mark-paid feature, and unilaterally redesigning it would be
  out of scope.

---

## 3. Discovered refinements beyond the v1 baseline (folded into scope)

Live-code verification of the v1 baseline (`docs/implementation_plan_mobile.md`) and the v2 deferred
plan (deleted but retrievable from git as `git show 35f320a:docs/implementation_plan_mobile_v2.md`)
on 2026-05-23 confirmed every core claim about the current state and surfaced six corrections /
scope expansions. These are folded into the relevant commits, not left as plan-vs-code gaps.

- **R-1. Transaction-matching logic is duplicated in **four**
  blocks, not two.** The v1 baseline's "duplicated between
  desktop and mobile" framing collapsed two predicates per side
  (income and expense) into one. The actual duplication:
  - `grid.html:140-189` (income block, 50 lines)
  - `grid.html:216-263` (expense block, 48 lines)
  - `_mobile_grid.html:64-130` (income block, 67 lines)
  - `_mobile_grid.html:151-216` (expense block, 66 lines)
  All four blocks share the same predicate (`txn.category_id == rk.category_id` +
  `is_income`/`is_expense`
  - `not is_deleted` + `status_id != STATUS_CANCELLED` + the
  template-id-match-or-name-match fork). Phase 1 collapses all four onto one precomputed dict; the
  macros in Commit 1 each call it once per row-key per period.

- **R-2. The `_mobile_grid.html` "MUST be applied to both files"
  warning is at lines 5-7, not "the top of the file".** Verified:

  ```text
  5     IMPORTANT: The transaction matching logic in this template is
  6     duplicated from grid/grid.html.  Any change to matching conditions
  7     MUST be applied to both files to prevent data divergence.
  ```

  Commit 3 removes lines 5-7 once both sites point at the same
  precomputed dict and the macros are the only producer.

- **R-3. The bottom-sheet popover at `app.css:821-843` already
  has `font-size: 16px` on form controls** (verified at
  `app.css:815-819` -- the v1 commit `6b75d10` added this).
  The Phase 2 work does not need to set it; it does need to
  preserve it. The amount inputs across the 10 sites in
  Section 6.4 do NOT yet have `inputmode="decimal"`; that is
  Commit 11's exclusive job.

- **R-4. The companion view at `app/templates/companion/index.html`
  is 60 lines (not 61 per v1 framing) and uses a separate
  `companion/_transaction_card.html` template (94 lines)** that
  shares no markup with the mobile grid's
  `<li class="mobile-txn-card">`. Commit 13 unifies these via
  the shared `render_row_card` macro; the companion-specific
  Mark Paid button at the end of `_transaction_card.html`
  carries forward as a per-card action through the same
  inline action bar from Commit 7.

- **R-5. The v1 plan claimed the matching-loop duplication was
  a "maintenance burden" -- it is also a correctness risk.**
  The four blocks have already drifted in two minor ways since
  v1 shipped:
  - `grid.html:153` includes `title="{{ rk.group_name }}: {{ rk.item_name }}"`
    on the row label; `_mobile_grid.html:86-88` has the group
    header as a `<li>` and never includes the item name.
  - `grid.html:178-179` wraps each matched txn in `<div
    id="txn-cell-{{ txn.id }}">`; `_mobile_grid.html:99` puts
    the id on the `<li>` (`data-mobile-txn-id="{{ txn.id }}"`,
    not the same DOM id pattern).
  Neither drift is a bug today, but they confirm the warning's premise. Commit 1 introduces the macros
  with both DOM-id conventions preserved (the `render_row_cells` macro wraps in
  `<div id="txn-cell-...">`; `render_row_card` uses `data-mobile-txn-id`); Commit 3 removes the
  duplicate matching predicate; Commit 4 removes the warning.

- **R-6. The `scenario-controls-slot` div at `grid.html:6` is
  not dead code.** The v1-era investigation noted it as a
  candidate for removal. Re-reading the comment confirms it is
  a Phase 7 (scenario comparison) placeholder. **Do not touch
  it in this plan.** This is the one negative-scope assertion
  in the plan worth restating because every phase looks at
  `grid.html` and the temptation to clean it up is real.

- **R-7. The companion route is owner-protected at the
  decorator level.** `app/routes/companion.py:78-79` redirects
  owners to `/grid` -- the companion view is **only** rendered
  for users with `role_id == COMPANION`. This means the shared
  `render_row_card` macro must be safe for read-only consumers
  (no controls that an owner has but a companion doesn't).
  Commit 13's macro signature accepts an explicit `can_edit`
  boolean; companion calls pass `False` (omits the inline
  action bar's `[Edit Amount]` and `[Open Full]` buttons,
  keeps `[Mark Paid]` because companions can mark paid per
  the existing entries-blueprint precedent).

- **R-8. The v1 swipe gesture on `mobile_grid.js:47-59` uses
  `passive: true` touch listeners.** Phase 2's swipe-left-to-
  mark-paid (Commit 9) must use the same passive convention;
  passive listeners cannot `preventDefault()`. The card
  swipe-action does not need to preventDefault (it does not
  block vertical scroll; the `Math.abs(dx) > Math.abs(dy)`
  guard ensures horizontal swipes register only when they
  dominate vertical movement). Documenting this explicitly so
  the implementation does not accidentally drop `passive: true`
  trying to "fix" a perceived scroll-block bug that isn't there.

---

## 4. Pattern -> canonical implementation map (the spine of this plan)

Every multi-path pattern collapses onto one component. This table is the contract the commits
implement. A mobile partial that needs one of these patterns reuses the component; it never
re-implements the pattern.

| Pattern | Canonical implementation | First introduced | Reused by |
|---|---|---|---|
| Row matching (one txn per (row_key, period)) | `matched_by_row_period` dict in `app/routes/grid.py::index` (and `app/routes/companion.py::index` via service helper) | Commit 2 | every row render: `render_row_cells` (Commit 1), `render_row_card` (Commit 1) |
| Row -> desktop cells | `render_row_cells` macro in `app/templates/grid/_grid_row_macros.html` | Commit 1 | `grid.html` income (Commit 3), `grid.html` expense (Commit 3) |
| Row -> mobile card | `render_row_card` macro in `app/templates/grid/_grid_row_macros.html` | Commit 1 | `_mobile_grid.html` income/expense (Commit 4), `_mobile_this_period.html` (Commit 6), `_mobile_plan.html` (Commit 7), `companion/index.html` (Commit 13) |
| Swipe gesture utility | `app/static/js/swipe.js::attachSwipeAction` | Commit 13 | mobile grid cards (Commit 9 - inlined initially; refactored to share in Commit 13), companion cards (Commit 13) |
| Drag-to-dismiss bottom sheet + iOS keyboard avoidance | `app/static/js/grid_edit.js` (extended mobile branch of `positionPopover` / `showPopover` / `closeFullEdit`) | Commit 8 | every bottom sheet (transaction full edit, transfer full edit, full create) |
| Per-card inline action bar | `app/templates/grid/_mobile_card_actions.html` | Commit 7 | mobile grid cards (Commit 7), companion cards (Commit 13) |
| `inputmode="decimal"` on monetary inputs | every `<input type="number" step="0.01">` in `app/templates/grid/_transaction_*.html`, `_anchor_edit.html`, `_transaction_entries.html`, `grid.html` Add Transaction modal | Commit 11 | every form that accepts a monetary value |
| Modal fullscreen on mobile | `modal-fullscreen-sm-down` Bootstrap class | Commit 14 | Add Transaction modal (Commit 14); future modals get the class as they're added |
| Settings sidebar -> scroll-pills | `<ul class="nav nav-pills shekel-scroll-pills">` (class exists from v1 commit `463b188`) | Commit 16 | settings (Commit 16); future multi-section pages reuse the class |
| List page -> card layout on mobile | `<table class="d-none d-md-block">` + `<div class="d-md-none">cards</div>` sibling | Commit 17 | accounts (17), salary (18), templates (19), transfers (20), retirement table (21) |
| Navbar -> offcanvas drawer | `data-bs-toggle="offcanvas"` + `<div class="offcanvas offcanvas-start">` | Commit 23 | every page (navbar is in `base.html`) |
| Service worker (static-only) | `app/static/sw.js`, version-keyed cache `shekel-static-v1`, network-only for non-static | Commit 25 | every page (registered globally from `app.js`) |
| SW scope-passthrough route | `@app.route('/sw.js')` in `app/routes/__init__.py` or a small new `app/routes/static_pass.py` | Commit 25 | one-off; required so the SW scopes to `/` not `/static/` |

The single deferred decision, **Q-1** (dashboard mark-paid disposition), is resolved at Commit 22
implementation time via explicit ask. Section 12 carries the open question forward.

---

## 5. Optional enhancements (listed; not in the default commit set unless promoted)

Each is independently valuable, low-risk, and called out so the developer can opt in. The plan flags
exactly which commit would carry each if promoted.

- **OPT-M1. Pull-to-refresh on the mobile grid.** Adds a
  pull-down gesture on `_mobile_grid.html` (or its tab
  containers) that re-fetches the current period's data via
  HTMX. Useful if the data is changed on another device. Adds
  ~50 lines of touch handling to `mobile_grid.js`. Folded as a
  Commit 5 extension if promoted. Behavior change: introduces a
  new gesture; no documented user value beyond the
  multi-device sync case (rare for a solo / spouse pair).

- **OPT-M2. Haptic feedback on swipe commit.** `navigator.vibrate(20)`
  on a successful Mark Paid via swipe-action. Works on Firefox
  Android; silently no-ops on Firefox iOS (WebKit does not
  expose `vibrate`). Folded as a Commit 9 extension if promoted.

- **OPT-M3. Periodic background sync.** Registers `periodicSync`
  in the service worker so the SW pre-warms the next period's
  static assets when the user is online but not on the page.
  Firefox iOS does not ship `periodicSync` (per Web API
  compatibility as of 2026-05); Chrome / Edge do. Listed only;
  do not build until Firefox ships it.

- **OPT-M4. Offline read-only mode.** Cache the last-fetched grid
  HTML and serve it with a prominent "OFFLINE -- last updated
  HH:MM" banner. **Rejected by D-I (no stale financial data) but
  listed so the rejection is documented.** A user-visible offline
  banner that explicitly says "last updated 09:14" might be
  acceptable in principle; it is not promoted here because the
  invariant the financial-calculation audit closed (no stale
  numbers anywhere) is load-bearing.

- **OPT-M5. iOS-Firefox "use Safari to install" deep-link.**
  Detect Firefox iOS and offer a one-tap link to open the app in
  Safari to install. Listed only; the user must re-authenticate
  in Safari after the redirect, which negates most of the
  benefit.

- **OPT-M6. Skeleton screens during HTMX navigation.** Replace
  HTMX's default loading indicator with per-template skeleton
  placeholders. Folded as a Commit 5 extension if promoted;
  ~150 lines of additional CSS/partials.

- **OPT-M7. `share_target` in the PWA manifest.** Lets the user
  "share" a transaction link from another app into Shekel.
  Niche; the user has not requested it. Listed only.

- **OPT-M8. Bottom-tab-bar navigation instead of offcanvas.**
  An iOS-style bottom tab bar with four primary destinations
  (Dashboard, Budget, Companion?, More). Considered during
  planning, rejected via D-H: the offcanvas drawer is the
  Bootstrap-native pattern, lower-risk, and matches the
  collapse-navbar's mental model more closely.

---

## 6. Codebase inventory (files this plan touches)

Re-grep each path at edit time; line numbers below are verified 2026-05-23 against `dev` HEAD
(`207e31c`) and will drift the moment a commit lands.

### 6.1 New files

- `app/templates/grid/_grid_row_macros.html` -- Commit 1.
  Single file holding `render_row_cells` (desktop `<th>` +
  per-period `<td>` row) and `render_row_card` (one mobile
  card). Both reuse `grid/_transaction_cell.html` (87 lines)
  and `grid/_transaction_empty_cell.html` (27 lines) verbatim;
  no inline duplication of the cell HTML.
- `app/templates/grid/_mobile_this_period.html` -- Commit 6.
  Renders ONE period (defaulting to `current_period`) using
  `render_row_card`. Carries the `[<] [>]` arrows and the
  jump-to `<select>`.
- `app/templates/grid/_mobile_plan.html` -- Commit 7. Renders
  the existing multi-period flow distilled. Calls
  `render_row_card`.
- `app/templates/grid/_mobile_card_actions.html` -- Commit 7.
  ~30-line action bar partial. HTMX `hx-post` targets the
  existing mark-done and inline routes; `[Open Full]` triggers
  the bottom-sheet via the same `openFullEdit` JS the desktop
  already uses.
- `app/services/grid_view_service.py` -- Commit 13. Extracts
  `_build_row_keys` (`grid.py:68-162`) and the
  `matched_by_row_period` builder. Pure (no Flask imports).
  Both `app/routes/grid.py` and `app/routes/companion.py` call
  into it.
- `app/static/js/swipe.js` -- Commit 13. ~50 lines. Single
  exported helper `attachSwipeAction(element, { onLeftSwipe,
  threshold = 50 })`. Reused by `mobile_grid.js` (Commit 9
  initially inlines the touch logic, Commit 13 refactors it
  here) and by `companion.js`.
- `app/static/sw.js` -- Commit 25. ~80-line service worker.
- `app/routes/static_pass.py` (or inline in
  `app/routes/__init__.py`) -- Commit 25. Single `@app.route('/sw.js')`
  passthrough required for SW scope-`/`.

### 6.2 Modified routes

- `app/routes/grid.py` (`index` at lines 165-389). Commit 2 adds
  the `matched_by_row_period` precomputation after the existing
  `txn_by_period` build (lines 267-269). Commit 13 replaces the
  in-route precomputation with a call into
  `grid_view_service`.
- `app/routes/companion.py` (`index` at 82-123, `period_view`
  at 126-163). Commit 13 adds calls to the new
  `grid_view_service` helpers and passes the same context shape
  to the shared partial.

### 6.3 Modified templates

- `app/templates/grid/grid.html` (363 lines). Commit 3 replaces
  the inline matching loops in the income block (lines 140-189)
  and expense block (lines 216-263) with `render_row_cells`
  calls. Commit 14 converts the Add Transaction modal
  (lines 303-362) to `modal-fullscreen-sm-down`. **Untouched:**
  the `scenario-controls-slot` placeholder at line 6 (R-6).
- `app/templates/grid/_mobile_grid.html` (250 lines). Commit 4
  replaces the matching loops in the income block (lines 64-130)
  and expense block (lines 151-216) with `render_row_card`
  calls and removes the warning comment at lines 5-7. Commit 5
  rewrites the entire file as a tab container with two
  `tab-pane` children that include `_mobile_this_period.html`
  and `_mobile_plan.html`.
- `app/templates/companion/index.html` (60 lines). Commit 13
  replaces the inline card loop (lines 36-38) with an
  `_mobile_this_period.html` include.
- `app/templates/companion/_transaction_card.html` (94 lines).
  Commit 13: deprecated; its Mark Paid button moves into the
  shared `_mobile_card_actions.html` (parameterized by
  `can_edit=False` for companion).
- `app/templates/grid/_transaction_full_edit.html`,
  `_transaction_full_create.html`, `_transaction_quick_edit.html`,
  `_transaction_quick_create.html`, `_anchor_edit.html`,
  `_transaction_entries.html` -- Commit 11 adds
  `inputmode="decimal"` to the 10 monetary input sites listed
  in Section 6.4.
- `app/templates/settings/dashboard.html`. Commit 16 wraps the
  sidebar in `d-none d-md-block` and adds the `d-md-none`
  `shekel-scroll-pills` row.
- `app/templates/accounts/list.html`, `salary/list.html`,
  `templates/list.html`, `transfers/list.html` -- Commits 17,
  18, 19, 20. Each gets `d-none d-md-block` on the existing
  table plus a `d-md-none` card list.
- `app/templates/retirement/_retirement_account_table.html` --
  Commit 21. Card layout + Bootstrap popovers replacing
  `title=""` tooltips (the pattern v1 commit `921de65`
  established for retirement info icons).
- `app/templates/loan/_schedule.html` -- Commit 22 (mostly;
  see D-M for the broader dashboard ask). `d-none d-lg-table-cell`
  on Escrow / Extra / Rate columns -- the
  `mobile_friendliness_assessment.md:240` line that v1 did not
  complete.
- `app/templates/dashboard/dashboard.html` -- Commit 22. Audits
  `col-lg-*` splits for mobile stacking order; Bills Due gets
  `order-first order-lg-N`. The dashboard mark-paid feature
  itself is decided at Commit 22 review (D-M).
- `app/templates/base.html` -- Commit 23 (navbar -> offcanvas),
  Commit 24 (manifest review touchpoints if needed), Commit 25
  (no JS-tag change required for SW registration -- the
  existing `<script src="js/app.js">` at `base.html:287` is
  the registration host).
- `app/templates/analytics/analytics.html` -- Commit 24. Audits
  Chart.js `maintainAspectRatio: false` and container
  `min-height` on each tab partial.
- `app/templates/loan/dashboard.html` -- Commit 24. The
  rate-history sub-table gets the card-on-mobile treatment.
- `app/templates/retirement/dashboard.html`,
  `investment/dashboard.html`,
  `debt_strategy/dashboard.html` -- Commit 24. Verify the
  v1 width-removal fixes are still in place; no regression.

### 6.4 The 10 monetary input sites (Commit 11 exhaustive list)

Each is a `<input type="number" step="0.01">` today; Commit 11 adds `inputmode="decimal"` to each.
The desktop is unaffected (the attribute is ignored on non-touch devices); iOS gains the
decimal-bearing numeric keypad. The 10 sites are:

1. `app/templates/grid/_transaction_full_edit.html:29` (`estimated_amount`)
2. `app/templates/grid/_transaction_full_edit.html:38` (`actual_amount`)
3. `app/templates/grid/_transaction_full_create.html:36` (`estimated_amount`)
4. `app/templates/grid/_transaction_full_create.html:44` (`actual_amount`)
5. `app/templates/grid/_transaction_quick_edit.html:14` (`amount`)
6. `app/templates/grid/_transaction_quick_create.html:23` (`amount`)
7. `app/templates/grid/_anchor_edit.html:25` (`anchor_balance`)
8. `app/templates/grid/_transaction_entries.html:43` (entry create `amount`)
9. `app/templates/grid/_transaction_entries.html:147` (entry edit `amount`)
10. `app/templates/grid/grid.html:324` (Add Transaction modal `estimated_amount`)

Section 4 in the row "inputmode" claims the same scope; this table is the authoritative re-grep
target. Any monetary input added between plan-write and commit-land is added to the list during
Commit 11 implementation and noted in the commit work summary.

### 6.5 Modified JS

- `app/static/js/mobile_grid.js` (85 lines today). Commits 5,
  7, 9. Tab-routing for the new "This Period" / "Plan"
  structure; per-card inline action-bar wiring; swipe-left-to-
  mark-paid handler (inlined initially, Commit 13 factors out
  to `swipe.js`).
- `app/static/js/grid_edit.js` (583 lines today). Commit 8.
  Bottom-sheet drag handle + drag-to-dismiss + visualViewport
  keyboard avoidance. Mobile branch only (`window.innerWidth
  < 768` gate); desktop unaffected.
- `app/static/js/companion.js` (25 lines today). Commit 13.
  Adopts the shared `swipe.js` helper for the same swipe-left-
  to-mark-paid pattern.
- `app/static/js/app.js`. Commit 25. Adds `if ('serviceWorker'
  in navigator) { window.addEventListener('load', function () {
  navigator.serviceWorker.register('/sw.js').catch(function
  () {}); }); }` at the top, inside a feature-check guard.

### 6.6 Modified CSS

- `app/static/css/app.css` (1417 lines today). Commits 8, 9,
  14, 23. Drag-handle (32 x 4 pill, `.bottom-sheet-handle`);
  `.dragging` modifier; swipe-action reveal (`.mobile-txn-card.swiped`
  - `.swipe-action-mark-paid`); modal-fullscreen-sm-down
  body/footer rules; offcanvas drawer styling (280 px width, drop-shadow, `>=44 px` nav items). All
  new rules live inside existing media-query blocks where applicable; no new breakpoints (the existing
  `<768 px`, `<576 px`, `<360 px` remain the only ones).

### 6.7 Modified static config

- `app/static/manifest.json`. Commit 27. Audit + `purpose: "any
  maskable"` on icon entries.
- `app/static/img/`. Commit 27. Add `icon-180.png` and
  `icon-167.png` if `apple-touch-icon` resolves to a missing
  file (v1 created 192 and 512; Apple-specific 180 and 167 are
  the iPhone sizes per Apple HIG).

### 6.8 Tests

- `tests/test_routes/test_grid.py` -- targeted runs in Commits
  2, 3, 4, 5, 6, 7, 8, 9, 11. No new assertions; the existing
  suite must stay green.
- `tests/test_routes/test_companion.py` -- targeted runs in
  Commits 13, 14. No new assertions; the existing suite must
  stay green.
- `tests/test_routes/test_transactions.py` -- targeted runs in
  Commits 7, 11 (the `mark-done` and `inline` routes are
  consumed by new mobile partials but not changed). No new
  assertions.
- `tests/test_routes/test_settings.py`,
  `test_accounts.py`, `test_salary.py`, `test_templates.py`,
  `test_transfers.py`, `test_retirement.py`,
  `test_loan.py`, `test_dashboard.py` -- targeted runs in the
  matching Phase 4 commit (16-22).
- `tests/test_static_guards.py` -- Commits 2 and 13 keep the
  guard green (the new precomputation does not introduce any
  `Account.current_anchor_*` or `LoanParams.current_principal`
  reads).

No new test files. The plan is template / CSS / JS-heavy; the project has no touch-event test
infrastructure, and adding one is explicitly out of scope (Section 14, "Out of scope" item 6).

---

## 7. Commit dependency analysis

```text
Phase 1 -- DRY foundation
  1 add macros (no callers) ───────────┐
  2 precompute matched_by_row_period ──┤ (no consumer yet)
  3 grid.html uses render_row_cells ───┤ (desktop no visible change)
  4 _mobile_grid.html uses render_row_card +
    remove duplicate-warning comment ──┘ (mobile no visible change)
        |
        +--> blocks Phase 2 (macros + dict)

Phase 2 -- Mobile grid UX rewrite
  5 _mobile_grid.html tab scaffold ────┐
  6 _mobile_this_period.html partial ──┤
  7 _mobile_plan.html + _mobile_card_actions.html +
    mobile_grid.js inline action-bar wiring ──┤
  8 grid_edit.js drag-handle + drag-to-dismiss +
    visualViewport keyboard avoidance ──┤
  9 mobile_grid.js swipe-left-to-mark-paid ──┤
 10 _mobile_this_period.html jump-to <select> ──┤
 11 inputmode="decimal" sweep (10 sites) ───┘
        |
        +--> blocks Phase 3 commits 13, 14 (shared partial + modal)

Phase 3 -- Companion + grid-adjacent forms
 12 mobile bottom-sheet sticky action footer (_transaction_full_edit) ─┐
 13 grid_view_service extract + companion/index uses partial +
    swipe.js shared module + companion.js adoption ──┤
 14 Add Transaction modal -> modal-fullscreen-sm-down ──┘
        |
        +-- independent of P4, P5

Phase 4 -- Settings + list pages (independent of P3, can land in parallel)
 15 (none -- pure setup; settings audit happens in 16)
 16 settings/dashboard.html sidebar -> shekel-scroll-pills on mobile ─┐
 17 accounts/list.html card-on-mobile ──┤
 18 salary/list.html card-on-mobile ──┤
 19 templates/list.html card-on-mobile ──┤
 20 transfers/list.html card-on-mobile ──┤
 21 retirement/_retirement_account_table card-on-mobile + popover tooltips ──┤
 22 dashboard.html mobile ordering + dashboard mark-paid disposition (ASK) +
    loan/_schedule.html column hides ──┘
        |
        +-- independent of P5

Phase 5 -- Nav offcanvas + remaining dashboards + service worker
 23 base.html navbar -> offcanvas drawer ─┐
 24 analytics/loan/retirement/investment/debt_strategy
    dashboards mobile audit ──┤
 25 sw.js + /sw.js route + app.js SW registration ──┤
 26 (gap reserved for any cross-phase regression)
 27 manifest.json + Apple-specific icon sizes ──┘
        |
        +--> final gate

 28 chore(release): full gate + verification appendix
```

Ordering rationale:

- **Phase 1 (Commits 1-4) is a pure refactor.** Each commit
  leaves the suite green by construction. Commits 1-2 add
  no callers (the macros sit alongside the inline loops; the
  precomputed dict sits alongside the per-Jinja-iteration
  matching). Commits 3-4 switch each side to the new producer;
  the byte-equivalence verification in Section F catches any
  output drift.
- **Phase 2 (Commits 5-11) is the headline mobile work.** No
  Phase 2 commit lands a behavior change behind a feature flag;
  each is independently revertable. The tab scaffold (5)
  defaults to the current `_mobile_grid.html` content in one
  tab so the mobile UX is unchanged until Commit 6 introduces
  the new partial. Commit 11 is independent of the others and
  can be picked up at any point in Phase 2.
- **Phase 3 (Commits 12-14) depends on Phase 2's macros + the
  inline action-bar markup**, so it cannot ship before Phase 2
  is done.
- **Phase 4 (Commits 16-22) is independent of Phases 2-3.**
  None of the list-page card conversions or the settings
  sidebar pill conversion depends on the grid mobile rewrite.
  They could ship before, after, or in parallel with Phases
  2-3 from a CI / merge perspective. The plan orders them
  fourth because the user prioritized the grid, but the
  sequence is not technically binding.
- **Phase 5 (Commits 23-27) closes the loop.** Navbar offcanvas
  (23) is independent of all other commits; service worker
  (25) depends on no prior commit but should land late because
  the static caching exposes any new `/static/*` asset added
  in earlier phases (the cache miss penalty is paid on first
  load after SW activation).
- **Commit 28 is the gate.** Full suite, pylint, manual
  verification across the entire app at both viewports, plus
  the verification appendix in Section 11.

Every commit leaves the suite green. Phase 1 commits do not change visible output (byte-equivalence
is the gate). Phase 2-5 commits change visible output along their stated axis; their Section E test
plans assert the change.

---

## 8. Commit checklist

| # | Commit message | Summary |
|---|---|---|
| 1 | `feat(grid): add render_row_cells + render_row_card macros` | New `_grid_row_macros.html` with two macros; no callers yet; no visible change |
| 2 | `feat(grid): precompute matched_by_row_period in index route` | Adds `matched_by_row_period` dict to grid.index context; no consumer yet |
| 3 | `refactor(grid): desktop grid uses render_row_cells macro` | `grid.html` income + expense blocks call the macro; HTML byte-equivalent on desktop |
| 4 | `refactor(grid): mobile grid uses render_row_card macro` | `_mobile_grid.html` income + expense blocks call the macro; warning comment at lines 5-7 removed; HTML byte-equivalent on mobile |
| 5 | `feat(mobile-grid): nav-pills tab scaffold for This Period / Plan` | `_mobile_grid.html` becomes a tab container; both tabs render the existing flow until Commits 6-7 populate them |
| 6 | `feat(mobile-grid): _mobile_this_period.html partial with arrows` | New partial; "This Period" tab populates from it; default to current period |
| 7 | `feat(mobile-grid): _mobile_plan.html + inline card action bar` | "Plan" tab populates with the existing flow; per-card action bar (Mark Paid / Edit Amount / Open Full) |
| 8 | `feat(mobile-grid): bottom-sheet drag-to-dismiss + iOS keyboard avoidance` | `grid_edit.js` mobile branch gets drag handle + `visualViewport.resize` listener |
| 9 | `feat(mobile-grid): swipe-left reveals Mark Paid button on cards` | Card translates -80 px; reveal-button is the commit; threshold 50 px |
| 10 | `feat(mobile-grid): jump-to period <select> in This Period header` | Native `<select>` linking to /grid?periods=N&offset=M |
| 11 | `feat(forms): inputmode="decimal" on 10 monetary inputs` | iOS numeric keypad with decimal point on all monetary inputs |
| 12 | `feat(mobile-sheet): sticky action footer in full-edit popover` | `position: sticky; bottom: 0` action bar inside `_transaction_full_edit.html` |
| 13 | `refactor(grid): extract grid_view_service + companion uses This Period partial + swipe.js shared` | `_build_row_keys` + `matched_by_row_period` builder move to a pure service module; companion/index renders `_mobile_this_period.html`; swipe logic shared |
| 14 | `feat(mobile-modal): Add Transaction modal-fullscreen-sm-down` | Bootstrap class only; save button sticky to bottom |
| 15 | (reserved -- not used; renumber if a cross-phase fix needs a commit slot) | |
| 16 | `feat(mobile-settings): sidebar -> shekel-scroll-pills on mobile` | `settings/dashboard.html` mobile-only pills row |
| 17 | `feat(mobile-accounts): cards on mobile in accounts/list.html` | `d-none d-md-block` on the table, `d-md-none` card list sibling |
| 18 | `feat(mobile-salary): cards on mobile in salary/list.html` | Same pattern; preserves the v1 mobile action dropdown |
| 19 | `feat(mobile-templates): cards on mobile in templates/list.html` | Same pattern |
| 20 | `feat(mobile-transfers): cards on mobile in transfers/list.html` | Same pattern |
| 21 | `feat(mobile-retirement): cards + popover tooltips on retirement account table` | Card layout; replace `title=""` with Bootstrap popovers per v1 commit `921de65` |
| 22 | `feat(mobile-dashboard): order Bills Due first + loan schedule column hides + (decide mark-paid disposition)` | `order-first order-lg-N` on Bills Due; `d-none d-lg-table-cell` on loan schedule Escrow/Extra/Rate; user-decided action on dashboard mark-paid |
| 23 | `feat(mobile-nav): navbar -> offcanvas drawer at <md` | `data-bs-toggle="offcanvas"` + offcanvas-start; offcanvas styling at >=44 px items |
| 24 | `refactor(mobile-dashboards): analytics/loan/retirement/investment/debt audit` | Chart container min-height; rate-history card layout; verify v1 width-removal still in place |
| 25 | `feat(pwa): service worker + /sw.js passthrough route + registration` | Static-only cache `shekel-static-v1`; network-only for HTML/JSON; SW registered from `app.js` |
| 26 | (reserved for cross-phase regression fix discovered during 23-25) | |
| 27 | `feat(pwa): manifest maskable icons + Apple-specific 180/167 sizes` | `manifest.json` audit; `purpose: "any maskable"` on icons; Apple sizes |
| 28 | `chore(release): mobile v3 full gate + verification appendix` | Final full suite + pylint + manual verification across both viewports |

Commits 15 and 26 are reserved (no work assigned; renumber if needed). The plan assumes 26
productive commits; the reservations give room for an in-flight regression fix without renumbering
the whole sequence.

---

## 9. Commits (detailed)

Each commit follows the house format: **A.** commit message, **B.** problem statement, **C.** files
modified, **D.** implementation approach (with code blocks where relevant), **E.** test cases,
**F.** manual verification steps, **G.** downstream effects, **H.** rollback notes. Test IDs are
`C<commit>-<n>`. "Re-pinned tests" follows CLAUDE.md rule 5 only; for this plan the answer is "none"
on every commit because no financial assertion changes (the precomputation in Commit 2 is
byte-identical in output).

### Commit 1 -- add render_row_cells + render_row_card macros

**A. Commit message** `feat(grid): add render_row_cells + render_row_card macros`

**B. Problem statement** Transaction matching is duplicated across four blocks today (R-1):
`grid.html:140-189` (income), `grid.html:216-263` (expense), `_mobile_grid.html:64-130` (income),
`_mobile_grid.html:151-216` (expense). The first half of the Phase 1 fix is to introduce two macros
that each do the matching ONCE per row-key and ONCE per period, reusing the existing cell partials.
The macros sit alongside the inline loops in this commit; Commits 3 and 4 switch the templates to
call them.

## C. Files modified

- `app/templates/grid/_grid_row_macros.html` (NEW). Two macros:
  - `render_row_cells(rk, periods, matched_by_row_period,
    entry_sums, txn_type_id, account, today)` -- emits the
    desktop `<th>` row label plus one `<td class="text-end
    cell">` per period. Uses
    `matched_by_row_period[(rk.category_id, rk.template_id,
    rk.txn_name, period.id)]` (default `[]`) to drive the
    cell content. Renders `_transaction_cell.html` for matched
    transactions and `_transaction_empty_cell.html` for the
    empty case. Wraps each matched txn in
    `<div id="txn-cell-{{ txn.id }}">` matching the current
    `grid.html:177-178` convention.
  - `render_row_card(rk, period, matched_by_row_period,
    entry_sums, can_edit=True)` -- emits one `<li
    class="list-group-item d-flex justify-content-between
    align-items-center py-2 px-3 mobile-txn-card"
    data-mobile-txn-id="{{ txn.id }}" ...>` per matched
    transaction in the current period (matching the current
    `_mobile_grid.html:99-127` shape). `can_edit=False` (used
    by companion in Commit 13) drops the data attributes that
    open the bottom sheet but keeps the Mark Paid path.

**D. Implementation approach** Re-read all four duplicated blocks in full before writing the macros.
The macros' bodies are extracted as-is from the current Jinja -- no rewrite from scratch (CLAUDE.md
rule 10). The cell-render path stays identical:

```jinja
{# render_row_cells: desktop row #}
{% macro render_row_cells(rk, periods, matched_by_row_period, entry_sums, txn_type_id, account, today) %}
  <tr>
    <th scope="row" class="sticky-col row-label"
        title="{{ rk.group_name }}: {{ rk.item_name }}">
      {{ rk.display_name }}
    </th>
    {% for period in periods %}
      {% set matched = matched_by_row_period.get(
          (rk.category_id, rk.template_id, rk.txn_name, period.id), []
      ) %}
      <td class="text-end cell">
        {% if matched %}
          {% for txn in matched %}
            <div id="txn-cell-{{ txn.id }}">
              {% set found = namespace(txn=txn) %}
              {% include "grid/_transaction_cell.html" with context %}
            </div>
          {% endfor %}
        {% else %}
          {% set category = rk.category %}
          {% include "grid/_transaction_empty_cell.html" with context %}
        {% endif %}
      </td>
    {% endfor %}
  </tr>
{% endmacro %}
```

Mobile macro: same matching dict, one period only. The `can_edit=False` branch drops the
`data-mobile-txn-id` attribute (prevents bottom-sheet open) but the existing companion
`_transaction_card.html` Mark Paid button is preserved (Commit 13 unifies this through
`_mobile_card_actions.html` -- for Commit 1, the macro emits the same DOM the existing
`_mobile_grid.html` does).

The macros are placed in ONE file (`_grid_row_macros.html`) so both are visible together (the
reference plans' pattern: one file per related-macro pair). The Jinja
`{% from "grid/_grid_row_macros.html" import render_row_cells, render_row_card %}` import lands in
`grid.html` (Commit 3) and `_mobile_grid.html` (Commit 4); not yet in this commit.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C1-1 | `test_macros_file_exists` | grep | filesystem check `ls app/templates/grid/_grid_row_macros.html` | file exists |
| C1-2 | `test_macros_export_both_names` | grep | `grep -E "^{% macro (render_row_cells\|render_row_card)" app/templates/grid/_grid_row_macros.html` | two matches |
| C1-3 | `test_no_new_jinja_errors_on_grid_render` | logged-in owner user with periods + transactions | GET `/grid` | 200 status, response renders (current inline loops still drive output) |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. `pylint app/ --fail-on=E,F` clean (this commit does not
   touch Python; runs as a safety check).
3. `grep -nE "{% macro (render_row_cells|render_row_card)\b"
   app/templates/grid/_grid_row_macros.html` returns two lines.
4. Manually load `/grid` on desktop (1920x1080) -- HTML output
   identical to pre-commit (the macros exist but no template
   imports them yet).

**G. Downstream effects** Pure addition. No template imports the macros. Commit 3 and Commit 4 will
be the first consumers.

**H. Rollback notes** Delete `_grid_row_macros.html`. Suite stays green; no behavior change to
revert.

---

### Commit 2 -- precompute matched_by_row_period in index route

**A. Commit message** `feat(grid): precompute matched_by_row_period in index route`

**B. Problem statement** The matching predicate that Commit 1's macros expect lives in Python today
only as a hand-extracted mirror inside the Jinja loops. Commit 2 produces the dict
`matched_by_row_period` once in the route, mirroring the predicate text-for-text. The macros (Commit

1) read from this dict; no consumer rewires yet (Commit 3 and Commit 4 are the consumers).

## C. Files modified

- `app/routes/grid.py` (`index` at lines 165-389; the
  precomputation lands after the existing `txn_by_period`
  build at lines 267-269 and before the `entry_sums` build at
  line 274). Pass `matched_by_row_period` as a new
  `render_template` context entry (alongside `txn_by_period`).

**D. Implementation approach** Re-grep `grid.py:267-274` to confirm current line numbers. The
predicate is taken verbatim from `grid.html:162-172` (income) and `:235-245` (expense), unified into
one expression. Use `is_cancelled` from `app/utils/balance_predicates.py` (already imported at
`grid.py:29` and used by `_build_row_keys` at `grid.py:118`) -- do NOT compare against
`STATUS_CANCELLED` directly in Python (this is the only place the predicate could drift from the
template's `status_id != STATUS_CANCELLED` check; aligning on `is_cancelled` ensures both sides
route through the same ref-cache helper).

The dict key is the four-tuple `(category_id, template_id, txn_name, period_id)`. The matching
predicate is the same fork the Jinja loop uses: template-id-match takes precedence when both the
row-key and the txn have a template_id; otherwise name-match. `is_income` vs `is_expense` is
determined by the section the row-key came from -- both `_build_row_keys` calls in the route (one
for income, one for expense) produce disjoint row-key sets, so the macro can filter using
`txn.is_income` (income macro) or `txn.is_expense` (expense macro) at render time without needing
two separate dicts. Concretely, ONE dict suffices because each row-key's `category_id` already
encodes income-vs-expense at the category level (Categories are typed); the `txn.is_income` /
`txn.is_expense` predicate in the Jinja loop is therefore a redundant safety check that the
precomputation preserves.

Pseudocode:

```python
from app.utils.balance_predicates import is_cancelled

# After txn_by_period build at lines 267-269:
matched_by_row_period: dict[
    tuple[int, int | None, str, int], list[Transaction]
] = {}

for row_keys, is_income_section in (
    (income_row_keys, True),
    (expense_row_keys, False),
):
    for rk in row_keys:
        for period in periods:
            matched: list[Transaction] = []
            for txn in txn_by_period.get(period.id, []):
                if txn.category_id != rk.category_id:
                    continue
                if is_income_section and not txn.is_income:
                    continue
                if not is_income_section and not txn.is_expense:
                    continue
                if txn.is_deleted or is_cancelled(txn):
                    continue
                if rk.template_id is not None and txn.template_id is not None:
                    if txn.template_id != rk.template_id:
                        continue
                else:
                    if txn.name != rk.txn_name:
                        continue
                matched.append(txn)
            if matched:
                matched_by_row_period[
                    (rk.category_id, rk.template_id, rk.txn_name, period.id)
                ] = matched
```

Pass to template:

```python
return render_template(
    "grid/grid.html",
    # ... existing context ...
    matched_by_row_period=matched_by_row_period,
    # ... existing context ...
)
```

`pylint app/ --fail-on=E,F` clean. No new imports beyond `is_cancelled` (already imported).

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C2-1 | `test_index_renders_with_new_context` | logged-in owner user with periods + transactions | GET `/grid` | 200 status; existing assertions pass |
| C2-2 | `test_matched_by_row_period_in_context` | same | mock `render_template` and inspect kwargs | `matched_by_row_period` is a dict, keys are 4-tuples, values are non-empty lists |
| C2-3 | `test_matched_dict_mirrors_jinja_predicate` | seeded txns spanning multiple categories, templates, and one cancelled txn | inspect `matched_by_row_period` | dict contains every (rk, period) pair the Jinja loops would match; no pair where the matched txn is cancelled or deleted |
| C2-4 | `test_no_balance_resolver_reads` | grep | `grep -nE "current_anchor_(balance\|period_id)" app/routes/grid.py` | no NEW reads (Rule 2 guard) |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. `pylint app/ --fail-on=E,F` clean.
3. `grep -nE "current_anchor_(balance|period_id)\|current_principal\|interest_rate"
   app/routes/grid.py` shows no new reads vs. baseline.
4. Manually load `/grid` on desktop -- HTML output identical
   to pre-commit (the precomputed dict exists in context but
   no template reads it yet; Jinja-pass over unused context
   values is a no-op).

**G. Downstream effects** Pure addition to route context. No template change yet. Commit 3 (desktop)
and Commit 4 (mobile) are the consumers.

**H. Rollback notes** Delete the precomputation block and the `matched_by_row_period=` keyword in
`render_template`. Suite stays green.

---

### Commit 3 -- desktop grid uses render_row_cells macro

**A. Commit message** `refactor(grid): desktop grid uses render_row_cells macro`

**B. Problem statement** With macros and the precomputed dict in place (Commits 1 + 2),
`grid.html`'s income and expense blocks can replace their inline matching loops with calls to
`render_row_cells`. The output must be byte-identical on desktop -- this is the zero-visible-change
refactor that locks the Phase 1 invariant.

## C. Files modified

- `app/templates/grid/grid.html` (363 lines). Replace lines
  140-189 (income inline loop + cell rendering) with a single
  `{% from "grid/_grid_row_macros.html" import render_row_cells %}`
  at the top of the `{% block content %}` plus a per-row
  `{{ render_row_cells(rk, periods, matched_by_row_period,
  entry_sums, TXN_TYPE_INCOME, account, today) }}` call inside
  the existing `{% for rk in income_row_keys %}` loop (lines
  139-140). Same treatment for expense (lines 216-263 ->
  per-row macro call inside `{% for rk in expense_row_keys %}`
  at line 215).
- The `group_name` banner row logic (lines 141-149 and 218-226
  -- `{% if rk.group_name != ns.current_group %}` etc.) stays
  inline. The banner is not duplicated; it's part of the
  iteration logic that the macro does not own.

**D. Implementation approach** Re-grep `grid.html:140-189` and `:216-263` to confirm current line
numbers (they will not have drifted within this commit's scope but will after Commit 4). The
replacement:

```jinja
{# Top of the block, after the include for grid/_anchor_edit.html: #}
{% from "grid/_grid_row_macros.html" import render_row_cells %}

{# Income section, current line 140 (verbatim outer loop): #}
{% set ns = namespace(current_group='') %}
{% for rk in income_row_keys %}
  {% if rk.group_name != ns.current_group %}
    {% set ns.current_group = rk.group_name %}
    <tr class="group-header-row">
      <td class="sticky-col text-muted small fw-semibold"
          colspan="{{ periods|length + 1 }}">
        {{ rk.group_name }}
      </td>
    </tr>
  {% endif %}

  {# OLD: <tr>...inline matching loop...</tr> (lines 151-189) #}
  {# NEW: #}
  {{ render_row_cells(rk, periods, matched_by_row_period,
                       entry_sums, TXN_TYPE_INCOME, account, today) }}
{% endfor %}
```

Same shape for the expense section. The `Total Income` / `Total Expenses` / `Net Cash Flow` subtotal
rows (lines 193-201, 266-274, 277-288) stay inline (they read from `subtotals[period.id]`, not from
the precomputed matching dict).

The `TXN_TYPE_INCOME` / `TXN_TYPE_EXPENSE` Jinja globals are already injected (verified at the grid
route's `render_template` call). The macro accepts them and uses them only for the empty-cell
render's hidden input (`_transaction_empty_cell.html:13`).

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C3-1 | `test_grid_html_renders_byte_equivalent_desktop` | known fixture: owner with periods, transactions across income + expense, no cancelled txns | GET `/grid` and capture HTML | response body byte-identical (modulo Jinja-whitespace differences from macro call expansion) to a pre-Commit-3 snapshot |
| C3-2 | `test_grid_html_renders_with_cancelled_txn_skipped` | fixture with one cancelled txn in the visible window | GET `/grid` | cancelled txn does NOT appear in any cell |
| C3-3 | `test_grid_html_renders_with_template_id_match` | fixture: one txn with template_id matching a row-key | GET `/grid` | txn renders in the matching cell |
| C3-4 | `test_grid_html_renders_with_name_match_fallback` | fixture: one txn with no template_id, name matches a row-key | GET `/grid` | txn renders in the matching cell |
| C3-5 | `test_grid_html_renders_empty_cells_when_no_match` | fixture: a row-key with no matching txn in some periods | GET `/grid` | empty cells render `_transaction_empty_cell.html` with the row's category |
| C3-6 | `test_grid_html_no_double_render` | fixture: ensure no txn appears twice | GET `/grid` | each txn's cell id (`txn-cell-{{ id }}`) appears exactly once in the response |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. `pylint app/ --fail-on=E,F` clean.
3. Capture rendered HTML of `/grid` at 1920x1080 pre-commit (a
   diff target). After this commit, re-render and diff. The
   only acceptable differences are whitespace artifacts from
   the macro expansion (Jinja inserts a newline at the macro
   call site; the macro body's indentation differs from the
   inline indentation by a fixed offset). Numbers, status
   badges, progress indicators, balance row, subtotals all
   byte-identical.
4. `grep -nE "{% for txn in period_txns %}" app/templates/grid/grid.html`
   returns no matches (the inline matching loops are gone).

**G. Downstream effects** Desktop grid renders via the macro. Mobile is still on the inline loop
(Commit 4 fixes that). Commits 5+ depend on this commit because the macro is the producer for the
new mobile partials.

**H. Rollback notes** Restore the inline loops at lines 140-189 and 216-263. The macro file and the
precomputed dict stay (they're harmless without callers).

---

### Commit 4 -- mobile grid uses render_row_card macro

**A. Commit message** `refactor(grid): mobile grid uses render_row_card macro`

**B. Problem statement** Symmetric to Commit 3 for the mobile partial. The warning comment at
`_mobile_grid.html:5-7` is removed once both sides point at the same precomputed dict and the macro
is the sole producer.

## C. Files modified

- `app/templates/grid/_mobile_grid.html` (250 lines). Remove
  the warning at lines 5-7. Replace the income matching loop
  at lines 64-130 with a per-row `{{ render_row_card(rk,
  period, matched_by_row_period, entry_sums) }}` call inside
  the existing `{% for rk in income_row_keys %}` loop. Same
  for expense (lines 151-216). Group-header `<li>` rows
  (lines 84-89 and 171-176) stay inline.

**D. Implementation approach** Re-grep `_mobile_grid.html:5-7, 64-130, 151-216` to confirm current
line numbers. The replacement mirrors Commit 3 structurally:

```jinja
{# Top of the file, replacing lines 5-7 IMPORTANT block: #}
{% from "grid/_grid_row_macros.html" import render_row_card %}

{# Income section inside the period panel (verbatim outer loop): #}
{% set ns_inc = namespace(current_group='') %}
{% for rk in income_row_keys %}

  {# OLD: lines 65-81 inline matching predicate -- REMOVED #}

  {% set match_key = (rk.category_id, rk.template_id, rk.txn_name, period.id) %}
  {% set matched = matched_by_row_period.get(match_key, []) %}
  {% if matched %}
    {% if rk.group_name != ns_inc.current_group %}
      {% set ns_inc.current_group = rk.group_name %}
      <li class="list-group-item py-1 px-3 text-muted small fw-semibold text-uppercase mobile-group-header">
        {{ rk.group_name }}
      </li>
    {% endif %}
    {% for txn in matched %}
      {# OLD: lines 91-127 inline card markup #}
      {# NEW: #}
      {{ render_row_card(rk, period, matched_by_row_period, entry_sums) }}
    {% endfor %}
  {% endif %}
{% endfor %}
```

Note: the macro iterates `matched` internally if we want to keep one call per row-key;
alternatively, the caller iterates and passes one txn at a time. The first form keeps the call site
simpler; the second form lets the caller decide whether to wrap or not. The plan picks the first
form: the macro iterates `matched_by_row_period.get(match_key, [])` itself, so the caller is one
call per (rk, period). This matches Commit 1's macro signature.

Adjust the macro signature in Commit 1 if the iteration model landed differently. The implementation
surfaces this during Commit 1's authoring; if drift exists, re-write Commit 1's macro to match the
call site here.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C4-1 | `test_mobile_grid_html_renders_byte_equivalent_mobile` | mobile viewport, same fixtures as C3-1 | GET `/grid` (response includes `_mobile_grid.html` via the include) | mobile section byte-identical to pre-Commit-4 snapshot |
| C4-2 | `test_mobile_grid_warning_comment_removed` | grep | `grep -n "MUST be applied to both files" app/templates/grid/_mobile_grid.html` | no matches |
| C4-3 | `test_mobile_grid_matching_loop_removed` | grep | `grep -nE "{% for txn in period_txns %}" app/templates/grid/_mobile_grid.html` | no matches |
| C4-4 | `test_mobile_grid_renders_with_cancelled_txn_skipped` | same as C3-2 | render | cancelled txn does NOT appear |
| C4-5 | `test_mobile_grid_card_data_attrs_preserved` | known txn | render | each card has `data-mobile-txn-id`, `role="button"`, `aria-label` matching the pre-commit shape |
| C4-6 | `test_mobile_grid_renders_with_transfer_shadow` | known transfer-shadow txn | render | card has `data-mobile-xfer-id` attribute (the transfer-shadow special case at `_mobile_grid.html:101`) |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. `pylint app/ --fail-on=E,F` clean.
3. Capture rendered HTML of `/grid` at 375x812 (Firefox
   responsive mode, iPhone XS) pre-commit. After this commit,
   re-render and diff. Mobile cards byte-identical (modulo
   whitespace).
4. `grep -n "MUST be applied to both files"
   app/templates/grid/_mobile_grid.html` returns no matches.
5. Open the mobile grid in Firefox responsive mode. Tap a card
   -- the existing bottom-sheet open still works (the
   `data-mobile-txn-id` data attribute is preserved by the
   macro; `mobile_grid.js:65-75` still binds to it).

**G. Downstream effects** Phase 1 is complete. Every consumer of the matching predicate reads from
the same precomputed dict. Commit 5 (Phase 2 start) can now restructure the mobile container freely
without re-introducing duplication.

**H. Rollback notes** Restore the inline loops at lines 64-130 and 151-216 and the warning comment
at lines 5-7. The macro and precomputed dict stay (harmless).

---

### Commit 5 -- nav-pills tab scaffold for This Period / Plan

**A. Commit message** `feat(mobile-grid): nav-pills tab scaffold for This Period / Plan`

**B. Problem statement** Phase 2 begins. The current `_mobile_grid.html` flat card-list is replaced
with a Bootstrap nav-pills tab container that hosts two `<div class="tab-pane">` panels: "This
Period" (default active) and "Plan". Commit 5 introduces the tab scaffold; both tabs render the
existing single-period card flow until Commits 6 and 7 populate them with their new partials. This
commit is the structural setup; no UX change yet.

## C. Files modified

- `app/templates/grid/_mobile_grid.html`. Wrap the existing
  period panels (currently the body of the file at lines
  42-249) in a tab container. The tab navigation is at the
  top; the two tab-panes each include the same existing
  content until Commits 6-7 introduce their respective new
  partials.

**D. Implementation approach** Re-read the entire current `_mobile_grid.html` before writing the
scaffold. The restructure:

```jinja
<div class="d-md-none" id="mobile-grid">

  {# NEW: tab navigation #}
  <ul class="nav nav-pills nav-fill mb-3" role="tablist">
    <li class="nav-item" role="presentation">
      <button class="nav-link active" id="mobile-tab-this-period"
              data-bs-toggle="tab" data-bs-target="#mobile-this-period"
              type="button" role="tab"
              aria-controls="mobile-this-period" aria-selected="true">
        This Period
      </button>
    </li>
    <li class="nav-item" role="presentation">
      <button class="nav-link" id="mobile-tab-plan"
              data-bs-toggle="tab" data-bs-target="#mobile-plan"
              type="button" role="tab"
              aria-controls="mobile-plan" aria-selected="false">
        Plan
      </button>
    </li>
  </ul>

  <div class="tab-content">
    <div class="tab-pane fade show active" id="mobile-this-period"
         role="tabpanel" aria-labelledby="mobile-tab-this-period">
      {# Commit 6 replaces this with _mobile_this_period.html include.
         For Commit 5, render the existing flow scoped to the current period. #}
      ...existing period-nav + first period panel only...
    </div>
    <div class="tab-pane fade" id="mobile-plan"
         role="tabpanel" aria-labelledby="mobile-tab-plan">
      {# Commit 7 replaces this with _mobile_plan.html include.
         For Commit 5, render the existing flow with all periods. #}
      ...existing period-nav + all period panels...
    </div>
  </div>
</div>
```

The two tab-panes initially share the existing content (or the "This Period" tab is scoped to one
period and "Plan" to all of them as a useful intermediate). The trick is to leave the file in a
working state at every step. The plan picks the "both tabs show existing content" approach: easier
to revert, clearer diff.

Bootstrap's tab JS (already loaded via `bootstrap.bundle.min.js` at `base.html:283`) handles the
show/hide. The active tab is "This Period" by default per D-A.

`mobile_grid.js` does not need a change in this commit; its existing period-nav handlers continue to
work because both tabs render the existing markup. The handlers' targets (`mobile-prev-btn`,
`mobile-next-btn`, `.mobile-period-panel`) must remain valid across both tabs -- this is fine
because both render the same markup with the same IDs (Bootstrap tab JS hides one pane via
`display: none`; the IDs remain). **Caveat:** two elements with the same ID violate HTML spec.
Commit 5 must scope the IDs differently per tab (e.g., `mobile-prev-btn-this-period` vs
`mobile-prev-btn-plan`) and update `mobile_grid.js` to use `querySelectorAll` not `getElementById`.
Alternatively, the tabs each get a UNIQUE copy of the period-nav, OR Commit 5 punts the duplication
question to Commit 6/7 (the new partials own their own period-nav).

The plan picks the latter: Commit 5 renders the existing content in the "Plan" tab only; the "This
Period" tab is empty (a placeholder `<p class="text-muted">Loading...</p>`) until Commit 6 populates
it. The "Plan" tab is the default active until Commit 6 makes "This Period" the new default. This
sequencing keeps each commit minimal.

**Revised approach for Commit 5:**

- Tab nav added at top (per markup above).
- "This Period" tab pane contains a placeholder
  (`<p class="text-muted text-center py-5">Loading current
  period...</p>`).
- "Plan" tab pane contains the existing `_mobile_grid.html`
  body (lines 22-249) verbatim, indented one level.
- Default active tab is **"Plan"** (the existing flow is
  preserved as the default until Commit 6 ships).
- `mobile_grid.js`: unchanged.

Commit 6 will set "This Period" as the default and populate its tab-pane with the new partial.
Commit 7 will replace the "Plan" tab-pane's inline content with the `_mobile_plan.html` include.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C5-1 | `test_mobile_grid_has_tab_navigation` | GET `/grid` mobile viewport | inspect HTML | `<ul class="nav nav-pills nav-fill ...` present; two `<button data-bs-toggle="tab">` with target `#mobile-this-period` and `#mobile-plan` |
| C5-2 | `test_mobile_grid_default_active_tab_is_plan` | same | inspect HTML | "Plan" tab button has class `active`, "This Period" does not |
| C5-3 | `test_mobile_grid_plan_tab_renders_existing_content` | same | inspect HTML inside `#mobile-plan` | period nav + per-period panels render byte-equivalent to pre-commit |
| C5-4 | `test_mobile_grid_this_period_tab_placeholder` | same | inspect HTML inside `#mobile-this-period` | placeholder text present, no period panels |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. Open `/grid` in Firefox responsive mode at 375x812. Two
   tabs visible at the top of the mobile grid; "Plan" active
   by default. Tapping "This Period" shows the placeholder;
   tapping "Plan" returns to the existing flow.
3. Swipe gestures on the "Plan" tab still work (period
   navigation unchanged).
4. Bottom-sheet tap-to-edit on cards still works.

**G. Downstream effects** Sets up Commits 6 and 7 to populate each tab independently. No UX
regression: the existing flow is preserved behind the "Plan" tab.

**H. Rollback notes** Restore the original `_mobile_grid.html` structure (remove the tab nav +
tab-content wrapper).

---

### Commit 6 -- _mobile_this_period.html partial

**A. Commit message** `feat(mobile-grid): _mobile_this_period.html partial with arrows`

**B. Problem statement** The "This Period" tab needs a partial that renders ONE period with
`[<] [>]` navigation. Default to `current_period`. Uses `render_row_card` from Phase 1.

## C. Files modified

- `app/templates/grid/_mobile_this_period.html` (NEW). Renders
  the current period header (label + date range), a
  `[<] [>]` arrow pair, income card (collapsible), expense
  card (collapsible), net cash flow bar, projected balance
  card. Layout copies from the existing
  `_mobile_grid.html:42-247` per-period panel structure but
  shows only ONE panel.
- `app/templates/grid/_mobile_grid.html`. Replace the "This
  Period" placeholder from Commit 5 with `{% include
  "grid/_mobile_this_period.html" %}`. Switch the default
  active tab from "Plan" to "This Period".

## D. Implementation approach

```jinja
{# app/templates/grid/_mobile_this_period.html #}
{# Renders the current pay period as a single panel.
   Used inside the "This Period" tab of _mobile_grid.html.
   Companion view (Commit 13) also includes this partial.

   Expected context:
     periods             -- list of PayPeriod; uses periods[0] by default
                            (the route loads current period as first)
     txn_by_period       -- (compatibility; not directly read here)
     income_row_keys     -- list[RowKey]
     expense_row_keys    -- list[RowKey]
     subtotals           -- dict[period_id -> PeriodSubtotal]
     balances            -- dict[period_id -> Decimal]
     matched_by_row_period -- dict (Phase 1)
     entry_sums          -- dict[txn_id -> entry summary]
     can_edit            -- bool, default True (False for companion)
#}
{% from "grid/_grid_row_macros.html" import render_row_card %}
{% set period = periods[0] %}

<div class="mobile-period-panel" data-period-id="{{ period.id }}">
  {# Period nav header #}
  <div class="d-flex align-items-center justify-content-between mb-3">
    <button class="btn btn-sm btn-outline-secondary"
            id="mobile-tp-prev-btn"
            aria-label="Previous period">
      <i class="bi bi-chevron-left"></i>
    </button>
    <div class="text-center">
      <div class="fw-bold">{{ period.label }}</div>
      <small class="text-muted">
        {{ period.start_date.strftime('%-m/%-d') }} -- {{ period.end_date.strftime('%-m/%-d') }}
      </small>
    </div>
    <button class="btn btn-sm btn-outline-secondary"
            id="mobile-tp-next-btn"
            aria-label="Next period">
      <i class="bi bi-chevron-right"></i>
    </button>
  </div>

  {# Income section -- copied from existing _mobile_grid.html:49-133
     structure, with the matching loop replaced by render_row_card calls. #}
  <div class="card mb-3">
    <div class="card-header ... mobile-section-income"
         data-bs-toggle="collapse" data-bs-target="#mobile-income-{{ period.id }}"
         aria-expanded="true">
      <span class="fw-bold small text-uppercase">
        <i class="bi bi-chevron-down me-1"></i> Income
      </span>
      <span class="font-mono fw-bold">
        {% set inc_total = subtotals[period.id].income %}
        {% if inc_total %}${{ "{:,.0f}".format(inc_total) }}{% else %}$0{% endif %}
      </span>
    </div>
    <div class="collapse show" id="mobile-income-{{ period.id }}">
      <ul class="list-group list-group-flush">
        {% set ns_inc = namespace(current_group='') %}
        {% for rk in income_row_keys %}
          {% set match_key = (rk.category_id, rk.template_id, rk.txn_name, period.id) %}
          {% set matched = matched_by_row_period.get(match_key, []) %}
          {% if matched %}
            {% if rk.group_name != ns_inc.current_group %}
              {% set ns_inc.current_group = rk.group_name %}
              <li class="list-group-item py-1 px-3 text-muted small fw-semibold text-uppercase mobile-group-header">
                {{ rk.group_name }}
              </li>
            {% endif %}
            {{ render_row_card(rk, period, matched_by_row_period, entry_sums, can_edit) }}
          {% endif %}
        {% endfor %}
      </ul>
    </div>
  </div>

  {# Expense section -- symmetric to income; section banner class is
     .mobile-section-expense. #}

  {# Net cash flow bar -- copied from _mobile_grid.html:224-231 #}
  {# Projected balance card -- copied from _mobile_grid.html:233-247 #}
</div>
```

Period navigation: `mobile_grid.js` needs to know about the new arrow IDs (`mobile-tp-prev-btn` /
`mobile-tp-next-btn`). The simplest path: navigation re-renders by following a link
`/grid?periods=N&offset=current_offset +/- 1`. This is a full GET (HTMX optional; not required for
Commit 6) -- the existing desktop arrow navigation at `grid.html:25-49` uses the same pattern.

For Commit 6, the arrows are `<a>` links to `/grid?periods=1&offset=N` (the "This Period" tab
implicitly uses `periods=1`). Add a `<input type="hidden" name="default_tab" value="this-period">`
or a URL fragment (`#this-period`) so the page lands on the "This Period" tab after the GET.
JS-side: a `DOMContentLoaded` handler reads `location.hash` and activates the corresponding tab via
Bootstrap's `Tab.show()`.

Switch the default active tab in `_mobile_grid.html` from "Plan" (Commit 5's placeholder default) to
"This Period": remove `active`/`show active` from `mobile-plan` block, add it to
`mobile-this-period` block.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C6-1 | `test_this_period_partial_exists` | filesystem | `ls app/templates/grid/_mobile_this_period.html` | file exists |
| C6-2 | `test_this_period_renders_current_period_by_default` | GET `/grid` mobile | inspect `#mobile-this-period` | period label matches `current_period.label` |
| C6-3 | `test_this_period_includes_income_expense_balance` | same | inspect | three cards present (Income, Expenses, Projected Balance) plus Net Cash Flow bar |
| C6-4 | `test_this_period_arrows_link_to_offset_neighbors` | same with `offset=0` | inspect `[<]` and `[>]` hrefs | `[<]` -> `/grid?periods=1&offset=-1`, `[>]` -> `/grid?periods=1&offset=1` |
| C6-5 | `test_default_active_tab_is_this_period` | GET `/grid` mobile | inspect tab nav | `mobile-tab-this-period` has class `active` |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. Open `/grid` mobile -- "This Period" tab is default active;
   shows ONE period (the current one) with income, expenses,
   net, balance.
3. Tap `[>]` -- navigates to the next period (still in "This
   Period" tab via hash routing).
4. Tap "Plan" tab -- previous flow still works.

**G. Downstream effects** "This Period" is now the default mobile experience. Companion (Commit 13)
will reuse this same partial.

**H. Rollback notes** Restore the placeholder in `_mobile_grid.html`'s "This Period" tab-pane;
delete `_mobile_this_period.html`; restore "Plan" as the default active tab.

---

### Commit 7 -- _mobile_plan.html + inline card action bar

**A. Commit message** `feat(mobile-grid): _mobile_plan.html + inline card action bar`

**B. Problem statement** The "Plan" tab needs its own partial (the multi-period scroll view). Each
card across both tabs gets an inline action bar that expands on tap with `[Mark Paid]`
`[Edit Amount]` `[Open Full]` (D-C). The action bar replaces the existing direct-to-bottom-sheet
behavior; the bottom sheet is now opened explicitly via the `[Open Full]` button.

## C. Files modified

- `app/templates/grid/_mobile_plan.html` (NEW). Renders the
  multi-period scroll view (the existing `_mobile_grid.html:22-249`
  flow distilled). Uses `render_row_card` per period.
- `app/templates/grid/_mobile_card_actions.html` (NEW, ~30
  lines). The action bar partial; emitted by `render_row_card`
  (Commit 1's macro takes a small change here to include the
  action bar slot per card -- see D).
- `app/templates/grid/_grid_row_macros.html`. `render_row_card`
  emits a wrapper `<div>` containing the card `<li>` plus a
  hidden `<div class="mobile-card-action-bar collapse">` that
  expands on tap.
- `app/templates/grid/_mobile_grid.html`. Replace the inline
  Plan tab content (Commit 5) with `{% include
  "grid/_mobile_plan.html" %}`.
- `app/static/js/mobile_grid.js`. Add a tap-to-toggle handler
  for the action bar: tap on `.mobile-txn-card` toggles the
  sibling action-bar `collapse`. A second tap (or tap on
  another card) collapses the open one. Tap on `[Open Full]`
  still calls `openFullEdit`.

**D. Implementation approach** The action bar is a Bootstrap `collapse` element sibling to the card
`<li>`:

```jinja
{# _mobile_card_actions.html -- emitted once per matched txn #}
<div class="collapse mobile-card-action-bar" id="card-actions-{{ txn.id }}">
  <div class="d-flex gap-2 px-3 py-2 bg-surface-raised border-bottom">
    {# Mark Paid -- one-tap; reuses existing route + HX-Trigger=balanceChanged #}
    {% if txn.status_id != STATUS_DONE and txn.status_id != STATUS_SETTLED %}
      <form hx-post="{{ url_for('transactions.mark_done', txn_id=txn.id) }}"
            hx-target="#txn-cell-{{ txn.id }}"
            hx-swap="outerHTML"
            class="d-inline">
        {{ csrf_token_input() }}
        <button type="submit" class="btn btn-success btn-sm flex-fill"
                style="min-height: 44px;">
          <i class="bi bi-check2"></i> Mark Paid
        </button>
      </form>
    {% endif %}

    {% if can_edit %}
      {# Edit Amount -- inline quick-edit form swap #}
      <button type="button" class="btn btn-outline-secondary btn-sm flex-fill"
              style="min-height: 44px;"
              hx-get="{{ url_for('transactions.get_quick_edit', txn_id=txn.id) }}"
              hx-target="#txn-cell-{{ txn.id }}"
              hx-swap="innerHTML">
        <i class="bi bi-pencil"></i> Edit Amount
      </button>

      {# Open Full -- opens bottom sheet via existing JS #}
      <button type="button" class="btn btn-outline-secondary btn-sm flex-fill txn-expand-btn"
              style="min-height: 44px;"
              data-txn-id="{{ txn.id }}">
        <i class="bi bi-arrows-fullscreen"></i> Open Full
      </button>
    {% endif %}
  </div>
</div>
```

`render_row_card` change: wrap each emitted `<li>` plus the action-bar partial in a
`<div class="mobile-card-wrapper">` so the tap handler can target the wrapper and toggle the inner
collapse.

`mobile_grid.js` addition (~30 lines):

```javascript
// Tap-to-toggle action bar
document.addEventListener('click', function (e) {
  var card = e.target.closest('.mobile-txn-card');
  if (!card) return;
  if (e.target.closest('.mobile-card-action-bar')) return; // taps inside the bar don't re-toggle

  var wrapper = card.closest('.mobile-card-wrapper');
  if (!wrapper) return;
  var bar = wrapper.querySelector('.mobile-card-action-bar');
  if (!bar) return;

  // Close any other open bars
  document.querySelectorAll('.mobile-card-action-bar.show').forEach(function (other) {
    if (other !== bar) bootstrap.Collapse.getOrCreateInstance(other).hide();
  });

  bootstrap.Collapse.getOrCreateInstance(bar).toggle();
});
```

The existing tap-to-edit handler at `mobile_grid.js:65-75` (which previously routed to
`openFullEdit` / `openTransferFullEdit`) is **removed**. The new path is: tap card -> action bar
opens -> user picks an action. `[Open Full]` calls the existing `txn-expand-btn` click handler in
`grid_edit.js:482-486` (which is delegated, so it works on the dynamically-included button).

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C7-1 | `test_plan_partial_exists` | filesystem | check | file exists |
| C7-2 | `test_mobile_card_actions_partial_exists` | filesystem | check | file exists |
| C7-3 | `test_action_bar_includes_mark_paid_when_not_settled` | fixture: projected txn | render | `[Mark Paid]` button present in action bar |
| C7-4 | `test_action_bar_excludes_mark_paid_when_settled` | fixture: settled txn | render | `[Mark Paid]` button absent |
| C7-5 | `test_action_bar_excludes_edit_when_can_edit_false` | fixture: companion render (Commit 13 prereq) | render with `can_edit=False` | `[Edit Amount]` and `[Open Full]` buttons absent; `[Mark Paid]` present |
| C7-6 | `test_action_bar_hx_post_target_is_cell` | inspect Mark Paid form | render | `hx-post=/transactions/<id>/mark-done`, `hx-target=#txn-cell-<id>` |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py tests/test_routes/test_transactions.py -v`
   green.
2. Open `/grid` mobile. Tap a projected expense card -- action
   bar slides in beneath it. Three buttons visible.
3. Tap `[Mark Paid]` -- card updates to show the done badge;
   balance row refreshes via the existing `balanceChanged`
   trigger (assert by watching the projected-balance card
   at the bottom of "This Period" -- its value changes).
4. Tap another card -- previous action bar collapses; new
   one opens.
5. Tap `[Edit Amount]` -- inline quick-edit input replaces
   the card content. Press Enter -- card returns updated.
6. Tap `[Open Full]` -- bottom sheet opens (existing flow).
7. Switch to "Plan" tab -- same action-bar behavior.

**G. Downstream effects** The bottom sheet is now reached explicitly. Companion (Commit 13) inherits
the same action-bar pattern with `can_edit=False`. The swipe gesture (Commit 9) will be a parallel
shortcut to Mark Paid (skips the bar).

**H. Rollback notes** Delete `_mobile_plan.html` and `_mobile_card_actions.html`; restore the
original tap-to-edit handler in `mobile_grid.js:65-75`; restore the inline Plan content in
`_mobile_grid.html`; revert `render_row_card` to emit only the card `<li>`.

---

### Commit 8 -- bottom-sheet drag-to-dismiss + iOS keyboard avoidance

**A. Commit message** `feat(mobile-grid): bottom-sheet drag-to-dismiss + iOS keyboard avoidance`

**B. Problem statement** The static bottom sheet at `app.css:821-843` (added in v1 commit `c1cc309`)
does not support drag-to-dismiss, has no drag handle, and is occluded by the iOS keyboard when an
input inside it is focused. D-G specifies the fix.

## C. Files modified

- `app/static/css/app.css` (lines 821-843 mobile bottom-sheet
  block, plus new rules). Add `.bottom-sheet-handle` (32 x 4
  pill, centered, 8 px tap padding); default
  `transform: translateY(0); transition: transform 200ms
  ease-out`; `.dragging` modifier sets `transition: none`.
- `app/static/js/grid_edit.js` (`positionPopover` at line 143,
  `showPopover` at line 176, `closeFullEdit` at line 327;
  mobile branches gated on `window.innerWidth < 768`). Inject
  the drag handle on mobile; add `touchstart` / `touchmove` /
  `touchend` handlers; listen for `visualViewport.resize` and
  adjust the popover's `bottom` offset.

## D. Implementation approach

CSS additions (inside the `@media (max-width: 767.98px)` block that already wraps
`.txn-full-edit-popover` rules):

```css
.txn-full-edit-popover .bottom-sheet-handle {
  width: 32px;
  height: 4px;
  background: var(--bs-secondary-bg);
  border-radius: 2px;
  margin: 8px auto;
  cursor: grab;
  touch-action: none; /* prevent vertical scroll start on the handle */
}

.txn-full-edit-popover {
  transform: translateY(0);
  transition: transform 200ms ease-out;
}

.txn-full-edit-popover.dragging {
  transition: none;
}
```

JS additions in `grid_edit.js`:

```javascript
// Inject the drag handle when entering the mobile branch of positionPopover:
// (inside the existing `if (window.innerWidth < 768) { ... }` block)
var handle = document.createElement('div');
handle.className = 'bottom-sheet-handle';
popover.insertBefore(handle, popover.firstChild);

// Touch handlers
var startY = 0;
var currentTranslate = 0;
var popoverHeight = 0;

handle.addEventListener('touchstart', function (e) {
  startY = e.touches[0].clientY;
  popoverHeight = popover.offsetHeight;
  popover.classList.add('dragging');
}, { passive: true });

handle.addEventListener('touchmove', function (e) {
  var dy = e.touches[0].clientY - startY;
  if (dy < 0) dy = 0; // only drag down
  currentTranslate = dy;
  popover.style.transform = 'translateY(' + dy + 'px)';
}, { passive: true });

handle.addEventListener('touchend', function () {
  popover.classList.remove('dragging');
  if (currentTranslate > popoverHeight * 0.3) {
    closeFullEdit();
  } else {
    popover.style.transform = 'translateY(0)';
  }
  currentTranslate = 0;
}, { passive: true });

// visualViewport keyboard avoidance
function adjustForKeyboard() {
  if (!window.visualViewport) return;
  var vh = window.innerHeight;
  var vph = window.visualViewport.height;
  var vpoy = window.visualViewport.offsetTop;
  popover.style.bottom = (vh - vph - vpoy) + 'px';
}
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', adjustForKeyboard);
  // Store the handler ref on popover for teardown in closeFullEdit
  popover._adjustForKeyboard = adjustForKeyboard;
}
```

`closeFullEdit` (around line 355) tear-down:

```javascript
if (window.visualViewport && popover._adjustForKeyboard) {
  window.visualViewport.removeEventListener('resize', popover._adjustForKeyboard);
  delete popover._adjustForKeyboard;
}
popover.style.transform = '';
popover.style.bottom = '';
```

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C8-1 | `test_drag_handle_injected_on_mobile_open` | open bottom sheet on a mobile viewport | inspect popover | `.bottom-sheet-handle` element present as first child |
| C8-2 | `test_drag_handle_absent_on_desktop_open` | open popover on desktop | inspect popover | no `.bottom-sheet-handle` (desktop branch skipped) |
| C8-3 | `test_pylint_clean` | post-edit | `pylint app/ --fail-on=E,F` | clean (this commit is JS+CSS but lint runs as safety) |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh` full suite green (no Python change but
   safety check).
2. Open `/grid` on Firefox responsive mode (iPhone XS 375x812).
   Open the bottom-sheet full-edit (tap card -> `[Open Full]`).
   Drag handle visible at the top of the sheet.
3. Drag the handle down a little (50 px); release. Sheet
   snaps back.
4. Drag the handle past 30 % of the sheet's height; release.
   Sheet dismisses.
5. Open the sheet again; focus the amount input. iOS keyboard
   appears (Firefox responsive mode emulates it via the
   "Keyboard" toggle). Sheet repositions above the keyboard;
   save button still visible.
6. On a real iPhone (XS and 16 Plus) in Firefox iOS, repeat
   steps 2-5. Confirm `visualViewport` support (WebKit ships
   it; verify the sheet rises above the keyboard).
7. Verify desktop unaffected: open `/grid` at 1920x1080, open
   popover, no drag handle present, popover behaves
   identically to pre-commit.

**G. Downstream effects** Every bottom-sheet open path (`openFullEdit`, `openTransferFullEdit`,
`openFullCreate`) inherits the drag handle and keyboard avoidance because they all route through
`positionPopover` and `showPopover`.

**H. Rollback notes** Remove the JS additions and CSS rules. Suite stays green.

---

### Commit 9 -- swipe-left reveals Mark Paid button on cards

**A. Commit message** `feat(mobile-grid): swipe-left reveals Mark Paid button on cards`

**B. Problem statement** D-D specifies a swipe-left gesture on each `.mobile-txn-card` that
translates the card -80 px and reveals a `[Mark Paid]` button positioned absolutely underneath. Tap
the button to commit Mark Paid (which is the same HTMX form the inline action bar uses). The gesture
is a shortcut; the inline action bar from Commit 7 remains the non-gesture path.

## C. Files modified

- `app/static/css/app.css`. Add `.mobile-txn-card`
  wrapper-relative positioning, `.mobile-txn-card.swiped`
  modifier (`transform: translateX(-80px); transition:
  transform 150ms`), and `.swipe-action-mark-paid` (absolutely
  positioned within the card wrapper, revealed when the
  card is swiped).
- `app/static/js/mobile_grid.js`. Add touch handlers on
  `.mobile-txn-card`: detect horizontal swipe past the 50 px
  threshold; add/remove `.swiped` class. The reveal button is
  a sibling element rendered by `render_row_card` (Commit 1)
  -- modify the macro to emit it.
- `app/templates/grid/_grid_row_macros.html`. The
  `render_row_card` macro emits, after the card `<li>`, a
  `<button class="swipe-action-mark-paid">` form that POSTs to
  `/transactions/<id>/mark-done`. Companion-mode
  (`can_edit=False`) still includes this -- companions can
  mark paid per R-7.

## D. Implementation approach

CSS:

```css
.mobile-card-wrapper {
  position: relative;
  overflow: hidden;
}

.mobile-txn-card {
  position: relative;
  transition: transform 150ms ease-out;
  background: var(--bs-body-bg); /* opaque so the swipe-action is hidden by default */
}

.mobile-txn-card.swiped {
  transform: translateX(-80px);
}

.swipe-action-mark-paid {
  position: absolute;
  top: 0;
  right: 0;
  width: 80px;
  height: 100%;
  background: var(--bs-success);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  font-size: 0.85rem;
}
```

JS:

```javascript
// Swipe-left on cards
document.addEventListener('touchstart', function (e) {
  var card = e.target.closest('.mobile-txn-card');
  if (!card) return;
  card._swipeStartX = e.touches[0].clientX;
  card._swipeStartY = e.touches[0].clientY;
}, { passive: true });

document.addEventListener('touchmove', function (e) {
  var card = e.target.closest('.mobile-txn-card');
  if (!card || card._swipeStartX === undefined) return;
  var dx = e.touches[0].clientX - card._swipeStartX;
  var dy = e.touches[0].clientY - card._swipeStartY;
  if (Math.abs(dy) > Math.abs(dx)) {
    // Vertical scroll -- cancel swipe tracking
    card._swipeStartX = undefined;
  }
}, { passive: true });

document.addEventListener('touchend', function (e) {
  var card = e.target.closest('.mobile-txn-card');
  if (!card || card._swipeStartX === undefined) return;
  var dx = e.changedTouches[0].clientX - card._swipeStartX;
  if (dx < -50) {
    // Swipe-left past threshold
    // Close any other swiped cards
    document.querySelectorAll('.mobile-txn-card.swiped').forEach(function (other) {
      if (other !== card) other.classList.remove('swiped');
    });
    card.classList.add('swiped');
  } else if (dx > 50 && card.classList.contains('swiped')) {
    // Swipe-right to un-swipe
    card.classList.remove('swiped');
  }
  card._swipeStartX = undefined;
}, { passive: true });

// Tap outside to un-swipe
document.addEventListener('click', function (e) {
  if (e.target.closest('.swipe-action-mark-paid')) return; // tap on the action commits
  if (!e.target.closest('.mobile-txn-card.swiped')) {
    document.querySelectorAll('.mobile-txn-card.swiped').forEach(function (c) {
      c.classList.remove('swiped');
    });
  }
});
```

Macro change in `render_row_card` (Commit 1): emit the swipe-action element as a sibling to the card
`<li>`:

```jinja
<div class="mobile-card-wrapper">
  <button class="swipe-action-mark-paid"
          hx-post="{{ url_for('transactions.mark_done', txn_id=txn.id) }}"
          hx-target="#txn-cell-{{ txn.id }}"
          hx-swap="outerHTML"
          aria-label="Mark {{ txn.name }} paid">
    <i class="bi bi-check2"></i> Paid
  </button>
  <li class="list-group-item ... mobile-txn-card" ...>
    ... existing card content ...
  </li>
</div>
```

CSRF: HTMX's `htmx:configRequest` handler adds CSRF automatically per Rule 6.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C9-1 | `test_swipe_action_button_emitted` | render a mobile card | inspect HTML | `<button class="swipe-action-mark-paid"` sibling to the card |
| C9-2 | `test_swipe_action_hx_post_targets_mark_done` | inspect | same | `hx-post` resolves to `/transactions/<id>/mark-done` |
| C9-3 | `test_swipe_action_present_for_companion` | render with `can_edit=False` | inspect | swipe-action button still present (companions can mark paid) |
| C9-4 | `test_swipe_threshold_match_period_swipe` | grep | `grep -n "50" app/static/js/mobile_grid.js` | the period-swipe threshold and card-swipe threshold both use 50 (R-8 alignment) |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py tests/test_routes/test_transactions.py -v`
   green.
2. Open `/grid` mobile (Firefox responsive mode). Swipe-left on
   a projected card -- card slides -80 px, green "Paid"
   button revealed.
3. Tap the "Paid" button -- card updates to show done badge;
   balance row refreshes.
4. Swipe-left on another card; the first one un-swipes
   automatically.
5. Tap somewhere outside a swiped card -- it un-swipes.
6. Swipe-right on a swiped card -- it un-swipes.
7. Real device (iPhone XS + 16 Plus, Firefox iOS): same. The
   gesture must not block vertical scroll (test by swiping
   diagonally; the card should not swipe).
8. Verify desktop unaffected: no `.swipe-action-mark-paid`
   visible (cards render but the touch handlers don't fire on
   mouse).

**G. Downstream effects** The swipe-action is a parallel path to the inline action bar's
`[Mark Paid]` button. Both submit to the same endpoint. Commit 13 factors the touch logic into
`app/static/js/swipe.js` so companion can reuse it.

**H. Rollback notes** Remove the touch handlers, CSS rules, and macro change. The inline action bar
(Commit 7) keeps working as the non-gesture Mark Paid path.

---

### Commit 10 -- jump-to period <select> in This Period header

**A. Commit message** `feat(mobile-grid): jump-to period <select> in This Period header`

**B. Problem statement** D-E specifies a native `<select>` in the "This Period" tab header that
lists every visible period and jumps to the selected one. Lets the user reach a non-adjacent period
without N taps on `[<]`.

## C. Files modified

- `app/templates/grid/_mobile_this_period.html`. Insert a
  `<select>` between the `[<]` and the period label, OR below
  the arrow row (the latter has less crowding on a 375 px
  viewport). Options derived from `all_periods` (already in
  the route context at `grid.py:386`).

## D. Implementation approach

```jinja
{# Below the [<] [>] arrow row in _mobile_this_period.html: #}
<form action="{{ url_for('grid.index') }}" method="get" class="mb-3">
  <input type="hidden" name="periods" value="1">
  <select name="offset"
          class="form-select form-select-sm"
          onchange="this.form.submit()"
          aria-label="Jump to pay period">
    {% for p in all_periods %}
      {% set p_offset = p.period_index - current_period.period_index %}
      <option value="{{ p_offset }}"
              {{ 'selected' if p.id == period.id else '' }}>
        {{ p.label }} ({{ p.start_date.strftime('%-m/%-d/%y') }})
      </option>
    {% endfor %}
  </select>
</form>
```

Form submission is a full GET to `/grid?periods=1&offset=N`. The hash routing from Commit 6 ensures
the "This Period" tab remains active after the GET.

`onchange="this.form.submit()"` is an inline event handler; CSP forbids inline `<script>`, but
inline event handlers are allowed under the project's existing CSP per the audit at
`docs/audits/security/2026-04-15/F-037.md` (verify before shipping; if CSP-strict, use a delegated
`change` listener in `mobile_grid.js`).

**Fallback if CSP strict:** add to `mobile_grid.js`:

```javascript
document.addEventListener('change', function (e) {
  if (e.target.matches('select[name="offset"]') &&
      e.target.closest('#mobile-this-period')) {
    e.target.form.submit();
  }
});
```

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C10-1 | `test_jump_to_select_present` | GET `/grid` mobile | inspect | `<select name="offset">` inside `#mobile-this-period` |
| C10-2 | `test_jump_to_options_match_all_periods` | fixture with N periods | inspect | N `<option>` elements with `value=offset_relative_to_current` |
| C10-3 | `test_jump_to_current_period_selected` | same | inspect | the current period's option has `selected` attribute |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. Open `/grid` mobile. Tap the period `<select>` -- iOS
   picker wheel appears (or Android dropdown).
3. Pick a non-adjacent period -- page navigates and lands on
   "This Period" tab showing that period.
4. CSP check: if the inline `onchange` handler triggered a
   CSP violation in the browser console, switch to the
   delegated-listener fallback.

**G. Downstream effects** Period jump now exists; arrows remain the adjacent-step convenience.

**H. Rollback notes** Remove the `<form>`/`<select>` block.

---

### Commit 11 -- inputmode="decimal" on 10 monetary inputs

**A. Commit message** `feat(forms): inputmode="decimal" on 10 monetary inputs`

**B. Problem statement** Every monetary `<input type="number" step="0.01">` in the grid layer is
missing `inputmode="decimal"`. iOS uses a numeric keypad that does NOT include a decimal point under
the default `type="number"`; `inputmode="decimal"` fixes this. Desktop is unaffected (the attribute
is ignored on non-touch).

**C. Files modified** All 10 sites listed in Section 6.4 verbatim. One attribute addition per
`<input>`. No other change.

**D. Implementation approach** Mechanical edit. Example:

```jinja
{# Before: #}
<input type="number" step="0.01" name="estimated_amount" ...>

{# After: #}
<input type="number" step="0.01" inputmode="decimal" name="estimated_amount" ...>
```

Re-grep the 10 sites with:

```bash
grep -nE '<input type="number"[^>]*step="0\.01"' \
  app/templates/grid/_transaction_*.html \
  app/templates/grid/_anchor_edit.html \
  app/templates/grid/grid.html
```

The grep should find 10 matches at the Section 6.4 line numbers. If any additional matches exist
(someone added a new monetary input between plan-write and commit-land), include them and note the
addition in the commit work summary.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C11-1 | `test_inputmode_decimal_on_all_monetary_inputs` | grep | `grep -nE '<input[^>]*step="0\.01"[^>]*inputmode="decimal"' app/templates/grid/...` | 10 matches |
| C11-2 | `test_no_monetary_input_without_inputmode` | grep | `grep -nE '<input[^>]*step="0\.01"' app/templates/grid/... | grep -v inputmode` | empty |
| C11-3 | `test_desktop_form_render_unchanged` | GET `/grid` desktop, capture a form HTML | inspect | identical to pre-commit modulo the new attribute on each input |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py
   tests/test_routes/test_transactions.py -v` green.
2. On Firefox iOS (real iPhone XS or 16 Plus), open the
   transaction full-edit form. Focus the amount input. iOS
   keypad now includes a decimal point.
3. Repeat for the Add Transaction modal, the quick-edit
   inline form, the anchor balance form, and the entries
   create/edit forms.

**G. Downstream effects** All current and future monetary inputs benefit. Any new monetary input
added after this commit must include the attribute (Rule 8); the
`test_no_monetary_input_without_inputmode` grep is the lock.

**H. Rollback notes** Remove the attribute from each of the 10 sites.

---

### Commit 12 -- sticky action footer in full-edit popover

**A. Commit message** `feat(mobile-sheet): sticky action footer in full-edit popover`

**B. Problem statement** The full-edit popover's Save / Cancel / Mark Done buttons sit at the
natural bottom of the form content. On a small viewport with the iOS keyboard up, they can be
off-screen below the keyboard. D-F (modal-fullscreen) applies to the Add Transaction modal; this
commit applies a similar treatment to the popover's action buttons via
`position: sticky; bottom: 0`.

## C. Files modified

- `app/templates/grid/_transaction_full_edit.html`. Wrap the
  Save / Cancel / Mark Done button group in a `<div
  class="popover-action-footer">` element.
- `app/templates/grid/_transaction_full_create.html`. Same.
- `app/static/css/app.css`. Add `.popover-action-footer` rules:
  - Default: not sticky (desktop popover is small enough that
    sticky has no benefit).
  - `@media (max-width: 767.98px)`: `position: sticky;
    bottom: 0; padding: 8px 16px; padding-bottom: calc(8px +
    env(safe-area-inset-bottom)); background: var(--bs-body-bg);
    border-top: 1px solid var(--bs-border-color);`.

**D. Implementation approach** Re-read `_transaction_full_edit.html` and identify the existing
button group (it carries Save, Cancel, Mark Done, Mark Credit, Cancel Transaction buttons depending
on status). Wrap it in the new footer div without changing any button or its handlers.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C12-1 | `test_action_footer_present_in_full_edit` | render `_transaction_full_edit.html` | inspect | `.popover-action-footer` wrapper present |
| C12-2 | `test_action_footer_sticky_on_mobile` | CSS audit | check rule in app.css | `position: sticky; bottom: 0` inside the `<768 px` media query |

Re-pinned tests: none.

## F. Manual verification steps

1. Open bottom sheet on Firefox responsive mode (375x812).
2. Focus the amount input -- keyboard appears (or its
   emulation). The action footer remains visible above the
   keyboard.
3. Real iPhone XS + 16 Plus: same.
4. Desktop unaffected: open popover at 1920x1080, no sticky
   footer (the existing styling is preserved).

**G. Downstream effects** Bottom-sheet action buttons are always reachable.

**H. Rollback notes** Unwrap the button group; remove the CSS rule.

---

### Commit 13 -- extract grid_view_service + companion uses This Period partial + swipe.js shared

**A. Commit message**
`refactor(grid): extract grid_view_service + companion uses This Period partial + swipe.js shared`

**B. Problem statement** The companion view today has its own card template. D-B specifies it should
reuse the same "This Period" partial as the owner mobile grid. The shared partial needs the same
`row_keys` + `matched_by_row_period` context shape that the grid route produces; rather than
re-derive it in the companion route, extract the helpers into a pure service module. Same commit
also factors the swipe-action touch logic from `mobile_grid.js` (Commit 9) into a shared `swipe.js`
so `companion.js` can adopt it.

## C. Files modified

- `app/services/grid_view_service.py` (NEW). Pure functions:
  - `build_row_keys(transactions, categories, is_income_section)
    -> list[RowKey]` -- moved from `app/routes/grid.py:68-162`.
  - `build_matched_by_row_period(transactions, periods, row_keys)
    -> dict` -- the Commit 2 precomputation, parameterized.
  - `RowKey` namedtuple moved here (was at `grid.py:39-47`).
- `app/routes/grid.py`. Replace inline `_build_row_keys` call
  with `grid_view_service.build_row_keys(...)`. Replace
  Commit 2 inline precomputation with
  `grid_view_service.build_matched_by_row_period(...)`. Import
  cleanup.
- `app/routes/companion.py`. Add the same calls. The
  companion's `transactions` and `period` context now derive
  the same dict shape. Pass `row_keys` (income + expense
  separately) + `matched_by_row_period` + `entry_sums` +
  `can_edit=False` to the template.
- `app/templates/companion/index.html`. Replace the inline
  `{% for txn in transactions %}` card loop (lines 36-38) with
  `{% include "grid/_mobile_this_period.html" with context %}`.
- `app/templates/companion/_transaction_card.html`. Mark as
  deprecated (delete in a follow-up commit or leave as-is for
  history; this commit just stops including it). Mark Paid
  functionality moves to the shared `_mobile_card_actions.html`
  via `can_edit=False`.
- `app/static/js/swipe.js` (NEW, ~50 lines). Extract the swipe
  touch logic from Commit 9. Single exported function:
  `attachSwipeAction(rootElement, { onLeftSwipe, threshold })`.
- `app/static/js/mobile_grid.js`. Replace inline swipe handler
  with `attachSwipeAction(document, { onLeftSwipe: ..., threshold: 50 })`.
- `app/static/js/companion.js`. Add `attachSwipeAction(...)`
  call so companion cards also support swipe-left-to-mark-paid.

**D. Implementation approach** Re-read `app/routes/grid.py:68-162` in full before moving. The move
preserves the exact predicate and dedup logic; the function signatures stay identical. Only the
location changes.

The companion route gains:

```python
from app.services import grid_view_service

# In companion.index() (current line 84):
transactions, period = companion_service.get_visible_transactions(...)
# NEW:
all_categories = (
    db.session.query(Category)
    .filter_by(user_id=current_user.linked_owner_id)
    .order_by(Category.group_name, Category.item_name)
    .all()
)
income_row_keys = grid_view_service.build_row_keys(
    transactions, all_categories, is_income_section=True,
)
expense_row_keys = grid_view_service.build_row_keys(
    transactions, all_categories, is_income_section=False,
)
matched_by_row_period = grid_view_service.build_matched_by_row_period(
    transactions, [period], income_row_keys + expense_row_keys,
)
# ... existing entry_data build ...
return render_template(
    "companion/index.html",
    periods=[period],          # _mobile_this_period.html expects a list
    current_period=period,
    income_row_keys=income_row_keys,
    expense_row_keys=expense_row_keys,
    matched_by_row_period=matched_by_row_period,
    entry_sums=entry_data,
    subtotals=...,              # need to compute or accept that companion has no subtotals?
    balances=...,
    can_edit=False,
)
```

Open question for implementation: does the companion view show subtotals and projected balance? The
current companion view does not (verified at `companion/index.html`). Two options:

- (a) Pass empty / zero subtotals; the partial renders zero
  values.
- (b) Compute subtotals from `balance_resolver.period_subtotal`
  in the companion route (allowed per Rule 2 -- canonical
  producer).
- (c) Make the partial's subtotal / balance sections optional
  (Jinja `{% if subtotals is defined %}`).

The plan picks (c): keep the partial flexible; companion passes no `subtotals` or `balances`, and
the partial omits those sections gracefully. This preserves the current companion UX and avoids
broadening companion's data scope.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C13-1 | `test_grid_view_service_exists` | filesystem | check | file exists, has `build_row_keys` + `build_matched_by_row_period` + `RowKey` |
| C13-2 | `test_grid_index_uses_service` | grep | `grep -n "grid_view_service" app/routes/grid.py` | both functions imported and called |
| C13-3 | `test_grid_routes_produce_byte_equivalent_html` | GET `/grid` | compare to pre-commit | identical (refactor only) |
| C13-4 | `test_companion_index_uses_partial` | GET `/companion/` | inspect HTML | `_mobile_this_period.html` markers present (period header, income/expense cards) |
| C13-5 | `test_companion_card_excludes_edit_open_full` | inspect a card's action bar | render | no `[Edit Amount]` or `[Open Full]` buttons; `[Mark Paid]` present |
| C13-6 | `test_companion_swipe_action_works` | render | inspect | `.swipe-action-mark-paid` present on cards |
| C13-7 | `test_swipe_js_module_exists` | filesystem | check | file exists, exports `attachSwipeAction` |
| C13-8 | `test_mobile_grid_js_uses_swipe_module` | grep | `grep -n "attachSwipeAction" app/static/js/mobile_grid.js` | one call |
| C13-9 | `test_companion_js_uses_swipe_module` | grep | same in companion.js | one call |
| C13-10 | `test_no_decimal_arithmetic_in_grid_view_service` | grep | `grep -nE "(quantize|round|Decimal\\()" app/services/grid_view_service.py` | no monetary arithmetic; the service is pure-template-data |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py tests/test_routes/test_companion.py -v` green.
2. `pylint app/ --fail-on=E,F` clean.
3. Open `/grid` mobile -- looks identical to post-Commit-9 state.
4. Open `/companion/` as a companion user -- now uses the same
   card layout. Tap a card -- action bar shows only Mark
   Paid (no Edit / Open Full).
5. Swipe-left on a companion card -- reveal-Paid works.
6. Confirm desktop `/grid` unaffected.

**G. Downstream effects** Companion is unified with owner mobile. Future mobile grid changes
propagate to companion automatically via the shared partial.

**H. Rollback notes** Restore the inline calls in both routes; restore `companion/index.html` to use
the dedicated card template. Service file can stay as a future-use stub.

---

### Commit 14 -- Add Transaction modal-fullscreen-sm-down

**A. Commit message** `feat(mobile-modal): Add Transaction modal-fullscreen-sm-down`

**B. Problem statement** D-F: the Add Transaction modal at `grid.html:303-362` is a standard
Bootstrap modal that centers vertically and gets occluded by the iOS keyboard on a 375x812 viewport.
Bootstrap's `modal-fullscreen-sm-down` class takes over the viewport at `<576 px`. Save button
sticky to the bottom.

## C. Files modified

- `app/templates/grid/grid.html` (lines 303-362). Add
  `modal-fullscreen-sm-down` to the `modal-dialog` class.
  Add `position: sticky; bottom: 0` styling to the modal
  footer (the existing one at lines 355-358) via CSS class.
- `app/static/css/app.css`. Add `.modal-fullscreen-sm-down
  .modal-footer` rule with `position: sticky; bottom: 0;
  background: var(--bs-body-bg); padding-bottom: calc(0.75rem
  - env(safe-area-inset-bottom));`.
- The 10 monetary inputs sweep from Commit 11 already covers
  this modal's amount input.

**D. Implementation approach** One-line change at line 305:

```jinja
{# Before: #}
<div class="modal-dialog">

{# After: #}
<div class="modal-dialog modal-fullscreen-sm-down">
```

CSS sticky footer (Bootstrap handles the fullscreen layout):

```css
@media (max-width: 575.98px) {
  .modal-fullscreen-sm-down .modal-footer {
    position: sticky;
    bottom: 0;
    background: var(--bs-body-bg);
    padding-bottom: calc(0.75rem + env(safe-area-inset-bottom));
    border-top: 1px solid var(--bs-border-color);
  }
}
```

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C14-1 | `test_add_modal_has_fullscreen_class` | GET `/grid` | inspect | `<div class="modal-dialog modal-fullscreen-sm-down">` |
| C14-2 | `test_add_modal_amount_has_inputmode` | inspect (from Commit 11) | check | `inputmode="decimal"` present |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. Open `/grid` mobile (375x812). Tap "Add Transaction" --
   modal takes over the viewport. Form fields visible top to
   bottom. Save button at the bottom, sticky.
3. Focus the amount input. iOS keyboard appears. Save button
   stays reachable above the keyboard.
4. Desktop unaffected: tap "Add Transaction" at 1920x1080,
   modal is the existing centered dialog.

**G. Downstream effects** The Add Transaction modal is the mobile pattern reference for future
modals.

**H. Rollback notes** Remove `modal-fullscreen-sm-down` class and the CSS rule.

---

### Commit 15 -- reserved

Reserved for a cross-phase regression fix discovered during Commits 12-14. If unused, renumber
Commits 16-28 down by 1 during the gate commit (Commit 28).

---

### Commit 16 -- settings sidebar -> shekel-scroll-pills on mobile

**A. Commit message** `feat(mobile-settings): sidebar -> shekel-scroll-pills on mobile`

**B. Problem statement** D-K. The settings sidebar at `app/templates/settings/dashboard.html` stacks
above the content on mobile; the user scrolls past the 8 section links to reach the form. Replace
with a horizontal scroll-pills row at the top on mobile.

## C. Files modified

- `app/templates/settings/dashboard.html`. Wrap the existing
  sidebar `<div class="col-md-3">` block in `d-none
  d-md-block`. Add a sibling at the top: `<div class="d-md-none
  mb-3"><ul class="nav nav-pills shekel-scroll-pills">...</ul></div>`
  containing the same section links.

**D. Implementation approach** Re-read `settings/dashboard.html` in full. Identify the section-link
block; the mobile pills row mirrors it.

```jinja
{# At the top of the settings dashboard content, before the row: #}
<div class="d-md-none mb-3">
  <ul class="nav nav-pills shekel-scroll-pills" role="tablist">
    {% for section in sections %}
      <li class="nav-item">
        <a class="nav-link {{ 'active' if section.key == current_section else '' }}"
           href="{{ url_for('settings.dashboard', section=section.key) }}">
          {{ section.label }}
        </a>
      </li>
    {% endfor %}
  </ul>
</div>

{# Existing layout, sidebar now hidden on mobile: #}
<div class="row">
  <div class="col-md-3 d-none d-md-block">
    {# existing list-group sidebar #}
  </div>
  <div class="col-md-9">
    {# existing content #}
  </div>
</div>
```

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C16-1 | `test_settings_mobile_pills_row_present` | GET `/settings` | inspect | `<ul class="nav nav-pills shekel-scroll-pills">` inside a `d-md-none` block |
| C16-2 | `test_settings_sidebar_hidden_on_mobile` | inspect | check | sidebar has `d-none d-md-block` |
| C16-3 | `test_settings_active_section_marked` | GET `/settings?section=tax` | inspect | the "Tax" pill has class `active` |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/test_settings.py -v` green.
2. Open `/settings` mobile. Scroll-pills row at top; content
   below.
3. Tap a pill -- navigates to that section.
4. Desktop unaffected: sidebar visible, pills row hidden.

**G. Downstream effects** Pattern available for any future multi-section settings/admin page.

**H. Rollback notes** Remove the pills row block; remove the `d-none d-md-block` from the sidebar.

---

### Commits 17-20 -- list pages card-on-mobile (accounts, salary, templates, transfers)

These four commits share the same pattern; documenting once here with file-specific variations
called out. Each ships as its own commit (per D-L) so any regression is bisectable.

## A. Commit messages (each)

- 17: `feat(mobile-accounts): cards on mobile in accounts/list.html`
- 18: `feat(mobile-salary): cards on mobile in salary/list.html`
- 19: `feat(mobile-templates): cards on mobile in templates/list.html`
- 20: `feat(mobile-transfers): cards on mobile in transfers/list.html`

**B. Problem statement** Each list page renders a wide `<table>` with `table-responsive` (the v1
horizontal-scroll fallback). Mobile users see a tiny truncated table with horizontal scroll. Replace
with a card layout sibling on mobile.

**C. Files modified (per commit)** The respective `app/templates/<feature>/list.html`. No other
change.

**D. Implementation approach (per commit)** Re-read the file. Wrap the existing
`<table class="table">` (or its `table-responsive` container) in `<div class="d-none d-md-block">`.
Add a sibling `<div class="d-md-none">` containing a card per row. Each card shows the most
important fields prominently; secondary fields below; actions in a dropdown or icon row at the
bottom.

**Per-commit specifics:**

- **17 / accounts**: card shows name + current balance
  prominently; account type below; actions (Edit, Archive,
  Detail) at bottom.
- **18 / salary**: card shows profile name + estimated net
  biweekly prominently; annual salary + filing status below;
  actions in the existing mobile dropdown from v1 commit
  `a3e9467` (preserve).
- **19 / templates**: card shows template name + amount
  prominently; recurrence pattern + category below; actions
  (Edit, Archive, Delete).
- **20 / transfers**: card shows transfer name + amount
  prominently; from->to + recurrence below; actions.

## E. Test cases (per commit)

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C<n>-1 | `test_table_hidden_on_mobile` | GET `/<feature>` | inspect | table has `d-none d-md-block` |
| C<n>-2 | `test_card_list_present_on_mobile` | inspect | check | `<div class="d-md-none">` with one card per row |
| C<n>-3 | `test_card_data_matches_table_row` | seeded data | inspect | each card's prominent fields match the corresponding table row's data |
| C<n>-4 | `test_desktop_table_unchanged` | GET at desktop viewport | inspect | table renders byte-identical to pre-commit |

**F. Manual verification (per commit)** Open `/<feature>` at 375x812 -- cards readable, no
horizontal scroll. Open at 1920x1080 -- table unchanged.

**G. Downstream effects (per commit)** Each list page is self-contained; no cross-commit dependency.

**H. Rollback notes (per commit)** Remove the `d-md-none` sibling block; remove `d-none d-md-block`
from the table.

---

### Commit 21 -- retirement account table cards + popovers

**A. Commit message**
`feat(mobile-retirement): cards + popover tooltips on retirement account table`

**B. Problem statement** Same pattern as Commits 17-20, plus the `title=""` hover tooltips at
`retirement/_retirement_account_table.html` (flagged in `mobile_friendliness_assessment.md:82-86`)
get replaced with Bootstrap popovers per the v1 commit `921de65` pattern (which applied this to a
different retirement file). Hover-only tooltips are inaccessible on touch.

## C. Files modified

- `app/templates/retirement/_retirement_account_table.html`.

**D. Implementation approach** Same card/table sibling as Commits 17-20. Plus: for every
`title="..."` on an info icon, replace with
`data-bs-toggle="popover" data-bs-trigger="click focus" data-bs-title="..."`.

The popover init JS at `app/static/js/app.js` already initialises popovers globally on HTMX swaps
(v1 commit `921de65`); no JS change needed.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C21-1 | `test_retirement_table_hidden_on_mobile` | GET `/retirement` | inspect | table d-none d-md-block |
| C21-2 | `test_retirement_cards_on_mobile` | inspect | check | card list under d-md-none |
| C21-3 | `test_no_title_only_tooltips_on_info_icons` | grep | `grep -nE 'title="[^"]+"' app/templates/retirement/_retirement_account_table.html | grep -v 'data-bs-toggle="popover"'` | empty (every title-bearing info icon has been converted) |

**F. Manual verification** Open `/retirement` mobile -- cards render readable; tap an info icon --
popover opens; tap elsewhere -- popover closes.

**G. Downstream effects** None.

**H. Rollback notes** Restore the table-only render; restore `title=` attributes if needed.

---

### Commit 22 -- dashboard mobile ordering + loan schedule column hides + dashboard mark-paid disposition (ASK)

**A. Commit message**
`feat(mobile-dashboard): order Bills Due first + loan schedule column hides + (mark-paid disposition)`

**B. Problem statement** Three sub-problems:

1. The dashboard's `col-lg-*` splits stack on mobile but the
   stacking order is not optimized: Bills Due (highest
   priority on mobile) should be first via `order-first
   order-lg-N` Bootstrap utility classes.
2. The loan amortization schedule (`loan/_schedule.html`) has
   secondary columns (Escrow, Extra, Rate) that should hide
   on small screens per `mobile_friendliness_assessment.md:240`.
3. The dashboard mark-paid feature (memory entry
   `project_dashboard_redesign_or_remove.md`) needs a
   disposition: keep / redesign / remove.

## C. Files modified

- `app/templates/dashboard/dashboard.html`. Add `order-first
  order-lg-N` to the Bills Due card. Review remaining
  `col-lg-*` splits for stacking sanity.
- `app/templates/loan/_schedule.html`. Add `d-none
  d-lg-table-cell` to Escrow, Extra, Rate column headers and
  cells.

**D. Implementation approach** Sub-problem 3 is asked explicitly during Commit 22 implementation,
BEFORE editing any dashboard code:

> Memory `project_dashboard_redesign_or_remove.md` flags the dashboard mark-paid feature as
> redesign-or-remove. Three options:
>
>     (a) Keep it. Apply the same swipe-left-mark-paid pattern as the grid cards (~80 lines of
>     additional JS+CSS). (b) Redesign it. The bill row becomes a tappable card that opens a small
>     confirmation modal with "Mark Paid" plus "Actual Amount" override; aligns with the grid's
>     `mark_done` route. (c) Remove it. Delete the mark-paid form from `dashboard/_bill_row.html`;
>     the user uses the grid for mark-paid going forward.
>
> Which path?

Sub-problem 1 (Bills Due order) is straightforward; sub-problem 2 (loan schedule columns) is a
three-class edit on three columns.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C22-1 | `test_bills_due_first_on_mobile` | GET `/` mobile | inspect | Bills Due card has `order-first` class |
| C22-2 | `test_loan_schedule_columns_hidden_on_mobile` | GET `/accounts/<id>/loan` mobile | inspect | Escrow/Extra/Rate headers + cells have `d-none d-lg-table-cell` |
| C22-3 | (depends on dashboard mark-paid disposition) | -- | -- | -- |

**F. Manual verification** Open dashboard mobile -- Bills Due at the top. Open loan schedule mobile
-- only the essential columns visible.

**G. Downstream effects** Dashboard disposition affects whether future mobile commits need to
consider this feature.

**H. Rollback notes** Remove the `order-first` and `d-none d-lg-table-cell` classes; revert any
mark-paid edit according to the chosen disposition.

---

### Commit 23 -- navbar -> offcanvas drawer at <md

**A. Commit message** `feat(mobile-nav): navbar -> offcanvas drawer at <md`

**B. Problem statement** D-H. The navbar at `base.html:39-149` uses `navbar-expand-md` with a
collapsing `navbar-collapse`; opening it pushes page content down. Replace with a Bootstrap
offcanvas drawer at `<md`.

## C. Files modified

- `app/templates/base.html` (lines 39-149). Change the
  toggler's `data-bs-toggle="collapse"` (line 44) to
  `data-bs-toggle="offcanvas"`; the `data-bs-target` becomes
  `#mainOffcanvas` (was `#navMain`). The
  `<div class="collapse navbar-collapse" id="navMain">` (line 48)
  becomes `<div class="offcanvas offcanvas-start"
  id="mainOffcanvas" tabindex="-1" aria-labelledby="mainOffcanvasLabel">`.
  Add an offcanvas-header with title + close button; the existing
  nav `<ul>`s move into an offcanvas-body.
- `app/static/css/app.css`. Add offcanvas styling: width 280
  px (vs Bootstrap's default 400 px which is too wide on a
  375 px viewport); nav items >= 44 px tall.

**D. Implementation approach** Re-read `base.html:37-149` in full. The restructure:

```jinja
<nav class="navbar navbar-expand-md navbar-dark sticky-top">
  <div class="container-fluid">
    <a class="navbar-brand" href="...">Shekel</a>
    <button class="navbar-toggler" type="button"
            data-bs-toggle="offcanvas"
            data-bs-target="#mainOffcanvas"
            aria-controls="mainOffcanvas"
            aria-expanded="false"
            aria-label="Toggle navigation">
      <span class="navbar-toggler-icon"></span>
    </button>

    <div class="offcanvas offcanvas-start" id="mainOffcanvas"
         tabindex="-1" aria-labelledby="mainOffcanvasLabel">
      <div class="offcanvas-header">
        <h5 class="offcanvas-title" id="mainOffcanvasLabel">Shekel</h5>
        <button type="button" class="btn-close btn-close-white"
                data-bs-dismiss="offcanvas" aria-label="Close"></button>
      </div>
      <div class="offcanvas-body">
        <ul class="navbar-nav me-auto">
          {# existing nav-item <li> blocks from lines 54-148 verbatim #}
        </ul>
        {# theme toggle + logout form from lines 127-148 #}
      </div>
    </div>
  </div>
</nav>
```

CSS:

```css
@media (max-width: 767.98px) {
  .offcanvas-start {
    width: 280px;
  }
  .offcanvas-body .nav-link {
    min-height: 44px;
    display: flex;
    align-items: center;
  }
}
```

The `navbar-expand-md` class is preserved at the outer `<nav>`. Above `md` the offcanvas markup
behaves as a regular inline nav (Bootstrap handles this transition); below `md` the offcanvas slides
from the left.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C23-1 | `test_offcanvas_markup_present` | GET any page logged in | inspect | `<div class="offcanvas offcanvas-start" id="mainOffcanvas">` present |
| C23-2 | `test_toggler_targets_offcanvas` | inspect | check | `data-bs-toggle="offcanvas"` on the toggler |
| C23-3 | `test_nav_links_carry_forward` | inspect offcanvas-body | count | same number of nav-items as the pre-commit collapse navbar |
| C23-4 | `test_theme_toggle_and_logout_preserved` | inspect | check | both present inside offcanvas-body |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh` full suite green.
2. Open any page mobile -- tap hamburger -- drawer slides
   from left.
3. Tap a link -- navigates; drawer closes.
4. Tap outside the drawer -- closes.
5. Desktop unaffected: nav items visible inline.

**G. Downstream effects** Navigation is consistently slide-over on mobile.

**H. Rollback notes** Restore `data-bs-toggle="collapse"`, restore the original
`<div class="collapse navbar-collapse" id="navMain">` structure.

---

### Commit 24 -- analytics / loan / retirement / investment / debt audit

**A. Commit message** `refactor(mobile-dashboards): analytics/loan/retirement/investment/debt audit`

**B. Problem statement** The remaining dashboards each have specific mobile issues per
`mobile_friendliness_assessment.md` that v1 partially addressed; this commit audits each in turn.

## C. Files modified

- `app/templates/analytics/analytics.html` (and per-tab partials).
  Chart.js charts need `maintainAspectRatio: false` and a
  `min-height` on the container.
- `app/templates/retirement/dashboard.html`. Verify v1 removed
  the hardcoded `width: 7rem` on slider inputs (assessment
  line 84); fix if regressed.
- `app/templates/loan/dashboard.html`. The rate-history
  sub-table gets the card-on-mobile treatment (same pattern
  as Commits 17-20).
- `app/templates/investment/dashboard.html`,
  `app/templates/debt_strategy/dashboard.html`. Verify
  width-removal fixes still in place.

**D. Implementation approach** Each sub-page is a small audit

- fix. Group by file:
- Analytics: locate each Chart.js init call, add
  `maintainAspectRatio: false`. Ensure the container `<div>`
  has `min-height: 250px` or similar so it doesn't crush.
- Retirement: `grep -n "width: 7rem"
  app/templates/retirement/dashboard.html` -- if any matches,
  replace with responsive Bootstrap utilities.
- Loan rate-history: same `d-none d-md-block` table +
  `d-md-none` cards pattern as Commits 17-20.
- Investment + debt_strategy: similar `grep` for hardcoded
  widths.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C24-1 | `test_analytics_charts_responsive` | grep | `grep -nE "maintainAspectRatio:\s*false" app/templates/analytics/_*` | every chart has it |
| C24-2 | `test_no_hardcoded_widths_on_retirement` | grep | `grep -n "width: 7rem" app/templates/retirement/dashboard.html` | empty |
| C24-3 | `test_loan_rate_history_card_on_mobile` | GET `/accounts/<id>/loan` mobile | inspect | rate-history table has d-none d-md-block sibling card list |

**F. Manual verification** Open each dashboard at mobile + desktop viewports; verify charts/tables
render correctly.

**G. Downstream effects** Closes out the remaining mobile audit items from v1's deferred list.

**H. Rollback notes** Per-edit reversion.

---

### Commit 25 -- service worker + /sw.js passthrough route + registration

**A. Commit message** `feat(pwa): service worker + /sw.js passthrough route + registration`

**B. Problem statement** D-I. Add a static-only service worker that speeds repeat loads. No
HTML/JSON in cache. SW served from `/sw.js` (scope `/`).

## C. Files modified

- `app/static/sw.js` (NEW, ~80 lines).
- `app/routes/__init__.py` (or new `app/routes/static_pass.py`).
  One passthrough route.
- `app/static/js/app.js` (top). SW registration in a
  feature-check guard.

## D. Implementation approach

`sw.js`:

```javascript
const CACHE = 'shekel-static-v1';
const STATIC_PREFIXES = [
  '/static/vendor/',
  '/static/css/',
  '/static/js/',
  '/static/img/',
  '/static/fonts/',
  '/static/manifest.json',
];

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names
        .filter(function (n) { return n.startsWith('shekel-static-') && n !== CACHE; })
        .map(function (n) { return caches.delete(n); })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  if (event.request.method !== 'GET') return; // pass-through for mutations
  var url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return; // cross-origin pass-through
  var isStatic = STATIC_PREFIXES.some(function (prefix) {
    return url.pathname.startsWith(prefix);
  });
  if (!isStatic) return; // network-only for HTML/JSON; NO respondWith

  // Cache-first for static
  event.respondWith(
    caches.open(CACHE).then(function (cache) {
      return cache.match(event.request).then(function (cached) {
        if (cached) return cached;
        return fetch(event.request).then(function (response) {
          if (response.ok) cache.put(event.request, response.clone());
          return response;
        });
      });
    })
  );
});
```

Passthrough route in `app/routes/__init__.py` or a new small module:

```python
from flask import send_from_directory, current_app

@app.route('/sw.js')
def service_worker():
    """Serve sw.js from the static folder at the root scope.

    Required because the browser scopes the service worker to
    the directory containing the worker file.  Serving from
    /static/sw.js would scope to /static/, which excludes app
    routes.  Serving from /sw.js scopes to /.
    """
    return send_from_directory(
        current_app.static_folder,
        'sw.js',
        mimetype='application/javascript',
    )
```

`app.js` registration:

```javascript
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}
```

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C25-1 | `test_sw_js_route_serves_static_file` | curl `/sw.js` | status code | 200, content-type `application/javascript` |
| C25-2 | `test_sw_js_does_not_cache_html` | source audit | `grep -n "html\|/$\|/grid\b" app/static/sw.js` | no HTML pattern in cache logic; STATIC_PREFIXES is /static/ only |
| C25-3 | `test_app_js_registers_sw` | grep | `grep -n "navigator.serviceWorker.register" app/static/js/app.js` | one match |

Re-pinned tests: none.

## F. Manual verification steps

1. `./scripts/test.sh tests/test_routes/ -v` green (the new
   route does not break existing tests).
2. `curl -I http://localhost:5000/sw.js` -> 200 + `Content-Type:
   application/javascript`.
3. Open any page in Firefox; check DevTools -> Application ->
   Service Workers: scope `/`, status `activated`.
4. Reload; check DevTools -> Network: static assets show
   `(ServiceWorker)` as the source; HTML and JSON show
   `(disk cache)` or fresh network.
5. DevTools -> Network -> Offline; reload -- HTML request
   fails with a network error; static assets still load.
6. Real iPhone: install via "Add to Home Screen"; open from
   home screen; confirm SW active.
7. Cache audit: in DevTools -> Application -> Cache Storage ->
   `shekel-static-v1`, every URL starts with `/static/`.

**G. Downstream effects** Subsequent loads are faster. PWA behavior is closer to "installed app"
feel.

**H. Rollback notes** Delete `sw.js`, the route, and the registration call. Existing SW
installations on user devices will eventually unregister when the route returns 404 (which it will
after revert); to force, the user can manually unregister via DevTools.

---

### Commit 26 -- reserved

Reserved for any cross-phase regression discovered during Commits 23-25.

---

### Commit 27 -- manifest maskable icons + Apple-specific 180/167 sizes

**A. Commit message** `feat(pwa): manifest maskable icons + Apple-specific 180/167 sizes`

**B. Problem statement** D-J. The existing `app/static/manifest.json` has 192/512 icons but no
`purpose: "any maskable"`. iOS crops icons unless they declare maskable intent. Apple-specific 180 x
180 and 167 x 167 sizes may be referenced by `base.html:27` `<link rel="apple-touch-icon">` but not
present in `app/static/img/`.

## C. Files modified

- `app/static/manifest.json`. Audit; add `purpose: "any
  maskable"` to icon entries.
- `app/static/img/icon-180.png`, `icon-167.png` (NEW). Generated
  from the existing 512.
- `app/templates/base.html:27` (if needed). Ensure the
  `apple-touch-icon` link resolves to a present file.

**D. Implementation approach** Audit `manifest.json`:

```json
{
  "name": "Shekel",
  "short_name": "Shekel",
  "icons": [
    {
      "src": "/static/img/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/static/img/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ],
  "theme_color": "#4A9ECC",
  "background_color": "#ffffff",
  "display": "standalone",
  "start_url": "/"
}
```

Generate the Apple icons (180/167) using ImageMagick:

```bash
convert app/static/img/icon-512.png -resize 180x180 app/static/img/icon-180.png
convert app/static/img/icon-512.png -resize 167x167 app/static/img/icon-167.png
```

Update `base.html:27` (if the v1 link references one of these):

```html
<link rel="apple-touch-icon" sizes="180x180" href="{{ url_for('static', filename='img/icon-180.png') }}">
<link rel="apple-touch-icon" sizes="167x167" href="{{ url_for('static', filename='img/icon-167.png') }}">
```

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C27-1 | `test_manifest_has_maskable_icons` | parse manifest.json | inspect | each icon entry has `purpose: "any maskable"` |
| C27-2 | `test_apple_touch_icons_exist` | filesystem | check | `icon-180.png` and `icon-167.png` present |

**F. Manual verification** Reload PWA on iPhone; icon updates on next "Add to Home Screen".

**G. Downstream effects** Installable-app icons display correctly on iOS.

**H. Rollback notes** Revert manifest edit; remove generated icons.

---

### Commit 28 -- mobile v3 full gate + verification appendix

**A. Commit message** `chore(release): mobile v3 full gate + verification appendix`

**B. Problem statement** Final gate. Full suite, pylint clean, manual verification across both
viewports + real devices. No code change in this commit beyond appending the verification appendix
to this plan (Section 11).

## C. Files modified

- `docs/implementation_plan_mobile_v3.md` (this file). Append
  the verification appendix.

**D. Implementation approach** Run all gates. Capture screenshots if useful. Document the final
state.

## E. Test cases

| ID | Test name | Setup | Action | Expected |
|---|---|---|---|---|
| C28-1 | `full_suite_green` | full project | `./scripts/test.sh` | `N passed`, no failed/errors/xfailed |
| C28-2 | `pylint_clean` | full project | `pylint app/ --fail-on=E,F` | clean |
| C28-3 | `static_guards_green` | `tests/test_static_guards.py` | targeted | no new `Account.current_anchor_*` or `LoanParams.current_principal` reads |

**F. Manual verification** Walk every workflow from Section 0: mark paid, edit amount, add ad-hoc,
review balances. On both viewports + both real iPhones.

**G. Downstream effects** v3 mobile work complete. The plan is the canonical reference.

**H. Rollback notes** N/A.

---

## 10. Cross-cutting verification

Beyond per-commit Section F, the plan's whole-project gates:

1. **CI gate per phase.** Each phase's last commit is the
   per-phase final commit; CI must be green before merging
   `dev -> main`. CI runs `pylint app/ --fail-on=E,F` plus
   the full pytest suite per `.github/workflows/ci.yml`.
2. **Desktop regression.** Firefox 1920x1080. Walk the touched
   pages, confirm zero visible change vs pre-phase. Phase 1
   (Commits 1-4) specifically must be byte-identical HTML on
   desktop.
3. **Mobile viewport coverage.** Firefox responsive design
   mode at 375x812 (iPhone XS) and 430x932 (iPhone 16 Plus).
   Walk the four daily workflows on every touched page.
4. **Real iPhone testing.** Before each phase merges to
   `main`, side-load via "Add to Home Screen" on the actual
   XS and 16 Plus in Firefox iOS. Walk the workflows.
5. **Companion check (Commits 13+).** Log in as the companion
   test user and walk her daily flow on a mobile viewport.
6. **Financial-correctness guard.** Anchor balance, projected
   balance, subtotals, period net cash flow identical before
   and after any commit in Phases 1-5 (the financial-resolver
   invariants are not touched by this plan; the static guard
   at `tests/test_static_guards.py` enforces no new direct
   reads).
7. **SW cache audit.** Post-Commit-25: DevTools -> Application
   -> Cache Storage -> `shekel-static-v1`. Every entry starts
   with `/static/`. No HTML, no JSON.

---

## 11. Verification appendix (filled in at Commit 28)

This section is appended at Commit 28 implementation time and records the final state of the work:
which commits landed, which screenshots / DevTools captures were taken, which user acceptance tests
passed, which OPT-* items were promoted (if any).

Filled in: 2026-05-25 at Commit 28 (`chore(release): mobile v3 full gate +
verification appendix`).  Resolved against the commit history via
`git log --oneline --reverse 207e31c..HEAD` (the plan-document anchor SHA
named in Section 1 rule 1).

### Commits landed

The Section 8 numbering and what shipped:

| # | Commit message | SHA |
|--:|---|---|
| 1 | `feat(grid): add render_row_cells + render_row_card macros` | `21d39a5` |
| 2 | `feat(grid): precompute matched_by_row_period in index route` | `2619c20` |
| 3 | `refactor(grid): desktop grid uses render_row_cells macro` | `3bcc003` |
| 4 | `refactor(grid): mobile grid uses render_row_card macro` | `7fa54a3` |
| 5 | `feat(mobile-grid): nav-pills tab scaffold for This Period / Plan` | `b19aab7` |
| 6 | `feat(mobile-grid): _mobile_this_period.html partial with arrows` | `3ea6960` |
| 7 | `feat(mobile-grid): _mobile_plan.html + inline card action bar` | `3383df1` |
| 8 | `feat(mobile-grid): bottom-sheet drag-to-dismiss + iOS keyboard avoidance` | `949a655` |
| 9 | `feat(mobile-grid): swipe-left reveals Mark Paid button on cards` | `446684d` |
| 10 | `feat(mobile-grid): jump-to period <select> in This Period header` | `9675b76` |
| 11 | `feat(forms): inputmode="decimal" on 10 monetary inputs` | `ac470d2` |
| 12 | `feat(mobile-sheet): sticky action footer in full-edit popover` | `1546c84` |
| 13 | `refactor(grid): extract grid_view_service + companion uses This Period partial + swipe.js shared` | `f884fd0` |
| 14 | `feat(mobile-modal): Add Transaction modal-fullscreen-sm-down` | `f12264d` |
| 15 | (reserved -- unused) | -- |
| 16 | `feat(mobile-settings): sidebar -> shekel-scroll-pills on mobile` | `6e807d8` |
| 17 | `feat(mobile-accounts): cards on mobile in accounts/list.html` | `69fc665` |
| 18 | `feat(mobile-salary): cards on mobile in salary/list.html` | `a5457af` |
| 19 | `feat(mobile-templates): cards on mobile in templates/list.html` | `9771eb8` |
| 20 | `feat(mobile-transfers): cards on mobile in transfers/list.html` | `95de055` |
| 21 | `feat(mobile-retirement): cards + popover tap trigger on retirement account table` | `5f31d5b` |
| 22 | `feat(mobile-dashboard): order Bills Due first + remove dashboard mark-paid form` | `e079a4e` |
| 23 | `feat(mobile-nav): navbar -> offcanvas drawer at <md` | `9afa579` |
| 24 | `refactor(mobile-dashboards): analytics/loan/retirement/investment/debt audit` | `7e65b9e` |
| 25 | `feat(pwa): service worker + /sw.js passthrough route + registration` | `1c19cd6` |
| 26 | (reserved -- unused) | -- |
| 27 | `feat(pwa): manifest maskable icons + Apple-specific 180/167 sizes` | `96f7ed7` |

Two supplementary commits landed outside the Section 8 numbering:

- `13b9086` -- `chore(test): playwright manual verification harness`.
  Landed between Commits 5 and 6.  Materialises the per-commit
  Manual browser verification gate from
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
  (storage_state login, headless Chromium at 375x812, `CheckResult`
  dataclass, per-step screenshot under `tests/manual/screenshots/`).
  Every subsequent commit with browser-visible UX (6, 7, 8, 9, 10,
  14, 23) ships a sibling `verify_*.py` script.
- `41ab404` -- `feat(mobile-retirement): convert gap-analysis title
  tooltips to popovers`.  Landed between Commits 21 and 22.  Extends
  Commit 21's popover-replacing-title pattern to the gap-analysis
  info icons that the Commit 21 scope did not cover.  No new logic;
  same Bootstrap popover wiring.

Commit 28 (this commit, `chore(release): mobile v3 full gate +
verification appendix`) lands the populated appendix only.  Its SHA
is not pre-recordable.

### Final test gate

Captured in the Commit 28 work summary section E (the load-bearing
evidence rows of the gate):

- `./scripts/test.sh`:
  `================= 5669 passed, 3 warnings in 67.02s (0:01:07) ==================`
- `pylint app/ --fail-on=E,F`:
  `Your code has been rated at 9.58/10 (previous run: 9.58/10, +0.00)`
- Static guards (`SKIP_DB_RESTART=1 ./scripts/test.sh
  tests/test_routes/test_grid.py::TestGridPeriodSubtotalCanonical::test_grid_inline_subtotal_loop_removed
  tests/test_routes/test_grid.py::TestGridPeriodSubtotalCanonical::test_grid_balance_computation_routed_through_resolver
  tests/test_routes/test_accounts.py::TestCheckingDetailCanonicalProducer::test_accounts_checking_balance_routed_through_resolver
  -v`):
  `============================== 3 passed in 1.63s ===============================`

Note on Section 6.8 / Section 10 item 6 phrasing: those bullets refer
to `tests/test_static_guards.py` as a standalone path.  No such file
exists.  The three regression locks the F-6 commit (`842d415`) added
live as test methods on `TestGridPeriodSubtotalCanonical` (in
`tests/test_routes/test_grid.py`) and
`TestCheckingDetailCanonicalProducer` (in
`tests/test_routes/test_accounts.py`).  The lock semantics are
unchanged -- `balance_resolver.balances_for` must appear in the
route source, `balance_calculator.calculate_balances(` must not --
and all three pass against the dev HEAD at Commit 27.

A targeted grep of `app/routes/`, `app/templates/`, and
`app/static/js/` for `current_anchor_balance`, `current_anchor_period_id`,
`current_principal`, and `interest_rate` returns no new direct reads
since the v3 work began (the single `app/routes/grid.py:127` site
that surfaces is unchanged since `7e1ac9b`, the pre-v3
canonical-producer landing).

### Manual verification matrix

All four daily workflows (Section 0) walked end-to-end on every
viewport / device row:

- Mark a transaction paid via the inline action bar AND via
  swipe-left-to-reveal.
- Edit a transaction's actual amount via `[Edit Amount]` AND via
  `[Open Full]`.
- Add an ad-hoc transaction through the `modal-fullscreen-sm-down`
  Add Transaction modal (Commit 14 / `f12264d`).
- Review upcoming bills and the projected end balance in both the
  "This Period" and "Plan" tabs.

| Viewport / device | Engine | Result |
|---|---|---|
| Firefox Desktop 1920x1080 | Gecko | PASS (zero desktop regression) |
| Firefox Responsive 375x812 (iPhone XS sim) | Gecko | PASS |
| Firefox Responsive 430x932 (iPhone 16 Plus sim) | Gecko | PASS |
| Real iPhone XS, Firefox iOS | WebKit | PASS |
| Real iPhone 16 Plus, Firefox iOS | WebKit | PASS |

Per-commit screenshot evidence is preserved by the Playwright
harness (`13b9086`) under `tests/manual/screenshots/commit{6,7,8,9,
10,14,23}_*.png`.  Each `verify_*.py` exited 0 on its commit; the
files remain as regression locks for later commits that touch the
same surface.

### SW cache audit (Section 10 item 7)

Deferred at Commit 28 by user election.  Tracked as follow-up
`F-10` in `docs/mobile_follow_up.md`.  The invariant is enforced
statically by `app/static/sw.js`'s `STATIC_PREFIXES` allow-list plus
the cache-first guard (only `/static/*` URLs reach `cache.put`); the
DevTools capture is the documented regression check, not the
enforcement point.  The capture should be taken before any future
edit to `app/static/sw.js`.

### OPT-* items promoted

None.  OPT-M1 through OPT-M8 (Section 5) all remain listed-only.
No commit body in 1-27 references an OPT-M item; no extension was
folded into a commit during execution.

### Open questions -- final state

Resolved during commit execution:

- **Q-1 (dashboard mark-paid disposition).** Resolved at Commit 22
  (`e079a4e`) as option (c) REMOVE.  The `<button>` was deleted from
  `dashboard/_bill_row.html`.  Bills are marked paid from the grid
  (`transactions.mark_done`) going forward.  The orphaned
  `dashboard.mark_paid` route, `MarkPaidSchema`, and seven
  `TestMarkPaid` tests remain inert -- cleanup recipe captured in
  `docs/mobile_follow_up.md` `F-9`.
- **Q-2 (companion subtotals visibility).** Resolved at Commit 13
  (`f884fd0`) as option (c) GRACEFUL-OMIT.  `_mobile_this_period.html`
  checks for the presence of subtotal / balance keys in its context
  and renders only what the caller supplies.  Companion preserves
  its no-subtotals behaviour; the grid route continues to pass the
  canonical-producer values.  If the user later wants subtotals
  visible in companion, plumb `balance_resolver.period_subtotal`
  through `app/routes/companion.py` (allowed per Rule 2).

Resolved at plan-write time (recorded for completeness):

- **Q-3 (bottom-tab-bar vs offcanvas drawer).** Resolved via
  Section 2 D-H as offcanvas.  OPT-M8 (Section 5) keeps the
  bottom-tab-bar option listed for a future iteration if usage data
  suggests it.  Commit 23 (`9afa579`) shipped the offcanvas
  implementation.
- **Q-4 (push notifications).** Out of scope for v3 (Section 14).
  The static-only service worker (D-I) does not preclude a v4
  push-event handler.

Carried forward into the next plan cycle:

- None.  Section 12 still names Q-3 and Q-4 as forward candidates;
  both are out of scope for v3 by design.

---

## 12. Open questions carried forward

- **Q-1. Dashboard mark-paid disposition** (keep / redesign /
  remove). Asked at Commit 22. Tracked in memory entry
  `project_dashboard_redesign_or_remove.md` until resolved.
  Recommendation: if the user uses the grid for all
  mark-paid, the dashboard mark-paid is redundant and should
  be removed; if dashboard is the "what to act on now" page,
  it's worth keeping with the swipe-action pattern.

- **Q-2. Should `_mobile_this_period.html` show subtotals
  for companions?** Resolved in Commit 13 D as (c): the
  partial gracefully omits subtotal / balance sections when
  the context lacks them. Companion does not show subtotals
  today; preserving that behavior. If the user later wants
  subtotals visible in companion, plumb `balance_resolver.period_subtotal`
  through the companion route (allowed per Rule 2).

- **Q-3. Bottom tab bar vs offcanvas drawer.** Resolved via
  D-H as offcanvas; OPT-M8 carries the bottom-tab-bar option
  forward for a future iteration if usage data suggests it.

- **Q-4. Push notifications.** Out of scope for v3 (v2's
  Phase B / Section 4 of the roadmap covers this). If the
  user wants push, a v4 plan picks it up; D-I's static-only
  service worker does not preclude later adding a push event
  handler.

---

## 13. Notes on executing this plan

- Run commits in order **within each phase**; phases can be
  reordered freely (Phase 4 and Phase 5 are independent of
  Phases 2-3, see Section 7). Phase 1 must complete before
  Phase 2; Phase 2 must complete before Phase 3.
- Every commit: re-grep cited lines first (line numbers
  drift); targeted tests during edits
  (`./scripts/test.sh tests/path/test_file.py -v`); `pylint
  app/ --fail-on=E,F` after edits; full suite via
  `./scripts/test.sh` as the per-commit final gate.
  `SKIP_DB_RESTART=1` on follow-up invocations in the same
  session.
- The test template does NOT need rebuilding by this plan (no
  schema changes, no `app/ref_seeds.py` or
  `app/audit_infrastructure.py` edits, no migrations).
- Never silently re-pin a test. The plan calls out "Re-pinned
  tests: none" on every commit; if execution surfaces a test
  that needs re-pinning, name the finding and the hand
  arithmetic in a comment, per CLAUDE.md rule 5.
- Every session ends with a work summary (the project's
  remediation-style A-M labels or a shorter ad-hoc summary;
  the plan is template/CSS/JS-heavy so the financial
  remediation's full A-M is not strictly required). The
  summary documents what landed, what stayed in scope, what
  was flagged out of scope (with `file:line` + reason), and
  asks "Ready to commit and push to dev?" -- do NOT push
  without explicit approval.
- This is an implementation plan only. No code is changed by
  producing this document. Execution happens in separate
  sessions; one commit per session, suite green before
  moving on.

---

## 14. Out of scope

- Service-worker offline editing queue. Read-only static
  caching only (D-I).
- Mobile-specific routes (`/m/...`). Single template tree with
  responsive Jinja + CSS (Rule 5).
- Web push notifications. Dropped from v2; if the user wants
  push, it lands in a v4 plan.
- New financial calculations or business logic. All math,
  status transitions, balance projections stay in the
  existing `app/services/` modules (Rule 4).
- Native iOS app, Capacitor wrapper, React Native rewrite.
  v1's `docs/mobile_friendliness_assessment.md` Tier 3
  assessment stands: disproportionate for a single-user app.
- New testing framework for touch events. Manual verification
  at the listed viewports + real devices is the chosen
  approach.
- Dashboard redesign beyond mobile ordering / mark-paid
  disposition (Commit 22). The dashboard's overall
  information architecture is a v4 candidate, not a v3
  concern.
