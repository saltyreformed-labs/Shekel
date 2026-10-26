> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Budget App -- Project Roadmap v4

**Version:** 4.0
**Date:** March 24, 2026
**Parent Documents:** project_requirements_v2.md, project_requirements_v3_addendum.md

---

## 1. Current State Summary

### Completed Phases

| Source Document | Phase | Status |
|---|---|---|
| v2 Phase 1 | Replace the Spreadsheet | Done |
| v2 Phase 2 | Paycheck Calculator | Done |
| v2 Phase 4 | Savings and Accounts | Done |
| v2 Phase 5 | Visualization | Done |
| v2 Phase 6 | Hardening and Ops | Done |
| v3 Phase 1 (was v2 P1) | Replace the Spreadsheet | Done |
| v3 Phase 2 (was v2 P2) | Paycheck Calculator | Done |
| v3 Phase 3 | HYSA and Accounts Reorganization | Done |
| v3 Phase 4 | Debt Accounts | Done |
| v3 Phase 5 | Investments and Retirement | Done |
| v3 Phase 6 | Visualization | Done |
| v3 Phase 8 | Hardening and Ops | Done |

### Deferred Indefinitely

| Source Document | Phase | Reason |
|---|---|---|
| v3 Phase 7 | Scenarios | Effort not worth the reward at this time |

### Production Status

The app moved to production on March 23, 2026. It runs as a Docker container on an Arch
Linux desktop, with internal access via Nginx and a DNS override, and external access via a
Cloudflare Tunnel. The primary focus is now stabilization, daily-use polish, and incremental
feature development.

---

## 2. Remaining Work -- Phase Ordering

The following phases are ordered by priority based on current needs. Bug fixes come first to
stabilize the production deployment, followed by UX improvements to reduce daily friction,
then new features.

| Priority | Phase | Summary |
|---|---|---|
| 1 | Critical Bug Fixes | Data correctness and broken workflow fixes from fixes_improvements.md |
| 1A | Transfer Architecture Rework | Shadow transactions for transfers. See `docs/transfer_rework_design.md`. Phase I (shadow architecture) complete. Phase II (grid subtotals/footer) pending. |
| 2 | UX/Grid Overhaul | Focused sprint to address daily-use friction in the budget grid and related views |
| 3 | Recurring Transaction Improvements | Workflow conveniences: mortgage auto-payment, other recurring transaction quality-of-life fixes |
| 4 | Phase 9: Smart Features | Seasonal expense forecasting, smart estimates, expense inflation, deduction inflation |
| 5 | Phase 10: Notifications | In-app alerts (low balance, large expenses, payday reminders), email delivery added later |
| 6 | Multi-user (far future) | Kid accounts, registration flow; not actively planned |

---

## 3. Critical Bug Fixes (Priority 1)

**Goal:** Fix data correctness issues and broken workflows before investing in new features.
These are bugs that produce wrong numbers or block normal operations.

### 3.1 Tax Calculation on Gross Pay Instead of Taxable Income

- **Severity:** Critical (affects every paycheck calculation)
- **Problem:** Taxes are being calculated on gross pay rather than taxable income. Pre-tax
  deductions (401(k), HSA, etc.) should reduce the taxable base before federal and state
  income tax are applied.
- **Impact:** Every projected paycheck in the grid is wrong by the amount of tax on pre-tax
  deductions.

### 3.2 Recurrence Rule: Every 6 Months Calculates Incorrectly

- **Severity:** Critical (data correctness)
- **Problem:** The "every 6 months" recurrence pattern appears to calculate 6 pay periods
  (approximately 3 months) instead of 6 calendar months. Other recurrence rules (every N
  periods, monthly, annual) should also be audited for correctness.
- **Impact:** Semi-annual expenses land in the wrong pay periods.

### 3.3 Net Biweekly Mismatch Between Salary Profile and Grid

- **Severity:** Critical (confusing and potentially wrong data)
- **Problem:** The net biweekly amount displayed on the salary profile page does not match the
  biweekly salary shown on the budget grid page. This could be a display issue or a
  calculation divergence between the two code paths.
- **Impact:** User cannot trust either number without manual verification.

### 3.4 Raises Require Page Refresh Before Adding a Second

- **Severity:** High (broken workflow)
- **Problem:** After adding a raise on the /salary page, the user must refresh the page before
  adding another raise. The HTMX response likely does not re-render the form or list correctly.
- **Impact:** Slows down salary profile setup; confusing behavior.

### 3.5 Cannot Edit Raises and Deductions

