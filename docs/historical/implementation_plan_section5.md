> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Section 5 -- Debt and Account Improvements

**Version:** 1.1
**Date:** April 1, 2026
**Prerequisite:** All Sections 3, 3A, 4, 4A, 4B, and 5A are implemented, tested, and merged.
**Scope:** Tasks 5.1--5.5, 5.7--5.16 from `docs/project_roadmap_v4-3.md`. Tasks 5.2 and 5.3
were previously removed (resolved by sections 3.2 and 3.10) but are now reassigned to new
work: Investment Contribution Linkage (5.2) and Investment What-If Calculator (5.3). Task 5.6
is already complete (see Discrepancies section).

---

## Documentation vs. Code Discrepancies

The following discrepancies were found between `docs/project_roadmap_v4-2.md` and the current
codebase. The implementation plan is based on the code, not the documentation.

### D-1: Task 5.6 is already complete

**Roadmap says:** "The `savings.py:dashboard` route function is approximately 470 lines and
mixes HTTP routing concerns with complex financial calculations. Create
`services/savings_dashboard_service.py`."

**Code says:** `app/services/savings_dashboard_service.py` already exists (549 lines).
`app/routes/savings.py` is 152 lines -- a thin routing layer that calls
`savings_dashboard_service.compute_dashboard_data(user_id)` and renders the template. The SRP
extraction was completed during Section 4A.

**Impact:** Task 5.6 is removed from this plan. Tasks 5.4 and 5.15, which the roadmap says
depend on 5.6, will modify the existing `savings_dashboard_service.py` directly.

### D-2: retirement_dashboard_service.py also exists

**Roadmap says:** "The retirement dashboard (`retirement.py:_compute_gap_data`, ~350 lines, same
SRP violation) is a candidate for the same treatment but is not in scope for Section 5."

**Code says:** `app/services/retirement_dashboard_service.py` already exists (437 lines).
`app/routes/retirement.py` is 338 lines and calls
`retirement_dashboard_service.compute_gap_data(user_id)`. This extraction was also completed
during Section 4A.

**Impact:** No action needed. Noted for completeness.

### D-3: v3 addendum references stale table names

**Roadmap v3 addendum says:** `budget.hysa_params`, `budget.mortgage_params`,
`budget.auto_loan_params`.

**Code says:** `HysaParams` was renamed to `InterestParams` (Section 4A). Mortgage and auto
loan params were unified into `LoanParams` (completed prior to Section 4). The current tables
are `budget.interest_params`, `budget.loan_params`, and `budget.investment_params`.

**Impact:** All references in this plan use the current names.

### D-4: Money Market and CD already have has_interest=True

**Roadmap says these were enabled in Section 4A.** Verified in `scripts/seed_ref_tables.py`:
Money Market has `has_interest=False`, `has_parameters=False`. CD has `has_interest=False`,
`has_parameters=False`. HSA has `has_parameters=True`, `has_interest=True`.

**Wait -- let me re-check.** The account_parameter_architecture.md recommends enabling them, and
Section 4A says they were enabled. But the seed data may not reflect this yet. This plan does
not modify account type dispatch -- it accepts whatever the current seed state is. If Money
Market and CD are not fully enabled, that is a Section 4A follow-up, not a Section 5 concern.

**Impact:** None for this plan.

### D-5: Roadmap v4-3 stale note about implementation_plan_section5.md

**Roadmap says:** "The existing `docs/implementation_plan_section5.md` predates the transfer
rework, Section 4 implementation, account parameter architecture changes, and adversarial audit.
Its assumptions about file structures, line numbers, dispatch patterns, and parameter table
layouts are stale. A new implementation plan must be written from scratch using a fresh codebase
inventory before starting this section." (Section 5 narrative, line ~579 of
`project_roadmap_v4-3.md`.)

**Code says:** The implementation plan WAS rewritten on March 30, 2026 (commit `00b2319`),
AFTER the transfer rework, Section 4, Section 4A, and Section 4B were all completed. The plan
uses the correct table names (`InterestParams`, `LoanParams`, `InvestmentParams`), references
the shadow transaction architecture, and has a codebase inventory built from reading the actual
files at their March 30 state.

**Impact:** The roadmap note is stale and should be updated in a future roadmap revision. This
plan is current and does not need to be rewritten from scratch. This version (1.1) reconciles
with v4-3 and Section 5A.

### D-6: Codebase inventory line counts changed by Section 5A implementation

**Plan says (v1.0):** Line counts for several files from March 30, 2026.

**Code says (April 1, 2026):** Section 5A tasks (5A.1 effective_amount fix, 5A.2 grid
sub-headers, 5A.3 salary button cleanup, 5A.4 category management, 5A.5 archive/unarchive
pattern) and Section 5 regression baseline (Commit #0) have changed file sizes.

Key changes:

- `app/models/transaction.py`: 149 -> 158 (+9, 5A.1 effective_amount simplification)
- `app/services/balance_calculator.py`: 332 -> 340 (+8, 5A.1 effective_amount usage)
- `app/schemas/validation.py`: 999 -> 1006 (+7, 5A.4 category schemas)
- `app/services/transfer_service.py`: 766 -> 707 (-59, post-audit cleanup)
- `app/models/__init__.py`: 58 -> 57 (-1)
- `tests/test_services/test_amortization_engine.py`: 595 -> 917 (+322, regression baseline)
- `tests/test_services/test_balance_calculator.py`: 1362 -> 2358 (+996, regression + 5A.1 tests)
- `tests/test_routes/test_loan.py`: 744 -> 892 (+148, regression baseline)
- `tests/test_routes/test_savings.py`: 1592 -> 1825 (+233, regression + 5A tests)

**Impact:** Line counts in the codebase inventory below are updated to April 1, 2026 values.
File paths and purposes are unchanged.

---

## Codebase Inventory

Every file that Section 5 tasks will create, modify, or depend on. Built from reading the
actual files, not from documentation assumptions.

### Models

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/models/savings_goal.py` | 53 | SavingsGoal model (target_amount, target_date, contribution_per_period, is_active) | 5.4 |
| `app/models/account.py` | 88 | Account model (is_active, current_anchor_balance, sort_order) | 5.9 |
| `app/models/loan_params.py` | 80 | LoanParams (principal, rate, term, ARM fields, payment_day) | 5.1 depends |
| `app/models/loan_features.py` | 84 | RateHistory, EscrowComponent | 5.7 depends |
| `app/models/recurrence_rule.py` | 66 | RecurrenceRule (end_date column, nullable) | 5.9 |
| `app/models/transaction.py` | 158 | Transaction (transfer_id, status, effective_amount) | 5.1 depends, 5.2 depends |
| `app/models/transfer.py` | 104 | Transfer model | 5.1 depends |
| `app/models/transfer_template.py` | 75 | TransferTemplate (recurrence_rule_id) | 5.1 depends, 5.2 depends |
| `app/models/investment_params.py` | 58 | InvestmentParams (return rate, limits, employer fields) | 5.2 depends, 5.3 depends |
| `app/models/ref.py` | 208 | All ref table models (AccountType flags, Status booleans) | 5.4 |
| `app/models/__init__.py` | 57 | Model registry for Alembic | 5.4, 5.9 |

### Services

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/services/amortization_engine.py` | 445 | Loan projection, schedule generation, payoff calculation. Pure function. | 5.1, 5.7, 5.8 |
| `app/services/balance_calculator.py` | 340 | Balance projection from anchor forward. Includes shadow transactions. | 5.1 depends |
| `app/services/savings_dashboard_service.py` | 548 | Savings dashboard orchestrator. Loads accounts, params, goals, emergency metrics. | 5.4, 5.15 |
| `app/services/savings_goal_service.py` | 206 | Goal calculations: required contribution, savings metrics, committed monthly. Pure function. | 5.4, 5.15 |
| `app/services/chart_data_service.py` | 720 | Chart data assembly. Calls amortization_engine for loan charts. | 5.5, 5.10 |
| `app/services/paycheck_calculator.py` | 462 | Net pay computation. Needed for 5.4 income-relative goals and 5.12 DTI. | 5.4 depends, 5.12 depends |
| `app/services/transfer_service.py` | 707 | Transfer CRUD with shadow transaction invariant enforcement. | 5.1 depends, 5.9 |
| `app/services/transfer_recurrence.py` | 261 | Transfer recurrence generation via transfer_service. | 5.1 depends, 5.2 depends |
| `app/services/recurrence_engine.py` | 552 | Transaction recurrence generation. | 5.9 depends |
| `app/services/escrow_calculator.py` | 115 | Monthly escrow and total payment calculation. | 5.14 depends |
| `app/services/growth_engine.py` | 210 | Investment growth projection. Pure function. | 5.2, 5.3 |
| `app/services/investment_projection.py` | 130 | Investment contribution/employer/YTD input calculation. Pure function. | 5.2 |
| `app/services/retirement_dashboard_service.py` | 436 | Retirement gap analysis orchestrator. Calls growth_engine. | 5.2 (backward compat) |
| `app/services/interest_projection.py` | 73 | Interest calculation. Pattern reference for pure functions. | -- |

### Services (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/services/debt_strategy_service.py` | Snowball/avalanche cross-account payoff strategy. Pure function. | 5.11 |

### Routes

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/routes/loan.py` | 487 | Loan dashboard, setup, params, escrow, rate history, payoff calculator. | 5.1, 5.5, 5.7, 5.9, 5.10, 5.13, 5.14 |
| `app/routes/investment.py` | 481 | Investment dashboard, growth chart, params. | 5.2, 5.3 |
| `app/routes/savings.py` | 152 | Savings dashboard (thin layer), goal CRUD. | 5.4 |
| `app/routes/accounts.py` | 811 | Account CRUD, type management, anchor true-up, interest/checking detail. | 5.9, 5.12 |
| `app/routes/transfers.py` | 695 | Transfer template CRUD, grid cell endpoints. | 5.1 depends |
| `app/routes/charts.py` | 202 | Chart dashboard with HTMX fragments. | 5.5 |
| `app/routes/grid.py` | 396 | Budget grid with balance row. | -- |

### Routes (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/routes/obligations.py` | Recurring obligation summary page. | 5.16 |
| `app/routes/debt_strategy.py` | Debt snowball/avalanche strategy page. | 5.11 |

### Schemas

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/schemas/validation.py` | 1006 | All Marshmallow validation schemas. | 5.1, 5.2, 5.4, 5.9, 5.10 |

### Enums and Cache

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/enums.py` | 129 | All enum definitions (StatusEnum, AcctTypeEnum, etc.) | 5.4 |
| `app/ref_cache.py` | 324 | Enum-to-DB-ID mapping cache. | 5.4 |

### Templates

| File | Purpose | Affected by |
|------|---------|-------------|
| `app/templates/loan/dashboard.html` | Loan dashboard with tabs (Overview, Escrow, Rate History, Payoff Calculator). 285 lines. | 5.1, 5.5, 5.10, 5.13, 5.14 |
| `app/templates/loan/setup.html` | Loan parameter setup form. | 5.1 |
| `app/templates/loan/_payoff_results.html` | Payoff calculator results partial. | 5.5 |
| `app/templates/investment/dashboard.html` | Investment dashboard with params, chart, employer info. 237 lines. | 5.2, 5.3 |
| `app/templates/savings/dashboard.html` | Accounts dashboard with category groups, goals, emergency fund. 283 lines. | 5.4, 5.9, 5.12, 5.15 |
| `app/templates/savings/goal_form.html` | Savings goal create/edit form. 86 lines. | 5.4 |

### Templates (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/templates/loan/_schedule.html` | Full amortization schedule partial (collapsible). | 5.13 |
| `app/templates/loan/_payment_breakdown.html` | Payment allocation breakdown partial. | 5.14 |
| `app/templates/loan/_refinance.html` | Refinance what-if calculator partial. | 5.10 |
| `app/templates/debt_strategy/dashboard.html` | Debt snowball/avalanche strategy page. | 5.11 |
| `app/templates/obligations/summary.html` | Recurring obligation summary page. | 5.16 |

### Tests

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `tests/test_services/test_amortization_engine.py` | 917 | Amortization engine unit tests. | 5.1, 5.7, 5.8 |
| `tests/test_services/test_balance_calculator.py` | 2358 | Balance calculator tests. | 5.1 regression |
| `tests/test_services/test_balance_calculator_debt.py` | exists | Debt-specific balance calculator tests. | 5.1 regression |
| `tests/test_services/test_transfer_service.py` | 1101 | Transfer service invariant tests. | 5.1 regression |
| `tests/test_services/test_growth_engine.py` | 482 | Growth engine unit tests. | 5.2 |
| `tests/test_services/test_savings_goal_service.py` | 288 | Savings goal service tests. | 5.4, 5.15 |
| `tests/test_services/test_savings_dashboard_service.py` | 197 | Savings dashboard service tests. | 5.4, 5.15 |
| `tests/test_services/test_chart_data.py` | exists | Chart data service tests. | 5.5 |
| `tests/test_services/test_escrow_calculator.py` | 246 | Escrow calculator tests. | 5.14 regression |
| `tests/test_services/test_paycheck_calculator.py` | 2895 | Paycheck calculator tests. | 5.12 regression |
| `tests/test_routes/test_loan.py` | 892 | Loan route tests (dashboard, setup, params, escrow, rate, payoff). | 5.1, 5.5, 5.7, 5.9, 5.10, 5.13, 5.14 |
| `tests/test_routes/test_investment.py` | 428 | Investment route tests (dashboard, params, growth chart). | 5.2, 5.3 |
| `tests/test_routes/test_savings.py` | 1825 | Savings route tests (dashboard, goals). | 5.4, 5.15 |
| `tests/test_routes/test_transfers.py` | 1312 | Transfer route tests. | 5.1 regression |
| `tests/test_routes/test_accounts_dashboard.py` | 278 | Accounts dashboard tests. | 5.9, 5.12 |
| `tests/conftest.py` | 1035 | Test fixtures (seed_user, auth_client, seed_periods, etc.) | 5.4 (new ref tables) |

### Tests (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `tests/test_services/test_debt_strategy_service.py` | Snowball/avalanche strategy tests. | 5.11 |
| `tests/test_routes/test_debt_strategy.py` | Debt strategy route tests. | 5.11 |
| `tests/test_routes/test_obligations.py` | Recurring obligation summary route tests. | 5.16 |

### Seed Scripts

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `scripts/seed_ref_tables.py` | 127 | Seeds all reference tables. | 5.4 |

### Other

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/__init__.py` | ~200 | Application factory, Jinja globals, blueprint registration. | 5.4 (new Jinja globals), 5.11, 5.16 (new blueprints) |

---

## Task Dependency Analysis and Commit Ordering

### Dependency Graph

```text
5.6 (ALREADY COMPLETE)
 |
 +---> 5.4 (Income-Relative Goals) ---> 5.15 (Goal Trajectory)
 |
5.1 (Payment Linkage) ---> 5.2 (Investment Contribution Linkage)
 |                              |
 |                              +---> 5.3 (Investment What-If Calculator)
 |
 +---> 5.7 (ARM Rates) ---> 5.8 (Edge Cases)
 |                                |
 +---> 5.5 (Multi-Scenario Viz) <-+
 |                                |
 +---> 5.14 (Payment Breakdown)   |
 |                                |
 +---> 5.13 (Full Schedule)       |
 |                                |
 +---> 5.12 (Debt Summary/DTI)    |
 |                                |
 +---> 5.10 (Refinance Calc)      |
 |                                |
 +---> 5.9 (Payoff Lifecycle) <---+
 |         |
 |         +---> 5.11 (Snowball/Avalanche) <--- 5.8
 |
5.16 (Recurring Obligations) -- independent
```

### Commit Order Rationale

The ordering follows five principles:

1. **Refactor-before-feature:** 5.6 is already complete, so no refactor is needed.
2. **Foundation-before-visualization:** 5.1 (debt data pipeline) and 5.2 (investment data
   pipeline) come before their respective visualization tasks (5.5 and 5.3).
3. **Mirror tasks grouped:** 5.2 immediately follows 5.1 because they share the same pattern
   (engine extension + route integration + recurring transfer prompt). Doing them consecutively
   minimizes context switching and allows 5.2 to directly reference the patterns 5.1 establishes.
4. **Edge cases alongside the work that creates them:** 5.8 immediately follows 5.7.
5. **Dependency-ordered:** Each task's prerequisites are satisfied before it begins.

**Phase 1 -- Regression Baseline:** Commit #0
**Phase 2 -- Engine Foundations:** 5.1, 5.2 (data pipeline work for both engines)
**Phase 3 -- Investment Visualization:** 5.3
**Phase 4 -- Amortization Engine Extensions:** 5.7, 5.8
**Phase 5 -- Loan Visualization:** 5.5, 5.14, 5.13
**Phase 6 -- Loan Lifecycle:** 5.9
**Phase 7 -- Savings Goals:** 5.4, 5.15
**Phase 8 -- Aggregate Metrics:** 5.12
**Phase 9 -- Advanced Calculators:** 5.10, 5.11
**Phase 10 -- Standalone Views:** 5.16

Tasks 5.2 and 5.3 (investment contribution linkage and what-if calculator) are placed in
Phases 2 and 3, immediately after 5.1. This groups all engine data pipeline work together and
delivers the investment contribution feature early, before the loan-specific extensions begin.
Task 5.3 follows 5.2 rather than waiting for 5.5 because the investment what-if chart is
simpler than the loan multi-scenario visualization (two lines vs. four elements) and can use
existing Chart.js patterns in `investment/dashboard.html` without needing 5.5's chart styling
as a precedent.

Tasks 5.4/5.15 (savings goals) and 5.12 (debt summary) are independent of the loan
visualization chain (5.5/5.14/5.13). They are placed after Phase 6 because 5.12 benefits from
accurate current principal values established by 5.1, and to keep the loan-related commits
grouped together for easier review. However, 5.4/5.15 could be reordered before Phase 5 if
the developer prefers to interleave loan and savings work.

---

## Commit #0: Regression Baseline Tests

### A. Commit message

```text
test(section5): add regression baseline for loan dashboard and savings goals
```

### B. Problem statement

Section 5 modifies the amortization engine, loan dashboard, savings goal service, and balance
calculator. Before any changes are made, a regression test suite must verify the complete loan
dashboard workflow and savings goal workflow. This is the safety net -- if any future commit
breaks existing behavior, these tests catch it immediately.

### C. Files modified

- `tests/test_routes/test_loan.py` -- Add regression test class
- `tests/test_services/test_amortization_engine.py` -- Add regression test class

### D. Implementation approach

Add a `TestLoanDashboardRegression` class to `test_loan.py` that exercises the full workflow:

1. Create an account of type Mortgage with `has_amortization=True`.
2. Create LoanParams: original_principal=$250,000, current_principal=$240,000, rate=6.5%,
   term=360 months, origination_date=2024-01-01, payment_day=1.
3. Load the loan dashboard (GET `/accounts/<id>/loan`).
4. Assert the response contains: monthly payment amount (computed from amortization engine),
   total interest, projected payoff date.
5. Run the payoff calculator with extra_payment=$200 (POST `/accounts/<id>/loan/payoff`).
6. Assert: payoff date is earlier, interest saved > 0, months saved > 0.
7. Run the payoff calculator with target_date mode.
8. Assert: required extra payment is returned.

Add a `TestAmortizationEngineRegression` class to `test_amortization_engine.py` that verifies:

1. `generate_schedule()` with known inputs produces expected output (spot-check first row,
   last row, and total interest).
2. `calculate_summary()` returns consistent values with `generate_schedule()`.
3. `calculate_payoff_by_date()` returns a value that, when fed back as extra_monthly to
   `generate_schedule()`, produces a payoff on or before the target date.
4. `get_loan_projection()` wrapper returns consistent results.

Add a `TestSavingsGoalRegression` class to `test_savings.py` that verifies:

1. Goal creation with target_amount and target_date.
2. Dashboard displays goal progress.
3. Goal update and deactivation.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.0-1 | test_full_loan_dashboard_workflow | Mortgage account with LoanParams | GET dashboard, POST payoff | Dashboard renders with correct metrics; payoff calculator returns valid results | New |
| C-5.0-2 | test_amortization_schedule_consistency | Known loan parameters | generate_schedule + calculate_summary | Summary metrics match schedule totals | New |
| C-5.0-3 | test_payoff_by_date_round_trip | Known loan parameters | calculate_payoff_by_date, then generate_schedule with result | Payoff on or before target date | New |
| C-5.0-4 | test_savings_goal_full_lifecycle | Savings account with goal | Create, read, update, deactivate goal | Goal appears on dashboard with correct progress | New |
| C-5.0-5 | test_balance_calculator_with_shadow_transactions | Checking + savings accounts, transfer between them | Calculate balances for both accounts | Shadow transactions correctly affect both account balances | New |

### F. Manual verification steps

No manual verification needed -- these are automated regression tests.

### G. Downstream effects

None. This commit adds tests only.

### H. Rollback notes

Test-only commit. Trivially revertable.

---

## Task 5.1: Debt Account Payment Linkage

### Overview

Connect debt account payments (transfers) to the amortization engine so that the full payment
timeline -- confirmed payments, committed future payments, and extra payments -- is reflected in
balance projections and payoff dates.

This task has three sub-commits:

1. Extend the amortization engine input contract.
2. Integrate payment data into the loan dashboard.
3. Add the recurring transfer creation prompt to the loan parameter page.

---

### Commit 5.1-1: Extend amortization engine to accept payment data

### A. Commit message

```text
feat(amortization): extend engine to accept payment history for projection scenarios
```

### B. Problem statement

The amortization engine (`app/services/amortization_engine.py:generate_schedule`, line ~130)
currently projects the entire remaining term using static loan parameters (original principal,
interest rate, term, origination date). It has no concept of actual payments made or committed
future payments. After this change, the engine accepts an optional list of payments so it can
compute different projection scenarios (original schedule, committed schedule, what-if schedule)
from the same input.

### C. Files modified

- `app/services/amortization_engine.py` -- Add `payments` parameter to `generate_schedule()`,
  `calculate_summary()`, and `get_loan_projection()`. Add `PaymentRecord` dataclass.
- `tests/test_services/test_amortization_engine.py` -- Add tests for payment-aware scenarios.

### D. Implementation approach

**New dataclass** (at top of `amortization_engine.py`):

```python
@dataclasses.dataclass(frozen=True)
class PaymentRecord:
    """A single payment applied to a loan."""
    payment_date: date
    amount: Decimal
    is_confirmed: bool  # True = Paid/Settled; False = Projected
```

**Extend `generate_schedule()` signature:**

```python
def generate_schedule(
    current_principal, annual_rate, remaining_months,
    extra_monthly=Decimal("0.00"),
    origination_date=None, payment_day=1,
    original_principal=None, term_months=None,
    payments=None,  # Optional[list[PaymentRecord]]
) -> list[AmortizationRow]:
```

**Behavior when `payments` is provided:**

For each month in the schedule:

1. Calculate the standard monthly payment (contractual P&I) as today.
2. Check if a payment exists for this month (match by year-month of `payment_date`).
3. If a matching payment exists: use `payment.amount` as the total payment for this month.
   Apply interest first (same as current logic), then the remainder goes to principal. If the
   payment exceeds P&I, the excess is extra principal reduction. If the payment is less than
   the interest due, negative amortization occurs (principal increases) -- this is correct
   behavior for modeling missed/partial payments.
4. If no matching payment exists: use the standard monthly payment + `extra_monthly` (same as
   current behavior). This handles months between payments and months after the payment list
   ends.
5. Track the `is_confirmed` flag on each `AmortizationRow` via a new boolean field so
   downstream consumers can distinguish confirmed vs. projected periods.

**Add `is_confirmed` to `AmortizationRow`:**

```python
@dataclasses.dataclass(frozen=True)
class AmortizationRow:
    month: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    extra_payment: Decimal
    remaining_balance: Decimal
    is_confirmed: bool = False  # New field, defaults to False for backward compat
```

**Backward compatibility:** When `payments` is `None`, behavior is identical to today. The
`is_confirmed` field defaults to `False` for all rows in the no-payments case.

**Extend `calculate_summary()` and `get_loan_projection()`** to accept and pass through the
`payments` parameter.

**Edge cases handled in this commit:**

- Empty payment list (same as None -- standard schedule).
- Payments with dates before `origination_date` (skip -- these predate the loan).
- Multiple payments in the same month (sum them for that month).
- Payment of $0 (treated as a missed payment -- only interest accrues).

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.1-1 | test_generate_schedule_no_payments_unchanged | $200K, 6%, 360mo | generate_schedule(payments=None) | Identical output to current behavior | New |
| C-5.1-2 | test_generate_schedule_with_exact_payments | $200K, 6%, 360mo, 12 payments at standard P&I | generate_schedule(payments=exact_payments) | Schedule matches standard for those 12 months | New |
| C-5.1-3 | test_generate_schedule_with_extra_payments | $200K, 6%, 360mo, 6 payments at P&I + $200 extra | generate_schedule(payments=extra_payments) | Faster principal reduction, earlier payoff | New |
| C-5.1-4 | test_generate_schedule_with_partial_payments | $200K, 6%, 360mo, 3 payments at 50% of P&I | generate_schedule(payments=partial) | Principal decreases slower (or increases if payment < interest) | New |
| C-5.1-5 | test_generate_schedule_mixed_confirmed_projected | Payments with mix of is_confirmed | generate_schedule(payments=mixed) | is_confirmed flag propagated to AmortizationRow | New |
| C-5.1-6 | test_generate_schedule_empty_payment_list | $200K, 6%, 360mo, payments=[] | generate_schedule(payments=[]) | Same as payments=None | New |
| C-5.1-7 | test_calculate_summary_with_payments | $200K, 6%, extra payments | calculate_summary(payments=...) | Summary reflects accelerated payoff | New |
| C-5.1-8 | test_multiple_payments_same_month | Two payments in January | generate_schedule | Amounts summed for that month | New |

### F. Manual verification steps

No UI changes in this commit. Verify via tests only.

### G. Downstream effects

- `get_loan_projection()` gains a new optional parameter. All existing callers pass no
  arguments for `payments`, so they are unaffected.
- `chart_data_service.get_amortization_breakdown()` calls `get_loan_projection()` -- unaffected
  (no payments passed yet; that comes in commit 5.5-1).
- `savings_dashboard_service._compute_account_projections()` calls
  `amortization_engine.get_loan_projection()` -- unaffected.

### H. Rollback notes

No migration. Service-only change with backward-compatible interface. Trivially revertable.

---

### Commit 5.1-2: Integrate payment data into loan dashboard

### A. Commit message

```text
feat(loan): pass payment history from shadow transactions to amortization engine
```

### B. Problem statement

The loan dashboard (`app/routes/loan.py:dashboard`, line ~30) calls
`amortization_engine.get_loan_projection(params)` with no payment data. After this change, the
dashboard queries shadow income transactions on the debt account (payments received by the
account via transfers) and passes them to the engine as `PaymentRecord` instances.

### C. Files modified

- `app/routes/loan.py` -- Add payment data query; pass payments to engine calls.
- `tests/test_routes/test_loan.py` -- Add tests for payment-aware dashboard.

### D. Implementation approach

**Payment data query** (add a helper function in `loan.py`):

```python
def _get_payment_history(account_id, scenario_id):
    """Query shadow income transactions on a debt account.

    Returns a list of PaymentRecord instances representing payments
    made to this debt account via transfers. Shadow income transactions
    have transfer_id IS NOT NULL and transaction_type_id = INCOME.
    """
    from app.models.transaction import Transaction
    from app.services.amortization_engine import PaymentRecord

    txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
        )
        .join(Transaction.status)
        .join(Transaction.pay_period)
        .order_by(PayPeriod.start_date)
        .all()
    )

    payments = []
    for txn in txns:
        amount = txn.actual_amount if txn.status.is_settled and txn.actual_amount else txn.estimated_amount
        is_confirmed = txn.status.is_settled
        payments.append(PaymentRecord(
            payment_date=txn.pay_period.start_date,
            amount=amount,
            is_confirmed=is_confirmed,
        ))
    return payments
