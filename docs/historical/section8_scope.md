> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Section 8: Visualization and Reporting Overhaul -- Scope and Design Specification

**Version:** 1.0
**Date:** April 6, 2026
**Prerequisite:** Sections 5, 5A complete. Section 6 tasks 6.1-6.4 are NOT prerequisites.
Tasks 6.5, 6.6, and 6.7 (computation engines) are pulled into this section and built
alongside their display layers.
**Scope:** Bug fix 8.0a, summary dashboard (8.1), financial calendar with annual overview
(8.2/6.6), year-end summary (8.3), budget variance analysis (6.5), spending trend detection
(6.7), CSV export for all views, `/charts` page replacement.

---

## 1. Overview

This section replaces the existing `/charts` page (all existing charts removed) with two
major additions:

1. **Summary Dashboard** (`/dashboard` or `/`) -- becomes the app's landing page. Interactive.
   Allows marking bills paid and true-up of account balances.
2. **Analytics Page** (`/analytics`, replaces `/charts`) -- tabbed container with four
   lazy-loaded sections: Calendar, Year-End Summary, Budget Variance, Spending Trends. Each
   tab includes CSV export.

Additionally, three computation engines from Section 6 are built here alongside their display
layers: budget variance analysis (6.5), annual expense calendar (6.6), and spending trend
detection (6.7).

---

## 2. Task Inventory

| Task    | Name                                | Type              | New Route | New Service |
| ------- | ----------------------------------- | ----------------- | --------- | ----------- |
| 8.0a    | X-axis date format fix              | Bug fix           | No        | No          |
| 8.1     | Summary Dashboard / Home Page       | Feature (complex) | Yes       | Yes         |
| 8.2/6.6 | Financial Calendar (month + year)  | Feature + Engine  | Yes       | Yes (6.6)   |
| 8.3     | Year-End Financial Summary          | Feature           | Yes       | Yes         |
| 6.5     | Budget Variance Analysis            | Engine + Display  | Yes       | Yes         |
| 6.7     | Spending Trend Detection            | Engine + Display  | Yes       | Yes         |
| 8.CSV   | CSV Export for all analytics views  | Feature           | Yes       | No          |

---

## 3. Architecture Decisions (Settled)

These decisions were made during the scoping process and should not be revisited during
implementation.

### 3.1 Navigation and Routing

- The summary dashboard (8.1) becomes the app's landing page (default route `/` or
  `/dashboard`).
- The budget grid remains accessible via its current route, one click from the dashboard.
- The `/charts` route is replaced by `/analytics`. All existing charts on `/charts` are
  removed (they exist elsewhere in the app).
- The nav bar item "Charts" is renamed to "Analytics."

### 3.2 Analytics Page Structure

- `/analytics` is a single route with four tabs: Calendar, Year-End Summary, Variance,
  Trends.
- Calendar is the default tab shown on page load.
- Each tab lazy-loads its content via HTMX when selected (consistent with the app's existing
  HTMX partial loading pattern).
- Each tab has its own HTMX endpoint that returns the tab's HTML partial.

### 3.3 Theming and Dark Mode

- All new views must support dark mode as a first-class citizen.
- Use the existing CSS custom property system (pulled from `chart_theme.js`).
- Chart.js charts follow the existing CSP-compliant `data-*` attribute pattern.

### 3.4 HTMX Refresh Pattern

- All dashboard sections refresh automatically via HTMX after user actions (mark paid,
  true-up balance). This follows the existing app pattern.
- No caching or snapshots. All data is live-calculated on every page load.

### 3.5 Mobile

- Not mobile-first, but must be mobile-friendly. The app is primarily desktop-based but is
  occasionally used on mobile.

---

## 4. Task Specifications

### 4.0a Chart Bug Fix: X-Axis Date Format

**Problem:** Charts that span more than the current calendar year show month/day without the
year on the x-axis.

**Fix:** Any Chart.js chart whose data range crosses a year boundary must include the year in
x-axis tick labels. Charts within a single year may omit the year. This is a Chart.js
configuration change, not a backend change.

