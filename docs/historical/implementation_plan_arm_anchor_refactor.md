> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Fix: ARM Amortization -- Anchor System + Engine Refactor

## Context

The recent fix (d2455e8) changed the amortization engine to calculate from origination using
`original_principal`. This works for fixed-rate loans but breaks ARM loans where the user
hasn't entered historical rate data. Without past rates, the engine uses the current rate for
all months from origination, producing a wrong balance trajectory. The schedule-derived
"current balance" differs from the verified `current_principal` ($178,103.41). This cascades
to: Debt Progress card (impossible Dec 31 > Apr 11), savings dashboard, amortization schedule.

**Root cause:** For ARM loans without rate history, forward-from-origination is mathematically
impossible to get right.

**DRY problem:** The same "load loan data + generate projection" pattern is repeated in 5
places: `loan.py:_load_loan_context`, `savings_dashboard_service`, `year_end_summary_service`,
`debt_strategy`, and `loan.py:payoff_calculate`. Each independently loads payments, escrow,
rate history, and calls the engine.

**Engine redundancy:** `get_loan_projection()` calls both `calculate_summary()` (which
internally generates a schedule) and `generate_schedule()` separately -- generating the same
schedule twice.

---

## Part 1: Engine Changes -- `app/services/amortization_engine.py`

### 1A. Add anchor to `generate_schedule()` (line 326)

New parameters:
```python
anchor_balance: Decimal | None = None,
anchor_date: date | None = None,
```

New logic in the month loop, after `pay_date` is computed (after line 462), BEFORE the ARM
rate adjustment block (before line 464):

```python
# Anchor: reset balance to user-verified value at the transition
# from historical to forward projection.  Pre-anchor rows have
# approximate P&I splits; post-anchor rows are exact.
if (anchor_balance is not None and anchor_date is not None
        and not anchor_applied and pay_date > anchor_date):
    balance = anchor_balance
    anchor_applied = True
    months_left = max_months - month_num + 1
    monthly_payment = calculate_monthly_payment(
        balance, current_annual_rate, months_left,
    )
```

Initialize `anchor_applied = False` before the loop (~line 454). Update docstring.

### 1B. New helper: `_derive_summary_metrics()` (~line 588)

```python
def _derive_summary_metrics(
    schedule: list[AmortizationRow],
    origination_date: date,
) -> tuple[Decimal, date]:
    """Extract total interest and payoff date from a generated schedule."""
```

Returns `(total_interest, payoff_date)` from an already-generated schedule. Eliminates the
need for `get_loan_projection` to call `calculate_summary` (which regenerates the schedule).

### 1C. Add `current_balance` to `LoanProjection` (line 782)

```python
@dataclass
class LoanProjection:
    remaining_months: int
    summary: AmortizationSummary
    schedule: list  # list[AmortizationRow]
    current_balance: Decimal  # NEW: balance at today (anchor for ARM, schedule-derived for fixed)
```

### 1D. Refactor `get_loan_projection()` (line 789)

**Before (generates schedule 2x):**
```
get_loan_projection
  -> calculate_summary -> generate_schedule (1st time)
  -> generate_schedule (2nd time, same params)
```

**After (generates schedule 1x):**
```
get_loan_projection
  -> generate_schedule (once, with anchor for ARM)
  -> _derive_summary_metrics (from the generated schedule)
  -> build AmortizationSummary directly
```

Key changes:
- For ARM: pass `anchor_balance=current_principal, anchor_date=today`
- Compute `monthly_payment` directly: ARM uses `calculate_monthly_payment(current_principal,
  rate, remaining)`, fixed uses `calculate_monthly_payment(original_principal, rate, term)`
- Derive `total_interest` and `payoff_date` from schedule via `_derive_summary_metrics`
- Build `AmortizationSummary` with `_with_extra` fields equal to standard (no extra_monthly
  in this path -- the payoff calculator uses `calculate_summary` for that)
- Set `current_balance`: for ARM, `current_principal`; for fixed, walk schedule to today
- Remove the post-hoc ARM monthly_payment override (lines 876-884) -- no longer needed

### 1E. Add anchor to `calculate_summary()` (line 591)

Add `anchor_balance, anchor_date` params. Pass through to both `generate_schedule()` calls.
Kept for the payoff calculator's extra_monthly comparison (needs standard vs accelerated).

---

## Part 2: Data Loading Consolidation -- `app/services/loan_payment_service.py`

### 2A. Add `LoanContext` dataclass

```python
@dataclass
class LoanContext:
    """All context data needed for loan projection.

    Loaded once per account, shared across all projection consumers.
    """
    payments: list[PaymentRecord]
    rate_changes: list[RateChangeRecord] | None
    escrow_components: list  # list[EscrowComponent]
    monthly_escrow: Decimal
    contractual_pi: Decimal
    rate_history: list  # list[RateHistory] ORM objects for display
```

### 2B. Add `load_loan_context()` function

