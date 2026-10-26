> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Transfer Architecture Rework -- Verification Report

**Date:** 2026-03-26
**Commit:** f1cfa2765ac0b432a4aff140e1a8085f025f50ad
**Verified by:** Claude Code (Opus 4.6)

## Summary

- Critical issues found: 0
- High-priority issues found: 1
- Medium-priority issues found: 3
- Low-priority issues found: 3
- Missing tests identified: 9
- Total findings: 16

---

## Findings

### Critical Issues

None. The core financial engine (balance calculator, transfer service invariants, shadow transaction
creation/sync, carry forward routing) is correct. No double-counting paths were found. No data
corruption paths were found. All five design-document invariants are enforced in the code and
verified by tests.

---

### High-Priority Issues

**H1. Transfer full edit form response renders `_transfer_cell.html` into a transaction cell div**

- **File:** `app/routes/transfers.py` lines 494-497, 578-581, 601-604
- **File:** `app/templates/transfers/_transfer_full_edit.html` lines 18-21, 82-94

**Description:** When a user opens the full edit popover on a shadow transaction cell, the
flow is:

1. Click shadow cell -> quick edit loads (transaction route, correct).
2. Expand (F2) -> `openFullEdit(txnId)` fetches `/transactions/<id>/full-edit`.
3. `transactions.get_full_edit` detects `transfer_id`, returns `_transfer_full_edit.html` with
   `source_txn_id=txn.id`.
4. The form's `hx-target` is correctly set to `#txn-cell-<source_txn_id>`.
5. The form's `hx-patch` targets `transfers.update_transfer` (the transfer route).
6. `transfers.update_transfer` renders `_transfer_cell.html` (a transfer cell template, not a
   transaction cell template).
7. HTMX injects the transfer cell content into the `#txn-cell-<id>` div.

After step 7, the cell now contains `_transfer_cell.html` content which has:
- `hx-get="{{ url_for('transfers.get_quick_edit', xfer_id=xfer.id) }}"` -- a transfer route
- `hx-target="#xfer-cell-{{ xfer.id }}"` -- a target that does not exist in the grid

Subsequent clicks on this cell will attempt to load a transfer quick-edit targeting a non-existent
`#xfer-cell-<id>` element. The HTMX swap silently fails. The cell becomes non-interactive until
a full page refresh.

The same issue affects the "Done" and "Cancel" buttons in the transfer full edit popover when
opened from a shadow cell. Both `transfers.mark_done` and `transfers.cancel_transfer` return
`_transfer_cell.html` with `HX-Trigger: balanceChanged` (not `gridRefresh`), so the broken cell
content persists.

**Impact:** After any save/done/cancel action via the transfer full edit popover opened from a
shadow transaction cell, the cell displays correctly (transfer cell looks similar to transaction
cell) but becomes non-interactive. A page refresh (F5 or navigation) restores correct behavior.
No data is lost -- the transfer service is called correctly and both shadows are updated.

**Recommended fix:** The `transfers.update_transfer`, `transfers.mark_done`, and
`transfers.cancel_transfer` route handlers should detect when the request originated from a
shadow transaction cell (e.g., via a `source_txn_id` form parameter or query param). When
detected, they should:

- Reload the shadow transaction via `db.session.get(Transaction, source_txn_id)`
- Return `render_template("grid/_transaction_cell.html", txn=shadow_txn)` instead of
  `_transfer_cell.html`
- OR, return with `HX-Trigger: gridRefresh` instead of `balanceChanged` to force a full
  page reload that restores correct cell content.

The simpler fix is the `gridRefresh` trigger, which matches what the transaction route guards
already do for mark-done and cancel operations on shadows.

---

### Medium-Priority Issues

**M1. `reactivate_transfer_template` bypasses transfer service for shadow restoration**

- **File:** `app/routes/transfers.py` lines 385-394

**Description:** The `reactivate_transfer_template` route directly sets `is_deleted=False` on
transfers and their shadow transactions via ORM attribute assignment:

```python
for xfer in transfers_to_restore:
    xfer.is_deleted = False
    shadows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
    for shadow in shadows:
        shadow.is_deleted = False
```

The design document states: "Every code path that creates, updates, or deletes a transfer MUST
go through this service." This direct manipulation bypasses the transfer service.

The current code correctly restores both the transfer and both shadows, so no data integrity
violation occurs. However:

1. If the transfer service gains additional logic in the future (e.g., logging, validation,
   audit trail), this code path would miss it.
2. The transfer service does not have an "undelete" or "restore" function, so there is no
   service method to call.

**Impact:** Low risk currently. The restoration correctly handles both shadows. The invariant
violation is architectural, not functional.

**Recommended fix:** Add a `restore_transfer(transfer_id, user_id)` method to the transfer
service that sets `is_deleted=False` on the transfer and both shadows. Update
`reactivate_transfer_template` to call it.

---

**M2. `_get_shadow_transactions` produces misleading error on soft-deleted transfers**

- **File:** `app/services/transfer_service.py` lines 239-242, 245-259

**Description:** The `_get_shadow_transactions` helper filters by `is_deleted=False`:

```python
shadows = db.session.query(Transaction).filter_by(
    transfer_id=transfer_id, is_deleted=False
).all()
```

Meanwhile, `_get_transfer_or_raise` (used by `update_transfer`) does NOT check `is_deleted`:

```python
xfer = db.session.get(Transfer, transfer_id)
if xfer is None or xfer.user_id != user_id:
    raise NotFoundError(...)
```

If `update_transfer` is called on a soft-deleted transfer:
1. `_get_transfer_or_raise` succeeds (transfer exists, correct owner).
2. `_get_shadow_transactions` finds 0 shadows (they are `is_deleted=True`).
3. A `ValidationError` is raised: "Transfer X has 0 shadow transactions instead of the
   expected 2. Data integrity issue -- cannot proceed."

The error message says "data integrity issue" when the real cause is that the transfer was
soft-deleted. This is misleading for debugging.

**Impact:** Misleading error message only. The soft-deleted transfer is correctly prevented from
being updated. No data corruption risk.

**Recommended fix:** Either:
- Add an `is_deleted` check to `_get_transfer_or_raise` so it raises a clear NotFoundError for
  soft-deleted transfers before reaching the shadow query.
- OR, in `_get_shadow_transactions`, check if the shadow count is 0 and the transfer's
  `is_deleted` flag is True, and provide a more specific error message.

---

**M3. Grid route tests: 26 of 85 tests lack docstrings**

- **File:** `tests/test_routes/test_grid.py`

**Description:** The project's CLAUDE.md states "All tests need docstrings explaining what is
verified and why." 26 test methods in test_grid.py lack docstrings. Most of the missing
docstrings are in the account-scoped grid tests (TestAccountScopedGrid class) and some
validation edge case tests.

Missing docstrings on:
- `test_create_transaction_missing_pay_period_id`
- `test_create_transaction_with_other_users_pay_period`
- `test_cancel_already_cancelled_transaction`
- `test_mark_done_with_invalid_actual_amount`
- `test_mark_done_with_negative_actual_amount`
- `test_transaction_without_account_id_raises_integrity_error`
- `test_inline_create_rejects_missing_account_id`
- `test_inline_create_rejects_other_users_account_id`
- `test_grid_shows_only_checking_transactions`
- `test_grid_account_override_shows_savings_transactions`
- `test_grid_shows_correct_account_name_in_header`
- `test_balance_uses_correct_anchor_for_each_account`
- `test_balance_excludes_other_accounts_transactions`
- `test_balance_row_refresh_scoped_to_account`
- `test_balance_row_refresh_includes_account_id_in_htmx_url`
- `test_footer_totals_reflect_viewed_account_only`
- `test_grid_for_account_with_no_transactions`
- `test_grid_hides_category_rows_without_account_transactions`
- `test_grid_account_with_no_anchor_balance`
- `test_grid_account_with_no_anchor_period`
- `test_cancelled_transactions_excluded_from_account_grid`
- `test_soft_deleted_transactions_excluded_from_account_grid`
- `test_carry_forward_moves_all_accounts_transactions`
- `test_inline_create_on_savings_grid_saves_to_savings`
- `test_balance_rolls_forward_correctly_per_account`
- `test_balance_row_refresh_excludes_net_cash_flow`

**Impact:** Reduced maintainability. Test intent must be inferred from the test name and code.

**Recommended fix:** Add docstrings to all 26 tests.

---

### Low-Priority Issues

**L1. `resolve_conflicts` in transfer_recurrence.py directly manipulates shadow state**

- **File:** `app/services/transfer_recurrence.py` lines 234-244

**Description:** The `resolve_conflicts` function syncs shadow transactions directly via ORM
instead of calling `transfer_service.update_transfer()`:

```python
shadows = db.session.query(Transaction).filter_by(transfer_id=xfer_id).all()
for shadow in shadows:
    shadow.is_override = False
    shadow.is_deleted = False
    if new_amount is not None:
        shadow.estimated_amount = new_amount
```

This bypasses the transfer service. However, it correctly syncs the three fields that change
during conflict resolution (is_override, is_deleted, estimated_amount). It does not need to sync
status_id or pay_period_id because those don't change during conflict resolution.

**Impact:** Low. The fields are correctly synced. A future change to the transfer service (e.g.,
adding an audit log) would not be applied to this code path.

**Recommended fix:** Consider using `transfer_service.update_transfer()` instead, but this is
optional since the current code correctly maintains invariants.

---

**L2. Transfer route mark-done/cancel responses use `balanceChanged` not `gridRefresh`**

- **File:** `app/routes/transfers.py` lines 581, 604

**Description:** When the `transfers.mark_done` and `transfers.cancel_transfer` routes are
called (whether from the transfer management page or from a shadow transaction's full edit
popover), they trigger `HX-Trigger: balanceChanged`. In contrast, the transaction route guards
for mark-done and cancel on shadows trigger `HX-Trigger: gridRefresh`.

The inconsistency means:
- Marking a shadow as done via the transaction route (clicking the "Done" button on the
  quick edit) triggers a full grid refresh.
- Marking a transfer as done via the transfer route (clicking the "Done" button on the
  full edit popover opened from a shadow) only refreshes the balance row.

This is only a consistency issue -- both paths correctly update the data. The `gridRefresh`
trigger is more appropriate for status changes that affect which cells are visible.

**Impact:** Minor UX inconsistency. The cell shows the correct post-update state, but the grid
body (subtotal rows, other affected cells) may not refresh until the next full page load.

**Recommended fix:** Change the transfer route status actions (mark_done, cancel_transfer) to
return `HX-Trigger: gridRefresh` instead of `balanceChanged`.