**Scope:** This applies to all existing charts throughout the app, not just the charts page
(which is being removed). Audit all Chart.js configurations for this issue.

---

### 4.1 Summary Dashboard (8.1)

**Route:** New route, becomes the app's landing page.

**Purpose:** Single-glance overview of financial position with interactive quick actions.
Replaces the budget grid as the default landing page. The grid remains one click away.

#### 4.1.1 Dashboard Sections (Priority Order)

The dashboard displays these sections in the following order, matching the user's stated
priorities:

**Section 1: Upcoming Bills**
- Shows all unpaid transactions for the remainder of the current pay period PLUS the entire
  next pay period. This guarantees at least 14 days of lookahead regardless of where the user
  is in the pay cycle.
- Each bill shows: name, amount (estimated), due date or paycheck date.
- Interactive: user can mark a bill as paid directly from the dashboard. The "mark paid" action
  accepts an optional actual amount (consistent with the grid's behavior -- actual amount is
  optional, there is no date field).
- When a bill is marked paid via HTMX, it transitions to a "paid" visual state (struck through
  or grayed out) for the remainder of the current page session. On the next full page load
  (user navigates away and returns), paid bills no longer appear in the upcoming list.
- Bills should be ordered by due date (earliest first). Transactions without explicit due dates
  appear on their paycheck date.

**Section 2: Alerts / Needs Attention**
- Limited to actionable operational items only. No analytical insights (those live on the
  Analytics page).
- Alert types:
  - Stale anchors: accounts that have not been true-up'd in N days (N to be determined --
    Claude Code should check if there's an existing staleness threshold or recommend one).
  - Negative projected balances: any future pay period where the projected end balance for the
    default account goes negative.
  - Overdue reconciliation: the current pay period has not been reconciled (no anchor balance
    set for the current period).
- If Section 7 (Notifications) is implemented later, this section can surface notification
  system alerts. For now, compute alerts directly from existing data.

**Section 3: Current Checking Balance and Cash Runway**
- Displays the current checking balance (from the most recent anchor or calculated balance for
  the default account).
- Cash runway: "At your current spending rate, your checking balance covers approximately N
  days." Computed using a 30-day rolling average of daily spending.
- The checking balance display should allow inline true-up: the user can enter a new balance
  and it records against the current pay period for the default account. This follows the same
  flow as the grid's anchor balance entry.

**Section 4: Days Until Next Payday / Next Paycheck Amount**
- Days until the next paycheck (computed from the pay period schedule).
- Next paycheck projected net amount (from the paycheck calculator).

**Section 5: Savings Goal Progress**
- Read-only progress indicators for each active savings goal.
- Shows: goal name, current balance, target amount, percentage complete, progress bar.
- Links to the savings dashboard for detail.

**Section 6: Debt Paydown Progress / DTI Snapshot**
- Read-only summary.
- Shows: total outstanding debt, DTI ratio (if Section 5 task 5.12 is complete -- check if
  this service exists), and a list of debt accounts with current balance and payoff date.
- Links to individual loan dashboards for detail.

**Section 7: Spending This Period vs. Last Period**
- Two numbers: total spending (paid transactions) in the current pay period so far, vs. total
  spending in the prior pay period.
- Delta shown with color coding (green if spending is lower, red if higher).
- Read-only.

#### 4.1.2 Dashboard Account Scope

- The primary dashboard view is from the default account's (usually checking) perspective.
- Other account balances appear in read-only summaries (Sections 5 and 6).
- The true-up action (Section 3) operates on the default account. If a mechanism to select a
  different account for true-up is straightforward to add, include it. Otherwise, the default
  account is sufficient for v1.

#### 4.1.3 Service Layer

- Create a new `dashboard_service.py` following the established pattern: service takes
  `user_id`, returns plain data, no Flask imports.
