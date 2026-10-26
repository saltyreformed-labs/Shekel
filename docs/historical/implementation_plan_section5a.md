> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Section 5A -- Cleanup Sprint

**Version:** 1.0
**Date:** March 31, 2026
**Prerequisite:** All Sections 3, 3A, 4, 4A, and 4B are implemented, tested, and merged.
**Scope:** Tasks 5A.1 through 5A.5 from `docs/project_roadmap_v4-3.md`. All five tasks are
included; no tasks were removed due to discrepancies.

**Relationship to Section 5:** Section 5A is a prerequisite to Section 5 (Debt and Account
Improvements). Task 5A.1 (effective amount calculation) directly enables Section 5.1 (payment
linkage), which depends on the balance calculator correctly using actual amounts. Task 5A.5
(delete/archive pattern) establishes the lifecycle model consumed by Section 5.9 (payoff
lifecycle). Both must land before Section 5 begins.

---

## Documentation vs. Code Discrepancies

The following discrepancies were found between `docs/project_roadmap_v4-3.md` Section 5A and
the current codebase. The implementation plan is based on the code, not the documentation.

### D-1: Transaction.effective_amount property already exists

**Roadmap says:** "This is the `effective_amount` concept ... All projection logic that
currently reads `estimated_amount` should read `effective_amount` instead."

**Code says:** `app/models/transaction.py` lines 122-136 already define an `effective_amount`
property. However, it only returns `actual_amount` for settled statuses
(`self.status.is_settled`). For Projected status, it unconditionally returns
`self.estimated_amount`, ignoring `actual_amount` even when populated.

**Impact:** The property exists but is incomplete for the roadmap's requirements. The fix
requires: (1) simplifying the property to prefer `actual_amount` for all active statuses, and
(2) updating the balance calculator to use the property instead of reading `estimated_amount`
directly.

### D-2: Grid subtotals already use effective_amount, but the balance calculator does not

**Roadmap says:** "The grid and balance calculator compute projected end balances using only
`estimated_amount`."

**Code says:** Grid subtotals (`app/routes/grid.py` lines 233-234) already use
`txn.effective_amount`. But the balance calculator (`app/services/balance_calculator.py` lines
298 and 326) uses `txn.estimated_amount` directly. This creates an inconsistency where
subtotals and projected end balances use different amount sources.

**Impact:** The subtotals are partially correct (they use effective_amount, but that property
itself has the Projected-status bug from D-1). After fixing both the property and the balance
calculator, subtotals and balances will be fully consistent.

### D-3: Grid transaction cell display already handles actual vs. estimated

**Roadmap says:** "The grid template's display of amounts may also need adjustment to visually
indicate when the effective amount is actual vs. estimated."

**Code says:** `app/templates/grid/_transaction_cell.html` line 9 already computes
`display_amount = t.actual_amount if t.actual_amount is not none else t.estimated_amount`, and
lines 22-31 visually distinguish actual from estimated (crossed-out estimate + bold actual).

**Impact:** No template display changes needed for 5A.1. The display logic is already correct.

### D-4: Balance calculator amortization variant also uses estimated_amount

**Roadmap says:** (Not explicitly mentioned in scope.)

**Code says:** `app/services/balance_calculator.py` line 255 uses `txn.estimated_amount` to
detect loan payment amounts in `calculate_balances_with_amortization()`. This affects debt
account balance projections.

**Impact:** The fix must also update line 255 to use `effective_amount`. This is included in
commit 5A.1.

### D-5: Category model uses flat strings, not FK-based group hierarchy

**Roadmap says:** "The category model itself may not need changes if group assignment is
already by foreign key."

**Code says:** `app/models/category.py` uses `group_name` (String(100)) and `item_name`
(String(100)) -- flat string columns, not a FK to a separate group table. "Re-parenting" a
category item means updating the `group_name` string. All transactions reference the category
by `category_id` (FK to the Category row), so re-parenting does not require data migration.

**Impact:** Re-parenting is simpler than anticipated. No FK migration needed. The group
dropdown in the edit form must be populated from a `SELECT DISTINCT group_name` query.

### D-6: Category model has no is_active column

**Roadmap says:** "The existing `is_active` column on some tables may need to be reconciled
with `is_archived`."

**Code says:** `app/models/category.py` has no `is_active` column. Categories can only be
hard-deleted (if not in use). The models that DO have `is_active`: Account, TransactionTemplate,
TransferTemplate, SalaryProfile, SavingsGoal, PensionProfile, PaycheckDeduction,
CalibrationOverride, EscrowComponent. `is_archived` does not exist anywhere in the codebase.

**Impact:** Task 5A.5 requires a migration to add `is_active` to the categories table. See
Risk R-1 for the `is_active` vs. `is_archived` design decision.

### D-7: Existing deactivate/reactivate already implements the "archive" pattern

**Roadmap says:** "Establish a two-step lifecycle pattern: Active -> Archived -> Permanently
Deleted."

**Code says:** Transaction templates (`app/routes/templates.py` lines 310-336), transfer
templates (`app/routes/transfers.py` lines 321-360), and accounts
(`app/routes/accounts.py` lines 251-287) already implement `is_active=False` deactivation
with reactivation. The behavioral semantics match "archive": hidden from active views,
recurrence stops, history preserved, reversible.

**Impact:** The archive pattern already exists functionally. Task 5A.5 adds: (1) permanent
delete with history check, (2) the pattern extended to categories, (3) UI terminology change
from "Deactivate/Reactivate" to "Archive/Unarchive", and (4) separated "Archived" section in
listing pages.

---

## Codebase Inventory

Every file that Section 5A tasks will create, modify, or depend on. Built from reading the
actual files; line counts verified as of March 31, 2026.

### Models

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/models/transaction.py` | 149 | Transaction model with effective_amount property | 5A.1 |
| `app/models/category.py` | 40 | Category model (group_name, item_name strings) | 5A.4, 5A.5 |
| `app/models/account.py` | 88 | Account model (is_active column exists) | 5A.5 |
| `app/models/transaction_template.py` | 64 | TransactionTemplate (is_active column exists) | 5A.5 |
| `app/models/transfer_template.py` | 75 | TransferTemplate (is_active column exists) | 5A.5 |
| `app/models/transfer.py` | 104 | Transfer model (is_deleted for soft-delete) | 5A.5 depends |
| `app/models/recurrence_rule.py` | 66 | RecurrenceRule (no is_active) | 5A.5 depends |
| `app/models/__init__.py` | 57 | Model registry for Alembic | -- |

### Services

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/services/balance_calculator.py` | 332 | Balance projection from anchor. Uses estimated_amount. | 5A.1 |
| `app/services/chart_data_service.py` | 720 | Chart data assembly. Calls balance_calculator. | 5A.1 (indirect) |
| `app/services/transfer_service.py` | 766 | Transfer CRUD with shadow transaction invariants. | 5A.5 depends |
| `app/services/recurrence_engine.py` | 552 | Transaction recurrence generation. | 5A.5 depends |
| `app/services/transfer_recurrence.py` | 261 | Transfer recurrence generation. | 5A.5 depends |

### Routes

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/routes/grid.py` | 394 | Budget grid with balance row and subtotals. | 5A.1 (indirect), 5A.2 |
| `app/routes/salary.py` | 1089 | Salary profile listing, CRUD, breakdown, projection. | 5A.3 |
| `app/routes/categories.py` | 123 | Category CRUD (create and delete only). | 5A.4, 5A.5 |
| `app/routes/settings.py` | 196 | Settings dashboard, loads category data. | 5A.4 |
| `app/routes/templates.py` | 446 | Transaction template CRUD, deactivate/reactivate. | 5A.5 |
| `app/routes/transfers.py` | 695 | Transfer template CRUD, deactivate/reactivate. | 5A.5 |
| `app/routes/accounts.py` | 811 | Account CRUD, deactivate/reactivate. | 5A.5 |

### Schemas

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/schemas/validation.py` | 999 | Marshmallow validation. CategoryCreateSchema on lines 141-146. | 5A.4 |

### Enums and Cache

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/enums.py` | 129 | All enum definitions. | -- |
| `app/ref_cache.py` | 324 | Enum-to-DB-ID mapping cache. | -- |

### Utils

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/utils/auth_helpers.py` | 86 | Ownership verification (get_or_404, get_owned_via_parent). | 5A.5 depends |

### Templates

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `app/templates/grid/grid.html` | 333 | Main grid with group headers, no item sub-headers. | 5A.2 |
| `app/templates/grid/_transaction_cell.html` | 59 | Cell display (already handles actual vs estimated). | -- |
| `app/templates/grid/_balance_row.html` | -- | Balance row partial. | -- |
| `app/templates/salary/list.html` | 112 | Salary listing with duplicate buttons. | 5A.3 |
| `app/templates/settings/_categories.html` | 54 | Category settings section (free-text group field). | 5A.4 |
| `app/templates/categories/_category_row.html` | 13 | HTMX row partial for category creation. | 5A.4 |
| `app/templates/categories/list.html` | 67 | Full categories listing page. | 5A.4 |
| `app/templates/templates/list.html` | 127 | Transaction template listing (muted inactive rows). | 5A.5 |
| `app/templates/transfers/list.html` | 121 | Transfer template listing (muted inactive rows). | 5A.5 |
| `app/templates/accounts/list.html` | 89 | Accounts listing (muted inactive rows). | 5A.5 |

