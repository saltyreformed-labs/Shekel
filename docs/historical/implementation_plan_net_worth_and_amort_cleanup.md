> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Net Worth Investment Accuracy and Amortization Cleanup

**Version:** 1.0
**Date:** April 9, 2026
**Prerequisite:** Year-end debt progress and savings progress fixes complete (Commits 1-2
from `implementation_plan_year_end_fixes.md`).
**Scope:** Fix net worth understatement for investment accounts (issue 6.1) and clean up
the now-misused `calculate_balances_with_amortization()` fallback path (issue 6.2).

---

## Root Cause Analysis

### Issue 6.1: Net worth understates investment account balances

**Symptom:** The Net Worth chart on the Year-End tab shows investment account balances
based only on anchor balance plus transfer transactions, missing assumed annual return and
employer contributions. A 401(k) with $50,000 and 7% annual return shows the same balance
all year instead of growing to ~$53,500.

**Root cause:** `_compute_net_worth()` calls `_build_account_data()` which calls
`_get_account_balance_map()` for ALL accounts. For investment accounts (those with
InvestmentParams), this function falls through to the plain `calculate_balances()` path
(line 1654-1656) which returns anchor + projected transactions -- no growth engine, no
employer contributions, no assumed return.

The savings progress section (`_compute_savings_progress()`) was fixed in Commit 2 to use
the growth engine for investment accounts. But the net worth section still uses the old
path, creating an inconsistency:

| Account type | Net worth path | Savings progress path |
|--------------|----------------|----------------------|
| Plain savings | `calculate_balances()` | `calculate_balances()` |
| HYSA / interest | `calculate_balances_with_interest()` | `calculate_balances_with_interest()` |
| Debt / amortization | Amortization schedule | Amortization schedule |
| **Investment** | **`calculate_balances()` (WRONG)** | **Growth engine (CORRECT)** |

**Correct approach:** `_get_account_balance_map()` should use the growth engine for
investment accounts, just as the savings progress section does. This requires threading
the investment-related context (`investment_params_map`, `deductions_by_account`,
`salary_gross_biweekly`, `year_period_ids`, `scenario_id`, and `year`) into the net worth
calculation path.

**Design consideration:** The growth engine's `project_balance()` returns a list of
`ProjectedBalance` objects, one per pay period. Each has `period_id` and `end_balance`.
This maps directly to the `OrderedDict[period_id -> Decimal]` that
`_get_account_balance_map()` returns. The integration point is clean.

### Issue 6.2: `calculate_balances_with_amortization()` is no longer used correctly

**Symptom:** After Commit 1, the year-end service generates proper amortization schedules
for debt accounts and passes them to `_get_account_balance_map()` via `debt_schedules`.
However, `_get_account_balance_map()` retains a fallback path (lines 1637-1652) that calls
`calculate_balances_with_amortization()` when no pre-generated schedule is available.

**Root cause:** The fallback exists because `_get_account_balance_map()` is designed as a
general-purpose function that might be called without `debt_schedules` (e.g., by the
savings progress section for non-debt accounts). The fallback ensures debt accounts still
get some balance calculation even without pre-generated schedules. But this fallback uses
the naive biweekly interest/principal split that was the original bug.

**Current callers of `calculate_balances_with_amortization()` in production code:**

| File | Line | Context | Status |
|------|------|---------|--------|
| `year_end_summary_service.py` | 1646 | Fallback in `_get_account_balance_map()` | Should never execute -- debt_schedules is always provided for debt accounts |
| `balance_calculator.py` | 176 | Function definition | Still needed by tests |

**Risk:** The fallback path is dead code in the current flow (`_build_summary()` always
generates `debt_schedules` and passes it through). But if a future change calls
`_get_account_balance_map()` without `debt_schedules`, the naive calculator silently
produces wrong results. Dead fallback code that silently produces wrong answers is a
latent bug.

**Current callers of `calculate_balances_with_amortization()` in test code:**

| File | Count | Context |
|------|-------|---------|
| `test_balance_calculator_debt.py` | 17 tests | Unit tests for the function itself |
| `test_integration/test_loan_payment_pipeline.py` | 2 calls | Integration tests |
| `test_integration/test_workflows.py` | 0 direct calls | Uses balance_calculator module |

**Approach:** The function itself is correct in what it does -- it's a simplified
amortization calculator useful for quick balance estimates. The problem is that the
year-end service was using it where the full amortization engine was needed. Now that the
year-end service uses proper schedules, the fallback in `_get_account_balance_map()` should
be removed. The function in `balance_calculator.py` stays -- it's still tested and may be
useful for other contexts (grid display, quick estimates). But the year-end service should
not use it at all.

---

## Downstream Effects

### Net worth chart