- The service aggregates data from existing services: balance calculator, paycheck calculator,
  recurrence engine, savings goal service/savings dashboard service, and any debt summary
  service from Section 5.
- Do NOT duplicate calculation logic. Call existing services.

---

### 4.2 Financial Calendar (8.2 + 6.6)

**Route:** A tab on the `/analytics` page (default tab). HTMX lazy-loaded partial.

**Purpose:** Month-by-month and year-overview calendar for financial planning. Forward-looking
by default.

#### 4.2.1 Month Detail View (8.2)

- Traditional 7-column Sun-Sat calendar grid showing one month at a time.
- Forward/back navigation to move between months.
- Default: current month.
- Each day cell shows:
  - Small markers/dots for transactions occurring that day. Not full transaction detail.
  - Paycheck days are visually highlighted (distinct color or icon).
  - Large or infrequent transactions are visually distinguished from regular ones (per the
    threshold rules in 4.2.3).
- Interaction: hovering or clicking a day with transactions shows a tooltip or popover with
  transaction names and amounts.
- Transaction date assignment rules:
  - Transactions with an explicit due date appear on that due date.
  - Transactions without an explicit due date (e.g., "groceries every paycheck") appear on
    their paycheck date.
- Primary focus: checking account inflows and outflows. If straightforward, add an account
  selector to let the user switch accounts. Otherwise, checking only for v1.
- Historical months show paid/actual transactions. Future months show projected transactions.
- No what-if capability. Read-only.

#### 4.2.2 Year Overview (6.6)

- 4x3 grid (12 months).
- Each month cell shows:
  - Total income for the month.
  - Total expenses for the month.
  - Net (income minus expenses).
  - Projected checking balance at month-end.
- 3rd-paycheck months: marked visually (two months per year for biweekly pay). The 3rd
  paycheck income is included in the monthly totals like any other paycheck -- no special
  calculation, just a visual marker noting that this is a 3rd-paycheck month.
- Color-coding months by expense load (green/yellow/red relative to income) would add
  scannability.
- Large or infrequent transactions are highlighted per the threshold rules in 4.2.3.

#### 4.2.3 Large/Infrequent Transaction Highlighting

Two mechanisms, both active simultaneously:
1. Any transaction with a recurrence frequency less frequent than monthly (quarterly,
   semi-annual, annual) is highlighted.
2. Any transaction above a user-configurable fixed dollar threshold is highlighted. The
   threshold needs a default value and a place in settings to configure it. Claude Code should
   determine the best place for this setting (existing settings page, a new field on
   `system.notification_settings` or a new user preferences table, etc.).

#### 4.2.4 Navigation Between Modes

- Clicking a month cell in the year overview navigates to the month detail view for that
  month.
- The month detail view has a "zoom out to year" button/link that returns to the year
  overview.

#### 4.2.5 Engine (6.6 -- Annual Expense Calendar)

- New service function that queries generated transactions for the next 12 months, groups by
  calendar month, and computes per-month totals (income, expenses, net).
- Also computes projected checking balance at month-end for each month.
- Identifies 3rd-paycheck months.
- Identifies large/infrequent transactions per the threshold rules.
- The recurrence engine and balance calculator already compute the underlying data. This
  service aggregates and reshapes it.

---

### 4.3 Year-End Financial Summary (8.3)

**Route:** A tab on the `/analytics` page. HTMX lazy-loaded partial.

**Purpose:** Consolidated annual financial report. Primary use case is tax preparation.

#### 4.3.1 Year Selector

- Dropdown or control to select the calendar year. Default: current year.
- Single-year view only (no year-over-year comparison in v1).

#### 4.3.2 Income and Tax Section (Tax Prep Priority)

- Organized as a clearly labeled list of annual totals.
- Line items mirror what appears on a W-2. Each line shows a common name and may include the
  W-2 box reference in parentheses for users who find that helpful.
