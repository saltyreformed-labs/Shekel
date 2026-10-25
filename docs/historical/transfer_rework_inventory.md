> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Transfer Architecture Rework -- Codebase Inventory

**Generated:** 2026-03-25
**Based on:** docs/transfer_rework_design.md v1.1
**Commit:** f1cfa2765ac0b432a4aff140e1a8085f025f50ad

---

## 1. Models

### 1.1 Transfer Model

**File:** `app/models/transfer.py`
**Table:** `budget.transfers`

| Column | Type | Nullable | FK / Constraint | Line |
|--------|------|----------|-----------------|------|
| `id` | Integer, PK | no | -- | 35 |
| `user_id` | Integer | no | `auth.users.id` ON DELETE CASCADE | 36-38 |
| `from_account_id` | Integer | no | `budget.accounts.id` | 40-41 |
| `to_account_id` | Integer | no | `budget.accounts.id` | 43-44 |
| `pay_period_id` | Integer | no | `budget.pay_periods.id` | 46-47 |
| `scenario_id` | Integer | no | `budget.scenarios.id` ON DELETE CASCADE | 49-52 |
| `status_id` | Integer | no | `ref.statuses.id` | 54-55 |
| `transfer_template_id` | Integer | yes | `budget.transfer_templates.id` ON DELETE SET NULL | 57-59 |
| `name` | String(200) | yes | -- | 61 |
| `amount` | Numeric(12,2) | no | CHECK > 0 | 62 |
| `is_override` | Boolean | no | default False | 63 |
| `is_deleted` | Boolean | no | default False | 64 |
| `notes` | Text | yes | -- | 65 |
| `created_at` | DateTime(tz) | -- | server_default now() | 66 |
| `updated_at` | DateTime(tz) | -- | server_default now(), onupdate | 67-70 |

**Constraints:** `ck_transfers_different_accounts` (from != to), `ck_transfers_positive_amount` (amount > 0).

**Indexes:** `idx_transfers_period_scenario` (pay_period_id, scenario_id); `idx_transfers_template_period_scenario` (UNIQUE partial, WHERE template_id IS NOT NULL AND is_deleted = FALSE).

**Relationships (lines 73-83):** template (TransferTemplate), from_account (Account), to_account (Account), status (Status), pay_period (PayPeriod), scenario (Scenario).

**Properties (lines 85-93):** `effective_amount` -- returns amount if not cancelled, else Decimal("0").

**Rework impact:** Add `category_id` (nullable FK to `budget.categories`). No `actual_amount` column exists -- the design document references `actual_amount` on transfers (section 4.3); this may need to be added or the shadow transactions' `actual_amount` serves this purpose. See Section 9 for details.

### 1.2 TransferTemplate Model

**File:** `app/models/transfer_template.py`
**Table:** `budget.transfer_templates`

| Column | Type | Nullable | FK / Constraint | Line |
|--------|------|----------|-----------------|------|
| `id` | Integer, PK | no | -- | 31 |
| `user_id` | Integer | no | `auth.users.id` ON DELETE CASCADE | 32-34 |
| `from_account_id` | Integer | no | `budget.accounts.id` | 36-37 |
| `to_account_id` | Integer | no | `budget.accounts.id` | 39-40 |
| `recurrence_rule_id` | Integer | yes | `budget.recurrence_rules.id` | 42-43 |
| `name` | String(200) | no | -- | 45 |
| `default_amount` | Numeric(12,2) | no | CHECK > 0 | 46 |
| `is_active` | Boolean | -- | default True | 47 |
| `sort_order` | Integer | -- | default 0 | 48 |
| `created_at` | DateTime(tz) | -- | server_default now() | 49 |
| `updated_at` | DateTime(tz) | -- | server_default now(), onupdate | 50-53 |

**Constraints:** `ck_transfer_templates_different_accounts`, `ck_transfer_templates_positive_amount`, `uq_transfer_templates_user_name`.

**Relationships (lines 56-66):** from_account (Account), to_account (Account), recurrence_rule (RecurrenceRule), transfers (Transfer, back_populates).

**Rework impact:** Add `category_id` (nullable FK to `budget.categories`).

### 1.3 Transaction Model

**File:** `app/models/transaction.py`
**Table:** `budget.transactions`

| Column | Type | Nullable | FK / Constraint | Line |
|--------|------|----------|-----------------|------|
| `id` | Integer, PK | no | -- | 37 |
| `template_id` | Integer | yes | `budget.transaction_templates.id` ON DELETE SET NULL | 38-41 |
| `pay_period_id` | Integer | no | `budget.pay_periods.id` ON DELETE CASCADE | 42-45 |
| `scenario_id` | Integer | no | `budget.scenarios.id` ON DELETE CASCADE | 47-50 |
| `status_id` | Integer | no | `ref.statuses.id` | 52-53 |
| `name` | String(200) | no | -- | 55 |
| `category_id` | Integer | yes | `budget.categories.id` | 56-57 |
| `transaction_type_id` | Integer | no | `ref.transaction_types.id` | 59-60 |
| `estimated_amount` | Numeric(12,2) | no | -- | 62 |
| `actual_amount` | Numeric(12,2) | yes | -- | 63 |
| `is_override` | Boolean | -- | default False | 64 |
| `is_deleted` | Boolean | -- | default False | 65 |
| `credit_payback_for_id` | Integer | yes | self-FK ON DELETE SET NULL | 66-68 |
| `notes` | Text | yes | -- | 70 |
| `created_at` | DateTime(tz) | -- | server_default now() | 71 |
| `updated_at` | DateTime(tz) | -- | server_default now(), onupdate | 72-75 |

**Relationships (lines 78-87):** template (TransactionTemplate), pay_period (PayPeriod), scenario (Scenario), status (Status), category (Category), transaction_type (TransactionType), credit_payback_for (self).

**Properties (lines 89-111):** `effective_amount` (status-aware), `is_income`, `is_expense`.

**Rework impact:** Add `account_id` (NOT NULL FK to `budget.accounts`), `transfer_id` (nullable FK to `budget.transfers` ON DELETE CASCADE). Add relationships to Account and Transfer. Add index `idx_transactions_account` on `account_id` and partial index `idx_transactions_transfer` on `transfer_id` WHERE NOT NULL.

### 1.4 TransactionTemplate Model

**File:** `app/models/transaction_template.py`
**Table:** `budget.transaction_templates`

Has `account_id` column (line 28-29, FK to `budget.accounts.id`). This is the source from which the new `transactions.account_id` will be copied during recurrence generation.

**Rework impact:** No schema changes. The `account_id` on templates is the source value copied to new transactions.

### 1.5 Other Models Referenced by Transfers

- **Account** (`app/models/account.py`): Referenced by Transfer.from_account_id and Transfer.to_account_id. No changes needed to Account model itself.
- **Category** (`app/models/category.py`): Will be referenced by new Transfer.category_id and TransferTemplate.category_id columns. No changes needed to Category model itself.
- **PayPeriod** (`app/models/pay_period.py`): Referenced by Transfer.pay_period_id. Has `transactions` relationship (line 34) but no `transfers` relationship.
- **Scenario** (`app/models/scenario.py`): Referenced by Transfer.scenario_id.
- **Status** (`app/models/ref.py`, lines 38-51): Referenced by both Transfer.status_id and Transaction.status_id.
- **RecurrenceRule** (`app/models/recurrence_rule.py`): Referenced by TransferTemplate.recurrence_rule_id and TransactionTemplate.recurrence_rule_id.

### 1.6 Models __init__.py

**File:** `app/models/__init__.py` (lines 1-58): Imports all model classes for Alembic autodiscovery. No changes needed unless new models are added.

---

## 2. Services

### 2.1 Balance Calculator

**File:** `app/services/balance_calculator.py` (398 lines)

