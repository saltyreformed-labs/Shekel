> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Section 3 -- Critical Bug Fixes

**Date:** 2026-03-24
**Source:** `docs/project_roadmap_v4.md` Section 3
**Scope:** Bugs 3.1 through 3.9 and Feature 3.10 (Paycheck Calibration)

---

## SECTION A: Bug Verification and Root Cause Analysis

---

### A -- 3.1: Tax Calculation on Gross Pay Instead of Taxable Income

**A1. Still present?** No.

The bug has already been fixed. In `app/services/paycheck_calculator.py`, the `calculate_paycheck()` function correctly handles pre-tax deductions before computing taxes:

- **Lines 94-97:** Pre-tax deductions are calculated and summed into `total_pre_tax`.
- **Lines 100-102:** `taxable_biweekly = gross_biweekly - total_pre_tax`, floored at zero.
- **Lines 106:** `annual_pre_tax = total_pre_tax * pay_periods_per_year` -- annualized for the federal withholding function.
- **Lines 115-125:** `calculate_federal_withholding()` receives `pre_tax_deductions=annual_pre_tax` as a named argument. Inside `tax_calculator.py` lines 112-113, this is subtracted from annual income: `adjusted_income = annual_income - pre_tax_deductions - additional_deductions`.
- **Lines 129-134:** State tax is computed on `taxable_biweekly * pay_periods_per_year` (i.e., gross minus pre-tax deductions), not on gross.
- **Lines 140-142:** FICA is correctly computed on `gross_biweekly` (not taxable), because FICA is assessed on gross wages per IRS rules.
- **Lines 151-159:** Net pay = gross - pre_tax - federal - state - SS - medicare - post_tax.

The pipeline order is:
`annual salary -> raises -> gross biweekly -> pre-tax deductions -> taxable biweekly -> federal tax (on taxable) -> state tax (on taxable) -> FICA (on gross) -> post-tax deductions -> net pay`

This matches the correct pipeline specified in the requirements.

**Evidence:** The test file `tests/test_services/test_paycheck_calculator.py` contains explicit regression tests:
- `test_flat_pretax_deduction_reduces_federal_and_state` (line 1710) -- verifies that a $200/paycheck 401(k) deduction reduces federal tax by exactly $20.00 (= $200 * 10% marginal rate) and state tax by $9.00 (= $200 * 4.5% flat rate). The test docstring explicitly states: "This test catches the section 3.1 bug if it were to exist."
- `test_pretax_deduction_does_not_reduce_fica` (line 1796) -- verifies FICA is still computed on gross, not taxable income.
- Additional tests: `test_percentage_pretax_deduction_reduces_taxes` (line 1858), `test_third_paycheck_skipped_deduction_increases_taxes` (line 1935), `test_multiple_pretax_deductions_stack` (line 2039), `test_post_tax_deduction_does_not_affect_any_tax` (line 2117), `test_mixed_pre_and_post_tax_deductions` (line 2186), `test_state_tax_with_standard_deduction_and_pretax` (line 2243), `test_net_pay_end_to_end_with_pretax` (line 2317), `test_pretax_deduction_in_higher_bracket_larger_reduction` (line 2374).

**No further action required for 3.1.** The bug has been comprehensively fixed and tested.

---

### A -- 3.2: Recurrence Rule: Every 6 Months Calculates Incorrectly

**A1. Still present?** No.

The semi-annual recurrence pattern is correctly implemented using calendar months, not pay-period counting. In `app/services/recurrence_engine.py`:

- **Lines 400-406:** `_match_semi_annual()` computes target months as `set(((start_month - 1 + i * 6) % 12) + 1 for i in range(2))`. For `start_month=1`, this produces `{1, 7}` (January and July). For `start_month=6`, this produces `{6, 12}` (June and December). The modular arithmetic correctly wraps around December.
- **Lines 409-430:** `_match_specific_months()` does calendar-month matching: for each period, it checks whether the period's date range contains the target day within a target month. This is the same approach used by `_match_monthly()` and `_match_quarterly()`.

The confusion described in the roadmap ("appears to calculate 6 pay periods (~3 months) instead of 6 calendar months") would only apply if semi-annual used `every_n_periods` with `n=6`, but the code uses a dedicated `_match_semi_annual()` function that works on calendar months.

**Evidence:** The test file `tests/test_services/test_recurrence_engine.py` has:
- `TestMatchSemiAnnual.test_semi_annual_jan_start` (line 384) -- verifies start_month=1 targets Jan and Jul, producing exactly 2 matches.
- `TestMatchSemiAnnual.test_semi_annual_aug_start_wraps` (line 399) -- verifies start_month=8 wraps correctly to target Aug and Feb.

**No further action required for 3.2.** The implementation is correct and tested.

---

### A -- 3.3: Net Biweekly Mismatch Between Salary Profile and Grid

**A1. Still present?** Likely yes, but it is a stale-data issue, not a calculation divergence.

**A2. Root cause.** Both the salary profile page and the grid ultimately use the same `paycheck_calculator.calculate_paycheck()` function, so the calculation logic is identical. However, the numbers can diverge because the grid displays **stored** `Transaction.estimated_amount` values, while the salary profile page computes net pay **live** on each page load.

The divergence occurs through this mechanism:

1. **Salary profile page** (`app/routes/salary.py` lines 76-88): On each page load, `list_profiles()` calls `paycheck_calculator.calculate_paycheck(profile, current_period, periods, tax_configs)` with the **current** salary profile data and **current** tax configs. The result is computed fresh and displayed.

2. **Grid** (`app/routes/grid.py` lines 81-91): The grid loads `Transaction` objects from the database. The `estimated_amount` on each income transaction was set by the recurrence engine at the time the transaction was last generated or regenerated.

3. **Recurrence engine** (`app/services/recurrence_engine.py` lines 496-518): `_get_transaction_amount()` calls `paycheck_calculator.calculate_paycheck()` to compute the net pay for salary-linked templates. However, this only runs when transactions are **generated or regenerated**. If the user changes tax configs, deductions, or other profile data AND the regeneration fails or doesn't reach certain periods, stale amounts persist.

4. **Tax year mismatch**: The salary route calls `load_tax_configs(user_id, profile)` with no `tax_year` argument (defaults to `date.today().year` per `tax_config_service.py` line 37). The recurrence engine calls `load_tax_configs(user_id, profile, tax_year=period.start_date.year)` (line 512), which uses the **period's** year. For periods in 2027 (future-year projections), if no 2027 tax configs exist, `bracket_set` and `fica_config` return `None`, causing the recurrence engine to fall back to `template.default_amount` (line 526 -- the `except Exception` catch-all). Meanwhile, the salary profile page always uses the current year's tax configs regardless of which period it's calculating.

5. **Regeneration scope**: `_regenerate_salary_transactions()` in the salary route (lines 688-722) regenerates with `effective_from=date.today()`, meaning it only touches transactions from today forward. Past-period transactions retain their old amounts.

**A3. Blast radius.** The net biweekly amount displayed on the salary profile list page (`app/templates/salary/list.html` line 50) does not match the income transaction amounts visible in the grid for the same pay period. This creates distrust -- the user sees one number on the salary page and a different number in the grid. The balance projections in the grid are based on the stored transaction amounts, so if those are stale, **all balance projections downstream are also wrong**.

**A4. Related issues discovered.**
- The `template.default_amount` is updated to net pay of the current period during regeneration (salary route line 710), but this is the **gross** biweekly divided by pay_periods in the creation path (salary route line 188: `default_amount=data["annual_salary"] / pay_periods`). There is a correction at lines 224-229 that updates it to net pay after initial generation, but if that initial calculation fails, the template retains the gross-based default.
- The `except Exception` block in `_get_transaction_amount()` (recurrence_engine.py line 520-526) silently falls back to `template.default_amount` on ANY error, which could mask misconfiguration bugs.

---

### A -- 3.4: Raises Require Page Refresh Before Adding a Second

**A1. Still present?** Yes.

**A2. Root cause.** In `app/templates/salary/form.html` line 159, the raises section is wrapped in:
```html
<div id="raises-section">
  {% include "salary/_raises_section.html" %}
</div>
```

The `_raises_section.html` partial (lines 7-133) renders a `<div class="card mb-3">` as its outermost element. This div does **not** have `id="raises-section"`.

The HTMX attributes on the add-raise form (`_raises_section.html` lines 77-80) are:
```html
hx-post="{{ url_for('salary.add_raise', profile_id=profile.id) }}"
hx-target="#raises-section"
hx-swap="outerHTML"
```

On the first add:
1. HTMX posts the form data.
2. The server returns the `_raises_section.html` partial (via `_render_raises_partial` in salary.py line 747).
3. HTMX finds `#raises-section` (the wrapper div from form.html) and swaps it with `outerHTML`.
4. The wrapper `<div id="raises-section">` is **replaced** by the partial's content, which is `<div class="card mb-3">` -- **without** `id="raises-section"`.
5. On the second add, HTMX looks for `#raises-section` and **cannot find it** because the id was lost in the first swap. The HTMX request fires but has nowhere to swap the response, so nothing visible happens.

The delete-raise form has the same `hx-target="#raises-section"` and `hx-swap="outerHTML"` (line 55-56), so it has the same bug -- after the first HTMX operation (add or delete), subsequent operations fail.

Additionally, the Bootstrap collapse state is lost after the swap. The "Add Raise" button uses `data-bs-toggle="collapse"` targeting `#add-raise-form`. After the HTMX swap replaces the DOM, Bootstrap's collapse instance is no longer bound, and the form may not be toggleable.

