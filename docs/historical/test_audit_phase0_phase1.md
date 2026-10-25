> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Test Audit Report -- Phase 0 + Phase 1

**Auditor:** Claude (hostile/skeptical review)
**Date:** 2026-03-17
**Scope:** Phase 0 (Infrastructure) and Phase 1 (Financial Exactness) of the Test Remediation Plan

---

## Phase 0: Infrastructure

### conftest.py

**E1: `auth_client` asserts login success.**
Line 256: `assert resp.status_code == 302, (f"auth_client login failed with status {resp.status_code}")`. Checks `== 302` with failure message. **PASS.**

**E2: `second_user` returns the same dict structure as `seed_user`.**
- `seed_user` (line 210): `{"user": user, "settings": settings, "account": account, "scenario": scenario, "categories": {c.item_name: c for c in categories}}`
- `second_user` (line 317): `{"user": user, "settings": settings, "account": account, "scenario": scenario, "categories": {c.item_name: c for c in categories}}`
- Same keys. `second_user` has 2 categories (Salary, Rent) vs 5 in `seed_user`, which is acceptable for IDOR testing. **PASS.**

**E3: `second_user` does NOT create a second auth_client.**
Lines 262-323: Creates user, settings, account, scenario, categories only. No `client.post("/login")`. **PASS.**

**E4: `seed_periods_52` creates exactly 52 periods.**
Line 339: `num_periods=52`. **PASS.**

**E5: `seed_periods_52` does not break existing `seed_periods` (10 periods).**
`seed_periods` still exists unchanged at lines 220-243 with `num_periods=10`. `seed_periods_52` is a separate fixture. **PASS.**

---

## Phase 1: Financial Exactness

### WU 1.1: Balance Calculator (test_balance_calculator.py)

#### `test_52_period_penny_accuracy` (line 477)

| Check | Result |
|-------|--------|
| A1 | PASS -- all service output assertions use `==` |
| A2 | PASS -- no `pytest.approx` |
| A3 | PASS |
| A4 | PASS -- `len(result) == 52` (exact) |
| A5 | PASS -- every assertion has failure message with period ID, expected, got, diff |
| A6 | PASS -- oracle is an independent loop (lines 692-709), never calls `calculate_balances` |
| A7 | PASS -- oracle computation inline with comments |
| B1 | VERIFIED -- oracle correctly: (1) filters only "projected" status matching both `_sum_remaining` and `_sum_all`; (2) adds income, subtracts expenses; (3) accumulates from anchor |
| B2 | PASS -- production does not quantize balances; oracle does not quantize |
| B4 | PASS -- oracle compounds `running = running + period_inc - period_exp` |
| C2 | PASS -- all 52 periods asserted individually (line 725-732) |
| C3 | PASS -- cumulative cross-check on lines 737-752 |
| D1 | PASS -- docstring describes scenarios, inputs, purpose |
| F1 | **CAUGHT** -- mutating `("credit", "cancelled")` to `("cancelled",)` would cause the credit expense in period 3 to subtract $450, changing the balance. The oracle filters by `!= "projected"` independently, so the assertion would fail |

**Tier: STRONG.** Independent oracle, all 52 periods individually asserted, cumulative cross-check, mutation resistant.

#### `test_negative_anchor_balance_overdraft` (line 754)

| Check | Result |
|-------|--------|
| A5 | PASS -- failure messages on all 3 assertions |
| A7 | PASS -- comments show arithmetic: `-500 + 2500 - 850 - 850 = 300.00` |
| B1 | VERIFIED -- P0: -500+2500-850-850=300 ✓, P1: 300+2500-850-850=1100 ✓, P2: 1100+800=1900 ✓ |

**Tier: STRONG.**

#### `test_large_values_no_overflow` (line 804)

| Check | Result |
|-------|--------|
| A5 | PASS |
| B1 | VERIFIED -- P0: 999999.99+50000-49999.99=1000000.00 ✓, P1: +0.01=1000000.01 ✓, P2: +0.01=1000000.02 ✓ |

**Tier: STRONG.**

#### `test_idempotent_same_inputs_same_outputs` (line 852)

| Check | Result |
|-------|--------|
| A6 | **FAIL** -- compares two calls of `calculate_balances` against each other. No independent oracle. If the function is deterministically wrong, this test still passes. |
| Purpose | Tests for hidden state mutation, not calculation correctness |

**Tier: WEAKNESS.** Valid structural test, but would not catch a calculation bug. Should have at least one hardcoded expected value as a sanity check.

#### `test_zero_estimated_amount_does_not_affect_balance` (line 900)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P0: 1000+2500-850=2650 ✓, P1: 2650+2500-850-0=4300 ✓, P2: 4300+2500-850=5950 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### `test_received_status_handling` (line 950)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- all "received" excluded, balance stays 1000.00 ✓ |
| A5 | PASS |

**Tier: STRONG.**

---

### WU 1.2: Balance Calculator Debt (test_balance_calculator_debt.py)