| Function | Signature | Lines | Transfer Logic |
|----------|-----------|-------|----------------|
| `calculate_balances` | `(anchor_balance, anchor_period_id, periods, transactions, transfers=None, account_id=None)` | 30-123 | Accepts optional `transfers` list. Calls `_sum_transfer_effects_remaining()` and `_sum_transfer_effects_all()` at lines 80-82 and 90-91 to factor transfer effects into running balance. |
| `calculate_balances_with_interest` | `(anchor_balance, anchor_period_id, periods, transactions, transfers=None, account_id=None, hysa_params=None)` | 126-189 | Passes `transfers` through to `calculate_balances()` at lines 140-142. |
| `calculate_balances_with_amortization` | `(anchor_balance, anchor_period_id, periods, transactions, transfers=None, account_id=None, loan_params=None)` | 192-286 | Passes `transfers` through at lines 220-223. Uses transfer-specific logic at lines 260-267 to detect transfers TO the loan account and split into principal/interest. |
| `_sum_remaining` | `(transactions)` | 289-319 | Sums projected transactions for anchor period. No transfer logic. |
| `_sum_all` | `(transactions)` | 322-348 | Sums projected transactions for post-anchor periods. No transfer logic. |
| `_sum_transfer_effects_remaining` | `(transfers, account_id)` | 351-374 | Sums transfer IN/OUT effects for anchor period. Checks `xfer.to_account_id` and `xfer.from_account_id`. Excludes settled/cancelled. |
| `_sum_transfer_effects_all` | `(transfers, account_id)` | 377-397 | Sums transfer effects for post-anchor periods. Excludes cancelled/done/received. |

**Rework impact:** This is the highest-risk change. Remove `_sum_transfer_effects_remaining`, `_sum_transfer_effects_all`, the `transfers` parameter, and all transfer-specific logic. After rework, shadow transactions handle all transfer effects through the standard transaction path. The amortization variant (lines 260-267) must be reworked to detect loan payments from transactions with `transfer_id IS NOT NULL` rather than from Transfer objects.

### 2.2 Transfer Recurrence Engine

**File:** `app/services/transfer_recurrence.py` (249 lines)

| Function | Signature | Lines | Description |
|----------|-----------|-------|-------------|
| `generate_for_template` | `(template, periods, scenario_id, effective_from=None)` | 31-117 | Generates Transfer records from a template. Creates Transfer objects directly via ORM at lines 96-108. |
| `regenerate_for_template` | `(template, periods, scenario_id, effective_from=None)` | 120-187 | Deletes non-overridden transfers and regenerates. Queries `Transfer` directly at lines 147-155. |
| `resolve_conflicts` | `(transfer_ids, action, user_id, new_amount=None)` | 190-227 | Resolves override/delete conflicts. |
| `_get_existing_map` | `(template_id, scenario_id, periods)` | 230-248 | Queries `Transfer` directly. |

**Transfer creation at lines 96-108:** Creates Transfer with `user_id`, `transfer_template_id`, `from_account_id`, `to_account_id`, `pay_period_id`, `scenario_id`, `status_id`, `name`, `amount`, `is_override=False`, `is_deleted=False`.

**Rework impact:** Change `generate_for_template` to call `transfer_service.create_transfer(...)` instead of directly constructing Transfer objects. The recurrence logic (pattern matching, period selection, conflict handling) does not change -- only the final creation step.

### 2.3 Transaction Recurrence Engine

**File:** `app/services/recurrence_engine.py` (550 lines)

| Function | Signature | Lines | Description |
|----------|-----------|-------|-------------|
| `generate_for_template` | `(template, periods, scenario_id, effective_from=None)` | 43-157 | Generates Transaction records. Creates at lines 138-149. |
| `regenerate_for_template` | `(template, periods, scenario_id, effective_from=None)` | 160-245 | Deletes and regenerates. |
| `resolve_conflicts` | `(transaction_ids, action, user_id, new_amount=None)` | 248-287 | Conflict resolution. |
| `_match_periods` | `(rule, pattern_name, periods, effective_from)` | 293-344 | Pattern matching dispatcher. |
| `_match_monthly` | `(periods, day_of_month)` | 347-374 | Monthly pattern. |
| `_match_monthly_first` | `(periods)` | 377-388 | Monthly first paycheck. |
| `_match_quarterly` | `(periods, start_month, day_of_month)` | 391-397 | Quarterly pattern. |
| `_match_semi_annual` | `(periods, start_month, day_of_month)` | 400-406 | Semi-annual pattern. |
| `_match_specific_months` | `(periods, target_months, day_of_month)` | 409-430 | Matches specific months. |
| `_match_annual` | `(periods, month, day)` | 433-453 | Annual pattern. |
| `_get_existing_map` | `(template_id, scenario_id, periods)` | 456-481 | Existing transaction lookup. |
| `_get_salary_profile` | `(template_id)` | 484-493 | Salary profile linkage. |
| `_get_transaction_amount` | `(template, salary_profile, period, all_periods)` | 496-549 | Amount determination. |

**Transaction creation at lines 138-149:** Does NOT set `account_id`. Sets `template_id`, `pay_period_id`, `scenario_id`, `status_id`, `name`, `category_id`, `transaction_type_id`, `estimated_amount`, `is_override`, `is_deleted`.

**Rework impact:** Add `account_id=template.account_id` to the Transaction constructor at line 138.

### 2.4 Carry Forward Service

**File:** `app/services/carry_forward_service.py` (97 lines)

| Function | Signature | Lines | Description |
|----------|-----------|-------|-------------|
| `carry_forward_unpaid` | `(source_period_id, target_period_id, user_id, scenario_id)` | 20-96 | Moves projected transactions from source to target period. Flags template-linked items as is_override=True. |

**Transfer awareness:** None. Only queries and modifies `budget.transactions`. Does not carry forward transfers.

**Rework impact:** Must be modified per design section 10A. After rework, shadow transactions (`transfer_id IS NOT NULL`) will be present in the transaction query. The service must:
1. Partition results into regular transactions (`transfer_id IS NULL`) and shadow transactions (`transfer_id IS NOT NULL`).
2. Move regular transactions directly (unchanged behavior).
3. For shadow transactions, extract distinct `transfer_id` values and route each through `transfer_service.update_transfer(transfer_id, pay_period_id=target)` to move the parent transfer and both shadows atomically.

### 2.5 Credit Workflow Service

**File:** `app/services/credit_workflow.py` (189 lines)

| Function | Signature | Lines | Description |
|----------|-----------|-------|-------------|
| `mark_as_credit` | `(transaction_id, user_id)` | 25-120 | Marks transaction as credit, creates payback in next period. |
| `unmark_credit` | `(transaction_id, user_id)` | 123-160 | Reverts credit, deletes payback. |
| `_get_or_create_cc_category` | `(user_id)` | 162-188 | Gets/creates "Credit Card: Payback" category. |

**Transfer awareness:** None.