---

**L3. `_transfer_cell.html` template references retired `#xfer-cell-` target IDs**

- **File:** `app/templates/transfers/_transfer_cell.html` lines 8, 10-13

**Description:** The `_transfer_cell.html` template still references `#xfer-cell-{{ xfer.id }}`
as its HTMX target and wrapping div ID. This target pattern was used when transfers had their
own section in the grid (the now-retired TRANSFERS section).

This template is still used by:
- `transfers.get_cell` (line 430-432)
- `transfers.update_transfer` response (line 494-496)
- `transfers.mark_done` response (line 578-581)
- `transfers.cancel_transfer` response (line 601-604)
- `transfers.create_ad_hoc` response (line 534-537)

Since the TRANSFERS grid section is removed, these routes are only reachable from:
1. The transfer management page (template list/edit) -- where `xfer-cell-*` IDs may exist
2. The shadow transaction full edit popover (via H1 above) -- where they do NOT exist

**Impact:** Part of the H1 finding above. The template works correctly on the transfer
management page but produces broken cell content when used in the grid context.

**Recommended fix:** See H1 fix recommendation.

---

## Missing Test Coverage

**T1. No test for shadow transactions in done/received status in balance calculator**

The balance calculator test suite has extensive status-filtering tests for regular transactions
(done, received, credit, cancelled excluded from projected calculations). However, no test
explicitly creates a shadow transaction with status "done" or "received" and verifies it is
excluded from the balance calculation. The existing tests prove this works for regular
transactions, and the calculator treats shadows identically (no type-specific filtering), so
the behavior is almost certainly correct -- but an explicit test would lock the guarantee.

**Why it matters:** If future code adds a filter like `WHERE transfer_id IS NULL` to any
calculation path, shadows would suddenly be excluded regardless of status. An explicit test
catches this regression.

---

**T2. No test for grid rendering of shadow transactions in INCOME/EXPENSES sections**

The grid route tests verify that no TRANSFERS section exists and that subtotal values are correct.
However, no test explicitly creates a shadow transaction and verifies it renders in the correct
section (expense shadow in EXPENSES, income shadow in INCOME) alongside regular transactions.

**Why it matters:** If a template filter were added that excludes transactions where
`transfer_id IS NOT NULL`, shadows would silently disappear from the grid.

---

**T3. No test for transfer indicator icon on shadow transactions vs. absence on regular
transactions**

The `_transaction_cell.html` template shows `<i class="bi bi-arrow-left-right">` for
transactions with `transfer_id`. No test verifies this icon appears for shadows or that it
is absent for regular transactions.

**Why it matters:** The indicator is the only visual cue that a transaction is transfer-linked.
If the template condition is broken, users cannot distinguish shadows from regular transactions.

---

**T4. No test for creating a transfer where one account is inactive (`is_active=False`)**

The `_get_owned_account` helper in transfer_service.py checks that the account exists and
belongs to the user, but does not check `is_active`. A transfer could be created between
a deactivated account and an active account.

**Why it matters:** An inactive account is one the user has "closed" or "hidden." Allowing
transfers to/from it could create confusing data. Whether this should be blocked depends on
the product requirements (the user may want to see past transfers involving a now-closed
account), but the decision should be explicit and tested.

---

**T5. No test for carry forward where the target period does not exist**

The carry forward service validates that both source and target periods exist and belong to the
user. If the target period does not exist, `NotFoundError` is raised. This validation path is
tested in the existing carry_forward tests (period ownership checks), but there is no explicit
test where the target_period_id is completely invalid (e.g., 999999).

**Why it matters:** Low risk since the carry_forward route always derives the target from
`pay_period_service.get_current_period()`, which always returns a valid period. But an explicit
test documents the contract.

---

**T6. No test for the balance calculator with a period containing ONLY shadow transactions**

All existing balance calculator tests that include shadow transactions also include regular
transactions in the same period or adjacent periods. No test verifies correct behavior when a
period has zero regular transactions and only shadow transactions.

**Why it matters:** If any calculation path counts only transactions where `transfer_id IS NULL`,
a shadow-only period would show zero income/expenses and a flat balance. An explicit test catches
this regression.

---

**T7. No test for updating a transfer's category to NULL after it was previously set**

The `test_category_set_to_none_uses_outgoing_fallback` test in test_transfer_service.py verifies
that setting `category_id=None` falls back to "Transfers: Outgoing." However, this test creates
the transfer with a category and immediately sets it to None in the same test. There is no test
where a transfer has been used with a category across periods and then the category is removed --
verifying that the fallback applies without affecting historical data.

**Why it matters:** Low risk since the service handles this case correctly. But an explicit test
documents the fallback behavior as intentional.

---

**T8. No test for the full edit routing when a shadow transaction's parent transfer has been
soft-deleted**

If a shadow transaction's parent transfer is soft-deleted (`is_deleted=True`), the shadow may
still be visible in the grid (if the grid query does not filter by `transfer.is_deleted`). When
the user clicks to edit this shadow, `transactions.get_full_edit` would load the transfer:

```python
xfer = db.session.get(Transfer, txn.transfer_id)
```

This would return the soft-deleted transfer. The edit form would render, and the user could
attempt to update it, which would fail in `_get_shadow_transactions` (M2 above).

**Why it matters:** If the grid ever shows soft-deleted shadow transactions (currently it does not
since `is_deleted=False` is filtered in the grid query), the full edit path would produce a
confusing error.

