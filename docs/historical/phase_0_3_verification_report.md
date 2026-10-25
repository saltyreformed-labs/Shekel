> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 0-3 Verification Report

**Date:** 2026-03-19
**Test Count:** 1258 collected (1257 passed, 1 xfailed)
**Suite Status:** PASS (all tests pass cleanly)

---

## Executive Summary

Phases 0-3 are **substantially complete** with high-quality implementations across the board. The infrastructure fixtures (Phase 0) are well-designed and actively used. Phase 1 financial exactness work is excellent -- the 52-period balance test and FICA cap boundary test are exemplary. Phase 2 security work (auth gate, IDOR DB verification) is thorough. Phase 3 assertion quality improvements addressed most targeted patterns. Three residual findings need attention: 3 remaining directional assertions on financial values in Phase 1 scope, ~23 status-code-only tests remaining in Phase 3 targeted files (most are error/IDOR tests where this is debatable), and 2 `len > 0`/`>= 1` assertion smells that should be exact counts.

---

## Section 1: Verified Complete

### Phase 0: Infrastructure

- [x] **CHECK 1.1: auth_client login assertion** -- PASS
  - `tests/conftest.py:255-261`: Captures `resp = client.post("/login", ...)`, asserts `resp.status_code == 302` with message `f"auth_client login failed with status {resp.status_code}"`

- [x] **CHECK 1.2: second_user fixture** -- PASS
  - `tests/conftest.py:266-326`: Creates user `other@shekel.local`, Account (Other Checking), Scenario (Baseline), Categories (Salary, Rent). Returns dict with keys: `user`, `settings`, `account`, `scenario`, `categories`. Does NOT create a logged-in client.
  - Used by 4+ test files: `test_auth_helpers.py`, `test_data_isolation.py`, `test_fixture_validation.py`, and all IDOR route tests via `seed_second_user`/`seed_full_second_user_data`.

- [x] **CHECK 1.3: seed_periods_52 fixture** -- PASS
  - `tests/conftest.py:330-353`: Generates exactly 52 periods via `pay_period_service.generate_pay_periods`, starts `date(2026, 1, 2)`, cadence 14 days, sets `current_anchor_period_id = periods[0].id`, commits, returns list.
  - Used by: `test_balance_calculator.py` (via `seed_periods_52` fixture reference in tests).

### Phase 1: Financial Exactness

- [x] **CHECK 2.1: Balance calculator 52-period test** -- PASS
  - `tests/test_services/test_balance_calculator.py:477-752`
  - All 4 required tests exist: `test_52_period_penny_accuracy` (477), `test_negative_anchor_balance_overdraft` (754), `test_large_values_no_overflow` (804), `test_idempotent_same_inputs_same_outputs` (852).
  - The 52-period test: creates 52 FakePeriod objects, 10 hand-crafted periods with mixed statuses (projected, done, cancelled, credit, received), plus 40 standard periods. Period 4 has only cancelled+credit (zero net). Period 1 has done expense. Independent Decimal oracle loop at lines 692-709 does NOT call the service. Asserts `==` for every period at line 726. All amounts are `Decimal` types. Includes cumulative cross-check at line 746.

- [x] **CHECK 2.2: Debt exact assertions** -- PASS
  - All required tests exist: `test_debt_26_period_amortization_accuracy` (198), `test_debt_zero_interest_rate` (327), `test_debt_zero_principal_paid_off` (412), `test_debt_overpayment_larger_than_remaining` (476).
  - Zero directional assertions remaining: `grep '< Decimal|> Decimal|<= Decimal|>= Decimal'` returns empty.

- [x] **CHECK 2.3: HYSA exact assertions** -- PASS
  - All required tests exist: `test_hysa_26_period_compounding_no_drift` (342), `test_hysa_invalid_compounding_frequency` (601).
  - Zero directional assertions remaining: grep returns empty.

