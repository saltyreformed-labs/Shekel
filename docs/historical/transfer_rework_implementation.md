> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Section 3A -- Transfer Architecture Rework

**Version:** 1.0
**Date:** March 25, 2026
**Prerequisite:** All Section 3 (Critical Bug Fixes) changes are implemented, tested, and merged.
**Design Document:** `docs/transfer_rework_design.md` v1.1
**Codebase Inventory:** `docs/transfer_rework_inventory.md` (commit f1cfa27)
**Scope:** Phase 3A-I (Shadow Transaction Architecture, Tasks 1-12) and Phase 3A-II (Grid Subtotals and Footer Condensation, Tasks 13-16).

---

## Grid Interaction Inventory

Every task that modifies the grid must be verified against this inventory. This catalog is reproduced from `docs/implementation_plan_section4.md` and extended with transfer-specific interactions.

### GI-1: Transaction Cell Click -> Quick Edit
- **Element:** `<div class="txn-cell" hx-get="/transactions/<id>/quick-edit" hx-trigger="click" hx-target="#txn-cell-<id>" hx-swap="innerHTML">`
- **Handler:** `transactions.get_quick_edit` (`app/routes/transactions.py:68`)
- **Response:** Renders `grid/_transaction_quick_edit.html`.

### GI-2: Quick Edit Submit -> Update Transaction
- **Element:** `<form hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">`
- **Handler:** `transactions.update_transaction` (`app/routes/transactions.py:89`)
- **Side effects:** `HX-Trigger: balanceChanged`.

### GI-3: Quick Edit Expand -> Full Edit Popover
- **Element:** `<button class="txn-expand-btn" data-txn-id="<id>">`. JS `fetch('/transactions/<id>/full-edit')` in `grid_edit.js:74`.
- **Handler:** `transactions.get_full_edit` (`app/routes/transactions.py:78`)

### GI-4: Full Edit Submit -> Update Transaction
- **Element:** `<form hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">`
- **Handler:** Same as GI-2. Side effect: `HX-Trigger: balanceChanged`.

### GI-5: Mark Done (Full Edit Popover)
- **Element:** `<button hx-post="/transactions/<id>/mark-done" hx-target="#txn-cell-<id>">`
- **Handler:** `transactions.mark_done` (`app/routes/transactions.py:125`)
- **Side effects:** `HX-Trigger: gridRefresh` (full page reload).

### GI-6: Mark Credit (Full Edit Popover)
- **Element:** `<button hx-post="/transactions/<id>/mark-credit">`
- **Handler:** `transactions.mark_credit` (`app/routes/transactions.py:159`)
- **Side effects:** `HX-Trigger: gridRefresh`. Creates payback via `credit_workflow`.

### GI-7: Undo Credit
- **Element:** `<button hx-delete="/transactions/<id>/unmark-credit">`
- **Handler:** `transactions.unmark_credit` (`app/routes/transactions.py:176`)
- **Side effects:** `HX-Trigger: gridRefresh`.

### GI-8: Cancel Transaction
- **Element:** `<button hx-post="/transactions/<id>/cancel">`
- **Handler:** `transactions.cancel_transaction` (`app/routes/transactions.py:193`)
- **Side effects:** `HX-Trigger: gridRefresh`.

### GI-9: Empty Cell Click -> Quick Create
- **Element:** `<div hx-get="/transactions/new/quick?..." hx-trigger="click" hx-target="closest td">`
- **Handler:** `transactions.get_quick_create` (`app/routes/transactions.py:211`)

### GI-10: Balance Row Refresh
- **Element:** `<tfoot id="grid-summary" hx-get="/grid/balance-row" hx-trigger="balanceChanged from:body" hx-swap="outerHTML">`
- **Handler:** `grid.balance_row` (`app/routes/grid.py:212`)

### GI-11: Anchor Click -> Edit Form
- **Handler:** `accounts.anchor_form` (`app/routes/accounts.py:508`)

### GI-12: Anchor Save
- **Handler:** `accounts.true_up` (`app/routes/accounts.py:453`). Side effect: `HX-Trigger: balanceChanged`.

### GI-13: Anchor Cancel
- **Handler:** `accounts.anchor_display` (`app/routes/accounts.py:523`)

### GI-14: Carry Forward
- **Element:** `<form hx-post="/pay-periods/<id>/carry-forward" hx-swap="none">`
- **Handler:** `transactions.carry_forward` (`app/routes/transactions.py:417`)
- **Side effects:** `HX-Trigger: gridRefresh`.

### GI-15: Inline Create Submit
- **Element:** `<form hx-post="/transactions/inline" hx-target="closest td">`
- **Handler:** `transactions.create_inline` (`app/routes/transactions.py:320`)
- **Side effects:** `HX-Trigger: balanceChanged`.

### GI-16: Add Transaction Modal Submit
- **Element:** `<form hx-post="/transactions" hx-swap="none">`
- **Handler:** `transactions.create_transaction` (`app/routes/transactions.py:368`)
- **Side effects:** Page reload via JS.

### GI-17: Date Range Controls
- Standard `<a href>` links. Full page load. Not HTMX.

### GI-18: Keyboard Navigation
- JS `getDataRows()` in `app.js:357-366`. Excludes: `section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`.

### GI-19: Transfer Cell Click -> Quick Edit
- **Element:** `<div hx-get="/transfers/<id>/quick-edit" hx-target="#xfer-cell-<id>">`
- **Handler:** `transfers.get_quick_edit` (`app/routes/transfers.py:387`)
- **RETIRED by Phase 3A-I.** Shadow transactions use GI-1.

### GI-20: Quick Edit Escape -> Revert
- JS in `grid_edit.js:214-265`. Calls `/transactions/<id>/cell` or `/transactions/empty-cell`.

### GI-21: F2 -> Full Edit/Create
- JS in `grid_edit.js:182-211`. Calls `/transactions/<id>/full-edit` or `/transactions/new/full`.

---

## SECTION A: Current State Analysis

---

### Task 1: Schema -- Add `account_id` to Transactions

**A1. Current behavior.**

The Transaction model (`app/models/transaction.py`) has 15 columns. There is no `account_id` column. Transactions are associated with accounts indirectly through `template_id -> budget.transaction_templates.account_id`. The TransactionTemplate model (`app/models/transaction_template.py:28-29`) has `account_id = db.Column(db.Integer, db.ForeignKey("budget.accounts.id"), nullable=False)`.

When the grid needs to show "all transactions for the checking account," it loads all transactions for visible periods and the template's `account_id` determines which account each belongs to. This works because every regular transaction has a `template_id`. Ad-hoc transactions (created inline or via the modal) also have their `template_id` set to NULL currently -- their account association is implicit from the grid context.

Transaction creation paths that must set the new column:
1. **Recurrence engine** (`app/services/recurrence_engine.py:138-149`): Creates Transaction with `template_id=template.id` but no `account_id`. Source: `template.account_id`.
2. **Inline create** (`app/routes/transactions.py:320-363`): Creates Transaction from form data `**data`. No `account_id` in form.
3. **Modal create** (`app/routes/transactions.py:368-392`): Creates Transaction from form data. No `account_id`.
4. **Credit workflow payback** (`app/services/credit_workflow.py:102-112`): Creates payback Transaction with `template_id=None`. Source: original transaction's `account_id` (once it exists).

**A2. What is wrong and why it matters.**

Without `account_id` on the transaction itself, shadow transactions (which have `template_id=NULL`) cannot be associated with an account. The grid query would require a conditional join: for regular transactions join through template, for shadows join through transfer to determine direction. This is fragile and error-prone. Adding `account_id` directly to transactions eliminates this and simplifies all account-filtered queries.

**A3. Impact on payday workflow.**

This is a foundational schema change that enables all subsequent tasks. Without it, shadow transactions cannot participate in account-scoped balance calculations or grid rendering.

---

### Task 2: Schema -- Add `transfer_id` to Transactions, `category_id` to Transfers/Templates

**A1. Current behavior.**

The Transaction model has no `transfer_id` column. There is no FK relationship between transactions and transfers. The Transfer model (`app/models/transfer.py`) has no `category_id` column. The TransferTemplate model (`app/models/transfer_template.py`) has no `category_id` column.

Transfers currently appear only in the TRANSFERS section of the grid and in the balance calculator's separate transfer logic. They have no category association, so they cannot appear in category-based charts.

**A2. What is wrong and why it matters.**

Without `transfer_id` on transactions, there is no way to identify which transactions are shadow transactions linked to a transfer. Without `category_id` on transfers, shadow expense transactions cannot be categorized (e.g., a mortgage transfer cannot appear under "Home: Mortgage Payment" in spending charts).

**A3. Impact on payday workflow.**

This is a prerequisite for shadow transactions. The `transfer_id` column is the marker that distinguishes shadow transactions from regular ones, enabling transfer detection guards on all mutation routes.

---

### Task 3: Seed Script -- Add Default Transfer Categories

**A1. Current behavior.**

Default categories are defined in `app/services/auth_service.py:22-45` as `DEFAULT_CATEGORIES`. The list includes 22 categories spanning Income, Home, Auto, Family, Health, Financial, and Credit Card groups. There are no "Transfers" group categories. The closest existing category is `("Financial", "Savings Transfer")`.

The `seed_user.py` script and the `register_user()` function both use `DEFAULT_CATEGORIES` to seed categories for new users.

**A2. What is wrong and why it matters.**

When the transfer service creates shadow transactions, the income-side shadow needs a default category. Without a seeded "Transfers: Incoming" category, income-side shadows would have `category_id=NULL`, which means they would appear as uncategorized items in charts and grid grouping.

**A3. Impact on payday workflow.**

Indirect. Without default categories, shadow transactions lack proper grouping in the grid's INCOME section.

---

### Task 4: Transfer Service -- Create, Update, Delete with Shadow Transactions

**A1. Current behavior.**

There is no `app/services/transfer_service.py`. Transfer mutations are handled directly in route handlers:
- **Create ad-hoc** (`app/routes/transfers.py:442-476`): Constructs a `Transfer(user_id=current_user.id, status_id=projected.id, **data)` and adds to session.
- **Update** (`app/routes/transfers.py:410-437`): Loads transfer, applies validated fields directly, sets `is_override=True` if amount changed.
- **Delete** (`app/routes/transfers.py:481-494`): Soft-delete if template-linked (`is_deleted=True`), hard-delete if ad-hoc (`db.session.delete(xfer)`).
- **Mark done** (`app/routes/transfers.py:502-518`): Sets `status_id` to done.
- **Cancel** (`app/routes/transfers.py:523-539`): Sets `status_id` to cancelled.

No shadow transactions are created. No invariant enforcement exists.

**A2. What is wrong and why it matters.**

All transfer mutations operate only on the `budget.transfers` table. No linked transactions are created, updated, or deleted. This is the core architectural gap that this rework addresses.

**A3. Impact on payday workflow.**

After this task, every transfer mutation atomically creates/updates/deletes two shadow transactions, making transfers visible in the transaction-based grid and balance calculation.

---

### Task 5: Wire Transfer Recurrence Engine to Transfer Service

**A1. Current behavior.**

The transfer recurrence engine (`app/services/transfer_recurrence.py:96-108`) creates Transfer objects directly via the ORM constructor:

```python
xfer = Transfer(
    user_id=template.user_id,
    transfer_template_id=template.id,
    from_account_id=template.from_account_id,
    to_account_id=template.to_account_id,
    pay_period_id=period.id,
    scenario_id=scenario_id,
    status_id=projected_status.id,
    name=template.name,
    amount=template.default_amount,
    is_override=False,
    is_deleted=False,
)
db.session.add(xfer)
```

This bypasses any shadow transaction creation logic.

**A2. What is wrong and why it matters.**

Recurring transfers generated by the recurrence engine produce Transfer records without shadow transactions. These transfers appear in the TRANSFERS grid section and the balance calculator's transfer path, but after the rework they would be invisible to the transaction-only balance calculator.

**A3. Impact on payday workflow.**

Without this change, recurring transfers (the majority of transfers) would not produce shadow transactions, meaning they would not appear in the INCOME/EXPENSES grid sections or affect balances.

---

### Task 6: Wire Transfer Routes to Transfer Service

**A1. Current behavior.**

All transfer route handlers in `app/routes/transfers.py` directly manipulate Transfer ORM objects. The `create_ad_hoc` handler (line 442-476), `update_transfer` (line 410-437), `delete_transfer` (line 481-494), `mark_done` (line 502-518), and `cancel_transfer` (line 523-539) all work directly on Transfer records without creating or updating shadow transactions.

The template create/edit forms (`app/templates/transfers/form.html`) have no category dropdown. The transfer full edit form (`app/templates/transfers/_transfer_full_edit.html`) has no category field.

**A2. What is wrong and why it matters.**

Route-level transfer mutations bypass the transfer service, creating transfers without shadows. Template forms lack category support, preventing users from categorizing transfers for chart reporting.

**A3. Impact on payday workflow.**

Ad-hoc transfers (one-time transfers created via the "Add Transfer" button) and all transfer status changes must go through the transfer service to maintain shadow transaction sync.

---

### Task 7: Transaction Route Guards for Shadow Transactions

**A1. Current behavior.**

Transaction mutation routes in `app/routes/transactions.py` operate on any transaction regardless of whether it is a shadow transaction. There is no check for `transfer_id`. The `mark_credit` route (line 159-171) would allow marking a shadow transaction as "credit," which makes no sense for transfers. The `delete_transaction` route (line 397-412) would allow deleting one shadow without its sibling. The `update_transaction` route (line 89-120) would allow changing a shadow's amount independently of the parent transfer.

**A2. What is wrong and why it matters.**

Without guards, a user clicking a shadow transaction cell could trigger operations that violate the invariant that shadow transactions always match their parent transfer. This is the "direct shadow mutation" risk from design document section 16.3.

**A3. Impact on payday workflow.**