### Utils (new files to create)

| File | Purpose | Created by |
|------|---------|------------|
| `app/utils/archive_helpers.py` | History detection for delete/archive eligibility. | 5A.5 |

### Tests

| File | Lines | Purpose | Affected by |
|------|-------|---------|-------------|
| `tests/test_services/test_balance_calculator.py` | 1362 | Balance calculator tests. | 5A.1 |
| `tests/test_routes/test_grid.py` | 3297 | Grid route tests. | 5A.1 regression, 5A.2 |
| `tests/test_routes/test_salary.py` | 2847 | Salary route tests. | 5A.3 regression |
| `tests/test_routes/test_categories.py` | 498 | Category route tests. | 5A.4, 5A.5 |
| `tests/test_routes/test_templates.py` | 862 | Transaction template route tests. | 5A.5 |
| `tests/test_routes/test_transfers.py` | 1312 | Transfer route tests. | 5A.5 |
| `tests/test_routes/test_accounts_dashboard.py` | 278 | Accounts dashboard tests. | 5A.5 |
| `tests/conftest.py` | 1035 | Test fixtures. | 5A.5 (new fixtures) |

---

## Task Dependency Analysis and Commit Ordering

### Dependency Graph

```
5A.1 (Effective Amount Fix)      -- independent
5A.2 (Grid Item Sub-Headers)     -- independent
5A.3 (Salary Button Cleanup)     -- independent
5A.4 (Category Mgmt Overhaul)  --+
                                  |
                                  +--> 5A.5 (CRUD Consistency)
                                       (modifies same category UI/routes)
```

### Commit Order Rationale

1. **5A.1 first:** Core calculation fix. This is the most impactful change and a prerequisite
   for Section 5. Running it first ensures the balance calculator is correct for all subsequent
   testing.
2. **5A.2 second:** Grid readability improvement. Independent of 5A.1 but benefits from the
   corrected calculations being in place during manual verification.
3. **5A.3 third:** Smallest change. Template-only cleanup. No dependencies.
4. **5A.4 fourth:** Category management improvements. Must come before 5A.5 because 5A.5
   modifies the same category routes and templates (adding archive/delete capabilities).
5. **5A.5 last:** Most complex task. Depends on 5A.4's category changes being in place.
   Multiple sub-commits spanning four entity types.

### Phase Grouping

**Phase 1 -- Regression Baseline:** Commit #0
**Phase 2 -- Core Calculation:** 5A.1 (1 commit)
**Phase 3 -- Grid Readability:** 5A.2 (1 commit)
**Phase 4 -- Template Cleanup:** 5A.3 (1 commit)
**Phase 5 -- Category Management:** 5A.4 (2 commits)
**Phase 6 -- CRUD Consistency:** 5A.5 (5 commits)

---

## Commit #0: Regression Baseline Tests

### A. Commit message

```
test(section5a): add regression baseline for balance calculator and category management
```

### B. Problem statement

Section 5A modifies the balance calculator, grid template, salary listing, category management,
and CRUD lifecycle for four entity types. Before any changes are made, a regression test suite
must verify the existing behavior of these systems. This is the safety net -- if any future
commit breaks existing behavior, these tests catch it immediately.

### C. Files modified

- `tests/test_services/test_balance_calculator.py` -- Add regression test class.
- `tests/test_routes/test_grid.py` -- Add regression test class.
- `tests/test_routes/test_categories.py` -- Add regression test class.
- `tests/test_routes/test_templates.py` -- Add regression test class.

### D. Implementation approach

Add a `TestBalanceCalculatorRegressionBaseline` class to `test_balance_calculator.py` that
verifies the CURRENT behavior (using estimated_amount only) so the 5A.1 change is clearly
intentional:

1. Create a transaction with `estimated_amount=100, actual_amount=150, status=Projected`.
2. Run `calculate_balances()`.
3. Assert the balance uses 100 (estimated), not 150 (actual). **This test will be updated in
   commit 5A.1 to assert the new correct behavior (150).**

Add a `TestGridSubtotalsRegressionBaseline` class to `test_grid.py`:

1. Create a period with one income transaction (estimated=500, actual=400, status=Projected).
2. GET the grid.
3. Assert subtotals use effective_amount (currently returns estimated for Projected).
   **This test will be updated in commit 5A.1.**

Add a `TestCategoryManagementBaseline` class to `test_categories.py`:

1. Create a category.
2. Verify it appears in the settings page.
3. Delete it.
4. Verify it is removed.
5. Create a category, assign a transaction to it, attempt to delete -- verify blocked.

Add a `TestTemplateLifecycleBaseline` class to `test_templates.py`:

1. Create a transaction template.
2. Deactivate it -- verify is_active=False and projected transactions soft-deleted.
3. Reactivate it -- verify is_active=True and transactions restored.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-0-1 | test_balance_uses_estimated_for_projected | Txn: est=100, act=150, Projected | calculate_balances | Balance reflects 100 | New |
| C-0-2 | test_balance_uses_actual_for_settled | Txn: est=100, act=150, Paid | calculate_balances | Txn excluded (settled = already in anchor) | New |
| C-0-3 | test_grid_subtotals_use_effective_amount | Txn: est=500, act=400, Projected | GET grid | Subtotal = 500 (current behavior) | New |
| C-0-4 | test_category_create_and_delete | None | POST create, POST delete | Created then removed | New |
| C-0-5 | test_category_delete_blocked_when_in_use | Category with template | POST delete | Warning flash, not deleted | New |
| C-0-6 | test_template_deactivate_reactivate | Active template + projected txns | POST deactivate, POST reactivate | is_active toggles, txns soft-deleted then restored | New |

### F. Manual verification steps

No manual verification -- automated regression tests.

### G. Downstream effects

None. Test-only commit.

### H. Rollback notes

Test-only commit. Trivially revertable.

---

## Task 5A.1: Grid -- Estimated vs. Actual Calculation

### Commit 5A.1: Fix balance calculator and effective_amount to use actual amounts

### A. Commit message

```
fix(balance): use actual_amount when populated in balance projections and effective_amount
```

### B. Problem statement

The balance calculator (`app/services/balance_calculator.py`) computes projected balances
using `txn.estimated_amount` directly (lines 298, 326, and 255), ignoring `actual_amount`
entirely. The `Transaction.effective_amount` property (line 122) also returns `estimated_amount`
for Projected-status transactions even when `actual_amount` is populated. This means when a user
enters a known actual amount on a still-projected transaction (e.g., they received a bill for
$350 but the estimate was $300), the balance projection uses $300 instead of $350. The user sees
incorrect available funds.

### C. Files modified

- `app/models/transaction.py` -- Simplify `effective_amount` property to prefer `actual_amount`
  for all active statuses.
- `app/services/balance_calculator.py` -- Replace `txn.estimated_amount` with
  `txn.effective_amount` in `_sum_remaining()`, `_sum_all()`, and
  `calculate_balances_with_amortization()`.
- `tests/test_services/test_balance_calculator.py` -- Add tests for actual_amount usage;
  update regression baseline test from Commit #0.
- `tests/test_routes/test_grid.py` -- Update regression baseline test from Commit #0.

### D. Implementation approach

**Model change (`transaction.py` lines 122-136):**

Current:

```python
@property
def effective_amount(self):
    if self.is_deleted:
        return Decimal("0")
    if self.status and self.status.excludes_from_balance:
        return Decimal("0")
    if self.status and self.status.is_settled:
        return self.actual_amount if self.actual_amount is not None else self.estimated_amount
    return self.estimated_amount
```

Changed to:

```python
@property
def effective_amount(self):
    """Return the amount used in balance calculations.

    - is_deleted: 0 (soft-deleted transactions contribute nothing)
    - excludes_from_balance (Credit, Cancelled): 0
    - All active statuses: actual_amount if populated, else estimated_amount.
      This ensures that when a user enters a known actual on a still-projected
      transaction, balance projections reflect reality.
    """
    if self.is_deleted:
        return Decimal("0")
    if self.status and self.status.excludes_from_balance:
        return Decimal("0")
    return self.actual_amount if self.actual_amount is not None else self.estimated_amount
```

The `is_settled` branch is removed because the logic is now identical for all active statuses:
prefer actual over estimated. The null check on `actual_amount` correctly distinguishes null
(no actual entered -- use estimate) from zero (actual is zero -- use zero).

**Balance calculator changes:**

In `_sum_remaining()` (line 298), replace:
```python
amount = Decimal(str(txn.estimated_amount))
```
with:
```python
amount = txn.effective_amount
```

In `_sum_all()` (line 326), same replacement.

