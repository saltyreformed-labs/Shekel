> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Budget App -- Project Roadmap v4.6

**Version:** 4.6
**Date:** April 9, 2026
**Parent Documents:** project_requirements_v2.md, project_requirements_v3_addendum.md

---

## 1. Current State Summary

### Completed Phases

| Source Document        | Phase                            | Status                        |
| ---------------------- | -------------------------------- | ----------------------------- |
| v2 Phase 1             | Replace the Spreadsheet          | Done                          |
| v2 Phase 2             | Paycheck Calculator              | Done                          |
| v2 Phase 4             | Savings and Accounts             | Done                          |
| v2 Phase 5             | Visualization                    | Done                          |
| v2 Phase 6             | Hardening and Ops                | Done                          |
| v3 Phase 1 (was v2 P1) | Replace the Spreadsheet          | Done                          |
| v3 Phase 2 (was v2 P2) | Paycheck Calculator              | Done                          |
| v3 Phase 3             | HYSA and Accounts Reorganization | Done                          |
| v3 Phase 4             | Debt Accounts                    | Done                          |
| v3 Phase 5             | Investments and Retirement       | Done                          |
| v3 Phase 6             | Visualization                    | Done                          |
| v3 Phase 8             | Hardening and Ops                | Done                          |
| Roadmap v4 Section 3   | Critical Bug Fixes               | Done (completed March 2026)   |
| Roadmap v4 Section 3A  | Transfer Architecture Rework     | Done (completed March 2026)   |
| Roadmap v4 Section 4   | UX/Grid Overhaul                 | Done (completed March 2026)   |
| Roadmap v4 Section 4A  | Account Parameter Architecture   | Done (completed March 2026)   |
| Roadmap v4 Section 4B  | Adversarial Audit Remediation    | Done (completed March 2026)   |
| Roadmap v4 Section 5A  | Cleanup Sprint                   | Done (completed April 2026)   |
| Roadmap v4 Section 5   | Debt and Account Improvements    | Done (completed April 2026)   |
| Unplanned              | Mobile Responsiveness            | Done (completed April 2026)   |

### Deferred Indefinitely

| Source Document | Phase     | Reason                                   |
| --------------- | --------- | ---------------------------------------- |
| v3 Phase 7      | Scenarios | Effort not worth the reward at this time |

### Production Status

The app moved to production on March 23, 2026. It runs as a Docker container on an Arch
Linux desktop, with internal access via Nginx and a DNS override, and external access via a
Cloudflare Tunnel. The primary focus is now stabilization, daily-use polish, and incremental
feature development.

---

## 2. Remaining Work -- Phase Ordering

| Priority | Phase                         | Summary                                                                                                                     |
| -------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| ~~1~~    | ~~Critical Bug Fixes~~        | ~~Done.~~ Completed March 2026.                                                                                             |
| ~~1A~~   | ~~Transfer Architecture~~     | ~~Done.~~ Shadow transactions, balance calculator simplification, grid integration. Completed March 2026.                   |
| ~~2~~    | ~~UX/Grid Overhaul~~          | ~~Done.~~ All 16 tasks completed. Completed March 2026.                                                                     |
| ~~2A~~   | ~~Account Parameter Arch.~~   | ~~Done.~~ Metadata-driven dispatch, unified interest params, enhanced settings UI. Completed March 2026.                    |
| ~~2B~~   | ~~Audit Remediation~~         | ~~Done.~~ Critical, high, and medium findings addressed. Completed March 2026.                                              |
| ~~3A~~   | ~~Cleanup Sprint~~            | ~~Done.~~ All 5 tasks completed. Completed April 2026.                                                                      |
| ~~3~~    | ~~Debt and Account Improvements~~ | ~~Done.~~ All 16 tasks completed. Completed April 2026.                                                                 |
| ~~--~~   | ~~Mobile Responsiveness~~     | ~~Done.~~ Unplanned work; CSS/JS/template-only changes for mobile-responsive web. Completed April 2026.                     |
| 4        | Visualization and Reporting   | **In progress.** Chart bug fixes, summary dashboard, financial calendar, year-end summary, budget variance (6.5), spending trends (6.7), analytics page replacing /charts, CSV export for analytics views. Tasks 6.5, 6.6, 6.7 computation engines and display layers built here. |
| 5        | Phase 9: Smart Features       | Seasonal expense forecasting, smart estimates, expense inflation, deduction inflation, third paycheck suggestions, estimate confidence indicator, bill due date optimization, YoY seasonal comparison, expense anomaly detection |
| 6        | Phase 10: Notifications       | 15 notification types across 6 groups (balance/cash flow, bills/payments, savings/goals, debt, templates/trends, digests). Bell icon with dropdown + `/notifications` page. Grouped settings UI at `/settings/notifications`. Snooze, auto-resolve, deduplication. Email delivery deferred pending mail server (quiet hours, preferred delivery time, batching). |
| 7        | Data Export                   | CSV export (full transaction export), PDF reports, full data backup. Note: CSV export for analytics views is built in Section 8; this section covers standalone transaction-level CSV export and other formats. |
| 8        | Spending Tracker & Companion  | Sub-transaction entry tracking on budget-type transactions with remaining balance visibility. Entry-level credit card workflow with aggregated CC paybacks. Companion user role with simplified mobile-first view for household members who handle purchasing but do not manage the full budget. |
| 9        | Multi-user (far future)       | Kid accounts, registration flow, account sharing model; not actively planned                                                |

---

## 3. Critical Bug Fixes (Priority 1) -- COMPLETE

All items in this section were completed in March 2026. The section is retained for
historical reference.

### 3.1 Tax Calculation on Gross Pay Instead of Taxable Income -- COMPLETE

- **Resolution:** Fixed. Taxes are now calculated on taxable income after pre-tax deductions.

### 3.2 Recurrence Rule: Every 6 Months Calculates Incorrectly -- COMPLETE

- **Resolution:** Investigated and found that recurrence rules calculate correctly. No code
  change was required. The audit confirmed that all recurrence patterns (every_n_periods,
  monthly, monthly_first, annual) produce correct results. This finding eliminates the need
  for the follow-up audit that was planned as task 5.2.

### 3.3 Net Biweekly Mismatch Between Salary Profile and Grid -- COMPLETE

- **Resolution:** Fixed. The salary profile and grid now display consistent net biweekly
  amounts.

### 3.4 Raises Require Page Refresh Before Adding a Second -- COMPLETE

- **Resolution:** Fixed. The HTMX response now correctly re-renders the form and list after
  adding a raise.

### 3.5 Cannot Edit Raises and Deductions -- COMPLETE

- **Resolution:** Fixed. Edit UI is now available for raises and deductions.

### 3.6 Escrow: Cannot Add Amount When Inflation Rate Is Present -- COMPLETE

- **Resolution:** Fixed. Escrow components can now be added with inflation rates.

### 3.7 Escrow: Hard Refresh Required After Adding Component -- COMPLETE

- **Resolution:** Fixed. The HTMX response now triggers recalculation of the payment summary
  after adding an escrow component.

### 3.8 Pension Date Validation Missing -- COMPLETE

- **Resolution:** Fixed. Server-side validation now enforces that retirement dates are after
  the hire date and after today.

### 3.9 Stale Retirement Settings Message -- COMPLETE

- **Resolution:** Fixed. The obsolete message has been removed.

### 3.10 Paycheck Calibration -- COMPLETE

- **Resolution:** Implemented. Users can calibrate the paycheck calculator from a real pay
  stub. This feature also eliminates the need for the "Actual Paycheck Value Entry" evaluation
  that was planned as task 5.3.

---

## 3A. Transfer Architecture Rework -- COMPLETE

All items in this section were completed in March 2026. The section is retained for
historical reference.

**Supporting documents:** `docs/transfer_rework_design.md`,
`docs/transfer_rework_inventory.md`, `docs/transfer_rework_implementation.md`

**Summary:** The app previously maintained two parallel systems for financial activity:
`budget.transactions` and `budget.transfers`. This dual-path architecture caused transfers to
be invisible to cash flow tracking and category-based reporting, imposed a feature tax on every
future phase, and prevented one-time transfers from working. The rework introduced linked shadow
transactions: when a transfer is created, the transfer service atomically creates two
`budget.transactions` rows (one expense, one income) linked back to the transfer via
`transfer_id`. The balance calculator was simplified to query only `budget.transactions`,
eliminating the dual-path problem.

**Key invariants established by this rework:**

1. Every transfer has exactly two linked shadow transactions (one expense, one income).
2. Shadow transactions are never orphaned and never created without their sibling.
3. Shadow transaction amounts, statuses, and periods always equal the parent transfer's.
4. No code path directly mutates a shadow transaction. All mutations go through the transfer
   service.
5. The balance calculator queries ONLY `budget.transactions`. It does not also query
   `budget.transfers`. Double-counting is the highest-risk failure mode.

**Impact on other sections:** Task 4.2 (Footer Condensation) was superseded by Phase 3A-II
of this rework. The grid's transfer section was removed; transfer-linked transactions now
appear inline in the income and expense sections.

---

## 4. UX/Grid Overhaul (Priority 2) -- COMPLETE

All items in this section were completed in March 2026. The section is retained for
historical reference.

**Supporting document:** `docs/implementation_plan_section4.md`

**Goal:** A focused sprint to improve the daily-use experience of the budget grid and related
views. Every fix in this phase made the payday reconciliation workflow faster or clearer.

### 4.1 Grid Layout: Category/Transaction Name Clarity -- COMPLETE

- **Resolution:** Implemented with Option A (full row headers). Each transaction gets its own
  row header with the transaction name, providing clear identification during the payday
  reconciliation workflow.

### 4.2 Footer Condensation -- COMPLETE (Superseded)

- **Resolution:** The footer was condensed during the transfer architecture rework (Phase
  3A-II). The transfer section of the grid was removed and the footer was condensed
  simultaneously. No further work needed.

### 4.3 Pay Period Date Format Cleanup -- COMPLETE

- **Resolution:** Fixed. Pay period column headers use a single date format. MM/DD within the
  current year, MM/DD/YY only when crossing into a different year.

### 4.4 Status Refactor and Rename -- COMPLETE

- **Resolution (4.4a):** All status lookups refactored from string-based `name` comparisons to
  integer ID lookups using enum-cached constants. Boolean columns added to `ref.statuses` for
  grouping logic.
- **Resolution (4.4b):** "Done" renamed to "Paid" in `ref.statuses.name`. The name field is now
  a display-only label with no load-bearing role in application logic.
- **Resolution (4.4c):** All reference tables (`ref.account_types`, `ref.transaction_types`,
  `ref.recurrence_patterns`) audited and converted from string-based lookups to ID-based
  lookups using enum-cached constants. The `category_id` foreign key on `ref.account_types` is
  now used for programmatic classification.

### 4.5 Deduction Frequency Display -- COMPLETE

- **Resolution:** Fixed. Deduction frequency displays as a descriptive label (e.g., "26x/year",
  "12x/year") with improved column spacing.

### 4.6 Tax Config Page Reorganization -- COMPLETE

- **Resolution:** Fixed. User-adjustable settings (filing status, state rate) moved to the top.
  Static bracket tables moved to a collapsible section at the bottom. Previous tax years hidden
  by default.

### 4.7 Account Parameter Setup UX -- COMPLETE

- **Resolution:** Implemented. All parameterized account types redirect to their parameter
  configuration page after creation. "Setup Required" badges appear on unconfigured account
  cards. Retirement accounts have default return rates pre-populated and saved as part of the
  creation flow.

### 4.8 Mortgage Parameter Page Flow -- COMPLETE

- **Resolution:** Evaluated and addressed as part of task 4.7. Mortgage accounts redirect to
  their parameter page after creation with a setup banner.

### 4.9 Chart: Mortgage Balance Over Time Contrast -- COMPLETE

- **Resolution:** Fixed. The "standard payments" line on the mortgage account detail page chart
  has increased contrast with a distinct dash pattern and line weight.

### 4.10 Chart: Balance Over Time Time Frame Control and Sizing -- COMPLETE

- **Resolution:** Fixed. Chart container CSS corrected so the Chart.js canvas fills the
  available card space. Time frame controls added (1 year, 5 years, 10 years, full term) with
  intelligent defaults based on account type.

### 4.11 Salary Profile Button Placement -- COMPLETE

- **Resolution:** View Breakdown and View Projection buttons moved to a prominent
  position near the top of the salary profile edit page (`/salary/{id}/edit`). The main
  salary listing page (`/salary`) duplicate buttons were removed in Section 5A, task 5A.3.

### 4.12 Grid Tooltip Enhancement -- COMPLETE

- **Resolution:** Fixed. Tooltips now show the full formatted dollar amount, actual vs.
  estimated comparison when different, status, and notes. Tooltip delay reduced for faster
  display. Transaction name removed from tooltips since task 4.1 (full row headers) makes it
  redundant.

### 4.13 Emergency Fund Coverage Calculation Fix -- COMPLETE

- **Resolution:** Fixed. All recurring transfers out of checking are now included in the monthly
  expense baseline used for the emergency fund coverage calculation.

### 4.14 Checking Account Balance Projection -- COMPLETE

- **Resolution:** Implemented. The checking account detail page now displays a balance
  projection using the same projected end balances that the grid's balance calculator computes.

### 4.15 Auto Loan Parameter Page Fixes -- COMPLETE

- **Resolution:** Fixed. The current principal field is pre-populated from the balance entered
  during account creation. The term field is now editable on the loan parameter page. Verified
  that personal loan and student loan accounts do not have the same issues (they share the
  unified loan parameter page).

### 4.16 Retirement Date Validation UX -- COMPLETE

- **Resolution:** Fixed. Field-level error indicators (red border, error message below the
  field) added to date fields that fail validation. Form data is preserved on validation failure.

### 4.17 Retirement Dashboard Return Rate Clarity -- COMPLETE

- **Resolution:** Fixed. Explanatory text added near the slider clarifying what it affects.
  Fields that are recalculated visually update when the slider changes. The relationship between
  the global slider rate and per-account rates is documented in the UI.

---

## 4A. Account Parameter Architecture -- COMPLETE

All items in this section were completed in March 2026. The section is retained for
historical reference.

**Supporting document:** `docs/account_parameter_architecture.md`

**Summary:** An architectural investigation and rework of how account types are detected and
dispatched throughout the codebase. The codebase was approximately 80% of the way to a fully
extensible account parameter architecture -- the liability category (unified `LoanParams` table,
metadata-driven dispatch, type-agnostic services) was the gold standard. The remaining
categories had hardcoded type ID checks and incomplete metadata utilization.

**Changes implemented:**

- **Asset category:** `HysaParams` model and table renamed to `InterestParams`. The
  `has_interest` boolean column added to `ref.account_types`. Dispatch logic for interest-bearing
  accounts uses the `has_interest` flag instead of hardcoded HYSA type ID checks.
- **Retirement/Investment categories:** Hardcoded type ID sets replaced with category-based
  queries using `category_id`. 529 Plan updated to `has_parameters=True`.
- **Settings UI:** Enhanced to expose category, `has_parameters`, `has_amortization`,
  `has_interest`, and `max_term_months` when creating or editing account types. User-created
  account types can now receive full functionality (parameter tables, calculation services,
  dashboards) through metadata flags alone.
- **Dispatch unification:** Account creation auto-params, chart service dispatch, and savings
  dashboard filtering all use metadata flags (`has_interest`, `has_amortization`, `category_id`)
  instead of hardcoded type ID checks.
- **Unimplemented types enabled:** Money Market and CD types enabled with proper parameter
  support. Interest detail template generalized to conditionally show maturity fields.

**Architectural result:** A user can create a new account type through the settings UI, set its
category and flags, and the system automatically provides the correct parameter table,
calculation service, and dashboard with zero code changes.

---

## 4B. Adversarial Audit Remediation -- COMPLETE

All items in this section were completed in March 2026. The section is retained for
historical reference.

**Supporting documents:** `docs/adversarial_audit.md`,
`docs/implementation_plan_audit_remediation.md`

**Summary:** A comprehensive adversarial codebase audit was conducted, reading 17,844 lines
across 57 files. The audit identified 1 critical finding, 11 high findings, 17 medium findings,
and 15 low findings. A remediation plan was created and fully implemented.

**Key findings addressed:**

- **C-01 (Critical):** Silent paycheck fallback in the recurrence engine. Broad
  `except Exception` block masked financial calculation failures, producing plausible-looking
  but incorrect numbers.
- **H-02 (High):** Scenario_id IDOR vulnerability. Added ownership checks to transaction create
  routes.
- **H-03 (High):** IDOR info leakage in salary route error messages. Unified "not found" and
  "not authorized" responses.
- **H-04 (High):** Shared AccountType mutation accessible to all users.
- **H-05 (High):** Grid subtotals using float arithmetic instead of Decimal.
- **H-10 (High):** Seed script crash on migrated databases.
- **H-01 (High):** Systematic use of ref-table string names for logic. (Addressed
  comprehensively by task 4.4a/4.4b/4.4c.)