The net worth chart (`chart_year_end.js`) renders 12 monthly data points from
`data.net_worth.monthly_values`. After this fix, investment account balances will include
growth and employer contributions, increasing the net worth values. The chart will show a
more realistic upward trajectory for users with investment accounts.

### Savings progress consistency

After this fix, the net worth section and savings progress section will use identical
calculation paths for all account types. This eliminates the inconsistency where savings
progress shows a 401(k) growing while net worth shows it flat.

### Other consumers of `_get_account_balance_map()`

The function is called from two places in the year-end service:

1. `_build_account_data()` (for net worth) -- will gain investment projection
2. `_compute_savings_progress()` via `_project_investment_for_year()` -- already uses the
   growth engine for investment accounts; `_get_account_balance_map()` is called only for
   the base balance (line 876), which is correct since the growth engine builds on top
3. `_compute_savings_progress()` for interest/plain accounts -- calls directly, which is
   correct (no investment projection needed for non-investment accounts)

### Template and CSV

No template or CSV changes needed. The net worth section's output structure is unchanged
(monthly_values, jan1, dec31, delta). Only the values become more accurate.

---

## Commit Sequence

| # | Commit Message | Summary |
|---|----------------|---------|
| 1 | `fix(year-end): include investment growth in net worth and remove amortization fallback` | Thread investment context through net worth; use growth engine for investment account balances; remove dead amortization fallback |

This is a single commit because the two changes are tightly coupled:
- Both modify `_get_account_balance_map()` and its callers
- The amortization fallback removal is a 6-line deletion, not a separate unit of work
- Testing both together ensures the function handles all account types correctly

---

## Commit 1: Include Investment Growth in Net Worth and Remove Amortization Fallback

### A. Commit message

```text
fix(year-end): include investment growth in net worth and remove amortization fallback
```

### B. Problem statement

The year-end net worth section computes investment account balances using the plain
`calculate_balances()` function, which returns only anchor + transactions. This misses the
assumed annual return and employer contributions that the savings progress section correctly
includes via the growth engine. Additionally, `_get_account_balance_map()` retains a dead
fallback to `calculate_balances_with_amortization()` that should never execute but would
silently produce inaccurate results if triggered.

### C. Files modified

| File | Change | Reason |
|------|--------|--------|
| `app/services/year_end_summary_service.py` | Thread investment context through net worth path; add growth engine dispatch in `_get_account_balance_map()`; remove amortization fallback | Fix investment balances in net worth; eliminate dead code |
| `tests/test_services/test_year_end_summary_service.py` | Add net worth tests with investment accounts | Validate growth engine is used for net worth |

### D. Implementation approach

**Step 1: Thread `ctx` through the net worth calculation path**

The growth engine requires `investment_params_map`, `deductions_by_account`,
`salary_gross_biweekly`, `year_period_ids`, `scenario_id`, and `year` -- all available in
`ctx` but not currently passed to the net worth functions.

Update `_compute_net_worth()` signature:

```python
def _compute_net_worth(
    year: int,
    accounts: list,
    all_periods: list,
    scenario: Scenario,
    debt_schedules: dict[int, list] | None = None,
    ctx: dict | None = None,
) -> dict:
```

Update `_build_account_data()` signature:

```python
def _build_account_data(
    accounts: list,
    scenario: Scenario,
    all_periods: list,
    debt_schedules: dict[int, list] | None = None,
    ctx: dict | None = None,
) -> list[dict]:
```

Update `_get_account_balance_map()` signature:

```python
def _get_account_balance_map(
    account: Account,
    scenario: Scenario,
    periods: list,
    debt_schedules: dict[int, list] | None = None,
    ctx: dict | None = None,
) -> dict | None:
```

Thread `ctx` through each call:
- `_build_summary()` passes `ctx=ctx` to `_compute_net_worth()`
- `_compute_net_worth()` passes `ctx=ctx` to `_build_account_data()`
- `_build_account_data()` passes `ctx=ctx` to `_get_account_balance_map()`

Existing callers that pass `ctx=None` (e.g., from `_compute_savings_progress()` via
`_project_investment_for_year()` line 876, and from `_compute_savings_progress()` for
interest/plain accounts) continue to work unchanged -- the parameter is optional and
`None` means "no investment projection."

**Step 2: Add investment projection in `_get_account_balance_map()`**

After the interest-bearing account block and before the amortization fallback, add a new
block for investment accounts:

```python
# Investment accounts: use growth engine when context is available.
if (ctx is not None
        and acct_type
        and getattr(acct_type, "has_parameters", False)
        and not acct_type.has_interest
        and not acct_type.has_amortization):
    inv_params = ctx["investment_params_map"].get(account.id)
    if inv_params:
        return _build_investment_balance_map(
            account, inv_params, scenario, periods, ctx,
        )
```