- [x] **CHECK 2.4: Tax calculator exact assertions** -- PASS
  - `test_26_period_annual_withholding_matches_annual_tax` exists at line 552.
  - Uses exact Decimal assertions throughout: `per_period == Decimal("371.54")`, `annual_tax == Decimal("9660.00")`, `annual_via_withholding == Decimal("9660.04")`, rounding discrepancy `== Decimal("0.04")`.
  - Zero directional assertions on financial values remaining.

- [x] **CHECK 2.5: Paycheck calculator exact assertions + FICA cap** -- PASS
  - All 4 new tests exist: `test_fica_ss_wage_cap_boundary` (971), `test_26_period_annual_net_pay_sum` (1226), `test_zero_salary` (1372), `test_negative_salary_behavior` (1419).
  - `test_basic_paycheck_no_deductions` (394): Now asserts exact Decimals for `federal_tax == Decimal("173.08")`, `state_tax == Decimal("103.85")`, `social_security == Decimal("143.08")`, `medicare == Decimal("33.46")`, `net_pay == Decimal("1854.22")`.
  - Zero directional assertions remaining: `grep '> ZERO|> Decimal|< result|< Decimal'` returns empty.

- [x] **CHECK 2.6: Recurrence engine safety guards** -- PASS
  - All required tests exist:
    - `test_every_n_periods_interval_zero_defaults_to_one` (498) -- verifies `or 1` fallback, matches all periods
    - `test_day_of_month_zero_via_match_periods` (557) -- verifies `or 1` fallback matches day 1
    - `test_day_of_month_zero_direct_raises` (595) -- `pytest.raises(ValueError)` for direct call
    - `test_day_of_month_32_clamped_to_last_day` (611) -- verifies clamping behavior
    - `test_month_of_year_zero_defaults_to_one` (672) -- verifies fallback
    - `test_month_of_year_13_annual_raises` (743) -- `pytest.raises(ValueError)`
    - `test_every_n_periods_interval_none_defaults_to_one` (530) -- None fallback
    - `test_cross_user_isolation` (1179) -- xfail, documents known IDOR in recurrence engine
  - The interval_n=0 test (498) verifies the `or 1` fallback returns `len(matched) == len(biweekly_periods)`. The NOTE at line 508 documents the hang risk. DB constraint `ck_recurrence_rules_positive_interval` provides production-level defense.

- [x] **CHECK 2.7: Growth engine, pension, amortization, retirement gap** -- PARTIAL (see Section 2)
  - **Pension calculator:** Zero directional assertions. PASS.
  - **Amortization engine:** `test_achievable_target` (333) and `test_summary_with_extra` (234) now use exact Decimal assertions with independent spot-checks of month 1-3 values. Only remaining comparison `payoff_date_with_extra < payoff_date` (line 266) is a DATE comparison, not financial. PASS.
  - **Retirement gap calculator:** `test_surplus` renamed to `test_shortfall_when_projected_below_required` (21) with exact assertions. `test_after_tax_view_traditional` (90) now uses exact Decimal assertions: `== Decimal("420000.00")`, `== Decimal("-879999.00")`. BUT 2 directional assertions remain (see Section 2).
  - **Growth engine:** 1 directional assertion remains (see Section 2).

### Phase 2: Security

- [x] **CHECK 3.1: Unauthenticated access tests** -- PASS
  - `tests/test_routes/test_auth_required.py` exists (10KB, modified 2026-03-17).
  - Uses `@pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)` over 127 method/path tuples.
  - Uses unauthenticated `client` fixture.
  - Asserts `status_code in (302, 303)` and `"/login" in location` for each endpoint.
  - Covers all 16 route blueprints: auth, grid, transactions, templates, pay_periods, accounts, categories, settings, salary, transfers, savings, mortgage, auto_loan, investment, retirement, charts.
  - App has 139 `@login_required` decorators; test has 127 endpoints. Difference is expected: some decorators protect multiple methods on the same route function, and some decorator references are imports.

