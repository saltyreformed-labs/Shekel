> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Section 4 -- UX/Grid Overhaul

**Version:** 3.3
**Date:** March 27, 2026
**Prerequisite:** All Section 3 (Critical Bug Fixes) and Section 3A (Transfer Architecture Rework) changes are implemented, tested, and merged.
**Scope:** Tasks 4.1, 4.3 through 4.17 from `docs/project_roadmap_v4-1.md`. Task 4.2 is complete (resolved during transfer architecture rework, Phase 3A-II).

---

## Grid Interaction Inventory

This inventory catalogs every HTMX interaction currently on the budget grid page (`app/templates/grid/grid.html`). Every task in this plan that modifies the grid must be verified against every item in this inventory. This inventory has been verified against the current codebase as of March 27, 2026 (post-transfer-rework).

### GI-1: Transaction Cell Click -> Quick Edit

- **Trigger element:** `<div class="txn-cell" hx-get="/transactions/<id>/quick-edit" hx-trigger="click" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_cell.html`, line 10-14)
- **HTTP request:** GET `/transactions/<txn_id>/quick-edit`
- **Server handler:** `transactions.get_quick_edit` in `app/routes/transactions.py:70`
- **Response:** HTMX fragment -- renders `grid/_transaction_quick_edit.html` with a single amount input and an expand button. For shadow transactions (transfer_id is not null), the same quick edit form is used.
- **DOM update:** `hx-target="#txn-cell-<id>"`, `hx-swap="innerHTML"`. Replaces the content inside the `<div id="txn-cell-<id>">` wrapper.
- **Side effects:** None. No HX-Trigger headers.
- **Dependencies:** `_get_owned_transaction()` ownership check.

### GI-2: Quick Edit Submit -> Update Transaction

- **Trigger element:** `<form class="txn-quick-edit" hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_quick_edit.html`, line 7-11)
- **HTTP request:** PATCH `/transactions/<txn_id>` with form data `estimated_amount`.
- **Server handler:** `transactions.update_transaction` in `app/routes/transactions.py` (line 89 area). For shadow transactions, detects `txn.transfer_id is not None` and routes through `transfer_service.update_transfer()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html` with updated amount.
- **DOM update:** `hx-target="#txn-cell-<id>"`, `hx-swap="innerHTML"`.
- **Side effects:** Response header `HX-Trigger: balanceChanged`. Triggers GI-10.
- **Dependencies:** `_update_schema` (Marshmallow), `_get_owned_transaction()`. Transfer service for shadows.

### GI-3: Quick Edit Expand Button -> Full Edit Popover

- **Trigger element:** `<button class="txn-expand-btn" data-txn-id="<id>">` (in `grid/_transaction_quick_edit.html`, line 20-26). Click handled by delegated JS in `grid_edit.js`.
- **HTTP request:** JS `fetch('/transactions/<id>/full-edit')` (vanilla fetch in `grid_edit.js:90`).
- **Server handler:** `transactions.get_full_edit` in `app/routes/transactions.py:80`. For shadow transactions, detects `transfer_id`, loads the parent Transfer, and returns `transfers/_transfer_full_edit.html` with `source_txn_id`.
- **Response:** HTML fragment. Injected into `#txn-popover` via JS.
- **DOM update:** Popover content set via `popover.innerHTML = html` in `showPopover()`. Popover positioned absolutely relative to `.grid-scroll-wrapper`.
- **Side effects:** `htmx.process(popover)` called to wire up HTMX attributes.
- **Dependencies:** `positionPopover()` needs `.grid-scroll-wrapper` and a `<td>` ancestor. Statuses list needed for dropdown.

### GI-4: Full Edit Form Submit -> Update Transaction

- **Trigger element:** `<form hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 16-19). For shadow transactions opened from the grid, `transfers/_transfer_full_edit.html` uses `hx-target="#txn-cell-<source_txn_id>"`.
- **HTTP request:** PATCH `/transactions/<txn_id>` (regular) or PATCH `/transfers/<xfer_id>` (shadow via transfer form).
- **Server handler:** `transactions.update_transaction` or `transfers.update_transfer`. The transfer route's `_resolve_shadow_context()` helper detects `source_txn_id` and returns `grid/_transaction_cell.html`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **DOM update:** Targets `#txn-cell-<id>` with `innerHTML` swap.
- **Side effects:** `HX-Trigger: balanceChanged`. The `htmx:afterSwap` handler in `app.js:78-93` fires `save-flash` animation and closes the popover if the swap target is outside it.
- **Dependencies:** `_update_schema`, `_get_owned_transaction()`, statuses list.

### GI-5: Mark Done Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/mark-done" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 75-82 for expenses, line 112-120 for income "Received"). For shadow transactions, `transfers/_transfer_full_edit.html` line 88-95.
- **HTTP request:** POST `/transactions/<txn_id>/mark-done` or POST `/transfers/<xfer_id>/mark-done`.
- **Server handler:** `transactions.mark_done` in `app/routes/transactions.py:189`. For shadow transactions, routes through `transfer_service.update_transfer()`. Transfer route's `mark_done` (line 574) also uses `_resolve_shadow_context()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **DOM update:** Targets `#txn-cell-<id>` with `innerHTML` swap.
- **Side effects:** `HX-Trigger: gridRefresh`. Triggers `window.location.reload()` via `app.js:40-43`.
- **Dependencies:** Status lookup by name (`"done"` or `"received"`). `_get_owned_transaction()`.

### GI-6: Mark Credit Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/mark-credit" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 84-91). Hidden for shadow transactions: `{% if not txn.transfer_id and txn.is_expense and txn.status.name == 'projected' %}`.
- **HTTP request:** POST `/transactions/<txn_id>/mark-credit`
- **Server handler:** `transactions.mark_credit` in `app/routes/transactions.py:251`. Returns 400 for shadow transactions.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`. Calls `credit_workflow.mark_as_credit()` which creates a payback transaction.
- **Dependencies:** `credit_workflow` service, `_get_owned_transaction()`.

### GI-7: Undo Credit Button (in Full Edit Popover)

- **Trigger element:** `<button hx-delete="/transactions/<id>/unmark-credit" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 92-100). Hidden for shadow transactions: `{% elif not txn.transfer_id and txn.status.name == 'credit' %}`.
- **HTTP request:** DELETE `/transactions/<txn_id>/unmark-credit`
- **Server handler:** `transactions.unmark_credit` in `app/routes/transactions.py:272`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`. Deletes the auto-generated payback transaction.
- **Dependencies:** `credit_workflow.unmark_credit()`, `_get_owned_transaction()`.

### GI-8: Cancel Transaction Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/cancel" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 102-110)
- **HTTP request:** POST `/transactions/<txn_id>/cancel`
- **Server handler:** `transactions.cancel_transaction` in `app/routes/transactions.py:293`. For shadow transactions, routes through `transfer_service.update_transfer()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`.
- **Dependencies:** Status lookup by name `"cancelled"`, `_get_owned_transaction()`.

### GI-9: Empty Cell Click -> Quick Create

- **Trigger element:** `<div class="txn-cell txn-empty-cell" hx-get="/transactions/new/quick?category_id=X&period_id=Y&txn_type_name=Z" hx-trigger="click" hx-target="closest td" hx-swap="innerHTML">` (in `grid/_transaction_empty_cell.html`)
- **HTTP request:** GET `/transactions/new/quick?category_id=<id>&period_id=<id>&txn_type_name=<income|expense>`
- **Server handler:** `transactions.get_quick_create` in `app/routes/transactions.py:327`
- **Response:** HTMX fragment -- renders `grid/_transaction_quick_create.html`.
- **DOM update:** `hx-target="closest td"`, `hx-swap="innerHTML"`.
- **Side effects:** None.
- **Dependencies:** Category and PayPeriod ownership checks. Scenario lookup. TransactionType lookup by name via `txn_type_name`.

### GI-10: Balance Row Refresh (Triggered by balanceChanged)

- **Trigger element:** `<tfoot id="grid-summary" hx-get="/grid/balance-row?periods=N&offset=O&account_id=A" hx-trigger="balanceChanged from:body" hx-swap="outerHTML">` (in `grid/_balance_row.html`, line 11-16)
- **HTTP request:** GET `/grid/balance-row?periods=N&offset=O[&account_id=A]`
- **Server handler:** `grid.balance_row` in `app/routes/grid.py:201`
- **Response:** HTMX fragment -- renders `grid/_balance_row.html` (the entire `<tfoot>`). Contains a single `<tr>` row: Projected End Balance. Total Income, Total Expenses, and Net Cash Flow are inline `<tbody>` subtotal rows, NOT in the tfoot.
- **DOM update:** `hx-swap="outerHTML"`. Replaces the entire `<tfoot id="grid-summary">`.
- **Side effects:** None. The new tfoot retains `hx-trigger="balanceChanged from:body"`.
- **Dependencies:** `balance_calculator.calculate_balances()`, all periods, all transactions (including shadow transactions).

### GI-11: Anchor Balance Click -> Edit Form

- **Trigger element:** `<div id="anchor-display" hx-get="/accounts/<id>/anchor-form" hx-trigger="click" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** GET `/accounts/<account_id>/anchor-form`
- **Server handler:** `accounts.anchor_form` in `app/routes/accounts.py:508`
- **Response:** HTMX fragment -- renders `grid/_anchor_edit.html` with `editing=True`.
- **DOM update:** `hx-swap="outerHTML"`.
- **Side effects:** None.
- **Dependencies:** Account ownership check.

### GI-12: Anchor Balance Save

- **Trigger element:** `<form hx-patch="/accounts/<id>/true-up" hx-target="this" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** PATCH `/accounts/<account_id>/true-up` with form data `anchor_balance`.
- **Server handler:** `accounts.true_up` in `app/routes/accounts.py:451`
- **Response:** Anchor display (editing=False) plus OOB swap for `#anchor-as-of`.
- **DOM update:** `hx-target="this"`, `hx-swap="outerHTML"`.
- **Side effects:** `HX-Trigger: balanceChanged`. Triggers GI-10.
- **Dependencies:** Account ownership, `_anchor_schema` validation, `pay_period_service.get_current_period()`.

### GI-13: Anchor Balance Cancel

- **Trigger element:** `<button hx-get="/accounts/<id>/anchor-display" hx-target="closest form" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** GET `/accounts/<account_id>/anchor-display`
- **Server handler:** `accounts.anchor_display` in `app/routes/accounts.py:523`
- **Response:** HTMX fragment -- renders `grid/_anchor_edit.html` with `editing=False`.
- **DOM update:** `hx-target="closest form"`, `hx-swap="outerHTML"`.
- **Side effects:** None.
- **Dependencies:** Account ownership check.

### GI-14: Carry Forward

- **Trigger element:** `<form hx-post="/pay-periods/<id>/carry-forward" hx-swap="none">` (in `grid/grid.html`, line 84-91)
- **HTTP request:** POST `/pay-periods/<period_id>/carry-forward`
- **Server handler:** `transactions.carry_forward` in `app/routes/transactions.py:566`
- **Response:** Empty body with status 200.
- **DOM update:** `hx-swap="none"`.
- **Side effects:** `HX-Trigger: gridRefresh`. Full page reload.
- **Dependencies:** `carry_forward_service.carry_forward_unpaid()`, period ownership, scenario lookup.

### GI-15: Inline Create Submit

- **Trigger element:** `<form class="txn-quick-edit" data-mode="create" hx-post="/transactions/inline" hx-target="closest td" hx-swap="innerHTML">` (in `grid/_transaction_quick_create.html`, line 12-17)
- **HTTP request:** POST `/transactions/inline` with form data (category_id, pay_period_id, scenario_id, transaction_type_id, account_id, estimated_amount).
- **Server handler:** `transactions.create_inline` in `app/routes/transactions.py:451`
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html` with `wrap_div=True`.
- **DOM update:** `hx-target="closest td"`, `hx-swap="innerHTML"`.
- **Side effects:** `HX-Trigger: balanceChanged`. Triggers GI-10.
- **Dependencies:** `_inline_create_schema`, category/period ownership.

### GI-16: Add Transaction Modal Submit

- **Trigger element:** `<form hx-post="/transactions" hx-swap="none" data-modal-auto-close>` (in `grid/grid.html`, line 301-303 area)
- **HTTP request:** POST `/transactions` with full form data.
- **Server handler:** `transactions.create_transaction` in `app/routes/transactions.py:504`
- **Response:** HTMX fragment.
- **DOM update:** `hx-swap="none"`. The `htmx:afterRequest` handler detects `data-modal-auto-close`, hides the modal, and calls `location.reload()`.
- **Side effects:** `HX-Trigger: balanceChanged`. Moot due to page reload.
- **Dependencies:** `_create_schema`, period ownership.

### GI-17: Date Range Controls (Navigation)

- **Trigger element:** Arrow buttons and quick-select buttons in the grid header. Standard `<a href="...">` links, NOT HTMX.
- **HTTP request:** GET `/?periods=N&offset=O` (full page load).
- **Server handler:** `grid.index` in `app/routes/grid.py:33`
- **Response:** Full page.
- **DOM update:** Full page load.
- **Side effects:** None.
- **Dependencies:** All grid context variables.

### GI-18: Keyboard Navigation

- **Trigger element:** Keyboard events on the document (in `app.js:357-553`).
- **HTTP request:** None directly. Arrow keys move `cell-focused` class. Enter/Space click the focused cell's `.txn-cell`, triggering GI-1 or GI-9.
- **Server handler:** N/A (client-side only).
- **DOM update:** Adds/removes `cell-focused` class on `<td>` elements. `getDataRows()` filters out rows with classes: `section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`.
- **Side effects:** After HTMX swaps, `htmx:afterSwap` handler restores focus position.
- **Dependencies:** `.grid-table` must exist. Data rows must NOT have excluded classes.

### GI-19: Transfer Cell Click -> Quick Edit -- RETIRED

> Retired by Phase 3A-I. The TRANSFERS section was removed from the grid. Shadow transactions now appear as regular transactions in INCOME/EXPENSES sections and use GI-1 instead. When a shadow transaction's expand button is clicked (GI-3), the server detects `transfer_id` and returns the transfer full edit form.

### GI-20: Quick Edit Escape -> Revert to Display

- **Trigger element:** Escape key in a quick edit input (handled by `grid_edit.js:221-260`).
- **HTTP request:** JS calls `htmx.ajax('GET', '/transactions/<id>/cell', ...)` or `htmx.ajax('GET', '/transactions/empty-cell?...', ...)`.
- **Server handler:** `transactions.get_cell` or `transactions.get_empty_cell`.
- **Response:** HTMX fragment -- original cell display or empty cell placeholder.
- **DOM update:** Targets `#txn-cell-<id>` (edit cancel) or `closest td` (create cancel).
- **Side effects:** None.
- **Dependencies:** Transaction/category/period ownership checks.

### GI-21: F2 Key -> Expand to Full Edit/Create

- **Trigger element:** F2 key in a quick edit/create input (handled by `grid_edit.js:197-219`).
- **HTTP request:** JS `fetch('/transactions/<id>/full-edit')` or `fetch('/transactions/new/full?...')`.
- **Server handler:** `transactions.get_full_edit` or `transactions.get_full_create`. For shadow transactions, returns transfer form.
- **Response:** Full edit/create popover HTML.
- **DOM update:** Injected into `#txn-popover`.
- **Side effects:** `htmx.process(popover)` called.
- **Dependencies:** Same as GI-3.

---


## Status String Audit

This audit catalogs every location in the codebase where `status.name` (or an equivalent string comparison against a status value) is used in logic. This is the foundation of task 4.4a. Organized by file.

**Reference data -- target state after refactor:**

Three boolean columns are added to `ref.statuses` for grouping logic. The `name` column becomes the sole display label (renamed from internal identifiers to user-facing text). A Python Enum defines valid status members, and a one-time startup cache maps enum members to their database integer IDs.

| ID | Name (display) | is_settled | is_immutable | excludes_from_balance |
|----|---------------|------------|-------------|----------------------|
| 1  | Projected     | FALSE      | FALSE       | FALSE                |
| 2  | Paid          | TRUE       | TRUE        | FALSE                |
| 3  | Received      | TRUE       | TRUE        | FALSE                |
| 4  | Credit        | FALSE      | TRUE        | TRUE                 |
| 5  | Cancelled     | FALSE      | TRUE        | TRUE                 |
| 6  | Settled       | TRUE       | TRUE        | FALSE                |

**Boolean column definitions:**

