> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Budget App -- Requirements & Architecture Document

**Version:** 2.0 **Date:** February 20, 2026 **Stack:** Flask · Jinja2 · HTMX · Bootstrap 5 ·
PostgreSQL

---

## 1. Project Overview

A personal budget application that replaces a biweekly-paycheck-based spreadsheet maintained for ~7
years. The app organizes finances around **pay periods** rather than calendar months, mapping every
expense to a specific paycheck and projecting balances forward over a ~2-year horizon.

### Core Philosophy

- **Paycheck-centric:** Every dollar of income and every expense belongs to a specific pay period.
- **Projection-forward:** The app always shows the long-term ripple effect of any change.
- **Actuals + budget hybrid:** Users budget estimates, record actuals, mark line items "done," and
  the remainder flows naturally.
- **Replace the spreadsheet first:** The app must handle the core payday reconciliation workflow
  before adding advanced features.

### What This App Is Not

The following are explicitly out of scope to prevent scope creep:

- **Bank account syncing** -- no Plaid, no OFX import, no screen scraping.
- **Receipt scanning or OCR** -- amounts are entered manually.
- **Multi-currency** -- USD only.
- **Shared household budgets with separate logins** -- single-user for now; kid accounts are a future
  stretch goal.
- **Spreadsheet import** -- the user starts fresh from the current period forward.
- **Mobile app** -- web-only; mobile-responsive layout is a later enhancement.
- **Debt amortization schedules** -- credit card is tracked as an expense line item, not a debt
  module.

---

## 2. Feature Priorities (MVP → Future)

| Phase                                 | Features                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Phase 1 -- Replace the Spreadsheet** | Session-based auth (one seeded user), pay period generation, transaction CRUD, flat categories, budget grid with HTMX inline editing, balance roll-forward (calculated on read), anchor balance true-up, status workflow (projected → done → credit), carry-forward for unpaid items, recurrence engine with templates and override handling, grid date range controls |
| **Phase 2 -- Paycheck Calculator**     | Salary profiles (multi-income ready), raises (merit, COLA, custom), paycheck deductions (pre-tax, post-tax, with deductions_per_year), 3rd paycheck detection, tax calculator (federal brackets, state flat rate, FICA with wage base cap), paycheck breakdown view, salary projection view                                                                            |
| **Phase 3 -- Scenarios**               | Named scenarios, clone from baseline (deep copy), scenario-scoped transactions and salary profiles, side-by-side comparison view, balance diff highlighting                                                                                                                                                                                                            |
| **Phase 4 -- Savings & Accounts**      | Savings account balance tracking, transfers (checking ↔ savings), savings goals (target amount, target date, auto-calculated contributions), accounts dashboard                                                                                                                                                                                                        |
| **Phase 5 -- Visualization**           | Balance over time (Chart.js), spending by category, budget vs. actuals, scenario comparison overlay, net pay trajectory                                                                                                                                                                                                                                                |
| **Phase 6 -- Hardening & Ops**         | Audit logging (PostgreSQL triggers), structured request logging, automated pg_dump backups, MFA/TOTP, export to CSV, mobile-responsive layout, registration flow for kid accounts                                                                                                                                                                                      |
| **Phase 7 -- Smart Features**          | Smart estimates (rolling average of actuals), expense inflation (global + per-template rates), deduction inflation at open enrollment                                                                                                                                                                                                                                  |
| **Phase 8 -- Notifications**           | In-app alerts for large expenses, low projected balances, savings milestones; optional email notifications                                                                                                                                                                                                                                                             |

---

## 3. Payday Workflow (Primary Use Case)

This is the core interaction loop the app must support. Every design decision should be measured
against: **does this make the payday workflow faster than the spreadsheet?**

### Step-by-Step Workflow

1. **Open the app.** The grid loads with the current pay period as the leftmost column, future
   periods extending to the right.
2. **True-up the checking balance.** The checking balance is displayed prominently at the top of the
   grid. Click it, type the real balance from the bank, press Enter. All projections recalculate
   instantly.
3. **Mark paycheck as received.** Click the paycheck row in the current period, set status to
   "received," enter the actual net amount if it differs from the estimate.
4. **Carry forward unpaid items.** If the previous period has items still in "projected" status,
   click the "Carry Forward Unpaid" button on that period. All projected items move to the current
   period in one action.
5. **Mark cleared expenses as done.** For each expense that has posted to the bank account, click
   the cell and set status to "done." Enter the actual amount if it differs from the estimate.
6. **Mark credit card expenses.** If an expense was charged to the credit card instead of checking
   (cash flow timing), set its status to "credit." The app automatically creates a corresponding
   payback expense in the next pay period.
7. **Check projections.** Scan the projected end balance across upcoming periods. If any period
   shows negative or dangerously low, adjust -- move an expense to a later period, or decide to use
   the credit card.
8. **Close.** Everything saves automatically on each edit. No save button needed.

### Frequency

The user performs this workflow every payday (biweekly) and checks the app 1-2 additional times per
week to update the true-up balance and mark newly cleared expenses.

---

## 4. Detailed Requirements

### 4.1 Pay Periods

- The user provides a **start date** (a known payday) and a **cadence** (biweekly, defaulting to
  every 14 days).
- The app **auto-generates pay periods** forward for a configurable horizon (default: 2 years, ~52
  periods).
- Each pay period is defined by a **start date** (payday) and an **end date** (day before next
  payday).
- The user can regenerate or extend the horizon at any time.

### 4.2 Income

- Each income entry belongs to a specific pay period.
- Income types include:
  - **Recurring salary** -- auto-generated per recurrence rule (e.g., every pay period). In Phase 1,
    the net amount is entered manually. In Phase 2, the paycheck calculator computes it.
  - **Recurring other** -- e.g., phone stipend (first paycheck of month), with its own recurrence
    rule.
  - **One-time income** -- e.g., tax return, other windfalls. Assigned to a specific period.
  - **Transfer from savings** -- modeled as income to checking (see §4.7).
- Each income entry has: **name, category, estimated amount, actual amount (nullable), status**
  (projected / received).

### 4.3 Expenses

- Each expense entry belongs to a specific pay period.
- Expense categories use a **flat two-level structure**: a top-level **group** and a specific
  **name**.
  - Examples: `Auto: Car Payment`, `Home: Electricity`, `Family: Kayla Spending Money`
  - Stored as two columns (`group` and `name`) on the category model -- no self-referencing hierarchy
    needed.
  - If a third level is needed later, the model can be migrated to an adjacency list.
- Each expense entry has:
  - **Name** and **category**
  - **Estimated amount** (the budgeted figure)
  - **Actual amount** (nullable -- filled in when the real cost is known)
  - **Status:** `projected` → `done` | `credit` | `received` (for income)
  - **Remainder behavior:** When an item is marked `done`, the balance calculator uses the actual
    amount instead of the estimate. Any difference (e.g., budgeted $500, spent $487) naturally stays
    in the checking balance -- no explicit reallocation needed. The grid displays both amounts so the
    user can see the savings at a glance.

### 4.4 Transaction Statuses

| Status      | Applies to        | Meaning                                      | Balance effect                                                                          |
| ----------- | ----------------- | -------------------------------------------- | --------------------------------------------------------------------------------------- |
| `projected` | Income & expenses | Expected but not yet occurred                | Uses `estimated_amount`                                                                 |
| `received`  | Income only       | Paycheck or income deposited                 | Uses `actual_amount` (falls back to estimated)                                          |
| `done`      | Expenses only     | Expense paid from checking                   | Uses `actual_amount` (falls back to estimated)                                          |
| `credit`    | Expenses only     | Expense charged to credit card, not checking | **Excluded from checking balance**; auto-generates a payback expense in the next period |

### 4.5 Credit Card Workflow

The credit card is not tracked as a separate account -- it's a cash flow timing tool modeled as a
status and a payback mechanism.

**When an expense is marked `credit`:**