```

**Dashboard integration:** In `dashboard()`, after loading `params`, call
`_get_payment_history(account.id, scenario.id)` and pass the result to
`get_loan_projection(params, payments=payments)`.

**Payoff calculator integration:** In `payoff_calculate()`, also load payment data. For the
"extra payment" mode:

- Standard schedule: `generate_schedule(..., payments=None)` (contractual baseline).
- Accelerated schedule: `generate_schedule(..., payments=confirmed_only, extra_monthly=extra)`
  where `confirmed_only` is the subset of payments with `is_confirmed=True`, plus the extra
  monthly amount applied from today forward.

For the "target date" mode:

- `calculate_payoff_by_date(...)` operates from the current real principal (derived from
  confirmed payments).

**Current real principal derivation:** The `current_principal` stored in `LoanParams` is a
manually-entered value. With payment data available, the "real" current principal is computed by
the amortization engine: replay confirmed payments against the original loan terms and read the
final `remaining_balance`. This replaces the static `current_principal` for projection purposes.
The `LoanParams.current_principal` field remains as a user-editable override for cases where the
engine's replay disagrees with the lender's statement.

**Transfer invariant check:** This query reads only from `budget.transactions`
(shadow income transactions). It does NOT query `budget.transfers`. This is consistent with
transfer invariant #5: the balance calculator and related services never query
`budget.transfers` directly.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.1-9 | test_dashboard_no_payments | Mortgage with LoanParams, no transfers | GET dashboard | Dashboard renders with standard schedule (backward compat) | New |
| C-5.1-10 | test_dashboard_with_confirmed_payments | Mortgage + 3 confirmed transfer payments | GET dashboard | Dashboard shows updated payoff based on payment history | New |
| C-5.1-11 | test_dashboard_with_projected_payments | Mortgage + recurring transfer (projected shadows) | GET dashboard | Dashboard shows committed schedule | New |
| C-5.1-12 | test_payoff_calculator_with_payments | Mortgage + payments + extra_payment POST | POST payoff | Payoff reflects confirmed payments + extra | New |
| C-5.1-13 | test_payment_query_excludes_deleted | Mortgage + deleted shadow transaction | GET dashboard | Deleted shadow not included in payment list | New |
| C-5.1-14 | test_payment_query_excludes_expense_shadows | Mortgage + transfer (has expense and income shadows) | Query _get_payment_history | Only income shadows returned (expense shadow is on checking account) | New |
| C-5.1-15 | test_payment_query_idor | Second user's mortgage | GET dashboard as user 1 | 404 (ownership check prevents access) | New |

### F. Manual verification steps

1. Create a mortgage account with LoanParams (e.g., $250K, 6.5%, 30yr).
2. Create a one-time transfer from checking to mortgage for $1,500.
3. Mark the transfer as "Paid" (confirmed).
4. Visit the mortgage dashboard.
5. Verify the loan summary reflects the payment: current principal should be lower than the
   static value, and the projected payoff should be slightly earlier.
6. Create a recurring transfer from checking to mortgage for the monthly payment amount.
7. Visit the dashboard again.
8. Verify the committed schedule reflects the recurring payments.

### G. Downstream effects

- The loan dashboard now depends on shadow transactions existing for accurate projections.
  Accounts with no transfers continue to work (empty payment list = standard schedule).
- The payoff calculator results will differ from pre-5.1 behavior when payments exist, because
  the engine now uses actual payment data instead of purely contractual terms. This is the
  intended improvement.
- Balance calculator is NOT affected -- it already handles shadow transactions correctly.

**Transfer invariant verification:**

1. Every transfer has exactly two shadows -- verified by query filtering on income type only.
2. Shadows never orphaned -- not affected (read-only query).
3. Shadow amounts = transfer amount -- used as payment amount.
4. Shadow statuses = transfer status -- used for is_confirmed flag.
5. Balance calculator queries only transactions -- no change to balance calculator.

### H. Rollback notes

No migration. Route-only change. Revert reverts to static projections.

---

### Commit 5.1-3: Add recurring transfer creation prompt to loan parameter page

### A. Commit message

```text
feat(loan): prompt recurring transfer creation after loan parameter save
```

### B. Problem statement

After a user creates a debt account and saves its parameters (`app/routes/loan.py:create_params`,
line ~90), the monthly payment amount is known (calculated by the amortization engine). The user
should be prompted to create a recurring monthly transfer from their checking account to the
debt account for this amount. This is the link between the transfer system and the amortization
engine.

### C. Files modified

- `app/routes/loan.py` -- After `create_params`, compute monthly payment and pass to template.
  Add new route for creating the recurring transfer.
- `app/templates/loan/setup.html` -- Add prompt section after successful parameter save.
- `app/templates/loan/dashboard.html` -- Add prompt banner if no recurring transfer exists.
- `app/schemas/validation.py` -- Add schema for the recurring transfer creation form.
- `tests/test_routes/test_loan.py` -- Add tests for the prompt and transfer creation.

### D. Implementation approach

**After parameter save:** When `create_params()` successfully saves LoanParams, compute the
monthly payment via `amortization_engine.calculate_monthly_payment()`. Add the escrow total
(if any) via `escrow_calculator.calculate_monthly_escrow()`. Flash the total and redirect to the
loan dashboard.

**Dashboard prompt:** On the loan dashboard, check if a recurring transfer template exists that
targets this account. Query:

```python
existing_template = (
    db.session.query(TransferTemplate)
    .filter(
        TransferTemplate.user_id == current_user.id,
        TransferTemplate.to_account_id == account.id,
        TransferTemplate.is_active.is_(True),
        TransferTemplate.recurrence_rule_id.isnot(None),
    )
    .first()
)
```

If no template exists, display a prompt: "Create a recurring monthly transfer of
$X from [source] to this account?" where X is the calculated monthly P&I (+ escrow).
The prompt includes a dropdown of the user's checking/savings accounts as the source, and a
"Create Transfer" button.

**New route:** `POST /accounts/<id>/loan/create-payment-transfer` that:

1. Validates ownership of both accounts.
2. Creates a RecurrenceRule with pattern=MONTHLY, day_of_month=params.payment_day.
3. Creates a TransferTemplate with from_account=selected, to_account=loan_account,
   default_amount=monthly_payment, recurrence_rule_id=rule.id.
4. Generates transfers for existing periods via
   `transfer_recurrence.generate_for_template(template, periods, scenario.id)`.
5. Redirects to the loan dashboard with a success flash.

**Source account selection:** The prompt shows a dropdown of the user's active accounts that
are NOT the current debt account. The default selection is the user's checking account (the
most common source). If the user has no checking account, the dropdown shows all active
accounts.

**Category assignment:** Auto-assign the transfer template a category based on the debt account
type. Use the account's name to construct a category like "Home: Mortgage Payment" for
mortgages or "Auto: Car Payment" for auto loans. If the category doesn't exist, create it.
Alternatively, use a generic "Debt: Payment" category. The exact category is a minor detail --
the user can change it later.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.1-16 | test_dashboard_shows_transfer_prompt_when_no_transfer | Mortgage with params, no transfer template | GET dashboard | Prompt to create recurring transfer is visible | New |
| C-5.1-17 | test_dashboard_hides_prompt_when_transfer_exists | Mortgage with params + active transfer template | GET dashboard | No prompt visible | New |
| C-5.1-18 | test_create_payment_transfer_success | Mortgage + checking account | POST create-payment-transfer | TransferTemplate created, transfers generated, redirect to dashboard | New |
| C-5.1-19 | test_create_payment_transfer_validates_source | POST with invalid source account | POST create-payment-transfer | 404 or validation error | New |
| C-5.1-20 | test_create_payment_transfer_idor | Other user's mortgage | POST create-payment-transfer | 404 | New |
| C-5.1-21 | test_monthly_payment_includes_escrow | Mortgage with escrow components | GET dashboard | Prompt amount includes escrow | New |

### F. Manual verification steps

1. Create a new mortgage account.
2. Fill in loan parameters (principal, rate, term, etc.) and save.
3. On the dashboard, verify the prompt appears: "Create a recurring monthly transfer of $1,293.96
   from Checking to this account?"
4. Select the checking account and click "Create Transfer."
5. Verify: redirect to dashboard, success flash, prompt disappears.
6. Navigate to the Transfers page and verify the recurring transfer template exists.
7. Navigate to the budget grid and verify shadow transactions appear for future pay periods.

### G. Downstream effects

- The recurring transfer creates shadow transactions that flow into the balance calculator
  (checking balance decreases, debt account receives income).
- The transfers appear in the grid as shadow transactions.
- The loan dashboard now shows the committed schedule (commit 5.1-2 passes these payments to
  the engine).

### H. Rollback notes

No migration. Route + template change. Revert removes the prompt; existing transfers are not
affected.

---

## Task 5.2: Investment Contribution Linkage

### Overview

Connect investment and retirement account contributions (transfers and paycheck deductions)
to the compound growth engine so that the full contribution timeline -- confirmed
contributions, committed future contributions, and employer contributions -- is reflected in
balance projections and growth charts. This is the investment/retirement equivalent of
task 5.1 (Debt Account Payment Linkage).

Two contribution paths:

1. **Transfer-based (IRA, Roth IRA, Brokerage):** Contributions arrive via transfers from
   checking. The shadow transaction architecture (Section 3A) already creates income-side
   shadows on the investment account. Contribution discovery follows the same pattern as debt
   payment discovery in task 5.1: query shadow income transactions on the target account,
   categorize by status (confirmed vs. projected).
2. **Paycheck-deduction-based (401(k), Roth 401(k)):** Contributions arrive via paycheck
   deductions linked by `target_account_id` on `salary.paycheck_deductions`. The deduction
   amount per period is computed from the deduction record. Employer contributions (flat
   percentage or match) are calculated by
   `growth_engine.calculate_employer_contribution()`.

This task has three sub-commits:

1. Extend the growth engine input contract.
2. Build contribution timeline and integrate into the investment dashboard.
3. Add the recurring transfer/deduction linkage prompt.

---

### Commit 5.2-1: Extend growth engine to accept contribution list

### A. Commit message

```text
feat(growth): extend engine to accept contribution timeline for projection scenarios
```

### B. Problem statement

The growth engine (`app/services/growth_engine.py:project_balance`, line 75) currently projects
the entire timeline using a static `periodic_contribution` Decimal. It has no concept of
individual contributions with dates or confirmed/projected status. After this change, the engine
accepts an optional list of contributions so it can incorporate the actual contribution timeline
into projections, distinguishing confirmed from projected contributions. This mirrors the
`PaymentRecord` approach added to the amortization engine in task 5.1 commit 5.1-1.

### C. Files modified

- `app/services/growth_engine.py` -- Add `ContributionRecord` dataclass, add `contributions`
  parameter to `project_balance()`, add `is_confirmed` field to `ProjectedBalance`.
- `tests/test_services/test_growth_engine.py` -- Add tests for contribution-list-aware
  projections.

### D. Implementation approach

**New dataclass** (at top of `growth_engine.py`, after the existing `ProjectedBalance`):

```python
@dataclass(frozen=True)
class ContributionRecord:
    """A single contribution to an investment account."""
    contribution_date: date  # Pay period start_date
    amount: Decimal
    is_confirmed: bool  # True = Paid/Settled; False = Projected