---

**T9. No test for grid rendering with a user who deleted the "Transfers: Incoming" category**

The transfer service's `_lookup_transfer_categories` function logs a warning and returns None
for the incoming category if it has been deleted. This means income-side shadows would have
`category_id=NULL`. In the grid, uncategorized transactions do not render in any category row
because the grid iterates categories and checks `txn.category_id == category.id`. A shadow
with `category_id=NULL` would not match any category and would be invisible in the grid while
still affecting the balance calculation.

**Why it matters:** If a user deletes the "Transfers: Incoming" category, their income-side
shadow transactions would silently disappear from the grid while still affecting projected
balances. The balance row would show different numbers than what the user can see in the grid
body. This is a confusing UX gap that should be either tested and documented as a known
limitation, or prevented by making the transfer categories undeletable.

---

## Verification Results

### Analysis 1: Transfer Service -- PASS

**1a. create_transfer:** Correct.
- Amount validation: `_validate_positive_amount()` at line 335 converts to Decimal via
  `Decimal(str(amount))`, raises `ValidationError` for zero (`<=0` check at line 70-73),
  negative, non-numeric. Verified: `Decimal("0")` triggers "must be positive."
- Same-account check: Line 337-339, raises `ValidationError`.
- Account ownership: `_get_owned_account()` at lines 342-347. Returns `NotFoundError` with
  identical message for non-existent and wrong-owner (security response rule). Verified at
  lines 93-95.
- Period ownership: `_get_owned_period()` at line 348. Same pattern. Verified at lines 110-112.
- Scenario ownership: `_get_owned_scenario()` at line 349. Same pattern. Verified at lines
  123-125.
- Category ownership: `_get_owned_category()` at line 350. Returns None for None input (line
  139-140), raises `NotFoundError` for wrong owner. Verified at lines 141-143.
- Flush for transfer.id: Line 393 (`db.session.flush()`). Transfer ID is available before
  shadow creation. Correct.
- Exactly two shadows: Lines 396-434 create two Transaction objects. No conditional logic
  between them -- both are always created. If the second insert fails, the flush at line 434
  would raise, and the caller's transaction rollback would remove both the transfer and the
  first shadow. Atomicity is ensured by the database transaction.
- Expense shadow account_id: Line 397, `account_id=from_account_id`. Correct.
- Income shadow account_id: Line 417, `account_id=to_account_id`. Correct.
- template_id on shadows: Lines 398, 418, both `template_id=None`. Correct.
- transfer_id on shadows: Lines 399, 419, both `transfer_id=xfer.id`. Correct.
- Missing "Transfers: Incoming" category: `_lookup_transfer_categories` at lines 164-205.
  Returns None and logs warning (lines 190-195). Income shadow gets `category_id=None`. Does
  not crash. Correct (graceful degradation).
- Decimal usage: Amount is `Decimal(str(amount))` at line 65. All arithmetic uses Decimal.
  No float. Correct.
- Does NOT commit: Line 434 calls `flush()`, not `commit()`. The docstring at line 25 states
  "Flushes to the session but does NOT commit." Correct.

**1b. update_transfer:** Correct.
- Ownership: `_get_transfer_or_raise()` at line 479 checks `xfer.user_id != user_id`.
- Shadow loading: `_get_shadow_transactions()` at line 480. Filters by `transfer_id` AND
  `is_deleted=False`.
- Shadow count != 2: Lines 245-259, raises `ValidationError` with detailed error and logging.
  Fails loudly. Correct.
- Expense/income identification: Lines 261-282, looks up TransactionType by name ("expense",
  "income"), then iterates shadows matching `transaction_type_id`. Does NOT assume ordering.
  Correct.
- Amount update: Lines 483-487. Sets `xfer.amount`, `expense_shadow.estimated_amount`,
  `income_shadow.estimated_amount`. BOTH shadows updated. Correct.
- Status update: Lines 490-494. Sets `status_id` on transfer and BOTH shadows. Correct.
- Period update: Lines 497-502. Validates ownership via `_get_owned_period()`, then sets on
  transfer and BOTH shadows. Correct.
- Category update: Lines 507-517. When non-None: validates ownership, updates transfer and
  expense shadow only. When None: falls back to "Outgoing" for expense shadow, sets transfer
  to None. Income shadow is NOT touched. Correct per design.
- actual_amount update: Lines 534-546. Sets on BOTH shadows. Transfer model has no
  actual_amount column (by design). Correct.
- is_override update: Lines 549-553. Sets on transfer and BOTH shadows. Correct.
- Amount validation on update: Line 484 calls `_validate_positive_amount()`. Same validation
  as create. Correct.
- No-op update (empty kwargs): No kwargs triggers none of the `if "X" in kwargs` blocks. The
  function reaches line 555 (flush) and returns the unchanged transfer. No crash. Correct.
- Does NOT commit: Line 555 calls `flush()`. Correct.

**1c. delete_transfer:** Correct.
- Hard delete CASCADE: Line 598 `db.session.delete(xfer)`. The Transaction model at line 77
  defines `db.ForeignKey("budget.transfers.id", ondelete="CASCADE")`. The backref at line 101
  uses `passive_deletes=True`. This means SQLAlchemy does NOT try to SET NULL before the
  database CASCADE fires. Correct.
