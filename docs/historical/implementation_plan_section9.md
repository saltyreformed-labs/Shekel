> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Section 9 -- Spending Tracker and Companion View

**Version:** 1.0
**Date:** April 11, 2026
**Prerequisite:** Section 8 (Visualization and Reporting Overhaul) complete, including 8A
(calendar, year-end, variance, trends, CSV export, due_date/paid_at, default route swap).
**Scope:** Sub-transaction entry tracking on budget-type transactions with remaining balance
visibility, entry-level credit card workflow with aggregated CC Paybacks, companion user role
with simplified mobile-first view. Tasks 9.1 through 9.7 from the project roadmap. Authoritative
specification: `docs/phase_scope_spending_tracker.md`.

---

## Documentation vs. Code Discrepancies and Open Question Resolution

The following discrepancies and open question resolutions were found by reading the actual
codebase on April 11, 2026. The implementation plan is based on the code, not the documentation.

### D-1: Scope doc says "credit card service" -- actual file is `credit_workflow.py`

**Scope document says (Section 5, task 11.7):** "Extend credit card service."

**Code says:** The credit card logic lives in `app/services/credit_workflow.py` (190 lines), not
a file named `credit_card_service.py`. The file contains `mark_as_credit()` and
`unmark_credit()` functions plus the helper `_get_or_create_cc_category()`.

**Impact:** All references to "credit card service extension" in this plan target
`credit_workflow.py`. A new companion file `entry_credit_workflow.py` handles the entry-level
credit aggregation to keep concerns separated (legacy per-transaction credit vs. new per-entry
credit).

### D-2: Scope doc says "bottom sheet" -- app uses Bootstrap popovers

**Scope document says (Section 6.2, 7.3):** "Tapping an entry-capable transaction opens the
transaction detail / bottom sheet" and "the existing bottom sheet pattern."

**Code says:** No bottom sheet or offcanvas component exists in the codebase. The transaction
detail view is a Bootstrap popover loaded via HTMX (`_transaction_full_edit.html`, 132 lines).
Clicking a grid cell triggers `hx-get` for the quick edit form; the expand button (or F2 key)
opens the full edit popover via JavaScript in `grid_edit.js`.

**Impact:** Entry CRUD on the full grid uses the existing popover pattern with an added "Entries"
section. The companion view uses its own dedicated card-based layout (not a popover) since the
companion interface is a standalone page, not the grid.

### D-3: Scope doc says "tooltip enhancement (task 4.12)" -- tooltips are HTML title attributes

**Scope document says (Section 7.2):** "The existing tooltip (from task 4.12) should be extended."

**Code says:** The "tooltip" is an HTML `title` attribute on the cell div in
`_transaction_cell.html` (line 19). It contains the amount, estimate, status, and notes as a
plain string. Status badges use Bootstrap's `data-bs-toggle="tooltip"` for a richer tooltip on
the badge itself, but the cell-level tooltip is a native browser tooltip.

**Impact:** Enhancement means adding entry data (spent, remaining, entry count, credit total) to
the `title` attribute string and `aria-label`. No tooltip library or component change needed.

### D-4: Scope doc uses `NUMERIC(10,2)` -- app standard is `NUMERIC(12,2)`

**Scope document says (Section 3.1):** `amount: NUMERIC(10,2) NOT NULL`.

**Code says:** Every monetary column in the app uses `Numeric(12, 2)`. This includes
`Transaction.estimated_amount`, `Transaction.actual_amount`, `TransactionTemplate.default_amount`,
`Account.current_anchor_balance`, and all loan/savings/interest columns.

**Impact:** The `TransactionEntry.amount` column uses `Numeric(12, 2)` for consistency with the
rest of the schema. This is a cosmetic discrepancy -- `NUMERIC(10,2)` supports values up to
$99,999,999.99 which is more than sufficient, but `NUMERIC(12,2)` matches the established
pattern and avoids a gratuitous difference.

### D-5: Scope doc says balance calculator changes modify "effective amount"

**Scope document says (Section 4.2):** Describes the `checking_impact` formula as a replacement
for `effective_amount` in the balance calculator context.

**Code says:** `Transaction.effective_amount` (line 130 of `transaction.py`) is used by 7+
services: balance_calculator, dashboard_service, calendar_service, budget_variance_service,
spending_trend_service, year_end_summary_service, and grid subtotal computation. Changing its
semantics for entry-capable transactions would require auditing and potentially modifying all
consumers.

**Impact:** This plan does NOT modify the `effective_amount` property. Instead, the balance
calculator's `_sum_remaining` and `_sum_all` functions call a new `_entry_aware_amount(txn)`
helper that applies the entry formula for expense transactions with loaded entries. All other
consumers continue to use `effective_amount` unchanged: for projected entry-capable transactions,
they see `estimated_amount` (correct for display, bills list, and analytics). For paid
transactions, `actual_amount` is set from the entry sum, so `effective_amount` returns the
correct value.

### D-6: Scope doc says `description` is NOT NULL -- but companion UX may need flexibility

**Scope document says (Section 3.1):** `description: VARCHAR(200) NOT NULL -- Store name or
brief note`.

**Code says:** No existing nullable/non-nullable pattern conflict -- this is a new column.

**Impact:** The plan follows the scope document: `description` is NOT NULL. The companion add
entry form requires the description field. For quick mobile entry, a short description like
"Kroger" or "gas" is expected. If this proves to be too much friction in practice, it can be
changed to nullable in a follow-up migration. However, entries without descriptions are far less
useful for budget review, so the requirement is justified.

### D-7: Transaction CRUD lives in routes, not a service

**Scope document says (task 11.9):** "Extend transaction service."

**Code says:** There is no `transaction_service.py`. Transaction create, update, delete, and
status change operations are handled directly in `app/routes/transactions.py` (654 lines). The
only transaction-related services are `credit_workflow.py` (credit status) and
`transfer_service.py` (shadow transactions).

**Impact:** The mark-as-paid extension (auto-populating actual_amount from entries) is
implemented directly in `routes/transactions.py` `mark_done` function (line 211). Status
prevention (blocking Credit on entry-capable transactions) is implemented both in
`credit_workflow.py` (service-level guard) and `routes/transactions.py` `update_transaction`
(route-level guard for the status dropdown).

### D-8: User model has CHECK constraint pattern but no enum for role

**Scope document says (Section 3.4):** `role: VARCHAR(20) NOT NULL DEFAULT 'owner'
CHECK (role IN ('owner', 'companion'))`.

**Code says:** The app uses ref tables + enums (in `app/enums.py`) + ref_cache (in
`app/ref_cache.py`) for all categorical values.

**Impact:** The plan uses a `ref.user_roles` table with a `RoleEnum` in `app/enums.py` and a
`role_id()` accessor in `ref_cache.py`, following the established pattern for all categorical
values. The scope document's VARCHAR CHECK approach is replaced with an integer FK to the ref
table. This follows the project's "IDs for logic, strings for display" rule consistently and
avoids creating technical debt that Section 10 (Multi-User) would need to undo. Adding future
roles (kid, partner) becomes a ref table INSERT and an enum member -- not a migration that drops
and recreates a CHECK constraint. The overhead is minimal: one 2-row ref table, one enum class,
one cache accessor, and one seed entry.

---

### Open Question Resolutions

### OQ-1: Does a credit card service exist? Where does CC Payback logic live?

**Answer:** The credit card logic lives in `app/services/credit_workflow.py` (190 lines).
`mark_as_credit(transaction_id, user_id)` sets status to CREDIT, creates a CC Payback
transaction in the next period using `pay_period_service.get_next_period()`. The payback is a
regular Transaction with `template_id=None`, `status=PROJECTED`,
`name="CC Payback: {original_name}"`, `category="Credit Card: Payback"` (found or created via
`_get_or_create_cc_category()`), and `credit_payback_for_id=original_txn.id`. The balance
calculator excludes Credit-status transactions via `status.excludes_from_balance == True`
(handled in the `effective_amount` property, line 148).

**Impact:** Entry-level credit workflow creates a parallel service
(`entry_credit_workflow.py`) that manages aggregated CC Paybacks per parent transaction. The
legacy `mark_as_credit` flow remains unchanged for non-entry-capable transactions but gains a
guard rejecting entry-capable transactions.

### OQ-2: What is the current User model structure?

**Answer:** `app/models/user.py` (144 lines) defines three classes:
- **User** (schema: auth): `id`, `email` (unique), `password_hash`, `display_name`, `is_active`
  (default True), `session_invalidated_at`, timestamps. Relationship to `UserSettings`
  (one-to-one, cascade).
- **UserSettings** (schema: auth): 12 settings columns including
  `large_transaction_threshold`, `trend_alert_threshold`, `anchor_staleness_days` (added by
  Section 8).
- **MfaConfig** (schema: auth): TOTP secret, backup codes, enabled flag.

No `role_id` column, no `linked_owner_id` column. No role or permission system exists.

**Impact:** Commit 1 adds `role_id` (FK to `ref.user_roles`) and `linked_owner_id` to the User
model. Creates the `ref.user_roles` table with rows for "owner" and "companion". Adds
`RoleEnum` to `app/enums.py` and a `role_id()` accessor to `app/ref_cache.py`. The migration
sets `server_default=1` (owner) so all existing users get the owner role. The seed script adds
the two ref table rows.

### OQ-3: What is the current login flow?

**Answer:** `app/routes/auth.py` (500 lines). Login flow:
1. `GET /login` -- render login form (redirect to `dashboard.page()` if already authenticated).
2. `POST /login` -- `auth_service.authenticate(email, password)`.
3. If MFA enabled: store pending state in `flask_session`, redirect to `auth.mfa_verify()`.
4. If no MFA: `login_user(user, remember=remember)`, store `_session_created_at`, redirect
   to `next` param (validated via `_is_safe_redirect()`) or `dashboard.page()`.
5. MFA verify POST: validate TOTP/backup, then same login_user + redirect flow.

**Impact:** Companion login routing adds a check after `login_user()`: if `user.role_id ==
ref_cache.role_id(RoleEnum.COMPANION)`, redirect to `companion.index` instead of
`dashboard.page()`. This applies in both the direct login path (line 105) and the MFA path
(after MFA verification).

### OQ-4: How does the grid render transaction cells?

**Answer:** `app/routes/grid.py` (399 lines) loads transactions for visible periods, groups
them by `(period_id, category_id, template_id, name)` into `txn_by_period`. The template
`grid/grid.html` (339 lines) iterates over periods and row keys, looking up matching
transactions. Each cell renders `_transaction_cell.html` (63 lines) which displays:
- Amount: estimated or struck-through estimate + bold actual when they differ.
- Status badge: checkmark for settled, "CC" for credit.
- Transfer/override/payback indicators.
- Due date if different from period start.
- Title tooltip with all transaction info.

The cell data is the full Transaction object (`found.txn`).

**Impact:** The progress indicator for entry-capable transactions replaces the amount display
when entries exist. The cell template checks `t.template.track_individual_purchases` (or uses
a pre-computed `entry_sums` dict) and renders "$330 / $500" format instead of the standard
amount. Over-budget amounts use warning styling.

### OQ-5: How does the existing tooltip work?

**Answer:** The tooltip is an HTML `title` attribute on the cell div (line 19 of
`_transaction_cell.html`). It contains:
`"${display_amount} (est: ${estimated}) -- {status.name} -- {notes}"`.
Bootstrap tooltips (`data-bs-toggle="tooltip"`) are used only on status badges (click/focus
trigger), not on the cell itself.

**Impact:** Enhancement adds entry data to the `title` attribute:
`"${spent} / ${budget} -- ${remaining} remaining -- {n} entries -- includes ${credit} on CC"`.
No component or library changes needed.

### OQ-6: What is the transaction detail pattern?

**Answer:** Clicking a grid cell loads the quick edit form via HTMX (`hx-get` to
`transactions.get_quick_edit`). The quick edit form (27 lines) has a single amount input and an
expand button. The expand button (or F2 key) triggers JavaScript in `grid_edit.js` that
fetches `transactions.get_full_edit` and displays it in a Bootstrap popover. The full edit
popover (132 lines) contains: estimated amount, actual amount, status dropdown, notes input,
due date input, and quick action buttons (Paid, Credit, Cancel, Received).

**Impact:** For entry-capable transactions, the full edit popover gains an "Entries" section
below the existing form fields. This section lazy-loads the entry list and add form via HTMX
`hx-trigger="load"`. The popover height may need CSS adjustment to accommodate the entry list.

### OQ-7: How does the grid handle mobile vs desktop?

**Answer:** `grid.html` (339 lines) includes `_mobile_grid.html` (236 lines) as a separate
responsive partial. The mobile grid uses a card-based layout instead of the desktop table. Both
files render transaction cells using `_transaction_cell.html`. A comment at the top of
`_mobile_grid.html` states: "Any change to matching conditions MUST be applied to both files."

**Impact:** The progress indicator must be added to both `_transaction_cell.html` (used by both
desktop and mobile grid) and the mobile grid's card layout if it has its own amount rendering.
The companion view uses its own dedicated mobile-first layout (not the grid mobile variant).

### OQ-8: What is the mark-as-paid flow?

**Answer:** `POST /transactions/<id>/mark-done` (line 211 of `transactions.py`).
1. Ownership check via `_get_owned_transaction(txn_id)`.
2. Determine status: income -> RECEIVED, expense -> DONE.
3. Transfer guard: if `transfer_id IS NOT NULL`, route through `transfer_service.update_transfer`.
4. Set `txn.status_id`, `txn.paid_at = db.func.now()`.
5. Accept optional `actual_amount` from form data.
6. Commit, return rendered cell + `HX-Trigger: gridRefresh`.

