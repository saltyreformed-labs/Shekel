> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase Scope: Spending Tracker and Companion View

**Version:** 1.0
**Date:** April 7, 2026
**Parent Documents:** project_roadmap_v4-5.md, project_requirements_v2.md,
project_requirements_v3_addendum.md
**Status:** Draft -- pending developer review

---

## 1. Problem Statement

The app excels at tracking fixed, known financial obligations against pay periods. However,
roughly 15 recurring transactions represent variable spending where the budgeted amount is a
ceiling, not a contractual payment. These include groceries, gas, personal spending money,
birthday gifts, holiday spending, clothes, and homeschool curriculum.

Today, these are modeled as single recurring transactions with a fixed estimated amount (e.g.,
Groceries: $500/paycheck). The actual spending pattern is multiple smaller purchases across the
period. The current workaround is either:

- The developer manually updates the estimated amount after each purchase to track what remains
  (error-prone, tedious).
- Cash is withdrawn for the full budgeted amount so the spender has physical visibility into what
  remains (eliminates digital tracking, creates friction when online purchases or card use is
  needed).

The spender (Kayla) handles most of the purchasing for these items but does not use the app because
the full budget view is overwhelming and irrelevant to her needs. She only cares about what she is
responsible for spending and how much remains.

### Goals

1. Allow individual purchases to be recorded against a budgeted transaction, with a computed
   remaining balance visible at all times.
2. Support credit card purchases at the individual entry level, with aggregated CC payback
   generation.
3. Provide Kayla with a simplified, mobile-first interface showing only her tagged transactions,
   with the ability to add entries and mark transactions as Paid.
4. Eliminate the need for cash-based spending tracking for items where digital tracking is
   preferred.

### Non-Goals

- This phase does not implement full discretionary budgeting (category-level spending envelopes,
  zero-based allocation, rollover between categories).
- This phase does not implement full multi-user (registration flow, kid accounts, role hierarchy,
  account sharing model). It implements the minimum companion access needed for Kayla.
- This phase does not change the balance calculator's fundamental approach (calculate on read, no
  stored balances). It extends the effective amount logic to account for sub-entries and
  entry-level credit.
- This phase does not change how non-entry-capable transactions work. Mortgages, subscriptions,
  fixed bills, and transfers are completely unaffected.

---

## 2. Feature Overview

### 2.1 Sub-Transaction Tracking (Transaction Entries)

Transaction templates can be flagged with `track_individual_purchases`. Transactions generated from
flagged templates support sub-entries -- individual purchase records that accumulate against the
parent transaction's estimated amount. A computed remaining balance is displayed in the grid and
the companion view.

### 2.2 Entry-Level Credit Card Workflow

Each sub-entry can be flagged as a credit card purchase. Credit entries are excluded from the
checking balance impact and generate a CC Payback in the next pay period. All credit entries under
one parent transaction in one period produce a single aggregated CC Payback. The payback amount
updates dynamically as credit entries are added, edited, or deleted.

### 2.3 Companion View

Kayla receives a companion user account with a simplified, mobile-first interface. She sees only
transactions from templates tagged as visible to her. She can add, edit, and delete sub-entries,
mark parent transactions as Paid, and navigate between pay periods. She cannot see the full grid,
account balances, dashboards, or settings.

---

## 3. Data Model

### 3.1 New Table: `budget.transaction_entries`

Stores individual purchase records against a parent transaction.

