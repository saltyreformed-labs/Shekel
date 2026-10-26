> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Bug Investigation Report

**Date:** 2026-04-06
**Status:** Investigation only -- no code changes made

---

## Bug 1: Money Market Accounts Broken When Set as Asset Category

### Root Cause

The Money Market account type is seeded with incorrect flags. In `app/ref_seeds.py:25`:

```python
("Money Market",    "Asset",      False, False, False, False, True,  "bi-cash-stack",     None),
#                                 ^^^^^ ^^^^^  ^^^^^
#                        has_parameters  has_amortization  has_interest
```

Compare with HYSA (`app/ref_seeds.py:24`) which works correctly:

```python
("HYSA",            "Asset",      True,  False, True,  False, True,  "bi-piggy-bank",     None),
#                                 ^^^^         ^^^^
#                        has_parameters   has_interest
```

Money Market has `has_parameters=False` and `has_interest=False`. Every dispatch branch in the
application checks these flags to determine behavior. The architecture document
(`docs/account_parameter_architecture.md:518`) explicitly called for this change but it was
never applied to seed data.

### Cascading Effects

| Effect | File:Line | Check | Result for Money Market |
|---|---|---|---|
| No InterestParams auto-created | `app/routes/accounts.py:136` | `account_type.has_interest` | `False` -- skipped |
| No redirect to interest detail after creation | `app/routes/accounts.py:156` | `account_type.has_interest` | Falls through to generic list |
| Interest detail page rejects it | `app/routes/accounts.py:579` | `account_type.has_interest` | Redirect with warning |
| No detail link on savings dashboard | `app/templates/savings/dashboard.html:152-172` | Hardcoded HYSA/Checking/amortization/Investment category checks | Matches none |
| No interest projections in charts | `app/services/chart_data_service.py:223` | `has_interest AND interest_params` | Both missing |
| No interest projections on savings dashboard | `app/services/savings_dashboard_service.py:200-203` | `has_interest` | Excluded |

**Why changing category to Investment "fixes" it:** The savings dashboard template at line 167
renders a detail link for Investment/Retirement categories. If `has_parameters=True` is also
set, InvestmentParams are auto-created. But this gives Money Market **investment growth
parameters instead of interest/APY parameters** -- wrong behavior.

**Why "has_parameters" checkbox doesn't help:** Setting `has_parameters=True` alone without
`has_interest=True` causes Money Market to match the InvestmentParams branch
(`app/routes/accounts.py:143-148`: `has_parameters AND NOT has_interest AND NOT
has_amortization`), which is the wrong parameter type.

**Secondary issue:** The savings dashboard template at lines 109, 131, 157 uses hardcoded
`ACCT_TYPE_HYSA` checks instead of `has_interest` flag checks. Even with fixed seed data,
HYSA-specific icon rendering wouldn't apply to Money Market.

### Files Requiring Changes

**Approach A -- Minimal fix (seed data only):**

| File | Change |
|---|---|
| `app/ref_seeds.py:25` | Change Money Market to `has_parameters=True, has_interest=True` |
| `app/templates/savings/dashboard.html:109,131,157` | Replace `ACCT_TYPE_HYSA` hardcoded checks with `has_interest` flag checks |
| Migration | `UPDATE ref.account_types SET has_parameters=true, has_interest=true WHERE name='Money Market'` |

Note: CD (line 26) has the same defect (`has_parameters=False, has_interest=False`) and should
likely get the same fix.

**Approach B -- Structural fix (flag-driven dispatch):**

Everything in Approach A, plus:

| File | Change |
|---|---|
| Rename `InterestParams` model | To remain consistent with any future interest-bearing types (optional -- current name is already generic per its docstring) |
| Any remaining hardcoded `ACCT_TYPE_HYSA` references | Replace with `has_interest` flag checks throughout |

### Downstream Effects

Grep for `ACCT_TYPE_HYSA` and `AcctTypeEnum.HYSA` shows these consumers:

- `app/__init__.py:156-157` -- Jinja globals (keep as convenience, no change needed)
- `app/templates/savings/dashboard.html:109,131,157` -- Needs update per above
- `app/templates/accounts/interest_detail.html:2,8,38` -- Hardcoded "HYSA" text (cosmetic only -- should say "Interest Parameters" generically)
- `tests/test_routes/test_accounts.py:1306-1327` -- Test `test_create_account_money_market_auto_creates_interest_params` already manually sets `mm_type.has_interest = True` at line 1311, confirming the intended behavior

### Risks

- Existing Money Market accounts in production have no InterestParams record. A migration or
  post-deploy script must create InterestParams rows for existing Money Market accounts.
- The interest_detail template says "HYSA" in its title -- purely cosmetic but confusing for
  Money Market users.