- Required line items (data source: paycheck calculator aggregated across all pay periods in
  the calendar year):
  - Gross wages
  - Federal income tax withheld
  - State income tax withheld
  - Social Security tax (employee share)
  - Medicare tax (employee share)
  - 401(k) contributions (pre-tax)
  - Roth 401(k) contributions (if applicable)
  - HSA contributions (if applicable)
  - Other pre-tax deductions (itemized by type)
  - Total post-tax deductions
  - Net pay (total)
- Mortgage interest total: sum of the interest portion of each mortgage payment for the year.
  The amortization logic from Section 5 provides this data. Include this as a separate line
  item labeled for Schedule A relevance.

#### 4.3.3 Spending by Category

- Hierarchical: top-level category groups with expandable drill-down to individual items.
- Both group totals and item totals shown.
- Data source: paid transactions with actual amounts, aggregated by category group and item
  for the calendar year.

#### 4.3.4 Transfers Summary

- Total transfers by destination account (mortgage payments, savings contributions, debt
  payments) for the year.

#### 4.3.5 Net Worth Change

- Month-by-month net worth trend line (Chart.js) for the selected year.
- Computed at 12 points: end of each month, sum of all account balances.
- Also displays Jan 1 vs. Dec 31 net worth with the delta.

#### 4.3.6 Debt Progress

- Per debt account: starting balance (Jan 1), ending balance (Dec 31), total principal paid
  during the year.
- Data source: loan account balances and payment history.

#### 4.3.7 Savings Progress

- Per savings/investment account: starting balance (Jan 1), ending balance (Dec 31), total
  contributions during the year.

#### 4.3.8 Service Layer

- New `year_end_summary_service.py`.
- Aggregates data from: paycheck calculator (income/tax/deductions), paid transactions
  (spending by category), transfers (by destination), account balances (net worth at 12
  monthly points), amortization service (mortgage interest), loan/savings account data.
- Returns a structured data object. No Flask imports.

---

### 4.4 Budget Variance Analysis (6.5)

**Route:** A tab on the `/analytics` page. HTMX lazy-loaded partial.

**Purpose:** Compare budgeted (estimated) amounts to actual (paid) amounts. Identify
over/under budget categories. Drill-down to transaction level.

#### 4.4.1 Time Window Toggle

- User can switch between: pay period, monthly, and annual views.
- Default: current pay period (aligns with the grid workflow).
- For the pay-period view: a selector to choose which pay period.
- For the monthly view: a selector to choose which month.
- For the annual view: a selector to choose which year.

#### 4.4.2 Transaction Attribution for Monthly View

- Transactions with an explicit due date are attributed to the month of their due date.
- Transactions without an explicit due date are attributed to the month of their paycheck
  date (the pay period start date).
- This is consistent with the calendar's date assignment rules (4.2.1).

#### 4.4.3 Display: Category Level

- A bar chart (Chart.js) showing estimated vs. actual amounts side by side per category.
- Also a tabular view with columns: category name, estimated total, actual total, variance
  (dollar), variance (percentage). Conditional color coding: red for over-budget, green for
  under-budget.
- Categories are expandable to drill down into individual transactions.

#### 4.4.4 Display: Transaction Level (Drill-Down)

- When a user expands a category, show individual transactions within that category for the
  selected time window.
- Each transaction shows: name, estimated amount, actual amount (if paid), variance.
- Checkbox/toggle: "Show only variances" vs. "Show all transactions." Default: show all (to
  provide context like "electric was $50 over but water was $10 under, net overage is $40").

#### 4.4.5 Engine (6.5 -- Budget Variance Analysis)

- New service function: `budget_variance_service.py`.
- Input: user_id, time window type (pay_period | month | year), time window identifier
  (period_id | month/year | year).
- Queries transactions in the time window. Groups by category group and category item.
- For each group and item: computes sum of estimated amounts, sum of actual amounts (using
  actual where status is paid, estimated where still projected), variance.
- Returns structured data: list of category groups, each with items, each with estimated,
  actual, variance, and child transactions.

---

### 4.5 Spending Trend Detection (6.7)