**Systemic pattern addressed:** Broad `except Exception` blocks that silently masked financial
calculation failures were narrowed or replaced with explicit error handling throughout the
codebase.

---

## 5A. Cleanup Sprint (Priority 3A) -- COMPLETE

All items in this section were completed in April 2026. The section is retained for
historical reference.

**Goal:** Address production feedback items from `fixes_improvements.md` that do not belong in
Section 5 but should be resolved before starting the debt and account improvements phase. This
sprint fixes a core grid calculation deficiency, improves grid readability, resolves settings
page gaps, and establishes a unified delete/archive pattern used by Section 5.9 and future
CRUD work.

**Prerequisite:** All Sections 3, 3A, 4, 4A, and 4B are implemented, tested, and merged
(same prerequisite as Section 5).

**Source:** All tasks in this section originate from `fixes_improvements.md` production
feedback analysis conducted on March 31, 2026.

### 5A.1 Grid: Estimated vs. Actual Calculation

- **Source:** fixes_improvements.md (Grid, item 2)
- **Problem:** The grid and balance calculator compute projected end balances using only
  `estimated_amount`, ignoring `actual_amount` entirely. This means that when a user enters
  an actual amount that differs from the estimate (e.g., an electricity bill comes in at $350
  vs. the $300 estimate), the projected end balance still reflects the estimate. The user
  sees more (or less) money available than reality. This undermines the core value proposition
  of tracking estimated vs. actual amounts.
- **Fix:** The balance calculator should use `actual_amount` when it is populated and fall
  back to `estimated_amount` when `actual_amount` is null. This is the `effective_amount`
  concept: `effective_amount = actual_amount if actual_amount is not None else
  estimated_amount`. All projection logic that currently reads `estimated_amount` should read
  `effective_amount` instead.
- **Scope:** `balance_calculator.py` is the primary target. Any other service that computes
  balances or totals from transaction amounts (e.g., grid subtotals, chart projections) must
  also be audited for the same pattern. The grid template's display of amounts may also need
  adjustment to visually indicate when the effective amount is actual vs. estimated.
- **Why prerequisite to Section 5:** Section 5.1 introduces payment linkage where confirmed
  payments (Paid/Settled status) carry actual amounts. If the balance calculator ignores
  actuals, the entire payment-aware projection system built in Section 5 would be undermined
  from the start.
- **Edge cases:** Transactions with `actual_amount = 0` (legitimate zero-dollar transaction,
  e.g., a waived fee) must be treated as "actual is populated" and use zero, not fall back to
  the estimate. The null check must distinguish between null (no actual entered) and zero
  (actual is zero).
- **Testing:** Test with: actual populated and differs from estimate (verify effective uses
  actual), actual is null (verify effective uses estimate), actual is zero (verify effective
  uses zero, not estimate), mix of actual and estimate transactions in the same pay period
  (verify subtotals use the correct effective amount for each).

### 5A.2 Grid: Category Item Sub-Headers

- **Source:** fixes_improvements.md (Grid, item 1)
- **Problem:** Grid transactions are sorted by Category Item Name, but the grid displays only
  Category Group and Transaction Name. Because the Category Item Name is not visible, the
  sort order appears random to the user. For example, transactions in the "Auto" group sorted
  by item (Car Insurance, Car Payment, Gas) appear interleaved in a way that makes no sense
  when only the group name "Auto" and the transaction name are shown.
- **Fix:** Add visual sub-header rows or styled separator bars within each Category Group to
  display the Category Item Name. Transactions are visually nested under their item
  sub-header, making the existing sort order self-evident. The hierarchy becomes:
  Category Group (existing header) -> Category Item Name (new sub-header) -> Transaction Name
  (existing row).
- **Implementation:** This is primarily a template change in the grid rendering logic. The
  grid query may need a minor adjustment to ensure results are ordered by Category Group,
  then Category Item Name, then Transaction Name, and that the template can detect when the
  item name changes to insert a sub-header. The sub-headers should be visually lightweight
  (not full row headers) to avoid cluttering the grid. A smaller font size, muted color, or
  indented label would distinguish them from the group headers.
- **Testing:** Test with: multiple items within a single group (verify sub-headers appear at
  each item boundary), a group with only one item (verify sub-header still appears for
  context), transactions spanning multiple groups (verify group and item headers nest
  correctly).

### 5A.3 Salary Profile: Remove Duplicate Buttons on /salary

- **Source:** fixes_improvements.md (Salary, item 1); partially addressed by Task 4.11
- **Problem:** Task 4.11 corrected the button placement on the salary profile edit page
  (`/salary/{id}/edit`), but the main salary listing page (`/salary`) still displays both
  inline action icons (View Breakdown, View Projection) under the Actions column and
  redundant full-width buttons below the salary profile card. The inline icons should be
  kept; the full buttons should be removed.
- **Fix:** Remove the full-width View Breakdown and View Projection buttons from the
  `/salary` template. Keep only the inline action icons under the Actions column.
- **Scope:** Template-only change to `app/templates/salary/index.html` (or equivalent
  listing template). No route or service changes.
- **Testing:** Visual verification that the `/salary` page shows only inline icons, no
  full buttons. Verify that the inline icons still link to the correct pages.

### 5A.4 Settings: Category Management Overhaul

- **Source:** fixes_improvements.md (CRUD, items 2 and related production feedback)
- **Problem:** The settings page for categories has three deficiencies:
  1. Categories can be deleted but not edited. If a user misspells a category item name or
     wants to rename it, they must delete and recreate it (losing any transaction
     associations).
  2. Adding a new item to an existing group requires the user to type the group name exactly.
     Any typo or case mismatch creates a new group instead of adding to the existing one.
     There is no dropdown, autocomplete, or selection mechanism.
  3. There is no explicit path to create a new group. New groups are created implicitly by
     typing a group name that does not already exist, but this is not discoverable.
- **Fix:** Three improvements to the category management UI:
  1. **Edit capability:** Each category item gets an edit action (icon or button) that opens
     an inline form or modal allowing the user to rename the item and/or re-parent it to a
     different group. Re-parenting uses the same group dropdown described below.
  2. **Improved add flow:** Replace the free-text group name field with a dropdown/select
     populated with existing group names. The dropdown includes an "Add new group" option at
     the bottom. Selecting an existing group sets the group context; the user then enters only
     the new item name. Selecting "Add new group" expands an additional field for the new
     group name before proceeding to the item name.
  3. **Re-parent support:** The edit form includes a group dropdown (same as the add flow)
     that allows moving an item from one group to another. All transactions associated with
     the item follow it to the new group (the association is by item ID, not group name, so
     no data migration is needed -- this should be verified during implementation).
- **Scope:** Settings routes for category management, the category form template(s), and
  validation schemas. The category model itself may not need changes if group assignment is
  already by foreign key rather than string matching (verify during implementation).
- **Testing:** Test with: editing an item name (verify rename persists and transactions
  retain association), re-parenting an item to a different group (verify item moves and
  transactions follow), adding an item to an existing group via dropdown (verify no duplicate
  group created), adding an item via "Add new group" (verify new group created with the new
  item), attempting to create a duplicate item name within the same group (verify validation
  rejection).

### 5A.5 CRUD Consistency: Unified Delete/Archive Pattern

- **Source:** fixes_improvements.md (CRUD, item 1)
- **Problem:** The app has inconsistent delete and deactivate behavior across entities. Some
  entities (Recurring Transactions, Accounts, Recurring Transfers) can only be deactivated
  but not deleted. If a user creates one of these by mistake, they must view it indefinitely.
  Other entities can be deleted but not deactivated. There is no unified pattern.
- **Fix:** Establish a two-step lifecycle pattern applied consistently across all deletable
  entities:

  **Lifecycle states:** Active -> Archived -> (conditionally) Permanently Deleted.

  **Archive (always available, reversible):**
  - The entity stops appearing in active views (grid, dashboards, dropdowns).
  - Recurrence generation stops for archived templates.
  - All historical data is preserved intact.
  - The entity appears in a collapsed "Archived" section on its parent page.
  - Un-archive action returns the entity to Active state.

  **Permanent delete (conditional, irreversible):**
  - Available directly from Active state (with confirmation dialog) only when the entity has
    no dependent history in Paid or Settled status.
  - For entities with Paid/Settled history: archive is the terminal state. Permanent delete
    is blocked entirely (Option B). The UI explains why: "This item has payment history and
    cannot be permanently deleted. It has been archived."
  - Confirmation dialog is explicit about consequences: "This template has no payment
    history. Delete permanently? This cannot be undone." or "This account has 47 paid
    transactions. It can be archived but not permanently deleted."

  **History detection per entity type:**
  - **Transaction templates:** History = any linked transaction where `status` is Paid or
    Settled. Projected-status transactions are ephemeral and can be cascade-deleted.
  - **Transfer templates:** History = any linked transfer whose shadow transactions have Paid
    or Settled status. Cascade must respect the transfer service's shadow transaction
    invariants.
  - **Accounts:** History = any transactions, transfers, or parameter records associated with
    the account. Accounts with history are always archive-only (Option B). This aligns with
    Section 5.9's planned `is_archived` column.
  - **Categories:** History = any transactions or templates assigned to the category.
    Categories with assignments are archive-only. Categories with no assignments can be
    hard-deleted.

- **Open design decision:** Whether `is_archived` is implemented as a unified column across
  all four entity tables (accounts, transaction templates, transfer templates, categories) or
  handled per-entity with potentially different column names. This will be decided during the
  implementation plan phase. The behavioral pattern is the same regardless of schema approach.
  The existing `is_active` column on some tables may need to be reconciled with `is_archived`
  (coexist with different semantics, or migrate one to the other).
- **Relationship to Section 5.9:** Section 5.9 (Loan Payoff Lifecycle) plans an `is_archived`
  column on accounts specifically for paid-off loan archival. Task 5A.5 establishes the
  broader pattern that 5.9 builds on. If 5A.5 adds `is_archived` to the accounts table, 5.9
  consumes it rather than creating it. If the `is_archived` unification decision defers the
  accounts column to 5.9, then 5A.5 implements the pattern on templates and categories only,
  and 5.9 extends it to accounts.
- **Scope:** Routes and templates for transaction template management, transfer template
  management, account management, and category management. Validation schemas for delete
  endpoints. Possibly a migration to add `is_archived` columns. The archive/delete logic
  should be extracted into a shared utility or mixin to avoid duplicating the history-check
  and state-transition logic across four different route files.
- **Testing:** Per entity type, test: archive an entity (verify hidden from active views,
  visible in archived section), un-archive (verify returns to active), delete entity with no
  history (verify permanent removal), attempt delete on entity with Paid history (verify
  blocked with appropriate message), delete entity with only Projected history (verify
  allowed after cascade-deleting projected records), archive then attempt delete on entity
  with history (verify still blocked from archived state).

---

## 5. Debt and Account Improvements (Priority 3) -- COMPLETE

All items in this section were completed in April 2026. The section is retained for
historical reference.

**Goal:** Complete the debt account story: connect payments to the amortization engine, add ARM
rate projection support, handle loan payoff lifecycle, provide multi-scenario and cross-account
payoff visualization (including snowball/avalanche strategies and refinance what-if), add
aggregate debt health metrics (summary and DTI ratio), and add income-relative savings goals.
This section also includes a prerequisite SRP refactor of the savings dashboard. This section
was originally titled "Recurring Transaction Improvements" and has been expanded and retitled
to reflect its broader scope.

**Account type context:** The app seeds four debt account types: mortgage, auto loan, personal
loan, and student loan. All share the amortization engine and a unified `LoanParams` table
(one row per amortizing account). All four types are served by a single route file (`loan.py`),
a single template set (`loan/`), and a single set of schemas. The `has_amortization` flag on
`ref.account_types` drives all debt-specific dispatch logic. The payoff calculator (extra
payment mode and target date mode) already renders on all debt account pages as a result of the
unified loan infrastructure.

**Architectural context -- what has changed since this section was originally written:**

The following work was completed between the original drafting of this section (v4.1, March 27,
2026) and the current version. These changes affect the assumptions, mechanics, and complexity
of the tasks below:

1. **Transfer architecture rework (Section 3A):** Transfers now produce linked shadow
   transactions in `budget.transactions`. The balance calculator queries only
   `budget.transactions`. Shadow income transactions on a debt account represent payments
   received by that account. This changes how payment history is discovered for the
   amortization engine (task 5.1).

2. **Section 4 UX/Grid Overhaul (fully completed):**
   - Task 4.4c established enum-cached ID lookups for all reference tables. The `category_id`
     foreign key on `ref.account_types` is now used for programmatic classification. Debt
     accounts are identified by `has_amortization=True`, not by hardcoded type name lists.
   - Task 4.7/4.8 implemented post-creation redirect to parameter configuration for all
     parameterized account types, including loan accounts. The UX flow for task 5.1's
     recurring transfer prompt builds on top of this existing redirect.
   - Task 4.15 fixed the auto loan parameter page (balance/principal disconnect, missing term
     field). All loan types share the unified parameter page.

3. **Account parameter architecture (Section 4A):** Dispatch logic is now fully
   metadata-driven. The `has_amortization` flag identifies debt accounts throughout the
   codebase without hardcoded type checks. The settings UI exposes all relevant flags for
   user-created account types.

4. **Adversarial audit remediation (Section 4B):** Audit findings related to the loan parameter
   layer (including CHECK constraints, schema validation gaps, and error handling) have been
   addressed.

**Note on implementation_plan_section5.md:** The existing `docs/implementation_plan_section5.md`
predates the transfer rework, Section 4 implementation, account parameter architecture changes,
and adversarial audit. Its assumptions about file structures, line numbers, dispatch patterns,
and parameter table layouts are stale. A new implementation plan must be written from scratch
using a fresh codebase inventory before starting this section.

**Prerequisite -- Section 5A:** Section 5A (Cleanup Sprint) must be completed before starting
Section 5. Task 5A.1 (estimated-vs-actual calculation fix) ensures the balance calculator
correctly uses actual amounts, which is foundational to the payment linkage work in task 5.1.
Task 5A.5 (unified delete/archive pattern) establishes the archival pattern that task 5.9
(loan payoff lifecycle) builds on.

### 5.1 Debt Account Payment Linkage

- **Goal:** Connect debt account payments (transfers) to the amortization engine so that the
  full payment timeline -- confirmed payments, committed future payments, and extra payments --
  is reflected in balance projections and payoff dates.
- **Scope:** All debt account types identified by `has_amortization=True` (currently mortgage,
  auto loan, personal loan, student loan).
- **Two components:**

  1. **Recurring transfer creation prompt:** After a debt account is created and its parameters
     are saved (the user is already on the parameter page via the 4.7/4.8 redirect flow),
     offer a prompt: "Create a recurring monthly transfer of $X from [source account] to this
     account?" where $X is the calculated monthly payment from the amortization engine. This
     creates a transfer record and a corresponding recurrence rule. The prompt should be
     offered on the parameter page after the initial save, not during account creation itself
     (the monthly payment amount is not known until parameters are saved and the amortization
     engine runs).

  2. **Payment linkage to amortization:** The amortization engine currently projects from
     static loan parameters (original principal, interest rate, term, origination date) without
     incorporating payment data from the transfer system. After this task, the engine accepts
     an additional input: a list of payments with dates, amounts, and statuses. The engine
     uses this data to compute different projection scenarios (see task 5.5).

- **How payment data is discovered (post-transfer-rework):** The transfer architecture rework
  (Section 3A) creates shadow transactions for every transfer. When a transfer sends money to a
  debt account, an income-type shadow transaction is created on that account's behalf. The
  recurrence engine generates projected shadow transactions for future pay periods from the
  recurring transfer template. The caller assembles the full payment timeline by querying shadow
  income transactions linked to the debt account (transactions where `transfer_id IS NOT NULL`
  targeting the debt account). This query runs against `budget.transactions` only -- consistent
  with the transfer rework invariant that the balance calculator and related services never
  query `budget.transfers` directly.
- **Payment categories:** The shadow transactions returned by this query fall into two
  categories based on their status:
  - **Confirmed payments** (status: Paid, Settled) -- payments the user has actually made.
    These establish the real current principal.
  - **Projected payments** (status: Projected) -- future payments generated by the recurrence
    engine from the recurring transfer. These represent the user's committed payment plan,
    including any extra amount built into the recurring transfer.
  The distinction between confirmed and projected payments is critical for the visualization
  scenarios in task 5.5. The amortization engine receives the full list; the caller or the
  route handler determines which subset to use for each scenario.
- **Amortization engine contract:** The engine remains a pure function service. It does not
  query the database. The caller provides the payment list as an argument alongside the
  existing loan parameters. This preserves the service isolation pattern. The engine should
  accept payments as a list of (date, amount, is_confirmed) tuples so it can compute different
  scenarios from the same input.