This check mirrors the filter in `_load_investment_params()` -- accounts with
`has_parameters=True` that are neither interest-bearing nor amortizing.

**Step 3: Add `_build_investment_balance_map()` helper**

```python
def _build_investment_balance_map(
    account: Account,
    investment_params: InvestmentParams,
    scenario: Scenario,
    periods: list,
    ctx: dict,
) -> OrderedDict:
    """Build period_id -> balance map using the growth engine.

    Starts from the anchor balance (via plain calculate_balances),
    then projects forward using the growth engine with employer
    contributions and assumed annual return.

    For periods at or before the anchor, uses the base balance
    calculator values (actual data).  For periods after the anchor,
    uses growth engine projections.

    Args:
        account: Investment account.
        investment_params: InvestmentParams for the account.
        scenario: Baseline scenario.
        periods: All user pay periods.
        ctx: Common data dict with deductions_by_account,
            salary_gross_biweekly, year_period_ids.

    Returns:
        OrderedDict mapping period_id to Decimal balance.
    """
```

Implementation:
1. Compute base balances using `calculate_balances()` (anchor + transactions). This gives
   accurate balances for periods up to and including the anchor.
2. Find the anchor period index to determine which periods need projection.
3. Load deductions and salary from `ctx`.
4. Adapt deductions for `calculate_investment_inputs()` (same pattern as
   `_project_investment_for_year()`).
5. Query shadow income transactions for the account.
6. Call `calculate_investment_inputs()` with the first post-anchor period as context.
7. Call `growth_engine.project_balance()` with:
   - `current_balance` = base balance at the anchor period
   - `assumed_annual_return` from investment_params
   - `periods` = post-anchor periods only
   - All contribution and employer params from inputs
8. Build an `OrderedDict` that uses base balances for anchor-and-earlier periods, and
   growth engine `end_balance` values for post-anchor periods.

This approach:
- Respects actual transaction history up to the anchor (no fictional growth on settled data)
- Projects growth only on future/unsettled periods (consistent with the investment dashboard)
- Includes employer contributions in the projection
- Returns the same `OrderedDict[period_id -> Decimal]` type that all callers expect

**Step 4: Remove the amortization fallback from `_get_account_balance_map()`**

Delete the fallback block (current lines 1637-1652):

```python
    # Amortizing loan accounts without pre-generated schedule (fallback).
    if acct_type and acct_type.has_amortization:
        loan_params = (
            db.session.query(LoanParams)
            .filter_by(account_id=account.id)
            .first()
        )
        if loan_params:
            balances, _ = (
                balance_calculator.calculate_balances_with_amortization(
                    **base_args,
                    account_id=account.id,
                    loan_params=loan_params,
                )
            )
            return balances
```

After this deletion, if a debt account reaches the plain `calculate_balances()` call at
the end of the function, it gets the basic anchor + transactions balance. This is an
acceptable degradation: without `debt_schedules`, the function cannot produce
amortization-accurate balances, and the naive calculator was producing wrong results
anyway. The correct fix is always to provide `debt_schedules`.

