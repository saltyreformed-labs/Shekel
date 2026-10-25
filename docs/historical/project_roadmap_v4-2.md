> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Budget App -- Project Roadmap v4.2

**Version:** 4.2
**Date:** March 30, 2026
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
| 3        | Debt and Account Improvements | Payment linkage, ARM support, payoff lifecycle, multi-scenario and cross-account payoff visualization, refinance calculator, debt snowball/avalanche, DTI ratio, income-relative savings goals |
| 4        | Phase 9: Smart Features       | Seasonal expense forecasting, smart estimates, expense inflation, deduction inflation, budget variance analysis, annual expense calendar, spending trend detection                             |
| 5        | Phase 10: Notifications       | In-app alerts (low balance, large expenses, payday reminders, missed payments, ARM rate reminders, goal pace alerts), email delivery added later                                              |
| 6        | Dashboard, Reporting, Data    | Summary dashboard, financial calendar, year-end financial summary, data export (CSV, PDF, full backup)                                                                                        |
| 7        | Multi-user (far future)       | Kid accounts, registration flow, account sharing model; not actively planned                                                                                                                  |

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

- **Resolution:** Fixed. View Breakdown and View Projection buttons moved to a prominent
  position near the top of the salary profile page.

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

## 5. Debt and Account Improvements (Priority 3)

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

## 6. Phase 9: Smart Features (Priority 4)

**Goal:** Make the app smarter about projecting future expenses based on historical patterns.
The core addition is seasonal expense forecasting, followed by rolling average estimates for
non-seasonal variable expenses, and then inflation adjustments.

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

---

## 7. Phase 10: Notifications (Priority 5)