- **Severity:** High (missing CRUD operation)
- **Problem:** There is no UI to edit existing raises or deductions. The user can only add and
  (presumably) delete, but cannot modify an existing entry.
- **Impact:** User must delete and re-create to correct a mistake.

### 3.6 Escrow: Cannot Add Amount When Inflation Rate Is Present

- **Severity:** High (broken workflow)
- **Problem:** Adding an escrow component fails when an inflation rate is included. Likely a
  validation or form handling issue.
- **Impact:** Blocks realistic escrow modeling (most escrow components inflate annually).

### 3.7 Escrow: Hard Refresh Required After Adding Component

- **Severity:** Medium (UX bug)
- **Problem:** After adding an escrow component, the total monthly payment does not update
  until the page is manually refreshed. The HTMX response does not trigger recalculation of
  the payment summary.
- **Impact:** Confusing; user thinks the escrow was not added.

### 3.8 Pension Date Validation Missing

- **Severity:** Medium (data integrity)
- **Problem:** The pension form does not validate that Earliest Retirement Date and Planned
  Retirement Date are after the Hire Date and after today's date.
- **Impact:** Allows nonsensical pension profiles.

### 3.9 Stale Retirement Settings Message

- **Severity:** Low (cosmetic)
- **Problem:** A message stating that retirement settings have moved to Settings > Retirement
  still displays and needs to be removed.
- **Impact:** Confusing navigation cue that is no longer accurate.

### 3.10 Paycheck Calibration (Distinct Task -- After Tax Bug Fix)

- **Severity:** High (accuracy improvement)
- **Dependency:** Complete section 3.1 (tax calculation fix) first. This feature builds on a
  correct tax calculation foundation.
- **Problem:** The paycheck calculator uses bracket-based tax estimates and user-configured
  deduction percentages/amounts. These often do not match the actual withholding on a real pay
  stub, and the user currently has no way to calibrate the calculator against reality without
  manually computing and entering effective rates.
- **Feature:** A one-time calibration workflow where the user enters actual line-item values
  from a real pay stub (federal tax withheld, state tax withheld, Social Security, Medicare,
  each deduction amount). The app back-calculates the effective rates (e.g., actual federal
  withholding / taxable income = effective federal rate) and stores them as overrides.
- **Scope:** One-time calibration. The user enters actuals once and the derived rates are
  locked in for future projections. The user can re-calibrate at any time (e.g., after a raise
  or tax year change) by entering a new pay stub.
- **Data model considerations:**
  - Add effective rate override columns to the salary profile or a new
    `salary.calibration_overrides` table.
  - Store both the raw actual amounts (for audit trail) and the derived rates.
  - When overrides exist, the paycheck calculator uses them instead of computing from
    brackets.
- **UI:** A "Calibrate from Pay Stub" button on the salary profile page. Opens a form with
  fields for each withholding and deduction line. On submit, the app calculates and displays
  the derived rates with a confirmation step before saving.
- **Impact:** Directly addresses the net biweekly mismatch (section 3.3) by grounding
  projections in real withholding data rather than estimated brackets.

---

## 4. UX/Grid Overhaul (Priority 2)

**Goal:** A focused sprint to improve the daily-use experience of the budget grid and related
views. Every fix in this phase should make the payday reconciliation workflow faster or clearer.

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

### 4.2 Footer Condensation

- **Problem:** The footer consumes excessive screen real estate.
- **Approach:** Condense the footer content or convert it to a fixed/sticky element at the
  bottom of the viewport. Evaluate whether the footer information (likely balance summaries)
  could be collapsed into a single summary row or moved to the grid header area.

### 4.3 Pay Period Date Format Cleanup

- **Problem:** Pay period column headers display both MM/DD and MM/DD/YY, which is redundant
  and wastes horizontal space.
- **Fix:** Display a single date format. Use MM/DD within the current year and MM/DD/YY (or
  MMM DD) only when the period crosses into a different year from the current view.

### 4.4 Terminology: "Done" to "Paid" / "Received"

- **Problem:** The status "Done" is generic. For expenses, "Paid" is clearer. For income,
  "Received" already exists.
- **Approach:** This is the recommended implementation:
  - Add a `display_label` column to `ref.statuses` (or a `context` column indicating
    expense vs income applicability).
  - The status ID remains constant in the database. Only the display text changes per context.
  - Update all templates, test assertions, and documentation that reference "Done" as a
    display string.
- **Ripple effects:** Tests, status badges in the grid, any filter/dropdown that shows status
  names, and documentation.

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
  discoverable.
