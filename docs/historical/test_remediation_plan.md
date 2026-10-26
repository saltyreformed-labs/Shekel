> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Test Suite Remediation Plan

**Date:** 2026-03-15
**Reference:** `docs/test_audit_report.md`
**Estimated Total Effort:** ~35 hours across 8 phases, 24 work units
**Approach:** Each work unit is atomic -- completable in one session, independently valuable, followed by `pytest` to verify no regressions.

---

## Phase 0: Test Infrastructure Foundation

Everything in later phases depends on having solid shared fixtures and a reliable test harness. Do this first.

### Work Unit 0.1: Conftest Hardening

**File:** `tests/conftest.py`
**Addresses:** Audit report sections 52, Cross-Cutting Issues 9, 10
**Estimated time:** 45 minutes

#### Changes

**0.1a -- Assert login success in `auth_client` (line 243)**

The fixture currently fires-and-forgets the login POST. If login fails (e.g., due to a seed_user bug), every downstream test fails with misleading 302 redirects.

```python
# BEFORE (line 249-251):
client.post("/login", data={...})
return client

# AFTER:
resp = client.post("/login", data={...})
assert resp.status_code == 302, f"auth_client login failed: {resp.status_code}"
return client
```

**0.1b -- Add `second_user` fixture**

At least 5 test files (`test_transaction_auth.py`, `test_hostile_qa.py`, `test_audit_fixes.py`, `test_integrity_check.py`, `test_transfers.py`) independently create second users with varying implementations. Create one shared fixture.

```python
@pytest.fixture()
def second_user(app, db):
    """Create a second user for IDOR testing.

    Returns dict with keys: user, account, scenario, categories.
    """
    # Mirror seed_user structure but with different email/credentials
    # email: "other@shekel.local", password: "otherpass"
    # Create UserSettings, Account (type=checking), Scenario (baseline)
    # Return dict matching seed_user shape
```

Implementation notes:

- Mirror `seed_user` exactly (same keys, same structure) so IDOR tests can swap interchangeably.
- Do NOT create a `second_auth_client` -- IDOR tests should use `auth_client` (logged in as user 1) and attempt to access `second_user` resources.
- Place after the existing `seed_user` fixture.

**0.1c -- Add `seed_periods_52` fixture**

The existing `seed_periods` creates 10 periods. Multiple audit findings require 52+ periods for realistic coverage. Add a second fixture rather than modifying the existing one (avoids breaking ~200 existing tests).

```python
@pytest.fixture()
def seed_periods_52(app, db, seed_user):
    """Generate 52 pay periods (2-year projection) starting from 2026-01-02.

    Sets anchor to period[0]. Use for FIN tests requiring production-scale data.
    """
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=date(2026, 1, 2),
        num_periods=52,
        cadence_days=14,
    )
    account = seed_user["account"]
    account.current_anchor_period_id = periods[0].id
    db.session.commit()
    return periods
```

**Verification:** Run `pytest tests/conftest.py -v` (should have no test errors), then `pytest --co -q` to confirm fixture collection doesn't break anything.

---

## Phase 1: P0 Financial Exactness

The most critical gap. A budget app that can't prove penny-level accuracy across its projection window is unreliable. This phase replaces every directional/range assertion on financial values with exact Decimal comparisons and adds long-horizon accuracy tests.

### Work Unit 1.1: Balance Calculator -- 52-Period FIN Test

**File:** `tests/test_services/test_balance_calculator.py`
**Addresses:** Audit sections 1 (FIN MISSING), Cross-Cutting Issue 1
**Depends on:** Work Unit 0.1 (needs `seed_periods_52`)
**Estimated time:** 2 hours

#### New Tests

**`test_52_period_penny_accuracy`**

This is the single most important missing test. Build it as a pure-function test (no DB) for speed and determinism.

Approach:

1. Create 52 synthetic period objects (namedtuples or SimpleNamespace) with sequential IDs and 14-day date ranges starting 2026-01-02.
2. Create a mix of transactions per period:
   - 1 income (`type=income`, `status=projected`, amount=`Decimal("2500.00")`)
   - 3-5 expenses (`type=expense`, `status=projected`, varying amounts like `Decimal("850.00")`, `Decimal("125.50")`, `Decimal("67.23")`, `Decimal("43.99")`)
   - Sprinkle in status variations: some `done` (with `actual_amount`), some `cancelled` (should be excluded), some `credit` (should be excluded)
3. Set `anchor_balance = Decimal("3245.67")`, `anchor_period_id = periods[0].id`.
4. Call `calculate_balances(...)`.
5. **Hand-compute expected balances for all 52 periods using Python Decimal arithmetic in the test itself** -- a simple loop: `expected[0] = anchor + income - expenses` (for anchor period remaining), `expected[n] = expected[n-1] + income[n] - expenses[n]`.
6. Assert `result[period.id] == expected[i]` for every single period.

Key design decisions:

- Use deterministic amounts (no randomness) so the test is reproducible.
- Include at least one period with only cancelled/credit transactions (verifies zero net effect).
- Include at least one period with a `done` transaction where `actual_amount != estimated_amount` (verifies actual is used).
- The hand-computation loop in the test acts as an independent oracle -- if the service and the test agree on all 52 values, the implementation is correct.

**`test_negative_anchor_balance_overdraft`**

```python
anchor_balance = Decimal("-500.00")
# 3 periods, 1 income of 2500, 2 expenses of 850 each
# Expected: period 0 = -500 + 2500 - 850 - 850 = 300.00
#           period 1 = 300 + 2500 - 850 - 850 = 1100.00
#           period 2 = 1100 + 2500 - 850 - 850 = 1900.00
```

**`test_large_values_no_overflow`**

```python
anchor_balance = Decimal("999999.99")
# Transactions with large amounts: income of 50000, expenses of 49999.99
# Verify no overflow or precision loss at Numeric(12,2) boundary
```

**`test_idempotent_same_inputs_same_outputs`**

Call `calculate_balances` twice with identical inputs. Assert `result1 == result2`. This covers the IDEM gap.

---

### Work Unit 1.2: Balance Calculator Debt -- Exact Assertions

**File:** `tests/test_services/test_balance_calculator_debt.py`
**Addresses:** Audit section 2 (all findings)
**Estimated time:** 1.5 hours

#### Modify Existing Tests

**`test_debt_balance_with_payments` (line 48)**

Read the test setup to identify the exact loan parameters (principal, rate, term). Then hand-compute:

- `monthly_rate = annual_rate / 12`
- Period 1 `interest_portion = principal * monthly_rate`, quantized to `0.01` with `ROUND_HALF_UP`
- Period 1 `principal_portion = payment - interest_portion`
- Period 1 `new_principal = principal - principal_portion`

Replace:

```python
# BEFORE:
assert balances[2] < Decimal("100000.00")
assert principal_by_period[2] > Decimal("0.00")

# AFTER:
assert balances[2] == Decimal("<computed_exact_value>")
assert principal_by_period[2] == Decimal("<computed_exact_principal>")
```

**`test_debt_principal_tracking` (line 100)**

Same approach -- replace all `> Decimal("0.00")` and relative comparisons with exact values.

#### New Tests

**`test_debt_26_period_amortization_accuracy`**

26 periods (1 year of biweekly payments). For a known loan ($200k, 6.5%, 30yr), compute the first 26 monthly payments' interest/principal split using the amortization formula. Verify every period's principal_by_period matches. Verify final balance matches known amortization table value after 26 payments.

Note: since periods are biweekly but mortgage payments are monthly, only ~12 of the 26 periods will have payment transfers. This tests the interaction between biweekly periods and monthly payment schedules.

**`test_debt_zero_interest_rate`**

```python
# interest_rate=0, all payment goes to principal
# Every period: interest=0, principal=full_payment
# After N payments: remaining = original - (N * payment)
```

**`test_debt_zero_principal_paid_off`**

```python
# current_principal=0
# All balances should remain at 0
# No interest accrues
```

**`test_debt_overpayment_larger_than_remaining`**

```python
# remaining_principal = 500, payment = 600
# principal_portion should be capped at 500, not 600
# Balance should reach 0, not go negative
```

