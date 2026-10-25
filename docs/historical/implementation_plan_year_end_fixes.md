> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Year-End Debt Progress and Savings Progress Fixes

**Version:** 1.0
**Date:** April 9, 2026
**Prerequisite:** Section 8 complete (Commits 1-17 implemented).
**Scope:** Fix two calculation bugs in the year-end summary service: (1) debt progress uses
a naive balance calculator instead of the amortization engine, and (2) savings progress uses
a naive formula instead of the growth engine.

---

## Root Cause Analysis

### Bug 1: Debt Progress balances are inaccurate

**Symptom:** The Debt Progress card on the Year-End tab shows balances that do not match the
amortization schedule displayed on individual loan account pages.

**Root cause:** `_compute_debt_progress()` (`year_end_summary_service.py:615-670`) calls
`_get_account_balance_map()` which dispatches to
`balance_calculator.calculate_balances_with_amortization()` (`balance_calculator.py:176-289`).
This function does NOT use the amortization engine. Instead, it:

1. Starts from the anchor balance and walks forward by pay period.
2. Detects shadow income transactions (transfer payments into the loan account).
3. Performs a **naive interest/principal split** per biweekly period:
   `interest = running_principal * monthly_rate` (line 274).
4. Subtracts only the principal portion from the running balance.

This is wrong for three reasons:

- **Biweekly periods are treated as monthly.** A single `monthly_rate` is applied per pay
  period, but pay periods are biweekly (14 days), not monthly (~30 days). This over-charges
  interest by roughly 2x.
- **No payment history replay.** The function starts from the anchor balance and only sees
  shadow income transactions. It does not replay the loan from origination through the actual
  amortization schedule with confirmed payment history.
- **No escrow subtraction or biweekly redistribution.** Raw shadow transaction amounts include
  escrow components (property tax, insurance), inflating apparent principal payments. Biweekly
  pay periods can also place two payments in the same calendar month, causing the amortization
  engine to sum them incorrectly if not redistributed first.

**Correct approach (used by loan dashboard):** The loan dashboard route
(`loan.py:566-568`) calls `amortization_engine.get_loan_projection()` which generates the
full month-by-month amortization schedule from origination, properly handling payment history,
escrow subtraction, biweekly redistribution, and ARM rate changes.

**Additional finding:** The existing `_compute_mortgage_interest()` function
(`year_end_summary_service.py:342-396`) also calls `amortization_engine.generate_schedule()`
but passes raw payment history directly without escrow subtraction or biweekly redistribution.
This means mortgage interest totals are also slightly inaccurate. The fix for debt progress
should also correct this pre-existing issue, since both functions need the same prepared
schedule.

### Bug 2: Savings Progress balances ignore employer contributions and investment growth

**Symptom:** The Savings Progress card shows balances computed as
`anchor_balance + user_contributions`, missing employer match and assumed annual return.

**Root cause:** `_compute_savings_progress()` (`year_end_summary_service.py:676-738`) uses
a completely naive formula:

```python
jan1_bal = account.current_anchor_balance or ZERO
dec31_bal = jan1_bal + contributions
```

Where `contributions` is the sum of shadow income transactions (user transfers in). This
bypasses ALL existing calculation engines:

- **No growth engine.** The `growth_engine.project_balance()` function
  (`growth_engine.py:164-294`) computes period-by-period balance growth including assumed
  annual return and employer contributions. It is never called.
- **No employer contributions.** InvestmentParams (`investment_params.py:11-58`) stores
  `employer_contribution_type` (none/flat_percentage/match),
  `employer_flat_percentage`, `employer_match_percentage`, and
  `employer_match_cap_percentage`. None of these are loaded or used.
- **No investment return.** InvestmentParams stores `assumed_annual_return` (default 7%).
  This is never applied.
- **No interest calculation.** For HYSA-type accounts with InterestParams, the balance
  calculator's `calculate_balances_with_interest()` function handles interest accrual.
  The savings progress section bypasses this entirely.
- **No balance calculator at all.** Even the basic `calculate_balances()` function (which
  properly handles anchor + transactions with status filtering) is not called. The naive
  formula uses the raw `current_anchor_balance` which is the balance at the anchor
  *period*, not at Jan 1 of the target year.

**Correct approach (used by savings dashboard):** The savings dashboard
(`savings_dashboard_service.py:355-359`) calls `_project_investment()` which:
1. Loads InvestmentParams and paycheck deductions for the account.
2. Calls `calculate_investment_inputs()` (`investment_projection.py:62-153`) to assemble
   periodic contribution, employer params, and annual limits.
3. Calls `growth_engine.project_balance()` with all inputs.
4. Returns projected balances including growth and employer contributions.

For non-investment accounts, the savings dashboard uses `calculate_balances()` or
`calculate_balances_with_interest()` through the standard balance calculator path.

---

## Downstream Effects of Both Fixes

### Net Worth section (partial fix, partial known issue)