- **Verification:** After implementation, verify that:
  1. Making a one-time extra payment (transfer) to a mortgage or loan account updates the
     current real principal on the account dashboard.
  2. Setting up a recurring transfer with an extra amount shows an accelerated payoff date.
  3. The balance projection chart reflects both confirmed and projected payments.

### 5.2 Audit Other Recurrence Patterns -- REMOVED

- **Resolution:** The investigation of section 3.2 confirmed that all recurrence patterns
  calculate correctly. No audit is needed. The test coverage added during 3.2 is sufficient.

### 5.3 Actual Paycheck Value Entry (Evaluate) -- REMOVED

- **Resolution:** Superseded by the Paycheck Calibration feature (section 3.10, completed).
  The calibration feature addresses the deeper need of deriving accurate tax and deduction
  rates from real pay stub data. The user can already enter an actual net pay amount in the
  grid.

### 5.4 Income-Relative Savings Goals

- **Source:** fixes_improvements.md (production feedback)
- **Problem:** Savings goals currently accept only a fixed dollar amount. For goals like
  "3 months of salary in the emergency fund," the user must manually calculate the target
  amount based on their current paycheck and re-calculate it whenever their salary changes
  (e.g., after a raise).
- **Feature:** Add a new goal amount mode alongside the existing fixed-amount mode. The user
  selects a unit (paychecks or months) and enters a multiplier (e.g., 3). The app calculates
  the dollar target automatically based on the active salary profile's projected net pay.
- **Dynamic recalculation:** When a scheduled raise takes effect (or a new calibration is
  saved), the income-relative goal target should update to reflect the new net pay. This
  prevents goals from going stale after salary changes.
- **Data model changes:**
  - Add two new reference tables following the established pattern (IDs for logic, names for
    display only, enum members in `enums.py`, ref_cache entries):
    - `ref.goal_modes` -- rows: (1, "Fixed"), (2, "Income-Relative"). Defines how the savings
      goal target amount is determined.
    - `ref.income_units` -- rows: (1, "Paychecks"), (2, "Months"). Defines the income
      multiplier unit. Used only when goal mode is income-relative.
  - Add columns to the savings goal model:
    - `goal_mode_id` (INT FK -> `ref.goal_modes`, NOT NULL, default 1 ["Fixed"]). All
      existing savings goals are fixed-amount, so the default preserves backward compatibility.
    - `income_unit_id` (INT FK -> `ref.income_units`, nullable). Used only when
      `goal_mode_id` references the income-relative mode.
    - `income_multiplier` (NUMERIC, nullable). The number of paychecks or months. Used only
      when `goal_mode_id` references the income-relative mode.
  - Add corresponding enum definitions (`GoalModeEnum`, `IncomeUnitEnum`) in `enums.py` and
    ref_cache mappings. Add seed data for the two new ref tables.
  - When `goal_mode_id` references the income-relative mode, the resolved dollar target is
    calculated on read: `multiplier * net_pay_per_unit`. This is consistent with the app's
    "calculate on read" philosophy.
- **Net pay basis:** Use the current projected net pay from the paycheck calculator (what the
  next paycheck would be). This naturally incorporates scheduled raises because the paycheck
  calculator already projects future net pay by applying raises at their effective dates. A
  rolling average of actuals would lag behind raises and add unnecessary complexity.
- **Unit definitions:**
  - Paychecks: `target = multiplier * net_biweekly_pay`
  - Months: `target = multiplier * (net_biweekly_pay * 26 / 12)` (annualize then divide by
    12 to get monthly net income)
- **UI:** On the savings goal form, add a toggle or dropdown for "Fixed amount" vs.
  "Based on income." When income-relative is selected, show the unit dropdown and multiplier
  field. Display the resolved dollar amount as a read-only calculated value so the user sees
  the actual target.

### 5.5 Payoff Calculator: Multi-Scenario Visualization -- PARTIALLY COMPLETE

- **Original scope (extend payoff calculator to all debt types):** Complete. The unified loan
  infrastructure (`loan.py`, `loan/` templates, shared `LoanParams` table) already renders the
  payoff calculator on all debt account detail pages. No additional work is needed to make the
  calculator available to auto loan, personal loan, and student loan accounts.
- **Remaining scope (multi-scenario visualization):** After task 5.1 connects payment data to
  the amortization engine, the payoff calculator and balance chart need to display three
  projection lines and a progress marker. The amortization engine receives the full payment
  list (confirmed + projected) from task 5.1; each scenario uses a different subset of that
  data.

#### Chart lines

  1. **Original schedule (line):** The standard amortization based on contractual parameters
     (original principal, interest rate, term, origination date). Standard payments only, no
     extras. This is the contractual baseline -- "what the bank expects if nothing changes."
     This is what the calculator already displays today. Computed by passing only the loan
     parameters to the amortization engine with no payment data.

  2. **Committed schedule (line):** All confirmed payments (Paid/Settled shadow transactions)
     plus all projected payments (Projected shadow transactions from the recurring transfer)
     fed into the amortization engine. This is "where I'm headed based on my current plan."
     If the user has set up a recurring transfer of $1,393.96/month ($100 over the standard
     $1,293.96 P&I), this line shows the accelerated payoff incorporating both the extra
     payments already made and the committed future extra payments. Computed by passing the
     full payment list (confirmed + projected) to the amortization engine.

  3. **What-if schedule (line, optional):** The user enters a hypothetical extra monthly
     payment amount. This projects forward from the current real principal (established by
     confirmed payments only) with the hypothetical extra added to the standard monthly
     payment. Shows "what if I changed my extra payment amount to $X going forward." Shown
     only when the user has entered a value in the what-if input. Computed by passing
     confirmed payments plus a synthetic projected series at the hypothetical amount.

#### Floor: progress marker

  The floor represents the user's confirmed position: all confirmed payments applied, then
  standard payments forward with no committed extras. This answers "where do I stand if I
  cancel all extra payments today and revert to the minimum."

  - **Chart:** Displayed as a single point/marker on the chart at the current date and current
    real principal. This marks "you are here" on the principal axis without adding a full line
    that would run nearly parallel to the original schedule and clutter the chart.
  - **Loan Summary metric:** A summary line in the Loan Summary section showing the floor
    payoff date and months saved vs. original. Example: "Current position: $178,375.43
    remaining -- payoff Dec 2048 at standard payments (0 months saved)." This gives the user
    a concrete measure of actual progress to date, separate from the projected impact of
    their committed recurring transfer.

#### Calculator display

  The payoff calculator results should show the committed schedule and floor side by side:
  committed payoff date (with months saved and interest saved vs. original) and floor payoff
  date (months saved vs. original based on confirmed payments only). When the user enters a
  what-if amount, a third result appears showing the what-if payoff date (with months saved
  and interest saved vs. committed -- showing the incremental impact of changing the extra
  payment amount).

  The "target date mode" should operate on the current real principal: given a desired payoff
  date, calculate the required monthly payment starting from the confirmed position.

#### Chart styling

  Each line should have a distinct visual style (color, dash pattern, line weight) consistent
  with the contrast improvements made in task 4.9. Suggested: original schedule as a lighter
  dashed line (reference baseline), committed schedule as a solid primary-color line (the
  user's plan), what-if schedule as a distinct dashed line (hypothetical). The floor marker
  should be a visible point (e.g., a filled circle) on the chart at the current date.

- **No recurring transfer:** If the user has not set up a recurring transfer for a debt
  account, the committed schedule line does not appear (there are no projected payments to
  consume). The chart shows only the original schedule line and the floor marker. The what-if
  input operates from the floor (current real principal, standard payments forward) and adds
  the hypothetical extra on top. This degrades gracefully to the current payoff calculator
  behavior.
- **Dependency:** Task 5.1 must be complete before the committed schedule and floor can be
  displayed. The original schedule line is already available and does not depend on 5.1.

### 5.6 Extract Savings Dashboard Business Logic into Service

- **Source:** Adversarial audit finding L-06 (SRP violation)
- **Problem:** The `savings.py:dashboard` route function is approximately 470 lines and mixes
  HTTP routing concerns (request handling, template rendering, redirect logic) with complex
  financial calculations (account balance aggregation, interest projection orchestration, goal
  progress computation, emergency fund coverage calculation). This violates the Single
  Responsibility Principle and the project's established pattern where all financial
  calculations live in service files.
- **Why now:** Task 5.4 modifies savings goal behavior (adding income-relative goals). Modifying
  financial logic embedded in a 470-line route function is risky and hard to test. Extracting
  the logic first makes 5.4 safer to implement and independently testable.
- **Approach:** Create `services/savings_dashboard_service.py`. Extract all financial
  computation logic from the route into pure functions that take plain data and return plain
  data (consistent with the existing service isolation pattern -- no Flask imports, no
  request/session access). The route function becomes a thin orchestrator: validate input, call
  service, render template.
- **Scope:** The savings dashboard route only. The retirement dashboard
  (`retirement.py:_compute_gap_data`, ~350 lines, same SRP violation) is a candidate for the
  same treatment but is not in scope for Section 5 since no Section 5 task modifies retirement
  logic. It can be addressed in a future tech debt pass.
- **Constraint:** This is a refactor -- no user-visible behavior changes. All existing savings
  dashboard tests must pass without modification after the extraction. New tests should cover
  the extracted service functions directly.

### 5.7 ARM Rate Adjustment Support in Amortization Engine

- **Problem:** The `LoanParams` table stores ARM fields (`is_arm`,
  `arm_first_adjustment_months`, `arm_adjustment_interval_months`) and the `rate_history` table
  stores historical rate changes. However, the amortization engine currently ignores ARM fields
  and projects the entire remaining term at the current interest rate. For an ARM mortgage, this
  produces incorrect long-term projections -- the rate will adjust at defined intervals, and the
  projected schedule should reflect that.
- **Scope:** ARM mortgages (and any future ARM loan type identified by `is_arm=True`).
- **Amortization engine changes:** The engine's input contract (being extended in task 5.1 to
  accept payment data) should also accept rate history entries. For ARM loans, the engine:
  1. Uses the current rate for periods before the next known adjustment date.
  2. At each adjustment point, applies the rate from rate_history if a historical entry exists
     for that date.
  3. For future adjustment dates beyond the last historical entry, projects the current rate
     forward (the user manually enters the new rate when it adjusts, per the v3 addendum
     design -- the app does not predict future rates).
- **Caller responsibility:** The route handler queries the `rate_history` table for the loan
  account and passes the entries to the engine alongside loan parameters and payment data. The
  engine remains a pure function -- it does not query the database.
- **UI:** No new UI elements. The existing rate history management on the mortgage detail page
  already supports adding rate change records. The improvement is that the amortization engine
  and the payoff calculator/chart projections now incorporate those rate changes instead of
  ignoring them.
- **Dependency:** Should be implemented after 5.1 (engine contract extension) since both modify
  the engine's input signature. Implementing them together or sequentially avoids redundant
  contract changes.

### 5.8 Amortization Engine Edge Cases

- **Problem:** With real payment data flowing into the amortization engine (task 5.1), edge
  cases that were theoretical with static projections become reachable in practice.
- **Scope:** Two defensive improvements to the amortization engine:

  1. **Overpayment handling:** If a payment amount exceeds the remaining principal (final
     payment, lump-sum payoff, or data entry error), the engine must cap the principal
     reduction at the remaining balance. The principal should never go negative. The engine
     should record the overpayment period as the final period in the schedule with the
     remaining balance reduced to zero.

  2. **Zero-balance termination:** Once the remaining principal reaches zero, the engine should
     stop generating schedule entries for subsequent periods, even if additional payments
     appear in the input data. Payments after payoff are ignored by the engine (they may
     represent refunds, escrow adjustments, or data artifacts).

- **Implementation:** These are guards added to the engine's per-period loop. They do not
  change the engine's interface -- only its behavior at boundary conditions.
- **Testing:** Test with: payment exactly equal to remaining balance, payment exceeding
  remaining balance, multiple payments after balance reaches zero, very large one-time payment
  that pays off the loan in a single period.

### 5.9 Loan Payoff Lifecycle

- **Problem:** When a loan reaches $0 principal (either through regular payments or
  accelerated payoff), several downstream concerns need to be handled:
  1. The recurring transfer created by task 5.1 should not generate shadow transactions for
     periods after the projected payoff date. Payments on a $0 balance are nonsensical.
  2. The account should be visually distinguished as paid off on the accounts dashboard.
  3. The user should have a way to archive or close a paid-off account so it stops appearing
     in active views.
- **Scope:** Three components:

  1. **Recurring transfer end date:** When the amortization engine computes a payoff date
     (incorporating actual payment data and the recurring transfer amount), the recurrence
     rule for the loan's recurring transfer should have its end date set to the projected
     payoff date. This prevents the recurrence engine from generating shadow transactions
     beyond payoff. The end date should update automatically when the projected payoff date
     changes (e.g., after an extra payment shifts it earlier). The mechanism for this update
     needs careful design -- it could be triggered when the loan dashboard is loaded
     (calculate payoff, compare to recurrence rule end date, update if different) or via a
     dedicated service function called after any payment is recorded.

  2. **Paid-off status indicator:** When a debt account's current real principal (derived from
     confirmed payments) reaches zero, the account card on the accounts dashboard should
     display a "Paid Off" badge or visual indicator. The account remains visible but is
     clearly distinguished from active debts.

  3. **Account archival:** Add the ability to archive an account. Archived accounts are hidden
     from the active accounts dashboard and do not appear in balance projections, but remain
     in the database for historical reference. The archive action should be available for any
     account type, not just debt accounts, but the primary use case is paid-off loans. A
     simple `is_archived` boolean on the `budget.accounts` table with a filter on dashboard
     queries is sufficient. An "Archived Accounts" section (collapsed by default) on the
     accounts dashboard allows the user to view and un-archive if needed.

- **Dependency:** Requires 5.1 (payment linkage) and 5.8 (zero-balance termination).
- **Relationship to Section 5A.5:** The unified delete/archive pattern established in task
  5A.5 provides the behavioral foundation for account archival. If 5A.5 adds `is_archived`
  to the accounts table, this task consumes the existing column. If 5A.5 defers the accounts
  column, this task creates it following the pattern 5A.5 established for templates and
  categories.

### 5.10 Refinance What-If Calculator

- **Problem:** A user considering refinancing has no way to compare the current loan schedule
  against a hypothetical refinanced schedule within the app. They must use external tools to
  answer "should I refinance?"
- **Feature:** A refinance scenario form on each debt account detail page (gated by
  `has_amortization=True`) that takes hypothetical refinance parameters and shows a
  side-by-side comparison against the current loan.
- **Input fields:**
  - New interest rate (required)
  - New term in months (required)
  - Closing costs (optional, default $0 -- rolled into the new principal if provided)
  - New principal defaults to: current real principal + closing costs. The user can override
    this (e.g., if they plan a cash-out refinance or are bringing cash to close).
- **Output (side-by-side comparison):**
  - Current schedule: remaining term, monthly payment, total remaining interest, payoff date
    (from the committed schedule if a recurring transfer exists, otherwise from the floor).
  - Refinanced schedule: new term, new monthly payment, total interest over new term, new
    payoff date.
  - Comparison metrics: monthly payment change (+/-), total interest change (+/-), break-even
    point (number of months until cumulative interest savings exceed closing costs -- relevant
    only when closing costs > 0).
- **Chart:** Add a refinance line to the balance-over-time chart (same styling approach as the
  what-if line in task 5.5) showing the refinanced principal trajectory alongside the current
  schedule.
- **Amortization engine reuse:** The engine already computes a schedule from any set of
  parameters. The refinance calculator calls the engine twice (once with current parameters,
  once with refinance parameters) and compares the results. No engine changes needed beyond
  what tasks 5.1, 5.7, and 5.8 already provide.
- **Dependency:** Depends on 5.1 for accurate current schedule data (confirmed + projected
  payments). The refinance comparison is most useful when the "current" side reflects the
  user's actual payment behavior, not just the original contractual terms.

### 5.11 Debt Snowball/Avalanche Strategy

- **Problem:** A user with multiple debt accounts (mortgage + auto loan + student loan) has no
  way to evaluate cross-account payoff strategies. The existing payoff calculator operates on
  one account at a time. The user cannot answer "if I have an extra $200/month for debt
  reduction, where should it go?"
- **Feature:** A cross-account debt payoff strategy calculator, accessible from the accounts
  dashboard (liability section) or a dedicated debt strategy page.
- **Strategies:**
  1. **Avalanche (mathematically optimal):** Extra payment goes to the highest interest rate
     account first. Remaining accounts receive minimum payments. When the targeted account is
     paid off, the freed payment (minimum + extra) rolls to the next highest rate.
  2. **Snowball (psychologically optimal):** Extra payment goes to the smallest balance first.
     Same rollover behavior. Pays more total interest than avalanche but provides faster
     "wins" that keep the user motivated.
  3. **Custom priority:** The user assigns a priority order to their debt accounts. Extra
     payment follows the user-defined sequence.
- **Input:**
  - Extra monthly amount available for debt reduction (required).
  - Strategy selection (avalanche, snowball, custom).
  - For custom: drag-and-drop or numbered priority list of active debt accounts.