In `calculate_balances_with_amortization()` (line 255), replace:
```python
total_payment_in += Decimal(str(txn.estimated_amount))
```
with:
```python
total_payment_in += txn.effective_amount
```

**Why `txn.effective_amount` without Decimal wrapping:** The property returns
`self.actual_amount` or `self.estimated_amount`, both of which are `db.Column(db.Numeric(12,2))`
-- SQLAlchemy returns these as Python `Decimal` objects. The `Decimal(str(...))` wrapping in the
current code is redundant. The grid subtotals (grid.py lines 233-234) already use
`txn.effective_amount` without wrapping.

**Update docstrings:** Update the module docstring of `balance_calculator.py` (line 14, which
says `projected -> estimated_amount`) to say `projected -> effective_amount (actual if
populated, else estimated)`. Update the `_sum_remaining` and `_sum_all` docstrings to reflect
the change.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.1-1 | test_balance_uses_actual_for_projected_when_populated | Txn: est=100, act=150, Projected | calculate_balances | Balance reflects 150 | Mod (C-0-1) |
| C-5A.1-2 | test_balance_uses_estimated_when_actual_is_null | Txn: est=100, act=None, Projected | calculate_balances | Balance reflects 100 | New |
| C-5A.1-3 | test_balance_uses_zero_actual_not_estimated | Txn: est=100, act=0, Projected | calculate_balances | Balance reflects 0 (waived fee) | New |
| C-5A.1-4 | test_mixed_actual_and_estimated_in_same_period | Period with 2 txns: one act=200, one act=None est=50 | calculate_balances | Correct sum: 200+50=250 | New |
| C-5A.1-5 | test_effective_amount_deleted_returns_zero | Txn: est=100, act=150, is_deleted=True | txn.effective_amount | 0 | New |
| C-5A.1-6 | test_effective_amount_excluded_returns_zero | Txn: est=100, act=150, status=Credit | txn.effective_amount | 0 | New |
| C-5A.1-7 | test_effective_amount_settled_with_actual | Txn: est=100, act=80, status=Paid | txn.effective_amount | 80 | New |
| C-5A.1-8 | test_effective_amount_settled_no_actual | Txn: est=100, act=None, status=Paid | txn.effective_amount | 100 | New |
| C-5A.1-9 | test_effective_amount_projected_with_actual | Txn: est=100, act=150, status=Projected | txn.effective_amount | 150 | New |
| C-5A.1-10 | test_amortization_balance_uses_effective | Shadow income txn: est=1000, act=1200 | calculate_balances_with_amortization | Payment detected as 1200 | New |
| C-5A.1-11 | test_grid_subtotals_reflect_actual | Txn: est=500, act=400, Projected | GET grid | Subtotal = 400 | Mod (C-0-3) |
| C-5A.1-12 | test_balance_decimal_precision | Txn: est=100.50, act=99.99, Projected | calculate_balances | Balance correct to 2 decimal places | New |

### F. Manual verification steps

1. Create a transaction with estimated amount $300.
2. Observe the projected end balance.
3. Enter an actual amount of $350 (leave status as Projected).
4. Refresh the grid.
5. Verify the projected end balance changed by $50 (reflecting the higher actual).
6. Enter actual amount $0 (waived fee).
7. Verify the balance reflects $0, not the $300 estimate.

### G. Downstream effects

- Grid subtotals already use `txn.effective_amount` -- they automatically gain correct
  behavior from the property fix (no code change in grid.py).
- Chart data service's `_calculate_account_balances()` calls `balance_calculator` -- it
  automatically gains correct behavior.
- Chart data service's `get_spending_by_category()` (line 401) already uses the
  `actual_amount if actual_amount is not None else estimated_amount` pattern inline -- no
  change needed.
- Chart data service's `get_budget_vs_actuals()` intentionally separates estimated and actual
  for comparison -- no change needed.
- `credit_workflow.py` line 101 already uses the correct conditional pattern -- no change
  needed.
- The existing tests that check balance calculation with `estimated_amount` will need review:
  any test that creates a Projected transaction with `actual_amount` populated will now see
  different results. The regression baseline test (C-0-1) is intentionally updated.

### H. Rollback notes

No migration. Model property + service logic change. Revertable by restoring the old
`effective_amount` property and reverting the three `estimated_amount` -> `effective_amount`
substitutions in the balance calculator.

---

## Task 5A.2: Grid -- Category Item Sub-Headers

### Commit 5A.2: Add category item sub-headers to grid rows

### A. Commit message

```
feat(grid): add category item sub-headers for visual grouping within category groups
```

### B. Problem statement

The grid sorts transactions by `(group_name, item_name, txn_name)` (`app/routes/grid.py`
line 126), but only renders group-level headers (`app/templates/grid/grid.html` lines 113-121
for income, 189-197 for expenses). The Category Item Name is invisible to the user, making
the sort order appear random within each group. For example, "Auto" group transactions sorted
by item (Car Insurance, Car Payment, Gas) appear interleaved without context.

### C. Files modified

- `app/templates/grid/grid.html` -- Add item-level sub-header rows in both income and expense
  sections.
- `tests/test_routes/test_grid.py` -- Add tests for sub-header rendering.

### D. Implementation approach

**Template change (`grid.html`):**

In the income section (currently lines 111-121), add item tracking alongside the existing
group tracking. Currently:

```html
{% set ns = namespace(current_group='') %}
{% for rk in income_row_keys %}
  {% if rk.group_name != ns.current_group %}
    {% set ns.current_group = rk.group_name %}
    <tr class="group-header-row">...</tr>
  {% endif %}
  <tr>...</tr>
{% endfor %}
```

Changed to:

```html
{% set ns = namespace(current_group='', current_item='') %}
{% for rk in income_row_keys %}
  {% if rk.group_name != ns.current_group %}
    {% set ns.current_group = rk.group_name %}
    {% set ns.current_item = '' %}
    <tr class="group-header-row">
      <td class="sticky-col text-muted small fw-semibold"
          colspan="{{ periods|length + 1 }}">
        {{ rk.group_name }}
      </td>
    </tr>
  {% endif %}
  {% if rk.item_name != ns.current_item %}
    {% set ns.current_item = rk.item_name %}
    <tr class="item-subheader-row">
      <td class="sticky-col ps-3 text-muted small"
          colspan="{{ periods|length + 1 }}">
        {{ rk.item_name }}
      </td>
    </tr>
  {% endif %}
  <tr>...</tr>
{% endfor %}
```

Apply the same change to the expense section (currently lines 187-197), using `ns2`.

**Styling:** The `item-subheader-row` class should be visually lighter than `group-header-row`.
Add to the grid CSS (likely in `static/css/grid.css` or inline):

```css
.item-subheader-row td {
  font-size: 0.8rem;
  padding-top: 0.15rem;
  padding-bottom: 0.15rem;
  border-bottom: none;
}
```

The `ps-3` (padding-start: 1rem) indents the item name under the group header. The `text-muted`
and `small` classes make it visually subordinate.

**No route changes needed.** The `RowKey` namedtuple already includes `item_name` (grid.py
line 38), and the sort key already orders by `(group_name, item_name, txn_name)` (line 126).
The template simply needs to detect when `item_name` changes.

**Single-item groups:** When a category group has only one item, the sub-header still renders.
This provides context ("Housing" group, "Rent" item) and maintains consistency. The visual
cost is minimal given the lightweight styling.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.2-1 | test_item_subheader_renders_for_multiple_items | Auto group with Car Insurance + Gas items | GET grid | Two item sub-headers within Auto group | New |
| C-5A.2-2 | test_item_subheader_renders_for_single_item | Housing group with Rent item only | GET grid | One item sub-header under Housing | New |
| C-5A.2-3 | test_item_subheaders_appear_in_both_sections | Income + expense items | GET grid | Sub-headers in both income and expense sections | New |
| C-5A.2-4 | test_group_change_resets_item_tracking | Two groups each with items | GET grid | Item sub-headers reset at each group boundary | New |
| C-5A.2-5 | test_subheader_content_matches_category_item | Category item "Car Payment" | GET grid | Sub-header text = "Car Payment" | New |

### F. Manual verification steps

1. Ensure you have categories with multiple items per group (e.g., "Auto: Car Insurance",
   "Auto: Car Payment", "Auto: Gas").
2. Load the grid.
3. Verify: "Auto" group header appears, followed by "Car Insurance" sub-header, then
   transaction rows for that item, then "Car Payment" sub-header, etc.
4. Verify the sub-headers are visually distinct from group headers (smaller, muted, indented).
5. Check a group with a single item -- verify the sub-header still appears.

### G. Downstream effects

None. Template-only change affecting visual presentation. No route, service, or model changes.
The grid subtotals, balance row, and cell rendering are not affected.

### H. Rollback notes

Template-only change. Trivially revertable. No migration.

---

## Task 5A.3: Salary Profile -- Remove Duplicate Buttons

### Commit 5A.3: Remove redundant full-width buttons from salary listing

### A. Commit message

```
fix(salary): remove duplicate View Breakdown and View Projection buttons from /salary
```