**A3. Blast radius.** Only the salary profile edit page is affected. The user must manually refresh the page between raise operations. No data corruption occurs -- the raise is saved correctly to the database.

**A4. Related issues discovered.** The deductions section has the **opposite** pattern and works correctly: `_deductions_section.html` line 1 includes `id="deductions-section"` on its outermost div, AND `form.html` line 164 wraps it in `<div id="deductions-section">`. This means after HTMX swap, the id is preserved because the partial's root element carries it. However, this creates a **double-id** situation on initial page load (both the wrapper and the partial have `id="deductions-section"`). While browsers typically use the first occurrence, this is technically invalid HTML. The deductions section has a different toggle mechanism (`data-toggle-target` instead of `data-bs-toggle="collapse"`) which may have its own issues.

---

### A -- 3.5: Cannot Edit Raises and Deductions

**A1. Still present?** Yes.

**A2. Root cause.** There are no edit routes, forms, or UI elements for raises or deductions. The salary route (`app/routes/salary.py`) defines only:
- `add_raise` (line 340) and `delete_raise` (line 383) for raises.
- `add_deduction` (line 418) and `delete_deduction` (line 463) for deductions.

There is no `update_raise` or `update_deduction` route. The `_raises_section.html` template shows each raise in a table row with only a delete button (line 52-63). There is no edit icon, no inline edit form, and no link to an edit view. The same is true for `_deductions_section.html` (line 52-65).

The validation schemas (`app/schemas/validation.py`) define `RaiseCreateSchema` and `DeductionCreateSchema` but no corresponding update schemas for these entities.

**A3. Blast radius.** The user must delete and re-create a raise or deduction to correct any mistake. This is especially painful for deductions, which have many fields (name, timing, method, amount, per-year, cap, inflation settings, target account). Deleting and re-creating also triggers a full salary transaction regeneration each time (once for delete, once for re-add), which is slow.

**A4. Related issues discovered.** None.

---

### A -- 3.6: Escrow: Cannot Add Amount When Inflation Rate Is Present

**A1. Still present?** Yes.

**A2. Root cause.** In `app/schemas/validation.py` line 571:
```python
inflation_rate = fields.Decimal(places=4, as_string=True, allow_none=True, validate=validate.Range(min=0, max=1))
```

The schema validates `inflation_rate` with `max=1`, meaning the value must be between 0 and 1 (a decimal rate like 0.03).

However, the UI form in `app/templates/mortgage/_escrow_list.html` lines 66-70 presents the field as a **percentage** input:
```html
<input type="number" ... name="inflation_rate" ... placeholder="3">
<span class="input-group-text">%</span>
```

The user enters "3" for a 3% inflation rate. The schema receives "3", validates it against `Range(min=0, max=1)`, and **rejects it** because 3 > 1. The route's conversion logic at `app/routes/mortgage.py` lines 270-272 divides by 100 AFTER schema validation, but execution never reaches that code because validation fails first.

The flow:
1. User enters "3" in the inflation rate field.
2. Schema validates: `3 > 1` -- FAIL.
3. Route returns HTTP 400 with "Please correct the highlighted errors and try again."
4. The user sees an error but has no indication that the input format is wrong.

Entering "0.03" directly would pass validation, but the form's placeholder says "3" and has a "%" suffix, strongly implying percentage input.

**A3. Blast radius.** Any escrow component with a non-zero inflation rate cannot be created through the UI. The escrow calculator (`app/services/escrow_calculator.py`) and its inflation logic are never exercised in production because the data cannot be saved. The total monthly payment displayed on the mortgage dashboard does not include inflation-adjusted escrow.

**A4. Related issues discovered.** The existing test `test_escrow_add` (line 292 in `tests/test_routes/test_mortgage.py`) passes `inflation_rate: "0.03"` -- a pre-converted decimal value, not the "3" that the UI form would send. This means the test does not catch the bug because it bypasses the user's actual input path.

---

### A -- 3.7: Escrow: Hard Refresh Required After Adding Component

**A1. Still present?** Yes.

**A2. Root cause.** When an escrow component is added via HTMX, the route `add_escrow()` (`app/routes/mortgage.py` lines 255-299) returns only the `_escrow_list.html` partial. The HTMX target is `#escrow-list` with `hx-swap="innerHTML"` (`_escrow_list.html` line 49).

The problem is that the **payment summary** section on the Overview tab (`mortgage/dashboard.html` lines 87-94) displays `total_payment`, which is computed server-side during the full page render (`mortgage.py` lines 103-106):
```python
total_payment = escrow_calculator.calculate_total_payment(
    summary.monthly_payment, escrow_components,
)
```

When a new escrow component is added via HTMX, only the `#escrow-list` div is updated. The Overview tab's "Total Monthly (with escrow)" line retains the value from the original page load. The monthly escrow badge in the Escrow tab header (`dashboard.html` line 174) also retains its stale value because it's outside the `#escrow-list` div.

The `add_escrow` route does not return any trigger or signal to update the payment summary or the escrow badge. The response only replaces the escrow list content.

**A3. Blast radius.** After adding or deleting an escrow component, the user sees the updated list of components but the total monthly payment on the Overview tab and the monthly escrow badge remain stale. The user thinks the add didn't affect the total and may try to re-add or may distrust the application's calculations.

**A4. Related issues discovered.** The `delete_escrow` route (lines 302-331) has the same issue -- it returns only the `_escrow_list.html` partial without updating the payment summary. Both add and delete need the same fix.

---

### A -- 3.8: Pension Date Validation Missing

**A1. Still present?** Yes.

**A2. Root cause.** In `app/schemas/validation.py`:

`PensionProfileCreateSchema` (lines 689-710) defines:
```python
hire_date = fields.Date(required=True)
earliest_retirement_date = fields.Date(allow_none=True)
planned_retirement_date = fields.Date(allow_none=True)
```

`PensionProfileUpdateSchema` (lines 712-730) defines:
```python
hire_date = fields.Date()
earliest_retirement_date = fields.Date(allow_none=True)
planned_retirement_date = fields.Date(allow_none=True)
```

Neither schema has any cross-field validation. There are no `@validates_schema` or `@post_load` methods that check:
- `earliest_retirement_date > hire_date`
- `planned_retirement_date > hire_date`
- `planned_retirement_date >= earliest_retirement_date` (logical, though not strictly required)

The route layer (`app/routes/retirement.py` lines 376-397 for create, lines 422-454 for update) performs no additional date validation beyond what the schema provides.

The model layer (`app/models/pension_profile.py`) has no check constraints on date ordering.

This allows the user to create pension profiles where:
- The hire date is in the future but retirement dates are in the past.
- The planned retirement date is before the hire date.
- The earliest retirement date is after the planned retirement date.

**A3. Blast radius.** The pension calculator (`app/services/pension_calculator.py` line 118) computes `delta_days = (retirement_date - hire_date).days`. If retirement is before hire, `delta_days` is negative, and `_calculate_years_of_service` returns `ZERO` (line 120: `if delta_days < 0: return ZERO`). This means the annual benefit would be zero, which is harmless but confusing. However, for the retirement gap calculator, a zero pension benefit could cause the income gap to appear larger than it actually is, leading to misleading retirement planning decisions.

The roadmap also mentions validating that dates are "after today's date." This is debatable for `hire_date` (a past date is the normal case) and `earliest_retirement_date` (could legitimately be in the past for someone already eligible). The `planned_retirement_date` being in the past would be nonsensical and should be rejected.

**A4. Related issues discovered.** None.

---

### A -- 3.9: Stale Retirement Settings Message

**A1. Still present?** Yes.

**A2. Root cause.** In `app/templates/retirement/dashboard.html` lines 182-186:
```html
<div class="text-muted mb-4">
  <i class="bi bi-info-circle"></i>
  Retirement settings (withdrawal rate, retirement date, tax rate) have moved to
  <a href="{{ url_for('settings.show', section='retirement') }}">Settings &gt; Retirement</a>.
</div>
```

This message was presumably added when retirement settings were moved from the retirement dashboard to the Settings page. Now that users are accustomed to the new location (or never knew the old one), this migration notice is confusing clutter. It implies a recent change when in fact the settings have always been on the Settings page for anyone using the current version.

**A3. Blast radius.** Cosmetic only. No data or calculation impact. The message takes up vertical space and adds cognitive load without providing useful information.

**A4. Related issues discovered.** None.

---

### A -- 3.10: Paycheck Calibration (New Feature)

**A1. Still present?** N/A -- this is a new feature, not a bug.

**A2. Feature analysis.** The paycheck calculator currently uses bracket-based federal/state tax estimates and user-configured deduction amounts. These may not match actual paycheck withholding due to:
- Employer-specific withholding algorithms that differ slightly from Pub 15-T estimates.
- State-specific withholding tables not fully modeled (NC flat rate is modeled; bracket-based states are not).
- Employer matching, benefits deductions, or other payroll specifics not captured.

The calibration feature allows the user to enter actual line-item values from a real pay stub and derive effective rates that override the bracket-based calculations.

**Current data model:** `SalaryProfile` (`app/models/salary_profile.py`) has no columns for calibration overrides.

**Current paycheck calculator:** `calculate_paycheck()` in `app/services/paycheck_calculator.py` always computes taxes from brackets/rates. There is no bypass path for override rates.

**A3. Dependencies.** Bug 3.1 (tax calculation) must be fixed first. Since 3.1 is already fixed, this dependency is satisfied. The calibration feature builds on a correct tax calculation foundation so that the override rates are computed against correct baselines.