- **`is_settled`** -- Real money has changed hands. The balance calculator uses `actual_amount` instead of `estimated_amount`. The grid shows a checkmark badge. Emergency fund calculations count this as a historical actual. TRUE for: Paid, Received, Settled.
- **`is_immutable`** -- The recurrence engine must not modify or delete transactions with this status. These represent finalized records (actual events or deliberate user actions). TRUE for: Paid, Received, Credit, Cancelled, Settled.
- **`excludes_from_balance`** -- This transaction contributes zero to the checking account balance. `effective_amount` returns `Decimal("0")`. TRUE for: Credit (money came from credit card, not checking), Cancelled (transaction didn't happen).

**Note on the Settled status:** The current codebase never assigns the "settled" status to any transaction -- no route or service transitions to it. It exists as a placeholder for a future reconciliation workflow. The boolean values are set correctly now (TRUE, TRUE, FALSE) to prevent latent bugs when that workflow is implemented. Without this, a settled transaction would be treated as projected by the balance calculator (wrong amounts), modifiable by the recurrence engine (data corruption), and missing from emergency fund actuals (undercounted expenses).

**Design principles:**

1. **Python Enum as the source of truth for valid statuses.** `app/enums.py` defines `StatusEnum` with string values matching the display names in the database. Code references `StatusEnum.PROJECTED`, not integer literals or name strings. Typos are caught at import time.
2. **One-time cached ID resolver.** `app/ref_cache.py` loads the mapping from enum member to database integer ID once at startup. All subsequent ID lookups are in-memory dict lookups. The loader fails loudly if any enum member is missing from the database.
3. **Boolean columns for groupings.** `is_settled`, `is_immutable`, `excludes_from_balance` replace all hardcoded frozensets. The balance calculator checks `txn.status.is_settled`. The recurrence engine checks `txn.status.is_immutable`. Adding a new status to a group means setting the boolean on the database row, not editing Python code.
4. **Single `name` column for display.** The `name` column is renamed from internal identifiers ("done") to user-facing labels ("Paid"). No separate `display_label` column. Logic never reads `name` (except the one-time cache loader at startup).
5. **IDs are database-assigned, not hardcoded.** No explicit IDs in seed scripts. Auto-increment is fine. The cache resolves names to IDs at boot. If the database has Projected at id=47, the app works correctly.

### Python Files -- Services

**`app/services/balance_calculator.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 32 | `SETTLED_STATUSES = frozenset({"done", "received"})` | Define settled set | Remove. Use `txn.status.is_settled` boolean column. |
| 101-102 | `status_name = txn.status.name ...` / `if status_name in SETTLED_STATUSES:` | Settled check in anchor period | `if txn.status.is_settled:` |
| 246-247 | `status_name = ...` / `if status_name in ("cancelled",):` | Skip cancelled | `if txn.status.excludes_from_balance:` |
| 287-290 | `status_name = ...` / `if status_name in ("credit", "cancelled"):` | Exclude from balance | `if txn.status.excludes_from_balance:` |
| 294 | `if status_name in SETTLED_STATUSES:` | Use actual_amount | `if txn.status.is_settled:` |
| 321-324 | `status_name = ...` / `if status_name in ("credit", "cancelled", "done", "received"):` | Skip non-projected for subtotals | `if txn.status_id != ref_cache.status_id(StatusEnum.PROJECTED):` |

**`app/services/recurrence_engine.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 40 | `IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})` | Statuses engine must not overwrite | Remove. Use `txn.status.is_immutable` boolean column. |
| 90 | `projected_status = db.session.query(Status).filter_by(name="projected").one()` | Get projected status ID | `ref_cache.status_id(StatusEnum.PROJECTED)` |
| 108-111 | `status_name = existing_txn.status.name ...` / `if status_name in IMMUTABLE_STATUSES:` | Skip immutable | `if existing_txn.status.is_immutable:` |
| 215-218 | Same pattern | Skip immutable during cleanup | `if txn.status.is_immutable:` |

**`app/services/transfer_recurrence.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 31 | `IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})` | Same | Remove. Use `xfer.status.is_immutable`. |
| 71 | `projected_status = db.session.query(Status).filter_by(name="projected").one()` | Get projected ID | `ref_cache.status_id(StatusEnum.PROJECTED)` |
| 82-83 | `status_name = xfer.status.name ...` / `if status_name in IMMUTABLE_STATUSES:` | Skip immutable | `if xfer.status.is_immutable:` |
| 166-168 | Same | Same | Same |

**`app/services/credit_workflow.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 60 | `txn.status.name == "credit"` | Already credit? | `txn.status_id == ref_cache.status_id(StatusEnum.CREDIT)` |
| 70 | `txn.status.name != "projected"` | Only projected -> credit | `txn.status_id != ref_cache.status_id(StatusEnum.PROJECTED)` |
| 77 | `filter_by(name="credit").one()` | Fetch credit ID | `ref_cache.status_id(StatusEnum.CREDIT)` |
| 78 | `filter_by(name="projected").one()` | Fetch projected ID | `ref_cache.status_id(StatusEnum.PROJECTED)` |
| 144 | `filter_by(name="projected").one()` | Revert to projected | `ref_cache.status_id(StatusEnum.PROJECTED)` |

**`app/services/carry_forward_service.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 65 | `filter_by(name="projected").one()` | Find projected transactions | `ref_cache.status_id(StatusEnum.PROJECTED)` |

**`app/services/chart_data_service.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 385 | `filter_by(name="done").first()` | Chart filter | `ref_cache.status_id(StatusEnum.DONE)` |
| 386 | `filter_by(name="projected").first()` | Chart filter | `ref_cache.status_id(StatusEnum.PROJECTED)` |

### Python Files -- Routes

**`app/routes/transactions.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 204 | `filter_by(name="received").one()` | Mark income received | `ref_cache.status_id(StatusEnum.RECEIVED)` |
| 206 | `filter_by(name="done").one()` | Mark expense done | `ref_cache.status_id(StatusEnum.DONE)` |
| 213 | `filter_by(name="done").one()` | Shadow txn done | `ref_cache.status_id(StatusEnum.DONE)` |
| 305, 315 | `filter_by(name="cancelled").one()` | Cancel | `ref_cache.status_id(StatusEnum.CANCELLED)` |
| 481, 524 | `filter_by(name="projected").one()` | Create with projected | `ref_cache.status_id(StatusEnum.PROJECTED)` |

**`app/routes/transfers.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 334, 374, 513 | `filter_by(name="projected").one()` | Create/restore transfer | `ref_cache.status_id(StatusEnum.PROJECTED)` |
| 574 | `filter_by(name="done").one()` | Mark done | `ref_cache.status_id(StatusEnum.DONE)` |
| 603 | `filter_by(name="cancelled").one()` | Cancel | `ref_cache.status_id(StatusEnum.CANCELLED)` |

**`app/routes/templates.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 318, 347 | `filter_by(name="projected").one()` | Create from template | `ref_cache.status_id(StatusEnum.PROJECTED)` |

**`app/routes/savings.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 400 | `txn.status.name in ("done", "received")` | Emergency fund filter | `txn.status.is_settled` |

### Python Files -- Models

**`app/models/transaction.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 116 | `status.name in ("credit", "cancelled")` | effective_amount -> 0 | `self.status.excludes_from_balance` |
| 118 | `status.name in ("done", "received")` | effective_amount -> actual | `self.status.is_settled` |
| 125 | `transaction_type.name == "income"` | is_income | `self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.INCOME)` |
| 130 | `transaction_type.name == "expense"` | is_expense | `self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)` |

**`app/models/transfer.py`:**

| Line | Code | Purpose | Replacement |
|------|------|---------|-------------|
| 95 | `status.name == "cancelled"` | effective_amount -> 0 | `self.status.excludes_from_balance` |

### Template Files

**`app/templates/grid/grid.html`:**

| Line | Code | Replacement |
|------|------|-------------|
| 140 | `txn.status.name != 'cancelled'` | `txn.status_id != STATUS_CANCELLED` |
| 168, 248, 264 | `txn.status.name not in ('credit', 'cancelled', 'done', 'received')` | `txn.status_id == STATUS_PROJECTED` |
| 220 | `txn.status.name != 'cancelled'` | `txn.status_id != STATUS_CANCELLED` |

**`app/templates/grid/_transaction_cell.html`:**

| Line | Code | Replacement |
|------|------|-------------|
| 17 | `({{ t.status.name }})` aria-label | Keep -- display use |
| 34 | `t.status.name in ('done', 'received')` | `t.status.is_settled` |
| 35 | `t.status.name\|capitalize` | `t.status.name` (already capitalized after rename) |
| 36 | `t.status.name == 'credit'` | `t.status_id == STATUS_CREDIT` |

**`app/templates/grid/_transaction_full_edit.html`:**

| Line | Code | Replacement |
|------|------|-------------|
| 74 | `txn.status.name != 'done'` | `txn.status_id != STATUS_DONE` |
| 84 | `txn.status.name == 'projected'` | `txn.status_id == STATUS_PROJECTED` |
| 92 | `txn.status.name == 'credit'` | `txn.status_id == STATUS_CREDIT` |
| 102 | `txn.status.name == 'projected'` | `txn.status_id == STATUS_PROJECTED` |
| 112 | `txn.status.name == 'projected'` | `txn.status_id == STATUS_PROJECTED` |

**`app/templates/grid/_transaction_full_create.html`:**

| Line | Code | Replacement |
|------|------|-------------|
| 54 | `s.name == 'projected'` | `s.id == STATUS_PROJECTED` |

**`app/templates/transfers/_transfer_cell.html`:**

| Line | Code | Replacement |
|------|------|-------------|
| 16 | `({{ xfer.status.name }})` | Keep -- display use |
| 34 | `xfer.status.name in ('done', 'received')` | `xfer.status.is_settled` |

**`app/templates/transfers/_transfer_full_edit.html`:**

| Line | Code | Replacement |
|------|------|-------------|
| 87 | `xfer.status.name == 'projected'` | `xfer.status_id == STATUS_PROJECTED` |

### Test Files

Tests use `filter_by(name="...")` extensively for fixture setup. After the refactor, tests should use `ref_cache.status_id(StatusEnum.PROJECTED)` for setting status IDs and the enum for assertions. The name-based queries in test fixtures should be replaced with enum-based cache lookups. Tests that assert on "Done" in HTML responses must change to "Paid".

---

## Reference Table String Audit

### TransactionType

A `TxnTypeEnum` is defined with members `INCOME` and `EXPENSE`. The ref_cache maps them to database IDs.

| File | Line | Code | Replacement |
|------|------|------|-------------|
| `transaction.py` | 125, 130 | `.name == "income"` / `.name == "expense"` | `ref_cache.txn_type_id(TxnTypeEnum.INCOME)` / `.EXPENSE` |
| `transfer_service.py` | 295, 298, 388, 391, 719, 722 | `filter_by(name="expense").one()` etc. | `ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)` etc. |
| `credit_workflow.py` | 79 | `filter_by(name="expense").one()` | `ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)` |
| `transactions.py` | 351, 394 | `filter_by(name=txn_type_name).one()` | Accept `transaction_type_id` param, use ID directly |
| `savings.py` | 85 | `filter_by(name="income").first()` | `ref_cache.txn_type_id(TxnTypeEnum.INCOME)` |
| `salary.py` | 158 | `filter_by(name="income").one()` | Same |
| `retirement.py` | 174 | `filter_by(name="income").first()` | Same |
| `investment.py` | 142, 361 | Same | Same |

**`txn_type_name` query parameter:** The grid passes `txn_type_name=income/expense` as strings. Change to pass `transaction_type_id` integers. Requires coordinated changes in grid template, JS, quick create template, and route handlers.

### AccountType

An `AcctTypeEnum` is defined with members for all 18 account types. A `ref.account_type_categories` table replaces the bare string `category` column with a proper FK. `AcctCategoryEnum` covers Asset/Liability/Retirement/Investment.

**Schema change:** `category VARCHAR(20)` on `ref.account_types` becomes `category_id INTEGER FK` referencing `ref.account_type_categories`. Boolean columns `has_parameters` and `has_amortization` are added.

| File | Line | Code | Replacement |
|------|------|------|-------------|
| `accounts.py` | 133, 143, 145 | `account_type.name == "hysa"` etc. | `account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA)` etc. |
| `accounts.py` | 548, 649 | `account_type.name != "hysa"` | ID comparison |
| `auto_loan.py` | 37, 111 | `account_type.name != "auto_loan"` | ID comparison |
| `mortgage.py` | 47, 172 | `account_type.name != "mortgage"` | ID comparison |
| `savings.py` | 100, 112-118, 412 | `filter_by(name="hysa").first()` etc. | Enum + cache |
| `account_resolver.py` | 44 | `filter_by(name="checking").first()` | Enum + cache |
| `auth_service.py` | 391 | `filter_by(name="checking").one()` | Enum + cache |
| `chart_data_service.py` | 218, 253 | `account.account_type.name` | `account.account_type.category_id` or `account.account_type_id` |
| `savings/dashboard.html` | 50-103 | `account_type.name == 'hysa'` etc. (12 occ.) | ID comparisons via Jinja globals |

### RecurrencePattern

A `RecurrencePatternEnum` is defined. Form dropdowns send pattern IDs instead of names.

| File | Line | Code | Replacement |
|------|------|------|-------------|
| `transfers.py` | 247 | `filter_by(name=pattern_name).one()` | Accept `pattern_id`, use directly |
| `templates.py` | 233 | Same | Same |

### Other Ref Tables (FilingStatus, DeductionTiming, CalcMethod, RaiseType)

Used only for display in templates (e.g., `filing_status.name|replace('_', ' ')|title`). No logic comparisons found. No code changes needed beyond adding enum definitions for future consistency.

---

## Commit Plan

### Ordering Rationale

1. **Task 4.4a+4.4b (Status refactor) MUST come first.** Adds boolean columns, creates enums and cache, migrates all logic, renames display names. Every subsequent task uses the corrected patterns.
2. **Task 4.4c (Ref table audit) immediately follows.** Same type of work for account types, transaction types, recurrence patterns. Includes `category_id` FK migration.
3. **Task 4.13 (Emergency fund fix) is a correctness fix** and is prioritized over cosmetic changes.
4. **Task 4.1 (Grid layout) comes before 4.12 (Tooltips).** Layout affects tooltip content.
5. **Tasks 4.7/4.8 and 4.15 are adjacent** (account parameter setup).

### Commit Sequence

| Commit | Task | Type | Commit Message |
|--------|------|------|----------------|
| #0 | Prereq | test | `test(grid): add payday workflow end-to-end regression test suite` |
| #1 | 4.4a+4.4b | refactor | `refactor(status): add boolean columns, enum cache, rename display names` |
| #2 | 4.4c | refactor | `refactor(ref): replace all ref table name lookups with enum-cached IDs` |
| #3 | 4.13 | fix | `fix(savings): include transfer expenses in emergency fund coverage` |
| #4 | 4.5 | feat | `feat(salary): improve deduction frequency display with descriptive labels` |
| #5 | 4.6 | feat | `feat(salary): reorganize tax config page -- adjustable settings first` |
| #6 | 4.11 | feat | `feat(salary): move View Breakdown and View Projection buttons higher` |
| #7 | 4.7/4.8 | feat | `feat(accounts): redirect to parameter config after creating parameterized accounts` |
| #8 | 4.15 | fix | `fix(auto_loan): add editable term field and pre-populate principal from balance` |
| #9 | 4.14 | feat | `feat(accounts): add balance projection to checking account detail page` |
| #10 | 4.16 | fix | `fix(retirement): preserve form data and highlight fields on validation errors` |
| #11 | 4.17 | feat | `feat(retirement): clarify return rate slider behavior with explanatory text` |
| #12 | 4.9 | feat | `feat(charts): add dash patterns and varied line weights to balance chart` |
| #13 | 4.10 | feat | `feat(charts): add time frame controls to balance over time chart` |
| #14 | 4.3 | feat | `feat(grid): condense pay period date headers -- omit year for current year` |
| #15 | 4.1 | feat | `feat(grid): show transaction names in row headers for clarity` |
| #16 | 4.12 | feat | `feat(grid): enhance tooltips with full amount and faster display` |

---

## Per-Task Sections

### Commit #0: Payday Workflow Regression Test Suite

**A. Commit message:** `test(grid): add payday workflow end-to-end regression test suite`

**B. Problem statement.** Before any Section 4 changes, a regression test suite must exist that verifies the complete payday workflow (true-up, mark received, carry forward, mark done, mark credit, balance refresh). This safety net protects against breakage during the schema changes, enum refactoring, and DOM restructuring that follows.

**C. Files modified.**
- `tests/test_routes/test_grid_regression.py` -- New. Contains 7 regression tests covering the payday workflow interaction sequence.

**D. Implementation approach.**

Create a new test file `tests/test_routes/test_grid_regression.py` with a `TestPaydayWorkflowRegression` class. Each test exercises a distinct step of the payday workflow as documented in the requirements (v2 Section 3). Tests use existing fixtures (`seed_user`, `auth_client`) and create necessary data (pay periods, transactions, accounts) inline. Every test verifies both the HTTP response and the database state after the operation.

The test file must import:
- `seed_user`, `auth_client` fixtures from conftest
- `Status`, `TransactionType`, `AccountType` from `app.models.ref`
- `Transaction`, `PayPeriod`, `Account`, `Scenario`, `Category` from budget models

Each test follows the pattern: create fixtures -> perform action -> assert response -> assert database state.

**E. Test cases.**

**C-0-1:** `test_trueup_anchor_balance`
- Setup: `seed_user`, `auth_client`, checking account with existing anchor balance, current pay period.
- Action: PATCH `/accounts/<id>/true-up` with `anchor_balance=5000.00`.
- Expected: Response status 200. Response contains updated balance display. `HX-Trigger` header contains `balanceChanged`. Database shows updated `anchor_balance` and `anchor_period_id`.
- New test.
- Why: Guards against anchor balance editing regression -- the foundation of all balance calculations.

**C-0-2:** `test_mark_paycheck_received`
- Setup: `seed_user`, `auth_client`, seeded income transaction with status "projected".
- Action: POST `/transactions/<id>/mark-done`.
- Expected: Response status 200. Transaction status changed to "received" (because it's income). `actual_amount` preserved. `HX-Trigger` contains `gridRefresh`. Response HTML contains the checkmark badge (`badge-done`).
- New test.
- Why: Guards against the income status transition workflow.

**C-0-3:** `test_carry_forward_unpaid`
- Setup: `seed_user`, `auth_client`, past period with 2 projected + 1 done transaction, current period.
- Action: POST `/pay-periods/<past_period_id>/carry-forward`.
- Expected: Response status 200. `HX-Trigger` contains `gridRefresh`. 2 projected transactions moved to current period (`pay_period_id` updated). Done transaction stays in past period. Moved transactions have `is_override=True`.
- New test.
- Why: Guards against carry forward logic regression.

**C-0-4:** `test_mark_expense_done`
- Setup: `seed_user`, `auth_client`, seeded expense transaction with status "projected".
- Action: POST `/transactions/<id>/mark-done`.
- Expected: Response status 200. Transaction status changed to "done". `HX-Trigger` contains `gridRefresh`. Response HTML contains the checkmark badge.
- New test.
- Why: Guards against expense status transition.

**C-0-5:** `test_mark_credit_creates_payback`
- Setup: `seed_user`, `auth_client`, seeded expense transaction with status "projected", next pay period exists.
- Action: POST `/transactions/<id>/mark-credit`.
- Expected: Response status 200. Transaction status changed to "credit". A payback transaction exists in the next period with `credit_payback_for_id` pointing to the original. Payback has status "projected", category "Credit Card: Payback", and amount matching the original. `HX-Trigger` contains `gridRefresh`.
- New test.
- Why: Guards against credit card workflow and auto-payback generation.

**C-0-6:** `test_balance_row_refresh`
- Setup: `seed_user`, `auth_client`, seeded periods, seeded transactions and account.
- Action: GET `/grid/balance-row?periods=6&offset=0`.
- Expected: Response status 200. Response contains `<tfoot id="grid-summary">`. Response contains exactly 1 `<tr>` element in the tfoot (Projected End Balance only -- Total Income, Total Expenses, and Net Cash Flow are in the `<tbody>`, not the `<tfoot>`). Response contains balance amounts. Response contains the `hx-trigger="balanceChanged from:body"` attribute.
- New test.
- Why: Guards against balance row HTMX partial rendering.

**C-0-7:** `test_full_payday_sequence`
- Setup: `seed_user`, `auth_client`, 3 periods (past, current, future) with income + expenses, checking account with anchor balance.
- Action: Perform steps 1-5 in sequence:
  1. PATCH `/accounts/<id>/true-up` with new balance.
  2. POST `/transactions/<income_id>/mark-done` to mark paycheck received.
  3. POST `/pay-periods/<past_id>/carry-forward` to carry forward.
  4. POST `/transactions/<expense_id>/mark-done` to mark expense done.
  5. POST `/transactions/<expense2_id>/mark-credit` to mark one as credit.
- Expected: After all steps, GET `/grid/balance-row` returns correct balances. The `<tfoot>` has a single row (Projected End Balance). The balance calculation matches hand-computed expected values.
- New integration test.
- Why: Guards against interactions between workflow steps.

**F. Manual verification steps.** N/A -- test-only commit.

**G. Downstream effects.** None. This commit only adds tests.

**H. Rollback notes.** No dependencies. Can be reverted independently.

---

### Commit #1: Task 4.4a+4.4b -- Status Schema, Enum Cache, Display Rename

**A. Commit message:** `refactor(status): add boolean columns, enum cache, rename display names`

**B. Problem statement.** The codebase uses `Status.filter_by(name="done")` and `txn.status.name == "done"` in 60+ locations across 15+ files for logic decisions. This makes the `name` column load-bearing for both logic and display, prevents safe renaming, and scatters 45 unnecessary database queries across request paths. The status audit (above) documents every occurrence.

This commit simultaneously: (1) adds boolean grouping columns to `ref.statuses`, (2) creates Python Enums and a one-time cached ID resolver, (3) migrates all logic from name strings to boolean/ID references, (4) renames status display names ("done" -> "Paid"), and (5) registers cached IDs as Jinja globals for templates.

**C. Files modified.**

- `app/enums.py` -- New. `StatusEnum` and `TxnTypeEnum` Python Enums.
- `app/ref_cache.py` -- New. One-time startup cache mapping enum members to database IDs.
- `migrations/versions/<new>.py` -- New. Alembic migration: add 3 boolean columns, rename 8 status/txn-type display names.
- `app/models/ref.py` -- Modified. Add `is_settled`, `is_immutable`, `excludes_from_balance` columns to Status model.
- `app/__init__.py` -- Modified. Call `ref_cache.init()` in `create_app()`. Register cached IDs as Jinja globals. Update `_seed_ref_tables()` to include boolean values.
- `scripts/seed_ref_tables.py` -- Modified. Add boolean column values to Status seeds. Capitalize TransactionType names.
- `tests/conftest.py` -- Modified. Same seed updates.
- `app/services/balance_calculator.py` -- Modified. Remove `SETTLED_STATUSES` frozenset. Replace all `status.name` comparisons with boolean columns (`is_settled`, `excludes_from_balance`) and ID comparisons. Affected lines: 32, 101-102, 246-247, 287-294, 321-324.
- `app/services/recurrence_engine.py` -- Modified. Remove `IMMUTABLE_STATUSES` frozenset. Replace with `status.is_immutable` boolean. Replace projected lookup with `ref_cache.status_id(StatusEnum.PROJECTED)`. Affected lines: 40, 90, 108-111, 215-218.
- `app/services/transfer_recurrence.py` -- Modified. Same pattern as recurrence_engine. Affected lines: 31, 71, 82-83, 166-168.
- `app/services/credit_workflow.py` -- Modified. Replace all `status.name` checks and `filter_by(name=...)` lookups with enum cache. Affected lines: 60, 70, 77-78, 144.
- `app/services/carry_forward_service.py` -- Modified. Replace `filter_by(name="projected")` with `ref_cache.status_id(StatusEnum.PROJECTED)`. Affected line: 65.
- `app/services/chart_data_service.py` -- Modified. Replace `filter_by(name="done")` and `filter_by(name="projected")` with enum cache. Affected lines: 385-386.
- `app/routes/transactions.py` -- Modified. Replace all status name lookups with enum cache. Affected lines: 204, 206, 213, 305, 315, 481, 524.
- `app/routes/transfers.py` -- Modified. Same. Affected lines: 334, 374, 513, 574, 603.
- `app/routes/templates.py` -- Modified. Replace projected lookups. Affected lines: 318, 347.
- `app/routes/savings.py` -- Modified. Replace `txn.status.name in ("done", "received")` with `txn.status.is_settled`. Affected line: 400.
- `app/models/transaction.py` -- Modified. Replace `status.name` checks in `effective_amount` with boolean columns. Replace `transaction_type.name` checks in `is_income`/`is_expense` with `ref_cache.txn_type_id()`. Affected lines: 116, 118, 125, 130.
- `app/models/transfer.py` -- Modified. Replace `status.name == "cancelled"` with `status.excludes_from_balance`. Affected line: 95.
- `app/templates/grid/grid.html` -- Modified. Replace all `txn.status.name` comparisons with `txn.status_id` / `txn.status.is_settled` / boolean checks. Affected lines: 140, 168, 220, 248, 264.
- `app/templates/grid/_transaction_cell.html` -- Modified. Replace `t.status.name in ('done', 'received')` with `t.status.is_settled` (line 34). Replace `t.status.name == 'credit'` with `t.status_id == STATUS_CREDIT` (line 36). Keep `t.status.name` in aria-label (line 17) -- this is display use. Remove `|capitalize` filter since names are already capitalized after rename (line 35).
- `app/templates/grid/_transaction_full_edit.html` -- Modified. Replace all `txn.status.name` comparisons with ID checks (lines 74, 84, 92, 102, 112). Change "Done" button text to "Paid" (line 80).
- `app/templates/grid/_transaction_full_create.html` -- Modified. Replace `s.name == 'projected'` with `s.id == STATUS_PROJECTED` (line 54).
- `app/templates/transfers/_transfer_cell.html` -- Modified. Replace `xfer.status.name in ('done', 'received')` with `xfer.status.is_settled` (line 34). Keep `xfer.status.name` in aria-label (line 16).
- `app/templates/transfers/_transfer_full_edit.html` -- Modified. Replace `xfer.status.name == 'projected'` with `xfer.status_id == STATUS_PROJECTED` (line 87). Change "Done" button text to "Paid" (line 94).
- `tests/` (multiple files) -- Modified. Update fixtures to use boolean columns. Update assertions for "Paid" instead of "Done". Use enum cache for status ID assignment.

**D. Implementation approach.**

**Step 1: Create `app/enums.py`.**

```python
"""
Shekel Budget App -- Reference Table Enums

Python Enums whose values match the ``name`` column in ref-schema tables.
Used by the ref_cache module to resolve enum members to database integer IDs
at application startup.  Application code references enum members
(e.g., StatusEnum.PROJECTED), never raw strings or integer literals.
"""