---

## Bug 2: Editing a Transfer Generates a 500 Error

### Reproduction

Unable to reproduce a 500 error against the dev database (278 transfers, all with correct
shadow pairs, 2822 tests all pass). The code paths are architecturally sound under normal
conditions. However, the investigation identified one concrete missing error handler and one
UX dead-end that are the most probable causes.

### Root Cause

**Most probable cause -- Missing `IntegrityError` handler in `app/routes/transfers.py:519-524`:**

The `update_transfer` route catches `NotFoundError` and `ShekelValidationError` but does
**not** catch `sqlalchemy.exc.IntegrityError`. The `transfer_service.update_transfer()` calls
`db.session.flush()` at line 522 of `transfer_service.py`. If any FK-constrained value (e.g.,
`status_id`) is invalid, the FK constraint fires at flush time and the `IntegrityError`
propagates uncaught to Flask's 500 handler.

By contrast, other routes catch this:
- `create_transfer_template` route (transfers.py:164-168) -- catches `IntegrityError`
- `update_transaction` route (transactions.py:182-186) -- catches `IntegrityError`

The transfer update route is the **only** state-changing transfer route missing this handler.

**UX dead-end -- Expand button broken in transfer quick edit:**

`app/templates/transfers/_transfer_quick_edit.html` uses CSS class `xfer-expand-btn` with
`data-xfer-id`, but `app/static/js/grid_edit.js` only handles `.txn-expand-btn[data-txn-id]`.
The expand button (and F2 key) in the transfer quick edit popover does nothing. If the user is
trying to reach the full edit form via this path, they hit a dead end.

### Potential Failure Points (exhaustive)

1. **`IntegrityError` from `db.session.flush()` (transfer_service.py:522):** Invalid
   `status_id` passes Marshmallow (valid integer) but fails FK constraint. Not caught by
   route. Produces 500.

2. **`IntegrityError` from `db.session.commit()` (transfers.py:526):** Same as above if
   deferred constraint check.

3. **Shadow FK violation:** Invalid `category_id` passes schema but `_get_owned_category`
   raises `NotFoundError`. This IS caught correctly.

4. **Template rendering error in `_transfer_cell.html`:** After successful update, rendering
   fails if relationships are broken. Extremely low likelihood.

5. **`_resolve_shadow_context` lazy load failure (transfers.py:725):** Accessing
   `shadow.pay_period.user_id` after period deletion. Extremely low likelihood due to CASCADE.

### Files Requiring Changes

| File | Change |
|---|---|
| `app/routes/transfers.py:519-524` | Add `except IntegrityError` handler matching the pattern in `create_transfer_template` (line 164-168) |
| `app/static/js/grid_edit.js` | Add handler for `.xfer-expand-btn[data-xfer-id]` to open transfer full edit (matching the `.txn-expand-btn` pattern) |

### Downstream Effects

- The `IntegrityError` fix is purely defensive -- changes error response from 500 to a clean
  flash message. No behavior change for valid inputs.
- The JS fix enables the expand button, which sends `hx-get` to `transfers/<id>/full-edit`.
  That route already exists and works (line 481-498).

### Risks

- Without a reproducible traceback from the user's specific scenario, this is the most
  probable but not confirmed root cause. The user should be asked to reproduce and share the
  terminal traceback.

---

## Bug 3: No Option to Hard Delete an Account

### Root Cause

**The feature is entirely unimplemented.** There is no `hard_delete_account()` function, no
route, no button, and no UI notification. Evidence:

- `app/routes/accounts.py` -- 812 lines. Only lifecycle routes are `archive_account()` (line
  251) and `unarchive_account()` (line 290). No delete route exists.
- `app/templates/accounts/list.html` -- 89 lines. Only buttons are Edit, Archive (for active),
  and Unarchive (for archived). No delete button.
- `app/utils/archive_helpers.py` -- **Does not exist.** The `app/utils/` directory contains
  only `auth_helpers.py`, `formatting.py`, `log_events.py`, `logging_config.py`.
- `grep -r "hard_delete" app/` -- **Zero matches.** The term only appears in docs and tests
  for other entity types.

The entire Section 5A.5 commit series (5A.5-1 through 5A.5-5 in
`docs/implementation_plan_section5a.md`) is unimplemented. This includes hard delete for
templates, transfer templates, categories, and accounts.

### Files Requiring Changes

Following the pattern from `docs/implementation_plan_section5a.md` commit 5A.5-4 (lines
1472-1617):

| File | Change |
|---|---|
| **New:** `app/utils/archive_helpers.py` | Create with `account_has_history(account_id)` -- checks for any non-deleted transactions on the account |
| `app/routes/accounts.py` | Add `POST /accounts/<int:account_id>/hard-delete` route with guard checks |
| `app/templates/accounts/list.html` | Add Delete button, separate active/archived sections, add archived collapse |

