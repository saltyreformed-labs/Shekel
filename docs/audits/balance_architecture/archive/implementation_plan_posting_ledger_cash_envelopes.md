> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation plan: post confirmed cash transactions + cleared envelope entries

**Status:** SHIPPED TO PROD (2026-06-29) -- all 8 commits, via PR #49 (`dev -> main`, merge
`9a1c883`) plus the deploy-fix PR #50 (merge `8c99799`); prod digest `aadcbdac3b99`, healthy, and the
ledger reconciles on real prod data (per-entry / trial-balance / completeness / per-(account,scenario)
all zero). C1 `72bdd77` (ref rows, migration `97bc03c2aa4c`), C2 `1b4d785` (schema `bdde62675c9b`;
revised mid-flight to add the `is_fallback` discriminator -- see Section 4.2's H1 fix), C3 `1190a46`
(the category/fallback resolver), C4 `fa79630` (the `posting_service.sync_transaction_postings`
builder), C5 `4c21af1` (the status-mechanics seam + the W9907 bypass checker + born-Projected -- see
the Commit 5 As-built note for the two developer-approved deviations), C6 `671f49f` (posting
emission: reconcile once at each handler's end + the entry/delete hooks), C7 `48479ef` (the
production-wide historical backfill migration `7d63529e4300`), C8 `57b74f3` (the 13-test cash
reconciliation oracle `tests/test_integration/test_posting_ledger_cash_reconciliation.py` + docs).
Full suite **6640 passed** (the +2 are the deploy-fix regression tests), `pylint app/ scripts/` 10.00.

**Deploy-fix deviation (NOT in the original plan -- the as-built record).** The first `shekel-deploy`
of the merged Step 3 FAILED its healthcheck and auto-rolled back (prod stayed healthy on the prior
image; DB clean at `db239773c2fd`; no outage). Root cause: the entrypoint's migration host
(`scripts/init_database.py`) builds the app via `create_app()` BEFORE running migrations, and
`create_app()` eagerly runs `ref_cache.init()`, which is FATAL on a missing ROW in an EXISTING ref
table (it tolerates only missing whole TABLES). Step 3 is the first migration to ADD rows to
pre-existing ref tables (`income`/`expense` posting kinds, the `transaction` source), so on the
pre-migration prod DB those rows were absent and `create_app()` raised before the seeding migration
could run. (Step 2 added new ref tables -- tolerated -- so this stayed latent; CI's fresh base->head
build and the bind-mounted dev container also never reproduced it.) Fix (PR #50, commit `3104f87`):
`create_app(config_name=None, *, init_ref_cache=True)`; the migration host calls
`create_app(init_ref_cache=False)` so the eager row-check is skipped on the pre-migration DB, while
Gunicorn / dev / test keep the strict default. Durable for Steps 4-5 (they also add ref rows);
regression-locked by `tests/test_ref_cache.py::TestCreateAppRefCacheGate`; verified on a prod clone.
NEXT (separate effort) = Build-Order Step 4 (loan-payment principal/interest splits).
**Build-Order Step 3** of the Option D architecture
(`docs/audits/balance_architecture/level1_level2_scope_and_fitness.md`, Decision section, build
order item 3: "Post confirmed cash transactions and cleared envelope entries at settle").
**Enabled by:** Build-Order Step 2 (the append-only double-entry posting ledger +
`budget.ledger_accounts` chart of accounts, shipped to prod in PR #48). Step 3 reuses Step 2's
tables, balanced-journal trigger, and `posting_service` write path unchanged; it adds the
income/expense counter-leg, the per-category chart-of-accounts rows, and the transaction lifecycle
wiring.
**Branch:** `feat/posting-ledger-cash-envelopes` off `dev`.
**Migration head at planning time:** `db239773c2fd` (verified: `flask db heads`).

**Developer decisions folded in (2026-06-28):**
- **D1 -- counter-leg granularity:** PER-CATEGORY ledger accounts now (not the coarse one-Income +
  one-Expense option). Each category becomes its own Income/Expense ledger account; this is the
  Step-5 income-statement end-state and avoids a future repoint.
- **D2 -- envelope posting:** at PARENT-SETTLE, post the sum of the envelope's DEBIT entries (credit
  entries are excluded -- the CC Payback posts those).
- **D3 -- historical backfill:** YES. A raw-SQL migration backfills every historical settled cash
  transaction (and its debit entries), so the reconciliation oracle is production-wide, mirroring
  Step 2.

---

## 1. What this delivers (plain language first)

Today, when you mark an ordinary grocery expense Paid, it is stored as a single
`budget.transactions` row on Checking, and its direction (money out) is implied by its
`transaction_type_id` (Expense). It is **single-entry**: one row, one account. There is no record
of *where* the $50 went, only that Checking dropped by $50. Balances are read from these rows
through the `balance_at` seam.

This step adds, **alongside** the existing tables, a balanced double-entry journal entry every time
an ordinary (non-transfer) transaction crosses into a settled status (Paid / Received). A $50
grocery expense writes one journal entry with two postings: `Checking -50.00` (credit: money out)
and `Groceries -- Expense +50.00` (debit: the expense lands somewhere). They sum to zero. The second
leg needs an account to land in, so this step creates a real **chart of expense and income
accounts**, one per budget category (Decision D1).

Envelope transactions (those that track individual purchases) are posted at the moment the **parent
settles** (Decision D2): we post the sum of their **debit** purchase entries as the checking
outflow, and exclude credit-card purchases -- those flow through the separate CC Payback, which is
itself an ordinary expense that posts when *it* settles.

**The one formula that drives everything.** A settled transaction's confirmed cash effect is, for
both plain and envelope rows:

> **effect = `effective_amount` − Σ(credit entry amounts)**, signed `+` for income / `−` for expense.

- A **plain** transaction has no entries, so `effect = effective_amount`
  (`transaction.py:225`).
- An **envelope** transaction at settle has `actual_amount = sum(all entries)`
  (`transaction_service.py:155` -> `compute_actual_from_entries`, `entry_service.py:591-609`), and
  `effective_amount = actual_amount`, so `effect = sum(all entries) − sum(credit entries) =
  sum(debit entries)` -- exactly the debit-only outflow Decision D2 asks for, with no branch on
  "is this an envelope."

This single definition is used identically by the go-forward service, the historical backfill SQL,
and the reconciliation oracle, so all three agree by construction.

**This step does NOT:**
- change how any screen reads a balance (every read stays on the `balance_at` seam over
  `budget.transactions`; the ledger remains a parallel, oracle-checked record);
- post transfer shadows again (Step 2 already posts those; this step filters them out with
  `transfer_id IS NULL`);
- post projected transactions (Option D posts confirmed facts only -- we emit exactly at the
  `is_settled` crossing);
- post loan-payment principal/interest splits or paycheck legs (Steps 4-5);
- introduce an opening-balance Equity posting (deferred; see Section 10).

### Worked example -- the postings we write

A $50 grocery expense (category "Food: Groceries") on Checking, marked Paid:

| Journal entry (source: transaction, transaction_id=N, scenario=S, period=P, date=2026-07-01, description "Groceries") | ledger account | posting kind | signed amount |
|---|---|---|---|
| leg 1 | Checking (asset, linked) | expense | **-50.00** (credit: money out) |
| leg 2 | Food: Groceries (expense, category) | expense | **+50.00** (debit: the expense) |
| | | **SUM** | **0.00** OK |

A $2,000 paycheck-category income ("Salary") deposited to Checking, marked Received:

| Journal entry (source: transaction) | ledger account | signed amount |
|---|---|---|
| leg 1 | Checking (asset) | **+2000.00** (debit: money in) |
| leg 2 | Salary (income, category) | **-2000.00** (credit: income earned) |
| | **SUM** | **0.00** OK |

A $200 Groceries **envelope** with entries `$60 debit / $50 debit / $40 credit`, parent marked Paid.
`effect = effective(150) − credit_sum(40) = 110`:

| Journal entry (source: transaction) | ledger account | signed amount |
|---|---|---|
| leg 1 | Checking (asset) | **-110.00** (the two debit purchases) |
| leg 2 | Food: Groceries (expense) | **+110.00** |
| | **SUM** | **0.00** OK |

The $40 credit purchase posts nothing here; its $40 CC Payback (a plain expense on the same account,
next period -- `credit_workflow.py:507-520`) posts `-40 / +40` when it settles. Total checking
impact across both = $60 + $50 (debit) + $40 (card payment) = $150 for $150 of spending. No
double-count.

**The sign rule is class-independent.** The cash-account leg is `+effect` for income, `−effect` for
expense (signed by the transaction *type*, never by account class); the category leg is its
negation. This holds whether the cash leg lands on an asset (Checking) or a liability (a direct
expense charged to a Credit Card account: the card leg `−50` correctly increases the credit-normal
liability, the expense leg `+50` increases the debit-normal expense). The builder never branches on
account class -- identical to Step 2.

---

## 2. Trust-but-verify findings (every claim cited to live code on `dev`)

The architecture-of-record and the Step-2 plan were written 2026-06; code has moved. I re-read the
load-bearing files directly. What I found:

### 2.1 Transactions have NO single settle chokepoint (unlike transfers)

Transfers funnel every status change through `transfer_service._apply_status_change`. Ordinary
transactions do not: `status_id` is assigned at **six independent sites**, only some of which call
`verify_transition`:

| Site | Location | Settles? | Guard |
|---|---|---|---|
| `_mark_done_regular` (manual branch) | `routes/transactions/mutations.py:605-606` | -> Paid / Received | `verify_transition` |
| `settle_from_entries` | `services/transaction_service.py:151-155` | -> Paid / Received (envelope) | `is_immutable` precondition |
| `_apply_regular_update` (PATCH setattr) | `routes/transactions/mutations.py:303-304` | any (incl. settle / un-settle) | `verify_transition` upstream (`:206`) |
| `cancel_transaction` | `routes/transactions/mutations.py:854` | -> Cancelled | `verify_transition` |
| `mark_as_credit` | `services/credit_workflow.py:325` | -> Credit (source) | `is_projected` precondition |
| `unmark_credit` | `services/credit_workflow.py:437` | -> Projected | `verify_transition` |

`settle_from_entries` is the **only shared mutating helper**, and it is reached from both the
`mark_done` envelope branch (`mutations.py:589-591`) and carry-forward (`_execute.py:324`). It
mutates exactly `status_id` / `paid_at` / `actual_amount` (`transaction_service.py:151-155`).

**Conclusion:** there is no `_apply_status_change` to hook. We hook each site that can change a
transaction's confirmed effect, and lean on the Step-2 reconcile-to-target design (Section 5) so the
hooks are idempotent and self-healing. This is the single biggest correctness risk in the step and
is the named focus of Commit 6.

### 2.2 The settle boundary is `is_settled`; SETTLED is never assigned to a transaction

`StatusEnum.SETTLED` exists but no route/service assigns it to a regular transaction (grep:
only the state-machine map, predicates, and Jinja globals reference it). For a cash transaction,
"crosses `is_settled`" means **Paid or Received** only. Per-status flags (seeded at
`app/ref_seeds.py`, Status block): `is_settled` TRUE for Paid / Received / Settled; `is_immutable`
TRUE for all but Projected; `excludes_from_balance` TRUE for Credit / Cancelled
(`settled_status_ids()` = {Paid, Received, Settled}, `balance_predicates.py:112`;
`balance_excluded_status_ids()` = {Credit, Cancelled}, `:82`). This matches Step 2's `is_settled`
boundary exactly.

### 2.3 Settled cash transactions DROP OUT of the balance projection

`sum_projected` sums only `is_projected` rows (`balance_calculator.py:513-515`: `if not
is_projected(txn): continue`); a settled transaction contributes nothing. The displayed checking
balance is `anchor + sum(projected only)` (`balance_calculator.py:99-118`), and the anchor is
**overwritten** on true-up with no plug transaction (`anchor_service.py:206-214`). There is no
account column or query anywhere that sums settled transactions to produce a displayed balance (the
only settled-summation sites are the year-end backward walk and spending reports, neither a
checking balance).

**Consequence:** exactly as in Step 2, the oracle reconciles ledger postings **against the source
transaction rows directly** (signed `effect` per Section 1), never against a displayed balance. An
opening-balance Equity posting (to make the ledger's Checking balance equal the anchor) is a Step-5
reporting concern and is deferred (Section 10).

### 2.4 The envelope balance formula and the credit double-count trap

The envelope reservation formula `max(estimated − cleared_debit − sum_credit, uncleared_debit)`
lives in `entry_checking_impact` (`balance_calculator.py:277-291`) and applies **only while the
parent is Projected** (`_entry_aware_amount`, `:382-401`; Gate 1 at `:513-515`). It is a
*projection* device and is NOT what we post. At settle, the parent's `actual_amount =
sum(all entries)` (`compute_actual_from_entries`, `entry_service.py:591-609`), which **includes
credit entries**; that figure's docstring notes "the credit portion is already handled by the CC
Payback in the next period." Posting the full actual would double-count the credit portion against
the payback (`credit_workflow.py:507-520`, payback is an Expense on `source_txn.account_id`). Hence
the `− Σ(credit entries)` term in the Section-1 formula.

### 2.5 Entry mutations on a settled envelope change the effect

`_update_actual_if_paid` (`entry_service.py:61-86`) recomputes `actual_amount` when the parent
`is_done` and has entries; it is called at the end of `create_entry` (`:244`), `update_entry`
(`:305`), and `delete_entry` (`:351`). So adding / editing / deleting an entry on a **Paid** envelope
changes its `effective_amount` (and `is_credit` edits change the credit sum), changing the confirmed
`effect`. These three entry-mutation sites therefore need a posting re-sync when the parent is
settled. `toggle_cleared` does NOT (the cleared/uncleared split does not change the debit sum;
`effect` is invariant under it), so it needs no hook.

### 2.6 Delete and the credit-revert payback delete are unguarded

`delete_transaction` (`mutations.py:438-488`) flips `is_deleted` (template-linked, `:475`) or
hard-deletes (ad-hoc, `:478`) with no `is_settled` guard -- a settled, posted transaction can be
deleted directly. Worse, credit paybacks are hard-deleted in several places
(`delete_payback_on_credit_revert` from `unmark_credit`, `credit_workflow.py:164-168`;
`delete_payback_on_source_delete` from the delete route; the DELETE branch of
`entry_credit_workflow.sync_entry_payback`), and a payback can be settled+posted before it is
deleted (mark the payback Paid, then `unmark_credit` the source). Any delete of a settled, posted
transaction must **reverse its postings first**, or the ledger keeps a posting whose source row is
gone and reconciliation breaks. This is the transaction analog of Step 2's reverse-before-delete and
the `account_has_ledger_postings` guard.

### 2.7 The Step-2 machinery Step 3 reuses verbatim

- `_emit_balanced_entry(entry, legs)` (`posting_service.py:311`) -- the shared balanced-write path,
  explicitly built "so later Build-Order steps add cash, loan, and paycheck sources by calling the
  same private balanced-write path." Reused unchanged.
- `sync_transfer_postings(xfer, *, settled)` (`:370`) -- the reconcile-to-target idempotent delta
  poster; `sync_transaction_postings` mirrors it (Section 5).
- The append-only ledger tables, the deferred `ck_account_postings_balanced` trigger
  (`posting_infrastructure.py`), and the immutability guards -- all reused; **no new table, no new
  trigger** (Step 3 adds only columns).
- `journal_entries.transfer_id` (nullable, SET NULL, partial index): Step 3 adds a parallel
  `transaction_id` the same way (`journal_entry.py:172-180`; migration DDL at
  `db239773c2fd:330-333,340-344`).
- `ledger_accounts` already permits unlinked rows (`account_id` NULL, `name` set) via
  `ck_ledger_accounts_name_present` (`ledger_account.py:101-104`); the model comments
  (`:82-95`) explicitly defer the unlinked natural key "to the step that first writes them" -- this
  step. **No Income/Expense/Equity ledger account exists yet** (grep: zero
  `LedgerAccountClassEnum.INCOME|EXPENSE|EQUITY` references in `app/`); this step is their first
  producer.
- `LedgerAccountClassEnum` already carries all five classes incl. Income/Expense (with correct
  `is_debit_normal`: Income FALSE, Expense TRUE -- `enums.py:203-218`, seeds
  `ref_seeds.py:146-152`). `PostingKindEnum` / `PostingSourceEnum` carry only `transfer`; Step 3
  adds `income` / `expense` kinds and a `transaction` source.

### 2.8 Adversarial pre-implementation review (2026-06-28) -- defects found and folded in

Before any code, an adversarial reviewer attacked this design against live code (1 CRITICAL, 2 HIGH,
1 MEDIUM, 3 LOW). The **effect formula (Section 1) and the credit-double-count logic were CONFIRMED
correct and complete** across every case (plain manual-actual; envelope debit+credit; all-credit
no-op; partial-entry-delete; income-with-entries is impossible -- `create_entry` blocks income,
`entry_service.py:198-201`). The defects -- all in lifecycle / reconcile completeness, the step's
named highest risk -- are folded into the design below:

- **CRITICAL -- revert-and-recategorize strands category postings.** `finalised_edit_rejection`
  (`state_machine.py:280-283`) *allows* a single PATCH to carry `status_id=Projected` +
  `category_id=B` on a Paid row (the lock lifts precisely on revert; the rejection message invites
  it). A reconcile that reverses the counter-leg against the *current* `txn.category_id` reverses
  into the wrong category: it strands `+100` in old category A and phantoms `-100` in new category B,
  silently breaking per-category reconciliation (cash + trial balance still tie, so it is
  category-specific and invisible to those checks). **Fix:** the reconcile reverses against the
  accounts the transaction has ALREADY posted to, read from the ledger by `transaction_id`, never
  recomputed from `txn.category_id` (Section 5.1, "reconcile over touched accounts").
- **HIGH -- a transaction can be CREATED already settled, with no posting.** Both create routes honor
  a caller-supplied `status_id` (`routes/transactions/create.py:137-142,85-92`;
  `TransactionCreateSchema.status_id` / `InlineTransactionCreateSchema.status_id` are unconstrained
  `fields.Integer()`, `schemas/validation/transactions.py:83,120`), so a born-Paid row has nonzero
  effect and zero journal entries -- a completeness violation. A status-*assignment* grep structurally
  cannot see *construction*. **Fix (born-Projected rule -- developer's choice, verified safe
  2026-06-29):** forbid creating a non-Projected transaction at all, rather than post one. A trace
  confirmed nothing depends on born-settled creates -- every service constructor hardcodes Projected
  (`credit_workflow.py:512`, `recurrence_engine.py:189`, `carry_forward_service/_execute.py:442`); no
  script / migration / seed builds transactions; zero create-route POSTs in the suite carry a settled
  status (tests make settled rows via direct ORM or `/mark-done`). The *only* affordance that submits
  a non-Projected status is the full-create form's Status `<select>`
  (`grid/_transaction_full_create.html:49-59`, fed all six statuses by `forms.py:191`), and a
  born-settled row is itself a latent bug (`paid_at` NULL, bypasses `verify_transition`, posts
  nothing, vanishes from `paid_at`-based analytics). So this is a bug closure, not a feature loss --
  the "record an already-paid item" flow is the correct create-Projected-then-mark-done. Implemented
  in Commit 5 (Section 5.2): drop `status_id` from both create schemas, unconditionally assign
  Projected in the routes, and remove the form selector + its dead `statuses` wiring in `forms.py`.
- **HIGH -- `settled=txn.status.is_settled` reads a stale relationship.** After
  `setattr(txn, "status_id", …)` the `lazy="joined"` `txn.status` is not refreshed (the exact trap
  `mark_as_credit` handles with `db.session.expire(txn, ["status"])`, `credit_workflow.py:337`), so a
  revert would compute `settled=True` and fail to reverse. **Fix:** the settle seam resolves
  `settled` from the NEW `status_id` by ID, never the stale relationship (Section 5.2).
- **MEDIUM -- `Transaction` has no `user_id`.** It is scoped via `txn.pay_period.user_id`
  (`mixins.py:39`). The category ledger account's owner and the journal entry's `user_id` must come
  from there, not `account.user_id` / `current_user`. **Fix:** Section 5.1 sources the owner from
  `txn.pay_period.user_id`.
- **LOW x3 -- documented-safe, with notes:** pay-period `reset`/`truncate` (gate on zero settled
  rows; otherwise the `journal_entries.pay_period_id` CASCADE disposes entries+legs outside the ORM
  and the balanced trigger does not fire on DELETE); `recurrence_engine.resolve_conflicts` (no route
  caller today; skips immutable rows); a defensive `transfer_id IS NOT NULL` no-op guard on the
  writer. Each carries a note for whoever relaxes those gates (Section 5.3).

These findings are why the chokepoint decision is **REVERSED** below (3.2): the per-site approach is
leaky by construction, and a single enforced settle seam is the financially-correct foundation.

### 2.8b Second adversarial review (after the seam revision) -- one HIGH + one MEDIUM folded in

A second pass attacked the revised plan and CONFIRMED the CRITICAL and both HIGH fixes are sound
(the reconcile-over-touched-accounts holds for multi-recategorize A->B->C and for a category deleted
mid-lifecycle; born-Projected is complete; settled-by-ID is correct). It found that the *revision
itself* introduced two problems, now fixed in Section 5.2 / Commits 5-6:

- **HIGH -- embedding the post inside the seam is order-fragile.** The seam (as first written) posted
  at the `status_id` assignment, but `_mark_done_regular` applies a manual `actual_amount` AFTER the
  status flip (`mutations.py:605-609`) and the PATCH `setattr` loop applies `category_id` AFTER
  `status_id` (marshmallow field order), so the post would read pre-edit values -- the forward-direction
  twin of the revert CRITICAL. **This is the exact trap the transfer service already documents and
  avoids** by posting at `update_transfer`'s END, not inside `_apply_status_change`
  (`transfer_service.py:628-636`, verified). **Fix:** the seam no longer posts; each handler reconciles
  ONCE, last, after all field mutations (Section 5.2).
- **MEDIUM -- the checker would false-positive on transfer shadows.** `transfer_service` legitimately
  assigns `Transaction.status_id` on a transfer's two shadows (`transfer_service.py:462-463,886`), and
  a name-based checker can't tell those from a real transaction. **Fix:** the checker allowlists
  `transfer_service` alongside `transaction_service` (Section 5.2).
- **LOW x2 (folded into Commits 5-6):** the seam must absorb `mark_as_credit`'s `expire(txn,
  ["status"])`; `settle_from_entries` must pass `_UNSET` (not its own default `None`, which means "use
  now()") so a settle does not clear `paid_at`; and the recursive `delete_payback_on_source_delete`
  (`credit_workflow.py:226`) must reverse postings at EACH level before each `db.session.delete`.

---

## 3. Resolved design decisions

### 3.1 Decided by the developer (2026-06-28) -- Section header D1/D2/D3 above

| Fork | Decision | Consequence |
|---|---|---|
| Counter-leg granularity (D1) | **Per-category** Income/Expense ledger accounts | New `ledger_accounts.category_id`; a lazy get-or-create resolver; a backfill pre-pass. Income-statement-by-category ready for Step 5; no future repoint. |
| Envelope posting (D2) | **Parent-settle, sum of debit entries** | Unified `effect = effective − Σ(credit)` formula; the envelope and plain paths share one code path and one settle hook. |
| Historical backfill (D3) | **Yes, production-wide** | A raw-SQL migration mirroring `db239773c2fd`, with a category-account pre-pass. |

### 3.2 Resolved here, with rationale (please confirm during plan review)

These follow from D1 and are stated firmly rather than re-asked; each is the financially-correct,
no-gold-plating choice. Push back on any.

| Sub-fork | Decision | Why |
|---|---|---|
| **Category-account natural key** | One ledger account per **(user_id, category_id, class_id)**, class ∈ {Income, Expense} derived from the transaction *type* at posting time | A `Category` is type-agnostic (`category.py` -- just `group_name`/`item_name`); the same category *could* be used for both an income and an expense transaction. Double-entry requires one normal-balance side per account, so a mixed category correctly yields two accounts (Income-class and Expense-class). Keying by (category, class) handles the common case (one account) and the edge (two) with no validation. |
| **Creation timing** | **Lazy get-or-create at posting time** (not an eager category-create hook + backfill-all-categories) | Eager creation would make two accounts (Income+Expense) per category, almost all unused; lazy creation makes exactly the accounts that are actually posted to. The historical backfill get-or-creates as it sweeps. New writer lives in `ledger_account_service` (sole writer of `ledger_accounts`), called by `posting_service` -- preserves SRP. |
| **Uncategorized fallback** | Per-user **"Uncategorized Income" / "Uncategorized Expense"** rows (`category_id` NULL, **`is_fallback` True**, `name` set), one per (user, class) | A transaction's `category_id` is nullable and is SET NULL when a category is deleted (`transaction.py:162-164`). Those rows still post; they land in the fallback account. The singleton keys on **`is_fallback`** (not the NULL `category_id` -- see 4.2's H1 fix); the resolver sets `is_fallback=True` when it creates the fallback. |
| **Display name** | **Snapshot** `category.display_name` ("Group: Item") into `name` at creation | A ledger account is a stable identifier; renaming the budgeting category should not rewrite posted history. The `category_id` link still enables live Step-5 grouping while the category exists, and the snapshot survives a category delete (when `category_id` goes NULL). This is also forced by `ck_ledger_accounts_name_present` (an `account_id`-NULL row must carry a name). |
| **`category_id` FK action** | **SET NULL** on category delete | Postings are immutable and reference the ledger account, so the ledger account can never be deleted; the category link is cleared and the `name` snapshot keeps the account identifiable -- the row becomes a coexisting **orphan** (`is_fallback` False; 4.2's H1 fix is what lets it coexist with the fallback instead of colliding). RESTRICT would wrongly forbid deleting a category that has posted history. |
| **Posting kind per leg** | Both legs of an entry carry the **same kind** (`income` or `expense`, by transaction type) | Mirrors Step 2 (both transfer legs are `transfer`); no Step-3 reader differentiates per-leg kind. |
| **Scope of "cash transaction"** | All settled, non-deleted, **non-transfer** (`transfer_id IS NULL`) transactions, on any account | The double-entry is correct for any account class; the oracle reconciles ledger-vs-source for every account; excluding non-cash accounts would leave confirmed rows unposted and add a classification dependency. Reads do not use the ledger yet, so there is no projection double-count in Step 3. Transfer shadows are excluded (Step 2 owns them). |
| **Chokepoint strategy** (REVERSED after the 2.8 review + developer question) | **Build a single status-transition seam** `transaction_service.apply_status_change` that all status changes route through, checker-enforced -- the transfer pattern (`transfer_service._apply_status_change`) applied to transactions. The seam does the status MECHANICS only (verify + assign + `paid_at` + expire); **posting is reconciled once at each handler's end, after all field mutations** (also the transfer pattern -- `update_transfer:628-643`). Born-Projected closes the create dimension; small hooks handle entry mutations and deletes. | The 2.8 review found two completeness HIGHs the per-site approach caused (create-time settle; stale-status read); the 2.8b review showed posting must NOT live inside the seam (order-fragility). The enforced seam makes the *status* dimension impossible to bypass; born-Projected + the entry/delete hooks + the production-wide oracle cover the rest. It pays off for Steps 4-5, which also post on settle. My original "hook each site, no seam" recommendation is withdrawn. |

### 3.3 Deliberate omissions (no gold-plating -- Rule 13; "doing more than D is worse")

- **No opening-balance Equity posting.** Deferred to Step 5 (reporting / balance sheet). The Step-3
  oracle reconciles against source rows, not the anchor, so it is not needed (Section 2.3).
- **No projected postings, ever** (Option D: confirmed facts only).
- **No loan / paycheck legs** (Steps 4-5).
- **No `posting_service`-sole-writer pylint checker.** The deferred DB trigger already forbids an
  unbalanced write; a construction-site checker is optional future hardening, unchanged from Step 2's
  deferral.
- **No new audited table.** Step 3 writes to the existing `journal_entries` / `account_postings`;
  `AUDITED_TABLES` is unchanged (`ledger_accounts` is already audited). A
  `category_id` column add does not change the table's audited status.

---

## 4. The schema (three additive columns; no new table)

All three changes are additive (no drops, no type changes). Every FK has an explicit named `ondelete`;
every new index matches its model `postgresql_where` byte-for-byte (so autogenerate yields an empty
diff). (As-built note: Section 4.2 was revised after an adversarial review found a category-delete
collision in the original design -- the `is_fallback` discriminator below is the fix. Shipped in
Commit 2, migration `bdde62675c9b`.)

### 4.1 `ref` additions (data, not schema)

- `ref.posting_kinds`: add rows **`income`**, **`expense`** (Step 2 seeded only `transfer`).
- `ref.posting_sources`: add row **`transaction`** (Step 2 seeded only `transfer`).
- Enums (`app/enums.py`): `PostingKindEnum.INCOME = "income"`, `EXPENSE = "expense"`;
  `PostingSourceEnum.TRANSACTION = "transaction"`. `ref_cache.posting_kind_id` /
  `posting_source_id` resolve them automatically (the accessors are generic over the member; no
  `ref_cache` change). `ref_cache.init()` raises if any enum member lacks a DB row -- so the
  migration MUST inline-seed these (dual-seed: migration `ON CONFLICT DO NOTHING` + the
  `_REF_TABLE_SEEDS` lists in `ref_seeds.py`).

### 4.2 `budget.ledger_accounts.category_id` + `is_fallback` (the category chart of accounts)

| column | type | notes |
|---|---|---|
| `category_id` | int NULL | FK `budget.categories.id` **SET NULL**. Set on Income/Expense category rows (`account_id` NULL); NULL on linked Asset/Liability rows, on the Uncategorized fallbacks, and on deleted-category orphans. |
| `is_fallback` | bool NOT NULL DEFAULT false | True ONLY on the per-(owner, class) Uncategorized fallback bucket; False on linked, category, and orphan rows. The discriminator that fixes the H1 collision below. |

Indexes / constraints (added; the existing `uq_ledger_accounts_account`,
`ck_ledger_accounts_name_present`, `idx_ledger_accounts_user` are unchanged):
- `uq_ledger_accounts_category` -- partial unique on `(user_id, category_id, class_id)`
  `WHERE category_id IS NOT NULL AND account_id IS NULL` (one ledger account per category per class
  per owner).
- `uq_ledger_accounts_uncategorized` -- partial unique on `(user_id, class_id)` **`WHERE is_fallback`**
  (exactly one Uncategorized-Income and one Uncategorized-Expense per owner).
- `ck_ledger_accounts_account_or_category_null` -- CHECK `account_id IS NULL OR category_id IS NULL`
  (a row links a real account XOR a category, never both).
- `ck_ledger_accounts_fallback_shape` -- CHECK
  `NOT is_fallback OR (account_id IS NULL AND category_id IS NULL)` (`is_fallback` is a true
  discriminator only on the NULL/NULL shape, so it cannot subvert the fallback singleton).

**Why `is_fallback` (the H1 fix -- a correction to the original plan).** The original design keyed the
fallback singleton `WHERE category_id IS NULL AND account_id IS NULL`. That collides with the
`category_id` SET NULL: deleting a budget category that has posted history turns its category ledger
row into a *second* NULL/NULL row, and -- because the SET NULL is part of the category DELETE -- the
whole delete aborts with a unique violation. It is a reachable, ordinary action (the hard-delete path
`routes/categories.py:delete_category` -> `archive_helpers.category_has_usage` checks only templates
and transactions, never ledger accounts). An adversarial review caught this before any rows were
written. The fix (developer-approved): the `is_fallback` discriminator confines the singleton to the
*true* fallback, so a deleted category becomes a freely-coexisting **orphan** (NULL/NULL,
`is_fallback` False) and the delete always succeeds.

The four row kinds partition cleanly and exhaustively (the two CHECKs make it storage-enforced, not
convention): **linked** (`account_id` NOT NULL, `category_id` NULL, `is_fallback` False),
**category** (`account_id` NULL, `category_id` NOT NULL, `is_fallback` False), **fallback** (NULL/NULL,
`is_fallback` True), **orphan** (NULL/NULL, `is_fallback` False). Each *constrained* kind has exactly
one partial unique; orphans carry none and coexist freely.

Model: add `category_id` + `is_fallback` columns + a one-directional `category` relationship (no
backref, lazy select); display rule extends to `COALESCE(account.name, ledger_account.name)` unchanged
(category / fallback / orphan rows carry `name`, so they resolve to the snapshot). `class_id` already
exists.

### 4.3 `budget.journal_entries.transaction_id` (the source link, mirroring `transfer_id`)

| column | type | notes |
|---|---|---|
| `transaction_id` | int NULL | FK `budget.transactions.id` **SET NULL** -- the immutable posted fact survives a source-transaction delete. Mirrors `transfer_id` exactly. |

Index: `idx_journal_entries_transaction` partial on `(transaction_id) WHERE transaction_id IS NOT
NULL` (lifecycle lookups: "what has this transaction posted?", the per-transaction reconcile-to-target
filter). Model: add the column + nullable FK + partial index (verbatim shape of the `transfer_id`
block, `journal_entry.py:103-107,172-180`). `source_kind_id` distinguishes the two (a `transaction`
entry has `transaction_id` set and `transfer_id` NULL, and vice-versa).

---

## 5. The posting lifecycle: a single settle seam + a correct-by-construction reconcile

Two mechanisms together make the ledger self-correct: (1) a `sync_transaction_postings` that
reconciles over the accounts a transaction has *already* posted to (so it is correct no matter when,
how, or how often it is called), and (2) a single status-transition seam every status change routes
through, enforced by a checker (so a posting can never be skipped or misfired by a forgotten site).
This is the transfer pattern (`transfer_service._apply_status_change`) applied to transactions, with
the reconcile generalized to handle a moving counter-leg (the 2.8 CRITICAL).

### 5.1 The service (`posting_service.sync_transaction_postings`, correct-by-construction)

```python
def sync_transaction_postings(txn, *, settled):
    """Reconcile a transaction's posted ledger effect to its target, idempotently.

    Targets (debit-positive), where
        cash_leg = (effective_amount - sum(credit entry amounts)) * (+1 income / -1 expense):
        settled -> {cash_ledger: cash_leg, current_category_ledger: -cash_leg}
        else    -> {}                       (everything reverses to zero)

    Reconciles over the UNION of the target accounts and the accounts this
    transaction has ALREADY posted to (read from the ledger by transaction_id),
    emitting ONE balanced delta entry for every account whose (target - posted)
    net is non-zero, or None when all deltas are zero.  Reading the posted side
    from the LEDGER -- never recomputing it from txn.category_id -- is what makes
    a revert-and-recategorize reverse the OLD category correctly (2.8 CRITICAL).
    """
```

Mechanics:
- Owner / scenario / period come from `txn.pay_period.user_id` (Transaction has **no** `user_id`,
  `mixins.py:39`), `txn.scenario_id`, `txn.pay_period_id`.
- `cash_ledger = _ledger_account_for(txn.account_id)` (existing helper). When `settled`,
  `category_ledger = ledger_account_service.get_or_create_category_ledger_account(owner_id,
  txn.category_id, ledger_class)`, `ledger_class` = Income if `txn.is_income` else Expense (by ID).
- `cash_leg = (txn.effective_amount − _credit_entry_sum(txn)) * (+1 income / −1 expense)`.
  `_credit_entry_sum` sums `e.amount for e in txn.entries if e.is_credit`.
- `target = {cash_ledger.id: cash_leg, category_ledger.id: −cash_leg}` when `settled` else `{}`.
- `posted = {ledger_account_id: SUM(amount)}` for this transaction's legs --
  `SELECT ledger_account_id, SUM(amount) FROM account_postings JOIN journal_entries WHERE
  transaction_id = txn.id GROUP BY ledger_account_id` (a new `_posted_net_by_account` keyed on
  `transaction_id`, generalizing the `transfer_id`/single-account `_posted_net` at `:175-204`).
- `deltas = {a: target.get(a, 0) − posted.get(a, 0) for a in target | posted}`; drop zeros. If empty,
  return None (idempotent no-op). Else emit one entry whose legs are the non-zero deltas, via the
  **existing** `_emit_balanced_entry`. The non-zero deltas always **sum to zero** (target sums to
  zero by construction; posted sums to zero because every prior entry balanced), which also forces
  `>= 2` non-zero legs whenever any is emitted -- so the entry is balanced and never single-legged by
  construction.
- Entry header: `source_kind=transaction`, `transaction_id=txn.id`, owner/scenario/period as above,
  `entry_date` = UTC civil date of `txn.paid_at` (fallback period start -- reuse `_utc_civil_date`,
  `:120-140`), `description=txn.name[:200]`, all legs kind = income/expense.
- **Defensive guard:** `txn.transfer_id is not None` -> no-op (a shadow must never be posted here;
  Step 2 owns it). Idempotency also rests on the `version_id` optimistic lock (a concurrent double
  mark-done collides on the version, 409). Flushes, does not commit.

Why "reconcile over touched accounts" is correct for every path (worked once): a $100 expense in
category A, Paid, then reverted-and-recategorized to B in one PATCH. Revert (`settled=False`):
`target={}`, `posted={cash:-100, A:+100}` -> deltas `{cash:+100, A:-100}` -> reverses cleanly **into
A** (read from the ledger, not from the now-`B` `category_id`). A later re-settle (`settled=True`):
`target={cash:-100, B:+100}`, `posted={}` (net zero after the revert) -> posts to **B**. A is left at
zero, B carries the expense -- the per-category books are correct. The cash side is immune anyway
(`account_id` is immutable: grep finds no `transaction.account_id =` assignment and no `account_id`
field in `TransactionUpdateSchema`).

New oracle reader `settled_transaction_effect(account_id, scenario_id)` -- the non-transfer analog of
`settled_transfer_effect` (`:500-549`): signed sum over settled, non-deleted, `transfer_id IS NULL`
transactions of `effective − credit_sum`. `account_posting_total` (`:461`) is reused unchanged (it
sums *all* legs on a ledger account: transfer + transaction).

### 5.2 The settle seam (the single source of truth for a transaction's settled state)

A new `transaction_service.apply_status_change(txn, new_status_id, *, paid_at=_UNSET) -> None`
mirrors `transfer_service._apply_status_change` and is the ONLY place a transaction's `status_id`
changes. It does the status MECHANICS and **nothing else** -- crucially, **it does NOT post**:

1. `verify_transition(txn.status_id, new_status_id, "transaction")`.
2. assign `txn.status_id`; set `paid_at = now()` when entering a settled status and none is set,
   clear to `None` when leaving settled. (This also fixes the existing inconsistency that the inline
   PATCH settle leaves `paid_at` NULL -- the seam stamps it the way `mark_done` and transfers already
   do, `transfer_service.py:465-471`.)
3. `db.session.expire(txn, ["status"])` so the `lazy="joined"` `status` relationship reflects the new
   row for any pre-commit reader (this absorbs the bespoke expire `mark_as_credit` does today,
   `credit_workflow.py:337`).

A new pylint checker (`shekel-transaction-status-bypass`, mirroring the balance-seam `W9906` / loan
`W9905`) forbids a direct `<x>.status_id = …` assignment outside an **allowlist** of
`transaction_service` (the seam) and `transfer_service` (which legitimately mirrors `status_id` onto a
transfer's two shadow `Transaction` rows -- `transfer_service.py:462-463,886` -- and which the checker
cannot tell apart from a real transaction at the AST, since the house checkers match syntactically by
name, not by type inference). So the status SSOT is **mechanical, not conventional**. Every
status-changing path reroutes through the seam: `_mark_done_regular` (both branches),
`settle_from_entries`, `_apply_regular_update` (PATCH -- the `status_id` field is routed to the seam
instead of the bare `setattr`), `cancel_transaction`, `mark_as_credit` / `unmark_credit`.

**Posting is reconciled ONCE at the end of each handler, AFTER all field mutations -- never inside the
seam.** This is the load-bearing correction from the second adversarial review (2.8b), and it copies
the transfer service verbatim. `transfer_service.update_transfer` places its `sync_transfer_postings`
call at the END, after every kwarg is applied, with this exact comment
(`transfer_service.py:628-636`): *"Placed here -- NOT inside `_apply_status_change` -- because
`actual_amount` is applied AFTER `status_id`... it must run once everything is in place or it would
post the pre-edit estimate."* The same hazard bites transactions in two places the seam alone would
get wrong: `_mark_done_regular` applies a manual `actual_amount` AFTER the status flip
(`mutations.py:605-609`), and the PATCH `setattr` loop applies `category_id` AFTER `status_id`
(marshmallow field order is `…estimated_amount, actual_amount, status_id, category_id…`), so a
settle-and-recategorize PATCH would post to the stale category if the post fired at the status flip.
Therefore each handler: (a) calls the seam for the status change, (b) applies all other effect fields
(`actual_amount`, `estimated_amount`, `category_id`), then (c) calls
`posting_service.sync_transaction_postings(txn, settled=txn.status.is_settled)` exactly once as its
final step. The PATCH path gates that final call on a `_POSTING_RELEVANT_FIELDS` set
(`{status_id, estimated_amount, actual_amount, category_id}`, the transaction analog of
`transfer_service._POSTING_RELEVANT_FIELDS`) so a notes-only edit posts nothing. `sync` is idempotent
(reconcile-to-target), so one end-of-handler call is always correct; ordering within the handler no
longer matters because the post is last.

The seam still earns its keep: it makes the status mechanics (transition validation, `paid_at`, the
`status` expire) uniform and checker-enforced, which is what closes the 2.8 stale-status HIGH and the
`paid_at`-on-PATCH inconsistency. The *posting* placement is a per-handler "reconcile last" discipline
-- exactly as transfers do it at `update_transfer`'s end -- backstopped by the production-wide oracle's
completeness check. (The trap I originally fell into was embedding the post in the seam; the transfer
service had already learned not to.)

**Creation is born-Projected (the create-side complement to the checker).** The checker covers status
*changes*; it cannot see `Transaction(**data)` *construction* (a constructor kwarg, not a
`status_id = …` statement). So the SSOT is completed at the create routes: a transaction can only ever
be born Projected, and the only way it becomes settled is through the seam. Verified safe (2.8 HIGH):
drop `status_id` from `TransactionCreateSchema` / `InlineTransactionCreateSchema`
(`schemas/validation/transactions.py:83,120`) so `unknown=EXCLUDE` silently drops a submitted value,
assign Projected unconditionally in `create_transaction` / `create_inline`
(`routes/transactions/create.py:137-141,85-92`), and remove the now-unreachable Status `<select>` in
`grid/_transaction_full_create.html:49-59` plus the dead `statuses = db.session.query(Status).all()`
wiring in `get_full_create` (`forms.py:191,200`). The "record an already-paid item" workflow is
unaffected -- it is the correct create-Projected-then-mark-done (which stamps `paid_at`, validates the
transition, and posts through the seam).

**One seam per domain, not one seam for both (a deliberate DRY/SOLID decision).** The transaction
seam PARALLELS `transfer_service._apply_status_change` but is NOT merged with it. The two share the
primitives that should be shared -- `state_machine.verify_transition` (one validator + one
`_build_transitions(context)` map, already parameterized by `"transfer"`/`"transaction"`) and
`posting_service._emit_balanced_entry` (one balanced-write path) and the reconcile-to-target pattern
-- but keep separate bodies, because the divergent parts (a transfer propagates status/amount/period
to its two shadows and has no `paid_at` column of its own -- it lives on the shadows; a transaction
has neither shadows nor that indirection; the legs and effect differ: from/to vs. cash+category,
transfer-kind vs. income/expense, income-shadow-effective vs. `effective − credit_sum`) would force
`if isinstance(entity, Transfer)` type-switching in a merged function. That is false DRY: it couples
two domains the codebase deliberately keeps apart (`journal_entry.py`'s domain-separation note;
coding-standards rule 13) and gives the merged function two reasons to change (SRP). The cross-domain
single source of truth for *correctness* is the reconciliation oracle, which already spans both
sources; the shared *mechanism* is `_emit_balanced_entry` + `verify_transition`. Two thin
domain-owned seams over shared primitives is the more SOLID factoring than one type-switching seam.

### 5.3 The non-status hooks (effect changes that are not transitions -- enumerable, each tested)

A transaction's confirmed effect changes via its status (the seam, 5.2), via its envelope entries, or
via deletion. Creation is no longer a posting boundary -- born-Projected (5.2) means a new row never
has a confirmed effect to post. The remaining two non-transition boundaries keep small, explicit
hooks:

| Boundary | Hook | `settled` |
|---|---|---|
| **Entry create / update / delete on a settled envelope** (`entry_service.py:244,305,351`, right after `_update_actual_if_paid`) | sync the parent | parent `is_settled` |
| **Every transaction DELETE, before removal** (`delete_transaction` `mutations.py:475,478`; the three payback-delete paths in `credit_workflow.delete_payback_on_credit_revert` `:164-168` / `delete_payback_on_source_delete` / `entry_credit_workflow.sync_entry_payback` DELETE branch) | a shared "reverse before delete" call | False |

Completeness notes:
- `toggle_cleared` is NOT hooked -- the cleared/uncleared split does not change `effective − credit_sum`.
- A soft-deleted-but-Paid row: `effective_amount` already returns 0 for deleted/excluded rows
  (`transaction.py:241-244`), so its target is 0, but the delete hook passes `settled=False`
  explicitly so the reversal fires *before* a hard delete removes the row (load-bearing for hard
  deletes).
- The CC Payback from `mark_as_credit` is Projected (posts nothing until it settles through the
  seam); its *source* is Credit (effect 0). No double-count. A settled payback later deleted via
  `unmark_credit` is reversed first by the delete hook.
- **Documented-safe, deliberately unhooked** (2.8 LOW): pay-period `reset`/`truncate` (gate on zero
  settled rows; otherwise the `journal_entries.pay_period_id` CASCADE disposes entries+legs outside
  the ORM and the balanced trigger does not fire on DELETE) and `recurrence_engine.resolve_conflicts`
  (no route caller; skips immutable rows). Each gets a code comment for whoever relaxes those gates.

---

## 6. The reconciliation oracle (the correctness gate)

A new integration test (`tests/test_integration/test_posting_ledger_cash_reconciliation.py`),
mirroring `test_posting_ledger_reconciliation.py`'s non-tautological pattern (two independent
queries + hand-computed literals + service helpers). Because Step 2 already posts transfer shadows
and Step 3 backfills + posts cash transactions, an account's ledger now accumulates **both** sources;
the oracle reconciles each source and the aggregate.

Invariants (asserted production-wide after the backfill, and re-checked after the wiring commit):

1. **Per linked account (cash side).** For each real account A (linked ledger account LA):
   `SUM(postings on LA, scenario S)` `== settled_transfer_effect(A, S) + settled_transaction_effect(A, S)`.
   The transaction term is the signed sum (income `+`, expense `−`) of `effective − credit_sum` over
   A's settled, non-deleted, `transfer_id IS NULL` transactions -- computed by an independent
   source-table query, not the service helper.
2. **Per counter account (category / fallback / orphan).** For each counter ledger account CA:
   `SUM(postings on CA)` `== −Σ(signed effect)` over **the transactions whose legs posted to CA**,
   identified by the `journal_entries.transaction_id` linkage -- NOT by `category_id` matching. For a
   *live category* account that equals the settled, non-deleted, non-transfer transactions of that
   category and matching class (the negation of the cash legs they produced); for the *fallback* it
   equals the still-`category_id IS NULL` transactions of that class; and for an *orphan* (a deleted
   category's former account, `is_fallback` False) it equals exactly the transactions that posted to
   it while the category was live -- which `category_id` matching could no longer find, since those
   transactions now read `category_id IS NULL` (`transactions.category_id` is itself SET NULL on
   category delete). The transaction_id-linkage formulation reconciles all three uniformly and is the
   reason orphans do not break per-counter reconciliation. (Forward note: this was a `category_id`-match
   formulation in the original plan; the `is_fallback`/orphan design in 4.2 requires the linkage
   formulation -- see the `ledger_account.py` module docstring's "Reconciliation of orphans".)
3. **Per-entry balance.** `SUM(amount) = 0` and `COUNT >= 2` for every `journal_entry_id` (also
   DB-enforced).
4. **Trial balance (global).** `SUM(all account_postings.amount) = 0`.
5. **Per-transaction completeness.** Every settled, non-deleted, non-transfer transaction with a
   nonzero `effect` has at least one journal entry (no silent unposted row), mirroring Step 2's
   per-transfer completeness check.
6. **Multi-scenario isolation** and **owner isolation** (via `journal_entry.user_id`; a category
   ledger account is owner-scoped) -- unchanged shape from Step 2.
7. **Backfill == go-forward.** Clear a transaction's postings, re-run the migration's
   `_backfill_*` helper, and assert identical ledger sums -- catches any divergence between the
   raw-SQL backfill and the Python builder (the same guard Step 2 uses).
8. **Revert-and-recategorize reconciles (2.8 CRITICAL regression lock).** Post a Paid expense in
   category A; in one PATCH revert to Projected + recategorize to B; re-settle. Assert category A nets
   to **zero** and category B carries the expense -- the explicit scenario the per-site approach got
   wrong. Also a born-settled create (2.8 HIGH) posts exactly one entry.

Two adversarial non-vacuity proofs (mirroring Step 2): tamper a settled transaction's
`estimated_amount` via raw SQL and assert the per-account check *fails*; inject an unbalanced leg and
assert the trial balance *catches* it.

A reversed/cancelled/deleted transaction reconciles at zero (original + reversal net to zero; the
source-side query drops it because it is no longer settled-and-active), proving the append-only
correction discipline end to end.

---

## 7. Atomic commits

Eight sequential commits; each independently green (targeted tests + `pylint app/` 10.00 on touched
files) and each gets an **adversarial `code-reviewer` subagent pass on the staged diff before
committing** -- findings fixed before the commit lands. The full suite is the final gate in Commit 8
(run alone -- the shared test DB on :5433 flakes under concurrent load,
`project_test_db_concurrency_flakes`). Each migration is tested up **and** down; new audited-table
work is N/A (no new table), but the two schema migrations carry a `Review:` line because they add FKs
and indexes. Commits 4 (correct-by-construction reconcile) and 5 (the enforced settle seam) are the
two that close the 2.8 review's CRITICAL/HIGH findings and carry the heaviest review focus.

### Commit 1 -- Ref: `income` / `expense` posting kinds + `transaction` source -- DONE (`72bdd77`)

- `app/enums.py`: `PostingKindEnum.INCOME`/`EXPENSE`, `PostingSourceEnum.TRANSACTION`.
- `app/ref_seeds.py`: extend the two lists to `("PostingKind", ["transfer", "income", "expense"])`
  and `("PostingSource", ["transfer", "transaction"])`.
- **Migration** (`down_revision = "db239773c2fd"`): inline `INSERT ... ON CONFLICT (name) DO NOTHING`
  for the three new rows (mirroring `f5037400dc5e`'s seed constants). No table. Downgrade: `DELETE`
  the three rows by name.
- **Tests:** `ref_cache` resolves each new member; a model smoke test; migration up/down +
  `ref_cache.init()` succeeds (an enum/seed mismatch makes `init()` raise loudly).
- **Review focus:** enum `.value` strings match the seeded names exactly; no name-compare introduced.

### Commit 2 -- Schema: `ledger_accounts.category_id` + `is_fallback` + `journal_entries.transaction_id` -- DONE (1b4d785)

**As-built note:** an adversarial review found the original `uq_ledger_accounts_uncategorized`
(`WHERE category_id IS NULL AND account_id IS NULL`) collides with the `category_id` SET NULL on a
category delete (4.2's H1). The implemented schema therefore adds an `is_fallback` discriminator and a
`ck_ledger_accounts_fallback_shape` CHECK beyond the original plan, and keys the singleton
`WHERE is_fallback`. What shipped:

- `app/models/ledger_account.py`: add `category_id` (nullable SET-NULL FK) **and `is_fallback` (bool
  NOT NULL DEFAULT false)**, the two new partial unique indexes (the uncategorized one keyed
  `WHERE is_fallback`), **the two CHECKs `ck_ledger_accounts_account_or_category_null` and
  `ck_ledger_accounts_fallback_shape`**, the `category` relationship; rewrite the module docstring's
  row-kind taxonomy to four kinds (linked / category / fallback / orphan) + the orphan-reconciliation
  forward note.
- `app/models/journal_entry.py`: add `transaction_id` (nullable SET-NULL FK) + partial index,
  verbatim shape of the `transfer_id` block; note the `source_kind_id` disambiguation.
- **Migration `bdde62675c9b`** (additive; `Review:` line): `add_column` all three; `create_index` the
  three new indexes; `create_check_constraint` the two CHECKs (hand-added -- Alembic does not
  autogenerate CHECKs). Downgrade drops them. Verified up/down with an empty autogenerate diff.
- **Tests:** model smoke; the partial uniques reject a duplicate category / fallback row and permit
  the disjoint kinds; **the H1 end-to-end regression (category delete with a same-class fallback
  present does NOT collide); fallback+orphan and multi-orphan coexistence; the `fallback_shape` CHECK
  rejects `is_fallback` on a linked/category row**; SET NULL on category delete leaves an orphan with
  the `name` snapshot intact; SET NULL on transaction delete clears `transaction_id`; migration
  up/down. Rejection tests pin the specific constraint name.
- **Outcome:** full suite 6542, `pylint app/` 10.00, two `code-reviewer` passes clean.

### Commit 3 -- `ledger_account_service`: the category/fallback resolver -- DONE

**As-built note (developer-approved deviations + an adversarial-review fix).** Four refinements to
the design below, each confirmed by the developer or the `code-reviewer` pass:
1. **Signature takes the enum member, not an int, and the `_ledger_class_for_txn_type` helper is
   dropped.** `get_or_create_category_ledger_account(user_id, category_id: int | None,
   ledger_class: LedgerAccountClassEnum)`. The class is part of the resolver's natural key, so passing
   the `LedgerAccountClassEnum` member (INCOME/EXPENSE) is the honest signature; the class ID comes
   from `ref_cache.ledger_account_class_id(ledger_class)` and the fallback label from an explicit
   `_FALLBACK_LEDGER_ACCOUNT_NAMES` map (no int -> "Income"/"Expense" reverse-map). The plan's private
   `_ledger_class_for_txn_type(is_income)` had no caller in Commit 3 (it would be dead code until the
   Commit-4 wiring), so it is dropped; Commit 4's `posting_service` inlines the trivial
   `LedgerAccountClassEnum.INCOME if txn.is_income else LedgerAccountClassEnum.EXPENSE` at its call
   site.
2. **An Income/Expense class guard** raises `ValueError` if `ledger_class` is anything else. No database
   CHECK constrains a category row's `class_id`, so this guard is the sole defense against a malformed
   chart entry (and future-proofs the Steps 4-5 callers). The valid-class set is `frozenset` of the
   fallback-name map's keys, so the two can never drift.
3. **The display `name` snapshot is truncated to the column width** (`_LEDGER_ACCOUNT_NAME_MAX_LEN`,
   read from `LedgerAccount.__table__.columns["name"].type.length` so it cannot drift). A category's
   `display_name` ("Group: Item") concatenates two `String(100)` halves and can reach ~202 chars, but
   `ledger_accounts.name` is `String(100)`; PostgreSQL rejects (does not silently truncate) the
   overflow, which would have 500'd a settle. `name` is display-only, so the clip is lossless for
   logic, and `display_name[:N]` is byte-for-byte equal to the Commit-7 backfill's
   `LEFT(group || ': ' || item, N)` (both count code points; verified on the live DB) -- so
   backfill==go-forward holds. **Commit 7's backfill MUST use the same `LEFT(..., N)` truncation.**
4. **The category snapshot load filters by `user_id`** (the `code-reviewer` MEDIUM): a `Category` is
   user-scoped data, so the load honours the "every user-data query filters by `user_id`" rule and
   matches the sibling idempotency lookup; a foreign `category_id` is treated as not-found. The
   resolver raises an actionable `ValueError` (with the offending id/user) instead of an opaque
   `AttributeError` on `None.display_name` when a category id is stale/foreign.

What shipped:

- `app/services/ledger_account_service.py`:
  `get_or_create_category_ledger_account(user_id, category_id, ledger_class) -> LedgerAccount` --
  idempotent (respects the partial uniques), snapshots the name (`category.display_name` truncated, or
  `"Uncategorized {Income|Expense}"`), leaves `account_id` NULL. Private helpers
  `_find_existing_category_ledger_account` (the kind-keyed idempotency lookup) and
  `_category_display_name` (the user-scoped, truncated snapshot loader). Flushes, does not commit.
  - **`is_fallback` handling (4.2 H1).** When `category_id` is NULL, the resolver creates / looks up
    the fallback with **`is_fallback=True`**, and its idempotency lookup keys on
    `(user_id, class_id) WHERE is_fallback` -- NOT on `category_id IS NULL` (a `category_id IS NULL`
    lookup would also match deleted-category *orphans* and return one of them as the fallback,
    commingling unrelated postings). When `category_id` is NOT NULL, it creates a category row with
    `is_fallback=False`. The resolver NEVER creates an orphan; orphans arise only from a category
    delete's SET NULL (Commit 2's schema), and the resolver must not resurrect or reuse them.
- **Tests:** creates one row per (category, class); dedups on re-call; a category used as both income
  and expense yields two rows (Income-class + Expense-class); NULL category -> the per-(user, class)
  fallback with `is_fallback=True` (singleton enforced); the name snapshot is `"Group: Item"`; the row
  survives a later category delete (becomes a `category_id`-NULL orphan, `is_fallback` still False,
  name intact); **a fallback created when an orphan of the same class already exists is the
  `is_fallback=True` row, not the orphan**; decimals/IDs only.
- **Review focus:** class derived by ID (never name); the fallback lookup keys on `is_fallback` (never
  on `category_id IS NULL`, the H1 trap); the singleton holds under the partial unique; SRP (only this
  service writes `ledger_accounts`); no Flask import.
- **Tests (as-built, 15 added in `tests/test_services/test_ledger_account_service.py`):** category-row
  shape + name snapshot; idempotency; the mixed-category two-class case; the category row surviving a
  category delete as an orphan; the **long-name truncation** regression (a 202-char `display_name`
  clips to 100, non-vacuous -- the un-truncated insert raises `DataError`); fallback creation/naming
  (both classes); the fallback singleton; the **fallback-vs-orphan H1** service-level lock; per-user
  fallback scoping; the class guard (Asset/Liability/Equity rejected); missing-category and
  **foreign-category** (cross-tenant) rejection. Decimals/IDs only.
- **Outcome:** 27 tests in the service suite pass (12 Step-2 + 15 Step-3), `pylint app/` 10.00/10 with
  every `--fail-on` checker, no new migration (head stays `bdde62675c9b`; no template rebuild). One
  `code-reviewer` adversarial pass: 0 Critical / 0 High; 1 MEDIUM (the user-scoped category load) + 2
  LOW (a missing type hint; a redundant test flush/comment) -- all fixed, plus the foreign-category
  regression test added to lock the MEDIUM.

### Commit 4 -- `posting_service.sync_transaction_postings` (correct-by-construction) + helpers -- DONE (`fa79630`)

Pure service, no wiring. This is where the 2.8 CRITICAL is structurally closed.

- `app/services/posting_service.py`: `sync_transaction_postings(txn, *, settled)` reconciling **over
  the union of target and already-posted accounts** read from the ledger by `transaction_id`
  (Section 5.1) -- NOT a single-account delta. A `_signed_cash_leg(txn)` / `_credit_entry_sum(txn)`
  pair; a `_posted_net_by_account(txn_id) -> dict[int, Decimal]` (the multi-account reconcile read);
  `settled_transaction_effect(account_id, scenario_id)` (the oracle reader); the defensive
  `transfer_id` no-op guard. Reuse `_emit_balanced_entry`, `_utc_civil_date`, `_ledger_account_for`
  unchanged.
- **Tests (hand-computed worked examples):** plain $50 expense -> legs `-50 / +50`, sum 0; $2000
  income -> `+2000 / -2000`; the $200 envelope with `60/50 debit + 40 credit` -> `-110 / +110`;
  an all-credit envelope -> no entry (effect 0); idempotent re-sync is a no-op; reverse negates the
  posted amount; **revert -> recategorize -> re-settle posts to the NEW category and zeroes the OLD**
  (the 2.8 CRITICAL, proven at the service layer); the counter leg lands in the right category account
  (and the uncategorized fallback for NULL category); a shadow (`transfer_id` set) is a no-op;
  `scenario`/owner sourced from `txn.pay_period`; a missing cash ledger account fails loudly. Decimals
  from strings throughout.
- **Review focus:** the over-touched-accounts reconcile reverses the *posted* category, not the
  current one; the non-zero deltas always sum to zero (>= 2 legs) so `_emit_balanced_entry` never sees
  a single leg; the `effective − credit_sum` formula and the income/expense sign mapping are
  class-independent and correct; owner from `txn.pay_period.user_id` (no `txn.user_id`); idempotency;
  no Flask import.

### Commit 5 -- The status-mechanics seam + enforcement checker + born-Projected (no posting yet) -- DONE

This closes the 2.8 stale-status HIGH and makes the status SSOT mechanical: settled state is reachable
ONLY through the seam (status changes) and never at birth (creation). It is a behavior-preserving
refactor of working status logic (Rule 10: NO rewrite of the status semantics -- the transitions,
guards, and statuses are unchanged; only the *call path* is centralized), plus two deliberate, verified
fixes (the inline-PATCH `paid_at` stamp; born-Projected). **The seam does NOT post** -- it is pure
status mechanics; ledger emission is Commit 6.

**As-built note (two developer-approved deviations, both `code-reviewer`-confirmed sound).**
1. **The seam lives in a NEW `app/services/status_seam.py` module, not `transaction_service`.** Placing
   `apply_status_change` in `transaction_service` (as the plan below proposed) creates a real import
   cycle -- `transaction_service` -> `entry_service` -> `entry_credit_workflow` -> `credit_workflow` ->
   (the seam) -- that pylint flags as `cyclic-import` (R0401) the moment `credit_workflow` imports the
   seam; a deferred import does NOT hide it (pylint counts function-local imports). The dedicated leaf
   module (it imports only `state_machine`, `balance_predicates`, `db`, and the model) breaks the cycle
   with zero `# pylint: disable`, lets `credit_workflow` import the seam at top level, and is the more
   SOLID placement (a low-level primitive sits below its callers). `transaction_service` re-exports
   `apply_status_change`, so the plan's `transaction_service.apply_status_change` name still resolves.
   The W9907 allowlist is therefore `{app.services.status_seam, app.services.transfer_service}`.
2. **`paid_at=None` means "derive from the new status", not the planned `_UNSET` sentinel.** A trace of
   all six status callers confirmed none ever needs to clear `paid_at` *while settling* (the
   explicit-None-to-clear pattern is transfer-only), so `None` is free to mean "derive" -- stamp now()
   on the first entry into a settled status, preserve an existing stamp on a re-settle, clear on
   leaving settled -- and the seam needs no sentinel. `settle_from_entries` forwards its `paid_at`
   verbatim (its historical `None` -> now() fallback is preserved); carry-forward back-dating passes a
   real datetime.

What shipped: `app/services/status_seam.py` (the seam); `transaction_service.settle_from_entries`, the
manual `mark_done` branch, the inline PATCH (`_apply_regular_update` -- `status_id` routed through the
seam and the old `revert_paid_at` handling removed), `cancel_transaction`, and `mark_as_credit` /
`unmark_credit` all rerouted; `shekel-transaction-status-bypass` (W9907) in
`tools/pylint/shekel_checkers.py` (+ an extracted `_module_in_allowlist` helper shared with W9906)
wired into `.pylintrc`, the per-edit hook, `ci.yml`, `.pre-commit-config.yaml`, and `CLAUDE.md`;
born-Projected (dropped `status_id` from both create schemas, unconditional Projected in both create
routes, removed the full-create Status `<select>` + `get_full_create`'s `statuses` query). The intended
PATCH-settle `paid_at` stamp fix shipped (it previously left a PATCH-settled row's `paid_at` NULL).

- **Tests:** `tests/test_services/test_status_seam.py` (8: paid_at derive/preserve/clear/explicit,
  illegal-transition-no-mutation, status-relationship-fresh-after-expire, no-commit); 10 W9907 checker
  cases in `test_shekel_checkers.py` (flag route + credit_workflow; allow seam + transfer_service +
  every-seam-module + package-submodule; reject same-basename-elsewhere; ignore other-attr +
  filing_status_id + value-side read); 4 route tests (PATCH-settle stamps `paid_at`, born-Projected
  create x2, full-create renders no Status control); the renamed
  `test_mark_done_idempotent_preserves_paid_at` now pins preservation (`==`, was `>=`).
- **Outcome:** full suite 6588, `pylint app/ scripts/` 10.00/10 with every `--fail-on` checker (R0401
  eliminated, no new messages), checker unit tests 72. One `code-reviewer` adversarial pass: 0 Critical
  / 0 High / 0 Medium; 3 LOW/informational, the one actionable (a stale test name) fixed. No migration
  (head unchanged at `db239773c2fd` -> Step-3's prior schema head; C5 adds none).

- `app/services/transaction_service.py`: `apply_status_change(txn, new_status_id, *, paid_at=_UNSET)`
  (Section 5.2) -- `verify_transition` + status assign + `paid_at` set/clear + `expire(txn,
  ["status"])`. No posting.
- Reroute every status site through it: `_mark_done_regular` (both branches),
  `settle_from_entries`, `_apply_regular_update` (PATCH: route the `status_id` field through the seam,
  not the bare `setattr`), `cancel_transaction`, `mark_as_credit` / `unmark_credit`.
- `tools/pylint/shekel_checkers.py` + tests + `.pylintrc` + `scripts/hooks/post-edit-python.sh` +
  `ci.yml` + `.pre-commit-config.yaml`: a new `shekel-transaction-status-bypass` checker (a `W99xx`,
  modeled on `W9906`) flagging any `<x>.status_id = …` assignment outside an **allowlist of
  `transaction_service` AND `transfer_service`** (the latter legitimately mirrors `status_id` onto
  transfer shadows, `transfer_service.py:462-463,886`; a name-based checker cannot tell them apart --
  2.8b MEDIUM). Add it to the `--fail-on` set.
- **Born-Projected create rule** (verified safe 2026-06-29; the create-side complement to the
  checker, Section 5.2): drop `status_id` from `TransactionCreateSchema` /
  `InlineTransactionCreateSchema` (`schemas/validation/transactions.py:83,120`); assign Projected
  unconditionally in `create_transaction` / `create_inline` (`routes/transactions/create.py`); remove
  the Status `<select>` from `grid/_transaction_full_create.html:49-59` and the dead `statuses`
  query/`STATUS_PROJECTED` wiring in `get_full_create` (`forms.py:191,200`).
- **Tests:** every existing `mark_done` / cancel / credit / PATCH-status / carry-forward path stays
  byte-for-byte green (behavior preserved -- and with NO postings yet, since C5 adds none); the seam
  stamps `paid_at` on a PATCH settle (the fixed inconsistency) and clears it on revert; `txn.status`
  is fresh after the seam (the absorbed expire); the checker fires on a planted bypass and passes on
  the seam AND on `transfer_service` (no false positive); a create POST carrying a settled `status_id`
  yields a Projected row; the full-create form renders no Status control.
- **Review focus:** the status semantics are unchanged (diff the transition outcomes); the seam does
  not post; the checker allowlist covers `transfer_service`; `mark_as_credit`'s bespoke guards/idempotency
  are preserved around the seam call; no create path can mint a settled row.

### Commit 6 -- Posting emission: reconcile ONCE at each handler's end + entry/delete hooks -- DONE (`671f49f`)

The ledger starts being written go-forward here. The cardinal rule (2.8b HIGH, copied verbatim from
`transfer_service.update_transfer:628-643`): **`sync_transaction_postings` is the LAST mutation in
every handler, after all effect fields are applied** -- never at the status flip.

- **Status handlers -- reconcile last.** In `_mark_done_regular` (apply the manual `actual_amount`
  first, then post), `_apply_regular_update` (apply all non-status fields, then post, gated on
  `_POSTING_RELEVANT_FIELDS = {status_id, estimated_amount, actual_amount, category_id}`),
  `cancel_transaction`, `settle_from_entries` (set `actual` first -- and pass `_UNSET` to the seam when
  its own `paid_at` is `None`, so a settle does not clear `paid_at`; 2.8b LOW), and the credit
  transitions (a defensive no-op): `sync_transaction_postings(txn, settled=txn.status.is_settled)` as
  the final step.
- **Settled-envelope entry mutations** (`entry_service.py:244,305,351`, right after
  `_update_actual_if_paid`): if the parent `is_settled`, sync it.
- **Reverse-before-delete** (a shared helper called by `delete_transaction` and the payback-delete
  paths, including the RECURSIVE `delete_payback_on_source_delete`, `credit_workflow.py:226`, which must
  reverse at EACH level before each `db.session.delete`; 2.8b LOW): `sync_transaction_postings(txn,
  settled=False)` before the row leaves the table.
- **Tests:** the full `is_settled` truth table for plain AND envelope; **a `mark_done` with a manual
  `actual_amount` posts the ACTUAL, not the estimate** (the 2.8b HIGH, forward direction); **a
  settle-and-recategorize PATCH posts to the NEW category** (2.8b HIGH); revert-and-recategorize
  reconciles (2.8 CRITICAL, route level); entry create/update (incl. `is_credit` flip) / delete on a
  SETTLED envelope re-syncs; `toggle_cleared` does NOT change the ledger; delete (soft + hard, ad-hoc +
  template, and a settled payback via `unmark_credit`, and a recursive payback chain) reverses first;
  the existing `test_transaction*` / `test_entries` / `test_credit*` / `test_carry_forward*` suites
  stay green.
- **Review focus:** the post is the LAST mutation in every handler (so it reads final
  amount/category); the PATCH gate skips notes-only edits; the recursive payback reverse fires per
  level; no path double-posts/double-reverses; reverse-before-delete ordering is correct for hard
  deletes; `toggle_cleared` correctly omitted.

### Commit 7 -- Historical backfill migration (production-wide) -- DONE (`48479ef`)

- **Migration** (`Review:` line; raw SQL, self-contained except the documented
  `apply_posting_infrastructure`-style exception is NOT needed here -- no trigger work). Two passes,
  mirroring `db239773c2fd:_backfill_settled_transfers`:
  - **Pass A -- create category/fallback ledger accounts.** `INSERT INTO budget.ledger_accounts
    (user_id, class_id, category_id, is_fallback, name) SELECT DISTINCT …` over every (user, category,
    class) and (user, NULL, class) appearing in settled, non-deleted, non-transfer transactions, with
    `ON CONFLICT … DO NOTHING` against the matching partial unique. The categorized pass inserts
    `is_fallback=false` (conflict target `uq_ledger_accounts_category`); the **fallback pass inserts
    `is_fallback=true`** (conflict target `uq_ledger_accounts_uncategorized`, which is `WHERE
    is_fallback`) -- the backfill must agree with the go-forward resolver, which sets `is_fallback=True`
    on the fallback (4.2 H1). Class from the transaction `transaction_type` -> Income/Expense by
    ID-resolved-by-name (the documented migration exception); name =
    `category.group_name || ': ' || category.item_name` or `'Uncategorized Income'/'Uncategorized
    Expense'`.
  - **Pass B -- post entries.** For every settled (`status.is_settled`), non-deleted,
    `transfer_id IS NULL` transaction with nonzero `effect`, insert one journal entry (`source_kind =
    transaction`, `transaction_id`, scenario/period/user, `entry_date = COALESCE((paid_at AT TIME
    ZONE 'UTC')::date, pp.start_date)`, `description = LEFT(name, 200)`) + two legs: cash leg
    `signed(effect)` on the linked ledger account, category leg `−signed(effect)` on the resolved
    category/uncategorized account. `effect = COALESCE(actual_amount, estimated_amount) −
    COALESCE((SELECT SUM(amount) FROM budget.transaction_entries e WHERE e.transaction_id = t.id AND
    e.is_credit), 0)`. `signed` = `+` income / `−` expense. Idempotent via `NOT EXISTS` on a prior
    entry for that `transaction_id`. Skip `effect = 0` (zero leg forbidden).
- **Tests:** backfill posts one balanced entry for a historical settled plain expense, income, and
  envelope (with the debit-only effect); excludes soft-deleted / Cancelled / Credit / Projected /
  transfer-shadow rows; creates the right category + uncategorized accounts and reuses them across
  rows; idempotent on re-run; migration up/down (down drops the backfilled cash entries and the
  category accounts they created -- reproducible on re-upgrade).
- **Review focus:** the SQL `effect` equals the Python `_signed_effect` exactly (the oracle's
  backfill==go-forward check is the backstop); the credit-sum subquery; the `transfer_id IS NULL`
  exclusion; `ON CONFLICT` targets match the partial-index predicates; no float (Numeric/Decimal
  only); downgrade correctness (only Step-3 entries removed -- filter `source_kind = transaction`).

### Commit 8 -- Reconciliation oracle, full suite, docs -- DONE (`57b74f3`)

- `tests/test_integration/test_posting_ledger_cash_reconciliation.py`: the Section-6 invariants
  (per-linked-account combining transfer+transaction, per-category, per-entry, trial balance,
  completeness, scenario/owner isolation, backfill==go-forward, two adversarial non-vacuity proofs).
  Reuse `seed_periods_today`, `create_account_of_type`, the `_test_helpers` migration loader; add a
  `clear_postings_for_transaction` helper (mirroring `clear_postings_for_transfer`).
- **Full suite** via `./scripts/test.sh` (run alone) -> expect `<N> passed` at the ~6510+ baseline;
  show the count. `pylint app/ scripts/` -> 10.00 with every `--fail-on` checker.
  `python scripts/build_test_template.py` (rebuilt after the migrations) -- note in the commit.
- **Docs:** flip `level1_level2_scope_and_fitness.md` Build-Order Step 3 to done with a completion
  record; update the `project_posting_ledger_transfers_plan` memory (or add a Step-3 memory).
- **Manual verification** (service-level against the prod-clone dev DB, 2FA disabled -- the Step-2
  pattern): mark a real expense Paid, confirm two balanced postings landing in the right category +
  cash accounts and the oracle; add a credit entry to a settled envelope and confirm the re-sync and
  the payback path; revert and confirm the reversal. Leave the dev DB pristine.
- **Review focus:** the oracle is non-tautological (independent queries, hand-computed literals); no
  fixture leakage between scenarios/owners; the trial balance and per-account checks fail on a
  deliberately-unbalanced seed (proven once).

---

## 8. Testing strategy (by layer)

- **Reference (C1):** enum<->seed parity for the new kinds/source; `ref_cache` resolution.
- **Schema (C2):** the three partial indexes + two CHECKs partition the four kinds
  (linked/category/fallback/orphan) correctly; the H1 regression (category delete with a same-class
  fallback present does not collide); SET-NULL behaviors; name-presence; the `fallback_shape` CHECK.
- **Chart of accounts (C3):** (category, class) keying incl. the mixed-category two-account case;
  fallback singleton (keyed `is_fallback`); fallback chosen over a same-class orphan; name snapshot;
  survives category delete (becomes an orphan).
- **Service (C4):** the unified `effective − credit_sum` effect with hand-computed worked examples
  (plain income/expense, envelope debit-only, all-credit no-op); idempotent reconcile; reversal;
  the revert-recategorize-resettle reconcile (2.8 CRITICAL); per-transaction isolation; counter-leg
  account routing.
- **Status-mechanics seam + checker + born-Projected (C5):** behavior-preserving reroute (existing
  status suites stay green, with NO postings yet); `paid_at` stamped on PATCH-settle; `status` fresh
  after the seam; the bypass checker fires on a bypass and passes on `transaction_service` AND
  `transfer_service`; a create POST with a settled `status_id` yields a Projected row; the full-create
  form has no Status control.
- **Posting emission (C6):** the post is LAST in every handler -- a `mark_done` with a manual
  `actual_amount` posts the actual not the estimate, and a settle-and-recategorize PATCH posts to the
  new category (both 2.8b HIGH); the full truth table; settled-envelope entry mutation re-syncs;
  `toggle_cleared` no-ops; delete/credit-revert (incl. recursive payback) reverse-before-delete;
  regression-lock the existing transaction/entry/credit/carry-forward suites.
- **Backfill (C7):** correct entries + account creation for historical plain/income/envelope;
  exclusions (deleted/cancelled/credit/projected/transfer); idempotency; up/down.
- **Integration oracle (C8):** per-linked-account (transfer+transaction), per-category, per-entry,
  trial balance, completeness, scenario/owner isolation, backfill==go-forward, adversarial; full
  suite.
- **Migrations:** all three Step-3 migrations up **and** down; rebuild the test template after.

All money assertions use `Decimal` from strings. Tests needing "now" use `seed_periods_today`.

---

## 9. Migrations summary

Three migrations, chained off `db239773c2fd`, each reversible:

1. **Ref rows** (C1): inline-seed `income` / `expense` posting kinds + `transaction` posting source.
   Down: delete the three rows by name.
2. **Schema** (C2, `bdde62675c9b`): add `ledger_accounts.category_id` + `is_fallback` + two partial
   uniques (uncategorized keyed `WHERE is_fallback`) + two CHECKs
   (`account_or_category_null`, `fallback_shape`); add `journal_entries.transaction_id` + partial
   index. Down: drop them. `Review:` line. DONE.
3. **Backfill** (C7): create category + fallback (`is_fallback=true`) ledger accounts, then post one
   balanced entry per historical settled non-transfer transaction. Down: delete Step-3
   (`source_kind = transaction`) journal entries + the category/fallback ledger accounts (reproducible
   on re-upgrade). `Review:` line.

Downgrade caveat (documented in each, matching `db239773c2fd`): a downgrade removes the backfilled +
go-forward Step-3 postings and the category accounts; a re-upgrade re-runs the backfill and
regenerates the historical postings + category accounts identically. (The schema migration's
downgrade must run after the backfill's, since the backfill rows depend on `category_id`.)

---

## 10. Out of scope (explicit, with pointers)

- **No opening-balance Equity posting / no read switch.** Balances stay on the `balance_at` seam;
  the ledger's Checking balance does not equal the anchor (it omits pre-anchor history). Tying the
  ledger to an absolute balance is Step 5 (an Equity opening posting + the read switch).
- **No loan / paycheck postings** (Step 4 and later).
- **No category->account *promotion of the budgeting UI*.** This step creates ledger accounts behind
  the scenes; the category management UI is untouched.
- **No projected postings, ever** (Option D).
- **No fix to existing envelope/anchor edge behaviors** (e.g. a settled envelope's uncleared entries,
  or a Paid envelope whose last entry is deleted leaving a stale `actual`). The ledger faithfully
  mirrors whatever the source rows say (`effective − credit_sum`), so the oracle holds; correcting
  the underlying app behavior is out of scope (Rule 6) and would be its own change.

---

## 11. Risks and rollback

- **Highest risk: posting must be the LAST mutation in every handler (C6, the 2.8b HIGH).** If a post
  fires before a manual `actual_amount` or a co-submitted `category_id` is applied, it records the
  pre-edit value -- the trap the transfer service documents at `update_transfer:628-636`. Mitigated by
  copying that exact placement (reconcile last, after all fields), the dedicated tests
  (mark-done-with-actual posts the actual; settle-and-recategorize posts the new category), and the
  oracle, which would catch the resulting per-account/per-category desync.
- **The settle-seam refactor (C5)** -- rerouting five working status paths through one seam could
  subtly change behavior. Mitigated by holding it behavior-preserving (status semantics unchanged;
  the seam adds no posting, so C5 changes no ledger state), the existing status suites as a regression
  lock, and the checker. The one intentional change (PATCH-settle now stamps `paid_at`) is tested.
- **Lifecycle completeness (C5 + C6)** -- a missed effect-changing crossing would desync the ledger.
  Mitigated structurally: the seam + `shekel-transaction-status-bypass` checker make the *status*
  dimension impossible to skip; the entry / delete hooks are a short enumerable list each tested;
  born-Projected removes the create dimension; and the production-wide oracle's per-transaction
  completeness check catches any unposted settled row on real data.
- **The correct-by-construction reconcile (C4)** -- the over-touched-accounts logic is more subtle
  than Step 2's single-account delta. Mitigated by the revert-recategorize regression test (2.8
  CRITICAL), the sum-to-zero-by-construction property, and the oracle.
- **Backfill correctness + category pre-pass (C7)** -- the raw-SQL `effect` must equal the Python
  builder's, and the lazy get-or-create must produce the SAME account both ways. With `is_fallback`
  (4.2 H1), "the same account both ways" now also requires both producers to set `is_fallback=true` on
  the fallback (else the backfill makes an `is_fallback=false` row the resolver's `WHERE is_fallback`
  lookup never finds, yielding two fallback-shaped rows and breaking backfill==go-forward). Mitigated
  by the oracle's backfill==go-forward check, the deterministic key + name snapshot + `is_fallback`
  value in both producers, and the `effect <> 0` / `transfer_id IS NULL` filters.
- **Coexistence double-count fear.** None: reads never sum postings in Step 3.
- **Rollback.** Each commit is independently revertible. Reverting C6/C5 stops go-forward emission
  (C5 also reverts the seam refactor) but leaves the backfilled historical postings (still
  reconciled). Reverting C7's migration removes the Step-3 entries + category accounts. Reverting C2
  removes the columns. Reverting C1 removes the ref rows.

---

## 12. Definition of Done

1. All eight commits landed, each green and each with its `code-reviewer` pass applied.
2. `pylint app/ scripts/` 10.00 with every `--fail-on` checker; zero new messages.
3. Full suite passes (count shown), run alone.
4. All three Step-3 migrations tested upgrade **and** downgrade; `build_test_template.py` rebuilt.
5. The cash reconciliation oracle is green, production-wide, and non-tautological.
6. Docs + memory updated; manual prod-clone verification offered/done.
7. Developer asked before commit/push; `dev -> main` PR opened so CI runs (CI does not run on `dev`
   pushes).

**Outcome (2026-06-29): ALL MET.** Eight commits landed and code-reviewer-passed; `pylint app/
scripts/` 10.00; full suite **6640 passed** (run alone); all three Step-3 migrations carry a tested
`downgrade()` and the template was rebuilt at C7; the oracle is green production-wide and reconciles
on real prod data; docs + memory updated; shipped via PR #49 (+ the deploy-fix PR #50). One
unforeseen item surfaced at deploy and is recorded in the Status header's deploy-fix note (the
`ref_cache` migration-host ordering bug; fixed by `create_app(init_ref_cache=False)`).
