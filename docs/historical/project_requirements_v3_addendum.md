> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Budget App -- Requirements Addendum: Extended Accounts & Financial Planning

**Version:** 3.0 **Date:** February 28, 2026 **Parent Document:** project_requirements_v2.md
**Scope:** Adds debt accounts, investment/retirement accounts, pension modeling, and HYSA interest
projection. Restructures the phase roadmap accordingly.

---

## 1. Addendum Overview

This document extends the v2 requirements with support for tracking the full lifecycle of personal
finances -- not just income and expenses flowing through checking, but where money goes and what it
does once it gets there. The core additions are:

- **High-yield savings accounts** with interest projection.
- **Debt accounts** -- mortgage (fixed and ARM) and auto loan -- with amortization, escrow, and payoff
  analysis.
- **Investment & retirement accounts** -- 401(k), Roth 401(k), Traditional IRA, Roth IRA, and taxable
  brokerage -- with compound growth projection, contribution limits, and employer contributions.
- **Pension modeling** -- projected pension income based on years of service and salary.
- **Retirement income gap analysis** -- how much additional savings are needed to maintain
  pre-retirement income.

### Core Principle: Follow the Money

Every dollar that leaves checking should be traceable to its destination. A mortgage payment reduces
checking and reduces the loan principal. A 401(k) contribution reduces gross pay and increases the
retirement account balance. A Roth IRA contribution is an expense from checking that credits the
IRA. The app models these flows explicitly using the existing transfer architecture, extended to
support new account types with type-specific financial properties.

### What This Addendum Does Not Add

The following remain out of scope:

- **Real-time market data** -- investment returns use a user-defined assumed annual rate, not live
  prices.
- **Individual stock/fund tracking** -- accounts have a single blended rate of return, not
  per-holding performance.
- **Tax optimization advice** -- the app shows projections, not recommendations.
- **Exact tax calculations on withdrawals** -- the user can apply a flat estimated tax rate to
  traditional account withdrawals.
- **Automated rebalancing** -- portfolio allocation is out of scope.

---

## 2. Revised Phase Roadmap

The original v2 phases are restructured to integrate the new account types at logical points. Phases
1 and 2 are unchanged. Phase 3 (Scenarios) is deferred to allow the financial foundation to be built
first.

**Already built (v2 phases):**

| Phase                                 | Status                                                                       |
| ------------------------------------- | ---------------------------------------------------------------------------- |
| **Phase 1 -- Replace the Spreadsheet** | ✅ Built                                                                     |
| **Phase 2 -- Paycheck Calculator**     | ✅ Built                                                                     |
| **Phase 4 -- Savings & Accounts**      | ✅ Built (savings dashboard, transfers, savings goals, balance roll-forward) |

**Note:** v2 Phase 3 (Scenarios) was **not** built and is deferred in this addendum to Phase 7.

**New and remaining phases:**

| Phase                                        | Features                                                                                                                                                                                                                                                                                          |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Phase 3 -- HYSA & Accounts Reorganization** | Extends the existing savings infrastructure (v2 Phase 4, already built) with: HYSA account type with interest projection, `category` column on `ref.account_types`, accounts dashboard reorganized by category (Asset / Liability / Retirement / Investment)                                      |
| **Phase 4 -- Debt Accounts**                  | Mortgage (fixed-rate and ARM), auto loan, escrow modeling, linked transactions (payment from checking → principal reduction), amortization engine, payoff calculator with basic inline chart                                                                                                      |
| **Phase 5 -- Investments & Retirement**       | 401(k), Roth 401(k), Traditional IRA, Roth IRA, taxable brokerage, compound growth engine, contribution limits, employer contributions (extends the existing paycheck deduction system from v2 Phase 2, already built), pension modeling, retirement income gap dashboard with basic inline chart |
| **Phase 6 -- Visualization**                  | Balance-over-time (all account types), spending by category, budget vs. actuals, amortization chart (principal vs. interest over time), investment growth curve, retirement gap waterfall, scenario comparison overlay, net pay trajectory                                                        |
| **Phase 7 -- Scenarios**                      | _(moved from v2 Phase 3, never built)_ Named scenarios, clone from baseline, scenario-scoped transactions, salary profiles, and account parameters; side-by-side comparison; balance diff highlighting; now includes debt and investment what-ifs                                                 |
| **Phase 8 -- Hardening & Ops**                | _(unchanged from v2 Phase 6)_ Audit logging, structured logging, backups, MFA, CSV export, mobile layout, registration                                                                                                                                                                            |
| **Phase 9 -- Smart Features**                 | _(unchanged from v2 Phase 7)_ Smart estimates, expense inflation, deduction inflation                                                                                                                                                                                                             |
| **Phase 10 -- Notifications**                 | _(unchanged from v2 Phase 8)_ In-app alerts, email notifications; extended to include loan payoff milestones, retirement goal milestones, contribution limit warnings                                                                                                                             |

---

## 3. Revised Scope Exclusion

The v2 requirements document lists "Debt amortization schedules" as explicitly out of scope. This
addendum removes that exclusion. The updated "What This App Is Not" section should read:

> ~~**Debt amortization schedules** -- credit card is tracked as an expense line item, not a debt
> module.~~

Replace with:

> **Credit card balance tracking** -- credit cards remain a cash flow timing tool (status + payback),
> not a tracked debt account. Mortgage and auto loan amortization are supported.

---

## 4. Detailed Requirements -- New Sections

### 4.22 Extended Account Types (Phases 3-5)

The `ref.account_types` table is extended with new types. Each type has different financial
properties stored in type-specific parameter tables. The `budget.accounts` table remains the
universal account record; type-specific data is joined via parameter tables keyed on `account_id`.

**New account types:**

| Account Type       | `ref.account_types.name` | Category   | Phase | Key Properties                                                        |
| ------------------ | ------------------------ | ---------- | ----- | --------------------------------------------------------------------- |
| High-yield savings | `hysa`                   | Asset      | 3     | APY, compounding frequency                                            |
| Mortgage           | `mortgage`               | Liability  | 4     | Principal, rate, term, ARM structure, escrow                          |
| Auto loan          | `auto_loan`              | Liability  | 4     | Principal, rate, term                                                 |
| 401(k)             | `401k`                   | Retirement | 5     | Assumed return rate, annual contribution limit, employer contribution |
| Roth 401(k)        | `roth_401k`              | Retirement | 5     | Assumed return rate, annual contribution limit                        |
| Traditional IRA    | `traditional_ira`        | Retirement | 5     | Assumed return rate, annual contribution limit                        |
| Roth IRA           | `roth_ira`               | Retirement | 5     | Assumed return rate, annual contribution limit                        |
| Brokerage          | `brokerage`              | Investment | 5     | Assumed return rate                                                   |