- Soft delete: Lines 581-595. Sets `is_deleted=True` on transfer. Queries shadows WITHOUT
  is_deleted filter (line 586-589: `filter_by(transfer_id=transfer_id)`), sets is_deleted on
  all. Correct.
- Idempotent soft-delete: Calling `delete_transfer(soft=True)` on already-soft-deleted transfer:
  `_get_transfer_or_raise` does not check is_deleted, so it loads the transfer. The shadow query
  finds shadows (both already is_deleted=True). Sets them to True again (no-op). Returns. No
  error. Correct (idempotent).
- Hard delete after soft-delete: Transfer is loaded, `db.session.delete()` fires, CASCADE
  removes shadows. Correct.
- Ownership: `_get_transfer_or_raise()` at line 579. Correct.

**1d. Invariant enforcement:**
1. **Exactly two shadows:** Enforced in `create_transfer` (always creates 2). Verified in
   `_get_shadow_transactions` (raises if count != 2). No code path creates only one shadow.
2. **No orphaned shadows:** CASCADE on hard delete. Explicit soft-delete on both shadows for
   soft delete. No try/except between the two shadow inserts in create_transfer that could
   catch one and leave the other.
3. **Amount sync:** `update_transfer` sets `estimated_amount` on both shadows whenever `amount`
   changes. No other code path mutates shadow amounts (route guards prevent direct editing).
4. **Status sync:** `update_transfer` sets `status_id` on both shadows whenever `status_id`
   changes. No other path mutates shadow status.
5. **Period sync:** `update_transfer` sets `pay_period_id` on both shadows whenever
   `pay_period_id` changes. No other path mutates shadow periods.

All five invariants are correctly enforced.

---

### Analysis 2: Balance Calculator -- PASS

**2a. No remaining transfer references:**

`grep -n "transfer\|xfer\|Transfer" app/services/balance_calculator.py` returns:
- Line 18: docstring comment about shadow transactions (acceptable)
- Line 45: docstring comment about shadow transactions (acceptable)
- Line 181: docstring comment about detection method (acceptable)
- Line 250: `txn.transfer_id is not None` -- runtime check for amortization payment detection

No Transfer model import. No Transfer query. No transfer-specific calculation logic. PASS.

**2b. Shadow transaction handling:**

Shadow transactions are processed by `_sum_remaining` (lines 274-304) and `_sum_all` (lines
307-333). These functions iterate all transactions and classify by `txn.is_income` and
`txn.is_expense`. They do NOT check `transfer_id`, `template_id`, or any other field that
would distinguish shadows from regular transactions. Shadow transactions are treated identically
to regular transactions. PASS.

No filter on `template_id IS NOT NULL` exists. No filter on `transfer_id IS NULL` exists. PASS.

**2c. Amortization variant:**

`calculate_balances_with_amortization()` at lines 240-252 detects loan payments via:
```python
if (txn.transfer_id is not None
        and hasattr(txn, "is_income") and txn.is_income):
    total_payment_in += Decimal(str(txn.estimated_amount))
```

This correctly identifies shadow income transactions as loan payments. The `hasattr` check
is defensive (all Transaction objects have `is_income`). If a loan account received a regular
income transaction (no transfer_id), it would NOT be treated as a payment. Correct.

Uses `txn.estimated_amount`, not `actual_amount`. For settled payments, the user may have set
`actual_amount` to a different value. This means the amortization calculation always uses the
estimated payment, not the actual. This is a minor gap but acceptable because the amortization
table is a projection tool, not a historical record.

**2d. Double-counting verification:**

`grep -rn "transfers=" app/routes/ app/services/ --include="*.py"` returns zero matches.
No caller passes `transfers=` to any balance calculator function. The `transfers` parameter does
not exist in any function signature. The removed functions (`_sum_transfer_effects_remaining`,
`_sum_transfer_effects_all`) are completely gone. No double-counting path exists. PASS.

**2e. Status-based filtering:**

- `_sum_remaining` (anchor period): Excludes credit, cancelled, done, received. Only projected
  transactions contribute. Shadow and regular transactions follow identical rules. Correct.
- `_sum_all` (post-anchor): Excludes credit, cancelled, done, received. Only projected
  transactions contribute. Correct.
- Stale anchor detection: Checks for done/received in post-anchor periods. Works for both
  regular and shadow transactions. Correct.

---

### Analysis 3: Grid Rendering -- ISSUES FOUND (H1, T2, T3, T9)

**3a. Transaction query:**

Grid query in `app/routes/grid.py` lines 87-98 filters by:
- `Transaction.pay_period_id.in_(period_ids)`
- `Transaction.scenario_id == scenario.id`
- `Transaction.is_deleted.is_(False)`
- `Transaction.account_id == account.id` (when account exists)

No filter excludes shadow transactions (`transfer_id IS NOT NULL` is not filtered). Shadow
transactions are loaded alongside regular transactions. PASS.

Single query for all periods (not per-period). PASS.

**3b. Section rendering:**

Grid iterates `categories` and within each, checks `txn.category_id == category.id` and
`txn.is_income` (or `txn.is_expense`) to render in the correct section. Shadow transactions
have `category_id` set and correct `transaction_type_id`, so they render in the correct
section. PASS.

No remaining TRANSFERS section. Verified by grep: `section-banner-transfer`, `xfer_by_period`,
`has_any_transfers` return zero matches in `app/`. PASS.

**3c. Subtotal rows:**