```

**Extend `project_balance()` signature:**

Current signature (line 75):

```python
def project_balance(
    current_balance,
    assumed_annual_return,
    periods,
    periodic_contribution=ZERO,
    employer_params=None,
    annual_contribution_limit=None,
    ytd_contributions_start=ZERO,
):
```

Extended to:

```python
def project_balance(
    current_balance,
    assumed_annual_return,
    periods,
    periodic_contribution=ZERO,
    employer_params=None,
    annual_contribution_limit=None,
    ytd_contributions_start=ZERO,
    contributions=None,  # Optional[list[ContributionRecord]]
):
```

**Add `is_confirmed` to `ProjectedBalance`:**

```python
@dataclass
class ProjectedBalance:
    """A single period's projected investment balance."""
    period_id: int
    start_balance: Decimal
    growth: Decimal
    contribution: Decimal
    employer_contribution: Decimal
    end_balance: Decimal
    ytd_contributions: Decimal
    contribution_limit_remaining: Decimal
    is_confirmed: bool = False  # New field, defaults False for backward compat
```

**Behavior when `contributions` is provided:**

Build a lookup dict mapping `contribution_date` -> `(total_amount, is_confirmed)` at the top
of the function. For each period in the main loop:

1. Check if a contribution exists for this period's `start_date` in the lookup.
2. If found: use the contribution's amount as the employee contribution for this period.
   Set `is_confirmed` on the `ProjectedBalance` from the contribution record.
3. If not found: use `periodic_contribution` (static fallback for periods beyond the
   contribution timeline). Set `is_confirmed = False`.
4. Apply the annual contribution limit cap using the period-specific contribution amount
   (same capping logic as today, lines 143-147).
5. Calculate employer contribution using the period's actual contribution amount (same
   `calculate_employer_contribution` call, line 150, but with the period-specific amount).

**Multiple contributions on the same date:** Sum the amounts. Use `is_confirmed = True` only
if ALL contributions on that date are confirmed; otherwise `is_confirmed = False`.

**Backward compatibility:** When `contributions` is `None`, behavior is identical to today.
The `is_confirmed` field defaults to `False` for all rows. All existing callers pass no
arguments for `contributions` and are unaffected.

**Edge cases handled in this commit:**

- Empty contribution list (same as None -- static fallback).
- Multiple contributions on the same date (summed).
- Contribution of $0 (zero contribution for that period -- only growth accrues).
- Contribution exceeding annual limit (capped at remaining limit, same as today).
- Year boundary with contributions (limit resets, same as today).

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.2-1 | test_project_balance_no_contributions_unchanged | $10K balance, 7% return, 10 periods | project_balance(contributions=None) | Identical output to current behavior | New |
| C-5.2-2 | test_project_balance_with_contributions | $10K, 7%, 5 periods, contribution for each | project_balance(contributions=list) | Contributions applied per-period | New |
| C-5.2-3 | test_contributions_with_static_fallback | Contributions for 3 of 5 periods | project_balance(contributions=partial, periodic_contribution=200) | First 3 use list amounts, last 2 use $200 | New |
| C-5.2-4 | test_contribution_limit_with_contributions | $7000 limit, contributions totaling $8000 | project_balance(contributions=over_limit) | Capped at $7000 total | New |
| C-5.2-5 | test_year_boundary_reset_with_contributions | Contributions spanning two calendar years | project_balance(contributions=cross_year) | Limit resets at year boundary | New |
| C-5.2-6 | test_employer_uses_period_contribution | Employer match 100% up to 6%, varying contributions | project_balance(contributions=varying) | Employer match computed per-period from actual amount | New |
| C-5.2-7 | test_is_confirmed_propagated | Mix of confirmed and projected contributions | project_balance(contributions=mixed) | is_confirmed flag matches input records | New |
| C-5.2-8 | test_empty_contribution_list | contributions=[] | project_balance(contributions=[]) | Same as contributions=None | New |

### F. Manual verification steps

No UI changes in this commit. Verify via tests only.

### G. Downstream effects

- `project_balance()` gains a new optional parameter. All existing callers pass no arguments
  for `contributions`, so they are unaffected.
- `retirement_dashboard_service.py` calls `project_balance()` -- unaffected (no contributions
  passed; backward compatibility maintained).
- `app/routes/investment.py:dashboard()` and `growth_chart()` call `project_balance()` --
  unaffected (contributions passed in commit 5.2-2).

### H. Rollback notes

No migration. Service-only change with backward-compatible interface. Trivially revertable.

---

### Commit 5.2-2: Build contribution timeline and integrate into investment dashboard

### A. Commit message

```text
feat(investment): pass contribution timeline from transactions and deductions to growth engine
```

### B. Problem statement

The investment dashboard (`app/routes/investment.py:dashboard`, line 40) calls
`calculate_investment_inputs()` (from `app/services/investment_projection.py`) which averages
all contributions into a static `periodic_contribution`. After this change, the dashboard
builds a per-period contribution timeline and passes it to the growth engine as
`ContributionRecord` instances.

### C. Files modified

- `app/services/investment_projection.py` -- Add `build_contribution_timeline()` function.
- `app/routes/investment.py` -- Call `build_contribution_timeline()` and pass `contributions`
  to `project_balance()` in both `dashboard()` and `growth_chart()`.
- `tests/test_services/test_growth_engine.py` -- Add integration tests.
- `tests/test_routes/test_investment.py` -- Add tests for contribution-aware dashboard.

### D. Implementation approach

**New function in `investment_projection.py`:**

```python
def build_contribution_timeline(
    deductions,
    contribution_transactions,
    periods,
    salary_gross_biweekly=None,
):
    """Build a list of ContributionRecords from deductions and shadow transactions.

    Combines paycheck deduction contributions and transfer-based contributions
    into a unified per-period timeline for the growth engine.

    Args:
        deductions:              List of deduction objects targeting this account.
                                 Each has .amount, .calc_method_id,
                                 .annual_salary, .pay_periods_per_year.
        contribution_transactions: List of shadow income transactions (transfer_id
                                   IS NOT NULL) on this account. Each has
                                   .estimated_amount, .actual_amount,
                                   .pay_period_id, .status.
        periods:                 List of period objects with .id, .start_date.
        salary_gross_biweekly:   Decimal or None -- gross pay per period.

    Returns:
        list[ContributionRecord] sorted by contribution_date.
    """
```

**Logic:**

1. Build a period lookup: `{period.id: period}` for fast access.

2. **Paycheck deduction contributions:** For each deduction, compute the per-period amount
   (flat or percentage of salary, same logic as `calculate_investment_inputs()` lines 67-75).
   For each period in `periods`, create a `ContributionRecord` with:
   - `contribution_date = period.start_date`
   - `amount = deduction_amount`
   - `is_confirmed = period.start_date < date.today()` (past periods are confirmed)

3. **Transfer-based contributions:** For each shadow income transaction, create a
   `ContributionRecord` with:
   - `contribution_date = period.start_date` (looked up from the transaction's pay_period_id)
   - `amount = txn.effective_amount` (prefers actual over estimated)
   - `is_confirmed = txn.status.is_settled`

4. **Sum contributions on the same date:** Aggregate by `contribution_date`. Sum amounts.
   Mark as confirmed only if all contributions for that date are confirmed.

5. Return sorted by `contribution_date`.

**Dashboard integration (`investment.py:dashboard()`):**

After loading params and contribution data (existing queries already load deductions and
shadow income transactions):

```python
from app.services.investment_projection import build_contribution_timeline

contributions = build_contribution_timeline(
    deductions=deductions,
    contribution_transactions=contribution_txns,
    periods=all_periods,
    salary_gross_biweekly=gross_biweekly,
)
```

Pass to the growth engine alongside existing parameters:

```python
projections = growth_engine.project_balance(
    current_balance=current_balance,
    assumed_annual_return=params.assumed_annual_return,
    periods=projection_periods,
    periodic_contribution=inputs.periodic_contribution,  # Fallback
    employer_params=inputs.employer_params,
    annual_contribution_limit=inputs.annual_contribution_limit,
    ytd_contributions_start=inputs.ytd_contributions,
    contributions=contributions,
)
```

**Growth chart integration:** The `growth_chart()` route (line 251) also calls
`project_balance()`. Update it to pass the contribution timeline.

**Callers NOT updated:** `retirement_dashboard_service.py` continues to use the static
`periodic_contribution` via `calculate_investment_inputs()`. The growth engine's backward
compatibility ensures this works. Updating the retirement dashboard to use contribution
timelines is a future enhancement.

**Transfer invariant check:** This query reads only from `budget.transactions` (shadow income
transactions). It does NOT query `budget.transfers`. Consistent with transfer invariant #5.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.2-9 | test_build_timeline_deduction_only | 401(k) with paycheck deduction, no transfers | build_contribution_timeline | One record per period from deduction | New |
| C-5.2-10 | test_build_timeline_transfer_only | IRA with recurring transfer shadows | build_contribution_timeline | One record per period from shadow txns | New |
| C-5.2-11 | test_build_timeline_both_paths | Account with deduction AND transfer | build_contribution_timeline | Amounts summed per period | New |
| C-5.2-12 | test_build_timeline_confirmed_vs_projected | Past and future periods | build_contribution_timeline | Past = confirmed, future = projected | New |
| C-5.2-13 | test_dashboard_with_contributions | Investment account with contributions | GET dashboard | Projection reflects contribution timeline | New |
| C-5.2-14 | test_dashboard_no_contributions_unchanged | No transfers, no deductions | GET dashboard | Same as current (static fallback) | New |
| C-5.2-15 | test_growth_chart_uses_contributions | Investment account with contributions | GET growth-chart (HTMX) | Chart reflects contribution timeline | New |
| C-5.2-16 | test_contribution_query_idor | Second user's investment account | GET dashboard as user 1 | 404 | New |

### F. Manual verification steps

1. Create a Roth IRA account with InvestmentParams (7% return, $7,000 limit).
2. Create a recurring transfer from checking to the Roth IRA for $269.23/period.
3. Mark the first 3 transfers as "Paid" (confirmed).
4. Visit the investment dashboard.
5. Verify the growth projection reflects actual contributions: confirmed for the first 3
   periods, projected for subsequent periods, static fallback beyond the recurrence horizon.
6. Verify the YTD contribution tracker uses actual contribution amounts.

### G. Downstream effects

- The investment dashboard now depends on shadow transactions for accurate contribution data.
  Accounts with no transfers or deductions continue to work (empty list = static fallback).
- The growth chart reflects per-period contributions instead of an averaged static rate.
- Retirement dashboard is NOT affected (backward compatibility).

### H. Rollback notes

No migration. Route + service change. Revert reverts to static contribution projections.

---

### Commit 5.2-3: Add recurring transfer/deduction linkage prompt

### A. Commit message

```text
feat(investment): prompt contribution setup after investment parameter save
```

### B. Problem statement

After investment parameters are saved, the user should be guided to set up their contribution
mechanism. For transfer-path accounts (IRA, Roth IRA, Brokerage), this means creating a
recurring transfer from checking. For paycheck-deduction-path accounts (401(k), Roth 401(k)),
this means verifying a paycheck deduction is linked to the account via `target_account_id`.
Same pattern as task 5.1 commit 5.1-3.

### C. Files modified

- `app/routes/investment.py` -- Add contribution prompt check; add transfer creation route.
- `app/templates/investment/dashboard.html` -- Add prompt section.
- `app/schemas/validation.py` -- Add schema for the recurring transfer creation form.
- `tests/test_routes/test_investment.py` -- Add tests for the prompt and transfer creation.

### D. Implementation approach

**Prompt detection in `dashboard()`:**

After loading params, check for existing contribution mechanisms:

```python
# Check for linked paycheck deductions (already queried for dashboard)
has_linked_deduction = bool(deductions)

# Check for recurring transfer templates targeting this account
existing_transfer = (
    db.session.query(TransferTemplate)
    .filter(
        TransferTemplate.user_id == current_user.id,
        TransferTemplate.to_account_id == account.id,
        TransferTemplate.is_active.is_(True),
        TransferTemplate.recurrence_rule_id.isnot(None),
    )
    .first()
)