During step 5 (mark expenses done), if the user clicks a shadow transaction and clicks "Done," it must update the parent transfer and both shadows -- not just the clicked transaction. During step 6 (mark credit), the "Credit" button must not appear for shadow transactions.

---

### Task 8: Carry Forward with Shadow Transaction Detection

**A1. Current behavior.**

The carry forward service (`app/services/carry_forward_service.py:20-96`) queries all projected, non-deleted transactions in the source period and moves them by updating `pay_period_id`. It has no awareness of `transfer_id`. After the rework, shadow transactions will be among the projected transactions returned by this query.

The current logic (lines 82-89):
```python
for txn in projected_txns:
    txn.pay_period_id = target_period_id
    if txn.template_id is not None:
        txn.is_override = True
    count += 1
```

**A2. What is wrong and why it matters.**

If carry forward moves one shadow transaction without moving its sibling and the parent transfer, the sync invariant (design section 4.5, invariant 5) is violated. The expense shadow would be in the new period while the income shadow and parent transfer remain in the old period.

**A3. Impact on payday workflow.**

Step 4 (carry forward unpaid) is a first-class operation performed every payday. If transfers were not fully carried forward, the user would see inconsistent data.

---

### Task 9: Balance Calculator -- Remove Transfer-Specific Logic

**A1. Current behavior.**

The balance calculator (`app/services/balance_calculator.py`) accepts an optional `transfers` parameter (line 31). When provided with `account_id`, it calls `_sum_transfer_effects_remaining()` (lines 80-82) and `_sum_transfer_effects_all()` (lines 90-91) to factor transfer IN/OUT effects into the running balance.

The four transfer-related functions are:
- `_sum_transfer_effects_remaining()` (lines 351-374): Sums projected transfer IN/OUT for anchor period.
- `_sum_transfer_effects_all()` (lines 377-397): Sums projected transfer IN/OUT for post-anchor periods.

The amortization variant (`calculate_balances_with_amortization`, lines 192-286) has its own transfer logic at lines 260-267 where it detects transfers TO the loan account and splits payments into principal/interest.

All three public functions pass `transfers` through: `calculate_balances()` (line 30), `calculate_balances_with_interest()` (line 127), `calculate_balances_with_amortization()` (line 193).

**A2. What is wrong and why it matters.**

This is the single highest-risk change. After shadow transactions exist, the balance calculator must query ONLY `budget.transactions`. If it still processes the `transfers` parameter, every transfer is double-counted (once through shadow transactions, once through the transfer path). If shadow transactions are not correctly set up and the old path is removed, transfers are not counted at all.

**A3. Impact on payday workflow.**

Step 7 (check projections) relies entirely on the balance calculator producing correct projected end balances. An error here means every balance in the grid is wrong.

---

### Task 10: Grid Rendering -- Remove TRANSFERS Section, Add Transfer Indicator

**A1. Current behavior.**

The grid template (`app/templates/grid/grid.html`) renders three sections:
- INCOME (lines 99-160)
- EXPENSES (lines 167-224)
- TRANSFERS (lines 226-282, conditional on `has_any_transfers`)

The TRANSFERS section groups by `xfer.name or 'Ad-hoc Transfer'` and renders via `transfers/_transfer_cell.html`. The grid route (`app/routes/grid.py:94-102`) loads Transfer objects separately and passes them as `xfer_by_period`.

The balance row template (`app/templates/grid/_balance_row.html:44-69`) has a conditional "Net Transfers" row.

**A2. What is wrong and why it matters.**

After the rework, shadow transactions appear automatically in INCOME/EXPENSES sections. The separate TRANSFERS section would show duplicate information and must be removed. The Net Transfers footer row becomes redundant since transfer effects are included in Total Income and Total Expenses.

**A3. Impact on payday workflow.**

Steps 5-7 (mark expenses, mark credit, check projections) involve scanning the grid. Transfer-linked items must appear inline with other transactions for a unified view.

---

### Task 11: Chart and Reporting Verification

**A1. Current behavior.**

The chart data service (`app/services/chart_data_service.py`) queries Transfer objects at lines 214-219 in `_calculate_account_balances()` and passes them to the balance calculator. Category-based charts (`get_spending_by_category`, `get_budget_vs_actuals`) query only `budget.transactions` and do not include transfers.

**A2. What is wrong and why it matters.**

After the rework, `_calculate_account_balances()` must stop querying transfers (to avoid double-counting). Category-based charts will automatically include transfer-linked expense transactions since they are now regular transactions with `category_id` values.

**A3. Impact on payday workflow.**

Charts are not part of the payday workflow but are used for financial review. Correct category reporting (e.g., mortgage payment under "Home") is a primary goal of this rework.

---

### Task 12: Cleanup -- Retire Dead Code, Remove Unused Templates

**A1. Current behavior.**

Several files and code paths will be dead after the rework:
- `transfers/_transfer_cell.html` -- No longer rendered in the grid.
- `transfers/_transfer_empty_cell.html` -- No longer rendered.
- Transfer-specific queries in `app/routes/savings.py`, `app/routes/investment.py`, `app/routes/retirement.py`.
- Transfer-specific logic in `app/services/investment_projection.py`.
- CSS for `.section-banner-transfer`.
- JS function `openTransferFullEdit()` in `grid_edit.js` (partially -- still used when server routes to transfer form, but entry point changes).

**A2. What is wrong and why it matters.**

Dead code is a maintenance burden and a source of confusion for future implementers.

**A3. Impact on payday workflow.**

None directly. This is housekeeping.

---

### Task 13: Phase 3A-II -- Inline Subtotal Rows

**A1. Current behavior.**

Total Income and Total Expenses are rendered in the `<tfoot>` (`grid/_balance_row.html:13-42`). They are sticky rows at the bottom of the grid.

**A2. What is wrong and why it matters.**

Sticky footer rows consume 120-175px of vertical space. Moving subtotals to the tbody as inline rows frees this space.

**A3. Impact on payday workflow.**

More transaction rows visible without scrolling during the reconciliation workflow.

---

### Task 14: Phase 3A-II -- Net Cash Flow Row

**A1. Current behavior.**

The "Net (Income - Expenses)" row is in the `<tfoot>` (`grid/_balance_row.html:72-94`).

**A2. What is wrong and why it matters.**

This row is secondary information. Making it a non-sticky tbody row reduces footer height.

**A3. Impact on payday workflow.**

Secondary reference number becomes scrollable content rather than consuming fixed space.

---

### Task 15: Phase 3A-II -- Footer Condensation

**A1. Current behavior.**

The `<tfoot>` contains 4-5 rows. The `<tfoot id="grid-summary">` element has HTMX attributes for the balance row refresh (GI-10).

**A2. What is wrong and why it matters.**

Only Projected End Balance is essential as a sticky footer. All other rows have been moved to tbody (Tasks 13-14).

**A3. Impact on payday workflow.**

Step 7 (check projections) -- the primary number (Projected End Balance) remains sticky and visible.

---

### Task 16: Phase 3A-II -- Database Rebuild

Not a code task. Operational procedure for rebuilding the production database.

---

## SECTION B: Solution Description

---

### Task 1: Schema -- Add `account_id` to Transactions

**B1. Solution description.**

Add a NOT NULL FK column `account_id` to `budget.transactions`:

```python
# In app/models/transaction.py
account_id = db.Column(
    db.Integer,
    db.ForeignKey("budget.accounts.id"),
    nullable=False,
)
```

Add index:
```python
db.Index("idx_transactions_account", "account_id"),
```

Add relationship:
```python
account = db.relationship("Account", lazy="joined")
```