- [x] **CHECK 3.2: IDOR tests with DB state verification** -- PASS
      All 10 specific tests verified:
      | Test | File | Evidence |
      |---|---|---|
      | `test_params_update_idor` | `test_auto_loan.py:169` | Snapshots `orig_principal`, `orig_rate`, `orig_day` at L176-181; `db.session.expire_all()` at L200; asserts unchanged at L204-211 |
      | `test_params_update_validation` | `test_auto_loan.py:134` | Snapshots at L141-146; `db.session.expire_all()` at L155; asserts unchanged at L159-167 |
      | `test_hysa_params_update_idor` | `test_hysa.py:137` | Snapshots `orig_apy`, `orig_freq` at L142-146; `db.session.expire_all()` at L161; asserts unchanged at L165-169 |
      | `test_params_idor` | `test_investment.py:182` | `db.session.expire_all()` at L210; `created = .first()` at L211; `assert created is None` at L214 |
      | `test_validation_error` | `test_investment.py:218` | `db.session.expire_all()` at L231; `created = .first()` at L232; `assert created is None` at L235 |
      | `test_params_update_idor` | `test_mortgage.py:182` | Snapshots at L189-195; `db.session.expire_all()` at L215; asserts unchanged at L219-224 |
      | `test_params_update_validation` | `test_mortgage.py:143` | Snapshots at L150-156; `db.session.expire_all()` at L165; asserts unchanged at L169-180 |
      | `test_edit_pension_idor` | `test_retirement.py:210` | Asserts 302 + Location + `b"Other Pension" not in resp.data` (read-only, no write path) |
      | `test_mark_credit_blocked` | `test_transaction_auth.py:157` | `db.session.expire_all()` at L167; asserts `status.name == "projected"` at L169; checks no payback txn at L173-183 |
      | `test_unmark_credit_blocked` | `test_transaction_auth.py:208` | Captures `orig_status` at L214; `db.session.expire_all()` at L222; asserts `status.name == orig_status` at L224 |

- [x] **CHECK 3.3: IntegrityError handling** -- PASS
  - `app/routes/transfers.py:15,158`: Imports `IntegrityError`, catches at line 158.
  - `app/routes/savings.py:13,503`: Imports `IntegrityError`, catches at line 503.
  - `test_transfers.py:259-312` (`test_create_template_double_submit`): No `pytest.raises(IntegrityError)`. Submits twice, asserts second returns 302, follows redirect, asserts `b"already exists"` in flash, verifies exactly 1 template in DB.
  - `test_savings.py:630-687` (`test_duplicate_goal_name_same_account`): Same pattern -- submits twice, asserts 302, checks flash, verifies `goal_count == 1`.
  - `test_audit_fixes.py:453`: `pytest.raises(IntegrityError, match="uq_transfer_templates_user_name")` -- specific, not overly broad.
  - `test_audit_fixes.py:477`: `pytest.raises(IntegrityError, match="uq_savings_goals_user_acct_name")` -- specific, not overly broad.
  - Zero `pytest.raises(Exception)` found anywhere in test suite.

### Phase 3: Assertion Quality

- [x] **CHECK 4.1: No b"form" sole assertions** -- PASS
  - `grep 'assert.*b"form"'` across test_accounts, test_salary, test_transfers, test_templates returns zero results.

- [x] **CHECK 4.5: Redirect Location assertions in charts** -- PASS
  - All 8 redirect tests in `test_charts.py` (lines 36-37, 46-47, 87-88, 94-95, 101-102, 108-109, 115-116, 122-123) assert both `status_code == 302` and `"/charts" in resp.headers["Location"]` (or `"/login"` for auth tests).

- [x] **CHECK 4.6: No remaining `or` assertions** -- PASS
  - `grep ' or b"| or b\''` across test_retirement.py and test_onboarding.py returns zero results.

- [x] **CHECK 4.7: Retirement SWR assertion** -- PASS
  - `test_update_settings_partial` (`test_retirement.py:293-323`): Snapshots `orig_retirement_date` and `orig_tax_rate` before POST. After POST, queries DB via `db.session.expire_all()` + `db.session.query()`. Asserts `settings_after.safe_withdrawal_rate == Decimal("0.0350")` and that other fields are unchanged.