needs_contribution_prompt = not has_linked_deduction and not existing_transfer
```

**Prompt content varies by account type:**

```python
is_paycheck_deduction_type = account.account_type_id in (
    ref_cache.acct_type_id(AcctTypeEnum.K401),
    ref_cache.acct_type_id(AcctTypeEnum.ROTH_401K),
)
```

- **For 401(k)/Roth 401(k):** Display: "Link a paycheck deduction to this account. Go to
  your Salary Profile to configure a deduction targeting this account." With a link to the
  salary profile edit page. This is a prompt only -- deduction linking is done on the salary
  page (the deduction model has a `target_account_id` FK that the salary edit form sets).

- **For IRA/Roth IRA/Brokerage:** Display: "Create a recurring transfer of $X from [source] to this
  account?" where X is calculated:

  ```python
  if params.annual_contribution_limit:
      remaining_periods = _count_remaining_periods_this_year(all_periods)
      suggested = (params.annual_contribution_limit / max(remaining_periods, 1)).quantize(TWO_PLACES)
  else:
      suggested = Decimal("500.00")  # Reasonable default for brokerage
  ```

  Include a source account dropdown and "Create Transfer" button.
.

**New route:** `POST /accounts/<id>/investment/create-contribution-transfer`:

1. Validate ownership of both accounts.
2. Create a RecurrenceRule with pattern=EVERY_PERIOD (biweekly contributions match paycheck
   cadence).
3. Create a TransferTemplate with from_account=selected, to_account=investment_account,
   default_amount=contribution_amount, recurrence_rule_id=rule.id.
4. Generate transfers for existing periods via
   `transfer_recurrence.generate_for_template(template, periods, scenario.id)`.
5. Redirect to the investment dashboard with a success flash.

**Source account selection:** Same pattern as 5.1-3: dropdown of user's active accounts
excluding the current investment account. Default to checking account.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.2-17 | test_prompt_shown_no_contribution | IRA with params, no transfer or deduction | GET dashboard | Transfer prompt visible | New |
| C-5.2-18 | test_prompt_shown_401k_no_deduction | 401(k) with params, no linked deduction | GET dashboard | Deduction linkage prompt visible | New |
| C-5.2-19 | test_prompt_hidden_transfer_exists | IRA + active recurring transfer template | GET dashboard | No prompt | New |
| C-5.2-20 | test_prompt_hidden_deduction_linked | 401(k) + linked paycheck deduction | GET dashboard | No prompt | New |
| C-5.2-21 | test_create_transfer_success | IRA + checking account | POST create-contribution-transfer | TransferTemplate created, redirect | New |
| C-5.2-22 | test_create_transfer_idor | Other user's IRA | POST create-contribution-transfer | 404 | New |

### F. Manual verification steps

1. Create a Roth IRA account; save investment parameters ($7,000 annual limit).
2. Verify the dashboard shows a prompt: "Create a recurring transfer of $X..."
3. Select checking account and click "Create Transfer."
4. Verify: redirect, success flash, prompt disappears.
5. Navigate to Transfers page; verify the recurring transfer template exists.
6. Navigate to the budget grid; verify shadow transactions appear.
7. For a 401(k): create the account, save params, verify the prompt links to salary profile.

### G. Downstream effects

- The recurring transfer creates shadow transactions that flow into the balance calculator
  (checking balance decreases, investment account receives income).
- The transfers appear in the grid as shadow transactions.
- The investment dashboard shows the contribution-aware projection (commit 5.2-2).

### H. Rollback notes

No migration. Route + template + schema change. Revert removes the prompt; existing transfers
and deductions are not affected.

---

## Task 5.3: Investment What-If Contribution Calculator

### Commit 5.3-1: Add what-if contribution input and dual-projection growth chart

### A. Commit message

```text
feat(investment): add what-if contribution calculator with dual-projection chart
```

### B. Problem statement

The investment dashboard growth chart (`app/routes/investment.py:growth_chart`, line 251) shows
a single projection line based on the current contribution plan. The user has no way to
visualize the impact of changing their contribution amount. This mirrors task 5.5 (Payoff
Calculator Multi-Scenario Visualization) but for investment accounts. After this change, a
what-if input allows the user to enter a hypothetical contribution amount, and the chart
overlays both the committed projection and the hypothetical projection.

### C. Files modified

- `app/routes/investment.py` -- Modify `growth_chart()` to accept `what_if_contribution`
  parameter; compute dual projections.
- `app/templates/investment/dashboard.html` -- Add what-if input field; update chart rendering
  for dual datasets with distinct styling.
- `tests/test_routes/test_investment.py` -- Add what-if tests.

### D. Implementation approach

**What-if input:** Add an input field on the investment dashboard below the existing horizon
slider. Label: "What if I contributed $____ per period?" Default value: empty (no what-if line
shown). Same slider/input two-way binding pattern used by the payoff calculator in
`loan/dashboard.html` (lines 203-276).

**HTMX integration:** The what-if amount is included in the growth chart HTMX request:

```html
<input type="number" name="what_if_contribution" step="0.01"
       data-slider-group="whatif"
       hx-get="{{ url_for('investment.growth_chart', account_id=account.id) }}"
       hx-trigger="change, slider-changed from:closest .slider-group"
       hx-target="#growth-chart-container"
       hx-include="[name='horizon_years'],[name='what_if_contribution']">
```

**Growth chart route modification (`growth_chart()`):**

Read the optional `what_if_contribution` parameter:

```python
what_if_raw = request.args.get("what_if_contribution", type=str)
what_if_amount = Decimal(what_if_raw) if what_if_raw else None
```

If provided:

1. Call `project_balance()` twice:
   - **Committed projection:** With the actual contribution timeline (from 5.2-2).
   - **What-if projection:** With `periodic_contribution=what_if_amount` and
     `contributions=None` (pure hypothetical at constant rate). If the account has employer
     match, build new `employer_params` with the hypothetical contribution for correct match
     calculation.
2. Return both datasets to the template.

If not provided: single projection as today (backward compatible).

**Chart rendering (`investment/dashboard.html`):**

The growth chart template (`investment/_growth_chart.html`) currently renders a single dataset.
Update to support two datasets via `data-*` attributes (CSP-compliant pattern):

- **Committed line:** Solid primary color (existing style).
- **What-if line:** Dashed line in a distinct color (e.g., `borderDash: [5, 5]`).

Both lines use the same x-axis (periods). The chart legend distinguishes them: "Current Plan"
vs. "What-If ($X/period)".

**Balance comparison card:** Below the chart, show a comparison card when what-if is active:

```text
At Year 30:
  Current plan: $1,245,000
  What-if:      $1,567,000
  Difference:   +$322,000
```

Use the last period's `end_balance` from each projection.

**Employer match adjustment:** When the what-if amount differs from the current contribution,
the employer match (if configured) changes accordingly. The what-if projection builds
`employer_params` with the what-if amount so `calculate_employer_contribution()` computes
the match on the hypothetical contribution.

**Contribution limit enforcement:** The what-if projection respects the annual contribution
limit. If the hypothetical amount would exceed the limit, contributions are capped (same
engine behavior as commit 5.2-1).

**Clearing what-if:** If the input is cleared (empty or zero), the chart reverts to showing
only the committed line and the comparison card is hidden.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.3-1 | test_chart_no_what_if | Investment account with params | GET growth-chart | Single line, no what-if | New |
| C-5.3-2 | test_chart_with_what_if | Investment, what_if_contribution=500 | GET growth-chart?what_if_contribution=500 | Two lines on chart | New |
| C-5.3-3 | test_what_if_exceeds_limit | $7000 limit, what_if=$500/period (>$7000/yr) | GET growth-chart?what_if_contribution=500 | Contributions capped at limit | New |
| C-5.3-4 | test_what_if_with_employer_match | Employer match 100% up to 6%, what_if=300 | GET growth-chart?what_if_contribution=300 | Employer match recalculated for hypothetical | New |
| C-5.3-5 | test_what_if_zero | what_if_contribution=0 | GET growth-chart?what_if_contribution=0 | What-if shows growth only, no contributions | New |
| C-5.3-6 | test_what_if_brokerage_no_limit | Brokerage (no limit), what_if=1000 | GET growth-chart?what_if_contribution=1000 | No capping, full contribution projected | New |
| C-5.3-7 | test_what_if_idor | Other user's account | GET growth-chart?what_if_contribution=500 | 404 | New |

### F. Manual verification steps

1. Create a Roth IRA with $7,000 annual limit, 7% return.
2. Set up a recurring transfer of $269/period.
3. Visit the investment dashboard.
4. Verify the growth chart shows one projection line.
5. Enter $500 in the "What if" input.
6. Verify the chart shows two lines: committed (solid) and what-if (dashed).
7. Verify the comparison card shows projected balances and the difference.
8. Verify the what-if line respects the contribution limit.
9. Clear the input; verify the chart reverts to one line.

### G. Downstream effects

- Chart rendering changes affect only the investment dashboard growth chart.
- The retirement dashboard is NOT affected (it does not show per-account what-if inputs).

### H. Rollback notes

Route + template change. No migration. Revertable.

---

## Task 5.7: ARM Rate Adjustment Support in Amortization Engine

### Commit 5.7-1: Extend amortization engine to accept rate change history

### A. Commit message

```text
feat(amortization): incorporate ARM rate change history into schedule projections
```

### B. Problem statement

The `LoanParams` table stores ARM fields (`is_arm`, `arm_first_adjustment_months`,
`arm_adjustment_interval_months`) and the `rate_history` table stores historical rate changes
(`app/models/loan_features.py:RateHistory`). However, the amortization engine
(`app/services/amortization_engine.py`) currently ignores both -- it projects the entire
remaining term at the single `interest_rate` from LoanParams. For ARM mortgages, the rate will
adjust at defined intervals, and the projected schedule should reflect known rate changes from
the rate_history table.

### C. Files modified

- `app/services/amortization_engine.py` -- Add `rate_changes` parameter to
  `generate_schedule()`, `calculate_summary()`, and `get_loan_projection()`. Add
  `RateChangeRecord` dataclass.
- `app/routes/loan.py` -- Load rate_history and pass to engine on ARM loan dashboards.
- `tests/test_services/test_amortization_engine.py` -- Add ARM rate change tests.

### D. Implementation approach

**New dataclass:**

```python
@dataclasses.dataclass(frozen=True)
class RateChangeRecord:
    """A rate change applied to a loan at a specific date."""
    effective_date: date
    interest_rate: Decimal  # New annual rate as decimal (e.g., 0.065 for 6.5%)
```

**Extend `generate_schedule()` signature:**

```python
def generate_schedule(
    current_principal, annual_rate, remaining_months,
    extra_monthly=Decimal("0.00"),
    origination_date=None, payment_day=1,
    original_principal=None, term_months=None,
    payments=None,
    rate_changes=None,  # Optional[list[RateChangeRecord]]
) -> list[AmortizationRow]:
```

**Behavior when `rate_changes` is provided:**

1. Sort rate_changes by effective_date.
2. For each month in the schedule:
   a. Determine the applicable rate: the most recent rate_change entry with
      `effective_date <= payment_date`. If no rate_change applies for this month, use the
      base `annual_rate`.
   b. When the rate changes, recalculate the monthly payment for the remaining balance and
      remaining months (re-amortize at the new rate). This is standard ARM behavior: when the
      rate adjusts, the monthly payment changes to amortize the remaining balance over the
      remaining term at the new rate.
   c. Record the rate used in a new `interest_rate` field on `AmortizationRow`.

**Add `interest_rate` to `AmortizationRow`:**

```python
@dataclasses.dataclass(frozen=True)
class AmortizationRow:
    month: int
    payment_date: date
    payment: Decimal
    principal: Decimal
    interest: Decimal
    extra_payment: Decimal
    remaining_balance: Decimal
    is_confirmed: bool = False
    interest_rate: Decimal | None = None  # New: annual rate used for this period
```

**Route integration:** In `loan.py:dashboard()`, when the loan has `is_arm=True`, query
`RateHistory` for the account and convert to `RateChangeRecord` instances:

```python
if params.is_arm:
    rate_history = (
        db.session.query(RateHistory)
        .filter_by(account_id=account.id)
        .order_by(RateHistory.effective_date)
        .all()
    )
    rate_changes = [
        RateChangeRecord(rh.effective_date, rh.interest_rate)
        for rh in rate_history
    ]
else:
    rate_changes = None
```

Pass `rate_changes` to `get_loan_projection()` and `payoff_calculate()`.

**For future adjustment dates beyond the last historical entry:** The engine projects the
current rate forward. The roadmap explicitly states: "the app does not predict future rates."
The user manually enters the new rate when it adjusts.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.7-1 | test_arm_no_rate_changes | ARM loan, empty rate_changes | generate_schedule(rate_changes=[]) | Same as fixed-rate schedule | New |
| C-5.7-2 | test_arm_single_rate_increase | ARM loan, rate_change from 5% to 7% at month 60 | generate_schedule(rate_changes=[...]) | Payment increases at month 60, total interest higher | New |
| C-5.7-3 | test_arm_single_rate_decrease | ARM loan, rate_change from 7% to 5% at month 60 | generate_schedule(rate_changes=[...]) | Payment decreases at month 60, total interest lower | New |
| C-5.7-4 | test_arm_multiple_rate_changes | ARM loan, 3 rate changes at different dates | generate_schedule | Rate applied at correct months, payments re-amortize each time | New |
| C-5.7-5 | test_arm_rate_change_before_origination | rate_change date before origination_date | generate_schedule | Rate change ignored (before loan start) | New |
| C-5.7-6 | test_arm_rate_change_with_payments | ARM loan + payments + rate_changes | generate_schedule | Both payments and rate changes applied correctly | New |
| C-5.7-7 | test_arm_dashboard_passes_rate_history | ARM mortgage with rate_history entries | GET dashboard | Dashboard uses rate changes in projection | New |
| C-5.7-8 | test_non_arm_dashboard_ignores_rate_history | Non-ARM auto loan with rate_history entries (shouldn't exist but defensive) | GET dashboard | rate_changes=None passed to engine | New |

### F. Manual verification steps

1. Create a mortgage account with `is_arm=True`.
2. Save loan parameters.
3. Add a rate change record: effective 2027-01-01, rate 7.0%.
4. Visit the loan dashboard.
5. Verify the projected payoff date and monthly payment reflect the rate change.
6. Verify the balance-over-time chart shows a steeper or flatter curve after the adjustment.

### G. Downstream effects

- Chart data service calls `get_loan_projection()` -- gains automatic ARM support when
  `rate_changes` are passed (commit 5.5-1 will thread this through).
- Payoff calculator operates on the rate-adjusted schedule.
- `interest_rate` field on `AmortizationRow` is informational -- existing consumers can ignore it.

### H. Rollback notes

No migration. Service-only change with backward-compatible interface. Revertable.

---

## Task 5.8: Amortization Engine Edge Cases

### Commit 5.8-1: Add overpayment and zero-balance guards to amortization engine

### A. Commit message

```text
fix(amortization): handle overpayment and zero-balance termination edge cases
```

### B. Problem statement

With real payment data flowing into the amortization engine (task 5.1), edge cases that were
theoretical with static projections become reachable in practice. Two guards are needed:

1. **Overpayment:** If a payment exceeds the remaining principal + interest for a period, the
   principal must not go negative.
2. **Zero-balance termination:** Once the principal reaches zero, the engine must stop
   generating schedule entries.

These guards currently do not exist in `generate_schedule()` (line ~130). The function
generates entries for the full `remaining_months` regardless of balance.

### C. Files modified

- `app/services/amortization_engine.py` -- Add guards in the per-period loop.
- `tests/test_services/test_amortization_engine.py` -- Add edge case tests.

### D. Implementation approach

In `generate_schedule()`, within the per-month loop:

**Guard 1 -- Overpayment:** After computing `principal_payment = payment - interest_payment`,
if `principal_payment > remaining_balance`, cap it:

```python
if principal_payment > remaining_balance:
    principal_payment = remaining_balance
    payment = interest_payment + principal_payment  # Adjust total payment
    remaining_balance = Decimal("0.00")
```

This records the final payment as exactly the amount needed to zero out the loan.

**Guard 2 -- Zero-balance termination:** After updating `remaining_balance`, if it is zero,
append the final row and break out of the loop:

```python
if remaining_balance <= Decimal("0.00"):
    remaining_balance = Decimal("0.00")
    rows.append(AmortizationRow(...))
    break