Update every transaction creation path:
1. **`app/services/recurrence_engine.py:138`** -- Add `account_id=template.account_id` to Transaction constructor.
2. **`app/routes/transactions.py:320` (`create_inline`)** -- Add `account_id` from form data (pass via hidden field from grid context).
3. **`app/routes/transactions.py:368` (`create_transaction`)** -- Add `account_id` from form data or grid account resolver.
4. **`app/services/credit_workflow.py:102`** -- Add `account_id=txn.account_id` to payback Transaction constructor (using the original transaction's account_id, which exists after this migration).
5. **`app/templates/grid/_transaction_quick_create.html`** -- Add hidden `<input name="account_id" value="{{ account.id }}">`.
6. **`app/templates/grid/_transaction_full_create.html`** -- Add hidden `account_id` field.

Update the grid transaction query to use the new column. The current query in `app/routes/grid.py:83-91` loads ALL transactions for visible periods and scenario without any account filter:

```python
# CURRENT (grid.py:83-91):
all_transactions = (
    db.session.query(Transaction)
    .filter(
        Transaction.pay_period_id.in_(period_ids),
        Transaction.scenario_id == scenario.id,
        Transaction.is_deleted.is_(False),
    )
    .all()
)
```

This works today because the grid template matches transactions to categories, and all transactions are for the single user regardless of account. After the rework, this query still works -- shadow transactions have the correct `category_id` and `transaction_type_id`, so the template's category-matching logic (`txn.category_id == category.id and txn.is_income`) includes them automatically. No account filter is needed on the grid query because the grid displays transactions from all accounts grouped by category, not by account.

However, the balance calculator call (`grid.py:110-117`) currently passes `account_id` so the calculator knows which account's transfers are incoming vs outgoing. After the rework, `account_id` is no longer needed by the calculator (shadow transactions already have the correct type -- expense for outgoing, income for incoming). The `account_id` parameter to the balance calculator is removed in Task 9.

The same applies to the `balance_row()` handler (`grid.py:238-260`). Both handlers have the same query pattern and both are updated in Task 9.

**Important note for implementers:** The grid query does NOT need an `account_id` filter. The grid shows all transactions for the user's scenario. Account-scoped filtering is used only in the savings/investment/retirement dashboards, not in the main budget grid.

Generate Alembic migration:
```
flask db migrate -m "add account_id to transactions"
```

**B2. Design decision rationale.**

The design document (section 3.1) specifies this as a denormalization for query performance and for supporting template-less transactions (shadow transactions, ad-hoc transactions). The `NOT NULL` constraint ensures every transaction belongs to an account.

**B3. Invariants that must hold.**

- Every transaction has a non-null `account_id`.
- For template-generated transactions, `account_id` equals `template.account_id` at creation time.
- For credit payback transactions, `account_id` equals the original transaction's `account_id`.

**B4. Dependencies.**

- No prerequisites. This is the first task.
- Enables: Tasks 2, 4, 7, 9, 10 (all depend on account_id existing).

**B5. HTMX regression analysis.**

- **GI-1 through GI-8:** NOT AFFECTED. These target `#txn-cell-<id>` by transaction ID. The addition of `account_id` to the model does not change the DOM structure or HTMX attributes.
- **GI-9 (Empty cell click):** AFFECTED. The quick-create form must include `account_id` as a hidden field. The handler must accept and save it.
- **GI-10 (Balance row refresh):** NOT AFFECTED. The balance row endpoint does not change structure yet.
- **GI-11 through GI-13:** NOT AFFECTED. Anchor balance interactions are on accounts, not transactions.
- **GI-14 (Carry forward):** NOT AFFECTED. Carry forward moves transactions but does not create them.
- **GI-15 (Inline create submit):** AFFECTED. The create handler must save `account_id` from form data.
- **GI-16 (Add transaction modal):** AFFECTED. The handler must accept `account_id`.
- **GI-17 through GI-21:** NOT AFFECTED.

**B6. Edge cases.**

- **Existing tests creating transactions without account_id:** All existing tests that create Transaction objects will break because `account_id` is NOT NULL. Every test fixture and test that creates a Transaction must be updated to include `account_id`. This is a large ripple but is mechanical.
- **Ad-hoc transactions with no template:** The `account_id` is passed from the grid's current account context via the hidden form field. If the grid has no account resolved, the create routes must reject the request (account_id is required).
- **Credit payback transaction:** The payback inherits `account_id` from the original transaction. The original transaction now has `account_id`, so this works.

**B7. Files to create or modify.**

- `app/models/transaction.py` -- Add column, index, relationship.
- `app/services/recurrence_engine.py` -- Add `account_id=template.account_id` to constructor.
- `app/services/credit_workflow.py` -- Add `account_id=txn.account_id` to payback constructor.
- `app/routes/transactions.py` -- Add `account_id` handling to `create_inline` and `create_transaction`.
- `app/templates/grid/_transaction_quick_create.html` -- Add hidden account_id field.
- `app/templates/grid/_transaction_full_create.html` -- Add hidden account_id field.
- `app/templates/grid/grid.html` -- Pass `account.id` to create templates.
- `migrations/versions/<new>_add_account_id_to_transactions.py` -- New migration.
- `tests/conftest.py` -- Update all fixtures that create Transaction objects to include `account_id`.
- All test files that create Transaction objects directly.

---

### Task 2: Schema -- Add `transfer_id` and `category_id`

**B1. Solution description.**

Add to Transaction model (`app/models/transaction.py`):
```python
transfer_id = db.Column(
    db.Integer,
    db.ForeignKey("budget.transfers.id", ondelete="CASCADE"),
)
```

Add partial index:
```python
db.Index(
    "idx_transactions_transfer",
    "transfer_id",
    postgresql_where=db.text("transfer_id IS NOT NULL"),
),
```

Add relationship:
```python
transfer = db.relationship("Transfer", backref="shadow_transactions", lazy="select")
```

Add to Transfer model (`app/models/transfer.py`):
```python
category_id = db.Column(
    db.Integer, db.ForeignKey("budget.categories.id")
)
category = db.relationship("Category", lazy="joined")
```

Add to TransferTemplate model (`app/models/transfer_template.py`):
```python
category_id = db.Column(
    db.Integer, db.ForeignKey("budget.categories.id")
)
category = db.relationship("Category", lazy="joined")
```

Generate migration.

**B2. Design decision rationale.**

- `transfer_id` with ON DELETE CASCADE ensures shadow transactions are automatically removed when the parent transfer is hard-deleted (design section 3.2).
- `category_id` on transfers enables shadow expense transactions to inherit a spending category (design section 3.3).
- `category_id` on transfer templates enables recurring instances to inherit the template's category (design section 3.4).

**B3. Invariants.**

- `transfer_id` is NULL for regular transactions, non-NULL for shadow transactions.
- ON DELETE CASCADE is the database-level safety net for invariant 2 (no orphaned shadows).

**B4. Dependencies.**

- Requires: Task 1 (account_id must exist first, since both columns are added in sequence).
- Enables: Task 4 (transfer service uses transfer_id to link shadows).

**B5. HTMX regression analysis.**

All interactions NOT AFFECTED. No DOM or route changes.

**B6. Edge cases.**

- **Existing transfers without category_id:** Nullable, so NULL is valid. No migration needed for existing data (database is being rebuilt anyway).
- **Deleting a transfer with CASCADE:** Hard-deleting a transfer auto-removes both shadows. Soft-deleting (is_deleted=True) must be handled explicitly in the transfer service.

**B7. Files to create or modify.**

- `app/models/transaction.py` -- Add transfer_id column, index, relationship.
- `app/models/transfer.py` -- Add category_id column, relationship.
- `app/models/transfer_template.py` -- Add category_id column, relationship.
- `app/schemas/validation.py` -- Add `category_id` field to `TransferTemplateCreateSchema`, `TransferTemplateUpdateSchema`, `TransferCreateSchema`, `TransferUpdateSchema`.
- `migrations/versions/<new>_add_transfer_id_and_category_id.py` -- New migration.

---

### Task 3: Seed Script -- Add Default Transfer Categories

**B1. Solution description.**

Add two entries to `DEFAULT_CATEGORIES` in `app/services/auth_service.py`:

```python
("Transfers", "Incoming"),
("Transfers", "Outgoing"),
```

Place them after the existing `("Financial", "Extra Debt Payment")` entry, before `("Credit Card", "Payback")`.

**B2. Design decision rationale.**

Design section 12.1 specifies "Transfers: Incoming" and "Transfers: Outgoing" as default categories. The "Transfers: Incoming" category is used as the default for all income-side shadow transactions. "Transfers: Outgoing" is the fallback for expense-side shadows with no user-specified category.

**B3. Invariants.** None specific to this task.

**B4. Dependencies.**

- No prerequisites.
- Enables: Task 4 (transfer service needs to look up the "Transfers: Incoming" category).

**B5. HTMX regression analysis.** Not applicable (no grid changes).

**B6. Edge cases.**

- **Existing users (after database rebuild):** The rebuild runs `seed_user.py` which uses `DEFAULT_CATEGORIES`, so existing users get the new categories.
- **`register_user()` path:** Also uses `DEFAULT_CATEGORIES`, so newly registered users get them too.

**B7. Files to create or modify.**

- `app/services/auth_service.py` -- Add two entries to `DEFAULT_CATEGORIES`.

---

### Task 4: Transfer Service -- Create, Update, Delete

**B1. Solution description.**

Create new file `app/services/transfer_service.py` with three functions:

**`create_transfer(user_id, from_account_id, to_account_id, pay_period_id, scenario_id, amount, status_id, category_id=None, notes=None, transfer_template_id=None, name=None)`:**

1. Validate: accounts exist and belong to user, from != to, period exists.
2. If no name provided, generate: `"Transfer to {to_account.name}"` / `"Transfer from {from_account.name}"` (these are for the shadow transaction names; the transfer itself gets the provided name or the to-account name).
3. Insert Transfer record.
4. Flush to get transfer.id.
5. Look up "expense" and "income" transaction_type_ids.
6. Look up "Transfers: Incoming" category for income-side shadow.
7. Insert expense shadow Transaction:
   - `account_id=from_account_id`
   - `template_id=None`
   - `transfer_id=transfer.id`
   - `pay_period_id=transfer.pay_period_id`
   - `scenario_id=transfer.scenario_id`
   - `status_id=transfer.status_id`
   - `name=f"Transfer to {to_account.name}"`
   - `category_id=transfer.category_id` (may be NULL)
   - `transaction_type_id=expense_type_id`
   - `estimated_amount=transfer.amount`
   - `is_override=False, is_deleted=False`
8. Insert income shadow Transaction:
   - `account_id=to_account_id`
   - `name=f"Transfer from {from_account.name}"`
   - `category_id=incoming_category.id` (the "Transfers: Incoming" category)
   - `transaction_type_id=income_type_id`
   - Everything else same as expense shadow.
9. Return the Transfer record.

**`update_transfer(transfer_id, user_id, **kwargs)`:**

Accepted kwargs: `amount`, `status_id`, `pay_period_id`, `category_id`, `notes`, `actual_amount`, `is_override`.

1. Load transfer. Verify `transfer.user_id == user_id`.
2. Update transfer fields from kwargs.
3. Query both shadow transactions: `Transaction.query.filter_by(transfer_id=transfer_id).all()`.
4. For each shadow:
   - If `amount` changed: update `estimated_amount`.
   - If `actual_amount` provided: update `actual_amount`.
   - If `status_id` changed: update `status_id`.
   - If `pay_period_id` changed: update `pay_period_id`.
   - If `category_id` changed: update on expense-side only.
   - If `is_override` set: update on both shadows.
5. Return updated transfer.

**`delete_transfer(transfer_id, user_id, soft=False)`:**

1. Load transfer. Verify ownership.
2. If soft: set `is_deleted=True` on transfer and both shadows.
3. If hard: `db.session.delete(transfer)` -- CASCADE removes shadows.

**B2. Design decision rationale.**

The transfer service is the single point of enforcement for all invariants (design section 4.1). Centralizing mutations prevents code duplication across routes and the recurrence engine.

**B3. Invariants that must hold.**

1. **Exactly two shadows per active transfer.** Verified by asserting `len(shadow_transactions) == 2` after creation. One expense, one income.
2. **No orphaned shadows.** Creation always creates both. Deletion always removes both.
3. **Amount sync.** Both shadows' `estimated_amount == transfer.amount`. Both `actual_amount` values match.
4. **Status sync.** Both shadows' `status_id == transfer.status_id`.
5. **Period sync.** Both shadows' `pay_period_id == transfer.pay_period_id`.
6. **Read-only shadows.** Enforced by route guards (Task 7), not by the service itself.
7. **No double-counting.** Enforced by balance calculator changes (Task 9), not by the service.

**B4. Dependencies.**

- Requires: Tasks 1, 2, 3 (schema and seed data).
- Enables: Tasks 5, 6, 7, 8, 9 (all consumers of the transfer service).

**B5. HTMX regression analysis.** Not applicable (service layer, no grid changes).

**B6. Edge cases.**

- **Transfer with category_id=NULL:** Valid. Expense shadow gets `category_id=NULL`. Income shadow gets "Transfers: Incoming". The transfer still works; it just won't appear in category-filtered charts.
- **Transfer with amount=0 or negative:** Blocked by database CHECK constraint `ck_transfers_positive_amount`. Service should validate before insert to give a clear error.
- **from_account == to_account:** Blocked by database CHECK constraint `ck_transfers_different_accounts`. Service should validate.
- **Update a cancelled transfer:** Allowed. The user might want to uncancel. Status changes are propagated to shadows.
- **Delete a transfer whose shadows have been manually tampered with:** CASCADE or explicit deletion handles this. The service does not assume shadow integrity; it enforces it.
- **actual_amount on transfer:** The Transfer model has no `actual_amount` column (inventory section 9.1). The service accepts `actual_amount` in `update_transfer` and applies it to both shadow transactions. The Transfer model does not store it -- the shadow transactions are the authoritative source for `actual_amount`. This is the recommended approach from the inventory (option 2, simpler).

**B7. Files to create or modify.**

- `app/services/transfer_service.py` -- New file.
- `tests/test_services/test_transfer_service.py` -- New file.

---

### Task 5: Wire Transfer Recurrence Engine

**B1. Solution description.**

In `app/services/transfer_recurrence.py`, replace the direct Transfer ORM construction (lines 96-108) with a call to `transfer_service.create_transfer()`:

```python
# BEFORE (lines 96-108):
xfer = Transfer(
    user_id=template.user_id,
    ...
)
db.session.add(xfer)

# AFTER:
from app.services import transfer_service
xfer = transfer_service.create_transfer(
    user_id=template.user_id,
    from_account_id=template.from_account_id,
    to_account_id=template.to_account_id,
    pay_period_id=period.id,
    scenario_id=scenario_id,
    amount=template.default_amount,
    status_id=projected_status.id,
    category_id=template.category_id,
    name=template.name,
    transfer_template_id=template.id,
)
```

The rest of the recurrence logic (period matching, existing entry checks, override/delete handling) remains unchanged.

In `regenerate_for_template()` (lines 120-187), the deletion of old transfers at lines 147-155 must also delete their shadows. If the deletion is hard-delete, CASCADE handles it. If soft-delete (setting `is_deleted=True`), use `transfer_service.delete_transfer(xfer.id, template.user_id, soft=True)` for each.

**B2. Design decision rationale.**

Design section 5.2 specifies this as the minimal change: "Only the final step of creating the transfer record changes from a direct insert to a service call."

**B3. Invariants.**

- All five shadow transaction invariants apply to recurrence-generated transfers.
- The one-time transfer bug (design section 1.4) is automatically fixed because ad-hoc creation now goes through the service.

**B4. Dependencies.**

- Requires: Task 4 (transfer service must exist).
- Enables: Task 9 (balance calculator can rely on shadows existing for all transfers).

**B5. HTMX regression analysis.** Not applicable (service layer change, not grid).

**B6. Edge cases.**

- **Template with category_id=NULL:** Shadow transactions get NULL expense category, "Transfers: Incoming" income category. Valid.
- **Regeneration with overridden transfers:** The existing override handling is unchanged. The RecurrenceConflict exception is still raised. The only change is how new transfers are created.
- **Regeneration deleting old transfers:** CASCADE deletes shadows for hard-deleted transfers. For soft-deleted, the transfer service sets `is_deleted=True` on shadows.

**B7. Files to create or modify.**

- `app/services/transfer_recurrence.py` -- Replace direct Transfer construction with service call.
- `tests/test_services/test_transfer_recurrence.py` -- Update assertions to verify shadow transactions exist after generation.

---

### Task 6: Wire Transfer Routes to Transfer Service

**B1. Solution description.**

Modify every mutation handler in `app/routes/transfers.py`:

1. **`create_ad_hoc` (line 442-476):** Replace `Transfer(...)` with `transfer_service.create_transfer(...)`.
2. **`update_transfer` (line 410-437):** Replace direct field updates with `transfer_service.update_transfer(xfer.id, current_user.id, **validated_data)`.
3. **`delete_transfer` (line 481-494):** Replace with `transfer_service.delete_transfer(xfer.id, current_user.id, soft=bool(xfer.transfer_template_id))`.
4. **`mark_done` (line 502-518):** Replace with `transfer_service.update_transfer(xfer.id, current_user.id, status_id=done_status.id)`.
5. **`cancel_transfer` (line 523-539):** Replace with `transfer_service.update_transfer(xfer.id, current_user.id, status_id=cancelled_status.id)`.
6. **`create_transfer_template` (line 93-180):** Add `category_id` from form data to template creation.
7. **`update_transfer_template` (line 212-299):** Add `category_id` handling.

Add category dropdown to templates:
- `app/templates/transfers/form.html` -- Add `<select name="category_id">` with categories.
- `app/templates/transfers/_transfer_full_edit.html` -- Add category dropdown.

Update template deletion to cascade to shadow transactions:
- **`delete_transfer_template` (`transfers.py:302-327`):** Currently soft-deletes projected transfers via a bulk `UPDATE ... SET is_deleted=True`. After the rework, this must also soft-delete the shadow transactions for each affected transfer. Replace the bulk update with a loop that calls `transfer_service.delete_transfer(xfer.id, user.id, soft=True)` for each projected transfer linked to the template. This ensures shadows are marked `is_deleted=True` alongside their parent transfers.
- **`reactivate_transfer_template` (`transfers.py:330-366`):** Currently restores soft-deleted transfers. After the rework, the restore must also un-delete shadow transactions. Replace the bulk `UPDATE ... SET is_deleted=False` with a loop that clears `is_deleted` on transfers and their shadows, or add a `restore_transfer(transfer_id, user_id)` function to the transfer service.
- **Three-level cascade verification:** Template deactivation -> Transfer soft-delete -> Shadow soft-delete. This is a three-level cascade handled explicitly by the service (not by database CASCADE, which only applies to hard deletes). The implementation must verify this works by testing: deactivate a template, confirm all its transfers AND all their shadows have `is_deleted=True`.

**B2. Design decision rationale.** Design section 9.2.

**B3. Invariants.** All service invariants are maintained because mutations go through the service.

**B4. Dependencies.** Requires: Task 4.

**B5. HTMX regression analysis.**

- **GI-19 (Transfer cell click):** Still works for the template management page but is retired from the grid (handled in Task 10).
- **GI-10 (Balance row refresh):** Transfer mutations that return `HX-Trigger: balanceChanged` continue to trigger the refresh. SAFE.
- All other GI interactions: NOT AFFECTED (they target transaction endpoints, not transfer endpoints).

**B6. Edge cases.**

- **Double-submit on ad-hoc create:** The existing idempotency handling catches duplicate names. Shadows are created atomically with the transfer, so no partial state.
- **Update with no changes:** The service accepts empty kwargs and makes no changes. SAFE.
- **Category dropdown with zero categories:** The dropdown has an empty option. `category_id=NULL` is valid.

**B7. Files to create or modify.**

- `app/routes/transfers.py` -- Modify all mutation handlers.
- `app/templates/transfers/form.html` -- Add category dropdown.
- `app/templates/transfers/_transfer_full_edit.html` -- Add category field.
- `app/schemas/validation.py` -- Add `category_id` to transfer schemas (done in Task 2).
- `tests/test_routes/test_transfers.py` -- Update assertions for shadow transaction creation.

---

### Task 7: Transaction Route Guards

**B1. Solution description.**

Add a transfer detection guard to each transaction mutation handler in `app/routes/transactions.py`. The pattern:

```python
def update_transaction(txn_id):
    txn = _get_owned_transaction(txn_id)
    if not txn:
        return "", 404

    # Transfer detection guard
    if txn.transfer_id is not None:
        # Route through transfer service
        data = TransferUpdateSchema().load(request.form)
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id,
            amount=data.get("estimated_amount"),
            ...
        )
        return render_template("grid/_transaction_cell.html", ...)
```

**Field name mapping for shadow transaction quick edit:** The transaction quick edit form submits `estimated_amount` (the field name on the Transaction model). The transfer service's `update_transfer()` accepts `amount` (the field name on the Transfer model). The guard must translate: `estimated_amount` from the form becomes `amount` in the transfer service call. The service then sets `estimated_amount` on both shadow transactions. Concretely:

```python
if txn.transfer_id is not None:
    form_amount = validated_data.get("estimated_amount")
    if form_amount is not None:
        transfer_service.update_transfer(
            txn.transfer_id, current_user.id,
            amount=form_amount,  # form's estimated_amount -> service's amount
        )
```

Specific guards per handler:
- **`update_transaction`:** Route amount/status changes through `transfer_service.update_transfer()`. Map `estimated_amount` from form to `amount` in service call. Map `status_id` directly.
- **`mark_done`:** Route through `transfer_service.update_transfer(status_id=done_id)`.
- **`mark_credit`:** Return 400 with error message. Shadow transactions cannot be marked as credit.
- **`unmark_credit`:** Return 400. Shadows are never in credit status.
- **`cancel_transaction`:** Route through `transfer_service.update_transfer(status_id=cancelled_id)`.
- **`delete_transaction`:** Return 400. Must delete the parent transfer instead.
- **`get_full_edit`:** Detect `transfer_id`, load parent Transfer, return `transfers/_transfer_full_edit.html` instead of `grid/_transaction_full_edit.html`.

For `_transaction_cell.html` -- add transfer indicator:
```html
{% if t.transfer_id %}
  <i class="bi bi-arrow-left-right text-muted small" title="Transfer"></i>
{% endif %}
```

For `_transaction_full_edit.html` -- hide "Mark Credit" button for shadow transactions. This is handled by the route returning the transfer form instead, but as a belt-and-suspenders measure, add:
```html
{% if not txn.transfer_id %}
  {# Mark Credit button #}
{% endif %}
```

**B2. Design decision rationale.** Design sections 10.1 and 10.2.

**B3. Invariants.** Enforces invariant 6 (shadows are read-only from user perspective).

**B4. Dependencies.** Requires: Tasks 1, 2, 4.

**B5. HTMX regression analysis.**

- **GI-1 (Cell click -> quick edit):** AFFECTED for shadow transactions. The quick edit form still loads via `/transactions/<id>/quick-edit`. The form targets `#txn-cell-<id>`. The PATCH submit goes to `/transactions/<id>` which now detects `transfer_id` and routes through the transfer service. The response is still `_transaction_cell.html`. **SAFE** -- the DOM target and swap behavior are unchanged.
- **GI-2 (Quick edit submit):** AFFECTED. The PATCH handler detects `transfer_id` and routes through transfer service, updating both shadows. Returns updated cell. **SAFE** -- response format unchanged.
- **GI-3 (Expand to full edit):** AFFECTED. For shadow transactions, the server returns the transfer full edit form instead. The JS `openFullEdit(txnId)` fetches `/transactions/<id>/full-edit`. The server detects `transfer_id` and returns `transfers/_transfer_full_edit.html`. The popover still works because the JS inserts whatever HTML the server returns. **SAFE** -- but the form's HTMX attributes must target the correct cell.

  **Targeting detail:** The current `_transfer_full_edit.html` form targets `#xfer-cell-<xfer.id>`. When rendered for a shadow transaction click, it must target `#txn-cell-<txn.id>` instead. The implementation:
  1. The `get_full_edit` route in `transactions.py` detects `txn.transfer_id is not None`.
  2. It loads the parent Transfer: `xfer = db.session.get(Transfer, txn.transfer_id)`.
  3. It renders `transfers/_transfer_full_edit.html` with an extra context variable: `target_cell_id="txn-cell-" + str(txn.id)`.
  4. The `_transfer_full_edit.html` template uses this variable: `hx-target="#{{ target_cell_id }}"` with a fallback to `#xfer-cell-{{ xfer.id }}` for when the form is rendered from the transfer management page (non-grid context).

  Concrete template change in `_transfer_full_edit.html`:
  ```html
  {# BEFORE: #}
  hx-target="#xfer-cell-{{ xfer.id }}"

  {# AFTER: #}
  hx-target="#{{ target_cell_id | default('xfer-cell-' ~ xfer.id) }}"
  ```
- **GI-4 (Full edit submit):** AFFECTED for shadow transactions. The form submits to the transfer update endpoint. **SAFE** if the target is correctly set.
- **GI-5 (Mark done):** AFFECTED. For shadows, routes through transfer service. Response triggers `gridRefresh`. **SAFE**.
- **GI-6 (Mark credit):** AFFECTED. Blocked for shadows. Returns 400. The popover should not show the credit button for shadows, so this is a defensive measure. **SAFE**.
- **GI-7 (Undo credit):** NOT AFFECTED. Shadows are never in credit status.
- **GI-8 (Cancel):** AFFECTED. Routes through transfer service. **SAFE**.
- **GI-9, GI-15, GI-16:** NOT AFFECTED (creation, not mutation of existing).
- **GI-10:** NOT AFFECTED (balance row structure unchanged in this task).
- **GI-11-13:** NOT AFFECTED (anchor).
- **GI-14 (Carry forward):** NOT AFFECTED here (handled in Task 8).
- **GI-17-18:** NOT AFFECTED.
- **GI-19:** RETIRED.
- **GI-20 (Escape):** AFFECTED. For shadow transaction quick edits, Escape calls `/transactions/<id>/cell` which returns the standard transaction cell. **SAFE**.
- **GI-21 (F2):** AFFECTED. Same as GI-3 analysis.

**B6. Edge cases.**

- **Shadow transaction in "done" status, user clicks "Mark Done" again:** The transfer service sets status to done (idempotent). Both shadows already have done status. No error.
- **Shadow transaction in "cancelled" status, user updates amount:** The transfer service allows updating cancelled transfers. The amount change propagates to both shadows.
- **Race condition: two users (not applicable -- single user app).**
- **Full edit form for shadow returns transfer form, but transfer form's HTMX targets differ:** The transfer full edit form currently targets `#xfer-cell-<xfer_id>`. When rendered for a shadow transaction click, it must target `#txn-cell-<txn_id>` instead. This requires passing the originating transaction ID to the template so the form targets the correct cell.

**B7. Files to create or modify.**

- `app/routes/transactions.py` -- Add guards to `update_transaction`, `mark_done`, `mark_credit`, `unmark_credit`, `cancel_transaction`, `delete_transaction`, `get_full_edit`.
- `app/templates/grid/_transaction_cell.html` -- Add transfer indicator icon.
- `app/templates/grid/_transaction_full_edit.html` -- Hide credit button for shadows (defensive).
- `app/templates/transfers/_transfer_full_edit.html` -- Accept `txn_id` for correct HTMX targeting.
- `app/static/css/app.css` -- Add `.is-transfer` indicator styling.
- `tests/test_routes/test_transactions.py` -- Add guard tests.

---

### Task 8: Carry Forward with Shadow Transaction Detection

**B1. Solution description.**

Modify `carry_forward_unpaid()` in `app/services/carry_forward_service.py`:

```python
# After querying projected_txns (line 70-79), partition:
regular_txns = [t for t in projected_txns if t.transfer_id is None]
shadow_txns = [t for t in projected_txns if t.transfer_id is not None]

# Move regular transactions (unchanged behavior):
for txn in regular_txns:
    txn.pay_period_id = target_period_id
    if txn.template_id is not None:
        txn.is_override = True
    count += 1

# Move transfers via service (de-duplicate by transfer_id):
moved_transfer_ids = set()
for txn in shadow_txns:
    if txn.transfer_id not in moved_transfer_ids:
        transfer_service.update_transfer(
            txn.transfer_id, user_id,
            pay_period_id=target_period_id,
            is_override=True,
        )
        moved_transfer_ids.add(txn.transfer_id)
        count += 1  # Count once per transfer, not per shadow
```

**`is_override` handling detail:** The Transfer model has an `is_override` column (`app/models/transfer.py:63`). The `update_transfer` service accepts `is_override` as a kwarg and sets it on the transfer record. It also propagates `is_override=True` to both shadow transactions. This is correct because:
- The existing carry forward logic sets `is_override=True` only on transactions with `template_id is not None` (recurrence-generated items). Shadow transactions have `template_id=None`, so the regular carry forward path would NOT set their `is_override`.
- By routing through the transfer service with `is_override=True`, both the transfer record and its shadows are flagged. The transfer's `is_override` flag tells the transfer recurrence engine that this transfer was moved and should be skipped on regeneration, matching the behavior of `is_override` on regular transactions.
- For ad-hoc transfers (no `transfer_template_id`), `is_override` has no recurrence engine effect but is still set for consistency.

**B2. Design decision rationale.** Design section 10A.

**B3. Invariants.** Preserves invariant 5 (period sync) by moving transfer + both shadows atomically.

**B4. Dependencies.** Requires: Task 4.

**B5. HTMX regression analysis.**

- **GI-14 (Carry forward):** AFFECTED. The carry forward route calls the service. Response is still `HX-Trigger: gridRefresh` (full page reload). **SAFE** -- the response format is unchanged.
- All other interactions: NOT AFFECTED.

**B6. Edge cases.**

- **Period with only shadow transactions:** All are routed through transfer service. Regular count is 0. Transfer count reflects unique transfers moved.
- **Period with mix of regular and shadow:** Both paths execute. Regular moved directly, shadows via service.
- **Two shadows of same transfer in period (normal case):** De-duplicated by `moved_transfer_ids` set. Only one `update_transfer` call per transfer.
- **Partial shadow presence (only one shadow in period):** This is a data integrity issue. The `update_transfer` call moves the parent transfer and both shadows regardless. This corrects the inconsistency.
- **Zero projected transactions:** Loop does not execute. Count returns 0. No error.

**B7. Files to create or modify.**

- `app/services/carry_forward_service.py` -- Add shadow detection and transfer service routing.
- `tests/test_services/test_carry_forward_service.py` -- Add shadow transaction carry forward tests.

---

### Task 9: Balance Calculator -- Remove Transfer Logic

**B1. Solution description.**

1. Remove `transfers` parameter from `calculate_balances()`, `calculate_balances_with_interest()`, and `calculate_balances_with_amortization()`.
2. Remove `_sum_transfer_effects_remaining()` and `_sum_transfer_effects_all()` functions entirely.
3. Remove all `xfer_by_period`, `period_xfers`, and transfer-related code in all three public functions.
4. For `calculate_balances_with_amortization()`, replace the transfer-based payment detection (lines 260-267) with transaction-based detection: identify income transactions with `transfer_id IS NOT NULL` in the loan account. These are the shadow income transactions representing loan payments.

The amortization rework:
```python
# BEFORE (lines 260-267):
for xfer in period_xfers:
    if xfer.to_account_id == account_id:
        total_payment_in += xfer.amount

# AFTER:
for txn in period_txns:
    if txn.transfer_id is not None and txn.is_income:
        total_payment_in += Decimal(str(txn.estimated_amount))
```

5. Update all callers to stop passing `transfers`:
   - `app/routes/grid.py:110-117` and `grid.py:272-280` -- Remove `transfers=all_transfers`.
   - `app/routes/grid.py:94-102` and `grid.py:250-260` -- Remove Transfer query entirely.
   - `app/services/chart_data_service.py:214-245` -- Remove Transfer query and `transfers` param.
   - `app/routes/savings.py:67-88` -- Remove Transfer query and `transfers` param.

6. Run the dual-path verification test BEFORE removing the old logic.

**Dual-path verification test (design section 6.3):**

Create a test that:
1. Sets up known transfers with shadow transactions.
2. Calculates balances using the OLD calculator (with transfers parameter).
3. Calculates balances using the NEW calculator (transactions only, shadows included).
4. Asserts balances match exactly.

This test is written and run BEFORE the old logic is removed. After verification, the old logic is removed in the same commit.

**B2. Design decision rationale.** Design section 6.2. "This is the single highest-risk change in the rework."

**B3. Invariants.** Enforces invariant 7 (no double-counting).

**B4. Dependencies.** Requires: Tasks 4, 5, 6 (all transfers must produce shadows before the old path is removed).

**B5. HTMX regression analysis.**

- **GI-10 (Balance row refresh):** AFFECTED. The balance row endpoint no longer passes `transfers` to the calculator. The response HTML changes: the "Net Transfers" row is removed from `_balance_row.html`. The `<tfoot>` now has 4 rows instead of 5. Since the swap is `outerHTML` on `#grid-summary`, the new structure replaces the old one entirely. **SAFE**.
- All other interactions: NOT AFFECTED.

**B6. Edge cases.**

- **Period with only transfer-linked transactions (no regular transactions):** Shadow transactions are processed normally by `_sum_remaining` and `_sum_all`. Income shadows add to income, expense shadows add to expenses.
- **Transfer in "done" status:** Shadow transactions have `status_id` matching the transfer's. The balance calculator's existing done/received exclusion logic applies. The done shadows are excluded from projected balance (correctly -- they're settled). **SAFE**.
- **Cancelled transfer:** Shadow transactions have cancelled status. `effective_amount` returns 0. Excluded from balance. **SAFE**.
- **HYSA with transfers:** `calculate_balances_with_interest` calls `calculate_balances` without transfers. Shadow income transactions (deposits) increase the HYSA balance before interest calculation. **CORRECT**.
- **Debt with transfers:** The reworked amortization logic detects income transactions with `transfer_id IS NOT NULL` as loan payments. These are shadow income transactions in the loan account (representing money coming IN to pay the loan). **CORRECT**.

**B7. Files to create or modify.**

- `app/services/balance_calculator.py` -- Remove transfer logic, update amortization.
- `app/routes/grid.py` -- Remove Transfer queries and `transfers` param in both `index()` and `balance_row()`.
- `app/routes/savings.py` -- Remove Transfer query and `transfers` param.
- `app/services/chart_data_service.py` -- Remove Transfer query and `transfers` param.
- `app/templates/grid/_balance_row.html` -- Remove "Net Transfers" row, remove `xfer_by_period` references.
- `tests/test_services/test_balance_calculator.py` -- Rewrite transfer tests to use shadow transactions.
- `tests/test_services/test_balance_calculator_debt.py` -- Rewrite transfer tests.
- `tests/test_services/test_balance_calculator_hysa.py` -- Rewrite transfer tests.
- `tests/test_services/test_chart_data_service.py` -- Update balance chart tests.
- `tests/test_integration/test_workflows.py` -- Update `test_transfer_reduces_source_balance`.
- `tests/test_audit_fixes.py` -- Update `TestTransferBalanceCalculation`.

---

### Task 10: Grid Rendering -- Remove TRANSFERS Section

**B1. Solution description.**

1. In `app/templates/grid/grid.html`, remove the entire TRANSFERS section (lines 226-282).
2. Remove the `xfer_by_period` context variable usage.
3. In `app/routes/grid.py`, remove the Transfer query (lines 94-102 in `index()`). Remove `xfer_by_period` from the template context.
4. Shadow transactions are already in `txn_by_period` (they are regular Transaction rows). They appear in INCOME or EXPENSES sections based on their `transaction_type_id`.
5. The transfer indicator (added in Task 7) makes shadow transactions visually distinguishable.

Remove `xfer_by_period` from the `balance_row()` handler context as well (already done in Task 9 for the calculator, but the template context must also be cleaned up).

In `grid_edit.js`, the `openTransferFullEdit()` function (lines 90-105) is kept but no longer called from the grid. The server-side approach (GI-3 returns transfer form for shadow transactions) means the JS does not need to distinguish. Remove the F2 handler's `xfer-expand-btn` check (lines 191-194) since transfer cells no longer exist in the grid. The Escape handler's transfer revert path (lines 230-239) can also be removed.

**B2. Design decision rationale.** Design section 7.2.

**B3. Invariants.** None new.

**B4. Dependencies.** Requires: Tasks 7, 9 (guards and balance calculator must be in place).

**B5. HTMX regression analysis.**

- **GI-1 through GI-8:** NOT AFFECTED. Transaction cells remain unchanged.
- **GI-9:** NOT AFFECTED. Empty cells remain unchanged.
- **GI-10:** AFFECTED. The balance row no longer includes "Net Transfers." Handled in Task 9.
- **GI-11-13:** NOT AFFECTED.
- **GI-14:** NOT AFFECTED. Carry forward handled in Task 8.
- **GI-15-16:** NOT AFFECTED.
- **GI-17:** NOT AFFECTED. Full page load.
- **GI-18 (Keyboard navigation):** AFFECTED. `section-banner-transfer` rows no longer exist. The exclusion list in `getDataRows()` does not include `section-banner-transfer` (a pre-existing bug), so removing the section does not change keyboard behavior. **SAFE**.
- **GI-19:** RETIRED. The transfer cell click interaction no longer exists in the grid.
- **GI-20:** AFFECTED. The transfer-specific Escape revert path (fetching `/transfers/cell/<id>`) is removed. For shadow transactions, Escape reverts by fetching `/transactions/<id>/cell` (the standard path). **SAFE**.
- **GI-21:** AFFECTED. The F2 handler no longer checks for `xfer-expand-btn`. For shadow transactions, F2 calls `openFullEdit(txnId)` which fetches `/transactions/<id>/full-edit`, and the server returns the transfer form. **SAFE**.

**B6. Edge cases.**

- **User with no transfers:** Grid renders identically to before (TRANSFERS section was already conditional and empty).
- **User with only transfers, no regular transactions:** INCOME/EXPENSES sections show only shadow transactions. Section banners still render. **CORRECT**.

**B7. Files to create or modify.**

- `app/templates/grid/grid.html` -- Remove TRANSFERS section.
- `app/routes/grid.py` -- Remove Transfer query, remove `xfer_by_period` from context.
- `app/static/js/grid_edit.js` -- Remove transfer-specific F2/Escape handling.
- `tests/test_routes/test_grid.py` -- Add test verifying TRANSFERS section absent, shadow transactions in INCOME/EXPENSES.

---

### Task 11: Chart and Reporting Verification

**B1. Solution description.**

Verify that category-based charts include transfer-linked expense transactions:
1. Read `chart_data_service.py` `_get_expense_transactions()` (lines 341-364). Confirm no WHERE clause excludes `transfer_id IS NOT NULL`.
2. Read `get_spending_by_category()` (lines 367-412). Confirm shadow expense transactions with a category appear in spending breakdowns.
3. The Transfer query removal from `_calculate_account_balances()` was done in Task 9.

Update `app/routes/investment.py` and `app/routes/retirement.py` to derive contribution data from shadow income transactions instead of Transfer objects (inventory section 9.2-9.3):

```python
# BEFORE (investment.py:99-109):
acct_transfers = db.session.query(Transfer).filter(
    Transfer.to_account_id == account_id, ...
).all()

# AFTER:
acct_contributions = db.session.query(Transaction).filter(
    Transaction.account_id == account_id,
    Transaction.transfer_id.isnot(None),
    Transaction.transaction_type_id == income_type_id,
    Transaction.pay_period_id.in_(period_ids),
    Transaction.is_deleted.is_(False),
).all()
```

Update `app/services/investment_projection.py` to accept transactions instead of transfers for contribution calculation.

**B2. Design decision rationale.** Design section 8. Consumers should read only `budget.transactions`.

**B3. Invariants.** Enforces invariant 7 (no double-counting in charts).

**B4. Dependencies.** Requires: Tasks 9 (balance calculator changes).

**B5. HTMX regression analysis.** Not applicable (chart pages, not grid).

**B6. Edge cases.**

- **Transfer with no category:** Shadow expense transaction has `category_id=NULL`. It does not appear in category-grouped spending charts. This is expected -- the user should assign a category for chart visibility.
- **Cancelled transfer:** Shadow transactions have cancelled status. `_get_expense_transactions()` filters by status. If it excludes cancelled, cancelled transfer expenses don't appear. **CORRECT**.

**B7. Files to create or modify.**

- `app/routes/investment.py` -- Replace Transfer query with Transaction query.
- `app/routes/retirement.py` -- Replace Transfer query with Transaction query.
- `app/services/investment_projection.py` -- Accept transactions instead of transfers.
- `tests/test_services/test_chart_data_service.py` -- Add test for transfer expenses in category charts.

---

### Task 12: Cleanup

**B1. Solution description.**

Each removal must be verified before deletion. Do not delete a file or CSS class without confirming zero remaining references.

1. **Verify and remove `transfers/_transfer_cell.html`:**
   - Run: `grep -r "_transfer_cell" app/` -- expect zero matches in `app/templates/grid/` (the TRANSFERS section was removed in Task 10). The file may still be referenced in `app/routes/transfers.py:get_cell()` which renders it for the transfer template management page.
   - **Decision:** If `get_cell()` in transfers.py still uses this template, keep it. It is still needed for the transfer management page's cell rendering (the route at `/transfers/cell/<xfer_id>` is used when the server returns a display cell after a quick edit on the transfer management page, if such a page exists). If no route references it, delete it.
   - Run: `grep -r "_transfer_cell" app/routes/` to confirm.

2. **Verify and remove `transfers/_transfer_empty_cell.html`:**
   - Run: `grep -r "_transfer_empty_cell" app/` -- expect zero matches after Task 10 removed the TRANSFERS section from `grid.html`.
   - If zero matches, delete the file.

3. **Verify and remove dead CSS:**
   - Run: `grep -r "section-banner-transfer" app/templates/` -- expect zero matches after Task 10.
   - Run: `grep -r "section-transfer-bg\|section-transfer-text" app/` -- expect only `app.css` matches.
   - If no template or JS references remain, remove `.section-banner-transfer td` (lines 298-307) and the CSS variables `--shekel-section-transfer-bg`/`--shekel-section-transfer-text` from both dark and light mode sections.

4. **Verify and clean `grid_edit.js`:**
   - Run: `grep -r "openTransferFullEdit" app/` -- if only `grid_edit.js` references it and no template or route calls it, remove the function.
   - Run: `grep -r "xfer-expand-btn" app/templates/` -- if zero matches in templates (transfer cells removed from grid), remove the click handler for `.xfer-expand-btn` in `grid_edit.js`.
   - Run: `grep -r "xfer-cell-" app/templates/` -- if zero matches, the Escape handler's transfer revert path (lines 230-239) is dead and can be removed.

5. **Run full test suite:** `timeout 660 pytest -v --tb=short`.
6. **Run pylint:** `pylint app/`.

**B4. Dependencies.** Requires: All Tasks 1-11 complete.

**B6. Edge cases.**

- **Transfer management page still uses transfer templates:** The `transfers/list.html` and `transfers/form.html` templates are NOT removed -- they are still used for managing recurring transfer templates. Only the grid-specific transfer cell templates are candidates for removal.
- **`_transfer_quick_edit.html` and `_transfer_full_edit.html`:** These are NOT removed. The `_transfer_full_edit.html` is still rendered by the `get_full_edit` transaction route guard (Task 7) when a shadow transaction is expanded. The `_transfer_quick_edit.html` may still be used by the transfer management page.

**B7. Files to create or modify.**

- `app/templates/transfers/_transfer_cell.html` -- Remove IF no remaining references outside the grid (verify with grep).
- `app/templates/transfers/_transfer_empty_cell.html` -- Remove (verify with grep).
- `app/static/css/app.css` -- Remove dead `.section-banner-transfer` CSS and variables (verify with grep).
- `app/static/js/grid_edit.js` -- Remove dead `openTransferFullEdit()` and `xfer-expand-btn` handler IF no remaining references (verify with grep).

---

### Task 13: Phase 3A-II -- Inline Subtotal Rows

**B1. Solution description.**

Add subtotal rows to `grid/grid.html` at the bottom of each section:

**Calculation approach:** Subtotals are computed in the Jinja2 template, not in the route handler. This matches the existing pattern in `_balance_row.html` (lines 13-42) where Total Income and Total Expenses are computed by iterating over `txn_by_period` and summing. The template already has access to `txn_by_period` (passed from the route), so no new context variables are needed.

After the last income row (before the spacer):
```html
<tr class="subtotal-row">
  <th class="sticky-col row-label fw-bold">Total Income</th>
  {% for period in periods %}
    <td class="text-end cell fw-bold">
      {% set ns_inc = namespace(total=0) %}
      {% for txn in txn_by_period.get(period.id, []) %}
        {% if txn.is_income and not txn.is_deleted and txn.status.name not in ('credit', 'cancelled', 'done', 'received') %}
          {% set ns_inc.total = ns_inc.total + (txn.estimated_amount|float) %}
        {% endif %}
      {% endfor %}
      {{ "${:,.2f}".format(ns_inc.total) if ns_inc.total else "" }}
    </td>
  {% endfor %}
</tr>
```

After the last expense row:
```html
<tr class="subtotal-row">
  <th class="sticky-col row-label fw-bold">Total Expenses</th>
  {# Same pattern as income, filtering txn.is_expense instead #}
</tr>
```

The Net Cash Flow row (Task 14) computes `Total Income - Total Expenses` per period using the same template-side iteration. Since Jinja2 namespace variables do not persist across loop iterations, each period cell must independently compute its sum. This is identical to how the existing `_balance_row.html` works.

**Why template-side, not route-side:** Adding new context variables (e.g., `income_totals_by_period`, `expense_totals_by_period`) would require the route to pre-compute these sums. This is cleaner but introduces a new data contract between route and template. Since the existing footer already computes these sums in the template, the subtotal rows follow the same pattern for consistency. A future refactor could move all calculations to the route, but that is out of scope for this rework.

Add `.subtotal-row` to the keyboard navigation exclusion list in `app.js:361-364`.

Add CSS for `.subtotal-row`:
```css
.subtotal-row td, .subtotal-row th {
  background-color: var(--shekel-summary-bg);
  font-weight: 700;
  border-top: 2px solid var(--bs-border-color);
}
```

**B5. HTMX regression analysis.**

- **GI-10:** NOT AFFECTED. Subtotal rows are in `<tbody>`, not `<tfoot>`. The balance row refresh targets `#grid-summary` which is the `<tfoot>`.
- **GI-18:** AFFECTED. `subtotal-row` must be added to `getDataRows()` exclusion list. Without this, subtotal rows would be keyboard-navigable (they are not data rows).
- All other interactions: NOT AFFECTED.

**B7. Files to create or modify.**

- `app/templates/grid/grid.html` -- Add subtotal rows.
- `app/static/js/app.js` -- Add `subtotal-row` to exclusion list.
- `app/static/css/app.css` -- Add subtotal row styling.

---

### Task 14: Phase 3A-II -- Net Cash Flow Row

**B1. Solution description.**

Add a Net Cash Flow row in the `<tbody>` after the expense subtotal:
```html
<tr class="net-cash-flow-row">
  <th class="sticky-col row-label fw-bold">Net Cash Flow</th>
  {% for period in periods %}
    <td class="text-end cell fw-bold">
      {# Total Income - Total Expenses for this period #}
    </td>
  {% endfor %}
</tr>
```

Add `.net-cash-flow-row` to exclusion list.

**B5. HTMX regression analysis.** Same as Task 13.

**B7. Files to create or modify.**

- `app/templates/grid/grid.html` -- Add net cash flow row.
- `app/static/js/app.js` -- Add `net-cash-flow-row` to exclusion list.
- `app/static/css/app.css` -- Add net cash flow row styling.

---

### Task 15: Phase 3A-II -- Footer Condensation

**B1. Solution description.**

Reduce `<tfoot>` to Projected End Balance only:

```html
<tfoot id="grid-summary"
       hx-get="{{ url_for('grid.balance_row', ...) }}"
       hx-trigger="balanceChanged from:body"
       hx-swap="outerHTML">
  <tr class="balance-row-summary fw-bold">
    <th class="sticky-col row-label">Projected End Balance</th>
    {% for period in periods %}
      <td class="text-end cell">
        {{ balances.get(period.id, '')|format_currency }}
      </td>
    {% endfor %}
  </tr>
</tfoot>
```

Remove Total Income, Total Expenses, and Net (Income - Expenses) rows from the tfoot (they are now in tbody from Tasks 13-14). **Note:** The "Net Transfers" row was already removed in Task 9. This task removes the remaining three summary rows, leaving only Projected End Balance.

Update `grid.balance_row()` handler to return only the projected end balance row.

**B5. HTMX regression analysis.**

- **GI-10:** AFFECTED. The tfoot now contains 1 row instead of 4-5. The `outerHTML` swap on `#grid-summary` replaces the entire tfoot. The new tfoot has the `hx-trigger` attribute. **SAFE**.

**B7. Files to create or modify.**

- `app/templates/grid/_balance_row.html` -- Reduce to single row.
- `app/routes/grid.py` -- Update `balance_row()` context (subtotals computed in template now).
- `tests/test_routes/test_grid.py` -- Update footer assertions.

---

### Task 16: Database Rebuild

Operational procedure, not a code change. Documented in Section F.

---

## SECTION C: Test Specifications

---

### Task 1 Tests

**C-1-1:** `test_transaction_model_has_account_id`
- **Category:** Model
- **Setup:** Database with migration applied.
- **Action:** Create a Transaction with `account_id=1`.
- **Assertion:** Transaction saved. `txn.account_id == 1`. `txn.account` relationship resolves.
- **Why:** Verifies the column exists and the FK works.

**C-1-2:** `test_recurrence_engine_sets_account_id`
- **Category:** Service
- **Setup:** `seed_user` with account, transaction template with `account_id=checking.id`, recurrence rule, periods.
- **Action:** Call `recurrence_engine.generate_for_template(template, periods, scenario_id)`.
- **Assertion:** All generated transactions have `account_id == template.account_id`.
- **Why:** Verifies the recurrence engine copies account_id from template.

**C-1-3:** `test_credit_payback_inherits_account_id`
- **Category:** Service
- **Setup:** `seed_user`, expense transaction with `account_id=checking.id`, next period exists.
- **Action:** Call `credit_workflow.mark_as_credit(txn.id, user.id)`.
- **Assertion:** Payback transaction has `account_id == checking.id`.
- **Why:** Verifies credit payback inherits account_id.

**C-1-4:** `test_inline_create_requires_account_id`
- **Category:** Route
- **Setup:** `auth_client`, seeded data.
- **Action:** POST `/transactions/inline` with `account_id=checking.id` in form data.
- **Assertion:** Transaction created with correct `account_id`.
- **Why:** Verifies inline creation saves account_id.

**C-1-5:** `test_transaction_without_account_id_rejected`
- **Category:** Model
- **Setup:** Database with migration.
- **Action:** Attempt to create Transaction without `account_id`.
- **Assertion:** IntegrityError raised (NOT NULL violation).
- **Why:** Verifies NOT NULL constraint.

**Existing tests to run:** `timeout 660 pytest -v --tb=short` (full suite -- many tests will need account_id updates).

---

### Task 4 Tests (Transfer Service)

**C-4-1:** `test_create_transfer_produces_two_shadows`
- **Category:** Service (unit)
- **Setup:** `seed_user` with checking and savings accounts, baseline scenario, periods.
- **Action:** Call `transfer_service.create_transfer(user_id, checking.id, savings.id, period.id, scenario.id, Decimal("200.00"), projected_status.id)`.
- **Assertion:** Transfer created. `Transaction.query.filter_by(transfer_id=transfer.id).count() == 2`. One has `transaction_type.name == "expense"` and `account_id == checking.id`. One has `transaction_type.name == "income"` and `account_id == savings.id`. Both have `estimated_amount == Decimal("200.00")`. Both have `status_id == projected_status.id`.
- **Why:** Core invariant 1 -- every transfer has exactly two linked transactions.

**C-4-2:** `test_create_transfer_shadow_names`
- **Category:** Service
- **Setup:** Same as C-4-1.
- **Action:** Create transfer.
- **Assertion:** Expense shadow name is `"Transfer to Savings"`. Income shadow name is `"Transfer from Checking"`.
- **Why:** Verifies naming convention.

**C-4-3:** `test_create_transfer_with_category`
- **Category:** Service
- **Setup:** `seed_user` with category "Home: Mortgage Payment".
- **Action:** Create transfer with `category_id=mortgage_cat.id`.
- **Assertion:** Expense shadow has `category_id == mortgage_cat.id`. Income shadow has `category_id == incoming_cat.id` (the "Transfers: Incoming" category).
- **Why:** Verifies category inheritance.

**C-4-4:** `test_update_transfer_amount_syncs_shadows`
- **Category:** Service
- **Setup:** Create a transfer. Record shadow IDs.
- **Action:** Call `transfer_service.update_transfer(transfer.id, user.id, amount=Decimal("300.00"))`.
- **Assertion:** Transfer amount == 300. Both shadows `estimated_amount == Decimal("300.00")`.
- **Why:** Invariant 3 -- amount sync.

**C-4-5:** `test_update_transfer_status_syncs_shadows`
- **Category:** Service
- **Setup:** Create a projected transfer.
- **Action:** Update with `status_id=done_status.id`.
- **Assertion:** Transfer, expense shadow, and income shadow all have `status_id == done_status.id`.
- **Why:** Invariant 4 -- status sync.

**C-4-6:** `test_update_transfer_period_syncs_shadows`
- **Category:** Service
- **Setup:** Create transfer in period 1. Period 2 exists.
- **Action:** Update with `pay_period_id=period_2.id`.
- **Assertion:** Transfer and both shadows have `pay_period_id == period_2.id`.
- **Why:** Invariant 5 -- period sync.

**C-4-7:** `test_delete_transfer_hard_removes_shadows`
- **Category:** Service
- **Setup:** Create an ad-hoc transfer (no template).
- **Action:** Call `transfer_service.delete_transfer(transfer.id, user.id, soft=False)`.
- **Assertion:** Transfer gone from database. Both shadow transactions gone (CASCADE).
- **Why:** Invariant 2 -- no orphaned shadows.

**C-4-8:** `test_delete_transfer_soft_marks_shadows_deleted`
- **Category:** Service
- **Setup:** Create a template-linked transfer.
- **Action:** Call `transfer_service.delete_transfer(transfer.id, user.id, soft=True)`.
- **Assertion:** Transfer `is_deleted == True`. Both shadows `is_deleted == True`.
- **Why:** Soft-delete invariant.

**C-4-9:** `test_create_transfer_same_account_rejected`
- **Category:** Service
- **Setup:** `seed_user` with one account.
- **Action:** Call `create_transfer(from_account_id=acct.id, to_account_id=acct.id, ...)`.
- **Assertion:** Raises ValueError or IntegrityError.
- **Why:** Database CHECK constraint.

**C-4-10:** `test_update_transfer_actual_amount_syncs`
- **Category:** Service
- **Setup:** Create a transfer.
- **Action:** Update with `actual_amount=Decimal("195.50")`.
- **Assertion:** Both shadows have `actual_amount == Decimal("195.50")`.
- **Why:** Invariant 3 extended to actual_amount.

**C-4-11:** `test_create_transfer_wrong_user_rejected`
- **Category:** Service
- **Setup:** Two users. Accounts belong to user 1.
- **Action:** Call `create_transfer` with `user_id=user_2.id` but account IDs belonging to user 1.
- **Assertion:** Raises validation error.
- **Why:** Ownership check.

**Existing tests to run:** `tests/test_services/test_transfer_recurrence.py`, `tests/test_routes/test_transfers.py`.

---

### Task 6 Tests (Transfer Routes -- additional)

**C-6-1:** `test_one_time_transfer_creates_shadows_and_affects_balance`
- **Category:** Route (integration)
- **Setup:** `seed_user`, `auth_client`, checking and savings accounts, baseline scenario, periods with anchor balance of $5000.00, one income transaction of $2000.00 in period 1.
- **Action:** POST `/transfers/ad-hoc` with `from_account_id=checking.id, to_account_id=savings.id, amount=500.00, pay_period_id=period_1.id, scenario_id=scenario.id`.
- **Assertion:**
  1. Transfer created (1 row in `budget.transfers`).
  2. Two shadow transactions created: one expense in checking (`account_id=checking.id, transaction_type.name="expense", estimated_amount=500.00`), one income in savings (`account_id=savings.id, transaction_type.name="income", estimated_amount=500.00`).
  3. GET `/grid/balance-row` returns Projected End Balance for period 1 that equals `$5000 + $2000 - $500 = $6500` (anchor + income - transfer out).
- **Why:** This is the explicit regression test for the one-time transfer bug (design document section 1.4). One-time transfers were previously invisible to the grid and balance calculator. This test proves they now work end-to-end: creation through the ad-hoc route produces shadows that the balance calculator includes.

**C-6-2:** `test_template_delete_cascades_to_shadows`
- **Category:** Route
- **Setup:** Create a transfer template with recurrence. Generate transfers (which create shadows).
- **Action:** POST `/transfers/<template_id>/delete`.
- **Assertion:** Template deactivated. All projected transfers have `is_deleted=True`. All shadow transactions of those transfers have `is_deleted=True`.
- **Why:** Verifies the three-level cascade: template deactivation -> transfer soft-delete -> shadow soft-delete.

**C-6-3:** `test_template_reactivate_restores_shadows`
- **Category:** Route
- **Setup:** Create, deactivate, then reactivate a transfer template.
- **Action:** POST `/transfers/<template_id>/reactivate`.
- **Assertion:** Regenerated transfers have `is_deleted=False`. Their shadow transactions have `is_deleted=False`.
- **Why:** Verifies reactivation restores shadows alongside transfers.

---

### Task 7 Tests (Transaction Route Guards)

**C-7-1:** `test_update_shadow_transaction_routes_through_transfer_service`
- **Category:** Route
- **Setup:** Create a transfer (which creates shadows). Get expense shadow ID.
- **Action:** PATCH `/transactions/<shadow_id>` with `estimated_amount=500.00`.
- **Assertion:** Response 200. Transfer amount updated to 500. BOTH shadows updated to 500.
- **Why:** Verifies PATCH on shadow routes through transfer service.

**C-7-2:** `test_mark_done_shadow_routes_through_transfer`
- **Category:** Route
- **Setup:** Create a projected transfer.
- **Action:** POST `/transactions/<shadow_id>/mark-done`.
- **Assertion:** Transfer, expense shadow, and income shadow all have done status.
- **Why:** Verifies mark-done routes through transfer service.

**C-7-3:** `test_mark_credit_blocked_for_shadow`
- **Category:** Route
- **Setup:** Create a projected transfer.
- **Action:** POST `/transactions/<shadow_id>/mark-credit`.
- **Assertion:** Response 400 or 403. Transaction status unchanged. No payback created.
- **Why:** Verifies credit is blocked for shadows.

**C-7-4:** `test_delete_shadow_blocked`
- **Category:** Route
- **Setup:** Create a transfer.
- **Action:** DELETE `/transactions/<shadow_id>`.
- **Assertion:** Response 400. Shadow still exists.
- **Why:** Verifies direct deletion is blocked.

**C-7-5:** `test_full_edit_shadow_returns_transfer_form`
- **Category:** Route
- **Setup:** Create a transfer.
- **Action:** GET `/transactions/<shadow_id>/full-edit`.
- **Assertion:** Response contains transfer edit form elements (from_account, to_account fields) rather than standard transaction form.
- **Why:** Verifies full edit routing for shadows.

**C-7-6:** `test_cancel_shadow_routes_through_transfer`
- **Category:** Route
- **Setup:** Create a projected transfer.
- **Action:** POST `/transactions/<shadow_id>/cancel`.
- **Assertion:** Transfer and both shadows have cancelled status.
- **Why:** Verifies cancel routes through transfer service.

**C-7-7:** `test_transfer_indicator_in_cell`
- **Category:** Route
- **Setup:** Create a transfer. Load grid.
- **Action:** GET `/`.
- **Assertion:** Response HTML contains `bi-arrow-left-right` icon in the shadow transaction's cell.
- **Why:** Verifies visual transfer indicator.

---

### Task 8 Tests (Carry Forward)

**C-8-1:** `test_carry_forward_with_shadow_moves_transfer_atomically`
- **Category:** Service
- **Setup:** Create a transfer in period 1 (creates 2 shadows). Create a regular transaction in period 1.
- **Action:** Call `carry_forward_unpaid(period_1.id, period_2.id, user.id, scenario.id)`.
- **Assertion:** Regular transaction in period 2. Transfer in period 2. Both shadows in period 2. Count returned includes 1 regular + 1 transfer = 2.
- **Why:** Verifies atomic carry forward of transfer + shadows.

**C-8-2:** `test_carry_forward_deduplicates_shadow_pairs`
- **Category:** Service
- **Setup:** Create a transfer (2 shadows in same period).
- **Action:** Carry forward.
- **Assertion:** Only one `update_transfer` call per transfer (not two). Transfer moved once.
- **Why:** Verifies de-duplication.

**C-8-3:** `test_carry_forward_sets_override_on_transfer`
- **Category:** Service
- **Setup:** Create a template-linked transfer.
- **Action:** Carry forward.
- **Assertion:** Transfer `is_override == True`. Both shadows `is_override == True`.
- **Why:** Verifies override flagging for carried-forward transfers.

---

### Task 9 Tests (Balance Calculator)

**C-9-1:** `test_dual_path_verification`
- **Category:** Service (critical)
- **Setup:** Create checking account with anchor balance $5000 at period 1. Create 3 periods. Create transactions and transfers with shadow transactions across multiple periods:
  - Period 1: salary income $2000, rent expense $1200, transfer to savings $500.
  - Period 2: salary income $2000, groceries expense $400, transfer to savings $500.
  - Period 3: salary income $2000, car payment expense $350, transfer to savings $500, transfer to mortgage $800.
- **Action:** Calculate balances using OLD path (with transfers param, no shadows in transaction list). Calculate using NEW path (shadows in transactions list, no transfers param).
- **Assertion:** Both produce identical balances for ALL three periods:
  - Period 1: $5000 + $2000 - $1200 - $500 = $5300
  - Period 2: $5300 + $2000 - $400 - $500 = $6400
  - Period 3: $6400 + $2000 - $350 - $500 - $800 = $6750
  - Balances match exactly (Decimal equality, not float approximation).
- **Why:** Proves shadow transactions produce identical rolling balances to the old transfer path across multiple periods. A single-period test could pass even if the rolling balance carry-forward is broken. This is the gate test.

**C-9-2:** `test_balance_calculator_no_transfers_param`
- **Category:** Service
- **Setup:** Transactions including shadow transactions (no Transfer objects passed).
- **Action:** Call `calculate_balances(anchor, period_id, periods, transactions)` -- no `transfers` kwarg.
- **Assertion:** Balances include shadow transaction effects. Income shadows increase balance, expense shadows decrease.
- **Why:** Verifies the new transaction-only path works.

**C-9-3:** `test_amortization_detects_payments_from_shadow_transactions`
- **Category:** Service
- **Setup:** Loan account with params. Shadow income transaction in loan account with `transfer_id IS NOT NULL`.
- **Action:** Call `calculate_balances_with_amortization()`.
- **Assertion:** Payment detected. Principal reduced. Interest calculated. `principal_by_period` populated.
- **Why:** Verifies the reworked amortization payment detection.

**C-9-4:** `test_no_double_counting_with_shadows`
- **Category:** Service (critical)
- **Setup:** Transfer with shadow transactions. Both shadows and transfer objects available.
- **Action:** Calculate balances with transactions only (correct). Hypothetically calculate if both were included.
- **Assertion:** The correct calculation (transactions only) matches expected values. Verify that no function signature accepts `transfers` param anymore.
- **Why:** Guards against double-counting regression.

---

### Task 10 Tests (Grid Rendering)

**C-10-1:** `test_grid_no_transfers_section`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, create transfers (which create shadows).
- **Action:** GET `/`.
- **Assertion:** Response does NOT contain `section-banner-transfer`. Response does NOT contain `xfer-cell-`. Shadow transactions appear in INCOME or EXPENSES sections (contain `txn-cell-` divs with transfer indicator).
- **Why:** Verifies TRANSFERS section removed.

**C-10-2:** `test_shadow_transactions_in_expense_section`
- **Category:** Route
- **Setup:** Create a transfer from checking to savings with category "Financial: Savings Transfer".
- **Action:** GET `/`.
- **Assertion:** An expense shadow transaction cell appears in the EXPENSES section under the "Financial" group. The cell contains `bi-arrow-left-right` transfer indicator. The amount matches the transfer amount.
- **Why:** Verifies shadow expense transactions render in the correct section with correct grouping.

**C-10-3:** `test_shadow_income_in_income_section`
- **Category:** Route
- **Setup:** Create a transfer from checking to savings. The income shadow has category "Transfers: Incoming".
- **Action:** GET `/`.
- **Assertion:** An income shadow transaction cell appears in the INCOME section under the "Transfers" group. Contains transfer indicator icon.
- **Why:** Verifies shadow income transactions (the receiving side of a transfer) render in the INCOME section.

**C-10-4:** `test_grid_with_no_transfers_still_works`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded periods and regular transactions, NO transfers.
- **Action:** GET `/`.
- **Assertion:** Grid renders without errors. INCOME and EXPENSES sections present. No TRANSFERS section. No transfer indicator icons. Balances correct.
- **Why:** Regression test -- removing the TRANSFERS section must not break the grid when there are no transfers.

**C-10-5:** `test_cancelled_transfer_shadows_excluded_from_grid`
- **Category:** Route
- **Setup:** Create a transfer, then cancel it (both shadows get cancelled status).
- **Action:** GET `/`.
- **Assertion:** The shadow transaction cells are NOT rendered (the template filters `txn.status.name != 'cancelled'`). Balances unaffected by cancelled transfer.
- **Why:** Verifies cancelled transfers do not produce visible grid artifacts.

**C-10-6:** `test_keyboard_navigation_no_transfer_banner`
- **Category:** Route (template structure)
- **Setup:** `seed_user`, `auth_client`, create transfers.
- **Action:** GET `/`.
- **Assertion:** Response HTML does NOT contain any element with class `section-banner-transfer`. All `<tr>` elements in `<tbody>` either have a data cell class or one of the known excluded classes (`section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`).
- **Why:** Confirms the transfer section banner is gone and the keyboard navigation exclusion list does not need `section-banner-transfer`.

---

### Phase 3A-II Tests

**C-13-1:** `test_inline_subtotal_rows_present`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded transactions.
- **Action:** GET `/`.
- **Assertion:** Response contains `<tr class="subtotal-row">` elements. One labeled "Total Income", one "Total Expenses". Amounts match expected sums.
- **Why:** Verifies subtotal row rendering.

**C-14-1:** `test_net_cash_flow_row_present`
- **Category:** Route
- **Setup:** Same as above.
- **Action:** GET `/`.
- **Assertion:** Response contains `<tr class="net-cash-flow-row">` with label "Net Cash Flow" and correct Income - Expenses value.
- **Why:** Verifies net cash flow row.

**C-15-1:** `test_footer_single_row`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded data.
- **Action:** GET `/grid/balance-row`.
- **Assertion:** `<tfoot>` contains exactly 1 `<tr>` (Projected End Balance). Does NOT contain "Total Income", "Total Expenses", "Net Transfers", or "Net" rows in the tfoot.
- **Why:** Verifies footer condensation.

**C-15-2:** `test_footer_htmx_refresh_works_after_condensation`
- **Category:** Route (HTMX regression)
- **Setup:** Same.
- **Action:** GET `/grid/balance-row`.
- **Assertion:** Response `<tfoot>` has `id="grid-summary"` and `hx-trigger="balanceChanged from:body"`.
- **Why:** Ensures the balance row refresh mechanism survives the condensation.

---

## SECTION D: Difficulty, Time, and Risk Assessment

### Task 1: Schema -- account_id
- **D1. Difficulty:** High. Wide blast radius -- every Transaction constructor in every test file must be updated.
- **D2. Estimated time:** 2-3 hours. The model change is simple. The test fixture updates are mechanical but numerous.
- **D3. Regression risk:** If `account_id` is not set on a creation path, a NOT NULL violation crashes that path. This is fail-loud (good). The risk is in missing a creation path.
- **D4. Rollback:** `git revert` plus `flask db downgrade`.

### Task 2: Schema -- transfer_id, category_id
- **D1. Difficulty:** Low. Additive columns with no NOT NULL constraint.
- **D2. Estimated time:** 30-60 minutes.
- **D3. Regression risk:** Minimal. No existing code reads these columns yet.
- **D4. Rollback:** `git revert` plus `flask db downgrade`.

### Task 3: Seed categories
- **D1. Difficulty:** Low. Two list entries.
- **D2. Estimated time:** 15-30 minutes.
- **D3. Regression risk:** None. Additive only.
- **D4. Rollback:** `git revert`.

### Task 4: Transfer service
- **D1. Difficulty:** High. Core new service with financial calculation implications.
- **D2. Estimated time:** 3-4 hours. Service + comprehensive tests.
- **D3. Regression risk:** If shadow creation fails silently, transfers exist without shadows. Balance calculator changes (Task 9) would then miss these transfers entirely, causing incorrect balances.
- **D4. Rollback:** `git revert`. Service is new; no existing code depends on it yet.

### Task 5: Wire recurrence engine
- **D1. Difficulty:** Medium. Replacing a constructor call with a service call.
- **D2. Estimated time:** 1-2 hours. Code change is small; test updates take time.
- **D3. Regression risk:** If the service call signature is wrong, recurring transfers fail to generate. Visible as empty grid.
- **D4. Rollback:** `git revert`.

### Task 6: Wire transfer routes
- **D1. Difficulty:** Medium. Multiple route handlers to update.
- **D2. Estimated time:** 2-3 hours. Routes + template category dropdowns + test updates.
- **D3. Regression risk:** Transfer CRUD breaks if service calls are wrong. Visible immediately in UI.
- **D4. Rollback:** `git revert`.

### Task 7: Transaction route guards
- **D1. Difficulty:** High. Many code paths with subtle HTMX interaction concerns.
- **D2. Estimated time:** 2-3 hours.
- **D3. Regression risk:** If a guard is missing, a shadow transaction can be directly mutated, breaking sync invariants. The user would see inconsistent amounts or statuses between the transfer and its shadows.
- **D4. Rollback:** `git revert`.

### Task 8: Carry forward
- **D1. Difficulty:** Medium. Localized change with clear partitioning logic.
- **D2. Estimated time:** 1-2 hours.
- **D3. Regression risk:** If shadow detection fails, carry forward moves one shadow without the other, breaking period sync. Visible as shadow in wrong period.
- **D4. Rollback:** `git revert`.

### Task 9: Balance calculator
- **D1. Difficulty:** Critical. Highest-risk change in the entire rework.
- **D2. Estimated time:** 3-4 hours. Dual-path test, code changes, test rewrites.
- **D3. Regression risk:** If the old transfer logic is removed before shadows are working, all transfer effects vanish from balances. If shadows are double-counted (old + new path), balances are inflated by total transfer amounts. Both are catastrophic for a budgeting app.
- **D4. Rollback:** `git revert`. This is the task where atomic commit discipline matters most.

### Task 10: Grid rendering
- **D1. Difficulty:** Medium. Template changes with HTMX interaction concerns.
- **D2. Estimated time:** 2-3 hours.
- **D3. Regression risk:** If the TRANSFERS section removal breaks the grid template, the page fails to render. Visible immediately.
- **D4. Rollback:** `git revert`.

### Task 11: Chart verification
- **D1. Difficulty:** Medium. Multiple files but straightforward changes.
- **D2. Estimated time:** 1-2 hours.
- **D3. Regression risk:** Investment/retirement contribution calculations could break if the query replacement is wrong.
- **D4. Rollback:** `git revert`.

### Task 12: Cleanup
- **D1. Difficulty:** Low.
- **D2. Estimated time:** 30-60 minutes.
- **D3. Regression risk:** Minimal. Removing dead code.
- **D4. Rollback:** `git revert`.

### Tasks 13-15: Phase 3A-II
- **D1. Difficulty:** Medium (combined).
- **D2. Estimated time:** 2-3 hours (combined).
- **D3. Regression risk:** Footer condensation could break the balance row refresh (GI-10). The outerHTML swap should handle structure changes, but must be tested.
- **D4. Rollback:** `git revert`.

### Task 16: Database rebuild
- **D1. Difficulty:** Low (operational).
- **D2. Estimated time:** 30-60 minutes.
- **D3. Regression risk:** Data loss if the rebuild is done prematurely. Mitigation: backup first.
- **D4. Rollback:** Restore from backup.

**Total estimated time:** 22-35 hours for Phase 3A-I, 3-5 hours for Phase 3A-II. Total: 25-40 hours.

---

## SECTION E: Implementation Sequencing

### Phase 3A-I: Shadow Transaction Architecture

---

**Step 1: Schema -- account_id**

- **E1. Commit:** `feat(transactions): add account_id column to transactions`
- **E2. Files:** `app/models/transaction.py`, `app/services/recurrence_engine.py`, `app/services/credit_workflow.py`, `app/routes/transactions.py`, `app/templates/grid/_transaction_quick_create.html`, `app/templates/grid/_transaction_full_create.html`, `app/templates/grid/grid.html`, `migrations/versions/<new>.py`, `tests/conftest.py`, multiple test files.
- **E3. Tests:** C-1-1 through C-1-5. Plus all existing tests updated with account_id.
- **E4. Run:** `timeout 660 pytest -v --tb=short` (full suite -- massive fixture update).
- **E5. Manual:** Start dev server. Load grid. Create inline transaction. Verify it saves. Create modal transaction. Verify.
- **E6. Gate:** Full test suite passes. Every Transaction creation path sets account_id.

---

**Step 2: Schema -- transfer_id, category_id**

- **E1. Commit:** `feat(models): add transfer_id to transactions, category_id to transfers`
- **E2. Files:** `app/models/transaction.py`, `app/models/transfer.py`, `app/models/transfer_template.py`, `app/schemas/validation.py`, `migrations/versions/<new>.py`.
- **E3. Tests:** Verify models load. Basic query tests.
- **E4. Run:** `pytest tests/test_models/ -v`
- **E5. Manual:** None needed -- schema only.
- **E6. Gate:** Migration applies cleanly. Models load without error.

---

**Step 3: Seed categories**

- **E1. Commit:** `feat(seed): add default transfer categories`
- **E2. Files:** `app/services/auth_service.py`.
- **E3. Tests:** Verify categories appear after seed.
- **E4. Run:** `pytest tests/test_scripts/ -v`
- **E5. Manual:** Run `python scripts/seed_user.py` on test database. Verify categories exist.
- **E6. Gate:** Categories seeded correctly.

---

**Step 4: Transfer service**

- **E1. Commit:** `feat(transfers): add transfer service with shadow transaction creation`
- **E2. Files:** `app/services/transfer_service.py` (new), `tests/test_services/test_transfer_service.py` (new).
- **E3. Tests:** C-4-1 through C-4-11.
- **E4. Run:** `pytest tests/test_services/test_transfer_service.py -v`
- **E5. Manual:** None -- service tested in isolation.
- **E6. Gate:** All 11 service tests pass. All invariants verified.

---

**Step 5: Wire recurrence engine**

- **E1. Commit:** `refactor(transfer-recurrence): route generation through transfer service`
- **E2. Files:** `app/services/transfer_recurrence.py`, `tests/test_services/test_transfer_recurrence.py`.
- **E3. Tests:** Update existing tests to assert shadow transaction creation.
- **E4. Run:** `pytest tests/test_services/test_transfer_recurrence.py -v`
- **E5. Manual:** None.
- **E6. Gate:** All recurrence tests pass. Generated transfers have shadows.

---

**Step 6: Wire transfer routes**

- **E1. Commit:** `refactor(transfer-routes): route mutations through transfer service`
- **E2. Files:** `app/routes/transfers.py`, `app/templates/transfers/form.html`, `app/templates/transfers/_transfer_full_edit.html`, `tests/test_routes/test_transfers.py`.
- **E3. Tests:** C-6-1 through C-6-3. Plus update existing route tests to verify shadow creation/update/deletion.
- **E4. Run:** `pytest tests/test_routes/test_transfers.py -v`
- **E5. Manual:** Start dev server. Create an ad-hoc (one-time) transfer. Verify two shadow transactions appear in the database. Edit the transfer amount. Verify shadows updated. Deactivate a transfer template. Verify shadows soft-deleted. Reactivate. Verify shadows restored.
- **E6. Gate:** All transfer route tests pass. C-6-1 (one-time transfer) passes. Manual verification confirms shadows.

---

**Step 7: Transaction route guards**

- **E1. Commit:** `feat(transactions): add transfer detection guards on mutation routes`
- **E2. Files:** `app/routes/transactions.py`, `app/templates/grid/_transaction_cell.html`, `app/templates/grid/_transaction_full_edit.html`, `app/templates/transfers/_transfer_full_edit.html`, `app/static/css/app.css`, `tests/test_routes/test_transactions.py`.
- **E3. Tests:** C-7-1 through C-7-7.
- **E4. Run:** `pytest tests/test_routes/test_transactions.py -v`
- **E5. Manual:** Load grid with transfers. Click a shadow transaction cell. Verify quick edit works and updates both shadows. Click expand. Verify transfer edit form appears. Verify "Mark Credit" button is NOT shown.
- **E6. Gate:** All guard tests pass. Manual HTMX verification confirms correct behavior.

---

**Step 8: Carry forward**

- **E1. Commit:** `feat(carry-forward): detect shadow transactions and route through transfer service`
- **E2. Files:** `app/services/carry_forward_service.py`, `tests/test_services/test_carry_forward_service.py`.
- **E3. Tests:** C-8-1 through C-8-3.
- **E4. Run:** `pytest tests/test_services/test_carry_forward_service.py -v`
- **E5. Manual:** Create a transfer in a past period. Click "Carry Forward Unpaid." Verify transfer and both shadows moved to current period.
- **E6. Gate:** Carry forward tests pass.

---

**Step 9: Balance calculator**

- **E1. Commit:** `refactor(balance): remove transfer-specific logic, rely on shadow transactions`
- **E2. Files:** `app/services/balance_calculator.py`, `app/routes/grid.py`, `app/routes/savings.py`, `app/services/chart_data_service.py`, `app/templates/grid/_balance_row.html`, `tests/test_services/test_balance_calculator.py`, `tests/test_services/test_balance_calculator_debt.py`, `tests/test_services/test_balance_calculator_hysa.py`, `tests/test_services/test_chart_data_service.py`, `tests/test_integration/test_workflows.py`, `tests/test_audit_fixes.py`.
- **E3. Tests:** C-9-1 through C-9-4. Plus rewritten transfer tests.
- **E4. Run:** `timeout 660 pytest -v --tb=short` (full suite -- critical checkpoint).
- **E5. Manual:** Load grid. Verify balances match expected values. Compare with hand-calculated values for a known scenario.
- **E6. Gate:** Full test suite passes. Dual-path verification test passes. Balances are correct.

---

**Step 10: Grid rendering**

- **E1. Commit:** `refactor(grid): remove TRANSFERS section, render shadows in income/expenses`
- **E2. Files:** `app/templates/grid/grid.html`, `app/routes/grid.py`, `app/static/js/grid_edit.js`, `tests/test_routes/test_grid.py`.
- **E3. Tests:** C-10-1 through C-10-6.
- **E4. Run:** `pytest tests/test_routes/test_grid.py -v`
- **E5. Manual:** Load grid. Verify no TRANSFERS section. Verify shadow transactions appear in INCOME/EXPENSES with transfer indicator. Test keyboard navigation (arrow keys skip section banners). Test F2 on shadow cell (should open transfer form). Test Escape from quick edit.
- **E6. Gate:** Grid tests pass. Full manual HTMX interaction walkthrough.

---

**Step 11: Chart verification**

- **E1. Commit:** `refactor(charts): remove transfer queries, derive contributions from shadow transactions`
- **E2. Files:** `app/routes/investment.py`, `app/routes/retirement.py`, `app/services/investment_projection.py`, `tests/test_services/test_chart_data_service.py`.
- **E3. Tests:** Category chart includes transfer expenses.
- **E4. Run:** `pytest tests/test_services/test_chart_data_service.py -v && pytest tests/test_routes/ -v`
- **E5. Manual:** Load charts page. Verify spending by category includes transfer expenses (e.g., mortgage under "Home").
- **E6. Gate:** Chart tests pass.

---

**Step 12: Cleanup**

- **E1. Commit:** `chore(cleanup): retire unused transfer grid templates and dead code`
- **E2. Files:** Remove `transfers/_transfer_cell.html`, `transfers/_transfer_empty_cell.html`. Clean CSS and JS.
- **E3. Tests:** None new.
- **E4. Run:** `timeout 660 pytest -v --tb=short` (full suite). `pylint app/`.
- **E5. Manual:** Full payday workflow walkthrough.
- **E6. Gate:** Full test suite passes. Pylint score maintained. No dead code warnings.

---

### Phase 3A-I Gate

Before proceeding to Phase II:
1. `timeout 660 pytest -v --tb=short` -- all tests pass.
2. Manual payday workflow: true-up balance, mark paycheck received, carry forward (including a transfer), mark expenses paid (including shadow transaction), check projections.
3. Balances match hand-calculated expected values.
4. Task 4.2 in `implementation_plan_section4.md` marked as superseded.

---

### Phase 3A-II: Grid Subtotals and Footer Condensation

---

**Step 13: Inline subtotal rows**

- **E1. Commit:** `feat(grid): add inline Total Income and Total Expenses subtotal rows`
- **E2. Files:** `app/templates/grid/grid.html`, `app/static/js/app.js`, `app/static/css/app.css`, `tests/test_routes/test_grid.py`.
- **E3. Tests:** C-13-1.
- **E4. Run:** `pytest tests/test_routes/test_grid.py -v`
- **E5. Manual:** Load grid. Verify subtotal rows at bottom of each section. Verify keyboard navigation skips them.
- **E6. Gate:** Subtotal tests pass.

---

**Step 14: Net Cash Flow row**

- **E1. Commit:** `feat(grid): add Net Cash Flow row after expenses`
- **E2. Files:** `app/templates/grid/grid.html`, `app/static/js/app.js`, `app/static/css/app.css`, `tests/test_routes/test_grid.py`.
- **E3. Tests:** C-14-1.
- **E4. Run:** `pytest tests/test_routes/test_grid.py -v`
- **E5. Manual:** Verify Net Cash Flow row appears and shows correct values.
- **E6. Gate:** Tests pass.

---

**Step 15: Footer condensation**

- **E1. Commit:** `refactor(grid): condense footer to Projected End Balance only`
- **E2. Files:** `app/templates/grid/_balance_row.html`, `app/routes/grid.py`, `tests/test_routes/test_grid.py`.
- **E3. Tests:** C-15-1, C-15-2.
- **E4. Run:** `timeout 660 pytest -v --tb=short` (full suite).
- **E5. Manual:** Load grid. Verify footer has single row. Verify HTMX balance refresh still works (edit a transaction amount, watch footer update). Verify subtotals and Net Cash Flow scroll with content.
- **E6. Gate:** Full test suite passes. GI-10 works correctly.

---

**Step 16: Database rebuild**

- **E1. Commit:** No code commit. Operational procedure.
- **Procedure:**
  1. Backup production database.
  2. Drop production database.
  3. `flask db upgrade` -- apply all migrations.
  4. `python scripts/seed_ref_tables.py`
  5. `python scripts/seed_user.py`
  6. `python scripts/seed_tax_brackets.py`
  7. Re-enter data (accounts, templates, transfers, anchor balances).
  8. Verify balances match known correct values.

---

### Phase 3A-II Gate

1. `timeout 660 pytest -v --tb=short` -- all tests pass.
2. Manual payday workflow verification.
3. Screenshot comparison: grid before and after (for project record).
4. Footer height measured: should be ~30-35px (single row) vs ~120-175px (previous 4-5 rows).

---

## SECTION F: Cross-Cutting Concerns

### F1. Database Rebuild Procedure

Per design section 2.4, the production database is dropped and recreated:

```bash
# 1. Backup
pg_dump shekel_prod > shekel_backup_$(date +%Y%m%d).sql

# 2. Drop and recreate
dropdb shekel_prod
createdb shekel_prod

# 3. Apply migrations
flask db upgrade

# 4. Seed reference data
python scripts/seed_ref_tables.py
python scripts/seed_user.py
python scripts/seed_tax_brackets.py

# 5. Re-enter data via UI or scripts
# (accounts, templates, transfers, anchor balances)

# 6. Verify
# Load grid, check balances against known values
```

### F2. Migration Strategy

The project uses forward Alembic migrations. Examining `migrations/versions/`, there are 21 migration files. The convention is always-forward: new schema changes get new migration files, even though the database will be rebuilt from scratch.

Create new migration files for:
1. `add_account_id_to_transactions` -- Task 1
2. `add_transfer_id_and_category_id` -- Task 2

### F3. Naming Conventions

**Service functions:** `snake_case` matching existing pattern. `create_transfer`, `update_transfer`, `delete_transfer` (matches `mark_as_credit`, `unmark_credit` in credit_workflow).

**Test functions:** `test_<what>_<condition>` matching existing pattern. E.g., `test_create_transfer_produces_two_shadows`.

**CSS classes:** Lowercase with hyphens. `.is-transfer`, `.subtotal-row`, `.net-cash-flow-row` (matches `.section-banner-income`, `.spacer-row`).

**Template files:** No new template files. Existing templates modified.

### F4. Patterns to Follow

- **Model column addition:** Follow `credit_payback_for_id` in `transaction.py:66-68` for nullable FK. Follow `template_id` in `transaction.py:38-41` for nullable FK with ondelete.
- **Service function:** Follow `carry_forward_service.py:20-96` for service that queries and modifies transactions. Follow `credit_workflow.py:25-120` for service that creates transactions with validation.
- **Route guard:** Follow `_get_owned_transaction()` pattern in `transactions.py:39-53` for ownership checks. The transfer detection guard follows the same early-return pattern.
- **Template conditional:** Follow status badge conditional in `_transaction_cell.html:33-39` for the transfer indicator.
- **Test fixture:** Follow `seed_full_user_data` in `conftest.py:502-622` for comprehensive fixtures.

---

## Design Document Gaps

### Gap 1: Transfer.actual_amount Column

The design document (section 4.3) references `actual_amount` as an updatable field on `update_transfer`, but the Transfer model has no `actual_amount` column (inventory section 9.1). The plan implements the simpler approach: `actual_amount` is stored only on the shadow transactions. The transfer service accepts `actual_amount` as a parameter to `update_transfer` and propagates it to both shadows. The Transfer model itself does not gain an `actual_amount` column.

**Risk:** If a future feature needs to query `actual_amount` from the Transfer record directly, it would need to join through shadow transactions. This is an acceptable trade-off to avoid adding a column that would be a pure duplication of data already on the shadows.

### Gap 2: implementation_plan_section4.md File Existence

The design document and CLAUDE.md reference `docs/implementation_plan_section4.md` with GI-1 through GI-21 interaction numbers. This file now exists (created between the inventory and this plan). The HTMX interactions are fully documented in this plan's Grid Interaction Inventory section and match the Section 4 document.

### Gap 3: Investment/Retirement Routes Query Transfers Directly

The design document's "What Does NOT Change" section (13) does not mention `app/routes/investment.py` or `app/routes/retirement.py`, which query Transfer objects for contribution calculations. These routes must be updated to query shadow transactions instead (Task 11). This is not a contradiction -- the design document focuses on what does NOT change, and these routes DO change.

### Gap 4: Savings Route Loads Transfers Separately

`app/routes/savings.py` loads transfers for balance projections. This is addressed in Task 9 (removing the Transfer query and `transfers` parameter from balance calculator calls).

---

## Summary

- **Phase 3A-I tasks:** 12 (Tasks 1-12)
- **Phase 3A-II tasks:** 4 (Tasks 13-16)
- **Total tasks:** 16
- **New tests specified:** 43 (C-1: 5, C-4: 11, C-6: 3, C-7: 7, C-8: 3, C-9: 4, C-10: 6, C-13: 1, C-14: 1, C-15: 2)
- **Total estimated time:** 25-40 hours
- **Design document gaps:** 4 (all resolved in the plan)
- **Superseded tasks:** Task 4.2 from implementation_plan_section4.md (superseded by Phase 3A-II)