### B. Problem statement

The salary listing page (`app/templates/salary/list.html`) displays View Breakdown and View
Projection buttons twice: once as full-width buttons nested inside the Name column (lines 46-55)
and again as compact icon buttons in the Actions column (lines 84-91). Task 4.11 moved buttons
to a prominent position on the edit page but left the listing page duplicates intact.

### C. Files modified

- `app/templates/salary/list.html` -- Remove the full-width buttons from the Name column.

### D. Implementation approach

Remove lines 46-55 from `salary/list.html`:

```html
{# REMOVE THIS BLOCK #}
<div class="d-flex gap-2 mt-1">
  <a href="{{ url_for('salary.breakdown_current', profile_id=p.id) }}"
     class="btn btn-sm btn-outline-info">
    <i class="bi bi-receipt"></i> View Breakdown
  </a>
  <a href="{{ url_for('salary.projection', profile_id=p.id) }}"
     class="btn btn-sm btn-outline-primary">
    <i class="bi bi-graph-up"></i> View Projection
  </a>
</div>
```

The Name column `<td>` retains `{{ p.name }}` only. The Actions column (lines 79-101) keeps
its Edit, Breakdown, Projection, and Deactivate icon buttons unchanged.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.3-1 | test_salary_listing_no_duplicate_buttons | Active salary profile | GET /salary | Breakdown/Projection icons in Actions only; no full-width buttons in Name cell | New |
| C-5A.3-2 | test_salary_action_icons_still_functional | Active salary profile | GET /salary | Action icons link to correct URLs | New |

### F. Manual verification steps

1. Navigate to `/salary`.
2. Verify: each salary profile row shows the name in the Name column without buttons underneath.
3. Verify: the Actions column shows Edit, Breakdown, Projection, and Deactivate icons.
4. Click each icon to verify it still links to the correct page.

### G. Downstream effects

None. Template-only change. No route, service, or model changes.

### H. Rollback notes

Template-only change. Trivially revertable. No migration.

---

## Task 5A.4: Settings -- Category Management Overhaul

### Overview

Three improvements to the category management UI: (1) edit capability for renaming and
re-parenting, and (2) replace the free-text group field with a dropdown. Two sub-commits:

1. Add edit route, schema, and template.
2. Replace the add form group field with a dropdown.

---

### Commit 5A.4-1: Add category edit capability with re-parenting

### A. Commit message

```
feat(categories): add edit endpoint for renaming and re-parenting category items
```

### B. Problem statement

The category management page (`app/routes/categories.py`) supports create and delete only.
If a user misspells a category item name or wants to move an item to a different group, they
must delete the category (losing transaction associations) and recreate it. Edit capability
allows renaming the item and/or changing its group_name to re-parent it.

### C. Files modified

- `app/routes/categories.py` -- Add `edit_category()` route.
- `app/schemas/validation.py` -- Add `CategoryEditSchema`.
- `app/templates/settings/_categories.html` -- Add edit icon and inline edit form per item.
- `app/templates/categories/_category_row.html` -- Update row partial to include edit.
- `tests/test_routes/test_categories.py` -- Add edit tests.

### D. Implementation approach

**New schema (`validation.py`):**

```python
class CategoryEditSchema(BaseSchema):
    """Validates POST data for editing a category."""

    group_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    item_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
```

**New route (`categories.py`):**

```python
@categories_bp.route("/categories/<int:category_id>/edit", methods=["POST"])
@login_required
def edit_category(category_id):
    """Edit a category item name and/or group assignment."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    errors = _edit_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    data = _edit_schema.load(request.form)
    new_group = data["group_name"].strip()
    new_item = data["item_name"].strip()

    if not new_group or not new_item:
        flash("Category names cannot be blank.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    # Check for duplicate: another category with the same group + item
    # for this user.
    duplicate = (
        db.session.query(Category)
        .filter(
            Category.user_id == current_user.id,
            Category.group_name == new_group,
            Category.item_name == new_item,
            Category.id != category_id,
        )
        .first()
    )
    if duplicate:
        flash(f"Category '{new_group}: {new_item}' already exists.", "warning")
        return redirect(url_for("settings.show", section="categories"))

    old_name = category.display_name
    category.group_name = new_group
    category.item_name = new_item
    db.session.commit()

    logger.info("Edited category: %s -> %s", old_name, category.display_name)
    flash(f"Category updated to '{category.display_name}'.", "success")
    return redirect(url_for("settings.show", section="categories"))
```

**Template change (`_categories.html`):**

Each category item in the list gets an edit icon button that toggles an inline edit form:

```html
<li class="list-group-item">
  <div class="d-flex justify-content-between align-items-center"
       id="cat-display-{{ cat.id }}">
    {{ cat.item_name }}
    <div>
      <button type="button" class="btn btn-sm btn-outline-secondary"
              onclick="document.getElementById('cat-edit-{{ cat.id }}').classList.toggle('d-none');
                       this.closest('[id^=cat-display]').classList.toggle('d-none');">
        <i class="bi bi-pencil"></i>
      </button>
      {# existing delete form #}
    </div>
  </div>
  <form method="POST" id="cat-edit-{{ cat.id }}"
        action="{{ url_for('categories.edit_category', category_id=cat.id) }}"
        class="d-none mt-2">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <div class="mb-2">
      <label class="form-label small">Group</label>
      <input type="text" name="group_name" class="form-control form-control-sm"
             value="{{ cat.group_name }}" required>
    </div>
    <div class="mb-2">
      <label class="form-label small">Item Name</label>
      <input type="text" name="item_name" class="form-control form-control-sm"
             value="{{ cat.item_name }}" required>
    </div>
    <div class="d-flex gap-1">
      <button type="submit" class="btn btn-sm btn-primary">Save</button>
      <button type="button" class="btn btn-sm btn-outline-secondary"
              onclick="this.closest('form').classList.add('d-none');
                       document.getElementById('cat-display-{{ cat.id }}').classList.remove('d-none');">
        Cancel
      </button>
    </div>
  </form>
</li>
```

**Transaction associations are preserved:** Transactions reference categories by `category_id`
(FK to `budget.categories.id`). Renaming or re-parenting changes `group_name` and `item_name`
on the Category row but does not change the row's `id`. All linked transactions, templates, and
transfers continue pointing to the same category row.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.4-1 | test_edit_category_rename | Category "Auto: Gas" | POST edit item_name="Fuel" | Category renamed to "Auto: Fuel" | New |
| C-5A.4-2 | test_edit_category_reparent | Category "Auto: Toll Pass" | POST edit group_name="Travel" | Category is now "Travel: Toll Pass" | New |
| C-5A.4-3 | test_edit_category_rename_and_reparent | Category "Auto: Gas" | POST edit group_name="Travel", item_name="Fuel" | Category is "Travel: Fuel" | New |
| C-5A.4-4 | test_edit_category_preserves_transaction_association | Category with linked txn | POST edit item_name="New Name" | Transaction still references same category_id | New |
| C-5A.4-5 | test_edit_category_duplicate_blocked | Two categories, edit one to match the other | POST edit | Warning flash, no change | New |
| C-5A.4-6 | test_edit_category_blank_name_rejected | Category | POST edit item_name="" | Error flash, no change | New |
| C-5A.4-7 | test_edit_category_idor | Other user's category | POST edit | "not found" flash | New |
| C-5A.4-8 | test_edit_category_nonexistent | Invalid category_id | POST edit | "not found" flash | New |

### F. Manual verification steps

1. Navigate to Settings > Categories.
2. Click the edit icon on a category item.
3. Verify: inline form appears with current group and item pre-populated.
4. Change the item name, click Save.
5. Verify: the item name is updated in the list. Navigate to the grid and verify transactions
   using this category still display correctly.
6. Edit a category and change its group. Verify it moves to the new group in the list.

### G. Downstream effects

- Grid display: transactions using the edited category will show the new group_name/item_name.
  The RowKey is built per render from current Category data, so it automatically reflects the
  rename.
- Grid sort order: if the item was re-parented to a different group, it will sort under the
  new group header and sub-header (after 5A.2).

### H. Rollback notes

No migration. Route + schema + template change. Revertable.

---

### Commit 5A.4-2: Replace category group free-text with dropdown

### A. Commit message

```
feat(categories): replace free-text group field with dropdown for add and edit forms
```

### B. Problem statement

Adding a new category item to an existing group requires the user to type the group name
exactly. Any typo or case mismatch creates a new group instead of adding to the existing one.
There is no dropdown, autocomplete, or selection mechanism. The edit form (from commit 5A.4-1)
has the same issue.

### C. Files modified

- `app/routes/settings.py` -- Pass `group_names` list to template context.
- `app/templates/settings/_categories.html` -- Replace free-text group field with
  select dropdown + "Add new group" option in both add and edit forms.
- `tests/test_routes/test_categories.py` -- Add dropdown-related tests.

### D. Implementation approach

**Route change (`settings.py`):**

In the `section == "categories"` branch (line 66), after loading categories, compute distinct
group names:

```python
group_names = sorted(set(cat.group_name for cat in categories))
```

Pass `group_names` to `render_template()`.

**Template change (`_categories.html`):**

Replace the free-text group input in the add form (currently line 38):

```html
<div class="mb-3">
  <label for="group_name" class="form-label">Group</label>
  <select id="group_select" class="form-select" onchange="
    var custom = document.getElementById('group_custom');
    var input = document.getElementById('group_name');
    if (this.value === '__new__') {
      custom.classList.remove('d-none');
      input.value = '';
      input.focus();
    } else {
      custom.classList.add('d-none');
      input.value = this.value;
    }
  ">
    {% for g in group_names %}
    <option value="{{ g }}">{{ g }}</option>
    {% endfor %}
    <option value="__new__">+ Add new group</option>
  </select>
  <div id="group_custom" class="mt-2 d-none">
    <input type="text" class="form-control" placeholder="New group name"
           oninput="document.getElementById('group_name').value = this.value;">
  </div>
  <input type="hidden" id="group_name" name="group_name"
         value="{{ group_names[0] if group_names else '' }}">
</div>
```

**Behavior:**
- Default: dropdown shows existing group names. Selecting one sets the hidden input value.
- "Add new group" option: reveals a text field. Typing in it updates the hidden input.
- The form submits `group_name` from the hidden input, which is always set correctly.
- If no groups exist yet, the dropdown shows only "+ Add new group" and the text field is
  visible by default.

**Edit form:** Apply the same dropdown pattern to the inline edit form from 5A.4-1. Pre-select
the current group. The dropdown options come from the same `group_names` list.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.4-9 | test_add_form_shows_group_dropdown | 2 existing groups | GET settings/categories | Dropdown contains both groups + "Add new group" | New |
| C-5A.4-10 | test_add_to_existing_group_via_dropdown | Existing group "Auto" | POST create with group_name="Auto" | Item added to existing Auto group, no duplicate group | New |
| C-5A.4-11 | test_add_with_new_group | No "Travel" group | POST create with group_name="Travel" | New Travel group created with the new item | New |
| C-5A.4-12 | test_edit_form_preselects_current_group | Category in "Auto" group | GET settings/categories | Edit form dropdown pre-selects "Auto" | New |
| C-5A.4-13 | test_no_existing_groups | No categories | GET settings/categories | Only "+ Add new group" option, text field visible | New |

### F. Manual verification steps

1. Navigate to Settings > Categories with existing categories in multiple groups.
2. In the Add Category form, verify the Group field is a dropdown with existing groups.
3. Select an existing group, enter an item name, submit. Verify the item is added to the
   correct group.
4. Select "+ Add new group", enter a new group name, enter an item name, submit. Verify a
   new group is created.
5. Click Edit on an existing category. Verify the group dropdown pre-selects the current group.
6. Change the group via dropdown to an existing group. Save. Verify the item moved to the
   target group.

### G. Downstream effects

None beyond what 5A.4-1 already established. The dropdown is a UX improvement over free text;
the submitted data is identical.

### H. Rollback notes

No migration. Route + template change. Revertable.

---

## Task 5A.5: CRUD Consistency -- Unified Delete/Archive Pattern

### Overview

Establish a consistent two-step lifecycle pattern across four entity types: transaction
templates, transfer templates, accounts, and categories.

**Lifecycle states:** Active -> Archived -> (conditionally) Permanently Deleted.

- **Archive** (always available, reversible): `is_active=False`.
- **Permanent delete** (conditional, irreversible): only when the entity has no Paid/Settled
  history.

Five sub-commits:
1. Migration to add `is_active` to categories + shared archive utility.
2. Apply pattern to transaction templates.
3. Apply pattern to transfer templates.
4. Apply pattern to accounts.
5. Apply pattern to categories.

---

### Commit 5A.5-1: Add is_active to categories and create shared archive utility

### A. Commit message

```
feat(categories): add is_active column and shared archive history-detection utility
```

### B. Problem statement

Categories have no `is_active` column (`app/models/category.py`) -- they can only be
hard-deleted (if not in use). To apply the unified archive/delete pattern, categories need
`is_active` for archive support. Additionally, the history-detection logic (determining whether
an entity is eligible for permanent deletion) needs to be shared across four entity types to
avoid duplicating query logic in each route file.

### C. Files modified

- Migration -- Add `is_active` column to `budget.categories`.
- `app/models/category.py` -- Add `is_active` column.
- `app/utils/archive_helpers.py` -- New file: history detection functions.
- `tests/test_routes/test_categories.py` -- Test that existing categories default to
  `is_active=True`.

### D. Implementation approach

**Migration:**

```python
# message: "add is_active column to categories"
def upgrade():
    op.add_column(
        'categories',
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        schema='budget',
    )

def downgrade():
    op.drop_column('categories', 'is_active', schema='budget')
```

No data backfill needed -- `server_default='true'` covers all existing rows.

**Model change (`category.py`):**

Add after `sort_order`:

```python
is_active = db.Column(db.Boolean, nullable=False, default=True, server_default='true')
```

**Shared utility (`app/utils/archive_helpers.py`):**

```python
"""
Shekel Budget App -- Archive and Delete History Helpers

Provides history-detection functions used by the unified delete/archive
pattern across transaction templates, transfer templates, accounts,
and categories. Each function answers: "Does this entity have
Paid/Settled history that prevents permanent deletion?"

These functions are pure queries -- they do not perform mutations.
"""

from app.extensions import db
from app import ref_cache
from app.enums import StatusEnum


def template_has_paid_history(template_id):
    """Check if a transaction template has any Paid or Settled transactions.

    Args:
        template_id: The TransactionTemplate.id to check.

    Returns:
        bool: True if at least one linked transaction has Paid or Settled
        status and is not soft-deleted.
    """
    from app.models.transaction import Transaction

    paid_id = ref_cache.status_id(StatusEnum.DONE)
    settled_id = ref_cache.status_id(StatusEnum.SETTLED)

    return db.session.query(
        db.session.query(Transaction).filter(
            Transaction.template_id == template_id,
            Transaction.status_id.in_([paid_id, settled_id]),
            Transaction.is_deleted.is_(False),
        ).exists()
    ).scalar()


def transfer_template_has_paid_history(template_id):
    """Check if a transfer template has any Paid or Settled transfers.

    Args:
        template_id: The TransferTemplate.id to check.

    Returns:
        bool: True if at least one linked transfer has Paid or Settled
        status and is not soft-deleted.
    """
    from app.models.transfer import Transfer

    paid_id = ref_cache.status_id(StatusEnum.DONE)
    settled_id = ref_cache.status_id(StatusEnum.SETTLED)

    return db.session.query(
        db.session.query(Transfer).filter(
            Transfer.transfer_template_id == template_id,
            Transfer.status_id.in_([paid_id, settled_id]),
            Transfer.is_deleted.is_(False),
        ).exists()
    ).scalar()


def account_has_history(account_id):
    """Check if an account has any non-deleted transactions or transfers.

    Accounts with ANY history (including Projected) are archive-only.
    This is stricter than templates because account deletion cascades
    to all related financial records.

    Args:
        account_id: The Account.id to check.

    Returns:
        bool: True if the account has any transaction or transfer history.
    """
    from app.models.transaction import Transaction

    return db.session.query(
        db.session.query(Transaction).filter(
            Transaction.account_id == account_id,
            Transaction.is_deleted.is_(False),
        ).exists()
    ).scalar()


def category_has_usage(category_id, user_id):
    """Check if a category is in use by templates or transactions.

    Args:
        category_id: The Category.id to check.
        user_id: The user who owns the category (for ownership scoping).

    Returns:
        bool: True if any templates or transactions reference this category.
    """
    from app.models.transaction_template import TransactionTemplate
    from app.models.transaction import Transaction
    from app.models.pay_period import PayPeriod

    has_templates = db.session.query(
        db.session.query(TransactionTemplate).filter_by(
            category_id=category_id, user_id=user_id,
        ).exists()
    ).scalar()

    if has_templates:
        return True

    return db.session.query(
        db.session.query(Transaction)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.category_id == category_id,
        ).exists()
    ).scalar()
```

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.5-1 | test_existing_categories_default_active | Seed categories | Query | All is_active=True | New |
| C-5A.5-2 | test_template_has_paid_history_true | Template with Paid transaction | Call helper | True | New |
| C-5A.5-3 | test_template_has_paid_history_false | Template with only Projected txns | Call helper | False | New |
| C-5A.5-4 | test_transfer_template_has_paid_history_true | Transfer template with Paid transfer | Call helper | True | New |
| C-5A.5-5 | test_transfer_template_has_paid_history_false | Transfer template with only Projected | Call helper | False | New |
| C-5A.5-6 | test_account_has_history_true | Account with any transaction | Call helper | True | New |
| C-5A.5-7 | test_account_has_history_false | Account with no transactions | Call helper | False | New |
| C-5A.5-8 | test_category_has_usage_true | Category with template | Call helper | True | New |
| C-5A.5-9 | test_category_has_usage_false | Category with no refs | Call helper | False | New |
| C-5A.5-10 | test_category_has_usage_scoped_to_user | Other user's template uses cat | Call helper for this user | False | New |