1. The expense's status changes to `credit`. It is excluded from the checking balance calculation
   for its pay period (the money didn't come out of checking).
2. The app auto-generates a new expense in the **next pay period** with:
   - **Name:** `CC Payback: {original expense name}`
   - **Category:** `Credit Card: Payback` (or the user's configured credit card category)
   - **Estimated amount:** same as the credited expense's amount
   - **Status:** `projected`
   - **Linked to the original** via a `credit_payback_for_id` foreign key (so the app can show the
     relationship and prevent orphaned paybacks)
3. The user can edit the payback amount or move it to a different period if needed.
4. If the user un-marks the original expense (changes status back to `projected` or `done`), the
   auto-generated payback is deleted.

**Why not a separate credit card account:** The user's actual workflow treats the credit card as a
temporary buffer, not a tracked balance. Expenses rarely carry beyond one pay period. Modeling it as
a status + auto-payback captures the real behavior without adding account management complexity.

### 4.6 Carry Forward

A first-class operation for moving unpaid items from a past period to the current period.

**"Carry Forward Unpaid" button** on each past pay period:

1. Finds all transactions in that period with status `projected` (not done, not received, not
   credit).
2. Moves them to the **current** pay period (updates `pay_period_id`).
3. The amount does not change -- it's the same expense, just paid from a different paycheck.
4. If any of the carried-forward items were auto-generated from a recurrence rule, they are flagged
   as `is_override = True` (since they're no longer in the period the rule assigned them to).
5. All balances recalculate automatically.

**Optional future enhancement:** Track carry-forward history (original period → new period) for
auditability. Not needed for MVP.

### 4.7 Recurrence Rules

Each income or expense template can have a recurrence rule:

| Pattern                | Example                                        | Rule Definition                           |
| ---------------------- | ---------------------------------------------- | ----------------------------------------- |
| Every pay period       | Fuel, Kayla's spending money                   | `every_period`                            |
| Every Nth pay period   | Car payment (every 2nd), insurance (every 2nd) | `every_n_periods`, `n=2`, `offset=0 or 1` |
| Monthly on a date      | Disney+ on the 15th                            | `monthly`, `day=15`                       |
| Monthly first paycheck | Phone stipend                                  | `monthly_first`                           |
| Annual on a date       | Property tax in October                        | `annual`, `month=10`, `day=1`             |
| One-time               | Car maintenance, Christmas                     | `once`, assigned to a specific period     |

**Auto-generation behavior:**

- When periods are generated (or a rule is created/updated), the app populates transaction entries
  into future periods based on the rule.
- For `monthly` rules, the app calculates which pay period contains the specified day and assigns
  the transaction there.
- For `monthly_first` rules, the transaction is assigned to the first pay period whose start date
  falls in each calendar month.

### 4.8 Recurrence Engine -- State Machine

This section defines exactly how the recurrence engine handles every combination of rule changes and
manual overrides.

#### Transaction States (relative to recurrence)

| State                          | Meaning                                                       | Set by                                         |
| ------------------------------ | ------------------------------------------------------------- | ---------------------------------------------- |
| `auto_generated`               | Created by the recurrence engine, unmodified                  | Recurrence engine                              |
| `overridden`                   | User has manually changed the amount, period, or other fields | User edit (sets `is_override = True`)          |
| `done` / `received` / `credit` | Finalized -- represents a historical record                    | User action                                    |
| `deleted_by_user`              | User intentionally removed an auto-generated entry            | User delete (soft-delete: `is_deleted = True`) |

#### Rule Change: Template default amount changes

The user changes a template's default amount with an **effective start date**:

1. All `auto_generated` transactions **on or after** the effective date → deleted and regenerated
   with the new amount.
2. All `overridden` transactions on or after the effective date → **user is prompted**: "N
   overridden transactions exist after this date. Update them to the new default, or keep your
   overrides?"
   - "Update" → override flag cleared, amount updated to new default.
   - "Keep" → no change.
3. All `done`, `received`, and `credit` transactions → **never touched**. These are historical.
4. All `deleted_by_user` entries → **user is prompted**: "You previously removed transactions from
   these periods. Keep them removed?"
   - "Keep removed" → soft-delete flag stays.
   - "Restore" → soft-delete cleared, new amount applied.

#### Rule Change: Recurrence pattern changes

Example: template changes from `every_period` to `every_n_periods, n=2`:

1. All `auto_generated` transactions → deleted.
2. New transactions generated from the updated rule.
3. `overridden` transactions → prompted (same as above).
4. `done`/`received`/`credit` → untouched.
5. `deleted_by_user` → prompted (same as above).

#### Single Transaction: User edits an auto-generated entry

- The transaction is flagged `is_override = True`.
- Future rule regenerations skip this transaction.
- If later the user is prompted "update to rule or keep override" and picks "update," the flag is
  cleared.

#### Single Transaction: User moves entry to a different period

- Flagged as `is_override = True`.
- User is offered the choice at the moment of the action: "Change the rule for all future
  occurrences, or one-time override?"
  - "Change rule" → rule is updated, future transactions regenerated per new rule.
  - "One-time" → only this transaction moves; future occurrences follow the original rule.

#### Single Transaction: User deletes an auto-generated entry

- Soft-deleted (`is_deleted = True`).
- User is offered: "Remove from all future periods (change the rule), or just this one time?"
  - "Change rule" → template/rule is deactivated or end-dated.
  - "Just this one" → soft-delete stays; future regenerations skip this period for this template.

#### Key Principle

`done`, `received`, and `credit` transactions are **immutable** with respect to the recurrence
engine. They represent what actually happened and are never modified or deleted by rule changes.

### 4.9 Balance Roll-Forward & True-Up

This is the financial backbone of the app.

**Design approach: Calculate on read (no stored balances).**

Balances are never stored in the database. Every time the UI requests balance data, the server
calculates the full chain from the anchor forward in real time. This approach was chosen because:

- It is the simplest to keep correct -- there is no risk of stored balances drifting out of sync with
  transactions.
- The balance calculator is a **pure function**: given an anchor and a list of transactions, it
  returns a list of balances. No side effects, no state to manage, easy to test.
- At the scale of a single-user budget (~52 periods × ~30 line items = ~1,500 rows), PostgreSQL can
  query and sum this in milliseconds.
- If performance ever becomes a concern, a caching layer can be added later without changing the
  core logic.

**Calculation rules:**

- **Checking account balance** is the primary tracked balance.
- The user sets an **anchor balance**: the real, actual checking account balance as of a specific
  pay period. This is displayed prominently at the top of the grid and is editable inline (click,
  type, Enter).
- From the anchor forward, each period's **projected end balance** is calculated:
  ```
  end_balance[n] = end_balance[n-1] + total_income[n] - total_expenses[n]
  ```
  For the anchor period:
  ```
  end_balance[anchor] = anchor_balance + income_remaining[anchor] - expenses_remaining[anchor]
  ```
  Where `_remaining` means only transactions NOT yet reflected in the anchor balance (i.e., items
  still in `projected` status are included; `done`/`received` items are excluded because they're
  already baked into the anchor balance the user entered).
- **Amount selection per transaction:**
  - Status `done` or `received` → use `actual_amount` (fall back to `estimated_amount` if NULL)
  - Status `projected` → use `estimated_amount`
  - Status `credit` → **excluded from checking balance** (does not reduce checking)
- **True-up workflow:** The user updates the anchor balance every time they open the app (multiple
  times per week). The anchor is always tied to the current pay period. Past periods become
  historical. All future periods recalculate automatically.
- **Anchor history:** Each true-up is recorded in an `account_anchor_history` table with the
  balance, period, and timestamp. This provides an audit trail but is not displayed in the UI for
  Phase 1.

### 4.10 What-If Scenarios (Phase 3)

- A **scenario** is a named, saved alternate version of the budget.
- The **baseline** scenario is the user's actual working budget.
- The user can create a new scenario by **cloning the baseline** and then making changes:
  - Add/remove/modify income or expenses
  - Change recurrence rules
  - Adjust salary amounts
  - Add large one-time expenses (e.g., "new car down payment")
- Scenarios are **independent** -- changes to one don't affect others.
- The app supports **side-by-side comparison** of any two scenarios, showing:
  - Differences in projected end balance over time
  - Net difference per period
  - Divergence point (where they start to differ)
- Example use cases:
  - "What if I get a 3% raise starting in June?"
  - "What if I add a $400/month car payment starting in October?"
  - "What if I increase my 401(k) contribution from 6% to 10%?"

### 4.11 Savings Account Modeling (Phase 4)

- The app tracks a **savings balance** separately from checking.
- **Transfers** between accounts are modeled as paired transactions:
  - Transfer from checking to savings = expense in checking + income in savings.
  - Transfer from savings to checking = the reverse.
- The **savings balance** rolls forward the same way checking does.
- **Savings goals:**
  - Define a goal: name, target amount, target date.
  - The app calculates the required contribution per period.
  - Track progress toward the goal over time.
  - Metrics: paychecks-in-savings, months-in-savings, years-in-savings.

### 4.12 Expense Assignment Logic

Since expense assignment is a **mix of due dates and manual decisions:**

- When creating a recurring expense with a rule, the user specifies **which pay period(s)** it falls
  into. The rule engine handles the rest.
- For monthly bills, the app calculates which pay period contains the bill's due date and assigns it
  there by default.
- The user can always **reassign** an expense to a different period (override -- see §4.8 for state
  machine behavior).
- The grid view makes the assignment visible and editable.

### 4.13 Paycheck Calculator & Salary Projection (Phase 2)

The user's income in the budget is a **net biweekly paycheck** -- the amount that actually lands in
checking. To project this accurately over a 2-year horizon, the app models the full pipeline from
annual salary to net pay, including raises, taxes, and deductions -- some of which inflate over time.

**Calculation pipeline:**

```
Annual Salary
  → Apply raises (merit, COLA, custom) at effective dates
  = Projected Annual Salary
  → ÷ 26 pay periods
  = Gross Biweekly Pay
  → − Pre-tax deductions (retirement, health insurance, HSA, FSA, dental, vision)
  = Taxable Biweekly Income
  → − Federal income tax (withholding estimate based on bracket + filing status)
  → − State income tax (flat rate or bracket-based, configurable)
  → − FICA: Social Security (6.2% up to wage base cap) + Medicare (1.45%, +0.9% above threshold)
  = Net Biweekly Pay (after tax)
  → − Post-tax deductions (Roth 401k, life insurance, other)
  = Net Biweekly Paycheck (deposited to checking)
```

**Salary profile (one per income source, per scenario):**

A salary profile stores compensation details for a single income source. Multiple profiles per
scenario are supported (e.g., "Primary Job", "Side Job", "Spouse"), each with its own salary,
raises, deductions, and tax treatment:

- **Name:** identifies this income source (e.g., "Primary", "Freelance", "Spouse")
- **Annual salary:** the base number that raises apply to (e.g., $66,768)
- **Filing status:** single, married filing jointly, married filing separately, head of household
- **State:** determines state tax rate or bracket
- **Pay periods per year:** defaults to 26 (biweekly); can differ per profile

**Raises:**

- **Merit raise:** recurring annual, configurable month (default: January), percentage (default:
  2.5%)
- **COLA raise:** recurring annual, configurable month (default: July), percentage (default: 2.5%)
- **Custom raise:** one-time, specific effective date, percentage or flat amount (e.g., promotion)
- Raises apply to the **annual salary** and compound in chronological order.
- Raises are **scenario-aware** -- each scenario can have different raise assumptions.

**Deductions:**

Each deduction is a separate record with:

- **Name:** e.g., "401(k)", "Health Insurance", "Dental", "Vision", "HSA"
- **Deduction type:** `pre_tax` or `post_tax`
- **Calculation method:** `flat` (fixed dollar amount per period) or `percentage` (of gross)
- **Amount:** the flat dollar amount or percentage
- **Deductions per year:**
  - `26` = every paycheck (default; e.g., 401k)
  - `24` = first 2 paychecks per calendar month only (e.g., health insurance, dental, vision). **The
    3rd paycheck in a month skips this deduction.**
  - `12` = once per month (first paycheck of each month only)
- **Annual cap:** optional (e.g., 401(k) annual contribution limit)
- **Inflation-enabled:** for deductions like insurance premiums that increase annually
- **Inflation rate:** annual rate (e.g., 5% for health insurance); NULL = use global default
- **Inflation effective month:** when the increase kicks in (e.g., month of open enrollment)

**3rd paycheck handling:**

In biweekly pay (26 checks/year), 2 months have 3 paychecks. Many employers only deduct benefits
from the first 2 paychecks of each calendar month -- the 3rd paycheck has higher net pay because
benefit deductions are skipped.

The paycheck calculator detects 3rd paychecks by counting how many pay period start dates fall in a
calendar month. If this is the 3rd, deductions with `deductions_per_year = 24` are skipped.

**Tax estimation:**

The app provides a **reasonable estimate** of tax withholding, not exact payroll calculation. This
is disclosed to the user.

- **Federal income tax:** estimated using current-year brackets, standard deduction, and filing
  status.
- **State income tax:** configurable as a flat rate or simple brackets.
- **FICA:** Social Security (6.2% up to wage base cap, tracked cumulatively) + Medicare (1.45%,
  +0.9% above threshold).
- Tax brackets and FICA caps can be updated annually by the user.

**How it integrates with the budget:**

- Each salary profile is linked to a salary income template.
- The recurrence engine calls the paycheck calculator to compute the net amount for each future
  period.
- Multiple income sources appear as separate rows in the grid.
- The user can **override** any individual paycheck amount.
- A **paycheck breakdown view** shows the full calculation for any selected period and profile.

### 4.14 Charts & Visualizations (Phase 5)

- **Balance over time:** Line chart showing projected checking (and savings) balance across all
  periods (Chart.js).
- **Spending by category:** Bar chart showing totals per top-level category for a selected time
  range.
- **Budget vs. actuals:** Bar chart comparing estimated vs. actual per category or per period.
- **Scenario comparison:** Overlay line chart comparing balance trajectories of two scenarios.
- **Net pay trajectory:** Line chart of net biweekly paycheck over time with step-ups at raise
  dates.

### 4.15 Smart Estimates (Phase 7)

- For variable expenses (groceries, fuel), the app can **suggest an estimate** for future periods
  based on a rolling average of the last N actuals.
- The suggestion is displayed but never auto-applied -- the user accepts or adjusts.

### 4.16 Expense Inflation Adjustment (Phase 7)

- **Per-template inflation settings:** opt-in per expense template, with a global default rate and
  per-template overrides.
- When enabled, the recurrence engine multiplies the base amount by
  `(1 + annual_rate) ^ (years_from_start)` for each future period.
- Inflated amounts are displayed with an indicator so the user knows the number includes an
  inflation adjustment.
- The user can **override** any individual period's amount, which locks it.

### 4.17 Authentication (Phase 1)

Built from the start to support hosting on Proxmox with possible external access via Cloudflare
Tunnel or Tailscale.

- **Session-based auth:** Flask-Login with server-side sessions. No JWT, no refresh tokens.
- **Single seeded user** for Phase 1: created by a seed script on first run. No registration UI
  needed yet.
- **Password hashing:** bcrypt.
- **Protected routes:** All routes except `/login` require an authenticated session.
- **Login page:** Simple form -- email + password. On success, redirects to the budget grid.
- **"Remember me":** Optional longer session duration (configurable, default 30 days).

**Multi-user (deferred):**

- The database schema includes `user_id` columns on all relevant tables for future multi-user
  support.
- Registration UI, user management, and kid accounts are deferred to Phase 6+.
- When built, the migration is mechanical: add a registration route and ensure all queries filter by
  `user_id`.

**MFA / TOTP (deferred to Phase 6+):**

- The database schema includes an `auth.mfa_configs` table stub so the schema is ready.
- Feature implementation: TOTP setup, QR code, backup codes, two-step login flow.
- Additive to session auth -- no rework of the login system needed.

### 4.18 Audit Logging (Phase 6)

Deferred from Phase 1 to reduce initial complexity. The schema should stabilize before adding
triggers.

- **PostgreSQL trigger-based:** A `system.audit_log` table captures every INSERT, UPDATE, DELETE on
  audited tables.
- **Append-only** with configurable retention.
- See v1.6 requirements document for full audit log specification (unchanged).

### 4.19 Application Logging (Phased)

- **Phase 1:** Python standard `logging` module with simple console output. Log unhandled
  exceptions, auth events, and significant business operations.
- **Phase 6+:** Structured JSON logging, request_id correlation, request duration tracking.

### 4.20 Data Backup & Recovery (Phase 6)

- Automated `pg_dump` backups via cron job, daily with retention policy.
- Documented restore procedure.
- See v1.6 requirements document for full backup specification (unchanged).

### 4.21 Data Import

- The app will **not** import data from the existing spreadsheet.
- The user starts fresh from the current period forward.

---

## 5. Data Model (PostgreSQL)

### Design Principles

- **Normalized (3NF):** No redundant data. Every non-key column depends on the whole key and nothing
  but the key.
- **Referential integrity:** All foreign keys enforced. No orphaned records.
- **User-scoped:** All tables include `user_id` for future multi-user support, but only one user
  exists in Phase 1.
- **Scenario isolation:** Scenario-specific data is cleanly FK'd to a scenario.
- **Logical schemas:** Tables organized into PostgreSQL schemas by domain.
- **Reference tables for enums:** New types added via INSERT, not schema migration.

### PostgreSQL Schemas

| Schema   | Purpose                                                                  | Tables                                                                                                                                                |
| -------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ref`    | Reference/lookup tables (type enums). Rarely written, frequently joined. | account_types, transaction_types, statuses, recurrence_patterns, filing_statuses, deduction_timings, calc_methods, tax_types, raise_types             |
| `auth`   | Identity and authentication.                                             | users, user_settings, mfa_configs (stub)                                                                                                              |
| `budget` | Core budgeting domain.                                                   | accounts, account_anchor_history, pay_periods, categories, recurrence_rules, scenarios, transaction_templates, transactions, transfers, savings_goals |
| `salary` | Paycheck calculation domain.                                             | salary_profiles, salary_raises, paycheck_deductions, tax_bracket_sets, tax_brackets, state_tax_configs, fica_configs                                  |
| `system` | Operational tables (added as needed in later phases).                    | audit_log, operation_log, notifications, notification_settings                                                                                        |

### Entity Relationship Summary

```
ref (lookup/enum tables)
 ├── account_types        -- 'checking', 'savings'
 ├── transaction_types    -- 'income', 'expense'
 ├── statuses             -- 'projected', 'done', 'received', 'credit'
 ├── recurrence_patterns  -- 'every_period', 'every_n_periods', 'monthly',
 │                           'monthly_first', 'annual', 'once'
 ├── filing_statuses      -- 'single', 'married_jointly', etc.
 ├── deduction_timings    -- 'pre_tax', 'post_tax'
 ├── calc_methods         -- 'flat', 'percentage'
 ├── tax_types            -- 'flat', 'none', 'bracket'
 └── raise_types          -- 'merit', 'cola', 'custom'

auth
 ├── users
 │    ├── user_settings (1:1)
 │    └── mfa_configs (1:1 stub -- feature Phase 6+)

budget
 ├── accounts (1:N per user) → ref.account_types
 │    └── account_anchor_history (1:N -- true-up audit trail)
 ├── pay_periods (1:N -- auto-generated biweekly dates)
 ├── categories (1:N -- flat: group + name)
 ├── recurrence_rules (1:N) → ref.recurrence_patterns
 ├── scenarios (1:N -- named budget versions)
 ├── transaction_templates (1:N) → ref.transaction_types
 │    └── transactions (1:N per period per scenario) → ref.statuses
 │         └── credit_payback_for_id (nullable self-FK for CC payback)
 ├── transfers (1:N -- scenario-scoped)
 └── savings_goals (1:N -- per account, Phase 4)

salary (Phase 2)
 ├── salary_profiles (N per scenario)
 │    ├── salary_raises (1:N) → ref.raise_types
 │    └── paycheck_deductions (1:N) → ref.deduction_timings, ref.calc_methods
 ├── tax_bracket_sets (by year + filing status)
 │    └── tax_brackets (1:N)
 ├── state_tax_configs
 └── fica_configs (by year)
```

### Tables -- Phase 1 (Core Schema)

```sql
-- ============================================================
-- SCHEMAS
-- ============================================================
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS budget;
CREATE SCHEMA IF NOT EXISTS salary;   -- tables created in Phase 2
CREATE SCHEMA IF NOT EXISTS system;   -- tables created in Phase 6

-- ============================================================
-- REF SCHEMA -- Reference / Lookup Tables
-- ============================================================

CREATE TABLE ref.account_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(30) UNIQUE NOT NULL
);
INSERT INTO ref.account_types (name) VALUES ('checking'), ('savings');

CREATE TABLE ref.transaction_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL
);
INSERT INTO ref.transaction_types (name) VALUES ('income'), ('expense');

CREATE TABLE ref.statuses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(15) UNIQUE NOT NULL
);
INSERT INTO ref.statuses (name)
VALUES ('projected'), ('done'), ('received'), ('credit');