The net worth section (`_compute_net_worth()`) calls `_get_account_balance_map()` for all
accounts. Since that function uses `calculate_balances_with_amortization()` for debt accounts,
**net worth is also wrong for debt accounts**. Commit 1 fixes `_get_account_balance_map()` for
debt accounts, which fixes net worth as a side effect.

For investment accounts, `_get_account_balance_map()` falls through to the plain
`calculate_balances()` -- no growth engine, no employer contributions, no assumed return.
**Net worth for investment accounts is therefore understated.** This is a pre-existing issue
that predates both bugs being fixed here. It is noted in Section 6 (Known Related Issues) but
is NOT fixed in this plan because:
- The growth engine requires additional inputs (deductions, salary, contribution history)
  that `_get_account_balance_map()` does not currently accept.
- Changing net worth for investment accounts would alter the net worth chart values and
  requires its own careful validation.
- The user did not report net worth as incorrect.

### CSV Export

The CSV export service (`csv_export_service.py`) writes debt_progress and savings_progress
dicts directly. Any new fields added to the service output (e.g., `employer_contributions`,
`investment_growth`) must be added to the CSV column headers and row writers.

### Template

The year-end template (`analytics/_year_end.html`) renders debt_progress and savings_progress
fields directly. Debt progress fields remain unchanged (account_name, jan1_balance,
dec31_balance, principal_paid) so no template change is needed for Commit 1. Savings progress
gains new fields in Commit 2, requiring template and CSV updates.

---

## Commit Sequence

| # | Commit Message | Summary |
|---|----------------|---------|
| 1 | `fix(year-end): use amortization engine for debt progress and mortgage interest` | Extract payment preparation to shared service; generate schedules once; fix debt progress, mortgage interest, and net worth for debt accounts |
| 2 | `fix(year-end): use growth engine and balance calculator for savings progress` | Load investment/interest params; use growth engine for investment accounts, balance calculator for others; add employer and growth fields |

---

## Commit 1: Fix Debt Progress with Amortization Engine

### A. Commit message

```text
fix(year-end): use amortization engine for debt progress and mortgage interest
```

### B. Problem statement

The year-end summary computes debt account balances using
`balance_calculator.calculate_balances_with_amortization()`, which performs a naive
interest/principal split per biweekly pay period rather than using the actual amortization
schedule. The mortgage interest calculation also passes raw payment history to the
amortization engine without subtracting escrow or redistributing biweekly overlaps. Both
produce inaccurate results compared to the loan dashboard, which uses the full amortization
engine with prepared payment history.

The fix generates amortization schedules once per debt account (with properly prepared
payments) and uses them for debt progress, mortgage interest, and net worth. This requires
extracting the payment preparation logic from `loan.py` into a shared service so that the
year-end service can reuse it without violating the "services don't import from routes"
boundary.

### C. Files modified

| File | Change | Reason |
|------|--------|--------|
| `app/services/loan_payment_service.py` | Add `prepare_payments_for_engine()` and `compute_contractual_pi()` | Extract from loan route for reuse by year-end service |
| `app/routes/loan.py` | Replace `_prepare_payments_for_engine()` and `_compute_contractual_pi()` with calls to shared service | Eliminate duplication |
| `app/services/year_end_summary_service.py` | Major refactor of debt-related sections | Use amortization engine with prepared payments |
| `tests/test_services/test_year_end_summary_service.py` | Update and add debt progress tests | Validate amortization-based calculations |
| `tests/test_services/test_loan_payment_service.py` | Add tests for extracted functions | Validate payment preparation logic |

### D. Implementation approach

**Step 1: Extract payment preparation to shared service**

Move `_prepare_payments_for_engine()` and `_compute_contractual_pi()` from
`app/routes/loan.py` (lines 324-437) into `app/services/loan_payment_service.py` as public
functions. The loan_payment_service already contains `get_payment_history()` and is the
natural home for payment preparation logic.

New functions in `app/services/loan_payment_service.py`:

```python
def compute_contractual_pi(params) -> Decimal:
    """Compute the standard monthly P&I payment from loan params.

    For ARM loans, the payment is re-amortized from current balance
    at the current rate.  For fixed-rate loans, uses original terms.

    Args:
        params: LoanParams model instance.

    Returns:
        Decimal monthly P&I payment.
    """

def prepare_payments_for_engine(
    payments: list,
    payment_day: int,
    monthly_escrow: Decimal,
    contractual_pi: Decimal,
) -> list:
    """Prepare payment records for the amortization engine.

    Corrects two mismatches between biweekly shadow transactions and
    the monthly amortization schedule:
    1. Escrow subtraction: removes escrow from amounts that exceed
       the standard P&I payment.
    2. Biweekly redistribution: shifts same-month payments to
       subsequent months to restore one-payment-per-month alignment.

    Args:
        payments: List of PaymentRecord from get_payment_history().
        payment_day: Mortgage payment day of month (from LoanParams).
        monthly_escrow: Monthly escrow amount from escrow_calculator.
        contractual_pi: Standard monthly P&I payment (no escrow).

    Returns:
        Corrected list of PaymentRecord.
    """
```