- **Output:**
  - Per-account payoff timeline showing when each debt reaches $0 under the selected strategy.
  - Total interest paid across all debts under the selected strategy.
  - Overall debt-free date (when the last account reaches $0).
  - Comparison table: avalanche vs. snowball vs. current (no extra payments) showing total
    interest and debt-free date for each.
- **Chart:** A stacked or multi-line chart showing all debt account balances over time under
  the selected strategy. Each account is a line; the user can see balances converging to zero
  in the priority order.
- **Implementation:** An orchestration service (`services/debt_strategy_service.py`) that:
  1. Loads all active debt accounts identified by `has_amortization=True`.
  2. For each account, retrieves current real principal (from confirmed payments via 5.1) and
     loan parameters.
  3. Runs the amortization engine iteratively: for each period, allocate minimum payments to
     all accounts, apply the extra amount to the target account (per strategy), detect payoff,
     redistribute freed payments.
  4. Returns per-account schedules and aggregate metrics.
  The service is a pure function -- it receives account data and parameters, returns results.
  It does not query the database.
- **Dependency:** Requires 5.1 (payment linkage for accurate current balances) and 5.8
  (zero-balance termination for correct payoff detection). Benefits from 5.9 (payoff lifecycle)
  for the rollover behavior.

### 5.12 Debt Summary Metrics and Debt-to-Income Ratio

- **Problem:** The accounts dashboard groups accounts by category but provides no aggregate
  metrics for the liability category. The user has no single view of their total debt position
  or debt health.
