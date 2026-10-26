> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan -- Adversarial Audit Remediation

**Date:** March 29, 2026
**Source:** `docs/adversarial_audit.md` (audit dated March 29, 2026)
**Status:** Planning document only -- no code changes made

This plan addresses all 44 actionable findings (C-01, H-01 through H-11, M-01 through M-17,
L-01 through L-15) from the adversarial codebase audit. Every finding is either assigned to
a specific commit, documented as an accepted risk with justification, or deferred to a named
roadmap section with reasoning.

---

## 1. Findings Status Summary

Every finding was verified against the current codebase (commit `5742c51`). Line numbers below
reflect the verified locations, not the audit's original references.

| ID | Title | Status | Resolution |
|----|-------|--------|------------|
| C-01 | Recurrence engine silent paycheck fallback | Confirmed | Commit #2 |
| H-01 | Ref table string comparisons (DeductionTiming, CalcMethod, TaxType) | Partially fixed | Commit #17 (remainder) |
| H-02 | Scenario_id IDOR on transaction creation | Confirmed | Commit #5 |
| H-03 | IDOR info leakage in salary routes | Confirmed | Commit #6 |
| H-04 | AccountType CRUD no user scoping | Confirmed | Accepted risk (single-user) |
| H-05 | Grid subtotals use float arithmetic | Confirmed | Commit #18 |
| H-06 | SalaryProfile missing annual_salary range validation | Confirmed | Commit #10 |
| H-07 | calibrate_confirm lacks Marshmallow validation | Confirmed | Commit #11 |
| H-08 | update_tax_config uses manual validation | Confirmed | Commit #12 |
| H-09 | Settings update() lacks Marshmallow schema | Confirmed | Commit #13 |
| H-10 | seed_user.py queries AccountType by wrong case | Confirmed | Commit #1 |
| H-11 | chart_data_service broad except hides salary errors | Confirmed | Commit #3 |
| M-01 | Raise application order depends on unsorted query | Confirmed | Commit #19 |
| M-02 | _get_cumulative_wages assumes sorted period list | Confirmed | Commit #19 |
| M-03 | Credit workflow no shadow transaction guard | Confirmed | Commit #7 |
| M-04 | Net worth chart uses float accumulation | Confirmed | Commit #20 |
| M-05 | Escrow calculator inflation uses integer year truncation | Confirmed | Commit #21 |
| M-06 | Growth engine allows negative investment balances | Confirmed | Commit #22 |
| M-07 | Missing CHECK constraints on loan financial columns | Confirmed | Commit #25 |
| M-08 | Inconsistent ondelete policy across foreign keys | Confirmed | Commit #26 |
| M-09 | Transaction.effective_amount doesn't check is_deleted | Confirmed | Commit #23 |
| M-10 | Broad except Exception blocks in multiple locations | Confirmed | Commit #4 |
| M-11 | grid.py unsafe int() conversion of query params | Confirmed | Commit #15 |
| M-12 | Logout accepts GET requests | Confirmed | Commit #8 |
| M-13 | Recurrence pattern name case wrong in transfer list template | Confirmed | Commit #16 |
| M-14 | Schema validation range gaps on financial inputs | Confirmed | Commit #10 |
| M-15 | Unhandled IntegrityError for invalid FK references | Confirmed | Commit #14 |
| M-16 | Transaction model lacks direct user_id column | Confirmed | Accepted (architectural) |
| M-17 | seed_user.py prints password to container logs | Confirmed | Commit #9 |
| L-01 | Transaction amounts no CHECK constraints | Confirmed | Commit #25 |
| L-02 | Rate limiter uses in-memory storage | Confirmed | Accepted risk (single-user) |
| L-03 | Various missing ondelete clauses on FKs | Confirmed | Commit #26 |
| L-04 | Interest projection uses hardcoded 365 days/year | Confirmed | Commit #24 |
| L-05 | Quarterly interest uses hardcoded 91 days | Confirmed | Commit #24 |
| L-06 | Business logic in route files (savings, retirement) | Confirmed | Commits #31-32 (before account architecture rework) |
| L-07 | Duplicate template code for tax configuration | Confirmed | Commit #33 (before account architecture rework) |
| L-08 | Transaction/transfer notes have no max length | Confirmed | Commit #14 |
| L-09 | Gunicorn keepalive comment contradicts value | Confirmed | Commit #27 |
| L-10 | run.py binds debug server on 0.0.0.0 | Confirmed | Commit #28 |
| L-11 | benchmark_triggers.py uses wrong case for ref names | Confirmed | Commit #1 |
| L-12 | audit_cleanup.py has no minimum days guard | Confirmed | Commit #29 |
| L-13 | reset_mfa.py lacks audit trail | Confirmed | Commit #30 |
| L-14 | Routes bypass centralized auth_helpers | Confirmed | Gradual migration |
| L-15 | No concurrent modification tests | Confirmed | Commit #35 |

### H-01 Partial Fix Note

Section 4 (UX/Grid Overhaul, now complete) replaced all string comparisons for Status,
TransactionType, AccountType, and RecurrencePattern with ID-based lookups via `ref_cache` and
Python Enums. The remaining H-01 violations are in **DeductionTiming** (2 locations),
**CalcMethod** (5 locations), and **TaxType** (2 locations), plus 3 template occurrences. These
are addressed in Commit #17.

---

## 2. Implementation Phases

### Phase 0: Immediate Production Safety

Fixes that must be deployed before anything else. These findings block container startup or
are identified as "fix tonight" items by the audit.

---

#### Commit #1

**Commit message:** `fix(scripts): update ref table name queries to match capitalized values`

**Finding(s) addressed:** H-10, L-11

**Files to modify:**
- `scripts/seed_user.py`
- `scripts/benchmark_triggers.py`

**What to change:**

`scripts/seed_user.py` line 109: Change
`db.session.query(AccountType).filter_by(name="checking").one()` to
`db.session.query(AccountType).filter_by(name="Checking").one()`.

`scripts/benchmark_triggers.py` line 58: Change `filter_by(name="checking")` to
`filter_by(name="Checking")`.

`scripts/benchmark_triggers.py` line 87: Change `filter_by(name="expense")` to
`filter_by(name="Expense")`.

`scripts/benchmark_triggers.py` line 88: Change `filter_by(name="every_period")` to
`filter_by(name="Every Period")`.

Search both files for any other lowercase ref table name queries and update them.

**What to test:**
- Run `seed_user.py` against a migrated database and confirm it completes without
  `NoResultFound`.
- Run `benchmark_triggers.py` and confirm it creates benchmark data without errors.

**Downstream effects:** None. These are standalone scripts.

**Migration required:** No.

---

### Phase 1: Error Handling and Silent Failures

The systemic `except Exception` pattern is the most dangerous class of bug in the codebase.
These blocks mask financial calculation failures and present incorrect data without any
indication that something went wrong.

---

#### Commit #2

**Commit message:** `fix(recurrence): propagate paycheck calculation errors instead of silent fallback`

**Finding(s) addressed:** C-01

**Files to modify:**
- `app/services/recurrence_engine.py`
- `tests/test_services/test_recurrence_engine.py`

**What to change:**

In `_get_transaction_amount` (around line 543), replace the broad `except Exception` block.
The current code catches ALL exceptions from `paycheck_calculator.calculate_paycheck()` and
silently returns `template.default_amount`. This masks 25-40% calculation errors.

Replace with:

```python
except (InvalidOperation, ZeroDivisionError, TypeError) as exc:
    logger.error(
        "Paycheck calculation failed for salary profile %d in period %s: %s. "
        "Transaction will use template default_amount but is flagged unreliable.",
        salary_profile.id, period.start_date, exc,
    )
    return template.default_amount
```

Import `InvalidOperation` from `decimal` at the top of the file. These three exceptions cover
the known failure modes: malformed Decimal data, division by zero from misconfigured tax
brackets, and type mismatches from missing data.

Any other exception (e.g., `AttributeError` from a missing relationship, `SQLAlchemyError`
from a DB issue) should propagate so the caller knows generation failed. The caller
(`_generate_period_transactions`) should catch unexpected exceptions at the period level and
log which period failed, rather than silently producing wrong data for all future periods.

**What to test:**
- Test that `InvalidOperation` from bad Decimal data returns `template.default_amount` with a
  log warning.
- Test that `ZeroDivisionError` from zero tax brackets returns `template.default_amount`.
- Test that an unexpected exception (e.g., `AttributeError`) propagates instead of being
  silently caught.
- Test the happy path still works (paycheck calculation succeeds, returns `breakdown.net_pay`).

**Downstream effects:** If an unexpected exception propagates, the recurrence engine will fail
to generate transactions for that period. This is the correct behavior -- failing visibly is
better than generating transactions with wrong amounts. The grid will show missing
transactions, which is an obvious signal to the user that something is wrong.

**Migration required:** No.

---

#### Commit #3

**Commit message:** `fix(charts): narrow chart_data_service exception handling and surface errors`

**Finding(s) addressed:** H-11