Total Income (lines 166-176), Total Expenses (lines 246-256), Net Cash Flow (lines 261-279)
all use consistent filtering: `not txn.is_deleted and txn.status.name not in ('credit',
'cancelled', 'done', 'received')`. All use `txn.effective_amount|float`. Shadow transactions
are included (no transfer_id filter). Cancelled/deleted are excluded. PASS.

**3d. Footer:**

`_balance_row.html` contains only the Projected End Balance row (1 `<tr>` in `<tfoot>`). The
`<tfoot>` has `id="grid-summary"`, `hx-get=...`, `hx-trigger="balanceChanged from:body"`,
`hx-swap="outerHTML"`. Self-referencing refresh cycle is intact. PASS.

The `balance_row()` handler in grid.py no longer passes `txn_by_period` -- only `periods`,
`balances`, `account`, `num_periods`, `start_offset`, `low_balance_threshold`. These match the
template's expected context. No unused variables remain. PASS.

**3e. Transfer indicator:**

`_transaction_cell.html` line 42: `{% if t.transfer_id %}` shows the icon. Correct use of
Jinja truthiness (transfer_id is an integer > 0 when set, None when not). The icon has
`title="Transfer"` and `aria-label="Transfer"`. PASS.

The indicator is absent for regular transactions (transfer_id is None, falsy). PASS.

---

### Analysis 4: Route Guards -- PASS

**4a. Guard presence:**

| Handler | Guard Present? | Action |
|---------|---------------|--------|
| `update_transaction` (PATCH) | YES (line 139) | Routes through service |
| `mark_done` (POST) | YES (line 209) | Routes through service |
| `mark_credit` (POST) | YES (line 258) | BLOCKS (returns 400) |
| `unmark_credit` (DELETE) | YES (line 279) | BLOCKS (returns 400) |
| `cancel_transaction` (POST) | YES (line 304) | Routes through service |
| `delete_transaction` (DELETE) | YES (line 549) | BLOCKS (returns 400) |
| `get_full_edit` (GET) | YES (line 92) | Returns transfer form |

All 7 handlers have guards. PASS.

**4b. Guard correctness:**

`update_transaction` guard (lines 139-167):
- Maps `estimated_amount` -> `amount`, passes through `actual_amount`, `status_id`, `notes`,
  `category_id`. Correct field mapping.
- Calls `transfer_service.update_transfer()`. Correct.
- Commits after service call. Correct.
- Returns `_transaction_cell.html` with txn (refreshed). Correct template.
- Triggers `balanceChanged`. Correct.

`mark_done` guard (lines 209-229):
- Uses 'done' status for the transfer service (not distinguishing done/received). Both shadows
  get 'done'. This is correct per design invariant 4.
- Commits and triggers `gridRefresh`. Correct.

`mark_credit` and `unmark_credit` guards: Return 400 with clear message. Prevent credit workflow
from executing. Template hides Credit/Undo CC buttons for shadows (lines 84, 92:
`{% if not txn.transfer_id and ... %}`). Correct double protection (server + UI).

`cancel_transaction` guard (lines 304-312): Routes through service with cancelled status.
Commits and triggers `gridRefresh`. Correct.

`delete_transaction` guard: Returns 400 with clear message. Prevents deletion. Correct.

`get_full_edit` guard (lines 92-109): Loads transfer, queries categories and statuses, passes
`source_txn_id=txn.id` for correct HTMX targeting. Returns transfer form. Correct.

**4c. Guard condition:**

All guards use `if txn.transfer_id is not None:` (correct). This is the identity check, not
truthiness. Even transfer_id=0 (impossible with auto-increment but defensive) would trigger.
Correct.

All guards execute AFTER ownership verification (line 127-129 `_get_owned_transaction`). No
mutation logic can execute before the guard. Correct.

---

### Analysis 5: Carry Forward -- PASS

**5a. Partition logic:**

Lines 85-91 partition into `regular_txns` (transfer_id is None) and `shadow_txns` (transfer_id
is not None). Partition happens after the query (line 71-80) and before any mutations. Condition
uses `is None` (correct identity check). PASS.

**5b. Regular transaction path:**

Lines 96-103. Unchanged from pre-rework: sets `pay_period_id` and `is_override=True` for
template-linked. Correct.

**5c. Shadow transaction path:**

Lines 108-122. De-duplicates by `transfer_id` via `moved_transfer_ids` set. Calls
`transfer_service.update_transfer(transfer_id, user_id, pay_period_id=target, is_override=True)`.
This moves the parent transfer and BOTH shadows to the target period (even if only one shadow
was in the query). Each transfer counts as 1 item. Correct.

**5d. Edge cases:**

- Zero projected: Empty lists, count=0, returns 0. No crash. Correct.
- All shadows: regular_txns loop is empty, shadow_txns loop moves all. Correct.
- Mix: Both loops execute. Correct.
- Done/cancelled shadow: Excluded by query filter `status_id == projected_status.id`. Correct.

---

### Analysis 6: Transfer Recurrence -- PASS

**6a. Service call:**

`generate_for_template` at line 102 calls `transfer_service.create_transfer(...)`. No direct
`Transfer()` constructor in the file. Verified by grep: `Transfer(` returns zero matches in
`app/services/transfer_recurrence.py`. PASS.

**6b. Category propagation:**

Line 110: `category_id=template.category_id`. When template has None, the service's fallback
handles it (uses "Transfers: Outgoing"). Correct.