- [x] **CHECK 4.8: Session invalidation timestamps** -- PASS
  - `test_invalidate_sessions` (`test_auth.py:195-213`): Captures `user.session_invalidated_at`, computes `now = datetime.now(timezone.utc)`, asserts `delta < timedelta(seconds=5)`.
  - `test_password_change_invalidates_sessions` (`test_auth.py:247-264`): Same pattern with UTC timezone.

- [x] **CHECK 4.9: MFA secret length** -- PASS
  - `test_generates_base32_string` (`test_mfa_service.py:13-20`): `assert len(secret) == 32` plus `re.fullmatch(r"[A-Z2-7=]+", secret)`.

- [x] **CHECK 4.10: Audit trigger DB user** -- PASS
  - `test_db_user_is_populated` (`test_audit_triggers.py:302-309`): `assert rows[-1]["db_user"] == "shekel_user"` -- exact match against the PostgreSQL role from test config.

---

## Section 2: Issues Found -- Planned But Incomplete

| #   | Work Unit | File                                | Issue                                                                                                                                                                                                          | Severity | Evidence                                                                                                                                                                                                                                                                |
| --- | --------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | WU1.7     | `test_growth_engine.py`             | Directional assertion on line 452: `assert result[-1].end_balance > Decimal("10000") + Decimal("500") * len(periods)`                                                                                          | MEDIUM   | `test_works_with_project_balance` uses `>` instead of exact Decimal. Inputs are deterministic (fixed balance, return rate, contribution, periods).                                                                                                                      |
| 2   | WU1.7     | `test_retirement_gap_calculator.py` | Directional assertion on line 71: `assert result.savings_surplus_or_shortfall < ZERO`                                                                                                                          | MEDIUM   | `test_shortfall` -- inputs are deterministic Decimals. Could compute exact shortfall.                                                                                                                                                                                   |
| 3   | WU1.7     | `test_retirement_gap_calculator.py` | Directional assertion on line 177: `assert result.savings_surplus_or_shortfall < ZERO`                                                                                                                         | MEDIUM   | `test_no_retirement_accounts` -- inputs are deterministic. Could compute exact shortfall.                                                                                                                                                                               |
| 4   | WU3.1     | `test_accounts.py`                  | 4 status-code-only tests remain: `test_inline_anchor_invalid_amount` (378), `test_inline_anchor_other_users_account` (390), `test_true_up_invalid_amount` (445), `test_true_up_other_users_account` (457)      | LOW      | Lines 388, 402, 453, 462 -- each has only `assert response.status_code == 4xx`. The error tests (400) are borderline acceptable; the IDOR tests (404) should add DB verification.                                                                                       |
| 5   | WU3.1     | `test_transfers.py`                 | 4 status-code-only tests remain: `test_get_cell_other_users_transfer` (503), `test_update_other_users_transfer` (626), `test_create_ad_hoc_other_users_period` (673), `test_create_ad_hoc_double_submit` (699) | MEDIUM   | L626 is a write-path IDOR test with no DB verification -- attacker PATCHes another user's transfer, test only checks 404 status code but doesn't verify the transfer amount is unchanged. L673 is a create-path test with no verification that no transfer was created. |
| 6   | WU3.1     | `test_grid.py`                      | 1 status-code-only test: `test_create_inline_no_scenario` (319)                                                                                                                                                | LOW      | Error path test, status-code-only may be acceptable for this context.                                                                                                                                                                                                   |
| 7   | WU3.2     | `test_mortgage.py`                  | 3 status-code-only tests: `test_rate_change_validation` (257), `test_escrow_add_duplicate_name` (310), `test_payoff_target_date` (393)                                                                         | LOW      | L257 and L310 are validation error tests. L393 (`test_payoff_target_date`) returns 200 but only checks status code -- should verify response contains payoff data.                                                                                                      |
| 8   | WU3.2     | `test_hysa.py`                      | 2 status-code-only tests: `test_hysa_detail_nonexistent` (81), `test_hysa_detail_wrong_type` (86)                                                                                                              | LOW      | Error path / redirect tests.                                                                                                                                                                                                                                            |
| 9   | WU3.2     | `test_auto_loan.py`                 | 1 status-code-only test: `test_dashboard_wrong_type` (100)                                                                                                                                                     | LOW      | Redirect test.                                                                                                                                                                                                                                                          |
| 10  | WU3.2     | `test_investment.py`                | 2 status-code-only tests: `test_dashboard_nonexistent` (99), `test_growth_chart_redirects_without_htmx` (243)                                                                                                  | LOW      | Error and redirect tests.                                                                                                                                                                                                                                               |
| 11  | WU3.2     | `test_retirement.py`                | 2 status-code-only tests: `test_dashboard_projects_without_retirement_date` (375), `test_gap_redirects_without_htmx` (465)                                                                                     | LOW      | L375 returns 200 but only checks status -- should verify dashboard renders without crash. L465 is a redirect test.                                                                                                                                                      |
| 12  | WU3.3A    | `test_savings.py`                   | `assert total_goals >= 1` at line 693                                                                                                                                                                          | LOW      | Inside `test_duplicate_goal_name_same_account`. Should be `== 1` since we know exactly one goal should exist after the duplicate was rejected.                                                                                                                          |
| 13  | WU3.3A    | `test_templates.py`                 | `assert txn_count > 0` at line 401                                                                                                                                                                             | LOW      | Precondition check inside `test_deactivate_template_with_transactions`. Could assert exact expected count.                                                                                                                                                              |