**Rework impact:** No functional changes needed. The guard preventing credit marking on shadow transactions is implemented in the transaction routes (section 10.2 of the design), not in this service. However, `mark_as_credit` creates a new Transaction (the payback) -- it must set `account_id` on the payback transaction (copy from the original transaction's `account_id`). This is a new code path that creates a transaction and must set the new column.

### 2.6 Chart Data Service

**File:** `app/services/chart_data_service.py` (730 lines)

| Function | Lines | Transfer Logic |
|----------|-------|----------------|
| `_calculate_account_balances` | 184-246 | **Lines 214-219:** Queries `db.session.query(Transfer).filter_by(scenario_id=...).filter(Transfer.pay_period_id.in_(...)).all()`. Passes transfers to balance_calculator at lines 227-228, 232-235, 240-243, 245. |
| `_get_expense_transactions` | 341-364 | Queries only `budget.transactions`. No transfer logic. |
| `get_spending_by_category` | 367-412 | Uses `_get_expense_transactions`. No transfer logic. |
| `get_budget_vs_actuals` | 418-465 | Uses `_get_expense_transactions`. No transfer logic. |

**Rework impact:** Remove the Transfer query and `transfers` parameter from `_calculate_account_balances()`. After rework, shadow transactions in `budget.transactions` provide transfer effects automatically. The category-based charts (`get_spending_by_category`, `get_budget_vs_actuals`) will automatically include transfer-linked expense transactions because they query `budget.transactions` -- no changes needed there.

### 2.7 Investment Projection Service

**File:** `app/services/investment_projection.py` (120 lines)

| Function | Lines | Transfer Logic |
|----------|-------|----------------|
| `calculate_investment_inputs` | 30-119 | **Lines 72-84:** Processes `all_transfers` argument to calculate transfer-based contributions. Filters transfers to target `account_id`, sums amounts, averages per period. **Lines 106-108:** Tracks YTD contributions from transfers. |

**Rework impact:** After the rework, investment contributions from transfers will exist as income-side shadow transactions in `budget.transactions`. This service currently receives `all_transfers` as a parameter from routes. It will need to be modified to derive contribution data from shadow transactions (income transactions with `transfer_id IS NOT NULL` targeting the investment account) instead of from Transfer objects. Alternatively, it could continue to query Transfer objects since `budget.transfers` still exists -- but this is inconsistent with the design principle of consumers reading only `budget.transactions`. See Section 9.

### 2.8 Other Services

- **`account_resolver.py`** (63 lines): No transfer references. No changes needed.
- **`amortization_engine.py`** (331 lines): No transfer references. Pure-function loan math.
- **`auth_service.py`** (415 lines): No transfer references. Contains `DEFAULT_CATEGORIES` at lines 22-45 -- this is where default transfer categories will be added.
- **`calibration_service.py`** (146 lines): No transfer references.
- **`escrow_calculator.py`** (107 lines): No transfer references.
- **`growth_engine.py`** (207 lines): No transfer references.
- **`interest_projection.py`** (61 lines): No transfer references.
- **`mfa_service.py`** (159 lines): No transfer references.
- **`paycheck_calculator.py`** (~550 lines): No transfer references.
- **`pay_period_service.py`** (168 lines): No transfer references.
- **`pension_calculator.py`** (~100 lines): No transfer references.
- **`retirement_gap_calculator.py`** (~100 lines): No transfer references.
- **`savings_goal_service.py`** (100 lines): No transfer references.
- **`tax_calculator.py`** (~100 lines): No transfer references.
- **`tax_config_service.py`** (70 lines): No transfer references.
- **`exceptions.py`** (45 lines): No transfer references.

---

## 3. Routes

### 3.1 Transfer Routes

**File:** `app/routes/transfers.py` (553 lines)

| Handler | Method | URL | Lines | Description |
|---------|--------|-----|-------|-------------|
| `list_transfer_templates` | GET | `/transfers` | 48-58 | List all transfer templates. |
| `new_transfer_template` | GET | `/transfers/new` | 61-88 | Template creation form. Accepts `from_account`/`to_account` query params for prefill. |
| `create_transfer_template` | POST | `/transfers` | 91-180 | Create template with optional recurrence. Calls `transfer_recurrence.generate_for_template()` directly. |
| `edit_transfer_template` | GET | `/transfers/<int:template_id>/edit` | 183-207 | Template edit form. |
| `update_transfer_template` | POST | `/transfers/<int:template_id>` | 210-299 | Update template, regenerate transfers. Handles RecurrenceConflict. |
| `delete_transfer_template` | POST | `/transfers/<int:template_id>/delete` | 302-327 | Soft-deactivate template, mark transfers as deleted. |
| `reactivate_transfer_template` | POST | `/transfers/<int:template_id>/reactivate` | 330-366 | Reactivate template, restore transfers, regenerate. |
| `get_cell` | GET | `/transfers/cell/<int:xfer_id>` | 372-382 | HTMX: transfer cell partial. |
| `get_quick_edit` | GET | `/transfers/quick-edit/<int:xfer_id>` | 385-392 | HTMX: inline amount edit form. |
| `get_full_edit` | GET | `/transfers/<int:xfer_id>/full-edit` | 395-405 | HTMX: full edit popover. |
| `update_transfer` | PATCH | `/transfers/instance/<int:xfer_id>` | 408-437 | Update transfer fields. Sets is_override. Triggers balanceChanged. |
| `create_ad_hoc` | POST | `/transfers/ad-hoc` | 440-476 | Create one-time transfer. Validates account ownership. |
| `delete_transfer` | DELETE | `/transfers/instance/<int:xfer_id>` | 479-494 | Soft-delete template-linked, hard-delete ad-hoc. |
| `mark_done` | POST | `/transfers/instance/<int:xfer_id>/mark-done` | 500-518 | Mark transfer as done. |
| `cancel_transfer` | POST | `/transfers/instance/<int:xfer_id>/cancel` | 521-539 | Mark transfer as cancelled. |
| `_get_owned_transfer` | (helper) | -- | 545-552 | Ownership verification. |

**Rework impact:** All mutation handlers (`create_ad_hoc`, `update_transfer`, `delete_transfer`, `mark_done`, `cancel_transfer`, `create_transfer_template`, `update_transfer_template`, `delete_transfer_template`, `reactivate_transfer_template`) must route through `transfer_service` instead of directly manipulating Transfer ORM objects. The `create_transfer_template` and `update_transfer_template` forms need a category dropdown. Grid-specific cell routes (`get_cell`, `get_quick_edit`, `get_full_edit`) remain for the transfer edit popover but may be accessed less often (shadow transaction clicks route through transaction cell handlers first, then detect transfer_id and redirect).

### 3.2 Transaction Routes

**File:** `app/routes/transactions.py` (449 lines)

| Handler | Method | URL | Lines | Description |
|---------|--------|-----|-------|-------------|
| `get_cell` | GET | `/transactions/<int:txn_id>/cell` | 56-63 | HTMX: transaction cell partial. |
| `get_quick_edit` | GET | `/transactions/<int:txn_id>/quick-edit` | 66-73 | HTMX: inline amount edit. |
| `get_full_edit` | GET | `/transactions/<int:txn_id>/full-edit` | 76-84 | HTMX: full edit popover. |
| `update_transaction` | PATCH | `/transactions/<int:txn_id>` | 87-120 | Update transaction. Sets is_override. |
| `mark_done` | POST | `/transactions/<int:txn_id>/mark-done` | 123-154 | Mark done/received. |
| `mark_credit` | POST | `/transactions/<int:txn_id>/mark-credit` | 157-171 | Mark credit, create payback. |
| `unmark_credit` | DELETE | `/transactions/<int:txn_id>/unmark-credit` | 174-188 | Revert credit. |
| `cancel_transaction` | POST | `/transactions/<int:txn_id>/cancel` | 191-206 | Cancel transaction. |
| `get_quick_create` | GET | `/transactions/new/quick` | 209-247 | HTMX: quick-create form. |
| `get_full_create` | GET | `/transactions/new/full` | 250-287 | HTMX: full create popover. |
| `get_empty_cell` | GET | `/transactions/empty-cell` | 290-315 | HTMX: empty cell placeholder. |
| `create_inline` | POST | `/transactions/inline` | 318-363 | Create inline transaction. |
| `create_transaction` | POST | `/transactions` | 366-392 | Create ad-hoc transaction. |
| `delete_transaction` | DELETE | `/transactions/<int:txn_id>` | 395-412 | Delete transaction. |
| `carry_forward` | POST | `/pay-periods/<int:period_id>/carry-forward` | 415-448 | Carry forward unpaid. |

**Rework impact:** Every mutation handler needs a transfer detection guard. When the target transaction has `transfer_id IS NOT NULL`:
- `update_transaction` (line 87): Route amount/status changes through `transfer_service.update_transfer()`.
- `mark_done` (line 123): Route through `transfer_service.update_transfer()` for status change.
- `mark_credit` (line 157): Block -- transfers cannot be put on credit.
- `cancel_transaction` (line 191): Route through `transfer_service.update_transfer()` or block.
- `delete_transaction` (line 395): Block direct deletion -- must delete the parent transfer.
- `get_full_edit` (line 76): Return the transfer edit form instead of the transaction edit form.
- `create_inline` (line 318): Must set `account_id` on the new Transaction.
- `create_transaction` (line 366): Must set `account_id` on the new Transaction.

### 3.3 Grid Route

**File:** `app/routes/grid.py` (296 lines)

| Handler | Method | URL | Lines | Description |
|---------|--------|-----|-------|-------------|
| `index` | GET | `/` | 32-175 | Main budget grid. Loads transactions (lines 82-91) and transfers (lines 93-102) separately. Groups by period (lines 120-127). Passes both to balance_calculator (lines 110-117). Template context includes `txn_by_period` and `xfer_by_period`. |
| `create_baseline` | POST | `/create-baseline` | 178-207 | Creates baseline scenario. No transfer logic. |
| `balance_row` | GET | `/grid/balance-row` | 210-295 | HTMX: recalculates and returns footer partial. Loads transactions (lines 238-248) and transfers (lines 250-260) separately. Passes both to balance_calculator (lines 272-280). Returns `_balance_row.html` with `txn_by_period` and `xfer_by_period`. |

**Rework impact:** Remove the separate Transfer query (lines 93-102 in `index`, lines 250-260 in `balance_row`). Remove `xfer_by_period` from template context. Remove `transfers` parameter from balance_calculator calls. After rework, shadow transactions appear automatically in the `txn_by_period` dict. For account-filtered transaction queries, add direct filter on `transactions.account_id` instead of joining through templates. Template context for grid.html changes: `xfer_by_period` is removed.

### 3.4 Other Routes With Transfer References

- **`accounts.py`** (699 lines): Line 237-258 -- Guard preventing account deactivation when active transfer templates reference the account. Queries `TransferTemplate` directly. **No change needed** -- this guard remains valid.
- **`savings.py`** (584 lines): Lines 67-88 -- Loads transfers for balance projections. Lines 214-225 -- Passes transfers to balance_calculator. **Rework impact:** Remove Transfer query and `transfers` parameter from balance_calculator calls.
- **`investment.py`** (404 lines): Lines 99-109 -- Queries transfers targeting the investment account for contribution calculations. **Rework impact:** Replace Transfer query with a query for income transactions with `transfer_id IS NOT NULL` and `account_id == investment_account_id`, or continue querying Transfer objects (see Section 9).
- **`retirement.py`** (~300 lines): Lines 159-170 -- Queries transfers targeting retirement accounts. **Rework impact:** Same as investment.py.

### 3.5 Routes With No Transfer References

- `auth.py`, `categories.py`, `charts.py`, `health.py`, `mortgage.py`, `auto_loan.py`, `pay_periods.py`, `salary.py`, `settings.py`, `templates.py`

---

## 4. Templates

### 4.1 Grid Templates

#### `app/templates/grid/grid.html` (352 lines)

- **Lines 99-160:** INCOME section. Banner row with class `section-banner-income`. Iterates categories, renders `_transaction_cell.html` or `_transaction_empty_cell.html`.
- **Lines 163-165:** Spacer row with class `spacer-row`.
- **Lines 167-224:** EXPENSES section. Banner row with class `section-banner-expense`. Same pattern as income.
- **Lines 226-282:** TRANSFERS section (CONDITIONAL). Banner row with class `section-banner-transfer`. Only rendered when `xfer_by_period` has data (`{% set has_any_transfers = ... %}`). Groups by `xfer.name or 'Ad-hoc Transfer'`. Renders `transfers/_transfer_cell.html` or `transfers/_transfer_empty_cell.html`.
- **Line 286:** Footer included from `_balance_row.html`.
- **Line 84:** Carry Forward button: `hx-post="{{ url_for('transactions.carry_forward', period_id=period.id) }}"`.

**Rework impact (Phase 3A-I):** Remove the entire TRANSFERS section (lines 226-282). Remove `xfer_by_period` usage. Shadow transactions will appear in INCOME/EXPENSES sections automatically. Add transfer indicator (CSS class `is-transfer` or icon) to `_transaction_cell.html` when `t.transfer_id` is not None.

**Rework impact (Phase 3A-II):** Add inline subtotal rows after INCOME section and after EXPENSES section. Add Net Cash Flow row after expense subtotal. These are `<tr>` in `<tbody>`, not `<tfoot>`.

#### `app/templates/grid/_balance_row.html` (114 lines)

- **Lines 13-26:** Total Income row. Sums income transactions per period.
- **Lines 28-42:** Total Expenses row. Sums expense transactions per period.
- **Lines 44-69:** Net Transfers row (CONDITIONAL). Calculates per-account net (incoming minus outgoing) from `xfer_by_period`. Conditional on `xfer_by_period is defined and xfer_by_period`.
- **Lines 71-94:** Net (Income - Expenses) row.
- **Lines 96-112:** Projected End Balance row. Reads from `balances` dict.
- **Line 6-9:** HTMX trigger: `hx-get="{{ url_for('grid.balance_row') }}" hx-trigger="balanceChanged from:body"` with `hx-target="#grid-summary"` and `hx-swap="outerHTML"`.

**Rework impact (Phase 3A-I):** Remove Net Transfers row (lines 44-69). Transfer effects are now included in Total Income and Total Expenses through shadow transactions. Remove `xfer_by_period` references.

**Rework impact (Phase 3A-II):** Move Total Income, Total Expenses, and Net rows out of `<tfoot>` and into `<tbody>` as inline subtotal rows (handled in grid.html changes). Reduce `<tfoot>` to only Projected End Balance.

#### `app/templates/grid/_transaction_cell.html` (52 lines)

- **Line 11:** HTMX: `hx-get="{{ url_for('transactions.get_quick_edit', txn_id=t.id) }}"`.
- Displays amount, status badge, override indicator, credit payback indicator.

**Rework impact:** Add conditional transfer indicator when `t.transfer_id` is not None (e.g., Bootstrap icon `bi-arrow-left-right` or CSS class `is-transfer`).

#### `app/templates/grid/_transaction_quick_edit.html` (28 lines)

- **Line 8:** HTMX: `hx-patch="{{ url_for('transactions.update_transaction', txn_id=t.id) }}"`.
- **Line 24:** Expand button with `data-txn-id`.

**Rework impact:** No template changes needed. The transfer detection happens in the route handler (`update_transaction`), not in the template.

#### `app/templates/grid/_transaction_full_edit.html` (123 lines)

- **Lines 73-121:** Quick status action buttons: Mark Done, Mark Credit, Undo CC, Cancel, Received.

**Rework impact:** When rendering for a shadow transaction (`t.transfer_id is not None`), either:
- Return the transfer full edit form instead (handled in the route, returning `transfers/_transfer_full_edit.html`).
- Or conditionally hide the "Mark Credit" button and route other actions through the transfer service. The route-level approach (detecting `transfer_id` in `get_full_edit` and returning the transfer form) is cleaner per the design document.

#### `app/templates/grid/_transaction_full_create.html` (78 lines)

**Rework impact:** Must include a hidden `account_id` field or infer it from context. Currently does not set `account_id`.

#### `app/templates/grid/_transaction_quick_create.html` (38 lines)

- **Lines 17-20:** Hidden fields: `category_id`, `pay_period_id`, `scenario_id`, `transaction_type_id`.

**Rework impact:** Must include hidden `account_id` field.

#### Other grid templates

- `_transaction_empty_cell.html` (20 lines): Empty cell placeholder. No transfer references. No changes needed.
- `_anchor_edit.html` (44 lines): Anchor balance display/edit. No transfer references. No changes needed.

### 4.2 Transfer Templates

#### `app/templates/transfers/_transfer_cell.html` (46 lines)

- **Line 10:** HTMX: `hx-get="{{ url_for('transfers.get_quick_edit', xfer_id=xfer.id) }}"`.
- **Lines 20-26:** Direction icon (incoming/outgoing) based on `xfer.to_account_id == account.id`.

**Rework impact (Phase 3A-I):** This template is no longer rendered in the grid's TRANSFERS section (that section is removed). However, it remains available if needed for any other context. Likely can be retired after Phase 3A-I is complete.

#### `app/templates/transfers/_transfer_empty_cell.html` (11 lines)

Display-only placeholder. No HTMX. **Rework impact:** Retire -- no longer needed.

#### `app/templates/transfers/_transfer_quick_edit.html` (29 lines)

- **Line 8:** HTMX: `hx-patch="{{ url_for('transfers.update_transfer', xfer_id=xfer.id) }}"`.
- **Line 22-25:** Expand button with `data-xfer-id`.

**Rework impact:** This template is still needed when a shadow transaction's quick edit routes through the transfer service. The route handler for the shadow transaction quick edit may use this template or may reuse the standard transaction quick edit but route the PATCH through the transfer service.

#### `app/templates/transfers/_transfer_full_edit.html` (83 lines)

- **Lines 21-49:** Form fields: amount, status_id, notes.
- **Lines 64-81:** Quick status buttons: Mark Done, Cancel.

**Rework impact:** Still needed. When a user opens full edit on a shadow transaction, the route detects `transfer_id`, loads the parent Transfer, and returns this template. Add `category_id` dropdown to this form.

#### `app/templates/transfers/form.html` (173 lines)

Transfer template create/edit form. Fields: name, amount, from_account, to_account, recurrence fields.

**Rework impact:** Add category dropdown.

#### `app/templates/transfers/list.html` (122 lines)

Transfer template list/dashboard.

**Rework impact:** No changes needed unless category display is desired in the list.

### 4.3 Other Templates With Transfer References

- **`base.html`** (238 lines): Navigation link to Transfers page at lines 73-77. **No changes needed** -- Transfers page remains for template management.
- **`savings/dashboard.html`** (287 lines): "New transfer from this account" button (line 89-92) and "Create recurring transfer to this goal" button (line 215-218). **No changes needed** -- these link to transfer template creation.

---

## 5. Static Assets

### 5.1 JavaScript

#### `app/static/js/app.js` (562 lines)

- **Lines 357-366:** `getDataRows()` function. Exclusion list filters out:
  - `section-banner-income`
  - `section-banner-expense`
  - `spacer-row`
  - `group-header-row`
  - **NOTE:** Does NOT exclude `section-banner-transfer`. This means transfer banner rows ARE currently keyboard-navigable (likely a pre-existing minor bug, since they are not data cells).

**Rework impact (Phase 3A-I):** After removing the TRANSFERS section, `section-banner-transfer` no longer exists in the DOM. No code change needed for the exclusion list. However, if the transfer indicator adds any new non-data rows, those must be added to the exclusion list.

**Rework impact (Phase 3A-II):** Subtotal rows and Net Cash Flow row must have CSS classes added to the exclusion list in `getDataRows()`.

#### `app/static/js/grid_edit.js` (310 lines)

- **Lines 90-105:** `openTransferFullEdit(xferId, triggerEl)` -- fetches transfer full edit from `/transfers/<xferId>/full-edit`.
- **Lines 184-211:** F2 handler differentiates transfer vs transaction. Checks for `.xfer-expand-btn[data-xfer-id]` (line 191-193). Calls `openTransferFullEdit()` for transfers.
- **Lines 230-239:** Escape handler reverts transfer quick edit by fetching `/transfers/cell/<xferId>`.
- **Lines 270-275:** Click handler for `.xfer-expand-btn[data-xfer-id]` buttons.

**Rework impact:** After the rework, shadow transactions rendered via `_transaction_cell.html` use `data-txn-id` attributes, not `data-xfer-id`. The F2 and click handlers must detect shadow transactions. Two approaches:
1. Add a `data-transfer-id` attribute to shadow transaction cells, then check for it in the F2/click handlers and call `openTransferFullEdit(transferId)`.
2. Let the server handle it: the transaction `get_full_edit` route detects `transfer_id` and returns the transfer edit form. The JS doesn't need to distinguish -- it always calls `openFullEdit(txnId)`, and the server decides which form to return.

Option 2 is cleaner and requires fewer JS changes.

#### `app/static/js/recurrence_form.js` (86 lines)

Used by both transaction and transfer template forms. No transfer-specific logic. **No changes needed.**

### 5.2 CSS

#### `app/static/css/app.css` (797 lines)

- **Lines 38-39 (dark mode):** CSS variables `--shekel-section-transfer-bg`, `--shekel-section-transfer-text`.
- **Lines 142-143 (light mode):** Same variables for light mode.
- **Lines 298-307:** `.section-banner-transfer td` styling (background, color, font, text-transform).

**Rework impact (Phase 3A-I):** Add `.is-transfer` indicator styling (e.g., left border accent, icon color). The `section-banner-transfer` CSS can be left in place as dead code during Phase I or removed during cleanup (step 12).

**Rework impact (Phase 3A-II):** Add subtotal row styling (bold, background). Add Net Cash Flow row styling. Modify footer styling to single row.

---

## 6. HTMX Interaction Inventory

**Note:** The file `docs/implementation_plan_section4.md` referenced in the task prompt does not exist in the repository. The HTMX interactions below are documented from reading the codebase directly. The GI-N numbering referenced in the design document (section 7.4) cannot be mapped to a source document that does not exist.

### 6.1 Transaction Cell Click (Quick Edit)

**Trigger:** Click on `_transaction_cell.html` cell.
**HTMX:** `hx-get="/transactions/<txn_id>/quick-edit"` -> swaps cell content with inline edit form.
**Affected:** Yes. Shadow transaction clicks follow this same path. The route handler must detect `transfer_id` on the transaction and may need to route through transfer-specific logic for amount updates.

### 6.2 Transaction Quick Edit Submit

**Trigger:** Blur or Enter on quick edit input.
**HTMX:** `hx-patch="/transactions/<txn_id>"` -> updates transaction, returns updated cell.
**Affected:** Yes. For shadow transactions, the PATCH handler must detect `transfer_id` and route through `transfer_service.update_transfer()`.

### 6.3 Transaction Full Edit (Expand)

**Trigger:** Expand button click or F2 in quick edit.
**JS:** `openFullEdit(txnId)` fetches `/transactions/<txn_id>/full-edit`.
**Affected:** Yes. For shadow transactions, the server returns the transfer edit form (`_transfer_full_edit.html`) instead of the transaction edit form.

### 6.4 Transaction Full Edit Submit

**Trigger:** Submit within full edit popover.
**HTMX:** `hx-patch="/transactions/<txn_id>"` -> updates transaction, returns cell.
**Affected:** Yes. Same transfer detection as 6.2.

### 6.5 Transaction Quick Create (Empty Cell Click)

**Trigger:** Click on `_transaction_empty_cell.html`.
**HTMX:** `hx-get="/transactions/new/quick"` -> returns inline create form.
**Affected:** Yes, minor. The create form must include `account_id`.

### 6.6 Transaction Full Create Submit

**Trigger:** Submit from full create form.
**HTMX:** `hx-post="/transactions/inline"` -> creates transaction, returns cell.
**Affected:** Yes, minor. Must set `account_id` on new transaction.

### 6.7 Mark Done

**Trigger:** "Mark Done" button in full edit popover.
**HTMX:** `hx-post="/transactions/<txn_id>/mark-done"`.
**Affected:** Yes. For shadow transactions, route through transfer service.

### 6.8 Mark Credit

**Trigger:** "Credit" button in full edit popover.
**HTMX:** `hx-post="/transactions/<txn_id>/mark-credit"`.
**Affected:** Yes. Block for shadow transactions -- transfers cannot be put on credit.

### 6.9 Cancel Transaction

**Trigger:** "Cancel" button in full edit popover.
**HTMX:** `hx-post="/transactions/<txn_id>/cancel"`.
**Affected:** Yes. For shadow transactions, route through transfer service.

### 6.10 Balance Row Refresh

**Trigger:** Custom event `balanceChanged from:body`.
**HTMX:** `hx-get="/grid/balance-row"` on `#grid-summary` tfoot, swap outerHTML.
**Affected:** Yes. The returned HTML structure changes (Net Transfers row removed in Phase I; footer reduced to single row in Phase II). The swap mechanism (outerHTML on `#grid-summary`) works regardless of content changes.

### 6.11 Transfer Cell Click (Quick Edit)

**Trigger:** Click on `_transfer_cell.html` cell.
**HTMX:** `hx-get="/transfers/quick-edit/<xfer_id>"`.
**Affected:** Retired. After the rework, there are no transfer cells in the grid. Shadow transaction cells use the transaction click path (6.1).

### 6.12 Transfer Quick Edit Submit

**Trigger:** Blur/Enter on transfer quick edit input.
**HTMX:** `hx-patch="/transfers/instance/<xfer_id>"`.
**Affected:** Retired from the grid context. The route remains for any other context.

### 6.13 Transfer Full Edit (Expand)

**Trigger:** Expand button or F2 in transfer quick edit.
**JS:** `openTransferFullEdit(xferId)`.
**Affected:** Partially retired. The JS function remains for when the server routes a shadow transaction's full edit through the transfer form. But the entry point changes -- users now click a transaction cell, the server detects `transfer_id`, and returns the transfer edit form.

### 6.14 Carry Forward

**Trigger:** "Carry Forward Unpaid" button on past period header.
**HTMX:** `hx-post="/pay-periods/<period_id>/carry-forward"`.
**Affected:** Yes. The carry forward service must handle shadow transactions (see Section 2.4).

### 6.15 Anchor Balance Edit

**Trigger:** Click on anchor balance display.
**HTMX:** Various inline edit interactions.
**Not affected.**

### 6.16 Period Navigation

**Trigger:** Left/right arrows or Ctrl+Left/Right.
**HTMX/JS:** Updates `?start_offset=` query param.
**Not affected.**

### 6.17 Keyboard Navigation

**Trigger:** Arrow keys, Tab, Enter, Escape, Space, Home, End.
**JS:** `getDataRows()` exclusion list.
**Affected (Phase 3A-II only):** New subtotal rows and Net Cash Flow row must be added to the exclusion list.

### 6.18 Delete Transaction

**Trigger:** Not currently in grid UI -- exists as route.
**HTMX:** `hx-delete="/transactions/<txn_id>"`.
**Affected:** Must block for shadow transactions.

---

## 7. Tests

### 7.1 Transfer Route Tests

**File:** `tests/test_routes/test_transfers.py` (1,021 lines)

**Classes and functions:**

| Class | Function | Line | Tests |
|-------|----------|------|-------|
| TestTemplateList | `test_list_templates` | 163 | GET /transfers renders list |
| | `test_new_template_form` | 174 | GET /transfers/new shows form |
| TestTemplatePrefill | `test_new_transfer_prefills_from_account` | 188 | Prefill from_account |
| | `test_new_transfer_prefills_to_account` | 197 | Prefill to_account |
| TestTemplateCreate | `test_create_template` | 210 | Create with recurrence, generates transfers |
| | `test_create_template_validation_error` | 234 | Missing field validation |
| | `test_create_template_same_accounts` | 244 | Rejects from==to |
| | `test_create_template_double_submit` | 259 | Duplicate name handling |
| TestTemplateUpdate | `test_edit_template_form` | 351 | GET edit form |
| | `test_update_template` | 362 | POST update |
| | `test_delete_template` | 382 | Deactivate + soft-delete |
| | `test_reactivate_template` | 403 | Restore + regenerate |
| | `test_update_other_users_template_redirects` | 429 | IDOR blocked |
| | `test_delete_other_users_template_redirects` | 445 | IDOR blocked |
| TestGridCells | `test_get_cell` | 467 | Cell partial |
| | `test_get_quick_edit` | 479 | Quick edit form |
| | `test_get_full_edit` | 491 | Full edit form |
| | `test_get_cell_other_users_transfer` | 503 | IDOR blocked |
| TestTransferInstance | `test_update_transfer_amount` | 525 | PATCH amount, HX-Trigger |
| | `test_mark_done` | 542 | Mark done, HX-Trigger |
| | `test_cancel_transfer` | 556 | Cancel |
| | `test_delete_ad_hoc_transfer` | 569 | Hard-delete |
| | `test_delete_template_transfer_soft_deletes` | 585 | Soft-delete |
| | `test_template_transfer_override_on_amount_change` | 601 | is_override=True |
| | `test_cancelled_transfer_effective_amount_zero` | 619 | effective_amount check |
| | `test_update_other_users_transfer` | 632 | IDOR blocked |
| TestAdHoc | `test_create_ad_hoc_transfer` | 667 | Create one-time |
| | `test_create_ad_hoc_validation_error` | 684 | Validation |
| | `test_create_ad_hoc_other_users_period` | 695 | IDOR blocked |
| | `test_create_ad_hoc_double_submit` | 739 | Idempotency |
| TestTransferNegativePaths | `test_update_nonexistent_transfer_instance` | 833 | 404 on missing |
| | `test_mark_done_already_done_transfer` | 843 | Idempotent done |
| | `test_cancel_already_cancelled_transfer` | 867 | Idempotent cancel |
| | `test_quick_edit_other_users_transfer_idor` | 890 | IDOR |
| | `test_full_edit_other_users_transfer_idor` | 904 | IDOR |
| | `test_mark_done_other_users_transfer_idor` | 915 | IDOR |
| | `test_cancel_other_users_transfer_idor` | 934 | IDOR |
| | `test_create_template_with_missing_accounts` | 953 | Missing account |
| | `test_create_ad_hoc_with_zero_amount` | 972 | Zero amount |
| | `test_create_ad_hoc_with_negative_amount` | 998 | Negative amount |

**Rework impact:** Tests must be updated to verify that transfer mutations produce shadow transactions. New assertions: after create, verify two linked transactions exist; after update, verify both shadows updated; after delete, verify shadows removed. Category handling tests needed.

### 7.2 Transfer Recurrence Tests

**File:** `tests/test_services/test_transfer_recurrence.py` (768 lines)

| Class | Function | Line | Tests |
|-------|----------|------|-------|
| TestTransferGeneration | `test_every_period_generates_for_all` | 72 | Every period generates |
| | `test_no_rule_returns_empty` | 87 | No rule -> empty |
| | `test_once_pattern_returns_empty` | 122 | Once pattern -> empty |
| | `test_skips_existing_entries` | 134 | No duplicates |
| | `test_skips_overridden_and_deleted` | 152 | Respects overrides |
| TestTransferRegeneration | `test_regenerate_deletes_unmodified_and_recreates` | 228 | Clean regen |
| | `test_regenerate_raises_conflict_for_overridden` | 258 | RecurrenceConflict |
| | `test_regenerate_preserves_immutable` | 285 | Done transfers survive |
| TestTransferResolveConflicts | `test_resolve_keep_no_changes` | 371 | Keep action |
| | `test_resolve_update_clears_flags_and_applies_amount` | 398 | Update action |
| | `test_cross_user_update_blocked` | 430 | IDOR |
| | `test_cross_user_keep_blocked` | 460 | IDOR |
| | `test_same_user_update_succeeds` | 489 | Correct user |
| | `test_mixed_ownership_list` | 518 | Mixed ownership |
| TestNegativePaths | `test_zero_amount_transfer_rejected_by_db` | 632 | DB check |
| | `test_self_transfer_same_account_rejected_by_db` | 653 | DB check |
| | `test_generate_with_empty_periods_returns_empty` | 677 | Empty periods |
| | `test_immutable_status_preserved_on_regeneration` | 697 | Done survives regen |
| | `test_negative_amount_rejected_by_db` | 747 | DB check |

**Rework impact:** After the rework, `generate_for_template` calls `transfer_service.create_transfer()`. Tests must verify that each generated transfer also produces two shadow transactions. The existing pattern/conflict tests remain valid but assertions expand.

### 7.3 Balance Calculator Tests

**File:** `tests/test_services/test_balance_calculator.py` (1,323 lines)

Key transfer-related tests (other tests are transaction-only and unaffected):

- `test_mixed_transactions_and_transfers` -- Tests mix of transactions and transfers in balance calculation.
- `test_multiple_transfers_same_period` -- Multiple transfers summed correctly.
- `test_cancelled_transfers_excluded` -- Cancelled transfers excluded from balance.
- `test_five_period_rollforward_with_transfers` -- 5 periods with transfers.
- `test_52_period_penny_accuracy` -- Comprehensive 52-period test including transfers.

**Rework impact:** Transfer-specific tests must be rewritten. After the rework, the balance calculator does not accept a `transfers` parameter. Instead, shadow transactions appear in the `transactions` list. Existing transfer-specific tests must be replaced with equivalent tests that create shadow transactions.

**File:** `tests/test_services/test_balance_calculator_debt.py` (674 lines)

Transfer-related:
- `test_debt_balance_with_payments` (line 51) -- Transfer reduces balance by principal.
- `test_debt_cancelled_transfer_excluded` (line 540) -- Cancelled transfer excluded.
- `test_debt_multiple_payments_same_period` (line 609) -- Multiple transfers summed.

**Rework impact:** These tests rely on the amortization variant receiving Transfer objects. After rework, loan payments are shadow expense transactions. The amortization logic must identify payments via `transfer_id IS NOT NULL` and the linked Transfer's `to_account_id == loan_account_id`.

**File:** `tests/test_services/test_balance_calculator_hysa.py` (928 lines)

Transfer-related:
- `test_hysa_with_transfers` -- Transfer affects HYSA balance before interest.
- `test_hysa_interest_on_zero_balance_with_transfer` -- Transfer adds to zero balance.
- `test_hysa_compounding_with_periodic_deposits` -- Periodic deposits.

**Rework impact:** Same as above -- replace Transfer objects with shadow transactions.

### 7.4 Grid Route Tests

**File:** `tests/test_routes/test_grid.py` (824 lines)

- `test_grid_loads_with_periods` (line 22) -- GET / renders grid.
- `test_balance_row_returns_partial` (line 57) -- GET /grid/balance-row returns HTML.

**Rework impact:** Add tests verifying: (1) shadow transactions appear in INCOME/EXPENSES sections, (2) TRANSFERS section is absent, (3) transfer indicator is present on shadow transaction cells, (4) footer structure changes.

### 7.5 Carry Forward Tests

**File:** `tests/test_services/test_carry_forward_service.py` (295 lines)

- `test_non_template_transaction_preserves_is_override_false` (line 67)
- `test_settled_status_not_moved` (line 92)
- `test_actual_amount_preserved_after_carry_forward` (line 115)
- `test_scenario_id_preserved_after_carry_forward` (line 143)
- `test_all_statuses_comprehensive` (line 164)

**Rework impact:** Add tests for carry forward with shadow transactions: verify that shadow transactions are not moved directly but routed through the transfer service; verify that both shadows and the parent transfer move atomically; verify de-duplication (two shadows per transfer, one transfer service call).

Additional carry forward tests exist in `tests/test_services/test_credit_workflow.py` (mixed workflow tests). These test carry forward of regular transactions and are unaffected by the rework.

### 7.6 Credit Workflow Tests

**File:** `tests/test_services/test_credit_workflow.py` (788 lines)

No direct transfer references. Tests `mark_as_credit`, `unmark_credit`, and carry forward of payback transactions.

**Rework impact:** `mark_as_credit` creates a payback transaction -- the new code must set `account_id` on the payback. Existing tests need updated assertions if the payback creation is checked at the field level.

### 7.7 Chart Data Service Tests

**File:** `tests/test_services/test_chart_data_service.py` (1,648 lines)

Extensive tests for all chart types. The balance-related tests implicitly include transfers via `_calculate_account_balances()` which currently queries Transfer objects.

**Rework impact:** After removing Transfer queries from chart_data_service, tests that set up transfers and verify balance charts must instead set up shadow transactions. Category-based chart tests should verify that transfer-linked expense transactions appear in spending-by-category results.

### 7.8 Integration and Adversarial Tests

**File:** `tests/test_integration/test_workflows.py` (984 lines)

- `test_transfer_reduces_source_balance` (line ~125) -- End-to-end: transfer affects balance.

**Rework impact:** Must be rewritten to verify that transfers produce shadow transactions and those shadow transactions affect balances.

**File:** `tests/test_adversarial/test_hostile_qa.py` (1,221 lines)

Tests various hostile inputs and state machine edge cases. No direct transfer creation in adversarial tests.

**File:** `tests/test_audit_fixes.py` (667 lines)

- `test_transfer_cancelled_returns_decimal` (line 152) -- Transfer.effective_amount for cancelled.
- `test_transfer_active_returns_decimal` (line 173) -- Transfer.effective_amount for active.
- `TestTransferAccountOwnership` (line 198+) -- IDOR tests for transfer template creation with foreign accounts.
- `TestTransferBalanceCalculation` -- Tests outgoing/incoming transfer balance effects.

**Rework impact:** Balance calculation tests must be updated to use shadow transactions. IDOR tests remain valid. effective_amount property tests remain valid (Transfer model still exists).

### 7.9 Model Tests

**File:** `tests/test_models/test_computed_properties.py` (461 lines)

Tests `effective_amount` property for both Transaction and Transfer models.

**Rework impact:** Transfer effective_amount tests remain valid. Transaction tests may need new cases for shadow transaction behavior.

### 7.10 Test Fixtures

**File:** `tests/conftest.py` (947 lines)

Key fixtures creating transfers:

- `seed_full_user_data` (line 502-622): Creates TransferTemplate "Monthly Savings" (checking -> savings, $200). Returns dict including `transfer_template`.
- `seed_full_second_user_data` (line 626-745): Creates TransferTemplate "Bi-Weekly Savings" (checking -> savings, $150).

**Rework impact:** Fixtures that create TransferTemplates will need to also generate transfers (via the transfer recurrence engine or transfer service), which will then produce shadow transactions. Alternatively, new fixtures specifically for shadow transaction testing may be needed.

---

## 8. Seed Scripts and Migrations

### 8.1 Seed Scripts

**`scripts/seed_ref_tables.py`** (90 lines): Seeds all `ref.*` tables. Account types, transaction types, statuses, recurrence patterns, etc. No transfer-specific categories.

**`scripts/seed_user.py`** (185 lines): Seeds default user with checking account, baseline scenario, and default categories. Uses `DEFAULT_CATEGORIES` from `app/services/auth_service.py` (lines 22-45).

**`app/services/auth_service.py` -- DEFAULT_CATEGORIES** (lines 22-45): Current categories include `("Financial", "Savings Transfer")` and `("Financial", "Extra Debt Payment")` but do NOT include "Transfers: Incoming" or "Transfers: Outgoing".

**Rework impact:** Add `("Transfers", "Incoming")` and `("Transfers", "Outgoing")` to `DEFAULT_CATEGORIES`. The seed_user script and `register_user` function both use this list, so both paths are covered by a single change.

**Other scripts** (`seed_tax_brackets.py`, `init_database.py`, `integrity_check.py`, etc.): No transfer references. Not affected.

### 8.2 Alembic Migrations

**`migrations/versions/9dea99d4e33e_initial_schema.py`**: Creates the original `budget.transfers` table (lines 243-264) with columns: id, user_id, from_account_id, to_account_id, pay_period_id, scenario_id, status_id, amount, notes, created_at, updated_at. Creates `budget.transactions` table (lines 266-292) without `account_id` or `transfer_id`.

**`migrations/versions/d4e5f6a7b8c9_add_phase4_transfers_savings.py`**: Creates `budget.transfer_templates` table. Adds `name`, `transfer_template_id`, `is_override`, `is_deleted` columns to `budget.transfers`. Creates unique partial index on transfers.

**Other migrations:** No transfer-related changes.

**Rework impact:** Per design section 2.4, the production database will be dropped and recreated. New migrations will add: (1) `account_id` NOT NULL FK and `transfer_id` nullable FK with CASCADE to `budget.transactions`, (2) `category_id` nullable FK to `budget.transfers` and `budget.transfer_templates`. The project convention is forward migrations, so new migration files should be created for these schema changes.

---

## 9. Discovered Dependencies

These items were found during the audit but are not explicitly covered in the design document.

### 9.1 Transfer Model Has No `actual_amount` Column

The design document section 4.2 (`create_transfer`) states: "actual_amount: NULL initially. Set when the transfer is marked done/paid." And section 4.3 (`update_transfer`) lists `actual_amount` as an updatable field. However, the current `Transfer` model has only an `amount` column -- there is no `actual_amount` column.

**Options:**
1. Add `actual_amount` to the Transfer model. The shadow transactions already have `actual_amount`. The transfer service would copy this value to both shadows.
2. Do not add `actual_amount` to Transfer. The transfer `amount` is the estimated amount. When marked done, the user enters the actual on the shadow transaction (or the transfer edit form sets it), and the service propagates to both shadows. The Transfer model does not track actuals separately.

**Recommendation:** Clarify with the developer. Option 2 is simpler -- the shadow transactions already have the `actual_amount` field, and the Transfer's `amount` serves as the estimate.

### 9.2 `investment_projection.py` Reads Transfer Objects Directly

The `calculate_investment_inputs` function (lines 72-84) receives `all_transfers` as a parameter and filters for transfers targeting the investment account to calculate periodic contributions and YTD totals. After the rework, the design principle is that consumers read only `budget.transactions`.

**Options:**
1. Modify `investment_projection.py` to derive contribution data from income shadow transactions with `transfer_id IS NOT NULL` and `account_id == investment_account_id`.
2. Allow `investment_projection.py` to continue reading Transfer objects as an exception, since Transfer is the source of truth.

**Recommendation:** Option 1 aligns with the design principle. The shadow income transaction for the investment account will have the transfer amount and can be summed identically.

### 9.3 `retirement.py` and `investment.py` Routes Query Transfer Objects

Both `app/routes/investment.py` (lines 99-109) and `app/routes/retirement.py` (lines 159-170) query `Transfer` objects to calculate contributions to investment/retirement accounts. After the rework, these queries should be replaced with queries on `budget.transactions` (income transactions with `transfer_id IS NOT NULL` targeting the account).

### 9.4 `savings.py` Route Loads Transfers Separately

`app/routes/savings.py` (lines 67-88) loads all transfers for balance projections and passes them to the balance calculator. This must be removed after the rework.

### 9.5 Keyboard Navigation Does Not Exclude `section-banner-transfer`

In `app/static/js/app.js` line 361-364, the `getDataRows()` function excludes `section-banner-income`, `section-banner-expense`, `spacer-row`, and `group-header-row` but does NOT exclude `section-banner-transfer`. This means transfer banner rows are currently keyboard-navigable, which is likely unintended. The rework eliminates the TRANSFERS section, so this becomes moot, but it is a pre-existing minor bug.

### 9.6 `_transfer_cell.html` Uses `xfer.amount` Not `xfer.effective_amount`

At line 28-30 of `app/templates/transfers/_transfer_cell.html`, the amount is displayed as `xfer.amount` rather than `xfer.effective_amount`. For cancelled transfers, `effective_amount` returns `Decimal("0")` while `amount` returns the original amount. This means cancelled transfers display their original amount in the grid cell even though they have no balance effect. This is a pre-existing display bug. The rework eliminates transfer cells from the grid, making this moot, but worth noting.

### 9.7 No Transfer Service File Exists Yet

The design document (section 4) specifies a new or heavily modified transfer service. Currently, there is no `app/services/transfer_service.py` file. Transfer creation, update, and deletion are handled directly in the route handlers (`app/routes/transfers.py`) and the recurrence engine (`app/services/transfer_recurrence.py`). The transfer service will be a new file.

### 9.8 `create_inline` and `create_transaction` Routes Need `account_id`

The transaction creation routes at `app/routes/transactions.py` lines 318-363 (`create_inline`) and 366-392 (`create_transaction`) create Transaction objects without setting `account_id` (because the column does not exist yet). After the rework, these must set `account_id`. The grid context knows which account is being viewed (from `account_resolver`), so the `account_id` can be inferred from the grid's current account or passed as a hidden form field.

### 9.9 Marshmallow Validation Schemas Need Updates

**File:** `app/schemas/validation.py`

Transfer-related schemas that need category field additions:

- `TransferTemplateCreateSchema` (line 312): Add `category_id` field (optional Integer).
- `TransferTemplateUpdateSchema` (line 348): Add `category_id` field (optional Integer).
- `TransferCreateSchema` (line 363): Add `category_id` field (optional Integer).
- `TransferUpdateSchema` (line 387): Add `category_id` field (optional Integer).

Transaction-related schemas that may need `account_id`:

- Any schema used by `create_inline` or `create_transaction` must include `account_id` as a required field.

### 9.10 Audit Infrastructure Includes Transfer Tables

**File:** `tests/conftest.py` lines 859-860: The audit trigger infrastructure in tests creates triggers for `budget.transfers` and `budget.transfer_templates`. These tables still exist after the rework and continue to be audited. No changes needed.

### 9.11 `balance_calculator_debt.py` Amortization Logic Needs Transfer Detection Rework

The `calculate_balances_with_amortization` function (lines 260-267 of `balance_calculator.py`) currently detects loan payments by examining Transfer objects: it checks if a transfer's `to_account_id` matches the loan `account_id`. After the rework, loan payments will be shadow expense transactions in the source account and shadow income transactions in the loan account. The amortization logic must identify loan payments from shadow income transactions with `transfer_id IS NOT NULL` in the loan account, or from the transactions list filtered by `account_id == loan_account_id` and `transfer_id IS NOT NULL`.

### 9.12 Phase 3A-II Subtotal Rows Need New CSS Classes

For Phase 3A-II, the inline subtotal rows (Total Income, Total Expenses) and the Net Cash Flow row need distinct CSS classes (e.g., `subtotal-row`, `net-cash-flow-row`) that must be:
1. Added to `app.css` for styling.
2. Added to the `getDataRows()` exclusion list in `app.js`.
3. Documented for any future template that renders the grid.