- **Approach:** When creating a new account of a type that requires parameters (HYSA, mortgage,
  investment, retirement), prompt the user to configure those parameters as part of the
  creation flow. Options:
  - Inline the parameter fields in the account creation form, shown/hidden based on
    account type selection.
  - Redirect to the parameter configuration page immediately after account creation with a
    setup wizard banner.
  - Show a "Setup required" badge on the account card until parameters are configured.

### 4.8 Mortgage Parameter Page Flow

- **Problem:** Mortgage accounts redirect to a separate page for setting parameters. This may
  be better handled as inline fields that appear when the mortgage account type is selected
  during creation.
- **Approach:** Evaluate as part of 4.7 above. The mortgage parameter form could become a
  section within the account creation/edit form, conditionally displayed based on account type.

### 4.9 Chart: Balance Over Time Contrast

- **Problem:** The "standard payments" line on the mortgage Balance Over Time chart is
  difficult to see due to low contrast.
- **Fix:** Increase line weight, change color, or add a distinct dash pattern.

### 4.10 Chart: Balance Over Time Time Frame Control

- **Problem:** The Balance Over Time chart defaults to a one-year view, which shows flat lines
  for long-duration accounts like mortgages and retirement.
- **Fix:** Add time frame controls (1 year, 5 years, 10 years, full term). Default should be
  intelligent based on account type (e.g., full term for mortgages, to-retirement for
  retirement accounts).

---

## 5. Recurring Transaction Improvements (Priority 3)

**Goal:** Small, focused phase to improve recurring transaction workflows. Kept separate from
Phase 9 smart features because these are workflow/convenience fixes, not forecasting logic.

### 5.1 Mortgage Auto-Payment Creation

- **Problem:** After creating a mortgage account with a monthly payment amount, there is no
  option to automatically create a recurring transfer from checking (or savings) to the
  mortgage account.
- **Approach:** After a mortgage or auto loan account is created and parameters are saved, offer
  the user a prompt: "Create a recurring monthly transfer of $X from [source account] to this
  account?" This creates a transfer record and a corresponding recurrence rule.
- **Applies to:** Mortgage and auto loan accounts.

### 5.2 Audit Other Recurrence Patterns

- **Dependency:** The critical bug fix for the "every 6 months" rule (section 3.2) should be
  completed first. This follow-up task audits all remaining recurrence patterns
  (every_n_periods, monthly, monthly_first, annual) to confirm they calculate correctly.
- **Deliverable:** A test suite that exercises every recurrence pattern with edge cases (month
  boundaries, year transitions, leap years).

### 5.3 Actual Paycheck Value Entry (Evaluate)

- **Note:** This item has been superseded by the Paycheck Calibration feature (section 3.10).
  The user can already enter an actual net pay amount in the grid. The calibration feature
  addresses the deeper need of deriving accurate tax and deduction rates from real pay stub
  data. This section is retained only as a cross-reference.

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
  5. The final forecast = weighted_average + (trend_slope * years_forward).
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
  template where status is "done"/"paid" and an actual amount exists. Return the average.
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

| Item | Deferred From | Notes |
|---|---|---|
| Scenarios (named, clone, compare) | v3 Phase 7 | Indefinitely deferred; effort not worth reward |
| Paycheck calibration | fixes_improvements.md | Added to Critical Bug Fixes as section 3.10 (distinct task after tax fix) |
| Fluctuating/seasonal bills | fixes_improvements.md | Addressed by Phase 9 seasonal forecasting |
| Multi-user / kid accounts | v2 Phase 6 | Far future; schema ready |

---

## 10. Change Log

| Version | Date | Changes |
|---|---|---|
| 4.0 | 2026-03-24 | Post-production roadmap: added critical bug fix sprint, UX/grid overhaul phase, recurring transaction improvements phase; rescoped Phase 9 with seasonal expense forecasting (historical data entry, weighted trend-adjusted projections); rescoped Phase 10 with tiered notification system (warning/critical thresholds, in-app first, email deferred pending mail server setup); added multi-user as far-future placeholder; established priority ordering based on production usage feedback |
| 4.0.1 | 2026-03-24 | Corrections: hosting updated to Arch Linux desktop with Docker/Nginx/Cloudflare Tunnel (was Proxmox); paycheck calibration feature added as section 3.10 (one-time calibration from real pay stub data, distinct task after tax bug fix); seasonal history data model updated with billing period dates (period_start_date, period_end_date, due_date) indexed by consumption period midpoint; grid layout section updated to prototype both full row headers and enhanced current layout side by side |