**Impact:** After step 4, add entry-aware actual_amount computation: if the transaction has
entries (template.track_individual_purchases and entries loaded), set `actual_amount` to the
sum of all entries (debit + credit). This overrides any manual actual from the form. If no
entries exist, the manual flow is unchanged.

### OQ-9: How does the recurrence engine propagate template flags?

**Answer:** `recurrence_engine.py` (line 139-153) creates Transaction objects by copying
specific fields from the template: `account_id`, `template_id`, `name`, `category_id`,
`transaction_type_id`, `estimated_amount`, `status_id=PROJECTED`, `due_date` (computed from
rule). Boolean flags on the template are NOT copied to the transaction.

**Impact:** The `track_individual_purchases` flag stays on the template only (per scope doc).
The transaction has a `template` relationship (`lazy="select"`) that allows looking up
`txn.template.track_individual_purchases` at runtime. For the balance calculator, the approach
is different: it checks if `txn.entries` is loaded and non-empty, avoiding the need to load the
template relationship. If entries exist, the transaction must be entry-capable (the entry
service validates this on create).

### OQ-10: What is the current status workflow?

**Answer:** StatusEnum in `app/enums.py`:
- **PROJECTED** ("Projected") -- default for new transactions.
- **DONE** ("Paid") -- expense paid. `is_settled=True` on ref table.
- **RECEIVED** ("Received") -- income deposited. `is_settled=True`.
- **CREDIT** ("Credit") -- paid via credit card. `excludes_from_balance=True`.
- **CANCELLED** ("Cancelled") -- user cancelled. `excludes_from_balance=True`.
- **SETTLED** ("Settled") -- archived/reconciled. `is_settled=True`.

Valid transitions from Projected: Done, Received, Credit, Cancelled.
Valid transitions from Done/Received: Settled.
Reversal from Credit: back to Projected (via `unmark_credit`).

**Impact:** Entry-capable transactions cannot use the CREDIT status. The legacy Credit status
sets `excludes_from_balance=True` on the entire transaction, which would conflict with
entry-level credit handling. Enforcement: (1) `credit_workflow.mark_as_credit` gains a guard
rejecting entry-capable transactions. (2) The `update_transaction` route gains a guard blocking
manual status_id changes to CREDIT for entry-capable transactions. (3) The full edit popover
hides the "Credit" button for entry-capable transactions.

### OQ-11: Does the dashboard need changes for entry-capable transactions?

**Answer:** `app/services/dashboard_service.py` (653 lines) uses `txn.effective_amount` for
the upcoming bills section (`_get_upcoming_bills`, line 145) and spending comparison
(`_sum_settled_expenses`, line 547). For projected entry-capable transactions,
`effective_amount` returns `estimated_amount` (since `actual_amount` is None until Paid).

**Impact:** The dashboard's upcoming bills section shows the estimated (budgeted) amount for
entry-capable transactions, which is correct -- the bill row represents the budget allocation.
A future enhancement (Opportunity OP-1) could show the progress indicator on the dashboard.
For the balance/runway calculation, `dashboard_service` calls `balance_calculator` (line 85),
which will use the entry-aware formula after Commit 3 if entries are loaded via selectinload.
The dashboard route needs to add `selectinload(Transaction.entries)` to its transaction query
for correct balance display.

### OQ-12: Does analytics/calendar need changes for entry data?

**Answer:** `calendar_service.py` (572 lines) groups transactions by month using `due_date` and
uses `effective_amount` for amounts. `spending_trend_service.py` (572 lines) analyzes settled
transactions using `effective_amount`. `year_end_summary_service.py` (1875 lines) aggregates
by category.

**Impact:** For settled transactions (the primary data for calendar, trends, year-end), the
`actual_amount` is set from the entry sum when marked Paid, so `effective_amount` returns the
correct value. No changes needed for these services in this phase. A future enhancement could
add per-entry breakdowns to the year-end summary (see Opportunity OP-3).

---

## Codebase Inventory

Every file that Section 9 will create, modify, or depend on.

### Models

| File | Lines | Action |
|------|-------|--------|
| `app/models/transaction_entry.py` | -- | **Create** |
| `app/models/ref.py` | ~220 | Modify (add `UserRole` model) |
| `app/models/transaction_template.py` | 64 | Modify (add 2 columns) |
| `app/models/user.py` | 144 | Modify (add `role_id` FK and `linked_owner_id` to User) |
| `app/models/transaction.py` | 191 | Modify (add `entries` relationship) |
| `app/models/__init__.py` | 59 | Modify (import TransactionEntry, UserRole) |
| `app/models/pay_period.py` | 49 | Read-only dependency |
| `app/models/category.py` | 41 | Read-only dependency |
| `app/models/account.py` | 88 | Read-only dependency |

### Services (existing, to modify)

| File | Lines | Action |
|------|-------|--------|
| `app/services/balance_calculator.py` | 354 | Modify (add `_entry_aware_amount` helper) |
| `app/services/credit_workflow.py` | 190 | Modify (add entry-capable guard) |
| `app/services/dashboard_service.py` | 653 | Modify (add selectinload for entries) |

### Services (existing, read-only dependencies)

| File | Lines | Reason |
|------|-------|--------|
| `app/services/pay_period_service.py` | 167 | Period navigation for CC Payback placement |
| `app/services/transfer_service.py` | 727 | Transfer invariant awareness |
| `app/services/recurrence_engine.py` | 622 | Template-to-transaction propagation patterns |
| `app/services/auth_service.py` | 421 | Authentication patterns for companion |
| `app/services/calendar_service.py` | 572 | Verify no entry-awareness needed |
| `app/services/spending_trend_service.py` | 572 | Verify no entry-awareness needed |
| `app/services/budget_variance_service.py` | 485 | Verify no entry-awareness needed |
| `app/services/year_end_summary_service.py` | 1875 | Verify no entry-awareness needed |

### Services (new files to create)

| File | Description |
|------|-------------|
| `app/services/entry_service.py` | Transaction entry CRUD, validation, summation, remaining balance |
| `app/services/entry_credit_workflow.py` | Entry-level CC Payback aggregation and lifecycle |
| `app/services/companion_service.py` | Companion data access with visibility filtering |

### Routes (existing, to modify)

| File | Lines | Action |
|------|-------|--------|
| `app/routes/transactions.py` | 654 | Modify (mark-done entry awareness, status guard) |
| `app/routes/grid.py` | 399 | Modify (selectinload, entry_sums computation) |
| `app/routes/auth.py` | 500 | Modify (companion login routing) |
| `app/routes/templates.py` | 517 | Modify (template flags in schema + update set) |
| `app/routes/settings.py` | 216 | Modify (add companion management section) |
| `app/routes/dashboard.py` | 183 | Modify (selectinload for entries) |
| `app/routes/accounts.py` | 946 | Modify (add require_owner guard) |
| `app/routes/analytics.py` | 472 | Modify (add require_owner guard) |
| `app/routes/categories.py` | 201 | Modify (add require_owner guard) |
| `app/routes/debt_strategy.py` | 444 | Modify (add require_owner guard) |
| `app/routes/investment.py` | 809 | Modify (add require_owner guard) |
| `app/routes/loan.py` | 1245 | Modify (add require_owner guard) |
| `app/routes/obligations.py` | 420 | Modify (add require_owner guard) |
| `app/routes/pay_periods.py` | 50 | Modify (add require_owner guard) |
| `app/routes/retirement.py` | 338 | Modify (add require_owner guard) |
| `app/routes/salary.py` | 1089 | Modify (add require_owner guard) |
| `app/routes/savings.py` | 228 | Modify (add require_owner guard) |
| `app/routes/transfers.py` | 851 | Modify (add require_owner guard) |
| `app/routes/charts.py` | 19 | Modify (add require_owner guard) |

### Routes (new files to create)

| File | Description |
|------|-------------|
| `app/routes/entries.py` | Entry CRUD endpoints (nested under transactions) |
| `app/routes/companion.py` | Companion view routes |

### Schemas

| File | Lines | Action |
|------|-------|--------|
| `app/schemas/validation.py` | 1308 | Modify (add EntryCreateSchema, EntryUpdateSchema, template booleans) |

### Enums and Cache

| File | Lines | Action |
|------|-------|--------|
| `app/enums.py` | 149 | Modify (add `RoleEnum`) |
| `app/ref_cache.py` | 385 | Modify (add `_role_map`, `role_id()` accessor, load `UserRole` in `init()`) |

### Auth and Utilities

| File | Lines | Action |
|------|-------|--------|
| `app/utils/auth_helpers.py` | 86 | Modify (add `require_owner` decorator) |
| `app/__init__.py` | 514 | Modify (register entries_bp, companion_bp) |

### Templates (existing, to modify)

| File | Lines | Action |
|------|-------|--------|
| `app/templates/grid/_transaction_cell.html` | 63 | Modify (add progress indicator) |
| `app/templates/grid/_transaction_full_edit.html` | 132 | Modify (add entries section, hide Credit button) |
| `app/templates/grid/_mobile_grid.html` | 236 | Modify (add progress indicator) |
| `app/templates/grid/grid.html` | 339 | Modify (pass entry_sums to cell includes) |
| `app/templates/templates/form.html` | 197 | Modify (add tracking/visibility toggles) |
| `app/templates/templates/list.html` | 184 | Modify (add indicator badges) |
| `app/templates/base.html` | 262 | Modify (add companion nav or hide full nav) |
| `app/templates/settings/dashboard.html` | 80 | Modify (add companion account section) |
| `app/templates/auth/login.html` | 40 | Read-only (login form pattern) |

### Templates (new files to create)

| File | Description |
|------|-------------|
| `app/templates/grid/_transaction_entries.html` | HTMX partial: entry list + add form |
| `app/templates/companion/index.html` | Companion main view |
| `app/templates/companion/_transaction_card.html` | Transaction card with progress and entries |
| `app/templates/companion/_entry_list.html` | HTMX partial: companion entry list |
| `app/templates/companion/_period_nav.html` | HTMX partial: period navigation |
| `app/templates/settings/_companion.html` | Settings partial: companion account management |

### Static Assets

| File | Description |
|------|-------------|
| `app/static/css/app.css` | Modify (add entry/progress/companion styles) |

### Tests (existing, to modify)

| File | Lines | Action |
|------|-------|--------|
| `tests/conftest.py` | 1037 | Modify (add entry-capable fixtures) |
| `tests/test_services/test_balance_calculator.py` | 2432 | Read-only (must all continue to pass) |
| `tests/test_services/test_credit_workflow.py` | 855 | Read-only (must all continue to pass) |
| `tests/test_routes/test_grid.py` | 3613 | Read-only (must all continue to pass) |
| `tests/test_routes/test_transaction_auth.py` | 602 | Read-only (must all continue to pass) |
| `tests/test_routes/test_transaction_guards.py` | 536 | Read-only (must all continue to pass) |
| `tests/test_routes/test_dashboard.py` | 522 | Read-only (must all continue to pass) |

### Tests (new files to create)

| File | Description |
|------|-------------|
| `tests/test_services/test_entry_service.py` | Entry CRUD, validation, summation tests |
| `tests/test_services/test_entry_credit_workflow.py` | Entry-level CC Payback tests |
| `tests/test_services/test_balance_calculator_entries.py` | Entry-aware balance tests (all 6 scenarios) |
| `tests/test_services/test_companion_service.py` | Companion data isolation tests |
| `tests/test_routes/test_entries.py` | Entry route integration tests |
| `tests/test_routes/test_companion_routes.py` | Companion view route tests |
| `tests/test_routes/test_companion_guards.py` | Role guard enforcement tests |

### Migrations (new)

| File | Description |
|------|-------------|
| `migrations/versions/xxxx_add_entry_tracking_and_companion_support.py` | Single migration: user_roles ref table, template flags, user role_id FK, transaction_entries table |

---

## Task Dependency Analysis

### Dependency Graph

```
Commit 1: Migration + Models
|
+--- Commit 2: Entry Service + Schema
|    |
|    +--- Commit 3: Balance Calculator Extension
|    |    |
|    |    +--- Commit 4: Entry-Level CC Workflow
|    |    |    |
|    |    |    +--- Commit 5: Mark-Paid + Status Prevention
|    |    |
|    |    +--- Commit 7: Grid Progress Indicator + Tooltip
|    |
|    +--- Commit 8: Entry CRUD UI [depends on 2, 4, 7]
|
+--- Commit 6: Template Settings UI [depends on 1 only]
|
+--- Commit 9: Companion Role + Route Guards [depends on 1 only]
     |
     +--- Commit 10: Companion View [depends on 2, 8, 9]
```

### Commit Order Rationale

1. **Infrastructure first.** Commit 1 (migration + models) provides the schema foundation for
   everything else. No other commit can start without it.

2. **Engine before display.** Commits 2-5 build the computation layer (entry service, balance
   calculator, credit workflow, mark-paid). Commits 7-8 build the display layer (grid progress,
   entry CRUD UI). The engine is fully tested before any UI work.

3. **Independent branches in parallel.** Commits 6 (template UI) and 9 (companion guards) depend
   only on Commit 1 and can be developed in parallel with the entry engine chain (2-5).

4. **Credit workflow after balance calculator.** Commit 4 (entry credit) depends on Commit 3
   (balance calculator) because the CC Payback amount must be consistent with how the balance
   calculator interprets credit entries.

5. **Companion view last.** Commit 10 (companion view) depends on the entry CRUD UI (Commit 8)
   and the companion guards (Commit 9). It reuses the entry service and entry routes, so those
   must be stable.