import enum


class StatusEnum(enum.Enum):
    """Transaction status values.  Values match ref.statuses.name."""
    PROJECTED = "Projected"
    DONE = "Paid"
    RECEIVED = "Received"
    CREDIT = "Credit"
    CANCELLED = "Cancelled"
    SETTLED = "Settled"


class TxnTypeEnum(enum.Enum):
    """Transaction type values.  Values match ref.transaction_types.name."""
    INCOME = "Income"
    EXPENSE = "Expense"
```

**Step 2: Create `app/ref_cache.py`.**

```python
"""
Shekel Budget App -- Reference Table ID Cache

Loaded once at application startup by init().  Maps Python Enum members
to their database integer IDs.  All application code uses these cached
IDs for status/type assignment.  Grouping logic uses boolean columns
on the ref models directly (e.g., txn.status.is_settled).

The cache eliminates per-request database queries for reference lookups
and decouples application logic from specific ID values.
"""

from app.enums import StatusEnum, TxnTypeEnum

_status_ids = {}      # StatusEnum member -> int
_txn_type_ids = {}    # TxnTypeEnum member -> int
_initialized = False


def init(db_session):
    """Load reference table IDs from the database.  Called once from create_app()."""
    global _initialized
    from app.models.ref import Status, TransactionType

    for row in db_session.query(Status).all():
        for member in StatusEnum:
            if member.value == row.name:
                _status_ids[member] = row.id
                break

    missing = [m.name for m in StatusEnum if m not in _status_ids]
    if missing:
        raise RuntimeError(
            f"ref.statuses is missing rows for: {missing}. "
            f"Run seed scripts."
        )

    for row in db_session.query(TransactionType).all():
        for member in TxnTypeEnum:
            if member.value == row.name:
                _txn_type_ids[member] = row.id
                break

    missing = [m.name for m in TxnTypeEnum if m not in _txn_type_ids]
    if missing:
        raise RuntimeError(
            f"ref.transaction_types is missing rows for: {missing}. "
            f"Run seed scripts."
        )

    _initialized = True


def status_id(member: StatusEnum) -> int:
    """Return the database ID for a StatusEnum member."""
    return _status_ids[member]


def txn_type_id(member: TxnTypeEnum) -> int:
    """Return the database ID for a TxnTypeEnum member."""
    return _txn_type_ids[member]
```

**Step 3: Create the Alembic migration.**

Migration message: `add boolean columns to ref.statuses and rename display names`

```python
def upgrade():
    # Add boolean grouping columns to ref.statuses.
    op.add_column('statuses', sa.Column('is_settled', sa.Boolean(),
                  nullable=False, server_default='false'), schema='ref')
    op.add_column('statuses', sa.Column('is_immutable', sa.Boolean(),
                  nullable=False, server_default='false'), schema='ref')
    op.add_column('statuses', sa.Column('excludes_from_balance', sa.Boolean(),
                  nullable=False, server_default='false'), schema='ref')

    # Set boolean values by current name (before rename).
    op.execute("UPDATE ref.statuses SET is_settled = TRUE "
               "WHERE name IN ('done', 'received', 'settled')")
    op.execute("UPDATE ref.statuses SET is_immutable = TRUE "
               "WHERE name IN ('done', 'received', 'credit', 'cancelled', 'settled')")
    op.execute("UPDATE ref.statuses SET excludes_from_balance = TRUE "
               "WHERE name IN ('credit', 'cancelled')")

    # Rename status display names (name column is now display-only).
    op.execute("UPDATE ref.statuses SET name = 'Projected' WHERE name = 'projected'")
    op.execute("UPDATE ref.statuses SET name = 'Paid' WHERE name = 'done'")
    op.execute("UPDATE ref.statuses SET name = 'Received' WHERE name = 'received'")
    op.execute("UPDATE ref.statuses SET name = 'Credit' WHERE name = 'credit'")
    op.execute("UPDATE ref.statuses SET name = 'Cancelled' WHERE name = 'cancelled'")
    op.execute("UPDATE ref.statuses SET name = 'Settled' WHERE name = 'settled'")

    # Also capitalize transaction type display names.
    op.execute("UPDATE ref.transaction_types SET name = 'Income' WHERE name = 'income'")
    op.execute("UPDATE ref.transaction_types SET name = 'Expense' WHERE name = 'expense'")


def downgrade():
    # Revert transaction type names.
    op.execute("UPDATE ref.transaction_types SET name = 'income' WHERE name = 'Income'")
    op.execute("UPDATE ref.transaction_types SET name = 'expense' WHERE name = 'Expense'")

    # Revert status names.
    op.execute("UPDATE ref.statuses SET name = 'projected' WHERE name = 'Projected'")
    op.execute("UPDATE ref.statuses SET name = 'done' WHERE name = 'Paid'")
    op.execute("UPDATE ref.statuses SET name = 'received' WHERE name = 'Received'")
    op.execute("UPDATE ref.statuses SET name = 'credit' WHERE name = 'Credit'")
    op.execute("UPDATE ref.statuses SET name = 'cancelled' WHERE name = 'Cancelled'")
    op.execute("UPDATE ref.statuses SET name = 'settled' WHERE name = 'Settled'")

    # Drop boolean columns.
    op.drop_column('statuses', 'excludes_from_balance', schema='ref')
    op.drop_column('statuses', 'is_immutable', schema='ref')
    op.drop_column('statuses', 'is_settled', schema='ref')
```

**Step 4: Update the Status model in `app/models/ref.py`.**

Add the three boolean columns to the existing Status class (currently at lines 38-51):

```python
class Status(db.Model):
    """Transaction status reference.

    Values: Projected, Paid, Received, Credit, Cancelled, Settled.
    Boolean columns define grouping behavior:
      is_settled: money has changed hands (use actual_amount, show checkmark)
      is_immutable: recurrence engine must not modify
      excludes_from_balance: contributes zero to checking balance
    """

    __tablename__ = "statuses"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(15), unique=True, nullable=False)
    is_settled = db.Column(db.Boolean, nullable=False, default=False)
    is_immutable = db.Column(db.Boolean, nullable=False, default=False)
    excludes_from_balance = db.Column(db.Boolean, nullable=False, default=False)