### F. Manual verification steps

No UI changes in this commit. Verify the migration applies and rolls back cleanly:

```bash
flask db upgrade
flask db downgrade
flask db upgrade
```

### G. Downstream effects

- Existing category queries that don't filter on `is_active` will continue to work -- the
  default is `True`, so all existing categories remain active.
- The settings page category query (`settings.py` line 67) does not filter on `is_active` --
  it will need updating in commit 5A.5-5 to show archived categories separately.

### H. Rollback notes

**Migration required.** Downgrade drops the `is_active` column from categories. The archive
utility file can be deleted. No data loss.

---

### Commit 5A.5-2: Apply archive/delete pattern to transaction templates

### A. Commit message

```
feat(templates): add permanent delete and rename deactivate to archive for transaction templates
```

### B. Problem statement

Transaction templates can be deactivated and reactivated (`app/routes/templates.py` lines
310-378) but cannot be permanently deleted. Templates created by mistake persist indefinitely.
The UI labels say "Deactivate/Reactivate" rather than "Archive/Unarchive". The listing page
shows all templates in a single table with muted rows for inactive items, rather than
separating active and archived.

### C. Files modified

- `app/routes/templates.py` -- Add `hard_delete_template()` route. Rename endpoints and flash
  messages from "deactivate/reactivate" to "archive/unarchive". Update `list_templates()` to
  pass separated lists.
- `app/templates/templates/list.html` -- Add archived section, rename button labels, add
  delete button.
- `tests/test_routes/test_templates.py` -- Add delete tests.

### D. Implementation approach

**New route (`templates.py`):**

```python
@templates_bp.route("/templates/<int:template_id>/hard-delete", methods=["POST"])
@login_required
def hard_delete_template(template_id):
    """Permanently delete a template (only if no Paid/Settled history)."""
    template = db.session.get(TransactionTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transaction not found.", "danger")
        return redirect(url_for("templates.list_templates"))

    if archive_helpers.template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently deleted. "
            "It has been archived instead.",
            "warning",
        )
        if template.is_active:
            template.is_active = False
            # Soft-delete projected transactions (same as archive).
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            db.session.query(Transaction).filter(
                Transaction.template_id == template.id,
                Transaction.status_id == projected_id,
                Transaction.is_deleted.is_(False),
            ).update({"is_deleted": True}, synchronize_session="fetch")
            db.session.commit()
        return redirect(url_for("templates.list_templates"))

    # No history -- safe to permanently delete.
    # First delete all linked transactions (only Projected should remain,
    # but delete unconditionally for safety).
    db.session.query(Transaction).filter(
        Transaction.template_id == template.id,
    ).delete(synchronize_session="fetch")

    db.session.delete(template)
    db.session.commit()

    flash(f"Recurring transaction '{template.name}' permanently deleted.", "info")
    return redirect(url_for("templates.list_templates"))
```

**Rename existing endpoints:** Update flash messages in `delete_template()` (the archive
endpoint) from "deactivated" to "archived", and in `reactivate_template()` from "reactivated"
to "unarchived".

**List route update:** Separate active and archived templates:

```python
active_templates = [t for t in templates if t.is_active]
archived_templates = [t for t in templates if not t.is_active]
```

Pass both to the template.

**Template update (`list.html`):**

- Active templates table: shows Archive and Delete buttons.
  - Archive: POST to the existing deactivate endpoint.
  - Delete: POST to the new hard-delete endpoint with
    `data-confirm="Permanently delete this recurring transaction? This cannot be undone."`.
- Collapsed "Archived" section below: shows archived templates with Unarchive and Delete
  buttons.
  - Uses Bootstrap collapse component, collapsed by default.
  - Header: "Archived (N)" with expand/collapse toggle.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.5-11 | test_hard_delete_template_no_history | Template with only Projected txns | POST hard-delete | Template and txns permanently removed | New |
| C-5A.5-12 | test_hard_delete_template_with_history | Template with Paid transaction | POST hard-delete | Blocked, archived instead | New |
| C-5A.5-13 | test_hard_delete_template_already_archived | Archived template, no history | POST hard-delete | Permanently deleted | New |
| C-5A.5-14 | test_hard_delete_template_idor | Other user's template | POST hard-delete | "not found" flash | New |
| C-5A.5-15 | test_list_separates_active_and_archived | 2 active, 1 archived template | GET list | Active section has 2, archived section has 1 | New |
| C-5A.5-16 | test_archive_label_in_flash | Active template | POST archive | Flash says "archived" not "deactivated" | Mod |

### F. Manual verification steps

1. Create a transaction template. Archive it. Verify it moves to the Archived section.
2. Unarchive it. Verify it returns to the Active section.
3. Create another template (no transactions generated). Click Delete. Confirm. Verify it
   is permanently removed.
4. Create a template, generate transactions, mark one as Paid. Try to Delete. Verify it is
   blocked and archived instead with an explanatory message.

### G. Downstream effects

- Permanently deleting a template removes its `id` from the `template_id` FK on any remaining
  transactions. The FK is `ON DELETE SET NULL`, so orphaned transactions get `template_id=NULL`.
  However, since we only allow deletion when no Paid/Settled history exists, the only remaining
  transactions would be Projected ones, which we explicitly delete first.
- Recurrence engine will no longer generate for deleted templates (they no longer exist).

### H. Rollback notes

No migration. Route + template change. Revert removes the hard-delete capability; existing
data is not affected.

---

### Commit 5A.5-3: Apply archive/delete pattern to transfer templates

### A. Commit message

```
feat(transfers): add permanent delete and rename deactivate to archive for transfer templates
```

### B. Problem statement

Same UX gap as transaction templates, but transfer templates have additional complexity:
deletion must respect the five transfer invariants from Section 3A. Shadow transactions must
never be orphaned, and all mutations must flow through the transfer service.

### C. Files modified

- `app/routes/transfers.py` -- Add `hard_delete_transfer_template()` route. Rename flash
  messages. Update `list_transfer_templates()` to pass separated lists.
- `app/templates/transfers/list.html` -- Add archived section, rename labels, add delete
  button.
- `tests/test_routes/test_transfers.py` -- Add delete tests.

### D. Implementation approach

**New route (`transfers.py`):**

```python
@transfers_bp.route("/transfers/<int:template_id>/hard-delete", methods=["POST"])
@login_required
def hard_delete_transfer_template(template_id):
    """Permanently delete a transfer template (only if no Paid/Settled history).

    Deletion cascades through the transfer service to ensure shadow
    transaction invariants are maintained:
    1. Every transfer has exactly two linked shadow transactions.
    2. Shadow transactions are never orphaned.
    3-5. Amount/status/period parity enforced during deletion.
    """
    template = db.session.get(TransferTemplate, template_id)
    if template is None or template.user_id != current_user.id:
        flash("Recurring transfer not found.", "danger")
        return redirect(url_for("transfers.list_transfer_templates"))

    if archive_helpers.transfer_template_has_paid_history(template.id):
        flash(
            f"'{template.name}' has payment history and cannot be permanently deleted. "
            "It has been archived instead.",
            "warning",
        )
        if template.is_active:
            # Perform archive (same as existing deactivate logic).
            template.is_active = False
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            transfers_to_delete = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == template.id,
                    Transfer.status_id == projected_id,
                    Transfer.is_deleted.is_(False),
                )
                .all()
            )
            for xfer in transfers_to_delete:
                transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)
            db.session.commit()
        return redirect(url_for("transfers.list_transfer_templates"))

    # No history -- safe to permanently delete.
    # Delete all linked transfers through the transfer service (hard delete).
    # This cascade-deletes shadow transactions via ON DELETE CASCADE.
    all_transfers = (
        db.session.query(Transfer)
        .filter(Transfer.transfer_template_id == template.id)
        .all()
    )
    for xfer in all_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    db.session.delete(template)
    db.session.commit()

    flash(f"Recurring transfer '{template.name}' permanently deleted.", "info")
    return redirect(url_for("transfers.list_transfer_templates"))
```

**Transfer invariant verification for hard delete:**

1. **Two linked shadows:** Each transfer being deleted has its shadows removed by
   `transfer_service.delete_transfer(soft=False)`, which uses DB-level CASCADE. The service
   verifies CASCADE worked (existing logic, lines 636-644).
2. **Never orphaned:** Shadows are deleted WITH their parent transfer atomically.
3. **Amount/status/period parity:** Not applicable -- the transfer and both shadows are being
   removed entirely.
4. **Mutations through transfer service:** Yes -- `delete_transfer()` is the single
   enforcement point.
5. **Balance calculator queries only transactions:** After deletion, the shadow transactions
   no longer exist in `budget.transactions`, so no double-counting risk.