6. **No commit modifies existing tests.** Every existing test must continue to pass after each
   commit. If an existing test fails, the implementation is wrong.

---

## Commit 1: Migration and Models -- Ref Table, Template Flags, User Role, TransactionEntry Table

### A. Commit message

```
feat(entries): add TransactionEntry model, user_roles ref table, template tracking flags, and companion user support
```

### B. Problem statement

Section 9 requires four data model changes that are prerequisites for all other work:
(1) A `ref.user_roles` table with `RoleEnum` and `ref_cache` integration for the companion role
system, following the established "IDs for logic" pattern. (2) Template flags
(`track_individual_purchases`, `companion_visible`) to control which transactions support
sub-entries and companion visibility. (3) User columns (`role_id` FK to `ref.user_roles`,
`linked_owner_id`) for the companion access model. (4) A new `transaction_entries` table for
individual purchase records. This commit establishes the schema foundation.

### C. Files modified

| File | Change |
|------|--------|
| `app/models/transaction_entry.py` | **Create.** New TransactionEntry model. |
| `app/models/ref.py` | Add `UserRole` model class. |
| `app/models/transaction_template.py` | Add `track_individual_purchases` and `companion_visible` boolean columns. |
| `app/models/user.py` | Add `role_id` (FK to `ref.user_roles`) and `linked_owner_id` columns to User class. Add `role` relationship. |
| `app/models/transaction.py` | Add `entries` relationship to TransactionEntry. |
| `app/models/__init__.py` | Import TransactionEntry, UserRole. |
| `app/enums.py` | Add `RoleEnum` with OWNER and COMPANION members. |
| `app/ref_cache.py` | Add `_role_map`, `role_id()` accessor, load `UserRole` in `init()`. |
| `scripts/seed_ref_tables.py` | Add `UserRole` to imports and `REF_DATA` with seed values. |
| `migrations/versions/xxxx_add_entry_tracking_and_companion_support.py` | **Create.** Alembic migration. |
| `tests/conftest.py` | Add fixtures for entry-capable templates, entries, companion users. |

### D. Implementation approach

**TransactionEntry model** (`app/models/transaction_entry.py`):

```python
class TransactionEntry(db.Model):
    """An individual purchase recorded against a parent transaction.

    Entries accumulate against the parent transaction's estimated amount.
    The sum of all entries determines the remaining budget and the
    checking balance impact for entry-capable transactions.
    """

    __tablename__ = "transaction_entries"
    __table_args__ = (
        db.Index("idx_transaction_entries_txn_id", "transaction_id"),
        db.Index(
            "idx_transaction_entries_txn_credit",
            "transaction_id", "is_credit",
        ),
        db.CheckConstraint(
            "amount > 0",
            name="ck_transaction_entries_positive_amount",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    entry_date = db.Column(db.Date, nullable=False, server_default=db.text("CURRENT_DATE"))
    is_credit = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    credit_payback_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.transactions.id", ondelete="SET NULL"),
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    transaction = db.relationship("Transaction", foreign_keys=[transaction_id],
                                  back_populates="entries")
    user = db.relationship("User", lazy="joined")
    credit_payback = db.relationship("Transaction", foreign_keys=[credit_payback_id],
                                     lazy="select")
```

**TransactionTemplate additions** (2 columns):

```python
track_individual_purchases = db.Column(
    db.Boolean, nullable=False, default=False, server_default="false",
)
companion_visible = db.Column(
    db.Boolean, nullable=False, default=False, server_default="false",
)
```

**UserRole model** (addition to `app/models/ref.py`):

```python
class UserRole(db.Model):
    """User role reference: 'owner', 'companion'.

    Determines route access and data visibility scope.
    Owner accounts have full access.  Companion accounts
    see only transactions from companion-visible templates
    belonging to their linked owner.
    """

    __tablename__ = "user_roles"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<UserRole {self.name}>"
```

**RoleEnum** (addition to `app/enums.py`):

```python
class RoleEnum(enum.Enum):
    """User role values.

    Values match ``ref.user_roles.name`` in the database.
    """

    OWNER = "owner"
    COMPANION = "companion"
```

**ref_cache additions** (`app/ref_cache.py`):

Add `_role_map = {}` to module-level state. In `init()`, import `UserRole` from
`app.models.ref`, load rows, and map `RoleEnum` members. Add accessor:

```python
def role_id(member):
    """Return the integer primary key for a RoleEnum member.

    Args:
        member: A ``RoleEnum`` member (e.g. ``RoleEnum.OWNER``).

    Returns:
        int -- the ``ref.user_roles.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid RoleEnum member.
    """
    if not _initialized:
        raise RuntimeError("ref_cache not initialized -- call init() first.")
    return _role_map[member]
```

**Seed data** (addition to `scripts/seed_ref_tables.py`):

Add `UserRole` to imports and add to `REF_DATA`:

```python
(UserRole, ["owner", "companion"]),
```

**User additions** (2 columns):

```python
role_id = db.Column(
    db.Integer,
    db.ForeignKey("ref.user_roles.id", ondelete="RESTRICT"),
    nullable=False,
    server_default="1",  # 1 = owner
)
linked_owner_id = db.Column(
    db.Integer,
    db.ForeignKey("auth.users.id", ondelete="SET NULL"),
)

# Relationships
role = db.relationship("UserRole", lazy="joined")
```

The `ondelete="RESTRICT"` on `role_id` follows the established pattern for ref table FKs --
roles cannot be deleted while users reference them. The `lazy="joined"` on the relationship
ensures `user.role.name` is available without an extra query (useful for display in settings
UI), but application logic always uses `user.role_id == ref_cache.role_id(RoleEnum.COMPANION)`
rather than string comparisons.

**Transaction relationship addition:**

```python
entries = db.relationship(
    "TransactionEntry", back_populates="transaction",
    foreign_keys="TransactionEntry.transaction_id",
    lazy="select", cascade="all, delete-orphan",
    order_by="TransactionEntry.entry_date",
)
```

`lazy="select"` means entries are not loaded unless explicitly accessed or eager-loaded via
`selectinload`. This prevents performance degradation on existing queries.

**Migration SQL (upgrade):**

```sql
-- User roles ref table (must precede user column addition)
CREATE TABLE ref.user_roles (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) NOT NULL UNIQUE
);
INSERT INTO ref.user_roles (id, name) VALUES (1, 'owner'), (2, 'companion');

-- Template flags
ALTER TABLE budget.transaction_templates
    ADD COLUMN track_individual_purchases BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE budget.transaction_templates
    ADD COLUMN companion_visible BOOLEAN NOT NULL DEFAULT FALSE;

-- User companion support
ALTER TABLE auth.users
    ADD COLUMN role_id INTEGER NOT NULL DEFAULT 1
        REFERENCES ref.user_roles(id) ON DELETE RESTRICT;
ALTER TABLE auth.users
    ADD COLUMN linked_owner_id INTEGER;
ALTER TABLE auth.users
    ADD CONSTRAINT fk_users_linked_owner
        FOREIGN KEY (linked_owner_id)
        REFERENCES auth.users(id)
        ON DELETE SET NULL;

-- Transaction entries table
CREATE TABLE budget.transaction_entries (
    id SERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL
        REFERENCES budget.transactions(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL
        REFERENCES auth.users(id) ON DELETE CASCADE,
    amount NUMERIC(12,2) NOT NULL,
    description VARCHAR(200) NOT NULL,
    entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
    is_credit BOOLEAN NOT NULL DEFAULT FALSE,
    credit_payback_id INTEGER
        REFERENCES budget.transactions(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_transaction_entries_positive_amount CHECK (amount > 0)
);
CREATE INDEX idx_transaction_entries_txn_id
    ON budget.transaction_entries(transaction_id);
CREATE INDEX idx_transaction_entries_txn_credit
    ON budget.transaction_entries(transaction_id, is_credit);
```

**Migration SQL (downgrade):**

```sql
DROP TABLE budget.transaction_entries;
ALTER TABLE auth.users DROP CONSTRAINT IF EXISTS fk_users_linked_owner;
ALTER TABLE auth.users DROP COLUMN IF EXISTS linked_owner_id;
ALTER TABLE auth.users DROP COLUMN IF EXISTS role_id;
DROP TABLE IF EXISTS ref.user_roles;
ALTER TABLE budget.transaction_templates DROP COLUMN IF EXISTS companion_visible;
ALTER TABLE budget.transaction_templates DROP COLUMN IF EXISTS track_individual_purchases;
```

**Test fixtures** (additions to `conftest.py`):

```python
@pytest.fixture
def seed_entry_template(db, seed_user, seed_periods):
    """Create a template with track_individual_purchases=True and a transaction."""
    ...returns dict with template, transaction, category...

@pytest.fixture
def seed_companion(db, seed_user):
    """Create a companion user linked to the seed_user owner."""
    ...returns dict with companion user, linked_owner_id...

@pytest.fixture
def companion_client(app, seed_companion):
    """Authenticated test client for the companion user."""
    ...logs in as companion, returns test client...
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 1.1 | `test_transaction_entry_cascade_delete` | Create transaction + entry | Delete transaction | Entry deleted via CASCADE | New |
| 1.2 | `test_transaction_entry_amount_positive_check` | -- | Create entry with amount=0 | IntegrityError (CHECK violation) | New |
| 1.3 | `test_transaction_entry_amount_negative_check` | -- | Create entry with amount=-5 | IntegrityError (CHECK violation) | New |
| 1.4 | `test_template_flags_default_false` | Create template | Read flags | track_individual_purchases=False, companion_visible=False | New |
| 1.5 | `test_user_role_default_owner` | Create user | Read role_id | role_id=ref_cache.role_id(RoleEnum.OWNER) | New |
| 1.6 | `test_user_linked_owner_nullable` | Create owner user | Read linked_owner_id | None | New |
| 1.7 | `test_companion_linked_to_owner` | Create companion | Read linked_owner_id | owner.id | New |
| 1.8 | `test_user_role_fk_constraint` | -- | Set role_id=999 (nonexistent) | IntegrityError (FK violation) | New |
| 1.8a | `test_user_role_ref_table_seeded` | App startup | Query ref.user_roles | 2 rows: owner, companion | New |
| 1.8b | `test_role_enum_cache_loaded` | App startup | ref_cache.role_id(RoleEnum.OWNER) | Returns integer ID | New |
| 1.9 | `test_entry_user_cascade_delete` | Create entry | Delete creating user | Entry deleted via CASCADE | New |
| 1.10 | `test_transaction_entries_relationship` | Create transaction + 3 entries | Access txn.entries | Returns 3 entries ordered by entry_date | New |
| 1.11 | `test_migration_upgrade_downgrade` | Run upgrade | Run downgrade | Clean schema, no leftover objects | New |

### F. Manual verification steps

1. Run `flask db upgrade` -- verify no errors.
2. Run `flask db downgrade` -- verify clean rollback.
3. Check `\d ref.user_roles` in psql -- verify 2 rows (owner, companion).
4. Check `\d budget.transaction_entries` in psql -- verify all columns, constraints, indexes.
5. Check `\d budget.transaction_templates` -- verify new boolean columns with defaults.
6. Check `\d auth.users` -- verify role_id column with FK to ref.user_roles.
7. Verify `ref_cache.role_id(RoleEnum.OWNER)` and `ref_cache.role_id(RoleEnum.COMPANION)` return
   correct IDs in a Flask shell.

### G. Downstream effects

All existing queries continue to work unchanged because:
- Template flags default to FALSE -- no template is entry-capable or companion-visible.
- User role_id defaults to 1 (owner) -- all existing users remain owners.
- The entries relationship uses `lazy="select"` -- not loaded unless requested.
- The ref_cache loads the new UserRole table at startup alongside existing ref tables.
- No existing tests are modified.

### H. Rollback notes

Run `flask db downgrade` to the previous revision. The migration drops the table and columns
cleanly. No data migration is involved (all new columns have defaults or are new tables).

---

## Commit 2: Transaction Entry Service and Schema

### A. Commit message

```
feat(entries): add entry service with CRUD, validation, summation, and remaining balance
```

### B. Problem statement

Entry-capable transactions need a service layer for creating, reading, updating, and deleting
sub-entries, plus computing entry sums and remaining balance. This service is the foundation
consumed by the balance calculator (Commit 3), credit workflow (Commit 4), mark-paid (Commit 5),
and all UI (Commits 7, 8, 10).

### C. Files modified

| File | Change |
|------|--------|
| `app/services/entry_service.py` | **Create.** Entry CRUD + business logic. |
| `app/schemas/validation.py` | Add `EntryCreateSchema`, `EntryUpdateSchema`. |
| `tests/test_services/test_entry_service.py` | **Create.** Comprehensive service tests. |

### D. Implementation approach

**entry_service.py** -- key functions:

```python
def create_entry(
    transaction_id: int,
    user_id: int,
    amount: Decimal,
    description: str,
    entry_date: date,
    is_credit: bool = False,
) -> TransactionEntry:
    """Create a new purchase entry against a transaction.

    Validates:
      - Transaction exists and belongs to user_id (or user_id's linked owner).
      - Transaction's template has track_individual_purchases=True.
      - Transaction is not a transfer (transfer_id IS NULL).
      - Transaction is an expense (not income).

    Args:
        transaction_id: Parent transaction ID.
        user_id: The creating user's ID (owner or companion).
        amount: Positive Decimal for the purchase amount.
        description: Store name or brief note.
        entry_date: Date of the purchase.
        is_credit: Whether this was paid with a credit card.

    Returns:
        The newly created TransactionEntry.

    Raises:
        NotFoundError: Transaction not found or not accessible.
        ValidationError: Transaction not entry-capable, is a transfer, or is income.
    """