```

**Step 5: Update seed scripts to include boolean values.**

In `scripts/seed_ref_tables.py`, `app/__init__.py._seed_ref_tables()`, and `tests/conftest.py`, change from the current list-based seeding:

```python
# BEFORE (current code in __init__.py line 367):
Status: ["projected", "done", "received", "credit", "cancelled", "settled"],
```

To explicit dict-based seeding with booleans:

```python
# AFTER:
STATUS_SEEDS = [
    {"name": "Projected", "is_settled": False, "is_immutable": False, "excludes_from_balance": False},
    {"name": "Paid",      "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
    {"name": "Received",  "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
    {"name": "Credit",    "is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
    {"name": "Cancelled", "is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
    {"name": "Settled",   "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
]
for seed in STATUS_SEEDS:
    if not db.session.query(Status).filter_by(name=seed["name"]).first():
        db.session.add(Status(**seed))
```

Also capitalize TransactionType names: `"Income"`, `"Expense"` (currently `"income"`, `"expense"`).

**Step 6: Call `ref_cache.init()` in `create_app()` and register Jinja globals.**

In `app/__init__.py`, after `_seed_ref_tables()`:

```python
from app import ref_cache
from app.enums import StatusEnum

ref_cache.init(db.session)

# Expose cached status IDs to all Jinja templates.
app.jinja_env.globals['STATUS_PROJECTED'] = ref_cache.status_id(StatusEnum.PROJECTED)
app.jinja_env.globals['STATUS_DONE'] = ref_cache.status_id(StatusEnum.DONE)
app.jinja_env.globals['STATUS_RECEIVED'] = ref_cache.status_id(StatusEnum.RECEIVED)
app.jinja_env.globals['STATUS_CREDIT'] = ref_cache.status_id(StatusEnum.CREDIT)
app.jinja_env.globals['STATUS_CANCELLED'] = ref_cache.status_id(StatusEnum.CANCELLED)
```

**Step 7: Replace all status name comparisons in services.**

The replacements follow consistent patterns. Here are the concrete before/after examples for each service:

**`balance_calculator.py` -- Remove `SETTLED_STATUSES`, use booleans:**

```python
# BEFORE (line 32):
SETTLED_STATUSES = frozenset({"done", "received"})

# AFTER: DELETE this line entirely.  Use boolean columns instead.

# BEFORE (lines 101-102):
status_name = txn.status.name if txn.status else "projected"
if status_name in SETTLED_STATUSES:

# AFTER:
if txn.status and txn.status.is_settled:

# BEFORE (lines 287-290 in _sum_remaining):
status_name = txn.status.name if txn.status else "projected"
if status_name in ("credit", "cancelled"):
    continue
if status_name in SETTLED_STATUSES:
    continue

# AFTER:
if txn.status and txn.status.excludes_from_balance:
    continue
if txn.status and txn.status.is_settled:
    continue

# BEFORE (lines 321-324 in _sum_all):
status_name = txn.status.name if txn.status else "projected"
if status_name in ("credit", "cancelled", "done", "received"):
    continue

# AFTER:
if txn.status_id != ref_cache.status_id(StatusEnum.PROJECTED):
    continue
```

**`recurrence_engine.py` -- Remove `IMMUTABLE_STATUSES`, use boolean:**

```python
# BEFORE (line 40):
IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})

# AFTER: DELETE.

# BEFORE (line 90):
projected_status = db.session.query(Status).filter_by(name="projected").one()

# AFTER:
projected_status_id = ref_cache.status_id(StatusEnum.PROJECTED)

# BEFORE (lines 108-111):
status_name = existing_txn.status.name if existing_txn.status else "projected"
if status_name in IMMUTABLE_STATUSES:
    should_skip = True

# AFTER:
if existing_txn.status and existing_txn.status.is_immutable:
    should_skip = True
```

**`credit_workflow.py` -- Replace all name checks:**

```python
# BEFORE (line 60):
if txn.status and txn.status.name == "credit":

# AFTER:
if txn.status_id == ref_cache.status_id(StatusEnum.CREDIT):

# BEFORE (line 70):
if txn.status.name != "projected":

# AFTER:
if txn.status_id != ref_cache.status_id(StatusEnum.PROJECTED):

# BEFORE (lines 77-78):
credit_status = db.session.query(Status).filter_by(name="credit").one()
projected_status = db.session.query(Status).filter_by(name="projected").one()

# AFTER:
credit_status_id = ref_cache.status_id(StatusEnum.CREDIT)
projected_status_id = ref_cache.status_id(StatusEnum.PROJECTED)
```

**Step 8: Replace all status name comparisons in routes.**

Pattern is identical across routes -- replace `filter_by(name="...").one()` with `ref_cache.status_id()`:

```python
# BEFORE (transactions.py line 204):
status = db.session.query(Status).filter_by(name="received").one()

# AFTER:
status_id = ref_cache.status_id(StatusEnum.RECEIVED)
```

This eliminates the database query entirely. The cached ID is an in-memory dict lookup.

**Step 9: Replace model property comparisons.**

```python
# BEFORE (transaction.py lines 116-120):
if self.status and self.status.name in ("credit", "cancelled"):
    return Decimal("0")
if self.status and self.status.name in ("done", "received"):
    return self.actual_amount if self.actual_amount is not None else self.estimated_amount

# AFTER:
if self.status and self.status.excludes_from_balance:
    return Decimal("0")
if self.status and self.status.is_settled:
    return self.actual_amount if self.actual_amount is not None else self.estimated_amount

# BEFORE (transaction.py lines 125, 130):
return self.transaction_type and self.transaction_type.name == "income"
return self.transaction_type and self.transaction_type.name == "expense"

# AFTER:
from app import ref_cache
from app.enums import TxnTypeEnum
return self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.INCOME)
return self.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
```

**Step 10: Replace template status comparisons.**

Templates use Jinja globals registered in Step 6:

```html
<!-- BEFORE (grid.html line 140): -->
{% if txn.category_id == category.id and txn.is_income and not txn.is_deleted and txn.status.name != 'cancelled' %}

<!-- AFTER: -->
{% if txn.category_id == category.id and txn.is_income and not txn.is_deleted and txn.status_id != STATUS_CANCELLED %}

<!-- BEFORE (grid.html line 168): -->
{% if txn.is_income and not txn.is_deleted and txn.status.name not in ('credit', 'cancelled', 'done', 'received') %}

<!-- AFTER: -->
{% if txn.is_income and not txn.is_deleted and txn.status_id == STATUS_PROJECTED %}

<!-- BEFORE (_transaction_cell.html line 34): -->
{% if t.status.name in ('done', 'received') %}

<!-- AFTER: -->
{% if t.status.is_settled %}

<!-- BEFORE (_transaction_cell.html line 35): -->
<span class="badge-done ms-1" title="{{ t.status.name|capitalize }}" aria-label="{{ t.status.name|capitalize }}">✓</span>

<!-- AFTER (name already capitalized, no filter needed): -->
<span class="badge-done ms-1" title="{{ t.status.name }}" aria-label="{{ t.status.name }}">✓</span>

<!-- BEFORE (_transaction_cell.html line 36): -->
{% elif t.status.name == 'credit' %}

<!-- AFTER: -->
{% elif t.status_id == STATUS_CREDIT %}

<!-- BEFORE (_transaction_full_edit.html line 74): -->
{% if txn.is_expense and txn.status.name != 'done' %}

<!-- AFTER: -->
{% if txn.is_expense and txn.status_id != STATUS_DONE %}

<!-- BEFORE (_transaction_full_edit.html line 80): -->
<i class="bi bi-check-circle"></i> Done

<!-- AFTER: -->
<i class="bi bi-check-circle"></i> Paid

<!-- BEFORE (_transaction_full_edit.html line 84): -->
{% if not txn.transfer_id and txn.is_expense and txn.status.name == 'projected' %}

<!-- AFTER: -->
{% if not txn.transfer_id and txn.is_expense and txn.status_id == STATUS_PROJECTED %}

<!-- BEFORE (_transaction_full_create.html line 54): -->
<option value="{{ s.id }}" {{ 'selected' if s.name == 'projected' else '' }}>

<!-- AFTER: -->
<option value="{{ s.id }}" {{ 'selected' if s.id == STATUS_PROJECTED else '' }}>
```

The same pattern applies to `transfers/_transfer_cell.html` and `transfers/_transfer_full_edit.html`.

**Step 11: Update tests.**

Tests that use `filter_by(name="done")` to obtain status objects must change to `filter_by(name="Paid")`. Better: use `ref_cache.status_id(StatusEnum.DONE)` directly for setting `status_id`. Tests that assert "Done" in response HTML must check for "Paid".

**E. Test cases.**

**C-4.4a-1:** `test_ref_cache_loads_all_statuses`
- Setup: Standard test database with seeded ref tables.
- Action: Call `ref_cache.init(db.session)`. Then call `ref_cache.status_id()` for each `StatusEnum` member.
- Expected: All 6 members return integer IDs. No RuntimeError raised.
- New test.

**C-4.4a-2:** `test_ref_cache_fails_on_missing_status`
- Setup: Delete one status row from the database.
- Action: Call `ref_cache.init(db.session)`.
- Expected: RuntimeError raised with descriptive message naming the missing status.
- New test.

**C-4.4a-3:** `test_effective_amount_uses_boolean_columns`
- Setup: Transaction with a status where `excludes_from_balance=True` (Credit or Cancelled).
- Action: Access `txn.effective_amount`.
- Expected: Returns `Decimal("0")` because `status.excludes_from_balance` is True. Verify this works without loading the status name.
- New test.

**C-4.4a-4:** `test_balance_calculator_with_boolean_columns`
- Setup: Existing balance calculator test data (settled, projected, credit, cancelled transactions).
- Action: Run `calculate_balances()`.
- Expected: Identical results to before the refactor. This is a regression test ensuring the boolean-based logic produces the same balances as the name-based logic.
- Modification of existing tests in `test_balance_calculator.py`.

**C-4.4a-5:** `test_grid_renders_with_enum_ids`
- Setup: `seed_user`, `auth_client`, transactions with various statuses (projected, done/paid, credit, cancelled).
- Action: GET `/`.
- Expected: Grid renders correctly. Cancelled transactions hidden from rows (line 140/220). Done/received show checkmark badges (`badge-done`). Credit shows CC badge. Subtotal rows (line 168/248) sum only projected. Net Cash Flow row (line 264) sums only projected.
- Modification of existing grid tests.

**C-4.4a-6:** `test_expense_button_says_paid`
- Setup: `seed_user`, `auth_client`, projected expense transaction.
- Action: GET `/transactions/<id>/full-edit`.
- Expected: Response HTML contains button text "Paid" (not "Done"). The `btn-success` button has text "Paid".
- Modification of existing test.

**C-4.4a-7:** `test_status_display_name_is_paid`
- Setup: Database with migration applied.
- Action: Query Status row that was previously "done".
- Expected: `name == "Paid"`. The `is_settled == True`, `is_immutable == True`, `excludes_from_balance == False`.
- New test.

**C-4.4a-8:** `test_settled_status_booleans_correct`
- Setup: Database with migration applied.
- Action: Query the Settled status row.
- Expected: `is_settled == True`, `is_immutable == True`, `excludes_from_balance == False`.
- New test. Guards against the latent bug identified in the boolean audit -- a settled transaction must be treated as settled by the balance calculator.

**C-4.4a-9:** `test_cell_badge_tooltip_says_paid`
- Setup: `seed_user`, `auth_client`, expense transaction marked as done (status_id = DONE).
- Action: GET `/`.
- Expected: Cell badge `title` attribute contains "Paid" (not "Done"). The `aria-label` also contains "Paid".
- New test.

**C-4.4a-10:** `test_transfer_full_edit_button_says_paid`
- Setup: `seed_user`, `auth_client`, shadow transaction from a transfer, projected status.
- Action: GET `/transactions/<shadow_id>/full-edit` (which returns the transfer form).
- Expected: Transfer full edit form contains "Paid" button text (not "Done"). The `transfers/_transfer_full_edit.html` template is rendered with source_txn_id.
- Modification of existing test.

**C-4.4a-11:** `test_mark_done_uses_cached_id`
- Setup: `seed_user`, `auth_client`, projected expense.
- Action: POST `/transactions/<id>/mark-done`.
- Expected: Transaction `status_id` equals the cached ID for `StatusEnum.DONE`. No `filter_by(name=...)` query needed. `HX-Trigger` contains `gridRefresh`.
- Modification of existing test.

**C-4.4a-12:** `test_recurrence_engine_skips_immutable_via_boolean`
- Setup: Transaction template with a recurrence rule. One generated transaction marked as done (immutable).
- Action: Call `regenerate_for_template()`.
- Expected: The done transaction is NOT deleted or modified. New transactions are generated for other periods. The engine checks `status.is_immutable` (boolean), not a frozenset of name strings.
- Modification of existing test.

**F. Manual verification steps.**

1. Start the app. Confirm no cache loader errors in the console.
2. Open the grid. Verify all transactions display with correct status badges.
3. Click a projected expense. Verify the full edit popover shows "Paid" button (not "Done").
4. Click the status dropdown. Verify it shows "Paid" option (capitalized).
5. Mark the expense as paid. Verify the cell badge tooltip says "Paid".
6. Mark a different expense as credit. Verify the payback is created in the next period.
7. Open the carry forward button on a past period. Verify only projected items are carried.
8. Check the balance row. Verify the Projected End Balance is correct.
9. Click a shadow transaction cell (from a transfer). Verify the transfer form also shows "Paid" button.
10. Income transactions should still show "Received" button -- verify unchanged.
11. Visit the salary page, savings dashboard, and charts page to verify no rendering errors from the status name change.

**G. Downstream effects.**

- `app/enums.py` and `app/ref_cache.py` become new dependencies for services, routes, models, and templates.
- The Jinja globals registration makes cached IDs available in all templates without explicit route passing.
- Seed scripts change format: from list-of-names to list-of-dicts with boolean values.
- The `name` column values change ("done" -> "Paid", etc.) -- any external tool or script that queries `ref.statuses` by old names will break.
- Tests that use `filter_by(name="done")` must update to `filter_by(name="Paid")` or use enum cache.
- The `SETTLED_STATUSES` and `IMMUTABLE_STATUSES` frozensets are removed from 3 service files. Any code that imported them will fail at import time (caught immediately, not a silent failure).

**H. Rollback notes.** Requires running the downgrade migration (reverts boolean columns and name renames). The `app/enums.py` and `app/ref_cache.py` files must also be removed, and all code changes reverted. This is the highest-risk commit in the section. The regression test suite (commit #0) is the safety net. If the migration runs but the code is not deployed, the old name-based lookups will fail because "done" is now "Paid" in the database. The migration and code must deploy atomically.

---

### Commit #2: Task 4.4c -- Reference Table Enum Cache for All Ref Tables

**A. Commit message:** `refactor(ref): replace all ref table name lookups with enum-cached IDs`

**B. Problem statement.** Beyond statuses, AccountType, TransactionType, and RecurrencePattern names are used in logic comparisons throughout the codebase. The Reference Table String Audit (above) documents 40+ occurrences. The `category` column on `ref.account_types` is a bare `VARCHAR(20)` string ("asset", "liability") rather than a foreign key, which means category grouping also uses string comparisons. This commit extends the enum/cache pattern to all remaining reference tables and introduces proper FK-based categorization.

**C. Files modified.**

- `app/enums.py` -- Modified. Add `AcctTypeEnum`, `AcctCategoryEnum`, `RecurrencePatternEnum`.
- `app/ref_cache.py` -- Modified. Add loaders and accessors for account types, categories, recurrence patterns.
- `app/models/ref.py` -- Modified. Add `AccountTypeCategory` model. Add `category_id` FK, `has_parameters`, `has_amortization` boolean columns to `AccountType`. Remove `category` string column.
- `migrations/versions/<new>.py` -- New. Create `ref.account_type_categories` table (4 rows). Add `category_id` FK to `ref.account_types`. Migrate data from string `category` to FK `category_id`. Add boolean columns. Drop old `category` column. Capitalize account type and recurrence pattern display names.
- `app/__init__.py` -- Modified. Seed account type categories with dict-based seeds. Register new Jinja globals. Update `ref_cache.init()` call.
- `scripts/seed_ref_tables.py` -- Modified. Seed categories table. Update account type seeds with `category_id`, `has_parameters`, `has_amortization`.
- `tests/conftest.py` -- Modified. Same seed updates.
- `app/routes/accounts.py` -- Modified. Replace `account_type.name == "hysa"` with `account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA)`. Affected lines: 133, 143, 145, 548, 649.
- `app/routes/auto_loan.py` -- Modified. Replace `account_type.name != "auto_loan"` with ID check. Affected lines: 37, 111.
- `app/routes/mortgage.py` -- Modified. Same pattern. Affected lines: 47, 172.
- `app/routes/savings.py` -- Modified. Replace `filter_by(name="hysa")`, `filter_by(name="mortgage")`, etc. with enum cache. Affected lines: 85, 100, 112-118, 412.
- `app/routes/transactions.py` -- Modified. Replace `txn_type_name` string parameter with `transaction_type_id` integer. Affected lines: 335, 351, 381, 394, 422, 427.
- `app/routes/transfers.py` -- Modified. Replace `filter_by(name=pattern_name)` with `filter_by(id=pattern_id)`. Affected line: 247.
- `app/routes/templates.py` -- Modified. Same pattern. Affected line: 233.
- `app/routes/investment.py` -- Modified. Replace `filter_by(name="income")` with enum cache. Affected lines: 142, 361.
- `app/routes/retirement.py` -- Modified. Same. Affected line: 174.
- `app/routes/salary.py` -- Modified. Same. Affected line: 158.
- `app/services/transfer_service.py` -- Modified. Replace `filter_by(name="expense")` and `filter_by(name="income")` with `ref_cache.txn_type_id()`. Affected lines: 295, 298, 388, 391, 719, 722.
- `app/services/credit_workflow.py` -- Modified. Replace `filter_by(name="expense")` with enum cache. Affected line: 79.
- `app/services/account_resolver.py` -- Modified. Replace `filter_by(name="checking")`. Affected line: 44.
- `app/services/auth_service.py` -- Modified. Replace `filter_by(name="checking")`. Affected line: 391.
- `app/services/chart_data_service.py` -- Modified. Replace `account.account_type.name` with `account.account_type_id` or `account.account_type.category_id`. Affected lines: 218, 253, 486.
- `app/templates/grid/grid.html` -- Modified. Replace `{% set txn_type_name = "income" %}` with `{% set txn_type_id = TXN_TYPE_INCOME %}` (line 153). Same for expense (line 233). Replace `tt.name == 'expense'` with `tt.id == TXN_TYPE_EXPENSE` in the modal dropdown (line 321).
- `app/templates/grid/_transaction_empty_cell.html` -- Modified. Change `txn_type_name=txn_type_name` to `transaction_type_id=txn_type_id` in the `hx-get` URL (line 11). Update `aria-label` (line 17).
- `app/templates/grid/_transaction_quick_create.html` -- Modified. Change `data-txn-type-name` to `data-txn-type-id` (line 37).
- `app/templates/savings/dashboard.html` -- Modified. Replace all 12 occurrences of `ad.account.account_type.name == 'hysa'` (etc.) with ID comparisons using Jinja globals (lines 50-103).
- `app/static/js/grid_edit.js` -- Modified. Change `txn_type_name` to `transaction_type_id` in URL construction (lines 125, 244).
- `app/templates/templates/list.html` -- Modified. Replace `t.transaction_type.name == 'income'` with ID check (line 48).
- `app/templates/templates/form.html` -- Modified. Replace `tt.name == 'expense'` with ID check (line 55).
- Tests (multiple files) -- Modified. Use enum cache for all ref table lookups.

**D. Implementation approach.**

**Step 1: Add enums for remaining ref tables to `app/enums.py`.**

```python
class AcctCategoryEnum(enum.Enum):
    """Account type category values.  Values match ref.account_type_categories.name."""
    ASSET = "Asset"
    LIABILITY = "Liability"
    RETIREMENT = "Retirement"
    INVESTMENT = "Investment"


class AcctTypeEnum(enum.Enum):
    """Account type values.  Values match ref.account_types.name."""
    CHECKING = "Checking"
    SAVINGS = "Savings"
    HYSA = "High-Yield Savings"
    MONEY_MARKET = "Money Market"
    CD = "CD"
    HSA = "HSA"
    CREDIT_CARD = "Credit Card"
    MORTGAGE = "Mortgage"
    AUTO_LOAN = "Auto Loan"
    STUDENT_LOAN = "Student Loan"
    PERSONAL_LOAN = "Personal Loan"
    HELOC = "HELOC"
    K401 = "401(k)"
    ROTH_401K = "Roth 401(k)"
    TRADITIONAL_IRA = "Traditional IRA"
    ROTH_IRA = "Roth IRA"
    BROKERAGE = "Brokerage"
    PLAN_529 = "529 Plan"


class RecurrencePatternEnum(enum.Enum):
    """Recurrence pattern values.  Values match ref.recurrence_patterns.name."""
    EVERY_PERIOD = "Every Period"
    EVERY_N_PERIODS = "Every N Periods"
    MONTHLY = "Monthly"
    MONTHLY_FIRST = "Monthly First"
    QUARTERLY = "Quarterly"
    SEMI_ANNUAL = "Semi-Annual"
    ANNUAL = "Annual"
    ONCE = "Once"
```

**Step 2: Extend `app/ref_cache.py` with loaders for all ref tables.**

Add `acct_type_id()`, `acct_category_id()`, `recurrence_pattern_id()` accessor functions. The `init()` function loads all tables in one pass.

**Step 3: Create `ref.account_type_categories` table.**

Alembic migration:

```python
def upgrade():
    # Create the categories table.
    op.create_table(
        'account_type_categories',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(20), unique=True, nullable=False),
        schema='ref',
    )

    # Seed the 4 categories.
    op.execute("INSERT INTO ref.account_type_categories (name) VALUES "
               "('Asset'), ('Liability'), ('Retirement'), ('Investment')")

    # Add category_id FK to account_types.
    op.add_column('account_types', sa.Column(
        'category_id', sa.Integer(),
        sa.ForeignKey('ref.account_type_categories.id'),
    ), schema='ref')

    # Migrate data from string category to FK.
    op.execute("""
        UPDATE ref.account_types at
        SET category_id = c.id
        FROM ref.account_type_categories c
        WHERE LOWER(at.category) = LOWER(c.name)
    """)

    # Add boolean columns.
    op.add_column('account_types', sa.Column(
        'has_parameters', sa.Boolean(), nullable=False, server_default='false',
    ), schema='ref')
    op.add_column('account_types', sa.Column(
        'has_amortization', sa.Boolean(), nullable=False, server_default='false',
    ), schema='ref')

    # Set boolean values.
    op.execute("UPDATE ref.account_types SET has_parameters = TRUE "
               "WHERE name IN ('hysa', 'mortgage', 'auto_loan', 'student_loan', "
               "'personal_loan', '401k', 'roth_401k', 'traditional_ira', "
               "'roth_ira', 'brokerage')")
    op.execute("UPDATE ref.account_types SET has_amortization = TRUE "
               "WHERE name IN ('mortgage', 'auto_loan', 'student_loan', "
               "'personal_loan', 'heloc')")

    # Capitalize display names.
    # (Account types and recurrence patterns get user-friendly names)
    # ... UPDATE statements for each name ...

    # Drop old category string column.
    op.drop_column('account_types', 'category', schema='ref')
