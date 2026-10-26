> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation plan: posting ledger + chart of accounts, piloted on transfers

**Status:** SHIPPED TO PROD (2026-06-28) via PR #48 (merge commit `9780464`; prod digest `8f95fcb6`).
All six commits + the guard fix landed on `feat/posting-ledger-transfers` -> `dev` -> `main`, each
green and `code-reviewer`-clean; the Commit-6 reconciliation oracle
(`tests/test_integration/test_posting_ledger_reconciliation.py`) is in and non-tautological; full
suite **6510 passed**; `pylint app/ scripts/` 10.00; CI green on the PR. The three Step-2 migrations
applied on the prod DB and the prod ledger reconciles on real data (per-entry / per-account /
per-transfer checks all clean). Reads remain on the `balance_at` seam (coexistence). NEXT: Build-Order
Step 3 (post confirmed cash transactions + cleared envelope entries at settle). See the completion
record in `level1_level2_scope_and_fitness.md` (Status section).
**Build-Order Step 2** of the Option D architecture
(`docs/audits/balance_architecture/level1_level2_scope_and_fitness.md`, Decision section).
**Enabled by:** Build-Order Step 1 (the `balance_at` seam, shipped to prod in PR #45) and the
kind-correct grid feature (PR #47). Step 1 produced the per-kind correctness oracle this step
reconciles against.
**Branch:** `feat/posting-ledger-transfers` off `dev`.
**Adversarial review (2026-06-28):** every load-bearing code claim re-verified against live `dev` HEAD;
migration DAG recomputed (84 files, single head `b483e2b8a6d2`, no merges). Three forks resolved with
the developer and folded in below: (D1) `ledger_accounts.name` normalized (NULL for linked rows,
derived from `account.name`); (D2) the balanced trigger fires `AFTER INSERT OR UPDATE`; (D3) historical
settled transfers are backfilled in Step 2, making the reconciliation oracle production-wide.

---

## 1. What this delivers (plain language first)

Today a settled transfer of $100 from Checking to Savings is stored as a parent `budget.transfers`
row plus two "shadow" `budget.transactions` rows (one expense on Checking, one income on Savings).
Balances are read from those transaction rows through the `balance_at` seam.

This step adds a second, **permanent, append-only double-entry ledger** that runs *alongside* the
existing tables. When a transfer **settles** (the user marks it Paid), we additionally write one
**journal entry** with two **postings**: Checking `-100.00` and Savings `+100.00`, which must sum to
zero. A real **chart of accounts** (`budget.ledger_accounts`) gives each posting an account to land
in. We pilot the whole mechanism on transfers because a transfer is *already* two balanced legs --
the simplest possible double-entry event -- so we exercise the posting machinery before any later
step touches cash transactions, loans, paychecks, or the read path.

**This step does NOT:**
- change how any screen reads a balance (every read stays on the `balance_at` seam over
  `budget.transactions`);
- drop, demote, or modify `budget.transfers` or `budget.transactions`;
- write any *projected* postings (Option D materializes confirmed facts only -- the future stays
  live-recompute, so audit-write volume stays low and the stale-cache class never reappears);
- promote categories to accounts or add income/expense legs (those are Steps 3-5).

The correctness gate is a **reconciliation oracle**: for every account, the sum of its posting legs
must equal the net effect of that account's *settled transfer shadows* in `budget.transactions`. The
ledger is a parallel, independently-checkable record of the confirmed-transfer subset -- not yet a
replacement for anything.

### Worked example -- the postings we write

A $100 transfer, Checking (asset) -> Savings (asset), marked Paid:

| Journal entry #N (source kind: transfer, transfer_id=42, scenario=baseline, period=P, date=2026-07-01) | ledger account | posting kind | signed amount |
|---|---|---|---|
| leg 1 | Checking (asset) | transfer | **-100.00** (credit: money out) |
| leg 2 | Savings (asset)  | transfer | **+100.00** (debit: money in) |
| | | **SUM** | **0.00** OK |

A $250 transfer, Checking (asset) -> Credit Card (liability), paying down the card:

| Journal entry #M | ledger account | signed amount | natural-balance effect |
|---|---|---|---|
| leg 1 | Checking (asset)        | **-250.00** (credit: money out) | asset down $250 |
| leg 2 | Credit Card (liability) | **+250.00** (debit: pay down)   | liability down $250 (debit-positive `* -1` for a credit-normal account) |
| | **SUM** | **0.00** OK | |

The sign rule is **class-independent and trivial**: the *from* account leg is `-amount` (a credit:
money leaving), the *to* account leg is `+amount` (a debit: money entering). This is true whether a
leg is an asset or a liability, so the posting builder never branches on account class -- the class
only affects how a *reader* later interprets the accumulated balance (see Section 6).

---

## 2. Trust-but-verify findings (every claim cited to live code on `dev` HEAD `87cfdc5`)

The architecture-of-record and the root-cause docs were written across 2026-05 to 2026-06; code has
moved. I re-read the load-bearing files directly rather than trusting the docs. What I found:

### 2.1 `Settled` is defined but never wired -- the post boundary is the `is_settled` flag

`StatusEnum.SETTLED` exists (`app/enums.py:28`) but **no route or service transitions a transfer or
a transaction into it**: `grep -rnE "StatusEnum\.SETTLED" app/` hits only `jinja_globals.py`,
`app/services/state_machine.py` (building the legal-transition map), and `balance_predicates.py` (a
display frozenset). Settlement flows through `mark_done`, which for a transfer always sets `Done`
(`app/routes/transfers/mutations.py`, `ref_cache.status_id(StatusEnum.DONE)`); the transfer state
machine has no `Received` transition (`Received` is the transaction income path). The real "this
happened"
boundary is the **`Status.is_settled` flag** -- TRUE for Paid, Received, and Settled
(`app/models/ref.py:208`, "The real-world transaction has completed"). Option D explicitly names
`is_settled` as the fact/derivation line (fitness doc Decision section). **Conclusion:** a transfer
becomes a confirmed fact at `Paid` (Done), not at `Settled`. Posting at `Settled` only would write
zero postings (a dead pilot). We post on the `is_settled` crossing.

### 2.2 The transfer settle chokepoint (verified end to end)

`mark_done` (`app/routes/transfers/mutations.py:349`) ->
`transfer_service.update_transfer(status_id=DONE, paid_at=now())`
(`app/services/transfer_service.py:574`) -> `_apply_status_change` (`:484`) ->
`verify_transition(xfer.status_id, new_status_id, "transfer")` (`:525`). This is the **single place
a transfer's status changes**, and the choke point Option D names for the emit-the-journal-entry
hook. `cancel_transfer` routes the same way with `CANCELLED`. The transfer transition map
(`app/services/state_machine.py:161-171`): `projected -> done|cancelled`, `done -> projected|settled`,
`cancelled -> projected`, `settled -> settled`.

### 2.3 Every transfer is created `Projected` -- no create-time posting needed

All four creation paths pass a projected status: `transfer_recurrence.py:103` (`plan.projected_id`),
`mutations.py:291` (ad-hoc), `templates.py:647` (template materialize), and `_build_shadow` copies
`xfer.status_id` (`transfer_service.py:312`). `create_transfer` never goes through
`_apply_status_change`. So the posting hook lives only on the *transition* path; create needs no hook
(I add a defensive note, not a second hook).

### 2.4 Carry-forward never moves a settled transfer

`carry_forward_service/_context.py:92-97` queries `is_projected_clause(Transaction)` only, and
`:168-173` skips rows whose status `is_immutable` (Paid/Received/Credit/Cancelled/Settled). So no
carry-forward path mutates a settled transfer -- no posting concern there.

### 2.5 The `loan_anchor_events` migration is the exact precedent

`migrations/versions/d3d25212504b_*.py` creates a ref table (`ref.loan_anchor_sources`, inline-seeded
so backfill in the same transaction can read the IDs), an append-only `budget` table with FKs +
CHECK + a functional unique dedupe index, a **manually attached audit trigger** (NOT via
`apply_audit_infrastructure` -- that runs earlier in the chain against the current `AUDITED_TABLES`
and would fail a from-scratch replay on a not-yet-created table), an idempotent backfill, and a
reversible downgrade. The module imports **nothing from `app`** ("Self-contained dependency policy")
-- raw SQL only, because migrations run at fragile bootstrap moments. The Step-2 migrations follow
this template exactly.

### 2.6 The append-only / immutability precedent

`app/models/loan_anchor_event.py`: `before_update` / `before_delete` SQLAlchemy event listeners raise
a named `LoanAnchorEventImmutableError`; database CASCADE from `budget.accounts` is the documented
disposal path (runs outside the ORM, so the listeners do not block it); the audit-log trigger
captures the row regardless. Corrections are new rows, never edits. The posting ledger mirrors this:
corrections are **reversing entries**, never edits or deletes.

### 2.7 A stale Phase 0 audit artifact -- mined, not followed; NOT authoritative

A prior artifact (`docs/double_entry_phase0_audit.md`, 39 KB, commit `4d0a5f2`, 2026-05-08) survives
only as a **stale local remote-tracking ref** (`remotes/origin/claude/migrate-double-entry-accounting-CdbAQ`)
-- the branch has been **deleted from the remote** (`git ls-remote origin` does not list it) and the
commit is **809 commits behind `main`**. It is not a current or authoritative branch; this plan does
not depend on it. **It predates Option D** and describes the *rejected maximalist* approach (combination F in
the fitness doc): drop `budget.transfers`, dual-write every path, rewrite the balance calculator's
three variants, cut over reads, remove the legacy tables. Option D deliberately chose the opposite
(materialize the confirmed layer only, keep projections live, never drop the tables, indefinite
coexistence). I **fold in its still-valid codebase findings** and **discard its plan**:

- Keep `budget.transaction_entries` -- it is an envelope/purchase-ledger feature, not double-entry
  legs (audit 2.1). Not touched by this step.
- Keep all 6 statuses and their `is_settled`/`is_immutable`/`excludes_from_balance` flags (audit 2.2).
- Keep `scenario_id` as a denormalized tenancy column on the journal entry (audit 2.5 recommendation).
- The "structural validation trigger" (sum=0, >=2 legs) is the right enforcement shape (audit 2.7 /
  section 6) -- adopted as our deferred balanced-journal trigger.
- The transfer service is the chokepoint whose semantics must be preserved (audit 2.9).

It also confirms doc drift: it counted 55 migrations / 4357 tests; today there are **84 migrations**
(head `b483e2b8a6d2`) and **~6394 tests**, and loans are now partly event-stored via
`loan_anchor_events` (shipped *after* that audit), so its "nothing is stored for loans" finding is
itself stale. None of this changes Step 2 (transfers), but it is why I trust the code over every doc.

### 2.8 The existing chart-of-accounts raw material

`ref.account_type_categories` already groups account types into **Asset / Liability / Retirement /
Investment** (`app/models/ref.py:13`, `AcctCategoryEnum`). A real account's accounting class derives
from this: **Liability** category -> Liability ledger class; everything else (Asset, Retirement,
Investment) -> Asset ledger class. `account_service.create_account` (`app/services/account_service.py:142`,
the single `Account(...)` factory) is the one go-forward account-creation site; the ledger-account
sync hook attaches there.

---

## 3. Resolved design decisions

### 3.1 Decided by the architecture-of-record (implemented, not re-litigated)

| Element | Decision | Source |
|---|---|---|
| Amount representation | One **signed** `Numeric(12,2)` column, debit-positive / credit-negative; `CHECK (amount <> 0)` | Fitness doc Part G ("the one genuinely new thing is a SIGNED amount column") |
| Entry structure | `journal_entries` header + `account_postings` legs; sum-to-zero per entry | Fitness doc Part G; Decision section |
| Immutability | Append-only; ORM `before_update`/`before_delete` guards; corrections = reversing entries | `loan_anchor_event.py` precedent; Decision section |
| Audit | All three new `budget` tables registered in `AUDITED_TABLES` | `.claude/rules/database.md`; `audit_infrastructure.py` |
| Migration style | Self-contained (no `app` imports), inline ref seed, manual audit-trigger attach, reversible | `d3d25212504b` precedent |
| Reads | Unchanged; stay on the `balance_at` seam over `budget.transactions` (coexistence) | Fitness doc Part H |
| Projected postings | None -- confirmed facts only | Fitness doc Decision (D beats E/F) |

The **sign convention must be debit-positive**, not "balance-effect" (always-`+`-increases-this-account).
A balance-effect sign sums to zero for an asset<->asset transfer but **breaks in Step 3**: a paycheck's
cash `+2000` / salary-income `+2000` would sum to `+4000`, not zero, destroying the sum-to-zero
self-check that is Option D's entire reason to prefer double-entry over single-entry
materialization. Debit-positive sums to zero across all five account classes.

### 3.2 Resolved with the developer (2026-06-28)

| Fork | Decision | Why |
|---|---|---|
| **Chart of accounts** | **Full `budget.ledger_accounts` table now** + `ref.ledger_account_classes` (Asset/Liability/Income/Expense/Equity + normal-balance side). Seed one Asset/Liability ledger account per existing account; an account-create sync hook keeps them paired; postings FK to `ledger_accounts`. | Future-proof, matches the step name, no FK repoint in Steps 3-5. Income/Expense/Equity rows arrive with their steps. |
| **Sum-to-zero enforcement** | **Deferred DB constraint trigger** (`SUM(amount)=0` and `COUNT>=2` per entry, validated at commit) **+ `posting_service` as sole writer + tests.** | Matches the house "service + DB backstop" pattern (the transfer shadow-pair unique index backstops the transfer service). The step literally names "the balanced-journal constraint." |
| **Emission wiring** | **Dedicated `app/services/posting_service.py`** as the sole writer, called from the transfer status chokepoint. | SRP; mirrors `transfer_service`'s design; directly reusable by Steps 3-5 (cash, loans, paychecks all call the same service). |
| **Source linkage** | **`ref.posting_sources`** names the kind (transfer / transaction / loan_payment / paycheck); the entry also carries a concrete nullable `transfer_id` FK, `ON DELETE SET NULL`. One concrete FK added per source type as steps land. | Keeps real FK integrity + the confirmed-fact-survives-source-delete property. |

### 3.3 Deliberate omissions (no gold-plating -- Rule 13, fitness doc "doing more than D is worse")

- **No `projected`/`confirmed` flag** on postings. The scope doc's generic Level-2 schema line listed
  one, but Option D posts confirmed facts *only* (we emit exactly at the `is_settled` crossing), so
  the flag would be a constant column that also invites the projected-materialization anti-pattern
  Option D rejects. Omitted; documented.
- **No `category_id` on `ledger_accounts` yet.** Categories become expense accounts in Step 3, and
  that mapping (1:1 vs group-level) is a Step-3 decision; pre-adding the column now would presume it.
  Step 3 adds it (a routine nullable-column migration). Documented.
- **No denormalized `user_id` on `account_postings`.** It is reachable via
  `journal_entry.user_id` (normalization over convenience). Add it later only if a measured query
  need appears.
- **No "posting_service is sole writer" pylint checker in Step 2.** The deferred DB trigger already
  makes an unbalanced write *impossible*; a checker forbidding `JournalEntry(`/`Posting(`
  construction outside the service is a reasonable future hardening (mirroring W9906) but is not named
  in the step and is deferred to avoid gold-plating.

---

## 4. The schema

Three `budget` tables, three `ref` tables. All money `Numeric(12,2)`. Every FK has an explicit named
`ondelete`. Every financial column has a CHECK. Append-only tables use `CreatedAtMixin` (no
`updated_at`).

### 4.1 `ref.ledger_account_classes` (the five accounting classes)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `name` | varchar(12) UNIQUE NOT NULL | Asset, Liability, Income, Expense, Equity |
| `is_debit_normal` | bool NOT NULL | TRUE for Asset/Expense; FALSE for Liability/Income/Equity. A boolean (not a name compare) keeps logic ID/flag-driven. |

Seeded inline by the migration and by `seed_ref_tables.py`. Enum `LedgerAccountClassEnum`; cached via
`ref_cache.ledger_account_class_id(member)` plus a `ledger_class_is_debit_normal(class_id)` meta
accessor (mirroring `acct_type_meta`).

### 4.2 `ref.posting_kinds` (the nature of a leg)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `name` | varchar(20) UNIQUE NOT NULL | Step 2 seeds **`transfer`**. Steps 3-5 INSERT `income`, `expense`, `principal`, `interest`, `contribution`, `tax`, ... via their own migrations (new values are data, never schema). |

Enum `PostingKindEnum` (TRANSFER only for now); `ref_cache.posting_kind_id`.

### 4.3 `ref.posting_sources` (the kind of source event)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `name` | varchar(20) UNIQUE NOT NULL | Step 2 seeds **`transfer`**. Later: `transaction`, `loan_payment`, `paycheck`, `credit_payback`. |

Enum `PostingSourceEnum` (TRANSFER only); `ref_cache.posting_source_id`.

### 4.4 `budget.ledger_accounts` (the chart of accounts)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | int NOT NULL | FK `auth.users.id` **CASCADE** (tenancy) |
| `class_id` | int NOT NULL | FK `ref.ledger_account_classes.id` **RESTRICT** (ref rows are non-removable invariants) |
| `account_id` | int NULL | FK `budget.accounts.id` **CASCADE**. Set for Asset/Liability rows (1:1 with a real account); NULL for Income/Expense/Equity rows (Steps 3-5) |
| `name` | varchar(100) NULL | **NULL for linked rows** (display derives from the live `account.name` via the relationship); NOT NULL only for non-linked Income/Expense/Equity rows (Steps 3-5), where it is the canonical label. Never used for logic (IDs-for-logic); display-only |

Indexes / constraints:
- `uq_ledger_accounts_account` -- **partial unique** on `(account_id) WHERE account_id IS NOT NULL`
  (exactly one ledger account per real account).
- `ck_ledger_accounts_name_present` -- `CHECK (name IS NOT NULL OR account_id IS NOT NULL)` (a linked
  row may omit `name` and derive it from `account.name`; an unlinked row must carry one). Display
  rule: `COALESCE(account.name, ledger_account.name)`.
- `idx_ledger_accounts_user` on `(user_id)` (ownership-filtered queries).
- Audited (`AUDITED_TABLES += ("budget","ledger_accounts")`).

**FK-action rationale (the cascade-imbalance impossibility argument -- review this carefully).**
`account_id` is CASCADE so a freshly-created *empty* account deletes cleanly (its empty ledger
account goes with it). A ledger account that *has postings* can never be reached by an account
delete, because such an account necessarily has settled transfer **shadow transactions**, and
`transactions.account_id` / `transfers.from|to_account_id` are `ON DELETE RESTRICT`
(`transaction.py:140`, `transfer.py:101-107`) -- the delete is refused before any cascade fires. So
CASCADE never orphans a single posting leg. (Accounts with history are *archived*, `is_active=False`,
not deleted.) This argument is load-bearing and is a named review focus for Commit 2/3.

### 4.5 `budget.journal_entries` (the event header)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | int NOT NULL | FK `auth.users.id` **CASCADE** (tenancy) |
| `scenario_id` | int NOT NULL | FK `budget.scenarios.id` **CASCADE** (denormalized tenancy; Phase 0 rec 3) |
| `pay_period_id` | int NOT NULL | FK `budget.pay_periods.id` **CASCADE** (period attribution; matches the source transfer's period -- the join key for period reconciliation) |
| `entry_date` | date NOT NULL | Civil date of the confirmed event (the transfer's `paid_at::date`; falls back to the period start). Not derivable from `pay_period_id` (a period spans 14 days), so non-redundant |
| `source_kind_id` | int NOT NULL | FK `ref.posting_sources.id` **RESTRICT** |
| `transfer_id` | int NULL | FK `budget.transfers.id` **SET NULL** -- the immutable posting survives a source-transfer delete |
| `description` | varchar(200) NOT NULL | Human label, e.g. "Transfer: Checking to Savings" |
| `created_at` | timestamptz NOT NULL default now() | `CreatedAtMixin` |

Indexes / constraints:
- `idx_journal_entries_user_scenario_period` on `(user_id, scenario_id, pay_period_id)` (reconciliation
  and reporting queries).
- `idx_journal_entries_transfer` partial on `(transfer_id) WHERE transfer_id IS NOT NULL` (lifecycle
  lookups: "what has this transfer posted?").
- Append-only: `before_update`/`before_delete` ORM guards raising `JournalEntryImmutableError`.
- Audited.
- No `is_deleted` (append-only; "undo" is a reversing entry). No `updated_at`.

### 4.6 `budget.account_postings` (the legs)

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `journal_entry_id` | int NOT NULL | FK `budget.journal_entries.id` **CASCADE** |
| `ledger_account_id` | int NOT NULL | FK `budget.ledger_accounts.id` **CASCADE** (the documented disposal path, mirroring `loan_anchor_events.account_id`; the impossibility argument in 4.4 keeps it safe) |
| `amount` | Numeric(12,2) NOT NULL | **Signed** (debit-positive / credit-negative); `CHECK (amount <> 0)` -- the one signed money column in the schema |
| `posting_kind_id` | int NOT NULL | FK `ref.posting_kinds.id` **RESTRICT** |
| `created_at` | timestamptz NOT NULL default now() | `CreatedAtMixin` |

Indexes / constraints:
- `idx_account_postings_entry` on `(journal_entry_id)` (the balanced-trigger's per-entry SUM, and
  leg retrieval).
- `idx_account_postings_ledger` on `(ledger_account_id)` (per-account reconciliation SUM).
- `ck_account_postings_amount_nonzero` -- `CHECK (amount <> 0)` (a zero leg is meaningless).
- The **deferred balanced-journal constraint trigger** (4.7).
- Append-only ORM guards raising `PostingImmutableError`.
- Audited.

### 4.7 The balanced-journal constraint trigger (the genuinely-new mechanism)

PostgreSQL cannot express a cross-row CHECK, so the sum-to-zero invariant is a **deferred constraint
trigger** that validates at COMMIT (after all of an entry's legs are inserted), not after the first
leg. The SQL lives in a new shared module `app/posting_infrastructure.py` (modeled exactly on
`app/audit_infrastructure.py`) so the migration, `scripts/init_database.py` (the `create_all` + stamp
path that bypasses migrations), and `tests/conftest.py` stay in lock-step -- the same lesson
`audit_infrastructure` encodes.

```sql
CREATE OR REPLACE FUNCTION budget.assert_journal_entry_balanced()
RETURNS TRIGGER AS $$
DECLARE v_sum NUMERIC(12,2); v_count INTEGER;
BEGIN
    SELECT COALESCE(SUM(amount), 0), COUNT(*)
      INTO v_sum, v_count
      FROM budget.account_postings
     WHERE journal_entry_id = NEW.journal_entry_id;
    IF v_count < 2 THEN
        RAISE EXCEPTION 'journal entry % has % posting(s); >= 2 required',
            NEW.journal_entry_id, v_count;
    END IF;
    IF v_sum <> 0 THEN
        RAISE EXCEPTION 'journal entry % postings sum to %; must be 0',
            NEW.journal_entry_id, v_sum;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER ck_account_postings_balanced
    AFTER INSERT OR UPDATE ON budget.account_postings
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION budget.assert_journal_entry_balanced();
```

Properties (named review foci): it fires `AFTER INSERT OR UPDATE` (so a raw-SQL amount edit that
unbalances an entry is also caught -- no legitimate UPDATE path exists, postings being append-only),
but deliberately **not** on DELETE, so the CASCADE-delete disposal path does not fire it (a
DELETE-firing trigger would see `count < 2` mid-cascade and abort a legitimate disposal; and 4.4
proves a cascade cannot orphan a leg anyway); it validates each
`journal_entry_id` independently, so a reversal entry (its own balanced pair) never interacts with
the original; it coexists with the row-level `audit_<table>` trigger (the audit trigger is immediate,
this one is deferred -- order is irrelevant to correctness). Confirmed-only posting keeps INSERT
volume low, so the doubled-audit-write concern the fitness doc raised for *projected* postings does
not apply.

---

## 5. The posting lifecycle (one idempotent entry point)

All emission goes through one `posting_service` function with a "reconcile to target" design that is
idempotent and handles every transfer lifecycle path through a single code path:

```python
def sync_transfer_postings(xfer: Transfer, *, settled: bool) -> JournalEntry | None:
    """Ensure the transfer's NET posted effect equals (its amount if settled, else 0).

    Reads the current net of this transfer's existing posting legs; if it differs from the
    target, emits ONE new balanced journal entry for the delta. Idempotent: a no-op when
    already at target. The reversal negates exactly what was posted (read from the ledger),
    so an amount edited while Projected and re-settled posts the new amount correctly.
    """
```

Mechanics: the *to* leg target is `+xfer.amount` when `settled` else `0`; the *from* leg is its
negation. The current posted amount is read as the net of this transfer's legs on the to-account
ledger. `delta = target - current`. If `delta != 0`, emit `{to_ledger: +delta, from_ledger: -delta}`
(always balanced by construction). The delta entry's `posting_kind = transfer`, `source_kind =
transfer`, `transfer_id = xfer.id`, and period/scenario/date from the transfer.

Every lifecycle path then becomes one call:

| Transition / action | `settled` arg | Net effect |
|---|---|---|
| `projected -> done` (mark_done) | `True` | current 0 -> post `+amount` |
| `done -> projected` (revert) | `False` | current `+amount` -> reverse `-amount` |
| `done -> settled` (archive) | `True` | already at target -> no-op |
| `projected -> cancelled` | `False` | current 0 -> no-op |
| soft/hard `delete` of a settled transfer | `False` | reverse (then the row is removed; `transfer_id` SET NULL keeps the immutable pair as history) |
| `restore` of a settled, soft-deleted transfer | `True` | re-post (re-sync to the restored status's `is_settled`) |

The `is_immutable` finalised-edit lock (`app/services/state_machine.py::finalised_edit_rejection`,
`app/routes/transfers/mutations.py::_reject_finalised_transfer_edit`) already blocks editing a settled
transfer's amount, so a posted amount cannot drift under the ledger -- the only way to change it is
revert (reverse) -> edit -> re-settle (post anew). This invariant is relied upon and tested.

Two further invariants this design leans on (both verified against the live transition map):
- **The state machine forbids `done -> cancelled`.** To cancel a posted transfer you must first revert
  (`done -> projected`, which reverses to net 0) and then cancel, so no path can strand a posting on a
  cancelled transfer.
- **Idempotency rests on the reconcile-to-target math plus the transfer's `version_id` optimistic
  lock.** A DB dedupe index is impossible (the legitimate entry-count per transfer varies: 1 settled,
  2 reverted, 3 re-settled). A repeated identical sync computes `delta = 0` and no-ops; concurrent
  mutations collide on `version_id` and surface as a 409 in `mark_done`. A double-`mark_done` test
  proves no double-post.

---

## 6. The reconciliation oracle (the correctness gate)

Because reads stay on `budget.transactions`, the ledger is validated against the **settled-transfer
subset** of transactions, not against full projected balances. Since Step 2 backfills historical
settled transfers (Commit 3, see Section 10), this reconciliation is **production-wide**: it holds
over every settled, non-deleted transfer (historical + go-forward), not just the test fixtures. The
invariants, asserted in a new integration test and re-checked after every reroute commit:

1. **Per-account reconciliation.** For each account A (its linked ledger account LA):
   `SUM(account_postings.amount WHERE ledger_account_id = LA.id)`
   `== SUM over A's settled (status.is_settled), non-excluded, non-deleted transfer shadows`
   `   of (+effective_amount if income shadow else -effective_amount)`.
   The right side is exactly a cash account's balance-effect from its settled transfer rows; the left
   is the ledger's accumulated debit-positive sum. They are equal by the Section-1 sign mapping
   (income shadow on A = money in = debit = `+amount`; expense shadow = money out = credit =
   `-amount`).
2. **Per-entry balance.** `SUM(amount) = 0` and `COUNT >= 2` for every `journal_entry_id` (also
   DB-enforced by 4.7).
3. **Trial balance (global).** `SUM(account_postings.amount) = 0` across the entire ledger (follows
   from 2, but asserted directly as a cheap whole-ledger self-check).
4. **Multi-scenario isolation.** Postings in scenario X never reconcile against transactions in
   scenario Y (the `scenario_id` denorm is honored).

A reversed transfer reconciles because original `+` reversal nets to the post-revert transaction
state (a reverted shadow is no longer `is_settled`, so the right side drops it; the left side nets to
zero). This is the append-only correction discipline proven end to end.

---

## 7. Atomic commits

Six sequential commits; each is independently green (targeted tests + `pylint app/` 10.00 on touched
files) and gets an **adversarial `code-reviewer` subagent pass on the staged diff before committing**
-- findings fixed before the commit lands. The full suite is the final gate in Commit 6 (run alone --
the shared test DB on :5433 flakes under concurrent load; see
`project_test_db_concurrency_flakes`). Each migration is tested up **and** down, and destructive
migrations carry the `Review: <name>, <date>` docstring line.

### Commit 1 -- Reference schema: ledger classes, posting kinds, posting sources

**Goal:** the three ref tables exist, seed, and resolve through `ref_cache`. No behavior change.

- `app/models/ref.py`: add `LedgerAccountClass` (name, `is_debit_normal`), `PostingKind` (name),
  `PostingSource` (name).
- `app/enums.py`: add `LedgerAccountClassEnum` (ASSET/LIABILITY/INCOME/EXPENSE/EQUITY),
  `PostingKindEnum` (TRANSFER), `PostingSourceEnum` (TRANSFER).
- `app/ref_cache.py`: add the three specs to `_build_ref_specs`; add accessors
  `ledger_account_class_id`, `posting_kind_id`, `posting_source_id`; load an `is_debit_normal` meta
  map (mirroring `acct_type_meta`) with a `ledger_class_is_debit_normal(class_id)` accessor.
- `app/models/__init__.py`: register the three models.
- `scripts/seed_ref_tables.py`: seed the three tables (Asset/Liability/Income/Expense/Equity with the
  correct `is_debit_normal`; `transfer` kind; `transfer` source).
- **Migration** (`down_revision = "b483e2b8a6d2"`): create the three ref tables, inline-seed Step-2
  values with `ON CONFLICT (name) DO NOTHING`. Ref tables are read-only seed catalogues -> **not
  audited** (matches the `audit_infrastructure` inclusion criteria; only `ref.account_types` is
  audited, because it is multi-tenant). Downgrade drops the three tables.
- **Tests:** `ref_cache` resolves each new member; `is_debit_normal` correct per class
  (Asset/Expense TRUE, others FALSE) with hand-checked assertions; a model smoke test; migration
  up/down + `ref_cache.init()` succeeds (an enum/seed name mismatch makes `init()` raise loudly).
- **Adversarial review focus:** enum `.value` strings exactly match the seeded `name`s (else
  `ref_cache.init` fails at app start); `is_debit_normal` seeding; IDs-for-logic compliance (no name
  comparisons introduced).
- **Gates:** `pylint app/` clean; `./scripts/test.sh tests/test_models/... tests/test_utils/test_ref_cache.py -v` green.

### Commit 2 -- Chart of accounts: `budget.ledger_accounts` + account-create sync + backfill

**Goal:** every account has exactly one paired Asset/Liability ledger account, go-forward and
backfilled. Nothing reads/writes postings yet.

- `app/models/ledger_account.py`: `LedgerAccount` (per 4.4), `UserScopedMixin` + `CreatedAtMixin`;
  relationships to `account`, `ledger_account_class`.
- `app/models/__init__.py`: register it.
- `app/services/ledger_account_service.py`: `create_ledger_account_for_account(account) ->
  LedgerAccount` -- idempotent (respects the partial unique), derives `class_id` from
  `account.account_type.category` (LIABILITY category -> Liability class; else Asset class, via
  `ref_cache` IDs), leaves `name = None` (a linked row derives its display name from `account.name`;
  see 4.4). Flushes, does not commit (service contract).
- `app/services/account_service.py:create_account`: after `db.session.add(account)` + flush, call
  `ledger_account_service.create_ledger_account_for_account(account)`. One added call; existing
  behavior otherwise unchanged.
- `app/audit_infrastructure.py`: `AUDITED_TABLES += ("budget","ledger_accounts")` (alphabetical
  placement). `EXPECTED_TRIGGER_COUNT` auto-bumps.
- **Migration** (destructive? no -- pure additive; still carries a `Review:` line per the audited-table
  convention): create `ledger_accounts`; manually attach `audit_ledger_accounts` (DROP IF EXISTS +
  CREATE, per the `d3d25212504b` precedent, NOT `apply_audit_infrastructure`); **backfill** one
  ledger account per existing `budget.accounts` row (class from the joined `account_type_categories`,
  `name = NULL` since linked rows derive it; idempotent via `WHERE NOT EXISTS` on `account_id`). Downgrade drops the table
  (the trigger drops with it) -- the paired rows are reproducible from `accounts` on re-upgrade.
- **Tests:** the sync hook creates exactly one paired row with the right class (asset account ->
  Asset; mortgage -> Liability); idempotent (no duplicate on re-run); partial unique rejects a second
  ledger account for the same account; backfill covers a pre-existing account and is idempotent;
  CASCADE removes a ledger account when an *empty* account is deleted; a linked row has `name = NULL`
  and display resolves to the live `account.name` (including after a rename); the
  `ck_ledger_accounts_name_present` CHECK rejects a row with neither `name` nor `account_id`;
  migration up/down; trigger count round-trips.
- **Adversarial review focus:** the class-derivation uses category **IDs** not names; the partial
  unique predicate matches the model and migration byte-for-byte; the FK-action impossibility argument
  (4.4) holds; no existing account-creation test regresses (the hook is additive); audited-table list
  stays alphabetical.
- **Gates:** `pylint app/` clean; targeted `test_account_service` + new `test_ledger_account_service`
  + migration test green.

### Commit 3 -- Ledger tables + immutability + balanced trigger + audit

**Goal:** the `journal_entries` and `account_postings` tables exist, are append-only, and the
DB refuses any unbalanced entry. No writer yet (zero rows in normal operation).

- `app/models/journal_entry.py`: `JournalEntry` (4.5) and `Posting` (4.6) with
  `before_update`/`before_delete` listeners raising `JournalEntryImmutableError` /
  `PostingImmutableError` (mirroring `loan_anchor_event.py` verbatim in structure). Relationships:
  `JournalEntry.postings` (ordered, `cascade="all, delete-orphan"`, `passive_deletes=True`),
  `Posting.journal_entry`, `Posting.ledger_account`, `Posting.posting_kind`.
- `app/models/__init__.py`: register both.
- `app/posting_infrastructure.py` (new, modeled on `audit_infrastructure.py`): the balanced-trigger
  function + constraint-trigger SQL (4.7) and `apply_posting_infrastructure(executor)` /
  `remove_posting_infrastructure(executor)`.
- `scripts/init_database.py`: call `apply_posting_infrastructure` after `create_all` (the trigger is
  not a table, so `create_all` does not create it -- same gap `audit_infrastructure` fills).
- `tests/conftest.py`: apply the posting infrastructure where it applies the audit infrastructure, so
  the test DB enforces the trigger.
- `app/audit_infrastructure.py`: `AUDITED_TABLES += ("budget","account_postings"), ("budget","journal_entries")`
  (alphabetical).
- **Migration:** create both tables (DDL hand-checked against the models for an empty autogenerate
  diff); manually attach `audit_journal_entries` + `audit_account_postings`; call
  `apply_posting_infrastructure(op.execute)` for the balanced trigger; then **backfill historical
  settled transfers** (raw SQL, no `app` imports, per the `d3d25212504b` precedent): for every
  `budget.transfers` row with `status.is_settled = TRUE` AND `is_deleted = FALSE`, insert one
  `journal_entries` row + two balanced `account_postings` legs, resolving each side's
  `ledger_account_id` by joining `ledger_accounts` on `account_id`, attributing
  `pay_period_id`/`scenario_id`/`entry_date` (= `paid_at::date`, fallback period start) and
  `transfer_id` from the transfer; idempotent via `WHERE NOT EXISTS` on a prior entry for that
  `transfer_id`. The finalised-edit lock guarantees a settled transfer's amount has not drifted, so
  the current amount is the settled amount. Downgrade: `remove_posting_infrastructure` then drop both
  tables (a re-upgrade re-runs the backfill, regenerating historical postings). Carries `Review:` line.
- **Tests:** ORM update/delete on a `JournalEntry`/`Posting` raises the immutable error; the **balanced
  trigger** via raw SQL -- a single-leg entry is rejected at COMMIT, an unbalanced two-leg entry is
  rejected, a balanced two-leg entry commits, and the **deferred** timing is proven (the first leg's
  INSERT does *not* raise mid-transaction); a raw-SQL UPDATE that unbalances an entry is rejected at
  COMMIT while a balanced raw-SQL UPDATE passes; a CASCADE delete of an entry does *not* fire the
  trigger; the **backfill** posts exactly one balanced entry for a pre-existing settled transfer and
  nothing for soft-deleted / cancelled / reverted historical transfers, and is idempotent on re-run;
  audit trigger count matches `EXPECTED_TRIGGER_COUNT`;
  migration up/down + `apply`/`remove` idempotency (run twice == run once).
- **Adversarial review focus:** deferred-constraint semantics (validates at commit, not after leg 1);
  both `COUNT>=2` and `SUM=0` enforced; the trigger fires on INSERT OR UPDATE but never on the CASCADE-delete path; FK ondelete
  actions match 4.5/4.6 and the 4.4 argument; `posting_infrastructure` SQL is idempotent and matches
  across all three callers; no float anywhere (Numeric only).
- **Gates:** `pylint app/` clean; targeted model + DB-constraint + migration tests green;
  `python scripts/build_test_template.py` after the migration.

### Commit 4 -- `posting_service`: balanced-entry builder + idempotent sync + reconciliation helper

**Goal:** the sole writer can emit balanced entries and reverse them; reconciliation helper available.
Pure service, no wiring.

- `app/services/posting_service.py`:
  - `sync_transfer_postings(xfer, *, settled)` (Section 5): reads current net, emits the delta entry,
    idempotent. Builds the journal entry (source/transfer/period/scenario/date/description) + two
    balanced legs (`from = -delta`, `to = +delta`, kind `transfer`) via the from/to accounts' ledger
    accounts. Flushes, does not commit.
  - a private leg/entry builder so the lifecycle and any future source type share one balanced-write
    path (DRY for Steps 3-5).
  - `account_posting_total(account_id, scenario_id) -> Decimal` and a
    `settled_transfer_effect(account_id, scenario_id) -> Decimal` helper used by the oracle.
- **Tests:** post a $100 Checking->Savings transfer -> legs `-100/+100`, sum 0 (hand-computed
  comment); a $250 asset->liability transfer -> `-250/+250` (hand-computed); idempotent re-sync is a
  no-op; reverse negates the *posted* amount; revert -> edit-amount -> re-settle posts the new amount;
  the per-account helper equals the settled-shadow effect; `scenario=None` and missing ledger account
  fail loudly. Decimals from strings throughout.
- **Adversarial review focus:** the from/to sign mapping (credit-out / debit-in) is class-independent
  and correct; idempotency (no double-post on repeated sync); the reversal reads the actual posted
  amount from the ledger (not the possibly-edited `xfer.amount`); no Flask import; balanced by
  construction even before the DB trigger sees it.
- **Gates:** `pylint app/` clean; `./scripts/test.sh tests/test_services/test_posting_service.py -v` green.

### Commit 5 -- Wire posting emission into the transfer lifecycle

**Goal:** settling/reverting/cancelling/deleting/restoring a transfer keeps the ledger correct.

- `app/services/transfer_service.py`:
  - `_apply_status_change`: after applying the transition, call
    `posting_service.sync_transfer_postings(xfer, settled=new_status.is_settled)` (it already loads
    `new_status` for the `paid_at` logic -- reuse it).
  - `delete_transfer` (soft and hard): before the delete, call `sync_transfer_postings(xfer,
    settled=False)` so a settled transfer's effect is reversed (the immutable pair survives a hard
    delete via `transfer_id` SET NULL).
  - `restore_transfer`: after re-syncing shadows, call `sync_transfer_postings(xfer,
    settled=<restored status>.is_settled)` so a restored-settled transfer re-posts.
- **Tests:** `projected->done` writes one balanced entry (oracle holds); `done->projected` reverses
  (net 0); `done->settled` is a no-op; `projected->cancelled` posts nothing; soft-delete of a settled
  transfer reverses and `restore` re-posts; hard-delete of a settled ad-hoc transfer reverses then the
  entries survive with `transfer_id` NULL; the **existing** `test_transfer_service` /
  `test_transfers` suites stay green (postings are additive -- they change no transfer/shadow field
  and no read path); identity re-submit (double mark_done) does not double-post.
- **Adversarial review focus:** every transition crossing is covered (the `is_settled` truth table);
  no path double-posts or double-reverses; the hard-delete ordering (reverse *before* delete) is
  correct and the SET NULL leaves a coherent net-zero history; commit boundaries unchanged (service
  flushes, route commits); no transfer invariant (1-5) weakened.
- **Gates:** `pylint app/` clean; targeted `test_transfer_service` + `test_transfers` +
  `test_posting_service` green.

### Commit 6 -- Reconciliation oracle, full suite, docs

**Goal:** lock the ledger-vs-transaction reconciliation, prove it on the whole suite, record it.

- `tests/test_integration/test_posting_ledger_reconciliation.py`: the Section-6 invariants --
  per-account reconciliation (asset and liability legs), per-entry sum=0, global trial balance,
  multi-scenario isolation, and a companion-owner case (postings inherit owner via
  `journal_entry.user_id`); each computes the expected value independently of the producer under test
  (non-tautological). The oracle holds over **both** the historical backfilled postings and the
  go-forward emitted postings (it reconciles them identically, catching any divergence between the
  raw-SQL backfill and the `posting_service` Python builder). Reuse `seed_periods_today`,
  `create_hysa_account`, `set_default_grid_account`.
- **Full suite** via `./scripts/test.sh` (run alone) -> expect `<N> passed` at the ~6394+ baseline;
  show the count.
- `pylint app/ scripts/` -> 10.00 with every `--fail-on` checker.
- `python scripts/build_test_template.py` (rebuilt after the migrations) -- note in the commit.
- **Docs:** flip `level1_level2_scope_and_fitness.md` Build-Order Step 2 to in-progress/done with a
  completion record; update `recurring_loan_balance_root_cause.md` status if it references the build
  order; update the `project_balance_at_seam_level1` / new `project_posting_ledger_transfers` memories.
- **Manual verification** (service-level against the prod-clone dev DB, 2FA disabled -- the pattern
  the Level-1 work used): create + mark-done a transfer, confirm two balanced postings and the oracle;
  revert + confirm the reversal; both themes for the (unchanged) screens. Leave the dev DB pristine.
- **Adversarial review focus:** the oracle is non-tautological; no fixture leakage between scenarios;
  the trial-balance check would actually fail on a deliberately-unbalanced seed (prove it once).
- **Gates:** full suite green (count shown); `pylint` 10.00; migrations tested both directions.

---

## 8. Testing strategy (by layer)

- **Reference (Commit 1):** enum<->seed parity; `is_debit_normal`; `ref_cache` resolution.
- **Schema / chart of accounts (Commit 2):** sync-hook pairing + class derivation by ID; partial
  unique; backfill idempotency; CASCADE on empty-account delete.
- **DB invariants (Commit 3):** immutability listeners; the deferred balanced trigger (reject
  single-leg, reject unbalanced, accept balanced, reject unbalanced UPDATE, prove deferral, prove
  cascade-delete does not fire it); the historical backfill (correct entries, exclusions, idempotency);
  audit trigger count; `apply`/`remove` idempotency.
- **Service (Commit 4):** the sign convention with hand-computed worked examples (asset->asset and
  asset->liability); idempotent sync; reversal-of-actual-posted-amount; reconciliation helpers.
- **Lifecycle (Commit 5):** the full `is_settled` truth table across mark-done / revert / cancel /
  soft+hard delete / restore; regression-lock the existing transfer suites.
- **Integration oracle (Commit 6):** per-account / per-entry / trial-balance / multi-scenario /
  companion reconciliation, over both historical-backfilled and go-forward postings (production-wide);
  full suite.
- **Migrations:** every migration up **and** down; trigger-count round-trip; rebuild the test
  template after the schema changes.

All money assertions use `Decimal` from strings. Tests that need "now" use `seed_periods_today` for
current-period determinism.

---

## 9. Migrations summary

Three migrations (one per schema-bearing commit), chained off head `b483e2b8a6d2`, each self-contained
(no `app` imports), each reversible:

1. **Ref tables** (Commit 1): create + inline-seed `ledger_account_classes`, `posting_kinds`,
   `posting_sources`. Down: drop all three.
2. **Chart of accounts** (Commit 2): create `ledger_accounts`, attach its audit trigger, backfill one
   row per existing account. Down: drop the table (trigger drops with it; rows reproducible).
   `Review:` line (new audited table).
3. **Ledger tables + historical backfill** (Commit 3): create `journal_entries` + `account_postings`,
   attach their audit triggers, apply the balanced-journal constraint trigger via
   `apply_posting_infrastructure`, then backfill one balanced journal entry per historical settled,
   non-deleted transfer (raw SQL, idempotent). Down: `remove_posting_infrastructure` + drop both
   tables. `Review:` line.

Downgrade caveat (documented in each migration, matching `loan_anchor_events`): a downgrade drops the
tables, so any posted rows are not preserved across a downgrade; a re-upgrade re-backfills the chart
of accounts **and** re-runs the historical-transfer backfill (so historical postings are regenerated);
only postings emitted go-forward between the upgrade and the downgrade are not preserved.

---

## 10. Out of scope (explicit, with pointers)

- **Historical settled transfers ARE backfilled** (Commit 3, raw-SQL migration; see Sections 7 and 9).
  The backfill set is every transfer with `status.is_settled = TRUE` AND `is_deleted = FALSE`;
  soft-deleted, cancelled, and reverted transfers are correctly excluded (their net posted effect is
  zero). The reconciliation oracle is therefore **production-wide**, not fixture-only -- it asserts the
  invariant over all settled, non-deleted transfers.
- **No read switch.** Balances stay on the `balance_at` seam over `budget.transactions`. Steps 4-5
  switch confirmed reads to postings.
- **No income/expense/equity legs, no category->account promotion.** Steps 3 (cash + envelope) and
  5 (reporting). `ledger_accounts.category_id` is deferred to Step 3.
- **No loan / paycheck postings.** Steps 3-4.
- **No "sole-writer" pylint checker.** The deferred DB trigger already forbids unbalanced writes; a
  construction-site checker is optional future hardening.
- **No projected postings, ever** (Option D: confirmed facts only).

---

## 11. Risks and rollback

- **Highest risk: the deferred balanced trigger (Commit 3)** -- the one genuinely-new DB mechanism.
  Mitigated by raw-SQL tests proving reject/accept/deferral and by the impossibility argument (4.4)
  that a cascade cannot orphan a leg. The trigger is fail-closed: an unbalanced entry aborts the
  transaction.
- **Lifecycle completeness (Commit 5)** -- a missed crossing would leave the ledger out of sync.
  Mitigated by the idempotent "reconcile-to-target" design (a later correct sync self-heals) and the
  full `is_settled` truth-table tests + the reconciliation oracle.
- **Coexistence double-count fear.** None: reads never sum postings in Step 2. The ledger is a
  parallel record; the oracle is the only consumer.
- **Historical backfill correctness (Commit 3).** The raw-SQL backfill duplicates the posting shape
  that `posting_service` builds in Python (unavoidable under migration isolation). Mitigated by the
  production-wide oracle, which reconciles backfilled and go-forward postings identically, and by the
  `is_settled AND NOT is_deleted` filter that excludes net-zero (reverted/soft-deleted) transfers.
- **Rollback.** Each commit is independently revertible. The three migrations downgrade cleanly
  (Commit 3's downgrade drops the ledger tables and the backfilled historical postings with them).
  Commit 3 backfills historical settled transfers; Commit 5 adds go-forward emission. Reverting
  Commit 5 stops go-forward emission but leaves the backfilled historical postings (still reconciled,
  since nothing reads them). Reverting Commit 3's migration removes the ledger tables entirely.

---

## 12. Definition of Done

1. All six commits landed, each green and each with its `code-reviewer` pass applied.
2. `pylint app/ scripts/` 10.00 with every `--fail-on` checker; zero new messages.
3. Full suite passes (count shown), run alone.
4. All three migrations tested upgrade **and** downgrade; trigger count round-trips;
   `build_test_template.py` rebuilt.
5. The reconciliation oracle is green and non-tautological.
6. Docs + memories updated; manual prod-clone verification offered/done.
7. Developer asked before commit/push; `dev -> main` PR opened so CI runs (CI does not run on `dev`
   pushes).