```
budget.transaction_entries
- id: SERIAL PRIMARY KEY
- transaction_id: INT NOT NULL FK -> budget.transactions(id)
- user_id: INT NOT NULL FK -> auth.users(id)
  -- The user who created the entry (you or Kayla). Used for audit/attribution,
  -- not for visibility filtering. Both users see all entries on shared transactions.
- amount: NUMERIC(10,2) NOT NULL
- description: VARCHAR(200) NOT NULL
  -- Store name or brief note (e.g., "Kroger", "Amazon order", "Target - kids clothes")
- entry_date: DATE NOT NULL DEFAULT CURRENT_DATE
  -- Date of the purchase. Auto-populated, editable.
- is_credit: BOOLEAN NOT NULL DEFAULT FALSE
  -- Whether this entry was paid with the credit card.
- credit_payback_id: INT FK -> budget.transactions(id) NULLABLE
  -- Links credit entries to the aggregated CC Payback transaction generated in the
  -- next period. All credit entries under one parent transaction in one period share
  -- the same credit_payback_id. NULL for debit entries.
- created_at: TIMESTAMPTZ NOT NULL DEFAULT NOW()
- updated_at: TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

**Indexes:**
- `(transaction_id)` -- primary lookup path for entries by parent transaction.
- `(transaction_id, is_credit)` -- for efficient credit entry summation.

**Constraints:**
- `amount > 0` -- entries represent spending; the sign is always positive.
- `transaction_id` must reference a transaction whose template has
  `track_individual_purchases = TRUE`. Enforced at the service level, not the database level,
  because the template flag lives on the template, not the transaction.

### 3.2 Modified Table: `budget.transaction_templates`

Add one column:

```
- track_individual_purchases: BOOLEAN NOT NULL DEFAULT FALSE
  -- When TRUE, transactions generated from this template support sub-entries
  -- with remaining balance tracking, progress indicator in the grid, and
  -- entry-level credit card workflow.
```

### 3.3 Modified Table: `budget.transaction_templates` (Companion Visibility)

Add one column:

```
- companion_visible: BOOLEAN NOT NULL DEFAULT FALSE
  -- When TRUE, transactions generated from this template are visible in the
  -- companion view. Set per-template by the full-access user. Does not affect
  -- visibility in the full grid (the full-access user always sees everything).
```

**Design note:** `track_individual_purchases` and `companion_visible` are independent flags. A
template can be companion-visible without tracking individual purchases (e.g., Ariella's birthday
-- Kayla sees it as an upcoming expense but doesn't need sub-entries). A template can track
individual purchases without being companion-visible (if the developer wanted sub-entry tracking
on something Kayla doesn't handle). In practice, most templates flagged for one will be flagged
for both, but the flags serve different purposes and should remain independent.

### 3.4 Companion User Support

**Modified table: `auth.users`**

Add one column:

```
- role: VARCHAR(20) NOT NULL DEFAULT 'owner'
  CHECK (role IN ('owner', 'companion'))
  -- 'owner' has full access to all app features (current behavior).
  -- 'companion' sees only companion-visible transactions and the companion UI.
```

**No registration flow.** The owner creates companion accounts through a settings page or a seed
script. The companion user has standard authentication (username/password, Flask-Login session).

**No changes to existing queries.** All existing routes continue to filter by `user_id` as they
do today. The companion view uses its own routes that query the owner's transactions filtered by
`companion_visible` templates. The companion user does not own transactions -- they interact with
the owner's transactions.

**Implication:** The companion user needs to know which owner's data to access. Since this is a
single-household app with one owner, the simplest approach is a `linked_owner_id` column on the
companion's user record that points to the owner. All companion queries filter by the owner's
`user_id`. This avoids modifying every existing query to handle shared data.

```
- linked_owner_id: INT FK -> auth.users(id) NULLABLE
  -- NULL for owner accounts. For companion accounts, references the owner whose
  -- budget data this companion can access.
```

---

## 4. Balance Calculator Changes

### 4.1 Current Behavior (Unchanged for Non-Entry Transactions)

```
effective_amount = actual_amount if actual_amount is not None else estimated_amount
```

- Status Projected: uses estimated_amount.
- Status Paid/Settled: uses actual_amount (falls back to estimated if NULL).
- Status Credit: excluded from checking balance; CC payback generated.

### 4.2 New Behavior for Entry-Capable Transactions

When a transaction has `track_individual_purchases = TRUE` on its template:

**Projected status with entries:**

> **Addendum (April 2026, is_cleared fix):** the formula below was
> extended to partition debit entries into cleared/uncleared buckets
> so the balance calculator stops double-counting debit purchases
> once the user has trued up the checking anchor. The semantics of
> the original formula are preserved when every entry is uncleared
> (the default for new entries). See section 4.2.1 below for details.

```
sum_debit = sum of entries where is_credit = FALSE
sum_credit = sum of entries where is_credit = TRUE
sum_all = sum_debit + sum_credit

cleared_debit   = sum of entries where not is_credit and     is_cleared
uncleared_debit = sum of entries where not is_credit and not is_cleared

