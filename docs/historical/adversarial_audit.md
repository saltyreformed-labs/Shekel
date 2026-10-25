> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Adversarial Codebase Audit -- Shekel Budget Application

## Date: 2026-03-29

## Executive Summary

The Shekel codebase is **well-architected for a solo-developer project**. Financial calculations
use `Decimal` throughout with zero float contamination in the core calculation path. Service
isolation from Flask is perfect -- no service imports request, session, or current_user. The
transfer architecture invariants are correctly enforced. The test suite is comprehensive (2,033+
tests) with dedicated IDOR, XSS, and adversarial coverage.

However, the audit identified **1 critical finding** (silent fallback to incorrect paycheck
amounts in the recurrence engine), **11 high findings** (including a confirmed IDOR on
scenario_id, IDOR info-leakage in error messages, systematic use of ref-table string names for
logic, missing schema validation on multiple routes, and a seed script that crashes on migrated
databases), **17 medium findings**, and **15 low findings**. The most dangerous pattern is
broad `except Exception` blocks that silently mask financial calculation failures -- the user
sees plausible-looking numbers with no indication anything went wrong.

**Production readiness assessment:** The app is functional but should not serve multiple users
without fixing H-02 (scenario_id IDOR), H-03 (IDOR info leakage), and H-04 (shared AccountType
mutation). For single-user deployment, fix C-01 (silent paycheck fallback) and H-10
(seed script crash) before the next container restart.

---

## Audit Scope

### Documentation Read

- `CLAUDE.md` -- Project rules, architecture, patterns
- `docs/project_requirements_v2.md` -- Data model, design philosophy
- `docs/project_requirements_v3_addendum.md` -- Service contracts, account types
- `docs/project_roadmap_v4-1.md` -- Roadmap, known issues
- `docs/transfer_rework_design.md` -- Transfer invariants
- `docs/implementation_plan_section4.md` -- Section 4 UX/Grid plan
- `docs/implementation_plan_section5.md` -- Section 5 Debt/Account plan
- `docs/fixes_improvements.md` -- Known bugs and planned work

### Application Code Read (17,844 lines across 57 files)

**Core:** `app/__init__.py`, `config.py`, `extensions.py`, `enums.py`, `ref_cache.py`,
`exceptions.py`

**Models (23 files):** `ref.py`, `user.py`, `account.py`, `transaction.py`, `transfer.py`,
`transaction_template.py`, `transfer_template.py`, `pay_period.py`, `category.py`,
`recurrence_rule.py`, `scenario.py`, `savings_goal.py`, `hysa_params.py`, `mortgage_params.py`,
`auto_loan_params.py`, `investment_params.py`, `pension_profile.py`, `salary_profile.py`,
`salary_raise.py`, `paycheck_deduction.py`, `tax_config.py`, `calibration_override.py`,
`models/__init__.py`

**Services (24 files):** `balance_calculator.py`, `recurrence_engine.py`,
`paycheck_calculator.py`, `transfer_service.py`, `transfer_recurrence.py`,
`credit_workflow.py`, `carry_forward_service.py`, `amortization_engine.py`,
`interest_projection.py`, `growth_engine.py`, `escrow_calculator.py`,
`pension_calculator.py`, `retirement_gap_calculator.py`, `chart_data_service.py`,
`account_resolver.py`, `auth_service.py`, `pay_period_service.py`,
`savings_goal_service.py`, `tax_calculator.py`, `tax_config_service.py`,
`calibration_service.py`, `investment_projection.py`, `mfa_service.py`,
`services/exceptions.py`

**Routes (18 files):** `auth.py`, `grid.py`, `transactions.py`, `transfers.py`,
`templates.py`, `accounts.py`, `savings.py`, `settings.py`, `salary.py`, `mortgage.py`,
`auto_loan.py`, `investment.py`, `retirement.py`, `charts.py`, `categories.py`,
`pay_periods.py`, `health.py`, `routes/__init__.py`

**Schemas:** `validation.py`

**Utilities:** `auth_helpers.py`, `log_events.py`, `logging_config.py`

**Templates (87 files):** All templates across `base.html`, `grid/`, `accounts/`,
`auto_loan/`, `categories/`, `charts/`, `investment/`, `mortgage/`, `pay_periods/`,
`retirement/`, `salary/`, `savings/`, `settings/`, `templates/`, `transfers/`,
`auth/`, `errors/`, and root partials

### Tests Read (51,846 lines across 71 files)

`conftest.py`, all `test_services/` (26), all `test_routes/` (25), all `test_integration/` (6),
all `test_models/` (3), `test_schemas/` (2), `test_adversarial/` (1), `test_scripts/` (5),
`test_utils/` (4), `test_performance/` (2), and root test files

### Scripts, Config, Deployment, Migrations

All 8 scripts, all config files (`config.py`, `requirements.txt`, `.env.example`,
`gunicorn.conf.py`, `pytest.ini`, `.pylintrc`, `run.py`), all Docker files (`Dockerfile`,
`docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.build.yml`, `entrypoint.sh`),
`migrations/env.py`, and all 25 migration files

---

## Findings by Severity

### Critical Findings

#### C-01: Recurrence engine silently falls back to wrong paycheck amount

**File:** `app/services/recurrence_engine.py:_get_transaction_amount:543-549`

```python
except Exception:
    logger.exception(
        "Failed to calculate paycheck for salary profile %d, "
        "falling back to template default_amount",
        salary_profile.id,
    )
    return template.default_amount
```

**Issue:** When `paycheck_calculator.calculate_paycheck()` throws any exception -- division by
zero from misconfigured tax brackets, missing FICA config, invalid salary data -- this block
catches it and silently substitutes `template.default_amount`. The template default is typically
the gross salary or an initial estimate, not the calculated net pay. The difference between
gross and net can be 25-40% of the amount.

**Consequence:** Every income transaction for every future pay period silently uses an incorrect
amount. Since income feeds directly into the balance calculator, every projected balance from
the anchor forward is wrong. The user sees plausible-looking dollar amounts with no visible
indication that the paycheck calculation failed. Financial decisions (can I afford this bill?
should I carry this forward?) are made on bad data.

**Fix:** Either propagate the exception (fail loudly), or narrow the catch to specific
recoverable failures. At minimum, if falling back, attach a flag to the generated transactions
indicating the amount is unreliable so the grid can display a warning.

---

### High Findings

#### H-01: Ref table string name comparisons used for logic across codebase