**Files to modify:**
- `app/services/chart_data_service.py`
- `tests/test_services/test_chart_data_service.py`

**What to change:**

In `get_net_pay_trajectory` (around line 713), replace:

```python
except Exception:  # pylint: disable=broad-except
    logger.exception("Failed to project salary for profile %d", profile.id)
    return empty
```

With:

```python
except (ValueError, KeyError, InvalidOperation, ZeroDivisionError) as exc:
    logger.error(
        "Salary projection failed for profile %d: %s", profile.id, exc,
    )
    return {
        "labels": [], "data": [], "gross_data": [],
        "error": f"Salary projection failed: {type(exc).__name__}",
    }
```

Import `InvalidOperation` from `decimal`. The `error` key in the return dict allows the
frontend to display a message instead of showing a blank chart with no explanation.

Update `chart_data_service.py` functions `get_spending_by_category` and
`get_budget_vs_actuals` with the same pattern: narrow the catch, add an `error` key to the
return dict.

**What to test:**
- Test that a valid salary profile returns correct chart data (no `error` key).
- Test that a profile triggering `ZeroDivisionError` returns dict with `error` key.
- Test that an unexpected exception (e.g., `SQLAlchemyError`) propagates.

**Downstream effects:** Chart templates that consume these dicts should check for an `error`
key and display a user-visible message. Add a simple check in the chart rendering partials:
`{% if data.error %}<div class="text-danger">{{ data.error }}</div>{% endif %}`.

**Migration required:** No.

---

#### Commit #4

**Commit message:** `fix(app): narrow broad except blocks in app factory, chart routes, and logging`

**Finding(s) addressed:** M-10

**Files to modify:**
- `app/__init__.py`
- `app/routes/charts.py`
- `app/utils/logging_config.py`
- `tests/test_routes/test_charts.py` (update existing tests if needed)

**What to change:**

**`app/__init__.py`** (around line 185): Replace `except Exception` around `ref_cache.init()`
with:

```python
except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError) as exc:
    app.logger.warning(
        "ref_cache initialization skipped (%s). "
        "Jinja globals will not be available until next restart.",
        type(exc).__name__,
    )
```

This catches the two expected failure modes: `ProgrammingError` (table doesn't exist yet,
migration pending) and `OperationalError` (DB unreachable). Any other exception (e.g., enum
mismatch in `ref_cache.init()`) should crash the app at startup -- running without the ref
cache produces silent template comparison failures that are worse than a startup crash.

**`app/routes/charts.py`** (6 route handlers): Replace each `except Exception` with
`except (ValueError, KeyError, SQLAlchemyError)`. Import `SQLAlchemyError` from
`sqlalchemy.exc`. These cover: bad query params (`ValueError`), missing dict keys (`KeyError`),
and DB errors (`SQLAlchemyError`).

**`app/utils/logging_config.py`** (2 locations):

Line ~123 (`_attach_request_id`): Replace `except Exception: pass` with
`except (RuntimeError, SQLAlchemyError): pass`. `RuntimeError` covers "working outside
application/request context." `SQLAlchemyError` covers session failures. Import
`SQLAlchemyError` from `sqlalchemy.exc`.

Line ~163 (`_log_request_summary`): Same pattern --
`except (RuntimeError, AttributeError): pass`. `RuntimeError` for outside-context,
`AttributeError` for anonymous user proxy.

**What to test:**
- Test app starts correctly when DB is available.
- Test that a missing ref table causes a startup warning (mock `ProgrammingError`).
- Test that a non-DB error during `ref_cache.init()` crashes the app (e.g., mock a
  `RuntimeError`).
- Test chart routes return error fragments for known exception types.
- Test chart routes let unknown exceptions propagate (500 response).

**Downstream effects:** If an unexpected exception occurs during ref_cache init, the app will
fail to start instead of running in a degraded state. This is intentional and safer.

**Migration required:** No.

---

### Phase 2: Security and Authorization

IDOR fixes, info leakage, and state-changing endpoint hardening.

---

#### Commit #5

**Commit message:** `fix(transactions): validate scenario_id ownership on transaction creation`

**Finding(s) addressed:** H-02

**Files to modify:**
- `app/routes/transactions.py`
- `tests/test_routes/test_transaction_auth.py`

**What to change:**

In `create_inline` (around line 486), after the pay period ownership check and before creating
the Transaction object, add:

```python
if "scenario_id" in data and data["scenario_id"] is not None:
    scenario = db.session.get(Scenario, data["scenario_id"])
    if not scenario or scenario.user_id != current_user.id:
        return "Not found", 404
```

Add the identical check in `create_transaction` (around line 528), in the same position.

Import `Scenario` from `app.models.scenario` if not already imported.

**Update tests in `test_transaction_auth.py`** (lines 285-341): The existing tests currently
assert `status_code == 201` when creating a transaction with another user's `scenario_id`.
These tests document the bug. Update them to assert `status_code == 404` and verify no
transaction was created.

**What to test:**
- Test creating a transaction with own scenario_id succeeds (201).
- Test creating a transaction with another user's scenario_id returns 404.
- Test creating a transaction with a nonexistent scenario_id returns 404.
- Test creating a transaction with no scenario_id (baseline) succeeds.
- Verify the same checks exist in both `create_inline` and `create_transaction`.

**Downstream effects:** Any transaction creation request with an invalid scenario_id will now
fail. This is correct behavior. Existing single-user data is unaffected.

**Migration required:** No.

---

#### Commit #6

**Commit message:** `fix(salary): unify error responses for not-found and not-authorized`

**Finding(s) addressed:** H-03

**Files to modify:**
- `app/routes/salary.py`
- `tests/test_routes/test_salary.py` (if tests assert on specific flash messages)

**What to change:**

In four functions (`delete_raise`, `update_raise`, `delete_deduction`, `update_deduction`),
replace the two-step check pattern:

```python
# BEFORE (separate messages leak information):
salary_raise = db.session.get(SalaryRaise, raise_id)
if salary_raise is None:
    flash("Raise not found.", "danger")
    return redirect(...)
profile = salary_raise.salary_profile
if profile.user_id != current_user.id:
    flash("Not authorized.", "danger")
    return redirect(...)
```

With a single combined check:

```python
# AFTER (identical response for both cases):
salary_raise = db.session.get(SalaryRaise, raise_id)
if salary_raise is None or salary_raise.salary_profile.user_id != current_user.id:
    flash("Raise not found.", "danger")
    return redirect(...)
```

Apply the same pattern to all four functions:
- `delete_raise` (lines ~393-399)
- `update_raise` (lines ~425-431)
- `delete_deduction` (lines ~528-534)
- `update_deduction` (lines ~560-566)

For deductions, use "Deduction not found." as the unified message.

**What to test:**
- Test that a nonexistent raise_id returns "Raise not found." flash.
- Test that another user's raise_id returns the same "Raise not found." flash (not "Not
  authorized.").
- Same tests for deductions.
- Test that own raise/deduction operations still work correctly.

**Downstream effects:** None. The only change is the flash message text for the unauthorized
case.

**Migration required:** No.

---

#### Commit #7

**Commit message:** `fix(credit): add shadow transaction guard in credit_workflow service`

**Finding(s) addressed:** M-03

**Files to modify:**
- `app/services/credit_workflow.py`
- `tests/test_services/test_credit_workflow.py`

**What to change:**

In `mark_as_credit` (around line 57), after the income check, add:

```python
if txn.transfer_id is not None:
    raise ValidationError("Cannot mark transfer transactions as credit.")
```

This adds defense-in-depth at the service layer. The route layer already blocks this
(transactions.py line 259), but the service should enforce its own invariants independently.

**What to test:**
- Test that calling `mark_as_credit` on a shadow transaction raises `ValidationError`.
- Test that calling `mark_as_credit` on a regular expense transaction succeeds.
- Test that calling `mark_as_credit` on income raises `ValidationError` (existing test).

**Downstream effects:** None. The route already blocks this. This commit only hardens the
service layer.

**Migration required:** No.

---

#### Commit #8

**Commit message:** `fix(auth): change logout to POST-only with CSRF-protected form`

**Finding(s) addressed:** M-12

**Files to modify:**
- `app/routes/auth.py`
- `app/templates/base.html` (or whichever template contains the logout link/button)
- `tests/test_routes/test_auth.py`

**What to change:**

In `auth.py`, change the logout route from:

```python
@auth_bp.route("/logout")
```

To:

```python
@auth_bp.route("/logout", methods=["POST"])
```

In the navigation template (`base.html`), change the logout link from an `<a>` tag to a
`<form>` with CSRF:

```html
<form action="{{ url_for('auth.logout') }}" method="post" class="d-inline">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <button type="submit" class="dropdown-item">Logout</button>
</form>
```

**What to test:**
- Test that POST to /logout succeeds and redirects to login.
- Test that GET to /logout returns 405 Method Not Allowed.
- Test that POST without CSRF token returns 400.
- Verify the nav template renders a form button, not an `<a>` link.