```

**Step 4: Refactor `txn_type_name` to `transaction_type_id`.**

This is the most complex change because it spans template, JS, and routes.

In `grid/grid.html` (line 153), change:
```html
{% set txn_type_name = "income" %}
```
to:
```html
{% set txn_type_id = TXN_TYPE_INCOME %}
```

In `grid/_transaction_empty_cell.html` (line 11), change the URL parameter:
```html
hx-get="{{ url_for('transactions.get_quick_create', category_id=category.id, period_id=period.id, transaction_type_id=txn_type_id, account_id=account.id) }}"
```

In `grid_edit.js` (line 125), change:
```javascript
'&txn_type_name=' + encodeURIComponent(txnTypeName)
```
to:
```javascript
'&transaction_type_id=' + encodeURIComponent(txnTypeId)
```

In `transactions.py` route handlers (lines 335, 381, 427), change:
```python
# BEFORE:
txn_type_name = request.args.get("txn_type_name", "expense")
txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()

# AFTER:
txn_type_id = request.args.get("transaction_type_id", type=int,
                                default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE))
```

**Step 5: Replace all AccountType name comparisons.**

```python
# BEFORE (accounts.py line 133):
if account_type and account_type.name == "hysa":

# AFTER:
if account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA):

# BEFORE (savings/dashboard.html line 50):
{% if ad.account.account_type and ad.account.account_type.name == 'hysa' %}

# AFTER:
{% if ad.account.account_type_id == ACCT_TYPE_HYSA %}
```

**Step 6: Register all new cached IDs as Jinja globals.**

```python
# Account type IDs
app.jinja_env.globals['ACCT_TYPE_CHECKING'] = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
app.jinja_env.globals['ACCT_TYPE_HYSA'] = ref_cache.acct_type_id(AcctTypeEnum.HYSA)
app.jinja_env.globals['ACCT_TYPE_MORTGAGE'] = ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE)
# ... etc for all types used in templates

# Transaction type IDs
app.jinja_env.globals['TXN_TYPE_INCOME'] = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
app.jinja_env.globals['TXN_TYPE_EXPENSE'] = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
```

**HTMX regression analysis:**

- **GI-9 (Empty cell click):** The `hx-get` URL changes from `txn_type_name=income` to `transaction_type_id=<int>`. The route handler must accept the new parameter name. If the route is updated but the template is not (or vice versa), the quick create form will fail. These must change atomically.
- **GI-15 (Inline create submit):** The form's hidden fields already use `transaction_type_id` (an integer FK). No change needed on the submit path -- only the empty cell GET request changes.
- **GI-20/21 (Escape/F2):** The JS in `grid_edit.js` constructs URLs with `txn_type_name`. Must change to `transaction_type_id`. Both the escape-revert path (line 244) and the F2-expand path (line 125) need updating.
- All other GI interactions: NOT AFFECTED. They use transaction IDs, not type names.

**E. Test cases.**

**C-4.4c-1:** `test_ref_cache_loads_all_account_types`
- Setup: Standard test database with seeded ref tables.
- Action: Call `ref_cache.acct_type_id()` for each `AcctTypeEnum` member.
- Expected: All 18 members return integer IDs. No RuntimeError.
- New test.

**C-4.4c-2:** `test_acct_category_fk_on_account_types`
- Setup: Standard test database.
- Action: Query AccountType for mortgage. Check `category_id`.
- Expected: `category_id` matches `ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)`.
- New test.

**C-4.4c-3:** `test_empty_cell_uses_transaction_type_id`
- Setup: `seed_user`, `auth_client`, seeded grid data.
- Action: GET `/`. Inspect empty cell `hx-get` URLs in the response HTML.
- Expected: URL contains `transaction_type_id=` with an integer value, NOT `txn_type_name=`.
- New test.

**C-4.4c-4:** `test_quick_create_with_type_id`
- Setup: `seed_user`, `auth_client`, category, period.
- Action: GET `/transactions/new/quick?category_id=X&period_id=Y&transaction_type_id=<expense_id>`.
- Expected: 200 response. Quick create form rendered. Form contains a hidden `transaction_type_id` field.
- Modification of existing test.

**C-4.4c-5:** `test_account_creation_redirect_uses_type_id`
- Setup: `seed_user`, `auth_client`.
- Action: POST `/accounts` with HYSA account type.
- Expected: 302 redirect to HYSA detail page. The route logic uses `account.account_type_id` comparison, not string name.
- Modification of existing test.

**C-4.4c-6:** `test_has_parameters_boolean_on_account_types`
- Setup: Standard test database.
- Action: Query AccountType for HYSA, checking, mortgage.
- Expected: HYSA `has_parameters == True`. Checking `has_parameters == False`. Mortgage `has_parameters == True` and `has_amortization == True`.
- New test.

**C-4.4c-7:** `test_savings_dashboard_uses_type_ids`
- Setup: `seed_user`, `auth_client`, HYSA and mortgage accounts.
- Action: GET `/savings`.
- Expected: 200. Dashboard renders correctly with type-specific icons and links. Verify by checking for the HYSA detail link and mortgage dashboard link in the response HTML.
- Modification of existing test.

**F. Manual verification steps.**

1. Open the grid. Click an empty cell. Verify the quick create form appears.
2. Submit a new transaction via inline create. Verify it saves correctly with the right type.
3. Press Escape on a quick edit. Verify the cell reverts to the display state.
4. Press F2 on a quick create. Verify the full create popover appears.
5. Open the Add Transaction modal. Verify the type dropdown works and defaults to "Expense".
6. Create a new HYSA account. Verify the post-creation redirect works.
7. Create a new mortgage account. Verify the redirect to the mortgage dashboard.
8. Create a checking account. Verify redirect to the accounts list (no parameters).
9. Visit the savings dashboard. Verify account type icons and detail links are correct for each type (HYSA, mortgage, auto loan, investment, retirement).
10. Visit the transfer template form. Verify the recurrence pattern dropdown works.

**G. Downstream effects.**

- The `txn_type_name` -> `transaction_type_id` change is the most cross-cutting: it affects the grid template, two JS files, the empty cell template, the quick create template, and 3 route handlers. All must change atomically.
- The `category` string column removal on `ref.account_types` breaks any code that reads `account_type.category`. Must be replaced with `account_type.category_id` or a join to `AccountTypeCategory`.
- The `has_parameters` boolean enables commit #7 (Account Setup UX) to use `account_type.has_parameters` instead of a hardcoded set of type names.

**H. Rollback notes.** Depends on commit #1 (the enum and cache modules). The `ref.account_type_categories` table and `category_id` FK migration must be downgraded. The `category` string column must be restored. If only this commit is reverted but commit #1 remains, the enum/cache for statuses still works -- only the account type and recurrence pattern enums are lost.

### Commit #3: Task 4.13 -- Emergency Fund Coverage Calculation Fix

**A. Commit message:** `fix(savings): include transfer expenses in emergency fund coverage`

**B. Problem statement.** The emergency fund coverage calculation in `app/routes/savings.py:376-428` averages actual expenses from the last 6 pay periods. It counts transactions where `txn.is_expense` is True and status is settled (currently `txn.status.name in ("done", "received")`, to be `txn.status.is_settled` after commit #1). After the transfer rework, shadow expense transactions from transfers (mortgage payments, savings transfers) have `is_expense=True` and ARE included in this query when their status is settled. However, the calculation only considers historical actuals -- recently created recurring transfers with sparse settlement history are undercounted. A user who just set up a $1500/month mortgage transfer won't see it reflected in the emergency fund coverage until several periods of "done" history accumulate.

**C. Files modified.**

- `app/routes/savings.py` -- Modified. Enhance the emergency fund expense calculation to include recurring transfer template amounts as a committed baseline.
- `app/services/savings_goal_service.py` -- Modified. Add a helper function for computing committed monthly expenses from active templates.
- `tests/test_routes/test_savings.py` -- Modified. Add tests for the corrected calculation.

**D. Implementation approach.**

The fix has two parts:

1. **Keep the historical actuals approach** for settled transactions (it correctly captures variable expenses like groceries and fuel that fluctuate).
2. **Add a floor based on recurring templates** to ensure committed outflows are always counted even when settlement history is sparse.

In `app/routes/savings.py`, after the existing `avg_monthly_expenses` calculation (line 407), also compute a `committed_monthly` figure from active recurring expense templates and transfer templates that debit checking. Use the higher of the two figures as the baseline for emergency fund coverage.

```python
# After the existing avg_monthly_expenses calculation (line 407):

# Also compute committed monthly from active recurring templates.
from app.models.budget import TransactionTemplate, TransferTemplate

# Get the checking account ID for this user.
checking_account = account  # The grid account, typically checking.

# Active expense templates for checking account.
expense_templates = (
    db.session.query(TransactionTemplate)
    .filter(
        TransactionTemplate.user_id == user_id,
        TransactionTemplate.is_active.is_(True),
        TransactionTemplate.account_id == checking_account.id,
        TransactionTemplate.transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )
    .all()
)

# Active transfer templates FROM checking.
transfer_templates = (
    db.session.query(TransferTemplate)
    .filter(
        TransferTemplate.user_id == user_id,
        TransferTemplate.is_active.is_(True),
        TransferTemplate.from_account_id == checking_account.id,
    )
    .all()
)

# Compute committed monthly using the helper.
committed_monthly = savings_goal_service.compute_committed_monthly(
    expense_templates, transfer_templates,
)

# Use the higher of historical actual average or committed baseline.
avg_monthly_expenses = max(avg_monthly_expenses, committed_monthly)
```

The `compute_committed_monthly()` helper in `savings_goal_service.py` accounts for each template's recurrence pattern:
- `every_period` templates: `amount * 26 / 12` (biweekly to monthly)
- `monthly` / `monthly_first` templates: `amount * 1` (already monthly)
- `every_n_periods` templates: `amount * (26 / n) / 12`
- `quarterly` templates: `amount / 3`
- `semi_annual` templates: `amount / 6`
- `annual` templates: `amount / 12`
- `once` templates: excluded (not recurring)

**E. Test cases.**

**C-4.13-1:** `test_emergency_fund_includes_transfer_templates`
- Setup: `seed_user`, `auth_client`, checking account, active transfer template from checking to mortgage ($1500 biweekly, `every_period` recurrence).
- Action: GET savings dashboard.
- Expected: Emergency fund `avg_monthly_expenses` includes the transfer amount (approximately $1500 * 26/12 = $3250/month).
- New test.

**C-4.13-2:** `test_emergency_fund_uses_higher_of_actual_or_committed`
- Setup: `seed_user`, `auth_client`, checking account. Historical actuals (6 periods of settled expense transactions) total $3000/month average. Committed templates total $3500/month.
- Action: GET savings dashboard.
- Expected: Emergency fund uses $3500 (the higher figure).
- New test.

**C-4.13-3:** `test_emergency_fund_with_no_history_uses_committed`
- Setup: `seed_user`, `auth_client`, checking account. No historical settled transactions (user just started). Active expense templates and transfer templates totaling $2000/month.
- Action: GET savings dashboard.
- Expected: `avg_monthly_expenses` is $2000 (committed baseline, not $0).
- New test.

**C-4.13-4:** `test_emergency_fund_no_templates_no_history`
- Setup: `seed_user`, `auth_client`, checking account. No templates, no settled transactions.
- Action: GET savings dashboard.
- Expected: `avg_monthly_expenses` is $0. Coverage shows appropriate fallback (infinity or N/A).
- New test (edge case).

**C-4.13-5:** `test_emergency_fund_monthly_template_contribution`
- Setup: `seed_user`, `auth_client`, checking account. One `monthly` expense template with default_amount=$500.
- Action: Compute committed monthly.
- Expected: Monthly contribution is $500 (not $500 * 26/12).
- New test. Verifies that the recurrence pattern is correctly accounted for in the monthly conversion.

**F. Manual verification steps.**

1. Open the savings dashboard. Note the emergency fund coverage (months covered).
2. Create a recurring transfer from checking to a savings account ($500 biweekly).
3. Refresh the dashboard. Verify the coverage decreases to reflect the new committed outflow.
4. Delete the transfer template. Verify coverage returns to the previous value.
5. Create a monthly expense template ($1000/month). Verify coverage decreases by approximately 1000/total.

**G. Downstream effects.**

- The `savings_goal_service.calculate_savings_metrics()` function is unchanged -- it receives the corrected `avg_monthly_expenses` value.
- No grid changes.
- The emergency fund display template (`savings/dashboard.html` lines 158-195) is unchanged.

**H. Rollback notes.** Pure route/service logic change. No migration. Revertable independently. Does not depend on commit #1 or #2 structurally, but the code will use enum cache (from commit #1) for the `TxnTypeEnum.EXPENSE` lookup.

---

### Commit #4: Task 4.5 -- Deduction Frequency Display

**A. Commit message:** `feat(salary): improve deduction frequency display with descriptive labels`

**B. Problem statement.** The deductions table in `app/templates/salary/_deductions_section.html` shows raw integer values like "26", "24", "12" in the "Per Year" column (line 42: `{{ d.deductions_per_year }}`). The column header is "Per Year" (line 19). These numbers are ambiguous without context -- does "26" next to "$500" mean "$500 is deducted 26 times" or "$500 is the annual amount divided by 26"? The add/edit form dropdown (lines 150-154) includes descriptive labels, but the display table strips them.

**C. Files modified.**

- `app/templates/salary/_deductions_section.html` -- Modified. Update the Per Year column header to "Frequency" and add descriptive labels to the cell content.
- `tests/test_routes/test_salary.py` -- Modified. Add test asserting descriptive labels appear.

**D. Implementation approach.**

In `salary/_deductions_section.html`, change the column header from "Per Year" (line 19) to "Frequency". Change the cell (line 42) from `{{ d.deductions_per_year }}` to:

```html
<td>
  {% if d.deductions_per_year == 26 %}
    26x/yr <small class="text-muted">(every paycheck)</small>
  {% elif d.deductions_per_year == 24 %}
    24x/yr <small class="text-muted">(skip 3rd)</small>
  {% elif d.deductions_per_year == 12 %}
    12x/yr <small class="text-muted">(monthly)</small>
  {% else %}
    {{ d.deductions_per_year }}x/yr
  {% endif %}
</td>
```

**HTMX regression analysis:** This task does not modify the grid page. The deductions table is on the salary profile page and uses HTMX for add/edit/delete within the `#deductions-section` target (line 94: `hx-target="#deductions-section"`). The display change is inside the existing table structure and does not affect any HTMX attributes.

**E. Test cases.**

**C-4.5-1:** `test_deduction_frequency_shows_descriptive_label`
- Setup: `seed_user`, `auth_client`, salary profile with deductions at 26x, 24x, and 12x per year.
- Action: GET salary profile page.
- Expected: Response HTML contains "26x/yr" with "(every paycheck)", "24x/yr" with "(skip 3rd)", "12x/yr" with "(monthly)".
- New test.

**C-4.5-2:** `test_deduction_frequency_fallback_for_unusual_value`
- Setup: `seed_user`, `auth_client`, salary profile with a deduction at 52x per year.
- Action: GET salary profile page.
- Expected: Response HTML contains "52x/yr" without a parenthetical label.
- New test (edge case).

**F. Manual verification steps.** Open the salary page. Verify deductions show "26x/yr (every paycheck)" etc. Verify column header says "Frequency".

**G. Downstream effects.** None. Single template change on a non-grid page.

**H. Rollback notes.** Template-only change. Revertable independently.

---

### Commit #5: Task 4.6 -- Tax Config Page Reorganization

**A. Commit message:** `feat(salary): reorganize tax config page -- adjustable settings first`

**B. Problem statement.** The tax configuration page (`app/templates/salary/tax_config.html`) renders three cards in this order: (1) Federal Tax Brackets (line 23-64), (2) FICA Configuration (line 66-142), (3) State Tax Configuration (line 144-211). The Federal Tax Brackets card is the longest section and is changed the least often. The State Tax Configuration (user-adjustable) is at the bottom, requiring scrolling past bracket tables. There is no way to collapse sections or hide previous tax year data.

**C. Files modified.**

- `app/templates/salary/tax_config.html` -- Modified. Reorder sections: State Tax first, FICA second, Federal Brackets third. Add Bootstrap 5 collapse to the Federal Brackets card.
- `tests/test_routes/test_salary.py` -- Modified. Add ordering and collapse tests.

**D. Implementation approach.**

1. **Reorder sections.** Move the State Tax Configuration card (currently lines 144-211) to position 1. Move FICA Configuration (currently lines 66-142) to position 2. Move Federal Tax Brackets (currently lines 23-64) to position 3.

