> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Budget App -- Project Roadmap v4.1

**Version:** 4.1
**Date:** March 27, 2026
**Parent Documents:** project_requirements_v2.md, project_requirements_v3_addendum.md

---

## 1. Current State Summary

### Completed Phases

| Source Document        | Phase                            | Status                      |
| ---------------------- | -------------------------------- | --------------------------- |
| v2 Phase 1             | Replace the Spreadsheet          | Done                        |
| v2 Phase 2             | Paycheck Calculator              | Done                        |
| v2 Phase 4             | Savings and Accounts             | Done                        |
| v2 Phase 5             | Visualization                    | Done                        |
| v2 Phase 6             | Hardening and Ops                | Done                        |
| v3 Phase 1 (was v2 P1) | Replace the Spreadsheet          | Done                        |
| v3 Phase 2 (was v2 P2) | Paycheck Calculator              | Done                        |
| v3 Phase 3             | HYSA and Accounts Reorganization | Done                        |
| v3 Phase 4             | Debt Accounts                    | Done                        |
| v3 Phase 5             | Investments and Retirement       | Done                        |
| v3 Phase 6             | Visualization                    | Done                        |
| v3 Phase 8             | Hardening and Ops                | Done                        |
| Roadmap v4 Section 3   | Critical Bug Fixes               | Done (completed March 2026) |

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
| 2        | UX/Grid Overhaul              | Focused sprint to address daily-use friction in the budget grid and related views. Expanded with production feedback items. |
| 3        | Debt and Account Improvements | Debt payment linkage, payoff calculators for all debt types, income-relative savings goals                                  |
| 4        | Phase 9: Smart Features       | Seasonal expense forecasting, smart estimates, expense inflation, deduction inflation                                       |
| 5        | Phase 10: Notifications       | In-app alerts (low balance, large expenses, payday reminders), email delivery added later                                   |
| 6        | Multi-user (far future)       | Kid accounts, registration flow; not actively planned                                                                       |

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

## 4. UX/Grid Overhaul (Priority 2)

**Goal:** A focused sprint to improve the daily-use experience of the budget grid and related
views. Every fix in this phase should make the payday reconciliation workflow faster or clearer.
This section has been expanded with production feedback items (tasks 4.11 through 4.17).

### 4.1 Grid Layout: Category/Transaction Name Clarity

- **Problem:** Showing only the category group and item name without the transaction name is
  confusing when multiple transactions share a category item. Hover-to-reveal is an extra step.
- **Approach:** Prototype two layouts side by side and compare before committing:
  - **Option A -- Full row headers:** Each transaction gets its own row header with the
    transaction name, similar to the spreadsheet being replaced. Use compact styling (smaller
    font, tighter row height) to prevent excessive vertical space consumption. This is the
    familiar pattern and may be the clearest for approximately 10-15 active line items.
  - **Option B -- Enhanced current layout:** Keep the grouped layout but add visible
    transaction name labels within each row (smaller font, secondary color, or indentation
    under the category header).
- **Decision criteria:** Whichever layout makes it fastest to identify a specific transaction
  during the payday reconciliation workflow wins. Both prototypes should be tested with a
  realistic number of line items.

### 4.2 Footer Condensation -- COMPLETE (Superseded)

- **Resolution:** The footer was condensed during the transfer architecture rework (Phase
  3A-II). The transfer section of the grid was removed and the footer was condensed
  simultaneously. No further work needed.

### 4.3 Pay Period Date Format Cleanup

- **Problem:** Pay period column headers display both MM/DD and MM/DD/YY, which is redundant
  and wastes horizontal space.
- **Fix:** Display a single date format. Use MM/DD within the current year and MM/DD/YY (or
  MMM DD) only when the period crosses into a different year from the current view.

### 4.4 Status Refactor and Rename