**A4. Related issues discovered.** None.

---

## SECTION B: Proposed Solution for Each Bug

---

### B -- 3.1: Tax Calculation on Gross Pay Instead of Taxable Income

**B1. Solution description.** No changes required. The bug has been fixed and thoroughly tested.

---

### B -- 3.2: Recurrence Rule: Every 6 Months Calculates Incorrectly

**B1. Solution description.** No changes required. The implementation is correct and tested.

---

### B -- 3.3: Net Biweekly Mismatch Between Salary Profile and Grid

**B1. Solution description.** The root cause is that grid income transactions store a `estimated_amount` that was computed at generation/regeneration time, while the salary page computes net pay live. The amounts diverge when:
- Tax configs change without full regeneration.
- Future-year periods lack tax configs, causing fallback to `template.default_amount`.
- The regeneration scope (`effective_from=date.today()`) leaves past-period transactions stale.

The fix has two parts:

**Part 1: Ensure regeneration covers all future periods consistently.** In `app/routes/salary.py`, the `_regenerate_salary_transactions()` function (line 688) calls `regenerate_for_template()` with `effective_from=date.today()`. This is correct for most operations. However, when tax configs change (in `_regenerate_all_salary_transactions()` called from `update_tax_config` and `update_fica_config`), the regeneration should also use the current period's start date as the effective_from, not `date.today()` (which might be mid-period). This is a minor adjustment.

**Part 2: Handle missing future-year tax configs gracefully.** In `app/services/recurrence_engine.py` `_get_transaction_amount()` (lines 496-526), when `load_tax_configs()` returns `None` for a future year's bracket_set, the paycheck calculator still works -- it just produces zero federal tax (paycheck_calculator.py line 127: `federal_biweekly = ZERO` when `bracket_set` is None). The resulting net pay would be higher than expected (no federal tax deducted), not a fallback to `template.default_amount`.

The `except Exception` catch-all (line 520) only triggers on actual errors, not on missing configs. So the real divergence source is likely that the grid shows **previously-generated** transactions from before a tax config or deduction change, while the salary page shows the **current** calculation.

The correct fix is to ensure that whenever the salary profile, tax config, or FICA config changes, **all** salary-linked transactions are regenerated for all periods from the current period forward. The current code already does this, but there's a subtle issue: the recurrence engine's `regenerate_for_template()` only deletes and recreates non-overridden, non-deleted, non-immutable transactions. If a user has manually edited an income transaction (making it an override), that transaction retains its old amount.

**Proposed approach:**
1. In `_regenerate_salary_transactions()`, after regeneration, also update `template.default_amount` to the current period's net pay (already done at lines 705-710).
2. Add a visible indicator on the salary profile list page when the displayed net pay differs from what the grid shows. This is a UI-level diagnostic, not a fix to the underlying data flow.
3. Document the expected behavior: the grid shows amounts computed at generation time; the salary page shows live calculations. When configs change, regeneration updates future transactions but cannot change historical (done/received) ones.

However, the most impactful fix is ensuring that the tax_year used in the recurrence engine matches the salary page's behavior for periods within the current tax year. For future years where tax configs don't exist yet, the recurrence engine should fall back to the **current year's** tax configs rather than producing zero-tax paychecks. This is the most common source of mismatch.

**Specific change in `app/services/recurrence_engine.py` `_get_transaction_amount()`:**
- After calling `load_tax_configs(user_id, profile, tax_year=period_year)`, check if the returned configs are all None.
- If so, fall back to `load_tax_configs(user_id, profile)` (current year default).
- This matches the salary page's behavior and eliminates the divergence for future-year periods.

**Files modified:**
- `app/services/recurrence_engine.py` -- Modified: add fallback tax config loading for future-year periods.

**B3. Edge cases.**
- Period in a year where no tax configs exist (e.g., 2028): should fall back to current year configs.
- Profile with no tax configs at all: `calculate_paycheck()` handles None configs gracefully (zero federal/state/FICA), so net pay = gross - deductions.
- Override transactions in the grid: these should NOT be regenerated (the recurrence engine already skips them). The user intentionally set a different amount.
- Third paycheck periods: the recurrence engine already computes these correctly via `calculate_paycheck()`.

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/services/recurrence_engine.py` | Modified | Add fallback to current-year tax configs when future-year configs are missing |
| `tests/test_services/test_recurrence_engine.py` | Modified | Add test for future-year tax config fallback |

---

### B -- 3.4: Raises Require Page Refresh Before Adding a Second

**B1. Solution description.** The `_raises_section.html` partial must include `id="raises-section"` on its outermost element so that after HTMX replaces the content, subsequent HTMX operations can still find the target.

**Current state:** `_raises_section.html` line 7: `<div class="card mb-3">`
**After fix:** `_raises_section.html` line 7: `<div class="card mb-3" id="raises-section">`

Additionally, remove the redundant wrapper div from `form.html`. Currently, `form.html` line 159-161:
```html
<div id="raises-section">
  {% include "salary/_raises_section.html" %}
</div>
```
Should become:
```html
{% include "salary/_raises_section.html" %}
```
This avoids the double-id issue on initial render. After the first HTMX swap, the partial replaces itself (via `outerHTML`) and retains the id.

**Bootstrap collapse re-initialization:** After HTMX swaps the raises section, the Bootstrap collapse for `#add-raise-form` needs to be re-initialized. Add an `hx-on::after-settle` attribute or use HTMX's `htmx:afterSettle` event to reinitialize Bootstrap's JavaScript on the new DOM. Alternatively, use a simpler toggle mechanism that doesn't depend on Bootstrap's JS state (e.g., a CSS class toggle via HTMX or Alpine.js, or the `data-toggle-target` pattern already used in the deductions section).

The simplest approach: replace `data-bs-toggle="collapse" data-bs-target="#add-raise-form"` with the same `data-toggle-target="add-raise-form"` pattern used in the deductions section (assuming there's a JS handler for this -- if the deductions section's toggle works, the same mechanism should be applied to raises).

**B3. Edge cases.**
- If JavaScript is disabled: the form includes `method="POST"` and `action=...` attributes as fallback, so a standard form submission will work and redirect.
- Double-click on submit: the `hx-disabled-elt="find button[type=submit]"` attribute disables the button during the request, preventing double submission.
- HTMX request failure (network error): HTMX will show the error in the console. The button re-enables after the timeout. The user can retry.
- First raise added when list is empty: the partial handles this (shows table or "No raises configured" message).

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/templates/salary/_raises_section.html` | Modified | Add `id="raises-section"` to outermost div; fix toggle mechanism |
| `app/templates/salary/form.html` | Modified | Remove redundant wrapper div around raises include |
| `tests/test_routes/test_salary.py` | Modified | Add test verifying HTMX raises partial includes the id attribute |

---

### B -- 3.5: Cannot Edit Raises and Deductions

**B1. Solution description.** Add edit functionality for raises and deductions, following the established CRUD patterns in the codebase.

**Raises -- Edit:**

1. **Route:** Add `update_raise(raise_id)` to `app/routes/salary.py`. Pattern: look up the `SalaryRaise` by id, verify ownership via `salary_raise.salary_profile.user_id`, validate with `_raise_schema`, update fields, call `_regenerate_salary_transactions()`, return the raises partial for HTMX.

2. **Schema:** The existing `RaiseCreateSchema` can be reused for updates since all the same fields apply. If partial updates are needed, create a `RaiseUpdateSchema` with all fields optional.

3. **Template:** In `_raises_section.html`, add an edit button next to each raise's delete button. Clicking edit should either:
   - **Option A (inline edit):** Replace the table row with an inline form pre-populated with the raise's current values. On submit, POST to the update route. This matches the grid's inline edit pattern.
   - **Option B (populate the add form):** Scroll to / expand the add form, populate it with the raise's values, and change the form's action to the update endpoint. On submit, the form updates instead of creates. A hidden `raise_id` field distinguishes create vs update.

   **Recommended: Option B** -- it reuses the existing form and avoids the complexity of inline row editing in a table.

4. **Implementation detail for Option B:** When the user clicks "Edit" on a raise:
   - A small JS handler reads the raise data from `data-*` attributes on the row.
   - Expands the collapse form if collapsed.
   - Populates the form fields with the raise's values.
   - Changes the form's `action` and `hx-post` to `/salary/raises/<raise_id>/edit`.
   - Changes the submit button text from "Add" to "Update".
   - Adds a hidden input `<input type="hidden" name="raise_id" value="...">`.
   - On submit (or cancel), resets the form back to "Add" mode.

5. **Route for update:**
   ```
   @salary_bp.route("/salary/raises/<int:raise_id>/edit", methods=["POST"])
   ```
   Same pattern as `add_raise` but updates an existing record instead of creating a new one.

**Deductions -- Edit:**

Same pattern as raises. Add an `update_deduction(ded_id)` route and edit UI to the deductions section.

**B3. Edge cases.**
- Editing a raise that was just deleted by another tab/session: the route should return 404 or flash an error.
- Percentage vs flat amount: the form should correctly populate the percentage field (converting from stored decimal 0.03 back to display 3%) or the flat amount field based on what was originally entered. The `raise_type` or presence of `percentage` vs `flat_amount` determines which field to populate.
- Deduction calc_method: editing a deduction should correctly select the method dropdown ("flat" or "percentage") and display the amount accordingly. A percentage deduction stores `0.06` but should display as "6" in the form.
- Editing triggers regeneration: updating a raise or deduction amount changes all future paycheck amounts, requiring a full regeneration.

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/routes/salary.py` | Modified | Add `update_raise` and `update_deduction` routes |
| `app/templates/salary/_raises_section.html` | Modified | Add edit button per row; add JS/data attributes for form population |
| `app/templates/salary/_deductions_section.html` | Modified | Add edit button per row; add JS/data attributes for form population |
| `app/static/js/salary_edit.js` (or inline) | New | JS handler for edit button (populate form, switch mode) |
| `tests/test_routes/test_salary.py` | Modified | Add tests for update_raise and update_deduction routes |