```

Any payments in the input list after the payoff month are ignored by the engine. The roadmap
explicitly states: "Payments after payoff are ignored by the engine (they may represent refunds,
escrow adjustments, or data artifacts)."

**Interaction with `calculate_summary()`:** The summary already computes payoff_date from the
schedule's last row. With early termination, this naturally reflects the accelerated payoff.
No changes needed to `calculate_summary()` beyond passing through the parameters.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.8-1 | test_payment_exactly_equals_remaining | $1000 remaining, payment=$1000 + interest | generate_schedule | One row, balance = $0 | New |
| C-5.8-2 | test_payment_exceeds_remaining | $1000 remaining, payment=$5000 | generate_schedule | One row, balance = $0, payment capped at $1000 + interest | New |
| C-5.8-3 | test_zero_balance_stops_schedule | 360mo term, lump sum pays off in month 6 | generate_schedule | 6 rows, not 360 | New |
| C-5.8-4 | test_payments_after_payoff_ignored | Payoff in month 6, payments exist for months 7-12 | generate_schedule | 6 rows, post-payoff payments ignored | New |
| C-5.8-5 | test_very_large_one_time_payment | $200K loan, single payment of $300K | generate_schedule | Single row with payoff | New |
| C-5.8-6 | test_remaining_balance_never_negative | Various edge cases | generate_schedule | remaining_balance >= 0 for every row | New |
| C-5.8-7 | test_zero_principal_loan | current_principal = $0 | generate_schedule | Empty schedule (already paid off) | New |

### F. Manual verification steps

No UI changes. Verify via tests.

### G. Downstream effects

- Loan dashboard projections now correctly terminate at payoff instead of projecting zeros or
  negative balances for the remaining term.
- Chart data service schedule data will be shorter for loans with extra payments.
- Payoff calculator results become more accurate for near-payoff loans.

### H. Rollback notes

No migration. Service-only change. Revertable.

---

## Task 5.5: Payoff Calculator Multi-Scenario Visualization

### Commit 5.5-1: Add multi-scenario chart lines and calculator display

### A. Commit message

```text
feat(loan): add multi-scenario visualization to payoff calculator and balance chart
```

### B. Problem statement

The loan dashboard balance chart currently shows a single line (original schedule). After
task 5.1 connects payment data, the chart and payoff calculator should display three projection
lines and a progress marker:

1. **Original schedule:** Contractual baseline (no payment data).
2. **Committed schedule:** Confirmed + projected payments.
3. **What-if schedule:** Confirmed payments + user-entered hypothetical extra.
4. **Floor marker:** Current position (confirmed payments only, standard payments forward).

### C. Files modified

- `app/services/chart_data_service.py` -- Extend `get_amortization_breakdown()` to return
  multi-line datasets.
- `app/routes/loan.py` -- Pass multi-scenario data to template.
- `app/templates/loan/dashboard.html` -- Update chart rendering for multiple lines.
- `app/templates/loan/_payoff_results.html` -- Update payoff results to show committed vs.
  floor side by side.
- `tests/test_services/test_chart_data.py` -- Add multi-scenario tests.
- `tests/test_routes/test_loan.py` -- Update payoff calculator tests.

### D. Implementation approach

**Chart data service changes:**

Extend `get_amortization_breakdown()` to accept payment data and return multiple datasets:

```python
def get_amortization_breakdown(user_id, account_id=None):
    # ... existing account/params loading ...
    payments = _get_payment_data(account_id, scenario_id)  # New helper

    # Line 1: Original schedule (no payments)
    original = amortization_engine.generate_schedule(
        params.current_principal, params.interest_rate,
        remaining_months, origination_date=params.origination_date,
        payment_day=params.payment_day,
        original_principal=params.original_principal,
        term_months=params.term_months,
    )

    # Line 2: Committed schedule (all payments)
    committed = amortization_engine.generate_schedule(
        ..., payments=payments, rate_changes=rate_changes,
    ) if payments else None

    # Floor: confirmed payments only, standard payments forward
    confirmed_only = [p for p in payments if p.is_confirmed]
    floor_schedule = amortization_engine.generate_schedule(
        ..., payments=confirmed_only, rate_changes=rate_changes,
    ) if confirmed_only else None
```

Return a dict with separate datasets for each line, plus a floor marker point.

**Chart styling:** Use distinct visual styles per the roadmap:

- Original: lighter dashed line (reference baseline).
- Committed: solid primary-color line (the user's plan).
- What-if: distinct dashed line (hypothetical, shown only when user enters a value).
- Floor: filled circle marker at current date/principal.

Chart.js supports multiple datasets with independent styles. The template passes each dataset
as a separate `data-*` attribute for CSP-compliant rendering.

**Payoff calculator display changes:**

The `_payoff_results.html` partial is updated to show:

- **Committed payoff:** date, months saved vs. original, interest saved vs. original.
- **Floor payoff:** date, months saved vs. original (based on confirmed payments only).
- **What-if payoff:** (when entered) date, months saved vs. committed, interest saved vs.
  committed.

The "target date mode" operates from the current real principal (from confirmed payments).

**What-if input:** An input field on the payoff calculator tab where the user enters a
hypothetical extra monthly payment. When submitted (HTMX POST), the engine computes the what-if
schedule from confirmed payments + synthetic projected payments at the hypothetical amount.

**No recurring transfer case:** If the user has no recurring transfer for the debt account, the
committed schedule line does not appear. The chart shows only the original schedule and the
floor marker. The what-if input operates from the floor.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.5-1 | test_chart_original_schedule_only | Mortgage, no payments | GET amortization chart | Single line (original), no committed/what-if | New |
| C-5.5-2 | test_chart_with_committed_schedule | Mortgage + recurring transfer | GET amortization chart | Two lines (original + committed) | New |
| C-5.5-3 | test_chart_with_floor_marker | Mortgage + confirmed payments | GET amortization chart | Floor marker at current principal | New |
| C-5.5-4 | test_payoff_results_committed_and_floor | Mortgage + payments | POST payoff | Both committed and floor payoff dates shown | New |
| C-5.5-5 | test_payoff_what_if | Mortgage + payments + what_if_extra=200 | POST payoff | Three scenarios compared | New |
| C-5.5-6 | test_payoff_no_transfer_degrades_gracefully | Mortgage, no transfer | POST payoff | Only original schedule and floor (which equals original) | New |

### F. Manual verification steps

1. Create a mortgage with a recurring transfer that includes $100 extra over the minimum.
2. Mark 3 transfers as "Paid" (confirmed payments).
3. Visit the loan dashboard.
4. Verify three chart lines: original (dashed, lighter), committed (solid), and a marker at
   the current principal.
5. Open the Payoff Calculator tab.
6. Verify committed payoff date and floor payoff date are shown side by side.
7. Enter $200 in the what-if field.
8. Verify a third what-if result appears, comparing against the committed schedule.

### G. Downstream effects

- Chart rendering changes affect only the loan dashboard's balance chart.
- The amortization breakdown chart on the Charts page (`GET /charts/amortization`) also calls
  `get_amortization_breakdown()` -- it will automatically show multi-scenario data. The chart
  template (`charts/_amortization.html`) must handle the new dataset structure. If this is too
  disruptive, the Charts page version can remain single-line by passing `include_scenarios=False`.

### H. Rollback notes

No migration. Template + service changes. Revertable, though chart styling requires
template restoration.

---

## Task 5.14: Payment Allocation Breakdown on Loan Dashboard

### Commit 5.14-1: Add payment breakdown display to loan dashboard

### A. Commit message

```text
feat(loan): add payment allocation breakdown showing principal/interest/escrow split
```

### B. Problem statement

When a user makes a mortgage payment, the full amount leaves checking but only the principal
portion reduces the loan balance. The user cannot see how their payment is split between
principal, interest, and escrow without consulting the full amortization schedule.

### C. Files modified

- `app/routes/loan.py` -- Compute payment breakdown for current period; pass to template.
- `app/templates/loan/dashboard.html` -- Add breakdown display to the Overview tab.
- `app/templates/loan/_payment_breakdown.html` -- New partial for the breakdown card.
- `tests/test_routes/test_loan.py` -- Add breakdown display tests.

### D. Implementation approach

The amortization engine already computes the principal/interest split in each `AmortizationRow`.
The escrow amount is computed by `escrow_calculator.calculate_monthly_escrow()`.

In `loan.py:dashboard()`:

1. From the schedule (already computed), find the row corresponding to the current month
   (or the most recent month if between payments).
2. Extract: `principal_portion`, `interest_portion` from the row.
3. Compute `escrow_portion` from `escrow_calculator.calculate_monthly_escrow(components)`.
4. Total = principal + interest + escrow.
5. Pass breakdown dict to template.

**Template (`_payment_breakdown.html`):**

A card in the Loan Summary section showing:

```text
Of your $1,910.95 payment:
  $395.12 to principal (20.7%)
  $898.84 to interest (47.0%)
  $616.99 to escrow (32.3%)
```

With a horizontal stacked bar (Bootstrap progress bar) visualizing the proportions.

**Edge cases:**

- No escrow components: show only principal/interest split.
- Loan with zero remaining balance: show "Loan paid off -- no payment due."
- No schedule available (params incomplete): hide the breakdown card.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.14-1 | test_breakdown_shows_on_dashboard | Mortgage with params + escrow | GET dashboard | Breakdown card visible with correct amounts | New |
| C-5.14-2 | test_breakdown_no_escrow | Auto loan with params, no escrow | GET dashboard | Breakdown shows only P&I split | New |
| C-5.14-3 | test_breakdown_proportions | Known P/I/E amounts | GET dashboard | Percentages sum to 100% | New |

### F. Manual verification steps

1. Visit a mortgage dashboard with escrow components.
2. Verify the payment breakdown card shows the correct split.
3. Add or remove an escrow component; refresh and verify the breakdown updates.

### G. Downstream effects

None. Display-only change.

### H. Rollback notes

Template-only addition. Trivially revertable.

---

## Task 5.13: Full Amortization Schedule View

### Commit 5.13-1: Add collapsible full amortization schedule to loan dashboard

### A. Commit message

```text
feat(loan): add full month-by-month amortization schedule view
```

### B. Problem statement

The loan detail page shows summary metrics but not the full month-by-month schedule. The
amortization engine already computes this data. Displaying it serves as both a user feature and
a verification tool -- the user can cross-reference against their lender's statement.

### C. Files modified

- `app/routes/loan.py` -- Pass full schedule to template (already computed by
  `get_loan_projection()`).
- `app/templates/loan/dashboard.html` -- Add a new tab "Amortization Schedule".
- `app/templates/loan/_schedule.html` -- New partial for the schedule table.
- `tests/test_routes/test_loan.py` -- Add schedule view tests.

### D. Implementation approach

The `get_loan_projection()` call in `dashboard()` already returns a `LoanProjection` with a
`schedule` field containing the list of `AmortizationRow` instances. Pass this directly to the
template.

**Template (`_schedule.html`):**

A collapsible section (Bootstrap collapse, hidden by default) with a toggle button
"View Full Schedule." The table has columns:

| # | Date | Payment | Principal | Interest | Extra | Balance | Status |
|---|------|---------|-----------|----------|-------|---------|--------|

The Status column shows a badge: "Confirmed" (bold/highlighted) for rows where
`row.is_confirmed == True`, "Projected" (normal weight) otherwise.

**Performance consideration:** A 30-year mortgage has 360 rows. Rendering a 360-row table is
fine for server-rendered HTML. The table is hidden by default (collapsed), so it only impacts
DOM size, not initial render time. No pagination needed.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.13-1 | test_schedule_tab_exists | Mortgage with params | GET dashboard | "Amortization Schedule" tab present | New |
| C-5.13-2 | test_schedule_has_correct_row_count | 30yr mortgage | GET dashboard | 360 rows (or fewer if early payoff) | New |
| C-5.13-3 | test_schedule_confirmed_rows_marked | Mortgage with confirmed payments | GET dashboard | Confirmed rows have distinct styling | New |
| C-5.13-4 | test_schedule_first_last_row | Known loan params | GET dashboard | First row has correct payment; last row has $0 balance | New |

### F. Manual verification steps

1. Visit a mortgage dashboard.
2. Click the "Amortization Schedule" tab.
3. Click "View Full Schedule" to expand.
4. Verify the table shows month-by-month data.
5. If payments exist, verify confirmed rows are visually distinct from projected.
6. Cross-reference a few rows against a mortgage calculator (e.g., bankrate.com).

### G. Downstream effects

None. Display-only change.

### H. Rollback notes

Template-only addition. Trivially revertable.

---

## Task 5.9: Loan Payoff Lifecycle

### Overview

Three components: (1) auto-update recurrence rule end date to projected payoff date,
(2) paid-off badge on accounts dashboard, (3) account archival.

---

### Commit 5.9-1: Auto-update recurring transfer end date to projected payoff date

### A. Commit message

```text
feat(loan): auto-set recurring transfer end date to projected payoff date
```

### B. Problem statement

When the amortization engine computes a payoff date (incorporating actual payment data and the
recurring transfer amount), the recurrence rule for the loan's recurring transfer should have
its `end_date` set to the projected payoff date. This prevents the recurrence engine from
generating shadow transactions beyond payoff.

### C. Files modified

- `app/routes/loan.py` -- After computing payoff date on dashboard load, compare to recurrence
  rule end date and update if different.
- `tests/test_routes/test_loan.py` -- Add end date auto-update tests.

### D. Implementation approach

In `loan.py:dashboard()`, after computing the projection:

1. Find the recurring transfer template for this account (same query as commit 5.1-3).
2. If a template exists and has a recurrence_rule:
   a. Get the committed payoff date from the engine's schedule (last row's payment_date).
   b. Compare to `template.recurrence_rule.end_date`.
   c. If different (or end_date is None), update: `template.recurrence_rule.end_date = payoff_date`.
   d. Commit the change.
3. If no template exists, do nothing.

**Trigger mechanism:** The update happens on dashboard load. This is simple and reliable:

- The payoff date changes only when payment behavior changes (extra payment, missed payment).
- The user visits the dashboard to check their loan status, which is when they would care
  about the payoff date.
- The recurrence engine checks `end_date` during generation. If the end date was updated, the
  next regeneration cycle skips periods after payoff.

**Edge case -- payoff date moves later:** If the user cancels an extra payment, the payoff date
extends. The end date is updated on the next dashboard visit, and subsequent regeneration
cycles will generate transfers for the extended periods.

**Edge case -- no payoff (principal increases due to negative amortization):** If payments are
insufficient to cover interest, the principal grows and there is no payoff date. Do not set
an end_date in this case (leave it None = indefinite).

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.9-1 | test_end_date_set_on_dashboard_load | Mortgage + recurring transfer, no end_date | GET dashboard | Recurrence rule end_date = projected payoff date | New |
| C-5.9-2 | test_end_date_updated_when_payoff_changes | Mortgage + transfer, extra payment changes payoff | GET dashboard twice | end_date reflects new payoff date | New |
| C-5.9-3 | test_end_date_cleared_when_no_payoff | Negative amortization scenario | GET dashboard | end_date remains None | New |
| C-5.9-4 | test_no_update_when_no_transfer | Mortgage without recurring transfer | GET dashboard | No error, no update | New |

### F. Manual verification steps

1. Create a mortgage with a recurring transfer.
2. Visit the dashboard; note the projected payoff date.
3. Navigate to Transfers page and verify the recurrence rule has an end_date matching the payoff.
4. Add an extra one-time payment.
5. Revisit the dashboard; verify the payoff date moved earlier and the end_date updated.

### G. Downstream effects

- The recurrence engine respects `end_date` in `RecurrenceRule.end_date` (verified in
  `recurrence_rule.py` line ~30). Transfers are not generated for periods after end_date.
- The transfer recurrence engine also respects end_date (same pattern matching via
  `_match_periods`).
- No balance calculator impact.

### H. Rollback notes

No migration. Route-only change. Revert leaves end_date at whatever value was last set (no
data corruption -- the end_date is simply not updated anymore).

---

### Commit 5.9-2: Add paid-off status indicator to accounts dashboard

### A. Commit message

```text
feat(accounts): display paid-off badge on debt accounts with zero principal
```

### B. Problem statement

When a debt account's current real principal reaches zero (derived from confirmed payments), the
account card on the accounts dashboard should display a "Paid Off" badge.

### C. Files modified

- `app/services/savings_dashboard_service.py` -- Add `is_paid_off` flag to loan account data.
- `app/templates/savings/dashboard.html` -- Display "Paid Off" badge when flag is true.
- `tests/test_services/test_savings_dashboard_service.py` -- Add paid-off indicator test.
- `tests/test_routes/test_savings.py` -- Add paid-off badge display test.

### D. Implementation approach

In `savings_dashboard_service.py:_compute_account_projections()`, when processing a loan
account:

1. After computing the schedule with payments, check if the final confirmed payment brings
   the principal to zero: replay confirmed payments through the engine and check
   `remaining_balance` of the last confirmed row.
2. Set `is_paid_off = True` if remaining_balance == 0 after all confirmed payments.
3. Include `is_paid_off` in the account data dict.

In `savings/dashboard.html`, when rendering a loan account card:

```html
{% if acct_data.is_paid_off %}
  <span class="badge bg-success">Paid Off</span>
{% endif %}
```

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.9-5 | test_paid_off_badge_shown | Mortgage with confirmed payment = full balance | GET savings dashboard | "Paid Off" badge visible | New |
| C-5.9-6 | test_no_badge_when_balance_remaining | Mortgage with partial payments | GET savings dashboard | No "Paid Off" badge | New |
| C-5.9-7 | test_no_badge_when_no_payments | Mortgage with no payments | GET savings dashboard | No "Paid Off" badge | New |

### F. Manual verification steps

1. Create a small loan ($1,000, 12 months, 5%).
2. Create a transfer for the full amount; mark it as Paid.
3. Visit the accounts dashboard.
4. Verify the "Paid Off" badge appears on the loan card.

### G. Downstream effects

None. Display-only change.

### H. Rollback notes

Service + template change. Revertable.

---

### Commit 5.9-3: Add account archival

### A. Commit message

```text
feat(accounts): add account archival for paid-off loans and inactive accounts
```

### B. Problem statement

Paid-off loans (and other no-longer-active accounts) clutter the accounts dashboard. The user
needs a way to archive an account so it is hidden from active views but remains in the database
for historical reference.

### C. Files modified

- `app/models/account.py` -- Add `is_archived` boolean column.
- Migration -- Add `is_archived` column to `budget.accounts`.
- `app/routes/accounts.py` -- Add archive/unarchive routes.
- `app/services/savings_dashboard_service.py` -- Filter out archived accounts.
- `app/templates/savings/dashboard.html` -- Hide archived accounts; add collapsed "Archived"
  section.
- `app/schemas/validation.py` -- Add `is_archived` to AccountUpdateSchema.
- `tests/test_routes/test_accounts_dashboard.py` -- Add archival tests.

### D. Implementation approach

**Migration:**

```python
# message: "add is_archived column to accounts"
op.add_column('accounts',
    sa.Column('is_archived', sa.Boolean(), nullable=False, server_default='false'),
    schema='budget'
)
```

Downgrade: `op.drop_column('accounts', 'is_archived', schema='budget')`.

No data backfill needed -- all existing accounts default to `is_archived=False`.

**Model change (`account.py`):**

Add `is_archived = db.Column(db.Boolean, nullable=False, default=False, server_default='false')`.

**Route changes (`accounts.py`):**

Add two routes:

- `POST /accounts/<id>/archive` -- Sets `is_archived=True`. Guards: ownership check, cannot
  archive an account that has active transfer templates (warn user to deactivate them first).
- `POST /accounts/<id>/unarchive` -- Sets `is_archived=False`.

**Dashboard filtering (`savings_dashboard_service.py`):**

In `compute_dashboard_data()`, filter the initial account query:

```python
accounts = Account.query.filter_by(user_id=user_id, is_archived=False).all()
```

Add a separate query for archived accounts:

```python
archived_accounts = Account.query.filter_by(user_id=user_id, is_archived=True).all()
```

Return both in the dashboard data dict.

**Template changes:**

In `savings/dashboard.html`:

- Active accounts render as they do now.
- Below the active accounts, a collapsed "Archived Accounts" section shows archived accounts
  with an "Unarchive" button. Collapsed by default.
- Account cards for archived accounts have an "Archive" action button.
- Paid-off loan cards (from commit 5.9-2) show an "Archive" button prominently.

**Balance calculator impact:** Archived accounts should be excluded from balance projections
on the grid. However, the grid already filters by `account_id` (the user selects which account
to view). Archived accounts should not appear in the grid's account selector dropdown. In
`app/routes/grid.py`, the account query should filter `is_archived=False`.

**Transfer template guard:** Before archiving, check if any active transfer templates reference
this account. If yes, flash a warning: "Deactivate transfer templates for this account before
archiving." Do not silently deactivate them -- the user should make that decision.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.9-8 | test_archive_account | Active account | POST /accounts/<id>/archive | is_archived=True, account hidden from dashboard | New |
| C-5.9-9 | test_unarchive_account | Archived account | POST /accounts/<id>/unarchive | is_archived=False, account visible on dashboard | New |
| C-5.9-10 | test_archive_blocked_by_active_transfers | Account with active transfer template | POST /accounts/<id>/archive | Flash warning, account not archived | New |
| C-5.9-11 | test_archived_accounts_hidden_from_dashboard | 2 active + 1 archived | GET savings dashboard | Only 2 accounts in main section, 1 in archived section | New |
| C-5.9-12 | test_archived_accounts_hidden_from_grid_selector | Archived account | GET grid | Account not in selector dropdown | New |
| C-5.9-13 | test_archive_idor | Other user's account | POST /accounts/<id>/archive | 404 | New |

### F. Manual verification steps

1. Visit accounts dashboard. Note 3 accounts visible.
2. Click "Archive" on one account.
3. Verify: account disappears from main view, appears in collapsed "Archived" section.
4. Expand "Archived" section, click "Unarchive."
5. Verify: account reappears in main view.

### G. Downstream effects

- Grid account selector must exclude archived accounts.
- Transfer creation form's account dropdowns should exclude archived accounts.
- Investment/retirement dashboards should exclude archived accounts from projections.
- Savings goal creation should only show non-archived accounts.

### H. Rollback notes

**Migration required.** Downgrade drops the `is_archived` column. All accounts revert to
visible. No data loss.

---

## Task 5.4: Income-Relative Savings Goals

### Overview

Add an income-relative goal mode alongside fixed-amount goals. Two new ref tables, three new
columns on the savings goal model, and UI changes. Four sub-commits:

1. Add ref tables, enums, ref_cache.
2. Add columns to savings goal model (migration).
3. Update service layer.
4. Update UI.

---

### Commit 5.4-1: Add goal mode and income unit reference tables

### A. Commit message

```text
feat(savings): add ref tables for goal modes and income units
```

### B. Problem statement

Income-relative savings goals need two new reference tables following the established pattern:
IDs for logic, names for display only, enum members in `enums.py`, ref_cache entries.

### C. Files modified

- `app/models/ref.py` -- Add `GoalMode` and `IncomeUnit` models.
- `app/enums.py` -- Add `GoalModeEnum` and `IncomeUnitEnum`.
- `app/ref_cache.py` -- Add `_goal_mode_map`, `_income_unit_map`, accessor functions.
- `scripts/seed_ref_tables.py` -- Add seed data for new ref tables.
- `tests/conftest.py` -- Add seed data to `_seed_ref_tables()`.
- Migration -- Create `ref.goal_modes` and `ref.income_units` tables.
- `app/models/__init__.py` -- Register new models.

### D. Implementation approach

**New models in `ref.py`:**

```python
class GoalMode(db.Model):
    """Reference table for savings goal amount modes."""
    __tablename__ = "goal_modes"
    __table_args__ = {"schema": "ref"}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