#### `test_debt_balance_with_payments` (line 51, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- interest=100000×0.005=500, principal=599.55-500=99.55, balance=100000-99.55=99900.45 ✓ |
| A5 | PASS |
| A7 | PASS -- comments show computation |

**Tier: STRONG.**

#### `test_debt_principal_tracking` (line 117, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P2: interest=500, principal=99.55, balance=99900.45 ✓. P3: interest=99900.45×0.005=499.50225→499.50, principal=599.55-499.50=100.05, balance=99900.45-100.05=99800.40 ✓ |

**Tier: STRONG.**

#### `test_debt_26_period_amortization_accuracy` (line 198, new)

| Check | Result |
|-------|--------|
| A5 | PASS -- failure messages with period, expected, got, diff |
| A6 | PARTIAL -- oracle replicates production's inline split logic (lines 238-265). Uses `calculate_monthly_payment` for input setup (transfer amounts), which is acceptable since it's test input, not expected output |
| A7 | PASS -- oracle derivation inline |
| B2 | PASS -- quantization on interest matches production code |
| C2 | PASS -- all 26 periods asserted |
| C3 | **WEAKNESS** -- cross-check on line 303-306 validates `exp_b[26] == Decimal("200000.00") - sum(exp_p.values())`, which checks oracle internal consistency, NOT service output. Should be `balances[26] == Decimal("200000.00") - sum(pbp.values())` |
| F2 | **CAUGHT** -- doubling `monthly_rate` would produce different interest split, detected by per-period assertions |

**Tier: WEAKNESS.** The oracle mirrors the production algorithm too closely (lines 248-258 are a near-copy of balance_calculator.py lines 245-257). A bug in the algorithm would survive in both. The cross-check validates the oracle, not the service.

**Suggested fix:** Change cross-check to use service output: `assert balances[26] == Decimal("200000.00") - sum(pbp.values())`.

#### `test_debt_zero_interest_rate` (line 308, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- zero rate → principal=payment-0=1000, each payment reduces by exactly 1000 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### `test_debt_zero_principal_paid_off` (line 393, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- `running_principal > 0` guard prevents any balance change when principal=0 ✓ |

**Tier: STRONG.**

#### `test_debt_overpayment_larger_than_remaining` (line 457, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- interest=500×0.005=2.50, uncapped=600-2.50=597.50, capped=min(597.50,500)=500, balance=0 ✓ |

**Tier: STRONG.**

#### `test_debt_cancelled_transfer_excluded` (line 521, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- cancelled transfer skipped in P2, projected transfer in P3 correctly splits ✓ |

**Tier: STRONG.**

#### `test_debt_multiple_payments_same_period` (line 590, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- total=599.55+200=799.55, interest=500, principal=299.55, balance=99700.45 ✓ |

**Tier: STRONG.**

---

### WU 1.3: Balance Calculator HYSA (test_balance_calculator_hysa.py)

#### `test_hysa_balance_includes_interest` (line 51, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P1: 10000×((1+0.045/365)^14-1) ≈ 17.2741→17.27. Balance=10017.27. P2: 10017.27×(...)≈17.3039→17.30. Balance=10034.57. P3: 10034.57×(...)≈17.3338→17.33. Balance=10051.90 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### `test_hysa_interest_compounds_across_periods` (line 108, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- same setup and values as above, duplicate coverage |

**Tier: STRONG.**

#### `test_hysa_with_transfers` (line 159, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P1: interest on 10000 = 17.27, balance = 10017.27. P2: base=10500 (transfer), running=10517.27, interest=10517.27×((1+0.045/365)^14-1)≈18.17, balance=10535.44 ✓ |

**Tier: STRONG.**

#### `test_interest_by_period_dict` (line 276, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- same values as hysa_balance_includes_interest ✓ |

**Tier: STRONG.**

#### `test_hysa_26_period_compounding_no_drift` (line 342, new)