```

Validation logic:
```python
txn = db.session.get(Transaction, transaction_id)
if txn is None:
    raise NotFoundError(f"Transaction {transaction_id} not found.")

# Ownership: owner checks pay_period.user_id == user_id.
# Companion checks pay_period.user_id == companion.linked_owner_id.
owner_id = _resolve_owner_id(user_id)
if txn.pay_period.user_id != owner_id:
    raise NotFoundError(f"Transaction {transaction_id} not found.")

# Entry-capable check.
if txn.template is None or not txn.template.track_individual_purchases:
    raise ValidationError(
        "This transaction does not support individual purchase tracking. "
        "Enable 'Track individual purchases' on the template first."
    )

# Transfer guard (mirrors credit_workflow.py line 59).
if txn.transfer_id is not None:
    raise ValidationError("Cannot add entries to transfer transactions.")

# Expense-only guard.
if txn.is_income:
    raise ValidationError("Cannot add purchase entries to income transactions.")
```

Helper for companion ownership resolution:
```python
def _resolve_owner_id(user_id: int) -> int:
    """Return the data-owning user_id.

    For owners, returns user_id unchanged.
    For companions, returns linked_owner_id.
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    companion_id = ref_cache.role_id(RoleEnum.COMPANION)
    if user.role_id == companion_id and user.linked_owner_id is not None:
        return user.linked_owner_id
    return user_id
```

Remaining functions:

```python
def update_entry(
    entry_id: int,
    user_id: int,
    **kwargs,
) -> TransactionEntry:
    """Update an existing entry. Allowed fields: amount, description, entry_date, is_credit."""

def delete_entry(entry_id: int, user_id: int) -> int:
    """Hard-delete an entry. Returns the parent transaction_id for payback sync."""

def get_entries_for_transaction(transaction_id: int, user_id: int) -> list[TransactionEntry]:
    """Return all entries for a transaction, ordered by entry_date ASC."""

def compute_entry_sums(entries: list[TransactionEntry]) -> tuple[Decimal, Decimal]:
    """Compute (sum_debit, sum_credit) from a list of entries.

    Pure function -- no database access.
    sum_debit = sum(e.amount for e in entries if not e.is_credit)
    sum_credit = sum(e.amount for e in entries if e.is_credit)
    """

def compute_remaining(estimated_amount: Decimal, entries: list[TransactionEntry]) -> Decimal:
    """Compute remaining budget: estimated_amount - sum(all entry amounts).

    Negative values indicate overspending. Uses sum of ALL entries
    regardless of payment method (debit + credit) because the remaining
    balance represents budget consumption, not checking impact.
    """

def compute_actual_from_entries(entries: list[TransactionEntry]) -> Decimal:
    """Compute actual_amount for a Paid transaction: sum of ALL entries (debit + credit).

    The actual_amount represents total spending for analytics/reporting.
    The credit portion is already handled by the CC Payback in the next period.
    """
```

**Schema additions** in `validation.py`:

```python
class EntryCreateSchema(BaseSchema):
    """Validates POST data for creating a transaction entry."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        required=True, places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(
        required=True, validate=validate.Length(min=1, max=200),
    )
    entry_date = fields.Date(required=True)
    is_credit = fields.Boolean(load_default=False)