class IncomeUnit(db.Model):
    """Reference table for income multiplier units."""
    __tablename__ = "income_units"
    __table_args__ = {"schema": "ref"}
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)
```

**Enum members:**

```python
class GoalModeEnum(enum.Enum):
    FIXED = "Fixed"
    INCOME_RELATIVE = "Income-Relative"

class IncomeUnitEnum(enum.Enum):
    PAYCHECKS = "Paychecks"
    MONTHS = "Months"
```

**Seed data:**

```python
GOAL_MODE_SEEDS = [
    ("Fixed",),
    ("Income-Relative",),
]
INCOME_UNIT_SEEDS = [
    ("Paychecks",),
    ("Months",),
]
```

**Ref cache entries:**

```python
_goal_mode_map: dict[GoalModeEnum, int] = {}
_income_unit_map: dict[IncomeUnitEnum, int] = {}

def goal_mode_id(member: GoalModeEnum) -> int: ...
def income_unit_id(member: IncomeUnitEnum) -> int: ...
```

**Migration message:** `"add goal_modes and income_units reference tables"`

**Downgrade:** Drop both tables.

**conftest.py `_seed_ref_tables()`:** Add seeding for GoalMode and IncomeUnit after existing
ref table seeds. Add to the truncate list in the `db` fixture (ref tables are NOT truncated --
they persist across tests -- so no truncate change needed; they are seeded once in
`setup_database`).

**App factory (`__init__.py`):** Add Jinja globals for goal mode and income unit IDs:

```python
app.jinja_env.globals["GOAL_MODE_FIXED"] = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
app.jinja_env.globals["GOAL_MODE_INCOME_RELATIVE"] = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
```

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.4-1 | test_goal_mode_ref_cache | App startup | Check ref_cache.goal_mode_id() | Returns valid integer IDs for both modes | New |
| C-5.4-2 | test_income_unit_ref_cache | App startup | Check ref_cache.income_unit_id() | Returns valid integer IDs for both units | New |
| C-5.4-3 | test_goal_mode_enum_matches_db | Query GoalMode table | Compare to GoalModeEnum | All enum members present in DB | New |

### F. Manual verification steps

Run seed script; verify ref tables populated.

### G. Downstream effects

None yet. Subsequent commits use these tables.

### H. Rollback notes

**Migration required.** Downgrade drops both tables. No data in other tables references them
yet (FK columns added in 5.4-2).

---

### Commit 5.4-2: Add income-relative columns to savings goal model

### A. Commit message

```text
feat(savings): add goal_mode_id, income_unit_id, income_multiplier to savings_goals
```

### B. Problem statement

The `SavingsGoal` model (`app/models/savings_goal.py`) currently has only a fixed
`target_amount`. Income-relative goals need three new columns: `goal_mode_id` (FK),
`income_unit_id` (FK), and `income_multiplier` (Numeric).

### C. Files modified

- `app/models/savings_goal.py` -- Add three columns with constraints.
- Migration -- Add columns to `budget.savings_goals`.
- `app/schemas/validation.py` -- Update `SavingsGoalCreateSchema` and
  `SavingsGoalUpdateSchema`.

### D. Implementation approach

**New columns:**

```python
goal_mode_id = db.Column(
    db.Integer,
    db.ForeignKey("ref.goal_modes.id"),
    nullable=False,
    server_default="1",  # Fixed mode (ID 1)
)
income_unit_id = db.Column(
    db.Integer,
    db.ForeignKey("ref.income_units.id"),
    nullable=True,  # Only used when mode is income-relative
)
income_multiplier = db.Column(
    db.Numeric(8, 2),
    nullable=True,  # Only used when mode is income-relative
)
```

**Constraints:**

```python
db.CheckConstraint(
    "income_multiplier IS NULL OR income_multiplier > 0",
    name="ck_savings_goals_multiplier_positive",
)
```

**Relationships:**

```python
goal_mode = db.relationship("GoalMode", lazy="joined")
income_unit = db.relationship("IncomeUnit", lazy="joined")
```

**Migration message:** `"add income-relative goal columns to savings_goals"`

The `server_default="1"` ensures all existing goals get `goal_mode_id=1` (Fixed) without a
data backfill. This depends on the GoalMode seed assigning ID 1 to "Fixed". Since the seed
script uses auto-increment and seeds "Fixed" first, this is reliable. However, to be safe, the
migration should include a data verification step that confirms goal_mode_id=1 maps to "Fixed":

```python
# In upgrade():
# Verify that ID 1 is "Fixed" (seeded in commit 5.4-1)
conn = op.get_bind()
result = conn.execute(sa.text("SELECT name FROM ref.goal_modes WHERE id = 1"))
row = result.fetchone()
assert row and row[0] == "Fixed", "Expected goal_mode ID 1 to be 'Fixed'"
```

**Downgrade:** Drop the three columns.

**Schema updates:**

In `SavingsGoalCreateSchema`:

```python
goal_mode_id = fields.Integer(load_default=1)  # Default to Fixed
income_unit_id = fields.Integer(load_default=None, allow_none=True)
income_multiplier = fields.Decimal(
    load_default=None, allow_none=True, places=2,
    validate=validate.Range(min=0, min_inclusive=False),
)
```

Cross-field validation: when `goal_mode_id` references income-relative mode, `income_unit_id`
and `income_multiplier` are required. When `goal_mode_id` references fixed mode,
`income_unit_id` and `income_multiplier` must be null. `target_amount` becomes optional when
mode is income-relative (it will be calculated).

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.4-4 | test_existing_goals_default_to_fixed | Existing goal (pre-migration) | Query goal_mode_id | goal_mode_id = 1 (Fixed) | New |
| C-5.4-5 | test_create_fixed_goal | POST savings goal with goal_mode_id=1, target_amount=5000 | Create goal | Goal created with fixed mode, income fields null | New |
| C-5.4-6 | test_create_income_relative_goal | POST with goal_mode_id=2, income_unit_id=1, multiplier=3 | Create goal | Goal created with income-relative mode | New |
| C-5.4-7 | test_income_relative_requires_unit_and_multiplier | POST with goal_mode_id=2, no unit/multiplier | Create goal | Validation error | New |
| C-5.4-8 | test_fixed_mode_rejects_income_fields | POST with goal_mode_id=1, income_unit_id=1 | Create goal | Validation error (income fields must be null for fixed mode) | New |
| C-5.4-9 | test_multiplier_must_be_positive | POST with income_multiplier=0 | Create goal | Validation error | New |

### F. Manual verification steps

Run migration. Verify existing goals still work. Create a new goal with income-relative mode.

### G. Downstream effects

- Goal CRUD in `savings.py` must handle new fields.
- Goal display in dashboard must resolve income-relative targets.
- `target_amount` semantics change: for income-relative goals, `target_amount` may be NULL
  (calculated on read) or stored as a cache. The design says "calculated on read" -- so
  `target_amount` is nullable for income-relative goals.

### H. Rollback notes

**Migration required.** Downgrade drops three columns. Income-relative goals lose their mode
data but the `target_amount` field (if populated) remains usable as a fixed amount.

---

### Commit 5.4-3: Add income-relative goal target resolution to services

### A. Commit message

```text
feat(savings): resolve income-relative goal targets from paycheck calculator
```

### B. Problem statement

When `goal_mode_id` references the income-relative mode, the resolved dollar target is
calculated on read: `multiplier * net_pay_per_unit`. The savings dashboard service must
compute this and provide it to the template.

### C. Files modified

- `app/services/savings_goal_service.py` -- Add `resolve_goal_target()` function.
- `app/services/savings_dashboard_service.py` -- Call `resolve_goal_target()` in goal progress
  computation; load paycheck data for income-relative goals.
- `tests/test_services/test_savings_goal_service.py` -- Add target resolution tests.
- `tests/test_services/test_savings_dashboard_service.py` -- Add income-relative dashboard tests.

### D. Implementation approach

**New function in `savings_goal_service.py`:**

```python
def resolve_goal_target(
    goal,
    net_biweekly_pay,
) -> Decimal:
    """Resolve the dollar target for a savings goal.

    For fixed-mode goals, returns goal.target_amount directly.
    For income-relative goals, computes the target from the
    multiplier and net pay.

    Args:
        goal: SavingsGoal instance with goal_mode_id, income_unit_id,
              income_multiplier, target_amount.
        net_biweekly_pay: Current projected net biweekly pay from
                          the paycheck calculator.

    Returns:
        Resolved target amount as Decimal.
    """
    from app import ref_cache
    from app.enums import GoalModeEnum

    if goal.goal_mode_id == ref_cache.goal_mode_id(GoalModeEnum.FIXED):
        return goal.target_amount

    # Income-relative mode
    from app.enums import IncomeUnitEnum

    multiplier = Decimal(str(goal.income_multiplier))

    if goal.income_unit_id == ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS):
        return (multiplier * net_biweekly_pay).quantize(TWO_PLACES)

    if goal.income_unit_id == ref_cache.income_unit_id(IncomeUnitEnum.MONTHS):
        monthly_net = (net_biweekly_pay * 26 / 12).quantize(TWO_PLACES)
        return (multiplier * monthly_net).quantize(TWO_PLACES)

    # Unknown unit -- fall back to target_amount if available
    return goal.target_amount or Decimal("0.00")