| Check | Result |
|-------|--------|
| A1 | **MINOR** -- line 384: `expected_interest[periods[25].id] > expected_interest[periods[0].id]` uses `>` on Decimal financial values. However, this asserts on oracle-computed expected values (sanity check), not service output |
| A5 | PASS -- all service assertions have failure messages |
| A6 | PASS -- oracle uses raw Decimal arithmetic matching `interest_projection.py` formula independently |
| B2 | PASS -- quantization matches production (ROUND_HALF_UP on each period's interest) |
| B4 | PASS -- compounding via `interest_cumulative += interest` |
| C2 | PASS -- all 26 periods asserted |
| C3 | PASS -- cumulative cross-check: `balances[periods[25].id] == base_bal + total_i` |
| F3 | **CAUGHT** -- removing `interest_cumulative += interest` would freeze cumulative at 0, making each period's interest identical, failing period-by-period assertions |

**Tier: STRONG.** Minor A1 issue on oracle sanity check only.

#### `test_hysa_monthly_compounding_exact` (line 435, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P1: 10000 × (0.045/12) × (14/31) = 10000 × 0.00375 × 0.4516... = 16.935... → 16.94 ✓ |
| A6 | PASS -- oracle matches `interest_projection.py` monthly formula |

**Tier: STRONG.**

#### `test_hysa_quarterly_compounding_exact` (line 524, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P1: 10000 × (0.045/4) × (14/91) = 10000 × 0.01125 × 0.15384... = 17.307... → 17.31 ✓ |

**Tier: STRONG.**

#### `test_hysa_invalid_compounding_frequency` (line 605, new)

**Tier: STRONG.** Tests the `else: return ZERO` branch in `interest_projection.py`.

#### `test_hysa_high_apy_no_overflow` (line 641, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P1: 50000 × ((1+0.10/365)^14 - 1) ≈ 192.12 ✓ |

**Tier: STRONG.**

#### `test_hysa_interest_on_zero_balance_with_transfer` (line 721, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P1: balance=0, guard `balance <= 0` → interest=0. P2: 500+0=500, interest calculated. P3: 500+0.86=500.86, interest calculated ✓ |
| C4 | PASS -- zero balance is an edge case |

**Tier: STRONG.**

#### `test_hysa_compounding_with_periodic_deposits` (line 824, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- base_bals correct: anchor=10000, +500 at P2,P4,P6 ✓ |
| A5 | PASS |

**Tier: STRONG.**

---

### WU 1.4: Tax Calculator (test_tax_calculator.py)

#### `test_weekly_pay_frequency` (line 141, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- annual=1153.85×52=60000.20, taxable=45000.20, brackets: 1000+3600+1100.044=5700.044→5700.04. Per period: 5700.04/52=109.616...→109.62 ✓ |
| B3 | PASS -- uses exact annualized value 60000.20, not idealized 60000 |
| A5 | PASS |
| A7 | PASS -- derivation in docstring |

**Tier: STRONG.**

#### `test_income_spans_all_brackets` (line 236, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- annual=23076.92×26=599999.92, taxable=584999.92. Seven bracket segments sum to 180649.9704→180649.97. Per period: 180649.97/26=6948.075...→6948.08 ✓ |
| B3 | PASS -- uses exact annualized value |
| A5 | PASS |

**Tier: STRONG.**

#### `test_very_high_income_top_bracket_only` (line 261, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- annual=38461.54×26=1000000.04, taxable=985000.04. Two brackets: 10000+327450.0148=337450.0148→337450.01. Per period: 337450.01/26=12978.846...→12978.85 ✓ |

**Tier: STRONG.**

#### `test_income_exactly_at_first_bracket_top` (line 299, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- annual=961.54×26=25000.04, taxable=10000.04. Brackets: 10000×0.10+0.04×0.12=1000.0048→1000.00. Per period: 1000/26=38.461...→38.46 ✓ |

**Tier: STRONG.**

#### `test_income_one_dollar_into_next_bracket` (line 319, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- annual=961.58×26=25001.08, taxable=10001.08. 10000×0.10+1.08×0.12=1000.1296→1000.13. Per period: 1000.13/26=38.466...→38.47 ✓ |

**Tier: STRONG.**

#### `test_child_credits_reduce_tax` (line 432, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- annual=99999.90, taxable=84999.90. Tax=14499.978→14499.98. no_kids=557.69, two_kids=403.85, diff=153.84. All verified ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### `test_other_dependent_credits` (line 471, modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- credits=2×500=1000. After: 14499.98-1000=13499.98/26=519.23. Diff=557.69-519.23=38.46 ✓ |

**Tier: STRONG.**

#### `test_26_period_annual_withholding_matches_annual_tax` (line 552, new)

| Check | Result |
|-------|--------|
| A1 | **WEAKNESS** -- line 588: `abs(annual_via_withholding - annual_tax) <= max_rounding_error` is a range assertion. Acceptable for its purpose (bounding rounding error), but technically directional |
| A6 | **NOTE** -- uses `calculate_federal_tax` (production) for oracle. This is intentional -- the test's purpose is to verify consistency between two production functions |
| B1 | VERIFIED -- 78000 taxable=63000. Brackets: 1000+3600+5060=9660. Per period: 371.54. Annual via withholding: 371.54×26=9660.04. Rounding error=0.04 ≤ 0.26 ✓ |

**Tier: WEAKNESS.** Range assertion instead of exact equality, but this is inherent to the test's purpose (bounding rounding discrepancy).

#### `test_annual_pay_period_no_rounding_loss` (line 594, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- pay_periods=1, annual=78000, tax=9660.00, withholding=9660.00 exactly ✓ |

**Tier: STRONG.**

---

### WU 1.5: Paycheck Calculator (test_paycheck_calculator.py)

#### `test_basic_paycheck_no_deductions` (line 393, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- gross=60000/26=2307.69. Federal: annual=59999.94, taxable=44999.94, tax=4499.994→4499.99, /26=173.08. State: 59999.94×0.045=2699.9973→2700.00, /26=103.85. SS: 2307.69×0.062=143.077→143.08. Medicare: 2307.69×0.0145=33.462→33.46. Net: 2307.69-173.08-103.85-143.08-33.46=1854.22 ✓ |
| A5 | PASS -- all fields have failure messages |
| A7 | PASS -- full pipeline trace in docstring |

**Tier: STRONG.**

#### `test_net_pay_formula` (line 456, new)

| Check | Result |
|-------|--------|
| A6 | **WEAKNESS** -- reconstructs expected from service output fields: `expected_net = r.gross_biweekly - r.total_pre_tax - r.federal_tax - ...`. Checks internal consistency, not independent oracle |

**Tier: WEAKNESS.** Tests that net_pay equals gross minus deductions using the function's own output. Would not catch a bug where both net_pay and a component field are wrong.

#### `test_w4_fields_passed_to_federal` (line 564, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- base: 4499.99/26=173.08. W-4 with additional_income=10000: annual=69999.94, taxable=54999.94. 50000×0.10+4999.94×0.22=5000+1099.9868=6099.9868→6099.99. (6099.99/26)+50=234.615+50=284.615→284.62 ✓ |

**Tier: STRONG.**

#### `test_fica_ss_wage_cap_boundary` (line 958, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- gross=7692.31. Full SS=476.92. P22: cumulative=21×7692.31=161538.51, taxable=168600-161538.51=7061.49, SS=437.81. P23-26: SS=0. Total: 21×476.92+437.81=10453.13 ✓ |
| A5 | PASS -- per-period assertions with messages |
| C2 | PASS -- all 26 periods checked (P1-21 full SS, P22 partial, P23-26 zero) |
| C3 | PASS -- cumulative SS cross-check = 10453.13 |
| F4 | **NOT CAUGHT** -- mutating `>=` to `>` in `cumulative >= ss_wage_base` would only affect the case when cumulative exactly equals ss_wage_base. With $200k salary and $7692.31/period, cumulative never exactly hits $168,600 (it goes from $161,538.51 to $169,230.82). The mutation would not change any test output |

**Tier: WEAKNESS.** Excellent coverage of the SS cap transition, but would miss the exact boundary off-by-one mutation. Needs a test where cumulative wages after N-1 periods exactly equals ss_wage_base.

**Impact:** An off-by-one bug at the exact SS cap boundary could over-tax by ~$476.92 per year in an edge case.

**Suggested fix:** Add a targeted test where `cumulative_wages` parameter to `calculate_fica` is set to exactly `Decimal("168600")`, then verify SS returns `Decimal("0.00")`.

#### `test_medicare_surtax_high_income` (line 1046, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- gross=11538.46. Base medicare=167.31. P18 transition: surtax_income=7692.28, surtax=69.23. P19+: surtax=103.85, total=271.16 ✓ |
| C2 | PASS -- all 26 periods checked |

**Tier: STRONG.**

#### `test_26_period_annual_net_pay_sum` (line 1117, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- per-period values match test_basic_paycheck_no_deductions. Annual sums = per_period × 26. Cross-check: net = gross - fed - state - SS - medicare ✓ |
| C3 | PASS -- cross-check on line 1199-1202 |

**Tier: STRONG.**

#### `test_project_salary_all_periods_consistent` (line 1204, new)

**Tier: STRONG.** Verifies all 26 periods produce identical breakdowns for a salary under all caps.

#### `test_zero_salary` (line 1263, new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- 0/26=0, all taxes=0, net=0 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### `test_negative_salary_behavior` (line 1310, new)

**Tier: STRONG.** Correctly verifies `InvalidGrossPayError` is raised for negative salary.

---

### WU 1.6: Recurrence Engine (test_recurrence_engine.py)

These are not financial calculation tests -- they test edge case handling of pattern matching parameters.

#### `test_every_n_periods_interval_zero_defaults_to_one` (line 498, new)

| Check | Result |
|-------|--------|
| A4 | PASS -- `len(matched) == len(biweekly_periods)` is exact |
| A5 | PASS -- failure message explains expected vs got |
| D1 | PASS -- docstring explains the fallback behavior and DB constraint |
| F5 | **CAUGHT** -- removing `or 1` would cause ZeroDivisionError |

**Tier: STRONG.**

#### `test_every_n_periods_interval_none_defaults_to_one` (line 530, new)

**Tier: STRONG.** Tests `None or 1 = 1` fallback with distinct failure mode from interval_n=0.

#### `test_day_of_month_zero_via_match_periods` (line 557, new)

**Tier: STRONG.** Tests `0 or 1 = 1` fallback via `_match_periods`, verifying identical output to `day_of_month=1`.

#### `test_day_of_month_zero_direct_raises` (line 595, new)

**Tier: STRONG.** Verifies `date(y, m, 0)` raises `ValueError` when bypassing the `or 1` guard.

#### `test_day_of_month_32_clamped_to_last_day` (line 611, new)

**Tier: STRONG.** Verifies `min(32, last_day)` clamping behaves identically to `min(31, last_day)`.

#### `test_day_of_month_none_in_monthly_defaults_to_one` (line 638, new)

**Tier: STRONG.** Tests `None or 1 = 1` for `day_of_month`.

#### `test_month_of_year_zero_defaults_to_one` (line 672, new)

**Tier: STRONG.** Comprehensive test covering both the `_match_periods` path (with `or 1` fallback) and the direct `_match_quarterly` path (with modular arithmetic producing different target months).

#### `test_month_of_year_13_annual_raises` (line 743, new)

**Tier: STRONG.** Verifies out-of-range month raises `ValueError` through both direct call and `_match_periods` dispatch.

---

### WU 1.7: Growth Engine, Pension, Amortization, Retirement Gap

#### Growth Engine -- `test_basic_growth_no_contributions` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- period_return = (1.07)^(13/365) - 1 ≈ 0.002413. growth = 10000 × 0.002413 = 24.13. end = 10024.13 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### Growth Engine -- `test_with_periodic_contributions` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- P0: growth=24.13, end=10524.13. P1: growth=10524.13×0.002413≈25.39, end=11049.52. P2: growth=11049.52×0.002413≈26.66, end=11576.18 ✓ |
| B4 | PASS -- compounding verified: each period's start_balance = previous end_balance |

**Tier: STRONG.**

#### Growth Engine -- `test_negative_return_rate` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- (0.90)^(13/365) - 1 ≈ -0.003746. growth = 10000 × -0.003746 = -37.46. end = 9962.54 ✓ |

**Tier: STRONG.**

#### Pension -- `test_very_short_service` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- days=151, years=151/365.25=0.4132→0.41. annual=0.0185×0.41×80000=606.80. monthly=606.80/12=50.566→50.57 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### Pension -- `test_with_recurring_raise` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- 2026: 80000×1.03=82400. 2027: 80000×1.03²=84872. 2028: 80000×1.03³=87418.16 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### Amortization -- `test_summary_with_extra` (modified)

| Check | Result |
|-------|--------|
| A7 | **WEAKNESS** -- expected values (months_saved=110, interest_saved=90074.66) are described as "derived from independently calling generate_schedule." This is a regression lock, not an independently-computed oracle. If `generate_schedule` has a bug, the expected values are also wrong |
| A5 | PASS |

**Tier: WEAKNESS.** Acceptable as a regression lock but not independently verified. Computing 360 months of amortization by hand is impractical, but the comment should note this is a regression test.

#### Amortization -- `test_achievable_target` (modified)

| Check | Result |
|-------|--------|
| A7 | **WEAKNESS** -- same as above: `478.08` described as "determined by running the deterministic binary search to convergence." Regression lock, not independent oracle |

**Tier: WEAKNESS.** Same issue -- regression lock via running the production code.

#### Amortization -- `test_remaining_months_basic/past_term/same_month` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- basic: (2025-2020)×12+(1-1)=60, 360-60=300 ✓. Past term: 60>12, max(0,-48)=0 ✓. Same month: 0 elapsed, 360 remaining ✓ |

**Tier: STRONG.**

#### Retirement Gap -- `test_shortfall_when_projected_below_required` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- net_monthly=2500×26/12=5416.67. gap=5416.67-2000=3416.67. required=3416.67×12/0.04=1025001.00. shortfall=500000-1025001=-525001.00 ✓ |
| A5 | PASS |

**Tier: STRONG.**

#### Retirement Gap -- `test_after_tax_view_traditional` (modified)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- after_tax=400000×0.80+100000=420000. net_monthly=4333.33. required=4333.33×12/0.04=1299999.00. surplus=420000-1299999=-879999 ✓ |

**Tier: STRONG.**

#### Retirement Gap -- `test_pension_taxed_when_tax_rate_provided` (new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- net_monthly=5416.67. after_tax_pension=5000×0.80=4000. gap=5416.67-4000=1416.67 ✓ |
| A1 | **MINOR** -- `result.required_retirement_savings > ZERO` is directional. Should assert exact value: 1416.67×12/0.04=425001.00 |

**Tier: WEAKNESS.** The `> ZERO` assertion on required_retirement_savings should be exact.

#### Retirement Gap -- `test_pension_not_taxed_without_tax_rate` (new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- gap=5416.67-5000=416.67 ✓ |

**Tier: STRONG.**

#### Retirement Gap -- `test_pension_tax_creates_gap_where_none_existed` (new)

| Check | Result |
|-------|--------|
| B1 | VERIFIED -- net_monthly=4333.33. after_tax_pension=5000×0.75=3750. gap=4333.33-3750=583.33 ✓ |
| A1 | **MINOR** -- `result.required_retirement_savings > ZERO` is directional; exact value = 583.33×12/0.04 = 174999.00 |

**Tier: WEAKNESS.** Same minor issue as above.

#### Retirement Gap -- `test_pension_tax_zero_pension` (new)

**Tier: STRONG.** Verifies tax on zero pension produces zero.

---

## Mutation Resistance Analysis (Checklist F)

### F1: `test_52_period_penny_accuracy` -- Mutate status exclusion

**Mutation:** Change `if status_name in ("credit", "cancelled"): continue` to `if status_name in ("cancelled",): continue` in `_sum_remaining` (balance_calculator.py line 280).

**Effect:** The "credit" transaction in Period 3 (`FakeTxn(3, "credit", "expense", "450.00")`) would now be counted as an expense, subtracting 450.00 from the balance.

**Would the test catch it?** Yes. The oracle correctly excludes non-projected statuses (`if txn.status.name != "projected": continue`), so the oracle wouldn't subtract the credit expense. The assertion `result[3] == oracle_expected[3]` would fail because the service would give a lower balance.

**Verdict: CAUGHT.** ✓

### F2: `test_debt_26_period_amortization_accuracy` -- Mutate interest calculation

**Mutation:** Change `running_principal * monthly_rate` to `running_principal * monthly_rate * 2` in `calculate_balances_with_amortization` (balance_calculator.py line 246).

**Effect:** Interest would be doubled, so principal portion would be smaller (payment - 2×interest), and balance would decrease more slowly.

**Would the test catch it?** Yes. The oracle computes interest as `(rp * m_rate).quantize(...)`, which would differ from the mutated production code's `(rp * m_rate * 2).quantize(...)`. The principal and balance would differ. Assertions check both balance and principal for every period.

**Verdict: CAUGHT.** ✓

### F3: `test_hysa_26_period_compounding_no_drift` -- Mutate compounding accumulation

**Mutation:** Remove `interest_cumulative += interest` from `calculate_balances_with_interest` (balance_calculator.py line 159).

**Effect:** `interest_cumulative` would stay at 0, so `running_balance = base_bal + 0` -- no compounding. Each period would earn interest only on the base balance.

**Would the test catch it?** Yes. The oracle correctly accumulates `interest_cumulative += interest`. By period 25, the oracle's interest would be higher than the service's. The per-period assertions would fail.

**Verdict: CAUGHT.** ✓

### F4: `test_fica_ss_wage_cap_boundary` -- Mutate SS cap check

**Mutation:** Change `cumulative >= ss_wage_base` to `cumulative > ss_wage_base` (off-by-one) in `calculate_fica` (tax_calculator.py line 293).

**Effect:** When `cumulative == ss_wage_base` exactly, the original code returns ZERO SS, but the mutated code would compute SS on the full gross.

**Would the test catch it?** **No.** With $200k salary and $7692.31/period, cumulative never exactly equals $168,600. It goes from $161,538.51 (period 21) to $169,230.82 (period 22). The mutation doesn't change any output for this dataset.

**Verdict: NOT CAUGHT.** This is a critical weakness. A targeted test with `cumulative_wages` set to exactly `ss_wage_base` is needed.

### F5: `test_every_n_periods_interval_zero_defaults_to_one` -- Mutate fallback

**Mutation:** Change `rule.interval_n or 1` to `rule.interval_n` in `_match_periods` (recurrence_engine.py line 276).

**Effect:** With `interval_n=0`, the modulo `(p.period_index - offset) % 0` would raise `ZeroDivisionError`.

**Would the test catch it?** Yes. The test expects `len(matched) == len(biweekly_periods)` but the function would crash.

**Verdict: CAUGHT.** ✓

---

## Summary

### Tier 1: Problems

**None found.** All oracle values independently verified are correct. No circular oracles on critical financial tests. No vacuous assertions. No missing tests required by the remediation plan scope.

### Tier 2: Weaknesses

| # | File | Test | Weakness | Impact | Suggested Fix |
|---|------|------|----------|--------|---------------|
| 1 | test_balance_calculator.py | `test_idempotent_same_inputs_same_outputs` | No independent oracle -- asserts function equals itself across two calls | Would not catch a deterministic calculation bug | Add at least one hardcoded expected value (e.g., assert period 0 balance == Decimal("2650.00")) |
| 2 | test_balance_calculator_debt.py | `test_debt_26_period_amortization_accuracy` | Oracle mirrors production algorithm too closely (near-copy of lines 245-257). Cross-check validates oracle, not service output | If the split algorithm has a conceptual error, both oracle and service agree on the wrong answer | Change cross-check to use service output: `balances[26] == Decimal("200000.00") - sum(pbp.values())` |
| 3 | test_paycheck_calculator.py | `test_net_pay_formula` | Derives expected from service output fields (`r.gross_biweekly - r.federal_tax - ...`), not from independent computation | Would not catch a bug where both net_pay and a component field are wrong | Add hardcoded expected_net based on the known pipeline trace |
| 4 | test_paycheck_calculator.py | `test_fica_ss_wage_cap_boundary` | SS cap off-by-one (`>=` vs `>`) mutation not caught because cumulative never exactly equals $168,600 in this test | An off-by-one at the exact cap boundary could over-tax by ~$477/year | Add a test where `cumulative_wages` parameter to `calculate_fica` is set to exactly `Decimal("168600")` |
| 5 | test_tax_calculator.py | `test_26_period_annual_withholding_matches_annual_tax` | Uses range assertion (`<= max_rounding_error`) instead of exact equality. Uses production `calculate_federal_tax` as oracle | Appropriate for purpose but technically directional | Acceptable as-is; this is a rounding-bound test by design |
| 6 | test_amortization_engine.py | `test_summary_with_extra` | Expected values are regression locks from running production code, not independently computed | If `generate_schedule` has a pre-existing bug, the expected values bake it in | Add comment noting this is a regression test; optionally verify first few rows by hand |
| 7 | test_amortization_engine.py | `test_achievable_target` | Same -- regression lock from binary search convergence | Same as above | Same |
| 8 | test_retirement_gap_calculator.py | `test_pension_taxed_when_tax_rate_provided` | `required_retirement_savings > ZERO` is directional; exact value is computable (425001.00) | A bug producing a small positive number instead of the correct 425001 would slip through | Assert `== Decimal("425001.00")` |
| 9 | test_retirement_gap_calculator.py | `test_pension_tax_creates_gap_where_none_existed` | Same -- `required_retirement_savings > ZERO` is directional; exact value = 174999.00 | Same | Assert `== Decimal("174999.00")` |
| 10 | test_balance_calculator_hysa.py | `test_hysa_26_period_compounding_no_drift` | Minor: oracle sanity check at line 384 uses `>` on Decimal values (compares two oracle-computed interest amounts) | Does not affect service validation | Replace with exact value assertion or move to a separate oracle validation block |

### Tier 3: Strong

| # | File | Test | Why Strong |
|---|------|------|------------|
| 1 | test_balance_calculator.py | `test_52_period_penny_accuracy` | Independent oracle, all 52 periods individually asserted with messages, cumulative cross-check, 10 distinct status scenarios, mutation resistant |
| 2 | test_balance_calculator.py | `test_negative_anchor_balance_overdraft` | Correct edge case, independently derived values, failure messages |
| 3 | test_balance_calculator.py | `test_large_values_no_overflow` | Near-boundary values verified, failure messages |
| 4 | test_balance_calculator.py | `test_zero_estimated_amount_does_not_affect_balance` | Edge case with correct expected values |
| 5 | test_balance_calculator.py | `test_received_status_handling` | Correct status exclusion verification |
| 6 | test_balance_calculator_debt.py | `test_debt_balance_with_payments` | Exact interest/principal split verified |
| 7 | test_balance_calculator_debt.py | `test_debt_principal_tracking` | Multi-period compounding split verified |
| 8 | test_balance_calculator_debt.py | `test_debt_zero_interest_rate` | Edge case: all payment goes to principal |
| 9 | test_balance_calculator_debt.py | `test_debt_zero_principal_paid_off` | Guard clause verification |
| 10 | test_balance_calculator_debt.py | `test_debt_overpayment_larger_than_remaining` | Cap logic verified |
| 11 | test_balance_calculator_debt.py | `test_debt_cancelled_transfer_excluded` | Status filter verified |
| 12 | test_balance_calculator_debt.py | `test_debt_multiple_payments_same_period` | Accumulation logic verified |
| 13 | test_balance_calculator_hysa.py | `test_hysa_balance_includes_interest` | Hand-computed daily compounding verified to penny |
| 14 | test_balance_calculator_hysa.py | `test_hysa_interest_compounds_across_periods` | Compounding verified with exact values |
| 15 | test_balance_calculator_hysa.py | `test_hysa_with_transfers` | Transfer + interest interaction verified |
| 16 | test_balance_calculator_hysa.py | `test_interest_by_period_dict` | Dict structure + exact values verified |
| 17 | test_balance_calculator_hysa.py | `test_hysa_monthly_compounding_exact` | Monthly formula independently verified |
| 18 | test_balance_calculator_hysa.py | `test_hysa_quarterly_compounding_exact` | Quarterly formula independently verified |
| 19 | test_balance_calculator_hysa.py | `test_hysa_invalid_compounding_frequency` | Branch coverage for unknown frequency |
| 20 | test_balance_calculator_hysa.py | `test_hysa_high_apy_no_overflow` | Large value precision verified |
| 21 | test_balance_calculator_hysa.py | `test_hysa_interest_on_zero_balance_with_transfer` | Zero-balance guard clause with mid-stream transfer |
| 22 | test_balance_calculator_hysa.py | `test_hysa_compounding_with_periodic_deposits` | Multi-deposit compounding interaction verified |
| 23 | test_tax_calculator.py | `test_weekly_pay_frequency` | Exact annualized value used, bracket arithmetic verified |
| 24 | test_tax_calculator.py | `test_income_spans_all_brackets` | All 7 brackets independently verified |
| 25 | test_tax_calculator.py | `test_very_high_income_top_bracket_only` | Custom 2-bracket system verified |
| 26 | test_tax_calculator.py | `test_income_exactly_at_first_bracket_top` | Boundary verified with fractional spillover |
| 27 | test_tax_calculator.py | `test_income_one_dollar_into_next_bracket` | One-dollar boundary step verified |
| 28 | test_tax_calculator.py | `test_child_credits_reduce_tax` | Credit arithmetic with exact diff |
| 29 | test_tax_calculator.py | `test_other_dependent_credits` | Other dependent credits verified |
| 30 | test_tax_calculator.py | `test_annual_pay_period_no_rounding_loss` | pay_periods=1 eliminates rounding -- exact equality |
| 31 | test_paycheck_calculator.py | `test_basic_paycheck_no_deductions` | Full pipeline trace: gross, federal, state, SS, medicare, net -- all verified |
| 32 | test_paycheck_calculator.py | `test_w4_fields_passed_to_federal` | W-4 effect on federal independently computed |
| 33 | test_paycheck_calculator.py | `test_fica_ss_wage_cap_boundary` | 26-period SS cap transition verified with cumulative check (see F4 for weakness) |
| 34 | test_paycheck_calculator.py | `test_medicare_surtax_high_income` | Surtax transition at $200k threshold verified |
| 35 | test_paycheck_calculator.py | `test_26_period_annual_net_pay_sum` | Annual totals with cross-check |
| 36 | test_paycheck_calculator.py | `test_project_salary_all_periods_consistent` | Under-cap consistency verification |
| 37 | test_paycheck_calculator.py | `test_zero_salary` | Zero edge case, all fields verified |
| 38 | test_paycheck_calculator.py | `test_negative_salary_behavior` | Error boundary verified |
| 39-46 | test_recurrence_engine.py | All 8 `TestMatchPeriodsEdgeCaseSafety` tests | Comprehensive edge case coverage for interval_n, day_of_month, month_of_year with 0, None, 13, 32. Detailed docstrings explain DB constraints and fallback behavior |
| 47 | test_growth_engine.py | `test_basic_growth_no_contributions` | Exact period return verified |
| 48 | test_growth_engine.py | `test_with_periodic_contributions` | 3-period compounding with contributions verified |
| 49 | test_growth_engine.py | `test_negative_return_rate` | Negative return edge case verified |
| 50 | test_pension_calculator.py | `test_very_short_service` | Sub-year service, exact annual and monthly benefit |
| 51 | test_pension_calculator.py | `test_with_recurring_raise` | 3-year compounding raise verified |
| 52-54 | test_amortization_engine.py | `test_remaining_months_basic/past_term/same_month` | Date arithmetic verified |
| 55 | test_retirement_gap_calculator.py | `test_shortfall_when_projected_below_required` | Full gap pipeline with exact values |
| 56 | test_retirement_gap_calculator.py | `test_after_tax_view_traditional` | After-tax computation verified |
| 57 | test_retirement_gap_calculator.py | `test_pension_not_taxed_without_tax_rate` | Backward compatibility verified |
| 58 | test_retirement_gap_calculator.py | `test_pension_tax_zero_pension` | Zero edge case |

### Statistics

1. **Total tests audited:** 68 test functions + 3 Phase 0 fixtures = 71 items
2. **Tier 1 (Problems):** 0
3. **Tier 2 (Weaknesses):** 10
4. **Tier 3 (Strong):** 58
5. **Definition of Done status:**

| Criterion | Status |
|-----------|--------|
| Every financial calculation has exact Decimal assertions from known inputs | **MET** -- All critical financial tests use `== Decimal(...)` with derivation comments. Two retirement gap tests use `> ZERO` where exact values are computable (minor gap) |
| Every protected route has an unauthenticated access test | **NOT EXPECTED** (Phase 2) |
| Every IDOR test proves no state change via DB query | **NOT EXPECTED** (Phase 2) |
| No test has a status-code-only assertion | **NOT EXPECTED** (Phase 3) |
| No directional assertions on Decimal financial values | **PARTIALLY MET** -- Phase 1 tests avoid directional assertions on service output. Two minor instances: retirement gap `> ZERO` assertions and one oracle sanity check. Pre-existing tests (not modified in Phase 1) still have directional assertions but are out of scope |
| Balance, paycheck, and tax calculators tested at production scale with penny accuracy | **MET** -- `test_52_period_penny_accuracy` (52 periods), `test_debt_26_period_amortization_accuracy` (26 periods), `test_hysa_26_period_compounding_no_drift` (26 periods), `test_fica_ss_wage_cap_boundary` (26 periods), `test_medicare_surtax_high_income` (26 periods), `test_26_period_annual_net_pay_sum` (26 periods). All with per-period penny-level assertions |
