> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Transfer Architecture Rework -- Design Document

**Version:** 1.1
**Date:** March 25, 2026
**Status:** Approved for implementation
**Roadmap Position:** Section 3A (between Section 3 Critical Bug Fixes and Section 4 UX/Grid
Overhaul). This is a prerequisite for Section 4, not part of it.
**Prerequisite:** All Section 3 (Critical Bug Fixes) changes are implemented, tested, and merged.
**Parent Documents:** project_requirements_v2.md, project_requirements_v3_addendum.md,
project_roadmap_v4.md

---

## 1. Problem Statement

The app currently maintains two parallel systems for financial activity: `budget.transactions` and
`budget.transfers`. Transactions represent income and expenses within a single account. Transfers
represent money moving between two accounts. These systems have separate models, separate templates,
separate recurrence engines, separate grid rendering paths, separate CRUD routes, and separate
balance calculation logic.

This dual-path architecture creates four specific problems:

### 1.1 Transfers Are Invisible to Cash Flow Tracking

Transfers appear in a dedicated TRANSFERS section at the bottom of the budget grid, separated from
income and expenses. This requires scrolling to see which pay period has a mortgage payment coming
out of checking. During the payday reconciliation workflow, the user must scan two different
sections to understand the full picture of what is happening to the checking account in a given
period. Items that function as expenses from the checking account perspective (e.g., mortgage
payment, savings transfer) are not displayed alongside other expenses.

### 1.2 Transfers Are Invisible to Category-Based Reporting

Charts that show spending by category query only `budget.transactions`. A mortgage payment is a
transfer, not a transaction, so it does not appear in the "Home" spending category even though it is
the largest home-related expense. This makes category reports and charts undercount actual spending.
Any future feature that operates on transactions by category (seasonal forecasting, expense
inflation, smart estimates) will also miss transfers unless each feature is independently patched
to handle the dual-path.

### 1.3 Feature Tax on Every Future Phase

Every feature built from this point forward must ask: "Does this also need to account for
transfers?" The balance calculator queries two tables. The grid rendering merges two object types.
The footer calculations combine two data sources. Phase 9 (seasonal forecasting, smart estimates,
expense inflation) and Phase 10 (notifications for large upcoming expenses, low balance warnings)
will each need parallel code paths for transactions and transfers. This compounds over time and
increases the probability of bugs where one path is updated and the other is not.

### 1.4 One-Time Transfers Do Not Work

A one-time transfer can be created but does not appear in the grid or affect account balances. The
workaround is to create a monthly recurring transfer with tight date bounds so only one instance is
generated. This bug is a symptom of the dual-path architecture: the transfer creation path for
one-time entries likely does not generate the downstream records that the grid and balance
calculator expect.

---

## 2. Architectural Decision

### 2.1 Decision

Transfers will produce linked shadow transactions. The `budget.transfers` table remains as the
user-facing abstraction and the source of truth. When a transfer is created, a service function
atomically creates two `budget.transactions` rows:

1. An **expense** transaction linked to the from_account (reduces from_account balance).
2. An **income** transaction linked to the to_account (increases to_account balance).

Both shadow transactions carry a `transfer_id` foreign key pointing back to the transfer record.

The `budget.transfer_templates` table remains as the user-facing abstraction for recurring
transfers. When the transfer recurrence engine generates a transfer record from a template, the
transfer service also creates the two linked transactions.

### 2.2 Rationale

This approach was chosen over three alternatives:

- **Display-layer only (no schema changes):** Solves grid visibility but not reporting. Charts
  still cannot categorize transfers. Rejected because it leaves problem 1.2 unsolved and creates
  a brittle pseudo-category system.
- **Add category to transfers only:** Solves all three problems but requires every grid query,
  chart query, and future feature to merge two different data types (transactions and transfers)
  at the query or rendering layer. The merge logic must be replicated and maintained in every
  consumer. Rejected because it perpetuates the feature tax (problem 1.3).
- **Consolidate transfer_templates into transaction_templates:** Architecturally cleaner but
  overloads the transaction_templates table with conditional fields (from_account, to_account,
  is_transfer). The transfer template has different semantics from a transaction template and the
  two forms collect different data. Rejected because it adds complexity to the template layer
  without proportional benefit.

The linked shadow transaction approach eliminates the dual-path at the consumer layer. Every
system that reads `budget.transactions` (balance calculator, grid, charts, future features)
automatically includes transfer effects. The transfer abstraction is preserved for the user:
they still create one transfer and affect two accounts. The complexity is contained in a single
service function rather than distributed across every consumer.

### 2.3 Key Principle

**The user never directly edits a shadow transaction.** The transfer record is the source of truth.
All mutations flow through the transfer service, which updates the master transfer record and both
linked transactions atomically. When the user clicks a shadow transaction in the grid, the UI
detects that it has a `transfer_id` and opens the transfer edit form instead of the transaction
edit form.

### 2.4 Database Approach

The production database was deployed on March 23, 2026 and contains minimal data. It will be
dropped and recreated from scratch rather than migrated. This eliminates the risk of data
migration errors and allows clean schema changes without Alembic migration complexity for
existing data. All seed scripts and initial data entry will be re-run after the schema changes.

---

## 3. Schema Changes

### 3.1 budget.transactions -- Add account_id Column

The `budget.transactions` table does not currently have an `account_id` column. Transactions are
associated with accounts through their `template_id` FK to `budget.transaction_templates`, which
has `account_id`. This works for template-generated transactions but fails for shadow
transactions, which have `template_id = NULL` (they are not generated from transaction templates).

Without `account_id` on the transaction itself, the grid query for "show me all transactions for
the checking account" would require a conditional join: for regular transactions, join through the
template; for shadow transactions, join through the transfer and determine direction. This is
fragile, slow, and error-prone.

Add a non-nullable foreign key column:

```sql
account_id INT NOT NULL REFERENCES budget.accounts(id)
```

- **NOT NULL:** Every transaction belongs to an account. No exceptions.
- **Index:** Add an index for the grid query, which filters by account.

```sql
CREATE INDEX idx_transactions_account
    ON budget.transactions(account_id);
```