---

### B -- 3.6: Escrow: Cannot Add Amount When Inflation Rate Is Present

**B1. Solution description.** Change the `EscrowComponentSchema` validation range for `inflation_rate` to accept percentage input (0-100) instead of decimal (0-1).

In `app/schemas/validation.py` line 571, change:
```python
inflation_rate = fields.Decimal(places=4, as_string=True, allow_none=True, validate=validate.Range(min=0, max=1))
```
to:
```python
inflation_rate = fields.Decimal(places=4, as_string=True, allow_none=True, validate=validate.Range(min=0, max=100))
```

The route already converts the percentage to decimal after validation (`mortgage.py` lines 270-272: `data["inflation_rate"] = D(str(data["inflation_rate"])) / D("100")`), so no route changes are needed.

**B3. Edge cases.**
- User enters "0" for inflation rate: 0 / 100 = 0.0, which is stored as zero. The escrow calculator handles zero inflation correctly (no inflation applied).
- User enters "100" for inflation rate: 100 / 100 = 1.0 (100% annual inflation). While extreme, it's a valid input. The schema range allows it.
- User enters "" (empty): The `strip_empty_strings` pre_load hook removes it, and `allow_none=True` lets it pass as None. The route's `if data.get("inflation_rate") is not None` check skips the conversion. Correct behavior.
- User enters a negative rate: `Range(min=0)` rejects it. Correct.

**Also fix the test:** Update `test_escrow_add` in `tests/test_routes/test_mortgage.py` to send `inflation_rate: "3"` (percentage input matching the UI) instead of `"0.03"`, and verify the stored value is `Decimal("0.03")`.

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/schemas/validation.py` | Modified | Change `inflation_rate` range from max=1 to max=100 |
| `tests/test_routes/test_mortgage.py` | Modified | Fix test to send percentage input; add test for percentage conversion |

---

### B -- 3.7: Escrow: Hard Refresh Required After Adding Component

**B1. Solution description.** After adding or deleting an escrow component, the HTMX response needs to trigger an update of the payment summary on the Overview tab and the escrow badge.

**Approach: Use HTMX out-of-band (OOB) swaps.**

1. Create a new template partial `app/templates/mortgage/_payment_summary.html` that renders the "Total Monthly (with escrow)" line and the monthly escrow badge. Wrap the existing elements in the dashboard with a targetable id (e.g., `id="payment-summary"`).

2. In the `add_escrow` and `delete_escrow` routes, after computing the updated escrow components, also compute the updated `total_payment` and `monthly_escrow`. Include an OOB swap fragment in the response that updates `#payment-summary`.

3. The response would include both the `_escrow_list.html` content (for the `#escrow-list` innerHTML swap) and an OOB fragment:
   ```html
   <div id="payment-summary" hx-swap-oob="true">
     ... updated total monthly payment ...
   </div>
   ```

**Alternative simpler approach:** Add an `HX-Trigger` response header that causes the Overview tab's payment summary to re-fetch itself. However, this requires the payment summary to have its own HTMX endpoint, which is more infrastructure.

**Recommended approach:** OOB swap. It's a single response that updates both areas.

**Implementation detail:**

In `mortgage/dashboard.html`, wrap the payment summary section (lines 87-94) and the escrow badge (line 174) with targetable ids:
- `<span id="total-payment-display">...</span>` around the total monthly amount.
- `<span id="escrow-badge">...</span>` around the badge in the Escrow tab header.

In the `add_escrow` and `delete_escrow` routes, compute the updated values and pass them to the template. The `_escrow_list.html` template would include OOB update fragments at the bottom.

**B3. Edge cases.**
- If the user is on the Overview tab when adding an escrow component via the Escrow tab: the Overview tab's DOM is present but not visible. OOB swaps update the DOM regardless of tab visibility, so when the user switches back to Overview, the updated values are there.
- If the mortgage has no P&I payment (params is None): the total payment section may not be rendered. The OOB target would not exist, and HTMX silently ignores OOB swaps for missing targets. No error.
- Network error during add: the escrow list is not updated, and neither is the summary. Consistent state.

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/routes/mortgage.py` | Modified | Compute updated payment summary in add_escrow/delete_escrow; pass to template |
| `app/templates/mortgage/_escrow_list.html` | Modified | Add OOB fragments for payment summary and badge |
| `app/templates/mortgage/dashboard.html` | Modified | Add ids to payment summary and badge elements |
| `tests/test_routes/test_mortgage.py` | Modified | Add test verifying OOB content in escrow add/delete responses |

---

### B -- 3.8: Pension Date Validation Missing

**B1. Solution description.** Add cross-field date validation to both `PensionProfileCreateSchema` and `PensionProfileUpdateSchema`.

Add a `@validates_schema` method to both schemas that checks:

1. If `earliest_retirement_date` is provided and `hire_date` is provided: `earliest_retirement_date` must be after `hire_date`.
2. If `planned_retirement_date` is provided and `hire_date` is provided: `planned_retirement_date` must be after `hire_date`.
3. If `planned_retirement_date` is provided: it must be after today's date (a past planned retirement date is nonsensical).
4. If both `earliest_retirement_date` and `planned_retirement_date` are provided: `planned_retirement_date` must be on or after `earliest_retirement_date`.

**Note on `hire_date`:** The hire date is typically in the past (the user was hired years ago). The roadmap says "after today's date" for retirement dates, not for hire date. The validation should NOT require hire_date to be after today.

**Note on `earliest_retirement_date`:** This could legitimately be in the past for someone who is already eligible for retirement but has not yet retired. However, the roadmap says "after today's date" for this field. A reasonable compromise: warn but don't block if `earliest_retirement_date` is in the past, or simply skip the "after today" check for this field and only enforce it for `planned_retirement_date`.

**Proposed validation rules:**
- `earliest_retirement_date > hire_date` (if both provided)
- `planned_retirement_date > hire_date` (if both provided)
- `planned_retirement_date > today` (if provided)
- `planned_retirement_date >= earliest_retirement_date` (if both provided)

For the update schema, validation should only apply to fields that are actually being changed. If `hire_date` is not in the update data but `planned_retirement_date` is, the validation needs access to the existing `hire_date`. Since Marshmallow schemas don't have access to the existing model instance by default, the route layer should pass the existing hire_date as context or perform the cross-field validation in the route instead.

**Simpler approach:** Do the cross-field validation in the route layer for updates, and in the schema for creates. For creates, all fields are required/provided, so the schema has everything it needs. For updates, the route has access to the existing pension object and can compare dates.

**B3. Edge cases.**
- Creating a pension with only hire_date (retirement dates left blank): no cross-field validation needed. Valid.
- Updating only the hire_date to a date after the existing planned_retirement_date: the route should validate this. The route has access to the full pension object.
- Setting planned_retirement_date to today's date: the validation checks `planned_retirement_date > today`, so today is rejected. "After today" means strictly tomorrow or later.
- Leap year dates: standard `date` comparison handles this.

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/schemas/validation.py` | Modified | Add `@validates_schema` to PensionProfileCreateSchema |
| `app/routes/retirement.py` | Modified | Add cross-field date validation in `update_pension` route |
| `tests/test_routes/test_retirement.py` | Modified | Add tests for date validation |

---

### B -- 3.9: Stale Retirement Settings Message

**B1. Solution description.** Remove lines 182-186 from `app/templates/retirement/dashboard.html`:

```html
<div class="text-muted mb-4">
  <i class="bi bi-info-circle"></i>
  Retirement settings (withdrawal rate, retirement date, tax rate) have moved to
  <a href="{{ url_for('settings.show', section='retirement') }}">Settings &gt; Retirement</a>.
</div>
```

That's it. A 5-line deletion.

**B3. Edge cases.** None. Pure template removal.

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/templates/retirement/dashboard.html` | Modified | Remove stale settings migration message |

---

### B -- 3.10: Paycheck Calibration (New Feature)

**B1. Solution description.** This is a new feature requiring new database tables, a service, routes, templates, and tests.

**Data Model:**

New table `salary.calibration_overrides`:
```
id              SERIAL PRIMARY KEY
salary_profile_id  INT NOT NULL FK -> salary.salary_profiles(id) ON DELETE CASCADE
                   UNIQUE (one calibration per profile)