**List route and template updates:** Same pattern as commit 5A.5-2 (separate active/archived,
collapsed archived section).

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.5-17 | test_hard_delete_transfer_template_no_history | Template with Projected transfers | POST hard-delete | Template, transfers, and shadows permanently removed | New |
| C-5A.5-18 | test_hard_delete_transfer_template_with_history | Template with Paid transfer | POST hard-delete | Blocked, archived instead | New |
| C-5A.5-19 | test_hard_delete_preserves_shadow_invariant | Template being deleted | POST hard-delete | No orphaned shadow transactions after deletion | New |
| C-5A.5-20 | test_hard_delete_transfer_template_idor | Other user's template | POST hard-delete | "not found" flash | New |
| C-5A.5-21 | test_list_separates_active_and_archived_transfers | Mixed active/archived | GET list | Correct separation | New |

### F. Manual verification steps

1. Create a transfer template with no transfers generated.
2. Click Delete. Confirm. Verify permanently removed.
3. Create a transfer template, generate transfers, mark one as Paid.
4. Click Delete. Verify blocked and archived with explanatory message.
5. Verify no orphaned shadow transactions remain in the database after any deletion.

### G. Downstream effects

- Permanently deleting a transfer template removes its `id` from the `transfer_template_id`
  FK on linked transfers. The FK is `ON DELETE SET NULL`. But since we explicitly delete all
  linked transfers first (through the service), no orphaned references remain.
- The balance calculator is unaffected -- deleted shadow transactions no longer exist in
  `budget.transactions`.

### H. Rollback notes

No migration. Route + template change. Revert removes hard-delete capability.

---

### Commit 5A.5-4: Apply archive/delete pattern to accounts

### A. Commit message

```
feat(accounts): add permanent delete and rename deactivate to archive for accounts
```

### B. Problem statement

Accounts can be deactivated (`app/routes/accounts.py` lines 251-287) but not permanently
deleted. Accounts created by mistake persist indefinitely. The roadmap specifies accounts with
ANY history are always archive-only (stricter than templates, which only check Paid/Settled).

### C. Files modified

- `app/routes/accounts.py` -- Add `hard_delete_account()` route. Rename flash messages.
  Update `list_accounts()` to pass separated lists.
- `app/templates/accounts/list.html` -- Add archived section, rename labels, add delete
  button.
- `tests/test_routes/test_accounts_dashboard.py` -- Add delete tests.

### D. Implementation approach

**New route (`accounts.py`):**

```python
@accounts_bp.route("/accounts/<int:account_id>/hard-delete", methods=["POST"])
@login_required
def hard_delete_account(account_id):
    """Permanently delete an account (only if no transaction/transfer history)."""
    account = db.session.get(Account, account_id)
    if account is None or account.user_id != current_user.id:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.list_accounts"))

    if archive_helpers.account_has_history(account.id):
        flash(
            f"'{account.name}' has transaction history and cannot be permanently deleted. "
            "It has been archived instead.",
            "warning",
        )
        if account.is_active:
            account.is_active = False
            db.session.commit()
        return redirect(url_for("accounts.list_accounts"))

    # No history -- safe to permanently delete.
    # Check for parameter records (LoanParams, InterestParams, InvestmentParams).
    from app.models.loan_params import LoanParams
    from app.models.interest_params import InterestParams
    from app.models.investment_params import InvestmentParams

    db.session.query(LoanParams).filter_by(account_id=account_id).delete()
    db.session.query(InterestParams).filter_by(account_id=account_id).delete()
    db.session.query(InvestmentParams).filter_by(account_id=account_id).delete()

    # Delete anchor history.
    from app.models.account import AccountAnchorHistory
    db.session.query(AccountAnchorHistory).filter_by(account_id=account_id).delete()

    db.session.delete(account)
    db.session.commit()

    flash(f"Account '{account.name}' permanently deleted.", "info")
    return redirect(url_for("accounts.list_accounts"))
```

**Transfer template guard:** The existing deactivation guard (checks for active transfer
templates referencing this account, lines 260-278) must also apply to hard deletion. The
hard-delete route checks this guard first:

```python
active_transfers = (
    db.session.query(TransferTemplate)
    .filter(
        TransferTemplate.user_id == current_user.id,
        TransferTemplate.is_active.is_(True),
        db.or_(
            TransferTemplate.from_account_id == account_id,
            TransferTemplate.to_account_id == account_id,
        ),
    )
    .first()
)
if active_transfers:
    flash(
        "Cannot delete this account -- it is used by active recurring transfers. "
        "Archive or delete those recurring transfers first.",
        "warning",
    )
    return redirect(url_for("accounts.list_accounts"))
```

**Template FK guard:** Also check for transaction templates referencing this account:

```python
active_templates = (
    db.session.query(TransactionTemplate)
    .filter_by(account_id=account_id, user_id=current_user.id)
    .first()
)
if active_templates:
    flash(
        "Cannot delete this account -- it has recurring transactions. "
        "Delete those recurring transactions first.",
        "warning",
    )
    return redirect(url_for("accounts.list_accounts"))
```

**List route and template updates:** Same pattern as prior commits.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.5-22 | test_hard_delete_account_no_history | Account with no transactions | POST hard-delete | Account permanently removed | New |
| C-5A.5-23 | test_hard_delete_account_with_history | Account with transactions | POST hard-delete | Blocked, archived | New |
| C-5A.5-24 | test_hard_delete_account_with_params | Account with LoanParams, no txn history | POST hard-delete | Params deleted, account removed | New |
| C-5A.5-25 | test_hard_delete_blocked_by_transfer_templates | Account with active transfer template | POST hard-delete | Blocked with warning | New |
| C-5A.5-26 | test_hard_delete_blocked_by_transaction_templates | Account with transaction template | POST hard-delete | Blocked with warning | New |
| C-5A.5-27 | test_hard_delete_account_idor | Other user's account | POST hard-delete | "not found" flash | New |
| C-5A.5-28 | test_list_separates_active_and_archived_accounts | Mixed active/archived | GET list | Correct separation | New |

### F. Manual verification steps

1. Create a new account with no transactions or templates.
2. Click Delete. Confirm. Verify permanently removed.
3. Create an account, add a transaction to it.
4. Click Delete. Verify blocked and archived with explanatory message.
5. Verify archived account appears in the collapsed Archived section.

### G. Downstream effects

- Grid account selector: should already filter `is_active=True` via `resolve_grid_account()`.
  Deleted accounts are gone entirely, so no filter change needed.
- Transfer creation form: account dropdowns should exclude deleted accounts. Since the account
  is gone, it naturally disappears.
- `is_active` column is used by account queries throughout the codebase (27+ locations).
  Permanently deleted accounts are no longer in the DB, so all queries naturally exclude them.

### H. Rollback notes

No migration. Route + template change. Revert removes hard-delete capability.

---

### Commit 5A.5-5: Apply archive/delete pattern to categories

### A. Commit message

```
feat(categories): add archive/unarchive and permanent delete for categories
```

### B. Problem statement

Categories can only be hard-deleted (and only when not in use). There is no way to archive a
category that is in use. If a user stops using a category but has historical transactions
referencing it, the category remains visible with no way to hide it. This commit adds
archive/unarchive using the `is_active` column from commit 5A.5-1, and enhances the existing
delete to use the shared history check.

### C. Files modified

- `app/routes/categories.py` -- Add `archive_category()`, `unarchive_category()` routes.
  Update `delete_category()` to use `archive_helpers.category_has_usage()`.
- `app/routes/settings.py` -- Update category section to pass `group_names`, `active_grouped`,
  and `archived_categories` separately.
- `app/templates/settings/_categories.html` -- Add archived section, archive/unarchive
  buttons, delete button with history check.
- `tests/test_routes/test_categories.py` -- Add archive and delete tests.

### D. Implementation approach

**New routes (`categories.py`):**

```python
@categories_bp.route("/categories/<int:category_id>/archive", methods=["POST"])
@login_required
def archive_category(category_id):
    """Archive a category (hide from active views, preserve data)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    category.is_active = False
    db.session.commit()
    flash(f"Category '{category.display_name}' archived.", "info")
    return redirect(url_for("settings.show", section="categories"))


@categories_bp.route("/categories/<int:category_id>/unarchive", methods=["POST"])
@login_required
def unarchive_category(category_id):
    """Unarchive a category (return to active views)."""
    category = db.session.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.show", section="categories"))

    category.is_active = True
    db.session.commit()
    flash(f"Category '{category.display_name}' unarchived.", "success")
    return redirect(url_for("settings.show", section="categories"))
```

**Update `delete_category()`:** Replace the existing in-use check with the shared helper and
add the archive-if-in-use fallback:

```python
if archive_helpers.category_has_usage(category_id, current_user.id):
    flash(
        f"'{category.display_name}' is in use and cannot be permanently deleted. "
        "It has been archived instead.",
        "warning",
    )
    if category.is_active:
        category.is_active = False
        db.session.commit()
    return redirect(url_for("settings.show", section="categories"))

# No usage -- safe to hard-delete.
db.session.delete(category)
db.session.commit()
```