---

## Section 3: Issues Found -- Not in Plan

| #   | File                         | Issue                                                                                                             | Severity | Planned Phase               | Notes                                                                                                                                                                                                                                 |
| --- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------- | -------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `test_transaction_auth.py`   | 4 status-code-only IDOR tests at lines 232, 247, 262, 282                                                         | MEDIUM   | Planned Phase 5 (WU5.1-5.4) | `test_inline_create_with_other_users_period/category`, `test_create_with_other_users_period`, `test_carry_forward_other_users_period` -- all create-path IDOR tests that only check 404, no DB verification that nothing was created. |
| 2   | `test_hostile_qa.py`         | 1 status-code-only test at line 707: `test_access_other_users_transaction`                                        | LOW      | Planned Phase 7 (WU7.1-7.2) | Adversarial test with only status code assertion.                                                                                                                                                                                     |
| 3   | `test_hostile_qa.py`         | Creates second user inline at lines 684-705 (`_create_second_user` method) instead of using `second_user` fixture | LOW      | Not Planned                 | Should migrate to shared fixture for consistency.                                                                                                                                                                                     |
| 4   | `test_accounts_dashboard.py` | `test_dashboard_no_accounts` (line 191) is status-code-only: `assert resp.status_code == 200`                     | LOW      | Not Planned                 | Dashboard empty state test should verify the page renders meaningful content (e.g., "no accounts" message).                                                                                                                           |
| 5   | `test_auth.py`               | `is not None` checks at lines 326, 328, 329 for MFA config fields                                                 | LOW      | Planned Phase 8 (WU8.3)     | After MFA setup, `config is not None`, `totp_secret_encrypted is not None`, `backup_codes is not None` could verify specific lengths/formats.                                                                                         |
| 6   | `test_auth.py`               | `is not None` checks at lines 768, 773, 778, 978 for onboarding user/settings/scenario creation                   | LOW      | Planned Phase 8 (WU8.3)     | Could verify specific field values rather than just existence.                                                                                                                                                                        |
| 7   | `test_audit_triggers.py`     | `is not None` checks at lines 80, 182, 183, 250 for JSON audit data                                               | LOW      | Planned Phase 6 (WU6.1)     | Should verify JSON contains expected keys/values, not just existence.                                                                                                                                                                 |
| 8   | `test_audit_triggers.py`     | `is not None` for `executed_at` at line 292                                                                       | LOW      | Planned Phase 6 (WU6.1)     | Should check timestamp is recent (within 5s), not just non-null.                                                                                                                                                                      |
| 9   | `test_recurrence_engine.py`  | xfail test `test_cross_user_isolation` (1179) documents a real IDOR vulnerability in recurrence engine            | HIGH     | Planned Phase 4 (WU4.1)     | The engine generates transactions across user boundaries. Test correctly documents the bug with `strict=True` xfail. The fix is in the application code, not the test.                                                                |

