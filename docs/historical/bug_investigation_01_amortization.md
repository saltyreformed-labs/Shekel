> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Bug Investigation: Transfers to Debt Accounts Do Not Affect Amortization Schedule

**Date:** 2026-04-10
**Status:** Core fix applied (d2455e8), remaining issues tracked

## Root Cause

`get_loan_projection()` in `app/services/amortization_engine.py` (lines 789--866) generates
the amortization schedule starting from today's date instead of the loan's origination date,
making all past confirmed payments invisible. Two specific defects:

1. **Missing `origination_date`:** Line 852 calls `generate_schedule()` without passing
   `origination_date`. This causes the schedule loop to start from `date.today()` (line 448),
   so payment records from past months are present in the `amount_by_month` lookup dict but
   never matched by a `month_key` in the loop. Only current-month or future payments can
   match.

2. **Static starting balance:** Line 828 uses `params.current_principal` -- a database field
   set during loan setup and never automatically updated by payment activity -- instead of
   `params.original_principal`. The schedule begins from a stale balance that does not reflect
   any confirmed payments.

The year-end service (`app/services/year_end_summary_service.py`, lines 1291--1301) calls
the same engine correctly: it passes `params.original_principal` as the starting balance,
`params.origination_date` as the origination date, and `params.term_months` as the term
length. This allows the engine to replay ALL payments from origination. The loan dashboard
did not follow this pattern until the fix below.

### Resolution (commit d2455e8)

Commit d2455e8 (2026-04-10) corrected `get_loan_projection()` to match the year-end service
pattern. The fix changed four things:

1. Starting balance: `params.current_principal` -> `params.original_principal`
2. Schedule length: computed `remaining` -> `params.term_months`
3. Origination date: `date.today().replace(day=1)` -> `params.origination_date`
4. ARM handling: derives real principal from last confirmed schedule row for display

The same commit also fixed `dashboard()` original/floor chart schedules,
`savings_dashboard_service` balance derivation (walks schedule rows by date instead of
index), and `debt_strategy._compute_real_principal()` (uses `original_principal` and
`term_months`).

---

## Code Path Trace

### 1. Payment discovery -- `app/services/loan_payment_service.py:get_payment_history()` (lines 45--119)

Queries shadow income transactions on the debt account:

- `Transaction.transfer_id.isnot(None)` -- shadow transactions only
- `Transaction.transaction_type_id == INCOME` -- income side of the transfer
- `Transaction.is_deleted.is_(False)` -- excludes soft-deleted
- `Status.excludes_from_balance.is_(False)` -- excludes Cancelled/Credit

Sets `is_confirmed=txn.status.is_settled` (True for Paid/Settled/Received, False for
Projected). Uses `txn.effective_amount` for the amount and `txn.pay_period.start_date` as
the payment date.

**Verdict: Works correctly.** Confirmed payments are discovered and converted to
`PaymentRecord` instances.

### 2. Payment preparation -- `app/services/loan_payment_service.py:prepare_payments_for_engine()` (lines 152--242)

Two correction steps before the engine receives payment data:

1. **Escrow subtraction** (lines 194--208): Removes escrow from payments exceeding the
   contractual P&I amount. Ensures the engine sees only the P&I portion.
2. **Biweekly redistribution** (lines 210--240): Shifts same-month payments to consecutive
   months to restore one-payment-per-month alignment.

Preserves `is_confirmed` flag throughout both steps.

**Verdict: Works correctly.**

### 3. Loan context loading -- `app/routes/loan.py:_load_loan_context()` (lines 329--415)

Loads escrow components, calls `get_payment_history()`, calls `prepare_payments_for_engine()`.
Returns a dict with prepared payments, rate changes, escrow data, and derived values.

**Verdict: Works correctly.** Payments are loaded and prepared.

### 4. Dashboard entry -- `app/routes/loan.py:dashboard()` (lines 433--593)

- Line 447: Calls `_load_loan_context()` to get payments
- Line 455: Calls `get_loan_projection(params, payments=payments, rate_changes=rate_changes)`
- Lines 479--485: Generates `original_schedule` with `orig_principal`, `params.origination_date`,
  `params.term_months` (no payments -- intentional contractual baseline)
- Lines 500--509: Generates `floor_schedule` with same origination params plus confirmed
  payments only
- Line 590: Passes `proj.schedule` to the template as `amortization_schedule`

**Verdict (post-fix): Correct.** All three schedule generations (committed, original, floor)
now start from origination with `original_principal`.