The implementations are identical to the existing private functions in `loan.py`. After
extraction, `loan.py` imports and calls the shared functions instead.

**Step 2: Update `loan.py` to use shared functions**

Replace `_compute_contractual_pi(params)` calls with
`loan_payment_service.compute_contractual_pi(params)`.

Replace `_prepare_payments_for_engine(raw_payments, ...)` calls with
`loan_payment_service.prepare_payments_for_engine(raw_payments, ...)`.

Delete the private implementations from `loan.py`. Keep the `_load_loan_context()` function
which orchestrates loading and preparation.

**Step 3: Add `_generate_debt_schedules()` to year-end service**

New helper in `year_end_summary_service.py`:

```python
def _generate_debt_schedules(
    debt_accounts: list,
    scenario_id: int,
) -> dict[int, list]:
    """Generate amortization schedules for all debt accounts.

    Loads loan params, payment history, escrow components, and rate
    changes for each debt account, then generates the full
    amortization schedule using properly prepared payments.

    Schedules are generated once and shared across mortgage interest,
    debt progress, and net worth calculations.

    Args:
        debt_accounts: Accounts with has_amortization=True.
        scenario_id: Baseline scenario ID for payment history.

    Returns:
        dict mapping account_id to list[AmortizationRow].
    """
```

Implementation:
1. For each debt account, load LoanParams.
2. Load payment history via `get_payment_history(account_id, scenario_id)`.
3. Load escrow components for the account.
4. Compute contractual P&I via `compute_contractual_pi(params)`.
5. Compute monthly escrow via `escrow_calculator.calculate_monthly_escrow(components)`.
6. Prepare payments via `prepare_payments_for_engine(...)`.
7. Load rate changes if the loan is ARM (`params.is_arm`).
8. Generate schedule via `amortization_engine.generate_schedule(...)` with prepared
   payments, rate changes, and proper original_principal handling (None for ARM).
9. Store schedule in dict keyed by account_id.

Required new imports in `year_end_summary_service.py`:
- `from app.models.loan_features import RateHistory, EscrowComponent`
- `from app.services.loan_payment_service import prepare_payments_for_engine, compute_contractual_pi`
- `from app.services import escrow_calculator`
- `from app.services.amortization_engine import RateChangeRecord`

**Step 4: Add `_balance_from_schedule_at_date()` helper**

```python
def _balance_from_schedule_at_date(
    schedule: list,
    target: date,
    original_principal: Decimal,
) -> Decimal:
    """Return the loan balance at a given date from an amortization schedule.

    Finds the last schedule row whose payment_date is on or before
    the target date and returns its remaining_balance.  If the target
    is before the first payment, returns the original principal.

    Args:
        schedule: List of AmortizationRow from generate_schedule().
        target: The date to look up the balance for.
        original_principal: The loan's original principal (balance
            before any payments).

    Returns:
        Decimal remaining balance at the target date.
    """
```

**Step 5: Refactor `_compute_mortgage_interest()` to use pre-generated schedules**

Current signature:
```python
def _compute_mortgage_interest(year, loan_accounts, scenario_id)
```

New signature:
```python
def _compute_mortgage_interest(year: int, debt_schedules: dict[int, list]) -> Decimal
```

The function no longer generates its own schedules. It iterates over the pre-generated
schedules and sums interest for rows with `payment_date.year == year`. This is simpler
and now uses correctly prepared payments (with escrow subtraction and biweekly
redistribution).

**Step 6: Refactor `_compute_debt_progress()` to use pre-generated schedules**

Current signature:
```python
def _compute_debt_progress(year, debt_accounts, all_periods, scenario)
```

New signature:
```python
def _compute_debt_progress(
    year: int,
    debt_accounts: list,
    debt_schedules: dict[int, list],
) -> list[dict]
```

Implementation:
1. For each debt account, get the schedule from `debt_schedules`.
2. Load `LoanParams` for `original_principal`.
3. Call `_balance_from_schedule_at_date(schedule, date(year, 1, 1), original_principal)`
   for jan1_balance.
4. Call `_balance_from_schedule_at_date(schedule, date(year, 12, 31), original_principal)`
   for dec31_balance.
5. `principal_paid = jan1_balance - dec31_balance`.
6. Return list of dicts with same structure as before.

**Step 7: Add `_schedule_to_period_balance_map()` helper**

```python
def _schedule_to_period_balance_map(
    schedule: list,
    periods: list,
    original_principal: Decimal,
) -> dict:
    """Map amortization schedule balances to pay period IDs.

    For each pay period, finds the last schedule row whose
    payment_date is on or before the period's end_date.  Returns the
    remaining_balance from that row.  Periods before the first payment
    use original_principal.

    Args:
        schedule: List of AmortizationRow.
        periods: List of PayPeriod objects sorted by period_index.
        original_principal: Balance before any payments.

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
```

**Step 8: Update `_get_account_balance_map()` for debt accounts**

Current implementation for amortizing accounts (lines 998-1012):
```python
if acct_type and acct_type.has_amortization:
    loan_params = ...
    if loan_params:
        balances, _ = balance_calculator.calculate_balances_with_amortization(...)
        return balances
```