**Guard checks (in order per plan):**

1. Ownership check
2. Active transfer template guard (from/to account)
3. Transaction template FK guard
4. History check via `account_has_history()`

**If blocked by history:** Flash warning explaining the account has transaction history and
cannot be permanently deleted, auto-archive instead (set `is_active=False`).

**If safe to delete (cascade order):**

1. Delete `LoanParams` where `account_id` matches
2. Delete `InterestParams` where `account_id` matches
3. Delete `InvestmentParams` where `account_id` matches
4. Delete `AccountAnchorHistory` where `account_id` matches
5. Delete `RateHistory` where `account_id` matches (gap in plan -- not listed but has CASCADE FK)
6. Delete `EscrowComponent` where `account_id` matches (gap in plan -- not listed but has CASCADE FK)
7. Delete `SavingsGoal` where `account_id` matches (gap in plan -- not listed but has CASCADE FK)
8. `db.session.delete(account)`
9. Commit
10. Flash `"Account '{name}' permanently deleted."`

### Tables Referencing `account_id`

| Model | ON DELETE | Hard-delete implication |
|---|---|---|
| `AccountAnchorHistory` | CASCADE | Explicit delete for safety |
| `InterestParams` | CASCADE | Explicit delete for safety |
| `InvestmentParams` | CASCADE | Explicit delete for safety |
| `LoanParams` | CASCADE | Explicit delete for safety |
| `RateHistory` | CASCADE | Explicit delete (missing from plan) |
| `EscrowComponent` | CASCADE | Explicit delete (missing from plan) |
| `SavingsGoal` | CASCADE | Explicit delete (missing from plan) |
| `Transaction` | **RESTRICT** | Guard check blocks deletion |
| `TransferTemplate` | **RESTRICT** | Guard check blocks deletion |
| `Transfer` | **RESTRICT** | Guard check blocks deletion |
| `TransactionTemplate` | **RESTRICT** | Guard check blocks deletion |
| `PaycheckDeduction` | SET NULL | Auto-handled by DB |
| `UserSettings` | SET NULL | Auto-handled by DB |

### Risks

- The plan's explicit deletion list omits `RateHistory`, `EscrowComponent`, and
  `SavingsGoal`. While CASCADE handles them at the DB level, the explicit-delete-for-safety
  pattern used for the other parameter models should include them for consistency.
- This is a significant feature -- routes, templates, helpers, tests. It's the entire 5A.5
  commit.

---

## Bug 4: Income Transactions with Same Description and Category on Separate Grid Lines

### Root Cause

The `RowKey` namedtuple in `app/routes/grid.py:33-41` has 7 fields, but uniqueness is
determined by a **3-tuple: `(category_id, template_id, txn_name)`** at line 114:

```python
key = (txn.category_id, txn.template_id, txn.name)
if key not in seen:
    seen.add(key)
    row_keys.append(RowKey(...))
```

The visible problem is **transfer shadow transactions**. The database shows multiple grid rows
all displaying as just "Checking" in the income section:

| Group | Display Name | Actual Name | category_id | template_id |
|---|---|---|---|---|
| Auto | Checking | Transfer from Checking | 33 (Car Insurance) | None |
| Auto | Checking | Transfer from Checking | 32 (Car Payment) | None |
| Financial | Checking | Transfer from Checking | 42 (Savings Transfer) | None |
| Home | Checking | Transfer from Checking | 25 (Mortgage/Rent) | None |
| Uncategorized | Checking | Transfer from Checking | None | None |

These are separate rows because they have **different `category_id` values** (32, 33, 42, 25,
NULL). The `_short_display_name` function (lines 44-59) strips "Transfer from " prefixes, so
all five rows display as just "Checking", making them look like duplicates. They're scattered
across group sections (Auto, Financial, Home, Uncategorized).

### Why This Happens

When a transfer is created, both shadow transactions (expense and income) inherit the
transfer's `category_id`. A transfer from Checking to a Car Insurance account gets
`category_id=33` (Car Insurance) on both shadows. The income shadow (money arriving in the
destination account) shows up in the income section under the "Auto" group because that's
where category 33 lives.

This is technically correct behavior -- each transfer has a distinct category. But it looks
wrong because the display name strips the prefix and all rows show as "Checking".

### Files Requiring Changes

This requires a design decision. Two approaches:

**Approach A -- Fix display names only:**

| File | Change |
|---|---|
| `app/routes/grid.py:44-59` | Modify `_short_display_name` to NOT strip "Transfer from/to" prefix, or to append the category name: "Checking (Car Insurance)" |