**6c. Deletion during regeneration:**

`regenerate_for_template` at lines 182-184: `db.session.delete(xfer)` for each unmodified
transfer. This is a per-object ORM delete, which triggers CASCADE. Shadows are removed. No
bulk delete (`Transfer.query.filter(...).delete()`) is used. PASS.

**6d. One-time transfers:**

`generate_for_template` line 64-65: `if pattern_name == "once": return []`. The once pattern
does NOT generate transfers via the recurrence engine. One-time transfers are created via the
ad-hoc transfer route (`transfers.create_ad_hoc`), which calls `transfer_service.create_transfer`.
This produces shadows. The original one-time transfer bug (design doc section 1.4) is fixed.
PASS.

---

### Analysis 7: Charts and Reporting -- PASS

**7a. No remaining Transfer queries for calculations:**

Verified by grep:
- `chart_data_service.py`: No import of Transfer model. No reference to transfer_id or Transfer.
  PASS.
- `investment_projection.py`: No import of Transfer model. Uses `all_contributions` parameter
  (shadow income transactions). PASS.
- `investment.py`: No Transfer import. Queries `Transaction.transfer_id.isnot(None)` at line 106
  to find shadow income contributions. PASS.
- `retirement.py`: No Transfer import. Uses `Transaction.transfer_id.isnot(None)` at line 167
  for contributions. PASS.
- `savings.py`: Uses `Transaction.transfer_id.isnot(None)` at line 86 for contributions. PASS.

**7b. Category-based charts:**

`chart_data_service.py` queries only `budget.transactions` with no filter on transfer_id. Shadow
expense transactions with category_id (e.g., "Home: Mortgage Payment") are automatically included
in category totals. PASS.

**7c. Investment/retirement contributions:**

All three routes (investment, retirement, savings) derive contributions from shadow income
transactions filtered by `transfer_id IS NOT NULL, transaction_type == income, account_id ==
<target>`. No Transfer model query. Correct.

---

### Analysis 8: Test Coverage -- ISSUES FOUND (M3, T1-T9)

**8a. Transfer service tests (42 tests):** Comprehensive. All tests have docstrings. Monetary
assertions use Decimal. Both shadows are verified in all relevant tests. Expense/income type
identification uses explicit TransactionType queries, not ordering assumptions. All five
invariants have dedicated tests. Validation edge cases covered. PASS.

**8b. Balance calculator tests (56 tests across 3 files):** Comprehensive. All tests have
docstrings. Monetary assertions use Decimal. One test (`test_no_transfers_parameter_accepted`)
explicitly verifies the `transfers=` parameter is rejected. Shadow transactions are tested via
lightweight mock objects (appropriate for a pure function). No-double-counting is explicitly
tested. Amortization payment detection from shadows is extensively tested.

**Gap:** No test for shadow transactions in done/received status (T1).

**8c. Route guard tests (17 tests):** Comprehensive. Tests cover mark-credit block, delete
block, PATCH update sync, mark-done sync, cancel sync. Six regression tests verify regular
transactions are unaffected. All tests have docstrings. Monetary assertions use Decimal. PASS.

**8d. Carry forward tests (16 tests):** Comprehensive. Mixed regular+shadow carry forward,
de-duplication (count per transfer), done/cancelled/soft-deleted shadow exclusion, atomic move,
multiple transfers, shadow-only period. All tests have docstrings. Monetary assertions use
Decimal. PASS.

**8e. Grid rendering tests (85 tests):** Mostly comprehensive. Tests verify no TRANSFERS section,
subtotal rows, Net Cash Flow, footer condensation, HTMX attributes. 26 tests lack docstrings
(M3). 7 tests only assert status codes (weak). No test for shadow rendering in sections (T2).
No test for transfer indicator (T3).

**8f. Test quality audit:**
- Transfer service: All 42 tests pass quality checks.
- Balance calculator: All 56 tests pass quality checks.
- Route guards: All 17 tests pass quality checks.
- Carry forward: All 16 tests pass quality checks.
- Transfer recurrence: All 26 tests pass quality checks.
- Grid routes: 26 tests missing docstrings; 7 tests with weak assertions.
- No tautological tests found in any file.

**8g. Missing coverage:** See T1-T9 in the Missing Test Coverage section above.

---

### Analysis 9: Data Integrity -- PASS

**9a. Foreign key constraints:**

Verified in migration files:

- `transactions.transfer_id` -> `transfers.id` with ON DELETE CASCADE:
  Migration `772043eee094`, lines 33-40: `ondelete='CASCADE'`. PASS.

- `transactions.account_id` -> `accounts.id` (NOT NULL):
  Migration `efffcf647644`, lines 19-37. Column is `nullable=False`. FK constraint created.
  PASS.

- `transfers.category_id` -> `categories.id`:
  Migration `772043eee094`, lines 43-54. Nullable, FK constraint created. PASS.

- `transfer_templates.category_id` -> `categories.id`:
  Migration `772043eee094`, lines 57-68. Nullable, FK constraint created. PASS.

**9b. Orphan prevention:**

- Shadow without transfer: Only possible via direct SQL INSERT bypassing the service. The
  application layer always creates shadows through `create_transfer`. CASCADE ensures deletion.
  No ORM path creates a Transaction with a transfer_id that points to a non-existent transfer.
  PASS.