- **Feature:** Two additions to the accounts dashboard:

  1. **Debt summary card:** A summary card in the liability section of the accounts dashboard
     showing aggregate metrics across all active debt accounts:
     - Total debt outstanding (sum of current real principal across all `has_amortization`
       accounts).
     - Total monthly debt payments (sum of monthly P&I from amortization engine across all
       accounts).
     - Weighted average interest rate (weighted by outstanding principal).
     - Projected debt-free date (the latest projected payoff date across all accounts, using
       the committed schedule if available or the standard schedule otherwise).
     These are computed values, not stored -- consistent with the app's "calculate on read"
     philosophy.

  2. **Debt-to-income ratio:** DTI = total monthly debt obligations / gross monthly income.
     Total monthly debt obligations comes from the debt summary (above). Gross monthly income
     comes from the paycheck calculator (active salary profile's gross biweekly pay * 26 / 12).
     Display DTI as a percentage on the accounts dashboard alongside the existing Emergency
     Fund Coverage metric (fixed in task 4.13). These two metrics together -- emergency fund
     coverage (liquidity health) and DTI (debt health) -- give the user a quick snapshot of
     their financial position.
     - Color coding or thresholds: DTI below 36% is generally considered healthy (this is the
       conventional lending guideline). Display as green below 36%, yellow 36-43%, red above
       43%. The thresholds could be user-configurable in a future enhancement but hard-coded
       defaults are fine for the initial implementation.

- **Dependency:** Requires 5.1 for accurate current principal values (derived from confirmed
  payments). The DTI calculation requires the paycheck calculator, which is already in place.

### 5.13 Full Amortization Schedule View

- **Source:** project_requirements_v3_addendum.md (deferred as "nice-to-have")
- **Problem:** The loan detail page shows summary metrics (monthly payment, payoff date, total
  interest) but does not display the full month-by-month amortization schedule. The v3 addendum
  explicitly deferred this as lower priority than summary metrics and payoff scenarios.
- **Why now:** Tasks 5.1, 5.7, and 5.8 rework the amortization engine's input contract to
  incorporate actual payments, rate changes, and edge cases. The engine now produces richer,
  more accurate output. Displaying the full schedule serves as both a user feature and a
  verification tool -- the user can cross-reference individual line items against their
  lender's statement to confirm the engine's accuracy.
- **Feature:** A collapsible section on the loan detail page showing the full month-by-month
  amortization schedule. Each row shows: payment number, date, payment amount, principal
  portion, interest portion, and remaining balance. For periods with confirmed payments, the
  row should visually distinguish actual data from projected data (e.g., bold or highlighted
  for confirmed, normal weight for projected).
- **Implementation:** The amortization engine already computes this data as its output (list of
  period tuples). The work is primarily template rendering -- iterate over the engine's output
  and display it in a table. The table should be collapsed by default (toggle button) to avoid
  cluttering the page for users who only want the summary.
- **Dependency:** Benefits from 5.1 (actual payments), 5.7 (ARM rates), and 5.8 (edge cases)
  for maximum accuracy, but can display the static schedule without them.

### 5.14 Payment Allocation Breakdown on Loan Dashboard

- **Problem:** When a user makes a mortgage payment, the full amount leaves checking but only
  the principal portion reduces the loan balance. The user cannot see how their payment is
  split between principal, interest, and escrow without consulting the full amortization
  schedule.
- **Feature:** A payment breakdown display on the loan detail page showing the allocation for
  the current (or most recent) payment period: principal portion, interest portion, and escrow
  portion (for mortgages). Example: "Of your $1,910.95 payment: $395.12 to principal, $898.84
  to interest, $616.99 to escrow."
- **Implementation:** The amortization engine already computes the principal/interest split. The
  escrow amount is the sum of active escrow components divided by 12. The breakdown is a
  display enhancement on the loan summary card -- the data is already available from existing
  service calls. For future periods, the breakdown changes as the principal decreases (more
  goes to principal over time), so the display should reflect the specific period being viewed.
- **Dependency:** Benefits from 5.1 for accurate current-period allocation based on actual
  payments.

### 5.15 Savings Goal Progress Trajectory

- **Problem:** Savings goals show a target amount and current balance but do not indicate how
  long it will take to reach the goal at the current savings rate. With task 5.4 adding
  income-relative goals where the target moves dynamically, the user needs to know whether
  they are keeping pace.
- **Feature:** For each savings goal, compute and display: estimated months to goal completion,
  projected completion date, and required monthly contribution to hit a target date (if the
  goal has one). The computation uses the current savings account balance, the goal target
  (fixed or resolved income-relative amount), and the recurring transfer amount into the
  savings account (if one exists).
- **Display:** On the savings goal card in the accounts dashboard: a progress bar (already
  exists or implied by the goal display), the projected completion date as a subtitle, and a
  pace indicator ("on track" / "behind" / "ahead" relative to the target date, if one is set).
- **Implementation:** The trajectory calculation is straightforward: `months_remaining =
  (target - current_balance) / monthly_contribution`. If no recurring transfer exists, the
  trajectory cannot be computed and the display shows "No recurring contribution set." The
  calculation should live in the savings dashboard service (extracted in task 5.6) as a pure
  function.
- **Dependency:** Requires 5.6 (savings dashboard service extraction) and 5.4 (income-relative
  goals). The recurring transfer amount is discovered the same way as debt payments in 5.1 --
  by querying shadow transactions linked to the savings account.

### 5.16 Recurring Obligation Summary Page

- **Problem:** The user's committed recurring outflows are spread across multiple views:
  recurring transaction templates (bills), recurring transfers (debt payments, savings
  contributions), and account dashboards. There is no single view showing all recurring
  financial obligations.
- **Feature:** A dedicated page (or a section on the accounts dashboard) listing every
  recurring financial obligation: all active recurring transaction templates and all active
  recurring transfer templates. For each item, display: name, amount, frequency, next
  occurrence date, and linked account (if applicable).
- **Grouping:** Group by type: recurring expenses (bills), recurring transfers out of checking
  (debt payments, savings contributions), and recurring income (paycheck). Within each group,
  sort by next occurrence date.
- **Summary metrics:** Total monthly committed outflows (sum of all recurring expenses and
  transfers, normalized to monthly), total monthly committed income, and net monthly committed
  cash flow. These are useful for answering "how much of my paycheck is already spoken for?"
- **Implementation:** Query `budget.transaction_templates` (active, with recurrence rules) and
  `budget.transfer_templates` (active, with recurrence rules). The recurrence engine already
  knows the next occurrence date for each template. The page is a read-only aggregation view --
  no new data model or services needed, just a route and template.
- **Dependency:** None. This can be implemented at any point. It becomes more valuable after
  5.1 (when debt payment transfers are created) because those transfers appear in the list.

---

## 6. Phase 9: Smart Features (Priority 5)

**Goal:** Make the app smarter about projecting future expenses based on historical patterns
and detecting anomalies. The core additions are seasonal expense forecasting, rolling average
estimates for non-seasonal variable expenses, inflation adjustments, and expense anomaly
detection at the point of data entry. This section also includes third paycheck actionable
suggestions, estimate confidence indicators, bill due date optimization analysis, and
year-over-year seasonal comparisons. Budget variance analysis (6.5), annual expense calendar
(6.6), and spending trend detection (6.7) computation engines and display layers are built in
Section 8 (Visualization and Reporting Overhaul), not in this section.

### 6.1 Seasonal Expense Forecasting

This is the primary feature of Phase 9.

#### 6.1.1 Concept

For a handful of expenses with predictable seasonal variation (electricity, gas, water, and
possibly 1-2 others), the user enters historical monthly amounts. The app uses this history to
forecast future amounts, weighting recent years more heavily and detecting year-over-year
trends.

#### 6.1.2 Data Model

New table for historical expense data:

```
budget.seasonal_history
- id: SERIAL PRIMARY KEY
- template_id: INT NOT NULL FK -> budget.transaction_templates(id)
- user_id: INT NOT NULL FK -> auth.users(id)
- year: INT NOT NULL
- month: INT NOT NULL (1-12)
- amount: NUMERIC(10,2) NOT NULL
- period_start_date: DATE             -- start of the billing/consumption period
- period_end_date: DATE               -- end of the billing/consumption period
- due_date: DATE                      -- when the bill was/is due
- created_at: TIMESTAMPTZ DEFAULT NOW()
- updated_at: TIMESTAMPTZ DEFAULT NOW()
- UNIQUE(template_id, year, month)
```

**Indexing by consumption period:** The `year` and `month` columns represent the consumption
period, not the due date. The consumption month is derived from the midpoint of
`period_start_date` and `period_end_date`. For example, a bill due April 14 covering
February 18 through March 18 has a midpoint of approximately March 4, so it is indexed as
year=2026, month=3. This ensures the seasonal curve reflects actual usage patterns (e.g.,
summer electricity consumption maps to summer months, not the billing month that follows).

Both `period_start_date`/`period_end_date` and `due_date` are stored for future-proofing but
are optional for historical data entry. If only the amount and due date are entered, the app
falls back to indexing by due date month.

New columns on `budget.transaction_templates`:

```
- is_seasonal: BOOLEAN DEFAULT FALSE
- seasonal_method: VARCHAR(20) DEFAULT 'weighted_trend'
    CHECK (seasonal_method IN ('weighted_trend', 'weighted_average'))
```

#### 6.1.3 Historical Data Entry

- A "Seasonal History" tab or section on the transaction template edit page, visible only when
  `is_seasonal` is checked.
- The form displays a grid: rows are years, columns are months (Jan-Dec). The user enters
  dollar amounts per cell. Each cell can optionally expand to enter billing period dates
  (period start, period end, due date). If billing period dates are provided, the app
  calculates the consumption month automatically and places the amount in the correct column.
- Manual entry only. No CSV import. This is intentional given the small number of templates
  that will use this feature (approximately 3-5).
- The form should support entering partial years (e.g., only the months you have data for).

#### 6.1.4 Forecasting Engine

New service: `services/seasonal_forecast.py`

- Pure function: given a list of (year, month, amount) historical data points (indexed by
  consumption period) and a target (year, month), return a projected amount.
- **Primary method -- weighted trend:**
  1. For the target month, collect all historical amounts across years.
  2. Apply exponential decay weighting (most recent year gets the highest weight, older years
     decay). Suggested default decay factor: 0.7 (configurable in user_settings if needed
     later).
  3. Calculate a weighted average as the base.
  4. Apply a linear trend adjustment: fit a simple linear regression to the year-over-year
     values for that month and extrapolate the slope forward.
  5. The final forecast = weighted_average + (trend_slope \* years_forward).
- **Fallback method -- weighted average only:** Same as above but without the trend adjustment.
  Used when there are fewer than 3 data points for a given month (not enough to detect a
  reliable trend).
- **Minimum data requirement:** At least 1 year of data for the target month. If no data
  exists for a given month, fall back to the template's base amount.
- **Course correction:** As actuals are recorded (transaction marked as "Paid" with an actual
  amount), the actual is automatically added to the seasonal history table for that month/year.
  Future forecasts for the same month in subsequent years will incorporate this new data point.

#### 6.1.5 Integration with Recurrence Engine

- When the recurrence engine generates a transaction for a seasonal template, it calls the
  forecasting engine to get the projected amount for that transaction's target month.
- The recurrence engine already handles monthly placement by due date. The seasonal forecast
  only supplies the amount; placement logic is unchanged.
- The generated transaction should display an indicator (icon or label) showing that the amount
  is a seasonal forecast, not a flat recurring amount.

#### 6.1.6 Applicable Templates

Expected to apply to approximately 3-5 expense templates: electricity, gas, water, and
possibly 1-2 others (e.g., lawn care, heating oil). The feature is opt-in per template via the
`is_seasonal` flag.

### 6.2 Smart Estimates (Rolling Average)

For non-seasonal variable expenses (groceries, fuel, dining out), suggest a future estimate
based on a rolling average of recent actuals.

#### 6.2.1 Concept

- When the user views a future transaction for a variable expense template, the app calculates
  the average of the last N actuals (suggested default: N = 6, configurable).
- The suggestion is displayed alongside the current estimate but is never auto-applied. The
  user must explicitly accept the suggestion to update the template or individual transaction.
- This feature becomes useful only after enough actuals have been recorded. Display the
  suggestion only when at least 3 actuals exist for the template.

#### 6.2.2 Implementation

- New service: `services/smart_estimate.py`
- Pure function: given a template_id and a count N, query the last N transactions for that
  template where status is "paid" and an actual amount exists. Return the average.
- UI: A small "suggested: $X.XX" label next to the amount field on future transactions for
  eligible templates. Clicking it populates the amount field.

### 6.3 Expense Inflation

- Per-template inflation settings: opt-in per expense template with a global default rate
  (stored in `auth.user_settings.default_inflation_rate`, already exists) and per-template
  override.
- When enabled, the recurrence engine multiplies the base amount by
  `(1 + annual_rate) ^ (years_from_start)` for each future period.
- Inflated amounts display with an indicator so the user knows the number includes an inflation
  adjustment.
- The user can override any individual period's amount, which locks it.
- **Interaction with seasonal forecasting:** If a template is both seasonal and has inflation
  enabled, the seasonal forecast already incorporates trend (which captures inflation
  implicitly). In this case, the explicit inflation adjustment should be disabled or the user
  warned about double-counting. Recommended: seasonal templates should not also use explicit
  inflation. The trend component of the seasonal forecast serves this purpose.

### 6.4 Deduction Inflation

- Applied at open enrollment time (user-specified month, likely November or December for a
  January effective date).
- Each paycheck deduction can have an optional annual inflation rate.
- At the open enrollment month, deduction amounts for the next year are recalculated:
  `new_amount = current_amount * (1 + deduction_inflation_rate)`.
- The user is prompted to review and confirm adjusted amounts rather than having them
  auto-applied.

### 6.5 Budget Variance Analysis

- **Problem:** The app records estimated amounts and actual amounts for every transaction but
  does not analyze the gap between them. Over time, this estimate-vs-actual data reveals which
  expenses the user consistently underestimates or overestimates, but the user has no way to
  see these patterns.
- **Feature:** A variance analysis view (section on the Charts page or a dedicated page) that
  computes per-template and per-category variance metrics using paid transactions with actual
  amounts. Metrics include: average variance (actual - estimated), variance percentage, and
  direction (consistently over or under). Templates with the largest consistent variances are
  highlighted as candidates for estimate adjustment.
- **Relationship to 6.2:** Smart Estimates (6.2) answers "what should the estimate be?" based
  on a rolling average of actuals. Variance analysis answers "how wrong have your estimates
  been?" Both features consume the same data (paid transactions with actual amounts). The
  variance analysis can reference the smart estimate suggestion: "Your grocery estimate is
  $400, your average actual is $452 (+13%). Smart estimate suggests $448."
- **Implementation:** A service function that queries paid transactions grouped by template,
  computes the variance statistics, and returns a sorted list. The route renders the results.
  No new data model -- this is a computed view over existing data.
- **Display layer:** The computation engine (service function) is built in this section. The
  final visualization and page layout are built in Section 8 (Visualization and Reporting
  Overhaul).

### 6.6 Annual Expense Calendar

- **Problem:** The budget grid organizes data by biweekly pay periods, which is ideal for the
  payday reconciliation workflow but makes it difficult to see the full-year picture. Large or
  irregular expenses (annual insurance premiums, property tax, quarterly bills, annual
  subscriptions) are hard to spot in a grid that scrolls horizontally across 26 pay periods.
- **Feature:** A 12-month calendar view showing when significant expenses hit. Each month cell
  shows: total projected expenses, highlighted markers for large or irregular items, and the
  pay periods that fall within that month (including 3rd-paycheck months, which occur twice per
  year with biweekly pay).
- **Implementation:** The recurrence engine already knows the schedule for every template. The
  calendar queries generated transactions for the next 12 months, groups by calendar month, and
  renders a grid. Large expenses (above a configurable threshold or flagged as irregular) get
  visual emphasis. Months containing a 3rd paycheck are highlighted as opportunities for extra
  debt payments or savings contributions.
- **Display:** A standalone page or a tab on the Charts page. The calendar should be simple --
  a 12-column (or 4x3) grid with month summaries, not a full day-by-day calendar. Clicking a
  month could expand to show the individual transactions for that month.
- **Display layer:** The data aggregation engine is built in this section. The final calendar
  rendering and page layout are built in Section 8 (Visualization and Reporting Overhaul).

### 6.7 Spending Trend Detection

- **Problem:** Gradual increases in spending categories go unnoticed because the user focuses
  on individual transactions, not category-level trends. Lifestyle inflation (slowly spending
  more on dining, entertainment, subscriptions) is invisible in the day-to-day view.
- **Feature:** A trend detection service that computes per-category spending totals over rolling
  windows (e.g., 3-month and 6-month periods) and flags categories where spending has increased
  by more than a configurable threshold (default: 10% over 6 months).
- **Implementation:** A service function that queries paid transactions grouped by category and
  rolling time window, computes the trend slope (same simple linear regression used in the
  seasonal forecast engine's trend component), and returns flagged categories with their trend
  direction and magnitude. The results display as a section on the Charts page or the variance
  analysis view (6.5).
- **Integration with notifications (Phase 7):** If the notification system is in place, trend
  alerts can be delivered as notifications rather than requiring the user to visit a specific
  page. This pairs with the daily scheduled check already planned in 7.1.3.
- **Display layer:** The trend detection engine (service function) is built in this section.
  The final visualization and page layout are built in Section 8 (Visualization and Reporting
  Overhaul).

### 6.8 Third Paycheck Suggestions

- **Problem:** Biweekly pay results in two months per year containing a third paycheck. The
  Section 8 calendar service detects these months (`is_third_paycheck_month` flag on the year
  overview), but the detection is passive -- the user sees a badge but receives no actionable
  guidance on how to use the extra funds.
- **Feature:** An actionable third paycheck card on the dashboard and enhanced calendar badge.
  When the next third paycheck is within 60 days, the dashboard displays a card showing the
  net amount of the extra check alongside two contextual facts: emergency fund status (current
  balance vs. goal target, if an emergency fund savings goal exists and is not yet met) and
  highest-rate debt (account name, rate, remaining balance, if active debt accounts exist).
  These facts give the user context for their decision without recommending one option over
  the other.
- **Action button:** A "Create Transfer" button opens the existing one-time transfer creation
  form (`/transfers/new`) with the amount pre-filled to the net paycheck amount via query
  parameter. The user selects the destination account and confirms. The transfer form route
  needs a small modification to accept an optional `amount` query parameter as a form default.
- **Calendar integration:** The existing "3rd check" badge on the year overview calendar gains
  a tooltip or popover showing the same net amount and contextual facts. No action button on
  the calendar -- the dashboard is the action surface.
- **Implementation:** No new service file. The dashboard service (Section 8) orchestrates the
  lookup using existing data: net biweekly pay from the salary service, emergency fund goal
  from the savings dashboard service, highest-rate debt from the debt summary service
  (`_compute_debt_summary`), and third paycheck dates from the calendar service's
  `_detect_third_paycheck_months()`. A helper function within the dashboard service assembles
  the card data.
- **Future enhancement -- priority engine (not in Phase 9):** A future mini-phase could add a
  priority engine that recommends the optimal destination for surplus funds. This would
  require new `auth.user_settings` columns (surplus priority mode, savings floor months),
  committed debt strategy selection (currently 5.11 is a what-if simulation, not a stored
  preference), and orchestration logic. Noted here for future scoping but explicitly out of
  scope for Phase 9.
- **Dependency:** Requires Section 8 Commits 3 and 6 (calendar service with third paycheck
  detection, dashboard service). Benefits from 5.12 (debt summary) and 5.15 (savings goal
  trajectory).

### 6.9 Estimate Confidence Indicator

- **Problem:** Every future transaction in the grid displays an estimated amount, but the
  reliability of that estimate varies dramatically depending on its source. A seasonal
  forecast backed by 4 years of history is far more reliable than a flat amount the user
  entered once during initial setup. The user has no visual signal for which estimates to
  trust and which to review.
- **Feature:** A three-tier confidence indicator displayed on future transactions in the grid
  and transaction detail views.
- **Confidence tiers:**
  1. **High confidence (green):** Seasonal forecast (6.1) with 3+ years of data for the
     target month, OR rolling average (6.2) with 6+ actuals.
  2. **Medium confidence (yellow):** Seasonal forecast with 1-2 years of data, OR rolling
     average with 3-5 actuals, OR inflation adjustment (6.3) applied.
  3. **Low confidence (gray):** Template's static base amount with no supporting actuals, no
     seasonal history, and no inflation adjustment.
  If multiple sources apply, the highest-confidence source wins.
- **Display:** A small color-coded icon (dot or badge) on each future transaction cell in the
  grid. Tooltip on hover shows the source: "Seasonal forecast (4 years of data)" or "Rolling
  average of last 6 actuals" or "Static estimate -- no actuals recorded." Same indicator with
  a one-line explanation in the transaction quick edit / detail view. No indicator on past
  transactions with actual amounts.
- **Implementation:** New service: `services/estimate_confidence.py`. Pure function that takes
  a template_id and target (year, month) and returns a confidence tier and source description.
  Checks: is the template seasonal? Count seasonal_history records. Does the template have
  actuals? Count paid transactions. Does the template have inflation enabled? Apply tier
  rules. Supports a batch mode (list of template_id/year/month tuples, two bulk queries,
  evaluate in memory) to avoid N+1 queries during grid rendering.
- **No new data model.** Computed from existing data: `seasonal_history` rows (6.1), paid
  transaction counts, and template flags.
- **Dependency:** Requires 6.1 (seasonal history table, `is_seasonal` flag) and 6.2 (rolling
  average service for actual count lookup). Benefits from 6.3 (inflation flag adds a
  medium-confidence signal).

### 6.10 Bill Due Date Optimization

- **Problem:** When expenses cluster unevenly across pay periods, one period may have a
  dangerously low projected end balance while the adjacent period has comfortable surplus.
  The user has no visibility into which expenses could be shifted to balance cash flow.
- **Feature:** An advisory analysis that detects pay period imbalance, identifies moveable
  expenses, simulates the balance impact of shifting them, and recommends an action path.
  The app advises but does not automate -- the user decides how to act (change the due date,
  use the Credit/Credit Payback feature, or transfer from savings).

#### 6.10.1 Data Model

New column on `budget.transaction_templates`:

```
- is_due_date_flexible: BOOLEAN DEFAULT FALSE
```

This flag indicates whether the template's due date can realistically be moved. Fixed
obligations (mortgage, loan payments, rent) are `FALSE`. Discretionary or provider-adjustable
expenses (groceries, subscriptions, some utilities) can be marked `TRUE` by the user on the
template edit form.

#### 6.10.2 Analysis Engine

New service: `services/due_date_optimizer.py`

- **Input:** A date range (typically the next 2-4 pay periods) and the user's active
  transaction templates.
- **Step 1 -- Detect imbalance:** For each pay period in the range, compute total committed
  outflows and projected end balance. Identify pairs of adjacent periods where the balance
  difference exceeds a threshold (any period's projected end balance drops below the user's
  warning threshold, or balance difference between adjacent periods exceeds 40% of net pay).
- **Step 2 -- Identify moveable expenses:** From the lower-balance period, collect generated
  transactions whose template has `is_due_date_flexible=TRUE`. Sort by amount descending.
- **Step 3 -- Simulate moves:** For each moveable expense, calculate the effect of shifting it
  to the adjacent higher-balance period. Report: "Moving [expense_name] ($[amount]) from
  period [date] to period [date] would raise your low-period end balance from $[old] to
  $[new]."
- **Step 4 -- Recommend action path:** For each suggested move:
  1. If the expense due date falls after the next paycheck date: "This bill can be
     rescheduled -- change the due date to [suggested_date] or later."
  2. If the expense due date falls before the next paycheck (must be paid before money
     arrives): "This bill is due before your next paycheck. Options: pay with a credit card
     (use the Credit/Credit Payback feature), or transfer from savings to cover the
     $[shortfall] gap."
  3. Compute the exact shortfall amount for bridge funding scenarios.
- **Output:** A list of suggestion objects: template name, amount, source period, target
  period, old end balance, new end balance, action path (reschedule / credit card / savings
  transfer), and shortfall amount.
- **Pure function:** Receives pre-loaded period and transaction data. Does not query the
  database.

#### 6.10.3 Display

- **Dashboard integration:** When the optimizer detects an actionable imbalance in the next 2
  pay periods, show a brief alert in the dashboard's "Alerts / Needs Attention" section:
  "Cash flow imbalance detected in [period_date] -- [count] suggestion(s) available." Links
  to the full analysis.
- **Full analysis view:** A section on the analytics page or a dedicated page. Table showing
  each suggestion with template name, amount, source/target periods, balance improvement, and
  recommended action. All suggestions are informational -- no action buttons that automate
  the move.

#### 6.10.4 Dependency

- **Requires:** Section 8 Commit 6 (dashboard service for alert integration), existing
  balance calculator for projected end balances.
- **No dependency on 6.1-6.4.** Works with whatever amounts are in the generated
  transactions.
- **Benefits from:** Section 7 (notifications) for delivering imbalance alerts.

### 6.11 Year-over-Year Seasonal Comparison

- **Problem:** Seasonal forecasts are opaque. The user sees a forecasted amount for a future
  seasonal bill but has no easy way to verify whether it feels right without manually looking
  up last year's actual.
- **Feature:** When viewing a future transaction for a seasonal template, show the prior
  year's actual alongside this year's forecast. Example: "Jul 2025 actual: $187.32 --
  Forecast: $195.00 (+4.1%)." If no prior year actual exists, show: "No prior year data for
  [month]."
- **Display locations:**
  - **Grid transaction cell:** Tooltip or expanded detail view shows the comparison.
  - **Transaction quick edit / detail:** Small info line below the amount field.
  - **Seasonal history entry form (6.1.3):** Highlight cells that already have prior year data
    and show the year-over-year change as the user enters new values. Catches data entry
    errors (e.g., $1,870 instead of $187).
- **Implementation:** Add a function to `services/seasonal_forecast.py` (created in 6.1.4):
  `get_prior_year_comparison(template_id, target_year, target_month)`. Returns a dict:
  `{prior_year, prior_amount, forecast_amount, change_pct}` or `None` if no prior year data.
  Supports batch lookup (list of template_id/year/month tuples, one bulk query) for grid
  rendering.
- **No new data model.** Reads from the `seasonal_history` table created by 6.1.
- **Dependency:** Requires 6.1 (seasonal history table and forecasting engine). Should be
  implemented after 6.1 is complete and the user has entered at least one year of seasonal
  history.

### 6.12 Expense Anomaly Detection

- **Problem:** When a recurring bill comes in significantly different from its expected amount,
  the user may not notice until they've already confirmed the transaction. Billing errors,
  forgotten add-ons, rate changes, and data entry typos go undetected at the point of entry.
- **Feature:** When the user records an actual amount for a recurring transaction (via
  mark-as-paid), the app compares the actual to the expected amount and flags significant
  deviations inline before the transaction is confirmed.

#### 6.12.1 Expected Amount Resolution

The comparison uses the most specific estimate source available, checked in priority order:

1. **Seasonal forecast (6.1):** If the template has `is_seasonal=True` and a forecast is
   available for the transaction's target month.
2. **Rolling average (6.2):** If the template has 3+ paid actuals.
3. **Template base amount:** The template's static `amount` field.

#### 6.12.2 Anomaly Threshold

An actual amount is flagged as anomalous when it deviates from the expected amount by more
than a configurable percentage threshold.

- **New column:** `auth.user_settings.anomaly_threshold_pct` (NUMERIC(5,2), DEFAULT 20.00).
- Global threshold (all templates). Per-template override deferred to a future enhancement.
- Both over and under deviations are flagged.

#### 6.12.3 Detection Timing and Display

- **Primary trigger -- mark-as-paid flow:** When the user enters an actual amount during
  mark-as-paid (grid or dashboard), the anomaly check runs before the transaction is
  confirmed. If anomalous, an inline warning is displayed: "This amount ($[actual]) is [X]%
  [higher/lower] than expected ($[expected] from [source_label]). Confirm or correct."
  Source label identifies the estimate source: "seasonal forecast", "rolling average", or
  "template amount."
- **Two-step confirmation:** When an anomaly is detected, the response renders a confirmation
  form with the warning and two buttons: "Confirm $[actual]" and "Edit Amount." The "Confirm"
  button submits with a `force=true` parameter that bypasses the anomaly check. This prevents
  accidental confirmation of typos while not blocking legitimate unusual amounts.
- **Dashboard mark-as-paid:** Same anomaly check and two-step confirmation applies to the
  dashboard's mark-as-paid flow (Section 8.1).

#### 6.12.4 Implementation

- **New service:** `services/anomaly_detection.py`
- **Primary function:** `check_anomaly(template_id, actual_amount, target_year, target_month,
  threshold_pct)` -- returns an `AnomalyResult` with fields: `is_anomalous` (bool),
  `expected_amount` (Decimal), `source` (str), `deviation_pct` (float), `direction` (str:
  'over' or 'under').
- **Resolution logic:** Checks sources in priority order (6.12.1). Calls
  `seasonal_forecast.get_forecast()` if seasonal, then `smart_estimate.get_rolling_average()`
  if actuals exist, then falls back to template base amount.
- **Integration:** The transaction route's mark-done handler calls `check_anomaly()` when
  `actual_amount` is provided. If anomalous and `force` is not set, the response includes the
  warning partial. If `force=true`, the transaction is committed normally.
- **Shared utility:** Tasks 6.9 and 6.12 both resolve "best estimate source for this
  template" in the same priority order. A shared `_resolve_best_estimate(template_id, year,
  month)` utility function avoids duplicating this logic.
- **No retroactive review screen.** Budget variance analysis (6.5) already provides
  retroactive visibility into estimate-vs-actual gaps.

#### 6.12.5 Interaction with Other Features

- **6.1 course correction:** When a seasonal transaction is marked paid, 6.1.4 writes the
  actual to seasonal_history. Anomaly detection runs before this write. If the user confirms
  an anomalous amount, it still gets recorded -- the actual is real data regardless.
- **6.2 smart estimates:** An anomalous-but-confirmed actual shifts the rolling average. Over
  time, if the new amount becomes the norm, future occurrences stop being flagged.
- **6.9 estimate confidence:** The anomaly service can reuse the confidence service's source
  resolution logic (or both use the shared utility).

#### 6.12.6 Dependency

- **Requires:** 6.1 (seasonal forecast as comparison source) and 6.2 (rolling average as
  comparison source). Can function with only the template base amount if 6.1 and 6.2 are not
  yet implemented, but value is significantly reduced.
- **Benefits from:** 6.9 (shared estimate source resolution logic).
- **Requires:** Section 8 Commit 7 (dashboard mark-as-paid flow) for dashboard integration.
  Grid integration depends only on the existing mark-as-paid endpoint.

---

## 7. Phase 10: Notifications (Priority 6)

**Goal:** Alert the user to important financial events without requiring them to manually scan
the grid or dashboard. Provide 15 notification types across 6 logical groups, delivered in-app
via a bell icon dropdown and a full `/notifications` page. Email delivery is added as a
deferred sub-phase once the self-hosted mail server is operational. All notification types
default to in-app enabled and email disabled. Users manage preferences through a grouped
settings UI with expandable sections at `/settings/notifications`.

### 7.1 Notification Infrastructure

#### 7.1.1 Data Model

The `system.notifications` and `system.notification_settings` tables are already stubbed in the
schema. The implementation should include:

```
system.notification_settings
- id: SERIAL PRIMARY KEY
- user_id: INT NOT NULL FK -> auth.users(id)
- notification_type: VARCHAR(50) NOT NULL
- is_enabled: BOOLEAN DEFAULT TRUE
- delivery_in_app: BOOLEAN DEFAULT TRUE
- delivery_email: BOOLEAN DEFAULT FALSE
- threshold_warning: NUMERIC(12,2)         -- low balance warning threshold (per account)
- threshold_critical: NUMERIC(12,2)        -- low balance critical threshold (per account)
- lookahead_periods: INT DEFAULT 6         -- how many future periods to check for low balance
                                           -- (default 6 = ~3 months biweekly)
- days_before: INT                         -- for upcoming expense / ARM rate reminders
- large_expense_amount: NUMERIC(12,2)      -- flat dollar threshold for large expense alerts
- large_expense_pct: NUMERIC(5,2)          -- percentage-of-net-paycheck threshold for large
                                           --   expense alerts (either or both may be set)
- missed_payment_escalation_days: INT DEFAULT 3
                                           -- days after due date before warning -> critical
- period_aging_warning_days: INT DEFAULT 3 -- days past period end before warning fires
- period_aging_critical_days: INT DEFAULT 7
                                           -- days past period end before critical fires
- template_change_threshold_pct: NUMERIC(5,2) DEFAULT 15.00
                                           -- rolling avg vs. template amount deviation
                                           --   threshold for template change detection
- created_at: TIMESTAMPTZ DEFAULT NOW()
- updated_at: TIMESTAMPTZ DEFAULT NOW()
- UNIQUE(user_id, notification_type)

system.notifications
- id: SERIAL PRIMARY KEY
- user_id: INT NOT NULL FK -> auth.users(id)
- notification_type: VARCHAR(50) NOT NULL
- severity: VARCHAR(10) NOT NULL CHECK (severity IN ('info', 'warning', 'critical'))
- title: VARCHAR(200) NOT NULL
- message: TEXT NOT NULL
- is_read: BOOLEAN DEFAULT FALSE
- is_pinned: BOOLEAN DEFAULT FALSE         -- for weekly digest pinned in dropdown
- related_entity_type: VARCHAR(50)         -- 'account', 'transaction', 'pay_period', etc.
- related_entity_id: INT
- related_entity_url: VARCHAR(500)         -- pre-computed link to relevant page
- snoozed_until: TIMESTAMPTZ              -- null = not snoozed; notification hidden until
                                           --   this timestamp
- resolved_at: TIMESTAMPTZ                -- null = unresolved; set when underlying condition
                                           --   clears (auto-resolve) or user dismisses
- created_at: TIMESTAMPTZ DEFAULT NOW()
- read_at: TIMESTAMPTZ

system.notification_email_preferences
- id: SERIAL PRIMARY KEY
- user_id: INT NOT NULL FK -> auth.users(id) UNIQUE
- preferred_delivery_time: TIME DEFAULT '07:00'
                                           -- user's preferred time for email delivery
- delivery_window_start: TIME DEFAULT '07:00'
                                           -- emails generated outside this window are held
- delivery_window_end: TIME DEFAULT '21:00'
                                           -- until the window opens
- created_at: TIMESTAMPTZ DEFAULT NOW()
- updated_at: TIMESTAMPTZ DEFAULT NOW()
```

#### 7.1.2 Notification Types

Organized into 6 groups matching the settings UI layout.

**Group 1: Balance & Cash Flow**

1. **Low projected balance (warning and critical):** Triggered when the projected end balance
   for any future pay period within the configured lookahead window drops below the configured
   threshold. Warning and critical thresholds are configurable per account. Severity is
   determined by which threshold is breached. The lookahead window defaults to 6 periods (~3
   months biweekly) and is configurable via `lookahead_periods`. The app calculates out to 52
   periods by default, but generating low-balance notifications for periods 6+ months out
   creates noise the user cannot act on.
2. **Balance recovery:** Triggered when a prior low-balance warning's underlying condition
   clears (the projected balance for that period recovers above the threshold). The original
   warning notification is auto-resolved (`resolved_at` set) and a new info-severity
   notification is created: "Your checking balance for [period_date] has recovered from
   -$[old_balance] to $[new_balance]. The low balance warning has been resolved." Provides
   confirmation that the user's corrective action worked.
3. **Pre-payday cash flow summary:** Triggered 2 days before each payday. Generates a single
   notification summarizing the upcoming period: bill count, total bill amount, and projected
   end balance. Example: "Next period (Apr 11--24): 6 bills totaling $1,847. Projected end
   balance: $423." Fires even when nothing is wrong -- proactive awareness, not just alerts.
   Implementation: one query against generated transactions for the next period, formatted
   into a single notification. Runs during the daily scheduled check.

**Group 2: Bills & Payments**

4. **Upcoming large expense:** Triggered N days before a large expense is due (`days_before`
   configurable, default: 7). "Large" is configurable as a flat dollar threshold
   (`large_expense_amount`), a percentage of net paycheck (`large_expense_pct`), or both. When
   both are set, an expense triggers the notification if it exceeds either threshold. Gives the
   user time to ensure funds are available.
5. **Missed payment detection:** Triggered when a recurring transaction passes its due date and
   remains in Projected status (not marked Paid). Catches genuinely missed payments and
   transactions the user forgot to reconcile. Detection runs during the daily scheduled check
   (7.1.3). Severity: warning initially, escalates to critical after a configurable number of
   days (`missed_payment_escalation_days`, default: 3). Auto-resolved when the transaction is
   marked Paid.
6. **Unreconciled period aging:** Triggered when a pay period's end date has passed and the
   period has not been reconciled (no anchor balance set). Warning fires after
   `period_aging_warning_days` (default: 3) past the period end date. Escalates to critical
   after `period_aging_critical_days` (default: 7). Message includes the downstream impact:
   "Your [period_date] period ended [N] days ago and hasn't been reconciled. Projected
   balances for all future periods may be inaccurate." Auto-resolved when the period is
   reconciled (anchor balance set). The Section 8 dashboard already computes this as an
   ephemeral alert -- this notification persists it and adds escalation.

**Group 3: Savings & Goals**

7. **Savings milestone reached:** Triggered when a savings goal reaches a milestone percentage
   (25%, 50%, 75%, 100%). Informational and motivational. The 100% milestone receives distinct
   celebratory treatment in the UI (different icon or color, congratulatory message) to
   differentiate it from routine progress notifications.
8. **Savings goal pace alert:** Triggered when a savings goal with a target date is falling
   behind the required savings rate. Depends on Section 5 task 5.15 (savings goal trajectory).
   Includes the corrective action: "At your current rate, you'll miss your Emergency Fund goal
   by $2,400. Increase monthly contributions from $500 to $650 to reach your target by
   December 2027." Links to the savings account dashboard.
9. **Savings contribution reminder:** Triggered at the start of each month if no transfer to a
   goal-linked savings account has been recorded for that month. Message: "No contribution to
   your [goal_name] this month. You need $[required_monthly]/month to stay on pace for your
   [target_date] target." Different from the pace alert (#8), which fires after you've fallen
   behind -- this fires before you fall behind, when there's still time to act. Depends on
   Section 5 task 5.15 (savings goal trajectory). Runs during the daily scheduled check.

**Group 4: Debt**

10. **Debt payoff milestone:** Triggered when a debt account's balance crosses a round-number
    threshold (e.g., drops below $10,000) or when the projected payoff date moves ahead of
    schedule. Example: "Your auto loan balance dropped below $10,000!" or "At your current
    payment rate, your student loan pays off in October 2028 -- 2 months ahead of schedule."
    Motivational. Uses data already computed by the debt payoff projection services from
    Section 5 (tasks 5.1, 5.5). Runs during the daily scheduled check.
11. **ARM rate adjustment reminder:** Triggered N days before an ARM loan's next scheduled rate
    adjustment date (`days_before` configurable, default: 30). The adjustment date is
    calculated from the loan's origination date, `arm_first_adjustment_months`, and
    `arm_adjustment_interval_months`. Reminds the user to watch for their lender's rate change
    notice and update the rate history in the app. Depends on Section 5 task 5.7 (ARM rate
    support). Runs during the daily scheduled check.

**Group 5: Templates & Trends**

12. **Recurring template change detection:** Triggered when the rolling average of a recurring
    transaction's actual amounts has diverged from the template's base amount by more than a
    configurable threshold (`template_change_threshold_pct`, default: 15%). Example: "Your
    T-Mobile bill has averaged $95.20 over the last 3 months, but the template amount is
    $85.00 (+12%). Update the template?" Links to the template edit page. Bridges the gap
    between anomaly detection (6.12, which catches one-off spikes) and the user forgetting to
    update a template after a legitimate rate change. Depends on Phase 9 task 6.2 (rolling
    average engine). Runs during the daily scheduled check.

**Group 6: Digests**

13. **Weekly summary:** Generated once per week (day configurable, default: Monday). In-app:
    rendered as a pinned notification at the top of the notification dropdown. Email: sent as
    an HTML digest. Content includes: upcoming bills for the week, savings goal progress
    summary, any unresolved alerts from the past week, and net worth change (if net worth is
    computed elsewhere; omit this section if not available). The weekly digest is an
    all-or-nothing toggle -- the user cannot select individual sections. Auto-resolved after
    7 days (replaced by the next week's digest).
14. **Payday reconciliation reminder:** Email only -- not shown in-app (the user is already in
    the app if they can see notifications). Triggered on payday or the day after as a reminder
    to open the app and reconcile the current period. Runs during the daily scheduled check.
    Delivered immediately (not batched).

Future notification types (not in initial build):

- Retirement goal milestones
- Contribution limit warnings (approaching annual 401(k)/IRA limit)
- Upcoming rate/term change awareness (promotional APR expiry, student loan deferment end,
  escrow analysis adjustment) -- depends on whether account parameter architecture stores
  future effective dates for rate changes

#### 7.1.3 Persist Dashboard Alerts as Notifications

The Section 8 dashboard computes three alert types ephemerally (stale anchors, negative
projected balances, overdue reconciliation). When the notification system is implemented,
these alerts should additionally write to `system.notifications` so they persist across page
navigations and accumulate history. The dashboard continues to compute and display alerts
directly (no dependency on the notification system for rendering), but the notification system
picks up the same conditions during its scheduled check and persists them.

Mapping:
- Stale anchor alert -> no dedicated notification type; covered by unreconciled period aging
  (#6) which escalates on a similar timeline. If the stale anchor alert uses a different
  staleness threshold than period aging, reconcile the two thresholds during implementation.
- Negative projected balance alert -> covered by low projected balance (#1). The dashboard
  alert fires for any negative balance; the notification fires based on configurable
  thresholds. A threshold of $0.00 produces equivalent behavior.
- Overdue reconciliation alert -> covered by unreconciled period aging (#6).

#### 7.1.4 Trigger Mechanism

- **On transaction edit:** After any transaction is created, updated, or status-changed, the
  balance roll-forward is recalculated. At this point, check projected balances against
  thresholds for low balance warnings (#1) and check for balance recovery (#2).
- **Scheduled check (daily):** A lightweight scheduled job (cron or APScheduler) runs daily.
  Checks for: pre-payday cash flow summary (#3), upcoming large expenses (#4), missed
  payments (#5), unreconciled period aging (#6), savings contribution reminders (#9), debt
  payoff milestones (#10), ARM rate adjustment reminders (#11), recurring template change
  detection (#12), weekly summary generation (#13, on the configured day), and payday
  reconciliation reminders (#14). This avoids making the transaction edit path heavier than
  necessary.
- **On savings goal update:** When a savings account balance changes (via transfer or anchor
  true-up), check goal progress for milestone notifications (#7) and pace alerts (#8).

#### 7.1.5 Deduplication

- The system should not generate duplicate notifications for the same event. Use a combination
  of notification_type + related_entity_type + related_entity_id + a time window to prevent
  duplicates.
- Example: A low balance warning for checking account ID 1 for pay period ID 47 should only
  be generated once. If the user fixes the issue and the balance recovers, the warning is
  auto-resolved and a balance recovery notification is generated. If the balance drops again
  later, a new warning is generated.
- Weekly summaries replace the prior week's pinned digest (old one is auto-resolved).
- Savings milestones use the milestone percentage as part of the deduplication key (reaching
  50% only fires once per goal, even if the balance fluctuates around the threshold).

#### 7.1.6 Snooze

- Any notification can be snoozed. Snoozing sets `snoozed_until` to a future timestamp.
- Snoozed notifications are hidden from the dropdown and the `/notifications` page until the
  snooze expires.
- Configurable snooze durations presented as options: 1 day, 3 days, 7 days, until next
  payday. "Until next payday" computes the next pay period start date from the pay period
  schedule.
- Snoozing does not prevent auto-resolve. If the underlying condition clears while a
  notification is snoozed, it is still resolved.
- Snoozing does not prevent deduplication. If the same event would generate a new notification
  while the original is snoozed, no duplicate is created.

#### 7.1.7 Auto-Resolve

- Notifications are automatically marked resolved (`resolved_at` set to current timestamp)
  when the underlying condition clears. Resolved notifications are visually distinct in the UI
  (muted styling, strikethrough, or moved to a "resolved" section).
- Auto-resolve conditions by type:
  - Low projected balance (#1): balance recovers above the threshold for that period.
  - Balance recovery (#2): not auto-resolved (informational; ages out naturally).
  - Pre-payday summary (#3): auto-resolved when the period begins (the summary is no longer
    relevant once the period is active).
  - Upcoming large expense (#4): auto-resolved when the transaction is marked Paid.
  - Missed payment (#5): auto-resolved when the transaction is marked Paid.
  - Unreconciled period aging (#6): auto-resolved when the period is reconciled.
  - Savings milestone (#7): not auto-resolved (permanent achievement record).
  - Savings goal pace (#8): auto-resolved when the savings rate recovers to on-pace.
  - Savings contribution reminder (#9): auto-resolved when a transfer to the goal account is
    recorded for the current month.
  - Debt payoff milestone (#10): not auto-resolved (permanent achievement record).
  - ARM rate reminder (#11): auto-resolved when the rate history is updated in the app for
    the current adjustment period.
  - Template change detection (#12): auto-resolved when the template amount is updated.
  - Weekly summary (#13): auto-resolved after 7 days (replaced by the next digest).
  - Payday reconciliation reminder (#14): auto-resolved when the period is reconciled.

### 7.2 In-App Delivery

#### 7.2.1 Notification Bell and Dropdown

- **Notification bell icon** in the app header (Bootstrap navbar). Displays an unread count
  badge. The badge counts only unread, non-snoozed, non-resolved notifications.
- **Dropdown panel** on click: shows recent notifications (last 10), newest first. The weekly
  digest (#13) is pinned to the top of the dropdown when present, regardless of age. Each
  notification shows severity (color-coded), title, timestamp, and a brief message.
- **Actions per notification:** mark as read (click), snooze (with duration picker), dismiss
  (mark resolved manually).
- **Bulk actions:** mark all as read.
- **Link to context:** Notifications with a `related_entity_url` link to the relevant page
  (e.g., a low balance warning links to the pay period in the grid; a savings milestone links
  to the account dashboard; a template change detection links to the template edit page).
- **"View all" link** at the bottom of the dropdown navigates to `/notifications`.

#### 7.2.2 Notifications Page (`/notifications`)

- Full-page view of all notifications.
- **Filters:** by notification type, by severity (info/warning/critical), by date range, by
  status (unread/read/resolved/snoozed).
- **Default view:** shows unresolved, non-snoozed notifications, newest first.
- **Resolved notifications** are accessible via the status filter but hidden by default to
  keep the view clean.
- **Bulk actions:** mark selected as read, snooze selected, dismiss selected.
- **No analytics or trends.** This is a filtered list, not a reporting tool. Historical
  analysis of notification patterns can be done via direct database queries if needed.

### 7.3 Settings UI (`/settings/notifications`)

- Located as a new tab or section in the existing Settings area.
- **Layout: Option B -- grouped settings with expandable sections.** Notification types are
  organized into 6 collapsible cards matching the notification type groups:
  1. Balance & Cash Flow (types #1, #2, #3)
  2. Bills & Payments (types #4, #5, #6)
  3. Savings & Goals (types #7, #8, #9)
  4. Debt (types #10, #11)
  5. Templates & Trends (type #12)
  6. Digests (types #13, #14)
- **Group-level master toggle:** Each group card has a toggle that enables/disables all
  notification types within the group at once. Expanding the card reveals per-type controls.
- **Per-type controls:** Each notification type within a group has:
  - Enabled/disabled toggle
  - Delivery channel checkboxes: In-App, Email
  - Type-specific parameters displayed inline (thresholds, days-before, lookahead window,
    percentages, etc.)
- **Email checkboxes** are greyed out with a tooltip ("Email delivery requires mail server
  configuration") until the mail server is configured. The presence of a configured mail
  server can be indicated by an application setting or environment variable.
- **Defaults for all types:** In-app enabled, email disabled.
- **Defaults for new types added in future releases:** In-app enabled, email disabled. When a
  new notification type is added in a future release, a row is inserted into
  `notification_settings` for each existing user with these defaults.
- **Email delivery preferences** section at the top or bottom of the page (outside the type
  groups): preferred delivery time, delivery window start, delivery window end. Greyed out
  until mail server is configured.

### 7.4 Email Delivery (Deferred Sub-phase)

- **Dependency:** Requires a self-hosted mail server to be operational. This is infrastructure
  work outside the app (OPNsense mail features or a dedicated mail service on the Arch Linux
  host). Research and setup should happen in parallel with the in-app notification build.
- **Implementation:** When a notification is generated and the user has `delivery_email = TRUE`
  for that type, queue an email. Use Python's `smtplib` with the self-hosted SMTP server.
- **Delivery window:** Emails are only sent during the user's configured delivery window
  (`delivery_window_start` to `delivery_window_end`). Emails generated outside this window
  are queued and delivered when the window opens. The preferred delivery time
  (`preferred_delivery_time`) is used for scheduled digests and reminders.
- **Email format:** Simple HTML email with the notification title, message, severity, and a
  link back to the app.
- **Batching:** For notification types that could fire frequently (low balance warnings during
  a heavy editing session), batch emails into a digest rather than sending one per event.
  Batch window: 1 hour. Milestone notifications, payday reconciliation reminders, and the
  weekly digest send immediately (within the delivery window).
- **Payday reconciliation reminder (#14):** Email only. This notification type is not rendered
  in-app. The in-app delivery checkbox is hidden or disabled for this type in the settings UI.
- **Weekly digest (#13):** Sent as an HTML email containing all digest sections. Delivered at
  the user's preferred delivery time on the configured day of week.

---

## 8. Visualization and Reporting Overhaul (Priority 4) -- IN PROGRESS

**Status:** Implementation began April 7, 2026. Supporting documents:
`docs/section8_scope.md`, `docs/implementation_plan_section8.md`.

**Goal:** Overhaul the app's visualization and reporting capabilities. Replace the existing
`/charts` page with two major additions: a summary dashboard (`/` -- becomes the app's landing
page) and an analytics page (`/analytics` -- tabbed container with calendar, year-end summary,
budget variance, and spending trends). Build the computation engines for budget variance
analysis (6.5), annual expense calendar (6.6), and spending trend detection (6.7) alongside
their display layers. Add CSV export for all analytics views. Fix the x-axis date format bug
(8.0a).

This section consolidates visualization and reporting work from multiple sources:
- Chart bug fix from `fixes_improvements.md` production feedback (task 8.0a).
- Chart redesign/overhaul (`fixes_improvements.md`, item 9).
- Summary dashboard, financial calendar, and year-end summary (originally planned as 8.1,
  8.2, 8.3 in roadmap v4.2).
- Computation engines AND display layers for budget variance analysis (6.5), annual expense
  calendar (6.6), and spending trend detection (6.7). These engines were originally scoped as
  Phase 9 computation-only tasks with display layers deferred to this section. The
  implementation plan builds both together.
- CSV export for all analytics views (partial coverage of 8A.1 scope -- analytics-level CSV,
  not full transaction-level export).

**Prerequisite:** Sections 5 and 5A complete. Section 6 tasks 6.1-6.4 are NOT prerequisites.
Section 7 (Notifications) is not required; the dashboard alerts section computes alerts
directly from existing data without the notification system.

**Scope exclusion:** Task 8.0b (inaccurate balance values) is excluded per the scope document.
The code audit found no obvious data pipeline bug in `chart_data_service.py`. If balance value
inaccuracies are observed during implementation, they will be addressed as part of the
relevant commit.

### 8.0a Chart Bug Fix: X-Axis Date Format

- **Source:** fixes_improvements.md (Charts, item 1)
- **Problem:** The Balance Over Time chart on the `/charts` page shows only month and day on
  the x-axis without the year. The user has no context for the actual length of time being
  displayed, especially when the chart spans multiple years.
- **Fix:** Update the chart's x-axis date formatting to include the year. Use a format that
  balances readability with space constraints (e.g., "Mar '26" or "03/2026"). For charts
  spanning less than 12 months, month/day may be sufficient with the year shown at year
  boundaries. For charts spanning multiple years, the year must be visible on enough tick
  marks to provide context.
- **Scope:** Chart.js configuration in `chart_data_service.py` or the chart template's
  JavaScript. No backend logic changes.

### 8.0b Chart Bug Fix: Inaccurate Balance Values

- **Source:** fixes_improvements.md (Charts, item 2)
- **Problem:** Some account balances on the charts page show zero or a static number even
  though the account detail page shows a changing balance. The chart data assembly is not
  correctly pulling or computing balance data for all account types.
- **Fix:** Investigate and fix the data pipeline in `chart_data_service.py` (720 lines). The
  root cause likely involves one or more of: accounts excluded from the chart query due to
  missing type dispatch, balance values not being recalculated for chart time points, or
  stale/cached data being used. The fix must ensure that every account type with a meaningful
  balance trajectory appears correctly on the charts page.
- **Scope:** `chart_data_service.py` and potentially the chart route. Requires investigation
  before the fix can be fully specified.
- **Dependency:** This fix should be done before any chart redesign work so that the redesign
  builds on correct data.

### 8.1 Summary Dashboard / Home Page

- **Problem:** The budget grid is currently the home page. While the grid is the right view for
  the payday reconciliation workflow, it is dense and detail-oriented. A user who opens the app
  to check their general financial position must scan the grid to extract high-level
  information.
- **Feature:** A summary dashboard that serves as the landing page, providing a single-glance
  overview of the user's financial position. The budget grid remains one click away for the
  detailed reconciliation workflow.
- **Dashboard contents:**
  - Current checking balance (from the most recent anchor or calculated balance).
  - Days until next paycheck and next paycheck net amount.
  - Upcoming bills in the next 7-14 days (from projected transactions).
  - Active alerts/notifications (from Phase 7 notification system, if implemented; otherwise
    a placeholder section).
  - Savings goal progress summary (progress bars for each active goal).
  - Debt summary snapshot (total debt, DTI ratio -- from Section 5 task 5.12 if implemented).
  - Quick-action buttons: "Reconcile Now" (goes to current pay period in the grid), "View
    Accounts" (goes to accounts dashboard).
- **Implementation:** A new route and template. All data is already computed by existing
  services (balance calculator, paycheck calculator, recurrence engine, savings goal service).
  The dashboard is a read-only aggregation view. No new services or data model needed.

### 8.2 Financial Calendar View

- **Problem:** The budget grid organizes data by biweekly pay periods. A traditional calendar
  view organized by month is a more intuitive format for planning around irregular expenses,
  identifying cash-tight months, and coordinating with external calendars (school year, tax
  deadlines, open enrollment).
- **Feature:** A month-view calendar showing: paycheck dates (highlighted), bill due dates
  (marked with amount), transfer dates, and projected checking balance at key dates. The
  calendar complements the grid -- the grid is for reconciliation, the calendar is for
  planning.
- **Display:** A standalone page or a tab on the Charts page. The calendar shows one month at
  a time with forward/back navigation. Each day cell shows transactions due on that date with
  their amounts. Days with paycheck income are visually highlighted. The running projected
  balance can be shown as a line or as end-of-day values on key dates.
- **Implementation:** The recurrence engine and balance calculator already compute the
  underlying data. The work is primarily template rendering -- transform the per-pay-period
  data into a per-day calendar layout. A month contains 1-3 pay periods depending on timing.

### 8.3 Year-End Financial Summary

- **Problem:** At the end of each calendar year (or during tax preparation), the user has no
  consolidated view of their annual financial activity. Tax-relevant figures (total income,
  total pre-tax deductions, mortgage interest paid) must be manually extracted from the grid
  or individual account pages.
- **Feature:** A year-end summary page (selectable by year) showing:
  - **Income:** Total gross income, total net income, total taxes paid (federal, state, FICA
    broken out), total pre-tax deductions by type (401(k), HSA, etc.), total post-tax
    deductions.
  - **Spending:** Total spending by category (from paid transactions with actual amounts).
  - **Transfers:** Total transfers by destination account (mortgage payments, savings
    contributions, debt payments).
  - **Net worth change:** Net worth at Jan 1 vs. Dec 31 (sum of all account balances at each
    date).
  - **Debt progress:** Starting vs. ending principal for each debt account, total principal
    paid down during the year.
  - **Savings progress:** Starting vs. ending balance for each savings/investment account,
    total contributions during the year.
- **Tax preparation utility:** The income, deduction, and mortgage interest figures are
  directly useful during tax filing. The summary does not provide tax advice but presents the
  raw numbers the user needs.
- **Implementation:** All data exists in the system across transactions, transfers, salary
  profiles, and account balances. The work is a service function that aggregates by calendar
  year and a template that renders the results. The service queries paid transactions (for
  spending), the paycheck calculator (for income/tax/deduction breakdowns), and account
  balances (for net worth snapshots).

---

## 8A. Data Export (Priority 7)

**Goal:** Provide data export capabilities for use in external tools, tax preparation, and
personal record-keeping. This was originally task 8.4 in roadmap v4.2 and has been separated
into its own section because it is a data management concern independent of the visualization
and reporting overhaul.

- **Problem:** The app currently has no data export capability. The user cannot extract their
  financial data for use in external tools, tax preparation, or personal record-keeping.
- **Feature:** Export functionality accessible from a settings or reports page. Three export
  types:

### 8A.1 CSV Export

Export transactions for a configurable date range. Columns include: date, pay period,
transaction name, category, estimated amount, actual amount, status, account, and notes.
Transfers include both the transfer record and the shadow transaction detail. The user selects
a date range and optionally filters by account, category, or status. The export downloads as
a `.csv` file.

### 8A.2 PDF Export

Generate printable PDF reports for account dashboards, payoff calculator results, amortization
schedules, and the year-end financial summary (8.3). These are formatted for sharing with a
financial advisor, lender, or for personal record-keeping. Each exportable page gets a
"Download PDF" button.

### 8A.3 Full Data Backup

A complete export of all user data as a structured file (JSON or SQL dump) that can be used
for disaster recovery without requiring database-level access. Triggered from the settings
page. The backup includes all accounts, transactions, templates, transfers, salary profiles,
tax configurations, and account parameters. A corresponding import/restore function allows the
user to rebuild their data from a backup file.

- **Implementation notes:** CSV export is the highest priority (most broadly useful). PDF
  export depends on a PDF generation library (e.g., WeasyPrint or ReportLab). Full data
  backup is an ops feature that should be simple and reliable rather than feature-rich.

---

## 9. Spending Tracker and Companion View (Priority 8)

**Supporting document:** `phase_scope_spending_tracker.md`

**Goal:** Enable individual purchase tracking against budget-type transactions with real-time
remaining balance visibility. Provide a companion user role with a simplified, mobile-first
interface for household members who handle day-to-day purchasing but do not manage the full
budget. Eliminate the need for cash-based spending workarounds by making digital tracking lower
friction than physical cash envelopes.

### Problem

Roughly a third of recurring transactions represent variable spending where the budgeted amount
is a ceiling, not a contractual payment (e.g., groceries, fuel, personal spending, gift budgets,
seasonal shopping). These are currently modeled as single recurring transactions with a fixed
estimate. The actual spending pattern is multiple smaller purchases across the period. The user
either manually adjusts the estimated amount after each purchase to track the remainder
(error-prone) or withdraws cash for the full budgeted amount to provide physical visibility into
what remains (creates friction with online purchases and card use).

Additionally, household members who handle purchasing do not use the app because the full budget
view is overwhelming and irrelevant to their needs. They only care about their spending
categories and how much remains in each.

### Feature Summary

Three interconnected features:

1. **Sub-transaction tracking (transaction entries).** Transaction templates can be flagged with
   `track_individual_purchases`. Transactions generated from flagged templates support
   sub-entries -- individual purchase records that accumulate against the parent transaction's
   estimated amount. A computed remaining balance is displayed in the grid and the companion
   view.

2. **Entry-level credit card workflow.** Each sub-entry can be flagged as a credit card purchase.
   Credit entries are excluded from the checking balance impact and generate a CC Payback in the
   next pay period. All credit entries under one parent transaction in one period produce a single
   aggregated CC Payback. The payback amount updates dynamically as credit entries are added,
   edited, or deleted.

3. **Companion view.** A secondary user role ("companion") with a simplified, mobile-first
   interface. The companion sees only transactions from templates tagged as visible to them. They
   can add, edit, and delete sub-entries, mark parent transactions as Paid, and navigate between
   pay periods. They cannot see the full grid, account balances, dashboards, or settings.

### 9.1 Data Model

**New table: `budget.transaction_entries`**

```
budget.transaction_entries
- id: SERIAL PRIMARY KEY
- transaction_id: INT NOT NULL FK -> budget.transactions(id)
- user_id: INT NOT NULL FK -> auth.users(id)
  -- The user who created the entry. Used for audit/attribution, not visibility
  -- filtering. Both owner and companion see all entries on shared transactions.
- amount: NUMERIC(10,2) NOT NULL CHECK (amount > 0)
- description: VARCHAR(200) NOT NULL
  -- Store name or brief note (e.g., "Warehouse club", "Online order")
- entry_date: DATE NOT NULL DEFAULT CURRENT_DATE
- is_credit: BOOLEAN NOT NULL DEFAULT FALSE
- credit_payback_id: INT FK -> budget.transactions(id) NULLABLE
  -- Links credit entries to the aggregated CC Payback. All credit entries under
  -- one parent in one period share the same credit_payback_id.
- created_at: TIMESTAMPTZ NOT NULL DEFAULT NOW()
- updated_at: TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

Indexes: `(transaction_id)`, `(transaction_id, is_credit)`.

**New columns on `budget.transaction_templates`:**

```
- track_individual_purchases: BOOLEAN NOT NULL DEFAULT FALSE
- companion_visible: BOOLEAN NOT NULL DEFAULT FALSE
```

These flags are independent. A template can be companion-visible without tracking individual
purchases (e.g., an upcoming annual expense the companion wants to see but does not need
sub-entries for). A template can track purchases without being companion-visible. In practice,
most templates flagged for one will be flagged for both.

**New columns on `auth.users`:**

```
- role: VARCHAR(20) NOT NULL DEFAULT 'owner'
  CHECK (role IN ('owner', 'companion'))
- linked_owner_id: INT FK -> auth.users(id) NULLABLE
  -- NULL for owner accounts. For companion accounts, references the owner whose
  -- budget data this companion can access.
```

No registration flow. The owner creates companion accounts through a settings page or seed
script.

### 9.2 Balance Calculator Changes

**Non-entry transactions:** No change to current behavior.

**Entry-capable transactions in Projected status:**

```
sum_debit  = sum of entries where is_credit = FALSE
sum_credit = sum of entries where is_credit = TRUE

checking_impact = max(estimated - sum_credit, sum_debit)
```

The full estimated amount is reserved from checking, minus the credit card portion. If debit
spending exceeds the adjusted reservation (overspend), the actual debit total is used,
immediately reflecting the overspend in projections.

**Examples:**

| Scenario | Estimated | Debit | Credit | Checking Impact | CC Payback |
|----------|-----------|-------|--------|-----------------|------------|
| No entries | $500 | $0 | $0 | $500 | $0 |
| Under budget, debit only | $500 | $200 | $0 | $500 | $0 |
| Under budget, mixed | $500 | $300 | $100 | $400 | $100 |
| Under budget, all credit | $500 | $0 | $400 | $100 | $400 |
| Over budget, debit only | $500 | $530 | $0 | $530 | $0 |
| Over budget, mixed | $500 | $400 | $200 | $400 | $200 |

**Paid status:** The `actual_amount` is auto-populated from the sum of all entries (debit +
credit) when marked Paid. If no entries exist, the transaction behaves exactly as today (manual
actual entry). Sub-entries are never required, only enabled.

**Remaining balance** (display only, not stored): `estimated_amount - sum_of_all_entries`.
Negative values indicate overspending. The remaining balance uses the sum of ALL entries
regardless of payment method because it represents budget consumption, not checking impact.

### 9.3 Credit Card Workflow Changes

**Non-entry transactions:** Existing credit card workflow is unchanged.

**Entry-capable transactions:** The credit card workflow operates at the sub-entry level. The
parent transaction cannot use the legacy Credit status when `track_individual_purchases` is
enabled. This prevents double-counting (entry-level credit + parent-level credit would both
generate paybacks).

When a sub-entry is created with `is_credit = TRUE`:

1. Check if an aggregated CC Payback already exists for this parent transaction in the next
   period.
2. If yes: update the payback's estimated amount to the new sum of credit entries.
3. If no: create a CC Payback in the next period linked to the parent transaction.

Editing or deleting a credit entry recalculates and updates the aggregated payback. If all
credit entries are removed, the payback is deleted. Changing an entry's `is_credit` flag is
treated as a delete-from-old-type plus create-in-new-type.

The CC Payback is a regular transaction (not entry-capable) that follows the existing
reconciliation workflow.

### 9.4 Grid Changes

**Progress indicator:** For entry-capable transactions with at least one recorded entry, the grid
cell displays a progress format (e.g., "$330 / $500") instead of the single estimated amount.
Over-budget values display in warning styling. Transactions with no entries display the standard
estimated amount. Paid transactions revert to standard actual amount display.

**Tooltip enhancement:** The existing tooltip (task 4.12) is extended for entry-capable
transactions to include: total spent, remaining amount, entry count, and credit total if
applicable.

**Transaction detail:** Tapping an entry-capable transaction opens the detail view with the
sub-entry list, add/edit/delete entry forms, and remaining balance display. On mobile, this
uses the existing bottom sheet pattern.

**Template settings:** The template edit page gains two toggles: "Track individual purchases"
and "Show in companion view."

### 9.5 Companion View

**Access model:** The owner creates companion accounts through a settings UI or seed script.
Standard Flask-Login authentication. Companion users are redirected to the companion view on
login and cannot access full-access routes (route guards return 404).

**Layout:** Mobile-first, single-period view with forward/back navigation arrows (matching the
existing mobile grid navigation pattern). Current period loads by default.

**Transaction list per period:** Shows all transactions from companion-visible templates.
Entry-capable transactions display the progress indicator and remaining balance. Non-entry
transactions display the estimated amount and status. Transactions are grouped by category.

**Transaction detail:** Tapping an entry-capable transaction opens the entry list with full CRUD
(add, edit, delete entries) and a "Mark as Paid" button. Tapping a non-entry transaction opens
the existing bottom sheet for status changes.

**Companion permissions:** Can add/edit/delete sub-entries, mark transactions as Paid, navigate
periods, view companion-visible transactions. Cannot access the full grid, account balances,
dashboards, analytics, settings, template management, or transfers.

### 9.6 Dependencies and Downstream Impact

**No hard prerequisites.** This phase can be implemented at any point. No dependency on
Sections 6, 7, 8, or 8A.

**Downstream considerations:**

- **Section 8 (Visualization):** Analytics and reporting should account for sub-entry data when
  available. Budget variance (6.5) and spending trends (6.7) benefit from per-entry detail. The
  year-end summary (8.3) should include per-entry breakdowns for entry-capable transactions.
- **Phase 9 (Smart Features):** Smart estimates and anomaly detection can use entry-level data
  for more accurate projections and finer-grained anomaly flagging.
- **Phase 10 (Notifications):** Sub-entry data enables budget pace notifications (e.g., "80% of
  grocery budget spent with 8 days remaining") and overspend trend alerts.
- **Section 10 (Multi-User):** The companion role and `linked_owner_id` approach is a deliberate
  subset of full multi-user. The multi-user design should evaluate compatibility with the
  companion model and plan the migration path from `linked_owner_id` to whatever shared-access
  model is adopted.

### 9.7 Task Inventory (Preliminary)

A detailed implementation plan with sequencing, file-level scope, and test specifications will
be written before implementation begins.

**Data model and migration:**

- Add `track_individual_purchases` and `companion_visible` to `budget.transaction_templates`.
- Create `budget.transaction_entries` table.
- Add `role` and `linked_owner_id` to `auth.users`.
- Alembic migration (upgrade and downgrade tested).

**Service layer:**

- Transaction entry service: CRUD, remaining balance, entry summation, validation.
- Credit card service extension: entry-level credit, aggregated payback management.
- Balance calculator extension: entry-aware effective amount with credit adjustment.
- Transaction service extension: auto-populate actual from entries on Paid; prevent legacy Credit
  status on entry-capable transactions.

**Grid integration:**

- Progress indicator rendering in grid cells.
- Tooltip enhancement for entry-capable transactions.
- Transaction detail / bottom sheet with entry list and CRUD forms.
- Template settings toggles.

**Companion access:**

- Companion role on user model with route guards.
- Companion account creation interface.
- Companion login routing.

**Companion view:**

- Single-period view with navigation.
- Transaction list with progress indicators.
- Transaction detail with entry CRUD and mark-as-Paid.
- Mobile optimization.

**Testing:**

- Entry service unit tests (CRUD, remaining balance, edge cases).
- Balance calculator tests (all scenarios from 8B.2 table).
- Credit card workflow tests (entry-level credit, aggregated payback, edit/delete).
- Companion view integration tests (visibility filtering, entry creation, status changes).
- Full regression suite.

---

## 10. Multi-User / Kid Accounts (Far Future)

Not actively planned. The database schema already includes `user_id` on all relevant tables.
Section 9 introduces a lightweight companion role (`owner`/`companion` with `linked_owner_id`)
as a precursor. The full multi-user design should evaluate compatibility with the companion
model and plan the migration path. When the time comes, the work is primarily:

- Registration UI and flow
- Ensuring all queries filter by `user_id` (audit needed)
- Role/permission model (parent vs kid account)
- Kid account restrictions (view-only? limited editing?)
- **Account sharing model:** Some accounts may need to be visible to multiple users (e.g., a
  joint checking account shared between spouses, a savings account visible to both parent and
  child). The multi-user design should not assume strictly siloed data. A sharing model where
  specific accounts can be linked to multiple users (with configurable permissions: view-only
  vs. full access) would support household financial management. This does not need to be
  designed now but should be noted as a constraint so the eventual implementation does not
  paint itself into a single-user-per-account corner.

This phase will be scoped when it becomes relevant.

---

## 11. Deferred Items Reference

| Item                                  | Deferred From             | Notes                                                                  |
| ------------------------------------- | ------------------------- | ---------------------------------------------------------------------- |
| Scenarios (named, clone, compare)     | v3 Phase 7                | Indefinitely deferred; effort not worth reward                         |
| Paycheck calibration                  | fixes_improvements.md     | Completed as section 3.10                                              |
| Fluctuating/seasonal bills            | fixes_improvements.md     | Addressed by Phase 9 seasonal forecasting (section 6.1)                |
| Multi-user / kid accounts             | v2 Phase 6                | Far future; schema ready                                               |
| Checking account APY/interest         | fixes_improvements.md     | User confirmed checking APY is negligible; not implementing            |
| Recurrence pattern audit              | Roadmap v4, task 5.2      | Removed; section 3.2 confirmed all patterns are correct                |
| Actual paycheck value entry           | Roadmap v4, task 5.3      | Removed; superseded by paycheck calibration (section 3.10)             |
| implementation_plan_section5.md       | Roadmap v4.1, Section 5   | Defunct; Section 5 completed April 2026 without this plan (a new implementation plan was written from scratch). |
| CSV export                            | v2 Phase 6                | Listed in v2 Phase 6 (Hardening & Ops) but not implemented. Moved to Section 8A.1. |
| Account Types editing                 | fixes_improvements.md     | Completed as Section 4A settings UI enhancement (edit path added with metadata flags) |
| Salary button duplication             | fixes_improvements.md     | Completed: task 4.11 (`/salary/{id}/edit` fixed March 2026); `/salary` page fix completed as Section 5A.3 (April 2026) |
| Grid estimated vs. actual             | fixes_improvements.md     | Completed as Section 5A.1 (April 2026)                                 |
| Grid transaction sort display         | fixes_improvements.md     | Completed as Section 5A.2 (April 2026)                                 |
| Category editing and add flow         | fixes_improvements.md     | Completed as Section 5A.4 (April 2026)                                 |
| CRUD deactivate/delete inconsistency  | fixes_improvements.md     | Completed as Section 5A.5 (April 2026)                                 |
| Charts: x-axis date format            | fixes_improvements.md     | Moved to Section 8.0a (Visualization phase prerequisite bug fix)       |
| Charts: inaccurate values             | fixes_improvements.md     | Moved to Section 8.0b (Visualization phase prerequisite bug fix)       |
| Charts: total overhaul                | fixes_improvements.md     | Addressed by Section 8 retitle to Visualization and Reporting Overhaul |

---

## 12. Change Log

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.0     | 2026-03-24 | Post-production roadmap: added critical bug fix sprint, UX/grid overhaul phase, recurring transaction improvements phase; rescoped Phase 9 with seasonal expense forecasting (historical data entry, weighted trend-adjusted projections); rescoped Phase 10 with tiered notification system (warning/critical thresholds, in-app first, email deferred pending mail server setup); added multi-user as far-future placeholder; established priority ordering based on production usage feedback                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 4.0.1   | 2026-03-24 | Corrections: hosting updated to Arch Linux desktop with Docker/Nginx/Cloudflare Tunnel (was Proxmox); paycheck calibration feature added as section 3.10 (one-time calibration from real pay stub data, distinct task after tax bug fix); seasonal history data model updated with billing period dates (period_start_date, period_end_date, due_date) indexed by consumption period midpoint; grid layout section updated to prototype both full row headers and enhanced current layout side by side                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 4.1     | 2026-03-27 | Section 3 marked complete (all critical bug fixes resolved). Section 4 expanded with production feedback: tasks 4.11 (salary button placement), 4.12 (tooltip enhancement), 4.13 (emergency fund coverage fix), 4.14 (checking balance projection), 4.15 (auto loan parameter fixes), 4.16 (retirement date validation UX), 4.17 (retirement return rate clarity). Task 4.2 marked complete (resolved during transfer rework). Task 4.4 reworked into 4.4a/4.4b/4.4c (refactor to ID-based lookups before renaming status). Task 4.7 expanded to include retirement default rate on creation. Task 4.9 clarified to mortgage account page only. Task 4.10 expanded to include chart sizing fix. Section 5 retitled to "Debt and Account Improvements": task 5.1 expanded to cover all debt types (mortgage, auto loan, personal loan, student loan) with extra payment linkage; tasks 5.2 and 5.3 removed (resolved by sections 3.2 and 3.10); task 5.4 added (income-relative savings goals); task 5.5 added (payoff calculator for all debt accounts). Added prerequisite note on ref.account_types.category column utilization. Deferred items reference updated. |
| 4.2     | 2026-03-30 | Major update. Sections 3A, 4, 4A, 4B marked complete with resolution notes. Section 5 significantly expanded: tasks 5.1 and 5.5 redesigned (payment timeline with status distinction, three-line chart with floor marker); task 5.4 updated to database-driven ref tables; seven new tasks added (5.6 savings dashboard SRP refactor, 5.7 ARM rate support, 5.8 amortization edge cases, 5.9 loan payoff lifecycle, 5.10 refinance calculator, 5.11 debt snowball/avalanche strategy, 5.12 debt summary and DTI); four more tasks added (5.13 full amortization schedule, 5.14 payment allocation breakdown, 5.15 savings goal trajectory, 5.16 recurring obligation summary). Section 6 expanded: 6.5 (budget variance analysis), 6.6 (annual expense calendar), 6.7 (spending trend detection). Section 7 notification types expanded: missed payment detection, ARM rate adjustment reminder, savings goal pace alert. New Section 8 added (Dashboard, Reporting, and Data Management): 8.1 summary dashboard, 8.2 financial calendar, 8.3 year-end summary, 8.4 data export (CSV, PDF, full backup). CSV export noted as unimplemented from v2 Phase 6. Section 9 (was 8, Multi-User) updated with account sharing model note. Sections 9-11 renumbered. CLAUDE.md Active Work section flagged as stale. |
| 4.3     | 2026-03-31 | New Section 5A added (Cleanup Sprint, Priority 3A): five tasks from fixes_improvements.md analysis -- estimated-vs-actual grid calculation fix (5A.1), category item sub-headers in grid (5A.2), salary listing page button fix (5A.3), category management overhaul with edit, re-parent, and group dropdown (5A.4), unified two-step delete/archive pattern (5A.5). Section 5A established as prerequisite to Section 5. Task 4.11 updated from COMPLETE to PARTIAL (salary edit page fixed, listing page deferred to 5A.3). Section 8 retitled from "Dashboard, Reporting, and Data Management" to "Visualization and Reporting Overhaul" (Priority 6): chart bug fixes added as prerequisite tasks (8.0a x-axis date format, 8.0b inaccurate balance values), scope expanded to include chart redesign and display layers for Section 6 Smart Features engine output. Task 8.4 (Data Export) separated into new Section 8A (Priority 7) with renumbered sub-tasks (8A.1 CSV, 8A.2 PDF, 8A.3 full backup). Phase ordering table updated: Cleanup Sprint (3A) before Debt Improvements (3), Smart Features (4) includes all seven tasks 6.1-6.7, Notifications (5) unchanged, Visualization and Reporting (6) consolidated, Data Export (7) standalone, Multi-user (8) unchanged. Section 6 scope clarified: tasks 6.5-6.7 build computation engines in Smart Features; display layers built in Section 8. Deferred Items Reference updated with full disposition of all fixes_improvements.md items. Account Types editing marked resolved by Section 4A. |
| 4.4     | 2026-04-07 | Sections 5A and 5 marked complete (April 2026). Mobile responsiveness added to completed phases as unplanned work (April 2026). Section 8 (Visualization and Reporting Overhaul) marked in progress with supporting documents (`section8_scope.md`, `implementation_plan_section8.md`); priority updated from 6 to 4 to reflect actual execution order; prerequisite corrected (Section 6 tasks 6.1-6.4 are NOT prerequisites per scope document); task 8.0b excluded per scope document (no bug found in code audit); scope clarified to include 6.5/6.6/6.7 computation engines AND display layers built together, plus CSV export for analytics views. Phase ordering renumbered: Section 8 now Priority 4 (in progress), Phase 9 now Priority 5, Phase 10 now Priority 6. Five new tasks added to Phase 9 (Section 6): 6.8 third paycheck suggestions (dashboard card with one-click transfer, future priority engine noted), 6.9 estimate confidence indicator (three-tier visual signal on future transactions), 6.10 bill due date optimization (advisory analysis with `is_due_date_flexible` flag, imbalance detection, action path recommendation), 6.11 year-over-year seasonal comparison (prior year actual alongside forecast), 6.12 expense anomaly detection (inline warning during mark-as-paid with two-step confirmation, `anomaly_threshold_pct` setting, shared estimate resolution utility with 6.9). Section 6 goal description updated to reflect expanded scope. Deferred Items Reference updated: implementation_plan_section5.md marked moot (Section 5 complete), salary button duplication marked fully complete (5A.3), grid estimated-vs-actual/transaction sort/category editing/CRUD consistency marked completed (5A.1/5A.2/5A.4/5A.5). |
| 4.5     | 2026-04-07 | Section 7 (Phase 10: Notifications) fully rescoped. Notification types expanded from 7 to 15 across 6 named groups: Balance & Cash Flow (low projected balance with configurable lookahead defaulting to 6 periods, balance recovery, pre-payday cash flow summary), Bills & Payments (upcoming large expense with flat dollar and/or percentage-of-paycheck thresholds, missed payment detection with escalation, unreconciled period aging with warning/critical escalation), Savings & Goals (savings milestone with celebratory 100% treatment, savings goal pace alert, savings contribution reminder), Debt (debt payoff milestone pulled from "future" into initial build, ARM rate adjustment reminder), Templates & Trends (recurring template change detection dependent on Phase 9 task 6.2 rolling averages), Digests (weekly summary as pinned dropdown item and email digest, payday reconciliation reminder as email-only). Data model expanded: `notification_settings` gains explicit columns for all configurable parameters (`lookahead_periods`, `large_expense_amount`, `large_expense_pct`, `missed_payment_escalation_days`, `period_aging_warning_days`, `period_aging_critical_days`, `template_change_threshold_pct`); `notifications` gains `snoozed_until`, `resolved_at`, `is_pinned`, `related_entity_url`; new `notification_email_preferences` table for `preferred_delivery_time`, `delivery_window_start`, `delivery_window_end`. New infrastructure subsections: snooze (1 day/3 days/7 days/until next payday), auto-resolve with per-type conditions, persist dashboard alerts as notifications (mapping Section 8 ephemeral alerts to notification types). In-app delivery split into bell/dropdown (last 10 with pinned digest, snooze/dismiss actions, "View all" link) and full `/notifications` page (type/severity/date/status filters). Settings UI design decision: Option B grouped expandable sections at `/settings/notifications` with group-level master toggles, per-type enabled/channel/parameter controls, email greyed out until mail server configured. Defaults: in-app enabled, email disabled for all types including future additions. Email delivery expanded with delivery window, preferred delivery time, batching (1-hour window for high-frequency types, immediate for milestones/reminders/digests). Phase ordering summary updated. |
| 4.6     | 2026-04-09 | New Section 9 added (Spending Tracker and Companion View, Priority 8): sub-transaction entry tracking on budget-type transactions with remaining balance visibility; entry-level credit card workflow with per-entry `is_credit` flag and aggregated CC paybacks per parent transaction per period; companion user role (`owner`/`companion` on `auth.users` with `linked_owner_id`) for household members who handle purchasing but do not manage the full budget; companion view is mobile-first single-period navigation with entry CRUD and mark-as-Paid capability; new `budget.transaction_entries` table; new `track_individual_purchases` and `companion_visible` flags on `budget.transaction_templates`; balance calculator extended with entry-aware effective amount formula `checking_impact = max(estimated - sum_credit, sum_debit)` for mid-period mixed debit/credit scenarios; parent transactions with `track_individual_purchases` cannot use legacy Credit status (entry-level credit replaces it); grid gains progress indicator ("$330 / $500") for entry-capable transactions; no hard prerequisites on any planned phase. Supporting document: `phase_scope_spending_tracker.md`. Multi-user (Section 10) bumped from Priority 8 to Priority 9. Sections 10-12 renumbered. Phase ordering table updated. |