**Downstream effects:** Any bookmarks or external links to /logout will stop working. This is
correct -- logout should not be triggerable via a URL.

**Migration required:** No.

---

#### Commit #9

**Commit message:** `fix(scripts): mask seed user password in container logs`

**Finding(s) addressed:** M-17

**Files to modify:**
- `scripts/seed_user.py`

**What to change:**

Line 142: Change `print(f"  Password: {password}")` to
`print("  Password: [set via SEED_USER_PASSWORD env var or default]")`.

**What to test:**
- Run `seed_user.py` and confirm the output does not contain the actual password string.
- Confirm the seed user can still log in with the configured password.

**Downstream effects:** None. The password is still set correctly; it's just not printed.

**Migration required:** No.

---

### Phase 3: Input Validation

Schema additions and validation gaps. All new schemas follow the existing Marshmallow patterns
in `app/schemas/validation.py`.

---

#### Commit #10

**Commit message:** `fix(schemas): add range validation to financial input schemas`

**Finding(s) addressed:** H-06, M-14

**Files to modify:**
- `app/schemas/validation.py`
- `tests/test_schemas/test_validation.py`

**What to change:**

**SalaryProfileCreateSchema** (around line 160): Add range validation to `annual_salary`:

```python
annual_salary = fields.Decimal(
    required=True, places=2, as_string=True,
    validate=validate.Range(min=0, min_inclusive=False),
)
```

**RaiseCreateSchema** (around lines 222-223): Add range to `effective_year`:

```python
effective_year = fields.Integer(
    required=True,
    validate=validate.Range(min=2000, max=2100),
)
```

For `percentage` and `flat_amount`, negative values are valid (pay cuts). Add bounds to prevent
absurd values:

```python
percentage = fields.Decimal(
    places=2, as_string=True,
    validate=validate.Range(min=-100, max=1000),
)
flat_amount = fields.Decimal(
    places=2, as_string=True,
    validate=validate.Range(min=-10000000, max=10000000),
)
```

**SavingsGoalCreateSchema** (around line 418): Add min=0 to `contribution_per_period`:

```python
contribution_per_period = fields.Decimal(
    places=2, as_string=True, allow_none=True,
    validate=validate.Range(min=0),
)
```

**AccountCreateSchema** (around line 450): `anchor_balance` is intentionally unbounded --
negative balances are valid for liability accounts. Document this with a comment. No change
needed.

**What to test:**
- Test `SalaryProfileCreateSchema` rejects zero and negative annual_salary.
- Test `RaiseCreateSchema` rejects effective_year outside 2000-2100.
- Test `SavingsGoalCreateSchema` rejects negative contribution_per_period.
- Test that valid values pass all schemas unchanged.

**Downstream effects:** Existing routes using these schemas will now reject invalid values at
the schema layer instead of cascading into calculation errors.

**Migration required:** No.

---

#### Commit #11

**Commit message:** `feat(schemas): add CalibrationConfirmSchema for paycheck calibration`

**Finding(s) addressed:** H-07

**Files to modify:**
- `app/schemas/validation.py`
- `app/routes/salary.py`
- `tests/test_routes/test_salary.py`

**What to change:**

Add a new schema in `validation.py`:

```python
class CalibrationConfirmSchema(Schema):
    """Validate calibrate_confirm POST inputs."""
    actual_gross_pay = fields.Decimal(required=True, places=2, as_string=True,
                                      validate=validate.Range(min=0, min_inclusive=False))
    actual_federal_tax = fields.Decimal(required=True, places=2, as_string=True,
                                        validate=validate.Range(min=0))
    actual_state_tax = fields.Decimal(required=True, places=2, as_string=True,
                                      validate=validate.Range(min=0))
    actual_social_security = fields.Decimal(required=True, places=2, as_string=True,
                                            validate=validate.Range(min=0))
    actual_medicare = fields.Decimal(required=True, places=2, as_string=True,
                                     validate=validate.Range(min=0))
    pay_stub_date = fields.Date(required=True)
```

Add fields for each deduction amount that the route currently reads from `request.form`.
Read the route's full field list to ensure all fields are covered.

In `salary.py:calibrate_confirm` (around line 797), replace the manual `Decimal()` wrapping
with:

```python
schema = CalibrationConfirmSchema()
errors = schema.validate(request.form)
if errors:
    flash("Please correct the highlighted errors.", "danger")
    return redirect(url_for("salary.calibrate", profile_id=profile_id))
data = schema.load(request.form)
```

Remove the broad `except Exception` at line ~818 that currently catches `InvalidOperation`
and `ValueError`.

**What to test:**
- Test valid calibration data is accepted and processed.
- Test non-numeric values produce field-level errors.
- Test negative gross pay is rejected.
- Test missing required fields produce errors.

**Downstream effects:** Users will get specific field-level error messages instead of the
generic "Failed to save calibration."

**Migration required:** No.

---

#### Commit #12

**Commit message:** `feat(schemas): add StateTaxConfigSchema for tax config update`

**Finding(s) addressed:** H-08

**Files to modify:**
- `app/schemas/validation.py`
- `app/routes/salary.py`
- `tests/test_routes/test_salary.py`

**What to change:**

Add a new schema:

```python
class StateTaxConfigSchema(Schema):
    """Validate state tax configuration update."""
    state_code = fields.String(required=True, validate=validate.Length(min=2, max=2))
    flat_rate = fields.Decimal(places=5, as_string=True,
                               validate=validate.Range(min=0, max=1))
    standard_deduction = fields.Decimal(places=2, as_string=True,
                                        validate=validate.Range(min=0))
    tax_year = fields.Integer(required=True,
                              validate=validate.Range(min=2000, max=2100))
```

In `salary.py:update_tax_config` (around line 883), replace the manual parsing and inline
`try/except` blocks with the schema. This also eliminates the `TaxType.filter_by(name="flat")`
string comparison at line 923 -- the schema should accept a `tax_type_id` integer field
instead of deriving it from a name string. (Note: the TaxType ID lookup fix overlaps with
H-01. If Phase 4 has not been implemented yet when this commit is made, use the name-based
lookup temporarily and add a TODO comment referencing H-01.)

**What to test:**
- Test valid tax config update is accepted.
- Test invalid state code (wrong length) is rejected.
- Test flat_rate > 100% is rejected.
- Test invalid tax_year is rejected.

**Downstream effects:** None beyond the route.

**Migration required:** No.

---

#### Commit #13

**Commit message:** `feat(schemas): add UserSettingsSchema for settings update`

**Finding(s) addressed:** H-09

**Files to modify:**
- `app/schemas/validation.py`
- `app/routes/settings.py`
- `tests/test_routes/test_settings.py`

**What to change:**

Add a new schema:

```python
class UserSettingsSchema(Schema):
    """Validate user settings update."""
    grid_default_periods = fields.Integer(
        validate=validate.Range(min=1, max=52))
    projection_periods = fields.Integer(
        validate=validate.Range(min=1, max=260))
    low_balance_threshold = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0))
    default_grid_account_id = fields.Integer(allow_none=True)
    default_inflation_rate = fields.Decimal(
        places=4, as_string=True,
        validate=validate.Range(min=0, max=1))
    retirement_age = fields.Integer(
        validate=validate.Range(min=18, max=100))
    safe_withdrawal_rate = fields.Decimal(
        places=4, as_string=True,
        validate=validate.Range(min=0, max=1))
```

Read `settings.py:update()` (lines 135-202) to capture all fields and their current manual
validation. Replace the inline `try/except` blocks with schema validation. Importantly,
change `low_balance_threshold` from `int` cast to `Decimal` (fixing the audit's note that
decimal values raise ValueError). Change `inflation_rate` from `float` cast to `Decimal`
(fixing float imprecision).

**What to test:**
- Test valid settings update is accepted.
- Test grid_default_periods of 0 or 53 is rejected.
- Test negative low_balance_threshold is rejected.
- Test inflation_rate as Decimal (not float) passes through correctly.

**Downstream effects:** The `low_balance_threshold` column may need to be verified as
`Numeric` in the model. If it's `Integer`, the Decimal schema field should be `Integer` to
match.

**Migration required:** No.

---

#### Commit #14

**Commit message:** `fix(schemas): add max length to notes fields and FK existence validation`

**Finding(s) addressed:** L-08, M-15

**Files to modify:**
- `app/schemas/validation.py`
- `app/routes/transactions.py`
- `tests/test_routes/test_transactions.py`
- `tests/test_adversarial/test_hostile_qa.py`

**What to change:**

**L-08 -- Notes max length:** Add `validate=validate.Length(max=500)` to every `notes` field
that currently lacks it. Affected schemas (verified from agent data):
- `TransactionUpdateSchema` line ~32
- `TransactionCreateSchema` line ~47
- `InlineTransactionCreateSchema` line ~65
- `TransferCreateSchema` line ~377
- `TransferUpdateSchema` line ~399

This matches the existing `max=500` on `CalibrationSchema` and `RateChangeSchema`.