**Goal:** Alert the user to important financial events without requiring them to manually scan
the grid. In-app notifications are built first. Email delivery is added later once the
self-hosted mail server is operational.

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
- threshold_warning: NUMERIC(12,2)    -- for balance warnings
- threshold_critical: NUMERIC(12,2)   -- for balance warnings
- days_before: INT                     -- for upcoming expense reminders
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
- related_entity_type: VARCHAR(50)     -- 'account', 'transaction', 'pay_period', etc.
- related_entity_id: INT
- created_at: TIMESTAMPTZ DEFAULT NOW()
- read_at: TIMESTAMPTZ
```

#### 7.1.2 Notification Types (Priority Order)

1. **Low projected balance (warning and critical):** Triggered when the projected balance for
   any future pay period drops below the configured threshold. Warning and critical thresholds
   are configurable per account. Severity is determined by which threshold is breached.
2. **Upcoming large expense:** Triggered N days before a large expense is due (configurable:
   what counts as "large" and how many days before). Gives the user time to ensure funds are
   available.
3. **Payday reconciliation reminder:** Triggered on payday (or the day after) as a reminder to
   open the app and reconcile the current period. Primarily useful for email delivery.
4. **Savings milestone reached:** Triggered when a savings goal reaches a milestone percentage
   (25%, 50%, 75%, 100%). Informational/motivational.
5. **Missed payment detection:** Triggered when a recurring transaction passes its due date and
   remains in Projected status (not marked Paid). Catches genuinely missed payments and
   transactions the user forgot to reconcile. Detection runs during the daily scheduled check
   (7.1.3). Severity: warning initially, escalates to critical if the transaction remains
   unreconciled after a configurable number of days (default: 3).
6. **ARM rate adjustment reminder:** Triggered N days before an ARM loan's next scheduled rate
   adjustment date (configurable, default: 30 days). The adjustment date is calculated from
   the loan's origination date, `arm_first_adjustment_months`, and
   `arm_adjustment_interval_months`. Reminds the user to watch for their lender's rate change
   notice and update the rate history in the app. Depends on Section 5 task 5.7 (ARM rate
   support).
7. **Savings goal pace alert:** Triggered when a savings goal with a target date is falling
   behind the required savings rate. Depends on Section 5 task 5.15 (savings goal trajectory).
   Example: "At your current rate, you'll miss your Emergency Fund goal by $2,400. Increase
   monthly contributions from $500 to $650 to reach your target by December 2027."

Future notification types (not in initial build):

- Loan payoff milestones
- Retirement goal milestones
- Contribution limit warnings (approaching annual 401(k)/IRA limit)

#### 7.1.3 Trigger Mechanism

- **On transaction edit:** After any transaction is created, updated, or status-changed, the
  balance roll-forward is recalculated. At this point, check projected balances against
  thresholds for low balance warnings.
- **Scheduled check (daily):** A lightweight scheduled job (cron or APScheduler) runs daily to
  check for upcoming large expenses and generate payday reminders. This avoids making the
  transaction edit path heavier than necessary.
- **On savings goal update:** When a savings account balance changes (via transfer or anchor
  true-up), check goal progress for milestone notifications.

#### 7.1.4 Deduplication

- The system should not generate duplicate notifications for the same event. Use a combination
  of notification_type + related_entity_type + related_entity_id + a time window to prevent
  duplicates.
- Example: A low balance warning for checking account ID 1 for pay period ID 47 should only
  be generated once. If the user fixes the issue and the balance recovers, the notification
  can be marked resolved. If the balance drops again later, a new notification is generated.

### 7.2 In-App Delivery

- **Notification bell icon** in the app header (Bootstrap navbar). Displays an unread count
  badge.
- **Dropdown panel** on click: shows recent notifications, newest first. Each notification
  shows severity (color-coded), title, timestamp, and a brief message.
- **Mark as read:** Clicking a notification marks it as read. Option to mark all as read.
- **Link to context:** Notifications with a related entity link to the relevant page (e.g., a
  low balance warning links to the pay period in the grid; a savings milestone links to the
  account dashboard).
- **Settings page:** A notification settings section (in the existing Settings area) where the
  user configures thresholds, toggles notification types on/off, and enables/disables email
  delivery per type.

### 7.3 Email Delivery (Deferred Sub-phase)

- **Dependency:** Requires a self-hosted mail server to be operational. This is infrastructure
  work outside the app (OPNsense mail features or a dedicated mail service on the Arch Linux
  host). Research and setup should happen in parallel with the in-app notification build.
- **Implementation:** When a notification is generated and the user has `delivery_email = TRUE`
  for that type, queue an email. Use Python's `smtplib` with the self-hosted SMTP server.
- **Email format:** Simple HTML email with the notification title, message, severity, and a
  link back to the app.
- **Batching:** For notification types that could fire frequently (low balance warnings during
  a heavy editing session), batch emails into a digest rather than sending one per event.
  Suggested: batch window of 1 hour. Payday reminders and milestone notifications send
  immediately.

---

## 8. Dashboard, Reporting, and Data Management (Priority 6)

**Goal:** Provide summary views, reporting capabilities, and data management tools that make
the app more useful for planning and record-keeping beyond the day-to-day payday workflow.

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

### 8.4 Data Export

- **Problem:** The app currently has no data export capability. The user cannot extract their
  financial data for use in external tools, tax preparation, or personal record-keeping.
- **Feature:** Export functionality accessible from a settings or reports page. Three export
  types:

  1. **CSV export:** Export transactions for a configurable date range. Columns include: date,
     pay period, transaction name, category, estimated amount, actual amount, status, account,
     and notes. Transfers include both the transfer record and the shadow transaction detail.
     The user selects a date range and optionally filters by account, category, or status.
     The export downloads as a `.csv` file.

  2. **PDF export:** Generate printable PDF reports for account dashboards, payoff calculator
     results, amortization schedules, and the year-end financial summary (8.3). These are
     formatted for sharing with a financial advisor, lender, or for personal record-keeping.
     Each exportable page gets a "Download PDF" button.

  3. **Full data backup:** A complete export of all user data as a structured file (JSON or
     SQL dump) that can be used for disaster recovery without requiring database-level access.
     Triggered from the settings page. The backup includes all accounts, transactions,
     templates, transfers, salary profiles, tax configurations, and account parameters.
     A corresponding import/restore function allows the user to rebuild their data from a
     backup file.

- **Implementation notes:** CSV export is the highest priority (most broadly useful). PDF
  export depends on a PDF generation library (e.g., WeasyPrint or ReportLab). Full data
  backup is an ops feature that should be simple and reliable rather than feature-rich.

---

## 9. Multi-User / Kid Accounts (Far Future)

Not actively planned. The database schema already includes `user_id` on all relevant tables.
When the time comes, the work is primarily:

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

## 10. Deferred Items Reference

| Item                              | Deferred From             | Notes                                                                  |
| --------------------------------- | ------------------------- | ---------------------------------------------------------------------- |
| Scenarios (named, clone, compare) | v3 Phase 7                | Indefinitely deferred; effort not worth reward                         |
| Paycheck calibration              | fixes_improvements.md     | Completed as section 3.10                                              |
| Fluctuating/seasonal bills        | fixes_improvements.md     | Addressed by Phase 9 seasonal forecasting (section 6.1)                |
| Multi-user / kid accounts         | v2 Phase 6                | Far future; schema ready                                               |
| Checking account APY/interest     | fixes_improvements.md     | User confirmed checking APY is negligible; not implementing            |
| Recurrence pattern audit          | Roadmap v4, task 5.2      | Removed; section 3.2 confirmed all patterns are correct                |
| Actual paycheck value entry       | Roadmap v4, task 5.3      | Removed; superseded by paycheck calibration (section 3.10)             |
| implementation_plan_section5.md   | Roadmap v4.1, Section 5   | Defunct; predates transfer rework, Section 4, account param arch, and audit. Must be rewritten before starting Section 5. |
| CSV export                        | v2 Phase 6                | Listed in v2 Phase 6 (Hardening & Ops) but not implemented. Moved to Section 8.4. |

---

## 11. Change Log

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.0     | 2026-03-24 | Post-production roadmap: added critical bug fix sprint, UX/grid overhaul phase, recurring transaction improvements phase; rescoped Phase 9 with seasonal expense forecasting (historical data entry, weighted trend-adjusted projections); rescoped Phase 10 with tiered notification system (warning/critical thresholds, in-app first, email deferred pending mail server setup); added multi-user as far-future placeholder; established priority ordering based on production usage feedback                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 4.0.1   | 2026-03-24 | Corrections: hosting updated to Arch Linux desktop with Docker/Nginx/Cloudflare Tunnel (was Proxmox); paycheck calibration feature added as section 3.10 (one-time calibration from real pay stub data, distinct task after tax bug fix); seasonal history data model updated with billing period dates (period_start_date, period_end_date, due_date) indexed by consumption period midpoint; grid layout section updated to prototype both full row headers and enhanced current layout side by side                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 4.1     | 2026-03-27 | Section 3 marked complete (all critical bug fixes resolved). Section 4 expanded with production feedback: tasks 4.11 (salary button placement), 4.12 (tooltip enhancement), 4.13 (emergency fund coverage fix), 4.14 (checking balance projection), 4.15 (auto loan parameter fixes), 4.16 (retirement date validation UX), 4.17 (retirement return rate clarity). Task 4.2 marked complete (resolved during transfer rework). Task 4.4 reworked into 4.4a/4.4b/4.4c (refactor to ID-based lookups before renaming status). Task 4.7 expanded to include retirement default rate on creation. Task 4.9 clarified to mortgage account page only. Task 4.10 expanded to include chart sizing fix. Section 5 retitled to "Debt and Account Improvements": task 5.1 expanded to cover all debt types (mortgage, auto loan, personal loan, student loan) with extra payment linkage; tasks 5.2 and 5.3 removed (resolved by sections 3.2 and 3.10); task 5.4 added (income-relative savings goals); task 5.5 added (payoff calculator for all debt accounts). Added prerequisite note on ref.account_types.category column utilization. Deferred items reference updated. |
| 4.2     | 2026-03-30 | Major update. Sections 3A, 4, 4A, 4B marked complete with resolution notes. Section 5 significantly expanded: tasks 5.1 and 5.5 redesigned (payment timeline with status distinction, three-line chart with floor marker); task 5.4 updated to database-driven ref tables; seven new tasks added (5.6 savings dashboard SRP refactor, 5.7 ARM rate support, 5.8 amortization edge cases, 5.9 loan payoff lifecycle, 5.10 refinance calculator, 5.11 debt snowball/avalanche strategy, 5.12 debt summary and DTI); four more tasks added (5.13 full amortization schedule, 5.14 payment allocation breakdown, 5.15 savings goal trajectory, 5.16 recurring obligation summary). Section 6 expanded: 6.5 (budget variance analysis), 6.6 (annual expense calendar), 6.7 (spending trend detection). Section 7 notification types expanded: missed payment detection, ARM rate adjustment reminder, savings goal pace alert. New Section 8 added (Dashboard, Reporting, and Data Management): 8.1 summary dashboard, 8.2 financial calendar, 8.3 year-end summary, 8.4 data export (CSV, PDF, full backup). CSV export noted as unimplemented from v2 Phase 6. Section 9 (was 8, Multi-User) updated with account sharing model note. Sections 9-11 renumbered. CLAUDE.md Active Work section flagged as stale. |