New implementation:
```python
if acct_type and acct_type.has_amortization:
    if debt_schedules and account.id in debt_schedules:
        loan_params = ...
        original = loan_params.original_principal if loan_params else anchor_balance
        return _schedule_to_period_balance_map(
            debt_schedules[account.id], periods, original,
        )
    # Fallback: no schedule available (should not happen in normal flow).
    balances, _ = balance_calculator.calculate_balances_with_amortization(...)
    return balances
```

Add `debt_schedules: dict | None = None` as an optional parameter to
`_get_account_balance_map()`. The parameter is optional so existing callers continue to
work. `_build_account_data()` passes it through from `_build_summary()`.

**Step 9: Thread schedules through `_build_summary()`**

```python
def _build_summary(user_id, year, scenario, ctx):
    debt_schedules = _generate_debt_schedules(
        ctx["debt_accounts"], scenario.id,
    )

    income_tax = _compute_income_tax(...)
    mortgage_interest = _compute_mortgage_interest(year, debt_schedules)
    income_tax["mortgage_interest_total"] = mortgage_interest

    return {
        "income_tax": income_tax,
        ...
        "net_worth": _compute_net_worth(
            year, ctx["accounts"], ctx["all_periods"], scenario,
            debt_schedules=debt_schedules,
        ),
        "debt_progress": _compute_debt_progress(
            year, ctx["debt_accounts"], debt_schedules,
        ),
        ...
    }
```

Update `_compute_net_worth()` to accept and pass `debt_schedules` to
`_build_account_data()`, which passes it to `_get_account_balance_map()`.

### E. Test cases

**Tests for extracted payment preparation (`test_loan_payment_service.py`):**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C1-1 | test_compute_contractual_pi_fixed | LoanParams: $240k, 6.5%, 360mo | `compute_contractual_pi(params)` | $1,517.09 (standard amortization formula) | New |
| C1-2 | test_compute_contractual_pi_arm | LoanParams: $230k, 7%, 330mo remaining, is_arm=True | `compute_contractual_pi(params)` | Re-amortized payment from current principal | New |
| C1-3 | test_prepare_payments_escrow_subtraction | 3 payments at $1,800, escrow=$283, contractual_pi=$1,517 | `prepare_payments_for_engine(...)` | Each payment reduced by $283 to $1,517 | New |
| C1-4 | test_prepare_payments_below_pi_not_adjusted | Payment at $1,500 (below contractual $1,517) | `prepare_payments_for_engine(...)` | Amount unchanged at $1,500 | New |
| C1-5 | test_prepare_payments_biweekly_redistribution | Two payments in same month | `prepare_payments_for_engine(...)` | Second payment shifted to next month | New |

**Updated and new tests for debt progress (`test_year_end_summary_service.py`):**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C1-6 | test_debt_progress_uses_amortization | Mortgage $240k @ 6.5% 30yr originated 2025-01-01, no payments | `compute_year_end_summary(uid, 2026)` | jan1_balance matches schedule row at Dec 2025, dec31_balance matches schedule row at Dec 2026, principal_paid = difference. Hand-computed: ~$237,280 and ~$234,416, principal ~$2,864 | Mod (existing test_debt_progress updated with value assertions) |
| C1-7 | test_debt_progress_with_payments | Mortgage + 6 transfer payments ($1,600 each) to loan account | `compute_year_end_summary(uid, 2026)` | Balances reflect actual payments in amortization schedule. Extra principal accelerates paydown. dec31 < schedule-only dec31 | New |
| C1-8 | test_debt_progress_escrow_excluded | Mortgage + payments that include escrow component | `compute_year_end_summary(uid, 2026)` | Escrow subtracted from payments before amortization. principal_paid reflects P&I only, not escrow | New |
| C1-9 | test_debt_no_accounts | No debt accounts | `compute_year_end_summary(uid, 2026)` | `debt_progress == []` | Existing (unchanged) |
| C1-10 | test_mortgage_interest_with_prepared_payments | Mortgage + escrow + payments | `compute_year_end_summary(uid, 2026)` | Interest total from prepared schedule matches independent calculation | New |

**Net worth affected by fix (verify no regression):**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C1-11 | test_net_worth_debt_uses_amortization | Mortgage $240k, originated 2025-01-01 | `compute_year_end_summary(uid, 2026)` | Monthly net worth values reflect amortization-based debt balances, not anchor-only | New |

### F. Manual verification steps

1. Navigate to `/analytics`, click "Year-End Summary" tab.
2. Verify Debt Progress section: Jan 1 and Dec 31 balances should match the loan
   dashboard's amortization table for the same dates.
3. Verify principal paid = Jan 1 balance - Dec 31 balance.
4. If you have escrow components on the mortgage, verify the principal paid value is
   reasonable (not inflated by escrow amounts).
5. Check the Mortgage Interest line in the Income & Tax section -- it should match the sum
   of interest column values from the loan dashboard's amortization table for the year.