- **Problem (original):** The status "Done" is generic. For expenses, "Paid" is clearer.
- **Problem (expanded -- root cause):** The codebase uses `ref.statuses.name` for logic
  comparisons instead of `ref.statuses.id`. This makes the display name load-bearing: renaming
  "done" to "paid" in the name column would break every comparison in the codebase. The
  database was designed with integer primary keys on reference tables specifically to allow
  display names to change without affecting logic.
- **Approach:** Split into three sub-tasks executed in order.

#### 4.4a Refactor Status Lookups to Use ID Field

Audit the entire codebase for places where `status.name` is used in logic (comparisons,
conditionals, queries, filter expressions) and replace with `status.id` lookups. This
includes routes, services, templates with conditional logic (Jinja `{% if %}` blocks), and
test assertions.

**Scope of the audit:**

- All Python files: search for string comparisons against status names ("projected", "done",
  "received", "credit", "cancelled").
- All Jinja templates: search for `status.name ==` or similar patterns.
- All test files: search for assertions that check status name strings.
- Replace each occurrence with an ID-based comparison. The ID values should be defined as
  constants (e.g., in a constants module or on the model) to avoid magic numbers.

#### 4.4b Rename "Done" to "Paid" in ref.statuses.name

Once all logic uses IDs (4.4a is complete), the name field becomes a display-only label.
Change the name value from "done" to "paid" in the seed data and create an Alembic migration
to update the existing row. No `display_label` column is needed because the `name` field
itself serves as the display label once logic no longer depends on its value.

#### 4.4c Audit All Reference Tables for String-Based Lookups

Extend the pattern established in 4.4a to all `ref.*` tables. Audit for any place the
codebase uses `ref.*.name` in logic where `ref.*.id` should be used instead. Common
candidates include:

- `ref.account_types`
- `ref.transaction_types`
- `ref.recurrence_patterns`

Fix any instances found. This prevents the same class of problem from recurring when display
names need to change for other reference data.

### 4.5 Deduction Frequency Display

- **Problem:** The amount and "per year" columns on the salary deductions view are squished
  together, making it unclear how often a deduction applies.
- **Fix:** Improve column spacing or display frequency as a descriptive label (e.g.,
  "24x/year", "26x/year", "12x/year") instead of raw numbers.

### 4.6 Tax Config Page Reorganization

- **Problem:** Static (rarely changed) tax bracket tables are displayed at the top of the Tax
  Config page. User-adjustable settings are buried below.
- **Fix:** Move adjustable settings (filing status, state rate, etc.) to the top. Move static
  bracket tables to a collapsible section at the bottom. Hide previous tax years (e.g., 2025)
  by default with an option to expand.

### 4.7 Account Parameter Setup UX

- **Problem:** Setting rates and parameters for savings and retirement accounts requires the
  user to know they need to click an icon in the corner of the account card. This is not
  discoverable. Additionally, retirement accounts show a default annual rate of return that
  is not automatically applied on creation -- the user must manually navigate to the account
  and save parameters before the default takes effect.
- **Approach:** When creating a new account of a type that requires parameters (HYSA, mortgage,
  auto loan, personal loan, student loan, investment, retirement), prompt the user to configure
  those parameters as part of the creation flow. Options:
  - Inline the parameter fields in the account creation form, shown/hidden based on
    account type selection.
  - Redirect to the parameter configuration page immediately after account creation with a
    setup wizard banner.
  - Show a "Setup required" badge on the account card until parameters are configured.
- **Retirement-specific:** Default return rates should be pre-populated and saved as part of
  the creation flow so the account's projections are immediately accurate without a separate
  manual step.

### 4.8 Mortgage Parameter Page Flow

- **Problem:** Mortgage accounts redirect to a separate page for setting parameters. This may
  be better handled as inline fields that appear when the mortgage account type is selected
  during creation.
- **Approach:** Evaluate as part of 4.7 above. The mortgage parameter form could become a
  section within the account creation/edit form, conditionally displayed based on account type.

### 4.9 Chart: Mortgage Balance Over Time Contrast

