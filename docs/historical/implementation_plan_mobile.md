> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Mobile Responsiveness

**Version:** 1.0
**Date:** April 6, 2026
**Prerequisite:** All prior sections (1-8, 5A, 5) complete.
**Scope:** Tier 1 mobile-responsive web from `docs/mobile_friendliness_assessment.md`, plus
web app manifest. CSS, JS, and template changes only -- no service, model, or route
modifications. No changes to the desktop experience.

---

## Documentation vs. Code Discrepancies

The following discrepancies were found between `docs/mobile_friendliness_assessment.md` and the
current codebase. The implementation plan is based on the code, not the assessment.

### D-1: `loan/_rate_history.html` already has `table-responsive`

**Assessment says:** Listed among "~8 tables lacking `table-responsive` wrappers" and rated
Needs-Work with "Missing `table-responsive`."

**Code says:** `app/templates/loan/_rate_history.html:16` already has
`<div class="table-responsive">`.

**Impact:** Task Group A wraps 7 tables, not 8. This file is excluded from Commit #1.

### D-2: `obligations/summary.html` tables already have `table-responsive` wrappers

**Assessment says:** Rated Needs-Work for "3 large tables (6-7 columns each)", implying missing
wrappers.

**Code says:** All three tables have `<div class="table-responsive">` wrappers:
`obligations/summary.html:81` (expenses), `:129` (transfers), `:178` (income).