Pro: No logic changes, just clearer labels. Con: Still many rows for the same source account.

**Approach B -- Group transfer shadows by source account, ignore category:**

| File | Change |
|---|---|
| `app/routes/grid.py:114` | For transfer shadows (where `transfer_id IS NOT NULL`), use `(None, None, txn.name)` as the key instead of `(category_id, template_id, name)` |

Pro: All "Transfer from Checking" rows merge into one row. Con: Loses per-category
granularity for transfers in the grid.

### Downstream Effects

Cell editing is transaction-ID based (`hx-get="/transactions/<txn_id>/quick-edit"`), not
RowKey-based. Multiple transactions in the same cell are independently clickable via
`{% for txn in matched %}` loop at grid.html:148. Merging rows would **not break editing**.

### Risks

- Approach B would stack many transfer amounts in a single cell, making it harder to
  distinguish individual transfers at a glance.
- Need design decision on which approach to take.

---

## Bug 5: What-If Chart X-Axis Shows "Dec 16" Instead of "Dec 2027"

### Root Cause

A copy-paste format string error. Two code paths in the same file use different date formats:

**Bug -- `app/routes/investment.py:248` (initial page load):**

```python
chart_date_labels.append(p.start_date.strftime("%b %d"))
#                                                ^^^^^ day of month -> "Dec 16"
```

**Correct -- `app/routes/investment.py:539` (HTMX slider/what-if updates):**

```python
chart_labels.append(p.start_date.strftime("%b %Y"))
#                                          ^^^^^ year -> "Dec 2027"
```

The `dashboard()` function's label code was likely copied from `chart_data_service.py:178`
(`_format_period_label`), which also uses `"%b %d"` -- appropriate for short-range pay-period
charts but wrong for multi-year investment projections.

### Files Requiring Changes

| File | Line | Change |
|---|---|---|
| `app/routes/investment.py` | 248 | Change `strftime("%b %d")` to `strftime("%b %Y")` |

One line fix.

### All Chart Date Formats Checked

| File:Line | Format | Context | Correct? |
|---|---|---|---|
| `app/routes/investment.py:248` | `"%b %d"` | **BUG** | No -- should be `"%b %Y"` |
| `app/routes/investment.py:539` | `"%b %Y"` | HTMX what-if update | Yes |
| `app/services/chart_data_service.py:178` | `"%b %d"` | Balance-over-time (short range) | Yes |
| `app/services/chart_data_service.py:545` | `"%b %Y"` | Amortization schedule | Yes |
| `app/services/chart_data_service.py:664,671` | `"%b %Y"` | Net pay trajectory | Yes |
| `app/routes/loan.py:95` | `"%b %Y"` | Loan amortization | Yes |
| `app/routes/debt_strategy.py:427` | `"%b %Y"` | Debt payoff timeline | Yes |

### Downstream Effects

- The `growth_chart()` HTMX endpoint (line 539) already uses `"%b %Y"` -- no change needed.
- Retirement dashboard uses a bar chart with no date axis -- not affected.
- Other charts using `"%b %d"` (`chart_data_service.py:178`) are short-range pay-period charts
  where day-of-month is appropriate -- not affected.

### Risks

None. This is a one-line format string fix with no behavioral side effects.

---

## Other Issues Found

1. **CD account type has the same defect as Money Market** (`app/ref_seeds.py:26`):
   `has_parameters=False, has_interest=False`. Should likely be `True, True` per the
   architecture plan.

2. **Transfer quick-edit expand button is dead**
   (`app/templates/transfers/_transfer_quick_edit.html`): Uses `xfer-expand-btn` class but
   `app/static/js/grid_edit.js` only handles `txn-expand-btn`. The F2 key and expand button do
   nothing for transfers. (Documented under Bug 2 but is a separate fix.)

3. **Entire Section 5A.5 is unimplemented**: Hard delete for templates (5A.5-1), transfer
   templates (5A.5-2), categories (5A.5-3), accounts (5A.5-4), and the active/archived UI
   separation (5A.5-5) are all missing. Bug 3 is one piece of a larger unbuilt feature set.

4. **`RateHistory` and `EscrowComponent` missing from 5A.5-4 cascade plan**: These models in
   `app/models/loan_features.py` have `account_id` FKs with `CASCADE` but are not in the
   implementation plan's explicit deletion list for account hard-delete. `SavingsGoal` also has
   this gap.

5. **`AccountTypeUpdateSchema` has no cross-field validation**
   (`app/schemas/validation.py:743-765`): Unlike `AccountTypeCreateSchema` (line 700), the
   update schema doesn't validate flag combinations. A user could set `has_parameters=True`
   without `has_interest=True`, causing Money Market to match the InvestmentParams branch
   instead of InterestParams.