- **Problem:** The "standard payments" line on the mortgage account page's Balance Over Time
  chart is difficult to see due to low contrast.
- **Scope clarification:** This task applies to the Balance Over Time chart on the **mortgage
  account detail page**, not the main Charts page. The "standard payments" line is a
  mortgage-specific concept that only appears on the mortgage account view.
- **Fix:** Increase line weight, change color, or add a distinct dash pattern to the standard
  payments line on the mortgage account detail page chart.

### 4.10 Chart: Balance Over Time Time Frame Control and Sizing

- **Problem (time frame):** The Balance Over Time chart defaults to a one-year view, which
  shows flat lines for long-duration accounts like mortgages and retirement.
- **Problem (sizing):** The Balance Over Time chart on the Charts page does not fill the whole
  card. It is compressed into a much smaller area than the card provides.
- **Fix (sizing):** Fix the chart container CSS so the Chart.js canvas fills the available
  card space. This should be addressed first as a prerequisite to the time frame controls.
- **Fix (time frame):** Add time frame controls (1 year, 5 years, 10 years, full term).
  Default should be intelligent based on account type (e.g., full term for mortgages,
  to-retirement for retirement accounts).

### 4.11 Salary Profile Button Placement

- **Source:** fixes_improvements.md (production feedback)
- **Problem:** The View Breakdown and View Projection buttons are at the bottom of the salary
  profile page and are hard to find.
- **Fix:** Move these buttons higher on the page. Options include placing them immediately
  below the salary summary section, in a sticky sub-header, or as prominent action buttons
  near the top of the page alongside the profile name/details.

### 4.12 Grid Tooltip Enhancement

- **Source:** fixes_improvements.md (production feedback)
- **Problem:** Grid tooltips are slow to appear and only show the transaction name. For
  transactions where the grid displays a rounded or truncated amount, the tooltip does not
  show the full dollar amount.
- **Fix (content):** Update tooltips to show both the transaction name and the full formatted
  dollar amount (e.g., "Audible -- $15.96" instead of just "Audible").
- **Fix (speed):** Investigate the tooltip delay. If it is a Bootstrap default delay, reduce
  it. If it is an HTMX fetch delay, evaluate whether the tooltip content can be rendered
  inline (in a `title` attribute or `data-*` attribute) rather than fetched on hover.
- **Note:** If task 4.1 is implemented with Option A (full row headers showing transaction
  names), the tooltip's transaction name becomes redundant. In that case, the tooltip should
  focus on showing the full amount and any other useful metadata (status, actual vs. estimated,
  notes). Re-evaluate tooltip content after the 4.1 decision is made.

### 4.13 Emergency Fund Coverage Calculation Fix

- **Source:** fixes_improvements.md (production feedback)
- **Severity:** Data correctness
- **Problem:** The Emergency Fund Coverage calculation on the accounts dashboard does not
  include transfers out of checking as expenses. This understates the user's committed monthly
  outflows and produces an inflated (inaccurate) coverage figure. For example, mortgage
  payments set up as transfers from checking to the mortgage account are not counted, but they
  represent real monthly obligations that would need to be covered in an emergency.
- **Fix:** Include all recurring transfers out of checking in the monthly expense baseline used
  for the emergency fund calculation. This is the simplest approach and correctly reflects the
  user's total committed outflows.
- **Rationale for including savings transfers:** In an emergency scenario (e.g., job loss), the
  user would likely stop regular savings contributions. The coverage metric should reflect
  total current committed outflows to give an accurate months-of-runway figure. The savings
  transfer represents discretionary-but-committed spending that would cease under duress,
  which is exactly what emergency fund coverage is meant to bridge.
- **Future enhancement (not in initial fix):** If the user later wants to exclude specific
  transfers from the emergency fund calculation, a per-transfer or per-destination-account
  flag could be added. This is not needed now.

### 4.14 Checking Account Balance Projection