**Route:** A tab on the `/analytics` page. HTMX lazy-loaded partial.

**Purpose:** Detect and display per-category spending trends over rolling windows. Flag
lifestyle inflation or spending decreases.

#### 4.5.1 Trend Computation

- Compute per-category-item spending totals over rolling windows.
- Window logic: use 3-month window until 6 months of data exist, then switch to 6-month
  rolling window.
- Trend slope: linear regression (same approach as the seasonal forecast engine's trend
  component if that exists, otherwise a simple least-squares fit over the window).
- Compute at the category item level. Aggregate to category group level using weighted
  average percentage change across items in the group.

#### 4.5.2 Alert Threshold

- Default: 10% change over the window period.
- Per-category configurable. Store the threshold alongside category data or in a new
  settings structure. Claude Code should determine the best storage location.
- Flag both increases and decreases.

#### 4.5.3 Display

- Two ranked lists: "Top 5 Trending Up" and "Top 5 Trending Down."
- Each entry shows: category item name (with group name for context), percentage change,
  absolute dollar change per period, and direction indicator (arrow or color).
- User can toggle between viewing increases and decreases.
- Drill-down: clicking a category item shows the group-level trend (aggregate of all items
  in that group).
- If fewer than 3 months of data exist, show a dismissable banner: "Trends are preliminary.
  At least 3 months of transaction history is needed for reliable trend detection."

#### 4.5.4 Engine (6.7 -- Spending Trend Detection)

- New service function: `spending_trend_service.py`.
- Input: user_id.
- Queries paid transactions grouped by category item and rolling time window.
- Computes trend slope per item via linear regression.
- Aggregates to group level via weighted average percentage.
- Returns: list of items with trend direction, percentage change, absolute change, flagged
  status (above/below threshold). Also returns group-level aggregates.

---

### 4.6 CSV Export

**Scope:** Every tab on the `/analytics` page gets an "Export CSV" button.

#### 4.6.1 Behavior