checking_impact = max(
    estimated - cleared_debit - sum_credit,
    uncleared_debit,
)
```

Explanation:

- The full estimated amount is reserved from checking, minus the portion covered by credit card
  (since credit entries don't hit checking) and minus the portion that has already been cleared
  against the anchor balance (those dollars are already gone from real checking and are reflected
  in the anchor number the user entered).
- If uncleared debit spending exceeds the adjusted reservation (overspend scenario), the uncleared
  debit total is used instead, immediately reflecting the overspend in projections.
- Cleared debits that exceed the estimated amount floor the reservation at zero (the overspend
  is already in the anchor, so no additional checking reservation is needed).
- Credit entries generate a CC Payback in the next period (see section 5). `is_cleared` is stored
  but ignored on credit entries.

### 4.2.1 The is_cleared flag

`budget.transaction_entries.is_cleared` is a boolean column, default FALSE, added by migration
`c7e3a2f9b104_add_is_cleared_to_transaction_entries.py`. It answers the question "is this purchase
already reflected in the current checking anchor balance?"

- **New entries** default to `is_cleared = FALSE`. Until reconciled, they behave exactly like the
  original formula: the full estimated amount stays reserved.
- **Anchor true-up auto-clear.** When the user updates the anchor balance on a **checking**
  account (via `accounts.true_up`, `accounts.inline_anchor_update`, or the anchor branch of
  `accounts.update_account`), every past-dated uncleared entry whose parent transaction is
  `Projected` and not soft-deleted is flipped to `is_cleared = TRUE`. This models the user's
  workflow: "I just looked at my real checking balance, every purchase that has already posted
  is in that number." Future-dated entries and entries on already-settled parents are left alone.
  Non-checking account true-ups do not touch entries.
- **Manual per-entry toggle.** A small check-circle icon in the entry list
  (`_transaction_entries.html`) posts `PATCH /transactions/<txn_id>/entries/<entry_id>/cleared`
  and flips the flag for one entry. Used to correct edge cases where auto-clear is wrong for a
  specific purchase (e.g. a debit that posted after the most recent anchor update but was
  incorrectly auto-cleared, or one the user wants to exclude from the reservation before they've
  formally updated the anchor). Hidden in the UI for credit entries.
- **Backfill on migration.** The upgrade script backfills `is_cleared = TRUE` for every existing
  entry whose parent is `Projected` and whose `entry_date <= CURRENT_DATE`. This keeps balances
  stable on the day the feature ships, matching the assumption that the current anchor already
  reflects all past-dated purchases on in-flight projected transactions.

**Examples:**

Below "Debit" is split into cleared and uncleared, and "checking_impact" uses the new formula
`max(estimated - cleared_debit - credit, uncleared_debit)`. The first six rows correspond to the
original table (every row has `cleared = 0`); the bottom rows cover the new behavior when
entries are reconciled with the anchor.

| Scenario | Estimated | Cleared | Uncleared | Credit | checking_impact | CC Payback | Notes |
|----------|-----------|---------|-----------|--------|-----------------|------------|-------|
| No entries yet | $500 | $0 | $0 | $0 | $500 | $0 | Full reservation |
| Mid-period, under budget (stale anchor) | $500 | $0 | $200 | $0 | $500 | $0 | Still fully reserved |
| Mid-period, mixed payment (stale anchor) | $500 | $0 | $300 | $100 | $400 | $100 | Credit reduces reservation |
| Under budget, all credit | $500 | $0 | $0 | $400 | $100 | $400 | Remaining $100 reserved |
| Over budget, debit only (stale anchor) | $500 | $0 | $530 | $0 | $530 | $0 | Overspend hits balance |
| Over budget, mixed (stale anchor) | $500 | $0 | $400 | $200 | $400 | $200 | Debit exceeds (est - credit) |
| Grocery bug fixed (all cleared) | $500 | $462.34 | $0 | $0 | **$37.66** | $0 | Anchor reflects the purchases, only the remainder is held |
| Partial reconcile | $500 | $100 | $50 | $0 | $400 | $0 | $350 future budget + $50 uncleared floor |
| Cleared overspend | $500 | $600 | $0 | $0 | $0 | $0 | Overspend already in anchor -- reservation floored |
| Cleared debit + credit | $500 | $200 | $0 | $100 | $200 | $100 | $200 in anchor, CC payback next period, $200 reserved |

**Projected status without entries:**

No change. Uses `estimated_amount` as today.

**Paid status:**

The `actual_amount` is auto-populated from the sum of all entries (debit + credit) when the
transaction is marked Paid. If no entries exist, `actual_amount` is entered manually as today.
The balance calculator uses `actual_amount` per current logic. Credit entries have already
generated their CC Payback; the actual amount on the Paid transaction represents total spending
for analytics and reporting purposes.

**Edge case -- Paid with no entries:** If the transaction is marked Paid and no entries exist,
it behaves exactly as today. The `track_individual_purchases` flag enables sub-entry capability;
it does not require sub-entries. This preserves backward compatibility for periods where the user
doesn't bother recording individual purchases.

**Edge case -- Entries added after Paid:** If entries are added to a transaction already in Paid
status, the actual amount should update to reflect the new sum. This handles late-posting
purchases. If the user manually entered an actual that differs from the entry sum, the entry sum
takes precedence once entries exist (the manual actual is overwritten).

### 4.3 Remaining Balance Calculation

The remaining balance is a display-only value, not stored in the database. It is computed by the
transaction entry service:

```
remaining = estimated_amount - sum_of_all_entries
```

This uses the sum of ALL entries (debit and credit), because the remaining balance represents how
much of the budget has been consumed, regardless of payment method. If Kayla has a $500 grocery
budget and has spent $300 on debit and $100 on credit, she has $100 remaining to spend -- the
payment method doesn't affect how much budget is left.

A negative remaining value means overspent. The UI should display this clearly (e.g., "-$30 over"
in a warning color).

---

## 5. Credit Card Workflow Changes

### 5.1 Current Credit Card Workflow (Unchanged)

When a regular (non-entry-capable) transaction is marked Credit:

1. The transaction is excluded from checking balance.
2. A CC Payback transaction is auto-generated in the next pay period.
3. The payback is linked via `credit_payback_for_id`.
4. If the credit status is reversed, the payback is deleted.

This workflow is unchanged for transactions without `track_individual_purchases`.

### 5.2 Entry-Level Credit Card Workflow (New)

For entry-capable transactions, the credit card workflow operates at the sub-entry level:

**When a sub-entry is created with `is_credit = TRUE`:**

1. Check if an aggregated CC Payback already exists for this parent transaction in the next
   period.
   - If yes: update the payback's estimated amount to the new sum of all credit entries for this
     parent transaction.
   - If no: create a new CC Payback transaction in the next pay period. Set its estimated amount
     to the credit entry's amount. Link it to the parent transaction. Store the
     `credit_payback_id` on the entry.
2. The credit entry is excluded from the checking balance (handled by the balance calculator
   formula in section 4.2).

**When a credit sub-entry is edited:**

1. Recalculate the sum of all credit entries for the parent transaction.
2. Update the aggregated CC Payback's estimated amount.

**When a credit sub-entry is deleted:**

1. Recalculate the sum of all credit entries for the parent transaction.
2. If remaining credit entries exist: update the CC Payback amount.
3. If no credit entries remain: delete the CC Payback transaction.

**When a sub-entry's `is_credit` flag is changed (debit -> credit or credit -> debit):**

1. Treat as a deletion from the old type and creation of the new type.
2. Recalculate and update/create/delete the CC Payback accordingly.

**CC Payback properties:**

- Name: `CC Payback: {parent transaction name}` (e.g., "CC Payback: Groceries")
- Category: Credit Card: Payback (existing category)
- Estimated amount: sum of all credit entries under the parent transaction for the period
- Status: Projected
- Linked to the parent transaction via `credit_payback_for_id`
- The CC Payback is a regular transaction, not entry-capable.

**Interaction with parent transaction status:**

- When the parent is marked Paid, the CC Payback's estimated amount is finalized to the sum of
  credit entries at that point. The payback remains in Projected status (it's a future obligation).
- The CC Payback follows the existing reconciliation workflow: the user marks it Paid in the next
  period when the credit card payment clears.

**Parent transaction cannot use the legacy Credit status** if it has `track_individual_purchases`
enabled. The credit card workflow is handled entirely at the entry level. If the parent transaction
status dropdown includes "Credit" as an option, it should be disabled or hidden for entry-capable
transactions. This prevents conflicting credit handling (entry-level credit + parent-level credit
would double-count).

---

## 6. Companion View

### 6.1 Access Model

- The owner creates a companion account through the settings UI (or seed script for initial setup).
- The companion logs in with their own username and password.
- Flask-Login session management works identically to the owner's login.
- The companion is redirected to the companion view upon login, not the full grid.
- The companion cannot navigate to full-access routes. Route guards check the user's role and
  return 404 for unauthorized companion access.

### 6.2 Companion View Layout

**Mobile-first, single-period view with navigation.**

The companion view displays one pay period at a time. The current period loads by default.
Forward and back arrows navigate between periods (matching the existing mobile grid navigation
pattern).

**Period display:**

- Period date range displayed as a header (e.g., "Apr 11 -- Apr 24, 2026").
- Navigation arrows for previous/next period.

**Transaction list:**

- Shows all transactions from companion-visible templates for the displayed period.
- Each transaction displays:
  - Transaction name (e.g., "Groceries")
  - For entry-capable transactions: progress indicator ("$330 / $500") and remaining ("$170
    remaining" or "-$30 over")
  - For non-entry-capable transactions: estimated amount and status
  - Status indicator (Projected, Paid, etc.)
- Transactions are grouped by the same category group headers used in the full grid.
- Tapping an entry-capable transaction opens the transaction detail (sub-entries list + add entry
  form).
- Tapping a non-entry-capable transaction opens the existing bottom sheet for marking status
  (Projected -> Paid) and entering an actual amount.

**Transaction detail (entry-capable transactions):**

- Transaction name and progress indicator at the top.
- Remaining balance prominently displayed.
- List of existing entries: description, amount, date, credit card indicator.
- Each entry has edit and delete actions.
- "Add Entry" form at the bottom (or triggered by a button):
  - Amount field (numeric, required)
  - Description field (text, required)
  - Date field (auto-populated with today, editable)
  - Credit card toggle (boolean, default OFF)
  - Submit button
- "Mark as Paid" button visible when the transaction is in Projected status.

### 6.3 What the Companion Cannot Do

- View or access the full budget grid.
- View account balances, dashboards, or projected end balances.
- View transactions not tagged as companion-visible.
- Create, edit, or delete transaction templates.
- Modify budget settings, account settings, or any configuration.
- Create, edit, or delete recurring transactions or transfers.
- Access the analytics, calendar, or reporting views.
- Manage other users or companion accounts.

### 6.4 What the Companion Can Do

- Log in and out.
- View companion-visible transactions across all pay periods.
- Navigate between pay periods.
- Add, edit, and delete sub-entries on entry-capable transactions.
- Mark entry-capable and non-entry-capable transactions as Paid.
- Enter an actual amount when marking a non-entry-capable transaction as Paid.
- View the remaining balance on entry-capable transactions.
- View upcoming transactions in future periods.

---

## 7. Grid Changes (Full-Access View)

### 7.1 Progress Indicator

For transactions with `track_individual_purchases` enabled and at least one entry recorded, the
grid cell displays a progress indicator instead of the standard amount:

- **Under budget:** "$330 / $500" in default styling.
- **At budget:** "$500 / $500" in default styling.
- **Over budget:** "$530 / $500" in warning styling (color TBD -- consistent with existing warning
  indicators).
- **No entries yet:** Standard display showing "$500" (estimated amount). No progress indicator
  until the first entry is recorded.

For transactions in Paid status, the grid cell reverts to the standard actual amount display,
consistent with all other Paid transactions.

### 7.2 Tooltip Enhancement

The existing tooltip (from task 4.12) should be extended for entry-capable transactions to show:

- Estimated amount (budget)
- Total spent (sum of entries)
- Remaining (or overspent amount)
- Entry count (e.g., "3 entries")
- Credit total if any credit entries exist (e.g., "includes $83 on credit card")

### 7.3 Transaction Detail / Bottom Sheet

When an entry-capable transaction is tapped in the grid (desktop or mobile), the detail view
includes:

- All existing entry CRUD and remaining balance display (same as companion view, section 6.2).
- The full-access user can also edit the parent transaction's estimated amount, status, and all
  other fields that are currently editable.

### 7.4 Template Settings

The transaction template edit page gains two new toggles:

- **Track individual purchases** (`track_individual_purchases`): "Enable tracking of individual
  purchases against this transaction's budgeted amount. Each pay period, you can record individual
  entries and see how much budget remains."
- **Show in companion view** (`companion_visible`): "Make this transaction visible to companion
  users. Companion users can view the transaction, add entries, and mark it as Paid."

Both default to FALSE. They are independent and can be set in any combination.

---

## 8. Migration Path

### 8.1 Existing Template Updates

The 15 templates that will use these features need to be updated with the new flags. This is a
one-time manual action by the developer through the template edit UI after deployment, or through
a data migration script. No automated migration of existing transaction data is needed -- the
feature is additive.

### 8.2 Existing Transactions

Transactions already generated from updated templates do not retroactively gain entries. The
entry capability applies going forward. Past Paid transactions remain as-is with their manually
entered actual amounts. Current-period Projected transactions can begin accepting entries
immediately after deployment.

### 8.3 Cash Workflow Transition

After deployment, the household can transition from cash-based tracking to digital entry at their
own pace. The feature does not require immediate adoption for all eligible transactions. Templates
can be flagged one at a time as the household adjusts their workflow.

---

## 9. Dependencies

### 9.1 No Hard Prerequisites

This phase has no dependencies on any in-progress or planned phase. It can be implemented at any
point after the current Section 8 (Visualization and Reporting Overhaul) is complete, or
concurrently if desired.

### 9.2 Downstream Considerations

**Section 8 (Visualization and Reporting):** If Section 8 is still in progress, the analytics
and reporting design should be made aware of sub-entry data. Budget variance analysis (6.5) and
spending trend detection (6.7) should account for per-entry data when available. The year-end
summary (8.3) should include per-entry detail for entry-capable transactions. If Section 8
completes before this phase, a follow-up task to integrate entry data into analytics may be
needed.

**Phase 9 (Smart Features):** Smart estimates (rolling averages) could use entry-level data for
more accurate variable expense projections. Expense anomaly detection (6.12) could flag unusual
individual entries, not just unusual period totals.

**Phase 10 (Notifications):** Sub-entry data enables notifications like "You've spent 80% of
your grocery budget with 8 days left in the period" or "Groceries was overspent 3 of the last 5
periods."

**Phase 9 Multi-User (roadmap Section 9):** The companion access model built here is a deliberate
subset of full multi-user. When full multi-user is implemented, the companion role and
`linked_owner_id` approach should be evaluated for compatibility with the broader
role/permission/sharing model. The companion implementation should not create patterns that
conflict with the eventual multi-user design. Key consideration: the companion accesses the
owner's data via `linked_owner_id`. Full multi-user may use a shared-account model instead. The
migration from one to the other should be assessed during multi-user design.

---

## 10. Open Questions for Implementation Planning

These do not need to be answered before accepting the scope. They will be resolved during the
implementation plan.

1. **Entry form location on desktop:** Does the entry form appear inline in the grid cell, in a
   sidebar, in a modal, or in a bottom sheet (matching mobile)? The mobile pattern is established
   (bottom sheet). Desktop may benefit from a different layout.

2. **Entry attribution display:** When both you and Kayla add entries to the same transaction,
   should the entry list show who added each entry? ("Kroger - $147.32 - Kayla" vs. just
   "Kroger - $147.32"). Useful for auditing but adds visual clutter.

3. **Companion account creation UI:** A page in settings where the owner creates companion
   accounts (username, password, display name, linked templates), or a seed script for initial
   setup with a settings page for managing visibility tags afterward.

4. **CC Payback update timing:** When a credit entry is added, the aggregated CC Payback in the
   next period updates. Should this be synchronous (immediate, within the same request) or
   handled by a lightweight background process? Synchronous is simpler and consistent with the
   existing CC workflow.

5. **Companion session duration:** Should the companion's "remember me" session be longer than
   the owner's? Kayla's phone usage pattern (quick entry from the parking lot) benefits from
   staying logged in for an extended period.

---

## 11. Task Inventory (Preliminary)

This is a rough task breakdown for estimation purposes. A detailed implementation plan with
sequencing, file-level scope, and test specifications will be written before implementation
begins.

### Data Model and Migration

- 11.1: Add `track_individual_purchases` column to `budget.transaction_templates`.
- 11.2: Add `companion_visible` column to `budget.transaction_templates`.
- 11.3: Create `budget.transaction_entries` table.
- 11.4: Add `role` and `linked_owner_id` columns to `auth.users`.
- 11.5: Alembic migration (upgrade and downgrade tested).

### Service Layer

- 11.6: Create `services/transaction_entry_service.py` -- CRUD for entries, remaining balance
  computation, entry summation, validation.
- 11.7: Extend credit card service -- entry-level credit handling, aggregated CC Payback
  creation/update/deletion.
- 11.8: Extend balance calculator -- entry-aware effective amount computation with credit
  adjustment.
- 11.9: Extend transaction service -- auto-populate actual amount from entry sum when marking
  Paid; prevent legacy Credit status on entry-capable transactions.

### Template Settings UI

- 11.10: Add `track_individual_purchases` and `companion_visible` toggles to the transaction
  template edit page.

### Grid Integration

- 11.11: Progress indicator rendering in grid cells for entry-capable transactions.
- 11.12: Tooltip enhancement for entry-capable transactions.
- 11.13: Transaction detail / bottom sheet with entry list and add/edit/delete entry forms.

### Companion Access

- 11.14: Add companion role to user model and route guards.
- 11.15: Companion account creation interface (settings page or seed script).
- 11.16: Companion login routing (redirect to companion view on login).

### Companion View

- 11.17: Companion view route and template -- single-period display with navigation.
- 11.18: Companion transaction list with progress indicators and status.
- 11.19: Companion transaction detail with entry CRUD and mark-as-Paid.
- 11.20: Mobile optimization and testing of companion view.

### Testing

- 11.21: Transaction entry service unit tests (CRUD, remaining balance, edge cases).
- 11.22: Balance calculator tests with entry-capable transactions (all scenarios from section 4.2
  table).
- 11.23: Credit card workflow tests (entry-level credit, aggregated payback, edit/delete
  scenarios).
- 11.24: Companion view integration tests (visibility filtering, entry creation, status changes).
- 11.25: Full regression suite -- verify all existing tests pass with no modifications.

---

## 12. Affected Transactions (Reference)

Transactions expected to be flagged with `track_individual_purchases` and `companion_visible`:

| Transaction | Amount | Recurrence | track_individual_purchases | companion_visible |
|-------------|--------|------------|---------------------------|-------------------|
| Groceries | $500.00 | Every paycheck | Yes | Yes |
| Gas | $80.00 | Every paycheck | Yes | Yes |
| Kayla (spending money) | $100.00 | Every paycheck | Yes | Yes |
| Christmas | $600.00 | Yearly (Nov 1) | Yes | Yes |
| Clothes | $600.00 | Every 6 months | Yes | Yes |
| Homeschool Curriculum | $1,000.00 | Yearly (May 20) | Yes | Yes |
| Ariella (birthday) | $100.00 | Yearly (Oct 7) | Yes | Yes |
| Cyrus (birthday) | $100.00 | Yearly (Aug 15) | Yes | Yes |
| Eliana (birthday) | $100.00 | Yearly (Jan 8) | Yes | Yes |
| Kayla's Birthday | $100.00 | Yearly (May 1) | Yes | Yes |
| Knox (birthday) | $100.00 | Yearly (Feb 22) | Yes | Yes |
| Valentine's Day | $100.00 | Yearly (Feb 14) | Yes | Yes |
| Father's Day | $100.00 | Yearly (Jun 14) | Yes | Yes |
| Mother's Day | $100.00 | Yearly (May 10) | Yes | Yes |
| Wedding Anniversary | $100.00 | Yearly (Oct 18) | Yes | Yes |
| Strawberry Picking | $200.00 | Yearly (Apr 15) | Yes | Yes |

Transactions that may be `companion_visible` but NOT `track_individual_purchases`:

None identified at this time. All companion-visible transactions are expected to use entry
tracking. This can be adjusted per-template after deployment.