class EntryUpdateSchema(BaseSchema):
    """Validates PATCH data for updating an entry. All fields optional."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        return {k: v for k, v in data.items() if v != ""}

    amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=Decimal("0.01")),
    )
    description = fields.String(validate=validate.Length(min=1, max=200))
    entry_date = fields.Date()
    is_credit = fields.Boolean()
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 2.1 | `test_create_entry_basic` | Entry-capable txn | create_entry(amount=50, desc="Kroger") | Entry created, amount=50, is_credit=False | New |
| 2.2 | `test_create_entry_credit` | Entry-capable txn | create_entry(is_credit=True) | Entry created, is_credit=True | New |
| 2.3 | `test_create_entry_rejects_non_tracking_template` | Template with track=False | create_entry | ValidationError | New |
| 2.4 | `test_create_entry_rejects_no_template` | Ad-hoc txn (template_id=None) | create_entry | ValidationError | New |
| 2.5 | `test_create_entry_rejects_transfer` | Transfer shadow txn | create_entry | ValidationError | New |
| 2.6 | `test_create_entry_rejects_income` | Income txn with track=True | create_entry | ValidationError | New |
| 2.7 | `test_create_entry_rejects_other_user` | Txn owned by user A | create_entry as user B | NotFoundError | New |
| 2.8 | `test_update_entry_amount` | Existing entry | update_entry(amount=75) | amount updated to 75 | New |
| 2.9 | `test_update_entry_credit_toggle` | Debit entry | update_entry(is_credit=True) | is_credit changed to True | New |
| 2.10 | `test_delete_entry` | Existing entry | delete_entry | Entry hard-deleted | New |
| 2.11 | `test_delete_entry_rejects_other_user` | Entry on user A's txn | delete_entry as user B | NotFoundError | New |
| 2.12 | `test_compute_entry_sums_all_debit` | 3 debit entries ($50, $100, $30) | compute_entry_sums | (180, 0) | New |
| 2.13 | `test_compute_entry_sums_mixed` | 2 debit ($100, $50), 1 credit ($80) | compute_entry_sums | (150, 80) | New |
| 2.14 | `test_compute_entry_sums_empty` | No entries | compute_entry_sums | (0, 0) | New |
| 2.15 | `test_compute_remaining_under_budget` | estimated=500, entries summing to 330 | compute_remaining | 170 | New |
| 2.16 | `test_compute_remaining_over_budget` | estimated=500, entries summing to 530 | compute_remaining | -30 | New |
| 2.17 | `test_compute_remaining_zero` | estimated=500, entries summing to 500 | compute_remaining | 0 | New |
| 2.18 | `test_compute_actual_includes_credit` | 2 debit ($300), 1 credit ($100) | compute_actual_from_entries | 400 | New |
| 2.19 | `test_get_entries_ordered_by_date` | 3 entries on different dates | get_entries_for_transaction | Ordered by entry_date ASC | New |
| 2.20 | `test_schema_rejects_zero_amount` | POST with amount=0 | Schema validate | Validation error | New |
| 2.21 | `test_schema_rejects_empty_description` | POST with description="" | Schema validate | Validation error | New |

### F. Manual verification steps

None -- this is a service-only commit with no UI changes. All verification is through tests.

### G. Downstream effects

The entry service is consumed by Commits 3-5, 8, and 10. No existing functionality is affected
because no existing code calls the entry service.

### H. Rollback notes

Remove `entry_service.py`, the schema additions, and the test file. No database changes.

---

## Commit 3: Balance Calculator Extension -- Entry-Aware Checking Impact

### A. Commit message

```
feat(balance): extend balance calculator with entry-aware checking impact for tracked expenses
```

### B. Problem statement

The balance calculator currently uses `effective_amount` (actual if set, else estimated) for all
transactions. For entry-capable transactions in Projected status, the checking impact is:
`max(estimated - sum_credit_entries, sum_debit_entries)`. This formula ensures credit entries
reduce the checking reservation while debit overspend is reflected immediately.

### C. Files modified

| File | Change |
|------|--------|
| `app/services/balance_calculator.py` | Add `_entry_aware_amount` helper; modify `_sum_remaining` and `_sum_all` to use it for expenses. |
| `app/routes/grid.py` | Add `selectinload(Transaction.entries)` to transaction query. |
| `app/services/dashboard_service.py` | Add `selectinload(Transaction.entries)` where transactions are loaded for balance calculations. |
| `tests/test_services/test_balance_calculator_entries.py` | **Create.** All 6 scenarios from the scope doc table plus edge cases. |

### D. Implementation approach

**New helper in `balance_calculator.py`:**

```python
def _entry_aware_amount(txn):
    """Compute the checking-balance impact for a single transaction.

    For transactions with loaded entries (entry-capable, sub-entries exist):
        sum_debit  = sum of entries where is_credit = False
        sum_credit = sum of entries where is_credit = True
        checking_impact = max(estimated - sum_credit, sum_debit)

    For all other transactions: returns effective_amount unchanged.

    The formula semantics:
      - estimated - sum_credit = budget reservation minus the credit card portion
      - sum_debit = actual debit purchases made
      - max() = whichever is larger determines checking impact
      - If no entries yet: max(estimated - 0, 0) = estimated (unchanged)
      - If debits exceed adjusted reservation: overspend hits balance immediately

    Args:
        txn: A Transaction object with entries optionally eager-loaded.

    Returns:
        Decimal -- the amount this transaction contributes to the checking balance.
    """
    # Guard: if entries aren't loaded or are empty, use standard logic.
    # The 'entries' attribute may not exist if selectinload was not applied,
    # or may be an empty list if the transaction has no entries.
    entries = getattr(txn, 'entries', None)
    if not entries:
        return txn.effective_amount

    # Only apply the entry formula to projected expenses.
    # Settled/cancelled/credit transactions already return 0 via effective_amount.
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    if txn.status_id != projected_id:
        return txn.effective_amount

    sum_debit = Decimal("0")
    sum_credit = Decimal("0")
    for entry in entries:
        if entry.is_credit:
            sum_credit += entry.amount
        else:
            sum_debit += entry.amount

    return max(txn.estimated_amount - sum_credit, sum_debit)
```

**Modification to `_sum_remaining` and `_sum_all`:**

Both functions have the same loop structure. The change replaces `txn.effective_amount` with
`_entry_aware_amount(txn)` for expense transactions:

```python
# Before (both functions):
amount = txn.effective_amount
if txn.is_income:
    income += amount
elif txn.is_expense:
    expenses += amount

# After:
if txn.is_income:
    income += txn.effective_amount  # Income unchanged
elif txn.is_expense:
    expenses += _entry_aware_amount(txn)  # Entry-aware for expenses
```

Income transactions are never entry-capable (the entry service rejects income transactions), so
`effective_amount` is correct for them.

**Grid route change** (`grid.py`):

Add `selectinload(Transaction.entries)` to the transaction query (around line 195):

```python
from sqlalchemy.orm import selectinload

# In index() and balance_row():
all_transactions = (
    db.session.query(Transaction)
    .options(selectinload(Transaction.entries))
    .filter(...)
    .all()
)
```

The `selectinload` issues a single `SELECT ... WHERE transaction_id IN (...)` after the main
query, loading entries only for transactions that have them. For the ~100 transactions in a
typical grid view, this adds at most one extra query.

**Dashboard service change** (`dashboard_service.py`):

Add `selectinload(Transaction.entries)` to the transaction queries used for balance calculation
(in `_compute_dashboard_data` or wherever transactions are loaded before passing to
`calculate_balances`).

**Worked examples** (verifying the formula against the scope doc table):

| # | Scenario | estimated | sum_debit | sum_credit | max(est-credit, debit) | Expected |
|---|----------|-----------|-----------|------------|------------------------|----------|
| 1 | No entries | 500 | 0 | 0 | max(500, 0) = 500 | 500 |
| 2 | Under budget, debit only | 500 | 200 | 0 | max(500, 200) = 500 | 500 |
| 3 | Mixed payment | 500 | 300 | 100 | max(400, 300) = 400 | 400 |
| 4 | All credit | 500 | 0 | 400 | max(100, 0) = 100 | 100 |
| 5 | Over budget, debit only | 500 | 530 | 0 | max(500, 530) = 530 | 530 |
| 6 | Over budget, mixed | 500 | 400 | 200 | max(300, 400) = 400 | 400 |

All match the scope document's Section 4.2 table.

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 3.1 | `test_entry_aware_no_entries` | Tracked txn, no entries, est=500 | calculate_balances | Balance reduces by 500 | New |
| 3.2 | `test_entry_aware_debit_under_budget` | Tracked txn, $200 debit, est=500 | calculate_balances | Balance reduces by 500 (reservation) | New |
| 3.3 | `test_entry_aware_mixed_under_budget` | Tracked txn, $300 debit + $100 credit, est=500 | calculate_balances | Balance reduces by 400 | New |
| 3.4 | `test_entry_aware_all_credit` | Tracked txn, $400 credit, est=500 | calculate_balances | Balance reduces by 100 | New |
| 3.5 | `test_entry_aware_debit_over_budget` | Tracked txn, $530 debit, est=500 | calculate_balances | Balance reduces by 530 | New |
| 3.6 | `test_entry_aware_mixed_over_budget` | Tracked txn, $400 debit + $200 credit, est=500 | calculate_balances | Balance reduces by 400 | New |
| 3.7 | `test_entry_aware_paid_uses_actual` | Tracked txn, status=DONE, actual=450 | calculate_balances | Transaction excluded (settled) | New |
| 3.8 | `test_entry_aware_income_unchanged` | Income txn, entries loaded | calculate_balances | Uses effective_amount (income never entry-capable) | New |
| 3.9 | `test_entry_aware_non_tracked_unchanged` | Non-tracked expense, no entries | calculate_balances | Uses effective_amount | New |
| 3.10 | `test_existing_balance_tests_unmodified` | All existing test_balance_calculator.py tests | Run full file | All pass | Existing |
| 3.11 | `test_entry_aware_entries_not_loaded` | Tracked txn, entries NOT selectinloaded | calculate_balances | Uses effective_amount (graceful fallback) | New |
| 3.12 | `test_multiple_tracked_txns_in_period` | 2 tracked expenses in same period | calculate_balances | Each uses its own entry formula | New |

### F. Manual verification steps

1. Start dev server (`flask run`).
2. Flag a template with `track_individual_purchases=True` (via database or Commit 6 UI).
3. Add entries to a transaction generated from that template (via database or Commit 8 UI).
4. Navigate the grid -- verify the balance row reflects the entry-aware formula.
5. Compare balance with and without credit entries to confirm the formula.

### G. Downstream effects

The balance calculator now produces different results for entry-capable transactions with
entries. The grid balance row and dashboard balance/runway will reflect entry-aware amounts.
All other services (calendar, trends, variance, year-end) are unaffected because they do not
selectinload entries and thus get `effective_amount` fallback behavior.

### H. Rollback notes

Revert the `_entry_aware_amount` function and the `selectinload` additions. The calculator
reverts to `effective_amount` for all transactions.

---

## Commit 4: Entry-Level Credit Card Workflow

### A. Commit message

```
feat(credit): add entry-level credit card payback with per-transaction aggregation
```

### B. Problem statement

When individual entries are flagged as credit card purchases, they need to generate an
aggregated CC Payback transaction in the next pay period. This payback must update dynamically
as credit entries are added, edited, or deleted. The legacy per-transaction Credit status must
be blocked on entry-capable transactions to prevent double-counting.

### C. Files modified

| File | Change |
|------|--------|
| `app/services/entry_credit_workflow.py` | **Create.** Entry-level CC Payback aggregation. |
| `app/services/entry_service.py` | Add post-mutation hooks calling `sync_entry_payback`. |
| `app/services/credit_workflow.py` | Add guard rejecting entry-capable transactions. |
| `tests/test_services/test_entry_credit_workflow.py` | **Create.** CC Payback lifecycle tests. |

### D. Implementation approach

**entry_credit_workflow.py:**

```python
def sync_entry_payback(transaction_id: int, user_id: int) -> Transaction | None:
    """Synchronize the aggregated CC Payback for a transaction's credit entries.

    Called after every entry mutation (create, update, delete, is_credit toggle).
    Ensures exactly one CC Payback exists when credit entries are present,
    and no payback exists when they are not.

    Logic:
      1. Sum all credit entries for the parent transaction.
      2. If total > 0 and no payback exists: create one in the next period.
      3. If total > 0 and payback exists: update its estimated_amount.
      4. If total == 0 and payback exists: delete the payback.
      5. If total == 0 and no payback: no-op.

    The payback is identified by credit_payback_for_id == transaction_id.
    All credit entries share the same credit_payback_id pointing to this payback.

    Args:
        transaction_id: The parent transaction's ID.
        user_id: The user ID for ownership verification.

    Returns:
        The CC Payback Transaction if one exists after sync, else None.
    """
```

Implementation:
```python
txn = db.session.get(Transaction, transaction_id)
owner_id = _resolve_owner_id(user_id)  # Reuse from entry_service

# Sum credit entries.
credit_entries = [e for e in txn.entries if e.is_credit]
total_credit = sum(e.amount for e in credit_entries)

# Find existing payback (same pattern as credit_workflow.py line 67-73).
existing_payback = (
    db.session.query(Transaction)
    .filter_by(credit_payback_for_id=txn.id)
    .first()
)

if total_credit > 0:
    if existing_payback is None:
        # Create new payback in next period.
        next_period = pay_period_service.get_next_period(txn.pay_period)
        if next_period is None:
            raise ValidationError("No next pay period. Generate more periods first.")

        cc_category = _get_or_create_cc_category(owner_id)
        payback = Transaction(
            account_id=txn.account_id,
            template_id=None,
            pay_period_id=next_period.id,
            scenario_id=txn.scenario_id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name=f"CC Payback: {txn.name}",
            category_id=cc_category.id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            estimated_amount=total_credit,
            credit_payback_for_id=txn.id,
        )
        db.session.add(payback)
        db.session.flush()

        # Link all credit entries to the payback.
        for entry in credit_entries:
            entry.credit_payback_id = payback.id

        return payback
    else:
        # Update existing payback amount.
        existing_payback.estimated_amount = total_credit
        return existing_payback
else:
    if existing_payback is not None:
        # Clear links and delete payback.
        for entry in txn.entries:
            if entry.credit_payback_id == existing_payback.id:
                entry.credit_payback_id = None
        db.session.delete(existing_payback)
    return None
```

Reuses `_get_or_create_cc_category` from `credit_workflow.py` -- extract to a shared utility
or import from `credit_workflow`.

**Integration with entry_service.py:**

After every create_entry, update_entry, and delete_entry, call:
```python
from app.services.entry_credit_workflow import sync_entry_payback
sync_entry_payback(transaction_id, user_id)
```

This is called after the entry mutation but before the caller's `db.session.commit()`. The sync
flushes but does not commit.

**Guard in credit_workflow.py** (`mark_as_credit`, after line 60):

```python
# Block legacy credit on entry-capable transactions.
if txn.template is not None and txn.template.track_individual_purchases:
    raise ValidationError(
        "This transaction uses individual purchase tracking. "
        "Mark individual entries as credit instead of the whole transaction."
    )
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 4.1 | `test_sync_creates_payback_first_credit_entry` | Tracked txn, no entries | Create credit entry ($100) | Payback created in next period, est=100 | New |
| 4.2 | `test_sync_updates_payback_on_second_credit` | Payback exists ($100) | Create second credit entry ($50) | Payback est updated to 150 | New |
| 4.3 | `test_sync_deletes_payback_when_last_credit_removed` | Payback exists ($100), 1 credit entry | Delete the credit entry | Payback deleted | New |
| 4.4 | `test_sync_updates_on_credit_entry_edit` | Payback ($100), credit entry=$100 | Update entry amount to $75 | Payback est updated to 75 | New |
| 4.5 | `test_sync_handles_credit_toggle_debit_to_credit` | Debit entry, no payback | Toggle is_credit to True | Payback created | New |
| 4.6 | `test_sync_handles_credit_toggle_credit_to_debit` | 1 credit entry, payback exists | Toggle is_credit to False | Payback deleted | New |
| 4.7 | `test_sync_idempotent` | Payback exists, matching amount | Call sync_entry_payback again | No change | New |
| 4.8 | `test_sync_no_next_period_raises` | Tracked txn in last period | Create credit entry | ValidationError (no next period) | New |
| 4.9 | `test_legacy_credit_blocked_on_tracked` | Tracked txn | mark_as_credit | ValidationError | New |
| 4.10 | `test_legacy_credit_still_works_non_tracked` | Non-tracked txn | mark_as_credit | Payback created (existing behavior) | New |
| 4.11 | `test_payback_links_all_credit_entries` | 3 credit entries | Check credit_payback_id | All 3 entries link to same payback | New |
| 4.12 | `test_mixed_entries_only_credit_sum_in_payback` | 2 debit ($200), 2 credit ($100, $50) | Check payback | Payback est=150 (only credit) | New |

### F. Manual verification steps

1. Create credit entries on an entry-capable transaction.
2. Verify a CC Payback appears in the next pay period.
3. Add another credit entry -- verify payback amount increases.
4. Delete all credit entries -- verify payback is deleted.
5. Try to mark the entry-capable transaction as Credit (legacy) -- verify rejection.

### G. Downstream effects

CC Payback transactions are regular PROJECTED expenses in the next period. The balance
calculator processes them identically to any other projected expense (no special handling).
The grid displays them with the "CC" badge (existing `credit_payback_for_id` indicator).

### H. Rollback notes

Remove `entry_credit_workflow.py`, revert the hooks in `entry_service.py`, and revert the guard
in `credit_workflow.py`.

---

## Commit 5: Mark-Paid Extension and Status Prevention

### A. Commit message

```
feat(transactions): auto-populate actual amount from entries on mark-paid; block Credit status on tracked transactions
```

### B. Problem statement

When an entry-capable transaction is marked as Paid, the actual_amount should be auto-computed
from the sum of all entries (debit + credit). The Credit status must be blocked in all code
paths that can set it on entry-capable transactions.

### C. Files modified

| File | Change |
|------|--------|
| `app/routes/transactions.py` | Modify `mark_done` for entry-aware actual. Modify `update_transaction` for Credit status guard. |
| `app/services/entry_service.py` | Add `update_actual_if_paid` helper; call after entry mutations on paid transactions. |
| `app/templates/grid/_transaction_full_edit.html` | Hide "Credit" button for entry-capable transactions. |
| `tests/test_routes/test_entries.py` | Tests for mark-paid and status guard (can also go in a new file). |

### D. Implementation approach

**mark_done modification** (`transactions.py`, after line 262):

```python
# Auto-populate actual from entries for entry-capable transactions.
if (txn.template is not None
        and txn.template.track_individual_purchases
        and txn.entries):
    from app.services.entry_service import compute_actual_from_entries
    txn.actual_amount = compute_actual_from_entries(txn.entries)
# If no entries, fall through to the existing manual actual_amount flow.
```

Note: `txn.entries` is loaded via selectinload (added in Commit 3). If entries exist, the
computed sum overrides any manual `actual_amount` from the form. If no entries exist, the
existing behavior (accept optional actual from form) is preserved.

**update_transaction status guard** (`transactions.py`, in the PATCH handler):

```python
if "status_id" in data:
    credit_id = ref_cache.status_id(StatusEnum.CREDIT)
    if (int(data["status_id"]) == credit_id
            and txn.template is not None
            and txn.template.track_individual_purchases):
        return ("Cannot set Credit status on transactions with individual "
                "purchase tracking. Use entry-level credit instead."), 400
```

**Entry mutations on Paid transactions** (`entry_service.py`):

After create_entry, update_entry, and delete_entry, if the parent transaction is in Paid/Done
status, update its actual_amount:

```python
def _update_actual_if_paid(txn: Transaction) -> None:
    """Re-compute actual_amount if the transaction is already Paid.

    Handles the edge case of entries added after the transaction was marked Paid
    (late-posting purchases). Per scope doc: the entry sum takes precedence over
    any manually entered actual.
    """
    done_id = ref_cache.status_id(StatusEnum.DONE)
    if txn.status_id == done_id and txn.entries:
        txn.actual_amount = compute_actual_from_entries(txn.entries)
```

**Template modification** (`_transaction_full_edit.html`, line 94-109):

Hide the Credit button for entry-capable transactions:

```html
{% if not txn.transfer_id and txn.is_expense and txn.status_id == STATUS_PROJECTED
   and not (txn.template and txn.template.track_individual_purchases) %}
  <button ...>Credit</button>
{% endif %}
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 5.1 | `test_mark_done_auto_populates_actual` | Tracked txn, entries sum=$400 | POST mark-done | actual_amount=400 | New |
| 5.2 | `test_mark_done_actual_includes_credit_entries` | Entries: $300 debit + $100 credit | POST mark-done | actual_amount=400 (all entries) | New |
| 5.3 | `test_mark_done_no_entries_manual_actual` | Tracked txn, no entries | POST mark-done actual=350 | actual_amount=350 (manual) | New |
| 5.4 | `test_mark_done_no_entries_no_actual` | Tracked txn, no entries | POST mark-done (no actual) | actual_amount=None | New |
| 5.5 | `test_mark_done_entries_override_form_actual` | Entries sum=$400 | POST mark-done actual=999 | actual_amount=400 (entries win) | New |
| 5.6 | `test_update_rejects_credit_status_tracked` | Tracked txn | PATCH status_id=CREDIT | 400 error | New |
| 5.7 | `test_update_allows_credit_status_non_tracked` | Non-tracked txn | PATCH status_id=CREDIT | Status updated | New |
| 5.8 | `test_entry_added_after_paid_updates_actual` | Paid tracked txn, actual=300 | Add entry ($50) | actual_amount updated to 350 | New |
| 5.9 | `test_entry_deleted_after_paid_updates_actual` | Paid tracked txn, entries sum=400 | Delete entry ($100) | actual_amount updated to 300 | New |
| 5.10 | `test_non_tracked_mark_done_unchanged` | Non-tracked txn | POST mark-done | Existing behavior (manual actual) | New |

### F. Manual verification steps

1. Mark an entry-capable transaction with entries as Paid -- verify actual_amount matches entry sum.
2. Try setting Credit status via the full edit dropdown on an entry-capable transaction -- verify rejection.
3. Verify the Credit button is hidden in the full edit popover for entry-capable transactions.
4. Mark a non-entry-capable transaction as Paid -- verify existing behavior unchanged.

### G. Downstream effects

Once marked Paid, the transaction's actual_amount is set. `effective_amount` returns
actual_amount (existing logic). All services (dashboard, calendar, trends, etc.) see the
correct actual spending amount.

### H. Rollback notes

Revert the mark_done and update_transaction changes in `transactions.py`, the
`_update_actual_if_paid` helper in `entry_service.py`, and the template conditional.

---

## Commit 6: Template Settings UI -- Tracking and Visibility Toggles

### A. Commit message

```
feat(templates): add tracking and companion visibility toggles to template form
```

### B. Problem statement

Users need a way to flag templates with `track_individual_purchases` and `companion_visible`.
These toggles control which transactions support sub-entries and which are visible to the
companion user.

### C. Files modified

| File | Change |
|------|--------|
| `app/templates/templates/form.html` | Add checkbox toggles. |
| `app/templates/templates/list.html` | Add indicator badges. |
| `app/schemas/validation.py` | Add boolean fields to TemplateCreateSchema. |
| `app/routes/templates.py` | Add fields to `_TEMPLATE_UPDATE_FIELDS`. |
| `tests/test_routes/test_templates.py` | Add tests for new toggle fields. |

### D. Implementation approach

**Template form** (`templates/form.html`):

Insert a new section between the category dropdown (line 84) and the recurrence rule section
(line 87):

```html
{# Tracking & Visibility flags #}
<hr>
<h5>Tracking & Visibility</h5>

<div class="form-check mb-3">
  <input type="checkbox" class="form-check-input"
         id="track_individual_purchases" name="track_individual_purchases"
         {{ 'checked' if template and template.track_individual_purchases else '' }}>
  <label class="form-check-label" for="track_individual_purchases">
    Track individual purchases
  </label>
  <div class="form-text">
    Record individual purchases against this budget and see remaining balance each period.
  </div>
</div>

<div class="form-check mb-3">
  <input type="checkbox" class="form-check-input"
         id="companion_visible" name="companion_visible"
         {{ 'checked' if template and template.companion_visible else '' }}>
  <label class="form-check-label" for="companion_visible">
    Visible to companion
  </label>
  <div class="form-text">
    Show transactions from this template in the companion view.
  </div>
</div>
```

**Template list** (`templates/list.html`):

Add small badge indicators next to template names:

```html
{% if t.track_individual_purchases %}
  <span class="badge bg-info-subtle text-info ms-1" title="Tracks purchases">
    <i class="bi bi-cart"></i>
  </span>
{% endif %}
{% if t.companion_visible %}
  <span class="badge bg-success-subtle text-success ms-1" title="Companion visible">
    <i class="bi bi-eye"></i>
  </span>
{% endif %}
```

**Schema additions** in `validation.py`:

Add to `TemplateCreateSchema` (line ~110):

```python
track_individual_purchases = fields.Boolean(load_default=False)
companion_visible = fields.Boolean(load_default=False)
```

`TemplateUpdateSchema` inherits from `TemplateCreateSchema` and gets these automatically.

**Route update** (`templates.py`, line 289):

```python
_TEMPLATE_UPDATE_FIELDS = {
    "name", "default_amount", "category_id", "transaction_type_id",
    "account_id", "is_active", "sort_order",
    "track_individual_purchases", "companion_visible",
}
```

**Validation rule**: In `create_template` and `update_template`, if `track_individual_purchases`
is True, verify the transaction type is Expense:

```python
if data.get("track_individual_purchases"):
    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    type_id = data.get("transaction_type_id",
                       template.transaction_type_id if template else None)
    if type_id != expense_type_id:
        flash("Purchase tracking is only available for expense templates.", "danger")
        return redirect(...)
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 6.1 | `test_create_template_with_tracking` | POST with track=on | Create template | track_individual_purchases=True | New |
| 6.2 | `test_create_template_tracking_default_false` | POST without track field | Create template | track_individual_purchases=False | New |
| 6.3 | `test_update_template_toggle_tracking` | Existing template (track=False) | POST with track=on | track_individual_purchases=True | New |
| 6.4 | `test_update_template_toggle_companion` | Existing template (visible=False) | POST with companion=on | companion_visible=True | New |
| 6.5 | `test_tracking_rejected_on_income_template` | Income template | POST with track=on | Flash error, tracking not set | New |
| 6.6 | `test_template_list_shows_badges` | Templates with mixed flags | GET /templates | Badges visible for flagged templates | New |

### F. Manual verification steps

1. Edit a template -- verify the checkboxes appear with correct state.
2. Enable tracking and save -- verify the flag persists on reload.
3. Try enabling tracking on an income template -- verify the error.
4. Check the template list -- verify badge indicators appear.

### G. Downstream effects

Once templates are flagged, new transactions generated by the recurrence engine will be
entry-capable. The recurrence engine copies `template_id` to the transaction, so
`txn.template.track_individual_purchases` is accessible at runtime.

### H. Rollback notes

Revert the form, list, schema, and route changes. Template flags in the database remain but
have no effect since no code reads them.

---

## Commit 7: Grid Progress Indicator and Tooltip Enhancement

### A. Commit message

```
feat(grid): add entry progress indicator and enhanced tooltip for tracked transactions
```

### B. Problem statement

Entry-capable transactions with entries should display a progress format ("$330 / $500") in the
grid cell instead of the standard single amount. The tooltip should include entry-specific data
(spent, remaining, entry count, credit total).

### C. Files modified

| File | Change |
|------|--------|
| `app/templates/grid/_transaction_cell.html` | Add progress display for tracked transactions. |
| `app/templates/grid/_mobile_grid.html` | Same progress display for mobile variant. |
| `app/templates/grid/grid.html` | Pass `entry_sums` to template context. |
| `app/routes/grid.py` | Compute `entry_sums` dict from loaded entries. |
| `app/static/css/app.css` | Add over-budget warning styling. |

### D. Implementation approach

**Pre-computation in grid.py** (after transaction loading, before render_template):

```python
# Pre-compute entry sums for tracked transactions.
entry_sums = {}
for period_txns in txn_by_period.values():
    for txn in period_txns:
        if txn.entries:
            debit = sum(e.amount for e in txn.entries if not e.is_credit)
            credit = sum(e.amount for e in txn.entries if e.is_credit)
            entry_sums[txn.id] = {
                "debit": debit,
                "credit": credit,
                "total": debit + credit,
                "count": len(txn.entries),
            }
```

Pass `entry_sums=entry_sums` to the `render_template` call.

**Cell template modification** (`_transaction_cell.html`):

Replace the amount display section (lines 22-31) with a conditional:

```html
{% set es = entry_sums.get(t.id) if entry_sums is defined else none %}
{% if es and t.status_id == STATUS_PROJECTED %}
  {# Entry progress display #}
  {% set remaining = t.estimated_amount - es.total %}
  {% set over_budget = remaining < 0 %}
  <span class="font-mono{% if over_budget %} text-danger fw-semibold{% endif %}">
    {{ "{:,.0f}".format(es.total) }} / {{ "{:,.0f}".format(t.estimated_amount) }}
  </span>
{% elif t.actual_amount is not none and t.actual_amount != t.estimated_amount %}
  {# Standard: crossed-out estimate + actual #}
  <span class="text-decoration-line-through small" ...>{{ ... }}</span>
  <span class="fw-semibold font-mono">{{ ... }}</span>
{% else %}
  {# Standard: estimated amount #}
  {% if t.estimated_amount|float != 0 %}
    <span class="font-mono">{{ "{:,.0f}".format(t.estimated_amount) }}</span>
  {% endif %}
{% endif %}
```

**Tooltip enhancement** (title and aria-label attributes):

For tracked transactions with entries:
```html
{% if es %}
title="${{ '{:,.0f}'.format(es.total) }} / ${{ '{:,.0f}'.format(t.estimated_amount) }} -- ${{ '{:,.0f}'.format(remaining) }} {% if over_budget %}over{% else %}remaining{% endif %} -- {{ es.count }} entr{{ 'ies' if es.count != 1 else 'y' }}{% if es.credit > 0 %} -- includes ${{ '{:,.0f}'.format(es.credit) }} on CC{% endif %}"
{% endif %}
```

**CSS** in `app.css`:

```css
/* Entry progress -- over-budget warning */
.text-danger.fw-semibold {
    /* Uses Bootstrap's existing danger color. No additional styling needed
       unless the design calls for a background highlight. */
}
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 7.1 | `test_grid_entry_sums_computed` | Tracked txn with 3 entries | GET /grid | entry_sums contains correct debit/credit/total/count | New |
| 7.2 | `test_grid_cell_shows_progress` | Tracked txn, 2 entries ($200) | GET /grid | Cell shows "200 / 500" | New |
| 7.3 | `test_grid_cell_no_entries_shows_estimated` | Tracked txn, no entries | GET /grid | Cell shows "500" (standard) | New |
| 7.4 | `test_grid_cell_paid_shows_actual` | Tracked txn, status=DONE, actual=450 | GET /grid | Cell shows actual (not progress) | New |
| 7.5 | `test_grid_tooltip_includes_entry_data` | Tracked txn, entries present | GET /grid | Title contains "remaining" and entry count | New |

### F. Manual verification steps

1. Add entries to a tracked transaction -- verify progress format in grid cell.
2. Check over-budget display (red styling when entries > estimated).
3. Hover over cell -- verify tooltip shows entry breakdown.
4. Verify mobile grid shows the same progress display.
5. Mark the transaction as Paid -- verify cell reverts to standard actual display.

### G. Downstream effects

Display-only changes. No computation or data flow changes.

### H. Rollback notes

Revert the cell template, grid route, and CSS changes.

---

## Commit 8: Entry CRUD UI -- Grid Transaction Detail with Entry Management

### A. Commit message

```
feat(entries): add entry CRUD endpoints and inline entry management in transaction popover
```

### B. Problem statement

Users need a way to add, edit, and delete sub-entries on entry-capable transactions. The entry
management UI integrates into the existing full edit popover on the grid and will be reused by
the companion view.

### C. Files modified

| File | Change |
|------|--------|
| `app/routes/entries.py` | **Create.** CRUD endpoints for entries. |
| `app/templates/grid/_transaction_entries.html` | **Create.** HTMX partial: entry list + add form. |
| `app/templates/grid/_transaction_full_edit.html` | Add entries section for tracked transactions. |
| `app/__init__.py` | Register entries_bp blueprint. |
| `tests/test_routes/test_entries.py` | **Create.** Entry route integration tests. |

### D. Implementation approach

**Entry routes** (`entries.py`):

```python
entries_bp = Blueprint("entries", __name__)

@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["GET"])
@login_required
def list_entries(txn_id):
    """HTMX partial: return the entry list for a transaction."""
    txn = _get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    entries = entry_service.get_entries_for_transaction(txn.id, current_user.id)
    remaining = entry_service.compute_remaining(txn.estimated_amount, entries)
    return render_template(
        "grid/_transaction_entries.html",
        txn=txn, entries=entries, remaining=remaining,
        today=date.today().isoformat(),
    )


@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["POST"])
@login_required
def create_entry(txn_id):
    """Create a new entry and return the updated entry list."""
    txn = _get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    errors = _create_schema.validate(request.form)
    if errors:
        return str(errors), 422

    data = _create_schema.load(request.form)
    entry_service.create_entry(
        transaction_id=txn.id,
        user_id=current_user.id,
        **data,
    )
    db.session.commit()

    # Return updated entry list.
    entries = entry_service.get_entries_for_transaction(txn.id, current_user.id)
    remaining = entry_service.compute_remaining(txn.estimated_amount, entries)
    response = render_template(
        "grid/_transaction_entries.html",
        txn=txn, entries=entries, remaining=remaining,
        today=date.today().isoformat(),
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}


@entries_bp.route("/transactions/<int:txn_id>/entries/<int:entry_id>", methods=["DELETE"])
@login_required
def delete_entry(txn_id, entry_id):
    """Delete an entry and return the updated entry list."""
    txn = _get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    entry_service.delete_entry(entry_id, current_user.id)
    db.session.commit()

    entries = entry_service.get_entries_for_transaction(txn.id, current_user.id)
    remaining = entry_service.compute_remaining(txn.estimated_amount, entries)
    response = render_template(
        "grid/_transaction_entries.html",
        txn=txn, entries=entries, remaining=remaining,
        today=date.today().isoformat(),
    )
    return response, 200, {"HX-Trigger": "balanceChanged"}
```

The `_get_accessible_transaction` helper validates ownership for both owners and companions:

```python
def _get_accessible_transaction(txn_id):
    """Get a transaction accessible to the current user (owner or companion)."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    companion_id = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id == companion_id:
        if (txn.pay_period.user_id != current_user.linked_owner_id
                or not txn.template
                or not txn.template.companion_visible):
            return None
    else:
        if txn.pay_period.user_id != current_user.id:
            return None
    return txn