CREATE TABLE ref.recurrence_patterns (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) UNIQUE NOT NULL
);
INSERT INTO ref.recurrence_patterns (name)
VALUES ('every_period'), ('every_n_periods'), ('monthly'),
       ('monthly_first'), ('annual'), ('once');

CREATE TABLE ref.filing_statuses (
    id SERIAL PRIMARY KEY,
    name VARCHAR(25) UNIQUE NOT NULL
);
INSERT INTO ref.filing_statuses (name)
VALUES ('single'), ('married_jointly'), ('married_separately'),
       ('head_of_household');

CREATE TABLE ref.deduction_timings (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL
);
INSERT INTO ref.deduction_timings (name) VALUES ('pre_tax'), ('post_tax');

CREATE TABLE ref.calc_methods (
    id SERIAL PRIMARY KEY,
    name VARCHAR(12) UNIQUE NOT NULL
);
INSERT INTO ref.calc_methods (name) VALUES ('flat'), ('percentage');

CREATE TABLE ref.tax_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL
);
INSERT INTO ref.tax_types (name) VALUES ('flat'), ('none'), ('bracket');

CREATE TABLE ref.raise_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(10) UNIQUE NOT NULL
);
INSERT INTO ref.raise_types (name) VALUES ('merit'), ('cola'), ('custom');