**Account categories** are informational groupings used for dashboard organization and do not affect
business logic:

| Category   | Meaning                                                     |
| ---------- | ----------------------------------------------------------- |
| Asset      | Accounts the user owns (checking, savings, HYSA, brokerage) |
| Liability  | Accounts the user owes (mortgage, auto loan)                |
| Retirement | Tax-advantaged retirement accounts (401k, IRA)              |
| Investment | Taxable investment accounts (brokerage)                     |

The `ref.account_types` table gains an optional `category` column:

```sql
ALTER TABLE ref.account_types ADD COLUMN category VARCHAR(20);
UPDATE ref.account_types SET category = 'asset' WHERE name IN ('checking', 'savings', 'hysa');
UPDATE ref.account_types SET category = 'liability' WHERE name IN ('mortgage', 'auto_loan');
UPDATE ref.account_types SET category = 'retirement' WHERE name IN ('401k', 'roth_401k', 'traditional_ira', 'roth_ira');
UPDATE ref.account_types SET category = 'investment' WHERE name = 'brokerage';
```

### 4.23 High-Yield Savings -- Interest Projection (Phase 3)

An HYSA behaves like a regular savings account but earns interest. The app projects future balances
by compounding interest on the projected balance.

**Account parameters (`budget.hysa_params`):**

- `account_id` -- FK to `budget.accounts`
- `apy` -- annual percentage yield (e.g., 0.0450 for 4.50%)
- `compounding_frequency` -- enum: `daily`, `monthly`, `quarterly` (default: `daily`)

**Interest projection calculation:**

Interest is projected per pay period using the APY and compounding frequency. The calculation is
applied during the balance roll-forward -- after all transactions and transfers for a period are
applied, the projected interest earned during that period is added.

```
For daily compounding:
  daily_rate = apy / 365
  period_days = end_date - start_date (of the pay period)
  interest_earned = balance_after_transactions * ((1 + daily_rate) ^ period_days - 1)

For monthly compounding:
  monthly_rate = apy / 12
  interest_earned = balance_after_transactions * monthly_rate * (period_days / days_in_month)
```

Interest is projected only -- it is not auto-generated as a transaction. It appears as a separate
line in the balance projection (e.g., "Projected interest: $X.XX") so the user can see the
contribution of interest to balance growth.

**True-up behavior:** When the user updates the HYSA anchor balance, actual earned interest is
implicitly captured (the real balance includes interest the bank has paid). No separate interest
tracking is needed for actuals.

**UI:** The HYSA appears on the savings dashboard alongside regular savings accounts. The interest
projection is shown as a tooltip or subtitle on future period balances (e.g., "$12,450.00 (incl.
~$18.50 interest)").

### 4.24 Debt Accounts -- Mortgage (Phase 4)

#### 4.24.1 Mortgage Account Setup

A mortgage is a liability account. The balance represents the remaining principal owed and decreases
over time as payments are made.

**Account parameters (`budget.mortgage_params`):**

| Column                           | Type          | Description                                                                      |
| -------------------------------- | ------------- | -------------------------------------------------------------------------------- |
| `account_id`                     | INT FK        | References `budget.accounts`                                                     |
| `original_principal`             | NUMERIC(12,2) | Original loan amount                                                             |
| `current_principal`              | NUMERIC(12,2) | Current remaining principal (anchor balance equivalent)                          |
| `interest_rate`                  | NUMERIC(7,5)  | Annual interest rate (e.g., 0.06500 for 6.5%)                                    |
| `term_months`                    | INT           | Original loan term in months (e.g., 360 for 30 years)                            |
| `origination_date`               | DATE          | Loan start date                                                                  |
| `payment_day`                    | INT           | Day of month payment is due (1-31)                                               |
| `is_arm`                         | BOOLEAN       | Whether this is an adjustable-rate mortgage                                      |
| `arm_first_adjustment_months`    | INT           | Months before first rate adjustment (e.g., 60 for 5/1 ARM); NULL if fixed        |
| `arm_adjustment_interval_months` | INT           | Months between adjustments after the first (e.g., 12 for 5/1 ARM); NULL if fixed |

**ARM rate changes:**

For adjustable-rate mortgages, the user manually inputs the new rate when it adjusts. The app stores
rate change history for projection purposes:

**Rate history table (`budget.mortgage_rate_history`):**

| Column           | Type         | Description                    |
| ---------------- | ------------ | ------------------------------ |
| `id`             | SERIAL PK    |                                |
| `account_id`     | INT FK       | References `budget.accounts`   |
| `effective_date` | DATE         | Date the new rate takes effect |
| `interest_rate`  | NUMERIC(7,5) | The new annual rate            |
| `created_at`     | TIMESTAMPTZ  |                                |

The most recent rate in `mortgage_rate_history` is the current rate. For projection purposes, the
app uses the current rate for all future periods unless the user specifies a different projected
rate (via the payoff calculator or scenarios).

#### 4.24.2 Escrow Modeling

Escrow is modeled as separate components that sum to the total escrow portion of the payment. This
allows independent tracking and projection.

**Escrow components table (`budget.escrow_components`):**

| Column           | Type          | Description                                                                 |
| ---------------- | ------------- | --------------------------------------------------------------------------- |
| `id`             | SERIAL PK     |                                                                             |
| `account_id`     | INT FK        | References `budget.accounts` (the mortgage)                                 |
| `name`           | VARCHAR(100)  | e.g., "Property Tax", "Homeowner's Insurance", "PMI"                        |
| `annual_amount`  | NUMERIC(12,2) | Current annual cost                                                         |
| `inflation_rate` | NUMERIC(5,4)  | Projected annual increase (e.g., 0.1000 for 10%). NULL = use global default |
| `is_active`      | BOOLEAN       |                                                                             |
| `created_at`     | TIMESTAMPTZ   |                                                                             |
| `updated_at`     | TIMESTAMPTZ   |                                                                             |