6. Compare the Net Worth chart's monthly values against a manual calculation: checking
   balance + savings - mortgage balance (using amortization schedule balances).

### G. Downstream effects

- **CSV Export:** Debt progress CSV uses the same dict fields (account_name, jan1_balance,
  dec31_balance, principal_paid). No changes needed. Values will be more accurate.
- **Template:** Debt progress template uses the same fields. No changes needed.
- **Loan route:** `_prepare_payments_for_engine()` and `_compute_contractual_pi()` are moved
  to the shared service. The loan route imports them instead of defining them locally. All
  existing loan route tests must continue to pass.
- **Net worth:** Debt account balances in the net worth section will use amortization-based
  values instead of the naive balance calculator. This is a side-effect fix.

### H. Rollback notes

If the extracted functions cause issues in the loan route, the private implementations can be
restored to `loan.py`. The year-end service changes are self-contained in one file. No
migration, no data impact.

---

## Commit 2: Fix Savings Progress with Growth Engine and Balance Calculator

### A. Commit message

```text
fix(year-end): use growth engine and balance calculator for savings progress
```

### B. Problem statement

The year-end summary computes savings progress balances using a naive formula
(`anchor_balance + user_contributions`) that ignores employer contributions, assumed annual
return, and interest accrual. Investment accounts with InvestmentParams and interest-bearing
accounts with InterestParams both get incorrect balances. The fix uses the growth engine for
investment accounts, the balance calculator with interest for HYSA-type accounts, and the
standard balance calculator for plain savings accounts -- matching the established patterns
in the savings dashboard.

Additionally, the savings progress section currently reports only user contributions
(`total_contributions`). After fixing the balances, the difference between jan1 and dec31
will include employer contributions and growth that are not explained by the contributions
column. Two new fields (`employer_contributions` and `investment_growth`) are added so the
year-end report provides a complete picture of where balance changes came from.

### C. Files modified

| File | Change | Reason |
|------|--------|--------|
| `app/services/year_end_summary_service.py` | Rewrite `_compute_savings_progress()`, add investment/interest param loading | Use growth engine and balance calculator |
| `app/templates/analytics/_year_end.html` | Add employer and growth columns to savings table | Display new fields |
| `app/services/csv_export_service.py` | Add employer and growth columns to savings CSV section | Include new fields in export |
| `tests/test_services/test_year_end_summary_service.py` | Add investment and interest account tests | Validate growth engine and interest calculations |
| `tests/test_routes/test_analytics.py` | Update savings progress template assertions | Verify new columns rendered |
| `tests/test_services/test_csv_export_service.py` | Update `_build_year_end_data()` and assertions | Validate new CSV columns |

### D. Implementation approach

**Step 1: Load investment and interest params in `_load_common_data()`**

Add to the returned dict:

```python
{
    ...
    "investment_params_map": _load_investment_params(savings_accounts),
    "interest_params_map": _load_interest_params(savings_accounts),
    "deductions_by_account": _load_deductions_by_account(
        savings_accounts, user_id,
    ),
    "salary_gross_biweekly": _load_salary_gross_biweekly(
        user_id, scenario,
    ),
}
```

New helper functions:

```python
def _load_investment_params(accounts: list) -> dict[int, InvestmentParams]:
    """Batch-load InvestmentParams for accounts that need them.

    Queries accounts whose account_type has has_parameters=True and
    does not have has_interest or has_amortization (i.e., investment
    and retirement accounts).

    Args:
        accounts: List of Account objects with loaded account_type.

    Returns:
        dict mapping account_id to InvestmentParams.
    """

def _load_interest_params(accounts: list) -> dict[int, InterestParams]:
    """Batch-load InterestParams for interest-bearing accounts.

    Args:
        accounts: List of Account objects with loaded account_type.

    Returns:
        dict mapping account_id to InterestParams.
    """

def _load_deductions_by_account(
    accounts: list, user_id: int,
) -> dict[int, list]:
    """Load paycheck deductions targeting investment accounts.

    Returns deductions grouped by target_account_id.  Each deduction
    has the SalaryProfile eagerly loaded for access to annual_salary.

    Args:
        accounts: List of Account objects.
        user_id: User ID for SalaryProfile ownership.

    Returns:
        dict mapping account_id to list of PaycheckDeduction.
    """

def _load_salary_gross_biweekly(
    user_id: int, scenario: Scenario,
) -> Decimal:
    """Load the user's gross biweekly pay from their active salary profile.

    Returns Decimal("0") if no active salary profile exists.

    Args:
        user_id: User ID.
        scenario: Baseline scenario.

    Returns:
        Decimal gross biweekly pay.
    """
```

These follow the exact batch-loading pattern used in
`savings_dashboard_service._load_account_params()` (lines 193-283).

New imports needed:
- `from app.models.investment_params import InvestmentParams`
- `from app.models.interest_params import InterestParams`
- `from app.models.salary_profile import SalaryProfile`  (already imported)
- `from app.models.deduction import PaycheckDeduction`
- `from app.services import growth_engine`
- `from app.services.investment_projection import calculate_investment_inputs`