---

## Section 4: Quality Assessment of Completed Tests

| Test                                                   | Plan Baseline                                      | Actual Implementation                                                                                                                                                                                                                                           | Assessment           |
| ------------------------------------------------------ | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| `test_52_period_penny_accuracy`                        | 52 periods, mixed txns, hand-computed oracle       | 52 periods, 10 hand-crafted scenarios covering all 6 statuses (projected/done/cancelled/credit/received/zero-amount), fractional-cent rounding test (3x33.33), independent Decimal oracle loop, per-period `==` assertions + cumulative cross-check. 276 lines. | **EXCEEDS** baseline |
| `test_fica_ss_wage_cap_boundary`                       | SS cap crossing with exact per-period assertions   | $200k salary across 26 periods, exact Decimal assertions for full SS ($476.92) x21, partial ($437.81) at transition, $0 x4 post-cap, cumulative total verification ($10,453.13), Medicare constant check across all 26 periods.                                 | **EXCEEDS** baseline |
| `test_26_period_annual_net_pay_sum`                    | Annual sum matches per-period \* 26                | Verifies total gross, federal, state, SS, Medicare, and net across 26 periods with exact Decimal \* 26 assertions.                                                                                                                                              | **MEETS** baseline   |
| `test_debt_26_period_amortization_accuracy`            | 26-period amortization with exact balance tracking | Comprehensive: exact Decimal assertions for payment_amount, interest, principal, remaining_balance each period + cumulative totals verification.                                                                                                                | **MEETS** baseline   |
| `test_hysa_26_period_compounding_no_drift`             | 26-period compounding with exact assertions        | Independent compound-interest oracle with `Decimal.quantize(ROUND_HALF_UP)`, per-period exact assertions, includes transfer deposits mid-stream.                                                                                                                | **EXCEEDS** baseline |
| `test_26_period_annual_withholding_matches_annual_tax` | Per-period withholding \* 26 vs annual tax         | Hand-traced bracket calculation, exact assertions for per-period, annual tax, annual via withholding, and explicit rounding discrepancy == Decimal("0.04").                                                                                                     | **EXCEEDS** baseline |
| `test_every_n_periods_interval_zero_defaults_to_one`   | Assert ValueError or empty list                    | Verifies `or 1` fallback returns all periods (matches `interval_n=1` behavior). Comments document hang risk and DB constraint as production defense.                                                                                                            | **MEETS** baseline   |
| `test_shortfall_when_projected_below_required`         | Renamed from test_surplus, exact assertions        | Full hand-traced calculation in docstring, exact Decimal assertions for all 5 result fields.                                                                                                                                                                    | **EXCEEDS** baseline |
| `test_summary_with_extra` (amortization)               | Exact assertions for months_saved, interest_saved  | Regression lock values + independent spot-checks of months 1-3 with hand-traced interest/principal/balance calculations.                                                                                                                                        | **EXCEEDS** baseline |
| `test_zero_salary`                                     | All fields zero                                    | Asserts exact Decimal("0.00") for all 6 fields.                                                                                                                                                                                                                 | **MEETS** baseline   |

---

## Section 5: Duplicate Second-User Implementations

| File                 | Line    | Pattern                                                                                            | Should Use Fixture?                                                         |
| -------------------- | ------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `test_hostile_qa.py` | 684-705 | `_create_second_user()` method creates `User(email="other@shekel.local", ...)` inline with Account | Yes -- should use `second_user` or `seed_second_user` fixture from conftest |

Note: `test_access_control.py`, `test_data_isolation.py`, and `test_fixture_validation.py` all properly use the centralized `seed_second_user`/`seed_full_second_user_data` fixtures from conftest.

---

## Section 6: Recommendations

### Critical (must fix before production)