### 5. Projection generation -- `app/services/amortization_engine.py:get_loan_projection()` (lines 789--890)

**THE BUG WAS HERE (fixed by d2455e8).**

Before the fix, this function used `date.today()` as the schedule start, `current_principal`
as the starting balance, and computed `remaining` months as the schedule length. Past payment
records were in `amount_by_month` but the schedule loop never generated their month keys.

After the fix:
- Line 833: `orig_principal = Decimal(str(params.original_principal))` -- full loan amount
- Lines 850--860: `calculate_summary(..., remaining_months=params.term_months,
  origination_date=params.origination_date)` -- full term from origination
- Lines 862--870: `generate_schedule(orig_principal, rate, params.term_months,
  origination_date=params.origination_date, ...)` -- full replay from origination

The schedule now starts from origination and iterates through the full loan term. Past
confirmed payments match by year-month. Future projected payments also match. Months
without payment records use the contractual payment.

### 6. Schedule generation -- `app/services/amortization_engine.py:generate_schedule()` (lines 326--588)

- Lines 390--398: `_build_payment_lookups()` includes ALL payments when `origination_date`
  is None (no pre-origination filtering).
- Lines 441--450: Without `origination_date`, the schedule starts from
  `(date.today().year, date.today().month)`.
- Lines 488--497: For each month, checks `month_key = (pay_year, pay_month)` against
  `amount_by_month`. Only current/future months are generated, so past payment records in
  the lookup dict are never matched.
- Lines 567--577: Correctly assigns `is_confirmed=row_confirmed` to each `AmortizationRow`.

**Verdict: The engine is correct.** It faithfully uses whatever starting point and payments
it receives. The problem is the inputs from `get_loan_projection()`.

### 7. Status propagation -- `app/services/transfer_service.py:update_transfer()` (lines 469--473)

When a transfer status changes:

```python
if "status_id" in kwargs:
    new_status_id = kwargs["status_id"]
    xfer.status_id = new_status_id
    expense_shadow.status_id = new_status_id
    income_shadow.status_id = new_status_id
```

`mark_done()` in `app/routes/transactions.py` (lines 232--260) routes shadow status changes
through `transfer_service.update_transfer()`. Both shadows receive the new status atomically.

**Verdict: Status propagation is correct.** Transfer invariant #4 is maintained.

### 8. Template -- `app/templates/loan/_schedule.html` (lines 52, 80--86)

- Line 52: `{% if row.is_confirmed %}table-success{% endif %}` -- green row highlighting
- Lines 81--85: Renders `<span class="badge bg-success">Confirmed</span>` or
  `<span class="badge bg-secondary">Projected</span>` based on `row.is_confirmed`

**Verdict: Template is correct.** It reads `is_confirmed` and renders appropriately.

### 9. Correct reference implementation -- `app/services/year_end_summary_service.py:_generate_debt_schedules()` (lines 1291--1301)

```python
schedule = amortization_engine.generate_schedule(
    current_principal=params.original_principal,   # start from the beginning
    annual_rate=params.interest_rate,
    remaining_months=params.term_months,            # full term
    origination_date=params.origination_date,       # actual origination
    payment_day=params.payment_day,
    original_principal=original_for_engine,
    term_months=params.term_months,
    payments=payments if payments else None,
    rate_changes=rate_changes,
)
```

This generates the full life-of-loan schedule. All confirmed and projected payments are
matched by month. The engine correctly computes the balance at every point.

---

## Remaining Issues (After d2455e8)

The core fix resolved `get_loan_projection()`, dashboard chart schedules,
`_compute_real_principal()`, and the refinance calculator's principal derivation.
Three issues remain unfixed:

### 1. `_check_loan_paid_off()` (savings_dashboard_service.py:431--501)

Line 467 uses `loan_params.current_principal`. Lines 469--471 compute `remaining` from
today. Line 478 calls `generate_schedule()` without `origination_date`. The schedule starts
from today, so confirmed payments from past months never match. The function cannot detect
a paid-off loan from past payment history.

### 2. Savings dashboard debt projections (savings_dashboard_service.py:349)

Calls `get_loan_projection(acct_loan_params)` without payments. The schedule replays from
origination (correct since d2455e8) but uses only contractual amounts. Debt projections on
the savings overview do not reflect actual payment data -- extra payments are not visible.
The user has noted this is tracked separately.

### 3. Payoff calculator (loan.py:862--958)