- The export respects the user's current filters, time window, and drill-down level.
- The export includes all data at the current view level, not just visible rows. Example: if
  the variance tab is showing category-level totals (user hasn't drilled into any category),
  the CSV includes all categories with their transaction-level detail underneath.
- If the user is viewing a single drilled-down category, the CSV includes only that
  category's transactions.

#### 4.6.2 Implementation

- Each tab's HTMX endpoint accepts a `format=csv` query parameter (or a separate route).
- When `format=csv`, the endpoint returns a CSV file response with `Content-Disposition:
  attachment` instead of HTML.
- The service layer returns the same data structure regardless of output format. The route
  layer handles rendering as HTML or CSV.

#### 4.6.3 CSV Content Per Tab

- **Calendar:** Transaction list for the selected month or year, with columns: date, name,
  category, amount, status, account.
- **Year-End Summary:** Multiple sections as labeled CSV blocks (income/tax lines,
  spending by category with group/item hierarchy, transfers, net worth monthly values, debt
  progress, savings progress).
- **Variance:** Category group, category item, estimated, actual, variance dollar, variance
  percent. When drilled down: includes individual transaction rows beneath each category.
- **Trends:** Category group, category item, period average, trend direction, percentage
  change, absolute change, threshold, flagged status.

---

## 5. What Is NOT In Scope

**Disclaimer:** These items are out of scope unless Claude Code determines during the codebase
analysis that any of them would be trivial to add. If an out-of-scope item can be implemented
with minimal effort and no architectural risk, Claude Code has the freedom to include it in the
implementation plan.

- Section 6 tasks 6.1-6.4 (seasonal forecasting, smart estimates, expense inflation,
  deduction inflation). These remain as a separate future phase.
- Section 7 (Notifications). If notifications are implemented later, the dashboard alerts
  section (4.1.1, Section 2) can be extended to surface them.
- PDF export (8A.2). Deferred.
- Full data backup/restore (8A.3). Handled elsewhere.
- Year-over-year comparison in the year-end summary. Single-year view only for v1.
- What-if capability on the calendar.
- Mobile-first design. Views must be mobile-friendly but are designed for desktop.

---

## 6. Dependencies and Existing Services

The implementation plan should identify the specific files, functions, and line counts for each
of these. They are listed here for orientation.

| Service / Component              | Expected Location                          | Used By         |
| -------------------------------- | ------------------------------------------ | --------------- |
| Balance calculator               | `app/services/balance_calculator.py`       | 8.1, 8.2, 8.3  |
| Paycheck calculator              | `app/services/paycheck_calculator.py`      | 8.1, 8.3       |
| Recurrence engine                | `app/services/recurrence_engine.py`        | 8.2/6.6        |
| Savings dashboard service        | `app/services/savings_dashboard_service.py`| 8.1, 8.3       |
| Retirement dashboard service     | `app/services/retirement_dashboard_service.py` | 8.3         |
| Amortization / loan services     | `app/services/` (Claude Code to identify)  | 8.3            |
| Chart data service               | `app/services/chart_data_service.py`       | 8.0a (audit)   |
| Chart theme JS                   | `app/static/js/chart_theme.js`             | All new charts  |
| Grid route (for mark-paid pattern)| `app/routes/grid.py`                      | 8.1 (reference) |
| Existing chart configurations    | Templates and JS (Claude Code to audit)    | 8.0a           |
| Debt summary / DTI service       | (Claude Code to check if 5.12 exists)      | 8.1            |

---

## 7. Implementation Sequencing Guidance

The following ordering is recommended but Claude Code should adjust based on actual code
dependencies discovered during the file audit.

**Phase 1: Bug Fix and Infrastructure**
- 8.0a: Audit and fix x-axis date formatting across all charts in the app.
- Set up the `/analytics` route with tab structure and lazy-loading HTMX partials (empty
  placeholders).
- Set up the dashboard route (empty placeholder).

**Phase 2: Computation Engines**
- 6.6 engine: annual expense calendar service (month grouping, totals, 3rd-paycheck
  detection, large transaction flagging).
- 6.5 engine: budget variance service.
- 6.7 engine: spending trend detection service.
- Each engine gets its own service file with full test coverage before any display work.

**Phase 3: Dashboard (8.1)**
- Dashboard service aggregation layer.
- Dashboard template with all 7 sections.
- Interactive elements: mark-as-paid, true-up balance.
- HTMX refresh wiring.
- Update the app's default route to point to the dashboard.

**Phase 4: Analytics Display Layers**
- Calendar month detail view (8.2) with tooltips and markers.
- Calendar year overview (6.6 display) with drill-through to month.
- Year-end summary (8.3) with all subsections.
- Variance display (6.5) with chart and drill-down table.
- Trend display (6.7) with ranked lists.

**Phase 5: CSV Export and Cleanup**
- CSV export endpoints for all four analytics tabs.
- Remove old `/charts` route and templates.
- Update nav bar: rename "Charts" to "Analytics", add "Dashboard" if needed.
- Dark mode verification pass on all new views.

---

## 8. Open Questions for Claude Code

These items require reading the actual codebase to resolve. The implementation plan should
address each one.

1. What is the current default route (`/`)? What renders when a user hits the app root?
2. Does a debt summary / DTI service exist from task 5.12? If not, the dashboard Section 6
   should degrade gracefully.
3. What is the existing pattern for the grid's "mark as paid" HTMX interaction? The dashboard
   must replicate this pattern.
4. What is the existing pattern for the grid's anchor balance / true-up interaction?
5. Where is the large transaction threshold best stored? Is there an existing user preferences
   or settings table that can accommodate a new field?
6. Where is the spending trend alert threshold per category best stored?
7. What Chart.js configurations exist across the app that need the 8.0a x-axis fix?
8. What is the structure of generated transactions -- do they have a due_date field separate
   from pay_period assignment?
9. What is the current nav bar structure (template file, menu items)?
10. What is the full list of files and templates under the current `/charts` route that will
    be removed?