```python
def load_loan_context(
    account_id: int,
    scenario_id: int | None,
    loan_params: LoanParams,
) -> LoanContext:
    """Load and prepare all context data for a loan account.

    Consolidates the pattern repeated in loan routes, savings dashboard,
    year-end service, and debt strategy: payment history retrieval, escrow
    loading, payment preparation (escrow subtraction + biweekly fix), and
    rate change loading for ARM loans.
    """
```

This replaces the duplicated data loading in:
- `loan.py:_load_loan_context()` (lines 329-415)
- `savings_dashboard_service.py` (lines 358-395)
- `year_end_summary_service.py:_generate_debt_schedules()` (lines 1237-1282)
- `debt_strategy.py:_compute_real_principal()` (lines 165-186, partial)

New imports needed: `EscrowComponent`, `RateHistory`, `Scenario`, `RateChangeRecord`,
`escrow_calculator`.

---

## Part 3: Caller Updates

### 3A. `app/routes/loan.py` -- `_load_loan_context()` (line 329)

Replace the body with a call to `loan_payment_service.load_loan_context()`. Keep the
route-specific derived values (principal, rate, remaining, original_for_engine) computed
from LoanParams, and return them alongside the LoanContext fields. This function becomes a
thin wrapper.

### 3B. `app/routes/loan.py` -- `dashboard()` (line 431)

- Uses `get_loan_projection()` which now handles anchor internally. No change needed for
  the committed schedule (proj.schedule).
- **Floor schedule** (line 500): Pass anchor for ARM:
  ```python
  anchor_bal = Decimal(str(params.current_principal)) if params.is_arm else None
  anchor_dt = date.today() if params.is_arm else None
  ```
- **Original schedule** (line 479): NO anchor (contractual baseline).

### 3C. `app/routes/loan.py` -- `payoff_calculate()` (line 834)

- **extra_payment mode** `calculate_summary` call (line 865): Pass anchor for ARM.
- **Committed schedule** (line 888): Pass anchor for ARM.
- **Accelerated schedule** (line 898): Pass anchor for ARM.
- **Original schedule** (line 880): NO anchor.
- **target_date mode** (line 950): `get_loan_projection` now handles anchor automatically.
  The `real_principal` derivation (lines 957-961) simplifies: use `proj.current_balance`.

### 3D. `app/services/savings_dashboard_service.py` -- `_compute_account_projections()`

Replace the inline data loading (lines 358-395) with
`loan_payment_service.load_loan_context()`. Replace the schedule-walk current balance
derivation (lines 408-415) with `proj.current_balance` from `LoanProjection`.

`_check_loan_paid_off()`: NO changes -- intentionally replays from origination with
confirmed-only payments.

### 3E. `app/services/year_end_summary_service.py` -- `_generate_debt_schedules()`

Replace the inline data loading (lines 1237-1282) with
`loan_payment_service.load_loan_context()`. Pass anchor for ARM to `generate_schedule()`.

### 3F. `app/routes/debt_strategy.py` -- `_compute_real_principal()`

For ARM loans, return `principal` (which is `params.current_principal`) directly:
```python
if params.is_arm:
    return principal
```

Skip the payment replay entirely -- current_principal is the user-verified source of truth.

---

## What does NOT change

- `generate_schedule()` core loop logic (only one conditional block added)
- Fixed-rate loan behavior (no anchor, origination-forward works)
- `calculate_monthly_payment()`, `calculate_payoff_by_date()`
- Models, schemas, migrations (no new columns)
- Templates (schedule display works as-is with approximate historical rows)
- `_check_loan_paid_off()` (intentionally uses origination replay)

---

## Files Modified

| File | Changes |
|------|---------|
| `app/services/amortization_engine.py` | Anchor params on `generate_schedule`, `_derive_summary_metrics` helper, refactor `get_loan_projection` (1x schedule gen), `current_balance` on LoanProjection, anchor on `calculate_summary` |
| `app/services/loan_payment_service.py` | `LoanContext` dataclass, `load_loan_context()` function |
| `app/routes/loan.py` | Simplify `_load_loan_context()` to use shared loader, anchor on floor/payoff schedules |
| `app/services/savings_dashboard_service.py` | Use shared loader, use `proj.current_balance` |
| `app/services/year_end_summary_service.py` | Use shared loader, pass anchor for ARM |
| `app/routes/debt_strategy.py` | ARM returns current_principal directly |

---

## Verification

1. **Amortization schedule** (`/accounts/3/loan`): Forward rows project from $178,103.41.
   Balance decreases monotonically from today forward.

2. **Savings** (`/savings`): Mortgage shows $178,103.41. Projected balances decrease.

3. **Year-End Debt Progress** (`/analytics`): Dec 31 balance < current balance.
   Principal paid is positive.

4. **Payoff calculator**: Correct projections from current_principal.

5. **Debt strategy** (`/debt-strategy`): ARM mortgage uses current_principal.

6. **Tests**: `pytest tests/test_services/test_amortization_engine.py -v` and
   `pytest tests/test_routes/test_loan.py -v`. Existing tests pass (don't use anchor
   params). Add new tests for anchor behavior.

7. **Pylint**: `pylint app/ --fail-on=E,F` passes.