2. **Add collapse to Federal Tax Brackets.** Wrap the card body in a Bootstrap 5 collapse component:

```html
<div class="card mb-4">
  <div class="card-header d-flex justify-content-between align-items-center">
    <h6 class="mb-0"><i class="bi bi-building"></i> Federal Tax Brackets</h6>
    <button class="btn btn-sm btn-outline-secondary" type="button"
            data-bs-toggle="collapse" data-bs-target="#federal-brackets-body"
            aria-expanded="false" aria-controls="federal-brackets-body">
      <i class="bi bi-chevron-down"></i> Show
    </button>
  </div>
  <div class="collapse" id="federal-brackets-body">
    <div class="card-body">
      {# existing bracket tables #}
    </div>
  </div>
</div>
```

3. **Group bracket tables by tax year.** If multiple years exist (e.g., 2025 and 2026), each year gets its own collapse section. Most recent year expanded by default (use `class="collapse show"`), previous years collapsed (use `class="collapse"`).

**HTMX regression analysis:** This task does not modify the grid page. The tax config page uses standard form submissions (no HTMX). The FICA and State Tax forms post to their respective route handlers and redirect back. No HTMX interactions are affected.

**E. Test cases.**

**C-4.6-1:** `test_tax_config_state_tax_appears_first`
- Setup: `seed_user`, `auth_client`, seeded tax config data.
- Action: GET `/salary/tax-config`.
- Expected: In the response HTML, the string "State Tax Configuration" appears before "FICA Configuration", which appears before "Federal Tax Brackets".
- New test.

**C-4.6-2:** `test_federal_brackets_collapsed_by_default`
- Setup: Same.
- Action: GET `/salary/tax-config`.
- Expected: The Federal Tax Brackets card body has `class="collapse"` (not `class="collapse show"`), indicating it is collapsed by default.
- New test.

**C-4.6-3:** `test_federal_brackets_still_accessible`
- Setup: Same.
- Action: GET `/salary/tax-config`.
- Expected: Response contains the `data-bs-toggle="collapse"` button and the `id="federal-brackets-body"` target. The bracket data is present in the HTML (just hidden via CSS collapse).
- New test. Ensures the collapse doesn't accidentally remove the bracket content from the DOM.

**F. Manual verification steps.** Open tax config. Verify State Tax is at the top. Verify FICA is second. Verify Federal brackets are collapsed at the bottom. Click the "Show" button and verify brackets expand.

**G. Downstream effects.** None. Non-grid page. No other templates include this page.

**H. Rollback notes.** Template-only change. Revertable independently.

---

### Commit #6: Task 4.11 -- Salary Profile Button Placement

**A. Commit message:** `feat(salary): move View Breakdown and View Projection buttons higher`

**B. Problem statement.** The View Breakdown and View Projection buttons are in `app/templates/salary/list.html` (lines 72-79) within each profile's action buttons in a table row. On the profile edit page (`salary/form.html`), these buttons appear at the very bottom (lines 206-211), after all the form fields, deductions, and raises sections. The user must scroll past all configuration to find them. Per the agent research, these link to `url_for('salary.breakdown_current', profile_id=p.id)` and `url_for('salary.projection', profile_id=p.id)`.

**C. Files modified.**

- `app/templates/salary/list.html` -- Modified. Add a prominent button group near the top of each profile section.
- `app/templates/salary/form.html` -- Modified. Duplicate the buttons near the top of the page (below the profile name/summary).
- `tests/test_routes/test_salary.py` -- Modified. Assert button placement.

**D. Implementation approach.**

On the salary profiles list page (`list.html`), add a prominent action bar immediately after each profile's summary row. The existing small icon buttons in the table row remain as secondary access:

```html
{# Add below the profile summary, before the deductions/raises sections #}
<div class="d-flex gap-2 mb-3">
    <a href="{{ url_for('salary.breakdown_current', profile_id=p.id) }}"
       class="btn btn-outline-primary btn-sm">
        <i class="bi bi-receipt"></i> View Breakdown
    </a>
    <a href="{{ url_for('salary.projection', profile_id=p.id) }}"
       class="btn btn-outline-primary btn-sm">
        <i class="bi bi-graph-up"></i> View Projection
    </a>
</div>
```

On the profile edit page (`form.html`), duplicate the buttons from lines 206-211 to a position immediately below the profile name field, wrapped in a conditional (only shown when editing an existing profile, not when creating a new one).

**HTMX regression analysis:** These are standard `<a href>` links, not HTMX interactions. No grid or HTMX interactions are affected.

**E. Test cases.**

**C-4.11-1:** `test_salary_list_buttons_visible_early`
- Setup: `seed_user`, `auth_client`, salary profile.
- Action: GET salary list page.
- Expected: Response HTML contains "View Breakdown" and "View Projection" links before the deductions table. Specifically, the URL patterns for `breakdown_current` and `projection` appear before the string "Deductions" or the `#deductions-section` div.
- New test.

**C-4.11-2:** `test_salary_edit_buttons_visible_early`
- Setup: `seed_user`, `auth_client`, salary profile.
- Action: GET salary edit page for the profile.
- Expected: "View Breakdown" and "View Projection" links appear early in the response, not just at the bottom.
- New test.

**F. Manual verification steps.** Open the salary page. Verify buttons are visible without scrolling past deductions/raises. Click each button and verify navigation works.

**G. Downstream effects.** None. Standard link elements.

**H. Rollback notes.** Template-only change. Revertable independently.

---

### Commit #7: Task 4.7/4.8 -- Account Parameter Setup UX

**A. Commit message:** `feat(accounts): redirect to parameter config after creating parameterized accounts`

**B. Problem statement.** After creating accounts that require parameters (HYSA, investment, retirement), the app redirects to the generic accounts list (`app/routes/accounts.py` line 148: `return redirect(url_for("accounts.list_accounts"))`). The user must then find the new account card on the savings dashboard and click a small icon button to configure parameters. Mortgage and auto loan already redirect to their parameter pages (lines 143-146), but HYSA, investment, and retirement do not. Additionally, there is no visual indicator on unconfigured accounts.

**C. Files modified.**

- `app/routes/accounts.py` -- Modified. Extend the post-creation redirect logic (lines 133-148) for all parameterized account types. Currently, HYSA auto-creates `HysaParams` (lines 136-137) but redirects to the list. After the change, it redirects to the HYSA detail page. Investment and retirement accounts get auto-created `InvestmentParams` and redirect to the investment dashboard.
- `app/routes/savings.py` -- Modified. Add a `needs_setup` flag to account data passed to the dashboard template.
- `app/templates/savings/dashboard.html` -- Modified. Add a "Setup Required" badge on account cards where params are at default values.
- `app/templates/accounts/hysa_detail.html` -- Modified. Show a dismissible alert banner when reached via post-creation redirect (`setup=1` query param).
- `app/templates/investment/dashboard.html` -- Modified. Same wizard banner.
- `tests/test_routes/test_accounts.py` -- Modified. Test redirect behavior for all account types.
- `tests/test_routes/test_savings.py` -- Modified. Test badge appearance.

**D. Implementation approach.**

In `accounts.py:create_account()` (line 133), extend the redirect logic using the `has_parameters` boolean added in commit #2:

```python
from app import ref_cache
from app.enums import AcctTypeEnum

# After account creation (replacing lines 133-148):
if account_type.has_parameters:
    if account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA):
        # HysaParams already auto-created at line 136-137.
        return redirect(url_for("accounts.hysa_detail",
                                account_id=account.id, setup=1))
    elif account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE):
        return redirect(url_for("mortgage.dashboard",
                                account_id=account.id, setup=1))
    elif account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN):
        return redirect(url_for("auto_loan.dashboard",
                                account_id=account.id, setup=1))
    else:
        # Investment/retirement accounts: auto-create InvestmentParams.
        from app.models.budget import InvestmentParams
        existing = db.session.query(InvestmentParams).filter_by(
            account_id=account.id).first()
        if not existing:
            db.session.add(InvestmentParams(account_id=account.id))
            db.session.commit()
        return redirect(url_for("investment.dashboard",
                                account_id=account.id, setup=1))

# Non-parameterized types (checking, savings) fall through to:
return redirect(url_for("accounts.list_accounts"))
```

The `setup=1` query parameter tells the target page to show a wizard banner:
```html
{% if request.args.get('setup') == '1' %}
<div class="alert alert-info alert-dismissible fade show" role="alert">
    <i class="bi bi-gear"></i> Account created. Configure the settings below
    to enable accurate projections.
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
</div>
{% endif %}
```

For the "Setup Required" badge, add a `needs_setup` flag in the savings dashboard route. The flag is True when params are at default values (APY=0 for HYSA, `assumed_annual_return` is None or 0 for investment/retirement):

```html
{% if ad.needs_setup %}
  <span class="badge bg-warning text-dark">Setup Required</span>
{% endif %}
```

**HTMX regression analysis:** This task does not modify the grid page. Account creation uses standard form POST with redirect. No grid HTMX interactions are affected.

**E. Test cases.**

**C-4.7-1:** `test_hysa_creation_redirects_to_params`
- Setup: `seed_user`, `auth_client`, HYSA account type seeded.
- Action: POST `/accounts` with HYSA type and name "Test HYSA".
- Expected: 302 redirect to `/accounts/<new_id>/hysa?setup=1`.
- New test.

**C-4.7-2:** `test_investment_creation_redirects_to_params`
- Setup: `seed_user`, `auth_client`, brokerage account type seeded.
- Action: POST `/accounts` with brokerage type.
- Expected: 302 redirect to `/investment/<new_id>?setup=1`. InvestmentParams record exists in DB.
- New test.

**C-4.7-3:** `test_checking_creation_redirects_to_list`
- Setup: `seed_user`, `auth_client`, checking account type seeded.
- Action: POST `/accounts` with checking type.
- Expected: 302 redirect to `/accounts` (the list page, no setup parameter).
- New test.

**C-4.7-4:** `test_setup_badge_shown_for_unconfigured_hysa`
- Setup: `seed_user`, `auth_client`, HYSA account with default params (APY=0).
- Action: GET savings dashboard.
- Expected: Response HTML contains "Setup Required" badge near the HYSA account card.
- New test.

**C-4.7-5:** `test_setup_badge_hidden_for_configured_hysa`
- Setup: `seed_user`, `auth_client`, HYSA account with APY=4.5% (non-default).
- Action: GET savings dashboard.
- Expected: Response HTML does NOT contain "Setup Required" badge for the HYSA account.
- New test.

**C-4.7-6:** `test_wizard_banner_shown_with_setup_param`
- Setup: `seed_user`, `auth_client`, HYSA account.
- Action: GET `/accounts/<id>/hysa?setup=1`.
- Expected: Response contains the "Account created. Configure the settings below" alert.
- New test.

**C-4.7-7:** `test_wizard_banner_hidden_without_setup_param`
- Setup: `seed_user`, `auth_client`, HYSA account.
- Action: GET `/accounts/<id>/hysa` (no `setup` param).
- Expected: Response does NOT contain the wizard banner alert.
- New test.

**F. Manual verification steps.**

1. Create a new HYSA account. Verify redirect to HYSA detail page with setup banner.
2. Create a new checking account. Verify redirect to accounts list (no parameter page).
3. Create a new brokerage account. Verify redirect to investment dashboard with setup banner.
4. Visit the savings dashboard. Verify "Setup Required" badge on unconfigured accounts.
5. Configure the HYSA's APY. Refresh the dashboard. Verify badge disappears.

**G. Downstream effects.** Investment/retirement account creation now auto-creates `InvestmentParams` records. The savings dashboard route now computes `needs_setup` flags, which adds a minor query overhead per account.

**H. Rollback notes.** Route and template changes. No migration. Revertable independently.

---

### Commit #8: Task 4.15 -- Auto Loan Parameter Page Fixes

**A. Commit message:** `fix(auto_loan): add editable term field and pre-populate principal from balance`

**B. Problem statement.** The auto loan parameter page (`app/templates/auto_loan/dashboard.html` lines 76-108) allows editing `current_principal`, `interest_rate`, and `payment_day`, but NOT `term_months`. The route handler (`app/routes/auto_loan.py` line 163) only accepts those three fields. Additionally, when creating a new auto loan account, the initial balance entered on the creation form does not carry over to the `current_principal` field on the parameter page -- the user must enter the same number twice.

**C. Files modified.**

- `app/templates/auto_loan/dashboard.html` -- Modified. Add `term_months` input field to the parameter form (after `payment_day`, before the submit button).
- `app/routes/auto_loan.py` -- Modified. Accept and save `term_months` in the update handler (line 163 area).
- `app/routes/accounts.py` -- Modified. In `create_account()`, when creating an auto loan, copy the initial balance to `AutoLoanParams.current_principal`.
- `tests/test_routes/test_auto_loan.py` -- Modified.

**D. Implementation approach.**

1. **Add term_months input.** In `auto_loan/dashboard.html`, after the `payment_day` field (line 103), add:

```html
<div class="mb-3">
  <label for="term_months" class="form-label">Loan Term (months)</label>
  <input type="number" class="form-control" id="term_months"
         name="term_months" min="1" max="360" step="1"
         value="{{ params.term_months or '' }}" required>
</div>
```

2. **Update route handler.** In `auto_loan.py`, the update handler (around line 163) currently saves only `current_principal`, `interest_rate`, `payment_day`. Add `term_months`:

```python
params.term_months = int(form_data["term_months"])
```

3. **Pre-populate principal.** In `accounts.py:create_account()`, after auto loan params are created, copy the anchor balance:

```python
if account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN):
    # Auto-create params with principal from initial balance.
    from app.models.auto_loan_params import AutoLoanParams
    existing = db.session.query(AutoLoanParams).filter_by(account_id=account.id).first()
    if not existing:
        params = AutoLoanParams(account_id=account.id)
        if account.current_anchor_balance:
            params.current_principal = account.current_anchor_balance
        db.session.add(params)
        db.session.commit()
```

4. **Verify other loan types.** Per the agent research, personal_loan and student_loan do NOT have dedicated parameter pages or route handlers. This fix applies only to auto_loan.

**HTMX regression analysis:** The auto loan parameter page uses standard form POST, not HTMX. No grid interactions are affected.

**E. Test cases.**

**C-4.15-1:** `test_auto_loan_term_field_present`
- Setup: `seed_user`, `auth_client`, auto loan account with params.
- Action: GET auto loan dashboard.
- Expected: Response HTML contains an `<input>` with `name="term_months"`.
- New test.

**C-4.15-2:** `test_auto_loan_term_update_saves`
- Setup: `seed_user`, `auth_client`, auto loan with `term_months=60`.
- Action: POST update with `term_months=48`.
- Expected: 200/302 success. Database shows `term_months=48`.
- New test.

**C-4.15-3:** `test_auto_loan_principal_prepopulated_from_balance`
- Setup: `seed_user`, `auth_client`.
- Action: Create auto loan account with initial balance $15,000. Then GET the parameter page.
- Expected: The `current_principal` input field contains `15000` (or `15000.00`).
- New test.

**C-4.15-4:** `test_auto_loan_principal_zero_when_no_balance`
- Setup: `seed_user`, `auth_client`.
- Action: Create auto loan account with no initial balance (or $0). Then GET the parameter page.
- Expected: The `current_principal` field is empty or zero. No error.
- New test (edge case).

**F. Manual verification steps.** Create auto loan with $15,000 balance. Verify the parameter page shows $15,000 in principal. Verify term_months field is present and editable. Change term to 48. Submit. Verify saved.

**G. Downstream effects.** The `create_account()` route now auto-creates `AutoLoanParams` with pre-populated principal. Previously, the mortgage route handled this redirect, but auto loan params were created later by the parameter page route. This change means the params record exists before the user reaches the parameter page.

**H. Rollback notes.** Route and template changes. No migration (the `term_months` column already exists on the `AutoLoanParams` model). Revertable independently.

---

### Commit #9: Task 4.14 -- Checking Account Balance Projection

**A. Commit message:** `feat(accounts): add balance projection to checking account detail page`

**B. Problem statement.** The checking account has no dedicated detail page. When the user visits the savings dashboard, HYSA accounts have a detail page with 3/6/12-month balance projections (at `app/routes/accounts.py:539-679`, template `accounts/hysa_detail.html`). The checking account -- the most important account for the payday workflow -- has no such forward-looking view outside the budget grid. The user has confirmed that checking APY is negligible and interest projection is not needed.

**C. Files modified.**

- `app/templates/accounts/checking_detail.html` -- New. Checking account detail page with balance projection table.
- `app/routes/accounts.py` -- Modified. Add `checking_detail(account_id)` route handler.
- `app/templates/savings/dashboard.html` -- Modified. Add a link from the checking account card to the detail page.
- `tests/test_routes/test_accounts.py` -- Modified.

**D. Implementation approach.**

Create a new route `accounts.checking_detail(account_id)` that:

1. Loads the checking account and verifies `account.account_type_id == ref_cache.acct_type_id(AcctTypeEnum.CHECKING)`. If not, return 404.
2. Loads all pay periods via `pay_period_service.get_all_periods(user_id)`.
3. Loads all transactions for the account.
4. Calls `balance_calculator.calculate_balances()` to get projected end balances per period.
5. Extracts projections at 3-month (period index +6), 6-month (+13), and 1-year (+26) intervals.
6. Renders a template showing: account name, current anchor balance, anchor date, and projected balances at the three intervals.

The template reuses the display pattern from `accounts/hysa_detail.html` (which shows projected balances in a card with 3/6/12 month rows) but without interest calculations.

On the savings dashboard, add a detail link to the checking account card, similar to the existing HYSA detail link (line 94: `<a href="{{ url_for('accounts.hysa_detail', account_id=ad.account.id) }}"`).

**HTMX regression analysis:** This adds a new page. No existing HTMX interactions are modified or affected.

**E. Test cases.**