---

### Work Unit 1.3: Balance Calculator HYSA -- Exact Assertions

**File:** `tests/test_services/test_balance_calculator_hysa.py`
**Addresses:** Audit section 3 (all findings)
**Estimated time:** 1.5 hours

#### Modify Existing Tests

For each test with directional assertions, compute the exact expected interest using the `interest_projection.py` formula (which is already the gold standard in the codebase):

**`test_hysa_balance_includes_interest` (line 36)**

Given: $10,000 balance, 4.5% APY, daily compounding, 14-day period.

```python
daily_rate = Decimal("0.045") / Decimal("365")
interest = (Decimal("10000") * ((1 + daily_rate) ** 14 - 1)).quantize(
    Decimal("0.01"), rounding=ROUND_HALF_UP
)
# Compute exact value, assert ==
```

Replace `assert balances[1] > Decimal("10000.00")` with `assert balances[1] == Decimal("10000.00") + interest`.

**`test_hysa_interest_compounds_across_periods` (line 54)**

Compute interest for each period sequentially (period 2 compounds on period 1's end balance). Assert exact values for all 3 periods.

**`test_hysa_with_transfers` (line 72)**

Compute interest on the starting balance, then add the transfer effect, then compute next period's interest on the new balance. Assert exact.

**`test_interest_by_period_dict` (line 154)**

Add actual expected value assertions alongside the existing type/quantization checks.

#### New Tests

**`test_hysa_26_period_compounding_no_drift`**

26 periods at 4.5% APY daily compounding, starting from $10,000. Compute expected balance at each period using an independent Python Decimal loop. Verify all 26 balances match.

**`test_hysa_invalid_compounding_frequency`**

Pass `compounding_frequency="invalid_string"`. Verify behavior (should raise or default gracefully).

---

### Work Unit 1.4: Tax Calculator -- Exact Assertions

**File:** `tests/test_services/test_tax_calculator.py`
**Addresses:** Audit section 6 (7 range/directional assertions)
**Estimated time:** 1.5 hours

#### Modify 7 Tests

For each test, read the exact inputs, manually apply the IRS Pub 15-T formulas documented in the service, and compute the exact expected Decimal result.

**`test_weekly_pay_frequency` (line 141)**

Inputs: `gross_pay=Decimal("1153.85")`, `pay_periods=52`.

1. `annual_income = 1153.85 * 52 = 60000.20`
2. `standard_deduction` = (read from bracket_set fixture)
3. `taxable = annual_income - standard_deduction`
4. Apply marginal brackets iteratively
5. `per_period = annual_tax / 52`
6. Assert `result == Decimal("<exact>")` -- replace the `Decimal("108") < result < Decimal("111")` range.

Repeat for all 7 tests:

- `test_income_spans_all_brackets` (line 231)
- `test_very_high_income_top_bracket_only` (line 252)
- `test_income_exactly_at_first_bracket_top` (line 280)
- `test_income_one_dollar_into_next_bracket` (line 293)
- `test_child_credits_reduce_tax` (line 398)
- `test_other_dependent_credits` (line 415)

#### New Test

**`test_26_period_annual_withholding_matches_annual_tax`**

Compute `calculate_federal_withholding(...)` for a representative salary ($80,000/yr) for one period. Multiply by 26. Separately compute `calculate_federal_tax(80000, bracket_set)`. Assert the annual withholding equals the annual tax (they should be identical since the withholding formula is designed to produce this result for standard scenarios with no adjustments).

---

### Work Unit 1.5: Paycheck Calculator -- Exact Assertions + FICA Cap

**File:** `tests/test_services/test_paycheck_calculator.py`
**Addresses:** Audit section 4 (all findings)
**Depends on:** Work Unit 0.1 (needs `seed_periods_52`)
**Estimated time:** 2 hours

#### Modify Existing Test

**`test_basic_paycheck_no_deductions` (line 392)**

Replace 6 directional assertions with exact values. Given the test's salary profile and tax bracket fixtures, hand-compute:

```python
# BEFORE:
assert result.federal_tax > ZERO
assert result.state_tax > ZERO
...

# AFTER:
# gross_biweekly = annual_salary / 26, quantized
expected_gross = (Decimal("<annual>") / Decimal("26")).quantize(
    Decimal("0.01"), rounding=ROUND_HALF_UP
)
assert result.gross_biweekly == expected_gross

# Compute expected federal using the tax bracket fixture
# Compute expected state using the state config fixture
# Compute expected SS = min(gross, remaining_cap) * ss_rate
# Compute expected Medicare = gross * medicare_rate
assert result.federal_tax == Decimal("<exact>")
assert result.state_tax == Decimal("<exact>")
...
```

This requires reading the test's fixture setup to know the exact tax brackets. If the fixtures don't seed tax brackets (which seems likely given that the audit noted "tax brackets are not seeded"), add bracket seeding to this test or create a `seed_tax_brackets` fixture.

#### New Tests

**`test_fica_ss_wage_cap_boundary`**

The single most important missing paycheck test. Set up a high-salary profile where cumulative wages cross the SS wage base mid-year.

```python
# salary = $200,000/yr → biweekly gross ≈ $7,692.31
# SS wage base (2026) = $168,600 (read from fica_config fixture)
# At period 22: cumulative ≈ 22 * 7692.31 = $169,230.82 → exceeds cap
# Period 21: cumulative ≈ $161,538.51 → SS applies to full gross
# Period 22: cumulative would exceed cap → SS applies only to remainder
# Period 23+: cumulative > cap → SS = 0

# Use project_salary() with 26 periods
# Assert: periods 1-21 have SS > 0
# Assert: period 22 has SS = (168600 - 161538.51) * ss_rate (exact)
# Assert: periods 23-26 have SS == 0
# Assert: Medicare continues on all 26 periods (no cap)
```

**`test_26_period_annual_net_pay_sum`**

```python
# Run project_salary() for 26 periods with a known salary
# Sum all net_pay values
# Independently compute: annual_gross - annual_fed - annual_state
#   - annual_ss - annual_medicare - annual_deductions
# Assert sums match to the penny (may differ by up to 26 * 0.01 = $0.26
#   due to per-period rounding, so assert within that tolerance)
```

Note: per-period rounding means the sum of 26 rounded values may differ slightly from rounding the annual value. The test should compute the expected sum by summing 26 independently-computed per-period values, not by dividing the annual amount.

**`test_zero_salary`**

```python
# annual_salary = Decimal("0")
# All outputs should be Decimal("0.00"): gross, taxes, net
```

**`test_negative_salary_rejected`**

```python
# annual_salary = Decimal("-50000")
# Should raise ValueError or return all zeros (verify actual behavior first)
```

---

### Work Unit 1.6: Recurrence Engine -- Safety Guards

**File:** `tests/test_services/test_recurrence_engine.py`
**Addresses:** Audit section 5 (all findings)
**Estimated time:** 1 hour

#### New Tests

**`test_every_n_periods_interval_zero_raises`** -- HIGHEST PRIORITY in this unit

```python
# interval_n=0 in every_n_periods pattern
# This MUST raise ValueError (or be guarded in the service).
# If the service doesn't guard against this, this test will hang
#   (infinite loop) or crash (ZeroDivisionError).
# Write the test first, run it. If it hangs/crashes, file a bug
#   and add the guard to the service code.
```

**If the service doesn't guard `interval_n=0`**, add a guard in `recurrence_engine.py`:

```python
if rule.interval_n is not None and rule.interval_n <= 0:
    return []  # or raise ValueError
```

**`test_day_of_month_zero_returns_empty`**

```python
# monthly pattern with day_of_month=0
# Should return empty (no day 0 exists) or raise
```

**`test_day_of_month_32_returns_empty`**

```python
# monthly pattern with day_of_month=32
# No month has 32 days → should return empty or clamp
```

**`test_month_of_year_zero_quarterly`**

```python
# quarterly with start_month=0 (invalid)
# Should raise or handle gracefully
```

**`test_month_of_year_13_annual`**

```python
# annual with month_of_year=13
# Should raise or handle gracefully
```

**`test_every_period_verifies_period_id_assignment`**

Strengthen existing `test_every_period_generates_for_all` (line 117):

```python
# After existing assertions, add:
period_ids = {txn.pay_period_id for txn in created}
expected_ids = {p.id for p in seed_periods}
assert period_ids == expected_ids  # 1:1 mapping
```

**`test_cross_user_isolation`** (IDOR)

```python
# Create template owned by user A
# Call generate_for_template with user B's scenario_id and periods
# Verify no transactions are created in user B's scenario
# (This may require reading how scenario_id is used in generation)
```

---

### Work Unit 1.7: Growth Engine, Pension, Amortization -- Exact Assertions

**Files:** `test_growth_engine.py`, `test_pension_calculator.py`, `test_amortization_engine.py`, `test_retirement_gap_calculator.py`
**Addresses:** Audit sections 30, 32, 35, 36
**Estimated time:** 1.5 hours

#### Growth Engine -- 3 tests to fix

**`test_basic_growth_no_contributions` (line 120)**

Given: $10,000 balance, known annual return, 14-day period.

```python
period_return = (Decimal("1") + annual_return) ** (Decimal("14") / Decimal("365")) - Decimal("1")
expected_growth = (Decimal("10000") * period_return).quantize(Decimal("0.01"), ROUND_HALF_UP)
assert result[0].growth == expected_growth
assert result[0].end_balance == Decimal("10000") + expected_growth
```

**`test_negative_return_rate` (line 264)** -- same approach, exact negative growth.

**`test_with_periodic_contributions` (line 145)** -- compute each period's balance incrementally (start + growth + contribution), assert final exactly.

#### Pension Calculator -- 2 tests to fix

**`test_very_short_service` (line 126)**

```python
# 5 months of service → years = 5/12 = 0.416...
# Compute exact: Decimal("5") / Decimal("12"), quantized
assert result.years_of_service == Decimal("<exact>")
# benefit = salary * multiplier * years, quantized
assert result.annual_benefit == Decimal("<exact>")
```

**`test_with_recurring_raise` (line 187)**

```python
# Year 1: 80000 * 1.03 = 82400.00
# Year 2: 82400 * 1.03 = 84872.00
assert result[0][1] == Decimal("82400.00")
assert result[1][1] == Decimal("84872.00")
```

#### Amortization Engine -- 2 tests to fix + 1 missing function

**`test_achievable_target` (line 266)** -- compute exact extra monthly payment.

**`test_summary_with_extra` (line 233)** -- compute exact months_saved and interest_saved.

**`test_calculate_remaining_months`** -- NEW (zero coverage):

```python
def test_remaining_months_basic():
    result = calculate_remaining_months(date(2020, 1, 1), 360, as_of=date(2025, 1, 1))
    assert result == 300  # 360 - 60

def test_remaining_months_past_term():
    result = calculate_remaining_months(date(2020, 1, 1), 12, as_of=date(2025, 1, 1))
    assert result == 0  # max(0, 12 - 60)

def test_remaining_months_none_as_of():
    # Uses date.today(), just verify it returns an int >= 0
    result = calculate_remaining_months(date(2020, 1, 1), 360)
    assert isinstance(result, int)
    assert result >= 0
```

#### Retirement Gap Calculator -- 2 fixes

**`test_surplus` (line 21)** -- rename to `test_shortfall_when_savings_insufficient`. Fix docstring.

**`test_after_tax_view_traditional` (line 83)** -- replace `is not None` with exact computed value.

---

## Phase 2: Security Hardening

### Work Unit 2.1: Unauthenticated Access Tests

**File:** New file `tests/test_routes/test_auth_required.py`
**Addresses:** Cross-Cutting Issue 3
**Estimated time:** 1.5 hours

#### Approach

Instead of adding auth tests to each of 11+ files, create one centralized parametrized test that covers all protected endpoints. This is DRY, easy to maintain, and impossible to forget when adding new routes.

```python
"""Verify all protected endpoints redirect unauthenticated users to /login."""
import pytest

# Every endpoint that requires login, grouped by HTTP method
PROTECTED_ENDPOINTS = [
    # Grid & Transactions
    ("GET", "/"),
    ("GET", "/grid/balance-row"),
    ("POST", "/transactions/create"),
    # Accounts
    ("GET", "/accounts"),
    ("GET", "/accounts/new"),
    ("POST", "/accounts"),
    # Salary
    ("GET", "/salary"),
    ("GET", "/salary/new"),
    ("POST", "/salary"),
    ("GET", "/salary/tax-config"),
    # Transfers
    ("GET", "/transfers"),
    ("GET", "/transfers/new"),
    ("POST", "/transfers"),
    # Savings
    ("GET", "/savings"),
    ("GET", "/savings/goals/new"),
    ("POST", "/savings/goals"),
    # Templates
    ("GET", "/templates"),
    ("GET", "/templates/new"),
    ("POST", "/templates"),
    # Categories
    ("GET", "/categories"),
    ("POST", "/categories"),
    # Pay Periods
    ("GET", "/pay-periods/generate"),
    ("POST", "/pay-periods/generate"),
    # Settings
    ("GET", "/settings"),
    ("POST", "/settings"),
    # Charts
    ("GET", "/charts"),
    ("GET", "/charts/fragment/spending"),
    # Investment / Mortgage / HYSA / Auto Loan / Retirement
    ("GET", "/retirement"),
    ("POST", "/retirement/pension"),
    ("POST", "/retirement/settings"),
    # Add more as needed...
]


@pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
def test_unauthenticated_redirects_to_login(client, method, path):
    """Every protected endpoint must redirect to /login when not authenticated."""
    if method == "GET":
        resp = client.get(path)
    elif method == "POST":
        resp = client.post(path, data={})
    elif method == "PATCH":
        resp = client.patch(path, data={})
    elif method == "DELETE":
        resp = client.delete(path)

    assert resp.status_code in (302, 303), f"{method} {path} returned {resp.status_code}"
    assert "/login" in resp.headers.get("Location", ""), \
        f"{method} {path} did not redirect to /login"
```

Implementation notes:

- Populate `PROTECTED_ENDPOINTS` by reading each blueprint's route registrations. Be exhaustive.
- Use the unauthenticated `client` fixture (not `auth_client`).
- This single file replaces the need for individual auth tests in 11+ files.
- When new routes are added in the future, add them to this list.

---

### Work Unit 2.2: IDOR Tests -- DB State Verification

**Files:** `test_auto_loan.py`, `test_hysa.py`, `test_investment.py`, `test_mortgage.py`, `test_retirement.py`
**Addresses:** Cross-Cutting Issue 4
**Depends on:** Work Unit 0.1 (`second_user` fixture)
**Estimated time:** 1.5 hours

#### Pattern

Every IDOR test that currently only asserts `status_code == 302` needs a DB query to prove no state change. The pattern:

```python
def test_params_update_idor(self, auth_client, second_user, db):
    other_acct = second_user["account"]
    original_value = other_acct.some_field  # Snapshot BEFORE

    resp = auth_client.post(f"/accounts/{other_acct.id}/params", data={
        "some_field": "hacked_value"
    })
    assert resp.status_code == 302

    # Refresh from DB and verify unchanged
    db.session.refresh(other_acct)
    assert other_acct.some_field == original_value  # MUST be unchanged
```

#### Files and specific tests to fix

**`test_auto_loan.py`:**

- `test_dashboard_idor` (line 70): Add `assert "/login" in resp.headers["Location"]` or verify redirect target.
- `test_params_update_idor` (line 140): Snapshot `AutoLoanParams` before, verify unchanged after.
- `test_params_update_validation` (line 131): Verify DB was NOT changed (query `AutoLoanParams`, assert original `payment_day`).

**`test_hysa.py`:**

- `test_hysa_detail_idor` (line 47): Verify redirect Location.
- `test_hysa_params_update_idor` (line 125): Snapshot `HysaParams.apy` before, verify unchanged after.

**`test_investment.py`:**

- `test_dashboard_idor` (line 83): Verify redirect Location.
- `test_params_idor` (line 182): Verify no `InvestmentParams` created on other account.
- `test_validation_error` (line 203): Verify no `InvestmentParams` created.

**`test_mortgage.py`:**

- `test_dashboard_idor` (line 70): Verify redirect Location.
- `test_params_update_idor` (line 147): Snapshot `MortgageParams` before, verify unchanged after.
- `test_params_update_validation` (line 138): Verify DB unchanged.

**`test_retirement.py`:**

- `test_edit_pension_idor` (line 215): Verify redirect, verify pension data unchanged.
- `test_update_settings_partial` (line 266): Verify SWR was actually saved (currently doesn't).

Also fix in **`test_transaction_auth.py`:**

- `test_mark_credit_blocked` (line 153): Add DB check that transaction status is unchanged.
- `test_unmark_credit_blocked` (line 185): Add DB check.

---

### Work Unit 2.3: IntegrityError Handling (Application Code Fix)

**Files:** `app/routes/transfers.py`, `app/routes/savings.py`, plus their test files
**Addresses:** Cross-Cutting Issue 5, Audit sections 11, 12
**Estimated time:** 1 hour

#### Application Code Changes

**`app/routes/transfers.py` -- `create_transfer_template()`**

Wrap the commit in a try/except:

```python
try:
    db.session.commit()
except IntegrityError:
    db.session.rollback()
    flash("A transfer template with that name already exists.", "warning")
    return redirect(url_for("transfers.list_templates"))
```

**`app/routes/savings.py` -- `create_goal()`**

Same pattern:

```python
try:
    db.session.commit()
except IntegrityError:
    db.session.rollback()
    flash("A goal with that name already exists for this account.", "warning")
    return redirect(url_for("savings.dashboard"))
```

#### Test Changes

**`test_transfers.py` -- `test_create_template_double_submit` (line 257)**

Replace `pytest.raises(IntegrityError)` with:

```python
# First submit succeeds
resp1 = auth_client.post("/transfers", data=form_data)
assert resp1.status_code == 302

# Second submit -- now handled gracefully
resp2 = auth_client.post("/transfers", data=form_data)
assert resp2.status_code == 302  # Redirects instead of 500

# Verify only 1 template exists
count = db.session.query(TransferTemplate).filter_by(name=form_data["name"]).count()
assert count == 1

# Verify flash warning (follow redirect)
resp3 = auth_client.get(resp2.headers["Location"])
assert b"already exists" in resp3.data
```

**`test_savings.py` -- `test_duplicate_goal_name_same_account` (line 613)**

Same pattern -- replace `pytest.raises(IntegrityError)` with graceful redirect + flash assertion.

**`test_audit_fixes.py` -- lines 452, 476**

Replace `pytest.raises(Exception)` with `pytest.raises(IntegrityError)` for precision. These test the DB constraint directly (not via routes), so they should still raise.

---

## Phase 3: Assertion Quality

Make existing tests meaningful. Every assertion should prove something specific.

### Work Unit 3.1: Shallow Route Assertions -- P1 Routes

**Files:** `test_accounts.py`, `test_salary.py`, `test_transfers.py`, `test_savings.py`, `test_grid.py`
**Addresses:** Audit sections 8, 10, 11, 12, 21 (assertion depth)
**Estimated time:** 2 hours

#### Pattern

For each shallow test, add at least one content assertion that proves the response contains the expected data. Choose assertions that are stable (won't break on minor UI changes) but specific (won't pass on a wrong page).

**`test_accounts.py`:**

- `test_new_account_form_renders` (line 63): Replace `b"form"` with `b"account_name"` or `b"anchor_balance"` (actual form field names from the template).
- `test_inline_anchor_form_returns_partial` (line 346): Assert response contains an `<input` element or the current anchor value.
- `test_inline_anchor_display_returns_partial` (line 359): Assert response contains a dollar sign or the balance value.

**`test_salary.py`:**

- `test_new_profile_form` (line 174): Replace `b"form"` with `b"annual_salary"` or `b"filing_status"`.
- `test_add_raise_htmx_returns_partial` (line 426): Assert response contains the raise data (percentage or amount).
- `test_add_deduction_htmx_returns_partial` (line 621): Assert response contains deduction name.
- `test_projection_renders` (line 701): Assert response contains at least one pay period date or net pay value.
- `test_breakdown_renders` (line 679): Assert response contains `b"gross"` or `b"net_pay"` (breakdown-specific terms).

**`test_transfers.py`:**

- `test_new_template_form` (line 176): Replace `b"form"` with `b"default_amount"` or `b"from_account"`.
- `test_get_cell` (line 398): Assert response contains the transfer name or amount.
- `test_get_quick_edit` (line 408): Assert response contains an input element.
- `test_get_full_edit` (line 418): Assert response contains the transfer name.
- `test_create_ad_hoc_validation_error` (line 587): Assert response contains error message text.

**`test_savings.py`:**

- `test_new_goal_form` (line 406): Assert `b"target_amount"` or `b"Create Goal"`.
- `test_dashboard_no_savings_accounts` (line 210): Assert empty-state text.

**`test_grid.py`:**

- `test_grid_period_controls` (line 41): Assert response contains period date strings.
- `test_balance_row_returns_partial` (line 53): Assert response contains a balance-related CSS class or dollar value.
- `test_balance_row_custom_offset` (line 67): Assert different period dates than offset=0.
- `test_grid_periods_large_value` (line 74): Assert response contains period data.
- `test_create_transaction` (line 99): Add DB query to verify transaction was persisted.

Implementation approach:

- Before modifying each test, read the corresponding template file to identify stable identifiers (field names, CSS classes, heading text).
- Prefer asserting on `name="field_name"` attributes or heading text over CSS classes (less likely to change).

---

### Work Unit 3.2: Shallow Route Assertions -- P2/P3 Routes

**Files:** `test_templates.py`, `test_categories.py`, `test_pay_periods.py`, `test_settings.py`, `test_charts.py`, `test_mortgage.py`, `test_hysa.py`, `test_auto_loan.py`, `test_investment.py`, `test_retirement.py`, `test_onboarding.py`, `test_transaction_auth.py`, `test_idempotency.py`, `test_hostile_qa.py`
**Addresses:** Audit sections 17-23, 28, 37-42, 47
**Estimated time:** 2 hours

#### Changes by file (apply same pattern as 3.1)

**`test_templates.py`:** Fix `test_new_template_form` (163), `test_list_templates_empty` (150).

**`test_categories.py`:** Fix `test_create_category_htmx_validation_error` (127) -- assert JSON error body.

**`test_pay_periods.py`:** Fix `test_generate_missing_start_date` (46), `test_generate_cadence_zero` (55) -- assert error message content.

**`test_settings.py`:** Fix `test_settings_page_renders` (22) -- assert form fields. Fix dashboard section tests (184-256) -- assert section-specific content.

**`test_charts.py`:** Fix 5 redirect tests -- assert Location header. Fix `test_spending_fragment_period_params` (199).

**`test_mortgage.py`:** Fix 5 shallow tests -- add DB verification and Location checks.

**`test_hysa.py`:** Fix `test_hysa_detail_idor` (47) -- add Location check. Fix `test_create_hysa_account_auto_params` (157) -- strengthen `is not None`.

**`test_auto_loan.py`:** Fix `test_create_auto_loan_account` (162) -- query DB to verify creation.

**`test_investment.py`:** Fix `test_dashboard_brokerage` (103) -- assert body content.

**`test_retirement.py` (highest volume):**

- `test_dashboard_empty` (138): Assert empty-state text.
- `test_dashboard_with_pension` (143): Assert `b"State Pension"` or pension name.
- `test_pension_list` (160): Assert pension name in list.
- `test_edit_pension_form` (184): Assert form pre-populated.
- `test_update_settings_partial` (266): Query DB, assert SWR was saved.
- `test_dashboard_projects_multiple_accounts` (358): Assert both account names.
- `test_gap_with_swr_param` (425): Replace `b"3"` with `b"3.0%"` or specific element.
- `test_gap_returns_fragment` (416): Replace `or` assertion with single deterministic check.

**`test_onboarding.py`:**

- `test_banner_not_shown_to_anonymous_user` (77): Assert Location contains `/login`.
- `test_banner_shows_checkmarks_for_completed_steps` (62): Replace `or` with definitive assertion.

**`test_transaction_auth.py`:**

- `test_get_cell_blocked` (107), `test_quick_edit_blocked` (113), `test_full_edit_blocked` (121): Assert response does not contain the other user's transaction name.
- `test_mark_credit_blocked` (153), `test_unmark_credit_blocked` (185): Add DB state verification.

**`test_idempotency.py`:**

- `test_double_login_succeeds` (85): After both logins, GET a protected page and assert 200.

**`test_hostile_qa.py`:**

- `test_grid_negative_periods_param` (467), `test_grid_extreme_periods_param` (478): Assert grid HTML rendered.
- `test_delete_account_with_transfers_blocked` (192), `test_delete_category_with_transactions_blocked` (211): Assert flash message.
- `test_access_other_users_account` (762): Assert no data leaked in response body.

---

### Work Unit 3.3: Assertion Smells -- Exact Counts and Values

**Files:** Multiple (see list below)
**Addresses:** Cross-Cutting Issues 6, 7; Audit sections 16, 24, 27, 29, 46, 49
**Estimated time:** 1.5 hours

This work unit is a sweep through all files fixing three specific smell patterns.

#### Pattern A: Replace `len > 0` / `>= 1` with exact expected counts

| File                      | Line(s)       | Fix                                                                         |
| ------------------------- | ------------- | --------------------------------------------------------------------------- |
| `test_savings.py`         | 275, 315, 386 | Count expected growth projection points from seeded data                    |
| `test_templates.py`       | 219           | `len(txns) > 0` → `len(txns) == len(seed_periods)` (every_period pattern)   |
| `test_templates.py`       | 475           | `active_txns > 0` → exact count matching `seed_periods`                     |
| `test_workflows.py`       | 108           | `len(txns) >= 1` → compute exact monthly hit count from 10 biweekly periods |
| `test_audit_triggers.py`  | 5 tests       | `len(rows) >= 1` → `len(rows) == 1` (exactly 1 record created)              |
| `test_integrity_check.py` | 11 tests      | `detail_count >= 1` → `detail_count == 1`                                   |

#### Pattern B: Replace `is not None` with specific values

| File                          | Test                               | Fix                                                             |
| ----------------------------- | ---------------------------------- | --------------------------------------------------------------- |
| `test_accounts.py:487`        | `test_create_account_type`         | `acct_type.name == "investment"`                                |
| `test_grid.py:246`            | `test_mark_credit_creates_payback` | Assert `payback.estimated_amount`, `payback.status.name`        |
| `test_credit_workflow.py:137` | `test_auto_creates_cc_category`    | Already followed by `.id` check -- remove the `is not None` line |
| `test_mfa_service.py:17`      | `test_generate_secret`             | `len(secret) == 32`                                             |
| `test_auth.py:202`            | `test_invalidate_sessions`         | Assert timestamp within 5 seconds of now                        |
| `test_auth.py:247`            | `test_password_change_invalidates` | Same timestamp check                                            |
| `test_audit_triggers.py:283`  | `test_executed_at_is_populated`    | Verify timestamp recent                                         |
| `test_audit_triggers.py:292`  | `test_db_user_is_populated`        | Assert matches expected PG role                                 |

#### Pattern C: Replace `b"form"` with specific content

| File                | Line | Fix                                            |
| ------------------- | ---- | ---------------------------------------------- |
| `test_accounts.py`  | 67   | `b"account_name"` or `b"anchor_balance"`       |
| `test_salary.py`    | 177  | `b"annual_salary"` or `b"filing_status"`       |
| `test_transfers.py` | 180  | `b"default_amount"` or `b"from_account"`       |
| `test_templates.py` | 167  | `b"recurrence_pattern"` or `b"default_amount"` |

Note: Pattern C overlaps with Work Unit 3.1. If already fixed there, skip here.

---

## Phase 4: Missing Negative Paths -- P0/P1 Services

### Work Unit 4.1: P0 Service Negative Paths

**Files:** `test_balance_calculator.py`, `test_paycheck_calculator.py`, `test_recurrence_engine.py`, `test_tax_calculator.py`, `test_transfer_recurrence.py`
**Addresses:** Audit sections 1, 4, 5, 6, 7 (negative paths)
**Estimated time:** 2 hours

#### Balance Calculator

```python
def test_zero_estimated_amount():
    """Transaction with amount=0 should not affect balance."""
    # Create transaction with estimated_amount=Decimal("0")
    # Balance should equal anchor_balance + other txns only

def test_received_status_excluded_from_anchor_remaining():
    """Standalone test for 'received' status exclusion."""
    # Create 'received' income in anchor period
    # Verify it's excluded from remaining calculation
```

#### Paycheck Calculator

```python
def test_pay_periods_per_year_zero_raises():
    """Division by zero must be caught."""
    # profile.pay_periods_per_year = 0
    # Should raise ValueError or return gracefully

def test_net_pay_negative_from_excessive_post_tax():
    """Post-tax deductions exceeding take-home produce negative net."""
    # gross = 2000, pre_tax = 500, taxes = 500, post_tax = 1500
    # net = 2000 - 500 - 500 - 1500 = -500
    # Verify the calculator handles this (returns negative or floors at 0)
```

#### Tax Calculator

```python
def test_pay_periods_one_annual_lump():
    """Annual pay frequency (pay_periods=1) computes correctly."""
    # gross = annual salary, periods = 1
    # withholding = annual tax (no per-period division rounding)
```

#### Transfer Recurrence

```python
def test_zero_amount_transfer():
    """Transfer with default_amount=0 should still generate."""
    # Verify behavior (probably creates transfers with amount=0)

def test_negative_amount_rejected():
    """Negative default_amount should raise or be rejected."""

def test_self_transfer_from_equals_to():
    """from_account_id == to_account_id should be rejected."""
    # This may be caught at schema level; verify at service level too
```

---

### Work Unit 4.2: P1 Service + Schema Negative Paths

**Files:** `test_pay_period_service.py`, `test_savings_goal_service.py`, `test_credit_workflow.py`, `test_auth_service.py`, `test_mfa_service.py`, `test_validation.py`
**Addresses:** Audit sections 13, 14, 15, 25, 26, 27
**Estimated time:** 2 hours

#### Pay Period Service

```python
def test_negative_num_periods():
    # num_periods = -1 → should return empty or raise

def test_boundary_date_first_day_of_period():
    # get_current_period with as_of = period.start_date → should return that period

def test_boundary_date_last_day_of_period():
    # get_current_period with as_of = period.end_date → should return that period
```

#### Savings Goal Service

```python
def test_required_contribution_none_balance():
    # current_balance = None → should handle (return None or treat as 0)

def test_metrics_negative_expenses():
    # average_monthly_expenses = Decimal("-100") → should handle
```

#### Credit Workflow

```python
def test_mark_credit_nonexistent_transaction():
    # transaction_id = 999999 → should raise NotFoundError

def test_unmark_credit_on_projected_transaction():
    # Transaction was never marked as credit → should raise or no-op

def test_double_mark_as_credit():
    # mark_as_credit twice → should return existing payback, not create duplicate

def test_carry_forward_same_period():
    # source_period_id == target_period_id → should raise or no-op
```

#### Auth Service

```python
def test_hash_password_empty_string():
    # hash_password("") → should work (bcrypt hashes empty strings)

def test_authenticate_none_email():
    # email=None → should raise AuthError, not crash

def test_authenticate_none_password():
    # password=None → should raise AuthError, not crash

def test_hash_password_long_bcrypt_limit():
    # 73+ byte password → bcrypt silently truncates at 72 bytes
    # Verify hash_password(long) works without error
    # Verify verify_password(long, hash) returns True
```

#### MFA Service

```python
def test_decrypt_corrupted_ciphertext():
    # decrypt_secret("not-valid-fernet-token") → should raise

def test_verify_totp_wrong_length():
    # verify_totp_code(secret, "12345") → should return False (5 digits)

def test_verify_totp_non_numeric():
    # verify_totp_code(secret, "abcdef") → should return False

def test_generate_backup_codes_zero():
    # generate_backup_codes(0) → should return empty list
```

#### Schema Validation

```python
def test_transaction_create_excessive_decimals():
    # estimated_amount = "100.12345" → should reject or truncate

def test_template_create_day_32():
    # day_of_month = 32 → should reject (Range max=31)

def test_transfer_create_negative_amount():
    # amount = "-100" → should reject

def test_salary_profile_empty_state_code():
    # state_code = "" → should reject

def test_fica_config_rate_over_one():
    # ss_rate = "2.0" → should reject (200% rate is nonsensical)

def test_transaction_create_xss_in_name():
    # name = '<script>alert(1)</script>'
    # Should either reject or accept (then verify output escaping in template)
```

---

## Phase 5: Missing Negative Paths -- Routes

### Work Unit 5.1: P1 Route Negative Paths

**Files:** `test_accounts.py`, `test_salary.py`, `test_transfers.py`, `test_savings.py`
**Addresses:** Audit sections 8, 10, 11, 12 (negative paths)
**Estimated time:** 2 hours

#### Account Routes

```python
def test_edit_nonexistent_account():
    resp = auth_client.get("/accounts/999999/edit")
    assert resp.status_code in (302, 404)

def test_update_nonexistent_account():
    resp = auth_client.post("/accounts/999999", data={...})
    assert resp.status_code in (302, 404)

def test_reactivate_other_users_account():
    resp = auth_client.post(f"/accounts/{other_acct.id}/reactivate")
    assert resp.status_code == 302
    db.session.refresh(other_acct)
    assert other_acct.is_active is False  # Still deactivated

def test_deactivate_already_inactive():
    # Deactivate, then deactivate again → should handle gracefully
```

#### Salary Routes

```python
def test_edit_nonexistent_profile():
    resp = auth_client.get("/salary/999999/edit")
    assert resp.status_code in (302, 404)

def test_add_raise_to_other_users_profile():
    resp = auth_client.post(f"/salary/{other_profile.id}/raises", data={...})
    assert resp.status_code == 302
    # Verify no raise was created on other user's profile

def test_add_deduction_to_other_users_profile():
    # Same pattern

def test_update_state_tax_idor():
    # Post state tax config referencing another user's state
    # (if tax config is user-scoped)
```

#### Transfer Routes

```python
def test_update_nonexistent_transfer():
    resp = auth_client.patch("/transfers/instance/999999", data={...})
    assert resp.status_code == 404

def test_mark_done_already_done():
    # Mark done, then mark done again → should be idempotent or reject

def test_cancel_already_cancelled():
    # Cancel, then cancel again

def test_quick_edit_other_users_transfer():
    resp = auth_client.get(f"/transfers/quick-edit/{other_transfer.id}")
    assert resp.status_code == 404

def test_full_edit_other_users_transfer():
    resp = auth_client.get(f"/transfers/{other_transfer.id}/full-edit")
    assert resp.status_code == 404
```

#### Savings Routes

```python
def test_create_goal_on_deactivated_account():
    # Deactivate account, then try to create goal → should reject

def test_update_goal_account_idor():
    # Update goal changing account_id to other user's account → should reject
```

---

### Work Unit 5.2: Grid & Transaction Negative Paths

**Files:** `test_grid.py`, `test_transaction_auth.py`
**Addresses:** Audit sections 21, 22 (negative paths)
**Estimated time:** 1.5 hours

#### Grid Routes

```python
def test_update_nonexistent_transaction():
    resp = auth_client.patch("/transactions/999999/update", data={...})
    assert resp.status_code == 404

def test_mark_done_nonexistent():
    resp = auth_client.post("/transactions/999999/mark-done")
    assert resp.status_code == 404

def test_delete_nonexistent():
    resp = auth_client.delete("/transactions/999999")
    assert resp.status_code == 404

def test_create_transaction_missing_name():
    resp = auth_client.post("/transactions/create", data={
        "estimated_amount": "100", "pay_period_id": period.id
        # name is missing
    })
    assert resp.status_code in (400, 422)

def test_create_transaction_negative_amount():
    resp = auth_client.post("/transactions/create", data={
        "name": "Bad", "estimated_amount": "-100", ...
    })
    assert resp.status_code in (400, 422)

def test_mark_done_already_done_idempotent():
    # Mark done, then mark done again → verify no error and status unchanged

def test_cancel_already_cancelled_idempotent():
    # Same pattern
```

#### Transaction Auth

```python
def test_create_with_other_users_scenario_id():
    # POST create transaction with scenario_id belonging to second_user
    # Should reject
```

---

### Work Unit 5.3: P2 Core Route Negative Paths

**Files:** `test_templates.py`, `test_categories.py`, `test_pay_periods.py`, `test_settings.py`, `test_charts.py`
**Addresses:** Audit sections 17-20, 23
**Estimated time:** 1.5 hours

#### Templates

```python
def test_delete_already_deactivated_template():
    # Deactivate, then deactivate again → should handle gracefully

def test_reactivate_already_active_template():
    # Template is active, reactivate → should handle gracefully

def test_create_template_double_submit():
    # Create same template twice rapidly → should handle (no unique constraint on templates)
```

#### Categories

```python
def test_create_category_double_submit():
    # Same group+item twice → second should get duplicate flash

def test_create_category_max_length_name():
    # 500-char group_name → should reject or truncate
```

#### Pay Periods

```python
def test_generate_invalid_date_format():
    resp = auth_client.post("/pay-periods/generate", data={
        "start_date": "not-a-date", "num_periods": "10", "cadence_days": "14"
    })
    assert resp.status_code == 422

def test_generate_negative_num_periods():
    resp = auth_client.post("/pay-periods/generate", data={
        "start_date": "2026-01-02", "num_periods": "-5", "cadence_days": "14"
    })
    assert resp.status_code == 422
```

#### Settings

```python
def test_grid_account_idor():
    # Set grid_default_account_id to second_user's account
    # Should reject (not owned by current user)

def test_negative_grid_periods():
    resp = auth_client.post("/settings", data={"grid_default_periods": "-5"})
    assert resp.status_code == 302
    # Verify setting was NOT saved as -5
```

---

### Work Unit 5.4: P2 Financial Account Route Negative Paths

**Files:** `test_mortgage.py`, `test_hysa.py`, `test_auto_loan.py`, `test_investment.py`, `test_retirement.py`
**Addresses:** Audit sections 37-41
**Estimated time:** 2 hours

#### Mortgage

```python
def test_escrow_add_missing_name():
    resp = auth_client.post(f"/accounts/{acct.id}/mortgage/escrow", data={
        "annual_amount": "1200"  # name missing
    })
    assert resp.status_code in (302, 400, 422)

def test_rate_change_missing_date():
    resp = auth_client.post(f"/accounts/{acct.id}/mortgage/rate", data={
        "new_rate": "5.5"  # effective_date missing
    })
    assert resp.status_code in (302, 400, 422)

def test_rate_change_rate_zero():
    # rate = "0" → should be accepted (0% ARM rate is valid)
    # or rejected depending on business rules

def test_params_update_nonexistent_account():
    resp = auth_client.post("/accounts/999999/mortgage/params", data={...})
    assert resp.status_code in (302, 404)
```

#### HYSA

```python
def test_params_update_invalid_apy():
    resp = auth_client.post(f"/accounts/{acct.id}/hysa/params", data={
        "apy": "abc"
    })
    assert resp.status_code == 302
    # Verify APY was NOT changed

def test_params_update_negative_apy():
    resp = auth_client.post(f"/accounts/{acct.id}/hysa/params", data={
        "apy": "-0.5"
    })
    # Should reject
```

#### Auto Loan

```python
def test_params_update_negative_interest():
    resp = auth_client.post(f"/accounts/{acct.id}/auto-loan/params", data={
        "interest_rate": "-1", ...
    })
    # Should reject

def test_params_update_payment_day_zero():
    resp = auth_client.post(f"/accounts/{acct.id}/auto-loan/params", data={
        "payment_day": "0", ...
    })
    # Should reject (no day 0)
```

#### Investment

```python
def test_dashboard_login_required():
    resp = client.get(f"/accounts/{acct.id}/investment")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

def test_params_login_required():
    resp = client.post(f"/accounts/{acct.id}/investment/params", data={...})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
```

#### Retirement

```python
def test_create_pension_missing_name():
    resp = auth_client.post("/retirement/pension", data={
        "benefit_multiplier": "0.02"  # name missing
    })
    assert resp.status_code in (302, 400, 422)

def test_edit_nonexistent_pension():
    resp = auth_client.get("/retirement/pension/999999/edit")
    assert resp.status_code in (302, 404)

def test_delete_nonexistent_pension():
    resp = auth_client.post("/retirement/pension/999999/delete")
    assert resp.status_code in (302, 404)

def test_update_settings_invalid_swr():
    resp = auth_client.post("/retirement/settings", data={
        "safe_withdrawal_rate": "abc"
    })
    # Should reject or ignore
```

---

## Phase 6: Missing Negative Paths -- P2 Services & Other

### Work Unit 6.1: P2 Service Negative Paths

**Files:** `test_chart_data_service.py`, `test_escrow_calculator.py`, `test_investment_projection.py`, `test_interest_projection.py`, `test_computed_properties.py`
**Addresses:** Audit sections 24, 29, 31, 33, 34
**Estimated time:** 1.5 hours

#### Chart Data Service (weakest file -- needs most work)

```python
def test_spending_by_category_with_real_data():
    """Seed 10+ transactions across 4 categories, verify exact aggregated values."""
    # Create transactions: 3 in "Rent" ($850 each), 4 in "Groceries" ($125 each),
    #   2 in "Utilities" ($80 each), 1 in "Insurance" ($200)
    # Call get_spending_by_category(...)
    # Assert exact label names and exact sum values

def test_balance_projection_verifies_chart_data():
    """Verify chart balance values match balance_calculator output."""
    # Use same seeded data for both chart service and balance calculator
    # Assert chart datasets contain exact balance values from calculator

def test_net_pay_trajectory_with_salary_data():
    """get_net_pay_trajectory with actual salary profile -- must not be empty."""
    # Seed salary profile + periods
    # Call get_net_pay_trajectory(...)
    # Assert labels and data are not empty
    # Assert at least one data point equals expected net_pay
```

#### Escrow Calculator

```python
def test_zero_annual_amount():
    result = calculate_monthly_escrow(Decimal("0"))
    assert result == Decimal("0.00")

def test_negative_inflation_rate():
    # Deflation scenario -- should still work
    result = project_annual_escrow(components, years=3, inflation=Decimal("-0.02"))
    assert result[0] > result[2]  # Amount decreases with deflation
```

#### Investment Projection

```python
def test_calculate_inputs_none_params():
    result = calculate_investment_inputs(None, period, all_periods, [])
    # Should return default/empty inputs, not crash

def test_calculate_inputs_empty_periods():
    result = calculate_investment_inputs(params, period, [], deductions)
    # Should handle gracefully
```

#### Interest Projection

```python
def test_leap_year_february():
    # Period: Feb 15-28, 2028 (leap year)
    # 14 days, daily compounding
    # Verify exact interest (daily_rate uses 366 or 365?)

def test_very_high_apy():
    result = calculate_interest(Decimal("1000"), Decimal("1.00"), "daily",
                                date(2026, 1, 1), date(2026, 1, 15))
    # 100% APY → verify exact value, no overflow
```

#### Computed Properties

```python
def test_credit_status_returns_zero():
    txn.status = Status(name="credit")
    assert txn.effective_amount == Decimal("0")

def test_cancelled_status_returns_zero():
    txn.status = Status(name="cancelled")
    assert txn.effective_amount == Decimal("0")

def test_received_status_returns_amount():
    txn.status = Status(name="received")
    # Verify returns estimated_amount (same as projected)

def test_transfer_cancelled_returns_zero():
    transfer.status = Status(name="cancelled")
    assert transfer.effective_amount == Decimal("0")

def test_period_label_cross_month():
    # Period: Jan 25 - Feb 7 → "01/25 - 02/07"
    period = PayPeriod(start_date=date(2026, 1, 25), end_date=date(2026, 2, 7))
    assert "01/25" in period.label and "02/07" in period.label

def test_breakdown_net_pay():
    # Verify net_pay = gross - total_pre_tax - total_taxes - total_post_tax
```

---

### Work Unit 6.2: Retirement Gap, Idempotency, Integration Negative Paths

**Files:** `test_retirement_gap_calculator.py`, `test_idempotency.py`, `test_workflows.py`
**Addresses:** Audit sections 28, 16, 36
**Estimated time:** 1.5 hours

#### Retirement Gap Calculator

```python
def test_safe_withdrawal_rate_zero():
    """SWR=0 → division by zero must be caught."""
    # Should raise ValueError or return infinity/None

def test_tax_rate_one_hundred_percent():
    """100% tax rate → after-tax income = 0."""
    # annual_expenses still need to be covered → large shortfall

def test_negative_net_biweekly_pay():
    """Negative pay → should handle gracefully."""
```

#### Idempotency

```python
def test_transaction_create_double_submit():
    """The most financially dangerous double-submit scenario."""
    # POST create transaction twice with identical data
    # Verify only 1 transaction exists (or 2 if no unique constraint -- document behavior)
    # If 2 are created, this documents a real risk for the user

def test_double_submit_pay_period_generate():
    # Generate 10 periods, then generate 10 more with same start_date
    # Verify duplicates are skipped (existing test covers this, but verify via integration)
```

#### Integration Workflows

```python
def test_carry_forward_preserves_amounts():
    """Verify transactions retain estimated_amount after carry-forward."""
    # Create 2 transactions with known amounts
    # Carry forward
    # Assert both transactions have original amounts

def test_carry_forward_target_has_existing_transactions():
    """Carry forward into a period that already has transactions."""
    # Target period has 2 existing transactions
    # Source period has 3 projected transactions
    # After carry forward: target has 5 transactions total
```

---

## Phase 7: Security Polish

### Work Unit 7.1: XSS Payload Tests

**File:** New file `tests/test_routes/test_xss_prevention.py`
**Addresses:** Cross-Cutting Issue 8
**Estimated time:** 1.5 hours

#### Approach

Create one centralized test file that submits XSS payloads to every text input field and verifies the payload is either rejected or properly escaped in the response.

```python
"""Verify XSS payloads are escaped or rejected in all text input fields."""
import pytest

XSS_PAYLOAD = '<script>alert("xss")</script>'
XSS_ESCAPED = '&lt;script&gt;'  # HTML-escaped version

class TestXSSPrevention:

    def test_transaction_name(self, auth_client, seed_user, seed_periods):
        """XSS in transaction name must be escaped in grid output."""
        resp = auth_client.post("/transactions/create", data={
            "name": XSS_PAYLOAD,
            "estimated_amount": "100",
            "transaction_type_id": ...,
            "pay_period_id": seed_periods[0].id,
            "scenario_id": seed_user["scenario"].id,
        })
        # If 201/302, fetch the grid and verify escaped
        if resp.status_code in (201, 302):
            grid_resp = auth_client.get("/")
            assert XSS_PAYLOAD.encode() not in grid_resp.data
            # Either escaped or not rendered at all

    def test_account_name(self, auth_client, seed_user):
        resp = auth_client.post("/accounts", data={
            "account_name": XSS_PAYLOAD,
            "account_type_id": ...,
            "anchor_balance": "0",
        })
        if resp.status_code == 302:
            list_resp = auth_client.get("/accounts")
            assert XSS_PAYLOAD.encode() not in list_resp.data

    def test_category_name(self, auth_client, seed_user):
        resp = auth_client.post("/categories", data={
            "group_name": XSS_PAYLOAD,
            "item_name": "Normal",
        })
        if resp.status_code == 302:
            list_resp = auth_client.get("/categories")
            assert XSS_PAYLOAD.encode() not in list_resp.data

    def test_template_name(self, auth_client, seed_user):
        # Same pattern

    def test_salary_profile_name(self, auth_client, seed_user):
        # Same pattern

    def test_transfer_template_name(self, auth_client, seed_user):
        # Same pattern

    def test_savings_goal_name(self, auth_client, seed_user):
        # Same pattern

    def test_pension_name(self, auth_client, seed_user):
        # Same pattern
```

Jinja2 with autoescaping (which Flask enables by default) should pass all of these. The tests serve as a regression safety net.

---

### Work Unit 7.2: State Machine + Adversarial Gaps

**Files:** `test_hostile_qa.py`, `test_audit_fixes.py`
**Addresses:** Audit sections 47, 48
**Estimated time:** 1 hour

#### State Machine Violations

```python
def test_projected_cancel_revert_to_projected():
    """projected → cancelled → projected (double reversal)."""
    # Cancel transaction, then attempt to revert to projected
    # Verify behavior (should this be allowed?)

def test_settled_cannot_revert_to_projected():
    """settled → projected should be rejected."""
    # Mark as done (→ settled pathway), then try to revert
```

#### Audit Fixes

```python
def test_update_transfer_with_foreign_account_idor():
    """PATCH transfer template changing to_account to other user's account."""
    # Should reject -- verify DB unchanged

def test_update_goal_with_foreign_account_idor():
    """Update savings goal changing account_id to other user's account."""
    # Should reject

def test_self_transfer_rejected():
    """from_account_id == to_account_id should be rejected."""
    resp = auth_client.post("/transfers", data={
        "from_account_id": acct.id,
        "to_account_id": acct.id,  # Same!
        ...
    })
    assert resp.status_code in (302, 400, 422)
    # Verify flash error about same account
```

---

## Phase 8: Final Polish

### Work Unit 8.1: Scale Tests -- Realistic Data

**Files:** `test_chart_data_service.py`, `test_balance_calculator.py`
**Addresses:** Audit sections 23, 24 (realistic data gaps), Cross-Cutting Issue 1
**Depends on:** Work Unit 0.1 (`seed_periods_52`)
**Estimated time:** 1.5 hours

#### Chart Data Service with Realistic Data

```python
def test_spending_chart_many_categories_many_periods():
    """Seed 50+ transactions across 8 categories over 10 periods."""
    # Verify all categories appear in labels
    # Verify amounts are correctly summed per category
    # Verify period filtering works with large datasets

def test_balance_chart_52_periods():
    """Full 2-year projection in chart data matches balance calculator."""
    # Seed 52 periods + transactions
    # Get chart data
    # Get balance calculator results
    # Assert chart data points match balance values exactly
```

---

### Work Unit 8.2: Scripts, Utils, Performance

**Files:** `test_audit_cleanup.py`, `test_integrity_check.py`, `test_reset_mfa.py`, `test_logging_config.py`, `test_trigger_overhead.py`
**Addresses:** Audit sections 49, 50, 51
**Estimated time:** 1 hour

#### Audit Cleanup

```python
def test_cleanup_negative_days():
    """days=-1 → should raise ValueError or treat as 0."""
    # verify behavior
```

#### Integrity Check

```python
# For each untested check ID, add a minimal detection test.
# Priority: FK-02, FK-03 (foreign key checks are most critical)
# Lower priority: OR-01, OR-04, BA-02, DC-02 (orphan/data checks)
```

#### Reset MFA

```python
def test_reset_empty_email():
    """reset_mfa('') → should raise or return 'not found'."""

def test_reset_partial_mfa_state():
    """MFA disabled but encrypted secret still present."""
    # Set is_enabled=False, totp_secret_encrypted=<valid>
    # reset_mfa should still clear the secret
```

#### Logging Config

- **Refactor `test_slow_request_logs_at_warning`** (lines 48-94): Remove ~20 lines of dead code and commented-out approaches. Simplify to use `monkeypatch` on the threshold constant.

- **Fix `test_log_includes_remote_addr`** (line 106): Replace `hasattr` with `assert summaries[-1].remote_addr == "127.0.0.1"`.

#### Performance

```python
def test_update_trigger_overhead():
    """Measure trigger overhead on UPDATE operations (only INSERT currently tested)."""
    # Same benchmark pattern as existing INSERT test but with UPDATE

def test_delete_trigger_overhead():
    """Measure trigger overhead on DELETE operations."""
```

Add minimum absolute time threshold to all performance tests:

```python
if time_without < 0.001:  # Less than 1ms -- too fast to measure meaningfully
    pytest.skip("Baseline too fast for reliable overhead measurement")
```

---

### Work Unit 8.3: Auth Route Gaps

**File:** `test_auth.py`
**Addresses:** Audit section 43
**Estimated time:** 45 minutes

```python
def test_login_nonexistent_email():
    """Login with email that doesn't exist → same error as wrong password."""
    resp = client.post("/login", data={
        "email": "nobody@shekel.local", "password": "anything"
    })
    assert resp.status_code == 200  # Re-renders login form
    assert b"Invalid" in resp.data  # Generic error (no email enumeration)

def test_login_missing_email_field():
    resp = client.post("/login", data={"password": "test"})
    assert resp.status_code in (200, 400, 422)

def test_login_missing_password_field():
    resp = client.post("/login", data={"email": "test@shekel.local"})
    assert resp.status_code in (200, 400, 422)

def test_mfa_verify_rate_limiting():
    """Brute force MFA codes should trigger rate limiting."""
    # If rate limiting is implemented on /mfa/verify:
    # Submit 10 wrong codes rapidly, verify 429 on the last ones
```

---

## Execution Summary

| Phase                            | Work Units | Est. Hours | Cumulative |
| -------------------------------- | ---------- | ---------- | ---------- |
| **0: Infrastructure**            | 0.1        | 0.75       | 0.75       |
| **1: Financial Exactness**       | 1.1-1.7    | 11.5       | 12.25      |
| **2: Security**                  | 2.1-2.3    | 4.0        | 16.25      |
| **3: Assertion Quality**         | 3.1-3.3    | 5.5        | 21.75      |
| **4: Negative Paths (Services)** | 4.1-4.2    | 4.0        | 25.75      |
| **5: Negative Paths (Routes)**   | 5.1-5.4    | 7.0        | 32.75      |
| **6: Negative Paths (Other)**    | 6.1-6.2    | 3.0        | 35.75      |
| **7: Security Polish**           | 7.1-7.2    | 2.5        | 38.25      |
| **8: Final Polish**              | 8.1-8.3    | 3.25       | 41.5       |

### Suggested Execution Order (if time is limited)

If you cannot complete all phases, prioritize in this order:

1. **Phase 0** (0.1) -- 45 min. Foundation for everything.
2. **Phase 1, Units 1.1 + 1.2 + 1.3** -- 5 hrs. The most critical financial gaps.
3. **Phase 2, Unit 2.3** -- 1 hr. Fixes two production bugs (IntegrityError 500s).
4. **Phase 1, Units 1.4 + 1.5** -- 3.5 hrs. Tax and paycheck exactness.
5. **Phase 2, Unit 2.1** -- 1.5 hrs. Auth coverage for all routes.
6. **Phase 1, Unit 1.6** -- 1 hr. Recurrence engine safety (interval_n=0 risk).
7. **Phase 3** -- 5.5 hrs. Makes existing tests actually reliable.
8. **Everything else** -- fills remaining gaps.

### After Each Work Unit

```bash
# Run full suite to verify no regressions
pytest --tb=short -q

# Run only the modified file to see new test results
pytest tests/test_services/test_balance_calculator.py -v

# Verify test count increased
pytest --co -q | tail -1
```

### Definition of Done

The test suite is "first rate" when:

1. Every financial calculation has at least one test with exact Decimal assertions computed from known inputs.
2. Every protected route has an unauthenticated access test.
3. Every IDOR test proves no state change on the victim's data via DB query.
4. No test has a status-code-only assertion -- every test verifies either response content or database state.
5. No test uses directional assertions (`>`, `<`) on Decimal financial values -- all use exact `==`.
6. The balance calculator, paycheck calculator, and tax calculator are each tested at production scale (26-52 periods) with penny-level accuracy verification.