- **Source:** fixes_improvements.md (production feedback)
- **Problem:** The checking account's dedicated page does not show a balance projection,
  while savings accounts do. The user has no forward-looking view of the checking balance
  outside the budget grid.
- **Fix:** Add a balance projection display to the checking account detail page, using the
  same projected end balances that the grid's balance calculator already computes. This
  reuses existing logic -- no new calculation engine is needed. Display it as a simple
  projection list or chart showing projected balance per pay period, consistent with the
  savings account projection view.
- **Note:** Checking account APY/interest projection is not included. The user has confirmed
  that checking APY is negligible and not worth implementing.

### 4.15 Auto Loan Parameter Page Fixes

- **Source:** fixes_improvements.md (production feedback)
- **Problem (balance/principal disconnect):** When creating a new auto loan account, the
  initial creation page has a field for current balance. After creation, the app redirects to
  the parameter page where the user must enter the current principal again. The current
  balance entered during creation does not carry over to the principal field. The user should
  not have to enter the same number twice.
- **Problem (missing term field):** The auto loan parameter page allows editing the current
  principal, interest rate, and payment day, but not the term. This is an inconsistency --
  every other parameter is editable except term.
- **Fix (balance/principal):** Either pre-populate the current principal field from the
  balance entered during account creation, or remove the current balance field from the
  creation form for account types that have a dedicated principal field on their parameter
  page. Evaluate which approach is cleaner in the context of task 4.7 (account parameter
  setup UX).
- **Fix (term):** Add the term field to the auto loan parameter page so it is editable like
  all other parameters.
- **Scope:** These fixes apply to auto loan accounts. Verify that personal loan and student
  loan accounts do not have the same issues. If they do, fix all loan types together.

### 4.16 Retirement Date Validation UX

- **Source:** fixes_improvements.md (production feedback)
- **Problem:** When an invalid date is entered for Earliest Retirement Date or Planned
  Retirement Date, the toast notification says to "correct the highlighted error" but no
  fields are highlighted. Additionally, the form clears all data on validation failure,
  forcing the user to re-enter everything.
- **Fix (highlights):** Add field-level error indicators (red border, error message below the
  field) to the specific date fields that failed validation. Ensure the toast message matches
  the actual visual feedback.
- **Fix (form state):** Preserve form data on validation failure. The server should return the
  submitted values so the template can repopulate the fields. This is standard form handling
  and prevents data loss on minor input errors.
- **Relationship to 3.8:** Task 3.8 (completed) added the server-side validation rules.
  This task covers the client-side error display that was not addressed in 3.8.

### 4.17 Retirement Dashboard Return Rate Clarity

- **Source:** fixes_improvements.md (production feedback)
- **Problem:** The Assumed Annual Return slider on the retirement dashboard is unclear. The
  user cannot determine what it controls, what fields it recalculates, or whether it overrides
  per-account return rates. The only visible change is to the Projected Retirement Savings
  field, but the individual retirement account projections do not appear to update.