**M-15 -- FK existence validation:** In `transactions.py` `create_inline` and
`create_transaction`, add an explicit check for `category_id` existence after schema
validation and before creating the Transaction:

```python
if "category_id" in data and data["category_id"] is not None:
    cat = db.session.get(Category, data["category_id"])
    if not cat or cat.user_id != current_user.id:
        return "Category not found", 404
```

The `create_inline` route already has this check. Verify `create_transaction` does as well.
If not, add it.

As a safety net, wrap the `db.session.commit()` in both creation routes with:

```python
try:
    db.session.commit()
except IntegrityError:
    db.session.rollback()
    return "Invalid reference. Check that all referenced records exist.", 400
```

Import `IntegrityError` from `sqlalchemy.exc`.

Update `test_hostile_qa.py` tests that currently document the 500 error to assert 400 instead.

**What to test:**
- Test that notes longer than 500 characters are rejected by schema.
- Test that submitting a nonexistent category_id returns 400 (not 500).
- Test that submitting a nonexistent status_id returns 400.
- Test that valid FK references are accepted.

**Downstream effects:** None. These are additional guards that prevent invalid data from
reaching the database.

**Migration required:** No.

---

#### Commit #15

**Commit message:** `fix(grid): use safe type-coercing query param parsing`

**Finding(s) addressed:** M-11

**Files to modify:**
- `app/routes/grid.py`
- `tests/test_routes/test_grid.py`

**What to change:**

In `grid.py:index` (around lines 158-159), replace:

```python
num_periods = int(request.args.get("periods", ...))
start_offset = int(request.args.get("offset", 0))
```

With:

```python
num_periods = request.args.get(
    "periods",
    default=(current_user.settings.grid_default_periods
             if current_user.settings else 6),
    type=int,
)
start_offset = request.args.get("offset", default=0, type=int)
```

Apply the same fix in `balance_row` (around lines 321-322).

Flask's `request.args.get(..., type=int)` returns the default value when the input cannot be
converted, instead of raising `ValueError`.

**What to test:**
- Test `/?periods=abc` returns the grid with the default number of periods (not 500 error).
- Test `/?offset=-1` works (negative offset may be valid for past periods).
- Test `/?periods=6&offset=0` works normally.

**Downstream effects:** None. Malformed URLs now gracefully fall back to defaults.

**Migration required:** No.

---

### Phase 4: Ref Table String Elimination

Complete the work started in Section 4 by covering the remaining ref tables: DeductionTiming,
CalcMethod, and TaxType.

---

#### Commit #16

**Commit message:** `fix(templates): correct recurrence pattern name case in transfer list`

**Finding(s) addressed:** M-13

**Files to modify:**
- `app/templates/transfers/list.html`
- `tests/test_routes/test_transfers.py` (verify display is correct)

**What to change:**

In `transfers/list.html` (lines ~53-70), update all pattern name comparisons from
snake_case to the correct capitalized database values. The `templates/list.html` file
already uses the correct format -- mirror it exactly:

| Current (wrong) | Correct |
|-----------------|---------|
| `'every_period'` | `'Every Period'` |
| `'every_n_periods'` | `'Every N Periods'` |
| `'monthly'` | `'Monthly'` |
| `'monthly_first'` | `'Monthly First'` |
| `'quarterly'` | `'Quarterly'` |
| `'semi_annual'` | `'Semi-Annual'` |
| `'annual'` | `'Annual'` |
| `'once'` | `'Once'` |

Alternatively, if Section 4 has already converted `templates/list.html` to use ID-based
comparisons (via Jinja globals like `PATTERN_EVERY_PERIOD`), use the same ID-based approach
here for consistency.

**What to test:**
- View the transfer templates list page and confirm each recurrence pattern displays its
  contextual description (e.g., "Every paycheck", "Monthly (day 15)") instead of falling
  through to the generic dict lookup.

**Downstream effects:** None. Display-only fix.

**Migration required:** No.

---

#### Commit #17

**Commit message:** `refactor(ref): add DeductionTiming/CalcMethod/TaxType enums and replace string comparisons`

**Finding(s) addressed:** H-01 (remainder)

**Files to modify:**
- `app/enums.py`
- `app/ref_cache.py`
- `app/services/paycheck_calculator.py`
- `app/services/tax_calculator.py`
- `app/services/investment_projection.py`
- `app/routes/salary.py`
- `app/templates/salary/_deductions_section.html`
- `scripts/seed_ref_tables.py`
- `tests/conftest.py`
- `tests/test_services/test_paycheck_calculator.py`
- `tests/test_services/test_tax_calculator.py`
- `tests/test_routes/test_salary.py`

**What to change:**

**Step 1 -- Add enums to `app/enums.py`:**

```python
class DeductionTimingEnum(str, Enum):
    """Valid deduction timing values."""
    PRE_TAX = "Pre-Tax"
    POST_TAX = "Post-Tax"

class CalcMethodEnum(str, Enum):
    """Valid calculation method values."""
    PERCENTAGE = "Percentage"
    FIXED = "Fixed"

class TaxTypeEnum(str, Enum):
    """Valid tax type values."""
    NONE = "None"
    FLAT = "Flat"
    BRACKETS = "Brackets"
```

**Step 2 -- Add cache functions to `app/ref_cache.py`:**

Add `deduction_timing_id()`, `calc_method_id()`, and `tax_type_id()` functions following the
existing pattern (e.g., `status_id()`). Update `init()` to load these mappings at startup.

**Step 3 -- Register Jinja globals in `app/__init__.py`:**

Add globals like `TIMING_PRE_TAX`, `TIMING_POST_TAX`, `CALC_PERCENTAGE`, `CALC_FIXED` for
template use.

**Step 4 -- Replace all string comparisons:**

| File | Current Code | Replacement |
|------|-------------|-------------|
| `paycheck_calculator.py:360` | `ded.deduction_timing.name != timing_name` | `ded.deduction_timing_id != ref_cache.deduction_timing_id(timing_enum)` (refactor `_calculate_deductions` to accept a `DeductionTimingEnum` member instead of a string) |
| `paycheck_calculator.py:374` | `ded.calc_method.name == "percentage"` | `ded.calc_method_id == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)` |
| `tax_calculator.py:254` | `state_config.tax_type.name == "none"` | `state_config.tax_type_id == ref_cache.tax_type_id(TaxTypeEnum.NONE)` |
| `investment_projection.py:68` | `ded.calc_method_name == "percentage"` | `ded.calc_method_id == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)` |
| `salary.py:498` | `calc_method.name == "percentage"` | `calc_method.id == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)` |
| `salary.py:580` | `calc_method.name == "percentage"` | Same |
| `salary.py:923` | `TaxType.filter_by(name="flat")` | `ref_cache.tax_type_id(TaxTypeEnum.FLAT)` |

**Step 5 -- Replace template comparisons in `_deductions_section.html`:**

| Current | Replacement |
|---------|-------------|
| `d.deduction_timing.name == 'pre_tax'` | `d.deduction_timing_id == TIMING_PRE_TAX` |
| `d.calc_method.name == 'percentage'` | `d.calc_method_id == CALC_PERCENTAGE` |

**Step 6 -- Update seed scripts and test conftest** to use capitalized display names for
these ref tables if they don't already.

**What to test:**
- Test `_calculate_deductions` correctly separates pre-tax and post-tax deductions using IDs.
- Test percentage-based deduction calculation works (CalcMethod ID comparison).
- Test state tax config with type "none" correctly skips calculation.
- Test that the salary deductions template renders pre-tax/post-tax badges correctly.
- Run the full paycheck calculator test suite.
- Run the full salary route test suite.

**Downstream effects:** After this commit, zero ref table string comparisons remain in the
codebase. All logic uses integer IDs via the enum cache. Display names can be freely changed
without affecting behavior.

**Migration required:** Only if ref table display names need updating (e.g., capitalizing
"pre_tax" to "Pre-Tax"). If the display names are already correct in the database, no
migration is needed. Verify by reading `scripts/seed_ref_tables.py` and the latest migration.

---

### Phase 5: Financial Correctness

Fixes to calculation logic, sort ordering, Decimal discipline, and edge cases. Each commit
addresses one logical concern.

---

#### Commit #18

**Commit message:** `fix(grid): compute subtotals server-side using Decimal arithmetic`

**Finding(s) addressed:** H-05

**Files to modify:**
- `app/routes/grid.py`
- `app/templates/grid/grid.html`
- `tests/test_routes/test_grid.py`

**What to change:**

In `grid.py:index`, after loading transactions per period, compute the subtotals using
Decimal:

```python
from decimal import Decimal

for period in periods:
    period_txns = transactions_by_period.get(period.id, [])
    total_income = sum(
        (t.effective_amount for t in period_txns if t.is_income),
        Decimal("0"),
    )
    total_expenses = sum(
        (t.effective_amount for t in period_txns if t.is_expense),
        Decimal("0"),
    )
    period.subtotals = {
        "income": total_income,
        "expenses": total_expenses,
        "net": total_income - total_expenses,
    }
```