**Files and lines:**

| Location | Code | String Used |
|----------|------|-------------|
| `paycheck_calculator.py:360` | `ded.deduction_timing.name != timing_name` | `"pre_tax"`, `"post_tax"` |
| `paycheck_calculator.py:374` | `ded.calc_method.name == "percentage"` | `"percentage"` |
| `tax_calculator.py:254` | `state_config.tax_type.name == "none"` | `"none"` |
| `investment_projection.py:68` | `ded.calc_method_name == "percentage"` | `"percentage"` |
| `salary.py:498-499` | `calc_method.name == "percentage"` | `"percentage"` |
| `salary.py:923` | `TaxType.filter_by(name="flat")` | `"flat"` |
| `templates/salary/_deductions_section.html:30,36,69` | `d.deduction_timing.name == 'pre_tax'` | `"pre_tax"` |
| `templates/salary/_deductions_section.html:69` | `d.calc_method.name == 'percentage'` | `"percentage"` |

**Issue:** CLAUDE.md non-negotiable rule: "All comparisons, conditionals, queries, and filter
expressions on reference tables must use integer primary key IDs, never string name columns."
These 10+ locations violate this rule by comparing against the `name` column of `DeductionTiming`,
`CalcMethod`, and `TaxType` reference tables.

**Consequence:** If any ref table display name is updated, these comparisons silently break.
Deductions stop being categorized (pre-tax treated as post-tax or vice versa), percentage
calculations are skipped, and tax calculations are wrong -- all without raising an error.

**Fix:** Add `DeductionTimingEnum`, `CalcMethodEnum`, and `TaxTypeEnum` to `app/enums.py`,
register them in `ref_cache.py`, and replace all string comparisons with ID-based lookups.

---

#### H-02: IDOR -- scenario_id not validated on transaction creation

**File:** `app/routes/transactions.py` (create_transaction and create_inline endpoints)
**Test documentation:** `tests/test_routes/test_transaction_auth.py:285-341`

**Issue:** The `POST /transactions` and `POST /transactions/inline` endpoints accept a
`scenario_id` from the user but do not verify that the scenario belongs to `current_user`.
An authenticated attacker can create transactions under another user's scenario by submitting
a guessed or enumerated `scenario_id`.