1. **Recurrence engine cross-user IDOR** (Section 3, #9): `generate_for_template()` has no ownership check -- User A's template can generate transactions into User B's scenario. The xfail test documents this correctly. The fix is in `app/services/recurrence_engine.py`, not the test. This is tagged for Phase 4 (WU4.1).

### High (should fix before production)

1. **Write-path IDOR tests missing DB verification** (Section 2, #5): `test_update_other_users_transfer` at `test_transfers.py:626` is a PATCH IDOR test that only checks status code 404. If the route has a bug where it returns 404 but still applies the update, this test would miss it. Add `db.session.expire_all()` + refresh + assert unchanged after the request.

2. **Create-path IDOR tests missing DB verification** (Section 2, #5 and Section 3, #1): `test_create_ad_hoc_other_users_period` (test_transfers.py:673) and the 4 tests in `test_transaction_auth.py` (232, 247, 262, 282) attempt to create records via another user's foreign key but don't verify nothing was created.

3. **Remaining directional assertions on financial values** (Section 2, #1-3): Three tests in Phase 1 scope still use `>` or `<` instead of exact Decimal assertions. The inputs are deterministic, so exact values can be computed.

### Medium (fix before making public)

1. **Status-code-only write-path tests** (Section 2, #7): `test_payoff_target_date` (test_mortgage.py:393) returns 200 but only checks status code. Should verify the response contains payoff calculation results.

2. **`len >= 1` assertion smell** (Section 2, #12): `test_savings.py:693` should assert `== 1` since the exact count is known after a duplicate rejection.

### Low (cleanup when convenient)

1. **Migrate inline second-user** in `test_hostile_qa.py` to use shared fixture (Section 5).
2. **Status-code-only error/redirect tests** (Section 2, #4, #6, #8-11): Many tests for 400/404 error responses and redirect behavior only check status code. While less critical than write-path tests, adding content assertions (e.g., error message text, Location header) would strengthen them.
3. **`is not None` patterns in test_auth.py** (Section 3, #5-6): MFA and onboarding existence checks could verify specific field values.
4. **Audit trigger `is not None` patterns** (Section 3, #7-8): JSON data and timestamp assertions could be more specific.
5. **`test_dashboard_no_accounts`** (Section 3, #4): Empty state test should verify meaningful content, not just 200.

---

## Appendix A: Test Count Verification

- Tests collected: 1258
- Tests passed: 1257
- Tests xfailed: 1 (`test_cross_user_isolation` -- strict xfail documenting recurrence engine IDOR)
- Tests failed: 0
- Expected minimum per plan: >= 495 (from original ~470 + ~25-30 new)
- Actual: 1258 (Phase 8E data isolation work added significant test coverage)

## Appendix B: Zero-Assertion Test Analysis

The AST scan found 100 test functions with zero `assert` statements. Classification:

| Category                            | Count | Disposition                                                                                                                                                                                                                                                               |
| ----------------------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pytest.raises()` context manager   | 29    | Valid assertion mechanism. Tests in `test_tax_calculator.py`, `test_paycheck_calculator.py`, `test_recurrence_engine.py`, `test_auth_service.py`, `test_credit_workflow.py`, `test_pay_period_service.py`, `test_errors.py`, `test_hostile_qa.py`, `test_audit_fixes.py`. |
| `_assert_blocked()` helper function | 66    | Valid. `test_access_control.py` delegates assertion to `_assert_blocked(response)` at `test_access_control.py:28-41`, which asserts `status_code in (302, 404)`.                                                                                                          |
| Potentially missing assertions      | 5     | `test_hostile_qa.py:329,345,452,620` (4 tests use `pytest.raises` but may lack content assertions inside the block), `test_hostile_qa.py:75` (uses `pytest.raises`). All are adversarial tests -- Phase 7 scope.                                                          |

## Appendix C: Float Usage in Financial Tests

`grep -rn 'float(' tests/test_services/` -- zero results. No Python `float` types detected in financial test assertions. All financial values use `Decimal` throughout.

## Appendix D: Warnings

No `UserWarning` or deprecation warnings detected in test output.