-- ============================================================
-- AUTH SCHEMA
-- ============================================================

CREATE TABLE auth.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE auth.user_settings (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    default_inflation_rate NUMERIC(5,4) DEFAULT 0.0300,
    grid_default_periods INT DEFAULT 6,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Stub for Phase 6+ MFA feature.
CREATE TABLE auth.mfa_configs (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    totp_secret_encrypted BYTEA,
    is_enabled BOOLEAN DEFAULT FALSE,
    backup_codes JSONB,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- BUDGET SCHEMA
-- ============================================================

CREATE TABLE budget.accounts (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    account_type_id INT NOT NULL REFERENCES ref.account_types(id),
    name VARCHAR(100) NOT NULL,
    current_anchor_balance NUMERIC(12,2),
    current_anchor_period_id INT,  -- FK added after pay_periods
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE TABLE budget.pay_periods (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    period_index INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, start_date)
);
CREATE INDEX idx_pay_periods_user_index
    ON budget.pay_periods(user_id, period_index);

-- Deferred FK for circular reference.
ALTER TABLE budget.accounts
    ADD CONSTRAINT fk_accounts_anchor_period
    FOREIGN KEY (current_anchor_period_id)
    REFERENCES budget.pay_periods(id);

CREATE TABLE budget.account_anchor_history (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL
        REFERENCES budget.accounts(id) ON DELETE CASCADE,
    pay_period_id INT NOT NULL
        REFERENCES budget.pay_periods(id),
    anchor_balance NUMERIC(12,2) NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_anchor_history_account
    ON budget.account_anchor_history(account_id, created_at DESC);

-- Flat two-level categories: group + name.
CREATE TABLE budget.categories (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    group_name VARCHAR(100) NOT NULL,
    item_name VARCHAR(100) NOT NULL,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, group_name, item_name)
);
CREATE INDEX idx_categories_user_group
    ON budget.categories(user_id, group_name);

CREATE TABLE budget.recurrence_rules (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    pattern_id INT NOT NULL REFERENCES ref.recurrence_patterns(id),
    interval_n INT DEFAULT 1,
    offset_periods INT DEFAULT 0,
    day_of_month INT
        CHECK (day_of_month IS NULL
            OR (day_of_month >= 1 AND day_of_month <= 31)),
    month_of_year INT
        CHECK (month_of_year IS NULL
            OR (month_of_year >= 1 AND month_of_year <= 12)),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE budget.scenarios (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    is_baseline BOOLEAN DEFAULT FALSE,
    cloned_from_id INT REFERENCES budget.scenarios(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

CREATE TABLE budget.transaction_templates (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    account_id INT NOT NULL REFERENCES budget.accounts(id),
    category_id INT NOT NULL REFERENCES budget.categories(id),
    recurrence_rule_id INT REFERENCES budget.recurrence_rules(id),
    transaction_type_id INT NOT NULL REFERENCES ref.transaction_types(id),
    name VARCHAR(200) NOT NULL,
    default_amount NUMERIC(12,2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_templates_user_type
    ON budget.transaction_templates(user_id, transaction_type_id);

CREATE TABLE budget.transactions (
    id SERIAL PRIMARY KEY,
    template_id INT
        REFERENCES budget.transaction_templates(id) ON DELETE SET NULL,
    pay_period_id INT NOT NULL
        REFERENCES budget.pay_periods(id) ON DELETE CASCADE,
    scenario_id INT NOT NULL
        REFERENCES budget.scenarios(id) ON DELETE CASCADE,
    status_id INT NOT NULL REFERENCES ref.statuses(id),
    name VARCHAR(200) NOT NULL,
    category_id INT REFERENCES budget.categories(id),
    transaction_type_id INT NOT NULL REFERENCES ref.transaction_types(id),
    estimated_amount NUMERIC(12,2) NOT NULL,
    actual_amount NUMERIC(12,2),
    is_override BOOLEAN DEFAULT FALSE,
    is_deleted BOOLEAN DEFAULT FALSE,
    credit_payback_for_id INT
        REFERENCES budget.transactions(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_transactions_period_scenario
    ON budget.transactions(pay_period_id, scenario_id);
CREATE INDEX idx_transactions_template
    ON budget.transactions(template_id);
CREATE INDEX idx_transactions_credit_payback
    ON budget.transactions(credit_payback_for_id);

-- Unique constraint: one transaction per template per period per scenario
-- (only for non-deleted, template-linked transactions).
CREATE UNIQUE INDEX idx_transactions_template_period_scenario
    ON budget.transactions(template_id, pay_period_id, scenario_id)
    WHERE template_id IS NOT NULL AND is_deleted = FALSE;

CREATE TABLE budget.transfers (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    from_account_id INT NOT NULL REFERENCES budget.accounts(id),
    to_account_id INT NOT NULL REFERENCES budget.accounts(id),
    pay_period_id INT NOT NULL REFERENCES budget.pay_periods(id),
    scenario_id INT NOT NULL
        REFERENCES budget.scenarios(id) ON DELETE CASCADE,
    status_id INT NOT NULL REFERENCES ref.statuses(id),
    amount NUMERIC(12,2) NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (from_account_id != to_account_id)
);
CREATE INDEX idx_transfers_period_scenario
    ON budget.transfers(pay_period_id, scenario_id);

CREATE TABLE budget.savings_goals (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL
        REFERENCES budget.accounts(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    target_amount NUMERIC(12,2) NOT NULL,
    target_date DATE,
    contribution_per_period NUMERIC(12,2),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Tables -- Phase 2 (Salary Schema)

```sql
CREATE TABLE salary.salary_profiles (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scenario_id INT NOT NULL
        REFERENCES budget.scenarios(id) ON DELETE CASCADE,
    template_id INT
        REFERENCES budget.transaction_templates(id),
    filing_status_id INT NOT NULL REFERENCES ref.filing_statuses(id),
    name VARCHAR(100) NOT NULL DEFAULT 'Primary',
    annual_salary NUMERIC(12,2) NOT NULL,
    state_code VARCHAR(2),
    pay_periods_per_year INT DEFAULT 26,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, scenario_id, name)
);

CREATE TABLE salary.salary_raises (
    id SERIAL PRIMARY KEY,
    salary_profile_id INT NOT NULL
        REFERENCES salary.salary_profiles(id) ON DELETE CASCADE,
    raise_type_id INT NOT NULL REFERENCES ref.raise_types(id),
    effective_month INT NOT NULL
        CHECK (effective_month >= 1 AND effective_month <= 12),
    effective_year INT,
    percentage NUMERIC(5,4),
    flat_amount NUMERIC(12,2),
    is_recurring BOOLEAN DEFAULT TRUE,
    notes VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CHECK (
        (percentage IS NOT NULL AND flat_amount IS NULL) OR
        (percentage IS NULL AND flat_amount IS NOT NULL)
    )
);
CREATE INDEX idx_salary_raises_profile
    ON salary.salary_raises(salary_profile_id);

CREATE TABLE salary.paycheck_deductions (
    id SERIAL PRIMARY KEY,
    salary_profile_id INT NOT NULL
        REFERENCES salary.salary_profiles(id) ON DELETE CASCADE,
    deduction_timing_id INT NOT NULL
        REFERENCES ref.deduction_timings(id),
    calc_method_id INT NOT NULL
        REFERENCES ref.calc_methods(id),
    name VARCHAR(100) NOT NULL,
    amount NUMERIC(12,4) NOT NULL,
    deductions_per_year INT NOT NULL DEFAULT 26
        CHECK (deductions_per_year IN (12, 24, 26)),
    annual_cap NUMERIC(12,2),
    inflation_enabled BOOLEAN DEFAULT FALSE,
    inflation_rate NUMERIC(5,4),
    inflation_effective_month INT
        CHECK (inflation_effective_month IS NULL
            OR (inflation_effective_month >= 1
                AND inflation_effective_month <= 12)),
    sort_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_deductions_profile
    ON salary.paycheck_deductions(salary_profile_id);

CREATE TABLE salary.tax_bracket_sets (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    filing_status_id INT NOT NULL REFERENCES ref.filing_statuses(id),
    tax_year INT NOT NULL,
    standard_deduction NUMERIC(12,2) NOT NULL,
    description VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, tax_year, filing_status_id)
);

CREATE TABLE salary.tax_brackets (
    id SERIAL PRIMARY KEY,
    bracket_set_id INT NOT NULL
        REFERENCES salary.tax_bracket_sets(id) ON DELETE CASCADE,
    min_income NUMERIC(12,2) NOT NULL CHECK (min_income >= 0),
    max_income NUMERIC(12,2),
    rate NUMERIC(5,4) NOT NULL,
    sort_order INT NOT NULL,
    CHECK (max_income IS NULL OR max_income > min_income)
);
CREATE INDEX idx_tax_brackets_set
    ON salary.tax_brackets(bracket_set_id, sort_order);

CREATE TABLE salary.state_tax_configs (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    tax_type_id INT NOT NULL REFERENCES ref.tax_types(id),
    state_code VARCHAR(2) NOT NULL,
    flat_rate NUMERIC(5,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, state_code)
);

CREATE TABLE salary.fica_configs (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    tax_year INT NOT NULL,
    ss_rate NUMERIC(5,4) DEFAULT 0.0620,
    ss_wage_base NUMERIC(12,2) DEFAULT 168600.00,
    medicare_rate NUMERIC(5,4) DEFAULT 0.0145,
    medicare_surtax_rate NUMERIC(5,4) DEFAULT 0.0090,
    medicare_surtax_threshold NUMERIC(12,2) DEFAULT 200000.00,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, tax_year)
);
```

### Scalability Notes

- **Multi-user:** Every table has `user_id`. Add registration route + query filters when ready.
- **Hierarchical categories:** Migrate `budget.categories` to adjacency list (add `parent_id`,
  `depth`) if flat group + name proves insufficient.
- **State bracket taxes:** Add `salary.state_tax_brackets` table when needed. `ref.tax_types`
  already includes `'bracket'`.
- **Audit log partitioning:** Partition `system.audit_log` by `created_at` if it grows large.

---

## 6. Architecture

### Design Patterns

**Application Factory Pattern.** The Flask app is created by a `create_app()` factory function.
Accepts a configuration object, initializes extensions, registers blueprints, returns the configured
app.

**Blueprints.** Each domain is a Flask Blueprint. Blueprints handle HTTP concerns only: request
parsing, response formatting, status codes, auth checks. They delegate business logic to the service
layer.

**Service Layer.** All business logic lives in `services/`. Services are plain Python classes or
modules -- not Flask-aware. They accept and return Python objects and raise domain-specific
exceptions. Independently testable.

**Server-rendered UI with HTMX.** The frontend is Jinja2 templates enhanced with HTMX for
interactive behavior. Flask routes return HTML fragments for HTMX swap targets and full pages for
standard navigation. No separate frontend build pipeline.

```
Request flow:
  Browser
    → Flask route (Blueprint)
      → validates input
      → calls Service method
        → Service contains business logic
        → Service calls SQLAlchemy models / other services
        → Service returns result or raises exception
      → Route renders Jinja2 template (full page or HTMX fragment)
    → Browser (HTMX swaps fragment into DOM, or full page load)
```

### High-Level Diagram

```
┌──────────────────────────────────────────────────────────┐
│               Browser (HTMX + Bootstrap 5)               │
│  ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌─────────────┐  │
│  │  Budget  │ │ Paycheck │ │Scenario │ │   Charts    │  │
│  │   Grid   │ │Breakdown │ │ Compare │ │  (Chart.js) │  │
│  └────┬─────┘ └────┬─────┘ └────┬────┘ └──────┬──────┘  │
│       └─────────────┴───────────┴──────────────┘         │
│                         │ HTTP (HTML responses)          │
└─────────────────────────┼────────────────────────────────┘
                          │
┌─────────────────────────┼────────────────────────────────┐
│  Flask Application (factory pattern + blueprints)        │
│                         │                                │
│  ┌─── Routes (Blueprints) ──────────────────────────┐    │
│  │ auth │ pay_periods │ transactions │ templates     │    │
│  │ accounts │ salary │ scenarios │ settings │ grid   │    │
│  └──────────────────────┬───────────────────────────┘    │
│                         │                                │
│  ┌─── Service Layer ────┴───────────────────────────┐    │
│  │ AuthService       │ PayPeriodService             │    │
│  │ RecurrenceEngine  │ BalanceCalculator            │    │
│  │ PaycheckCalc      │ ScenarioService              │    │
│  │ CreditWorkflow    │ CarryForwardService          │    │
│  └──────────────────────┬───────────────────────────┘    │
│                         │                                │
│  ┌─── Data Layer (SQLAlchemy ORM) ──────────────────┐    │
│  │ Models map 1:1 to normalized PostgreSQL tables    │    │
│  └──────────────────────┬───────────────────────────┘    │
└─────────────────────────┼────────────────────────────────┘
                          │
                 ┌────────┴────────┐
                 │   PostgreSQL    │
                 └─────────────────┘
```

### Project Structure

```
budget-app/
├── app/
│   ├── __init__.py                  # Application factory (create_app)
│   ├── extensions.py                # SQLAlchemy, Migrate, LoginManager
│   ├── config.py                    # Dev / Test / Prod config classes
│   │
│   ├── models/                      # SQLAlchemy models (mirror DB schemas)
│   │   ├── __init__.py              # Imports all models for Alembic
│   │   ├── ref.py                   # All ref.* lookup tables
│   │   ├── user.py                  # auth.users, user_settings, mfa_configs
│   │   ├── account.py               # budget.accounts, anchor_history
│   │   ├── pay_period.py            # budget.pay_periods
│   │   ├── category.py              # budget.categories
│   │   ├── recurrence_rule.py       # budget.recurrence_rules
│   │   ├── scenario.py              # budget.scenarios
│   │   ├── transaction_template.py  # budget.transaction_templates
│   │   ├── transaction.py           # budget.transactions
│   │   ├── transfer.py              # budget.transfers
│   │   ├── savings_goal.py          # budget.savings_goals
│   │   ├── salary_profile.py        # salary.* (Phase 2)
│   │   └── tax_config.py            # salary.tax_* (Phase 2)
│   │
│   ├── routes/                      # Blueprints (HTTP + template rendering)
│   │   ├── __init__.py              # Registers all blueprints
│   │   ├── auth.py                  # /login, /logout
│   │   ├── grid.py                  # / (main grid view + HTMX partials)
│   │   ├── transactions.py          # /transactions/* (CRUD + HTMX)
│   │   ├── templates.py             # /templates/* (recurrence template CRUD)
│   │   ├── pay_periods.py           # /pay-periods/*
│   │   ├── accounts.py              # /accounts/*
│   │   ├── categories.py            # /categories/*
│   │   ├── settings.py              # /settings
│   │   ├── salary.py                # /salary/* (Phase 2)
│   │   └── scenarios.py             # /scenarios/* (Phase 3)
│   │
│   ├── services/                    # Business logic (no Flask imports)
│   │   ├── auth_service.py          # Login, password hashing
│   │   ├── pay_period_service.py    # Generate, extend, list periods
│   │   ├── recurrence_engine.py     # Auto-generate from rules (§4.8)
│   │   ├── balance_calculator.py    # Pure function: anchor → balances
│   │   ├── carry_forward_service.py # Move unpaid items to current period
│   │   ├── credit_workflow.py       # Mark credit + auto-generate payback
│   │   ├── paycheck_calculator.py   # Annual salary → net biweekly (Phase 2)
│   │   ├── tax_calculator.py        # Federal, state, FICA (Phase 2)
│   │   └── scenario_service.py      # Clone, diff, compare (Phase 3)
│   │
│   ├── templates/                   # Jinja2 templates
│   │   ├── base.html                # Base layout (Bootstrap 5, HTMX script)
│   │   ├── auth/
│   │   │   └── login.html
│   │   ├── grid/
│   │   │   ├── grid.html            # Full grid page
│   │   │   ├── _period_column.html  # HTMX partial: single period column
│   │   │   ├── _transaction_cell.html  # HTMX partial: single cell
│   │   │   ├── _transaction_edit.html  # HTMX partial: inline edit form
│   │   │   ├── _balance_row.html    # HTMX partial: balance summary row
│   │   │   └── _anchor_edit.html    # HTMX partial: anchor balance edit
│   │   ├── templates/               # Template/recurrence management
│   │   │   ├── list.html
│   │   │   └── form.html
│   │   ├── salary/                  # Phase 2
│   │   │   ├── breakdown.html
│   │   │   └── projection.html
│   │   ├── scenarios/               # Phase 3
│   │   │   └── compare.html
│   │   └── settings/
│   │       └── settings.html
│   │
│   ├── static/                      # Static assets
│   │   ├── css/
│   │   │   └── app.css              # Custom styles (minimal -- Bootstrap handles most)
│   │   └── js/
│   │       └── app.js               # Minimal JS (HTMX config, confirm dialogs)
│   │
│   ├── schemas/                     # Marshmallow for input validation
│   │   ├── transaction.py
│   │   ├── template.py
│   │   └── salary.py
│   │
│   ├── exceptions.py                # Domain-specific exceptions
│   └── utils/
│       └── validators.py
│
├── migrations/                      # Alembic DB migrations
├── scripts/
│   ├── seed_user.py                 # Create the initial user + default data
│   ├── seed_ref_tables.py           # Seed all ref.* lookup data
│   └── seed_tax_brackets.py         # Seed current-year tax data (Phase 2)
├── tests/
│   ├── conftest.py                  # Fixtures: test app, test db, test client
│   ├── test_services/
│   │   ├── test_balance_calculator.py
│   │   ├── test_recurrence_engine.py
│   │   ├── test_credit_workflow.py
│   │   ├── test_carry_forward.py
│   │   └── test_paycheck_calculator.py  # Phase 2
│   └── test_routes/
│       ├── test_auth.py
│       ├── test_grid.py
│       └── test_transactions.py
├── docker-compose.yml               # PostgreSQL (dev) + app (prod deployment)
├── Dockerfile                       # For production deployment
├── requirements.txt
├── run.py                           # Entry point: from app import create_app
└── README.md
```

### Key Backend Services

**Auth Service** (`auth_service.py`)

- Verify email + password against bcrypt hash.
- Flask-Login `user_loader` callback.
- In Phase 1, the only user is seeded by `scripts/seed_user.py`.

**Recurrence Engine** (`recurrence_engine.py`)

- Input: a transaction template + its recurrence rule + a list of pay periods + an effective start
  date.
- Output: generated transaction entries for applicable periods.
- Implements the full state machine defined in §4.8:
  - Respects `is_override` and `is_deleted` flags.
  - Prompts for override conflicts (returns a list of conflicts for the route to present to the
    user).
  - For salary templates (Phase 2): delegates to PaycheckCalculator for net amount.
- The most complex service in the app -- test thoroughly.

**Balance Calculator** (`balance_calculator.py`)

- A **pure function** -- no database writes, no side effects.
- Input: account, anchor balance, anchor period, all transactions from anchor forward.
- Output: list of `(period_id, projected_end_balance)` tuples.
- Uses actual amounts where status is `done`/`received`, estimated amounts where `projected`.
- **Excludes** transactions with status `credit` from checking balance.
- Called on every grid load.

**Credit Workflow** (`credit_workflow.py`)

- `mark_as_credit(transaction_id)`:
  1. Set status to `credit`.
  2. Find the next pay period.
  3. Create a payback transaction with `credit_payback_for_id` pointing to the original.
- `unmark_credit(transaction_id)`:
  1. Set status back to `projected`.
  2. Delete the auto-generated payback transaction.

**Carry Forward Service** (`carry_forward_service.py`)

- `carry_forward_unpaid(source_period_id, target_period_id)`:
  1. Find all transactions in source period with status `projected`.
  2. Update their `pay_period_id` to target period.
  3. Flag as `is_override = True` if they were auto-generated from a template.
  4. Return count of moved items.

**Paycheck Calculator** (`paycheck_calculator.py`) -- Phase 2

- Pure function: given a salary profile and a target date → net biweekly paycheck.
- Pipeline: annual salary → apply raises → gross biweekly → pre-tax deductions → taxes → post-tax
  deductions → net.
- 3rd paycheck detection: skip 24-per-year deductions.
- Handles annual caps.

**Tax Calculator** (`tax_calculator.py`) -- Phase 2

- Pure function: taxable income + filing status + state + year → federal tax, state tax, SS,
  Medicare.
- Tracks cumulative wages for SS wage base cap.

**Scenario Service** (`scenario_service.py`) -- Phase 3

- Clone baseline → new scenario (deep copy: transactions, templates, salary profiles).
- Diff two scenarios → balance deltas per period.

### Routes & URL Structure

Since the app is server-rendered with HTMX, routes return either full HTML pages or HTML fragments.

| Method   | URL                                          | Returns  | Description                          | Phase |
| -------- | -------------------------------------------- | -------- | ------------------------------------ | ----- |
| `GET`    | `/login`                                     | Page     | Login form                           | 1     |
| `POST`   | `/login`                                     | Redirect | Authenticate, create session         | 1     |
| `GET`    | `/logout`                                    | Redirect | End session                          | 1     |
| `GET`    | `/`                                          | Page     | Budget grid (main view)              | 1     |
| `GET`    | `/grid/period/<id>`                          | Fragment | Single period column (HTMX)          | 1     |
| `GET`    | `/grid/balance-row`                          | Fragment | Balance summary row (HTMX)           | 1     |
| `PATCH`  | `/transactions/<id>`                         | Fragment | Update transaction (inline edit)     | 1     |
| `POST`   | `/transactions/<id>/mark-done`               | Fragment | Set done + actual amount             | 1     |
| `POST`   | `/transactions/<id>/mark-credit`             | Fragment | Set credit + auto-generate payback   | 1     |
| `DELETE` | `/transactions/<id>/unmark-credit`           | Fragment | Revert credit status, delete payback | 1     |
| `POST`   | `/transactions`                              | Fragment | Create ad-hoc transaction            | 1     |
| `DELETE` | `/transactions/<id>`                         | Fragment | Soft-delete transaction              | 1     |
| `POST`   | `/pay-periods/<id>/carry-forward`            | Fragment | Carry forward unpaid to current      | 1     |
| `PATCH`  | `/accounts/<id>/true-up`                     | Fragment | Update anchor balance                | 1     |
| `GET`    | `/templates`                                 | Page     | List transaction templates           | 1     |
| `GET`    | `/templates/new`                             | Page     | Template creation form               | 1     |
| `POST`   | `/templates`                                 | Redirect | Create template + recurrence rule    | 1     |
| `GET`    | `/templates/<id>/edit`                       | Page     | Template edit form                   | 1     |
| `PUT`    | `/templates/<id>`                            | Redirect | Update template, regenerate          | 1     |
| `DELETE` | `/templates/<id>`                            | Redirect | Deactivate template                  | 1     |
| `GET`    | `/categories`                                | Page     | Category management                  | 1     |
| `POST`   | `/categories`                                | Fragment | Create category                      | 1     |
| `GET`    | `/pay-periods/generate`                      | Page     | Pay period generation form           | 1     |
| `POST`   | `/pay-periods/generate`                      | Redirect | Generate periods                     | 1     |
| `GET`    | `/settings`                                  | Page     | User settings                        | 1     |
| `PUT`    | `/settings`                                  | Redirect | Update settings                      | 1     |
| `GET`    | `/salary/profiles`                           | Page     | List salary profiles                 | 2     |
| `POST`   | `/salary/profiles`                           | Redirect | Create salary profile                | 2     |
| `GET`    | `/salary/breakdown/<profile_id>/<period_id>` | Page     | Paycheck breakdown                   | 2     |
| `GET`    | `/salary/projection/<profile_id>`            | Page     | Salary projection view               | 2     |
| `GET`    | `/scenarios`                                 | Page     | List scenarios                       | 3     |
| `POST`   | `/scenarios`                                 | Redirect | Clone baseline → new scenario        | 3     |
| `GET`    | `/scenarios/compare`                         | Page     | Side-by-side comparison              | 3     |

---

## 7. Frontend -- Key Views

### 7.1 Budget Grid (Primary View)

The main screen mirrors the spreadsheet layout:

- **Columns** = pay periods. Current period is the **leftmost column**. Future periods extend to the
  right.
- **Rows** = income and expense line items, grouped by category group name with visual headers.
- **Cells** show the amount (estimated or actual) with visual indicators:
  - Gray = projected
  - Green = done/received
  - Yellow/orange = credit
  - When actual differs from estimate, show both (e.g., `~~$500~~ $487 ✓`)
- **Bottom summary rows:**
  - Total Income
  - Total Expenses
  - Net (Income − Expenses)
  - Projected End Balance (checking)

**Anchor balance** is displayed prominently at the top. Click to edit inline (HTMX swap: display →
input → display on save). Every edit triggers a balance recalculation across all visible periods.

**HTMX interaction pattern:**

- Click a cell → HTMX `hx-get` loads an inline edit form → user edits → `hx-patch` sends update →
  server returns the updated cell fragment + triggers a balance row refresh via `HX-Trigger`
  response header.
- Status changes (mark done, mark credit) are single-click actions via `hx-post`.
- "Carry Forward Unpaid" button on past periods: `hx-post` → server moves items → returns refreshed
  period columns.
- Save on every edit -- no explicit save button.

**Date range controls:**

- Quick-select buttons: "3 Periods" · "6 Periods" · "3 Months" · "6 Months" · "1 Year" · "2 Years"
- Left/right arrows to shift the visible window without changing the range size.
- The full 2-year projection is always calculated -- the range only controls what's displayed.

**Column sizing:**

- 1-6 periods visible: wide columns with full detail (name, estimated, actual, status icon)
- 7-13 periods: medium columns (amount + status icon)
- 14+ periods: compact columns (amount only, hover/click for detail)

### 7.2 Transaction Detail (Inline or Modal)

Click any transaction cell for the full detail view:

- Edit estimated and actual amounts
- Change status (projected → done, or mark credit)
- Reassign to a different pay period
- Add notes
- View linked recurrence template (link to edit)
- If this is a credit payback, show the original transaction it's paying back

### 7.3 Template Management Page

List all transaction templates with their recurrence rules:

- Add new template (form with name, category, amount, recurrence rule selector)
- Edit existing template (changing the rule triggers regeneration with override prompts)
- Deactivate template (stops future generation, keeps historical transactions)

### 7.4 Paycheck Breakdown View (Phase 2)

Accessible from the salary income row in the grid:

- **Gross section:** Annual salary (with raise history), gross biweekly amount
- **Pre-tax deductions:** Each line item with amount
- **Taxable income**
- **Tax section:** Federal, state, Social Security, Medicare
- **Post-tax deductions**
- **Net pay:** Final amount (bold, highlighted)

### 7.5 Salary Projection View (Phase 2)

Projected compensation over the 2-year horizon:

- Table: raise events with before/after salary and net pay
- Chart (Chart.js): net biweekly paycheck over time with step-ups at raise dates

### 7.6 Scenario Comparison View (Phase 3)

- Two grids side by side (or overlaid)
- Differences highlighted (red = worse, green = better)
- Summary: total difference in end balance at 6 months, 1 year, 2 years

---

## 8. Development Roadmap

### Phase 1 -- Replace the Spreadsheet (Weeks 1-4)

**Goal:** Stop opening the spreadsheet. Use the app for the payday workflow.

**Week 1: Skeleton + pay periods + manual transactions**

- [ ] Flask app factory, config.py (dev/test/prod), extensions.py
- [ ] Docker Compose with PostgreSQL for development
- [ ] Alembic setup + initial migration (ref, auth, budget schemas)
- [ ] Seed scripts: ref tables, single user, default checking account, baseline scenario
- [ ] Flask-Login session auth (login page, one seeded user)
- [ ] Pay period model + generation service (start date, biweekly, 2-year horizon)
- [ ] Transaction model (manual entries -- no templates yet)
- [ ] Category model (flat: group_name + item_name)
- [ ] Base Jinja2 layout with Bootstrap 5 + HTMX script tags
- [ ] Basic grid template: periods as columns, transactions as rows
- [ ] HTMX inline editing: click cell → edit → save on blur

**Week 2: Balance calculator + status workflow**

- [ ] Balance calculator service (pure function, calculated on read)
- [ ] Anchor balance display at top of grid, editable inline
- [ ] Anchor history tracking (record each true-up)
- [ ] Status workflow: projected → done (with actual amount)
- [ ] Status workflow: mark as credit + auto-generate payback (credit_workflow service)
- [ ] Visual indicators in grid (gray/green/yellow)
- [ ] Show both estimated and actual when they differ
- [ ] "Carry Forward Unpaid" button + service
- [ ] Grid date range controls (quick-select buttons, left/right navigation)

**Week 3: Recurrence engine**

- [ ] Transaction template model with recurrence rules
- [ ] Recurrence engine service: auto-generate transactions from rules
- [ ] Rule types: every_period, every_n_periods, monthly, monthly_first, annual, once
- [ ] Override flagging on manual edits (is_override)
- [ ] Soft-delete for user-removed auto-generated entries (is_deleted)
- [ ] Regeneration logic: delete non-overridden after effective date, recreate

**Week 4: Recurrence polish + daily usability**

- [ ] Override conflict prompts during regeneration (return conflicts, user chooses)
- [ ] Template CRUD pages (list, create, edit, deactivate)
- [ ] Category CRUD page
- [ ] Carry forward properly handles recurrence-generated items
- [ ] Responsive column sizing (wide/medium/compact based on visible period count)
- [ ] Basic mobile-passable layout (usable from phone, not perfect)
- [ ] Test suite: balance calculator, recurrence engine, credit workflow, carry forward

**Milestone:** Full payday workflow works in the app. Ready for daily use.

### Phase 2 -- Paycheck Calculator (Weeks 5-8)

- [ ] Salary profiles CRUD (name, annual salary, filing status, state)
- [ ] Salary raises CRUD (merit, COLA, custom -- per profile)
- [ ] Paycheck deductions CRUD (pre-tax, post-tax, deductions_per_year, inflation)
- [ ] Tax bracket + FICA config seeding and CRUD
- [ ] Paycheck calculator service (annual salary → net biweekly)
- [ ] 3rd paycheck detection (skip 24-per-year deductions)
- [ ] Tax calculator service (federal brackets, state flat rate, FICA with wage base cap)
- [ ] Wire paycheck calculator into recurrence engine for salary templates
- [ ] Paycheck breakdown view
- [ ] Salary projection view (raises + net pay over time, per profile)
- [ ] Test suite: paycheck calculator, tax calculator

### Phase 3 -- Scenarios (Weeks 9-12)

- [ ] Scenario CRUD: create, clone from baseline (deep copy), rename, delete
- [ ] Scenario switcher in grid header
- [ ] Scenario-scoped transactions, templates, and salary profiles
- [ ] Side-by-side comparison view
- [ ] Balance diff calculation and visual highlighting
- [ ] Test suite: scenario clone, diff

### Phase 4 -- Savings & Accounts (Weeks 13-15)

- [ ] Savings account setup (tracked balance separate from checking)
- [ ] Transfer creation and tracking (checking ↔ savings, scenario-scoped)
- [ ] Savings balance roll-forward
- [ ] Savings goals: target amount, target date, auto-calculated contributions
- [ ] Paychecks-in-savings, months-in-savings, years-in-savings metrics
- [ ] Accounts dashboard view

### Phase 5 -- Visualization (Weeks 16-18)

- [ ] Balance-over-time line chart (Chart.js)
- [ ] Category spending breakdown (bar chart)
- [ ] Budget vs. actuals comparison chart
- [ ] Scenario comparison overlay chart
- [ ] Net pay trajectory chart (from salary projection view)

### Phase 6 -- Hardening & Ops (Weeks 19-24)

- [ ] Audit logging: audit_log table + trigger function + attach to all tables
- [ ] Structured request logging (JSON, request_id correlation)
- [ ] MFA / TOTP: setup, confirm, verify, disable + login flow integration
- [ ] Registration flow (for kid accounts / multi-user)
- [ ] Automated pg_dump backups (cron, daily, retention policy)
- [ ] Restore scripts and documentation
- [ ] Export to CSV
- [ ] Mobile-responsive layout refinement
- [ ] Production deployment guide (Docker Compose on Proxmox, Nginx, HTTPS, Cloudflare Tunnel)

### Phase 7 -- Smart Features (Weeks 25-28)

- [ ] Smart estimates: rolling average of actuals for variable expenses
- [ ] Expense inflation: global default + per-template rates
- [ ] Deduction inflation: apply at open enrollment month
- [ ] Recurrence engine: inflation formula integration
- [ ] Grid indicators for inflation-adjusted amounts

### Phase 8 -- Notifications (Weeks 29-31)

- [ ] Notification settings (per-type toggle, thresholds)
- [ ] In-app notification generation after balance recalculations
- [ ] Notification bell icon + dropdown in app header
- [ ] Optional email notifications (SendGrid or SMTP)

---

## 9. Key Technical Decisions

| Decision             | Choice                                             | Rationale                                                                     |
| -------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------- |
| App structure        | Application factory pattern                        | Testable, multi-config, clean extension init                                  |
| Route organization   | Flask Blueprints                                   | Each domain is a self-contained module                                        |
| Business logic       | Service layer (plain Python)                       | No Flask coupling; independently testable                                     |
| Input validation     | Marshmallow schemas                                | Validates input, keeps routes thin                                            |
| Backend framework    | Flask                                              | Known, proven, good for this scale                                            |
| Frontend             | Jinja2 + HTMX + Bootstrap 5                        | No build pipeline; server-rendered with interactivity; fast to ship           |
| CSS framework        | Bootstrap 5                                        | Functional UI fast; forms, tables, grid layout out of the box                 |
| Interactivity        | HTMX                                               | Inline editing, partial page updates, no JavaScript framework needed          |
| Authentication       | Flask-Login + sessions + bcrypt                    | Natural fit for server-rendered app; simple; secure                           |
| Balance calculation  | Calculate on read (pure function)                  | No stored balances; no drift; fast at single-user scale                       |
| Paycheck calculation | Annual salary → net biweekly (pure function)       | Mirrors real payroll; scenario-aware; handles raises, deductions, taxes, FICA |
| Multi-income         | salary_profiles keyed (user_id, scenario_id, name) | Multiple profiles per scenario from day one                                   |
| Database             | PostgreSQL                                         | Right for normalized relational data at this scale                            |
| ORM                  | SQLAlchemy                                         | Standard for Flask, strong PostgreSQL support                                 |
| Migrations           | Alembic                                            | Version-controlled schema changes                                             |
| Categories           | Flat (group + name)                                | Simple; migrate to adjacency list later if needed                             |
| Credit card handling | Status + auto-payback expense                      | Matches actual workflow; no separate account needed                           |
| Enum strategy        | Reference/lookup tables in `ref` schema            | New types via INSERT, not migration                                           |
| Charting (Phase 5)   | Chart.js                                           | Works without React; embeds in Jinja templates                                |
| Dev environment      | Arch Linux, NeoVim/LazyVim, native PostgreSQL      | Clean and fast for development                                                |
| Deployment           | Docker Compose on Proxmox LXC/VM                   | Nginx reverse proxy already in place                                          |
| External access      | Cloudflare Tunnel or Tailscale                     | Avoids double-NAT; secure without port forwarding                             |
| Linting (Python)     | Pylint                                             | Per user preference                                                           |
| Linting (SQL)        | SQLFluff                                           | Per user preference                                                           |
| Naming convention    | snake_case                                         | Per user preference                                                           |

---

## 10. Resolved Design Decisions

| Question            | Decision                                                                                                                               |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Primary goal        | Build a daily-use product that replaces a 7-year-old spreadsheet                                                                       |
| Authentication      | Flask-Login sessions; single seeded user for Phase 1; multi-user deferred                                                              |
| Frontend stack      | HTMX + Jinja2 + Bootstrap 5 (no React, no separate build pipeline)                                                                     |
| Grid interaction    | Save on every cell edit (no explicit save button); HTMX partial swaps                                                                  |
| Grid layout         | Current period leftmost, future periods to the right                                                                                   |
| True-up frequency   | Every visit (multiple times per week); anchor balance editable inline at top of grid                                                   |
| Carry forward       | First-class "Carry Forward Unpaid" button on past periods; moves all projected items to current period                                 |
| Credit card         | Not a separate account; `credit` status excludes from checking balance; auto-generates payback expense in next period                  |
| Recurrence engine   | Full state machine with override/delete tracking; user prompted on conflicts; done/received/credit items never touched by regeneration |
| Categories          | Flat two-level (group + name); adjacency list deferred                                                                                 |
| Expense line items  | ~31 total, ~10-15 active per period; flat list grouped by category header (no collapse/expand needed)                                  |
| Paycheck calculator | Deferred to Phase 2; manual net pay entry in Phase 1                                                                                   |
| Scenarios           | Deferred to Phase 3; baseline-only in Phases 1-2                                                                                       |
| Audit logging       | Deferred to Phase 6; schema should stabilize first                                                                                     |
| Structured logging  | Basic logging in Phase 1; structured JSON deferred to Phase 6                                                                          |
| Undo/redo           | Deferred; not built until needed                                                                                                       |
| Notifications       | Deferred to Phase 8; frequent manual check-ins reduce urgency                                                                          |
| Multi-user          | user_id in schema from day one; registration UI deferred to Phase 6                                                                    |
| Database schemas    | Five logical schemas: ref, auth, budget, salary, system                                                                                |
| Balance calculation | Calculate on read; pure function; no stored balances                                                                                   |
| Remainder handling  | No explicit reallocation; difference stays in checking balance                                                                         |
| Salary modeling     | Full paycheck calculator in Phase 2; scenario-aware; multi-income from day one                                                         |
| Tax estimation      | Reasonable estimate, not exact payroll; user-updatable brackets                                                                        |
| 3rd paycheck        | Detected by counting paychecks in calendar month; 24-per-year deductions skipped                                                       |
| Data import         | Start fresh -- no spreadsheet import                                                                                                    |
| Hosting             | Proxmox LXC/VM, Docker Compose, Nginx reverse proxy                                                                                    |
| External access     | Cloudflare Tunnel or Tailscale (no port forwarding; ISP double-NAT)                                                                    |
| Dev environment     | Arch Linux, NeoVim/LazyVim, native PostgreSQL                                                                                          |

---

## 11. Change Log

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.6     | 2026-02-17 | Original requirements document                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 2.0     | 2026-02-20 | Major revision: replaced React with HTMX + Jinja2; replaced JWT with Flask-Login sessions; added payday workflow section; added credit card workflow with auto-payback; added carry forward as first-class operation; added recurrence engine state machine; simplified categories to flat model; reordered phases (spreadsheet replacement first, infrastructure later); added Bootstrap 5; updated project structure; updated deployment for Proxmox + Cloudflare Tunnel |