**Consequence:** The attacker's transaction is invisible in their own grid (wrong scenario) but
pollutes the victim's scenario data. The victim sees phantom transactions they didn't create,
corrupting their budget projections. This is confirmed by tests that assert the broken behavior
(`status_code == 201` with another user's scenario_id).

**Fix:** Before creating the transaction, verify
`db.session.get(Scenario, scenario_id).user_id == current_user.id`. Return 404 if not.

---

#### H-03: IDOR info leakage -- salary routes distinguish not-found from not-yours

**File:** `app/routes/salary.py`

| Route | Not Found Message | Not Yours Message |
|-------|-------------------|-------------------|
| `delete_raise` (line 394 vs 399) | "Raise not found." | "Not authorized." |
| `update_raise` (line 427 vs 431) | "Raise not found." | "Not authorized." |
| `delete_deduction` (line 529 vs 534) | "Deduction not found." | "Not authorized." |
| `update_deduction` (line 561 vs 566) | "Deduction not found." | "Not authorized." |

**Issue:** CLAUDE.md rule: "When a resource is not found OR belongs to another user, the
response MUST be identical." These four routes return different flash messages, allowing an
attacker to enumerate valid SalaryRaise and PaycheckDeduction IDs belonging to other users.

**Consequence:** Information disclosure. An attacker can confirm the existence of specific
financial records (raises, deductions) belonging to other users.

**Fix:** Return "Raise not found." / "Deduction not found." in both cases. Restructure as
a single check: `if raise is None or raise.salary_profile.user_id != current_user.id`.

---

#### H-04: AccountType CRUD routes have no user scoping

**File:** `app/routes/accounts.py:393-471`

**Issue:** `AccountType` is a shared `ref` schema table with no `user_id` column. The
`create_account_type` (line 393), `update_account_type` (line 411), and `delete_account_type`
(line 445) routes are protected by `@login_required` but have no further authorization. Any
authenticated user can create, rename, or delete account types that are visible to all users.

**Consequence:** In a multi-user deployment, user A can rename an account type used by user B
(changing their display), or delete an unused type that user B intended to use. The `in_use`
check (line 454) prevents deleting types with active accounts, but the query is global
(`filter_by(account_type_id=type_id)` without user scoping), which means user A's delete
is blocked if user B uses the type -- a confusing error from user A's perspective.

**Fix:** Either add `user_id` to `AccountType` (making them per-user), restrict mutation to
admin-only, or accept the shared-table design and document it explicitly. For single-user
deployments this is not exploitable.

---

#### H-05: Grid template subtotals use float arithmetic on monetary amounts

**File:** `app/templates/grid/grid.html:171,249,266,268`

**Issue:** The Total Income, Total Expenses, and Net Cash Flow subtotal rows use Jinja2's
`|float` filter to accumulate `txn.effective_amount`. This converts `Decimal` to Python `float`
for arithmetic, introducing floating-point precision errors.

**Consequence:** Subtotals could disagree with the balance row (which uses server-side Decimal
arithmetic) by pennies. For a user reconciling their budget, a 1-cent discrepancy between the
subtotal and the balance erodes trust in the tool.

**Fix:** Compute subtotals in the route handler using Decimal arithmetic and pass pre-computed
values to the template. The balance row already uses server-side Decimal values.

---

#### H-06: SalaryProfileCreateSchema missing annual_salary range validation

**File:** `app/schemas/validation.py:160`

```python
annual_salary = fields.Decimal(required=True, places=2, as_string=True)
```

**Issue:** No `validate=validate.Range(...)` on `annual_salary`. Negative, zero, or absurdly
large values pass schema validation.

**Consequence:** A zero salary flows into `gross_biweekly = 0 / 26 = 0`, which propagates
through tax calculations. The salary breakdown template (line 155) computes
`breakdown.net_pay / breakdown.gross_biweekly * 100`, producing a `DivisionByZero` for zero
salary. A negative salary produces negative gross biweekly pay, cascading incorrect signs
through all tax and deduction calculations.

**Fix:** Add `validate=validate.Range(min=0, min_inclusive=False)`.

---

#### H-07: salary.py calibrate_confirm lacks Marshmallow validation

**File:** `app/routes/salary.py:calibrate_confirm:797-806`

**Issue:** The `calibrate_confirm` POST endpoint reads `request.form["actual_gross_pay"]`,
`request.form["actual_federal_tax"]`, and 6 other fields directly, wrapping each in `Decimal()`.
No Marshmallow schema validates these inputs. The broad `except Exception` at line 818 catches
`InvalidOperation` from malformed Decimal input and `ValueError` from
`date.fromisoformat(request.form["pay_stub_date"])`, returning only "Failed to save
calibration."

**Consequence:** No data corruption (the transaction rolls back on error), but invalid data
produces a generic error message with no field-level feedback. Violates the project rule:
"Input validation via Marshmallow schemas applied BEFORE any database operations."

**Fix:** Create a `CalibrationConfirmSchema` in `validation.py` with all 8+ fields properly
typed and range-validated.

---

#### H-08: salary.py update_tax_config uses manual validation

**File:** `app/routes/salary.py:update_tax_config:883-943`

**Issue:** This route manually parses `state_code`, `flat_rate`, `standard_deduction`, and
`tax_year` from `request.form` with inline `try/except` blocks and string operations. No
Marshmallow schema is used. This is the only state-changing POST route in the codebase that
completely bypasses schema validation.

**Consequence:** The manual checks are less robust than Marshmallow. There is no length limit
on `standard_deduction`, no validation that `tax_year` is in a reasonable range, and the
`TaxType` lookup at line 923 uses a string name comparison (`filter_by(name="flat")`) which
compounds H-01.

**Fix:** Create a `StateTaxConfigSchema` in `validation.py` and use it like every other route.

---

#### H-09: Settings route update() lacks Marshmallow schema validation

**File:** `app/routes/settings.py:135-202`

**Issue:** The `update()` route uses manual `request.form.get()` + `try/except` for all settings
fields (grid periods, projection periods, low balance threshold, default grid account, inflation
rate, retirement age, withdrawal rate). No Marshmallow schema exists for this endpoint.

**Consequence:** Manual validation is fragile. The `low_balance_threshold` is cast to `int`
(line 175), so decimal values raise ValueError. The inflation rate cast to `float` (line 187)
introduces float imprecision for a value that should be Decimal. No max-value guards exist on
any field.

**Fix:** Create a `UserSettingsSchema` in `validation.py`.

---

#### H-10: seed_user.py queries AccountType by wrong case

**File:** `scripts/seed_user.py:109`

```python
db.session.query(AccountType).filter_by(name="checking").one()
```

**Issue:** Migration `415c517cf4a4` renamed all account type names from lowercase to
capitalized (e.g., `"checking"` became `"Checking"`). This query uses the old lowercase name
and will raise `NoResultFound`, crashing the script.

**Consequence:** `entrypoint.sh` calls `seed_user.py` on every container start. On a migrated
database, the seed script crashes and the app fails to start. This blocks all production
deployments and fresh installs that have run the migrations.

**Fix:** Change to `filter_by(name="Checking")`.

---

#### H-11: chart_data_service broad except hides salary projection errors

**File:** `app/services/chart_data_service.py:get_net_pay_trajectory:731-733`

```python
except Exception:  # pylint: disable=broad-except
    logger.exception("Failed to project salary for profile %d", profile.id)
    return empty
```

**Issue:** If `paycheck_calculator.project_salary()` fails for any reason (bad tax config,
missing data, division by zero), the Net Pay Trajectory chart silently returns empty data.
The user sees a blank chart with no error message.

**Consequence:** The user has no way to know their salary projection failed. They might assume
they have no salary data when in fact the calculation is broken. This masks configuration
errors indefinitely.

**Fix:** Return an error indicator in the chart data dict (e.g., `"error": "Salary projection
failed"`) so the frontend can display a message.

---

### Medium Findings

#### M-01: Raise application order depends on unsorted query results

**File:** `app/services/paycheck_calculator.py:_apply_raises:234-264`

**Issue:** `for raise_obj in profile.raises` iterates the SQLAlchemy relationship with no
explicit `order_by`. When mixing flat and percentage raises (e.g., +$5K flat and +3% percentage,
both effective January), the order affects the compounded result:

- Flat first: $100K + $5K = $105K, then 3% = $108,150
- Percentage first: $100K x 1.03 = $103K, then +$5K = $108,000

The $150 difference compounds each year.

**Consequence:** Non-deterministic salary projections. The database may return raises in
different orders between runs, producing different projected balances.

**Fix:** Add `order_by` to the `raises` relationship in `salary_profile.py`, or sort
`profile.raises` by `effective_year, effective_month` at the start of `_apply_raises`.

---

#### M-02: _get_cumulative_wages assumes sorted period list

**File:** `app/services/paycheck_calculator.py:_get_cumulative_wages:415-436`

**Issue:** The function iterates `all_periods` and uses `break` when
`p.start_date >= period.start_date` (line 429). This assumes the list is sorted by
`start_date`. The docstring says "All pay periods for the year" but does not specify order.

**Consequence:** If periods are unsorted, the SS wage base cap is applied incorrectly -- either
too early (under-taxing SS, the user keeps more than they should) or too late (over-taxing SS,
the user's projection shows less net pay than they'll actually receive).

**Fix:** Sort `all_periods` by `start_date` at the start of the function, or add a defensive
assertion.

---

#### M-03: Credit workflow doesn't guard against shadow transactions

**File:** `app/services/credit_workflow.py:mark_as_credit:26-58`

**Issue:** The function checks ownership (line 55) and checks if the transaction is income
(line 57) but does not check if the transaction is a shadow (`transfer_id is not None`). The
route layer blocks credit marking on shadows (transactions.py lines 259, 280), but the service
itself has no guard.

**Consequence:** If any code path calls `mark_as_credit` on a shadow transaction without the
route-level check, a payback transaction would be created for a transfer shadow, double-counting
the transfer amount in the next period. Currently mitigated by route-level checks, but violates
defense-in-depth.

**Fix:** Add `if txn.transfer_id is not None: raise ValidationError("Cannot mark transfer
transactions as credit.")` in the service.

---

#### M-04: Net worth chart uses float accumulation

**File:** `app/services/chart_data_service.py:get_net_worth_over_time:598-618`

**Issue:** `net_worth = [0.0] * num_points` initializes with floats, and account balance
floats are accumulated via `net_worth[i] += sign * val`. The `round(v, 2)` at line 617
masks but doesn't eliminate float imprecision from multi-account accumulation.

**Consequence:** For display-only purposes this is acceptable (Chart.js output), but the pattern
contradicts the project standard of "Decimal for all calculations, float only at the final
presentation boundary." Accumulation should use Decimal with conversion to float only at output.

**Fix:** Accumulate as `Decimal`, convert to float in the final list comprehension.

---

#### M-05: Escrow calculator inflation uses integer year truncation

**File:** `app/services/escrow_calculator.py:calculate_monthly_escrow:41`

**Issue:** `years_elapsed = (as_of_date.year - created.year)` computes inflation years as a
simple year difference. If a component was created December 2024 and `as_of_date` is January
2025, `years_elapsed = 1` even though only one month has passed.

**Consequence:** Escrow projections are overstated in early months of a new year relative to
component creation. For a $4,000 annual escrow with 3% inflation, the error is ~$120/year
applied one year too early.

**Fix:** Use month-aware logic like `_inflation_years` in paycheck_calculator.py.

---

#### M-06: Growth engine allows negative investment balances

**File:** `app/services/growth_engine.py:project_balance:136-140`

**Issue:** When `assumed_annual_return` is sufficiently negative and the balance is small,
`current_balance + growth` can go negative. The function does not clamp at zero.

**Consequence:** Investment projections show negative account balances, which is unrealistic for
most investment accounts (you can't owe money in a standard brokerage account).

**Fix:** Add `current_balance = max(current_balance, Decimal("0"))` after the growth step, or
document that negative balances are intentionally supported for margin accounts.

---

#### M-07: Missing CHECK constraints on mortgage/auto loan financial columns

**Files:** `app/models/mortgage_params.py:30-33`, `app/models/auto_loan_params.py:29-33`

**Issue:** `original_principal`, `current_principal`, `interest_rate`, and `term_months` on both
`MortgageParams` and `AutoLoanParams` have no CHECK constraints. The amortization engine
assumes positive values -- zero `term_months` causes division-by-zero, negative principal
produces nonsensical amortization schedules.

**Consequence:** A data entry error or import bug could insert invalid values that crash
or corrupt amortization calculations.

**Fix:** Add CHECK constraints: `original_principal > 0`, `current_principal >= 0`,
`interest_rate >= 0`, `term_months > 0`.

---

#### M-08: Inconsistent ondelete policy across foreign keys

**Files:** `account.py`, `transaction.py`, `transfer.py`, `transaction_template.py`

**Issue:** User-owned tables consistently use `ondelete="CASCADE"` for `user_id` FKs, but many
inter-table FKs (`account_id`, `category_id`, `status_id`, `pattern_id`) have no explicit
`ondelete` policy. PostgreSQL defaults to `NO ACTION`, which is safe but inconsistent -- some
FKs explicitly say `CASCADE` or `SET NULL` while others rely on the implicit default.

**Consequence:** A developer cannot tell whether the omission was intentional or accidental.
If an account is hard-deleted (bypassing soft-delete), transactions with FK references to it
produce IntegrityErrors instead of being cleaned up or nullified.

**Fix:** Add explicit `ondelete` clauses to all FKs. Use `RESTRICT` for ref table FKs,
`CASCADE` or `SET NULL` for inter-domain FKs based on intended behavior.

---

#### M-09: Transaction.effective_amount doesn't check is_deleted

**File:** `app/models/transaction.py:effective_amount:110-122`

**Issue:** The `effective_amount` property returns `Decimal("0")` for excluded statuses
(cancelled, credit) but does not check `is_deleted`. A deleted transaction with a non-excluded
status would return its `estimated_amount` if the property is called without first filtering
on `is_deleted=False`.

**Consequence:** Any code path that uses `effective_amount` on an unfiltered query set could
include deleted transactions in calculations. The balance calculator bypasses this property
(reads `estimated_amount` directly with its own filtering), but the grid template uses it
for subtotals.

**Fix:** Add `if self.is_deleted: return Decimal("0")` as the first check.

---

#### M-10: Broad except Exception blocks mask errors in multiple locations

**Files and lines:**

| Location | What Is Caught | Fallback |
|----------|----------------|----------|
| `app/__init__.py:185-189` | ref_cache.init() failure | App starts without Jinja globals |
| `app/routes/charts.py:63,88,113,143,169,199` | Any chart calculation error | Empty chart, no message |
| `app/utils/logging_config.py:123-124,163-164` | DB session variable set, current_user access | Silent pass |

**Issue:** CLAUDE.md rule: "Do not suppress errors, swallow exceptions, or ignore edge cases
to make something work." These blocks catch `Exception` broadly, masking bugs that should be
surfaced.

**Consequence:** The app factory catch is the most concerning -- if `ref_cache.init()` fails for
a reason other than a missing migration (e.g., enum mismatch, connectivity blip), the app starts
with no Jinja globals for status IDs, and templates silently render incorrect comparisons.

**Fix:** Narrow catches to specific expected exceptions. For app factory, catch only
`sqlalchemy.exc.ProgrammingError` and `sqlalchemy.exc.OperationalError`. For charts, catch
`ValueError`, `KeyError`, and `SQLAlchemyError`. Re-raise unexpected exceptions.

---

#### M-11: grid.py unsafe int() conversion of query params

**File:** `app/routes/grid.py:index:158-159`

```python
num_periods = int(request.args.get("periods", ...))
start_offset = int(request.args.get("offset", 0))
```

**Issue:** Raw `int()` raises `ValueError` for non-numeric input like `?periods=abc`. Flask's
`request.args.get(..., type=int)` handles this safely by returning the default.

**Consequence:** A malformed URL produces a 500 Internal Server Error instead of falling back
to the default value.

**Fix:** Use `request.args.get("periods", default=6, type=int)`.

---

#### M-12: Logout accepts GET requests

**File:** `app/routes/auth.py:logout:174`

**Issue:** The logout endpoint accepts GET. State-changing GET endpoints can be triggered by
image tags, link prefetching, or crawler bots.

**Consequence:** A malicious page could embed `<img src="https://shekel.example.com/logout">`
to force-logout users (cross-site logout).

**Fix:** Change to POST-only with a CSRF-protected form in the nav dropdown.

---

#### M-13: Recurrence pattern name format inconsistency between template pages

**Files:** `app/templates/templates/list.html:59-76` vs
`app/templates/transfers/list.html:53-70`

**Issue:** Transaction templates page compares `rr.pattern.name` against capitalized names
(`'Every Period'`, `'Monthly'`, `'Semi-Annual'`) which match the database values. Transfer
templates page compares against snake_case (`'every_period'`, `'monthly'`, `'semi_annual'`)
which do NOT match.

**Consequence:** On the transfers list page, every `if/elif` condition fails and all recurrence
rules fall through to the `else` branch, which uses the `recurrence_pattern_labels` dict lookup.
The fallback produces acceptable (but less informative) display text -- contextual details like
interval count and start month are lost.

**Fix:** Update `transfers/list.html` to use the same capitalized names as `templates/list.html`.
Better yet, replace all string comparisons with ID-based logic per H-01.

---

#### M-14: Schema validation range gaps on financial inputs

**Files and fields:**

| Schema | Field | Issue |
|--------|-------|-------|
| `SavingsGoalCreateSchema:418` | `contribution_per_period` | No min=0; negative contribution accepted |
| `RaiseCreateSchema:222-223` | `percentage`, `flat_amount` | No min=0; negative raises accepted |
| `RaiseCreateSchema:218` | `effective_year` | No range; year 0 or 999999 accepted |
| `AccountCreateSchema:450` | `anchor_balance` | No range; extreme values accepted |

**Consequence:** Negative contributions make savings progress nonsensical. Negative raises may
be valid (pay cuts) but the UI labels them "raises." Unbounded years pollute data. Extreme
anchor balances cascade through all projections.

**Fix:** Add appropriate `validate=validate.Range(...)` to each field.

---

#### M-15: Unhandled IntegrityError for invalid FK references

**Files:** `app/routes/transactions.py` (create endpoints)
**Test documentation:** `tests/test_routes/test_transaction_auth.py:370-399`,
`tests/test_adversarial/test_hostile_qa.py:77-96`

**Issue:** Submitting a nonexistent `category_id` or `status_id` in a transaction
create/update request triggers an unhandled `IntegrityError` at commit time. The Marshmallow
schema validates the field type (integer) but does not check that the referenced row exists.

**Consequence:** 500 Internal Server Error in production instead of a clean 400 validation
error. Tests document this as a known bug.

**Fix:** Either add `db.session.get(Category, category_id)` checks before commit, or add a
custom Marshmallow validator that confirms FK existence.

---

#### M-16: Transaction model lacks direct user_id column

**File:** `app/models/transaction.py`

**Issue:** `Transaction` has no `user_id` column. Ownership is determined via
`pay_period_id -> PayPeriod.user_id`, requiring a JOIN for every ownership check. All other
user-data models (`Transfer`, `Account`, `SalaryProfile`, etc.) have direct `user_id` columns.

**Consequence:** Any service-layer query that filters `Transaction` without joining `PayPeriod`
has no user scoping. The `get_owned_via_parent` helper handles this at the route level, but the
pattern is fragile -- a new code path could easily forget the JOIN.

**Fix:** Consider adding a denormalized `user_id` column for defense-in-depth, or document this
as a known architectural constraint.

---

#### M-17: seed_user.py prints password to container logs

**File:** `scripts/seed_user.py:142`

**Issue:** The script prints the plaintext seed password to stdout:
`print(f"  Password: {password}")`. In Docker deployments, this goes to container logs
accessible via `docker logs`.

**Consequence:** The seed user password persists in container log history. Anyone with Docker
access can read it.

**Fix:** Print "Password: [set via SEED_USER_PASSWORD]" instead of the actual value.

---

### Low Findings

#### L-01: Transaction model missing CHECK constraints on amounts

**File:** `app/models/transaction.py:73-74`

**Issue:** `estimated_amount` and `actual_amount` have no CHECK constraints. The schemas
validate `min=0`, and `TransactionTemplate` has `CHECK(default_amount >= 0)`, but the
Transaction table itself has no database-level guard.

**Fix:** Add CHECK constraints as defense-in-depth: `estimated_amount >= 0` and
`actual_amount IS NULL OR actual_amount >= 0`.

---

#### L-02: Rate limiter uses in-memory storage

**File:** `app/extensions.py:31`

**Issue:** `storage_uri="memory://"` means rate limit state is per-worker-process. With
Gunicorn's 2 workers, a client gets 2x the configured rate limits.

**Fix:** Acceptable for single-user deployment. For multi-user, use Redis-backed storage.

---

#### L-03: Various missing ondelete clauses on non-critical FKs

**Files:** `account.py:27,32,75`, `transaction.py:47,68`, `transfer.py:41,44`,
`transaction_template.py:35`

**Issue:** Multiple foreign keys to `account_types`, `categories`, `pay_periods`, and
`recurrence_rules` have no explicit `ondelete` clause. PostgreSQL defaults to `NO ACTION`.

**Fix:** Add explicit `ondelete` policies for clarity and intent documentation.

---

#### L-04: Interest projection uses hardcoded 365 days/year

**File:** `app/services/interest_projection.py:13`

**Issue:** `DAYS_IN_YEAR = Decimal("365")` is used as the daily rate divisor for HYSA interest.
Most US banks use actual/365 convention, making this technically correct. However, in leap
years, interest is very slightly overstated (~$1.23 per $100K at 4.5% APY).

**Fix:** Document the convention. Optionally check for leap years if period spans one.

---

#### L-05: Quarterly interest uses hardcoded 91 days

**File:** `app/services/interest_projection.py:55`

**Issue:** `days_in_quarter = Decimal("91")` approximates all quarters equally. Actual quarters
range 90-92 days.

**Fix:** Calculate actual quarter length from the period's start date.

---

#### L-06: Business logic in route files

**Files:** `app/routes/savings.py:dashboard:44-511` (~470 lines),
`app/routes/retirement.py:_compute_gap_data:54-401` (~350 lines)

**Issue:** These route-level functions contain extensive financial calculations (balance
projections, growth projections, amortization, emergency fund metrics, gap analysis) that
belong in the service layer per the project architecture rule.

**Fix:** Extract into `savings_dashboard_service.py` and `retirement_dashboard_service.py`.

---

#### L-07: Duplicate template code for tax configuration

**Files:** `app/templates/salary/tax_config.html` and
`app/templates/settings/_tax_config.html`

**Issue:** Nearly identical code for tax bracket display, FICA config form, and state tax form.

**Fix:** Extract shared content into a partial and include from both locations.

---

#### L-08: Transaction/transfer notes have no max length in schema

**File:** `app/schemas/validation.py:32,47`

**Issue:** `notes = fields.String(allow_none=True)` with no `validate.Length(max=...)`. Other
notes fields in the codebase (CalibrationSchema, MortgageRateChangeSchema) use `max=500`.

**Fix:** Add `validate=validate.Length(max=500)` for consistency.

---

#### L-09: Gunicorn keepalive comment contradicts value

**File:** `gunicorn.conf.py:38-39`

**Issue:** `keepalive = 5` with comment "Slightly higher than Nginx's keepalive_timeout (65s)".
5 < 65 -- the comment describes the opposite of the actual value.

**Consequence:** If Nginx's keepalive_timeout is 65s and Gunicorn closes at 5s, Nginx could
reuse a connection Gunicorn already closed, causing intermittent 502 errors.

**Fix:** Either set Gunicorn keepalive higher than Nginx's timeout (e.g., 70), or set Nginx
lower than 5s (e.g., 2s). Verify actual Nginx config and align.

---

#### L-10: run.py binds Flask debug server on 0.0.0.0

**File:** `run.py:14`

**Issue:** `app.run(debug=True, host="0.0.0.0")` exposes Werkzeug's interactive debugger on
all network interfaces. Only used for local development (production uses Gunicorn).

**Fix:** Bind to `127.0.0.1` instead of `0.0.0.0`.

---

#### L-11: benchmark_triggers.py uses wrong case for ref table names

**File:** `scripts/benchmark_triggers.py:58,87,88`

**Issue:** Same as H-10 -- uses lowercase `filter_by(name="checking")`, `filter_by(name="expense")`,
`filter_by(name="every_period")` which were renamed to capitalized forms by migrations.

**Fix:** Update to `"Checking"`, `"Expense"`, `"Every Period"`.

---

#### L-12: audit_cleanup.py has no minimum days guard

**File:** `scripts/audit_cleanup.py:47`

**Issue:** `--days` accepts any integer including 0 or negative, which would delete the entire
audit log.

**Fix:** Add `choices=range(1, ...)` or a minimum check.

---

#### L-13: reset_mfa.py lacks audit trail

**File:** `scripts/reset_mfa.py:21-49`

**Issue:** MFA reset runs immediately with no confirmation prompt and no audit log entry.

**Fix:** Add `--force` flag to skip confirmation, and log the action.

---

#### L-14: Routes bypass centralized auth_helpers.py

**Files:** All route files

**Issue:** `app/utils/auth_helpers.py` provides `get_or_404()` and `get_owned_via_parent()`,
but no route file imports or uses them. Every route implements inline ownership checks:
`if obj is None or obj.user_id != current_user.id`. The inline checks are correct but
duplicated ~80+ times.

**Fix:** Gradually migrate to the centralized helpers to reduce duplication.

---

#### L-15: No concurrent modification tests

**Files:** Test suite

**Issue:** No tests for race conditions -- e.g., two requests simultaneously updating the same
transaction, or two carry-forward operations running concurrently. Given HTMX supports
multiple tabs, this is a plausible scenario.

**Fix:** Add targeted concurrency tests for critical paths (mark-done + carry-forward,
simultaneous anchor updates).

---

## Findings by Category

### 2.1 Financial Correctness

| ID | Title | Severity |
|----|-------|----------|
| C-01 | Recurrence engine silent paycheck fallback | CRITICAL |
| H-05 | Grid subtotals use float arithmetic | HIGH |
| H-06 | SalaryProfile missing salary range validation | HIGH |
| M-01 | Raise order dependency | MEDIUM |
| M-02 | Cumulative wages assumes sorted periods | MEDIUM |
| M-04 | Net worth chart float accumulation | MEDIUM |
| M-05 | Escrow inflation year truncation | MEDIUM |
| M-06 | Growth engine negative balances | MEDIUM |
| M-09 | effective_amount ignores is_deleted | MEDIUM |
| L-04 | Interest projection 365 days hardcoded | LOW |
| L-05 | Quarterly interest 91 days hardcoded | LOW |

### 2.2 Security

| ID | Title | Severity |
|----|-------|----------|
| H-02 | Scenario_id IDOR | HIGH |
| H-03 | IDOR info leakage in salary routes | HIGH |
| H-04 | AccountType CRUD no user scoping | HIGH |
| M-12 | Logout accepts GET | MEDIUM |
| M-16 | Transaction lacks user_id | MEDIUM |
| M-17 | Seed password in container logs | MEDIUM |
| L-10 | Debug server on 0.0.0.0 | LOW |

### 2.3 DRY (Don't Repeat Yourself)

| ID | Title | Severity |
|----|-------|----------|
| L-06 | Business logic in route files (savings, retirement) | LOW |
| L-07 | Duplicate tax config templates | LOW |
| L-14 | Routes bypass centralized auth helpers (~80 inline copies) | LOW |

The account-type-specific route files (mortgage, auto_loan, investment) share ~60% identical
patterns (load account, check type, load params, call service, render template). This is noted
but not a finding per se -- the unique 40% justifies separate files, and premature abstraction
would violate the "no speculative abstractions" principle.

### 2.4 SOLID Principles

**Single Responsibility:** The `savings.py:dashboard` (470 lines) and
`retirement.py:_compute_gap_data` (350 lines) route functions violate SRP by mixing routing
with complex financial calculations (L-06).

**Open/Closed:** Adding a new account type requires modifying `account_resolver.py` and the
savings dashboard route to handle the new type. The `if/elif` chains for account type dispatch
are a known consequence of the current architecture; Section 5 of the roadmap addresses this.

**Other SOLID principles** are not materially violated. The codebase does not use deep
inheritance hierarchies, services have focused interfaces, and dependency direction is
consistently Routes -> Services -> Models.

### 2.5 Pythonic Code

The codebase is generally idiomatic. Specific observations:

- **Comprehensions used appropriately** throughout services
- **f-strings used consistently** (no string concatenation)
- **Type hints:** Not used. The project relies on docstrings and Marshmallow schemas instead.
  This is a valid style choice but limits static analysis.
- **`Decimal` usage is correct** everywhere in calculation paths (see I-01)

### 2.6 Database Design

| ID | Title | Severity |
|----|-------|----------|
| M-07 | Missing CHECK constraints on loan columns | MEDIUM |
| M-08 | Inconsistent ondelete policy | MEDIUM |
| M-16 | Transaction lacks user_id | MEDIUM |
| L-01 | Transaction amounts no CHECK constraints | LOW |
| L-03 | Various missing ondelete clauses | LOW |

Normalization is correct (3NF). Referential integrity is enforced via FKs throughout. The
`ref` schema organization is clean. The tax config models (`TaxBracketSet`, `TaxBracket`,
`StateTaxConfig`, `FicaConfig`) have the most thorough CHECK constraints in the codebase --
a model for the others to follow.

### 2.7 Error Handling

| ID | Title | Severity |
|----|-------|----------|
| C-01 | Silent paycheck fallback | CRITICAL |
| H-11 | Chart service hides salary errors | HIGH |
| M-10 | Broad except in app factory, charts, logging | MEDIUM |
| M-11 | Grid unsafe int() conversion | MEDIUM |
| M-15 | Unhandled IntegrityError for invalid FKs | MEDIUM |

The pattern of broad `except Exception` blocks that log + return empty/default is the most
systemic issue in the codebase. It appears in 8+ locations and violates the project's "no
silent failures" rule.

### 2.8 Testing

| ID | Title | Severity |
|----|-------|----------|
| H-02 | Tests document IDOR but assert broken behavior | HIGH |
| M-15 | Tests document IntegrityError bug but don't gate it | MEDIUM |
| L-15 | No concurrent modification tests | LOW |

The test suite is comprehensive (2,033+ tests, 71 files). Every service and route has
dedicated tests. Financial calculations are tested with hand-verified expected values.
Security testing (IDOR, XSS, auth-required) is thorough. The main gap is that known bugs
are documented in tests as assertions on the broken behavior rather than as failing tests
or skipped tests with `pytest.mark.xfail`.

### 2.9 Flask and SQLAlchemy Patterns

**Application factory:** Correctly implemented with deferred imports to avoid circular imports.

**Service isolation:** Perfect. Zero Flask imports in any service file (confirmed by grep).

**N+1 queries:** The grid route loads transactions in a single query per account, then groups
by period. No obvious N+1 patterns in the hot path.

**Session management:** `db.session` usage is correct. Rollbacks are present in error handlers.
No session leaks identified.

### 2.10 Configuration and Deployment

| ID | Title | Severity |
|----|-------|----------|
| H-10 | seed_user.py wrong case query | HIGH |
| M-17 | Password in container logs | MEDIUM |
| L-09 | Gunicorn keepalive comment mismatch | LOW |
| L-10 | Debug server on 0.0.0.0 | LOW |
| L-11 | benchmark_triggers wrong case | LOW |
| L-12 | audit_cleanup no minimum days | LOW |
| L-13 | reset_mfa no audit trail | LOW |

Dependencies are pinned. Dockerfile uses non-root user. Backend network is internal. ProdConfig
validates secrets at startup. These are correct patterns worth preserving.

### 2.11 Code Organization and Maintainability

**Largest files:** `salary.py` (1097 lines), `accounts.py` (823 lines),
`validation.py` (817 lines), `transfer_service.py` (766 lines),
`chart_data_service.py` (743 lines). These are large but internally well-organized with clear
function boundaries.

**Most complex functions:** `savings.py:dashboard` (~470 lines) and
`retirement.py:_compute_gap_data` (~350 lines) have high cyclomatic complexity. They should be
extracted to service modules.

**Dead code:** None identified. No commented-out blocks or TODO/FIXME in application code.

**Documentation:** All modules, classes, and functions have docstrings. Inline comments are
present on non-obvious logic (especially in balance_calculator.py and paycheck_calculator.py).

### 2.12 Transfer Architecture Invariants

All five invariants from `docs/transfer_rework_design.md` are correctly enforced:

**Invariant 1: Every transfer has exactly two linked shadow transactions.**
Enforced by `transfer_service.create_transfer()` which atomically creates both shadows.
`_get_shadow_transactions()` validates exactly 2 non-deleted shadows exist before any mutation.

**Invariant 2: Shadow transactions are never orphaned and never created without their sibling.**
Enforced by the atomic creation in `create_transfer()` and the pair-validation in
`_get_shadow_transactions()`. `delete_transfer()` soft-deletes both shadows in the same
transaction.

**Invariant 3: Shadow amounts, statuses, and periods always equal the parent transfer's.**
Enforced by `update_transfer()` which propagates all changes to both shadows.
`carry_forward_service` routes through `transfer_service.update_transfer()` rather than
mutating shadows directly.

**Invariant 4: No code path directly mutates a shadow transaction.**
Route-level guards in `transactions.py` block direct deletion (line 554) and credit marking
(lines 259, 280) of shadows. All status changes route through transfer_service.

**Invariant 5: Balance calculator queries ONLY transactions, NOT transfers.**
Confirmed by reading `balance_calculator.py` -- it accepts only a `transactions` parameter.
The `test_no_transfers_parameter_accepted` test (test_balance_calculator.py:489) is a
regression guard against double-counting.

---

## Positive Findings (INFO)

These patterns are correct and worth preserving. Do not change them.

**I-01: Decimal used consistently for all monetary amounts.** Every model column uses
`Numeric(12,2)`, every service uses `Decimal`, every conversion goes through
`Decimal(str(...))`. No float contamination in the calculation path. The only float usage is
in `chart_data_service.py:_to_chart_float()` at the explicit Chart.js serialization boundary.

**I-02: Service layer fully isolated from Flask.** Zero imports of `request`, `session`,
`current_user`, `g`, or `current_app` in any service file. All services take plain data and
return plain data or ORM objects.

**I-03: All five transfer architecture invariants are correctly enforced.** See section 2.12.

**I-04: Security headers are comprehensive.** CSP, X-Frame-Options DENY, Referrer-Policy
strict-origin-when-cross-origin, Permissions-Policy denying camera/microphone/geolocation.

**I-05: CSRF protection properly applied.** Global via `CSRFProtect()`, HTMX header injection
via `htmx:configRequest` event, hidden fields on non-HTMX forms, disabled only in TestConfig.

**I-06: Rate limiting on authentication endpoints.** Login: 5/15min, registration: 3/hour,
MFA verify: 5/15min. Correct and proportionate.

**I-07: Open redirect protection is thorough.** `_is_safe_redirect()` in auth.py rejects
schemes, netlocs, protocol-relative URLs, backslash-prefixed paths, and embedded newlines.
Validated at both storage and redirect time.

**I-08: Session management is robust.** Password change invalidates all other sessions via
`session_invalidated_at`. Session creation timestamps are tracked. Dedicated invalidation
endpoint exists.

**I-09: All data routes have @login_required.** Verified by
`test_auth_required.py` which enumerates 139 protected endpoints.

**I-10: Ref cache implementation is sound.** Clears prior state before reload, collects all
missing members before raising, uses RuntimeError with descriptive message. Module-level dicts
are write-once-at-startup.

**I-11: ProdConfig validates critical settings at startup.** Fails fast if SECRET_KEY is
missing/default or DATABASE_URL is unset.

**I-12: All models have docstrings.** Every model file and class has a docstring explaining its
purpose.

**I-13: snake_case naming is consistent.** All column names, table names, variables, and
functions follow snake_case. No violations found.

**I-14: All dependencies pinned to exact versions.** 16 production dependencies with `==`
pinning. Reproducible builds.

**I-15: Dockerfile uses non-root user.** Container processes run as `shekel` user.

**I-16: Database network marked internal.** `backend` network in docker-compose has
`internal: true`, preventing direct host access to PostgreSQL.

**I-17: Comprehensive test suite.** 2,033+ tests across 71 files. Every service and route has
dedicated tests. IDOR, XSS, and adversarial tests exist. Financial calculations tested with
hand-verified values.

**I-18: Migration chain is descriptive and reversible.** All 25 migrations have descriptive
messages and downgrade functions (one documented exception with explicit `pass` and comment).

**I-19: FICA SS wage base cap correctly implemented.** The `calculate_fica` function handles
all three scenarios: already over cap, crossing the cap this period, and under cap.

**I-20: No sensitive data exposed in templates.** No password fields leak, backup codes shown
once with warning, no financial data outside authenticated contexts.

---

## Cross-Reference with Known Issues

### Items in `docs/fixes_improvements.md` that overlap with audit findings:

| Known Issue | Audit Finding | Status |
|-------------|---------------|--------|
| "Taxes: Calculating on Gross Pay Not Taxable Income" | Not directly found in audit. Tax calculator appears to handle pre-tax deductions. Verify fix is complete. | Needs verification |
| "Recurrence Rule: Every 6 Months" | Not directly found in audit. May relate to recurrence engine patterns. | Needs verification |
| "Net Biweekly Mismatch: Salary vs Grid" | Possibly related to M-01 (raise ordering) or C-01 (paycheck fallback). | Needs investigation |
| "Cannot Edit Raises and Deductions" | Not a code bug -- feature gap. Routes exist (H-03 references update_raise). | Appears fixed |
| "Pension: Date Validation Missing" | Not audited at field level. Related to M-14 (schema range gaps). | Needs verification |

### Items NOT in known issues (newly discovered):

| Finding | Description |
|---------|-------------|
| H-02 | Scenario_id IDOR vulnerability (documented in tests but not in fixes_improvements.md) |
| H-04 | AccountType CRUD accessible to all users |
| H-10 | seed_user.py crash on migrated database |
| M-13 | Transfer template recurrence labels all broken (wrong string format) |

### Items planned in roadmap that relate to audit findings:

- **Section 4 (UX/Grid Overhaul):** H-05 (grid float arithmetic) and M-11 (grid query params)
  could be addressed during grid work.
- **Section 5 (Debt/Account Improvements):** M-07 (loan CHECK constraints) and the
  account-type dispatch pattern are planned for this phase.

---

## Summary Statistics

- **Total findings:** 64
- **By severity:** CRITICAL: 1, HIGH: 11, MEDIUM: 17, LOW: 15, INFO: 20
- **By category:**

| Category | C | H | M | L | Total |
|----------|---|---|---|---|-------|
| Financial Correctness | 1 | 2 | 5 | 2 | 10 |
| Security | 0 | 3 | 3 | 1 | 7 |
| Input Validation | 0 | 4 | 2 | 1 | 7 |
| Error Handling | 0 | 1 | 4 | 0 | 5 |
| Database Design | 0 | 0 | 3 | 2 | 5 |
| DRY/Organization | 0 | 0 | 0 | 3 | 3 |
| Testing | 0 | 1 | 1 | 1 | 3 |
| Config/Deployment | 0 | 1 | 1 | 5 | 7 |
| Template/Display | 0 | 1 | 1 | 1 | 3 |

- **Files with zero findings:** `extensions.py`, `exceptions.py` (both app and services),
  `category.py`, `scenario.py`, `savings_goal.py`, `transfer_template.py`, `pay_period.py`,
  `health.py`, `pay_periods.py`, `categories.py`, `__init__.py` (routes), `account_resolver.py`,
  `pension_calculator.py`

- **Files with highest finding density:** `salary.py` (routes) -- 4 findings,
  `paycheck_calculator.py` -- 3 findings, `validation.py` -- 3 findings,
  `chart_data_service.py` -- 3 findings

---

## Production Readiness Assessment

**Is this codebase ready for production?**

For a **single-user deployment** (its current intended use), the codebase is **conditionally
ready** -- functional with caveats. The architecture is sound, the test coverage is strong, and
the core financial calculations use correct Decimal arithmetic. The transfer invariants are
properly enforced. Security headers, CSRF protection, and rate limiting are in place.

For a **multi-user deployment**, the codebase is **not ready** due to H-02 (scenario_id IDOR),
H-03 (IDOR info leakage), and H-04 (shared AccountType mutation).

**If shipping tomorrow, fix these tonight (in priority order):**

1. **H-10: seed_user.py wrong case** -- The app won't start after the next container rebuild.
   This is a 1-line fix (`"checking"` -> `"Checking"`).

2. **C-01: Recurrence engine silent fallback** -- Narrow the `except Exception` to specific
   expected failures, or at minimum add a user-visible warning when the fallback activates.

3. **H-02: Scenario_id IDOR** -- Add `scenario.user_id == current_user.id` check in the
   transaction create routes. Critical for multi-user safety.

4. **H-03: IDOR info leakage** -- Unify the "not found" and "not authorized" error messages
   in the four salary routes.

5. **H-05: Grid float arithmetic** -- Move subtotal computation to the route handler using
   Decimal. Prevents the user from seeing discrepancies between subtotals and balance rows.

**What can wait:**

- H-01 (ref table strings): Systematic but not causing incorrect results today as long as
  ref table names remain unchanged.
- M-01 through M-17: All medium findings are either defense-in-depth improvements or
  edge cases unlikely to be hit in normal use.
- L-01 through L-15: Quality improvements with no immediate production risk.