```

**Dashboard service changes:**

In `savings_dashboard_service.py:_compute_goal_progress()`:

1. Load the user's active salary profile and compute current net biweekly pay via
   `paycheck_calculator.calculate_paycheck()` for the current period.
2. For each goal, call `resolve_goal_target(goal, net_biweekly_pay)` to get the resolved
   target.
3. Use the resolved target for progress percentage and required contribution calculations.
4. Include `resolved_target` and `goal_mode` in the goal data dict for display.

**Edge cases:**

- No salary profile: `net_biweekly_pay = Decimal("0.00")`. Income-relative target resolves
  to $0.00. Display a warning: "No salary profile configured."
- Zero net pay: Same behavior. The goal target is $0.00, which means any balance meets it.
  This is mathematically correct but practically useless -- display a note.
- Goal with target_amount already set: For fixed goals, target_amount is authoritative. For
  income-relative goals, target_amount is ignored in favor of the calculated value.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.4-10 | test_resolve_fixed_goal | Fixed goal, target=$5000 | resolve_goal_target() | Returns $5000 | New |
| C-5.4-11 | test_resolve_income_relative_paychecks | 3 paychecks, net=$2000/paycheck | resolve_goal_target() | Returns $6000 | New |
| C-5.4-12 | test_resolve_income_relative_months | 3 months, net=$2000/paycheck | resolve_goal_target() | Returns $13,000 (3 * 2000 * 26/12) | New |
| C-5.4-13 | test_resolve_no_salary_profile | Income-relative goal, no salary profile | resolve_goal_target(net=0) | Returns $0.00 | New |
| C-5.4-14 | test_dashboard_shows_resolved_target | Income-relative goal, salary configured | GET savings dashboard | Dashboard shows calculated dollar amount | New |
| C-5.4-15 | test_goal_target_updates_with_salary_change | Create goal, then change salary | GET dashboard twice | Resolved target changes | New |

### F. Manual verification steps

1. Create a salary profile with known net pay.
2. Create an income-relative savings goal: 3 months of salary.
3. Visit the accounts dashboard.
4. Verify the goal card shows the calculated target: 3 *(net_biweekly* 26 / 12).
5. Add a raise to the salary profile.
6. Revisit the dashboard; verify the target updates.

### G. Downstream effects

- Goal progress percentage changes when salary changes (dynamic target).
- Required contribution per period changes accordingly.
- Emergency fund metrics are not affected (they use total balance, not goal targets).

### H. Rollback notes

Service-only change. Revertable.

---

### Commit 5.4-4: Update savings goal UI for income-relative mode

### A. Commit message

```text
feat(savings): add income-relative mode toggle to savings goal form
```

### B. Problem statement

The savings goal form (`app/templates/savings/goal_form.html`) currently has only a fixed
target_amount field. It needs a mode toggle and conditional fields for income-relative goals.

### C. Files modified

- `app/routes/savings.py` -- Pass goal modes and income units to template; handle new fields
  in create/update.
- `app/templates/savings/goal_form.html` -- Add mode toggle, conditional fields.
- `app/templates/savings/dashboard.html` -- Display resolved target with mode indicator.
- `tests/test_routes/test_savings.py` -- Add UI-level tests for income-relative goals.

### D. Implementation approach

**Form changes (`goal_form.html`):**

Add a dropdown or radio group for Goal Mode (Fixed / Income-Relative).

When "Income-Relative" is selected (via HTMX or JavaScript):

- Hide the `target_amount` field.
- Show: Unit dropdown (Paychecks / Months) and Multiplier input.
- Show a read-only calculated value: "Target: $X,XXX.XX" based on the current salary.

When "Fixed" is selected:

- Show the `target_amount` field.
- Hide Unit and Multiplier fields.

The form toggles can use simple JavaScript (no HTMX needed for client-side show/hide).

**Route changes (`savings.py`):**

In `create_goal()` and `update_goal()`:

- Load and validate the new fields from the schema.
- For income-relative goals, set `target_amount=None` (calculated on read).
- For fixed goals, ensure `income_unit_id=None` and `income_multiplier=None`.

Pass `goal_modes` and `income_units` to the template context:

```python
goal_modes = GoalMode.query.all()
income_units = IncomeUnit.query.all()
```

**Dashboard display:**

On the goal card, show:

- Fixed: "Target: $5,000.00"
- Income-relative: "Target: $13,000.00 (3 months of salary)"

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.4-16 | test_goal_form_shows_mode_selector | GET /savings/goals/new | Check form | Mode dropdown present with both options | New |
| C-5.4-17 | test_create_income_relative_goal_via_form | POST with income-relative fields | Create goal | Goal created correctly | New |
| C-5.4-18 | test_edit_goal_mode_change | Change goal from fixed to income-relative | POST update | Mode updated, target_amount cleared | New |
| C-5.4-19 | test_dashboard_shows_income_relative_label | Income-relative goal exists | GET dashboard | Card shows "3 months of salary" descriptor | New |

### F. Manual verification steps

1. Navigate to New Goal.
2. Select "Income-Relative" mode.
3. Verify: target_amount field hides, unit dropdown and multiplier field appear.
4. Select "3 Months" and save.
5. Verify goal card shows calculated target with "3 months of salary" label.

### G. Downstream effects

None beyond the savings dashboard.

### H. Rollback notes

Template + route change. Revertable.

---

## Task 5.15: Savings Goal Progress Trajectory

### Commit 5.15-1: Add goal completion trajectory and pace indicator

### A. Commit message

```text
feat(savings): add goal completion trajectory and pace indicator
```

### B. Problem statement

Savings goals show a target and current balance but do not indicate how long it will take to
reach the goal at the current savings rate. With income-relative goals (5.4) where the target
moves dynamically, the user needs to know whether they are keeping pace.

### C. Files modified

- `app/services/savings_goal_service.py` -- Add `calculate_trajectory()` function.
- `app/services/savings_dashboard_service.py` -- Call trajectory calculation in goal progress
  computation; query recurring transfer amount for the goal's account.
- `app/templates/savings/dashboard.html` -- Display trajectory on goal cards.
- `tests/test_services/test_savings_goal_service.py` -- Add trajectory tests.

### D. Implementation approach

**New function in `savings_goal_service.py`:**

```python
def calculate_trajectory(
    current_balance,
    target_amount,
    monthly_contribution,
    target_date=None,
) -> dict:
    """Calculate savings goal completion trajectory.

    Returns:
        months_to_goal: int or None (None if no contribution)
        projected_completion_date: date or None
        pace: 'on_track' | 'behind' | 'ahead' | None
              (None if no target_date set)
        required_monthly: Decimal or None (to hit target_date)
    """
```

**Logic:**

- `months_to_goal = ceil((target - balance) / monthly_contribution)` if contribution > 0.
- `projected_completion_date = today + months_to_goal months`.
- If `target_date` is set: compare projected_completion_date to target_date.
  - ahead: projected < target
  - on_track: projected == target (within same month)
  - behind: projected > target
- If no recurring transfer: `monthly_contribution = 0`, trajectory cannot be computed.
  Return `months_to_goal = None` with a message: "No recurring contribution set."

**Monthly contribution discovery:** Query shadow income transactions linked to the savings
account (same pattern as debt payment discovery in 5.1). If a recurring transfer template
exists for this account, use its `default_amount` normalized to monthly. Otherwise, check for
periodic shadow income from ad-hoc transfers and average them.

**Dashboard display:**

On the goal card:

- Progress bar (already exists or implied).
- Projected completion date: "On track to reach goal by Dec 2027" or "Behind pace -- increase
  monthly contribution from $500 to $650."
- If no contribution: "No recurring contribution set."

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.15-1 | test_trajectory_on_track | Balance=$3000, target=$6000, $500/mo, target_date=6mo | calculate_trajectory() | pace='on_track' | New |
| C-5.15-2 | test_trajectory_behind | Balance=$1000, target=$6000, $500/mo, target_date=3mo | calculate_trajectory() | pace='behind', required_monthly calculated | New |
| C-5.15-3 | test_trajectory_ahead | Balance=$5000, target=$6000, $500/mo, target_date=12mo | calculate_trajectory() | pace='ahead' | New |
| C-5.15-4 | test_trajectory_no_contribution | No recurring transfer | calculate_trajectory(monthly=0) | months_to_goal=None | New |
| C-5.15-5 | test_trajectory_goal_already_met | Balance=$7000, target=$6000 | calculate_trajectory() | months_to_goal=0 | New |
| C-5.15-6 | test_trajectory_no_target_date | Goal without target_date | calculate_trajectory() | pace=None, months_to_goal computed | New |

### F. Manual verification steps

1. Create a savings goal with a target date 6 months away.
2. Create a recurring transfer into the savings account.
3. Visit the dashboard.
4. Verify the goal card shows the projected completion date and pace indicator.

### G. Downstream effects

None beyond the savings dashboard.

### H. Rollback notes

Service + template change. Revertable.

---

## Task 5.12: Debt Summary Metrics and Debt-to-Income Ratio

### Commit 5.12-1: Add debt summary card and DTI ratio to accounts dashboard

### A. Commit message

```text
feat(accounts): add debt summary metrics and debt-to-income ratio
```

### B. Problem statement

The accounts dashboard groups accounts by category but provides no aggregate metrics for the
liability category. The user has no single view of their total debt position or debt health.

### C. Files modified

- `app/services/savings_dashboard_service.py` -- Add `_compute_debt_summary()` helper;
  include debt metrics in dashboard data.
- `app/templates/savings/dashboard.html` -- Add debt summary card in liability section; add
  DTI metric alongside emergency fund metrics.
- `tests/test_services/test_savings_dashboard_service.py` -- Add debt summary tests.
- `tests/test_routes/test_savings.py` -- Add DTI display test.

### D. Implementation approach

**New helper in `savings_dashboard_service.py`:**

```python
def _compute_debt_summary(loan_accounts, loan_params_map, payments_map, scenario_id):
    """Compute aggregate debt metrics across all active loan accounts.

    Returns:
        total_debt: Decimal (sum of current real principal)
        total_monthly_payments: Decimal (sum of monthly P&I)
        weighted_avg_rate: Decimal (weighted by outstanding principal)
        projected_debt_free_date: date or None (latest payoff date)
    """
```

**Logic:**

- For each loan account with LoanParams:
  - Compute current real principal by replaying confirmed payments through the engine
    (or use LoanParams.current_principal if no payments exist).
  - Get monthly P&I from `amortization_engine.calculate_monthly_payment()`.
  - Get payoff date from the schedule's last row.
  - Accumulate weighted rate: `sum(rate * principal) / sum(principal)`.
- `projected_debt_free_date` = latest payoff date across all accounts.

**DTI calculation:**

```python
# Total monthly debt = sum of monthly P&I + monthly escrow for each loan
# Gross monthly income = gross_biweekly * 26 / 12
dti = total_monthly_debt / gross_monthly_income
```

Gross monthly income comes from the paycheck calculator (active salary profile's
`gross_biweekly`). The savings_dashboard_service already loads salary data for investment
projections, so this data is available.

**DTI thresholds (hardcoded, per roadmap):**

- Below 36%: "Healthy" (green badge)
- 36--43%: "Moderate" (yellow badge)
- Above 43%: "High" (red badge)

**Template display:**

In the liability section of `savings/dashboard.html`, add a summary card:

```text
Total Debt: $312,450.00
Monthly Payments: $2,847.92
Weighted Avg Rate: 5.82%
Debt-Free Date: January 2054
```

Below the emergency fund metrics, add:

```text
Debt-to-Income: 34.2% [Healthy]
```

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.12-1 | test_debt_summary_single_loan | One mortgage | GET dashboard | Summary shows loan's metrics | New |
| C-5.12-2 | test_debt_summary_multiple_loans | Mortgage + auto loan | GET dashboard | Summary aggregates both | New |
| C-5.12-3 | test_debt_summary_no_loans | No loan accounts | GET dashboard | No debt summary card | New |
| C-5.12-4 | test_weighted_avg_rate | Two loans, different rates and principals | Compute | Correct weighted average | New |
| C-5.12-5 | test_dti_calculation | Known debt and income | Compute | DTI = expected percentage | New |
| C-5.12-6 | test_dti_no_salary | Loans exist but no salary profile | GET dashboard | DTI shows "N/A" | New |
| C-5.12-7 | test_dti_thresholds | DTI below 36%, 36-43%, above 43% | Compute | Correct color/label | New |

### F. Manual verification steps

1. Create a mortgage ($250K) and auto loan ($25K).
2. Create a salary profile.
3. Visit the accounts dashboard.
4. Verify: debt summary card shows aggregate metrics.
5. Verify: DTI ratio appears with the correct threshold color.

### G. Downstream effects

None beyond the accounts dashboard.

### H. Rollback notes

Service + template change. No migration. Revertable.

---

## Task 5.10: Refinance What-If Calculator

### Commit 5.10-1: Add refinance scenario form and comparison to loan dashboard

### A. Commit message

```text
feat(loan): add refinance what-if calculator with side-by-side comparison
```

### B. Problem statement

A user considering refinancing has no way to compare the current loan schedule against a
hypothetical refinanced schedule within the app.

### C. Files modified

- `app/routes/loan.py` -- Add `POST /accounts/<id>/loan/refinance` endpoint.
- `app/templates/loan/dashboard.html` -- Add "Refinance" tab.
- `app/templates/loan/_refinance.html` -- New partial for refinance form and results.
- `app/schemas/validation.py` -- Add `RefinanceSchema`.
- `tests/test_routes/test_loan.py` -- Add refinance calculator tests.

### D. Implementation approach

**New schema in `validation.py`:**

```python
class RefinanceSchema(BaseSchema):
    new_rate = fields.Decimal(required=True, places=5,
        validate=validate.Range(min=0, max=100))
    new_term_months = fields.Integer(required=True,
        validate=validate.Range(min=1, max=600))
    closing_costs = fields.Decimal(load_default=Decimal("0.00"),
        places=2, validate=validate.Range(min=0))
    new_principal = fields.Decimal(load_default=None, allow_none=True,
        places=2, validate=validate.Range(min=0, min_inclusive=False))
```

**New route (`loan.py`):**

`POST /accounts/<id>/loan/refinance` (HTMX):

1. Validate input via `RefinanceSchema`.
2. Load current loan params and payment history.
3. Compute current schedule (committed if payments exist, otherwise original).
4. Compute refinance new_principal: `current_real_principal + closing_costs` (or user override).
5. Compute refinance schedule: `amortization_engine.generate_schedule(new_principal, new_rate,
   new_term_months)`.
6. Compute comparison metrics:
   - Current: remaining term, monthly payment, total remaining interest, payoff date.
   - Refinanced: new term, monthly payment, total interest, payoff date.
   - Comparison: monthly payment change (+/-), total interest change (+/-).
   - Break-even: `closing_costs / monthly_savings` months (if closing_costs > 0 and
     monthly_savings > 0).
7. Return `_refinance.html` partial with comparison data.

**Template (`_refinance.html`):**

Form fields: new rate, new term, closing costs, optional principal override.
Results: side-by-side comparison table + chart line (if desired).

**Chart line (optional -- can be deferred to a follow-up commit):** Add a refinance line to
the balance chart alongside existing lines. This is a nice-to-have visualization but the
side-by-side table provides the core comparison.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.10-1 | test_refinance_lower_rate | Mortgage 6.5%, refinance to 5% | POST refinance | Monthly payment decreases, interest saved > 0 | New |
| C-5.10-2 | test_refinance_shorter_term | 30yr to 15yr, same rate | POST refinance | Monthly payment increases, total interest decreases | New |
| C-5.10-3 | test_refinance_with_closing_costs | Refinance with $5000 closing | POST refinance | Break-even point calculated | New |
| C-5.10-4 | test_refinance_validation | Missing required fields | POST refinance | Validation error | New |
| C-5.10-5 | test_refinance_idor | Other user's mortgage | POST refinance | 404 | New |

### F. Manual verification steps

1. Visit a mortgage dashboard.
2. Click the "Refinance" tab.
3. Enter: new rate 5.0%, new term 360 months, closing costs $3,000.
4. Verify: side-by-side comparison shows current vs. refinanced metrics.
5. Verify: break-even point is reasonable (e.g., 18 months).

### G. Downstream effects

None beyond the loan dashboard.

### H. Rollback notes

Route + template + schema change. No migration. Revertable.

---

## Task 5.11: Debt Snowball/Avalanche Strategy

### Overview

A cross-account debt payoff strategy calculator. Three sub-commits:

1. Create the strategy service.
2. Create the route and template.
3. Add multi-line chart.

---

### Commit 5.11-1: Create debt strategy service

### A. Commit message

```text
feat(debt): add snowball/avalanche/custom strategy service
```

### B. Problem statement

A user with multiple debt accounts has no way to evaluate cross-account payoff strategies.

### C. Files modified

- `app/services/debt_strategy_service.py` -- New file.
- `tests/test_services/test_debt_strategy_service.py` -- New file.

### D. Implementation approach

**New service: `debt_strategy_service.py`**

Pure function service (no DB access). Receives account data and parameters, returns results.

**Input dataclass:**

```python
@dataclasses.dataclass(frozen=True)
class DebtAccount:
    account_id: int
    name: str
    current_principal: Decimal
    interest_rate: Decimal
    minimum_payment: Decimal  # Standard monthly P&I
    term_months: int
    remaining_months: int
```

**Main function:**

```python
def calculate_strategy(
    debts: list[DebtAccount],
    extra_monthly: Decimal,
    strategy: str,  # 'avalanche', 'snowball', 'custom'
    custom_order: list[int] | None = None,  # account_ids in priority order
) -> StrategyResult:
```

**Algorithm:**

1. Sort debts by strategy:
   - Avalanche: highest interest rate first.
   - Snowball: smallest balance first.
   - Custom: user-provided order.
2. For each month:
   a. Apply minimum payment to all debts.
   b. Apply extra payment to the target debt (first in priority order).
   c. If the target debt is paid off, add its freed payment (minimum + extra allocated) to the
      extra pool for the next target.
   d. For each debt, compute interest for the month and update the balance.
   e. Record the state.
3. Continue until all debts are paid off or a maximum horizon is reached (e.g., 600 months).
4. Return per-account payoff timeline and aggregate metrics.

**Output dataclass:**

```python
@dataclasses.dataclass(frozen=True)
class StrategyResult:
    per_account: list[AccountPayoff]  # Per-account payoff timeline
    total_interest: Decimal  # Total interest paid across all debts
    debt_free_date: date  # When the last debt reaches $0
    total_months: int

@dataclasses.dataclass(frozen=True)
class AccountPayoff:
    account_id: int
    name: str
    payoff_month: int
    payoff_date: date
    total_interest: Decimal
    balance_timeline: list[Decimal]  # Monthly balance for chart