**Denormalization note:** For template-generated transactions, `account_id` is copied from
`template.account_id` at creation time. The template still owns the account association as part
of its configuration. The transaction's `account_id` is a denormalization for query performance
and for supporting template-less transactions (shadow transactions and manually created ad-hoc
transactions). If a template's `account_id` changes (unlikely but possible), existing
transactions are not retroactively updated -- they record which account they were generated for
at the time of creation. This is consistent with how `estimated_amount` is copied from the
template's `default_amount` at creation time and is not retroactively changed for finalized
transactions.

**Impact on existing transaction creation paths:** Every code path that creates a
`budget.transactions` row must set `account_id`. Claude Code must identify all such paths during
the inventory phase:

- The transaction recurrence engine (creates transactions from templates -- copy
  `template.account_id`).
- Manual transaction creation routes (ad-hoc transactions -- the form already collects account
  context or it can be inferred from the grid's current account view).
- The credit workflow (creates payback transactions -- copy the original transaction's
  `account_id`).
- The transfer service (creates shadow transactions -- use `from_account_id` for the expense
  shadow, `to_account_id` for the income shadow).

**Impact on existing queries:** Any query that currently joins through templates to get the
account can be simplified to filter directly on `transactions.account_id`. Claude Code should
identify and update these queries during implementation. The grid query in `app/routes/grid.py`
is the primary one.

### 3.2 budget.transactions -- Add transfer_id Column

Add one nullable foreign key column to the existing `budget.transactions` table:

```sql
transfer_id INT REFERENCES budget.transfers(id) ON DELETE CASCADE
```

- **Nullable:** Regular transactions (non-transfer) have `transfer_id = NULL`.
- **ON DELETE CASCADE:** When a transfer record is deleted, both linked shadow transactions are
  automatically removed by the database. This prevents orphaned shadow transactions.
- **Index:** Add an index on `transfer_id` for efficient lookups when updating or deleting a
  transfer's linked transactions.

```sql
CREATE INDEX idx_transactions_transfer
    ON budget.transactions(transfer_id)
    WHERE transfer_id IS NOT NULL;
```

The Transaction model gains the `transfer_id` column and a relationship to the Transfer model.

### 3.3 budget.transfers -- Add category_id Column

Add one nullable foreign key column to the existing `budget.transfers` table:

```sql
category_id INT REFERENCES budget.categories(id)
```

- **Nullable:** Existing transfers created before this change (during re-entry after database
  rebuild) may not have a category. New transfers should be encouraged but not forced to have
  one.
- **Purpose:** The shadow expense transaction inherits this category so it appears in the
  correct spending category for charts and reports. For example, a transfer from checking to the
  mortgage account is assigned category "Home: Mortgage Payment." The shadow expense transaction
  in the checking account grid appears under that category.
- **Shadow income transaction category:** The income-side shadow transaction gets a separate
  category. This could be a generic "Transfer In" category or a specific one. The design
  recommendation is to create a standard "Transfers: Incoming" category during seeding and use
  it as the default for all income-side shadow transactions. The user can override this per
  transfer if desired.

### 3.4 budget.transfer_templates -- Add category_id Column

Add the same nullable foreign key column to the existing `budget.transfer_templates` table:

```sql
category_id INT REFERENCES budget.categories(id)
```

- **Purpose:** When the recurrence engine generates a transfer from a template, the transfer
  inherits the template's category_id. This ensures all recurring instances of a transfer are
  categorized consistently.

### 3.5 No Changes to Other Tables

The `budget.transaction_templates` table is not modified. Transaction templates and transfer
templates remain separate tables. They serve different purposes, collect different form data, and
have different recurrence engine paths. Consolidation is not part of this rework.

The `ref.statuses` table is not modified. Shadow transactions use the same statuses as regular
transactions. The transfer's status and the shadow transactions' statuses are kept in sync by the
transfer service.

---

## 4. Transfer Service

### 4.1 Overview

A new or heavily modified service file handles all transfer mutations. Every code path that
creates, updates, or deletes a transfer must go through this service. No code outside this service
should directly insert, update, or delete rows in `budget.transfers` if the intent is to create a
functional transfer. (The exception is the transfer recurrence engine, which is discussed in
section 5. Even there, it must call this service rather than inserting directly.)

The service ensures atomicity: the transfer record and both shadow transactions are always created,
updated, or deleted together in a single database transaction. If any part fails, everything rolls
back.

### 4.2 create_transfer

**Input:** from_account_id, to_account_id, pay_period_id, scenario_id, amount, status_id,
category_id (optional), notes (optional), template_id (optional, for recurrence-generated
transfers).

**Behavior:**

1. Validate inputs (accounts exist, belong to user, from != to, period exists, etc.).
2. Insert one row into `budget.transfers` with the provided fields.
3. Insert an **expense** transaction into `budget.transactions`:
   - `account_id`: The from_account_id (the account money is leaving).
   - `template_id`: NULL (shadow transactions are not template-generated; they are
     transfer-generated).
   - `pay_period_id`: Same as the transfer.
   - `scenario_id`: Same as the transfer.
   - `status_id`: Same as the transfer.
   - `name`: Generated from the transfer context. Format: `"Transfer to {to_account.name}"`.
     Claude Code should verify the current naming convention used for transfers and match it
     if a convention already exists.
   - `category_id`: The transfer's category_id. May be NULL if the transfer has no category.
   - `transaction_type_id`: The ID for "expense" from `ref.transaction_types`.
   - `estimated_amount`: The transfer amount.
   - `actual_amount`: NULL initially. Set when the transfer is marked done/paid.
   - `is_override`: FALSE.
   - `is_deleted`: FALSE.
   - `transfer_id`: The ID of the transfer record just created.
4. Insert an **income** transaction into `budget.transactions`:
   - Same structure as above but mirrored:
   - `account_id`: The to_account_id (the account money is entering).
   - `name`: `"Transfer from {from_account.name}"`
   - `category_id`: A default "Transfers: Incoming" category, or the user's choice if the
     transfer form allows specifying a separate income-side category. The simplest initial
     approach is to use a single standard category for all transfer income. This can be
     refined later.
   - `transaction_type_id`: The ID for "income" from `ref.transaction_types`.
   - `transfer_id`: Same transfer ID.
5. Flush and commit. Return the created transfer record.

**Important:** The `template_id` on the shadow transactions must be NULL, not the transfer
template's ID. The `budget.transaction_templates` table and the `budget.transfer_templates` table
are separate. A transfer template ID is not a valid foreign key for `budget.transactions.template_id`
which references `budget.transaction_templates`. Setting it would violate the FK constraint.

### 4.3 update_transfer

**Input:** transfer_id, and any combination of: amount, status_id, pay_period_id, category_id,
notes, actual_amount.

**Behavior:**

1. Load the transfer record. Verify ownership.
2. Update the transfer record with the provided fields.
3. Find both linked transactions (`WHERE transfer_id = ?`).
4. Update both transactions to match:
   - If amount changed: update `estimated_amount` on both shadows.
   - If actual_amount provided: update `actual_amount` on both shadows.
   - If status_id changed: update `status_id` on both shadows.
   - If pay_period_id changed: update `pay_period_id` on both shadows.
   - If category_id changed: update `category_id` on the expense-side shadow. The income-side
     shadow keeps its own category (typically "Transfers: Incoming").
   - If notes changed: update `notes` on the transfer. Shadow transactions do not need
     individual notes; the transfer's notes are displayed when the transfer edit form is opened.
5. Flush and commit. Return the updated transfer record.

**Status sync rule:** The transfer's status and both shadow transactions' statuses are always
identical. There is no scenario where a shadow transaction has a different status from its parent
transfer. This invariant is enforced by the service: any status change goes through
`update_transfer`, which updates all three records.

### 4.4 delete_transfer

**Input:** transfer_id.

**Behavior:**

1. Load the transfer record. Verify ownership.
2. Delete the transfer record.
3. The `ON DELETE CASCADE` on `budget.transactions.transfer_id` automatically deletes both
   shadow transactions.
4. Commit.

**Note:** If the codebase uses soft-delete for transfers (an `is_deleted` flag), the service
must also soft-delete both shadow transactions. Claude Code should verify whether transfers use
soft-delete or hard-delete and implement accordingly. If soft-delete, set `is_deleted = TRUE` on
the transfer and both shadow transactions. Do not rely on CASCADE for soft-delete; it must be
explicit.

### 4.5 Invariants

These invariants must hold at all times. They should be verified by tests.

1. **Every transfer with status other than cancelled/deleted has exactly two linked transactions.**
   One expense-type, one income-type. If either is missing, the data is corrupt.
2. **Shadow transactions are never orphaned.** Deleting a transfer removes its shadows. Creating
   a transfer always creates both shadows.
3. **Shadow transaction amounts always equal the transfer amount.** The expense shadow's
   `estimated_amount` equals `transfer.amount`. The income shadow's `estimated_amount` equals
   `transfer.amount`. If the transfer has an `actual_amount`, both shadows have the same
   `actual_amount`.
4. **Shadow transaction statuses always equal the transfer status.** No divergence.
5. **Shadow transaction periods always equal the transfer period.** No divergence.
6. **Shadow transactions are read-only from the user's perspective.** No code path should allow
   a user to directly edit a transaction where `transfer_id IS NOT NULL` through the regular
   transaction edit route. The transaction edit route must check for `transfer_id` and redirect
   to the transfer edit flow.
7. **No double-counting in balance calculations.** After this rework, the balance calculator
   queries only `budget.transactions`. It must NOT also query `budget.transfers`. If both are
   counted, every transfer is double-counted and all balances are wrong.

---

## 5. Transfer Recurrence Engine Changes

### 5.1 Current Behavior

The transfer recurrence engine (file: `app/services/transfer_recurrence.py`) generates transfer
records from `budget.transfer_templates` based on their associated recurrence rules. It currently
inserts directly into the `budget.transfers` table.

### 5.2 Required Change

The transfer recurrence engine must call `transfer_service.create_transfer(...)` instead of
directly inserting transfer records. This ensures that every recurrence-generated transfer also
creates its two linked shadow transactions.

**This is the minimal change to the recurrence engine.** The recurrence logic itself (pattern
matching, period calculation, effective dates, end dates, override handling) does not change. Only
the final step of creating the transfer record changes from a direct insert to a service call.

### 5.3 One-Time Transfers

The one-time transfer bug (section 1.4) is automatically fixed by this change. One-time transfers
that were previously created without going through the recurrence engine's generation path will now
go through the transfer service, which always creates the linked shadow transactions. Claude Code
should verify during the inventory phase exactly where one-time transfers are created (which route,
which function) and confirm that the code path routes through the transfer service after this
rework.

### 5.4 Transfer Template Category

When the recurrence engine creates a transfer from a template, it passes the template's
`category_id` to the transfer service. This ensures all recurring instances inherit the template's
category assignment.

---

## 6. Balance Calculator Changes

### 6.1 Current Behavior

The balance calculator (`app/services/balance_calculator.py`) currently queries both
`budget.transactions` and `budget.transfers` to compute account balances. Transfers are handled
as a separate data source with separate logic: transfers from the account reduce the balance,
transfers to the account increase it.

### 6.2 Required Change

Remove all transfer-specific query logic from the balance calculator. The calculator should query
only `budget.transactions`. Shadow transactions linked to transfers are regular transaction rows
with the correct `transaction_type_id` (expense or income) and the correct amounts. They
participate in the balance calculation identically to any other transaction.

**This is the single highest-risk change in the rework.** If the balance calculator still counts
transfers AND also counts shadow transactions, every transfer is double-counted. If it stops
counting transfers but shadow transactions are not correctly created, transfers are not counted at
all. The changeover must be atomic: the old transfer logic is removed in the same commit that the
new shadow transaction logic is verified to work.

### 6.3 Verification Strategy

Before removing transfer logic from the balance calculator:

1. Create a test that sets up a known scenario with transfers.
2. Calculate balances using the current (dual-path) calculator.
3. Record the expected balances.
4. Create the shadow transactions for those transfers.
5. Calculate balances using the new (transaction-only) calculator.
6. Assert that the balances match exactly.

This test proves that the shadow transactions produce identical balance results to the old
transfer path before the old path is removed.

---

## 7. Grid Rendering Changes

### 7.1 Current Behavior

The grid (`app/routes/grid.py` and `app/templates/grid/grid.html`) renders three sections in the
`<tbody>`:

1. **INCOME section** with banner row `section-banner-income`. Rows grouped by category.
2. **EXPENSES section** with banner row `section-banner-expense`. Rows grouped by category.
3. **TRANSFERS section** (conditional) with banner row `section-banner-transfer`. Rows grouped by
   transfer template name.

Transfer cells use separate templates: `transfers/_transfer_cell.html` and
`transfers/_transfer_full_edit.html`. Transfer HTMX interactions use separate routes (the
implementation plan documents this as GI-19).

### 7.2 Required Changes

**Remove the TRANSFERS section entirely.** After the rework, there are no transfer rows to render
separately. Transfer-linked transactions appear as regular transactions in the INCOME and EXPENSES
sections, grouped by their category like any other transaction.

**Visual transfer indicator.** Shadow transactions should be visually distinguishable from regular
transactions so the user can see at a glance that an item is a transfer. Implementation options
(Claude Code should choose the approach that best fits the existing CSS patterns):

- A small Bootstrap Icon (e.g., `bi-arrow-left-right`) next to the amount in the cell.
- A subtle left-border accent color on the cell or row.
- A CSS class `is-transfer` on the transaction cell that applies a distinct but subtle visual
  treatment.

The indicator should be obvious enough to notice during scanning but not so prominent that it
distracts from the amount and status, which are the primary data during the payday workflow.

**Cell click routing for transfer-linked transactions.** When the user clicks a shadow transaction
cell:

- The **quick edit** interaction should work normally for amount changes. The quick edit submits
  a PATCH to the transaction, but the transaction route must detect `transfer_id IS NOT NULL`
  and route the update through the transfer service (which updates the transfer and both
  shadows).
- The **full edit** interaction (expand button or F2) must return the **transfer edit form**
  instead of the transaction edit form. The full edit route detects `transfer_id IS NOT NULL`,
  loads the parent transfer, and renders the transfer edit form. This allows the user to see
  and edit the from_account, to_account, amount, status, and notes.

Claude Code should examine the existing transaction quick edit and full edit routes to determine
the cleanest way to add the transfer detection branch. The goal is minimal changes to the existing
transaction route code: a check at the top of each relevant handler that redirects to the transfer
flow when `transfer_id` is present.

### 7.3 Footer Changes

The current footer (`app/templates/grid/_balance_row.html`) contains 4-5 rows: Total Income, Total
Expenses, Net Transfers (conditional), Net (Income - Expenses), and Projected End Balance.

After this rework:

- **Total Income** and **Total Expenses** become inline subtotal rows in the grid body, placed
  at the bottom of their respective sections (after the last income row and after the last
  expense row). These are `<tr>` elements in the `<tbody>`, not in the `<tfoot>`. They should
  be styled distinctly (bold, background color) but remain part of the scrollable grid content.
  They include transfer-linked transaction amounts because those are now regular transactions.
- **Net Transfers** row is removed. Transfers are included in the income and expense totals.
- **Net Cash Flow** becomes a row in the `<tbody>` after the expense subtotal row (or after a
  spacer row following the expense section). It is not sticky. It scrolls with the grid content.
  It shows: Total Income - Total Expenses for each period. This is a secondary reference number.
- **Projected End Balance** remains in the `<tfoot>` as the only sticky footer row. This is
  the primary number the user checks during the payday workflow.

The footer reduction from 4-5 sticky rows to 1 sticky row frees approximately 90-140px of
vertical space on a 1080p display, allowing 3-4 more transaction rows to be visible without
scrolling.

### 7.4 HTMX Interaction Impact

Claude Code must verify every HTMX interaction documented in `implementation_plan_section4.md`
(GI-1 through GI-21) against the grid changes. The key interactions affected are:

- **GI-10 (Balance row refresh):** The `<tfoot id="grid-summary">` structure changes (fewer
  rows). The `hx-trigger="balanceChanged from:body"` mechanism is unchanged. The endpoint that
  returns the tfoot partial needs to return the new structure. Since the swap is `outerHTML` on
  `#grid-summary`, the new tfoot replaces the old one entirely. This is safe.
- **GI-19 (Transfer cell click):** This interaction is retired. Transfer-linked transactions
  use the regular transaction cell click interaction (GI-1) with transfer detection.
- **GI-18 (Keyboard navigation):** The `getDataRows()` function filters out rows with certain
  CSS classes. The new subtotal rows and Net Cash Flow row must have CSS classes that are added
  to the exclusion list so they are not keyboard-navigable. Claude Code should verify the
  exact class names used in the exclusion list and ensure the new rows are excluded.
- **All other interactions:** Should be unaffected because they target specific transaction
  cells by ID (`#txn-cell-<id>`), which does not change. Claude Code should verify.

---

## 8. Chart and Reporting Changes

### 8.1 Current Behavior

The chart data service (`app/services/chart_data_service.py`) queries `budget.transactions`
grouped by category to produce spending breakdowns. Transfers are not included because they are
in a separate table.

### 8.2 Required Changes

After the rework, no changes may be needed. Transfer-linked transactions are regular transaction
rows with `category_id` values. The existing category-based queries should automatically include
them.

Claude Code should verify by reading the chart data service and confirming:

1. The queries select from `budget.transactions` (not from a joined view that might exclude
   transfer-linked rows).
2. There is no WHERE clause that would accidentally exclude transactions where
   `transfer_id IS NOT NULL`.
3. There is no existing transfer-specific chart logic that needs to be removed or updated.

If the chart data service currently has separate transfer handling (a parallel query of
`budget.transfers` for chart data), that logic should be removed. Shadow transactions make it
redundant.

---

## 9. Transfer CRUD Route Changes

### 9.1 Current Behavior

Transfer CRUD is handled by routes in the transfers blueprint (likely `app/routes/transfers.py`).
These routes handle creating, reading, updating, and deleting transfers and transfer templates.
They also handle the transfer grid interactions (cell click, quick edit, full edit, mark done).

### 9.2 Required Changes

**Create transfer route:** Must call `transfer_service.create_transfer(...)` instead of directly
inserting a transfer record. The form should include a category dropdown (optional) for assigning
the transfer to a spending category.

**Update transfer route:** Must call `transfer_service.update_transfer(...)` instead of directly
updating the transfer record.

**Delete transfer route:** Must call `transfer_service.delete_transfer(...)` instead of directly
deleting the transfer record.

**Mark done / mark paid route:** Must call `transfer_service.update_transfer(...)` with the new
status. This ensures the status change propagates to both shadow transactions.

**Transfer template CRUD:** The template creation and editing forms should include a category
dropdown. When a template is created or edited, the `category_id` is saved on the template. When
the recurrence engine generates transfers from this template, the category is inherited.

**Routes that can be retired or simplified:** Any route that exists solely to render transfer
cells in the grid (e.g., a `/transfers/<id>/cell` endpoint) may be retirable. Shadow transactions
use the regular transaction cell templates and routes. Claude Code should verify which transfer
routes are grid-specific vs. CRUD-specific and determine which can be retired.

---

## 10. Transaction Route Changes

### 10.1 Transfer Detection

The transaction routes that handle quick edit, full edit, mark done, and update must detect
when the target transaction is a shadow transaction (`transfer_id IS NOT NULL`). The behavior
for each:

- **Quick edit (amount change):** When a user edits the amount on a shadow transaction via
  quick edit, the route should call `transfer_service.update_transfer(transfer_id, amount=new_amount)`
  instead of directly updating the transaction. This ensures both shadows are updated.
  Alternatively, the quick edit could be disallowed for shadow transactions (forcing the user
  to use the full transfer edit form). Claude Code should evaluate which approach is more
  consistent with existing UX patterns. If regular transactions support quick edit for amount,
  shadow transactions should too, for consistency.
- **Full edit (expand):** Returns the transfer edit form (from `transfers/` templates) instead
  of the transaction full edit form. The response is the transfer edit popover, pre-populated
  with the parent transfer's data.
- **Mark done / mark paid:** Calls `transfer_service.update_transfer(transfer_id, status_id=done_status)`
  instead of directly updating the transaction status.
- **Direct update (PATCH):** Must detect `transfer_id` and route through the transfer service.
  Must NOT allow the user to change the amount, status, or period of a shadow transaction
  independently of its parent transfer.
- **Delete:** Must NOT allow direct deletion of a shadow transaction. If the user wants to
  remove a transfer, they must delete the transfer (which cascades to both shadows). The
  delete route should detect `transfer_id` and either redirect to the transfer delete flow
  or return an error.

### 10.2 Guard Against Direct Shadow Mutation

Add a defensive check at the top of each transaction mutation route (update, delete, mark done,
mark credit, cancel). If the transaction has `transfer_id IS NOT NULL`, the route must not
proceed with a direct transaction mutation. It must either:

- Route the mutation through the transfer service (preferred for amount and status changes).
- Return an error response (for operations that do not make sense for shadow transactions, such
  as marking a shadow transaction as "credit" since transfers cannot be put on a credit card).

The specific operations that should be blocked for shadow transactions:

- **Mark credit:** A transfer cannot be put on a credit card. This operation makes no sense for
  shadow transactions. Block it and do not show the "Credit" button in the UI for
  transfer-linked transactions.
- **Cancel:** Cancelling a shadow transaction without cancelling the parent transfer would
  violate the sync invariant. If the user wants to cancel a transfer, they should cancel
  the transfer itself (which cancels both shadows).
- **Move to different period:** Moving a shadow transaction to a different period without
  moving the parent transfer and the other shadow violates the sync invariant. Period changes
  must go through the transfer service.

---

## 10A. Carry Forward Behavior for Shadow Transactions

### 10A.1 Current Behavior

The carry forward service finds all transactions in a past period with status "projected" and
moves them to the current period by updating their `pay_period_id`. If any carried-forward
transactions were auto-generated from a recurrence rule, they are flagged as `is_override = TRUE`.

### 10A.2 Required Behavior After Rework

When carry forward encounters a shadow transaction (`transfer_id IS NOT NULL`), it must not move
the shadow transaction directly. Moving one shadow without moving its sibling and the parent
transfer would violate the sync invariant (section 4.5, invariant 5: shadow transaction periods
always equal the transfer period).

**Carry forward algorithm for shadow transactions:**

1. Find all transactions in the target period with status "projected."
2. Partition into two groups:
   - **Regular transactions** (`transfer_id IS NULL`): moved directly, same as today.
   - **Shadow transactions** (`transfer_id IS NOT NULL`): collected for transfer-level handling.
3. From the shadow transactions, extract the distinct set of `transfer_id` values. This
   de-duplicates: each transfer has two shadows in the period, but we only want to move the
   transfer once.
4. For each distinct `transfer_id`, call
   `transfer_service.update_transfer(transfer_id, pay_period_id=current_period_id)`. This moves
   the parent transfer and both shadow transactions atomically.

**Edge case -- partial shadow presence:** If only one shadow of a transfer is in the period
being carried forward (the other shadow is in a different period), this indicates a data
integrity violation. The invariant says both shadows and the parent transfer share the same
period. If this state is detected, the carry forward should log a warning and still route
through the transfer service, which will correct both shadows to match the parent transfer's
new period.

**Override flag:** When a shadow transaction is moved by carry forward, the parent transfer
should also be flagged as overridden if the transfer recurrence engine uses an equivalent
concept. Claude Code should verify whether `budget.transfers` has an `is_override` column
and handle accordingly. If transfers do not have override tracking, the shadow transactions'
`is_override` flag is set by the transfer service during the period update.

### 10A.3 Impact on Carry Forward Service

The carry forward service is modified. It is NOT in the "does not change" list. The change is
localized: add a transfer detection branch that partitions transactions and routes shadow
transaction period changes through the transfer service. The rest of the carry forward logic
(finding projected transactions, the user prompt flow, the override flagging for regular
transactions) is unchanged.

---

## 11. Template Management Changes

### 11.1 Transfer Template Form

The existing transfer template create and edit forms need a **category dropdown** added. This
allows the user to assign a spending category when setting up a recurring transfer. The category
is stored on `budget.transfer_templates.category_id` and inherited by every transfer instance
generated from the template.

### 11.2 Transaction Template -- No Changes

The `budget.transaction_templates` table and its CRUD are not modified by this rework. Transaction
templates are a separate system from transfer templates and remain so.

---

## 12. Seed Script and Ref Data Changes

### 12.1 Default Transfer Categories

The seed script that populates `budget.categories` for a new user should include default
categories for transfers. Recommended:

- `Transfers: Incoming` -- default category for income-side shadow transactions.
- `Transfers: Outgoing` -- optional default for expense-side shadow transactions that the user
  has not categorized into a specific spending category.

These are suggestions. The user can override the category on any transfer. The point is to have
sensible defaults so that uncategorized transfers still appear in a logical place on charts rather
than being uncategorized.

Claude Code should verify how categories are currently seeded and add the transfer defaults
following the existing pattern.

### 12.2 No Ref Table Changes

No new entries in `ref.transaction_types`, `ref.statuses`, `ref.recurrence_patterns`, or any
other ref table. Shadow transactions use the existing "income" and "expense" transaction types
and the existing status values.

---

## 13. What Does NOT Change

This section explicitly lists things that are out of scope for this rework. Claude Code should
not modify these unless a dependency is discovered during the inventory phase (in which case,
document it and get approval before proceeding).

- **`budget.transaction_templates` table and model.** No schema changes, no new columns, no
  modified behavior.
- **`budget.recurrence_rules` table and model.** No changes.
- **`app/services/recurrence_engine.py` (the transaction recurrence engine).** This handles
  transaction templates, not transfer templates. It is not affected by this rework.
- **`app/services/credit_workflow.py`.** The credit workflow (mark as credit, auto-generate
  payback) operates on regular transactions. Shadow transactions are explicitly excluded from
  the credit workflow (section 10.2). No changes needed.
- **`ref.statuses` table.** No changes to the status values or the planned `display_label`
  column (Section 4 Task 4.4 in the roadmap).
- **Authentication, session management, user settings, salary/paycheck system, scenario system.**
  None of these are affected.
- **Mortgage parameters, escrow, amortization engine, HYSA parameters, investment parameters,
  pension profiles.** None of these are affected. The mortgage payment is modeled as a transfer
  from checking to the mortgage account. The transfer itself is what changes; the mortgage
  parameter system does not.
- **Account anchor balance and true-up workflow.** The anchor balance is set manually by the
  user and represents the real bank balance. The balance calculator uses it as the starting
  point. This workflow is unchanged.

---

## 14. File Impact Summary

This section lists the areas of the codebase affected. **Exact file paths, function names, and
line numbers must be verified by Claude Code during the inventory phase.** The categories below
are based on the project structure documented in `project_requirements_v2.md` and references in
`implementation_plan_section4.md`.

### 14.1 Models (schema/ORM changes)

- **Transaction model** -- Add `account_id` column (NOT NULL FK to accounts), `transfer_id`
  column (nullable FK to transfers), and relationships to Account and Transfer models.
- **Transfer model** -- Add `category_id` column and relationship.
- **Transfer template model** -- Add `category_id` column and relationship.

### 14.2 Services (business logic changes)

- **Transfer service** -- New or heavily modified. Core of this rework (create, update, delete
  with linked shadow transactions).
- **Transfer recurrence engine** -- Modified to call transfer service instead of direct insert.
- **Transaction recurrence engine** -- Modified to set `account_id` on generated transactions
  (copied from `template.account_id`).
- **Balance calculator** -- Remove transfer-specific query and calculation logic.
- **Chart data service** -- Verify no changes needed; remove transfer-specific chart logic if
  it exists.
- **Carry forward service** -- Modified. Add transfer detection branch that routes shadow
  transaction period changes through the transfer service (section 10A).
- **Credit workflow** -- Verify that shadow transactions are excluded from credit marking. No
  functional changes expected; the guard is in the transaction routes.

### 14.3 Routes (HTTP handler changes)

- **Transfer routes** -- Modified to call transfer service. Add category to forms.
- **Transaction routes** -- Add transfer detection guards on mutation endpoints.
- **Grid route** -- Remove transfer section rendering. Add subtotal row data. Modify footer
  data.

### 14.4 Templates (Jinja2/HTML changes)

- **`grid/grid.html`** -- Remove TRANSFERS section. Add subtotal rows. Add Net Cash Flow row.
- **`grid/_balance_row.html`** -- Reduce to Projected End Balance only.
- **`grid/_transaction_cell.html`** -- Add transfer indicator for shadow transactions.
- **`grid/_transaction_full_edit.html`** -- Add transfer detection (show transfer edit form
  when transfer_id is present).
- **`transfers/_transfer_cell.html`** -- Likely retired or reduced in scope.
- **`transfers/_transfer_full_edit.html`** -- Kept for the transfer edit popover (opened when
  a shadow transaction is clicked).
- **Transfer template create/edit forms** -- Add category dropdown.
- **Transfer create form** -- Add category dropdown.

### 14.5 Static assets

- **`app/static/css/app.css`** -- Add `.is-transfer` indicator styling. Add subtotal row
  styling. Modify footer styling.
- **`app/static/js/`** -- Verify keyboard navigation exclusion list includes new row classes.
  Verify popover logic handles transfer detection.

### 14.6 Tests

Every modified service, route, and template needs corresponding test updates. Additionally:

- **Transfer service tests** -- New. Comprehensive coverage of create, update, delete, and all
  invariants from section 4.5.
- **Balance calculator tests** -- Modified. Remove transfer-specific test paths. Add tests that
  verify shadow transactions produce correct balances. Add the dual-path verification test from
  section 6.3.
- **Grid rendering tests** -- Modified. Verify shadow transactions appear in income/expense
  sections. Verify TRANSFERS section is absent. Verify subtotal rows. Verify footer structure.
- **Transfer route tests** -- Modified. Verify routes call transfer service. Verify category
  handling.
- **Transaction route tests** -- Modified. Verify transfer detection guards. Verify shadow
  transactions cannot be directly mutated.
- **Transfer recurrence tests** -- Modified. Verify generated transfers have linked shadow
  transactions.
- **Chart data tests** -- Verify category totals include transfer-linked transactions.
- **Carry forward tests** -- Verify transfer-linked transactions are handled correctly.
- **HTMX regression tests** -- Verify all grid interactions work with the new structure.

### 14.7 Seed scripts

- Add default transfer categories ("Transfers: Incoming", "Transfers: Outgoing").

### 14.8 Alembic migrations

Since the database is being dropped and recreated, the schema changes can be applied to the
initial migration files rather than creating new migration files. However, if the project
convention is to always create forward migrations even for fresh databases, Claude Code should
follow that convention. Verify the project's migration strategy during inventory.

---

## 15. Implementation Sequencing

The work is split into two phases within Section 3A. Phase I is the core architectural rework
(shadow transactions, balance calculator, grid rendering). Phase II is the grid subtotal and
footer work that builds on the completed architecture. Phase I must be fully completed and
verified before Phase II begins.

This split allows the core shadow transaction system to be verified independently. During
Phase I, the existing footer (Total Income, Total Expenses, Net Transfers, Net, Projected End
Balance) remains in place as a familiar reference point for verifying that balances are correct.
The footer is condensed only in Phase II after the architecture is proven.

### Phase 3A-I: Shadow Transaction Architecture

The tasks below are ordered by dependency. Each task must be completed and verified before the
next begins. Claude Code should translate these into atomic commits during the implementation
planning phase.

1. **Schema changes: account_id.** Add `account_id` (NOT NULL FK to accounts) to the
   Transaction model. Update every code path that creates a transaction to set `account_id`
   (recurrence engine copies from template, credit workflow copies from original transaction,
   manual creation infers from context). Update queries that currently join through templates
   to get account. Generate migration. Test that all existing transaction creation paths set
   account_id correctly.
2. **Schema changes: transfer_id and category_id.** Add `transfer_id` (nullable FK with CASCADE)
   to Transaction model. Add `category_id` (nullable FK) to Transfer and TransferTemplate
   models. Generate migration. Verify models load and basic queries work.
3. **Seed script updates.** Add default transfer categories ("Transfers: Incoming",
   "Transfers: Outgoing"). Test fresh database seeding.
4. **Transfer service.** Write `create_transfer`, `update_transfer`, `delete_transfer` with
   full test coverage including all invariants from section 4.5. Test in isolation (no route
   changes yet).
5. **Wire transfer recurrence engine.** Modify to call transfer service instead of direct
   insert. Test that recurring transfers produce linked shadow transactions. Test one-time
   transfers specifically.
6. **Wire transfer routes.** Modify create, update, delete, mark-done to call transfer service.
   Add category to transfer and transfer template forms. Test that API calls produce correct
   results.
7. **Transaction route guards.** Add transfer detection to transaction mutation routes. Test
   that shadow transactions cannot be directly mutated. Test that full-edit returns transfer
   form for shadow transactions. Test that credit action is blocked for shadow transactions.
8. **Carry forward.** Modify carry forward service to detect shadow transactions and route
   period changes through the transfer service (section 10A). Test carry forward with mixed
   regular and shadow transactions. Test the de-duplication (two shadows per transfer, only
   one transfer service call). Test the partial-shadow edge case.
9. **Balance calculator.** Remove transfer-specific logic. Run the dual-path verification test
   (section 6.3) to confirm shadow transactions produce identical balances to the old transfer
   path. Run full balance test suite.
10. **Grid rendering.** Remove TRANSFERS section. Render shadow transactions in income/expense
    sections with visual transfer indicator. Update keyboard navigation exclusion list for any
    new row classes. Run grid tests and HTMX regression tests. Run the full payday workflow
    manually to verify.
11. **Chart and reporting verification.** Verify category totals include transfer-linked
    transactions. Remove any transfer-specific chart logic. Test charts.
12. **Cleanup.** Retire unused transfer grid templates. Remove dead code paths. Run full test
    suite: `pytest -v --tb=short`. All tests must pass.

**Phase 3A-I Gate:** Before proceeding to Phase II, the full test suite must pass and the payday
workflow must be manually verified end-to-end: true-up balance, mark paycheck received, carry
forward unpaid (including a transfer), mark expenses paid (including a transfer-linked expense),
check projections. Balances must be correct.

### Phase 3A-II: Grid Subtotals and Footer Condensation

These tasks build on the completed Phase I. The grid already shows shadow transactions in the
income/expense sections. These tasks add summary rows and condense the footer.

13. **Inline subtotal rows.** Add Total Income and Total Expenses subtotal rows at the bottom
    of their respective sections in the grid `<tbody>`. These are styled distinctly (bold,
    background color) and included in the keyboard navigation exclusion list. Amounts include
    transfer-linked transactions (since they are now regular transactions). Test calculations
    and rendering. Verify HTMX balance row refresh is not affected (subtotals are in tbody,
    not tfoot).
14. **Net Cash Flow row.** Add a Net Cash Flow row in the `<tbody>` after the expense section
    (Total Income - Total Expenses). Non-sticky. Scrolls with grid content. Add to keyboard
    navigation exclusion list. Test.
15. **Footer condensation.** Reduce `<tfoot>` to Projected End Balance only. Remove Total
    Income, Total Expenses, Net Transfers, and Net (Income - Expenses) rows from the tfoot.
    Update the balance row endpoint to return the new structure. Test footer rendering and
    HTMX refresh (GI-10). Verify the `hx-trigger="balanceChanged from:body"` still works on
    the new single-row tfoot.
16. **Database rebuild.** Drop production database. Run all migrations. Run seed scripts.
    Re-enter data. Verify balances manually against known correct values.

**Phase 3A-II Gate:** Full test suite passes. Manual payday workflow verification. Screenshot
comparison of grid before and after (for the project record).

---

## 16. Risks and Mitigations

### 16.1 Double-Counting Risk (Critical)

**Risk:** If the balance calculator counts both transfer records and shadow transactions, every
transfer is double-counted and all balances are wrong.

**Mitigation:** The dual-path verification test (section 6.3) must pass before the old transfer
logic is removed. The removal of old logic and the reliance on shadow transactions must happen
in a single atomic commit. After the commit, the full balance test suite must pass.

### 16.2 Orphaned Shadow Transactions (High)

**Risk:** A code path creates a transfer without creating shadow transactions, or deletes a
transfer without removing shadows.

**Mitigation:** All transfer mutations go through the transfer service. No code outside the
service directly inserts into or deletes from `budget.transfers`. The inventory phase must
identify every code path that touches the transfers table and verify it routes through the
service. The `ON DELETE CASCADE` on `transfer_id` provides a database-level safety net for
hard deletes.

### 16.3 Direct Shadow Mutation (High)

**Risk:** A user or code path edits a shadow transaction directly, causing it to diverge from
its parent transfer and sibling shadow.

**Mitigation:** Transfer detection guards on all transaction mutation routes (section 10).
Tests that attempt direct shadow mutation and verify it is blocked or routed through the
transfer service.

### 16.4 Carry Forward Sync (Medium)

**Risk:** Carry forward moves a shadow transaction to a new period without moving the parent
transfer and sibling shadow, breaking the sync invariant.

**Mitigation:** Explicit carry forward behavior specified in section 10A. The carry forward
service detects shadow transactions, de-duplicates by transfer_id, and routes period changes
through the transfer service. Tests verify the complete flow including the partial-shadow edge
case.

### 16.5 Credit Workflow on Shadow Transactions (Medium)

**Risk:** A user marks a shadow transaction as "credit" (charged to credit card instead of
checking). This does not make sense for transfers and would generate an orphaned payback
transaction.

**Mitigation:** Block the credit action on shadow transactions (section 10.2). Do not render
the "Credit" button in the UI for transfer-linked transactions.

### 16.6 Recurrence Regeneration (Medium)

**Risk:** When a transfer template's recurrence rule is changed and transfers are regenerated,
the old shadow transactions are not cleaned up.

**Mitigation:** The transfer recurrence engine currently deletes old transfer records before
regenerating. The `ON DELETE CASCADE` (or explicit soft-delete logic) on shadow transactions
ensures they are removed when their parent transfers are removed. Claude Code should verify
this cascade behavior during the inventory phase.

---

## 17. Success Criteria

The rework is complete when all of the following are true:

1. **All existing tests pass.** No regressions.
2. **The TRANSFERS section is absent from the grid.** Transfer-linked transactions appear in
   the INCOME and EXPENSES sections with a visual indicator.
3. **The footer contains only Projected End Balance (sticky).** Total Income and Total Expenses
   are inline subtotal rows in the grid body. Net Cash Flow is a non-sticky row in the grid
   body.
4. **Category-based charts include transfer expenses.** A mortgage transfer assigned to
   "Home: Mortgage Payment" appears in the Home category on spending charts.
5. **One-time transfers work.** Creating a one-time transfer produces a transfer record and two
   shadow transactions that appear in the grid and affect balances.
6. **The payday workflow is functional.** True-up balance, mark paycheck received, mark expenses
   paid (including transfer expenses), carry forward unpaid, check projections -- all steps
   work without errors.
7. **Balance accuracy.** Given a known set of transactions and transfers, the projected end
   balance for each period matches the manually calculated expected value.
8. **No double-counting.** Balances with the new system match balances calculated from the old
   system for the same data set.
9. **Transfer service invariants hold.** All five invariants from section 4.5 are verified by
   passing tests.
10. **Full test suite passes.** `pytest -v --tb=short` with zero failures.

---

## 18. Impact on Existing Roadmap and Implementation Plan

### 18.1 Roadmap Position

This rework is inserted into `project_roadmap_v4.md` as **Section 3A: Transfer Architecture
Rework** between Section 3 (Critical Bug Fixes, completed) and Section 4 (UX/Grid Overhaul).

The priority table at the top of the roadmap should be updated:

| Priority | Phase                              | Summary                                                                                                     |
| -------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1        | Critical Bug Fixes                 | COMPLETED                                                                                                   |
| 1A       | Transfer Architecture Rework       | Shadow transactions, balance calculator simplification, grid integration, subtotals and footer condensation |
| 2        | UX/Grid Overhaul                   | Focused sprint to address daily-use friction (minus Task 4.2, absorbed into 1A)                             |
| 3        | Recurring Transaction Improvements | Mortgage auto-payment, recurrence audit                                                                     |
| 4        | Phase 9: Smart Features            | Seasonal forecasting, smart estimates, inflation                                                            |
| 5        | Phase 10: Notifications            | In-app alerts, email delivery                                                                               |
| 6        | Multi-user (far future)            | Not actively planned                                                                                        |

### 18.2 Impact on implementation_plan_section4.md

**Task 4.2 (Footer Condensation) is superseded by Phase 3A-II of this rework.** The original
Task 4.2 condensed the footer from 4-5 rows to 2 rows (Net Cash Flow and Projected End Balance).
Phase 3A-II goes further: it adds inline subtotal rows to the grid body, adds a Net Cash Flow
row to the grid body, and reduces the sticky footer to Projected End Balance only. The original
Task 4.2 also referenced a "Net Transfers" component which no longer exists after the transfer
rework.

The implementation plan should be updated:

- Mark Task 4.2 as "Superseded by Section 3A-II (Transfer Architecture Rework, Phase II)."
- Remove Commit #9 (Task 4.2) from the commit sequence.
- Note that test specs C-4.2-1 through C-4.2-3 have been moved to the Section 3A-II test plan
  with updated assertions (no "Net Transfers" row, different row count expectations, subtotal
  rows present in tbody).

All other tasks in the Section 4 implementation plan (4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9,
4.10) are unaffected by this rework and proceed as planned after Section 3A is complete.

**Note for Task 4.1 (Grid Layout: Category/Transaction Name Clarity):** After Section 3A, the
grid body no longer has a TRANSFERS section. Transfer-linked transactions appear in the income
and expense sections. The row structure analysis in Task 4.1 should account for this. Option A
(full row headers) and Option B (enhanced current layout) both apply to transfer-linked
transactions the same as regular transactions. No special handling needed, but the prototype
should include transfer-linked rows in the test data to verify they render correctly with
whatever layout is chosen.

---

## 19. Change Log

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| ------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2026-03-25 | Initial design. Covers transfer shadow transaction architecture, schema changes, service contract, balance calculator simplification, grid rendering changes, footer condensation, chart integration, HTMX interaction impact, and implementation sequencing.                                                                                                                                                                               |
| 1.1     | 2026-03-25 | Added account_id column to budget.transactions (section 3.1). Specified explicit carry forward behavior for shadow transactions (section 10A). Split implementation into Phase 3A-I (shadow transaction architecture) and Phase 3A-II (grid subtotals and footer condensation). Added roadmap positioning as Section 3A between Sections 3 and 4. Documented that Task 4.2 in implementation_plan_section4.md is superseded by Phase 3A-II. |