- **Fix:** Address the ambiguity with the following:
  1. Add explanatory text near the slider stating exactly what it affects (e.g., "This rate
     is used to project your total retirement savings. Individual account rates are set on
     each account's parameter page.").
  2. When the slider value changes, visually update all fields that are recalculated. If
     individual account projections on the dashboard are affected, they should update in
     real time. If they use their own per-account rates and are not affected, make that
     distinction visible.
  3. Clarify the relationship between the global slider rate and per-account rates. Options:
     the slider could be a global override, a default for accounts without a configured rate,
     or a separate planning parameter used only for the aggregate projection. Document which
     interpretation is implemented and make it clear in the UI.

---

## 5. Debt and Account Improvements (Priority 3)

**Goal:** Improve how debt accounts interact with the transfer system, extend payoff tools
to all debt account types, and add income-relative savings goals. This section was originally
titled "Recurring Transaction Improvements" and has been expanded and retitled to reflect its
broader scope.

**Account type context:** The app seeds four debt account types: mortgage, auto loan, personal
loan, and student loan. All share the amortization engine but have type-specific parameter
tables with varying structures. Tasks in this section that reference "all debt accounts"
apply to all four types.

**Prerequisite note on `ref.account_types.category`:** The `category` column exists on
`ref.account_types` but is not fully utilized in the codebase. Several tasks in this section
benefit from being able to programmatically identify debt accounts by querying
`category = 'liability'` rather than hardcoding a list of account type names. Before starting
this section, verify that the category column is populated for all seeded account types and
consider whether the column should be promoted to an explicit classification used in business
logic (e.g., to drive which accounts show payoff calculators, which accounts accept debt
payment linkage, etc.). This aligns with the pattern established in task 4.4c: using stable
identifiers rather than string name matching.

If the category column is insufficient for the distinctions needed (e.g., differentiating
between debt-with-amortization accounts and simple savings accounts), evaluate whether an
additional field such as `is_debt`, `is_amortized`, or a more granular `account_class` is
warranted. The goal is a single source of truth for "which account types support payoff
calculations" that does not require maintaining a hardcoded list of type names in the
service layer.

### 5.1 Debt Account Payment Linkage

- **Original scope (Mortgage Auto-Payment Creation):** After creating a mortgage account with
  a monthly payment amount, offer to automatically create a recurring transfer from checking
  to the mortgage account.
- **Expanded scope:** This task now covers two related needs for all debt account types
  (mortgage, auto loan, personal loan, student loan):
  1. **Recurring transfer creation prompt:** After a debt account is created and parameters
     are saved, offer the user a prompt: "Create a recurring monthly transfer of $X from
     [source account] to this account?" This creates a transfer record and a corresponding
     recurrence rule. Applies to all four debt types.
  2. **Extra payment linkage:** Ensure that all transfers to a debt account (both recurring
     and one-time/ad hoc) are reflected in the account's balance projection and amortization
     schedule. The amortization engine should accept actual payment history as input and
     recalculate the projected payoff date, remaining interest, and schedule accordingly.
     Currently, the amortization appears to project from static loan parameters without
     incorporating actual transfer history.
- **Implementation notes:** The amortization engine is a pure function service. To support
  actual payment history, it needs an additional input: a list of actual payments made (dates
  and amounts). The engine calculates the principal after applying each actual payment, then
  projects the remaining schedule from the current principal forward. This preserves the pure
  function pattern -- the engine does not query the database; the caller provides the payment
  history.
- **Verification:** After implementation, verify that making a one-time extra payment
  (transfer) to a mortgage or loan account updates the projected payoff date, total interest,
  and months saved on the account dashboard. Verify that the balance projection chart reflects
  the extra payment.

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
  - Add columns to the savings goal model: `goal_mode` (enum: "fixed", "income_relative"),
    `income_unit` (enum: "paychecks", "months"; nullable, used only when mode is
    income_relative), `income_multiplier` (numeric; nullable).
  - When `goal_mode = 'income_relative'`, the resolved dollar target is calculated on read:
    `multiplier * net_pay_per_unit`. This is consistent with the app's "calculate on read"
    philosophy.
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

### 5.5 Payoff Calculator for All Debt Accounts

- **Source:** fixes_improvements.md (production feedback)
- **Problem:** The payoff calculator (extra payment mode and target date mode) exists only on
  the mortgage account page. Auto loan, personal loan, and student loan accounts do not have
  this feature despite sharing the same amortization engine.
- **Feature:** Extend the payoff calculator UI to all debt account types. The calculator form
  and results display should be consistent across types. The amortization engine is already
  a shared pure function service; the work is primarily UI wiring.
- **Implementation:** Each debt account detail page gets the same payoff calculator form that
  the mortgage page has:
  - **Extra payment mode:** User enters a monthly extra payment amount. The calculator shows
    the new payoff date, total interest saved, and months saved compared to the standard
    schedule.
  - **Target date mode:** User enters a desired payoff date. The calculator shows the
    required monthly payment to achieve that date.
- **Scope:** Mortgage, auto loan, personal loan, and student loan. All four types use the
  same amortization engine. Type-specific differences in parameter structures (e.g., personal
  loan and student loan may have different fields than auto loan) should be accounted for
  when passing parameters to the engine but do not affect the payoff calculator UI.
- **Dependency:** Pairs naturally with task 5.1 (debt account payment linkage). Once actual
  payments affect the account balance, the payoff calculator can show projections based on
  real payment history rather than static parameters.

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

## 8. Multi-User / Kid Accounts (Far Future)

Not actively planned. The database schema already includes `user_id` on all relevant tables.
When the time comes, the work is primarily:

- Registration UI and flow
- Ensuring all queries filter by `user_id` (audit needed)
- Role/permission model (parent vs kid account)
- Kid account restrictions (view-only? limited editing?)

This phase will be scoped when it becomes relevant.

---

## 9. Deferred Items Reference

| Item                              | Deferred From         | Notes                                                       |
| --------------------------------- | --------------------- | ----------------------------------------------------------- |
| Scenarios (named, clone, compare) | v3 Phase 7            | Indefinitely deferred; effort not worth reward              |
| Paycheck calibration              | fixes_improvements.md | Completed as section 3.10                                   |
| Fluctuating/seasonal bills        | fixes_improvements.md | Addressed by Phase 9 seasonal forecasting (section 6.1)     |
| Multi-user / kid accounts         | v2 Phase 6            | Far future; schema ready                                    |
| Checking account APY/interest     | fixes_improvements.md | User confirmed checking APY is negligible; not implementing |
| Recurrence pattern audit          | Roadmap v4, task 5.2  | Removed; section 3.2 confirmed all patterns are correct     |
| Actual paycheck value entry       | Roadmap v4, task 5.3  | Removed; superseded by paycheck calibration (section 3.10)  |

---

## 10. Change Log

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4.0     | 2026-03-24 | Post-production roadmap: added critical bug fix sprint, UX/grid overhaul phase, recurring transaction improvements phase; rescoped Phase 9 with seasonal expense forecasting (historical data entry, weighted trend-adjusted projections); rescoped Phase 10 with tiered notification system (warning/critical thresholds, in-app first, email deferred pending mail server setup); added multi-user as far-future placeholder; established priority ordering based on production usage feedback                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 4.0.1   | 2026-03-24 | Corrections: hosting updated to Arch Linux desktop with Docker/Nginx/Cloudflare Tunnel (was Proxmox); paycheck calibration feature added as section 3.10 (one-time calibration from real pay stub data, distinct task after tax bug fix); seasonal history data model updated with billing period dates (period_start_date, period_end_date, due_date) indexed by consumption period midpoint; grid layout section updated to prototype both full row headers and enhanced current layout side by side                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 4.1     | 2026-03-27 | Section 3 marked complete (all critical bug fixes resolved). Section 4 expanded with production feedback: tasks 4.11 (salary button placement), 4.12 (tooltip enhancement), 4.13 (emergency fund coverage fix), 4.14 (checking balance projection), 4.15 (auto loan parameter fixes), 4.16 (retirement date validation UX), 4.17 (retirement return rate clarity). Task 4.2 marked complete (resolved during transfer rework). Task 4.4 reworked into 4.4a/4.4b/4.4c (refactor to ID-based lookups before renaming status). Task 4.7 expanded to include retirement default rate on creation. Task 4.9 clarified to mortgage account page only. Task 4.10 expanded to include chart sizing fix. Section 5 retitled to "Debt and Account Improvements": task 5.1 expanded to cover all debt types (mortgage, auto loan, personal loan, student loan) with extra payment linkage; tasks 5.2 and 5.3 removed (resolved by sections 3.2 and 3.10); task 5.4 added (income-relative savings goals); task 5.5 added (payoff calculator for all debt accounts). Added prerequisite note on ref.account_types.category column utilization. Deferred items reference updated. |
