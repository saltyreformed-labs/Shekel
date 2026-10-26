> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Test Suite Audit Report

**Date:** 2026-03-15
**Scope:** All test files under `tests/` (~65 files, ~470 tests)
**Reference:** `tests/TEST_PLAN.md` categories (HP, SP, IDOR, BE, SM, IDEM, FIN) and priority matrix (P0-P3)

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [P0 -- Critical (Financial Correctness, Data Corruption)](#p0--critical)
  - [1. Balance Calculator](#1-balance-calculator)
  - [2. Balance Calculator -- Debt](#2-balance-calculator--debt)
  - [3. Balance Calculator -- HYSA](#3-balance-calculator--hysa)
  - [4. Paycheck Calculator](#4-paycheck-calculator)
  - [5. Recurrence Engine](#5-recurrence-engine)
  - [6. Tax Calculator](#6-tax-calculator)
  - [7. Transfer Recurrence](#7-transfer-recurrence)
- [P1 -- High (Security Gaps, IDOR, Core Routes)](#p1--high)
  - [8. Account Routes](#8-account-routes)
  - [9. Accounts Dashboard](#9-accounts-dashboard)
  - [10. Salary Routes](#10-salary-routes)
  - [11. Transfer Routes](#11-transfer-routes)
  - [12. Savings Routes](#12-savings-routes)
  - [13. Pay Period Service](#13-pay-period-service)
  - [14. Savings Goal Service](#14-savings-goal-service)
  - [15. Schema Validation](#15-schema-validation)
  - [16. Integration Workflows](#16-integration-workflows)
- [P2 -- Medium (Functional Gaps)](#p2--medium)
  - [17. Template Routes](#17-template-routes)
  - [18. Category Routes](#18-category-routes)
  - [19. Pay Period Routes](#19-pay-period-routes)
  - [20. Settings Routes](#20-settings-routes)
  - [21. Grid Routes](#21-grid-routes)
  - [22. Transaction Auth Routes](#22-transaction-auth-routes)
  - [23. Chart Routes](#23-chart-routes)
  - [24. Chart Data Service](#24-chart-data-service)
  - [25. Credit Workflow Service](#25-credit-workflow-service)
  - [26. Auth Service](#26-auth-service)
  - [27. MFA Service](#27-mfa-service)
  - [28. Idempotency Tests](#28-idempotency-tests)
  - [29. Computed Properties](#29-computed-properties)
  - [30. Amortization Engine](#30-amortization-engine)
  - [31. Escrow Calculator](#31-escrow-calculator)
  - [32. Growth Engine](#32-growth-engine)
  - [33. Interest Projection](#33-interest-projection)
  - [34. Investment Projection](#34-investment-projection)
  - [35. Pension Calculator](#35-pension-calculator)
  - [36. Retirement Gap Calculator](#36-retirement-gap-calculator)
  - [37. Mortgage Routes](#37-mortgage-routes)
  - [38. HYSA Routes](#38-hysa-routes)
  - [39. Auto Loan Routes](#39-auto-loan-routes)
  - [40. Investment Routes](#40-investment-routes)
  - [41. Retirement Routes](#41-retirement-routes)
  - [42. Onboarding Routes](#42-onboarding-routes)
- [P3 -- Low (Edge Polish, Infrastructure)](#p3--low)
  - [43. Auth Routes](#43-auth-routes)
  - [44. Error Routes](#44-error-routes)
  - [45. Health Routes](#45-health-routes)
  - [46. Audit Triggers](#46-audit-triggers)
  - [47. Adversarial / Hostile QA](#47-adversarial--hostile-qa)
  - [48. Audit Fixes](#48-audit-fixes)
  - [49. Scripts (Cleanup, Integrity, MFA Reset)](#49-scripts)
  - [50. Utils (Logging)](#50-utils)
  - [51. Performance Tests](#51-performance-tests)
  - [52. Conftest (Fixtures)](#52-conftest)
- [Cross-Cutting Systemic Issues](#cross-cutting-systemic-issues)

---

## Executive Summary

The test suite is substantial (~470 tests) with good coverage of happy paths and IDOR protection across most route files. However, five systemic issues undermine confidence in financial correctness and security:

1. **No FIN tests verifying penny-level Decimal accuracy across 52+ periods** in any balance calculator file -- the single most important gap for a financial app.
2. **Pervasive directional/range assertions on financial values** (`> Decimal("0")`, `< Decimal("100000")`) instead of exact Decimal comparisons, especially in debt, HYSA, tax, and growth engine tests.
3. **Shallow status-code-only assertions** in ~30 tests across route files, particularly in newer financial account routes (mortgage, HYSA, auto loan, investment, retirement).
4. **Missing unauthenticated-access tests** in 8+ route files -- only `test_auth.py` and `test_savings.py` consistently verify login requirements.
5. **Realistic data gaps** -- most test modules seed 1-3 records and 10 periods, while production involves 52+ periods with 15-20 transactions each.

### Finding Counts by Type

| Finding Type                    | P0  | P1  | P2  | P3  | Total |
| ------------------------------- | --- | --- | --- | --- | ----- |
| Assertion Depth (shallow)       | 4   | 8   | 18  | 8   | 38    |
| Missing Category Coverage (FIN) | 5   | 2   | 6   | 0   | 13    |
| Realistic Data Gaps             | 5   | 3   | 5   | 2   | 15    |
| Missing Negative Paths          | 8   | 10  | 20  | 12  | 50    |
| Assertion Smell                 | 10  | 2   | 8   | 6   | 26    |

---

## P0 -- Critical

### 1. Balance Calculator

**File:** `tests/test_services/test_balance_calculator.py` (14 tests)

#### ASSERTION DEPTH

All tests assert exact `Decimal` values. **PASS.**

#### MISSING CATEGORY COVERAGE

| Category | Status      | Notes                                                                                                        |
| -------- | ----------- | ------------------------------------------------------------------------------------------------------------ |
| HP       | Present     | Multiple happy-path tests                                                                                    |
| SP       | Partial     | None anchor covered; no error-raising sad paths                                                              |
| BE       | Partial     | None anchor, empty inputs covered; **missing: negative anchor (overdraft), zero amounts, very large values** |
| SM       | Partial     | done/credit/projected/cancelled tested; no standalone `received` test                                        |
| FIN      | **MISSING** | **No test verifying penny-level accuracy across 52+ periods**                                                |
| IDEM     | Missing     | No test calling `calculate_balances` twice returns identical results                                         |

#### REALISTIC DATA GAPS

- **`test_multi_period_roll_forward`** (line 151): Uses 2 periods with 2 transactions each. Production has 52+ periods with 15-20 transactions.
- **`test_five_period_rollforward_with_transfers`** (line 413): 5 periods is better but still far short.
- **`seed_periods` fixture**: Only 10 periods; production generates ~52.
- **Recommendation:** Add a test with 52+ periods, each with 10-15 mixed transactions (income, expenses, various statuses), verifying cumulative penny-level accuracy across the full projection window.

#### MISSING NEGATIVE PATHS

- No test for negative `anchor_balance` (deficit/overdraft scenario).
- No test for `estimated_amount=0` or negative `estimated_amount`.
- No test for very large numbers (e.g., `Decimal("999999999.99")`).

---

### 2. Balance Calculator -- Debt

**File:** `tests/test_services/test_balance_calculator_debt.py`

#### ASSERTION DEPTH

- **`test_debt_balance_with_payments`** (line 48): Asserts `balances[2] < Decimal("100000.00")` and `principal_by_period[2] > Decimal("0.00")` -- **directional only, no exact values.**
- **`test_debt_principal_tracking`** (line 100): Same pattern -- `> Decimal("0.00")` and `balances[3] < balances[2]`.
- **Recommendation:** For $100k at 6%/30yr, month 1 interest = $500.00, principal = $99.55. Assert `principal_by_period[2] == Decimal("99.55")` and `balances[2] == Decimal("99900.45")` exactly.

#### ASSERTION SMELL CHECK

- Lines 73-76, 126-129: Every financial assertion is directional. **Zero exact Decimal values verified in this entire file.**

#### MISSING CATEGORY COVERAGE

| Category | Status                                                              |
| -------- | ------------------------------------------------------------------- |
| HP       | Present                                                             |
| SP       | **MISSING** -- no invalid loan params, zero rate, zero term          |
| BE       | **MISSING** -- no zero principal, zero rate, overpayment, max values |
| SM       | **MISSING** -- no status-related tests on debt transfers             |
| FIN      | **MISSING** -- every financial assertion is directional              |

#### REALISTIC DATA GAPS

- Tests use 2-3 periods. A real mortgage has 360 payments. Test with at least 26 periods (1 year).

#### MISSING NEGATIVE PATHS

- No test for `interest_rate=0` (interest-free loan).
- No test for `term_months=0` or negative term.
- No test for payment larger than remaining principal (overpayment).
- No test for `current_principal=0` (fully paid off).

---

### 3. Balance Calculator -- HYSA

**File:** `tests/test_services/test_balance_calculator_hysa.py`

#### ASSERTION DEPTH

- **`test_hysa_balance_includes_interest`** (line 36): `assert balances[1] > Decimal("10000.00")` -- **directional only.**
- **`test_hysa_interest_compounds_across_periods`** (line 54): `assert interest[2] > interest[1]` -- **directional only.**
- **`test_hysa_with_transfers`** (line 72): `assert balances[2] > Decimal("10500.00")` -- **directional only.**
- **Recommendation:** For $10k at 4.5% APY daily compounding over 14 days, compute and assert the exact rounded value.

#### ASSERTION SMELL CHECK

- 3 of 6 tests use only directional assertions on financial values.
- **`test_interest_by_period_dict`** (line 154): Verifies `isinstance(amt, Decimal)` and quantization but not actual computed value.

#### MISSING CATEGORY COVERAGE

| Category | Status                                                                                       |
| -------- | -------------------------------------------------------------------------------------------- |
| FIN      | **MISSING** -- no exact interest values verified; all positive-APY assertions are directional |
| BE       | Partial -- zero APY covered; **missing: negative APY, extreme APY**                           |
| SP       | Partial -- **missing: invalid `compounding_frequency` values**                                |

#### REALISTIC DATA GAPS

- Tests use 2-3 periods. Add a full-year (26 periods) test to verify compounding accuracy doesn't drift.

---

### 4. Paycheck Calculator

**File:** `tests/test_services/test_paycheck_calculator.py` (47 tests)

#### ASSERTION DEPTH

- **`test_basic_paycheck_no_deductions`** (line 392): Six assertions all using `> ZERO` or `< result.gross_biweekly`. Exercises the full pipeline but **verifies no specific output values.**
- **`test_w4_fields_passed_to_federal`** (line 518): `assert w4_result.federal_tax > base_result.federal_tax` -- directional only.
- `test_net_pay_formula` (line 410): Verifies algebraic identity -- **excellent.**
- `test_gross_biweekly_calculation` (line 429): Exact gross -- **good.**
- Raise tests use exact Decimal -- **good.**

#### MISSING CATEGORY COVERAGE

| Category | Status                                                                                                         |
| -------- | -------------------------------------------------------------------------------------------------------------- |
| FIN      | **PARTIAL** -- raise compounding exact, but basic paycheck has zero exact values; no full-year net pay sum test |
| BE       | Partial -- taxable floor at zero tested; **missing: zero salary, FICA SS wage cap boundary**                    |
| SP       | Partial -- None configs tested; **missing: negative salary, `pay_periods_per_year=0`**                          |

#### REALISTIC DATA GAPS

- **`test_returns_one_breakdown_per_period`** (line 839): Uses only 3 periods. `project_salary` is never tested with 26 or 52 periods.
- No test runs `project_salary` across a full year and verifies that the sum of all `net_pay` matches expected annual net (would catch cumulative rounding drift).
- **No FICA Social Security wage cap boundary test.** The `_get_cumulative_wages` function exists for this, but no test verifies SS tax stops accruing when wages exceed `ss_wage_base`.

#### MISSING NEGATIVE PATHS

- No test for `annual_salary=0`.
- No test for negative salary.
- No test for `pay_periods_per_year=0` (division by zero risk).
- No test for net_pay going negative from excessive post-tax deductions.

---

### 5. Recurrence Engine

**File:** `tests/test_services/test_recurrence_engine.py` (28 tests)

#### ASSERTION DEPTH

Good -- generation tests verify `len(created)` and specific field values. Pattern matching tests verify counts and dates.

#### ASSERTION SMELL CHECK

- **`test_every_period_generates_for_all`** (line 117): Count check and field verification are good, but does not verify `pay_period_id` assignment to the correct period.
- **Recommendation:** Assert each created transaction maps to a distinct period and the set of period IDs matches `seed_periods`.

#### MISSING CATEGORY COVERAGE

| Category | Status                                                                                           |
| -------- | ------------------------------------------------------------------------------------------------ |
| HP       | Present -- all 8 patterns tested                                                                  |
| SP       | Partial -- unknown pattern tested; **missing: invalid numeric params**                            |
| BE       | Partial -- Feb clamping covered; **missing: `day_of_month=0`, `day_of_month=32`, `interval_n=0`** |
| SM       | Present -- override, deletion, done status transitions                                            |
| IDOR     | **MISSING** -- no test verifying template from user A cannot generate into user B's scenario      |

#### MISSING NEGATIVE PATHS

- **`interval_n=0`** -- potential infinite loop or division by zero in `every_n_periods`. High risk.
- No test for `month_of_year=0` or `month_of_year=13` in quarterly/semi-annual/annual patterns.
- No test for `None` `day_of_month` when pattern requires it.

#### REALISTIC DATA GAPS

- Pure pattern-matching tests use 26 periods (**good**). DB integration tests use 10 periods. No test at 52+ periods (production scale).

---

### 6. Tax Calculator

**File:** `tests/test_services/test_tax_calculator.py` (30 tests)

#### ASSERTION DEPTH

Some tests use exact values (`== Decimal("219.23")`, `== Decimal("475.00")`) -- **excellent**. However, 7 tests use range or directional assertions:

#### ASSERTION SMELL CHECK

| Test                                       | Line | Issue                                                               |
| ------------------------------------------ | ---- | ------------------------------------------------------------------- |
| `test_weekly_pay_frequency`                | 141  | `Decimal("108") < result < Decimal("111")` -- range instead of exact |
| `test_income_spans_all_brackets`           | 231  | `Decimal("6940") < result < Decimal("6960")` -- range                |
| `test_very_high_income_top_bracket_only`   | 252  | `result > Decimal("12900")` -- directional only                      |
| `test_income_exactly_at_first_bracket_top` | 280  | `Decimal("38") < result < Decimal("39")` -- range                    |
| `test_income_one_dollar_into_next_bracket` | 293  | `result >= Decimal("38.46")` -- directional                          |
| `test_child_credits_reduce_tax`            | 398  | `Decimal("153") < diff < Decimal("155")` -- range                    |
| `test_other_dependent_credits`             | 415  | `Decimal("38") < diff < Decimal("39")` -- range                      |

- **Recommendation:** Every test should compute the exact expected result from the given inputs since the function is deterministic and uses only Decimal arithmetic.

#### MISSING CATEGORY COVERAGE

| Category | Status                                                                        |
| -------- | ----------------------------------------------------------------------------- |
| FIN      | **PARTIAL** -- some exact values, but 7 tests use range/directional assertions |
| BE       | Partial -- bracket boundaries tested with ranges instead of exact values       |

#### REALISTIC DATA GAPS

- No test computes withholding for all 26 pay periods in a year and verifies total annual withholding matches annual tax liability.

---

### 7. Transfer Recurrence

**File:** `tests/test_services/test_transfer_recurrence.py` (10 tests)

#### ASSERTION DEPTH

Good -- generation tests verify `len(created)`, `amount`, and `name`. Conflict resolution verifies field values.

#### ASSERTION SMELL CHECK

**PASS.** All assertions use exact values or counts.

#### MISSING CATEGORY COVERAGE

| Category | Status                                                        |
| -------- | ------------------------------------------------------------- |
| SP       | Partial -- None rule covered; **missing: invalid amounts**     |
| BE       | **MISSING** -- no zero-amount, no self-transfer, no max amount |
| IDOR     | **MISSING** -- no cross-user isolation test                    |

#### MISSING NEGATIVE PATHS

- No test for `default_amount=0` (zero-amount transfer).
- No test for `default_amount` as negative.
- No test for `from_account_id == to_account_id`.
- No test for deleted template.

---

## P1 -- High

### 8. Account Routes

**File:** `tests/test_routes/test_accounts.py` (29 tests)

#### ASSERTION DEPTH

- **`test_new_account_form_renders`** (line 63): Asserts `b"form" in response.data` -- extremely weak (any HTML page contains `<form>`). **Recommendation:** Assert `b"Create Account"` or `b"anchor_balance"`.
- **`test_inline_anchor_form_returns_partial`** (line 346): Only `status_code == 200`.
- **`test_inline_anchor_display_returns_partial`** (line 359): Only `status_code == 200`.

#### ASSERTION SMELL CHECK

- **`test_create_account_type`** (line 487): `assert acct_type is not None` after `.one()` which already raises.

#### MISSING NEGATIVE PATHS

- No unauthenticated access test.
- No test for nonexistent IDs: `GET /accounts/999999/edit`, `POST /accounts/999999`.
- No test for non-numeric `anchor_balance`, negative `anchor_balance`, XSS in `name`.
- No `test_reactivate_other_users_account_redirects` (IDOR gap).
- No test for deactivating an already-inactive account.

---

### 9. Accounts Dashboard

**File:** `tests/test_routes/test_accounts_dashboard.py`

#### ASSERTION DEPTH

- **`test_dashboard_no_accounts`** (line 191): Only `status_code == 200`. No empty-state UI text verified.

#### MISSING NEGATIVE PATHS

- No IDOR test (user A's dashboard shows user B's accounts).
- No test with mix of active and inactive accounts.

---

### 10. Salary Routes

**File:** `tests/test_routes/test_salary.py` (36 tests)

#### ASSERTION DEPTH

- **`test_new_profile_form`** (line 174): `b"form" in response.data` -- weak.
- **`test_add_raise_htmx_returns_partial`** (line 426): Only `status_code == 200`.
- **`test_add_deduction_htmx_returns_partial`** (line 621): Only `status_code == 200`.
- **`test_projection_renders`** (line 701): Only `status_code == 200`.
- **`test_breakdown_renders`** (line 679): `b"breakdown" in response.data.lower()` -- very weak.

#### MISSING NEGATIVE PATHS

- No unauthenticated access tests.
- No test for nonexistent profile ID on edit/update.
- No test for negative `annual_salary`, zero `pay_periods_per_year`, XSS in profile name.
- No IDOR tests on tax config endpoints.
- No test for adding raise/deduction to another user's profile (only delete is IDOR-tested).

---

### 11. Transfer Routes

**File:** `tests/test_routes/test_transfers.py` (28 tests)

#### ASSERTION DEPTH

- **`test_new_template_form`** (line 176): `b"form"` -- weak.
- **`test_get_cell`** (line 398), **`test_get_quick_edit`** (line 408), **`test_get_full_edit`** (line 418): All status-code-only.
- **`test_create_ad_hoc_validation_error`** (line 587): Only `status_code == 400`.

#### MISSING NEGATIVE PATHS

- No unauthenticated access tests.
- No test for nonexistent transfer IDs.
- No test for malformed amounts (`"abc"`, `"-100"`, `"0"`).
- No test for mark-done on already-done transfer (invalid state transition).
- No IDOR on quick-edit/full-edit for other user's transfers.

#### APPLICATION BUG INDICATOR

- **`test_create_template_double_submit`** (line 257): Uses `pytest.raises(IntegrityError)` -- the route does NOT gracefully handle duplicates and would 500 in production. Route should catch `IntegrityError` and flash a user-friendly message.

---

### 12. Savings Routes

**File:** `tests/test_routes/test_savings.py` (19 tests)

#### ASSERTION DEPTH

- **`test_new_goal_form`** (line 406): Only `status_code == 200`.
- **`test_dashboard_no_savings_accounts`** (line 210): Only `status_code == 200`.

#### ASSERTION SMELL CHECK

- **`test_dashboard_investment_account_shows_growth_projections`** (line 275): `assert len(amounts_int) > 0` -- "len > 0" smell without specific expected value.
- **`test_dashboard_investment_account_includes_contributions`** (line 315): Same pattern.
- **`test_dashboard_employer_contribution_without_employee_deduction`** (line 386): Same pattern.

#### APPLICATION BUG INDICATOR

- **`test_duplicate_goal_name_same_account`** (line 613): `pytest.raises(IntegrityError)` -- route doesn't handle duplicates gracefully; would 500 in production.

#### MISSING NEGATIVE PATHS

- No test for creating a goal on a deactivated account.
- No IDOR on goal update changing `account_id` to another user's account.

---

### 13. Pay Period Service

**File:** `tests/test_services/test_pay_period_service.py` (17 tests)

#### MISSING NEGATIVE PATHS

- No test for `num_periods` with negative value.
- No test for `None` user_id or non-existent user_id.
- No test for `get_current_period` on exact boundary (first/last day of period).
- No test for `get_periods_in_range` with `start_index < 0` or `count < 0`.

#### REALISTIC DATA GAPS

- `seed_periods` creates 10 periods; production has ~52-104. Add a test generating 104 periods and querying ranges in the middle.

---

### 14. Savings Goal Service

**File:** `tests/test_services/test_savings_goal_service.py` (14 tests)

#### ASSERTION DEPTH

All tests make exact Decimal equality assertions. **PASS.**

#### MISSING NEGATIVE PATHS

- No test for `calculate_required_contribution` with `current_balance=None` or `target_amount=None`.
- No test for negative `savings_balance` or `average_monthly_expenses`.

---

### 15. Schema Validation

**File:** `tests/test_schemas/test_validation.py` (51 tests)

#### ASSERTION DEPTH

All assertions verify loaded data values or ValidationError messages. **PASS.**

#### MISSING NEGATIVE PATHS

- No test for extremely long name strings (Length max enforcement).
- No test for `estimated_amount` with excessive decimal places (e.g., `"100.12345"`).
- No test for `day_of_month=32` on TemplateCreateSchema.
- No test for negative amount on TransferCreateSchema.
- No test for `state_code=""` or lowercase state code on SalaryProfileCreateSchema.
- No test for FICA rates > 1.0 (e.g., `ss_rate=2.0`).
- No XSS/HTML payload tests in any string field.

---

### 16. Integration Workflows

**File:** `tests/test_integration/test_workflows.py` (6 tests)

#### ASSERTION SMELL CHECK

- **`test_monthly_recurrence_hits_correct_periods`** (line 108): `assert len(txns) >= 1` -- should be exact count (5 months in 10 biweekly periods).
- **`test_carry_forward_moves_projected_items`** (line 311): Does not verify transactions retained their `estimated_amount` after move.

#### MISSING NEGATIVE PATHS

- No test for carry-forward when target period already has transactions.
- No test for `mark_as_credit` on already-credit transaction.
- No cross-user isolation tests.
- No error/failure scenario tests.

---

## P2 -- Medium

### 17. Template Routes

**File:** `tests/test_routes/test_templates.py` (24 tests)

#### ASSERTION DEPTH

- **`test_new_template_form`** (line 163): Only `status_code == 200`.
- **`test_list_templates_empty`** (line 150): Only `status_code == 200`.

#### ASSERTION SMELL CHECK

- **`test_create_template_with_recurrence`** (line 219): `assert len(txns) > 0` -- should be exact count.
- **`test_reactivate_restores_transactions`** (line 475): `assert active_txns > 0` -- same.

#### MISSING NEGATIVE PATHS

- No unauthenticated access tests.
- No double-submit (IDEM) test.
- No test for deleting already-deactivated template or reactivating already-active template.

---

### 18. Category Routes

**File:** `tests/test_routes/test_categories.py` (11 tests)

#### ASSERTION DEPTH

- **`test_create_category_htmx_validation_error`** (line 127): Only `status_code == 400`.

#### MISSING NEGATIVE PATHS

- No unauthenticated access tests.
- No boundary tests (max-length names, empty-string-after-trim, special characters).
- No IDEM (double-submit) test.

---

### 19. Pay Period Routes

**File:** `tests/test_routes/test_pay_periods.py` (6 tests)

#### ASSERTION DEPTH

- **`test_generate_missing_start_date`** (line 46): Only `status_code == 422`.
- **`test_generate_cadence_zero`** (line 55): Only `status_code == 422`.

#### MISSING NEGATIVE PATHS

- No unauthenticated access tests.
- No test for `start_date=not-a-date`, `num_periods=-5`, `num_periods=9999999`.

---

### 20. Settings Routes

**File:** `tests/test_routes/test_settings.py` (7 tests)

#### ASSERTION DEPTH

- **`test_settings_page_renders`** (line 22): Only `status_code == 200`.
- Dashboard section tests (lines 184-256): All only assert status 200 + single keyword.

#### MISSING NEGATIVE PATHS

- No unauthenticated access tests.
- No IDOR on grid account (can user A set user B's account as their default).
- No boundary tests (negative `grid_default_periods`, zero values, extreme `inflation_rate`).
- No IDEM test.

---

### 21. Grid Routes

**File:** `tests/test_routes/test_grid.py` (19 tests)

#### ASSERTION DEPTH

- **`test_grid_period_controls`** (line 41): Only `status_code == 200`.
- **`test_balance_row_returns_partial`** (line 53): Only `status_code == 200`.
- **`test_balance_row_custom_offset`** (line 67): Only `status_code == 200`.
- **`test_grid_periods_large_value`** (line 74): Only `status_code == 200`.
- **`test_create_transaction`** (line 99): Only `status_code == 201` -- does not verify DB persistence.

#### ASSERTION SMELL CHECK

- **`test_mark_credit_creates_payback`** (line 246): `assert payback is not None` -- does not verify amount, status, or category.

#### MISSING NEGATIVE PATHS

- No test for nonexistent transaction IDs on PATCH/POST/DELETE.
- No test for malformed POST data (missing `name`, negative amount, XSS payloads).
- No double-submit for mark-done or cancel.
- No test for invalid state transitions (cancel already-cancelled, mark-done already-done).

---

### 22. Transaction Auth Routes

**File:** `tests/test_routes/test_transaction_auth.py` (13 tests)

#### ASSERTION DEPTH

- **`test_get_cell_blocked`** (line 107), **`test_quick_edit_blocked`** (line 113), **`test_full_edit_blocked`** (line 121): Status code only; don't verify no data leaked.
- **`test_mark_credit_blocked`** (line 153), **`test_unmark_credit_blocked`** (line 185): Status code only; don't verify other user's transaction state unchanged (unlike `test_mark_done_blocked` which does).

#### MISSING NEGATIVE PATHS

- No unauthenticated access test.
- No test for creating transaction with another user's `scenario_id`.
- No test with ID=0, ID=-1, or ID=MAX_INT.

---

### 23. Chart Routes

**File:** `tests/test_routes/test_charts.py`

#### ASSERTION DEPTH

- **`test_spending_fragment_period_params`** (line 199): Only `status_code == 200`.
- 5 redirect tests (lines 90-113): Only `status_code == 302` without verifying Location header.

#### MISSING CATEGORY COVERAGE

| Category | Status                                                           |
| -------- | ---------------------------------------------------------------- |
| BE       | **MISSING** -- no tests for large date ranges, single-period data |
| FIN      | **MISSING** -- no verification of chart numeric values            |

#### REALISTIC DATA GAPS

- Chart tests seed at most 1 transaction. Production aggregates hundreds across categories/periods.

---

### 24. Chart Data Service

**File:** `tests/test_services/test_chart_data_service.py`

**This is the weakest test file in the audit.**

#### ASSERTION DEPTH

- **`test_single_checking_account`** (line 94): `assert len(result["labels"]) > 0` and `len(result["datasets"]) >= 1` -- **shallow.**
- **`test_groups_correctly`** (line 167): `assert len(result["labels"]) >= 2` -- **shallow.**
- **`test_date_range_filter`** (line 147): `assert len(result["labels"]) <= 4` -- **shallow.** Only checks upper bound.
- **`test_matches_engine_output`** (line 285): `assert len(result["labels"]) > 0` -- **shallow.**
- **`test_assets_minus_liabilities`** (line 333): `assert result["data"][0] > 0` -- **shallow.**

#### MISSING CATEGORY COVERAGE

| Category | Status                                                                             |
| -------- | ---------------------------------------------------------------------------------- |
| SP       | **MISSING** -- no invalid user, invalid account, invalid period range               |
| FIN      | **SEVERELY LACKING** -- no test verifies actual computed balance or spending values |

#### REALISTIC DATA GAPS

- Most tests seed only 1-2 transactions. `get_net_pay_trajectory` has no non-empty test.

---

### 25. Credit Workflow Service

**File:** `tests/test_services/test_credit_workflow.py` (15 tests)

#### ASSERTION SMELL CHECK

- Line 137: `assert new_cat is not None` -- weak; `.one()` query would already raise if missing.

#### MISSING NEGATIVE PATHS

- No test for `mark_as_credit` on non-existent `transaction_id`.
- No test for `unmark_credit` on a transaction never marked as credit.
- No test for double-mark (mark_as_credit on already-credit).
- No test for `carry_forward` with `source_period_id == target_period_id`.

---

### 26. Auth Service

**File:** `tests/test_services/test_auth_service.py` (7 tests)

#### MISSING NEGATIVE PATHS

- No test for `hash_password("")` (empty string).
- No test for bcrypt 72-byte limit (very long password).
- No test for `authenticate` with `None` email/password.
- No test for `change_password` where new password equals old password.

---

### 27. MFA Service

**File:** `tests/test_services/test_mfa_service.py`

#### ASSERTION SMELL CHECK

- Line 17: `assert len(secret) > 0` -- should assert `len(secret) == 32` (pyotp base32 default).

#### MISSING NEGATIVE PATHS

- No test for `encrypt_secret("")`, `decrypt_secret` with corrupted ciphertext.
- No test for `verify_totp_code` with non-numeric, empty, or wrong-length code.
- No test for `generate_backup_codes(0)`.

---

### 28. Idempotency Tests

**File:** `tests/test_integration/test_idempotency.py` (4 tests)

#### ASSERTION DEPTH

- **`test_double_login_succeeds`** (line 85): Only `status_code == 302` for both requests -- does not verify session state.
- **`test_duplicate_template_creates_second`** (line 116): Status-code-only for responses (DB count check saves it).

#### MISSING NEGATIVE PATHS

- **No test for double-submit of transaction creation** -- the most financially dangerous double-submit scenario.
- No test for double-submit with one valid + one invalid attempt.
- No test for double-submit of pay period generation.

---

### 29. Computed Properties

**File:** `tests/test_models/test_computed_properties.py` (13 tests)

#### MISSING NEGATIVE PATHS

- Missing `credit` status (should return `Decimal("0")`) -- tested in `test_audit_fixes.py` but not here.
- Missing `cancelled` status for both Transaction and Transfer.
- Missing `received` status.
- Missing `estimated_amount=None` edge case.
- Missing `actual_amount=Decimal("0")` with status=done.
- Missing cross-month/cross-year `PayPeriod.label` test.
- Missing `PaycheckBreakdown.net_pay` computation test.

---

### 30. Amortization Engine

**File:** `tests/test_services/test_amortization_engine.py`

#### ASSERTION SMELL CHECK

- **`test_achievable_target`** (line 266): `assert result > Decimal("0.00")` -- should verify exact extra payment amount.
- **`test_summary_with_extra`** (line 233): `assert summary.months_saved > 0` and `interest_saved > 0` -- directional only.

#### MISSING COVERAGE

- **`calculate_remaining_months`** function has **zero test coverage.**
- No test for negative interest rate, very high rate (50%), or `payment_day=31` crossing February.

---

### 31. Escrow Calculator

**File:** `tests/test_services/test_escrow_calculator.py`

#### ASSERTION DEPTH

All tests verify exact Decimal values. **PASS.**

#### MISSING NEGATIVE PATHS

- No test for negative `annual_amount`, `annual_amount=0`, `years=0`, negative inflation rate.

---

### 32. Growth Engine

**File:** `tests/test_services/test_growth_engine.py`

#### ASSERTION SMELL CHECK

- **`test_basic_growth_no_contributions`** (line 120): `assert result[0].end_balance > Decimal("10000")` -- **directional.**
- **`test_negative_return_rate`** (line 264): `assert result[0].growth < ZERO` -- **directional.**
- **`test_with_periodic_contributions`** (line 145): `assert result[-1].end_balance > Decimal("10000") + Decimal("1500")` -- **directional.**
- **Recommendation:** Compute exact expected growth and assert equality.

#### REALISTIC DATA GAPS

- Uses 10 periods. `test_period_count_twenty_years` verifies 520-period generation but `project_balance` is never tested at that scale.

---

### 33. Interest Projection

**File:** `tests/test_services/test_interest_projection.py`

**This is the gold standard for financial calculation testing in this codebase.** Every test verifies exact Decimal values with manual calculations in comments.

#### MISSING NEGATIVE PATHS (minor)

- No leap year February period test.
- No cross-month period for monthly compounding.
- No very high APY (100%) test.

---

### 34. Investment Projection

**File:** `tests/test_services/test_investment_projection.py`

#### ASSERTION SMELL CHECK

- **`test_employer_flat_percentage`** (line 116): `assert result.employer_params is not None` -- followed by specific checks, acceptable.

#### MISSING NEGATIVE PATHS

- No test for `investment_params=None`, empty `all_periods`, soft-deleted transfers, zero/negative deduction amounts.

---

### 35. Pension Calculator

**File:** `tests/test_services/test_pension_calculator.py`

#### ASSERTION SMELL CHECK

- **`test_very_short_service`** (line 126): `assert result.years_of_service < Decimal("1.00")` and `result.annual_benefit > ZERO` -- **directional.**
- **`test_with_recurring_raise`** (line 187): `assert result[0][1] > Decimal("80000")` -- **directional.** Should verify exact: `80000 * 1.03 = 82400.00`.

#### MISSING NEGATIVE PATHS

- No test for `benefit_multiplier=0` or negative multiplier.
- No test for `start_year > end_year`.
- No test for multiple overlapping raises in same year.
- No test for `hire_date` after `retirement_date`.

---

### 36. Retirement Gap Calculator

**File:** `tests/test_services/test_retirement_gap_calculator.py`

#### ASSERTION SMELL CHECK

- **`test_surplus`** (line 21): Test name says "surplus" but asserts a shortfall (`< ZERO`). **Naming bug** -- should be `test_shortfall_when_savings_insufficient`.
- **`test_after_tax_view_traditional`** (line 83): `assert result.after_tax_surplus_or_shortfall is not None` -- **shallow**.

#### MISSING NEGATIVE PATHS

- No test for `safe_withdrawal_rate=0` (division by zero risk).
- No test for `estimated_tax_rate=1.0` (100% tax) or negative tax rate.
- No test for negative `net_biweekly_pay` or `monthly_pension_income`.

---

### 37. Mortgage Routes

**File:** `tests/test_routes/test_mortgage.py`

#### ASSERTION DEPTH

- 5 tests are status-code-only: `test_dashboard_idor` (70), `test_dashboard_wrong_type` (97), `test_dashboard_nonexistent` (104), `test_params_update_validation` (138), `test_params_update_idor` (147).
- **`test_params_update_validation`** (line 138): Does NOT verify DB was unchanged.
- **`test_params_update_idor`** (line 147): Does NOT verify other user's mortgage was not modified.

#### MISSING NEGATIVE PATHS

- No auth tests for POST endpoints (only GET dashboard covered).
- No malformed POST for escrow (non-numeric, negative, missing name).
- No malformed POST for rate change (missing date, negative rate, rate=0).
- No nonexistent account for params/rate/escrow.
- No IDEM tests.

#### MISSING CATEGORY COVERAGE

| Category | Status                                                                                   |
| -------- | ---------------------------------------------------------------------------------------- |
| BE       | **MISSING** -- no 0% rate, 0 principal, term=1, payment_day edge cases                    |
| SM       | **MISSING** -- no ARM rate change workflow, no refinance scenarios                        |
| FIN      | Partial -- `test_percentage_input_stored_as_decimal` good; no amortization precision test |

---

### 38. HYSA Routes

**File:** `tests/test_routes/test_hysa.py`

#### ASSERTION DEPTH

- **`test_hysa_detail_idor`** (line 47): Status-code-only.
- **`test_hysa_params_update_idor`** (line 125): Does NOT verify other user's params were not modified.

#### ASSERTION SMELL CHECK

- **`test_create_hysa_account_auto_params`** (line 157): `assert params is not None` -- minor smell.

#### MISSING NEGATIVE PATHS

- No auth test for `POST /accounts/<id>/hysa/params`.
- No test for `apy=abc`, `apy=-0.5`, `compounding_frequency=bogus`.
- No IDEM tests.

---

### 39. Auto Loan Routes

**File:** `tests/test_routes/test_auto_loan.py`

#### ASSERTION DEPTH

- 5 tests are shallow: `test_dashboard_idor` (70), `test_dashboard_wrong_type` (97), `test_params_update` (114), `test_params_update_validation` (131), `test_params_update_idor` (140).
- **`test_params_update_validation`** (line 131): Does NOT verify DB unchanged.
- **`test_params_update_idor`** (line 140): Does NOT verify other user's data unmodified.
- **`test_create_auto_loan_account`** (line 162): Does not verify DB record created.

#### MISSING NEGATIVE PATHS

- No malformed POST data tests (non-numeric principal, negative interest, `payment_day=0`).
- No nonexistent account for params update.
- No IDEM tests.

#### MISSING CATEGORY COVERAGE

| Category | Status                                                             |
| -------- | ------------------------------------------------------------------ |
| BE       | **MISSING**                                                        |
| SM       | **MISSING**                                                        |
| FIN      | **MISSING** -- no interest rate or principal amount precision tests |

---

### 40. Investment Routes

**File:** `tests/test_routes/test_investment.py`

#### ASSERTION DEPTH

- **`test_dashboard_idor`** (line 83), **`test_dashboard_nonexistent`** (line 98), **`test_dashboard_brokerage`** (line 103): Status-code-only.
- **`test_params_idor`** (line 182): Does NOT verify other user's account unmodified.
- **`test_validation_error`** (line 203): Does NOT verify DB unchanged.

#### ASSERTION SMELL CHECK

- `test_create_params` (line 136), `test_create_params_with_employer_match` (line 177): `assert params is not None`.

#### MISSING NEGATIVE PATHS

- **No unauthenticated access tests at all.**
- Only one validation error test. Missing: negative returns, missing employer fields, contribution limit in past.
- No IDEM tests.

---

### 41. Retirement Routes

**File:** `tests/test_routes/test_retirement.py`

**Largest file in batch 2 but highest ratio of shallow tests.**

#### ASSERTION DEPTH

- **`test_dashboard_empty`** (line 138), **`test_dashboard_with_pension`** (line 143), **`test_pension_list`** (line 160), **`test_edit_pension_form`** (line 184): All status-code-only.
- **`test_update_settings_partial`** (line 266): Does not verify SWR was saved.
- **`test_dashboard_projects_multiple_accounts`** (line 358): Does not verify both accounts appear.
- **`test_gap_with_swr_param`** (line 425): Asserts `b"3" in resp.data` -- matches any digit "3" anywhere. **Effectively meaningless.**

#### ASSERTION SMELL CHECK

- **`test_gap_returns_fragment`** (line 416): Uses `or` assertion -- `b"Configure your salary" in resp.data or b"Gap" in resp.data` -- either branch passing masks the other.

#### MISSING NEGATIVE PATHS

- Auth tests only for GET dashboard; **missing for all POST endpoints and pension CRUD.**
- **No malformed POST tests at all** for pension or settings.
- No nonexistent pension ID tests.
- No IDEM tests.

---

### 42. Onboarding Routes

**File:** `tests/test_routes/test_onboarding.py`

#### ASSERTION DEPTH

- **`test_banner_not_shown_to_anonymous_user`** (line 77): Only `status_code in (302, 303)` -- no redirect target verified.

#### ASSERTION SMELL CHECK

- **`test_banner_shows_checkmarks_for_completed_steps`** (line 62): Uses `or` assertion that can mask failures.

#### MISSING NEGATIVE PATHS

- No test for partially-complete setup permutations (only "none complete" and "periods complete" tested).

---

## P3 -- Low

### 43. Auth Routes

**File:** `tests/test_routes/test_auth.py` (7 tests)

#### ASSERTION SMELL CHECK

- **`test_invalidate_sessions`** (line 202): `session_invalidated_at is not None` -- should verify timestamp is recent.
- **`test_password_change_invalidates_sessions`** (line 247): Same.

#### MISSING NEGATIVE PATHS

- No test for login with non-existent email (vs. wrong password).
- No test for malformed POST (missing email/password fields entirely).
- No XSS in login fields.
- No MFA brute force / rate limiting on `/mfa/verify`.
- No IDOR on MFA config (user A accessing user B's MFA).
- No IDEM tests for password change or MFA setup.
- No CSRF token validation tests.

---

### 44. Error Routes

**File:** `tests/test_routes/test_errors.py`

All tests check status codes AND body content AND headers. **PASS.**

#### MISSING NEGATIVE PATHS (minor)

- No 405 Method Not Allowed test.

---

### 45. Health Routes

**File:** `tests/test_routes/test_health.py`

Well-structured with specific JSON assertions. **PASS.**

#### MISSING NEGATIVE PATHS (minor)

- No test confirming only GET is accepted (POST → 405).

---

### 46. Audit Triggers

**File:** `tests/test_integration/test_audit_triggers.py`

#### ASSERTION SMELL CHECK

- 5 tests use `assert len(rows) >= 1` when exactly 1 record was created. **Should use `== 1`.**
- **`test_executed_at_is_populated`** (line 283): `rows[-1]["executed_at"] is not None` -- classic "not None" smell. Should verify timestamp is recent.
- **`test_db_user_is_populated`** (line 292): `rows[-1]["db_user"] is not None` and `len > 0` -- should match expected PostgreSQL role.

#### MISSING NEGATIVE PATHS

- No test for audit logging during a rolled-back transaction (should NOT create rows).
- No test for UPDATE of multiple columns verifying `changed_fields` captures all.

---

### 47. Adversarial / Hostile QA

**File:** `tests/test_adversarial/test_hostile_qa.py`

#### ASSERTION DEPTH

- **`test_grid_negative_periods_param`** (line 467), **`test_grid_extreme_periods_param`** (line 478): Status-code-only.
- **`test_delete_account_with_transfers_blocked`** (line 192), **`test_delete_category_with_transactions_blocked`** (line 211): Status-code-only.
- **`test_access_other_users_account`** (line 762): Status-code-only.

#### MISSING NEGATIVE PATHS

- No test for `projected → cancelled → projected` (double reversal).
- No test for `settled → projected` (revert settled).
- No test for SQL injection in query parameters.
- No test for XSS payloads via PATCH.
- No IDOR on salary profiles, categories, or transfer templates.

---

### 48. Audit Fixes

**File:** `tests/test_audit_fixes.py` (15 tests)

#### ASSERTION SMELL CHECK

- **`test_duplicate_transfer_template_name_fails`** (line 452): `pytest.raises(Exception)` -- too broad. Should be `pytest.raises(IntegrityError)`.
- **`test_duplicate_savings_goal_name_fails`** (line 476): Same -- `pytest.raises(Exception)`.

#### MISSING NEGATIVE PATHS

- No IDOR test for updating (PATCH) transfers/goals with foreign account IDs (only create is tested).
- No test for self-transfer (`from_account_id == to_account_id`).

---

### 49. Scripts

**Files:** `tests/test_scripts/test_audit_cleanup.py`, `test_integrity_check.py`, `test_reset_mfa.py`

#### ASSERTION SMELL CHECK (Integrity Check)

- 11 tests use `assert detail_count >= 1` when exactly 1 anomaly was created. **Should use `== 1`.**

#### MISSING COVERAGE (Integrity Check)

- Many individual check IDs lack dedicated tests: FK-02 through FK-04, FK-06 through FK-09, FK-11 through FK-13, OR-01, OR-04, OR-05, BA-02, BA-05, DC-02 through DC-04, DC-06.

#### MISSING NEGATIVE PATHS (Audit Cleanup)

- No test for `execute_cleanup(days=-1)` (negative retention).
- No test for cleanup during rolled-back transaction.

#### MISSING NEGATIVE PATHS (Reset MFA)

- No test for `reset_mfa("")` (empty email).
- No test for partial MFA state (`is_enabled=False` with `totp_secret_encrypted` set).

---

### 50. Utils

**Files:** `tests/test_utils/test_log_events.py`, `test_logging_config.py`

#### ASSERTION SMELL CHECK

- **`test_log_includes_remote_addr`** (line 106): `hasattr(summaries[-1], "remote_addr")` -- checks existence, not value. Should assert `== "127.0.0.1"`.

#### CODE QUALITY

- **`test_slow_request_logs_at_warning`** (lines 48-94): Contains ~20 lines of dead code and comments documenting failed approaches. Needs refactoring.

---

### 51. Performance Tests

**File:** `tests/test_performance/test_trigger_overhead.py`

#### MISSING COVERAGE

- Only INSERT operations tested. No UPDATE or DELETE trigger overhead tests.
- Tests are inherently flaky due to system load. No statistical significance or minimum absolute time threshold.

---

### 52. Conftest (Fixtures)

**File:** `tests/conftest.py`

#### DESIGN ISSUES

- **`auth_client`** (line 243): Logs in via POST but does not assert success. If login fails silently, all downstream tests fail with confusing errors. **Recommendation:** Add `assert resp.status_code == 302`.
- **`seed_periods`**: Only 10 periods; production has ~52.
- **No shared "second user" fixture.** At least 5 test files independently create second users with varying implementations.
- **No shared fixtures for**: user with MFA enabled, user with salary profile, user with transfers/templates, bulk transaction seeding.

---

## Cross-Cutting Systemic Issues

### Issue 1: No FIN Tests for Long-Horizon Decimal Accuracy

No test in the entire suite verifies penny-level balance accuracy across 52+ periods. The balance calculator, paycheck calculator, and tax calculator are all untested at production scale. This is the highest-priority gap for a financial application.

**Recommendation:** Add at minimum:

- `test_balance_52_period_penny_accuracy` -- 52 periods, 10+ txns each, verify every period balance to the penny
- `test_annual_paycheck_sum_matches_expected` -- 26 periods, verify sum of net_pay matches independently computed annual net
- `test_annual_tax_withholding_matches_annual_liability` -- 26 periods, verify total withholding ≈ annual tax

### Issue 2: Pervasive Directional Assertions on Financial Values

~25 tests use `> Decimal(...)`, `< Decimal(...)`, or range assertions instead of exact Decimal comparisons on financial outputs. Files: `test_balance_calculator_debt.py` (all assertions), `test_balance_calculator_hysa.py` (3/6 tests), `test_tax_calculator.py` (7 tests), `test_paycheck_calculator.py` (1 test), `test_growth_engine.py` (3 tests), `test_pension_calculator.py` (2 tests).

**Recommendation:** Replace every directional assertion on a Decimal financial value with an exact computed expectation. These functions are deterministic -- there is no reason for approximate assertions.

### Issue 3: Unauthenticated Access Tests Missing From Most Route Files

Only `test_auth.py` and `test_savings.py` consistently test that unauthenticated users are redirected. Missing from: `test_grid.py`, `test_accounts.py`, `test_accounts_dashboard.py`, `test_salary.py`, `test_transfers.py`, `test_templates.py`, `test_categories.py`, `test_pay_periods.py`, `test_settings.py`, `test_investment.py`, `test_charts.py`.

**Recommendation:** Add a `test_requires_auth` test to every route test file, or create a shared parametrized test that hits every protected endpoint.

### Issue 4: IDOR Tests Don't Verify DB State Unchanged

In `test_auto_loan.py`, `test_hysa.py`, `test_investment.py`, `test_mortgage.py`, and `test_retirement.py`, IDOR tests assert only `status_code == 302` without verifying the victim's data was not modified. An IDOR test must prove no state change occurred.

**Recommendation:** Every IDOR test should query the database after the blocked request and assert the target record is unchanged.

### Issue 5: IntegrityError Bubbles Up as 500 in Production

Two tests explicitly catch `IntegrityError` on double-submit, indicating routes don't handle duplicates gracefully:

- `test_transfers.py:257` (`uq_transfer_templates_user_name`)
- `test_savings.py:624` (`uq_savings_goals_user_acct_name`)

**Recommendation:** Routes should catch `IntegrityError` and flash user-friendly messages.

### Issue 6: Weak `b"form"` Assertions

4 tests assert only `b"form" in response.data` which matches any HTML page: `test_accounts.py:67`, `test_salary.py:177`, `test_transfers.py:180`, `test_templates.py:167`.

**Recommendation:** Assert form-specific content (field names, submit button text, page title).

### Issue 7: `len > 0` Assertions Without Specific Counts

Tests that create a deterministic number of records but assert only `len > 0` or `>= 1`:

- `test_savings.py:275,315,386`
- `test_templates.py:219,475`
- `test_workflows.py:108`
- `test_audit_triggers.py` (5 tests)
- `test_integrity_check.py` (11 tests)

**Recommendation:** Assert the exact expected count.

### Issue 8: No XSS Payload Tests

No test file submits XSS payloads (`<script>alert(1)</script>`) in text fields. This is a systemic gap across the entire route test suite.

**Recommendation:** Add XSS tests for at least the highest-traffic text inputs: transaction name, template name, account name, category name, salary profile name.

### Issue 9: No Shared Second-User Fixture

At least 5 test files independently create second users with varying implementations. This duplicates code and risks inconsistency.

**Recommendation:** Add a `second_user` fixture to `conftest.py` that returns user, account, and scenario for IDOR testing.

### Issue 10: `auth_client` Fixture Doesn't Verify Login Success

The `auth_client` fixture (conftest.py:243) logs in via POST but never asserts the response. If login fails silently, all downstream tests fail with confusing 302/401 errors.

**Recommendation:** Add `assert resp.status_code == 302` to the fixture.