**Escrow calculation:**

```
monthly_escrow = SUM(each active component's annual_amount / 12)
```

For projection, each component inflates independently at its own rate:

```
projected_annual[year_n] = annual_amount * (1 + inflation_rate) ^ n
```

The user updates escrow components manually when they receive their annual escrow analysis. The
inflation rate provides a reasonable projection between updates.

**Total monthly payment:**

```
total_payment = principal_and_interest + monthly_escrow
```

The mortgage payment appears in the budget grid as a single expense from checking. The mortgage
account dashboard breaks it into components (principal, interest, escrow) for visibility.

#### 4.24.3 Amortization Engine

The amortization engine is a pure function service (`services/amortization_engine.py`) that
calculates the payment schedule given loan parameters.

**Input:** current principal, annual rate, remaining term (months), extra payment amount (optional).

**Output:** list of `(month, payment, principal_portion, interest_portion, remaining_balance)`.

**Standard amortization formula:**

```
monthly_rate = annual_rate / 12
monthly_payment = principal * (monthly_rate * (1 + monthly_rate)^term) / ((1 + monthly_rate)^term - 1)

For each month:
  interest = remaining_principal * monthly_rate
  principal_portion = monthly_payment - interest + extra_payment
  remaining_principal -= principal_portion
```

**Summary metrics (always displayed):**

- Current principal balance
- Monthly P&I payment (excluding escrow)
- Total monthly payment (P&I + escrow)
- Original payoff date (based on standard schedule)
- Projected payoff date (with any extra payments)
- Total interest remaining
- Interest saved (if extra payments are being made)
- Months saved (if extra payments are being made)

**Full amortization schedule (deferred -- nice-to-have):**

A toggle to show the full month-by-month breakdown table. Lower priority than summary metrics and
payoff scenarios.

#### 4.24.4 Linked Transactions -- Mortgage Payments

A mortgage payment is modeled as a **transfer from checking to the mortgage account**, extending the
existing `budget.transfers` table. This ensures:

- The expense appears in the checking account's budget grid.
- The mortgage principal balance decreases accordingly.
- The transfer amount equals the full PITI payment (principal + interest + escrow).

**How principal reduction is calculated:**

The amortization engine determines how much of each payment goes to principal vs. interest. The
transfer reduces checking by the full payment amount. On the mortgage account side, only the
principal portion reduces the balance -- interest is the cost of the loan and does not reduce the
principal.

**Implementation detail:** The transfer stores the full payment amount. The mortgage balance
calculator calls the amortization engine to determine the principal portion for each period and
applies only that amount as the balance reduction. This keeps the transfer simple while the
amortization math lives in the service layer.

**True-up behavior:** The user periodically updates the mortgage's anchor balance (remaining
principal) from their lender's statement. This corrects for any rounding differences or extra
payments made outside the app.

#### 4.24.5 Payoff Calculator (Phase 4)

An interactive tool on the mortgage dashboard for exploring payoff scenarios.

**Mode 1 -- Extra payment:** User enters an extra monthly amount. The calculator shows:

- New payoff date
- Months saved
- Total interest saved
- New monthly payment amount (base P&I + extra)

**Mode 2 -- Target payoff date:** User enters a desired payoff date. The calculator shows:

- Required extra monthly payment
- Total interest at the new payoff date
- Interest savings vs. standard schedule

**UI approach:** Start with a form-based input (enter value, click calculate, see results). Build
interactivity (slider or live-updating input) in Phase 6 (Visualization) or when revisiting the UI
for polish. The form approach is simpler to build with HTMX -- an `hx-post` to a calculation endpoint
that returns a results fragment.

**Basic inline chart:** A simple line chart (Chart.js) showing the principal balance over time for
the standard schedule vs. the accelerated schedule. This is built alongside the payoff calculator,
not deferred to the Visualization phase, because the chart is core to understanding the payoff
scenario.

### 4.25 Debt Accounts -- Auto Loan (Phase 4)

The auto loan is a simplified version of the mortgage -- a standard fixed-rate installment loan with
no escrow and no ARM complexity.

**Account parameters (`budget.auto_loan_params`):**

| Column               | Type          | Description                  |
| -------------------- | ------------- | ---------------------------- |
| `account_id`         | INT FK        | References `budget.accounts` |
| `original_principal` | NUMERIC(12,2) | Original loan amount         |
| `current_principal`  | NUMERIC(12,2) | Current remaining principal  |
| `interest_rate`      | NUMERIC(7,5)  | Annual interest rate         |
| `term_months`        | INT           | Original loan term in months |
| `origination_date`   | DATE          | Loan start date              |
| `payment_day`        | INT           | Day of month payment is due  |

**Behavior:**

- Uses the same amortization engine as the mortgage (the engine is loan-type-agnostic).
- Payment flows through checking as a transfer, same as the mortgage.
- Summary metrics: current balance, monthly payment, payoff date, total interest remaining.
- No payoff scenario calculator needed (per user requirements -- tracking balance and schedule is
  sufficient).
- A basic balance-over-time chart is shown on the auto loan dashboard.

### 4.26 Investment & Retirement Accounts (Phase 5)

#### 4.26.1 Account Parameters

All investment and retirement accounts share a common parameter structure. The key difference
between account types is how contributions enter the account (pre-tax paycheck deduction vs.
post-checking expense) and contribution limits.

**Common parameters (`budget.investment_params`):**

| Column                          | Type          | Description                                                                                      |
| ------------------------------- | ------------- | ------------------------------------------------------------------------------------------------ |
| `account_id`                    | INT FK        | References `budget.accounts`                                                                     |
| `assumed_annual_return`         | NUMERIC(7,5)  | Expected annual rate of return (e.g., 0.0700 for 7%)                                             |
| `annual_contribution_limit`     | NUMERIC(12,2) | IRS annual contribution limit; NULL if none (brokerage)                                          |
| `contribution_limit_year`       | INT           | Tax year the limit applies to (for annual updates)                                               |
| `employer_contribution_type`    | VARCHAR(20)   | `none`, `flat_percentage`, `match`; default `none`                                               |
| `employer_flat_percentage`      | NUMERIC(5,4)  | For `flat_percentage`: employer contributes this % of salary regardless of employee contribution |
| `employer_match_percentage`     | NUMERIC(5,4)  | For `match`: employer matches up to this % of salary                                             |
| `employer_match_cap_percentage` | NUMERIC(5,4)  | For `match`: maximum salary % matched (e.g., 0.0600 = matches on first 6% of salary)             |