**C-4.14-1:** `test_checking_detail_page_renders`
- Setup: `seed_user`, `auth_client`, checking account with anchor balance $5000, current and future periods, seeded expense and income transactions.
- Action: GET `/accounts/<checking_id>/detail`.
- Expected: 200 response. Response contains account name, current balance ($5000), and projected balance values for 3/6/12 months.
- New test.

**C-4.14-2:** `test_checking_detail_projection_values`
- Setup: `seed_user`, `auth_client`, checking account with anchor $5000, one recurring income ($2000/period) and one recurring expense ($1500/period) for the next 26 periods.
- Action: GET `/accounts/<checking_id>/detail`.
- Expected: 3-month projection shows $5000 + 6*(2000-1500) = $8000. (This is approximate -- actual calculation depends on exactly which periods fall in the range.)
- New test. Verifies the projection logic matches the grid's balance calculator.

**C-4.14-3:** `test_checking_detail_rejects_non_checking`
- Setup: `seed_user`, `auth_client`, savings account (not checking).
- Action: GET `/accounts/<savings_id>/detail`.
- Expected: 404 response. Not a checking account.
- New test.

**C-4.14-4:** `test_checking_detail_rejects_other_user`
- Setup: `seed_user`, `auth_client`, `seed_second_user`, `second_auth_client`, checking account owned by second user.
- Action: First user GETs `/accounts/<second_user_checking_id>/detail`.
- Expected: 404 response. Ownership check fails.
- New test. Standard IDOR protection test.

**F. Manual verification steps.** Navigate to the checking account detail page. Verify the current balance is displayed. Verify 3/6/12 month projections are shown. Compare the values with the grid's balance row to ensure consistency.

**G. Downstream effects.** A new route and template are added. The savings dashboard gets a new link for checking accounts. No existing functionality is modified.

**H. Rollback notes.** New route and template. Revertable by removing the route, template, and dashboard link. No migration.

---

### Commit #10: Task 4.16 -- Retirement Date Validation UX

**A. Commit message:** `fix(retirement): preserve form data and highlight fields on validation errors`

**B. Problem statement.** When an invalid date is entered for Earliest Retirement Date or Planned Retirement Date on the pension form, the toast notification says "correct the highlighted error" but no fields are actually highlighted. The form clears all data on validation failure, forcing the user to re-enter everything. Per the agent research, the validation is in `app/routes/retirement.py` (lines 531-556): errors are flashed and a redirect is issued (line 553-556), which loses all form data.

**C. Files modified.**

- `app/routes/retirement.py` -- Modified. In the pension update handler (line 509-556), change from `flash() + redirect()` to `render_template()` on validation failure, passing back the submitted form data and field-level error messages. Same change for `update_settings` handler (lines 626-666).
- `app/templates/retirement/pension_form.html` -- Modified. Add Bootstrap `is-invalid` class and `invalid-feedback` divs for date fields. Pre-populate fields from `form_data` dict when re-rendering after error.
- `app/templates/settings/_retirement.html` -- Modified. Same error display pattern for retirement settings form.
- `tests/test_routes/test_retirement.py` -- Modified.

**D. Implementation approach.**

The current pattern:
```python
# BEFORE (retirement.py lines 553-556):
if errors:
    for err in errors:
        flash(err, "danger")
    return redirect(url_for("retirement.edit_pension", pension_id=pension_id))
```

The fix -- render the form directly with error context:
```python
# AFTER:
if errors:
    field_errors = {}
    for field, msg in errors:
        field_errors[field] = msg
    return render_template(
        "retirement/pension_form.html",
        pension=pension,
        form_data=form_data,
        field_errors=field_errors,
        salary_profiles=salary_profiles,
    ), 422
```

Where `errors` is changed from a list of strings to a list of `(field_name, message)` tuples:

```python
# BEFORE:
errors.append("Planned retirement date must be after hire date.")

# AFTER:
errors.append(("planned_retirement_date", "Must be after hire date."))
```

In the template, add error display:

```html
<input type="date" name="planned_retirement_date"
       class="form-control {% if field_errors and 'planned_retirement_date' in field_errors %}is-invalid{% endif %}"
       value="{{ form_data.planned_retirement_date if form_data else (pension.planned_retirement_date or '') }}">
{% if field_errors and 'planned_retirement_date' in field_errors %}
  <div class="invalid-feedback">{{ field_errors['planned_retirement_date'] }}</div>
{% endif %}
```

The same pattern applies to `earliest_retirement_date`, `hire_date`, and the settings form fields.

**HTMX regression analysis:** The retirement dashboard uses HTMX for the gap analysis slider (`hx-trigger="slider-changed"` on the gap-analysis-container div). The pension form and settings form use standard POST. This change does not affect any HTMX interactions.

**E. Test cases.**

**C-4.16-1:** `test_pension_validation_preserves_form_data`
- Setup: `seed_user`, `auth_client`, pension profile with valid dates.
- Action: POST pension update with `planned_retirement_date` set to a date in the past (invalid).
- Expected: 422 response. Form re-rendered. The `planned_retirement_date` input contains the submitted (invalid) value. Other fields (name, hire_date, benefit_multiplier) retain their submitted values.
- New test.

**C-4.16-2:** `test_pension_validation_highlights_invalid_field`
- Setup: `seed_user`, `auth_client`, pension profile.
- Action: POST with `planned_retirement_date` before `hire_date`.
- Expected: 422 response. Response HTML contains `is-invalid` class on the `planned_retirement_date` input. Contains `invalid-feedback` div with error text.
- New test.

**C-4.16-3:** `test_pension_validation_valid_fields_not_highlighted`
- Setup: Same as C-4.16-2.
- Action: POST with invalid planned_retirement_date but valid hire_date.
- Expected: The `hire_date` input does NOT have `is-invalid` class. Only the invalid field is highlighted.
- New test.

**C-4.16-4:** `test_settings_validation_preserves_form_data`
- Setup: `seed_user`, `auth_client`.
- Action: POST retirement settings with an invalid `safe_withdrawal_rate` (e.g., negative).
- Expected: 422. Form re-rendered with submitted values. Error on the invalid field.
- New test.

**F. Manual verification steps.** Open the pension form. Enter an invalid planned retirement date (before hire date). Submit. Verify: the field is highlighted with a red border, the error message appears below the field, and all other fields retain their entered values.

**G. Downstream effects.** Changes the response pattern from redirect-on-error to render-on-error for the pension and settings forms. The template must handle both initial load (pension object) and error re-render (form_data dict).

**H. Rollback notes.** Route and template changes. No migration. Revertable independently.

---

### Commit #11: Task 4.17 -- Retirement Dashboard Return Rate Clarity

**A. Commit message:** `feat(retirement): clarify return rate slider behavior with explanatory text`

**B. Problem statement.** The Assumed Annual Return slider on the retirement dashboard (`app/templates/retirement/dashboard.html` lines 47-61) is unclear. Per the agent research, the slider triggers an HTMX request to `retirement.gap_analysis` (line 73-78) which recalculates the aggregate gap analysis. However, the user cannot determine: what the slider controls, whether it overrides per-account rates, or what fields change when it moves. Individual retirement account projections on the dashboard appear to use per-account rates, not the slider value.

**C. Files modified.**

- `app/templates/retirement/dashboard.html` -- Modified. Add explanatory text below the slider. Clarify the relationship between the global slider and per-account rates.
- `tests/test_routes/test_retirement.py` -- Modified.

**D. Implementation approach.**

Add a `<small class="text-muted d-block mt-1">` paragraph below the return rate slider (after line 61):

```html
<small class="text-muted d-block mt-1">
  This rate is used to project your <strong>aggregate</strong> retirement
  savings in the gap analysis below. Individual account projections use
  their own configured rates (set on each account's parameter page).
</small>
```

If the dashboard also shows individual account projections, add a visual distinction:
```html
{# Near individual account projection cards: #}
<small class="text-muted">Using account rate: {{ params.assumed_annual_return * 100 }}%</small>
```

**HTMX regression analysis:** The slider's HTMX behavior (`hx-trigger="slider-changed"`, `hx-get`, `hx-target="closest .card-body"`) is unchanged. Only static text is added below the slider. No HTMX attributes are modified.

**E. Test cases.**

**C-4.17-1:** `test_retirement_slider_has_explanatory_text`
- Setup: `seed_user`, `auth_client`, retirement dashboard data.
- Action: GET retirement dashboard.
- Expected: Response contains the explanatory text string "aggregate" and "Individual account projections" (or similar distinguishing text).
- New test.

**F. Manual verification steps.** Open the retirement dashboard. Read the text below the slider. Verify it clearly states what the slider affects. Move the slider. Verify the gap analysis section updates via HTMX. Verify individual account projections do NOT change (they use their own rates).

**G. Downstream effects.** None. Template-only addition of static text.

**H. Rollback notes.** Template-only. Revertable independently.

---

### Commit #12: Task 4.9 -- Chart Balance Over Time Contrast

**A. Commit message:** `feat(charts): add dash patterns and varied line weights to balance chart`

**B. Problem statement.** The Balance Over Time chart (`app/static/js/chart_balance.js`) renders all dataset lines with `borderWidth: 2`, `fill: false`, `tension: 0.3` and no dash pattern differentiation. With 5+ accounts (checking, savings, HYSA, mortgage, retirement), lines can be hard to distinguish, especially when colors are similar or lines are close together. The roadmap specifically mentions the "standard payments" line on the mortgage account detail chart is difficult to see.

**C. Files modified.**

- `app/static/js/chart_balance.js` -- Modified. Add a `lineStyles` array with cycling dash patterns and varied border widths.
- `tests/test_routes/test_charts.py` -- Modified (if chart rendering is testable via route response).

**D. Implementation approach.**

Add a `lineStyles` array in `chart_balance.js` that cycles through visual differentiation patterns:

```javascript
var lineStyles = [
  { borderWidth: 2.5, borderDash: [] },             // solid thick
  { borderWidth: 2, borderDash: [8, 4] },            // dashed
  { borderWidth: 2, borderDash: [2, 3] },            // dotted
  { borderWidth: 2.5, borderDash: [12, 4, 2, 4] },   // dash-dot
  { borderWidth: 2, borderDash: [] },                 // solid normal
  { borderWidth: 2, borderDash: [6, 3] },            // short dash
  { borderWidth: 2, borderDash: [3, 2] },            // dense dot
  { borderWidth: 2.5, borderDash: [8, 3, 2, 3] },    // dash-dot-dot
];
```

Apply in the `datasets.map()` call where chart datasets are configured:

```javascript
var style = lineStyles[i % lineStyles.length];
return {
  label: ds.label,
  data: ds.data,
  borderColor: color.dark,
  backgroundColor: color.light,
  borderWidth: style.borderWidth,
  borderDash: style.borderDash,
  fill: false,
  tension: 0.3,
  // ... existing properties ...
};
```

Chart.js legends automatically display line samples with dash patterns, so legend readability is maintained.

**E. Test cases.**

**C-4.9-1:** `test_balance_chart_renders_after_style_changes`
- Setup: `seed_user`, `auth_client`, multiple accounts (checking + HYSA + mortgage).
- Action: GET `/charts/balance-over-time` (HTMX request).
- Expected: 200 response. Response contains the chart canvas element with `data-datasets` attribute. This is a smoke test -- the JS-level dash patterns can only be visually verified, but the route test confirms the chart data is served correctly.
- New or modification of existing chart test.

**F. Manual verification steps.** Open the Charts page. Verify the Balance Over Time chart shows lines with distinct visual styles (solid, dashed, dotted). Select multiple accounts and verify each line has a unique combination of color and dash pattern. Open the mortgage detail page and verify the "standard payments" line is visually distinguishable.

**G. Downstream effects.** None. JS-only change. The chart data format is unchanged -- only the visual presentation changes.

**H. Rollback notes.** JS-only change. Revertable by reverting the single file.

---

### Commit #13: Task 4.10 -- Chart Time Frame Controls

**A. Commit message:** `feat(charts): add time frame controls to balance over time chart`

**B. Problem statement.** The Balance Over Time chart has no time frame controls. The chart always shows all periods from the anchor forward. For long-duration accounts (30-year mortgage at ~780 periods, retirement), the chart has so many data points that near-term detail is compressed to flat lines. The user cannot zoom in.

**C. Files modified.**

- `app/templates/charts/_balance_over_time.html` -- Modified. Add a button group (1Y, 5Y, 10Y, Full) in the chart card header.
- `app/routes/charts.py` -- Modified. Accept `range` query parameter in `balance_over_time()` (line 40-65).
- `app/services/chart_data_service.py` -- Modified. Filter periods by range in `get_balance_over_time()`.
- `tests/test_routes/test_charts.py` -- Modified.

**D. Implementation approach.**

Add time frame buttons as a Bootstrap button group in the chart card. Each button triggers an HTMX request that replaces the chart:

```html
<div class="btn-group btn-group-sm ms-auto" role="group">
  {% for label, value in [('1Y', '1y'), ('5Y', '5y'), ('10Y', '10y'), ('Full', 'full')] %}
  <button class="btn {{ 'btn-primary active' if selected_range == value else 'btn-outline-secondary' }}"
          hx-get="{{ url_for('charts.balance_over_time', range=value) }}"
          hx-include="[name='account_id']:checked, [name='dual_axis']"
          hx-target="closest .card-body"
          hx-swap="innerHTML">
    {{ label }}
  </button>
  {% endfor %}
</div>
```

The route translates the range to a period count:
- `1y` -> 26 periods (1 year of biweekly)
- `5y` -> 130 periods
- `10y` -> 260 periods
- `full` -> all periods (no limit)

Default: intelligent based on account types. If any mortgage or retirement account is checked, default to `full`. If only checking/savings, default to `1y`.

In `chart_data_service.py`, the `get_balance_over_time()` function accepts a `max_periods` parameter and truncates the period list accordingly.

**HTMX regression analysis:** The new buttons use HTMX to re-fetch the chart fragment, targeting `closest .card-body` with `innerHTML` swap. This is the same pattern used by the existing account checkbox toggles (lines 11-14 of `_balance_over_time.html`). No other interactions are affected.

**E. Test cases.**

**C-4.10-1:** `test_balance_chart_accepts_range_parameter`
- Setup: `seed_user`, `auth_client`, seeded periods (2 years worth, ~52 periods) and accounts.
- Action: GET `/charts/balance-over-time?range=1y` (HTMX request).
- Expected: 200 response. Chart data contains approximately 26 labels (1 year of biweekly periods). Not 52.
- New test.

**C-4.10-2:** `test_balance_chart_full_range`
- Setup: Same.
- Action: GET `/charts/balance-over-time?range=full`.
- Expected: 200. Chart data contains all available periods (~52 labels).
- New test.

**C-4.10-3:** `test_balance_chart_range_buttons_rendered`
- Setup: `seed_user`, `auth_client`.
- Action: GET `/charts/balance-over-time`.
- Expected: Response HTML contains buttons with text "1Y", "5Y", "10Y", "Full".
- New test.

**C-4.10-4:** `test_balance_chart_range_fewer_periods_than_requested`
- Setup: `seed_user`, `auth_client`, only 1 year of periods (~26).
- Action: GET `/charts/balance-over-time?range=10y`.
- Expected: 200. Chart shows all available periods (26). Does not error because fewer periods exist than requested.
- New test (edge case).

**F. Manual verification steps.** Open the Charts page. Verify 1Y/5Y/10Y/Full buttons appear. Click each button. Verify the chart updates with different numbers of data points. With a mortgage account selected, verify the default is `full`. With only checking, verify the default is `1y`.

**G. Downstream effects.** None beyond the charts page.

**H. Rollback notes.** Route, service, and template changes. No migration. Revertable.

---

### Commit #14: Task 4.3 -- Pay Period Date Format Cleanup

**A. Commit message:** `feat(grid): condense pay period date headers -- omit year for current year`

**B. Problem statement.** Pay period column headers in `grid/grid.html` (lines 81-82) always show two lines:
```html
<div class="fw-bold">{{ period.start_date.strftime('%m/%d') }}</div>
<div class="small text-light-emphasis">{{ period.end_date.strftime('%m/%d/%y') }}</div>
```
The year suffix on the end date is redundant for current-year periods and wastes horizontal space. Every period shows this format regardless of whether the year is obvious from context.

**C. Files modified.**

- `app/templates/grid/grid.html` -- Modified. Update period header date format logic (lines 81-82).
- `tests/test_routes/test_grid.py` -- Modified.

**D. Implementation approach.**

Replace the current two-line header with a conditional format. The `today` variable is already in the template context (passed from `grid.index()` at line 160):

```html
{% set current_year = today.year %}
{% if period.start_date.year != current_year or period.end_date.year != current_year %}
  <div class="fw-bold">{{ period.start_date.strftime('%m/%d/%y') }}</div>
  <div class="small text-light-emphasis">{{ period.end_date.strftime('%m/%d/%y') }}</div>
{% else %}
  <div class="fw-bold">{{ period.start_date.strftime('%m/%d') }} - {{ period.end_date.strftime('%m/%d') }}</div>
{% endif %}
```

For current-year periods, this combines the dates into a single line "03/24 - 04/06", saving one line of vertical space in the header and improving scannability. For cross-year periods (start or end date in a different year), the full MM/DD/YY format is shown on both dates.

**HTMX regression analysis:** This task modifies only the `<thead>` period header content. No HTMX interactions target the `<thead>` directly. The carry forward button (GI-14) is inside the `<th>` but below the date display -- the `hx-post` and `hx-swap` attributes are unaffected by the date format change. All interactions SAFE.

**E. Test cases.**

**C-4.3-1:** `test_period_header_omits_year_for_current_year`
- Setup: `seed_user`, `auth_client`, seeded periods all within the current year.
- Action: GET `/`.
- Expected: Period headers show "MM/DD - MM/DD" format on a single line. Response does NOT contain "/26" (or current year suffix) in the period headers.
- New test.

**C-4.3-2:** `test_period_header_includes_year_for_different_year`
- Setup: `seed_user`, `auth_client`, seeded periods that span into the next year (e.g., a period starting 12/30/26 and ending 01/12/27).
- Action: GET `/`.
- Expected: For the cross-year period, the header shows "MM/DD/YY" format on both dates.
- New test.