**Step 2: Rewrite `_compute_savings_progress()`**

New signature:

```python
def _compute_savings_progress(
    savings_accounts: list,
    year_period_ids: list[int],
    scenario_id: int,
    all_periods: list,
    year: int,
    scenario: Scenario,
    investment_params_map: dict,
    interest_params_map: dict,
    deductions_by_account: dict,
    salary_gross_biweekly: Decimal,
) -> list[dict]:
    """Compute balance growth, contributions, and returns for savings accounts.

    Dispatches to three calculation paths based on account type:
    - Investment accounts (with InvestmentParams): growth engine with
      employer contributions and assumed annual return.
    - Interest-bearing accounts (with InterestParams): balance
      calculator with interest accrual.
    - Plain savings accounts: standard balance calculator.

    Args:
        savings_accounts: Non-debt, non-checking accounts.
        year_period_ids: IDs of pay periods in the target year.
        scenario_id: Baseline scenario ID.
        all_periods: All user pay periods.
        year: Target calendar year.
        scenario: Baseline scenario.
        investment_params_map: account_id -> InvestmentParams.
        interest_params_map: account_id -> InterestParams.
        deductions_by_account: account_id -> list of PaycheckDeduction.
        salary_gross_biweekly: Decimal gross biweekly pay.

    Returns:
        List of dicts: [{account_name, account_id, jan1_balance,
        dec31_balance, total_contributions, employer_contributions,
        investment_growth}].
    """
```

Implementation per account:

```python
for account in savings_accounts:
    # 1. Compute user contributions (shadow income transactions).
    #    Same query as current code -- this remains correct.
    contributions = _sum_shadow_income(account.id, year_period_ids, scenario_id)

    inv_params = investment_params_map.get(account.id)
    int_params = interest_params_map.get(account.id)

    if inv_params:
        # Investment account: use growth engine.
        jan1_bal, dec31_bal, employer_total, growth_total = (
            _project_investment_for_year(
                account, inv_params, all_periods, year,
                scenario, deductions_by_account,
                salary_gross_biweekly, year_period_ids,
                scenario_id,
            )
        )
    elif int_params:
        # Interest-bearing account: balance calculator with interest.
        balances = _get_account_balance_map(account, scenario, all_periods)
        jan1_bal = _lookup_period_balance(balances, year, 1, all_periods)
        dec31_bal = _lookup_period_balance(balances, year, 12, all_periods)
        employer_total = ZERO
        growth_total = _compute_interest_for_year(
            account, int_params, scenario, all_periods, year,
        )
    else:
        # Plain savings: standard balance calculator.
        balances = _get_account_balance_map(account, scenario, all_periods)
        jan1_bal = _lookup_period_balance(balances, year, 1, all_periods)
        dec31_bal = _lookup_period_balance(balances, year, 12, all_periods)
        employer_total = ZERO
        growth_total = ZERO

    result.append({
        "account_name": account.name,
        "account_id": account.id,
        "jan1_balance": jan1_bal,
        "dec31_balance": dec31_bal,
        "total_contributions": contributions,
        "employer_contributions": employer_total,
        "investment_growth": growth_total,
    })
```

**Step 3: Add `_project_investment_for_year()` helper**

```python
def _project_investment_for_year(
    account: Account,
    investment_params: InvestmentParams,
    all_periods: list,
    year: int,
    scenario: Scenario,
    deductions_by_account: dict,
    salary_gross_biweekly: Decimal,
    year_period_ids: list[int],
    scenario_id: int,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Project investment account balance through the target year.

    Uses the growth engine with employer contributions and assumed
    annual return, following the same pattern as
    savings_dashboard_service._project_investment().

    Args:
        account: The investment account.
        investment_params: InvestmentParams for the account.
        all_periods: All user pay periods.
        year: Target calendar year.
        scenario: Baseline scenario.
        deductions_by_account: account_id -> list of PaycheckDeduction.
        salary_gross_biweekly: Decimal gross biweekly pay.
        year_period_ids: Pay period IDs in the target year.
        scenario_id: Baseline scenario ID.

    Returns:
        Tuple of (jan1_balance, dec31_balance, employer_contributions,
        investment_growth).
    """
```

Implementation:
1. Get the base balance map using `_get_account_balance_map()` (plain calculator).
2. Find the period closest to Jan 1 to get `jan1_balance`.
3. Get the year's pay periods from `all_periods`.
4. Build adapted deductions from `deductions_by_account` (same pattern as
   `savings_dashboard_service._project_investment()` lines 486-495).
5. Query shadow income transactions for the account in the year's periods.
6. Call `calculate_investment_inputs()` to get periodic contribution, employer params,
   annual limit, and YTD contributions.
7. Call `growth_engine.project_balance()` with jan1_balance as starting balance and the
   year's periods.
8. Sum `employer_contribution` and `growth` from all ProjectedBalance objects.
9. Use the last ProjectedBalance's `end_balance` as dec31_balance.