In `grid.html`, replace the Jinja `|float` accumulation loops (lines ~171, 249, 266) with
direct access to the pre-computed subtotals:

```html
<!-- Replace float accumulation with: -->
{{ "${:,.2f}".format(period.subtotals.income) }}
```

Remove the `{% set total = namespace(val=0) %}` / `{% set total.val = total.val + ... %}` loop
patterns that use `|float`.

**What to test:**
- Test that subtotals match the balance row values exactly (no penny discrepancies).
- Test with a large number of transactions (20+) to verify no float drift.
- Test with zero transactions in a period (subtotals should be $0.00).
- Test that the grid renders correctly with the new subtotal source.

**Downstream effects:** The balance row and subtotals now both use server-side Decimal.
Discrepancies between them are eliminated.

**Migration required:** No.

---

#### Commit #19

**Commit message:** `fix(paycheck): enforce deterministic raise and period ordering`

**Finding(s) addressed:** M-01, M-02

**Files to modify:**
- `app/services/paycheck_calculator.py`
- `tests/test_services/test_paycheck_calculator.py`

**What to change:**

**M-01:** In `_apply_raises` (around line 224), add an explicit sort at the start of the
function:

```python
sorted_raises = sorted(
    profile.raises,
    key=lambda r: (r.effective_year, r.effective_month),
)
for raise_obj in sorted_raises:
```

This ensures flat raises are applied before percentage raises within the same effective date,
producing deterministic results regardless of database query order. Document the sort order
choice in the docstring.

**M-02:** In `_get_cumulative_wages` (around line 415), add a defensive sort:

```python
sorted_periods = sorted(all_periods, key=lambda p: p.start_date)
for p in sorted_periods:
```

This ensures the `break` on `p.start_date >= period.start_date` works correctly regardless
of the input list's order.

**What to test:**
- Test `_apply_raises` with two raises on the same effective date (flat + percentage).
  Verify the result is deterministic and matches the expected calculation (flat first, then
  percentage).
- Test `_apply_raises` with raises in reverse order -- result should match the sorted-order
  result.
- Test `_get_cumulative_wages` with an unsorted period list. Verify cumulative wages match
  the expected sorted result.
- Test `_get_cumulative_wages` with a single period (edge case).
- Test with periods spanning the year boundary.

**Downstream effects:** All salary projections now produce deterministic results. Users may
see slightly different projected values if raises were previously applied in inconsistent
order. The new values are mathematically correct.

**Migration required:** No.

---

#### Commit #20

**Commit message:** `fix(charts): use Decimal accumulation in net worth chart data`

**Finding(s) addressed:** M-04

**Files to modify:**
- `app/services/chart_data_service.py`
- `tests/test_services/test_chart_data_service.py`

**What to change:**

In `get_net_worth_over_time` (around line 598), change:

```python
net_worth = [0.0] * num_points
```

To:

```python
net_worth = [Decimal("0")] * num_points
```

Update all accumulation lines that use `+=` to work with Decimal values. At the final output
step (line ~617), convert to float for Chart.js:

```python
return {
    "labels": labels,
    "data": [float(v.quantize(Decimal("0.01"))) for v in net_worth],
}
```

This ensures accumulation precision is maintained and rounding only happens once at the
serialization boundary.

**What to test:**
- Test with multiple accounts whose balances have sub-cent precision during accumulation.
- Test that the output values are floats (Chart.js requirement).
- Test that the output is rounded to 2 decimal places.

**Downstream effects:** None. Chart output format is unchanged.

**Migration required:** No.

---

#### Commit #21

**Commit message:** `fix(escrow): use month-aware inflation year calculation`

**Finding(s) addressed:** M-05

**Files to modify:**
- `app/services/escrow_calculator.py`
- `tests/test_services/test_escrow_calculator.py`

**What to change:**

In `calculate_monthly_escrow` (around line 41), replace:

```python
years_elapsed = (as_of_date.year - created.year)
```

With a month-aware calculation similar to `_inflation_years` in `paycheck_calculator.py`:

```python
months_elapsed = (
    (as_of_date.year - created.year) * 12
    + (as_of_date.month - created.month)
)
years_elapsed = max(months_elapsed / Decimal("12"), Decimal("0"))
```

This prevents the case where a component created in December and projected in January of the
next year incorrectly applies a full year of inflation (~$120 error on $4,000 at 3%).

**What to test:**
- Test component created Dec 2024, as_of Jan 2025: years_elapsed should be ~0.08 (1/12),
  not 1.
- Test component created Jan 2024, as_of Jan 2025: years_elapsed should be 1.
- Test component created Jan 2024, as_of Jul 2025: years_elapsed should be 1.5.
- Test with zero inflation rate (no change regardless of years).

**Downstream effects:** Escrow projections near year boundaries will be more accurate.
Existing projections may change by up to ~$10/month in the affected edge case.

**Migration required:** No.

---

#### Commit #22

**Commit message:** `fix(growth): clamp investment balance to non-negative`

**Finding(s) addressed:** M-06

**Files to modify:**
- `app/services/growth_engine.py`
- `tests/test_services/test_growth_engine.py`

**What to change:**

In `project_balance` (around line 155, after the growth calculation), add:

```python
current_balance = max(current_balance, Decimal("0"))
```

This prevents investment projections from showing negative account balances, which is
unrealistic for standard investment accounts (brokerage, IRA, 401k). If margin accounts are
ever supported, this clamp can be made conditional on account type.

**What to test:**
- Test with a high negative return rate that would push balance below zero. Verify balance
  clamps to 0.
- Test that a positive return rate is unaffected by the clamp.
- Test with zero starting balance and negative growth (stays at 0).
- Test with zero starting balance and positive contributions (works correctly).

**Downstream effects:** Investment projection charts will show zero instead of negative values
when projected returns are sufficiently negative.

**Migration required:** No.

---

#### Commit #23

**Commit message:** `fix(transaction): check is_deleted in effective_amount property`

**Finding(s) addressed:** M-09

**Files to modify:**
- `app/models/transaction.py`
- `tests/test_models/test_transaction.py`

**What to change:**

In `effective_amount` property (around line 110), add `is_deleted` check as the first line:

```python
@property
def effective_amount(self):
    """Return the amount used in balance calculations."""
    if self.is_deleted:
        return Decimal("0")
    if self.status and self.status.excludes_from_balance:
        return Decimal("0")
    if self.status and self.status.is_settled:
        return self.actual_amount if self.actual_amount is not None else self.estimated_amount
    return self.estimated_amount
```

**What to test:**
- Test deleted transaction with status "projected" returns Decimal("0").
- Test deleted transaction with status "done" returns Decimal("0").
- Test non-deleted transaction with status "done" returns actual_amount.
- Test non-deleted transaction with status "projected" returns estimated_amount.