**C-4.3-3:** `test_period_header_year_boundary`
- Setup: `seed_user`, `auth_client`, period starting 12/30 of current year and ending 01/12 of next year.
- Action: GET `/`.
- Expected: This period shows year suffix because `end_date.year != current_year`. The adjacent all-current-year period shows the compact format.
- New test (boundary case).

**F. Manual verification steps.** Open the grid. Verify current-year periods show "MM/DD - MM/DD" format on one line. If the grid extends to the next year, verify those periods show year suffixes. Verify the carry forward button still appears and functions on past periods.

**G. Downstream effects.** None. Template-only change to the `<thead>`.

**H. Rollback notes.** Template-only change. Revertable.

---

### Commit #15: Task 4.1 -- Grid Layout: Transaction Name Row Headers

**A. Commit message:** `feat(grid): show transaction names in row headers for clarity`

**B. Problem statement.** The grid currently iterates over `categories` (from `grid.py` line 120-125, ordered by `group_name, item_name`) and renders one `<tr>` per category. The row label shows `category.item_name` (line 133-134: `<th scope="row" class="sticky-col row-label" title="{{ category.display_name }}">{{ category.item_name }}</th>`). When multiple transactions share a category item (e.g., "Insurance: Auto" with templates "State Farm" and "Geico"), they appear in the same row and are only distinguishable by hovering over the cell to see the `title` attribute on the `.txn-cell` div (line 18: `title="{{ t.name }}..."`). Shadow transactions from transfers sharing "Transfers: Outgoing" have the same problem.

**C. Files modified.**

- `app/routes/grid.py` -- Modified. Build `row_keys` from transaction data for template-name-based rows, replacing the category-based iteration.
- `app/templates/grid/grid.html` -- Modified. Replace category iteration with row_key iteration for the INCOME (lines 109-160) and EXPENSES (lines 191-240) sections.
- `app/static/css/app.css` -- Modified. Adjust `.row-label-col` width if row labels are longer.
- `tests/test_routes/test_grid.py` -- Modified. Update row structure assertions.

**D. Implementation approach.**

**Recommendation: Option A (Full Row Headers).** Each transaction template gets its own row with the transaction name in the row header.

**Step 1: Build row_keys in the route.**

In `grid.py:index()`, after the `categories` query (line 120-125) and the `txn_by_period` grouping (line 115-117), build a list of row keys:

```python
def _build_row_keys(txn_by_period, categories, is_income_fn):
    """Build ordered row keys for the grid, one per unique transaction identity.

    Args:
        txn_by_period: dict mapping period_id -> list of Transaction objects.
        categories: list of Category objects ordered by group_name, item_name.
        is_income_fn: callable that returns True for income transactions.

    Returns:
        List of dicts, each with keys: category, group_name, display_name,
        category_id, template_id, txn_name.
    """
    row_keys = []
    seen = set()

    for cat in categories:
        # Collect all distinct transaction identities for this category.
        for period_txns in txn_by_period.values():
            for txn in period_txns:
                if txn.category_id != cat.id or not is_income_fn(txn):
                    continue
                if txn.is_deleted:
                    continue
                key = (cat.id, txn.template_id, txn.name)
                if key not in seen:
                    seen.add(key)
                    row_keys.append({
                        "category": cat,
                        "group_name": cat.group_name,
                        "display_name": txn.name,
                        "category_id": cat.id,
                        "template_id": txn.template_id,
                        "txn_name": txn.name,
                    })

    return row_keys

income_row_keys = _build_row_keys(
    txn_by_period, categories, lambda t: t.is_income
)
expense_row_keys = _build_row_keys(
    txn_by_period, categories, lambda t: t.is_expense
)
```

Pass `income_row_keys` and `expense_row_keys` to the template in the `render_template()` call (line 145-164).

**Step 2: Update the grid template.**

Replace the category-based INCOME section (lines 109-160):

```html
{# BEFORE: iterate over categories #}
{% for category in categories %}
  {% set has_income = [] %}
  ...
  {% if has_income %}
    <tr>
      <th scope="row" class="sticky-col row-label"
          title="{{ category.display_name }}">
        {{ category.item_name }}
      </th>
      ...
    </tr>
  {% endif %}
{% endfor %}

{# AFTER: iterate over row_keys #}
{% set ns = namespace(current_group='') %}
{% for rk in income_row_keys %}
  {% if rk.group_name != ns.current_group %}
    {% set ns.current_group = rk.group_name %}
    <tr class="group-header-row">
      <td class="sticky-col text-muted small fw-semibold"
          colspan="{{ periods|length + 1 }}">
        {{ rk.group_name }}
      </td>
    </tr>
  {% endif %}

  <tr>
    <th scope="row" class="sticky-col row-label"
        title="{{ rk.category.display_name }}">
      {{ rk.display_name }}
    </th>
    {% for period in periods %}
      {% set period_txns = txn_by_period.get(period.id, []) %}
      {% set matched = [] %}
      {% for txn in period_txns %}
        {% if txn.category_id == rk.category_id
            and (txn.template_id == rk.template_id or txn.name == rk.txn_name)
            and txn.is_income and not txn.is_deleted
            and txn.status_id != STATUS_CANCELLED %}
          {% if matched.append(txn) %}{% endif %}
        {% endif %}
      {% endfor %}
      <td class="text-end cell">
        {% if matched %}
          {% for txn in matched %}
            <div id="txn-cell-{{ txn.id }}">
              {% set found = namespace(txn=txn) %}
              {% include "grid/_transaction_cell.html" with context %}
            </div>
          {% endfor %}
        {% else %}
          {% set txn_type_id = TXN_TYPE_INCOME %}
          {% include "grid/_transaction_empty_cell.html" with context %}
        {% endif %}
      </td>
    {% endfor %}
  </tr>
{% endfor %}
```

The same change applies to the EXPENSES section (lines 191-240) using `expense_row_keys`.

**Step 3: Verify subtotal and Net Cash Flow calculations.**

The subtotal rows (lines 163-176 and 243-256) and Net Cash Flow row (lines 259-280) iterate over ALL transactions in `txn_by_period.get(period.id, [])`, not over row keys. They are unaffected by the row key restructure. Verify that the sums remain identical.

**HTMX regression analysis:**

- **GI-1 (Cell click -> quick edit):** `hx-target="#txn-cell-<id>"` -- SAFE. The target is the `<div id="txn-cell-<id>">` inside the `<td>`, which is unchanged.
- **GI-2 (Quick edit submit):** Same target -- SAFE.
- **GI-3 (Expand to full edit):** JS uses `triggerEl.closest('td')` -- SAFE. The `<td>` structure is unchanged.
- **GI-4 (Full edit submit):** Targets `#txn-cell-<id>` -- SAFE.
- **GI-5 through GI-8 (Status changes):** Use `gridRefresh` which reloads the page -- SAFE.
- **GI-9 (Empty cell click):** `hx-target="closest td"` -- SAFE. The `<td>` is still present. The `hx-get` URL passes `category_id` and `period_id`, both of which remain valid per the row key.
- **GI-10 (Balance row refresh):** Targets `#grid-summary` tfoot -- NOT AFFECTED by tbody changes.
- **GI-11/12/13 (Anchor edit):** Outside the grid table -- NOT AFFECTED.
- **GI-14 (Carry forward):** Returns `gridRefresh` -- SAFE.
- **GI-15 (Inline create):** `hx-target="closest td"` -- SAFE.
- **GI-16 (Add transaction modal):** Uses `location.reload()` -- SAFE.
- **GI-17 (Date range controls):** Full page load -- SAFE.
- **GI-18 (Keyboard navigation):** `getDataRows()` (app.js:357-368) filters out rows with classes: `section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`. The new template still uses `group-header-row` for group headers. Transaction rows do NOT have any excluded classes. Subtotal and Net Cash Flow rows retain their excluded classes. **SAFE** -- no changes to the exclusion list needed.
- **GI-20/21 (Escape/F2):** Target resolution unchanged -- SAFE.

**E. Test cases.**

**C-4.1-1:** `test_grid_renders_transaction_names_in_row_labels`
- Setup: `seed_user`, `auth_client`, two transaction templates in the same category (e.g., "Insurance: Auto" with templates "State Farm" and "Geico"), each with a transaction in the current period.
- Action: GET `/`.
- Expected: Response HTML contains two separate `<tr>` rows within the EXPENSES section. Each row has a `<th class="sticky-col row-label">` containing "State Farm" and "Geico" respectively. Verify by checking for both names within `.row-label` elements.
- New test.

**C-4.1-2:** `test_grid_shadow_transactions_get_own_rows`
- Setup: `seed_user`, `auth_client`, transfer with shadow transactions (expense shadow in checking grid).
- Action: GET `/`.
- Expected: The shadow transaction appears in its own row with its template name (e.g., "Transfer to Savings") in the `<th class="row-label">`. The cell contains the transfer indicator icon (`bi-arrow-left-right`).
- New test.

**C-4.1-3:** `test_grid_inline_edit_after_layout_change`
- Setup: `seed_user`, `auth_client`, transaction in the grid.
- Action: GET `/transactions/<id>/quick-edit`. Then PATCH `/transactions/<id>` with a new amount.
- Expected: Quick edit returns 200 with the input form. Patch returns 200 with updated cell content. `HX-Trigger` header contains `balanceChanged`.
- New test (HTMX regression).

**C-4.1-4:** `test_grid_empty_state_after_layout_change`
- Setup: `seed_user`, `auth_client`, seeded periods but NO transaction templates (empty grid).
- Action: GET `/`.
- Expected: Grid renders without errors. Section banners (INCOME, EXPENSES) appear. No transaction rows. Subtotal rows show zeros. Net Cash Flow row shows zero. No Python errors from empty `row_keys`.
- New test (edge case).

**C-4.1-5:** `test_keyboard_navigation_after_layout_change`
- Setup: `seed_user`, `auth_client`, transactions in the grid.
- Action: GET `/`. Inspect the CSS classes on all `<tr>` elements in the response.
- Expected: Transaction rows do NOT have any of the excluded classes (`section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`). Banner, spacer, group header, subtotal, and net cash flow rows DO have their respective excluded classes.
- New test.

**C-4.1-6:** `test_payday_workflow_complete_after_layout_change`
- Setup: Full payday workflow data (3 periods, income + expenses, checking account, anchor balance).
- Action: Execute C-0-7 sequence (true-up, mark received, carry forward, mark done, mark credit).
- Expected: All steps pass. Final balance matches expected values.
- Regression test (reuses C-0-7 logic).

**C-4.1-7:** `test_group_headers_still_appear`
- Setup: `seed_user`, `auth_client`, transactions in two different category groups (e.g., "Home" and "Insurance").
- Action: GET `/`.
- Expected: Group header rows (`<tr class="group-header-row">`) appear before each group's transaction rows. The group name text is visible.
- New test.

**F. Manual verification steps.**

1. Open the grid. Verify each transaction has its own row with its name in the row header.
2. Verify category group headers still appear above grouped transaction rows.
3. Count rows: should be one per unique transaction template plus group headers, section banners, subtotals, and net cash flow.
4. Verify shadow transactions (from transfers) appear with their template names and transfer indicator icons.
5. Perform the complete payday workflow (true-up, mark received, carry forward, mark done, mark credit). Verify every step works.
6. Test keyboard navigation (arrow keys). Verify it navigates through transaction rows and skips banner/subtotal/group-header rows.
7. Test with a narrow browser window. Verify row labels truncate gracefully with ellipsis.
8. Click an empty cell in a row-key row. Verify the quick create form appears with the correct category pre-selected.

**G. Downstream effects.**

- The `categories` context variable is supplemented by `income_row_keys` and `expense_row_keys`. The `categories` variable is still passed (used by the Add Transaction modal and potentially by other template includes).
- The empty cell interaction (GI-9) passes `category_id` which is still present in each row key.
- The subtotal rows iterate over all transactions, not row keys, so they are unaffected.

**H. Rollback notes.** Route and template changes. No migration. No database changes. Revertable by reverting the grid.py and grid.html changes.

---

### Commit #16: Task 4.12 -- Grid Tooltip Enhancement

**A. Commit message:** `feat(grid): enhance tooltips with full amount and faster display`

**B. Problem statement.** Grid cell tooltips currently show only the transaction name (line 18 of `_transaction_cell.html`: `title="{{ t.name }}{% if t.notes %} -- {{ t.notes }}{% endif %}"`). For transactions where the grid displays a rounded amount (e.g., "$16" for $15.96), the tooltip does not show the full dollar amount. Additionally, the tooltip may be slow to appear if Bootstrap's default delay is in effect.

**C. Files modified.**

- `app/templates/grid/_transaction_cell.html` -- Modified. Update the `title` attribute on the `.txn-cell` div (line 18) to include the full formatted dollar amount.
- `app/static/js/app.js` or `grid_edit.js` -- Modified. Initialize Bootstrap tooltips with reduced delay if not already configured.
- `app/static/css/app.css` -- Modified if tooltip styling adjustments are needed.
- `tests/test_routes/test_grid.py` -- Modified. Assert tooltip content.

**D. Implementation approach.**

Since task 4.1 (commit #15) puts transaction names in row headers, the tooltip no longer needs to show the name redundantly. Update the tooltip to focus on the full dollar amount, actual vs. estimated comparison, and status:

```html
{# BEFORE (line 18): #}
title="{{ t.name }}{% if t.notes %} -- {{ t.notes }}{% endif %}"

{# AFTER: #}
title="${{ '{:,.2f}'.format(t.effective_amount) }}{% if t.actual_amount is not none and t.actual_amount != t.estimated_amount %} (est: ${{ '{:,.2f}'.format(t.estimated_amount) }}){% endif %}{% if t.status %} -- {{ t.status.name }}{% endif %}{% if t.notes %} -- {{ t.notes }}{% endif %}"
```

This shows (example): "$15.96 -- Paid" for a settled transaction, or "$500.00 (est: $550.00) -- Paid" for one where the actual differed from the estimate, or "$150.00 -- Projected -- Auto-pay scheduled" for a projected transaction with notes.

For tooltip speed, check whether Bootstrap tooltips are initialized in `app.js`. If they use the default data-attribute initialization (via `data-bs-toggle="tooltip"`), add an explicit initialization with reduced delay:

```javascript
// In app.js, after DOM ready:
var tooltipTriggerList = [].slice.call(
    document.querySelectorAll('[data-bs-toggle="tooltip"]')
);
tooltipTriggerList.forEach(function(el) {
    new bootstrap.Tooltip(el, { delay: { show: 200, hide: 0 } });
});
```

If tooltips use the native browser `title` attribute (no Bootstrap tooltip initialization), the speed is controlled by the browser and cannot be adjusted. In that case, consider switching to Bootstrap tooltips with explicit initialization for the grid cells.

**HTMX regression analysis:** The `title` attribute change is purely cosmetic -- it does not affect any HTMX targets, triggers, or swap behavior. The tooltip content is rendered server-side and does not involve any HTMX request. All grid interactions SAFE.

**E. Test cases.**

**C-4.12-1:** `test_tooltip_contains_full_amount`
- Setup: `seed_user`, `auth_client`, transaction with `estimated_amount=1234.56`.
- Action: GET `/`.
- Expected: The `.txn-cell` div's `title` attribute contains "$1,234.56". NOT just "$1,235" (the rounded display).
- New test.

**C-4.12-2:** `test_tooltip_shows_actual_when_different`
- Setup: `seed_user`, `auth_client`, settled transaction with `estimated_amount=500.00` and `actual_amount=487.32`.
- Action: GET `/`.
- Expected: Tooltip contains "$487.32" AND "(est: $500.00)".
- New test.

**C-4.12-3:** `test_tooltip_includes_status`
- Setup: `seed_user`, `auth_client`, transaction with Paid status.
- Action: GET `/`.
- Expected: Tooltip ends with "-- Paid".
- New test.

**C-4.12-4:** `test_tooltip_includes_notes`
- Setup: `seed_user`, `auth_client`, transaction with notes="Auto-pay scheduled".
- Action: GET `/`.
- Expected: Tooltip contains "-- Auto-pay scheduled".
- New test.

**F. Manual verification steps.** Hover over a grid cell. Verify the tooltip appears quickly (~200ms). Verify it shows the full dollar amount with cents. For a transaction where actual differs from estimated, verify both amounts appear. Verify the status label is included.

**G. Downstream effects.** None. Template content change only.

**H. Rollback notes.** Template and possibly JS changes. Revertable.

---

## Final Gate

After all commits are merged, run the full test suite:

```bash
timeout 660 pytest -v --tb=short
```

All tests must pass before reporting the phase as complete.

Additionally, perform a complete manual payday workflow walkthrough:

1. Open the app. Verify the grid loads correctly with transaction names in row headers.
2. True-up the checking balance. Verify projections recalculate.
3. Mark paycheck as received. Verify the status updates and balance reflects actual amount.
4. Carry forward unpaid items from a past period. Verify items move and balances recalculate.
5. Mark cleared expenses as paid (not "done"). Verify status badge shows "Paid" and balance updates.
6. Mark one expense as credit. Verify payback transaction appears in next period.
7. Scan projected balances across future periods. Verify they are reasonable.
8. Visit the accounts dashboard. Verify emergency fund coverage reflects transfers. Verify "Setup Required" badges on unconfigured accounts.
9. Visit the checking account detail page. Verify balance projection is displayed.
10. Visit the salary profile page. Verify View Breakdown and View Projection buttons are accessible near the top.
11. Visit the tax config page. Verify State Tax is first, Federal brackets are collapsed.
12. Visit the retirement dashboard. Verify the return rate slider has explanatory text. Test date validation error display.
13. Open the Charts page. Verify Balance Over Time chart has distinct line styles and time frame controls.
14. Create a new HYSA account. Verify redirect to parameter page with setup banner.
15. Test keyboard navigation on the grid (arrow keys, Enter, Escape, F2).