**Step 4: Add `_lookup_period_balance()` helper**

```python
def _lookup_period_balance(
    balances: dict,
    year: int,
    month: int,
    all_periods: list,
) -> Decimal:
    """Look up the balance at the end of a specific month.

    Finds the last pay period ending on or before the month's last day
    and returns its balance from the balance map.

    Args:
        balances: period_id -> Decimal balance map.
        year: Calendar year.
        month: Month number (1-12).
        all_periods: All user pay periods.

    Returns:
        Decimal balance, or ZERO if no matching period.
    """
```

**Step 5: Add `_compute_interest_for_year()` helper**

```python
def _compute_interest_for_year(
    account: Account,
    interest_params,
    scenario: Scenario,
    all_periods: list,
    year: int,
) -> Decimal:
    """Compute total interest earned on an account during the year.

    Calls calculate_balances_with_interest() and sums the interest
    from periods whose start_date falls in the target year.

    Args:
        account: Interest-bearing account.
        interest_params: InterestParams for the account.
        scenario: Baseline scenario.
        all_periods: All user pay periods.
        year: Target calendar year.

    Returns:
        Decimal total interest earned in the year.
    """
```

**Step 6: Extract `_sum_shadow_income()` helper**

Refactor the existing shadow income query from `_compute_savings_progress()` lines 706-721
into a dedicated helper. This improves readability and allows reuse.

```python
def _sum_shadow_income(
    account_id: int,
    period_ids: list[int],
    scenario_id: int,
) -> Decimal:
    """Sum shadow income transactions (transfers in) for an account.

    Args:
        account_id: Target account ID.
        period_ids: Pay period IDs to query.
        scenario_id: Baseline scenario ID.

    Returns:
        Decimal total contributions from shadow income transactions.
    """
```

**Step 7: Update template (`analytics/_year_end.html`)**

Add two columns to the savings progress table:

Current columns: Account | Jan 1 Balance | Dec 31 Balance | Contributions

New columns: Account | Jan 1 Balance | Dec 31 Balance | Contributions | Employer | Growth

The Employer and Growth columns display `{{ fmt(s.employer_contributions) }}` and
`{{ fmt(s.investment_growth) }}`. When both are zero (plain savings accounts), the cells
show `$0.00` which is correct and informative.

Optionally, add a footer row summing each column, consistent with the debt progress
section's visual style.

**Step 8: Update CSV export (`csv_export_service.py`)**

In `_add_savings_section()`:

Current column headers:
```python
["Account", "Jan 1 Balance ($)", "Dec 31 Balance ($)", "Contributions ($)"]
```

New column headers:
```python
["Account", "Jan 1 Balance ($)", "Dec 31 Balance ($)", "Contributions ($)",
 "Employer ($)", "Growth ($)"]
```

Add `_dec(s["employer_contributions"])` and `_dec(s["investment_growth"])` to each row.

**Step 9: Update `_build_summary()` call site**

Pass the new params from `_load_common_data()` through to `_compute_savings_progress()`:

```python
"savings_progress": _compute_savings_progress(
    ctx["savings_accounts"],
    ctx["year_period_ids"],
    scenario.id,
    ctx["all_periods"],
    year,
    scenario,
    ctx["investment_params_map"],
    ctx["interest_params_map"],
    ctx["deductions_by_account"],
    ctx["salary_gross_biweekly"],
),
```

### E. Test cases

**New test helpers needed:**

- `_create_investment_account(user, periods)`: Creates an investment account (e.g.,
  "401k") with InvestmentParams (assumed_annual_return=0.07, employer_match at 50% up to
  6% of salary).
- `_create_hysa_account(user, periods)`: Creates an HYSA account with InterestParams
  (apy=0.05, compounding_frequency="daily").

**Tests for savings progress (`test_year_end_summary_service.py`):**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C2-1 | test_savings_progress_basic | Savings account ($500 anchor) + $200 transfer | `compute_year_end_summary(uid, 2026)` | jan1 and dec31 from balance calculator; total_contributions=$200; employer=0; growth=0 | Mod (update existing test to assert balance calculator values and new fields) |
| C2-2 | test_savings_contributions_from_shadows | 3 transfers totaling $500 | `compute_year_end_summary(uid, 2026)` | total_contributions=$500; employer=0; growth=0 | Mod (add assertions for new fields) |
| C2-3 | test_savings_investment_with_growth | Investment account ($10,000 anchor), assumed_annual_return=0.07, no employer | `compute_year_end_summary(uid, 2026)` | dec31_balance includes growth from 7% return. investment_growth > 0. employer_contributions=0. Hand-computed: ~$10,700 after 10 biweekly periods (~140 days) of 7% annual return | New |
| C2-4 | test_savings_employer_match | Investment account + employer_match at 50% up to 6%, salary=$75k, paycheck deduction $200/period | `compute_year_end_summary(uid, 2026)` | employer_contributions > 0, reflects 50% match of $200 = $100/period * num_periods. dec31 includes employee + employer + growth | New |
| C2-5 | test_savings_employer_flat_pct | Investment account + employer_flat_percentage=0.03, salary=$75k | `compute_year_end_summary(uid, 2026)` | employer_contributions = gross_biweekly * 0.03 * num_periods | New |
| C2-6 | test_savings_hysa_with_interest | HYSA account ($5,000 anchor), apy=0.05 | `compute_year_end_summary(uid, 2026)` | dec31_balance includes interest accrual. investment_growth = total interest earned. Hand-computed from compound interest formula | New |
| C2-7 | test_savings_no_accounts | No savings accounts | `compute_year_end_summary(uid, 2026)` | savings_progress == [] | Existing (unchanged) |
| C2-8 | test_savings_mixed_accounts | One plain savings + one investment + one HYSA | `compute_year_end_summary(uid, 2026)` | Each account uses correct calculation path. All three appear in results with accurate balances | New |
| C2-9 | test_savings_investment_no_deductions_transfer_only | Investment account with transfers but no paycheck deductions | `compute_year_end_summary(uid, 2026)` | periodic_contribution derived from transfer average. Growth and employer still computed correctly | New |