In practice, `_build_summary()` always provides `debt_schedules` to
`_compute_net_worth()`, so debt accounts always take the schedule path (lines 1594-1605).
The only callers that omit `debt_schedules` are from `_compute_savings_progress()`, which
never processes debt accounts (they're filtered to `savings_accounts` only).

**Step 5: Update `_build_summary()` call to pass `ctx`**

```python
"net_worth": _compute_net_worth(
    year, ctx["accounts"], ctx["all_periods"], scenario,
    debt_schedules=debt_schedules,
    ctx=ctx,
),
```

**Step 6: Remove unused `LoanParams` import if appropriate**

After removing the amortization fallback, check if `LoanParams` is still imported
elsewhere in the file. It is -- used in `_generate_debt_schedules()` and
`_compute_debt_progress()`. So the import stays.

### E. Test cases

**Updated tests (existing):**

| ID | Test name | Change | Reason |
|----|-----------|--------|--------|
| - | test_net_worth_debt_uses_amortization | No change | Already validates debt path; continues to pass |
| - | test_net_worth_12_points | No change | Structural test; unaffected |
| - | test_net_worth_jan_dec_delta | No change | Invariant test; unaffected |

**New tests:**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C1-1 | test_net_worth_investment_includes_growth | 401(k) ($10k, 7% return, no employer) + Checking ($1k) | `compute_year_end_summary(uid, 2026)` | Net worth monthly values increase over time (growth on $10k). Month 5 net worth > $11,000 (checking $1k + 401k $10k + growth). Without the fix, net worth would be flat at $11,000 | New |
| C1-2 | test_net_worth_investment_with_employer | 401(k) ($10k, 7% return, flat 3% employer) + salary | `compute_year_end_summary(uid, 2026)` | Net worth increases faster than growth-only case due to employer contributions. Dec 31 net worth > growth-only Dec 31 net worth | New |
| C1-3 | test_net_worth_mixed_account_types | Checking ($1k) + 401(k) ($10k, 7%) + HYSA ($5k, 5% APY) + Mortgage ($240k) | `compute_year_end_summary(uid, 2026)` | All account types use their correct calculation path. Net worth reflects amortization for debt, growth for investments, interest for HYSA, and plain for checking | New |
| C1-4 | test_net_worth_consistent_with_savings_progress | 401(k) ($10k, 7% return) | `compute_year_end_summary(uid, 2026)` | The Dec 31 net worth balance for the 401(k) should be consistent with the savings progress Dec 31 balance (both use the growth engine) | New |
| C1-5 | test_amortization_fallback_removed | Mortgage account, call `_get_account_balance_map()` WITHOUT debt_schedules | Direct call | Function returns plain balance (not amortization), proving the fallback is gone. Balance equals anchor + transactions, not the naive amortization split | New |

### F. Manual verification steps

1. Navigate to `/analytics`, click "Year-End Summary" tab.
2. If you have a 401(k) or other investment account, verify the Net Worth chart shows
   an upward trajectory (investment growth) rather than a flat line.
3. Compare the Dec 31 net worth with a manual calculation:
   checking + savings + investment_with_growth + HYSA_with_interest - mortgage_amortized.
4. Verify the Savings Progress Dec 31 balance for investment accounts is consistent with
   the net worth section's treatment of the same account.
5. Verify the Net Worth Jan 1 / Dec 31 / Delta values update correctly when switching
   years via the year selector.

### G. Downstream effects

- **Net Worth Chart:** Monthly values will be higher for users with investment accounts.
  The chart's visual trajectory will show realistic growth.
- **Savings Progress Consistency:** The Dec 31 balance shown in savings progress and the
  balance used in net worth will now agree for investment accounts.
- **`calculate_balances_with_amortization()`:** No longer called by any production code
  in the year-end service. The function remains in `balance_calculator.py` for potential
  use by other code paths (grid, other services) and is still covered by its dedicated
  test suite (`test_balance_calculator_debt.py`, 17 tests). No changes to the function
  itself or its tests.
- **Other callers of `_get_account_balance_map()`:** The savings progress section calls
  this function for non-investment accounts (interest-bearing and plain). These paths are
  unaffected since `ctx` defaults to `None` and the new investment block is skipped.
  The savings progress section's investment path calls `_get_account_balance_map()` for
  the base balance only (line 876) -- this also works correctly since it passes no `ctx`.

### H. Rollback notes

Revert the year-end service changes. No migration, no data impact. The amortization
fallback can be restored by un-deleting the block. The function signatures revert to
their previous forms (without `ctx` parameter).

---

## Section 2: What This Plan Does NOT Change

### `calculate_balances_with_amortization()` function itself

The function in `balance_calculator.py` (lines 176-289) is **not modified or deleted**.
It remains available for:
- Direct unit testing (`test_balance_calculator_debt.py`, 17 tests)
- Potential use by other services that need a quick amortization estimate
- Integration tests that test the balance calculator in isolation

The function's naive biweekly interest/principal split is a valid approximation for
display contexts (like the grid) where generating a full amortization schedule would be
too expensive. The bug was using it for year-end financial reporting where accuracy
matters. That's now fixed.

### Other balance calculator consumers

The grid route, accounts route, investment route, savings dashboard service, calendar
service, dashboard service, and retirement dashboard service all call
`calculate_balances()` or `calculate_balances_with_interest()` directly. These are
unaffected by this change. They compute balances for their own display contexts and
do not use the year-end service's `_get_account_balance_map()`.

### Test suite for `calculate_balances_with_amortization()`

The 17 tests in `test_balance_calculator_debt.py` are **not modified**. They test the
function in isolation with mock data and verify its internal logic (interest/principal
split, payment detection, ARM handling). These tests remain valid as unit tests for the
function itself, regardless of whether the year-end service uses it.

---

## Section 3: Verification Checklist

Before marking this work complete, all of the following must be true:

- [ ] `pylint app/ --fail-on=E,F` passes with no new warnings.
- [ ] All existing tests in `test_year_end_summary_service.py` pass.
- [ ] All existing tests in `test_balance_calculator_debt.py` pass (function not broken).
- [ ] All existing tests in `test_analytics.py` pass.
- [ ] All new tests pass.
- [ ] Full suite passes: `pytest tests/test_services/ -v --tb=short` and
      `pytest tests/test_routes/ -v --tb=short`.
- [ ] Manual verification on live data confirms net worth reflects investment growth.