**Impact:** This template needs column hiding (Commit #7), not responsive wrappers. Commit #1
does not touch this file.

### D-3: Loan dashboard has 5 or 6 tabs, not always 6

**Assessment says:** "6 tabs in `nav nav-tabs` -- will wrap onto two lines."

**Code says:** The Rate History tab (`loan/dashboard.html:30-35`) is conditional on
`params.is_arm`. Non-ARM loans show 5 tabs (Overview, Escrow, Amortization Schedule, Payoff
Calculator, Refinance Calculator).

**Impact:** Commit #8 handles both the 5-tab and 6-tab cases. The scrollable treatment is
needed either way since 5 long tab labels still wrap on narrow screens.

### D-4: `salary/tax_config.html` is a 27-line thin wrapper

**Assessment says:** "Table layout may overflow."

**Code says:** The template (`salary/tax_config.html:23`) delegates all content to
`{% include "settings/_tax_config_sections.html" %}`. The outer wrapper uses `col-lg-10` which
is already responsive. Any table overflow is in the included partial, which is covered by the
`settings/_*.html` group in the assessment.

**Impact:** This template requires no direct fix. It is already functional at mobile widths.

### D-5: Assessment CSS line numbers are off by 1-2 in several places

**Assessment says:** Grid column widths at "app.css:258-281", `.txn-cell` at "465-487", cell
hover at "471-474".

**Code says:** `.row-label-col` starts at line 257. `.txn-cell` block spans 465-479.
`.txn-cell:hover` is at lines 472-474.

**Impact:** Minor. All line references in this plan use current code positions.

### D-6: Grid route data structure confirmed -- no route changes needed

**Assessment says:** "The data is already structured per-period in the route, so no service
changes needed."

**Code confirms:** `app/routes/grid.py:219-222` builds `txn_by_period` as
`dict[period_id -> list[Transaction]]`. The route also passes `subtotals[period.id]` (income,
expense, net as Decimal), `balances[period.id]` (projected end balance as Decimal),
`income_row_keys` and `expense_row_keys` (sorted RowKey namedtuples with category grouping).
All data needed for the mobile card view is already in the template context. No service, model,
or route changes are needed for Commit #10.

---

## Automated Frontend Testing Assessment

**Recommendation: Do not adopt Playwright for this work.**

**Setup:** Playwright has a Python client (`pip install playwright`) so no Node.js is needed.
Browser binaries (~500MB) must be installed via `playwright install chromium`. Tests would use
`pytest-playwright` and require a running Flask dev server with a seeded test database.

**What it catches:** Element visibility at breakpoints (`d-none` toggling), overflow, element
sizing. These are the same things verified by resizing a browser window in DevTools.

**What it misses:** Visual aesthetics, readability, touch feel, scroll behavior -- the things
that matter most for mobile UX.

**Maintenance cost:** Screenshot baselines break on any CSS change (theme colors, spacing,
content differences), producing false positives. Each template modification requires reviewing
and potentially updating baseline images. For a solo developer, this maintenance burden
outweighs the value.

**Alternative:** Every commit in this plan includes manual verification steps specifying exact
viewport widths (320px, 375px, 428px, 768px) and specific checks at each width. This is faster,
catches more real issues, and has zero maintenance cost.

---

## Needs-Work Template Resolution

Every template rated "Needs-Work" in the assessment is addressed by a specific commit or
determined to need no fix. Templates rated "Broken" are resolved by Commits #6, #9, and #10.

### Needs-Work Templates

| Template | Commit | Resolution |
|----------|--------|------------|
| `accounts/list.html` | #1 | Add `table-responsive` wrapper |
| `salary/list.html` | #1, #7 | Add wrapper; hide Filing Status and State columns; collapse action buttons to dropdown |
| `salary/tax_config.html` | -- | No fix needed -- thin wrapper delegates to included partial; `col-lg-10` is already responsive |
| `salary/_deductions_section.html` | #1 | Add `table-responsive` wrapper |
| `salary/_raises_section.html` | #1 | Add `table-responsive` wrapper |
| `transfers/list.html` | #1, #7 | Add wrapper; hide Recurrence column |
| `transfers/_transfer_quick_edit.html` | #6 | Touch target fixes (expand button, input sizing) |
| `transfers/_transfer_full_edit.html` | #9 | Bottom sheet on mobile |
| `templates/list.html` | #1, #7 | Add wrapper; hide Category and Type columns |
| `templates/form.html` | -- | No fix needed -- `col-md-8 col-lg-6` form with recurrence fields stacks naturally via Bootstrap grid |
| `savings/dashboard.html` | #1 | Add `flex-wrap` to header button group |
| `settings/dashboard.html` | -- | No fix needed -- `col-md-3`/`col-md-9` sidebar stacks at `<768px` via Bootstrap grid behavior |
| `settings/_*.html` (7 partials) | -- | No fix needed -- form layouts inside responsive Bootstrap columns stack naturally |
| `loan/dashboard.html` | #8 | Convert tabs to scrollable pills on mobile |
| `loan/_schedule.html` | #7 | Hide Escrow, Extra, and Rate columns on mobile |
| `loan/_rate_history.html` | -- | No fix needed -- already has `table-responsive` (D-1) |
| `retirement/dashboard.html` | #2, #4 | Remove hardcoded `7rem` widths; convert info icon tooltips to popovers |
| `retirement/_retirement_account_table.html` | #1, #4 | Add `table-responsive` wrapper; convert info icon tooltips to popovers |
| `investment/dashboard.html` | #2 | Remove hardcoded `100px` width |
| `debt_strategy/dashboard.html` | #2 | Remove hardcoded `80px` width |
| `obligations/summary.html` | #7 | Hide Account and Category columns in expense table; hide Account column in income table |

### Broken Templates

| Template | Commits | Resolution |
|----------|---------|------------|
| `grid/grid.html` | #10 | New mobile card view (`d-md-none` toggle); desktop grid unchanged behind `d-none d-md-block` |
| `grid/_transaction_quick_edit.html` | #6 | Touch target fixes (expand button) |
| `grid/_transaction_quick_create.html` | #6 | Touch target fixes (expand button) |
| `grid/_transaction_full_edit.html` | #6, #9 | Touch target fixes; bottom sheet on mobile |
| `grid/_transaction_full_create.html` | #6, #9 | Touch target fixes; bottom sheet on mobile |

---

## Codebase Inventory

Every file that this plan will create, modify, or depend on. Built from reading the actual
files on April 6, 2026.

### Templates (modified)

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/templates/base.html` | 247 | App shell, navbar, scripts | #4, #10, #11 |
| `app/templates/grid/grid.html` | 334 | Budget grid with income/expense rows | #10 |
| `app/templates/grid/_transaction_cell.html` | 59 | Transaction display cell | #4 |
| `app/templates/accounts/list.html` | 90 | Account list table | #1 |
| `app/templates/salary/list.html` | 101 | Salary profile list table | #1, #7 |
| `app/templates/transfers/list.html` | 122 | Recurring transfer list table | #1, #7 |
| `app/templates/templates/list.html` | 128 | Recurring transaction list table | #1, #7 |
| `app/templates/obligations/summary.html` | 218 | Recurring obligation summary tables | #7 |
| `app/templates/retirement/dashboard.html` | 166 | Retirement planning dashboard | #2, #4 |
| `app/templates/retirement/_retirement_account_table.html` | 23 | Retirement account projection table | #1, #4 |
| `app/templates/investment/dashboard.html` | 323 | Investment account dashboard | #2 |
| `app/templates/debt_strategy/dashboard.html` | 164 | Debt payoff strategy dashboard | #2 |
| `app/templates/loan/dashboard.html` | 349 | Loan dashboard with tabs | #8 |
| `app/templates/loan/_schedule.html` | 113 | Amortization schedule table | #7 |
| `app/templates/salary/_deductions_section.html` | 205 | Salary deductions table | #1 |
| `app/templates/salary/_raises_section.html` | 147 | Salary raises table | #1 |
| `app/templates/savings/dashboard.html` | 470 | Accounts/savings dashboard | #1 |

### Templates (new)

| File | Purpose | Created by |
|------|---------|------------|
| `app/templates/grid/_mobile_grid.html` | Single-period card view for `<768px` | #10 |

### Static files (modified)

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/static/css/app.css` | 829 | App-wide custom styles | #2, #3, #5, #6, #8, #9, #10 |
| `app/static/js/app.js` | 563 | Theme toggle, HTMX helpers, keyboard navigation | #4 |
| `app/static/js/grid_edit.js` | 298 | Grid edit/create popover positioning and lifecycle | #9 |

### Static files (new)

| File | Purpose | Created by |
|------|---------|------------|
| `app/static/js/mobile_grid.js` | Period navigation and swipe support for mobile grid | #10 |
| `app/static/manifest.json` | Web app manifest for home screen installation | #11 |
| `app/static/img/icon-192.png` | Home screen icon 192x192 | #11 |
| `app/static/img/icon-512.png` | Home screen icon 512x512 | #11 |

### Existing files (dependencies, not modified)

| File | Lines | Purpose | Depended on by |
|------|-------|---------|----------------|
| `app/routes/grid.py` | 397 | Grid route -- provides `txn_by_period`, `income_row_keys`, `expense_row_keys`, `subtotals`, `balances` | #10 (verified data structure, no changes) |
| `app/templates/grid/_transaction_quick_edit.html` | 28 | Quick edit form inside cell | #6 (CSS targets its elements) |
| `app/templates/grid/_transaction_quick_create.html` | 42 | Quick create form inside cell | #6 (CSS targets its elements) |
| `app/templates/grid/_transaction_full_edit.html` | 123 | Full edit popover content | #9 (bottom sheet container) |
| `app/templates/grid/_transaction_full_create.html` | 81 | Full create popover content | #9 (bottom sheet container) |
| `app/templates/grid/_balance_row.html` | 34 | Balance summary tfoot row | #10 (mobile balance display) |
| `app/templates/transfers/_transfer_full_edit.html` | 107 | Transfer full edit popover | #9 (bottom sheet container) |
| `app/static/img/favicon.png` | -- | Existing favicon | #11 (source for icon generation) |
| `app/static/img/shekel_logo.png` | -- | Existing logo | #11 (source for icon generation) |

---

## Task Dependency Analysis and Commit Ordering

### Dependency Graph

```text
#1 (table-responsive) ──────────────────────────────→ #7 (column hiding)

#2 (inline widths) ──────────── independent

#3 (touch states) ───────────── independent

#4 (tooltips) ───────────────── independent

#5 (320px query) ────────────────────────────────────────────→ #10 (mobile grid)

#6 (touch targets) ─────────→ #9 (bottom sheet) ────────────→ #10 (mobile grid)

#8 (loan tabs) ──────────────── independent

#11 (manifest) ──────────────── independent
```

### Commit Order Rationale

The ordering follows four principles:

1. **Quick wins first (1-5):** Mechanical fixes that improve every page with minimal risk. Each
   is independently testable. `table-responsive` wrappers must precede column hiding (which
   assumes the table already scrolls). CSS hover states and the 320px query are standalone
   additions.

2. **Touch targets before complex layouts (6):** Mobile touch sizing must be in place before
   building the grid card view and bottom sheet, since both rely on touch-sized interactive
   elements.

3. **Bottom sheet before mobile grid (9 before 10):** The mobile grid's edit flow depends on
   the bottom sheet. Tapping a transaction card on the mobile grid opens the full edit form,
   which must render as a bottom sheet at `<768px`. Building the bottom sheet first means the
   mobile grid can use it immediately.

4. **Manifest last (11):** Fully independent and adds no functional behavior. Placed last so all
   UI work is complete before the cosmetic "add to home screen" feature.

### Commit Checklist

| # | Commit Message | Summary |
|---|----------------|---------|
| 1 | `style(mobile): add table-responsive wrappers to 7 data tables` | Wrap 7 tables in `<div class="table-responsive">` for horizontal scrolling; add `flex-wrap` to dashboard header button groups |
| 2 | `style(mobile): remove hardcoded inline widths from dashboard templates` | Replace `style="width: 7rem"`, `style="width: 100px"`, and `style="width: 80px"` with responsive alternatives |
| 3 | `style(mobile): add touch feedback states alongside hover rules` | Add `:active` and `:focus-visible` selectors to 5 hover-only CSS rules |
| 4 | `style(mobile): replace hover-only title tooltips with tap-accessible popovers` | Convert ~8 retirement info icon `title` attributes to Bootstrap popovers; initialize popovers in `app.js` |
| 5 | `style(mobile): add 320px breakpoint for very small screens` | New `@media (max-width: 359.98px)` query for grid, typography, and control sizing |
| 6 | `style(mobile): increase touch target sizes for interactive elements` | Mobile media query increasing padding on `.btn-xs`, `.txn-cell`, `.txn-expand-btn`, `.period-btn-group .btn`, and popover form controls |
| 7 | `style(mobile): hide non-essential table columns on small screens` | Add `d-none d-md-table-cell` / `d-none d-lg-table-cell` to 6 templates; collapse salary action buttons into dropdown |
| 8 | `style(mobile): make loan dashboard tabs scrollable on mobile` | Convert `nav-tabs` to horizontally scrollable `nav-pills` layout at `<576px` |
| 9 | `feat(mobile): convert full-edit popover to bottom sheet on small screens` | At `<768px`, popover becomes full-width bottom sheet with backdrop; update positioning logic in `grid_edit.js` |
| 10 | `feat(mobile): add single-period card view for budget grid` | New `grid/_mobile_grid.html` template; period navigation with swipe; accordion sections; balance summary; tap-to-edit |
| 11 | `feat(mobile): add web app manifest for home screen installation` | Create `manifest.json` with icons; link from `base.html`; add `theme-color` meta tag |

---

## Commit #1: Add table-responsive wrappers to 7 data tables

### A. Commit message

```text
style(mobile): add table-responsive wrappers to 7 data tables
```

### B. Problem statement

Seven data tables across the app lack Bootstrap's `table-responsive` wrapper. On screens
narrower than the table's content width, these tables overflow their container and create a
horizontal scrollbar on the entire page instead of scrolling within the table area. Adding the
wrapper confines horizontal overflow to the table element.

### C. Files modified

- `app/templates/accounts/list.html` -- Wrap table in `table-responsive` div
- `app/templates/salary/list.html` -- Wrap table in `table-responsive` div
- `app/templates/transfers/list.html` -- Wrap table in `table-responsive` div
- `app/templates/templates/list.html` -- Wrap table in `table-responsive` div
- `app/templates/retirement/_retirement_account_table.html` -- Wrap table in `table-responsive` div
- `app/templates/salary/_deductions_section.html` -- Wrap table in `table-responsive` div
- `app/templates/salary/_raises_section.html` -- Wrap table in `table-responsive` div
- `app/templates/savings/dashboard.html` -- Add `flex-wrap` to header button group

### D. Implementation approach

For each of the 7 templates, wrap the `<table>` element in a `<div class="table-responsive">`:

**`accounts/list.html:27`** -- wrap the `<table class="table table-hover table-sm">`:

```html
<!-- Before -->
<table class="table table-hover table-sm">

<!-- After -->
<div class="table-responsive">
  <table class="table table-hover table-sm">
    ...
  </table>
</div>
```

Apply the same pattern to:

- **`salary/list.html:27`** -- `<table class="table table-hover table-sm">`
- **`transfers/list.html:29`** -- `<table class="table table-hover table-sm">`
- **`templates/list.html:29`** -- `<table class="table table-hover table-sm">`
- **`retirement/_retirement_account_table.html:3`** -- `<table class="table table-hover table-sm mb-0">`. This is a partial included by `retirement/dashboard.html`, so the wrapper goes around the `<table>` inside this partial.
- **`salary/_deductions_section.html:12`** -- `<table class="table table-hover table-sm mb-0">` inside the card body.
- **`salary/_raises_section.html:18`** -- `<table class="table table-hover table-sm mb-0">` inside the card body.

**`savings/dashboard.html:16`** -- the header button group `<div class="d-flex gap-2">` has
three buttons that can overflow on narrow screens. Add `flex-wrap`:

```html
<!-- Before -->
<div class="d-flex gap-2">

<!-- After -->
<div class="d-flex gap-2 flex-wrap">
```

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-1-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-1-2 | Accounts table scroll | Chrome DevTools, 375px | Load `/accounts` | Table scrolls horizontally within its container; no page-level horizontal scrollbar | Manual |
| M-1-3 | Salary table scroll | Chrome DevTools, 375px | Load salary list | Same behavior | Manual |
| M-1-4 | Transfers table scroll | Chrome DevTools, 375px | Load transfers list | Same behavior | Manual |
| M-1-5 | Templates table scroll | Chrome DevTools, 375px | Load templates list | Same behavior | Manual |
| M-1-6 | Retirement table scroll | Chrome DevTools, 375px | Load retirement dashboard (with accounts) | Same behavior | Manual |
| M-1-7 | Deductions table scroll | Chrome DevTools, 375px | Load salary profile edit (with deductions) | Same behavior | Manual |
| M-1-8 | Raises table scroll | Chrome DevTools, 375px | Load salary profile edit (with raises) | Same behavior | Manual |
| M-1-9 | Savings header buttons | Chrome DevTools, 320px | Load accounts dashboard | Three buttons wrap to second line instead of overflowing | Manual |

### F. Manual verification steps

1. **320px:** Load each of the 7 pages. Verify no horizontal page-level scrollbar appears.
   Tables should have their own internal horizontal scroll. On savings dashboard, header buttons
   should wrap to a second row.
2. **375px:** Same checks. Tables with fewer columns (accounts: 5, raises: 5) may fit without
   scrolling. Wider tables (salary: 7, transfers: 7, templates: 7) should scroll.
3. **768px:** Verify no visual change from current desktop behavior. Tables should display
   normally without scrollbars if content fits.

### G. Downstream effects

None. `table-responsive` is a pure CSS wrapper that adds `overflow-x: auto`. It does not
affect table content, HTMX targets, or DOM structure.

### H. Rollback notes

Template-only change. Remove the 7 `<div class="table-responsive">` wrappers and the
`flex-wrap` class. No migration, no data changes.

---

## Commit #2: Remove hardcoded inline widths from dashboard templates

### A. Commit message

```text
style(mobile): remove hardcoded inline widths from dashboard templates
```

### B. Problem statement

Three dashboard templates use `style="width: ..."` on input elements, which prevents them from
resizing on narrow screens. On a 320px viewport, a `width: 7rem` (112px) input-group consumes
35% of the screen width, and `width: 100px` is similarly rigid. These must be replaced with
responsive alternatives.

### C. Files modified

- `app/templates/retirement/dashboard.html` -- Remove `style="width: 7rem; flex-shrink: 0;"`
  from 2 input-groups (lines 37 and 56)
- `app/templates/investment/dashboard.html` -- Remove `style="width: 100px;"` from contribution
  amount input (line 146)
- `app/templates/debt_strategy/dashboard.html` -- Remove `style="width: 80px;"` from Priority
  column header (line 109)

### D. Implementation approach

**`retirement/dashboard.html:37` and `:56`** -- two slider value input-groups:

```html
<!-- Before (line 37) -->
<div class="input-group input-group-sm" style="width: 7rem; flex-shrink: 0;">

<!-- After -->
<div class="input-group input-group-sm" style="width: 7rem; min-width: 5rem; flex-shrink: 1;">
```

Change `flex-shrink: 0` to `flex-shrink: 1` so the input can shrink on narrow screens. Add
`min-width: 5rem` as a floor to prevent the input from becoming unusable. Same change at
line 56 for the return rate input-group.

**`investment/dashboard.html:146`** -- contribution amount input inside an input-group:

```html
<!-- Before -->
<input type="number" step="0.01" min="0.01" name="amount" id="contribution_amount"
       class="form-control" style="width: 100px;"
       ...>

<!-- After -->
<input type="number" step="0.01" min="0.01" name="amount" id="contribution_amount"
       class="form-control"
       ...>
```

Remove the inline `style` entirely. The input is inside an `input-group input-group-sm` within
a `col-auto` flex item. Without the fixed width, the input-group will size naturally based on
its container. The `col-auto` flex behavior keeps it compact on desktop.

**`debt_strategy/dashboard.html:109`** -- Priority column width:

```html
<!-- Before -->
<th style="width: 80px;">Priority</th>

<!-- After -->
<th class="text-nowrap" style="width: auto; min-width: 60px;">Priority</th>
```

Replace the fixed `80px` with `auto` sizing and a `60px` minimum. The `text-nowrap` prevents
the header text from wrapping. On desktop, the column sizes to content. On mobile, it can
shrink below `80px` to give more space to the Account and Balance columns.

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-2-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-2-2 | Retirement sliders at 320px | Chrome DevTools | Load retirement dashboard, resize to 320px | Slider value inputs shrink but remain usable; labels and inputs don't overlap | Manual |
| M-2-3 | Investment contribution at 375px | Chrome DevTools | Load investment dashboard with contribution prompt | Amount input fits within its row without overflow | Manual |
| M-2-4 | Debt strategy priority at 375px | Chrome DevTools | Load debt strategy with custom priority selected | Priority column is narrower; account names have more space | Manual |
| M-2-5 | Retirement sliders at 768px | Chrome DevTools | Load retirement dashboard | Slider inputs look the same as before (7rem fits comfortably) | Manual |

### F. Manual verification steps

1. **320px:** Retirement dashboard -- slider value inputs shrink to `5rem` but remain readable
   and interactive. Investment dashboard -- contribution input sizes to container width.
2. **375px:** All three dashboards should look good. No overflow, no clipping.
3. **768px:** Verify no visual change from current desktop behavior. Retirement slider inputs
   should still appear at approximately `7rem` (flex-shrink only activates when space is tight).

### G. Downstream effects

None. These are display-only width changes. No data, forms, or HTMX behavior is affected.

### H. Rollback notes

Restore the original inline `style` attributes. No migration, no data changes.

---

## Commit #3: Add touch feedback states alongside hover rules

### A. Commit message

```text
style(mobile): add touch feedback states alongside hover rules
```

### B. Problem statement

Five interactive elements in `app.css` have `:hover` states but no `:active` or `:focus-visible`
equivalents. On touch devices, `:hover` provides no feedback -- the user taps with no visual
confirmation that the tap registered. Adding `:active` (fires on press) and `:focus-visible`
(fires on keyboard/tap focus) provides immediate feedback on all input methods.

### C. Files modified

- `app/static/css/app.css` -- Add `:active` and `:focus-visible` selectors alongside 5 existing
  `:hover` rules

### D. Implementation approach

Add a grouped selector to each hover rule. The `:active` state fires during the press, and
`:focus-visible` fires when the element receives focus from a non-mouse source (keyboard, tap).

**1. Grid row hover (`app.css:216-218`):**

```css
/* Before */
.grid-table tbody tr:hover td {
  background-color: var(--shekel-row-hover);
}

/* After */
.grid-table tbody tr:hover td,
.grid-table tbody tr:active td {
  background-color: var(--shekel-row-hover);
}
```

No `:focus-visible` here because `<tr>` elements are not focusable.

**2. Anchor balance hover (`app.css:452-453`):**

```css
/* Before */
.anchor-balance-display:hover {
  background-color: rgba(var(--shekel-accent-rgb), 0.12);
}

/* After */
.anchor-balance-display:hover,
.anchor-balance-display:active,
.anchor-balance-display:focus-visible {
  background-color: rgba(var(--shekel-accent-rgb), 0.12);
}
```

**3. Transaction cell hover (`app.css:472-474`):**

```css
/* Before */
.txn-cell:hover {
  background-color: rgba(var(--shekel-accent-rgb), 0.10);
}

/* After */
.txn-cell:hover,
.txn-cell:active,
.txn-cell:focus-visible {
  background-color: rgba(var(--shekel-accent-rgb), 0.10);
}
```

**4. Empty cell hover (`app.css:485-487`):**

```css
/* Before */
.txn-empty-cell:hover {
  background-color: rgba(var(--shekel-accent-rgb), 0.15);
}

/* After */
.txn-empty-cell:hover,
.txn-empty-cell:active,
.txn-empty-cell:focus-visible {
  background-color: rgba(var(--shekel-accent-rgb), 0.15);
}
```

**5. Expand button hover (`app.css:612-613`):**

```css
/* Before */
.txn-expand-btn:hover {
  color: var(--shekel-accent);
}

/* After */
.txn-expand-btn:hover,
.txn-expand-btn:active,
.txn-expand-btn:focus-visible {
  color: var(--shekel-accent);
}
```

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-3-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-3-2 | Transaction cell tap feedback | Mobile device or Chrome touch emulation, 375px | Tap a transaction cell on the grid | Cell shows blue tint during press | Manual |
| M-3-3 | Anchor balance tap feedback | Same | Tap the anchor balance display | Background tint appears during press | Manual |
| M-3-4 | Desktop hover unchanged | Chrome, 1024px, no touch emulation | Hover over a transaction cell | Same hover effect as before | Manual |

### F. Manual verification steps

1. **375px with touch emulation:** Enable touch emulation in Chrome DevTools. Tap each
   interactive element (transaction cell, empty cell, anchor balance, expand button). Verify
   visual feedback appears during the tap.
2. **768px:** Hover with mouse. Verify hover effects are unchanged.

### G. Downstream effects

None. CSS-only addition. No DOM structure changes.

### H. Rollback notes

Remove the added `:active` and `:focus-visible` selectors. CSS-only revert.

---

## Commit #4: Replace hover-only title tooltips with tap-accessible popovers

### A. Commit message

```text
style(mobile): replace hover-only title tooltips with tap-accessible popovers
```

### B. Problem statement

The retirement dashboard and retirement account table use `title="..."` attributes on info
icons (`<i class="bi bi-info-circle">`) to display explanatory text. These tooltips are
hover-only and inaccessible on touch devices. The transaction cell also uses `title` attributes
on status badges, but those are supplementary (the badge's visual indicator conveys the same
information). The retirement info icons are the critical case because they explain financial
terms that users need to understand.

### C. Files modified

- `app/templates/retirement/dashboard.html` -- Convert ~5 `title` attributes on info icons to
  Bootstrap popovers
- `app/templates/retirement/_retirement_account_table.html` -- Convert 3 `title` attributes on
  column header info icons to Bootstrap popovers
- `app/templates/grid/_transaction_cell.html` -- Convert `title` on status badges to
  `data-bs-toggle="tooltip"` with `data-bs-trigger="click focus"` (lighter treatment)
- `app/static/js/app.js` -- Add popover and tooltip initialization

### D. Implementation approach

**Retirement info icons -- convert to Bootstrap popovers:**

In `retirement/_retirement_account_table.html`, convert each info icon. Example for the
"Current Balance" column header (line 9):

```html
<!-- Before -->
<th class="text-end">Current Balance
  <i class="bi bi-info-circle text-muted" title="Current balance from your account records."></i>
</th>

<!-- After -->
<th class="text-end">Current Balance
  <i class="bi bi-info-circle text-muted"
     role="button" tabindex="0"
     data-bs-toggle="popover"
     data-bs-trigger="focus"
     data-bs-placement="top"
     data-bs-content="Current balance from your account records."></i>
</th>
```

Apply the same conversion to:
- `_retirement_account_table.html:12` -- "Annual Return" info icon
- `_retirement_account_table.html:15` -- "Projected at Retirement" info icon
- `retirement/dashboard.html:48` -- "Assumed Annual Return" info icon (long explanation text)
- `retirement/dashboard.html:97` -- "Years of Service" info icon
- `retirement/dashboard.html:103` -- "High Salary Average" info icon
- `retirement/dashboard.html:109` -- "Annual Benefit" info icon
- `retirement/dashboard.html:115` -- "Monthly Benefit" info icon

For `data-bs-trigger="focus"`: on touch devices, tapping the icon gives it focus, which opens
the popover. Tapping elsewhere removes focus, which closes it. On desktop, clicking works the
same way. The `tabindex="0"` and `role="button"` make the icon keyboard-focusable and
semantically correct.

**Transaction cell badges -- lighter treatment:**

In `grid/_transaction_cell.html`, the status badge `title` attributes (lines 36, 38) provide
supplementary information. The visual indicator (checkmark, "CC") already communicates the
status. Convert these to Bootstrap tooltips with click/focus trigger rather than popovers:

```html
<!-- Before -->
<span class="badge-done ms-1" title="{{ t.status.name }}" aria-label="{{ t.status.name }}">✓</span>

<!-- After -->
<span class="badge-done ms-1" aria-label="{{ t.status.name }}"
      data-bs-toggle="tooltip" data-bs-trigger="click focus"
      data-bs-title="{{ t.status.name }}">✓</span>
```

Same for the credit badge at line 38.

**Popover and tooltip initialization in `app.js`:**

Add initialization at the end of the DOMContentLoaded toast block (`app.js:559-563`):

```javascript
document.addEventListener('DOMContentLoaded', function() {
  // Existing toast initialization...
  document.querySelectorAll('.toast').forEach(function(el) {
    new bootstrap.Toast(el).show();
  });

  // Initialize Bootstrap popovers (retirement info icons)
  document.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(el) {
    new bootstrap.Popover(el);
  });

  // Initialize Bootstrap tooltips (transaction status badges)
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(el) {
    new bootstrap.Tooltip(el);
  });
});
```

Also add re-initialization after HTMX swaps, since swapped content may contain new popover or
tooltip triggers. Add to the existing `htmx:afterSwap` handler (`app.js:78-93`):

```javascript
document.body.addEventListener("htmx:afterSwap", function(event) {
  // Existing save-flash and popover-close logic...

  // Re-initialize Bootstrap popovers/tooltips in swapped content.
  var target = event.detail.target || event.detail.elt;
  if (target) {
    target.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(el) {
      new bootstrap.Popover(el);
    });
    target.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(el) {
      new bootstrap.Tooltip(el);
    });
  }
});
```

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-4-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-4-2 | Retirement info popover tap | Chrome touch emulation, 375px | Tap an info icon on retirement dashboard | Popover appears with explanatory text | Manual |
| M-4-3 | Popover dismiss on tap-away | Same | Tap outside the popover | Popover closes | Manual |
| M-4-4 | Info icon keyboard access | Chrome, 768px | Tab to info icon, press Enter/Space | Popover appears | Manual |
| M-4-5 | Transaction badge tooltip | Chrome touch emulation, 375px | Tap a status badge (checkmark) on a grid cell | Tooltip shows status name | Manual |
| M-4-6 | HTMX swap re-init | Chrome, 768px | Edit a transaction, save | After swap, badge tooltip still works on the new content | Manual |

### F. Manual verification steps

1. **375px with touch emulation:** Load retirement dashboard. Tap each info icon. Verify
   popover appears with full explanation text and dismisses on tap-away. Check all 8 info icons.
2. **375px:** Load budget grid. Tap a settled transaction's checkmark badge. Verify tooltip shows
   the status name.
3. **768px with mouse:** Click info icons on retirement page. Verify popovers open and close
   correctly. Hover should NOT trigger the popover (only click/focus).

### G. Downstream effects

- Bootstrap's Popover JS is already loaded via the Bootstrap bundle (`base.html:230`). No new
  dependencies.
- HTMX swaps that replace transaction cells or retirement account rows will re-initialize
  tooltips/popovers via the `htmx:afterSwap` handler.

### H. Rollback notes

Restore `title` attributes on info icons and badges. Remove popover/tooltip initialization from
`app.js`. No migration.

---

## Commit #5: Add 320px breakpoint for very small screens

### A. Commit message

```text
style(mobile): add 320px breakpoint for very small screens
```

### B. Problem statement

The smallest existing breakpoint (`<576px`, `app.css:773`) reduces the grid sticky column to
90px and font to 0.65rem. On 320px screens (iPhone SE, older Android devices), even these
reduced sizes leave insufficient space for content. A dedicated breakpoint for very small
screens ensures text remains legible and controls remain usable at the smallest supported width.

### C. Files modified

- `app/static/css/app.css` -- Add `@media (max-width: 359.98px)` query

### D. Implementation approach

Add a new media query block after the existing `<576px` block (`app.css:795`). The `359.98px`
breakpoint targets screens 360px and below, covering the iPhone SE (375px is above this) and
older 320px devices.

```css
@media (max-width: 359.98px) {
  /* Further reduce the grid sticky column for very small screens */
  .sticky-col {
    min-width: 70px;
    max-width: 90px;
  }

  .row-label-col {
    min-width: 70px;
    max-width: 90px;
  }

  .grid-table {
    font-size: 0.6rem;
  }

  .row-label {
    font-size: 0.55rem;
  }

  /* Prevent anchored balance from dominating the header */
  .anchor-balance-display {
    font-size: 18px;
  }

  /* Allow period buttons to wrap tighter */
  .period-btn-group .btn {
    font-size: 0.65rem;
    padding: 0.15rem 0.35rem;
  }

  /* Reduce breadcrumb and general text sizes */
  .breadcrumb {
    font-size: 0.75rem;
  }
}
```

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-5-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-5-2 | Grid at 320px | Chrome DevTools, 320px | Load budget grid | Sticky column is 70px, text is small but legible, no horizontal page scroll | Manual |
| M-5-3 | Period buttons at 320px | Chrome DevTools, 320px | Load budget grid | Quick-select buttons (3P, 6P, etc.) fit within the viewport or wrap cleanly | Manual |
| M-5-4 | Grid at 375px | Chrome DevTools, 375px | Load budget grid | No change from current `<576px` behavior -- 375px is above the new breakpoint | Manual |
| M-5-5 | Desktop unchanged | Chrome, 1024px | Load budget grid | No visual change | Manual |

### F. Manual verification steps

1. **320px:** Load the budget grid. Verify: sticky column is narrower (70px), text is small but
   readable, period buttons wrap if needed, no page-level overflow. Note: after Commit #10, the
   mobile card view will be shown at this width instead, but the desktop grid CSS should still
   be correct for the `d-none d-md-block` fallback.
2. **375px:** Verify no change from existing behavior (375px is above the 360px breakpoint).
3. **768px:** Verify no change.

### G. Downstream effects

None. CSS-only addition behind a new media query. Does not affect existing breakpoints.

### H. Rollback notes

Remove the `@media (max-width: 359.98px)` block from `app.css`. CSS-only revert.

---

## Commit #6: Increase touch target sizes for interactive elements

### A. Commit message

```text
style(mobile): increase touch target sizes for interactive elements
```

### B. Problem statement

WCAG 2.5.8 recommends a minimum 44x44px touch target. Multiple elements fall below this:
`.btn-xs` (~20-24px), `.txn-cell` (~24px height), `.txn-expand-btn` (~20px),
`.period-btn-group .btn` (~24-32px), and form controls inside the full-edit popover. These must
be enlarged on mobile to prevent mis-taps without changing the desktop layout.

### C. Files modified

- `app/static/css/app.css` -- Add touch target overrides inside the existing
  `@media (max-width: 767.98px)` block

### D. Implementation approach

Add the following rules inside the existing `<768px` media query block (`app.css:739`). All
changes are mobile-only -- the desktop layout is unaffected.

```css
@media (max-width: 767.98px) {
  /* ... existing rules ... */

  /* Touch target: extra-small buttons (status actions in full-edit popover) */
  .btn-xs {
    padding: 0.4rem 0.6rem;
    font-size: 0.8rem;
  }

  /* Touch target: transaction cell tap area */
  .txn-cell {
    padding: 0.35rem 0.4rem;
  }

  /* Touch target: expand button (three dots) */
  .txn-expand-btn {
    width: 32px;
    min-height: 32px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  /* Touch target: period navigation quick-select buttons */
  .period-btn-group .btn {
    padding: 0.35rem 0.65rem;
    font-size: 0.8rem;
  }

  /* Touch target: form controls inside full-edit popover */
  .txn-full-edit-popover .form-control-sm,
  .txn-full-edit-popover .form-select-sm {
    min-height: 44px;
    font-size: 16px; /* Prevents iOS Safari zoom on input focus */
  }
}
```

The `font-size: 16px` on popover form controls is critical for iOS Safari: inputs with
`font-size < 16px` trigger automatic zoom on focus, which disrupts the layout.

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-6-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-6-2 | btn-xs size at 375px | Chrome DevTools, 375px | Open full-edit popover on a transaction | Status action buttons (Paid, Credit, Cancel) are visibly larger, ~36-40px tall | Manual |
| M-6-3 | txn-cell tap area at 375px | Chrome DevTools, 375px | View budget grid | Transaction cells have more padding, easier to tap | Manual |
| M-6-4 | Period buttons at 375px | Chrome DevTools, 375px | View budget grid header | Quick-select buttons (3P, 6P, etc.) are taller and easier to tap | Manual |
| M-6-5 | Popover inputs at 375px | Chrome DevTools, 375px | Open full-edit popover | Form inputs are 44px tall with 16px font | Manual |
| M-6-6 | Desktop unchanged | Chrome, 1024px | View budget grid and full-edit popover | No visual change in button or input sizes | Manual |

### F. Manual verification steps

1. **375px:** Load the budget grid. Verify period buttons are larger. Tap a transaction cell --
   verify it's easier to tap accurately. Open the full-edit popover -- verify inputs are 44px
   tall and text is 16px.
2. **320px:** Same checks. Verify nothing overflows.
3. **768px:** Verify no visual change from current desktop behavior.

### G. Downstream effects

- `.btn-xs` is used in the full-edit popover for status actions (Paid, Credit, Cancel) and in
  the transfer full-edit popover. Both benefit from the size increase on mobile.
- The `font-size: 16px` on popover inputs prevents iOS Safari auto-zoom. This is a
  well-known iOS behavior where inputs with `font-size < 16px` cause the viewport to zoom in.

### H. Rollback notes

Remove the added rules from the `<768px` media query. CSS-only revert.

---

## Commit #7: Hide non-essential table columns on small screens

### A. Commit message

```text
style(mobile): hide non-essential table columns on small screens
```

### B. Problem statement

Data-heavy tables with 6-7 columns overflow on mobile even with `table-responsive` wrappers
(added in Commit #1). Users must scroll horizontally to see all columns, most of which are
non-essential for quick reference. Hiding secondary columns via responsive visibility classes
shows only the most important data on mobile while preserving the full table on desktop.

Additionally, `salary/list.html` has 4 icon-only action buttons per row that create a wide
action column. On mobile, these should collapse into a dropdown menu.

### C. Files modified

- `app/templates/salary/list.html` -- Hide Filing Status and State columns; collapse action
  buttons into dropdown
- `app/templates/transfers/list.html` -- Hide Recurrence column
- `app/templates/templates/list.html` -- Hide Category and Type columns
- `app/templates/obligations/summary.html` -- Hide Account and Category in expense table;
  hide Account in income table
- `app/templates/loan/_schedule.html` -- Hide Escrow, Extra, and Rate columns

### D. Implementation approach

Add `d-none d-md-table-cell` (hidden below 768px) or `d-none d-lg-table-cell` (hidden below
992px) to both `<th>` and `<td>` elements for the target columns. The choice of breakpoint
depends on how many columns remain visible: tables with 4+ remaining visible columns use
`d-lg-table-cell`, tables with 3 or fewer use `d-md-table-cell`.

**`salary/list.html`** -- hide Filing Status (col 2) and State (col 3) below 992px:

```html
<!-- Header -->
<th scope="col" class="d-none d-lg-table-cell">Filing Status</th>
<th scope="col" class="d-none d-lg-table-cell">State</th>

<!-- Body rows -->
<td class="d-none d-lg-table-cell">{{ p.filing_status.name|replace('_', ' ')|title ... }}</td>
<td class="d-none d-lg-table-cell">{{ p.state_code }}</td>
```

Collapse the 4 action buttons (Edit, Breakdown, Projection, Deactivate) into a Bootstrap
dropdown on mobile. Show the original buttons on `md+`:

```html
<td>
  {# Desktop: individual buttons #}
  <span class="d-none d-md-inline">
    <a href="..." class="btn btn-sm btn-outline-secondary me-1" title="Edit">
      <i class="bi bi-pencil"></i>
    </a>
    <a href="..." class="btn btn-sm btn-outline-info me-1" title="Breakdown">
      <i class="bi bi-receipt"></i>
    </a>
    <a href="..." class="btn btn-sm btn-outline-primary me-1" title="Projection">
      <i class="bi bi-graph-up"></i>
    </a>
    {% if p.is_active %}
    <form method="POST" ... class="d-inline" ...>
      ...
    </form>
    {% endif %}
  </span>

  {# Mobile: dropdown #}
  <div class="dropdown d-md-none d-inline-block">
    <button class="btn btn-sm btn-outline-secondary dropdown-toggle"
            type="button" data-bs-toggle="dropdown" aria-expanded="false">
      <i class="bi bi-three-dots"></i>
    </button>
    <ul class="dropdown-menu dropdown-menu-end">
      <li><a class="dropdown-item" href="..."><i class="bi bi-pencil me-2"></i>Edit</a></li>
      <li><a class="dropdown-item" href="..."><i class="bi bi-receipt me-2"></i>Breakdown</a></li>
      <li><a class="dropdown-item" href="..."><i class="bi bi-graph-up me-2"></i>Projection</a></li>
      {% if p.is_active %}
      <li><hr class="dropdown-divider"></li>
      <li>
        <form method="POST" ... class="px-3" ...>
          ...
          <button type="submit" class="dropdown-item text-danger">
            <i class="bi bi-x-circle me-2"></i>Deactivate
          </button>
        </form>
      </li>
      {% endif %}
    </ul>
  </div>
</td>
```

**`transfers/list.html`** -- hide Recurrence column (col 5) below 768px:

```html
<th scope="col" class="d-none d-md-table-cell">Recurrence</th>
<!-- In body -->
<td class="d-none d-md-table-cell">{% if t.recurrence_rule %}...{% endif %}</td>
```

This leaves 6 visible columns (Name, From, To, Amount, Active, Actions), which is still wide
but the most important data is visible.

**`templates/list.html`** -- hide Category (col 2) and Type (col 3) below 768px:

```html
<th scope="col" class="d-none d-md-table-cell">Category</th>
<th scope="col" class="d-none d-md-table-cell">Type</th>
<!-- In body -->
<td class="d-none d-md-table-cell">{{ t.category.display_name ... }}</td>
<td class="d-none d-md-table-cell">{% if t.transaction_type_id == ... %}</td>
```

This leaves 5 visible columns (Name, Amount, Recurrence, Active, Actions).

**`obligations/summary.html`** -- hide secondary columns below 768px:

Expense table: hide Account (col 2) and Category (col 3):
```html
<th class="d-none d-md-table-cell">Account</th>
<th class="d-none d-md-table-cell">Category</th>
<!-- Body -->
<td class="d-none d-md-table-cell">{{ item.account_name }}</td>
<td class="d-none d-md-table-cell">{{ item.category_name }}</td>
```
Update the tfoot `colspan` from 5 to include a conditional: on mobile the visible column count
is lower. Use a responsive approach: wrap the colspan in the full column count but let
`d-none` handle the visual:
```html
<td colspan="5" class="text-end fw-bold">Total Monthly</td>
```
This does not need to change -- `colspan="5"` with hidden columns still works because the
hidden cells are `display: none`, and the colspan spans the visible cells.

Actually, `colspan` counts DOM cells regardless of `display`. With 2 hidden cells, `colspan="5"`
would span 5 out of 5 visible cells on mobile (7 total - 2 hidden = 5 visible). This is
correct.

Income table: hide Account (col 2):
```html
<th class="d-none d-md-table-cell">Account</th>
<!-- Body -->
<td class="d-none d-md-table-cell">{{ item.account_name }}</td>
```
Update tfoot `colspan` from 4 to 3? Actually, with 1 hidden cell, the visible count is 5
(6 - 1). The current `colspan="4"` in tfoot spans to the Monthly column. With Account hidden,
the visible columns before Monthly are: Name, Amount, Frequency = 3 columns. The `colspan`
should adapt. Use two separate `td` elements with responsive visibility:

```html
<td colspan="4" class="d-none d-md-table-cell text-end fw-bold">Total Monthly</td>
<td colspan="3" class="d-md-none text-end fw-bold">Total Monthly</td>
```

Same approach for the expense table tfoot:
```html
<td colspan="5" class="d-none d-md-table-cell text-end fw-bold">Total Monthly</td>
<td colspan="3" class="d-md-none text-end fw-bold">Total Monthly</td>
```

**`loan/_schedule.html`** -- hide Escrow, Extra, and Rate columns below 992px. These are
conditional columns (only shown when escrow > 0, extra payments exist, or ARM rate changes
exist). Add the responsive class to both the header and body cells:

```html
<!-- Escrow column (lines 29, 58-59) -->
{% if monthly_escrow|float > 0 %}
<th class="text-end d-none d-lg-table-cell">Escrow</th>
{% endif %}

<!-- Extra column (lines 33, 62-67) -->
{% if schedule_totals.has_extra %}
<th class="text-end d-none d-lg-table-cell">Extra</th>
{% endif %}

<!-- Rate column (lines 37, 72-79) -->
{% if show_rate_column %}
<th class="text-end d-none d-lg-table-cell">Rate</th>
{% endif %}
```

And corresponding `<td>` cells in the body and tfoot. This leaves the core columns visible:
#, Date, Payment, Principal, Interest, Balance, Status.

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-7-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-7-2 | Salary list at 375px | Chrome DevTools, 375px | Load salary profiles | Filing Status and State hidden; action buttons in dropdown | Manual |
| M-7-3 | Salary dropdown actions | Chrome DevTools, 375px | Click dropdown, click Edit | Navigates to edit page | Manual |
| M-7-4 | Transfers list at 375px | Chrome DevTools, 375px | Load transfers list | Recurrence column hidden; 6 columns visible | Manual |
| M-7-5 | Templates list at 375px | Chrome DevTools, 375px | Load templates list | Category and Type hidden; 5 columns visible | Manual |
| M-7-6 | Obligations at 375px | Chrome DevTools, 375px | Load obligations summary | Account/Category hidden in expense table; Account hidden in income table; totals correct | Manual |
| M-7-7 | Amortization schedule at 428px | Chrome DevTools, 428px | Load loan dashboard, Amortization tab (with escrow) | Escrow, Extra, Rate columns hidden; core columns visible | Manual |
| M-7-8 | All tables at 992px | Chrome DevTools, 992px | Load each page | All columns visible, including hidden ones | Manual |

### F. Manual verification steps

1. **375px:** Load each of the 5 template pages. Verify hidden columns are not visible and the
   remaining columns display correctly. For salary, verify the dropdown menu works (open, click
   each action).
2. **428px:** Same checks. The slightly wider viewport should not change which columns are
   hidden (breakpoints are at 768px and 992px).
3. **992px:** Verify all columns are visible. The `d-lg-table-cell` columns should appear at
   this width.
4. **1024px+:** Verify no change from current desktop layout.

### G. Downstream effects

- The salary action dropdown duplicates the action button markup in two responsive variants
  (`d-none d-md-inline` for desktop, `d-md-none` for mobile). Future changes to salary actions
  must update both.
- The obligation table tfoot has dual `colspan` elements for responsive column counts.
- The amortization schedule's hidden columns reduce information density on mobile. Users who
  need escrow or rate details must view the page at a wider viewport.

### H. Rollback notes

Remove `d-none d-md-table-cell` and `d-none d-lg-table-cell` classes from all affected `<th>`
and `<td>` elements. Remove the mobile dropdown and dual-colspan markup. Template-only revert.

---

## Commit #8: Make loan dashboard tabs scrollable on mobile

### A. Commit message

```text
style(mobile): make loan dashboard tabs scrollable on mobile
```

### B. Problem statement

The loan dashboard (`loan/dashboard.html`) uses Bootstrap `nav-tabs` with 5-6 tab items
(Overview, Escrow, Rate History (conditional), Amortization Schedule, Payoff Calculator,
Refinance Calculator). On screens below ~576px, these tabs wrap onto multiple lines, creating a
cluttered and confusing navigation area.

### Options considered

**Option A: Scrollable nav-pills with `flex-nowrap overflow-auto`**

Convert tabs to a horizontally scrollable strip at `<576px`. Users swipe left/right to see all
tabs.

- Pros: Simple CSS-only solution, no JS needed, preserves Bootstrap tab behavior, all tabs
  remain directly accessible
- Cons: Scroll affordance is not visually obvious (no scrollbar on mobile), users may not
  discover tabs beyond the visible area

**Option B: Collapse to `<select>` dropdown at `<576px`**

Replace tab navigation with a `<select>` element. Selecting an option shows the corresponding
tab pane.

- Pros: All options visible in one tap (native dropdown), familiar mobile pattern
- Cons: Requires JS to sync `<select>` changes with Bootstrap tab activation, dual markup
  (tabs + select), more complex maintenance

**Recommendation: Option A (scrollable pills).** The 5-6 tab labels are short enough that
2-3 are visible at once, making it discoverable that more exist. The CSS-only approach is
simpler to maintain and does not require JS synchronization logic.

### C. Files modified

- `app/templates/loan/dashboard.html` -- Add responsive class to tab `<ul>`
- `app/static/css/app.css` -- Add scrollable tab styles

### D. Implementation approach

**`loan/dashboard.html:21`** -- add a `mobile-scroll-tabs` class to the tab `<ul>`:

```html
<!-- Before -->
<ul class="nav nav-tabs mb-3" role="tablist">

<!-- After -->
<ul class="nav nav-tabs mobile-scroll-tabs mb-3" role="tablist">
```

**`app.css`** -- add styles for scrollable tabs at `<576px`:

```css
/* =============================================
   MOBILE SCROLLABLE TABS
   ============================================= */
@media (max-width: 575.98px) {
  .mobile-scroll-tabs {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none; /* Firefox */
    -ms-overflow-style: none; /* IE/Edge */
  }

  .mobile-scroll-tabs::-webkit-scrollbar {
    display: none; /* Chrome/Safari */
  }

  .mobile-scroll-tabs .nav-link {
    white-space: nowrap;
    padding: 0.5rem 0.75rem;
  }
}
```

The scrollbar is hidden because mobile scrollbars are visually distracting and the user
interface convention on mobile is swipe-to-scroll without visible indicators. The
`-webkit-overflow-scrolling: touch` enables momentum scrolling on iOS.

Also apply the same class to the payoff calculator sub-tabs within the loan dashboard
(`loan/dashboard.html:267`):

```html
<ul class="nav nav-tabs mobile-scroll-tabs mb-3" role="tablist">
```

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-8-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-8-2 | Loan tabs at 375px | Chrome DevTools, 375px | Load loan dashboard (non-ARM) | 5 tabs display in single scrollable row; 2-3 visible at once | Manual |
| M-8-3 | Loan tabs scroll | Chrome DevTools, 375px, touch emulation | Swipe tabs left/right | Hidden tabs scroll into view smoothly | Manual |
| M-8-4 | Tab activation | Chrome DevTools, 375px | Tap "Amortization Schedule" tab | Correct tab pane activates; tab scrolls into view | Manual |
| M-8-5 | ARM loan tabs at 375px | Chrome DevTools, 375px | Load loan dashboard (ARM) | 6 tabs in scrollable row | Manual |
| M-8-6 | Desktop unchanged | Chrome, 1024px | Load loan dashboard | Tabs display as normal tabs with wrapping (if needed) | Manual |

### F. Manual verification steps

1. **375px:** Load a loan dashboard. Verify tabs are in a single horizontal row with no
   wrapping. Swipe to see all tabs. Tap each tab to verify activation.
2. **320px:** Same checks. Fewer tabs visible at once but scrolling works.
3. **768px+:** Verify tabs display normally as before -- no scrolling behavior.

### G. Downstream effects

The `mobile-scroll-tabs` class can be reused on any `nav nav-tabs` element that wraps on mobile.

### H. Rollback notes

Remove the `mobile-scroll-tabs` class from the template and the CSS rules. Template + CSS
revert.

---

## Commit #9: Convert full-edit popover to bottom sheet on small screens

### A. Commit message

```text
feat(mobile): convert full-edit popover to bottom sheet on small screens
```

### B. Problem statement

The full-edit popover (`.txn-full-edit-popover`) is fixed at 280px wide and positioned relative
to the triggering cell via JS (`grid_edit.js:23-56`). On mobile, this results in a narrow
floating panel that is hard to interact with -- form controls are small and the popover can be
clipped by viewport edges. Converting it to a bottom sheet on `<768px` provides a
full-width editing surface with touch-sized controls.

### C. Files modified

- `app/static/css/app.css` -- Add bottom sheet styles inside `<768px` media query
- `app/static/js/grid_edit.js` -- Add mobile detection to `positionPopover()`; add backdrop
  creation/removal

### D. Implementation approach

**CSS -- bottom sheet styles (`app.css`, inside `<768px` media query):**

```css
@media (max-width: 767.98px) {
  /* ... existing rules ... */

  /* Bottom sheet: full-edit popover becomes a bottom-anchored panel on mobile */
  .txn-full-edit-popover {
    width: 100% !important;
    left: 0 !important;
    bottom: 0 !important;
    top: auto !important;
    right: 0 !important;
    max-height: 70vh;
    overflow-y: auto;
    border-radius: 12px 12px 0 0;
    padding: 16px 16px calc(16px + env(safe-area-inset-bottom));
    box-shadow: 0 -4px 24px rgba(0, 0, 0, 0.3);
    transition: transform 0.2s ease-out;
  }

  /* Backdrop behind the bottom sheet */
  .bottom-sheet-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    z-index: 99;
  }
}
```

The `env(safe-area-inset-bottom)` adds padding for devices with home indicators (iPhone X+).
The `transition` enables a smooth slide-up animation when the sheet appears.

**JS -- mobile detection in `positionPopover()` (`grid_edit.js:23-56`):**

Replace the current positioning logic with a mobile/desktop branch:

```javascript
function positionPopover(cell) {
    var popover = document.getElementById('txn-popover');
    if (!popover) return null;

    closeFullEdit();

    // Mobile: bottom sheet -- CSS handles positioning.
    // Just add a backdrop overlay.
    if (window.innerWidth < 768) {
        var backdrop = document.createElement('div');
        backdrop.className = 'bottom-sheet-backdrop';
        backdrop.id = 'bottom-sheet-backdrop';
        backdrop.addEventListener('click', closeFullEdit);
        document.body.appendChild(backdrop);

        // Clear any desktop positioning styles
        popover.style.top = '';
        popover.style.left = '';
        return popover;
    }

    // Desktop: existing positioning logic (unchanged)
    var cellRect = cell.getBoundingClientRect();
    var popoverHeight = 300;
    var topPos = cellRect.bottom;
    var leftPos = cellRect.left;

    if (cellRect.bottom + popoverHeight > window.innerHeight) {
        topPos = cellRect.top - popoverHeight;
        if (topPos < 0) topPos = 0;
    }

    if (leftPos + 280 > window.innerWidth) {
        leftPos = window.innerWidth - 290;
    }

    popover.style.top = topPos + 'px';
    popover.style.left = leftPos + 'px';

    return popover;
}
```

**JS -- backdrop cleanup in `closeFullEdit()` (`grid_edit.js:168-183`):**

Add backdrop removal to the existing close function:

```javascript
function closeFullEdit() {
    // Remove backdrop if present
    var backdrop = document.getElementById('bottom-sheet-backdrop');
    if (backdrop) backdrop.remove();

    var popover = document.getElementById('txn-popover');
    if (popover) {
        popover.classList.add('d-none');
        popover.innerHTML = '';
    }
    activePopover = null;
    document.removeEventListener('click', handleClickOutside);

    var wrapper = document.querySelector('.grid-scroll-wrapper');
    if (wrapper) {
        wrapper.removeEventListener('scroll', closeFullEdit);
    }
}
```

**JS -- body scroll lock while bottom sheet is open:**

When the bottom sheet is visible on mobile, the page behind it should not scroll. Add scroll
lock in `showPopover()` and `openFullCreate()`, and unlock in `closeFullEdit()`:

In `showPopover()` (`grid_edit.js:61`), after showing the popover:
```javascript
// Lock body scroll on mobile when bottom sheet is open
if (window.innerWidth < 768) {
    document.body.style.overflow = 'hidden';
}
```

In `closeFullEdit()`:
```javascript
// Unlock body scroll
document.body.style.overflow = '';
```

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-9-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-9-2 | Bottom sheet opens at 375px | Chrome touch emulation, 375px | Tap a transaction cell, then tap expand (three dots) | Bottom sheet slides up from bottom, full-width, with backdrop | Manual |
| M-9-3 | Bottom sheet dismiss via backdrop | Same | Tap the dark backdrop area | Bottom sheet closes, backdrop removed | Manual |
| M-9-4 | Bottom sheet dismiss via Escape | Same | Press Escape key | Bottom sheet closes | Manual |
| M-9-5 | Bottom sheet form submit | Same | Change amount in bottom sheet, tap Save | Transaction updates, bottom sheet closes, cell shows new value | Manual |
| M-9-6 | Bottom sheet height | Same | Open bottom sheet with many status buttons | Sheet is at most 70vh tall, scrollable if content overflows | Manual |
| M-9-7 | Body scroll locked | Same | Open bottom sheet, try to scroll page behind it | Page does not scroll | Manual |
| M-9-8 | Desktop popover unchanged | Chrome, 1024px | Click expand on a transaction | Existing popover appears positioned near the cell, not as bottom sheet | Manual |
| M-9-9 | Create mode bottom sheet | Chrome touch emulation, 375px | Tap empty cell, tap expand | Full create form appears in bottom sheet | Manual |

### F. Manual verification steps

1. **375px with touch emulation:** Open a full edit on a transaction. Verify: bottom sheet
   covers ~60-70% of screen, full-width, dark backdrop behind it, form controls are 44px tall
   (from Commit #6), Save/Cancel buttons are touch-sized, tapping backdrop dismisses.
2. **320px:** Same checks. Verify the bottom sheet doesn't clip or overflow.
3. **428px:** Same checks.
4. **768px:** Verify popover appears in the desktop position (floating near the cell), no
   backdrop, no bottom sheet behavior.

### G. Downstream effects

- The transfer full-edit popover (`transfers/_transfer_full_edit.html`) is rendered inside the
  same `#txn-popover` container and benefits from the bottom sheet treatment automatically.
- The `openFullCreate()` function in `grid_edit.js` also calls `positionPopover()`, so the
  bottom sheet applies to create mode as well.
- Body scroll lock prevents accidental scrolling behind the sheet, which would detach a
  `position: fixed` popover from its context on desktop (the existing scroll-to-close behavior
  is bypassed on mobile since the bottom sheet doesn't need cell-relative positioning).

### H. Rollback notes

Remove the bottom sheet CSS rules from the `<768px` media query. Revert `positionPopover()`
and `closeFullEdit()` to their original implementations. Remove the body scroll lock lines from
`showPopover()` and `openFullCreate()`. JS + CSS revert, no migration.

---

## Commit #10: Add single-period card view for budget grid

### A. Commit message

```text
feat(mobile): add single-period card view for budget grid
```

### B. Problem statement

The budget grid is a `<table>` with 1 sticky label column + N period columns (typically 6-52).
At mobile widths, this is fundamentally unusable: the sticky column shrinks to 90px, font drops
to 0.65rem (borderline illegible), and the user must scroll horizontally through dozens of
period columns. A mobile-specific layout is needed that presents one pay period at a time in a
card-based view with left/right navigation.

### C. Files modified

- `app/templates/grid/grid.html` -- Wrap desktop grid in `d-none d-md-block`; include mobile
  partial with `d-md-none`
- `app/templates/grid/_mobile_grid.html` -- New template: single-period card view
- `app/static/css/app.css` -- Mobile card view styles
- `app/static/js/mobile_grid.js` -- New file: period navigation and swipe support
- `app/templates/base.html` -- Include `mobile_grid.js` script

### D. Implementation approach

**Grid template toggle (`grid/grid.html`):**

Wrap the existing desktop grid (the `<div class="table-responsive ...">` and the popover
container) in a `d-none d-md-block` wrapper so it's hidden on mobile. Add the mobile partial
with `d-md-none`:

```html
{# --- Desktop Grid (hidden on mobile) --- #}
<div class="d-none d-md-block">
  <div class="table-responsive grid-wrapper grid-scroll-wrapper" style="position: relative;">
    <table class="table table-bordered table-sm grid-table grid-{{ col_size }}">
      ...existing table...
    </table>
  </div>
  <div id="txn-popover" class="txn-full-edit-popover d-none" ...></div>
</div>

{# --- Mobile Grid (shown only on mobile) --- #}
{% include "grid/_mobile_grid.html" %}
```

Note: the `#txn-popover` container must remain outside the `d-none d-md-block` wrapper because
the bottom sheet (from Commit #9) uses `position: fixed` and needs to be visible on mobile.
Move it after both the desktop and mobile sections:

```html
<div class="d-none d-md-block">
  <div class="table-responsive ...">...</div>
</div>
{% include "grid/_mobile_grid.html" %}

{# Popover/bottom-sheet container -- outside both grid variants #}
<div id="txn-popover" class="txn-full-edit-popover d-none" role="dialog"
     aria-label="Edit transaction" aria-live="assertive"></div>
```

**Mobile grid template (`grid/_mobile_grid.html`):**

```html
{# Mobile card view -- shown only at <768px.
   Single-period view with left/right navigation.
   Transaction matching logic duplicated from grid/grid.html --
   if the matching algorithm changes, update both templates.

   Expected context: periods, txn_by_period, income_row_keys, expense_row_keys,
   subtotals, balances, current_period, statuses, STATUS_CANCELLED, STATUS_CREDIT,
   STATUS_PROJECTED, STATUS_DONE, TXN_TYPE_INCOME, TXN_TYPE_EXPENSE.
#}
<div class="d-md-none" id="mobile-grid">
  {# --- Period Navigation --- #}
  <div class="d-flex justify-content-between align-items-center mb-3">
    <button class="btn btn-outline-secondary" id="mobile-period-prev"
            aria-label="Previous period">
      <i class="bi bi-chevron-left"></i>
    </button>
    <div class="text-center" id="mobile-period-label">
      <strong>{{ periods[0].start_date.strftime('%-m/%-d') if periods else '--' }}</strong>
      <div class="text-muted small" id="mobile-period-range"></div>
    </div>
    <button class="btn btn-outline-secondary" id="mobile-period-next"
            aria-label="Next period">
      <i class="bi bi-chevron-right"></i>
    </button>
  </div>

  {# --- Period Panels (one per period, JS toggles visibility) --- #}
  {% for period in periods %}
  <div class="mobile-period-panel" data-period-index="{{ loop.index0 }}"
       data-period-label="{{ period.start_date.strftime('%-m/%-d') }}"
       data-period-range="{{ period.start_date.strftime('%-m/%-d') }} - {{ period.end_date.strftime('%-m/%-d') }}"
       {% if loop.index0 != 0 %}style="display: none;"{% endif %}>

    {# --- Income Section --- #}
    <div class="card mb-2">
      <div class="card-header py-2 d-flex justify-content-between"
           data-bs-toggle="collapse"
           data-bs-target="#mobile-income-{{ loop.index0 }}"
           role="button" aria-expanded="true">
        <span class="fw-bold small text-uppercase"
              style="color: var(--shekel-section-income-text);">
          <i class="bi bi-arrow-up-circle me-1"></i>Income
        </span>
        <span class="font-mono fw-bold small">
          ${{ "{:,.0f}".format(subtotals[period.id].income) }}
        </span>
      </div>
      <div class="collapse show" id="mobile-income-{{ loop.index0 }}">
        <div class="list-group list-group-flush">
          {% set ns_inc = namespace(current_group='') %}
          {% for rk in income_row_keys %}
            {% if rk.group_name != ns_inc.current_group %}
              {% set ns_inc.current_group = rk.group_name %}
              <div class="list-group-item py-1 bg-body-tertiary">
                <small class="fw-semibold text-muted text-uppercase"
                       style="font-size: 0.7rem; letter-spacing: 0.5px;">
                  {{ rk.group_name }}
                </small>
              </div>
            {% endif %}
            {% set period_txns = txn_by_period.get(period.id, []) %}
            {% set matched = [] %}
            {% for txn in period_txns %}
              {% if txn.category_id == rk.category_id and txn.is_income and not txn.is_deleted and txn.status_id != STATUS_CANCELLED %}
                {% if rk.template_id is not none and txn.template_id is not none %}
                  {% if txn.template_id == rk.template_id %}
                    {% if matched.append(txn) %}{% endif %}
                  {% endif %}
                {% else %}
                  {% if txn.name == rk.txn_name %}
                    {% if matched.append(txn) %}{% endif %}
                  {% endif %}
                {% endif %}
              {% endif %}
            {% endfor %}
            {% for txn in matched %}
            <div class="list-group-item d-flex justify-content-between align-items-center py-2 mobile-txn-card"
                 role="button" tabindex="0"
                 data-mobile-txn-id="{{ txn.id }}"
                 aria-label="{{ rk.display_name }}: ${{ '{:,.2f}'.format(txn.effective_amount) }}">
              <div class="d-flex align-items-center gap-2">
                <span class="small">{{ rk.display_name }}</span>
                {% if txn.status.is_settled %}
                  <span class="badge-done">&#10003;</span>
                {% elif txn.status_id == STATUS_CREDIT %}
                  <span class="badge-credit">CC</span>
                {% endif %}
                {% if txn.transfer_id %}
                  <i class="bi bi-arrow-left-right text-muted"
                     style="font-size: 0.65rem;"></i>
                {% endif %}
              </div>
              <span class="font-mono fw-semibold">${{ "{:,.0f}".format(txn.effective_amount) }}</span>
            </div>
            {% endfor %}
          {% endfor %}
        </div>
      </div>
    </div>

    {# --- Expense Section --- #}
    <div class="card mb-2">
      <div class="card-header py-2 d-flex justify-content-between"
           data-bs-toggle="collapse"
           data-bs-target="#mobile-expense-{{ loop.index0 }}"
           role="button" aria-expanded="true">
        <span class="fw-bold small text-uppercase"
              style="color: var(--shekel-section-expense-text);">
          <i class="bi bi-arrow-down-circle me-1"></i>Expenses
        </span>
        <span class="font-mono fw-bold small">
          ${{ "{:,.0f}".format(subtotals[period.id].expense) }}
        </span>
      </div>
      <div class="collapse show" id="mobile-expense-{{ loop.index0 }}">
        <div class="list-group list-group-flush">
          {% set ns_exp = namespace(current_group='') %}
          {% for rk in expense_row_keys %}
            {% if rk.group_name != ns_exp.current_group %}
              {% set ns_exp.current_group = rk.group_name %}
              <div class="list-group-item py-1 bg-body-tertiary">
                <small class="fw-semibold text-muted text-uppercase"
                       style="font-size: 0.7rem; letter-spacing: 0.5px;">
                  {{ rk.group_name }}
                </small>
              </div>
            {% endif %}
            {% set period_txns = txn_by_period.get(period.id, []) %}
            {% set matched = [] %}
            {% for txn in period_txns %}
              {% if txn.category_id == rk.category_id and txn.is_expense and not txn.is_deleted and txn.status_id != STATUS_CANCELLED %}
                {% if rk.template_id is not none and txn.template_id is not none %}
                  {% if txn.template_id == rk.template_id %}
                    {% if matched.append(txn) %}{% endif %}
                  {% endif %}
                {% else %}
                  {% if txn.name == rk.txn_name %}
                    {% if matched.append(txn) %}{% endif %}
                  {% endif %}
                {% endif %}
              {% endif %}
            {% endfor %}
            {% for txn in matched %}
            <div class="list-group-item d-flex justify-content-between align-items-center py-2 mobile-txn-card"
                 role="button" tabindex="0"
                 data-mobile-txn-id="{{ txn.id }}"
                 aria-label="{{ rk.display_name }}: ${{ '{:,.2f}'.format(txn.effective_amount) }}">
              <div class="d-flex align-items-center gap-2">
                <span class="small">{{ rk.display_name }}</span>
                {% if txn.status.is_settled %}
                  <span class="badge-done">&#10003;</span>
                {% elif txn.status_id == STATUS_CREDIT %}
                  <span class="badge-credit">CC</span>
                {% endif %}
                {% if txn.transfer_id %}
                  <i class="bi bi-arrow-left-right text-muted"
                     style="font-size: 0.65rem;"></i>
                {% endif %}
              </div>
              <span class="font-mono fw-semibold">${{ "{:,.0f}".format(txn.effective_amount) }}</span>
            </div>
            {% endfor %}
          {% endfor %}
        </div>
      </div>
    </div>

    {# --- Net Cash Flow --- #}
    {% set net = subtotals[period.id].net %}
    <div class="d-flex justify-content-between align-items-center px-3 py-2 mb-2 rounded"
         style="background: var(--shekel-group-header-bg);">
      <span class="fw-bold small">Net Cash Flow</span>
      <span class="font-mono fw-bold {{ 'text-danger' if net < 0 else 'text-success' }}">
        {% if net < 0 %}-{% endif %}${{ "{:,.0f}".format(net|abs) }}
      </span>
    </div>

    {# --- Projected End Balance --- #}
    {% set bal = balances.get(period.id) %}
    <div class="card {{ 'border-danger' if bal is not none and bal < 0 else '' }}">
      <div class="card-body text-center py-2">
        <div class="text-muted small">Projected End Balance</div>
        <div class="fs-4 fw-bold font-mono"
             style="color: {{ 'var(--shekel-danger)' if bal is not none and bal < 0 else 'var(--shekel-accent)' }};">
          {% if bal is not none %}
            {% if bal < 0 %}<i class="bi bi-exclamation-triangle-fill me-1"></i>{% endif %}
            ${{ "{:,.0f}".format(bal) }}
          {% else %}
            --
          {% endif %}
        </div>
      </div>
    </div>
  </div>
  {% endfor %}
</div>
```

**Mobile grid CSS (`app.css`):**

```css
/* =============================================
   MOBILE GRID CARD VIEW
   ============================================= */
.mobile-txn-card {
  cursor: pointer;
  transition: background-color 0.1s;
  min-height: 44px;
}

.mobile-txn-card:active,
.mobile-txn-card:focus-visible {
  background-color: rgba(var(--shekel-accent-rgb), 0.10);
}

#mobile-grid .card-header[data-bs-toggle="collapse"] {
  cursor: pointer;
}

#mobile-grid .card-header[data-bs-toggle="collapse"]:active {
  opacity: 0.8;
}
```

**Period navigation JS (`mobile_grid.js`):**

```javascript
/**
 * mobile_grid.js -- Period navigation and swipe support for the mobile
 * budget grid card view.
 *
 * Manages visibility of period panels and updates the header label.
 * Supports arrow button clicks and touch swipe gestures.
 */
(function() {
  var currentIndex = 0;
  var panels = [];

  function init() {
    panels = Array.from(document.querySelectorAll('.mobile-period-panel'));
    if (panels.length === 0) return;

    var prevBtn = document.getElementById('mobile-period-prev');
    var nextBtn = document.getElementById('mobile-period-next');

    if (prevBtn) prevBtn.addEventListener('click', function() { navigate(-1); });
    if (nextBtn) nextBtn.addEventListener('click', function() { navigate(1); });

    updateLabel();
    setupSwipe();
  }

  function navigate(delta) {
    var newIndex = currentIndex + delta;
    if (newIndex < 0 || newIndex >= panels.length) return;

    panels[currentIndex].style.display = 'none';
    currentIndex = newIndex;
    panels[currentIndex].style.display = '';
    updateLabel();
  }

  function updateLabel() {
    var panel = panels[currentIndex];
    if (!panel) return;

    var label = document.getElementById('mobile-period-label');
    if (label) {
      var strong = label.querySelector('strong');
      if (strong) strong.textContent = panel.dataset.periodLabel || '';
      var range = document.getElementById('mobile-period-range');
      if (range) range.textContent = panel.dataset.periodRange || '';
    }

    // Update button disabled state
    var prevBtn = document.getElementById('mobile-period-prev');
    var nextBtn = document.getElementById('mobile-period-next');
    if (prevBtn) prevBtn.disabled = currentIndex === 0;
    if (nextBtn) nextBtn.disabled = currentIndex === panels.length - 1;
  }

  function setupSwipe() {
    var grid = document.getElementById('mobile-grid');
    if (!grid) return;

    var startX = 0;
    var startY = 0;

    grid.addEventListener('touchstart', function(e) {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
    }, { passive: true });

    grid.addEventListener('touchend', function(e) {
      var dx = e.changedTouches[0].clientX - startX;
      var dy = e.changedTouches[0].clientY - startY;

      // Only trigger horizontal swipe if horizontal distance > vertical
      // and exceeds a minimum threshold (50px).
      if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 50) {
        if (dx < 0) navigate(1);   // Swipe left: next period
        else navigate(-1);          // Swipe right: previous period
      }
    }, { passive: true });
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Tap-to-edit: open bottom sheet for transaction
  document.addEventListener('click', function(e) {
    var card = e.target.closest('.mobile-txn-card[data-mobile-txn-id]');
    if (card) {
      var txnId = parseInt(card.dataset.mobileTxnId);
      if (typeof openFullEdit === 'function') {
        openFullEdit(txnId, card);
      }
    }
  });
})();
```

**Include script in `base.html`:**

Add after `grid_edit.js` (`base.html:241`):

```html
<script src="{{ url_for('static', filename='js/mobile_grid.js') }}"></script>
```

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-10-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-10-2 | Mobile grid visible at 375px | Chrome DevTools, 375px | Load budget grid | Card view visible; desktop table hidden | Manual |
| M-10-3 | Desktop grid visible at 768px | Chrome DevTools, 768px | Load budget grid | Desktop table visible; card view hidden | Manual |
| M-10-4 | Period navigation | Chrome DevTools, 375px | Click right arrow | Next period's transactions appear; header label updates | Manual |
| M-10-5 | Swipe navigation | Chrome touch emulation, 375px | Swipe left on card view | Next period appears | Manual |
| M-10-6 | Income accordion collapse | Chrome DevTools, 375px | Tap Income section header | Income section collapses; tap again to expand | Manual |
| M-10-7 | Transaction tap-to-edit | Chrome touch emulation, 375px | Tap a transaction card | Bottom sheet opens with full edit form (from Commit #9) | Manual |
| M-10-8 | Transaction save from mobile | Chrome touch emulation, 375px | Edit amount in bottom sheet, tap Save | Bottom sheet closes; page reloads with updated data | Manual |
| M-10-9 | Balance display | Chrome DevTools, 375px | View period with negative balance | Balance card shows red border, danger icon | Manual |
| M-10-10 | Category groups | Chrome DevTools, 375px | View period with multiple categories | Category group headers separate transactions | Manual |
| M-10-11 | Empty period | Chrome DevTools, 375px | Navigate to a period with no transactions | Income/expense sections are empty; balance shows | Manual |
| M-10-12 | No horizontal scroll | Chrome DevTools, 320px | Load grid | No horizontal scrollbar on page | Manual |

### F. Manual verification steps

1. **320px:** Load the budget grid. Verify: card view is shown, no horizontal scroll, period
   navigation works (arrows and swipe), income/expense sections are collapsible, tapping a
   transaction opens the bottom sheet, balance summary is visible at bottom.
2. **375px:** Same checks. Verify text is legible, amounts are formatted correctly, status
   badges (checkmark, CC) appear next to transaction names.
3. **428px:** Same checks.
4. **768px:** Verify the desktop table grid is shown, card view is hidden. The desktop grid
   should be unchanged from pre-mobile-work behavior.
5. Test with multiple periods -- navigate through all of them. Verify the first and last
   period disable the left and right arrows respectively.

### G. Downstream effects

- The mobile grid template duplicates the transaction matching logic from `grid/grid.html`.
  If the matching algorithm changes in the desktop grid, the mobile grid must be updated too.
  Comments in both templates note this dependency.
- The `openFullEdit()` function from `grid_edit.js` is called when tapping a transaction card.
  This reuses the existing edit infrastructure and bottom sheet (Commit #9).
- The `mobile_grid.js` script is loaded on all pages via `base.html` but only activates when
  `.mobile-period-panel` elements exist (grid page only). The IIFE pattern prevents global
  namespace pollution.
- The Add Transaction modal (`grid.html:274-332`) remains outside the `d-none d-md-block`
  wrapper and works on mobile for creating new transactions.

### H. Rollback notes

Delete `grid/_mobile_grid.html` and `mobile_grid.js`. Remove the `d-none d-md-block` wrapper
from `grid.html` and the `{% include "grid/_mobile_grid.html" %}` line. Remove the
`mobile_grid.js` script tag from `base.html`. Remove mobile grid CSS from `app.css`.
Move the `#txn-popover` div back inside the grid section. Template + JS + CSS revert, no
migration.

---

## Commit #11: Add web app manifest for home screen installation

### A. Commit message

```text
feat(mobile): add web app manifest for home screen installation
```

### B. Problem statement

Without a web app manifest, mobile browsers cannot offer "Add to Home Screen" with a proper app
icon, name, and standalone display mode. Adding a manifest provides a native app-like launch
experience without any service worker or offline support.

### C. Files modified

- `app/static/manifest.json` -- New file: web app manifest
- `app/static/img/icon-192.png` -- New file: 192x192 home screen icon
- `app/static/img/icon-512.png` -- New file: 512x512 splash screen icon
- `app/templates/base.html` -- Link manifest and add theme-color meta tag

### D. Implementation approach

**Create `app/static/manifest.json`:**

```json
{
  "name": "Shekel Budget",
  "short_name": "Shekel",
  "description": "Personal budget app organized around pay periods",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "background_color": "#1E2228",
  "theme_color": "#4A9ECC",
  "icons": [
    {
      "src": "/static/img/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/static/img/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    }
  ]
}
```

The `background_color` matches the dark theme body background (`#1E2228`). The `theme_color`
matches the app's accent color (`#4A9ECC`). The `display: standalone` removes the browser
chrome when launched from the home screen.

**Create icons:**

Generate 192x192 and 512x512 PNG icons from the existing `app/static/img/shekel_logo.png` or
`favicon.png`. These can be created using any image editor or command-line tool:

```bash
# Example using ImageMagick (if available)
convert app/static/img/shekel_logo.png -resize 192x192 app/static/img/icon-192.png
convert app/static/img/shekel_logo.png -resize 512x512 app/static/img/icon-512.png
```

If the source image is not square, center it on a `#1E2228` background to match the dark theme.

**Link manifest from `base.html`:**

Add after the favicon link (`base.html:23`):

```html
<!-- Favicon -->
<link rel="icon" type="image/png" href="{{ url_for('static', filename='img/favicon.png') }}">
<!-- Web App Manifest -->
<link rel="manifest" href="{{ url_for('static', filename='manifest.json') }}">
<meta name="theme-color" content="#4A9ECC">
<!-- iOS home screen meta tags -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Shekel">
<link rel="apple-touch-icon" href="{{ url_for('static', filename='img/icon-192.png') }}">
```

The `apple-mobile-web-app-*` meta tags are needed because iOS Safari does not fully support the
web app manifest for home screen behavior. The `apple-touch-icon` provides the icon for iOS's
"Add to Home Screen."

### E. Test cases

| ID | Test | Setup | Action | Expected | Type |
|----|------|-------|--------|----------|------|
| M-11-1 | Existing test suite | Full suite | `timeout 720 pytest -v --tb=short` | All tests pass unchanged | Existing |
| M-11-2 | Manifest loads | Chrome DevTools | Open Application tab, Manifest section | Manifest loads with correct name, icons, theme color | Manual |
| M-11-3 | Add to Home Screen prompt | Chrome on Android (or Chrome DevTools mobile emulation) | Navigate to app | Browser offers "Add to Home Screen" or "Install app" | Manual |
| M-11-4 | Standalone launch | Android after adding to home screen | Tap home screen icon | App opens without browser chrome (address bar, tabs) | Manual |
| M-11-5 | iOS home screen icon | Safari on iOS | Share > Add to Home Screen | App added with correct icon and name "Shekel" | Manual |
| M-11-6 | Theme color | Chrome on Android | View app | Status bar/address bar tinted with accent color (#4A9ECC) | Manual |

### F. Manual verification steps

1. **Chrome DevTools:** Open Application tab > Manifest section. Verify the manifest loads
   without errors, shows correct name ("Shekel Budget"), start URL ("/"), display mode
   ("standalone"), and both icon sizes.
2. **Chrome on Android (if available):** Visit the app. Verify the "Add to Home Screen" prompt
   appears (or use the menu option). After adding, verify the home screen icon uses the correct
   image and tapping it opens the app in standalone mode.
3. **Safari on iOS (if available):** Use Share > Add to Home Screen. Verify icon and title
   appear correctly.
4. **Any browser:** Verify the `<meta name="theme-color">` tag is present in the page source.

### G. Downstream effects

None. The manifest is a static JSON file with no runtime behavior. No service worker is
registered, so no caching or offline behavior is introduced.

### H. Rollback notes

Delete `manifest.json`, `icon-192.png`, and `icon-512.png`. Remove the manifest link,
theme-color meta, and apple-mobile-web-app meta tags from `base.html`. Static file + template
revert, no migration.