**Downstream effects:** The grid template uses `effective_amount` for subtotals (via Commit
#18). After this fix, any code path that accidentally includes deleted transactions in its
query will get zero from `effective_amount` instead of the estimated amount. The balance
calculator bypasses this property (reads amounts directly with its own `is_deleted` filter),
so balance calculations are unaffected.

**Migration required:** No.

---

#### Commit #24

**Commit message:** `fix(interest): use actual calendar day counts for interest calculations`

**Finding(s) addressed:** L-04, L-05

**Files to modify:**
- `app/services/interest_projection.py`
- `tests/test_services/test_interest_projection.py`

**What to change:**

**L-04:** Add a comment documenting the actual/365 convention:

```python
# US bank convention: actual/365 day count. Leap years cause ~0.27%
# overstatement ($1.23 per $100K at 4.5% APY). Acceptable approximation
# for projection purposes. See also: 30/360, actual/actual conventions.
DAYS_IN_YEAR = Decimal("365")
```

This is documented as an accepted approximation. No code change needed beyond the comment.

**L-05:** Replace the hardcoded `days_in_quarter = Decimal("91")` (around line 55) with an
actual quarter length calculation:

```python
elif compounding_frequency == "quarterly":
    quarterly_rate = apy / QUARTERS_IN_YEAR
    # Calculate actual quarter length from the period's position in the year
    quarter_start_month = ((period_start.month - 1) // 3) * 3 + 1
    quarter_start = date(period_start.year, quarter_start_month, 1)
    next_quarter_month = quarter_start_month + 3
    if next_quarter_month > 12:
        quarter_end = date(period_start.year + 1, next_quarter_month - 12, 1)
    else:
        quarter_end = date(period_start.year, next_quarter_month, 1)
    days_in_quarter = Decimal(str((quarter_end - quarter_start).days))
    interest = balance * quarterly_rate * (period_days / days_in_quarter)
```

This uses the actual number of days in the quarter containing the period start date (90-92
days depending on the quarter).

**What to test:**
- Test Q1 (Jan-Mar): 90 days (non-leap year).
- Test Q1 (Jan-Mar): 91 days (leap year).
- Test Q2 (Apr-Jun): 91 days.
- Test Q3 (Jul-Sep): 92 days.
- Test Q4 (Oct-Dec): 92 days.
- Test interest calculation matches expected values for each quarter.

**Downstream effects:** HYSA interest projections will be slightly more accurate near quarter
boundaries. The change is small (1-2 days difference, ~$0.10 per $100K at 4.5% APY per
quarter).

**Migration required:** No.

---

### Phase 6: Database Hardening

CHECK constraints and ondelete policies via Alembic migrations.

---

#### Commit #25

**Commit message:** `fix(db): add CHECK constraints to loan params and transaction amounts`

**Finding(s) addressed:** M-07, L-01

**Files to modify:**
- `app/models/loan_params.py`
- `app/models/transaction.py`
- `migrations/versions/<new>.py` (Alembic migration)

**What to change:**

**LoanParams** (`loan_params.py`): Add CHECK constraints to the model's `__table_args__`:

```python
db.CheckConstraint("original_principal > 0", name="ck_loan_params_orig_principal"),
db.CheckConstraint("current_principal >= 0", name="ck_loan_params_curr_principal"),
db.CheckConstraint("interest_rate >= 0", name="ck_loan_params_interest_rate"),
db.CheckConstraint("term_months > 0", name="ck_loan_params_term_months"),
```

**Transaction** (`transaction.py`): Add CHECK constraints:

```python
db.CheckConstraint("estimated_amount >= 0", name="ck_transactions_estimated_amount"),
db.CheckConstraint(
    "actual_amount IS NULL OR actual_amount >= 0",
    name="ck_transactions_actual_amount",
),
```

Generate the Alembic migration:
`flask db migrate -m "add CHECK constraints to loan_params and transactions"`

Verify the migration adds the constraints without failing on existing data. If any existing
rows violate the constraints (e.g., negative amounts), fix the data first.

**What to test:**
- Test that inserting a loan with `term_months=0` raises `IntegrityError`.
- Test that inserting a loan with `original_principal=-1` raises `IntegrityError`.
- Test that inserting a transaction with `estimated_amount=-1` raises `IntegrityError`.
- Test that valid values are accepted.
- Run the full test suite to verify no existing tests violate the new constraints.

**Downstream effects:** Any code path that creates transactions or loans with invalid values
will now fail at the database level. This is defense-in-depth -- the Marshmallow schemas
should catch these first (after Phase 3 commits).

**Migration required:** Yes. `flask db migrate -m "add CHECK constraints to loan_params and transactions"`

---

#### Commit #26

**Commit message:** `fix(db): standardize ondelete policies across all foreign keys`

**Finding(s) addressed:** M-08, L-03

**Files to modify:**
- `app/models/account.py`
- `app/models/transaction.py`
- `app/models/transfer.py`
- `app/models/transaction_template.py`
- Any other models with missing ondelete policies
- `migrations/versions/<new>.py` (Alembic migration)

**What to change:**

Apply consistent ondelete policies based on FK type:

| FK Target | Policy | Rationale |
|-----------|--------|-----------|
| `ref.*` tables (account_types, statuses, etc.) | `RESTRICT` | Prevent deleting ref data in use |
| `auth.users` (user_id) | `CASCADE` | Already set; user deletion removes all data |
| `budget.accounts` (account_id) | `RESTRICT` | Prevent deleting accounts with transactions |
| `budget.categories` (category_id) | `SET NULL` | Allow category deletion, preserve transactions |
| `budget.pay_periods` (pay_period_id) | `RESTRICT` | Prevent deleting periods with transactions |
| `budget.scenarios` (scenario_id) | `CASCADE` | Already set; scenario deletion removes transactions |
| `budget.recurrence_rules` (recurrence_rule_id) | `SET NULL` | Allow rule deletion, preserve templates |

For each model, add the `ondelete` clause to ForeignKey definitions that currently lack one.
Generate the Alembic migration to alter the constraints.

**What to test:**
- Test that deleting a ref table row in use raises `IntegrityError`.
- Test that deleting a category sets transactions' category_id to NULL.
- Test that deleting a scenario cascades to its transactions.
- Run the full test suite.

**Downstream effects:** Deleting records that have dependent rows will now behave consistently
and predictably instead of relying on PostgreSQL's implicit `NO ACTION`.

**Migration required:** Yes. `flask db migrate -m "standardize ondelete policies across all foreign keys"`

---

### Phase 7: Scripts and Deployment

Configuration and script fixes with no impact on core application logic.

---

#### Commit #27

**Commit message:** `fix(gunicorn): align keepalive with Nginx timeout configuration`

**Finding(s) addressed:** L-09

**Files to modify:**
- `gunicorn.conf.py`

**What to change:**

Verify the actual Nginx `keepalive_timeout` by reading the Nginx configuration. Then set
Gunicorn's keepalive accordingly:

- If Nginx keepalive_timeout is 65s: set `keepalive = 70` and update the comment to
  "Slightly higher than Nginx keepalive_timeout (65s)."
- If Nginx keepalive_timeout is lower (e.g., 5s): set Gunicorn `keepalive = 2` and update
  comment accordingly.

The key principle: Gunicorn keepalive must be **higher** than Nginx keepalive_timeout so
that Nginx closes idle connections first. Otherwise Nginx may attempt to reuse a connection
that Gunicorn has already closed, causing intermittent 502 errors.

**What to test:**
- Deploy and verify no 502 errors under normal use.
- Verify the comment matches the actual value.

**Downstream effects:** May resolve intermittent 502 errors if they exist.

**Migration required:** No.

---

#### Commit #28

**Commit message:** `fix(dev): bind Flask debug server to localhost only`

**Finding(s) addressed:** L-10

**Files to modify:**
- `run.py`

**What to change:**

Line 14: Change `app.run(debug=True, host="0.0.0.0", port=5000)` to
`app.run(debug=True, host="127.0.0.1", port=5000)`.

**What to test:**
- Verify the dev server starts and is accessible on `localhost:5000`.
- Verify it is NOT accessible from other machines on the network.

**Downstream effects:** None in production (Gunicorn is used, not `run.py`). Only affects
local development.

**Migration required:** No.

---

#### Commit #29

**Commit message:** `fix(scripts): add minimum retention guard to audit_cleanup`

**Finding(s) addressed:** L-12

**Files to modify:**
- `scripts/audit_cleanup.py`

**What to change:**

In `parse_args()` (around line 47), add a minimum value check after argument parsing:

```python
args = parser.parse_args()
if args.days < 1:
    parser.error("--days must be at least 1 to prevent deleting the entire audit log.")
return args
```

Alternatively, use `type=int` with a custom validator or `choices=range(1, 3651)`.

**What to test:**
- Test `--days 0` produces an error message and exits non-zero.
- Test `--days -1` produces an error message.
- Test `--days 1` is accepted.
- Test `--days 365` (default) works normally.

**Downstream effects:** None.

**Migration required:** No.

---

#### Commit #30

**Commit message:** `fix(scripts): add confirmation prompt and audit logging to reset_mfa`

**Finding(s) addressed:** L-13

**Files to modify:**
- `scripts/reset_mfa.py`

**What to change:**

Add a confirmation prompt before clearing MFA:

```python
if not args.force:
    confirm = input(f"Reset MFA for {email}? This cannot be undone. [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        sys.exit(0)
```

Add `--force` flag to `argparse` to skip the prompt (for scripted use in entrypoint.sh).

Add an audit log entry after successful MFA reset:

```python
from app.utils.log_events import log_event

log_event("AUTH", "mfa_reset", {
    "user_email": email,
    "reset_by": "admin_script",
})
```

Verify that `log_event` is importable from a script context. If not, use a direct print
statement to stdout as a fallback audit trail.

**What to test:**
- Test that running without `--force` prompts for confirmation.
- Test that entering "n" aborts without changes.
- Test that `--force` skips the prompt.
- Test that a successful reset logs an audit entry.

**Downstream effects:** None. The MFA reset still works the same way; it's just more
cautious.

**Migration required:** No.

---

### Phase 8: Architecture and DRY (Before Account Architecture Rework)

These refactors should be executed **before** the upcoming account architecture rework and
Section 5 implementation. Section 5 has not yet started, and its implementation plan will be
rewritten to accommodate the new account table architecture. Extracting the business logic
from route files into services (L-06) before that rework is beneficial -- the rework will be
cleaner when it modifies focused service functions rather than 444-line route handlers. L-07
has no dependency and can be done at any point.

---

#### Commit #31

**Commit message:** `refactor(savings): extract dashboard calculations to savings_dashboard_service`

**Finding(s) addressed:** L-06 (savings.py:dashboard, ~444 lines)

**Files to modify:**
- `app/services/savings_dashboard_service.py` (new)
- `app/routes/savings.py`
- `tests/test_services/test_savings_dashboard_service.py` (new)
- `tests/test_routes/test_savings.py`

**What to change:**

Create `app/services/savings_dashboard_service.py`. Extract all financial calculation logic
from `savings.py:dashboard` into service functions. The route should only handle:
1. Authentication and request parsing
2. Calling service functions with plain data
3. Rendering templates with the results

The service should NOT import Flask. It should accept user_id, account lists, and settings
as plain parameters, and return dicts or dataclasses with computed values.

Key functions to extract:
- `compute_account_projections(user_id, accounts, periods)` -- balance projections for each
  account type
- `compute_emergency_fund_metrics(user_id, accounts, transactions)` -- emergency fund
  coverage calculation
- `compute_savings_goal_progress(goals, account_balances)` -- goal progress computations

**What to test:**
- Existing savings dashboard tests should continue to pass.
- New unit tests for each extracted service function with known inputs and expected outputs.

**Downstream effects:** None. The route produces the same output; the logic is just in the
correct architectural layer.

**Migration required:** No.

---

#### Commit #32

**Commit message:** `refactor(retirement): extract gap analysis to retirement_dashboard_service`

**Finding(s) addressed:** L-06 (retirement.py:_compute_gap_data, ~347 lines)

**Files to modify:**
- `app/services/retirement_dashboard_service.py` (new)
- `app/routes/retirement.py`
- `tests/test_services/test_retirement_dashboard_service.py` (new)
- `tests/test_routes/test_retirement.py`

**What to change:**

Same pattern as Commit #31. Extract `_compute_gap_data` into a service module. The route
should call the service and pass results to the template.

**What to test:**
- Existing retirement tests should continue to pass.
- New unit tests for the extracted service functions.

**Downstream effects:** None.

**Migration required:** No.

---

#### Commit #33

**Commit message:** `refactor(templates): deduplicate tax config template markup`

**Finding(s) addressed:** L-07

**Files to modify:**
- `app/templates/salary/_tax_config_shared.html` (new partial)
- `app/templates/salary/tax_config.html`
- `app/templates/settings/_tax_config.html`

**What to change:**

Identify the shared markup between `salary/tax_config.html` and `settings/_tax_config.html`.
Extract the common sections (state config table, FICA form, bracket display) into a shared
partial `salary/_tax_config_shared.html`. Include this partial from both locations using
Jinja's `{% include %}`.

**What to test:**
- Visit `/salary/tax-config` and verify the page renders correctly.
- Visit `/settings` and verify the tax config section renders correctly.
- Verify both pages display the same markup for shared sections.

**Downstream effects:** Future changes to tax config display only need to be made in one
place.

**Migration required:** No.

---

### Phase 9: Testing

Test updates that were deferred from earlier phases to keep commits focused, plus new test
coverage for identified gaps.

---

#### Commit #34

**Commit message:** `test(transactions): update IDOR tests to assert correct behavior`

**Finding(s) addressed:** H-02 test updates (deferred from Commit #5)

**Files to modify:**
- `tests/test_routes/test_transaction_auth.py`
- `tests/test_adversarial/test_hostile_qa.py`

**What to change:**

If Commit #5 did not update the IDOR tests (they may have been updated there), verify and
update any remaining tests that assert the old broken behavior (201 with another user's
scenario_id). All scenario_id IDOR tests should now assert 404.

Also verify that `test_hostile_qa.py` IntegrityError tests (from M-15) assert 400 after
Commit #14.

**What to test:**
- Run the full test suite and verify all tests pass.

**Downstream effects:** None.

**Migration required:** No.

---

#### Commit #35

**Commit message:** `test(concurrent): add race condition tests for critical paths`

**Finding(s) addressed:** L-15

**Files to modify:**
- `tests/test_concurrent/test_race_conditions.py` (new)

**What to change:**

Create targeted concurrency tests for the most critical paths:

1. **Simultaneous mark-done on the same transaction:** Two threads submit POST
   `/transactions/<id>/mark-done` concurrently. Verify no database corruption and exactly one
   status change succeeds.

2. **Carry-forward during transaction edit:** One thread carries forward a period while
   another thread edits a transaction in that period. Verify the carried-forward transaction
   has the correct state.

3. **Simultaneous anchor balance updates:** Two threads submit PATCH
   `/accounts/<id>/true-up` with different balances concurrently. Verify one wins cleanly
   and the final state is consistent.

Use `threading.Thread` and the Flask test client. Each test should:
- Create a test app with a real database session
- Spawn 2 threads that hit the same endpoint
- Assert no IntegrityError, no deadlock, and consistent final state

**What to test:**
- Each race condition scenario above.
- Verify no test hangs (set a 10-second timeout per test).

**Downstream effects:** These tests will guard against future regressions in concurrent
request handling.

**Migration required:** No.

---

## 3. Accepted Risks and Deferrals

### H-04: AccountType CRUD No User Scoping -- Accepted (Single-User)

**Justification:** `AccountType` is a shared `ref` schema table with no `user_id` column. In
the current single-user deployment, there is only one authenticated user, so no exploitation
is possible. The app does not have a role/admin system, so restricting mutation requires
either adding one or hardcoding user_id=1 -- both are scope creep for this remediation.

**Recommendation:** When multi-user support is pursued (roadmap Phase 6, "far future"),
add `user_id` to `ref.account_types` to make them per-user, or implement an admin role
system. Add this to the multi-user prerequisites list.

### M-16: Transaction Lacks Direct user_id -- Accepted (Architectural)

**Justification:** Adding a denormalized `user_id` to `budget.transactions` would require:
an Alembic migration with backfill, updating every transaction creation path (recurrence
engine, credit workflow, transfer service, route handlers), adding a composite index, and
updating all queries. This is a significant architectural change. The current pattern
(scoping through `PayPeriod.user_id`) is correct and enforced at the route layer via
`get_owned_via_parent`. The service layer queries that filter transactions are always called
with user-scoped inputs from routes.

**Recommendation:** Document as a known architectural constraint. Add a guard comment in
`app/models/transaction.py` noting that user ownership is via `pay_period.user_id`. Revisit
if multi-user deployment reveals performance issues with the JOIN-based ownership checks.

### L-02: Rate Limiter In-Memory Storage -- Accepted (Single-User)

**Justification:** With Gunicorn's 2 workers, each worker has its own rate limit counter,
effectively doubling the allowed rate. For single-user deployment with authentication already
required, this is acceptable. Rate limiting primarily protects against brute-force attacks
on login, and 10 attempts per 15 minutes (2x the configured 5) is still a reasonable limit.

**Recommendation:** When multi-user support is pursued, switch to Redis-backed storage
(`storage_uri="redis://..."`) for accurate cross-worker rate limiting.

### L-14: Routes Bypass Centralized auth_helpers -- Gradual Migration

**Justification:** The 80+ inline ownership checks are functionally correct but duplicated.
Migrating them all in one commit touches every route file simultaneously, creating a large
blast radius with no behavioral change. The risk of introducing a subtle regression (e.g.,
a helper returning a different HTTP status than the inline check) outweighs the maintenance
benefit for a solo developer.

**Recommendation:** Adopt the centralized helpers for all NEW route functions going forward.
Migrate existing routes opportunistically when they are modified for other reasons. Over time,
all routes will use the helpers without a risky bulk migration.

### L-06, L-07: Phase 8 Timing

**Context:** Section 5 (Debt and Account Improvements) has not started. Its implementation
plan will be rewritten due to an upcoming account table architecture rework. Extracting the
savings dashboard (L-06a) and retirement gap analysis (L-06b) into service files before that
rework is advantageous: the rework will modify focused service functions rather than 444-line
and 347-line route handlers. The extraction also makes the code easier to test during the
rework.

**Recommendation:** Execute Phase 8 after the audit remediation phases (0-7 and 9) are
complete, but before the account architecture rework begins. This gives the rework a cleaner
starting point.

---

## 4. Dependency Map

```
Phase 0 ──────────────────────────────────────────────────────────────────────>
  │ (no dependencies -- execute first)
  v
Phase 1 ──> Phase 2 ──> Phase 3 ──> Phase 4 ──> Phase 5 ──> Phase 6
  │           │           │           │           │           │
  │           │           │           │           │           v
  │           │           │           │           │         Phase 7
  │           │           │           │           │           │
  │           │           │           │           │           v
  │           │           │           │           │         Phase 9
  │           │           │           │           │           │
  │           │           │           │           │           v
  │           │           │           │           │         Phase 8
  │           │           │           │           │    (before acct rework)
  v           v           v           v           v           v
```

### Strict Dependencies

| Commit | Depends On | Reason |
|--------|-----------|--------|
| #5 (H-02 IDOR fix) | None | Standalone security fix |
| #12 (H-08 tax config schema) | None, but should precede #17 | Both touch `salary.py:update_tax_config` |
| #17 (H-01 enum migration) | #12 (H-08) | H-08 adds the schema; H-01 changes the ID lookup in the same function |
| #18 (H-05 subtotals) | None | But verify template changes are compatible with Section 4's grid work |
| #25 (CHECK constraints) | Commits #10-14 | Schema validation should be in place before DB constraints enforce them, so invalid data is caught at the application layer with clear errors first |
| #26 (ondelete policies) | None | But execute after #25 to avoid migration ordering issues |
| #31-32 (L-06 extraction) | Phases 0-7, 9 complete | Execute before account architecture rework for cleaner starting point |
| #34 (IDOR test updates) | #5 (H-02 fix) | Tests must assert the new correct behavior |
| #35 (concurrent tests) | All Phase 2 commits | Test the secured endpoints |

### Phases Are Sequential

Each phase should be completed before starting the next. Within a phase, commits can be
executed in the listed order. Commits within a phase have no cross-dependencies unless noted
above.

---

## 5. Risk Assessment

### Phase 0: Immediate Production Safety

- **Risk:** LOW. One-line string changes in scripts.
- **Rollback:** `git revert` the single commit.
- **Verification:** Run `seed_user.py` against the production database (read-only mode first
  if concerned). Verify no `NoResultFound` exception.

### Phase 1: Error Handling

- **Risk:** MEDIUM. Narrowing exception handlers means previously-caught exceptions may now
  propagate. If the narrowed catch list misses a legitimate exception type, the app will show
  a 500 error instead of gracefully degrading.
- **Rollback:** `git revert` individual commits. The old broad catch behavior is immediately
  restored.
- **Verification:** After each commit, trigger the affected code paths with both valid and
  invalid data. For C-01, create a salary profile with misconfigured tax data and verify the
  recurrence engine either calculates correctly or fails visibly. For M-10, temporarily break
  the ref_cache init (e.g., by renaming an enum member) and verify the app fails to start
  instead of starting in a degraded state.

### Phase 2: Security

- **Risk:** LOW-MEDIUM. The IDOR fix (H-02) adds a check; the risk is that the check
  is too aggressive and rejects valid requests. The logout POST change (M-12) could break
  user workflows if the template isn't updated simultaneously.
- **Rollback:** `git revert` individual commits.
- **Verification:** Test each fix manually with two user accounts. For H-02, create a
  transaction with a valid scenario_id and verify it works. For M-12, verify the logout button
  in the nav still works.

### Phase 3: Input Validation

- **Risk:** LOW. Adding validation only rejects previously-unvalidated input. Existing valid
  inputs are unaffected.
- **Rollback:** `git revert` individual commits.
- **Verification:** Submit valid data through each form and verify acceptance. Submit the
  edge cases (zero salary, negative raise, etc.) and verify rejection with clear messages.

### Phase 4: Ref Table Strings

- **Risk:** MEDIUM-HIGH. This commit touches 8+ files and changes how deduction calculations
  identify pre-tax vs. post-tax. An incorrect ID mapping would silently miscategorize
  deductions, producing wrong paycheck amounts.
- **Rollback:** `git revert` the commit.
- **Verification:** After the commit, run the full paycheck calculator test suite. Compare
  the salary breakdown page before and after -- all values should be identical. Create a new
  deduction and verify it's correctly categorized as pre-tax or post-tax.

### Phase 5: Financial Correctness

- **Risk:** MEDIUM. Changes to calculation logic could produce different projected values.
  Every change is a bug fix (the new values are more correct), but users may notice their
  projections changed.
- **Rollback:** `git revert` individual commits.
- **Verification:** For each commit, compare before/after values for a representative set of
  data. For M-01 (raise ordering), verify with a known test case that the result is
  deterministic. For H-05 (grid subtotals), verify subtotals match the balance row exactly.

### Phase 6: Database Hardening

- **Risk:** MEDIUM. CHECK constraints on existing data could fail the migration if any rows
  violate the new constraints. ondelete policy changes could affect delete behavior.
- **Rollback:** `flask db downgrade` to reverse the migration.
- **Verification:** Before running the migration, query for any violating rows:
  `SELECT * FROM budget.transactions WHERE estimated_amount < 0;`
  `SELECT * FROM budget.loan_params WHERE term_months <= 0;`
  Fix any violations before migrating.

### Phase 7: Scripts and Deployment

- **Risk:** LOW. These changes affect scripts and config, not core application logic.
- **Rollback:** `git revert` individual commits.
- **Verification:** Restart the application after each change and verify normal operation.

### Phase 8: Architecture and DRY

- **Risk:** MEDIUM. Large refactors (L-06) move substantial code between files. Logic errors
  during extraction could produce subtly wrong results.
- **Rollback:** `git revert` individual commits.
- **Verification:** Run the full test suite after each commit. Compare the savings dashboard
  and retirement dashboard output before and after -- all values should be identical.

### Phase 9: Testing

- **Risk:** NONE. Test-only changes.
- **Rollback:** `git revert` individual commits.
- **Verification:** All tests pass.

---

## 6. Roadmap Cross-Reference

| Finding | Resolution | Roadmap Reference |
|---------|------------|-------------------|
| C-01 | Commit #2 | -- |
| H-01 | Commit #17 (remainder); Status/TxnType/AcctType/RecurrencePattern fixed in Section 4 (tasks 4.4a-4.4c) | Section 4, tasks 4.4a-4.4c (complete for statuses/types; this plan covers the remainder) |
| H-02 | Commit #5 | -- |
| H-03 | Commit #6 | -- |
| H-04 | Accepted risk (single-user) | Multi-user (roadmap Priority 6, far future) |
| H-05 | Commit #18 | Section 4 grid work is complete; this fix is standalone |
| H-06 | Commit #10 | -- |
| H-07 | Commit #11 | -- |
| H-08 | Commit #12 | -- |
| H-09 | Commit #13 | -- |
| H-10 | Commit #1 | -- |
| H-11 | Commit #3 | -- |
| M-01 | Commit #19 | -- |
| M-02 | Commit #19 | -- |
| M-03 | Commit #7 | -- |
| M-04 | Commit #20 | -- |
| M-05 | Commit #21 | -- |
| M-06 | Commit #22 | -- |
| M-07 | Commit #25 | Section 5 debt improvements; CHECK constraints are independent and do not conflict |
| M-08 | Commit #26 | -- |
| M-09 | Commit #23 | -- |
| M-10 | Commit #4 | -- |
| M-11 | Commit #15 | Section 4 grid work is complete; this fix is standalone |
| M-12 | Commit #8 | -- |
| M-13 | Commit #16 | Section 4, task 4.4c (covered RecurrencePattern but missed this template) |
| M-14 | Commit #10 | -- |
| M-15 | Commit #14 | -- |
| M-16 | Accepted (architectural) | Multi-user (roadmap Priority 6, far future) |
| M-17 | Commit #9 | -- |
| L-01 | Commit #25 | -- |
| L-02 | Accepted risk (single-user) | Multi-user (roadmap Priority 6, far future) |
| L-03 | Commit #26 | -- |
| L-04 | Commit #24 (documented) | -- |
| L-05 | Commit #24 | -- |
| L-06 | Commits #31-32 | Phase 8; execute before account architecture rework |
| L-07 | Commit #33 | Phase 8; no dependency on account rework |
| L-08 | Commit #14 | -- |
| L-09 | Commit #27 | -- |
| L-10 | Commit #28 | -- |
| L-11 | Commit #1 | -- |
| L-12 | Commit #29 | -- |
| L-13 | Commit #30 | -- |
| L-14 | Gradual migration | Ongoing; adopt for new code, migrate existing opportunistically |
| L-15 | Commit #35 | -- |

---

## 7. Summary Statistics

- **Total planned commits:** 35
- **Findings addressed directly:** 40 (across 35 commits)
- **Findings deferred:** 1 (L-14 -- gradual migration)
- **Findings in Phase 8 (before architecture rework):** 3 (L-06a, L-06b, L-07)
- **Findings accepted as-is:** 3 (H-04, M-16, L-02)
- **Findings partially already fixed:** 1 (H-01 -- Status, TxnType, AcctType, RecurrencePattern portions fixed in Section 4; DeductionTiming, CalcMethod, TaxType addressed in Commit #17)

### Divergences from Audit Suggestions

| Finding | Audit Suggestion | Plan Recommendation | Reason |
|---------|-----------------|---------------------|--------|
| C-01 | Propagate exception or flag transactions | Narrow catch to specific exceptions; let unexpected errors propagate | Flagging transactions as unreliable adds UI complexity. Narrowing the catch is simpler and surfaces the real error class. The fallback to `template.default_amount` is preserved only for the three known recoverable exception types. |
| H-04 | Add user_id, restrict to admin, or document | Accept for single-user | No admin role system exists. Adding one is scope creep. Single-user deployment has no exploit path. |
| M-16 | Add denormalized user_id or document | Document as architectural constraint | The change touches too many files for the benefit. Current ownership pattern via PayPeriod is correct and tested. |
| L-04 | Document or check for leap years | Document the convention with a comment | The actual/365 convention is standard US banking practice. The ~$1.23/$100K annual error is acceptable for projection purposes. |
| L-14 | Gradually migrate to centralized helpers | Adopt for new code; migrate opportunistically | Bulk migration of 80+ patterns risks regressions with no behavioral change. Gradual migration is safer for a solo developer. |