**Contribution limits (2025 values for reference -- user-updatable):**

| Account Type    | Employee Limit | Catch-up (50+) |
| --------------- | -------------- | -------------- |
| 401(k)          | $23,500        | +$7,500        |
| Roth 401(k)     | $23,500        | +$7,500        |
| Traditional IRA | $7,000         | +$1,000        |
| Roth IRA        | $7,000         | +$1,000        |
| Brokerage       | No limit       | N/A            |

The app tracks cumulative contributions per calendar year and warns the user when a projected
contribution would exceed the annual limit. Projections are capped at the annual limit -- the growth
engine does not project contributions beyond the limit.

#### 4.26.2 Contribution Flow -- How Money Enters Accounts

**Pre-tax paycheck deductions (401(k), Roth 401(k)):**

These contributions are already modeled as paycheck deductions in the Phase 2 salary profile
(§4.13). The extension is: when a paycheck is marked as `received`, the app credits the
corresponding retirement account with the deduction amount.

**Implementation:**

- Each `salary.paycheck_deductions` record gains an optional `target_account_id` column (FK to
  `budget.accounts`). When set, the deduction amount is automatically applied as income to the
  target account each time the paycheck is processed.
- Employer contributions (flat percentage or match) are calculated by the paycheck calculator and
  added to the target account as a separate credited amount.
- The retirement account's balance roll-forward includes both employee and employer contributions.

**Post-checking contributions (Roth IRA, Traditional IRA, Brokerage):**

These are standard transfers from checking to the investment account. They appear as expenses in the
budget grid and as income in the investment account. The existing `budget.transfers` table handles
this without modification.

The user sets up a recurring transfer (using the existing recurrence engine) for periodic
contributions, or creates one-time transfers as needed.

#### 4.26.3 Compound Growth Engine

A pure function service (`services/growth_engine.py`) that projects investment account balances
forward over time.

**Input:**

- Current balance (anchor)
- Assumed annual rate of return
- Periodic contribution amount and frequency
- Employer contribution amount (if applicable)
- Annual contribution limit (if applicable)
- Projection horizon (in periods)

**Calculation (per pay period):**

```
period_return_rate = (1 + annual_return) ^ (period_days / 365) - 1

For each period:
  growth = current_balance * period_return_rate
  contribution = MIN(periodic_contribution, remaining_annual_limit)
  employer_contribution = calculated per employer rules
  new_balance = current_balance + growth + contribution + employer_contribution
  remaining_annual_limit -= contribution
  (reset remaining_annual_limit at calendar year boundary)
```

**Important:** Growth is applied to the balance _before_ the period's contribution is added. This
models the reality that existing investments grow while new money is contributed.

**True-up behavior:** The user updates the account's anchor balance from their brokerage/retirement
account statement. This captures actual market performance (which will differ from the assumed rate)
and any transactions not modeled in the app.

#### 4.26.4 Contribution Limit Tracking

The app tracks cumulative contributions per account per calendar year:

**Contribution tracking view (`budget.contribution_tracker`):**