- Transfer without shadows: Could only occur if `create_transfer` fails between the transfer
  flush (line 393) and the shadow flush (line 434). Since both operations are in the same
  database transaction, a failure rolls back everything (including the transfer). PASS.

- Atomicity: `create_transfer` uses `flush()` (not `commit()`). The caller owns the transaction.
  A failure at any point rolls back the entire operation. PASS.

**9c. Index verification:**

- `idx_transactions_account`: Migration `efffcf647644` line 24-30 creates this index. Also
  declared in Transaction model `__table_args__` at line 25. PASS.

- `idx_transactions_transfer` (partial, WHERE transfer_id IS NOT NULL): Migration `772043eee094`
  lines 25-32 creates this index with `postgresql_where=sa.text('transfer_id IS NOT NULL')`.
  Also declared in Transaction model `__table_args__` at lines 26-30. PASS.

---

### Analysis 10: Schema and Models -- PASS

**10a. Transaction model:**
- `account_id`: Lines 44-46. NOT NULL, FK to `budget.accounts.id`. Indexed (line 25). PASS.
- `transfer_id`: Lines 75-78. Nullable, FK to `budget.transfers.id` with `ondelete="CASCADE"`.
  Partial indexed (lines 26-30). PASS.
- `transfer` relationship: Lines 99-103. Backref `shadow_transactions` with
  `passive_deletes=True`. Lazy "select". PASS.
- No unexpected changes to other columns. PASS.

**10b. Transfer model:**
- `category_id`: Lines 65-67. Nullable, FK to `budget.categories.id`. PASS.
- `category` relationship: Line 87. Lazy "joined". PASS.
- No other unexpected changes. PASS.

**10c. TransferTemplate model:**
- `category_id`: Lines 49-51. Nullable, FK to `budget.categories.id`. PASS.
- `category` relationship: Line 67. Lazy "joined". PASS.
- No other unexpected changes. PASS.

**10d. Marshmallow schemas:**
- `TransferTemplateCreateSchema`: `category_id` field at line 327. PASS.
- `TransferTemplateUpdateSchema`: Inherits from create schema, overrides at line 361. PASS.
- `TransferCreateSchema`: `category_id` field at line 382. PASS.
- `TransferUpdateSchema`: `category_id` field at line 404. PASS.
- `TransactionCreateSchema`: No `transfer_id` field. BaseSchema uses `EXCLUDE` for unknown
  fields. Users cannot set transfer_id via form submission. PASS.
- `TransactionUpdateSchema`: No `transfer_id` field. Same EXCLUDE behavior. PASS.
- `InlineTransactionCreateSchema`: No `transfer_id` field. PASS.

---

### Analysis 11: Dead Code -- PASS

`grep -rn "xfer_by_period|has_any_transfers|section-banner-transfer|_sum_transfer_effects" app/`
returns zero matches (excluding __pycache__ and test files). All dead code from the old
dual-path architecture has been removed. PASS.

Transfer model imports in files that should only work with transactions:
- `chart_data_service.py`: No Transfer import. PASS.
- `balance_calculator.py`: No Transfer import. PASS.
- `grid.py`: No Transfer import. PASS.
- `investment_projection.py`: No Transfer import. PASS.

The `transactions.py` route file imports Transfer (line 16) for the full-edit guard that loads
the parent transfer. This is correct -- the route needs to load the Transfer to render its edit
form.

The `_transfer_cell.html` template still exists and is used by transfer routes. It is not dead
code -- it serves the transfer management CRUD pages. PASS.

Keyboard navigation exclusion in `app.js` `getDataRows()` (lines 360-367): Correctly excludes
`subtotal-row` and `net-cash-flow-row` alongside the existing exclusions. PASS.

`summary-row` CSS class: Zero matches in app/. Removed. PASS.

---

## Conclusion

The Transfer Architecture Rework is **production-ready with one high-priority issue to address.**

The core financial engine is correct. All five design-document invariants are enforced and tested.
The balance calculator correctly processes shadow transactions without double-counting. The
transfer service is well-structured with comprehensive validation. Route guards correctly
intercept shadow transaction mutations. The carry forward service atomically moves transfers and
shadows. The recurrence engine delegates all creation to the service.

**Before deployment, fix H1** (transfer edit form response rendering wrong template for shadow
cells). This is a user-facing bug that makes shadow transaction cells non-interactive after
editing via the full edit popover. The simplest fix is to have the transfer routes return
`HX-Trigger: gridRefresh` instead of `balanceChanged` when the request originated from a shadow
context, forcing a full page reload that restores correct cell content.

**M1 (service bypass in reactivate) and M2 (misleading error on soft-deleted transfers)** are
low-risk architectural issues that should be addressed before the next major feature work but
do not block the current deployment.

**M3 (missing docstrings) and T1-T9 (missing tests)** should be addressed incrementally.
None represent functional bugs -- they are coverage gaps that reduce confidence in future
regression detection.

The 16 findings total break down as:
- **Financial correctness:** Zero issues. All calculations are correct.
- **Data integrity:** Zero issues. All invariants are enforced.
- **UI/UX:** 1 high + 2 low issues (H1, L2, L3) affecting the full edit popover flow.
- **Code quality:** 3 medium + 1 low issues (M1, M2, M3, L1) for architectural cleanliness.
- **Test gaps:** 9 missing tests (T1-T9) for defense-in-depth coverage.