```

**Entry list template** (`_transaction_entries.html`):

```html
{# Entry list with inline add form.
   Expected context: txn, entries, remaining, today.
   Returns full entry list container for HTMX swap. #}
<div id="entry-list-{{ txn.id }}">
  {% for entry in entries %}
  <div class="d-flex justify-content-between align-items-center py-1 border-bottom small">
    <div class="text-truncate me-2" style="max-width: 120px;">
      {{ entry.description }}
    </div>
    <div class="d-flex align-items-center gap-1">
      {% if entry.is_credit %}
        <span class="badge bg-warning-subtle text-warning" title="Credit card">CC</span>
      {% endif %}
      <span class="font-mono">${{ "{:,.2f}".format(entry.amount) }}</span>
      <button class="btn btn-link btn-sm p-0 text-danger"
              hx-delete="{{ url_for('entries.delete_entry',
                           txn_id=txn.id, entry_id=entry.id) }}"
              hx-target="#entry-list-{{ txn.id }}"
              hx-swap="outerHTML"
              hx-confirm="Delete this entry?"
              aria-label="Delete entry">
        <i class="bi bi-x-circle"></i>
      </button>
    </div>
  </div>
  {% endfor %}

  {# Remaining balance #}
  <div class="d-flex justify-content-between py-1 small fw-semibold">
    <span>Remaining</span>
    <span class="font-mono{% if remaining < 0 %} text-danger{% endif %}">
      {% if remaining < 0 %}-{% endif %}${{ "{:,.2f}".format(remaining|abs) }}
    </span>
  </div>

  {# Add entry form #}
  <form class="mt-2"
        hx-post="{{ url_for('entries.create_entry', txn_id=txn.id) }}"
        hx-target="#entry-list-{{ txn.id }}"
        hx-swap="outerHTML"
        hx-disabled-elt="find button[type=submit]">
    <div class="d-flex gap-1 align-items-end">
      <input type="number" step="0.01" name="amount" min="0.01"
             class="form-control form-control-sm" placeholder="$" required
             style="max-width: 80px;">
      <input type="text" name="description"
             class="form-control form-control-sm" placeholder="Store" required>
      <input type="hidden" name="entry_date" value="{{ today }}">
      <div class="form-check">
        <input type="checkbox" name="is_credit" class="form-check-input"
               id="cc-{{ txn.id }}">
        <label class="form-check-label small" for="cc-{{ txn.id }}">CC</label>
      </div>
      <button type="submit" class="btn btn-sm btn-primary">
        <i class="bi bi-plus"></i>
      </button>
    </div>
  </form>
</div>
```

**Full edit popover integration** (`_transaction_full_edit.html`):

After the quick status actions section (line 131), add:

```html
{# Entry tracking section #}
{% if txn.template and txn.template.track_individual_purchases %}
<hr class="my-2">
<div class="small fw-semibold mb-1">Purchases</div>
<div hx-get="{{ url_for('entries.list_entries', txn_id=txn.id) }}"
     hx-trigger="load"
     hx-target="this"
     id="entry-list-{{ txn.id }}">
  <span class="spinner-border spinner-border-sm" role="status"></span>
  <span class="small text-muted">Loading entries...</span>
</div>
{% endif %}
```

**Blueprint registration** (`__init__.py`):

```python
from app.routes.entries import entries_bp
app.register_blueprint(entries_bp)
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 8.1 | `test_list_entries_returns_partial` | Tracked txn + 2 entries | GET /transactions/X/entries | 200, HTML contains entry descriptions | New |
| 8.2 | `test_create_entry_via_form` | Tracked txn | POST amount=50, desc=Kroger | 200, entry list updated, HX-Trigger: balanceChanged | New |
| 8.3 | `test_create_entry_validation_error` | Tracked txn | POST amount=0 | 422 | New |
| 8.4 | `test_delete_entry_via_route` | Entry exists | DELETE /transactions/X/entries/Y | 200, entry removed | New |
| 8.5 | `test_entry_route_rejects_non_tracked` | Non-tracked txn | POST entry | 400 (ValidationError) | New |
| 8.6 | `test_entry_route_rejects_other_user` | Other user's txn | POST entry | 404 | New |
| 8.7 | `test_popover_loads_entries_section` | Tracked txn | GET /transactions/X/full-edit | HTML contains hx-get for entries | New |
| 8.8 | `test_popover_no_entries_section_non_tracked` | Non-tracked txn | GET /transactions/X/full-edit | No entries section | New |
| 8.9 | `test_create_entry_triggers_balance_refresh` | Tracked txn | POST entry | Response has HX-Trigger: balanceChanged | New |

### F. Manual verification steps

1. Open a tracked transaction's full edit popover -- verify entries section loads.
2. Add an entry via the inline form -- verify it appears in the list.
3. Delete an entry -- verify it disappears and balance row updates.
4. Toggle the CC checkbox on an entry -- verify CC badge appears.
5. Check mobile grid -- verify entries section works in the popover.

### G. Downstream effects

The `balanceChanged` HX-Trigger causes the balance row to refresh via HTMX, reflecting the
entry-aware formula from Commit 3. The grid cell also updates via `gridRefresh` if the progress
indicator changes.

### H. Rollback notes

Remove `entries.py`, `_transaction_entries.html`, revert `_transaction_full_edit.html`, and
deregister the blueprint.

---

## Commit 9: Companion Role, Route Guards, and Login Routing

### A. Commit message

```
feat(companion): add companion role with route guards and role-based login routing
```

### B. Problem statement

Companion users must be restricted to the companion view. All existing full-access routes need
guards that check the user's role. The login flow must redirect companions to their dedicated
view.

### C. Files modified

| File | Change |
|------|--------|
| `app/utils/auth_helpers.py` | Add `require_owner` decorator. |
| `app/routes/auth.py` | Add companion login routing. |
| `app/routes/grid.py` | Add `@require_owner`. |
| `app/routes/templates.py` | Add `@require_owner`. |
| `app/routes/settings.py` | Add `@require_owner`. |
| `app/routes/dashboard.py` | Add `@require_owner`. |
| `app/routes/accounts.py` | Add `@require_owner`. |
| `app/routes/analytics.py` | Add `@require_owner`. |
| `app/routes/categories.py` | Add `@require_owner`. |
| `app/routes/debt_strategy.py` | Add `@require_owner`. |
| `app/routes/investment.py` | Add `@require_owner`. |
| `app/routes/loan.py` | Add `@require_owner`. |
| `app/routes/obligations.py` | Add `@require_owner`. |
| `app/routes/pay_periods.py` | Add `@require_owner`. |
| `app/routes/retirement.py` | Add `@require_owner`. |
| `app/routes/salary.py` | Add `@require_owner`. |
| `app/routes/savings.py` | Add `@require_owner`. |
| `app/routes/transfers.py` | Add `@require_owner`. |
| `app/routes/charts.py` | Add `@require_owner`. |
| `app/templates/base.html` | Conditionally hide full nav for companions. |
| `scripts/seed_companion.py` | **Create.** Seed script for companion account. |
| `tests/test_routes/test_companion_guards.py` | **Create.** Comprehensive guard tests. |

### D. Implementation approach

**require_owner decorator** (`auth_helpers.py`):

```python
from functools import wraps
from flask import abort
from flask_login import current_user

from app import ref_cache
from app.enums import RoleEnum


def require_owner(f):
    """Restrict route to owner-role users.

    Must be applied AFTER @login_required to ensure current_user is set.
    Companions receive 404 (not 403) to avoid revealing route existence,
    matching the project's security response rule.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        owner_id = ref_cache.role_id(RoleEnum.OWNER)
        if getattr(current_user, "role_id", owner_id) != owner_id:
            abort(404)
        return f(*args, **kwargs)
    return decorated
```

Note: Returns 404 (not 403) per CLAUDE.md: "Security response rule: 404 for both 'not found'
and 'not yours.'" The `getattr` fallback to `owner_id` ensures safe behavior if `role_id` is
absent (e.g., in test fixtures that don't set it).

**Application pattern** (same on all 17 route files):

```python
from app.utils.auth_helpers import require_owner

@grid_bp.route("/grid")
@login_required
@require_owner
def index():
    ...
```

The decorator must be AFTER `@login_required` in the decorator chain (which means it executes
AFTER login_required confirms the user is authenticated).

**Routes NOT guarded** (companion can access):
- `auth_bp` -- login, logout, MFA (authentication itself).
- `health_bp` -- health check endpoint.
- `entries_bp` -- entry CRUD (companion's primary interaction). Entry routes have their own
  companion-aware access check via `_get_accessible_transaction`.
- `transactions_bp.mark_done` -- companion can mark transactions as Paid. This specific route
  needs a companion-aware check instead of `require_owner`.

**Mark-done companion access** (`transactions.py`):

The `mark_done` route needs to allow companions to mark companion-visible transactions as Paid.
Replace the existing `_get_owned_transaction` call with a companion-aware version:

```python
def _get_accessible_transaction_for_status(txn_id):
    """Owner or companion access for status changes."""
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    companion_id = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id == companion_id:
        if (txn.pay_period.user_id != current_user.linked_owner_id
                or not txn.template
                or not txn.template.companion_visible):
            return None
    else:
        if txn.pay_period.user_id != current_user.id:
            return None
    return txn
```

**Login routing** (`auth.py`):

After `login_user(user, remember=remember)` in both the direct login path (line 105) and
after MFA verification, add:

```python
companion_id = ref_cache.role_id(RoleEnum.COMPANION)
if user.role_id == companion_id:
    return redirect(url_for("companion.index"))
```

Also update the "already authenticated" check (line 76-77):
```python
if current_user.is_authenticated:
    companion_id = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id == companion_id:
        return redirect(url_for("companion.index"))
    return redirect(url_for("dashboard.page"))
```

**Nav bar** (`base.html`):

Wrap the full navigation in a companion check. The template uses the role_id integer comparison
via a context variable set in the base template's before_request or passed from the route.
For simplicity, compare against the role relationship's name (display-only usage in templates
is acceptable -- the template is not performing business logic):

```html
{% if current_user.is_authenticated and current_user.role_id != COMPANION_ROLE_ID %}
  {# Full navigation: Dashboard, Budget, Recurring, etc. #}
  ...
{% elif current_user.is_authenticated %}
  {# Companion nav: just the companion view link and logout #}
  <li class="nav-item">
    <a class="nav-link" href="{{ url_for('companion.index') }}">My Budget</a>
  </li>
{% endif %}
```

**Seed script** (`scripts/seed_companion.py`):

```python
"""Create a companion user account linked to the primary owner.

Usage: python scripts/seed_companion.py

Prompts for companion email, display name, and password.
Sets role_id to ref_cache.role_id(RoleEnum.COMPANION) and
links the companion to the first owner account found.
Idempotent: if the companion email already exists, updates the
role_id and linked_owner_id.
"""
```

The `COMPANION_ROLE_ID` template context variable referenced in `base.html` is injected via a
`@app.context_processor` in `app/__init__.py`:

```python
@app.context_processor
def inject_role_ids():
    """Make role IDs available in all templates."""
    return {"COMPANION_ROLE_ID": ref_cache.role_id(RoleEnum.COMPANION)}
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 9.1 | `test_companion_blocked_from_grid` | Companion client | GET /grid | 404 | New |
| 9.2 | `test_companion_blocked_from_templates` | Companion client | GET /templates | 404 | New |
| 9.3 | `test_companion_blocked_from_settings` | Companion client | GET /settings | 404 | New |
| 9.4 | `test_companion_blocked_from_dashboard` | Companion client | GET /dashboard | 404 | New |
| 9.5 | `test_companion_blocked_from_accounts` | Companion client | GET /accounts | 404 | New |
| 9.6 | `test_companion_blocked_from_analytics` | Companion client | GET /analytics | 404 | New |
| 9.7 | `test_companion_blocked_from_salary` | Companion client | GET /salary | 404 | New |
| 9.8 | `test_companion_blocked_from_transfers` | Companion client | GET /transfers | 404 | New |
| 9.9 | `test_companion_blocked_from_retirement` | Companion client | GET /retirement | 404 | New |
| 9.10 | `test_owner_can_access_grid` | Owner client | GET /grid | 200 | New |
| 9.11 | `test_owner_can_access_all_routes` | Owner client | GET each route | 200 | New |
| 9.12 | `test_companion_redirect_on_login` | Companion user | POST /login | Redirect to /companion | New |
| 9.13 | `test_owner_redirect_on_login` | Owner user | POST /login | Redirect to /dashboard | New |
| 9.14 | `test_companion_can_mark_visible_txn_done` | Companion, visible txn | POST mark-done | 200 | New |
| 9.15 | `test_companion_cannot_mark_non_visible_txn` | Companion, non-visible txn | POST mark-done | 404 | New |

### F. Manual verification steps

1. Log in as companion -- verify redirect to companion view (not dashboard).
2. Manually navigate to /grid, /settings, /templates -- verify 404.
3. Verify the nav bar shows only "My Budget" and "Logout" for companion.
4. Log in as owner -- verify full navigation and dashboard redirect.
5. Run the seed companion script -- verify account creation.

### G. Downstream effects

All 17 existing route files gain a `@require_owner` decorator on their endpoints. The login
flow gains role-based routing. The nav bar conditionally renders based on role_id.

### H. Rollback notes

Remove `@require_owner` from all routes, revert login routing, revert nav bar changes, remove
the context processor, delete the seed script. The `role_id` column remains in the database but
has no effect (all users have role_id=1, owner).

---

## Commit 10: Companion View -- Mobile-First Interface with Entry Management

### A. Commit message

```
feat(companion): add mobile-first companion view with period navigation and entry management
```

### B. Problem statement

The companion user (Kayla) needs a simplified, mobile-first interface showing only her tagged
transactions with the ability to add entries and mark transactions as Paid. She navigates
between pay periods and sees progress indicators for budget spending.

### C. Files modified

| File | Change |
|------|--------|
| `app/services/companion_service.py` | **Create.** Data access with visibility filtering. |
| `app/routes/companion.py` | **Create.** Companion view routes. |
| `app/templates/companion/index.html` | **Create.** Main companion view template. |
| `app/templates/companion/_transaction_card.html` | **Create.** Transaction card with progress. |
| `app/templates/companion/_period_nav.html` | **Create.** HTMX period navigation. |
| `app/__init__.py` | Register companion_bp blueprint. |
| `tests/test_services/test_companion_service.py` | **Create.** Data isolation tests. |
| `tests/test_routes/test_companion_routes.py` | **Create.** Route integration tests. |

### D. Implementation approach

**companion_service.py:**

```python
def get_visible_transactions(
    companion_user_id: int,
    period_id: int | None = None,
) -> list[Transaction]:
    """Get transactions visible to a companion user.

    Queries the linked owner's transactions filtered to those from
    templates with companion_visible=True. Eager-loads entries for
    progress computation.

    Defense-in-depth: verifies the user is a companion with a valid
    linked_owner_id before querying.

    Args:
        companion_user_id: The companion user's ID.
        period_id: Optional period filter. If None, returns the current period.

    Returns:
        List of Transaction objects with entries eager-loaded.

    Raises:
        NotFoundError: User is not a companion or has no linked owner.
    """
    user = db.session.get(User, companion_user_id)
    companion_role = ref_cache.role_id(RoleEnum.COMPANION)
    if user is None or user.role_id != companion_role or user.linked_owner_id is None:
        raise NotFoundError("Invalid companion configuration.")

    owner_id = user.linked_owner_id

    if period_id is None:
        period = pay_period_service.get_current_period(owner_id)
    else:
        period = db.session.get(PayPeriod, period_id)
        if period is None or period.user_id != owner_id:
            raise NotFoundError("Period not found.")

    transactions = (
        db.session.query(Transaction)
        .join(TransactionTemplate, Transaction.template_id == TransactionTemplate.id)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .options(selectinload(Transaction.entries))
        .filter(
            PayPeriod.user_id == owner_id,
            Transaction.pay_period_id == period.id,
            TransactionTemplate.companion_visible.is_(True),
            Transaction.is_deleted.is_(False),
        )
        .order_by(Transaction.name)
        .all()
    )

    return transactions, period


def get_companion_periods(companion_user_id: int) -> list[PayPeriod]:
    """Get all pay periods for the companion's linked owner."""
    user = db.session.get(User, companion_user_id)
    if user is None or user.linked_owner_id is None:
        return []
    return pay_period_service.get_all_periods(user.linked_owner_id)
```

**Companion routes** (`companion.py`):

```python
companion_bp = Blueprint("companion", __name__, url_prefix="/companion")


@companion_bp.route("/")
@login_required
def index():
    """Companion landing page: current period transactions."""
    companion_role = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id != companion_role:
        return redirect(url_for("grid.index"))

    transactions, period = companion_service.get_visible_transactions(
        current_user.id,
    )

    # Compute entry summaries for each transaction.
    entry_data = {}
    for txn in transactions:
        if txn.entries:
            total = sum(e.amount for e in txn.entries)
            remaining = txn.estimated_amount - total
            entry_data[txn.id] = {
                "total": total,
                "remaining": remaining,
                "count": len(txn.entries),
            }

    # Get adjacent periods for navigation.
    prev_period = pay_period_service.get_previous_period(period)
    next_period = pay_period_service.get_next_period(period)

    return render_template(
        "companion/index.html",
        transactions=transactions,
        period=period,
        prev_period=prev_period,
        next_period=next_period,
        entry_data=entry_data,
    )


@companion_bp.route("/period/<int:period_id>")
@login_required
def period_view(period_id):
    """Navigate to a specific period."""
    companion_role = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id != companion_role:
        return redirect(url_for("grid.index"))
    # Same logic as index but with explicit period_id.
    ...
```

**Companion template** (`companion/index.html`):

```html
{% extends "base.html" %}
{% block title %}My Budget -- Shekel{% endblock %}

{% block content %}
<div class="container px-2" style="max-width: 600px;">

  {# Period navigation #}
  <div class="d-flex justify-content-between align-items-center my-3">
    {% if prev_period %}
    <a href="{{ url_for('companion.period_view', period_id=prev_period.id) }}"
       class="btn btn-outline-secondary btn-sm">
      <i class="bi bi-chevron-left"></i>
    </a>
    {% else %}
    <div></div>
    {% endif %}

    <h5 class="mb-0">
      {{ period.start_date.strftime('%b %-d') }} -- {{ period.end_date.strftime('%b %-d, %Y') }}
    </h5>

    {% if next_period %}
    <a href="{{ url_for('companion.period_view', period_id=next_period.id) }}"
       class="btn btn-outline-secondary btn-sm">
      <i class="bi bi-chevron-right"></i>
    </a>
    {% else %}
    <div></div>
    {% endif %}
  </div>

  {# Transaction cards #}
  {% for txn in transactions %}
    {% include "companion/_transaction_card.html" %}
  {% endfor %}

  {% if not transactions %}
  <div class="text-center text-muted py-5">
    No transactions for this period.
  </div>
  {% endif %}

</div>
{% endblock %}
```

**Transaction card** (`companion/_transaction_card.html`):

```html
<div class="card mb-2">
  <div class="card-body py-2 px-3">
    <div class="d-flex justify-content-between align-items-center">
      <span class="fw-semibold">{{ txn.name }}</span>
      {% set ed = entry_data.get(txn.id) %}
      {% if txn.template and txn.template.track_individual_purchases %}
        {% if ed %}
        <span class="font-mono{% if ed.remaining < 0 %} text-danger{% endif %}">
          ${{ "{:,.0f}".format(ed.total) }} / ${{ "{:,.0f}".format(txn.estimated_amount) }}
        </span>
        {% else %}
        <span class="font-mono">${{ "{:,.0f}".format(txn.estimated_amount) }}</span>
        {% endif %}
      {% else %}
        <span class="font-mono">${{ "{:,.0f}".format(txn.estimated_amount) }}</span>
      {% endif %}
    </div>

    {# Progress bar for tracked transactions #}
    {% if ed %}
    {% set pct = (ed.total / txn.estimated_amount * 100) if txn.estimated_amount > 0 else 0 %}
    <div class="progress mt-1" style="height: 4px;">
      <div class="progress-bar{% if ed.remaining < 0 %} bg-danger{% endif %}"
           style="width: {{ [pct, 100]|min }}%"></div>
    </div>
    <div class="d-flex justify-content-between small text-muted mt-1">
      <span>{{ ed.count }} entr{{ 'ies' if ed.count != 1 else 'y' }}</span>
      <span>
        {% if ed.remaining >= 0 %}
          ${{ "{:,.0f}".format(ed.remaining) }} left
        {% else %}
          ${{ "{:,.0f}".format(ed.remaining|abs) }} over
        {% endif %}
      </span>
    </div>
    {% endif %}

    {# Entry section for tracked transactions #}
    {% if txn.template and txn.template.track_individual_purchases %}
    <div hx-get="{{ url_for('entries.list_entries', txn_id=txn.id) }}"
         hx-trigger="load"
         hx-target="this"
         id="entry-list-{{ txn.id }}">
      <span class="spinner-border spinner-border-sm" role="status"></span>
    </div>
    {% endif %}

    {# Mark as Paid button #}
    {% if txn.status_id == STATUS_PROJECTED %}
    <div class="mt-2">
      <button class="btn btn-sm btn-success w-100"
              hx-post="{{ url_for('transactions.mark_done', txn_id=txn.id) }}"
              hx-target="closest .card"
              hx-swap="outerHTML"
              hx-disabled-elt="this">
        <i class="bi bi-check-circle"></i> Mark as Paid
      </button>
    </div>
    {% elif txn.status and txn.status.is_settled %}
    <div class="mt-1 text-center small text-success">
      <i class="bi bi-check-circle-fill"></i> Paid
    </div>
    {% endif %}
  </div>
</div>
```

### E. Test cases

| ID | Test Name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| 10.1 | `test_companion_sees_visible_transactions` | 3 templates (2 visible, 1 not) | GET /companion | Only 2 transactions shown | New |
| 10.2 | `test_companion_sees_no_non_visible` | All templates non-visible | GET /companion | Empty state shown | New |
| 10.3 | `test_companion_period_navigation` | Multiple periods | GET /companion/period/X | Correct period displayed | New |
| 10.4 | `test_companion_entry_data_computed` | Tracked txn + entries | GET /companion | Entry data (total, remaining, count) correct | New |
| 10.5 | `test_companion_can_add_entry` | Visible tracked txn | POST entry via entries route | Entry created, user_id=companion.id | New |
| 10.6 | `test_companion_entry_user_id_is_companion` | Companion adds entry | Check entry.user_id | companion.id (not owner.id) | New |
| 10.7 | `test_companion_cannot_access_non_visible_entries` | Non-visible txn | POST entry | 404 | New |
| 10.8 | `test_companion_cannot_guess_txn_id` | Random txn_id | POST entry | 404 | New |
| 10.9 | `test_companion_mark_paid_visible_txn` | Visible txn | POST mark-done | Status changed to DONE | New |
| 10.10 | `test_companion_mark_paid_non_visible_rejected` | Non-visible txn | POST mark-done | 404 | New |
| 10.11 | `test_owner_redirected_from_companion` | Owner user | GET /companion | Redirect to /grid | New |
| 10.12 | `test_companion_service_rejects_non_companion` | Owner user | get_visible_transactions | NotFoundError | New |
| 10.13 | `test_companion_service_cross_owner_blocked` | Companion linked to owner A | Query with owner B's period | NotFoundError | New |

### F. Manual verification steps

1. Log in as companion -- verify the companion view loads with correct transactions.
2. Navigate between periods -- verify arrows work and transactions update.
3. Add an entry -- verify it appears in the entry list with correct amount.
4. Mark a transaction as Paid -- verify status changes.
5. Test on mobile device (or browser responsive mode) -- verify layout is usable.
6. Attempt to access /grid, /settings manually -- verify 404.
7. Verify the companion cannot see non-visible transactions.

### G. Downstream effects

The companion view is a self-contained feature. It does not affect the owner's grid, dashboard,
or any existing functionality. Entry routes (from Commit 8) are reused for companion entry CRUD.

### H. Rollback notes

Remove all companion files (service, routes, templates), deregister the blueprint. The
companion user account remains in the database but has no accessible routes.

---

## Opportunities

Optional enhancements identified during the code audit. These are not committed work -- they
are options for the developer to consider during or after implementation.

### OP-1: Dashboard progress indicators for entry-capable bills

**What:** Show the spending progress ("$330 / $500") on the dashboard's upcoming bills section
for entry-capable transactions. Currently the bills section shows the estimated amount; with
entry data available, it could show how much has been spent.

**Effort:** ~15 lines of template logic in the dashboard bills partial. The entry_sums data is
already available if selectinload is applied (done in Commit 3).

**Tradeoff:** Adds visual complexity to the dashboard bills section. The bills list is designed
for quick "what's upcoming" scanning; progress indicators may be distracting for fixed bills
(which don't have entries) and entry-capable bills side by side.

### OP-2: Companion account management in settings

**What:** Add a "Companion" section to the settings page where the owner can create, edit, and
delete companion accounts, manage which templates are companion-visible, and reset companion
passwords.

**Effort:** ~100 lines of route code, ~80 lines of template. A new settings partial
(`_companion.html`) with a form for companion management.

**Tradeoff:** The seed script (Commit 9) handles initial companion creation. A settings page
adds ongoing management capability but increases the settings page complexity. For a
single-companion household, the seed script may be sufficient. If more companions are added
later (kids, etc.), the settings page becomes essential.

### OP-3: Per-entry breakdown in year-end summary

**What:** Extend the year-end summary service to include per-entry detail for entry-capable
transactions. Instead of "Groceries: $12,000" show "Groceries: $12,000 (312 entries,
avg $38.46, $4,200 on credit card)".

**Effort:** ~30 lines of service code querying entries for settled transactions. ~20 lines of
template code in the year-end display.

**Tradeoff:** Useful for annual financial review but adds query complexity to the year-end
service. Entry data for a full year could be substantial (300+ entries for biweekly groceries).
Consider lazy-loading the detail on demand rather than pre-computing.

### OP-4: Entry date picker with validation

**What:** Add a date picker to the entry form that restricts dates to the parent transaction's
pay period range. Prevent entries dated before the period start or after the period end.

**Effort:** ~10 lines of JavaScript for date range validation, ~5 lines of service-level
validation.

**Tradeoff:** Prevents accidental misdating of entries. However, late-posting purchases (e.g.,
a credit card charge that posts 3 days after the period ends) may legitimately fall outside the
period range. A soft warning ("This date is outside the pay period") may be better than a hard
block.

---

## Commit Checklist

| # | Commit Message | Summary |
|---|----------------|---------|
| 1 | `feat(entries): add TransactionEntry model, user_roles ref table, template tracking flags, and companion user support` | Migration: ref.user_roles table (owner, companion), transaction_entries table, template booleans, user role_id FK/linked_owner_id. RoleEnum + ref_cache.role_id(). TransactionEntry model. Transaction.entries relationship. Seed data. Test fixtures. |
| 2 | `feat(entries): add entry service with CRUD, validation, and balance computation` | Entry service: create/update/delete, ownership validation, entry sums, remaining balance, actual computation. Marshmallow schemas. 21 service tests. |
| 3 | `feat(balance): extend balance calculator with entry-aware checking impact for tracked expenses` | Balance calculator: _entry_aware_amount helper using max(est-credit, debit) formula. selectinload in grid and dashboard. 12 tests covering all 6 scope doc scenarios. |
| 4 | `feat(credit): add entry-level credit card payback with per-transaction aggregation` | Entry credit workflow: sync_entry_payback creates/updates/deletes aggregated CC Payback. Legacy credit guard on entry-capable transactions. 12 lifecycle tests. |
| 5 | `feat(transactions): auto-populate actual amount from entries on mark-paid; block Credit status on tracked transactions` | mark_done: auto-compute actual from entry sum. Status guard in update_transaction. Post-paid entry mutation updates actual. Full edit hides Credit button. 10 tests. |
| 6 | `feat(templates): add tracking and companion visibility toggles to template form` | Template form: two checkbox toggles. Schema booleans. _TEMPLATE_UPDATE_FIELDS update. List page badges. Expense-only validation. 6 tests. |
| 7 | `feat(grid): add entry progress indicator and enhanced tooltip for tracked transactions` | Grid cell: "$330 / $500" progress format. Over-budget warning styling. Tooltip with entry breakdown. Mobile grid parity. entry_sums pre-computation in route. |
| 8 | `feat(entries): add entry CRUD endpoints and inline entry management in transaction popover` | Entry blueprint: CRUD routes with companion-aware access. _transaction_entries.html partial. Full edit popover integration. balanceChanged triggers. 9 route tests. |
| 9 | `feat(companion): add companion role with route guards and role-based login routing` | require_owner decorator on 17 route files. Login routing via role_id. Nav bar role_id check with context processor. Companion seed script. 15 guard and routing tests. |
| 10 | `feat(companion): add mobile-first companion view with period navigation and entry management` | Companion service: visibility-filtered queries. Companion routes + templates. Period navigation. Transaction cards with progress. Mark-as-paid. 13 tests. |