-- Actual amounts from pay stub (audit trail)
actual_gross_pay          NUMERIC(10,2) NOT NULL
actual_federal_tax        NUMERIC(10,2) NOT NULL
actual_state_tax          NUMERIC(10,2) NOT NULL
actual_social_security    NUMERIC(10,2) NOT NULL
actual_medicare           NUMERIC(10,2) NOT NULL
-- Derived effective rates
effective_federal_rate    NUMERIC(7,5) NOT NULL
effective_state_rate      NUMERIC(7,5) NOT NULL
effective_ss_rate         NUMERIC(7,5) NOT NULL
effective_medicare_rate   NUMERIC(7,5) NOT NULL
-- Metadata
pay_stub_date             DATE NOT NULL
is_active                 BOOLEAN DEFAULT TRUE
notes                     TEXT
created_at                TIMESTAMPTZ DEFAULT NOW()
updated_at                TIMESTAMPTZ DEFAULT NOW()
```

New table `salary.calibration_deduction_overrides`:
```
id              SERIAL PRIMARY KEY
calibration_id  INT NOT NULL FK -> salary.calibration_overrides(id) ON DELETE CASCADE
deduction_id    INT NOT NULL FK -> salary.paycheck_deductions(id) ON DELETE CASCADE
actual_amount   NUMERIC(10,2) NOT NULL
-- UNIQUE(calibration_id, deduction_id)
created_at      TIMESTAMPTZ DEFAULT NOW()
```

**Service Layer:**

New service `app/services/calibration_service.py`:

1. `derive_effective_rates(actual_gross, actual_federal, actual_state, actual_ss, actual_medicare, taxable_income)`:
   - `effective_federal_rate = actual_federal / taxable_income` (if taxable_income > 0)
   - `effective_state_rate = actual_state / taxable_income` (if taxable_income > 0)
   - `effective_ss_rate = actual_ss / actual_gross` (FICA is on gross)
   - `effective_medicare_rate = actual_medicare / actual_gross` (FICA is on gross)
   - Returns the four rates.

2. `apply_calibration(breakdown, calibration)`:
   - If calibration exists and is active, replace the bracket-based tax values with rate-based calculations:
     - `federal_tax = taxable_biweekly * effective_federal_rate`
     - `state_tax = taxable_biweekly * effective_state_rate`
     - `social_security = gross_biweekly * effective_ss_rate`
     - `medicare = gross_biweekly * effective_medicare_rate`
   - Recalculate net_pay.

**Integration with Paycheck Calculator:**

In `paycheck_calculator.py` `calculate_paycheck()`, after computing the standard tax amounts, check if the profile has an active calibration override. If so, replace the tax values with calibrated values. The calibration override should be loaded eagerly with the profile or passed as an additional argument.

**Preferred approach:** Add an optional `calibration` parameter to `calculate_paycheck()`. When provided and active, use `calibration.effective_federal_rate * taxable_biweekly` instead of the bracket-based calculation. This keeps the calculator pure and testable.

The route/recurrence engine is responsible for loading the calibration and passing it to the calculator.

**Route Layer:**

Add routes to `app/routes/salary.py`:
- `GET /salary/<profile_id>/calibrate` -- Display the calibration form.
- `POST /salary/<profile_id>/calibrate` -- Process the calibration form: validate inputs, derive rates, show confirmation page with derived rates.
- `POST /salary/<profile_id>/calibrate/confirm` -- Save the calibration, regenerate transactions.
- `POST /salary/<profile_id>/calibrate/delete` -- Remove calibration (revert to bracket-based).

**Template Layer:**

New templates:
- `app/templates/salary/calibrate.html` -- Form with fields for each withholding and deduction line from the pay stub.
- `app/templates/salary/calibrate_confirm.html` -- Shows derived rates with comparison to bracket-based estimates. "Confirm" and "Cancel" buttons.

Modify existing:
- `app/templates/salary/form.html` -- Add a "Calibrate from Pay Stub" button linking to the calibration page.
- `app/templates/salary/list.html` -- Add a badge or indicator when a profile has calibration overrides active.

**B3. Edge cases.**
- Zero taxable income (excessive pre-tax deductions): effective federal/state rates cannot be computed. Display an error: "Taxable income is zero; cannot derive effective rates."
- Zero gross pay: reject the input.
- Actual withholding of zero for a tax category: the effective rate is zero. Valid (e.g., a state with no income tax).
- Negative withholding amounts: reject. Withholding cannot be negative on a normal pay stub.
- Re-calibrating: the user enters a new pay stub. The old calibration is replaced (updated, not a second row).
- Deleting calibration: reverts to bracket-based calculations. Must regenerate transactions.
- Multiple salary profiles: each profile has its own calibration. This is handled by the UNIQUE constraint on salary_profile_id.
- Deduction actual amounts: if the user enters actual deduction amounts that differ from the configured amounts, the calibration_deduction_overrides table stores these. The paycheck calculator uses the override amounts instead of the configured amounts.

**B4. Files to create or modify.**
| File | New/Modified | Summary |
|---|---|---|
| `app/models/calibration_override.py` | New | CalibrationOverride and CalibrationDeductionOverride models |
| `app/services/calibration_service.py` | New | Rate derivation and application logic |
| `app/schemas/validation.py` | Modified | Add CalibrationSchema for form validation |
| `app/routes/salary.py` | Modified | Add calibrate, calibrate_confirm, calibrate_delete routes |
| `app/services/paycheck_calculator.py` | Modified | Add optional calibration parameter to calculate_paycheck |
| `app/services/recurrence_engine.py` | Modified | Load calibration and pass to paycheck calculator |
| `app/templates/salary/calibrate.html` | New | Calibration form |
| `app/templates/salary/calibrate_confirm.html` | New | Confirmation page with derived rates |
| `app/templates/salary/form.html` | Modified | Add "Calibrate" button |
| `app/templates/salary/list.html` | Modified | Add calibration badge |
| `migrations/versions/xxxx_add_calibration_overrides.py` | New | Alembic migration for new tables |
| `tests/test_services/test_calibration_service.py` | New | Unit tests for rate derivation |
| `tests/test_services/test_paycheck_calculator.py` | Modified | Tests for calibrated paycheck calculation |
| `tests/test_routes/test_salary.py` | Modified | Route tests for calibration endpoints |

---

## SECTION C: Required Tests

---

### C -- 3.1 and 3.2: No New Tests Required

These bugs are already fixed and comprehensively tested. See Section A for the list of existing tests.

---

### C -- 3.3: Net Biweekly Mismatch

**Test 1: `test_recurrence_engine_uses_fallback_tax_configs_for_future_year`**
- **Category:** Unit (service method)
- **Setup:** Create a salary profile with tax configs for 2026 only. Create pay periods spanning 2026 and 2027. Create a transaction template linked to the salary profile.
- **Action:** Call `_get_transaction_amount()` for a 2027 period.
- **Assertion:** The returned amount should be based on the 2026 tax configs (fallback), not zero federal tax (which would happen if bracket_set is None). Specifically, the amount should equal `calculate_paycheck()` called with the 2026 tax configs for the same period.
- **Why:** Guards against the income mismatch where future-year grid transactions show different amounts than the salary page because the salary page always uses current-year configs.

**Test 2: `test_salary_page_and_grid_show_same_net_pay`**
- **Category:** Integration (route)
- **Setup:** Use `seed_full_user_data` fixture. Create a salary profile with deductions and tax configs.
- **Action:** Load the salary list page and extract the displayed net biweekly amount. Load the grid page and find the income transaction for the current period. Compare amounts.
- **Assertion:** The two amounts must be equal (within rounding tolerance of $0.01).
- **Why:** End-to-end regression test for the mismatch bug. If any code path diverges, this test catches it.

---

### C -- 3.4: Raises HTMX Fix

**Test 3: `test_add_raise_htmx_response_contains_raises_section_id`**
- **Category:** Route (HTTP + HTMX)
- **Setup:** Use `seed_user`, `seed_periods`, `auth_client`. Create a salary profile with a raise.
- **Action:** POST to add_raise with `HX-Request: true` header.
- **Assertion:** Response contains `id="raises-section"` in the HTML body. This ensures the HTMX target survives the swap.
- **Why:** Directly tests the root cause of 3.4. If the id is missing from the partial, subsequent HTMX operations will fail.

**Test 4: `test_delete_raise_htmx_response_contains_raises_section_id`**
- **Category:** Route (HTTP + HTMX)
- **Setup:** Same as above, with an existing raise.
- **Action:** POST to delete_raise with `HX-Request: true` header.
- **Assertion:** Response contains `id="raises-section"`.
- **Why:** Delete operations also return the partial and must preserve the id.

---

### C -- 3.5: Edit Raises and Deductions

**Test 5: `test_update_raise`**
- **Category:** Route
- **Setup:** Create a salary profile with a raise (3% merit, effective March 2026).
- **Action:** POST to `/salary/raises/<raise_id>/edit` with updated data: 4% merit, effective April 2026.
- **Assertion:** Response redirects or returns partial. The raise in the DB has percentage=0.04, effective_month=4. Salary transactions were regenerated.
- **Why:** Core CRUD test for the new edit functionality.

**Test 6: `test_update_raise_htmx_returns_partial`**
- **Category:** Route (HTMX)
- **Setup:** Same as above.
- **Action:** POST with `HX-Request: true` header.
- **Assertion:** Response is 200, contains updated raise data in the partial, contains `id="raises-section"`.
- **Why:** Ensures the HTMX path works correctly for edit.

**Test 7: `test_update_raise_other_user_blocked`**
- **Category:** Route (security/IDOR)
- **Setup:** Create a raise owned by user 1. Authenticate as user 2.
- **Action:** POST to update user 1's raise.
- **Assertion:** Response is redirect (not 200). Raise is unchanged in DB.
- **Why:** Ownership check for the new route.

**Test 8: `test_update_deduction`**
- **Category:** Route
- **Setup:** Create a salary profile with a deduction (401k, $200 flat, pre-tax).
- **Action:** POST to `/salary/deductions/<ded_id>/edit` with updated data: $300 flat.
- **Assertion:** Deduction amount in DB is $300. Salary transactions regenerated.
- **Why:** Core CRUD test for deduction edit.

**Test 9: `test_update_deduction_percentage_conversion`**
- **Category:** Route
- **Setup:** Create a percentage deduction stored as 0.06 (6%).
- **Action:** POST to edit with `amount: "8"` (user enters 8 for 8%).
- **Assertion:** Deduction amount in DB is 0.08. The route correctly converts the percentage input.
- **Why:** Catches the conversion bug where percentage deductions are stored as decimals but displayed/input as percentages.

**Test 10: `test_update_deduction_other_user_blocked`**
- **Category:** Route (security/IDOR)
- **Setup:** Create a deduction owned by user 1. Authenticate as user 2.
- **Action:** POST to update user 1's deduction.
- **Assertion:** Response is redirect. Deduction unchanged.
- **Why:** Ownership check.

---

### C -- 3.6: Escrow Inflation Rate Validation

**Test 11: `test_escrow_add_with_percentage_inflation_rate`**
- **Category:** Route
- **Setup:** Create a mortgage account.
- **Action:** POST to add_escrow with `name: "Property Tax"`, `annual_amount: "4800"`, `inflation_rate: "3"`.
- **Assertion:** Response is 200. Component created in DB with `inflation_rate = Decimal("0.03")`.
- **Why:** Tests the exact user flow that was broken. The user enters "3" for 3%, the schema accepts it (max=100), and the route converts it to 0.03.

**Test 12: `test_escrow_add_with_zero_inflation_rate`**
- **Category:** Route (edge case)
- **Setup:** Create a mortgage account.
- **Action:** POST with `inflation_rate: "0"`.
- **Assertion:** Component created with `inflation_rate = Decimal("0")` or None (depending on route logic for zero).
- **Why:** Zero should be valid and not cause division issues.

**Test 13: `test_escrow_add_with_empty_inflation_rate`**
- **Category:** Route (edge case)
- **Setup:** Create a mortgage account.
- **Action:** POST with `inflation_rate: ""` (empty string).
- **Assertion:** Component created with `inflation_rate = None`. No conversion error.
- **Why:** The strip_empty_strings pre_load removes it, allow_none=True accepts None.

**Test 14: `test_escrow_add_with_negative_inflation_rate_rejected`**
- **Category:** Route (validation)
- **Setup:** Create a mortgage account.
- **Action:** POST with `inflation_rate: "-2"`.
- **Assertion:** Response is 400. No component created.
- **Why:** Negative inflation rates should be rejected by Range(min=0).

---

### C -- 3.7: Escrow Payment Summary Update

**Test 15: `test_escrow_add_response_includes_oob_payment_summary`**
- **Category:** Route (HTMX)
- **Setup:** Create a mortgage account with params (so payment summary exists).
- **Action:** POST to add_escrow.
- **Assertion:** Response contains an element with `id="total-payment-display"` and `hx-swap-oob="true"`. The displayed amount reflects the new escrow component.
- **Why:** Verifies the OOB mechanism works for updating the payment summary after escrow add.

**Test 16: `test_escrow_delete_response_includes_oob_payment_summary`**
- **Category:** Route (HTMX)
- **Setup:** Create a mortgage account with params and an escrow component.
- **Action:** POST to delete_escrow.
- **Assertion:** Response contains OOB payment summary with updated (lower) amount.
- **Why:** Same fix applies to delete path.

---

### C -- 3.8: Pension Date Validation

**Test 17: `test_create_pension_retirement_before_hire_rejected`**
- **Category:** Route (validation)
- **Setup:** Standard auth_client.
- **Action:** POST to create pension with `hire_date: "2020-01-01"`, `planned_retirement_date: "2019-01-01"`.
- **Assertion:** Flash error. No pension created.
- **Why:** Core validation rule: retirement must be after hire.

**Test 18: `test_create_pension_retirement_in_past_rejected`**
- **Category:** Route (validation)
- **Setup:** Standard auth_client.
- **Action:** POST to create pension with `planned_retirement_date: "2020-01-01"` (in the past).
- **Assertion:** Flash error. No pension created.
- **Why:** A past planned retirement date is nonsensical.

**Test 19: `test_create_pension_earliest_before_hire_rejected`**
- **Category:** Route (validation)
- **Setup:** Standard auth_client.
- **Action:** POST with `hire_date: "2020-01-01"`, `earliest_retirement_date: "2019-06-01"`.
- **Assertion:** Flash error. No pension created.
- **Why:** Earliest retirement cannot be before employment started.

**Test 20: `test_create_pension_planned_before_earliest_rejected`**
- **Category:** Route (validation)
- **Setup:** Standard auth_client.
- **Action:** POST with `earliest_retirement_date: "2050-01-01"`, `planned_retirement_date: "2045-01-01"`.
- **Assertion:** Flash error. No pension created.
- **Why:** Planned retirement before earliest eligibility is nonsensical.

**Test 21: `test_create_pension_valid_dates_accepted`**
- **Category:** Route (regression)
- **Setup:** Standard auth_client.
- **Action:** POST with valid dates: `hire_date: "2015-01-01"`, `earliest_retirement_date: "2045-01-01"`, `planned_retirement_date: "2050-01-01"`.
- **Assertion:** Pension created successfully.
- **Why:** Ensures the new validation doesn't reject valid inputs.

**Test 22: `test_update_pension_retirement_before_hire_rejected`**
- **Category:** Route (validation)
- **Setup:** Create a pension. Authenticate.
- **Action:** POST update with `planned_retirement_date` before the existing `hire_date`.
- **Assertion:** Flash error. Pension unchanged.
- **Why:** Update path needs the same validation.

---

### C -- 3.9: Stale Message Removal

**Test 23: `test_retirement_dashboard_no_settings_migration_message`**
- **Category:** Route (regression)
- **Setup:** Standard auth_client with retirement data.
- **Action:** GET `/retirement`.
- **Assertion:** Response does NOT contain "have moved to" or "Settings &gt; Retirement" in the context of a migration notice.
- **Why:** Ensures the stale message is removed and stays removed.

---

### C -- 3.10: Paycheck Calibration

**Test 24: `test_derive_effective_rates_basic`**
- **Category:** Unit
- **Setup:** Actual values: gross=$2307.69, federal=$153.08, state=$94.85, SS=$143.08, medicare=$33.46, taxable=$2107.69.
- **Action:** Call `derive_effective_rates()`.
- **Assertion:** `effective_federal_rate = 153.08 / 2107.69 = 0.07261` (approx), `effective_state_rate = 94.85 / 2107.69 = 0.04500` (approx), `effective_ss_rate = 143.08 / 2307.69 = 0.06200`, `effective_medicare_rate = 33.46 / 2307.69 = 0.01450`.
- **Why:** Core calibration logic test.

**Test 25: `test_derive_effective_rates_zero_taxable_income_error`**
- **Category:** Unit (edge case)
- **Setup:** Zero taxable income.
- **Action:** Call `derive_effective_rates()`.
- **Assertion:** Raises `ValidationError` (or returns an error indicator).
- **Why:** Division by zero guard.

**Test 26: `test_calibrated_paycheck_uses_override_rates`**
- **Category:** Unit
- **Setup:** Profile with calibration override. Known effective rates.
- **Action:** Call `calculate_paycheck()` with calibration parameter.
- **Assertion:** Federal tax equals `taxable_biweekly * effective_federal_rate`, not the bracket-based calculation. Net pay reflects the calibrated values.
- **Why:** Verifies the calculator respects overrides.

**Test 27: `test_calibrated_paycheck_without_override_uses_brackets`**
- **Category:** Unit (regression)
- **Setup:** Profile without calibration override.
- **Action:** Call `calculate_paycheck()` with calibration=None.
- **Assertion:** Tax values match bracket-based calculation (same as before calibration feature).
- **Why:** Ensures the calibration feature doesn't break existing behavior.

**Test 28: `test_calibrate_route_renders_form`**
- **Category:** Route
- **Setup:** Create a salary profile.
- **Action:** GET `/salary/<profile_id>/calibrate`.
- **Assertion:** 200, form fields for each withholding line.
- **Why:** Verifies the calibration form renders.

**Test 29: `test_calibrate_saves_and_regenerates`**
- **Category:** Route (integration)
- **Setup:** Create a salary profile with transactions in the grid.
- **Action:** POST calibration data, then POST confirm.
- **Assertion:** CalibrationOverride created in DB. Grid transactions regenerated with calibrated amounts.
- **Why:** End-to-end test of the calibration workflow.

**Test 30: `test_calibrate_delete_reverts_to_brackets`**
- **Category:** Route
- **Setup:** Profile with active calibration.
- **Action:** POST to calibrate/delete.
- **Assertion:** CalibrationOverride.is_active = False. Transactions regenerated with bracket-based amounts.
- **Why:** Verifies revert-to-brackets works.

---

### Cross-Cutting Regression Tests

**Test 31: `test_full_suite_passes_after_all_fixes`**
- Run `timeout 660 pytest -v --tb=short` as the final gate.
- All 1258+ existing tests must pass. Any failure indicates a regression from the fixes.

---

## SECTION D: Risk and Regression Assessment

---

### D -- 3.1 and 3.2: No Risk (Already Fixed)

No changes are being made. No regression risk.

---

### D -- 3.3: Net Biweekly Mismatch

**D1. What could break.** The change to recurrence_engine's `_get_transaction_amount()` to fall back to current-year tax configs could change the amounts of all future-year income transactions. Any test that asserts specific transaction amounts for future-year periods will fail.

**D2. Mitigation.** The new test `test_recurrence_engine_uses_fallback_tax_configs_for_future_year` validates the new behavior. Existing tests in `test_recurrence_engine.py` that test salary-linked template amounts should be reviewed for future-year period assertions.

**D3. Data migration risk.** No schema change. Existing transactions in the grid will have stale amounts from the old behavior. Running a "regenerate all salary transactions" operation (which the app already does when tax configs change) will update them. No manual intervention needed beyond triggering a regeneration.

**D4. Rollback plan.** Revert the commit. No migration to roll back.

---

### D -- 3.4: Raises HTMX Fix

**D1. What could break.** Changing the wrapper structure in `form.html` and adding the id to `_raises_section.html` could affect CSS styling if any styles target the old wrapper div. The collapse/toggle behavior change could break if the deductions-style toggle JS isn't present for the raises section.

**D2. Mitigation.** Test 3 and Test 4 verify the id is present. Manual verification: on the running app, add two raises in sequence without refreshing. Both should work.

**D3. Data migration risk.** None. Template-only change.

**D4. Rollback plan.** Revert the commit. Pure template change.

---

### D -- 3.5: Edit Raises and Deductions

**D1. What could break.** New routes could have ownership check issues if the pattern differs from existing add/delete routes. The JavaScript for form population could have bugs that cause wrong data to be sent on update.

**D2. Mitigation.** Tests 5-10 cover the happy path, percentage conversion, and IDOR protection. Manual verification: edit a raise and verify the paycheck breakdown changes correctly.

**D3. Data migration risk.** None. No schema changes for existing tables.

**D4. Rollback plan.** Revert the commit. No migration.

---

### D -- 3.6: Escrow Inflation Rate Validation

**D1. What could break.** Any code that constructs EscrowComponentSchema programmatically and passes pre-converted decimal values (like "0.03") will still work because 0.03 < 100. The only change is allowing values up to 100 instead of 1.

**D2. Mitigation.** Tests 11-14 cover the user-facing flow. The existing test `test_escrow_add` should be updated to send "3" (percentage) instead of "0.03" (decimal) to match the actual UI behavior.

**D3. Data migration risk.** None. The schema change only affects validation, not stored data.

**D4. Rollback plan.** Revert the commit. No migration.

---

### D -- 3.7: Escrow Payment Summary Update

**D1. What could break.** OOB swaps depend on the target id existing in the DOM. If the dashboard template is changed later and the id is removed, the OOB swap silently fails (no error, but no update). The existing escrow add/delete behavior (updating the list) is unchanged.

**D2. Mitigation.** Tests 15-16 verify the OOB content is in the response. Manual verification: add an escrow component and verify the total payment updates without refresh.

**D3. Data migration risk.** None. Template and route changes only.

**D4. Rollback plan.** Revert the commit. No migration.

---

### D -- 3.8: Pension Date Validation

**D1. What could break.** Existing pension profiles with invalid dates (retirement before hire) were already saved and are unaffected. The validation only applies to new creates and updates. A user trying to update an existing invalid pension without fixing the dates would be blocked.

**D2. Mitigation.** Tests 17-22 cover all validation rules and the happy path. The regression test (Test 21) ensures valid inputs are still accepted.

**D3. Data migration risk.** None. No schema change. Existing invalid data remains in the database and is not retroactively validated.

**D4. Rollback plan.** Revert the commit. No migration.

---

### D -- 3.9: Stale Message Removal

**D1. What could break.** Nothing. Removing a static text block has no functional impact.

**D2. Mitigation.** Test 23 verifies the message is gone.

**D3. Data migration risk.** None.

**D4. Rollback plan.** Revert the commit.

---

### D -- 3.10: Paycheck Calibration

**D1. What could break.** The calibration parameter added to `calculate_paycheck()` changes the function signature. Any caller that passes positional arguments could break if the new parameter shifts positions. However, since it will be added as a keyword-only argument with a default of None, existing callers are unaffected.

Changing `_get_transaction_amount()` in the recurrence engine to load and pass calibration data adds a database query per salary-linked period. For 26+ periods, this could be slow. Mitigation: load the calibration once per template and pass it through.

**D2. Mitigation.** Tests 24-30 cover the calibration service, calculator integration, route endpoints, and the revert-to-brackets flow. Test 27 is the key regression test ensuring uncalibrated behavior is unchanged.

**D3. Data migration risk.** New tables only. No existing data is modified. The migration adds two tables to the salary schema. If rolled back, the tables must be dropped.

**D4. Rollback plan.** Revert the code commit AND run `flask db downgrade` to drop the new tables. Since the tables are new (no existing data), the downgrade is safe.

---

## SECTION E: Difficulty, Time Estimates, and Implementation Order

---

### E1. Difficulty Ratings

| Task | Difficulty | Time Estimate | Justification |
|---|---|---|---|
| 3.1 | N/A | 0 hours | Already fixed and tested |
| 3.2 | N/A | 0 hours | Already fixed and tested |
| 3.3 | Simple | 1-2 hours | Single-file change in recurrence engine + one integration test |
| 3.4 | Simple | 30-60 min | Template-only: add one id attribute, remove one wrapper div, fix toggle |
| 3.5 | Moderate | 4-6 hours | New routes, template JS for edit mode, conversion logic, 6+ tests |
| 3.6 | Trivial | 15-30 min | One-line schema change + test update |
| 3.7 | Simple | 1-2 hours | OOB swap in templates, compute values in route, 2 tests |
| 3.8 | Simple | 1-2 hours | Schema validation + route validation for updates, 6 tests |
| 3.9 | Trivial | 10-15 min | Delete 5 lines from a template |
| 3.10 | Complex | 10-14 hours | New tables, migration, service, routes, templates, 7+ tests |

### E2. Implementation Order

1. **3.9 -- Stale Retirement Message** (Trivial, 10 min)
   - *Rationale:* Easiest possible fix. Clears the board and builds momentum. Zero risk.

2. **3.6 -- Escrow Inflation Rate Validation** (Trivial, 15-30 min)
   - *Rationale:* One-line fix with immediate user impact. Unblocks escrow inflation modeling.

3. **3.4 -- Raises HTMX Fix** (Simple, 30-60 min)
   - *Rationale:* Template-only fix. Unblocks the raise workflow needed for 3.5.

4. **3.8 -- Pension Date Validation** (Simple, 1-2 hours)
   - *Rationale:* Independent of other fixes. Schema-only change with straightforward tests.

5. **3.7 -- Escrow Payment Summary Update** (Simple, 1-2 hours)
   - *Rationale:* Grouped with 3.6 (same domain -- mortgage/escrow). Natural follow-up after the validation fix.

6. **3.3 -- Net Biweekly Mismatch** (Simple, 1-2 hours)
   - *Rationale:* Addresses a data correctness issue. Should be done before 3.5 because editing raises/deductions triggers regeneration, and the mismatch fix ensures regenerated amounts are correct.

7. **3.5 -- Edit Raises and Deductions** (Moderate, 4-6 hours)
   - *Rationale:* Depends on 3.4 being done (the HTMX fix ensures the form works). Depends on 3.3 (regenerated amounts should be correct). The most complex bug fix in the section.

8. **3.10 -- Paycheck Calibration** (Complex, 10-14 hours)
   - *Rationale:* Explicitly depends on 3.1 being fixed (satisfied). Depends on 3.3 and 3.5 being done so that the salary profile page is fully functional before adding calibration. This is the largest task and should be done last.

### E3. Total Estimated Time Range

**18-28 hours** of development time (including tests and manual verification).

Breakdown:
- Trivial fixes (3.9 + 3.6): ~0.5 hours
- Simple fixes (3.4 + 3.8 + 3.7 + 3.3): ~4-7 hours
- Moderate fix (3.5): ~4-6 hours
- Complex feature (3.10): ~10-14 hours

---

## SECTION F: Atomic Commit Plan

---

### Commit 1: Remove stale retirement settings message (3.9)

**F1. Commit message:** `fix(retirement): remove stale settings migration message from dashboard`

**F2. Files included:**
- `app/templates/retirement/dashboard.html` -- Remove lines 182-186 (the "have moved to Settings" notice)

**F3. Tests that must pass:**
- `test_retirement_dashboard_no_settings_migration_message` (new, add to `tests/test_routes/test_retirement.py`)
- All existing `tests/test_routes/test_retirement.py` tests

**F4. Manual verification:** Open `/retirement` in the browser and confirm the "Retirement settings have moved to..." message is no longer displayed. The disclaimer about projections being estimates should still appear.

---

### Commit 2: Fix escrow inflation rate validation range (3.6)

**F1. Commit message:** `fix(mortgage): accept percentage input for escrow inflation rate validation`

**F2. Files included:**
- `app/schemas/validation.py` -- Change `EscrowComponentSchema.inflation_rate` range from `max=1` to `max=100`
- `tests/test_routes/test_mortgage.py` -- Update `test_escrow_add` to send percentage input ("3" not "0.03"); add `test_escrow_add_with_percentage_inflation_rate`, `test_escrow_add_with_zero_inflation_rate`, `test_escrow_add_with_empty_inflation_rate`, `test_escrow_add_with_negative_inflation_rate_rejected`

**F3. Tests that must pass:**
- All tests in `tests/test_routes/test_mortgage.py`
- `test_escrow_add_with_percentage_inflation_rate` (new)

**F4. Manual verification:** On the mortgage dashboard, go to the Escrow tab, add an escrow component with name "Property Tax", annual amount $4800, inflation rate 3. It should succeed. The component should appear in the list with "3.0%" inflation.

---

### Commit 3: Fix raises section HTMX swap target (3.4)

**F1. Commit message:** `fix(salary): preserve raises-section id after HTMX swap for consecutive operations`

**F2. Files included:**
- `app/templates/salary/_raises_section.html` -- Add `id="raises-section"` to the outermost div; replace `data-bs-toggle="collapse"` with `data-toggle-target` pattern for consistency with deductions
- `app/templates/salary/form.html` -- Remove the `<div id="raises-section">` wrapper around the include (the partial now carries the id)
- `tests/test_routes/test_salary.py` -- Add `test_add_raise_htmx_response_contains_raises_section_id`, `test_delete_raise_htmx_response_contains_raises_section_id`

**F3. Tests that must pass:**
- All tests in `tests/test_routes/test_salary.py`
- `test_add_raise_htmx_response_contains_raises_section_id` (new)
- `test_delete_raise_htmx_response_contains_raises_section_id` (new)

**F4. Manual verification:** On the salary profile edit page, add a raise. Without refreshing, add a second raise. Both should appear in the table. Delete one. Without refreshing, add another. All operations should work without page refresh.

---

### Commit 4: Add pension date cross-field validation (3.8)

**F1. Commit message:** `fix(retirement): add cross-field date validation to pension profile forms`

**F2. Files included:**
- `app/schemas/validation.py` -- Add `@validates_schema` method to `PensionProfileCreateSchema` enforcing date ordering rules
- `app/routes/retirement.py` -- Add date validation in `update_pension` route (for update path where schema doesn't have all fields)
- `tests/test_routes/test_retirement.py` -- Add `test_create_pension_retirement_before_hire_rejected`, `test_create_pension_retirement_in_past_rejected`, `test_create_pension_earliest_before_hire_rejected`, `test_create_pension_planned_before_earliest_rejected`, `test_create_pension_valid_dates_accepted`, `test_update_pension_retirement_before_hire_rejected`

**F3. Tests that must pass:**
- All tests in `tests/test_routes/test_retirement.py`
- All new pension date validation tests

**F4. Manual verification:** Go to `/retirement/pension`, try to create a pension with planned retirement date before hire date. Should see a validation error. Try with valid dates. Should succeed.

---

### Commit 5: Update escrow HTMX response with payment summary (3.7)

**F1. Commit message:** `fix(mortgage): update payment summary via OOB swap after escrow add/delete`

**F2. Files included:**
- `app/routes/mortgage.py` -- In `add_escrow` and `delete_escrow`, compute updated `total_payment` and `monthly_escrow`; pass to template
- `app/templates/mortgage/_escrow_list.html` -- Add OOB swap fragments for payment summary and escrow badge
- `app/templates/mortgage/dashboard.html` -- Add `id="total-payment-display"` and `id="escrow-badge"` to the relevant elements
- `tests/test_routes/test_mortgage.py` -- Add `test_escrow_add_response_includes_oob_payment_summary`, `test_escrow_delete_response_includes_oob_payment_summary`

**F3. Tests that must pass:**
- All tests in `tests/test_routes/test_mortgage.py`
- New OOB tests

**F4. Manual verification:** On the mortgage dashboard, add an escrow component. Without refreshing, switch to the Overview tab. The "Total Monthly (with escrow)" line should show the updated amount including the new escrow component.

---

### Commit 6: Fix grid income mismatch with salary page (3.3)

**F1. Commit message:** `fix(recurrence): fall back to current-year tax configs for future-year salary transactions`

**F2. Files included:**
- `app/services/recurrence_engine.py` -- In `_get_transaction_amount()`, add fallback to current-year tax configs when future-year configs are all None
- `tests/test_services/test_recurrence_engine.py` -- Add `test_recurrence_engine_uses_fallback_tax_configs_for_future_year`

**F3. Tests that must pass:**
- All tests in `tests/test_services/test_recurrence_engine.py`
- All tests in `tests/test_services/test_paycheck_calculator.py`
- New fallback test

**F4. Manual verification:** On the salary profile list page, note the "Est. Net Biweekly" amount. Go to the grid and find the income transaction for the current period. The amounts should match.

---

### Commit 7: Add edit functionality for salary raises (3.5, part 1)

**F1. Commit message:** `feat(salary): add edit route and UI for salary raises`

**F2. Files included:**
- `app/routes/salary.py` -- Add `update_raise` route
- `app/templates/salary/_raises_section.html` -- Add edit button per row with data attributes; add JS for form population and mode switching
- `app/static/js/salary_edit.js` -- JS handler for edit/cancel mode (or inline in template)
- `tests/test_routes/test_salary.py` -- Add `test_update_raise`, `test_update_raise_htmx_returns_partial`, `test_update_raise_other_user_blocked`

**F3. Tests that must pass:**
- All tests in `tests/test_routes/test_salary.py`
- New raise edit tests

**F4. Manual verification:** On the salary profile edit page, click the edit icon on an existing raise. The add form should expand and populate with the raise's current values. Change the percentage and submit. The table should update with the new values.

---

### Commit 8: Add edit functionality for salary deductions (3.5, part 2)

**F1. Commit message:** `feat(salary): add edit route and UI for salary deductions`

**F2. Files included:**
- `app/routes/salary.py` -- Add `update_deduction` route
- `app/templates/salary/_deductions_section.html` -- Add edit button per row with data attributes
- `tests/test_routes/test_salary.py` -- Add `test_update_deduction`, `test_update_deduction_percentage_conversion`, `test_update_deduction_other_user_blocked`

**F3. Tests that must pass:**
- All tests in `tests/test_routes/test_salary.py`
- New deduction edit tests

**F4. Manual verification:** On the salary profile edit page, click the edit icon on an existing deduction. Change the amount and submit. The table should update. Verify that a percentage deduction correctly converts between display (6%) and storage (0.06).

---

### Commit 9: Add calibration data model and migration (3.10, part 1)

**F1. Commit message:** `feat(salary): add calibration_overrides and calibration_deduction_overrides tables`

**F2. Files included:**
- `app/models/calibration_override.py` -- New model file with CalibrationOverride and CalibrationDeductionOverride
- `migrations/versions/xxxx_add_calibration_overrides.py` -- New Alembic migration
- `app/models/__init__.py` -- Import new models (if needed for discovery)

**F3. Tests that must pass:**
- `flask db upgrade` succeeds without errors
- `flask db downgrade` removes the tables cleanly
- All existing tests still pass

**F4. Manual verification:** Run the migration against the dev database. Verify the tables exist in the salary schema with correct columns and constraints.

---

### Commit 10: Add calibration service (3.10, part 2)

**F1. Commit message:** `feat(salary): add calibration_service with rate derivation and application logic`

**F2. Files included:**
- `app/services/calibration_service.py` -- New service with `derive_effective_rates()` and `apply_calibration()`
- `tests/test_services/test_calibration_service.py` -- New test file with `test_derive_effective_rates_basic`, `test_derive_effective_rates_zero_taxable_income_error`, and other unit tests

**F3. Tests that must pass:**
- All new calibration service tests
- All existing service tests

**F4. Manual verification:** None needed (service-only, no UI yet).

---

### Commit 11: Integrate calibration into paycheck calculator (3.10, part 3)

**F1. Commit message:** `feat(salary): integrate calibration overrides into paycheck calculator`

**F2. Files included:**
- `app/services/paycheck_calculator.py` -- Add optional `calibration` parameter to `calculate_paycheck()`; when provided, use override rates instead of bracket-based calculations
- `app/services/recurrence_engine.py` -- Load calibration in `_get_transaction_amount()` and pass to calculator
- `tests/test_services/test_paycheck_calculator.py` -- Add `test_calibrated_paycheck_uses_override_rates`, `test_calibrated_paycheck_without_override_uses_brackets`

**F3. Tests that must pass:**
- All tests in `tests/test_services/test_paycheck_calculator.py`
- All tests in `tests/test_services/test_recurrence_engine.py`

**F4. Manual verification:** None yet (routes not added).

---

### Commit 12: Add calibration routes and templates (3.10, part 4)

**F1. Commit message:** `feat(salary): add calibration form, confirmation page, and route endpoints`

**F2. Files included:**
- `app/routes/salary.py` -- Add `calibrate_form`, `calibrate_preview`, `calibrate_confirm`, `calibrate_delete` routes
- `app/schemas/validation.py` -- Add `CalibrationSchema`
- `app/templates/salary/calibrate.html` -- New calibration form template
- `app/templates/salary/calibrate_confirm.html` -- New confirmation template
- `app/templates/salary/form.html` -- Add "Calibrate from Pay Stub" button
- `app/templates/salary/list.html` -- Add calibration badge indicator
- `tests/test_routes/test_salary.py` -- Add `test_calibrate_route_renders_form`, `test_calibrate_saves_and_regenerates`, `test_calibrate_delete_reverts_to_brackets`

**F3. Tests that must pass:**
- All tests in `tests/test_routes/test_salary.py`
- Full test suite: `timeout 660 pytest -v --tb=short`

**F4. Manual verification:** On the salary profile edit page, click "Calibrate from Pay Stub." Enter actual values from a real pay stub. Review the derived rates on the confirmation page. Confirm. Verify the salary profile list now shows the calibrated net pay. Go to the grid and verify the income transactions match the calibrated amount.

---

### Final Gate

After all commits, run the full test suite:
```bash
timeout 660 pytest -v --tb=short
```

All tests must pass before reporting done.