**Template and CSV tests:**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C2-10 | test_year_end_savings_employer_column | Investment account with employer match | GET /analytics/year-end | "Employer" column header and match amount in HTML | New |
| C2-11 | test_year_end_savings_growth_column | Investment account with growth | GET /analytics/year-end | "Growth" column header and growth amount in HTML | New |
| C2-12 | test_csv_savings_new_columns | Full year-end data with investment account | CSV export | "Employer ($)" and "Growth ($)" in header row, values in data rows | New |

### F. Manual verification steps

1. Navigate to `/analytics`, click "Year-End Summary" tab.
2. Verify Savings Progress section for each account type:
   - **Plain savings:** Jan 1 and Dec 31 should match anchor + transfer activity.
   - **HYSA:** Dec 31 should be slightly higher than anchor + transfers (interest earned).
   - **401k / Investment:** Dec 31 should reflect contributions + employer match + growth.
3. Verify the Employer and Growth columns show non-zero values for investment accounts
   and zero for plain savings.
4. Compare the investment account's Dec 31 balance with the savings dashboard's projected
   balance -- they should use the same calculation engine and produce consistent results
   for equivalent time windows.
5. Export CSV and verify the new columns appear with correct values.
6. Check that the savings progress section does not appear when no savings accounts exist.

### G. Downstream effects

- **CSV Export:** Two new columns added to savings progress section. Any scripts or tools
  that parse the year-end CSV may need to account for the new columns.
- **Template:** Two new columns in the savings progress table. Mobile responsiveness should
  be verified at the `sm` breakpoint.
- **Test fixtures:** New `_create_investment_account()` and `_create_hysa_account()` helpers
  are available for future tests.

### H. Rollback notes

The savings progress fix is self-contained in the year-end service, template, and CSV export.
Reverting to the naive formula requires restoring the old `_compute_savings_progress()` and
removing the new template columns. No migration, no data impact.

---

## Section 6: Known Related Issues (Out of Scope)

### 6.1 Net worth understates investment account balances

**Issue:** The net worth section uses `_get_account_balance_map()` for investment accounts,
which falls through to the plain `calculate_balances()` path. This misses assumed annual
return and employer contributions, understating net worth for users with investment accounts.

**Impact:** Net worth monthly values and the jan1/dec31/delta figures do not include
investment growth. The chart shows a flatter trajectory than reality.

**Recommendation:** After the savings progress fix is validated, apply the same growth engine
logic to `_get_account_balance_map()` for investment accounts. This requires threading
investment params, deductions, and salary data through `_build_account_data()`. Estimated as
a separate commit with its own test coverage.

### 6.2 `calculate_balances_with_amortization()` is no longer used correctly

**Issue:** After Commit 1, `_get_account_balance_map()` uses the amortization schedule for
debt accounts, bypassing `calculate_balances_with_amortization()`. This balance calculator
function may still be called by other code paths (e.g., the old chart_data_service if any
remnants remain). Its naive interest/principal split per biweekly period is fundamentally
flawed.

**Recommendation:** Audit all callers of `calculate_balances_with_amortization()`. If the
year-end service was the only remaining consumer, consider deprecating or removing the
function. If other consumers exist, they need the same amortization engine fix.

---

## Section 7: Verification Checklist

Before marking this work complete, all of the following must be true:

- [ ] `pylint app/ --fail-on=E,F` passes with no new warnings.
- [ ] All existing tests in `test_year_end_summary_service.py` pass.
- [ ] All existing tests in `test_analytics.py` pass.
- [ ] All existing tests in `test_csv_export_service.py` pass.
- [ ] All existing tests in `test_routes/test_loan.py` pass (loan route still works after
      extracting payment preparation).
- [ ] All new tests pass.
- [ ] Full test suite passes: `pytest tests/test_services/ -v --tb=short` and
      `pytest tests/test_routes/ -v --tb=short`.
- [ ] Manual verification on live data confirms debt and savings balances match account pages.