This is a computed view (or calculated on read, consistent with the app's design philosophy), not a
stored table. For each investment/retirement account:

```sql
-- Conceptual query (actual implementation in the service layer)
SELECT
    account_id,
    EXTRACT(YEAR FROM pp.start_date) AS contribution_year,
    SUM(t.amount) AS ytd_contributions
FROM budget.transfers t
JOIN budget.pay_periods pp ON t.pay_period_id = pp.id
WHERE t.to_account_id = :account_id
GROUP BY account_id, EXTRACT(YEAR FROM pp.start_date);
```

**UI behavior:**

- The account dashboard shows: "2026 contributions: $4,500 / $7,000 (64%)" with a progress bar.
- If a projected future contribution would exceed the annual limit, the app displays a warning icon
  on that period's transfer in the grid.
- The growth engine caps contributions at the limit in projections -- it does not project overage.

#### 4.26.5 Employer Contributions

**Flat percentage (no employee contribution required):**

The employer contributes a fixed percentage of gross salary regardless of employee participation.

```
employer_contribution_per_period = gross_biweekly_pay * employer_flat_percentage
```

This is calculated by the paycheck calculator and credited to the target retirement account
alongside the employee's deduction (if any).

**Match (employee contribution required):**

The employer matches employee contributions up to a cap.

```
matchable_salary = gross_biweekly_pay * employer_match_cap_percentage
employee_contribution_this_period = paycheck_deduction_amount
matched_amount = MIN(employee_contribution_this_period, matchable_salary) * employer_match_percentage
```

Example: Employer matches 100% up to 6% of salary. If gross biweekly pay is $2,500 and the employee
contributes $150 (6%): matched = MIN($150, $150) \* 1.00 = $150. Total to 401(k): $300.

### 4.27 Pension Modeling (Phase 5)

#### 4.27.1 Pension Profile

The pension is not an account the user contributes to -- it is a defined-benefit plan that pays
income in retirement based on service and salary. It is modeled as a separate entity, not an account
type.

**Pension profile table (`salary.pension_profiles`):**

| Column                     | Type         | Description                                                                      |
| -------------------------- | ------------ | -------------------------------------------------------------------------------- |
| `id`                       | SERIAL PK    |                                                                                  |
| `user_id`                  | INT FK       | References `auth.users`                                                          |
| `salary_profile_id`        | INT FK       | References `salary.salary_profiles` (links to the job that provides the pension) |
| `name`                     | VARCHAR(100) | e.g., "State Pension"                                                            |
| `benefit_multiplier`       | NUMERIC(7,5) | Percentage per year of service (e.g., 0.0185 for 1.85%)                          |
| `consecutive_high_years`   | INT          | Number of highest consecutive salary years averaged (e.g., 4)                    |
| `hire_date`                | DATE         | Used to calculate years of service                                               |
| `earliest_retirement_date` | DATE         | Earliest date pension benefits can begin (nullable)                              |
| `planned_retirement_date`  | DATE         | User's intended retirement date                                                  |
| `is_active`                | BOOLEAN      |                                                                                  |
| `created_at`               | TIMESTAMPTZ  |                                                                                  |
| `updated_at`               | TIMESTAMPTZ  |                                                                                  |

#### 4.27.2 Pension Benefit Calculation

```
years_of_service = (planned_retirement_date - hire_date) in years
high_salary_average = average of the [consecutive_high_years] highest consecutive annual salaries
  (projected using raises from the linked salary profile)
annual_pension_benefit = benefit_multiplier * years_of_service * high_salary_average
monthly_pension_benefit = annual_pension_benefit / 12
```

**Example:** 1.85% × 25 years × $85,000 average = $39,312.50/year = $3,276.04/month.

The salary projection from Phase 2 provides the future salary data needed to project the high-salary
average. The pension calculator uses the same raise schedule to project forward.

### 4.28 Retirement Income Gap Analysis (Phase 5)

This is the dashboard view that ties together pension income, retirement account projections, and
pre-retirement income to answer: "How much more do I need to save?"

**Calculation:**

```
Step 1: Determine pre-retirement net monthly income
  = Current net biweekly paycheck * 26 / 12
  (Uses the Phase 2 paycheck calculator for the most recent salary profile)

Step 2: Determine projected monthly pension income
  = annual_pension_benefit / 12
  (From §4.27.2)

Step 3: Calculate the monthly income gap
  = pre_retirement_net_monthly - monthly_pension_income

Step 4: Determine required retirement savings
  = monthly_gap * 12 / safe_withdrawal_rate
  (Default safe withdrawal rate: 0.04 -- the "4% rule". Configurable in user settings.)

Step 5: Project total retirement savings at planned retirement date
  = SUM of projected balances for all retirement + investment accounts at retirement date
  (From the compound growth engine, §4.26.3)

Step 6: Determine the gap (or surplus)
  = required_retirement_savings - projected_total_savings
```

**User settings additions (`auth.user_settings`):**

- `safe_withdrawal_rate` -- NUMERIC(5,4), default 0.0400
- `planned_retirement_date` -- DATE, nullable
- `estimated_retirement_tax_rate` -- NUMERIC(5,4), nullable. If set, applied to traditional account
  (401(k), Traditional IRA) projected withdrawals to estimate net retirement income.

**Dashboard display:**

| Metric                       | Value           |
| ---------------------------- | --------------- |
| Current net monthly income   | $X,XXX          |
| Projected monthly pension    | $X,XXX          |
| Monthly income gap           | $X,XXX          |
| Required savings (4% rule)   | $XXX,XXX        |
| Projected retirement savings | $XXX,XXX        |
| **Surplus / shortfall**      | **+/- $XX,XXX** |

If a tax rate is set, the dashboard also shows an "after-tax" view that reduces traditional account
balances by the estimated tax rate to show a more realistic net income projection.

**Basic inline chart:** A simple bar or waterfall chart showing the income sources and the gap.
Built alongside the dashboard, not deferred to the Visualization phase.

---

## 5. Data Model Changes

### 5.1 New Reference Data

```sql
-- Extended account types
INSERT INTO ref.account_types (name, category) VALUES
    ('hysa', 'asset'),
    ('mortgage', 'liability'),
    ('auto_loan', 'liability'),
    ('401k', 'retirement'),
    ('roth_401k', 'retirement'),
    ('traditional_ira', 'retirement'),
    ('roth_ira', 'retirement'),
    ('brokerage', 'investment');
```

### 5.2 New Tables -- Phase 3 (HYSA)

```sql
CREATE TABLE budget.hysa_params (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL UNIQUE REFERENCES budget.accounts(id) ON DELETE CASCADE,
    apy NUMERIC(7,5) NOT NULL DEFAULT 0.04500,
    compounding_frequency VARCHAR(10) NOT NULL DEFAULT 'daily'
        CHECK (compounding_frequency IN ('daily', 'monthly', 'quarterly')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.3 New Tables -- Phase 4 (Debt Accounts)

```sql
CREATE TABLE budget.mortgage_params (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL UNIQUE REFERENCES budget.accounts(id) ON DELETE CASCADE,
    original_principal NUMERIC(12,2) NOT NULL,
    current_principal NUMERIC(12,2) NOT NULL,
    interest_rate NUMERIC(7,5) NOT NULL,
    term_months INT NOT NULL,
    origination_date DATE NOT NULL,
    payment_day INT NOT NULL CHECK (payment_day >= 1 AND payment_day <= 31),
    is_arm BOOLEAN NOT NULL DEFAULT FALSE,
    arm_first_adjustment_months INT,
    arm_adjustment_interval_months INT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE budget.mortgage_rate_history (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES budget.accounts(id) ON DELETE CASCADE,
    effective_date DATE NOT NULL,
    interest_rate NUMERIC(7,5) NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_mortgage_rate_history_account
    ON budget.mortgage_rate_history(account_id, effective_date DESC);

CREATE TABLE budget.escrow_components (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL REFERENCES budget.accounts(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    annual_amount NUMERIC(12,2) NOT NULL,
    inflation_rate NUMERIC(5,4),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(account_id, name)
);

CREATE TABLE budget.auto_loan_params (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL UNIQUE REFERENCES budget.accounts(id) ON DELETE CASCADE,
    original_principal NUMERIC(12,2) NOT NULL,
    current_principal NUMERIC(12,2) NOT NULL,
    interest_rate NUMERIC(7,5) NOT NULL,
    term_months INT NOT NULL,
    origination_date DATE NOT NULL,
    payment_day INT NOT NULL CHECK (payment_day >= 1 AND payment_day <= 31),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.4 New Tables -- Phase 5 (Investment & Retirement)

```sql
CREATE TABLE budget.investment_params (
    id SERIAL PRIMARY KEY,
    account_id INT NOT NULL UNIQUE REFERENCES budget.accounts(id) ON DELETE CASCADE,
    assumed_annual_return NUMERIC(7,5) NOT NULL DEFAULT 0.07000,
    annual_contribution_limit NUMERIC(12,2),
    contribution_limit_year INT,
    employer_contribution_type VARCHAR(20) NOT NULL DEFAULT 'none'
        CHECK (employer_contribution_type IN ('none', 'flat_percentage', 'match')),
    employer_flat_percentage NUMERIC(5,4),
    employer_match_percentage NUMERIC(5,4),
    employer_match_cap_percentage NUMERIC(5,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE salary.pension_profiles (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    salary_profile_id INT REFERENCES salary.salary_profiles(id) ON DELETE SET NULL,
    name VARCHAR(100) NOT NULL DEFAULT 'Pension',
    benefit_multiplier NUMERIC(7,5) NOT NULL,
    consecutive_high_years INT NOT NULL DEFAULT 4,
    hire_date DATE NOT NULL,
    earliest_retirement_date DATE,
    planned_retirement_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5.5 Schema Modifications to Existing Tables

```sql
-- Add category to account types (Phase 3 migration)
ALTER TABLE ref.account_types ADD COLUMN category VARCHAR(20);

-- Link paycheck deductions to target accounts (Phase 5 migration)
ALTER TABLE salary.paycheck_deductions
    ADD COLUMN target_account_id INT REFERENCES budget.accounts(id) ON DELETE SET NULL;

-- Add retirement planning settings (Phase 5 migration)
ALTER TABLE auth.user_settings
    ADD COLUMN safe_withdrawal_rate NUMERIC(5,4) DEFAULT 0.0400,
    ADD COLUMN planned_retirement_date DATE,
    ADD COLUMN estimated_retirement_tax_rate NUMERIC(5,4);
```

### 5.6 Updated Entity Relationship Summary

```
budget (extended)
 ├── accounts (1:N per user) → ref.account_types
 │    ├── account_anchor_history (1:N)
 │    ├── hysa_params (1:1 -- Phase 3, for HYSA accounts only)
 │    ├── mortgage_params (1:1 -- Phase 4, for mortgage accounts only)
 │    │    ├── mortgage_rate_history (1:N)
 │    │    └── escrow_components (1:N)
 │    ├── auto_loan_params (1:1 -- Phase 4, for auto loan accounts only)
 │    └── investment_params (1:1 -- Phase 5, for investment/retirement accounts)
 ├── transfers (1:N -- now used for all inter-account flows)
 └── savings_goals (1:N -- unchanged)

salary (extended)
 ├── salary_profiles (existing)
 │    └── paycheck_deductions (existing -- gains target_account_id)
 └── pension_profiles (1:N per user -- Phase 5)
```

---

## 6. New Services

### 6.1 Interest Projection Service (`services/interest_projection.py`) -- Phase 3

- Pure function: given HYSA params, balance, and date range → projected interest per period.
- Called by the balance calculator when computing HYSA balances.

### 6.2 Amortization Engine (`services/amortization_engine.py`) -- Phase 4

- Pure function: given loan params (principal, rate, term) and optional extra payment → full
  schedule or summary metrics.
- Shared by mortgage and auto loan -- the engine is loan-type-agnostic.
- Handles both fixed-rate and ARM (using rate history for rate changes).
- Methods:
  - `calculate_monthly_payment(principal, annual_rate, remaining_months) → Decimal`
  - `generate_schedule(params, extra_monthly=0) → list[AmortizationRow]`
  - `calculate_summary(params, extra_monthly=0) → AmortizationSummary`
  - `calculate_payoff_by_date(params, target_date) → Decimal` (returns required extra monthly)

### 6.3 Escrow Calculator (`services/escrow_calculator.py`) -- Phase 4

- Pure function: given escrow components → monthly escrow amount.
- Projects escrow forward using per-component inflation rates.
- `calculate_monthly_escrow(components, as_of_date=None) → Decimal`
- `project_annual_escrow(components, years_forward) → list[(year, amount)]`

### 6.4 Compound Growth Engine (`services/growth_engine.py`) -- Phase 5

- Pure function: given account params, current balance, contributions, and horizon → projected
  balances per period.
- Handles contribution limits (caps at annual limit, resets at year boundary).
- Incorporates employer contributions when applicable.
- `project_balance(params, current_balance, contributions, periods) → list[ProjectedBalance]`

### 6.5 Pension Calculator (`services/pension_calculator.py`) -- Phase 5

- Pure function: given pension profile and salary projection → annual and monthly benefit.
- Uses the salary projection from Phase 2 to compute the high-salary average.
- `calculate_benefit(pension_profile, salary_projections) → PensionBenefit`

### 6.6 Retirement Gap Calculator (`services/retirement_gap_calculator.py`) -- Phase 5

- Orchestrates pension calculator, growth engine, and paycheck calculator to produce the gap
  analysis.
- `calculate_gap(user) → RetirementGapAnalysis`

---

## 7. New Routes & URL Structure

### Phase 3 Additions

| Method | URL                          | Returns  | Description                                |
| ------ | ---------------------------- | -------- | ------------------------------------------ |
| `GET`  | `/accounts/<id>/hysa`        | Page     | HYSA detail view with interest projections |
| `POST` | `/accounts/<id>/hysa/params` | Redirect | Update HYSA parameters (APY, compounding)  |

### Phase 4 Additions

| Method   | URL                                    | Returns  | Description                                             |
| -------- | -------------------------------------- | -------- | ------------------------------------------------------- |
| `GET`    | `/accounts/<id>/mortgage`              | Page     | Mortgage dashboard (summary, escrow, payoff calculator) |
| `POST`   | `/accounts/<id>/mortgage/params`       | Redirect | Update mortgage parameters                              |
| `POST`   | `/accounts/<id>/mortgage/rate`         | Fragment | Record a rate change (ARM)                              |
| `GET`    | `/accounts/<id>/mortgage/escrow`       | Fragment | Escrow component list                                   |
| `POST`   | `/accounts/<id>/mortgage/escrow`       | Fragment | Add/update escrow component                             |
| `DELETE` | `/accounts/<id>/mortgage/escrow/<cid>` | Fragment | Remove escrow component                                 |
| `POST`   | `/accounts/<id>/mortgage/payoff`       | Fragment | Calculate payoff scenario (HTMX)                        |
| `GET`    | `/accounts/<id>/auto-loan`             | Page     | Auto loan dashboard                                     |
| `POST`   | `/accounts/<id>/auto-loan/params`      | Redirect | Update auto loan parameters                             |

### Phase 5 Additions

| Method | URL                                | Returns  | Description                                  |
| ------ | ---------------------------------- | -------- | -------------------------------------------- |
| `GET`  | `/accounts/<id>/investment`        | Page     | Investment/retirement account dashboard      |
| `POST` | `/accounts/<id>/investment/params` | Redirect | Update investment parameters                 |
| `GET`  | `/retirement`                      | Page     | Retirement planning dashboard (gap analysis) |
| `GET`  | `/retirement/pension`              | Page     | Pension profile management                   |
| `POST` | `/retirement/pension`              | Redirect | Create/update pension profile                |
| `GET`  | `/retirement/gap`                  | Fragment | Retirement income gap calculation (HTMX)     |

---

## 8. UI -- New Views

### 8.1 Accounts Dashboard (Phase 3, extended in 4 & 5)

The existing savings dashboard is expanded into a unified accounts dashboard that groups accounts by
category:

- **Assets** -- Checking, Savings, HYSA (with interest projection)
- **Liabilities** -- Mortgage, Auto Loan (with remaining balance and payoff date)
- **Retirement** -- 401(k), Roth 401(k), IRA, Roth IRA (with projected balances)
- **Investments** -- Brokerage (with projected balance)

Each account card shows: current balance, a key metric (interest rate, rate of return, payoff date),
and a link to the account-specific dashboard.

### 8.2 Mortgage Dashboard (Phase 4)

- **Summary card:** Current principal, interest rate, monthly payment breakdown (P&I + escrow),
  original and projected payoff dates.
- **Escrow section:** List of escrow components with individual amounts and inflation rates.
  Editable inline.
- **Payoff calculator:** Form with two tabs -- "Extra Payment" (input: extra $/month) and "Target
  Date" (input: desired payoff date). Results update via HTMX.
- **Payoff chart:** Chart.js line chart -- principal balance over time for standard vs. accelerated
  schedule.
- **Rate history:** For ARM accounts, a table of historical rate changes.

### 8.3 Investment/Retirement Dashboard (Phase 5)

- **Account summary card:** Current balance, assumed return rate, YTD contributions vs. limit (with
  progress bar), projected balance at retirement.
- **Employer contribution display:** Shows employer contribution amount per paycheck and type (flat
  or match).
- **Growth projection chart:** Chart.js line chart -- balance over time with contributions and growth
  layered (stacked area or dual-line).

### 8.4 Retirement Planning Dashboard (Phase 5)

- **Income gap summary:** The table from §4.28 -- current income, pension, gap, required savings,
  projected savings, surplus/shortfall.
- **Pension details:** Benefit multiplier, years of service (current and projected), high-salary
  average, projected monthly benefit.
- **Gap chart:** Bar or waterfall chart -- income sources stacked, gap highlighted.
- **Sensitivity note:** A disclaimer that projections are estimates based on assumed rates of return
  and the 4% rule, and actual results will vary.

---

## 9. Updated Project Structure

New files added to the existing project structure:

```
app/
├── models/
│   ├── hysa_params.py              # budget.hysa_params (Phase 3)
│   ├── mortgage_params.py          # budget.mortgage_params, rate_history, escrow (Phase 4)
│   ├── auto_loan_params.py         # budget.auto_loan_params (Phase 4)
│   ├── investment_params.py        # budget.investment_params (Phase 5)
│   └── pension_profile.py          # salary.pension_profiles (Phase 5)
│
├── routes/
│   ├── mortgage.py                 # /accounts/<id>/mortgage/* (Phase 4)
│   ├── auto_loan.py                # /accounts/<id>/auto-loan/* (Phase 4)
│   ├── investment.py               # /accounts/<id>/investment/* (Phase 5)
│   └── retirement.py               # /retirement/* (Phase 5)
│
├── services/
│   ├── interest_projection.py      # HYSA interest calculation (Phase 3)
│   ├── amortization_engine.py      # Loan amortization (Phase 4)
│   ├── escrow_calculator.py        # Escrow projection (Phase 4)
│   ├── growth_engine.py            # Investment compound growth (Phase 5)
│   ├── pension_calculator.py       # Pension benefit calculation (Phase 5)
│   └── retirement_gap_calculator.py # Gap analysis orchestrator (Phase 5)
│
├── schemas/
│   ├── mortgage.py                 # Marshmallow schemas (Phase 4)
│   ├── auto_loan.py                # (Phase 4)
│   ├── investment.py               # (Phase 5)
│   └── pension.py                  # (Phase 5)
│
├── templates/
│   ├── accounts/
│   │   └── dashboard.html          # Unified accounts dashboard (Phase 3, extended 4 & 5)
│   ├── mortgage/
│   │   ├── dashboard.html          # Mortgage dashboard page (Phase 4)
│   │   ├── _payoff_results.html    # HTMX fragment: payoff calculator results
│   │   ├── _escrow_list.html       # HTMX fragment: escrow components
│   │   └── _rate_history.html      # HTMX fragment: ARM rate changes
│   ├── auto_loan/
│   │   └── dashboard.html          # Auto loan dashboard (Phase 4)
│   ├── investment/
│   │   └── dashboard.html          # Investment account dashboard (Phase 5)
│   └── retirement/
│       ├── dashboard.html          # Retirement planning overview (Phase 5)
│       ├── pension_form.html       # Pension profile form (Phase 5)
│       └── _gap_analysis.html      # HTMX fragment: gap calculation results
│
└── tests/
    ├── test_services/
    │   ├── test_interest_projection.py
    │   ├── test_amortization_engine.py
    │   ├── test_escrow_calculator.py
    │   ├── test_growth_engine.py
    │   ├── test_pension_calculator.py
    │   └── test_retirement_gap_calculator.py
    └── test_routes/
        ├── test_mortgage.py
        ├── test_auto_loan.py
        ├── test_investment.py
        └── test_retirement.py
```

---

## 10. Development Roadmap -- New Phases

### Phase 3 -- HYSA & Accounts Reorganization (Weeks 9-12)

This phase extends the **already-built** savings infrastructure (v2 Phase 4) rather than building it
from scratch. The existing savings dashboard, savings goals, transfers, and balance roll-forward are
all in place.

- [ ] Add `category` column to `ref.account_types`; backfill existing types (`checking` → asset,
      `savings` → asset)
- [ ] Seed new account type: `hysa` (category: asset)
- [ ] HYSA parameter table + model (`budget.hysa_params`)
- [ ] Interest projection service (pure function, daily/monthly/quarterly compounding)
- [ ] Integrate interest projection into existing balance calculator for HYSA accounts
- [ ] Reorganize existing accounts/savings dashboard into unified accounts dashboard grouped by
      category
- [ ] HYSA detail view with interest projections
- [ ] Update navigation: savings dashboard route becomes the unified accounts dashboard
- [ ] Test suite: interest projection service, HYSA balance calculation, accounts dashboard
      rendering
- [ ] Verify all existing savings tests still pass (no regressions)

### Phase 4 -- Debt Accounts (Weeks 13-18)

- [ ] Seed new account types: `mortgage`, `auto_loan`
- [ ] Mortgage parameter table + model
- [ ] Mortgage rate history table + model
- [ ] Escrow components table + model
- [ ] Amortization engine service (pure function -- shared by mortgage and auto loan)
- [ ] Escrow calculator service
- [ ] Auto loan parameter table + model
- [ ] Linked transactions: mortgage/loan payments as transfers from checking
- [ ] Mortgage balance calculator (applies only principal portion from amortization)
- [ ] Auto loan balance calculator
- [ ] Mortgage dashboard with summary metrics, escrow management, rate history
- [ ] Payoff calculator (form-based: extra payment mode + target date mode)
- [ ] Payoff chart (Chart.js -- standard vs. accelerated schedule)
- [ ] Auto loan dashboard with summary metrics and balance chart
- [ ] Test suite: amortization engine, escrow calculator, payoff scenarios

### Phase 5 -- Investments & Retirement (Weeks 19-26)

This phase extends the **already-built** paycheck calculator (v2 Phase 2). The salary profiles,
paycheck deductions, and tax calculator are all in place. The key integration point is adding
`target_account_id` to the existing `salary.paycheck_deductions` table so that deductions can credit
retirement accounts.

- [ ] Seed new account types: `401k`, `roth_401k`, `traditional_ira`, `roth_ira`, `brokerage`
- [ ] Investment parameter table + model
- [ ] Add `target_account_id` to `salary.paycheck_deductions`
- [ ] Wire paycheck deductions to credit target retirement accounts on paycheck receipt
- [ ] Employer contribution calculation (flat percentage and match)
- [ ] Compound growth engine service
- [ ] Contribution limit tracking (cumulative per calendar year, with warnings)
- [ ] Pension profile table + model
- [ ] Pension calculator service
- [ ] Retirement gap calculator service
- [ ] Add retirement planning settings to `auth.user_settings`
- [ ] Investment/retirement account dashboard with growth projection
- [ ] Retirement planning dashboard with gap analysis
- [ ] Pension profile management (CRUD)
- [ ] Growth projection chart (Chart.js)
- [ ] Retirement gap chart (bar/waterfall)
- [ ] Test suite: growth engine, pension calculator, retirement gap calculator, contribution limits

---

## 11. Key Technical Decisions (Addendum)

| Decision               | Choice                                 | Rationale                                                                                                              |
| ---------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Type-specific params   | Separate tables per account type       | Avoids wide sparse table; each type has clean schema; joins are cheap at this scale                                    |
| Amortization math      | Pure function service                  | Consistent with balance calculator pattern; no state; easy to test                                                     |
| Growth projection      | Pure function service                  | Same pattern; assumed return rate keeps it simple; user true-ups cover market reality                                  |
| Debt balance direction | Liabilities stored as positive numbers | Simpler math; the account type's category indicates it's owed money; no negative balance confusion                     |
| Pension modeling       | Separate from accounts                 | A pension is not an account the user contributes to; it's a benefit formula. Different enough to warrant its own model |
| Contribution limits    | Calculated on read                     | Consistent with "no stored balances" philosophy; contributions are just transfers; sum them per year when needed       |
| Inline charts          | Built with each feature phase          | A mortgage dashboard without a payoff chart is incomplete; deferring all charts loses context                          |
| Payoff calculator      | Form-first, interactivity later        | HTMX form → results fragment is simple and reliable; slider/live-update can be added in visualization phase            |
| ARM rate changes       | Manual input by user                   | Rate adjustments happen infrequently (annually at most); automated rate projection adds complexity with little value   |
| Escrow inflation       | Per-component rates                    | Property tax and insurance inflate at different rates; separate tracking allows accurate projection                    |

---

## 12. Resolved Design Decisions (Addendum)

| Question                | Decision                                                                                 |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| Mortgage type           | Fixed-rate and 5/1 ARM supported; user manually inputs new ARM rate                      |
| Escrow modeling         | Separate components (tax, insurance) with independent inflation rates                    |
| Payoff scenarios        | Extra monthly payment and target payoff date; no refinancing comparison in initial build |
| Auto loan complexity    | Balance and schedule tracking only; no payoff scenario calculator                        |
| Investment return model | Single assumed annual rate per account; user true-ups handle real market performance     |
| Employer 401(k)         | Flat percentage (no match required) and traditional match both supported                 |
| Pension formula         | 1.85% × consecutive high years average × years of service                                |
| Retirement income gap   | Pre-retirement net income minus pension, remainder covered by 4% withdrawal rule         |
| Contribution limits     | Tracked and enforced in projections; user warned on projected overage                    |
| Chart timing            | Basic charts built inline with each feature; advanced visualization in Phase 6           |
| Scenario timing         | Deferred to Phase 7; financial foundation built first                                    |

---

## 13. Change Log

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3.0     | 2026-02-28 | Addendum: extended account types (HYSA, mortgage, auto loan, 401(k), Roth 401(k), Traditional IRA, Roth IRA, brokerage); amortization engine with ARM and escrow support; compound growth engine; pension modeling; retirement income gap analysis; contribution limit tracking; employer contribution modeling; restructured phase roadmap (scenarios moved to Phase 7, debt accounts Phase 4, investments Phase 5); removed "debt amortization schedules" from out-of-scope list; added inline charts per feature phase |
| 3.0.1   | 2026-03-04 | Clarified that v2 Phases 1, 2, and 4 are already built; Phase 3 renamed to "HYSA & Accounts Reorganization" to reflect that it extends existing savings infrastructure; Phase 5 notes clarify it extends the existing paycheck deduction system; added build status table                                                                                                                                                                                                                                                 |