Line 862 sets `schedule_start = date.today().replace(day=1)`. Lines 870 and 953 pass this
as `origination_date` to `calculate_summary` and `calculate_payoff_by_date`. Past payments
(loaded via `_load_loan_context`) have dates before today and never match schedule rows.

The chart schedules (original at line 881, committed at line 888, accelerated at line 897)
also use `principal` (= `current_principal`) and `remaining_months` without
`origination_date`. Past confirmed payments in the `payments` list do not match.

---

## Proposed Fix Approach (Remaining Issues Only)

### 1. Fix `_check_loan_paid_off()` (savings_dashboard_service.py:431--501)

Mirror the `get_loan_projection()` pattern -- use `original_principal`, `term_months`, and
`origination_date`:

```python
orig_principal = Decimal(str(loan_params.original_principal))
schedule = amortization_engine.generate_schedule(
    orig_principal, rate, loan_params.term_months,
    origination_date=loan_params.origination_date,
    payment_day=loan_params.payment_day,
    original_principal=original,
    term_months=loan_params.term_months,
    payments=confirmed,
)
```

Remove the `remaining` computation (lines 469--471) and the `principal` variable (line 467).

### 2. Fix savings dashboard debt projections (savings_dashboard_service.py:349)

Load payment history for each debt account and pass it to `get_loan_projection()`. Requires
calling `get_payment_history()` and `prepare_payments_for_engine()` with escrow data for each
debt account. The scenario ID is already available in `params["scenario_id"]`. This is a
larger change and is tracked separately per the user.

### 3. Fix payoff calculator (loan.py:862--958)

Use `params.origination_date`, `orig_principal`, and `params.term_months` instead of
`schedule_start`, `principal`, and `remaining_months`:

- Lines 866--877 (`extra_payment` mode): change `current_principal=principal` to
  `orig_principal`, `remaining_months=remaining_months` to `params.term_months`,
  `origination_date=schedule_start` to `params.origination_date`
- Lines 881--905 (chart schedules): same parameter changes; remove `schedule_start` variable
- Lines 948--958 (`target_date` mode): same parameter changes for `calculate_payoff_by_date`

---

## Files That Would Need to Change

1. `app/services/savings_dashboard_service.py` -- `_check_loan_paid_off()`: use
   `original_principal`, `term_months`, `origination_date`
2. `app/services/savings_dashboard_service.py` -- line 349: pass payments to
   `get_loan_projection()` (tracked separately)
3. `app/routes/loan.py` -- `payoff_calculate()`: use origination params instead of
   `schedule_start` / `current_principal` / `remaining_months`

---

## Tests That Should Verify the Remaining Fixes

1. **Unit: `_check_loan_paid_off()` with past confirmed payments** -- verify the function
   returns True when confirmed payments have driven the balance to zero. The current
   implementation cannot detect this because past payments never match.
2. **Unit: `_check_loan_paid_off()` with partial payoff** -- verify the function returns
   False when confirmed payments exist but balance remains positive.
3. **Unit: Payoff calculator `extra_payment` mode with confirmed payments** -- verify the
   summary reflects real principal from payment replay, not the static
   `current_principal`.
4. **Unit: Payoff calculator chart schedules with confirmed payments** -- verify committed
   and accelerated chart data incorporate past confirmed payments.
5. **Integration: Savings dashboard debt projections with payments** -- verify projected
   balances incorporate payment data (once fix #2 is implemented).

---

## Cross-Cutting Note: Other Account Types

**Savings and interest-bearing accounts are NOT affected by this root cause.** They use
`balance_calculator.calculate_balances()` or `calculate_balances_with_interest()`, which
process ALL transactions (including shadow transactions from transfers) period-by-period
from the anchor balance. Transfer status changes are correctly reflected via
`effective_amount`.

**Investment/retirement accounts have a distinct issue.** The savings dashboard
(`savings_dashboard_service.py:98--109`) loads shadow income transactions without status
eager-loading. `investment_projection.py:220--224` then accesses `txn.status` directly,
causing N+1 queries. This is a performance issue, not a correctness bug -- the data is
correct, but the queries are inefficient. Tracked separately.

**Debt accounts on the savings dashboard have two issues:**
1. `get_loan_projection()` is called without payments (remaining issue #2 above) --
   projections are contractual-only. Tracked separately per the user.
2. `_check_loan_paid_off()` uses the old pattern without origination params (remaining
   issue #1 above) -- cannot detect paid-off loans from past payments.