**Settings route update (`settings.py`):**

In the `section == "categories"` branch, separate active and archived:

```python
active_categories = [c for c in categories if c.is_active]
archived_categories = [c for c in categories if not c.is_active]
active_grouped = {}
for cat in active_categories:
    active_grouped.setdefault(cat.group_name, []).append(cat)
```

Pass `active_grouped`, `archived_categories`, and `group_names` to the template.

**Template update (`_categories.html`):**

Active categories table: each item gets Archive and Delete action buttons. The existing group
header cards remain. Edit form from 5A.4-1 is preserved.

Collapsed "Archived Categories" section below: shows archived categories with Unarchive and
Delete buttons. Uses Bootstrap collapse, collapsed by default.

**Grid category dropdown:** The grid's Add Transaction modal category dropdown
(`grid/grid.html` lines 306-310) loads all categories. After this change, archived categories
should be excluded from the dropdown. Update the grid route's category query to filter
`is_active=True`:

```python
categories = (
    db.session.query(Category)
    .filter_by(user_id=user_id, is_active=True)
    .order_by(Category.group_name, Category.item_name)
    .all()
)
```

Also update `_build_row_keys` to exclude archived categories from the category lookup:
```python
cat_by_id = {c.id: c for c in categories}
```

This means archived categories' transactions will still appear in the grid (the transactions
exist), but using the full category set for row key building requires loading archived
categories too for row key lookups. The simpler approach: load ALL categories for `cat_by_id`
(so existing transactions render correctly), but only pass active ones to the Add Transaction
dropdown.

### E. Test cases

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| C-5A.5-29 | test_archive_category | Active category | POST archive | is_active=False | New |
| C-5A.5-30 | test_unarchive_category | Archived category | POST unarchive | is_active=True | New |
| C-5A.5-31 | test_delete_category_no_usage | Category with no refs | POST delete | Permanently removed | Mod |
| C-5A.5-32 | test_delete_category_with_usage_archives | Category with template | POST delete | Archived instead, warning flash | New |
| C-5A.5-33 | test_archived_categories_hidden_from_settings | 2 active, 1 archived | GET settings/categories | Active grouped section shows 2; archived section shows 1 | New |
| C-5A.5-34 | test_archived_categories_hidden_from_grid_dropdown | Archived category | GET grid | Category not in Add Transaction dropdown | New |
| C-5A.5-35 | test_archived_category_transactions_still_render | Archived category with transactions | GET grid | Transactions still visible with correct category | New |
| C-5A.5-36 | test_archive_category_idor | Other user's category | POST archive | "not found" flash | New |

### F. Manual verification steps

1. Navigate to Settings > Categories.
2. Archive a category. Verify it moves to the collapsed Archived section.
3. Expand the Archived section. Unarchive the category. Verify it returns.
4. Create a new category with no transactions. Delete it. Verify permanent removal.
5. Try to delete a category in use. Verify it is archived instead with a message.
6. Navigate to the grid. Verify archived categories do not appear in the Add Transaction
   dropdown.
7. Verify transactions with archived categories still display correctly in the grid.

### G. Downstream effects

- Grid: archived categories excluded from Add Transaction modal dropdown. Existing
  transactions with archived categories continue rendering normally.
- Templates: new transaction templates should not allow selecting archived categories. The
  template creation form's category dropdown should filter `is_active=True`.
- Transfer templates: same dropdown filtering.

### H. Rollback notes

No migration (the `is_active` column was added in commit 5A.5-1). Route + template change.
Revert removes archive/unarchive capability; categories revert to hard-delete-only behavior.

---

## Risks and Unknowns

### R-1: is_active vs. is_archived design decision

**Description:** The roadmap discusses `is_archived` as a potential column. Section 5.9 plans
an `is_archived` column on accounts. This plan uses `is_active=False` as the archive state
instead.

**Rationale:** The existing `is_active` column on accounts, transaction templates, and transfer
templates already provides identical semantics to "archived": hidden from active views,
recurrence stops, data preserved, reversible. Adding a separate `is_archived` column would
create redundant state with ambiguous edge cases (`is_active=True, is_archived=True`?).
Using `is_active` avoids schema churn and leverages the 97 existing `is_active` references.

**Impact on Section 5.9:** When Section 5.9 implements paid-off loan archival, it uses the
same `is_active=False` mechanism. No new column needed. The Section 5 implementation plan
should be updated to remove the `is_archived` migration from commit 5.9-3.

**Mitigation:** Document this decision clearly. If the developer prefers `is_archived` for
semantic clarity, the plan can be adjusted -- the behavioral logic is identical.

### R-2: Cascade behavior on account hard-delete

**Description:** Hard-deleting an account requires removing associated parameter records
(LoanParams, InterestParams, InvestmentParams) and anchor history. The FK relationships use
ON DELETE RESTRICT for some and ON DELETE CASCADE for others. The hard-delete route must
explicitly delete associated records before deleting the account.

**Mitigation:** The implementation explicitly deletes LoanParams, InterestParams,
InvestmentParams, and AccountAnchorHistory before deleting the account. The history check
(`account_has_history`) gates this path -- only accounts with zero transactions reach it.
RESTRICT FKs on Transaction.account_id and TransferTemplate.from/to_account_id would block
deletion if any records exist, which the history check prevents.

### R-3: Transfer template hard-delete cascade through transfer_service

**Description:** Hard-deleting a transfer template requires deleting all linked transfers
and their shadow transactions. The transfer service's `delete_transfer(soft=False)` uses
DB CASCADE for shadow deletion and verifies no orphans remain. This is well-tested existing
behavior, but the pattern of deleting multiple transfers in a loop adds transaction risk.

**Mitigation:** The entire deletion loop runs within a single database transaction
(`db.session.commit()` only at the end). If any deletion fails, the transaction rolls back
and no data is lost.

### R-4: Grid category dropdown filtering after archive

**Description:** Archiving a category should hide it from the Add Transaction modal dropdown
in the grid. However, transactions with archived categories must still render correctly. This
requires loading archived categories for row key building but filtering them from the dropdown.

**Mitigation:** Load all categories for `cat_by_id` (used in `_build_row_keys`). Pass only
active categories to the template's dropdown. The grid route already loads categories in two
contexts (for row keys and for the modal) -- this simply filters the modal set.

### R-5: Existing tests that create inactive templates or accounts

**Description:** Existing tests may create templates or accounts with `is_active=False` and
rely on them appearing in certain views. The UI separation of active/archived could affect
test assertions.

**Mitigation:** Review existing tests during implementation. Tests that check listing pages
for inactive items should be updated to look in the archived section. This is a test-only
concern -- production behavior is correct.

---

## Opportunistic Improvements

These are small improvements noticed while reading the code that are not in the roadmap but
would be trivial to add. They should NOT be folded into Section 5A commits without developer
approval.

### O-1: Consistent Decimal wrapping in balance calculator

The balance calculator uses `Decimal(str(txn.estimated_amount))` for amounts, but
`txn.estimated_amount` is already a Python Decimal (from SQLAlchemy's Numeric type). The
wrapping is redundant. The grid subtotals (`grid.py` lines 233-234) already use
`txn.effective_amount` without wrapping. Removing the redundant `Decimal(str(...))` would
simplify the code. Estimated effort: 5 minutes.

### O-2: Category sort_order is unused in the grid

The Category model has a `sort_order` column (default 0), but the grid sorts by
`(group_name, item_name, txn_name)`, ignoring `sort_order`. If the user wants custom
ordering, `sort_order` could be incorporated into the sort key. However, this is a feature
addition, not a cleanup. Estimated effort: 10 minutes.

### O-3: Consolidate deactivate/archive endpoint URLs

Currently, the archive endpoints are named `delete_template`, `delete_transfer_template`,
and `deactivate_account`. After 5A.5 renames them to "archive", the URL paths
(`/templates/<id>/delete`, `/transfers/<id>/delete`, `/accounts/<id>/delete`) no longer
match the action. Renaming to `/archive` would improve clarity. However, URL changes
affect bookmarks and any external links. Estimated effort: 15 minutes.

---

## Test Strategy Summary

### Prerequisite regression suite (Commit #0)

6 tests that lock down existing behavior before any changes. Must pass after every subsequent
commit.

### Per-commit test cases

Total new test cases across all commits: approximately 68.

### Full suite gate

The full test suite (currently 2281 tests, expected to grow to ~2350) must pass after every
commit. Use `timeout 660 pytest -v --tb=short` as the final gate before reporting done.

### Expected test count increase per phase

| Phase | Commits | New tests (approx) |
|-------|---------|-------------------|
| Regression Baseline | 1 | 6 |
| 5A.1 (Effective Amount) | 1 | 12 |
| 5A.2 (Grid Sub-Headers) | 1 | 5 |
| 5A.3 (Salary Buttons) | 1 | 2 |
| 5A.4 (Category Mgmt, 2 commits) | 2 | 13 |
| 5A.5 (CRUD Consistency, 5 commits) | 5 | 30 |
| **Total** | **11** | **~68** |