```

**Edge cases:**

- Single debt account: strategy is trivial -- all extra goes to the one debt.
- Extra monthly = 0: minimum payments only. Payoff follows standard amortization for each debt.
- Debt already paid off (principal = 0): skip it.
- Extra monthly exceeds all minimum payments + remaining balances: all debts paid off in month 1.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.11-1 | test_avalanche_highest_rate_first | 3 debts, different rates | avalanche strategy | Highest rate paid off first | New |
| C-5.11-2 | test_snowball_smallest_balance_first | 3 debts, different balances | snowball strategy | Smallest balance paid off first | New |
| C-5.11-3 | test_avalanche_less_total_interest | Same debts | Compare avalanche vs snowball | Avalanche total_interest <= snowball total_interest | New |
| C-5.11-4 | test_snowball_earlier_first_payoff | Same debts | Compare avalanche vs snowball | Snowball first payoff date <= avalanche first payoff | New |
| C-5.11-5 | test_freed_payment_rolls_to_next | 2 debts, first paid off | Strategy | Extra pool increases after first payoff | New |
| C-5.11-6 | test_single_debt | 1 debt | Strategy | All extra goes to it | New |
| C-5.11-7 | test_zero_extra | 3 debts, extra=0 | Strategy | Standard payoff for each | New |
| C-5.11-8 | test_custom_order | 3 debts, user-specified order | custom strategy | Payments follow user order | New |
| C-5.11-9 | test_already_paid_off_debt_skipped | 1 of 3 debts has principal=0 | Strategy | Skipped, only 2 debts processed | New |

### F. Manual verification steps

Verify via tests (no UI in this commit).

### G. Downstream effects

None. New service, no dependencies on it yet.

### H. Rollback notes

New file. Revertable by deletion.

---

### Commit 5.11-2: Add debt strategy route and template

### A. Commit message

```text
feat(debt): add snowball/avalanche strategy page with comparison table
```

### B. Problem statement

The strategy service needs a user-facing page.

### C. Files modified

- `app/routes/debt_strategy.py` -- New blueprint with route.
- `app/templates/debt_strategy/dashboard.html` -- New template.
- `app/__init__.py` -- Register blueprint.
- `tests/test_routes/test_debt_strategy.py` -- New test file.

### D. Implementation approach

**New blueprint: `debt_strategy.py`**

`GET /debt-strategy` -- Dashboard page:

1. Load all active, non-archived debt accounts (has_amortization=True).
2. For each, load LoanParams and compute current real principal (from confirmed payments).
3. Compute minimum payment via `amortization_engine.calculate_monthly_payment()`.
4. Package into `DebtAccount` instances.
5. Render the form.

`POST /debt-strategy/calculate` (HTMX):

1. Parse: `extra_monthly`, `strategy` (avalanche/snowball/custom), optional `custom_order`.
2. Call `debt_strategy_service.calculate_strategy()` for each strategy.
3. Return comparison partial: avalanche vs. snowball vs. current (no extra).

**Template (`dashboard.html`):**

Form: extra monthly amount input, strategy selector (radio: Avalanche / Snowball / Custom).
For custom: sortable list of debt accounts (drag-and-drop or numbered inputs).

Results: comparison table:

| Metric | No Extra | Avalanche | Snowball |
|--------|----------|-----------|----------|
| Debt-free date | ... | ... | ... |
| Total interest | ... | ... | ... |
| Months saved | ... | ... | ... |

Per-account payoff timeline table showing when each debt reaches $0 under the selected strategy.

**Navigation:** Link to this page from the accounts dashboard (liability section) or from
each loan dashboard.

**Blueprint registration (`__init__.py`):**

```python
from app.routes.debt_strategy import debt_strategy_bp
app.register_blueprint(debt_strategy_bp)
```

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.11-10 | test_strategy_page_renders | Multiple loan accounts | GET /debt-strategy | Page renders with account list | New |
| C-5.11-11 | test_strategy_calculate | 2 loans, extra=$200, avalanche | POST calculate | Results show payoff timeline | New |
| C-5.11-12 | test_strategy_no_debts | No loan accounts | GET /debt-strategy | "No debt accounts" message | New |
| C-5.11-13 | test_strategy_idor | Other user's debts | POST calculate | 404 | New |

### F. Manual verification steps

1. With 2+ loan accounts, navigate to the Debt Strategy page.
2. Enter $200 extra monthly.
3. Click Calculate.
4. Verify the comparison table shows avalanche, snowball, and no-extra scenarios.
5. Verify per-account payoff dates are reasonable.

### G. Downstream effects

None beyond the new page.

### H. Rollback notes

New files. Revertable by deletion and unregistering the blueprint.

---

### Commit 5.11-3: Add multi-line chart to debt strategy page

### A. Commit message

```text
feat(debt): add multi-line balance chart to debt strategy visualization
```

### B. Problem statement

The strategy comparison is more powerful with a chart showing all debt balances converging to
zero over time under the selected strategy.

### C. Files modified

- `app/templates/debt_strategy/dashboard.html` -- Add Chart.js canvas and data attributes.
- `app/routes/debt_strategy.py` -- Include chart data in response.

### D. Implementation approach

The `StrategyResult` already contains `balance_timeline` per account. Convert to Chart.js
datasets (one line per account) and pass to the template via `data-*` attributes (CSP-compliant
pattern used by existing charts).

Each account line: distinct color, labeled with account name. X-axis: months. Y-axis: balance.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.11-14 | test_chart_data_included | 2 loans, calculate | POST calculate | Response includes chart datasets | New |

### F. Manual verification steps

View the strategy page with results and verify the chart renders with one line per debt.

### G. Downstream effects

None.

### H. Rollback notes

Template change. Revertable.

---

## Task 5.16: Recurring Obligation Summary Page

### Commit 5.16-1: Add recurring obligation summary page

### A. Commit message

```text
feat(obligations): add recurring obligation summary page
```

### B. Problem statement

The user's committed recurring outflows are spread across multiple views. There is no single
view showing all recurring financial obligations.

### C. Files modified

- `app/routes/obligations.py` -- New blueprint with route.
- `app/templates/obligations/summary.html` -- New template.
- `app/__init__.py` -- Register blueprint.
- `tests/test_routes/test_obligations.py` -- New test file.

### D. Implementation approach

**New blueprint: `obligations.py`**

`GET /obligations` -- Summary page:

1. Load active transaction templates with recurrence rules:

   ```python
   expense_templates = (
       TransactionTemplate.query
       .filter_by(user_id=current_user.id, is_active=True)
       .filter(TransactionTemplate.recurrence_rule_id.isnot(None))
       .join(TransactionTemplate.transaction_type)
       .all()
   )
   ```

2. Load active transfer templates with recurrence rules:

   ```python
   transfer_templates = (
       TransferTemplate.query
       .filter_by(user_id=current_user.id, is_active=True)
       .filter(TransferTemplate.recurrence_rule_id.isnot(None))
       .all()
   )
   ```

3. Group by type:
   - **Recurring expenses** (bills): transaction templates with type=EXPENSE.
   - **Recurring transfers out** (debt payments, savings): transfer templates.
   - **Recurring income** (paycheck): transaction templates with type=INCOME.

4. For each template, compute:
   - Monthly equivalent amount using `savings_goal_service.compute_committed_monthly()`
     conversion factors (already implemented for the pattern-to-monthly normalization).
   - Next occurrence date from the recurrence rule.

5. Compute summary metrics:
   - Total monthly committed outflows = sum of expense + transfer monthly equivalents.
   - Total monthly committed income.
   - Net monthly committed cash flow = income - outflows.

**Template (`summary.html`):**

Three grouped sections with tables showing: Name, Amount, Frequency, Monthly Equivalent,
Next Occurrence, Linked Account (if applicable).

Summary bar at top: Total Committed Monthly Outflows, Total Committed Monthly Income, Net.

**Next occurrence date:** Use `recurrence_engine._match_periods()` to find the next matching
period for each template's rule. This function is already public enough to be imported. If not,
compute it from the rule's pattern and the current date:

- EVERY_PERIOD: next period start_date.
- MONTHLY: next occurrence of day_of_month.
- ANNUAL: next occurrence of month/day.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5.16-1 | test_obligations_page_renders | 2 expense templates, 1 transfer template, 1 income template | GET /obligations | Page shows 3 groups | New |
| C-5.16-2 | test_monthly_totals_correct | Known templates with different frequencies | GET /obligations | Monthly equivalents correct | New |
| C-5.16-3 | test_net_cash_flow | Income - outflows | GET /obligations | Net = expected | New |
| C-5.16-4 | test_no_templates | No active templates | GET /obligations | "No recurring obligations" message | New |
| C-5.16-5 | test_obligations_idor | Other user's templates | GET /obligations | Only current user's templates shown | New |

### F. Manual verification steps

1. With recurring transaction templates and transfer templates configured, navigate to
   /obligations.
2. Verify all recurring items are listed in the correct groups.
3. Verify monthly equivalents are reasonable.
4. Verify the summary totals are correct.

### G. Downstream effects

None. Read-only aggregation view.

### H. Rollback notes

New files. Revertable by deletion and unregistering the blueprint.

---

## Risks and Unknowns

### R-1: Payment-date-to-schedule-month matching

The amortization engine generates a month-by-month schedule. Shadow transaction dates come from
pay period start dates (biweekly). Matching a biweekly payment date to a monthly schedule period
requires year-month matching, not exact date matching. This is straightforward but must be
verified: a payment on 2026-03-06 (pay period start) matches schedule month 2026-03 (payment
due on the 1st or whatever payment_day is). If a pay period spans two calendar months, the
payment date (period start) determines the month.

**Mitigation:** Test explicitly with payments that fall on different days of the month and
verify correct matching.

### R-2: Current real principal vs. stored current_principal

After 5.1, the "real" current principal is derived by replaying confirmed payments through the
engine. This may disagree with `LoanParams.current_principal` (which the user entered or last
updated manually). The plan treats the engine's replay as authoritative for projections but
keeps the stored field as a manual override. If this dual-source causes confusion, a future
enhancement could display both values and let the user reconcile.

**Mitigation:** Display a note on the dashboard when the engine-derived principal differs from
the stored value by more than $0.01.

### R-3: Performance of payment-aware projections

Computing the schedule with payment data is more expensive than the static schedule because it
involves replaying N payments. For a 30-year mortgage with 360 monthly payments, this is still
trivial (<1ms). For the debt strategy calculator with 3-5 accounts, it is still fast. No
performance concern for the expected data volumes.

### R-4: Transfer recurrence end_date update on dashboard load

Commit 5.9-1 updates the recurrence rule end_date every time the dashboard is loaded. This is
a write operation on a GET request. While this is pragmatic (the dashboard is the natural place
to detect payoff date changes), it violates REST semantics. An alternative is to update the
end_date only when payments are created/modified (via a hook in the transfer service), but this
adds complexity and couples the transfer service to the amortization engine.

**Recommendation:** Accept the pragmatic approach for now. The write is idempotent (same payoff
date = no change), lightweight (single column update), and the dashboard is the only place
where the payoff date is computed with full payment data.

### R-5: Snowball/avalanche with ARM loans

The debt strategy service uses a fixed interest rate per debt. For ARM loans, the rate may
change during the payoff period. The initial implementation uses the current rate. A future
enhancement could accept rate_changes per debt, but this adds significant complexity to the
iterative algorithm.

**Recommendation:** Document this limitation. Use the current rate and note in the UI that ARM
rate adjustments are not incorporated into the strategy projection.

---

## Opportunistic Improvements

These are small improvements noticed while reading the code that are not in the roadmap but
would be trivial to add. They should NOT be folded into the Section 5 commits without developer
approval.

### O-1: chart_data_service._get_payment_data helper could be shared

Both the loan route (5.1-2) and chart_data_service (5.5-1) need to query shadow income
transactions for a debt account. Extract the query into a shared helper in the chart_data_service
or a new `loan_data_service.py` to avoid duplication. Estimated effort: 30 minutes.

### O-2: Add payment_day validation to prevent day 29-31 issues

The `payment_day` field accepts 1-31, but months with fewer than 31 days cause the engine to
clamp to the last day of the month (existing behavior). A UI tooltip on the payment_day field
noting this behavior would improve UX. Estimated effort: 5 minutes (template-only).

### O-3: Escrow inflation display on payment breakdown

The payment breakdown (5.14) shows the current escrow split. If escrow components have inflation
rates, the breakdown could show the projected change: "Escrow will increase to $X next year."
Estimated effort: 20 minutes (extend the breakdown card).

---

## Test Strategy Summary

### Prerequisite regression suite (Commit #0)

5 tests that lock down existing behavior before any changes. Must pass after every subsequent
commit.

### Per-commit test cases

Total new test cases across all commits: approximately 85-90.

### Integration tests

After 5.1 is complete, add an integration test verifying that:

1. Creating a transfer to a debt account creates shadow transactions.
2. The loan dashboard reads those shadow transactions as payment data.
3. The amortization engine produces a schedule incorporating those payments.
4. The balance calculator produces correct balances for both the checking and debt accounts.
5. All five transfer invariants hold throughout the process.

### Full suite gate

The full test suite (currently ~2300 tests, expected to grow to ~2450) must pass after every
commit. Use `timeout 720 pytest -v --tb=short` as the final gate before reporting done.

### Expected test count increase per phase

| Phase | Commits | New tests (approx) |
|-------|---------|-------------------|
| Regression | 1 | 5 |
| 5.1 (3 commits) | 3 | 21 |
| 5.2 (3 commits) | 3 | 22 |
| 5.3 | 1 | 7 |
| 5.7 | 1 | 8 |
| 5.8 | 1 | 7 |
| 5.5 | 1 | 6 |
| 5.14 | 1 | 3 |
| 5.13 | 1 | 4 |
| 5.9 (3 commits) | 3 | 13 |
| 5.4 (4 commits) | 4 | 19 |
| 5.15 | 1 | 6 |
| 5.12 | 1 | 7 |
| 5.10 | 1 | 5 |
| 5.11 (3 commits) | 3 | 14 |
| 5.16 | 1 | 5 |
| **Total** | **27** | **~152** |

---

## Commit Checklist

This is the complete list of commits in dependency order. Each commit must be implemented,
tested, and verified before moving to the next. Run the full test suite before marking any
commit as done.

| # | Commit ID | Commit Message | Summary |
|---|-----------|---------------|---------|
| 1 | #0 | test(section5): add regression baseline for loan dashboard and savings goals | Lock down existing amortization engine, loan dashboard, savings goal, and balance calculator behavior |
| 2 | 5.1-1 | feat(amortization): extend engine to accept payment history for projection scenarios | Add PaymentRecord dataclass and payments parameter to generate_schedule(), calculate_summary(), get_loan_projection() |
| 3 | 5.1-2 | feat(loan): pass payment history from shadow transactions to amortization engine | Query shadow income transactions on debt accounts, pass as PaymentRecord list to amortization engine |
| 4 | 5.1-3 | feat(loan): prompt recurring transfer creation after loan parameter save | Add prompt on loan dashboard to create recurring monthly transfer when none exists |
| 5 | 5.2-1 | feat(growth): extend engine to accept contribution timeline for projection scenarios | Add ContributionRecord dataclass and contributions parameter to project_balance() |
| 6 | 5.2-2 | feat(investment): pass contribution timeline from transactions and deductions to growth engine | Build per-period contribution timeline from shadow transactions and paycheck deductions |
| 7 | 5.2-3 | feat(investment): prompt contribution setup after investment parameter save | Add prompt for recurring transfer (IRA/brokerage) or deduction linkage (401(k)) |
| 8 | 5.3-1 | feat(investment): add what-if contribution calculator with dual-projection chart | Add what-if input, compute dual projections, overlay on growth chart with comparison card |
| 9 | 5.7-1 | feat(amortization): incorporate ARM rate change history into schedule projections | Add RateChangeRecord and rate_changes parameter; re-amortize at each rate adjustment |
| 10 | 5.8-1 | fix(amortization): handle overpayment and zero-balance termination edge cases | Cap principal at remaining balance, stop schedule at payoff |
| 11 | 5.5-1 | feat(loan): add multi-scenario visualization to payoff calculator and balance chart | Add original/committed/what-if chart lines and floor marker to loan dashboard |
| 12 | 5.14-1 | feat(loan): add payment allocation breakdown showing principal/interest/escrow split | Display current period principal/interest/escrow breakdown on loan dashboard |
| 13 | 5.13-1 | feat(loan): add full month-by-month amortization schedule view | Add collapsible schedule table with confirmed/projected row distinction |
| 14 | 5.9-1 | feat(loan): auto-set recurring transfer end date to projected payoff date | Update recurrence rule end_date on dashboard load when payoff date changes |
| 15 | 5.9-2 | feat(accounts): display paid-off badge on debt accounts with zero principal | Show "Paid Off" badge when confirmed payments reduce principal to zero |
| 16 | 5.9-3 | feat(accounts): add account archival for paid-off loans and inactive accounts | Add is_archived column, archive/unarchive routes, filter from active views |
| 17 | 5.4-1 | feat(savings): add ref tables for goal modes and income units | Create ref.goal_modes and ref.income_units tables with enums and ref_cache |
| 18 | 5.4-2 | feat(savings): add goal_mode_id, income_unit_id, income_multiplier to savings_goals | Add income-relative columns with FK constraints and cross-field validation |
| 19 | 5.4-3 | feat(savings): resolve income-relative goal targets from paycheck calculator | Add resolve_goal_target() function and integrate into savings dashboard service |
| 20 | 5.4-4 | feat(savings): add income-relative mode toggle to savings goal form | Add mode selector, conditional fields, and resolved target display |
| 21 | 5.15-1 | feat(savings): add goal completion trajectory and pace indicator | Compute months-to-goal, projected completion date, and on-track/behind/ahead pace |
| 22 | 5.12-1 | feat(accounts): add debt summary metrics and debt-to-income ratio | Add aggregate debt card (total debt, monthly payments, weighted rate) and DTI ratio |
| 23 | 5.10-1 | feat(loan): add refinance what-if calculator with side-by-side comparison | Add refinance tab with form, comparison table, and break-even calculation |
| 24 | 5.11-1 | feat(debt): add snowball/avalanche/custom strategy service | Create pure-function debt_strategy_service with iterative payoff algorithm |
| 25 | 5.11-2 | feat(debt): add snowball/avalanche strategy page with comparison table | Create debt_strategy blueprint, route, and template with strategy comparison |
| 26 | 5.11-3 | feat(debt): add multi-line balance chart to debt strategy visualization | Add Chart.js per-account balance lines to debt strategy page |
| 27 | 5.16-1 | feat(obligations): add recurring obligation summary page | Create obligations blueprint with grouped listing and monthly totals |

**Totals:** 26 implementation commits + 1 regression baseline = **27 commits**. Approximately
**~152 new test cases** across all tasks.
