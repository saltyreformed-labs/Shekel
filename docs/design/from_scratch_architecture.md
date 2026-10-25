# Shekel from scratch: what it should be, what it would cost, and in what order

**STATUS: REVISED 2026-09-01 after three adversarial reviews, which broke a great deal of it.
Section 9 records what they refuted.** Section 3.2 is RULED.
**Section 6's order was ruled and is now WITHDRAWN by its own author** -- the plan gate refused it
and a cheaper order buys the same correctness (Section 6). Nothing here has been built. This
document answers the developer's question of 2026-09-01,
*"how would you design this from scratch?"*, and it exists because `balance:X-f3c-4`'s specification
was measured unbuildable-as-written and the measurement that refuted it turned out to refute more
than the one step.

Its companion is `cash_difference_acceptance_audit.md`, which holds the option space for the
acceptance act alone. **This document supersedes that one's framing**: the audit asked what
`X-f3c-4`'s gate should be; this asks why the app needs the act at all, and finds the answer one
layer down.

**Two rulings have been taken, both 2026-09-01 (developer).**

1. The two competing balance-level records become ONE evidence-ranked relation (Section 3.2).
2. **Section 6's ORDER is approved and goes AHEAD OF EVERYTHING ELSE in `docs/plans/steps.md`** --
   *"I approve adding this work ahead of everything else in steps.md"*. That inverts today's `#5` /
   `#6` and puts the restructure ahead of six ranked steps plus the `recurrence:R5` family.

Everything else below is a proposal, and Section 8 lists what is still open.

---

## 1. What was measured, on which database, on what day

**Every figure here was re-derived in this session. Each decays. Re-derive before relying on one.**

Two databases, both throwaways on the dev postmaster:

- `shekel_xf3c4` -- the scheduled production dump of **2026-09-01 02:06:55**, migrated to
  `31bb08f73e50`.
- `shekel_x4head` / `shekel_x4now` -- a
  **live `pg_dump` of `shekel-prod-db` taken 2026-09-01 22:20**, after the developer's own true-up
  that evening. Production's stamp is `a4c6f1d92b73`; migrating it to this branch's head applies
  **28 Alembic revisions** -- an endpoint that measures nothing anyone will ship; see Section 5.

Bank evidence: five SECU exports in `~/Downloads/checking/` covering **2026-01-02..2026-07-17**,
plus `~/Downloads/Transactions-2026-09-01-ce49076a-….csv` covering **2026-07-01..2026-08-31** and
stating a balance as of 2026-09-01 of `$3,847.00`.

### 1.1 The two exports chain, and the chain is self-verifying

The new file carries no running-balance column, so its daily closings were derived BACKWARD from its
stated as-of balance. It overlaps the older export on **11 days (2026-07-01..07-17)** and the two
agree on **all 11**. Independently, the daily-closing-only export agrees with the line export on all
**112** shared days.

**State the control at its real strength.** Of the 155 days in the measured span the bank *states* a
closing for only **71**; 42 are derived backward from the 2026-09-01 export's own lines and 42 are
carried forward over quiet days. The 11-day overlap is ONE level constraint -- that
`3847.00 - SUM(new-file deltas 07-18..08-31) = 2229.73` -- plus ten confirmations that the two files
agree on lines they share. **So 2026-07-18..2026-08-31 has its AGGREGATE pinned and its per-day
distribution corroborated by nothing.** For `e_a` the exposure is small and an adversarial review
could not exploit it (the only lines between 08-28 and the as-of are five 08-31 postings totalling
`-$269.42`), but "known for every day" was an overstatement.

### 1.2 The identity, closed against the shipped producer

An account's outstanding difference decomposes exactly into the three things that can be wrong:

> `difference = e_a - e_o - e_m`

where `e_a` is the owner's typed balance being wrong, `e_o` the stored opening being wrong, and
`e_m` the app's movement records being wrong. The derivation is
`cash_difference_acceptance_audit.md` Section 3; its two code preconditions were re-verified here
(`cash_ledger.dated_deltas` at `_walk.py:350` and `balance_at._cash_fold`'s `recorded` at
`_cash_fold.py:595-597` are the same expression over the same facts, and no movement may be dated at
or before `opened_on` since `X-f3c-2b-1`).

On the 2026-09-01 02:06 restore, over 2026-03-26..2026-08-28:

| term | what it means | measured |
|---|---|---|
| `e_a` | the owner's typed balance vs the bank's closing that day | **`$0.00`** |
| `e_o` | stored opening equity vs the bank's closing for `opened_on` | **`-$2,493.47`** |
| `e_m` | app movements vs bank movements over the span | **`+$4,270.78`** |

`0 - (-2493.47) - 4270.78 = -1777.31`, which is `balance_at.cash_outstanding_difference`'s own
answer for that database. **The identity closes to the cent against the shipped producer**, and a
hand replay of `opening + SUM(dated_deltas)` reproduced the producer's `$5,893.73` before any of it
was trusted.

**`e_a = $0.00`, and it is ONE DAY'S draw rather than a property.** The governing assertion,
2026-08-28 at `$4,116.42`, IS the bank's own closing for that day, exactly -- but
**5 of the last 13 assertions are non-zero, up to `$645.18`**, and 12 of those 13 sit in the window
whose closings are DERIVED rather than stated. What carries the claim is the aggregate, not the
draw: across all 58 assertions in the bank-covered range
**22 are exact, the mean miss is `$158.05` and the median `$39.20`**. On that basis the owner's
true-up is the most accurate instrument in the system -- and on the newer restore the decomposition
cannot be recomputed at all, because the governing assertion has moved to 2026-09-01, a day no bank
record covers.

**The identity's closure is NOT corroboration of any bank figure.** The bank terms cancel by
construction, so the closure holds against a fabricated bank series just as well -- adversarial
review demonstrated exactly that. What it verifies is the two preconditions and the fold's seed.

### 1.3 Which side the movement error is on

Over 2026-03-27..2026-08-28, comparing the fold's own source facts against the bank's lines:

| | app | bank | app - bank | rows |
|---|---|---|---|---|
| money IN | `$37,232.92` | `$34,918.29` | **`+$2,314.63`** | 28 vs 25 |
| money OUT | `-$32,028.35` | `-$33,984.50` | **`+$1,956.15`** | 178 vs **233** |
| net | `$5,204.57` | `$933.79` | `$4,270.78` | 238 vs 258 |

**Both sides are wrong and they partly cancel.** The app records `$2,314.63` more income than the
bank shows and `$1,956.15` less spending, against **233 bank debit lines for 178 app outflows**. The
`-$1,777.31` outstanding difference is the residue of two large errors, not an economic quantity.

The shape of the app's own facts, decomposed from `walk.source_facts` (net `$5,204.57`, reconciling
with the producer by construction):

| type | kind | facts | net |
|---|---|---|---|
| Expense | purchase inside an envelope | 77 | `-$5,752.40` |
| Expense | envelope's own leg | 40 | `-$794.79` |
| Expense | one-off row | 31 | `-$7,184.32` |
| Expense | recurring template row | 46 | `-$3,332.39` |
| Expense | transfer shadow | 16 | `-$14,964.45` |
| Income | one-off row | 3 | `+$2,304.27` |
| Income | recurring template row | 23 | `+$31,428.65` |
| Income | transfer shadow | 2 | `+$3,500.00` |

*A first version of this table classified on the raw `transactions.is_envelope` COLUMN and
misassigned 37 facts worth `$586.64`, including every Groceries row. The operative predicate is
`Transaction.tracks_purchases`, which defers to the TEMPLATE's flag -- the same error the migration
rule made in Section 4.3. The figures above are the corrected ones.*

**Only 77 of 238 facts are movements with their own date and amount.** Of the other 161,
**116 carry a figure DERIVED from the plan**; 13 are owner-corrected and 32 are computed from
entries. The day half is unqualified:
**every one of the 161 carries `settled_day_basis = entered`** --
*"the app's own record with no bank document behind it"* -- and
**no fact in the span carries an `observed` day.**

### 1.4 What THE FLIP would do

`balance_at._outstanding._books_balance_at` IS `X-f3c-5`'s balance function, shipped early at
`X-f3c-3` precisely so the flip becomes a re-pointing. Evaluated over every day the bank states a
closing for, after the books open (155 days, 2026-03-27..2026-08-28):

| series | mean abs error | median | max | days exactly right |
|---|---|---|---|---|
| today (assertions reset the fold) | `$341.04` | `$79.79` | `$2,572.35` | 37 / 155 |
| **after THE FLIP** (`opening + SUM(postings)`) | **`$2,411.59`** | **`$2,133.22`** | **`$7,564.25`** | **0 / 155** |

**This head-to-head is NOT the load-bearing claim and must not be quoted as one.** It is partly
confounded by the mechanism under test: an assertion resets the fold, so today's series is pinned to
what the owner typed on 58 of those days. Partitioned on that mechanism, every one of the 155 days
falls in one of two classes -- 58 assertion days and 97 days carried forward from one -- because
**all 51 days with recorded movement are also assertion days**. The owner settles rows and types the
balance in the same sitting, every time.

Two claims survive any partition and they are the ones that carry the argument:

1. **`e_m = +$4,270.78` over 2026-03-27..2026-08-28; of the 101 days on which either record shows
   activity, 98 disagree.** *(An earlier draft welded two spans: the "71 of 74" belonged to a
   113-day sub-window ending 2026-07-17, over which `e_m` is `$6,943.99`, not `$4,270.78`.)* This is
   a direct measurement of the movement record against the bank. THE FLIP makes the balance a pure
   function of that record, so the flip cannot be safe until the postings are the bank's. No
   comparison is involved.
2. **Post-flip is right on 0 of 155 days.** It applies no assertion policy at all, so its error is
   not a comparison against the reset and no partition can rescue it. *(An earlier draft added
   "including the days the reset does not touch"; the same paragraph proves that set is EMPTY.)*

And one sentence about the step this began with: **`X-f3c-4` as specified would book `-$1,777.31` on
2026-08-28, the single day `e_a = $0.00` proves the app already correct.**

### 1.5 The import is money-neutral, rehearsed on production

`statement_import.record_statement` writes exactly three tables --
`budget.account_external_identities`, `budget.bank_statement_lines`, `budget.statement_imports` --
and no assertion and no transaction. Grepped across `app/services/statement_import/` and
`app/services/statement_match/`.

Rehearsed on the live production restore at branch head, importing both exports in the order `N-368`
names:

- file 1: **306 lines recorded**, balance `$2,229.73` effective 2026-07-17, evidence `FILE_CHAIN`;
- file 2: 119 lines, **91 recorded** (the 28-line overlap correctly deduplicated), balance
  `$3,847.00` effective 2026-08-31, evidence **`CORROBORATED`** -- the app graded the new file
  against the existing chain and confirmed it, reproducing by itself the hand check of Section 1.1;
- `budget.transactions` unchanged at 1,028; `budget.account_anchor_history` unchanged at 86;
- **balances moved on 0 of 1,431 account-days** (9 accounts x 159 days, `cash_daily_balance_series`
  captured before and after and diffed).

**THE BALANCE SNAPSHOT WAS NEARLY BLIND, AND IT WAS PRESENTED AS THE STRONGEST EVIDENCE.**
`cash_daily_balance_series` applies the assertion RESET, so a change to a movement dated on an
assertion day is cancelled on that same day -- and on account 1,
**54 of 56 movement days are assertion days**. A positive control run against a production restore
injected `+$500.00` into settled row 1629 on 2026-08-14 and the harness reported **`0 of 1431`**:

```text
row 1629 settled_on=2026-08-14  amount 2572.36 -> 3072.36  (THE MUTATION LANDED)
assertions on that same day: 1
ACCOUNT-DAYS WHOSE BALANCE MOVED: 0 of 1431
```

**The reason it cannot fail is the assertion reset -- the exact mechanism Section 1.4 argues to
delete.** What the snapshot rules out is a NEW row or a NEW assertion, never an ALTERED one.

**What actually carries the money-neutrality claim** is the other two controls: `record_statement`'s
only three `session.add` targets are the bank tables, and the row counts were unchanged. Both hold.

**A RESET-FREE instrument catches what this one misses**, verified on the same mutation --
`cash_ledger.dated_deltas` reported `account 1 2026-08-14: 2611.90 -> 3111.90`. Any A/B rehearsal of
the RELEASE must use the reset-free view (`dated_deltas`, or `_books_balance_at`, which is THE
FLIP's own function and applies no reset), never the balance series.

And immediately after the import, with no further act, the app said what it could not say before:

```text
OPENING CORROBORATION: stored=689.16  bank=3182.63  agrees=False
SPAN AGREEMENT: days=159 compared=158 unchecked=1 unimported=1 disagreeing=99 reconciles=False
```

*(The outstanding difference on the live restore reads `-$1,850.98` rather than `-$1,777.31`,
because the developer true-upped on 2026-09-01 and the governing assertion moved. The figure is a
function of the LAST assertion alone -- N-171's row says so, and this is that row being right.)*

---

## 2. The root cause, in one sentence

> **Shekel stores a PLAN and a RECORD OF WHAT HAPPENED in one table, and uses a status column to
> pretend the first turns into the second.**

Every open finding in this arc is a symptom. The assertion RESET exists to hide the gap between the
two. The `anchor_equity` plug is the reset's residue, which is `N-171`. `X-f3c-4` exists to book the
plug. `N-314` exists because two records both claim to say what the balance is. `X-f3c-5` is
dangerous because it deletes the reset without replacing what the reset was hiding.

Section 1.3 is the mechanism, stated concretely: a `$400` grocery envelope is consumed by real
purchase rows carrying their own dates and amounts, and that works -- but a `$150` electric bill is
"settled" by flipping its own status, and **no movement is recorded at all**. The row's *planned*
figure enters the fold as though it were an *observed* movement. 161 of 238 facts in the span are
that.

---

## 3. The design

### 3.1 One movement concept, not two

**Every plan item is satisfied by zero or more MOVEMENTS.** A bill is the one-movement case; an
envelope is the many-movement case. `budget.transaction_entries` becomes the general movement table,
and `is_envelope` stops being a RECORD-side concept: the fold and the settle verb read movements
(`X-bi-4a`); the definition keeps it as the plan item's KIND (**R-BAL85**).

`budget.transaction_entries` is already most of a movement row: `transaction_id` (the plan item it
satisfies), `account_id` (held to the parent's by `fk_transaction_entries_parent_account`),
`amount`, `description`, `purchased_on` (when it happened), `settled_on` (when the money moved),
`user_id`, an optimistic lock and timestamps. *What it lacks against `budget.transactions` is a
category, a type, `reconciled_by_id`, and the settle-day basis pair* -- MEASURED FALSE on two of the
four at `balance:X-bi-2` (ruling **R-BAL35**, 2026-09-15): `reconciled_by_id` and the settle-day
basis pair (`settled_on` + `settled_day_basis_id`) were both on the entry when this sentence was
written, and the other two are its plan row's, read through `transaction_id` rather than copied.

What this buys, structurally rather than aesthetically:

- **the balance folds movements only, never a plan row.** `opening + SUM(movements)` becomes true by
  construction, which is exactly what THE FLIP needs and cannot currently have;
- **a `$400` envelope with `$437.22` of real spending shows a `$37.22` variance** instead of losing
  it. That mechanism, generalised, is what produced the `$1,956.15` of under-recorded spend;
- **"Paid" stops being a state anyone sets.** It becomes a consequence of coverage, so it cannot be
  set wrongly;
- the two settle paths that already sit side by side in one door (`transaction_service/_settle.py`:
  `settle_from_entries` versus the MANUAL branch) collapse to one;
- 80 branch sites go (Section 4).

### 3.2 One evidence-ranked LEVEL relation -- **RULED 2026-09-01**

The owner's true-ups and the bank's statement closings are observations of the same quantity -- an
account's balance on a day -- stored in unrelated places with no rule about which governs. That is
`N-314`.

**They become one append-only relation:** `(account, day, amount, source, evidence)`,
evidence-ranked. `app.enums.StatementBalanceEvidenceEnum` and `statement_import.weaker_of` already
ship and already rank exactly this.

**A level observation never moves a balance.** It produces `discrepancy = observed - computed`. Zero
is the healthy state; non-zero is a reconciliation exception with named remedies (Section 3.3).

This dissolves `N-314` rather than ruling it: there is no "which authority wins" branch to write,
because there is one relation with a declared rank. It also removes the need for `bank_import:X-gh`
to be *a rule* -- what remains there is the *write* (a confirmed import's anchor becomes a level
row), which is mechanical.

The OPENING stays a separate, constitutive fact (`budget.account_openings`, shipped at `X-f3c-2a`
under `R-GX`/`R-HE`). It is the seed the fold starts from, not an observation of it, and merging the
two would undo a shipped ruling for no gain.

### 3.3 A discrepancy is resolved by naming its cause -- never by a plug

Four causes, four doors, and **all four already exist**:

| cause | measured on production | remedy | state |
|---|---|---|---|
| the opening is wrong | `e_o = -$2,493.47` | restate the opening | shipped, `59b485df` (`X-f3c-2b-2a`) |
| movements missing or wrong | `e_m = +$4,270.78` | match, or mint from the bank line | shipped (`statement_match`) |
| the observation is wrong | `e_a = $0.00` | supersede it | shipped, append-only (`X-f3c-2c`) |
| genuinely unidentifiable | what remains | an explicit accepted movement | `mint_uncategorized`, shipped |

The automatic `account_trueup` plug goes away.
**`N-171` is then deleted by construction rather than booked**, which is what `X-f3c-4` was trying
to do the hard way.

**`X-f3c-4` becomes small and honest.** It is the fourth row above, offered where the bank has NOT
read -- because that is the only place unidentified movement can hide -- and refused over reconciled
days, naming the true-up or restatement door instead. That is the audit's Option 1, and it is the
only reading under which the act and its evidence point the same way.

**The acceptance act must NOT get its own writer.** `statement_match/_uncategorized.py`'s
`mint_uncategorized` is already the single door for "the row a bank observation requires and the
books do not hold", with two callers (`_variance.mint` under `R-GD(i)`, and
`_income.record_income_from_line`). Its own docstring names the reason:
*"this package's own root cause is a money rule spelled twice."* `X-f3c-4` is its third caller.
Whether it moves out of the `statement_match` package is a placement question, not a design one.

### 3.4 The bank is the default source of movements

The owner already does not type most purchases; they roll into the true-up. Importing does it for
him and yields **more** information for **less** typing: real posting days, real amounts, merchant
strings, and the bank's own categories. Over the measured span those categories are Shopping (120
lines), Food & Drink (35), Transportation (13), Utilities (11), Services (6), Home (7),
Entertainment (12), Financial Services (29), Income (20).

The true-up then becomes what it should be -- a check that reads *agrees*.

### 3.5 What does NOT change

The grid, the pay-period clock, envelopes as budgets, the two-year projection, the recurrence
engine, the double-entry ledger underneath, and the `Routes -> Services -> Models` boundary. The
planning surface is what Shekel gets right. What changes is that
**ticking a row stops being how reality enters the app**.

---

## 4. What the movement unification would cost

Measured on this branch, 2026-09-01.

### 4.1 Application code

**`is_envelope` is the WRONG PROXY for this refactor's size, and 80 is not an estimate of it.** The
raw counts are exact -- 62 Python sites across 28 modules, 18 in templates -- but
**only 19 of the 62 are executable**; 43 are prose. Actual branches on the flag: **four**. All five
sites in `transaction_service/_settle.py`, called "the heart of the change" below, are COMMENTS; its
real predicate is `if not txn.tracks_purchases:`.

**On the right token** (`tracks_purchases`) there are ~14 executable Python sites in modules the
table below never lists.
**And the layer the design actually rewrites is invisible to both censuses**: `balance_at` and
`cash_ledger` are **17,125 lines with ZERO occurrences of either token**. The union blast radius --
`is_envelope`, `tracks_purchases`, `TransactionEntry`, `settled_amount`, `settled_on`, `status_id`,
`credit_payback_id` -- is **187 files in `app/`, 152 of which never mention `is_envelope`.**

The table below is kept because its counts are exact and its shape is informative,
**not because it sizes the work.**

| area | sites | what they do |
|---|---|---|
| `app/schemas/validation/` (3 files) | 17 | form validation branching on whether a row takes entries |
| `app/models/` (4 files) | 8 | the column, `tracks_purchases`, relationships |
| `app/services/transaction_service/_settle.py` | 5 | the two settle paths -- **the heart of the change** |
| `app/routes/templates/crud.py` | 4 | template create/edit |
| `app/services/carry_forward_service/` (3 files) | 5 | envelope carry-forward |
| `app/services/statement_match/` (4 files) | 5 | candidate/offer shaping |
| `app/services/reconcile_service/` (3 files) | 3 | reconcile panel rows |
| `app/services/recurrence_engine/` (2 files) | 3 | generation |
| `app/services/entry_service/` (2 files) | 3 | the entry doors |
| `app/routes/transactions/`, `app/routes/_form_errors.py` | 5 | gates and mutations |
| `app/services/dashboard_service/_bills.py` | 2 | bill listing |
| `app/ref_cache/_accessors.py` | 1 | |
| templates | 18 | grid row macros, full-edit form, mobile |

### 4.2 Tests

**This is the dominant cost, and it is a feature rather than a problem.**

- **422 `is_envelope` sites across 63 test files**;
- **62 test files reference `TransactionEntry` / `transaction_entries`**;
- the suite is 399 test files, so roughly **one test file in six** touches the concept directly.

Those tests are the reason the change is safe to attempt at all. They are also why it cannot be done
in one commit.

### 4.3 The data migration -- NOT small, and forbidden as a migration

**Two errors, and the document contained the evidence against itself.**

**(a) The predicate was a dead column.** The rule was keyed on `transactions.is_envelope`. The app's
own single source of truth is `Transaction.tracks_purchases`, which
**defers to the TEMPLATE's flag** whenever `template_id` is set -- so every recurring envelope
(Groceries, Gas, Kayla's Spending Money) reads `is_envelope = FALSE` on the row and is an envelope
anyway. Measured on a production restore migrated to `dev`'s head:

| | rows |
|---|---|
| `transactions.is_envelope` (the wrong predicate) | 4 |
| `tracks_purchases` (the right one) | **238** |
| settled | 196 |
| the old rule's migration set | 193 |
| not purchase-tracked, needs one movement | 156 |
| purchase-tracked but holding no entries | 9 |
| **rows genuinely needing a movement** | **165** |
| rows that already have movements | 31 |

The old rule would have **over-minted 30 rows worth `$7,680.86`**, double-counting against entries
that already exist, and under-minted **`$208.15`** -- which is exactly Section 1.3's
*"envelope's own leg"*. The document measured the money its own migration rule drops and never
reconciled the two sections.

**(b) A migration may not do this at all.** Ruling **`balance:R-HJ`** (developer, 2026-08-28): *"**A
DATA REPAIR IS PERFORMED THROUGH THE APP'S OWN DOORS, NEVER BY A MIGRATION WRITING MONEY ROWS.** ...
A question that only exists because you are writing raw SQL is evidence you are writing it in the
wrong place."* Section 4.3 proposed a migration writing 165 money rows the cash fold reads. The
ruling was never cited.

**So the cost is not a migration at all**, and the rows that make that expensive are named: of the
165, **14 carry no `settled_on`** and **38 are transfer shadows** bound by the four Transfer
Invariants. Those are 52 design questions, not a `WHERE` clause -- which is the same conclusion
`R-HJ` reaches from the other direction. Section 4.5's re-cut routes them through the settle door,
where a human answers them.

**And the DOWNGRADE was never addressed**, which `CLAUDE.md`'s Definition of Done item 7 requires.
After the change a plan row may be covered by *N* movements and a movement may have no plan row at
all; the downgrade has one `settled_amount` and one `settled_on` per transaction, so it must sum the
movements (losing every date) and has nowhere to put a parentless one. It is value-lossy the moment
the feature is used. Two revisions already in the release REFUSE their downgrade; this one would
have to do the same, explicitly, rather than silently lose money records.

### 4.4 Honest risks

1. **Transfer shadows are 248 of 926 rows** and carry FIVE critical invariants (`CLAUDE.md`; this
   line said four until 2026-09-09, and a correctness pass on this document's account of them owed
   its own paragraph the same count). A shadow's movement is the parent transfer's, and getting that
   wrong breaks invariants 3 and 4. `transaction_service/_settle.py` already records that an
   adversarial review settled one leg of a pair through exactly this seam. 2.
   **The envelope's own leg.** Three facts in the measured span are an envelope's own delta rather
   than a purchase's (`-$208.15`), and `balance_at._cash_periods._budget_legs` reads a partially
   spent envelope at its whole cost -- the spent part as movements, the rest as a reservation. The
   refactor must preserve that reading exactly or the grid's subtotals move. 3.
   **`credit_payback_id` / `is_credit`** on entries, and the `Credit` status, are a second axis the
   unification has to carry rather than flatten. 4. **`pay_period_id`.** A purchase takes its
   parent's, deliberately. A movement with no plan item (a bank line nothing was planned for) has no
   parent to take it from, and `ReviewScope.period_holding` is the resolver that must answer
   instead.

### 4.5 Decomposition -- RE-CUT, by provenance rather than by row kind

**The first cut was wrong in two ways.** Leaf 1 was not additive: the real column diff between a
movement and a transaction is **21 columns**, not four, and it includes `scenario_id`, which the
fold REFUSES a mismatch on. Leaf 2 -- *"the fold reads movements for one row kind at a time"* -- has
no row-kind axis to switch: the fold's predicate is kind-blind and consumed at 66 sites across 17
modules, so that leaf would have to ERECT a discriminator in the one layer the concept has never
reached, run two balance semantics live on one account, and have leaf 5 delete it again. That is the
defect this arc exists to remove, shipped on purpose.

**The re-cut, taking Section 4.3's measurement seriously:**

1. **`tracks_purchases` DERIVES through one accessor from the template's flag** -- ruled
   `balance:R-JQ` on 2026-09-03, correcting this list's *stored column, template-derived backfill*,
   which was the cache `R-IY` deletes. Green, no fold change -- and it makes Section 4.3's 30-row
   error unwritable in the application (Python, Jinja, the ORM); raw SQL naming the column is what
   remains until leaf 5 deletes it. SHIPPED `18f9efac`, sealing the cell rather than pinning the
   move.
2. ~~**Entries gain the full movement column set, `scenario_id` included**, backfilled from their
   parent. Additive; downgrade is a column drop.~~ **DISSOLVED** as `balance:X-bi-2` by ruling
   **R-BAL35** (2026-09-15): a backfilled copy is the cache **R-IY** deletes, the shape leaf 1 was
   re-specified over, and since **R-BAL20** every entry has a plan row one hop away. The column a
   movement genuinely lacks -- its own figure's provenance -- is leaf 3's, stated with its writer.
   **What was measured for the ruling, 2026-09-15.** The real column diff, from the mappers, is 20
   columns on `budget.transactions` only (not 21) and 6 on the entry only, 9 shared; the sentence
   named three real ones. Both live writers of a parent's scope would have staled a copy silently:
   the popover PATCH re-categorises a row (`TransactionUpdateSchema.category_id`) and the recurrence
   engine rewrites a definition's `category_id` and `transaction_type_id` onto its existing rows on
   every regenerate (`recurrence_engine._amounts.DerivedRowFields`); `scenario_id` and the type have
   no application writer after creation and one scenario exists per user (no clone door), so that
   divergence was `psql`-only. On the dev snapshot (rows to 2026-09-06): 100 entries under 34
   parents, all expense and non-transfer (`create_entry` refuses both), one scenario, 0 orphans, 55
   of 100 authored by a user other than the owner -- the entry's `user_id` is the AUTHOR, so a
   composite key over it can never hold. The option grid put to the developer: (1) plain backfilled
   copies, as written -- R-IY's rejected shape; (2) co-located keys, `scenario_id` and
   `transaction_type_id` each held by `(transaction_id, col) -> transactions(id, col)` with
   `ON UPDATE CASCADE` over a `UNIQUE (id, col)` superkey, `account_id`'s own pattern on this table,
   with `category_id` derived because a nullable column (7b-1) cannot be held by a key -- correct
   only if an entry must stand without its parent, a need 7b-1 deleted; (3) derive all three through
   the parent, as `_posted_purchase_facts` already does. Worked on row 2282 (Groceries, Paid, three
   posted purchases summing `$499.82`): re-categorising the envelope to Household leaves (1)'s three
   copies saying Groceries while the grid says Household, and (2) and (3) agree. Ruled: (3).
3. **`settle_from_entries` becomes the ONLY settle path** -- RULED 2026-09-15 as four leaves
   (**R-BAL39**, **R-BAL40**, **R-BAL41**; `HANDOFF-X-bi-3.md`): the settle writes the movement, the
   existing rows are cut over by a fail-closed MIGRATION by a total rule, R-HJ scoped to repairs. As
   first written: the MANUAL branch writes a single covering movement **through the service door**,
   which is what satisfies `R-HJ`, for the 205 purchase-tracked rows holding no entries and the
   untracked ones. The fold still reads transactions.
   **This is where the 14 dateless and 38 invariant-bound rows get answered by a human at a door**,
   not by a `WHERE` clause.
4. **The fold re-points to movements in ONE commit, for every kind at once** -- because after leaf 3
   every settled row has exactly one covering movement, so `opening + SUM(movements)` is an identity
   provable against the pre-state. No semantics straddle.
5. ~~**Delete `is_envelope`, `tracks_purchases`'s branch sites and the template sites.**~~
   **DISSOLVED** as `balance:X-bi-5` by ruling **R-BAL85** (2026-09-20): rule 14's two homes closed
   at `X-bi-7d` and the settle verb's branch stopped reading the flag at `X-bi-4a`
   (`settles_from_entries` is `bool(purchases)`, **R-BAL78**); what the flag still decides,
   carry-forward's rollover against move-whole, derives from nothing stored (Gas 51/51, Groceries
   51/51 forward rows empty and envelopes; every bill's likewise), so the definition keeps it as the
   plan item's kind; the cumulative envelope is NAMED (not ruled) as the from-scratch end state for
   `bank_import:X-gg`'s owned question.

The costly, irreversible act is leaf 3, and it is a service-door write with a rehearsal. The fold
flip is a re-pointing graded by a before/after equality.

---

## 5. Production, the release, and the import

**The developer's question: when does production need the release and the import?**

**Three different numbers measure three different questions, and a reader must not collapse them.**

| endpoint | revisions | what it answers |
|---|---|---|
| production -> `release/2026-09-01` (cut at `5108de8c`) | **30** | what the release actually ships |
| production -> `origin/dev` today (`f0018d86`) | **31** | how far behind production has drifted |
| production -> this branch's head | 28 | nothing shippable -- a stale sibling of the fork |

**The release number is 30 and merging into `dev` cannot move it**, which is the whole point of
cutting a release BRANCH rather than opening a `dev -> main` pull request. `C4-c`'s `b7a41e2c9d63`
is what took `dev` to 31 after that cut.

Production's live stamp is `a4c6f1d92b73`, read from the running `shekel-prod-db` container on
2026-09-01 rather than quoted; that revision is `origin/main`'s single migration head, so
**production sits exactly at `main`**. `main` carries 140 migration files and `dev` 170, and
restoring the live dump and migrating it applied **30** revisions, landing on `c9a4e7b21d58`, and
re-taken after `C4-c` merged it applies **31**, landing on `b7a41e2c9d63`. Three independent
derivations agree at each point. The earlier "28" was production to THIS BRANCH's head -- and that
branch is **37 commits behind `dev`**, which is also why every rank read from its `steps.md` is one
high.

*(The alembic chain FORKS at `e2d7a94f61c3`: this branch's `31bb08f73e50` and dev's `f1c8b3d5e920`
are siblings. Adding this branch to a release produces two heads and a REFUSED `flask db upgrade`
until the second lander re-parents. A release cut from `dev` alone is unaffected.)*

### 5.1 What is and is not money-neutral

| act | moves money? | evidence |
|---|---|---|
| `statement_import.record_statement` | **no** | its only three `session.add` targets are the bank tables |
| the import ROUTE (`import_statement`) | **yes, once rules exist** | it commits `record_statement` AND `file_new_swipes` in one unit of work |
| the RELEASE | **yes** | at least `X-f3c-2b-1` legalises rows by moving five openings |

**`file_new_swipes` is not merely a money-moving act bundled into an import.** Its module docstring:
*"**It MOVES MONEY, and it is the only door in the app that moves money without a press.**"* It
turns each new swipe line whose merchant carries a standing rule into a purchase, dated by the bank.

**Today it would file nothing, because `budget.merchant_rules` is 0 rows** (66 merchants exist, zero
rules). **Neutrality is a property of an EMPTY RULE SET, not of the import**, and the first rule the
owner states ends it. So the honest split is: *recording the lines never moves money; the import
route bundles the only unpressed money door in the app; the release moves money regardless.*

### 5.2 What the release is still the gate for

Production has no `budget.account_openings` table at all, so
**the opening cannot be restated there** and `X-f3c-2b-2c` stays blocked (true when written; the
door has since deployed, and `X-f3c-2b-2c` was performed through it 2026-10-12 and shipped at
`2bcf2da5`). That was unchanged and it was the real reason to release.

**Testing against dev data cannot answer what a release does to production.** Every rehearsal here
ran against a restore of production instead, and that should be the standing procedure.

**The A/B instrument must be RESET-FREE.** Section 1.5's positive control shows
`cash_daily_balance_series` reporting `0 of 1431` for a `$500` move that landed. A migration ALTERS
EXISTING ROWS -- exactly the class that instrument is blind to -- so a release rehearsal built on it
would grade a money-moving release as neutral. Use `cash_ledger.dated_deltas` or
`balance_at._outstanding._books_balance_at`; neither applies an assertion policy.

**Two revisions in the release invalidated Section 4's original pricing**, and neither was in any
database this document first rehearsed on: `c9a4e7b21d58` makes a transfer shadow's amount DERIVED
(0 of 248 live shadows now store a figure), and `b8e4c1f7a903` changes the entry amount constraint
to `<> 0` so a refund is a negative purchase.

**Section 4 has now been re-priced twice, and the second re-take changed nothing.** Against a
restore migrated to `b7a41e2c9d63` (2026-09-02) every figure is identical to the `c9a4e7b21d58`
reading -- 926 live rows, 238 purchase-tracked, 165 needing a movement, 14 dateless, 38 shadows.
**The reason is worth stating rather than the result**: `C4-c` drops `period_index` and `end_date`
from `budget.pay_periods`, and this census is over `budget.transactions`. A census is only stale
when a revision touches ITS OWN subject, which is a thing to check rather than to assume in either
direction.

*(`C4-c` is nonetheless the same pattern one table over: a pay period's `end_date` stops being
stored and becomes derived. That is the amount-ownership argument of Section 6.1 applied to the
calendar, and it is corroboration that the direction is the project's own.)*

## 6. The order -- the ruled one is WITHDRAWN, and a cheaper one replaces it

**Section 6's original order was ruled by the developer on 2026-09-01 and is withdrawn by its own
author the same day.** Two findings make that unavoidable rather than advisable:

1. **The plan gate REFUSED it.** Inverting `#5`/`#6` was demonstrated against a mirror of
   `tools/plan_gate` and fails **three arms** -- `X-f3c-5` would be ranked ahead of the unshipped
   step it is `blocked by`, `X-f3c-2b-3`'s `starts` would precede its blocker, and the order table
   would no longer be sorted into execution order.
   **An approved order the gate will not accept is not a plan.**
2. **It front-runs a family nobody has started while a cheaper family is already in flight**, and it
   moves the ENTIRE credit-card arc from "behind one step" to "behind a schema refactor". `X-f3c`
   transitively blocks **29 open ranked steps -- 18 of them the whole credit_card arc** -- against
   the standing developer priority of 2026-08-19 (*bank_import to production, then credit_card*).

### 6.1 The replacement

**It is largely a RE-RANK of rows that already exist rather than an insertion**, which is a real
argument beside the technical one:

| # | what | why here | new rows? |
|---|---|---|---|
| 1 | release, import, restate the opening to `$3,182.63` | removes **`$2,493.47`** of `e_o` now; owned by a separate session | none -- operator |
| 2 | the amount-ownership cutover: `X-au-k` `#10`, `X-au-d` `#11`, `X-au-e` `#12`, `X-au-f` `#17`, `X-au-h` `#18` | **already ranked, four of five `starts=NOW`** | **none -- a re-rank** |
| 3 | the level relation, reshaped per 6.2 | ruled; independent of the movement work | 1-2 |
| 4 | the movement unification, re-cut per 4.5 | priced against the POST-cutover column set | 5 |
| 5 | the one-time manual reconcile | done once, into the right shape | 1 |
| 6 | THE FLIP | by then it moves nothing | re-point |
| 7 | `X-f3c-4`, re-specified per 3.3 | almost nothing left to do | re-point |

**Why the cutover goes SECOND, and this is the strongest single argument in the document:**

- `X-au-e` NULLs `estimated_amount` on **511 template rows** and `X-au-d` on 51 salary rows. A
  movement table built before them copies figures three ranked steps are about to delete.
- **`X-au-f` makes Transfer Invariant 3 STRUCTURAL rather than maintained.** Transfer shadows are
  the single largest named risk in Section 4.4 -- 38 of the 165 rows needing a movement. Running
  `X-au-f` first REMOVES that risk instead of managing it.
- `c9a4e7b21d58`, already on `dev`, has begun the same cutover: 0 of 248 live shadows still store a
  figure. The family is in motion.
- `X-au-g` ticks with `#16`, which **another balance session is building right now**. Sequencing the
  cutover forward finishes work already in progress rather than opening a second front.

**What it costs, stated plainly.** The credit-card arc still waits, and so does THE FLIP. This order
is not faster to the flip; it is cheaper to sequence, it lands the `$2,493.47` immediately, and it
builds the movement table against derivations rather than against stored figures that are being
deleted underneath it.

**It also creates a work-in-progress hazard the coordinator must manage**: both balance lanes would
be inside `X-au-*` at once, and `X-au-d`/`X-au-e`/`X-au-f` are explicitly DELETION steps. A module
list accompanies the recommendation.

### 6.2 The level relation still needs re-ruling

- **`R-GW`** (developer, 2026-08-27, on measurement):
  *"the residual is RECORDED before the reset is DELETED."* Rows 6 and 7 above delete the reset
  before the residual act exists. The proposal's own answer is that after a full reconcile
  **there is no residual left**, so R-GW's premise dissolves -- but that argument was never made,
  and the ruling is the developer's to dissolve or keep.
- **Section 3.2's "one append-only relation" is not implementable as written.**
  `budget.account_anchor_history` is append-only at the DATABASE tier -- a `BEFORE UPDATE` trigger
  that raises, plus `REVOKE UPDATE, DELETE`. The bank's anchor is **RELEASED BY AN UPDATE**
  (`release_anchors_from` nulls `balance_effective_on` and `balance_evidence_id`), and that release
  exists because two adversarial reviews reproduced a `$150.00` hole reported as covered.
  **The fix is more correct than the original**: a release becomes an APPENDED superseding row
  rather than an in-place update, which also preserves the audit trail the UPDATE destroys. But it
  makes 3.2 a larger change than "move two tables into one", and 3.2 is the part that was ruled.

## 7. What this deletes

The developer's standing instruction is to eliminate fences, checkers and allowlists in favour of
structural code. What this design removes, as opposed to guards:

- **the `account_trueup` equity plug** -- no longer written, so `N-171` needs no booking act;
- **the assertion RESET** -- `R-FO`'s five-way dispatch loses its PLAIN arm honestly rather than by
  the flip's fiat;
- **`is_envelope` as a RECORD concept** -- the settle verb's branch reads `bool(purchases)` since
  `X-bi-4a` (**R-BAL78**), so the flag picks no settle path; the definition's flag stays as the plan
  item's KIND (**R-BAL85**);
- **`N-314`'s ruling** -- replaced by an evidence rank in a schema, so no code branches on which
  record wins;
- **`R-GY`'s offer gate in its current direction** -- the gate stops being a fence on an act and
  becomes the flip's own per-account readiness measure, which is what `SpanAgreement.reconciles` is
  actually good at.

What it does NOT delete, and should not: the books-boundary triggers (`X-f3c-2b-1`, `2b-2b`, `2d`)
and the append-only refusals. Those make states unstorable, which is structure, not a fence.

**The transfer invariants stood in that list until 2026-09-09 and the claim was measured FALSE**
(ruling **R-BAL13**). *Those make states unstorable* is true of invariant 1's UPPER bound
(`uq_transactions_transfer_type_active`), invariant 2's hard-delete arm (the `transfer_id` FK's
CASCADE) and invariant 3's derived-amount half (`X-au-g-2c-2`). It is false of the rest: invariant
1's LOWER bound is a Python validator that runs only when a mutation door loads the pair, its
ENDPOINT clause -- the expense leg sits on the parent's `from_account_id` -- is held by nothing at
all while `account_posting_service/_walk.py` raises on the state the schema admits (**BAL-475**),
invariant 2's creation arm is convention, and invariant 4 is TWENTY `transfer_id IS NOT NULL` branch
sites across twelve files in `app/`. `posting_service.py` ships a repairer for pairs that break,
which is the tell: a rule with a documented repair procedure is a rule that gets violated.

So this design DOES delete them, and the deletion is `X-bi-6a` + `X-bi-6`. It also corrects this
document's own scope: section 4.4 names transfer shadows as the largest risk and this section
treated their invariants as settled, but the movement unification replaces the RECORD half only --
`balance_at/_plan.py`'s PLANNED tier reads projected shadows, so the PLAN half needs a producer of
its own before the shadow rows can go.

---

## 8. Open questions

1. ~~Is the movement unification approved?~~ **Approved 2026-09-01** as part of Section 6's order.
   What is now open is Section 4.5's RE-CUT, which is a different decomposition from the one
   approved.
2. ~~Does the reconcile happen after the restructure?~~ **Answered: after.**
3. **Is the replacement order in Section 6.1 accepted?** The ruled order is withdrawn; the gate
   refuses it. This is a fresh decision, not a revision.
4. **`R-GW`** ruled the residual is recorded before the reset is deleted. Does a full reconcile
   dissolve its premise, or does it still bind (Section 6.2)?
5. **`R-IQ`**: rationale refuted, mechanism sound and load-bearing for the `-$2,493.47`. Amend into
   a standing repair prompt, withdraw, or keep as a gate?
6. **`R-IP` ruled the acceptance act is a TWO-armed door** -- mint OR reprice. Section 3.3 specifies
   only the mint, and Section 3.1 would delete the columns (`settled_amount`, `settled_basis_id`,
   `honoured_correction`) that R-IP's freeze predicate reads.
   **Section 3.1 silently repeals half a ruling taken the same day.** How is the reprice arm
   written?
7. **The INVERTED gate is unsafe as specified.** `statement_imports.period_start`/`period_end` are
   min/max of the file's LINE days, so coverage is systematically UNDER-claimed (**N-434**). Under
   the current gate that error is safe -- it refuses spans the bank covered. Inverted, it means days
   the bank DID read are reported unread, so the act would book an expense there. Does the inversion
   wait on N-434, or does the gate need a different shape?
8. **`R-GL`** sequences `bank_import:X-gh` after the cutover. Under Section 3.2 that reason may no
   longer bind.
9. **`R-FN`'s "categorisable later"** rests on a settled row being re-categorisable, and
   `category_id` is locked. Nobody has specified the revert-categorise-re-settle path.
10. Is a single 30-revision release acceptable after a RESET-FREE A/B rehearsal, or should it split?

---

## 9. What three adversarial reviews refuted, 2026-09-01

Three neutral reviewers ran independently against the measurements, the design, and the cost and
order. **The direction survived; a great deal of the evidence did not.** Recorded here rather than
silently corrected, because the pattern matters more than any single figure.

**Refuted, and corrected above:**

| claim | what was wrong |
|---|---|
| "0 of 1,431 account-days moved" as the money-neutrality proof | the harness is pinned by the assertion RESET; a `$500` injected move read `0 of 1431` |
| "the import is money-neutral" | true of `record_statement`; the ROUTE bundles the only unpressed money door in the app |
| the migration set of 192 rows | keyed on a dead column; the real predicate is `tracks_purchases`, the set is 165, and `R-HJ` forbids a migration doing it |
| "80 branch sites" as the refactor's size | 19 of 62 are executable, 4 are branches, and the 17,125-line fold layer contains neither token |
| "one test file in six" | one in two on the real surface -- and the suite cannot grade `e_m` at all |
| the five-leaf decomposition | leaf 1 not additive (21 columns, not 4), leaf 2 has no axis to switch and would run two semantics live |
| "71 of 74 compared days disagreeing" | welded from a different, shorter span; over this span it is 98 of 101 |
| the `-$1,850.98` explanation | BOTH terms moved, not just the assertion |
| the shape table's envelope rows | classified on the inert column; 37 facts misassigned |
| "the bank's closing is known for every day" | stated for 71 of 155; the rest derived or carried |
| "the identity closes to the cent" as corroboration | the bank terms cancel; it holds against a fabricated bank series |
| the ruled ORDER | refused by the plan gate on three arms |

**Attacked and NOT broken:** the algebra of `difference = e_a - e_o - e_m`; both books-boundary
triggers, with firing positive controls; `opening + SUM(dated_deltas)` IS `_books_balance_at`, 155
days, 0 mismatches; no plan-tier contamination of the compared window; the bank export dedup,
reproducing `$933.79` to the cent; no transfer-shadow double count; the `transaction_entries` hole
closed; `record_statement` writing exactly three tables; every Section 1.5 figure; and
**`e_m = +$4,270.78` with THE FLIP right on 0 of 155 days** -- the two claims the argument rests on.

**The lesson worth keeping.** The blind harness is the one that matters: a gate shipped with no
positive control, presented as the strongest evidence in the document, and unable to fail for
exactly the reason the document was arguing about. It was found only because a reviewer was told to
construct a control and try to make it fire.

---

## 10. Every plan item has exactly one definition -- the audit behind `X-bi-7`

**Ruled 2026-09-12 (developer, `balance:R-BAL20`, granted by the coordinator and filed in this
family's registry commit), audited the same day on tree `81b92227`** (`71e09443`, X-bi-1b's seal of
`companion_visible`, with `dev` `98cc0ab4` merged) and on the production restore `shekel_xbi1b`. Two
adversarial passes over the draft refuted four of its figures and one of its central claims; what
stands below is the corrected text, and 10.9 records what fell. This section is the argument the
family's specifications point at; the specifications themselves are the family's cards in the
tracker. **Every fork in 10.6 was RULED by the developer on 2026-09-12, in this session, and 10.10
states the ruled design in one place.**

Three words are used precisely throughout: a row is **undated** when its `due_date` is NULL; it
**answers no occurrence** when its `occurs_on` is NULL (a different column and a different fact);
and **settled** is `Status.is_settled` -- Paid or Received -- never Credit or Cancelled, which are
immutable but not settled.

### 10.1 The ruling, in plain words

Today a budget line is one of two things. A **recurring** line has a *definition* (a row in
`budget.transaction_templates`: its name, category, account, price and the two flags "track
individual purchases" and "visible to companion") plus one *occurrence* per paycheck, written by the
recurrence engine and pointing back at the definition. A **one-off** line has no definition at all:
the occurrence row carries its own name, price and flags, and `template_id IS NULL` is how the code
tells the two apart.

That split is why finding **BAL-484** exists. A generated row still has the two flag cells, they are
dead there (the definition answers), and one keeps a writer. `X-bi-1` and `X-bi-1b` sealed them; the
developer refused a CHECK and a refusal as fences on cells with no reason to exist and ruled the
from-scratch shape instead: **every plan item has exactly one definition.** A one-off becomes a
definition with no recurrence rule (a shape the schema has allowed since `R-F6` inverted the rule's
key) plus one placed occurrence. The row carries no flag, so both cells are deleted;
`visible_to_companion` and `tracks_purchases` read the definition with no branch; and the
`template_id IS NULL` test stops meaning "one-off".

### 10.2 What was measured, and two figures in the handoff that were wrong

Over the 951 live rows of `budget.transactions` on the restore:

| population | rows | note |
|---|---|---|
| template-linked | 636 | 40 definitions, every one with a rule |
| transfer shadows (`transfer_id`) | 252 | `X-bi-6` deletes them |
| CC paybacks (`credit_payback_for_id`) | 29 | the card arc's `CC-7` (re-minted 2026-09-18; was `CC3b` / `CC3c`) deletes the payback; `X-au-i` is WITHDRAWN |
| **link-less (the true one-offs)** | **34** | 0 soft-deleted; all baseline scenario; all OWN their figure |

**The handoff's "~417 one-off rows (315 live)" is wrong, and so is the 315.** Both figures counted
`template_id IS NULL`, which is shadows and paybacks too. `ck_transactions_one_pricing_link` allows
at most ONE of the three links, so neither a shadow nor a payback can take a definition, and the
migration mints **34**, not 417 -- on the restore; the migration selects by predicate, because
10.7's second leaf changes the population before its fourth runs. Of the 34: 4 are envelopes (the "4
where there are 238"), 3 are companion-visible, 1 carries purchases (3 entries, `$1,000.00`
Homeschool Curriculum), 12 are Paid, 3 Received, 14 Credit, 4 Cancelled, 1 Projected;
**8 carry a due date and 26 are undated** (of the 26: 7 Paid, 1 Received, 13 Credit, 4 Cancelled, 1
Projected -- 8 settled, 25 immutable); no two share `(category_id, name)`, but
**three share `(category_id, name)` with a recurring definition** (Strawberry Picking / template 23,
Mother's Day / 24, Homeschool Curriculum / 21), which is trace 5's subject.

**And the discriminator does NOT lose its meaning under this step alone.** After it,
`template_id IS NULL` still names a shadow or a payback -- 281 live rows -- until `X-bi-6` takes the
shadows and the card arc the paybacks. What the step does is make the ONE-OFF half of the test
unwritable, which 10.5 classifies site by site.

### 10.3 The transfer twin is already at this shape, and it has two live defects there

`budget.transfers` reached the one-definition shape without anyone naming it: production holds
**0 ad-hoc transfers** (all 126 live ones are template-linked) and **2 one-time transfers**
(definitions 7 and 10 of its 6 transfer definitions, both Paid), each a rule-less `TransferTemplate`
plus one materialised `Transfer`. The code is the precedent for every question 10.4 asks:

- **birth**: `routes/transfers/_instances._materialize_one_time_transfer` (`:81`) creates the one
  row through the service door, priced by its DEFINITION (`derived_ownership(TEMPLATE)`, plan step
  `X-au-f`), `due_date` = the chosen paycheck's start, answering no occurrence, `is_override` False;
- **the definition speaking to its row**: `transfer_recurrence.propagate_to_unruled_template`
  (`:248`) is the maintain pass's decision for the one shape that does not regenerate -- it applies
  `_derive_unruled_fields` (`:220`) to the live, non-overridden rows and retains a reattributed row
  that holds owner records, exactly as `classify_maintain_work` does for a recurring definition;
- **the pass never runs on it**: `_recurrence_conflict_chooser.regenerate_or_conflict_chooser:576`
  skips a template that neither has nor had a rule, because a regeneration with no rule names no
  occurrence and RETIRES every record-free dated row (defect **D16**).

**Two defects on that twin, found by this audit and filed under 10.7, and a third already owned:**

1. `pay_period_gates.count_discardable_items:380-389` counts a transfer as unrecoverable when
   `transfer_template_id IS NULL`, so a one-time transfer -- template-linked, Projected, not
   overridden -- is counted as REGENERABLE, and truncate / regenerate's confirmation promises a row
   back that no rule will write. Both one-time transfers on production are Paid, so the count is
   right today by accident; a Projected one would be destroyed uncounted. The transaction arm
   (`Transaction.template_id.is_(None)`) has the identical shape and is what this step would
   inherit.
2. A period MOVE or a typed figure on a one-time transfer flips `is_override`
   (`routes/transfers/mutations.py:317`) and a typed figure makes it OWN (`:717`), after which
   `non_repeating_live_transfers` (`_instances.py:242`, `is_override IS FALSE`) excludes it and the
   definition never speaks to it again -- its price and name go dormant on the Recurring page with
   no live reader. Measured: transfer 409 (Rogue Equipment, moved to an earlier paycheck) is
   `is_override = TRUE`, OWN `$2,000.00` beside a definition stating `$2,000.00`. Agreement is not
   the test (`R-IZ`): a one-off has ONE occurrence, so "detach the row from its definition" leaves a
   definition with nothing to define.
3. The twin's delete door keys soft-versus-hard on the LINK (`routes/transfers/mutations.py:489`,
   `soft = bool(xfer.transfer_template_id)`), so a one-time transfer is soft-deleted and stays
   restorable -- finding **N-386**, owned by `X-bg`. `_delete.py:131` is the same shape on this
   table, and 10.5 re-keys it; the twin is NOT a precedent for `recurs` on that site.

### 10.4 The ten traces

Every line number is on tree `81b92227`; the handoff's `create.py:176,261` are `:175,259` here.

**1. The create doors, and the price.** Four writers make a link-less plan item, not two:
`routes/transactions/create.py:175` (inline) and `:259` (full) for the owner, and two bank-import
producers -- `statement_match/_container._create_envelope:360` (the NEW-ENVELOPE answer's row,
`is_envelope=True`, budgeting `$0.00`) and `statement_match/_uncategorized.mint_uncategorized:178`
(the row a bank line's money requires, which **carries `category_id NULL`** for two of its three
callers, `:129`, where a definition's `category_id` is `NOT NULL`). A fifth shape is the template
form's own "Does not repeat" (`_recurrence_fields.html:99`): `crud.create_template:228` then writes
a definition with no rule and generates nothing (`:262`, `if rule:`), so it is a definition with no
occurrence -- production has never held one. The price: `cash_ledger._amount_source` prices a row
either OWN (rule 1) or by its definition's series as of the row's own due date (rule 3),
`DerivedRowFields.amount_ownership` is the constant `derived(TEMPLATE)` since `X-au-e`
(`_amounts.py:172`), and **`_definition_cash._stated_amount:161` REFUSES a row with no due date.**
`X-au-f` ruled the one-time TRANSFER definition-priced. Which state a one-off transaction is born in
is fork 10.6-A, and the series rule that fork rests on is stated there.

**2. The edit door.** `routes/transactions/mutations._apply_field_updates:238-240` `setattr`s every
non-seam PATCH field onto the ROW -- `name`, `category_id`, `pay_period_id`, `due_date`, `notes` and
the two flags (`schemas/validation/transactions.py:128-129`) -- and `:267` flips `is_override` when
the row is template-linked and the figure was authored or the period moved.
`routes/templates/crud._apply_fields_and_propagate_rename:120` is the definition's side: it writes
the allowlisted fields (`_TEMPLATE_UPDATE_FIELDS:97`, the flags included) and propagates a rename to
every row. Under the ruling a one-off's identity fields, flags and (fork A) price are the
definition's, so the row's PATCH door must route them there and the flag fields leave all three row
schemas (`:128-129`, `:158-159`, `:196-197`). **`BAL-484`'s writer is that route path** -- the
schema field plus the `setattr` -- and it goes at the doors leaf; the model SETTERS stay until the
cutover leaf drops the columns, because 12 test constructors (8 of them link-less) pass a flag until
the fixture leaf moves them.

**3. The engine on a rule-less definition -- the trace that decides the shape.**
`recurrence_engine._plan.resolve_generation_plan:200` answers `None` for a template with no rule,
and `_pass.regenerate_definition` reads that `None` as
*a CLEARED recurrence, which names no occurrence and so considers every existing row for retirement*.
`_recurrence_common .classify_maintain_work:708-716` then RETAINS a row answering no occurrence as a
conflict (finding **REC-516**) and RETIRES -- a hard delete, `_maintain.py:355-357` -- a
record-free, non-overridden Projected row whose `occurs_on` the empty rule does not name. So **a
one-off's row survives the pass only through the route gate at
`regenerate_or_conflict_chooser:576`** (never-recurred definitions are skipped) and
`crud.unarchive_template:691` (`if template.recurrence_rule:`), and what the one-off row records in
`occurs_on` decides what happens the day its owner adds a rule to it -- fork 10.6-E walks both
answers. `classify_maintain_work`'s docstring calls its retain arm
*scaffolding, deleted when every such row records an occurrence*; under fork E(a) a one-off is a
designed, permanent producer of a template-linked row that answers no occurrence, and that sentence
is corrected by the leaf that makes one.

**4. Carry-forward.** `carry_forward_service/_context.py:190` routes a row to the envelope ROLLOVER
on `template_id is not None and tracks_purchases` and lets an ad-hoc envelope fall through to the
discrete bucket, which MOVES it whole with its purchases.
**The rollover does not need a next canonical, and the first draft said it did**:
`_classify_leftover_target:425-441` falls past GENERATE (dead for a rule-less definition,
`can_generate_in_period` needs a rule) to CREATE, and `_create_target_override_row`
(`_execute.py:619-683`) mints a `$0.00` override row of the SAME definition and folds the leftover
in -- the documented yearly-envelope case. So a one-off envelope has two candidate behaviours under
the ruling, and the gate's predicate is a decision: (i) keyed on "the definition has a rule", the
one-off moves whole, as today; (ii) left as is, the source row settles at its purchases and a second
ROW of the definition (answering no occurrence, like the first) takes the leftover -- which gives a
rule-less definition two rows, against the ruling's *one placed occurrence*, and breaks the one-row
signature 10.8 folds on. The specification takes (i). `_execute.py:157-190` then moves the discrete
rows in TWO bulk updates, flipping `is_override` on template-linked ones only; under the ruling that
flip is keyed on the same predicate, or a moved one-off leaves its definition (defect 2 of 10.3 on
this table). `_context.py:433` and `:439` read `source_txn.template` and are two more sites of
10.5's first class. `_execute._leftover_due_date:515` already states the ONE date rule for a
rule-less definition: the target paycheck's start (`:615`), *the paycheck's start is
`compute_due_date`'s OWN answer for a cadence that names no day of the month* -- the rule
`_materialize_one_time_transfer` applies too.

**5. The grid -- a live defect the step fixes, so the grade is a DIFF, not an equality.**
`grid_view_service.build_row_keys:152` keys a template-linked row's grid row on
`(category_id, template_id, None)` under the definition's name and a link-less row on
`(category_id, None, name)`.
**`_match_row_in_period:207-210` compares by NAME whenever EITHER side is link-less**, so a one-off
that shares `(category_id, name)` with a recurring definition is matched into that definition's row
AND its own, and the definition's live generated rows are matched into the one-off's row too. On
production that is **six cells**: one-off 2584 Mother's Day shown in template 24's row at paycheck
6, and template 24's rows 2379 and 2660 (paychecks 30, 56) shown in the one-off's row; one-off 2581
Homeschool Curriculum shown in template 21's row at paycheck 8, and template 21's rows 2324 and 2653
(paychecks 31, 57) shown in the one-off's row. Each definition's paycheck-4 row is Cancelled and
skipped at `:205`, as is one-off 2458 Strawberry Picking, which mints no key and matches nothing.
Those six cells change when the one-off takes a definition of its own and
`txn.template_id != rk.template_id` separates them; the ROW SET is unchanged, because no two of the
34 share `(category_id, name)`. **The handoff said the name arm becomes dead code; it does not** --
shadows and paybacks are keyed by name until `X-bi-6` and the card arc take them -- and the label
fallback at `:158` (*if the relationship failed to load*) is what dies once the FK stops being
`SET NULL` (10.8).

**6. The Recurring surface.** `recurring_view._described:477` already words a rule-less definition
as `None`, the macro renders it *One-time* (`_recurrence_macros.html:44`), it carries no monthly
figure, sorts last and is excluded from the section total (`_build_section:574-611`).
`archive_template`, `unarchive_template` and `hard_delete_template` (`crud.py:602`, `:658`, `:721`)
are generic over a definition's rows: archive soft-deletes its Projected rows, hard-delete refuses
on paid history or a standing merchant rule and archives instead, else deletes the non-settled rows
and the definition. Whether one-offs are LISTED there is fork 10.6-C. Four more readers see a
minted, active definition: `templates/surface.py:98` (the archived drawer),
`period_population.py:158` and `savings_dashboard_service/_metrics.py:481` read the rule and find
none; **`routes/accounts/crud.py:662-671` changes behaviour** -- an account whose only history is a
settled one-off is ARCHIVED today (`account_has_history`) and would be REFUSED with
*delete those recurring transactions first*, naming definitions that under fork C(a) are listed
nowhere -- so it is a `recurs` site of 10.5's first class.

**7. The popover.** Three branches on the row's link in `grid/_transaction_full_edit.html`: `:386`
(due date is an INPUT on a link-less row, TEXT on a generated one), `:453` (the two flag controls,
link-less rows only), `:568` (the delete dialog's *this cannot be undone* against
*this occurrence will not come back*). No other Jinja file branches on a transaction's link; the two
`template_id` tests in `accounts/_statement_reconcile_body.html` are on a rule STATEMENT.

**8. The data migration.** `X-au-e`'s `c8f3a5d2e714` is the precedent: a migration that emptied a
money column on 525 rows, declared `TEMPLATE`, and reproduced every figure on downgrade under
rulings `R-JB` / `R-JC`. This one mints a definition per link-less row (`user_id`, `account_id`,
`category_id`, `transaction_type_id`, `name`, `default_amount` = the row's figure, both flags from
the row's cells, `sort_order` 0, `is_active` TRUE) and one `template_amount_versions` row each
(`effective_date` = the row's due date, the series `set_amount` would open), dates the undated rows
(fork B), links each row and -- under fork A -- declares it `TEMPLATE` and empties its figure, then
drops both flag columns, re-cuts `ck_transactions_one_pricing_link` from `<= 1` to `= 1`, and moves
`template_id`'s key from `ON DELETE SET NULL` to `RESTRICT`.
**Two of its writes are not the same kind, and `R-HJ` is asked of each.** Moving a stored figure
from a row to the definition that now states it creates no money row -- the shape `c8f3a5d2e714`
already took. Writing a due date onto the 26 undated rows -- 8 of them settled, 25 immutable --
records a fact nobody stated, finding **N-181**'s class, and whether that is `R-HJ`'s class or the
cutover's is fork B's question, asked there with its cost. The downgrade is 10.8.

**9. Overlap with `X-bi-5`.** Re-pointed here at the DEFINITION's `is_envelope` and DISSOLVED
2026-09-20 (**R-BAL85**): after this family the definition's flag is the kind's one home and
`tracks_purchases` its one reader, carry-forward needs the kind before any purchase exists, and no
step deletes it. `companion_visible` likewise stays on the definition and no step touches it.

**10. The cap.** The balance README stands at 1308 of 1330 with the two owed ticks netting about
`-1`; a container entry and four leaf entries at two to three lines each fit, with the argument
HERE. If the next edit binds, that is the developer's question under rule 5 and never a squeeze.

### 10.5 What the discriminator becomes: `recurs`, stated once

Reading every `template_id` and `.template` test on a TRANSACTION row on this tree (a name-keyed
grep over-connects -- `MerchantRule.template_id` and the statement-match rule sites are another
table's link -- so each site was read, not counted):

| meaning today | sites | under the ruling |
|---|---|---|
| "no cadence to derive this from" | `mutations.py:267` (override on move), `_gates.py:127` (due-date edit refused), `_context.py:190`, `:433`, `:439` and `_execute.py:157-161` (carry-forward), `_delete.py:131` (soft vs hard), `pay_period_gates.py:380` (discardable), `accounts/crud.py:662-671` (an account's delete refusal), popover `:386`, `:568` | become **"the definition has no rule"**, through ONE accessor -- `Transaction.recurs` reading `template.recurrence_rule is not None` -- because two of them already spell it that way inline (`calendar_infrequency.py:84`, `:135`) and the route gate at `:576` is a third |
| "the flag is the row's own" | `transaction.py:754`, `:809` (the accessor branches), popover `:453`, the flag fields on three row schemas, `_apply_field_updates`' setattr of them | **deleted**: the accessors read the definition with no branch, the controls edit the definition |
| "this is a shadow or a payback" | `income_service.py:624`, `_conflicts.py:187`, `_amount_rule.py:190`, `grid_view_service.py:152`, `:207` | **stay** until `X-bi-6` and the card arc take those rows; each already reads correctly for a definition-linked row |
| bank import's own arc | `statement_match/_placement.py:355` (the cross-statement NEW-ENVELOPE suggestion keeps `template_id is None`), `_stating.py:646` (rule submission: template versus new envelope), `_rules.offerable_templates:772` (every active envelope definition is a pickable answer), and the two minters in trace 1 | fork 10.6-D: the suggestion arm matches NOTHING once every envelope has a definition, so finding **N-327**'s cross-statement half regresses (its within-press half, `_container.MintedEnvelopes`, is key-based and unaffected); a one-off envelope becomes a pickable "recurring definition"; a rule stated on a one-off destination flips from NEW-ENVELOPE to TEMPLATE and then resolves UNRESOLVED in every other paycheck (`_template_placement:224-286`) |

`recurs` in SQL (`count_discardable_items`) is `BAL-482`'s shape -- a rule spelled a second time in
another language -- and takes the same remedy: load the period's rows and ask each one. Re-keying
the first class is behaviour-preserving on production only as a DATA fact (no rule-less transaction
definition exists); on any definition whose recurrence is later cleared, `recurs` turns
`_delete.py:131`'s soft arm hard, drops the `:267` flip and opens the `:127` due-date edit, which is
the behaviour a rule-less definition's row should have -- the twin does NOT have it yet (10.3, item
3).

### 10.6 The forks, each with its ruling (developer, 2026-09-12)

Walked on one row: **Kayla's Kindle, `$162.25`, paid 2026-05-13, budgeted in the paycheck starting
2026-05-07, undated, id 2459.**

**A. Where a one-off's price lives.** RULED (a), `R-BAL21`.

- *(a) The definition prices it, and a one-off's series holds ONE version* -- recommended, and the
  from-scratch answer: the migration (or the create door) opens the definition's series with one
  version, `$162.25` effective on the row's due date, the row states nothing and resolves it through
  amount rule 3, exactly as `X-au-f` ruled for the one-time transfer.
  **The series rule has to be stated, because `set_amount` alone does not give it**: that door
  corrects a version IN PLACE only when one already stands on `effective_on`
  (`template_amount_service.py:334`) and otherwise APPENDS, and both existing amount doors pass a
  date of their own (`crud.py:255` today's, `:485` the form's `effective_from`, defaulting to today
  at `:408`). Walked without the rule: the owner types `$170.00` on 2026-09-13, a second version
  `2026-09-13: $170.00` is appended, `amount_as_of(definition, 2026-05-07)` still answers
  `$162.25` -- the grid and the balance read the old figure while the edit form reads the new, which
  is the two-homes crossing this option exists to refuse. **The rule**: a one-off's price is one
  fact, so a restatement lands on the date of the version `amount_as_of` answers for the row's due
  date and corrects it (`set_amount` rule 1, `_version_on:293`; an unchanged figure is a no-op plus
  resync; the scalar follows), never appends; with one version the series is flat on every date, so
  the row's due date can move freely and prices nothing differently, and a definition that was made
  to repeat and then cleared -- two standing versions, one row -- still restates the one its row
  reads. Where that rule lives -- an arm in `set_amount` beside its existing "does not own its
  amount" arm, or a `restate_one_off` door the popover's one-off arm calls -- is the building leaf's
  placement question, not a design fork. This differs from a RECURRING row, whose typed figure
  detaches the row (OWN, `is_override`) because THAT statement is about one occurrence among many;
  for a one-off the only occurrence IS the definition, and detaching it is defect 2 of 10.3. It
  requires the row to carry a due date (`_stated_amount:161`), which is fork B and is what `X-bv-2`
  already makes structural for every definition-linked row.
- *(b) The row owns its figure, the definition keeps `default_amount` as a copy* -- rejected: after
  the edit the row says `$170.00` and the definition `$162.25`, two homes with no reconciler, rule
  14's crossing case -- the same failure (a) has WITHOUT its series rule.
- *(c) The row owns it and the definition's `default_amount` becomes NULLABLE for a rule-less
  definition* -- rejected: two shapes of definition, one of which cannot be turned recurring without
  a price being restated, and a NULL that means "ask the row" is `amount_source_id`'s deleted design
  (`R-IY`) one table over.

**B. The one-off's due date.** RULED (a), `R-BAL22`. Under A(a) and `X-bv-2` every one-off carries
one.

- *(a) The placed paycheck's start, unless the owner states otherwise* -- recommended: the ONE rule
  the app already states twice (`_leftover_due_date:615`, `_materialize_one_time_transfer`), and it
  is BALANCE-NEUTRAL by construction -- `DerivedPeriod.attribution_day`
  (`pay_calendar/_derive.py:311`) already budgets an undated item to its paycheck's start and clamps
  a dated one into the paycheck, so the 26 rows land where they land today.
  **The evidence from the owner's own 8 dated one-offs is weaker than the first draft claimed**: 5
  of 8 sit on their paycheck's start; 3 (1507, 1509, 1825) sit on an EARLIER paycheck's start,
  consistent with a move after creation, and 1507 is one day off it. The inline create takes no date
  today and defaults to it; the popover keeps the input and stops offering to clear it; the
  migration dates the 26 undated rows by it. **The cost, stated**: 8 of the 26 are settled, and the
  7 settled EXPENSES among them (2818 is income and outside the metric) then read 6 to 11 days late
  in `spending_analysis.payment_timeliness_from_txns` -- a day nothing observed, finding **N-181**'s
  shape, which that step resolved by leaving the edit door open rather than narrowing the backfill;
  the same remedy holds here, and whether a migration may write that day at all (`R-HJ`, trace 8) is
  part of this ruling.
- *(b) `settled_on` where settled, the paycheck's start otherwise* -- rejected: it manufactures "on
  time" instead of "late", two date rules for one column, and a settled row's date would then depend
  on a fact (`settled_on`) a revert withdraws.
- *(c) Leave a one-off undated and exempt it from `X-bv-2`'s CHECK and `_stated_amount`* --
  rejected: a CHECK cannot see whether a template has a rule, so the exemption is a fence in Python
  beside a constraint that no longer states the invariant, and amount rule 3 grows a branch for the
  case.
- *(d) The migration refuses on an undated row and the owner dates the 26 by hand first* -- the
  `R-HJ`-shaped alternative, stated because the rows are few and one owner's: it blocks the deploy
  on a hand task, and the app's own rule for a rule-less definition's date already exists, so it is
  not recommended.

**C. Where a one-off's definition is edited and seen.** RULED (a), `R-BAL23`.

- *(a) The grid popover is its whole lifecycle; the Recurring LIST shows definitions with a rule* --
  recommended: the owner creates it at the grid as today, the popover routes name / category / flags
  / price to the definition without showing one, delete removes row and definition together (a
  rule-less definition with no row defines nothing)
  **subject to the same refusal `hard_delete_template` makes for a standing merchant rule** --
  `fk_merchant_rules_template_account` is `ON DELETE CASCADE` (`merchant_rule.py:293`), so a bare
  delete would take an owner's stated answer with it -- and the popover offers *make this repeat*,
  which is `templates.edit_template`: the definition is reachable, not listed. The transaction
  form's *Does not repeat* option is removed: it makes a definition with no occurrence, and the grid
  is the one door for a one-off. The transfer twin keeps its period picker (that form IS its one-off
  door) until its own leaf decides. The account-delete refusal (trace 6) then counts definitions
  with a rule, so an account holding only a one-off archives as it does today.
- *(b) One-offs are listed on the Recurring page in a One-time section with edit / archive / delete
  there* -- a second door for every act the popover already performs, and the transaction form's
  *Does not repeat* would need the period picker `R7b-4` removed from it.

**D. The bank-import minters and the merchant-rule surfaces.** RULED (`R-BAL24`), and NOT as
recommended; **RE-RULED 2026-09-13** (10.11: the identity question was already
`bank_import:X-f6c`'s, and `X-bi-7b` now builds that shape with `X-f6c` as its alias):
**X-bi-7 builds it** -- balance makes the definition's `category_id` nullable and mints a definition
per bank-minted row and per envelope; the merchant-rule picker lists one-off envelopes; N-327's
cross-period identity question is left as it is. The recommendation had been that balance state the
constraint and bank_import rule the shape; 10.10 carries what the ruled answer commits to.

- `mint_uncategorized:178` cannot simply mint a definition: two of its three callers leave
  `category_id NULL` and a definition's is `NOT NULL`. Three answers, none in the first draft: the
  definition's `category_id` becomes nullable (a schema change on the definition for one producer's
  sake); such rows take a real Uncategorized CATEGORY -- which is the OPPOSITE of the mechanism the
  module's header and ruling **R-FN** describe, where a category-less row books its counter leg to
  the per-owner Uncategorized LEDGER ACCOUNT (`posting_service._settled_target`) precisely so it can
  be categorised later rather than misfiled now; under a real category the leg books through that
  category's ledger account and the spending reports gain a category nobody chose, which is that
  answer's cost; or bank-minted movement records stay link-less as a THIRD meaning of
  `template_id IS NULL` until the movement unification makes them MOVEMENTS with no plan item
  (section 4.4, risk 4) -- in which case the CHECK cannot tighten to `= 1` at 10.7's fourth leaf and
  the one-off half stays a Python invariant until then.
- `_create_envelope:360` and `_placement.py:355`: the NEW-ENVELOPE answer exists because
  *an ad-hoc row carries no identity across periods for anything to converge on* (N-327). A
  definition IS that identity. The from-scratch answer collapses `RuleAnswer.TEMPLATE` and
  `NEW_ENVELOPE` into one -- the merchant rule names a definition, and the per-paycheck envelope is
  that definition's occurrence placed in the line's period -- which is bank_import's to rule, and
  **it gives a rule-less definition one row per paycheck by design**, so 10.8's one-row downgrade
  signature and the ruling's *one placed occurrence* both need that shape named when it is ruled.
  **The regression starts at this family's doors leaf, not later**: the moment one-off definitions
  exist, `offerable_templates:772` offers every one-off envelope (4 today, plus one per bank-import
  CREATE-NEW press per paycheck) as a pickable "recurring definition", and `rule_naming:646` records
  a rule on a one-off destination as TEMPLATE, which `_template_placement` then answers UNRESOLVED
  in every other paycheck. So the doors leaf either filters both on `recurs` -- the one interim that
  asks nothing, since a one-off is not a standing answer today either -- or the family waits on
  bank_import's ruling. What balance requires of the same leaf that tightens the CHECK: the two
  minters create no link-less row, the suggestion arm is re-keyed on the definition rather than on
  `template_id is None`, and a one-off's delete refuses (or re-points) a standing rule.

**E. What the one-off's row records as its occurrence.** RULED (a), `R-BAL25`;
**RE-RULED (b) 2026-09-13** (10.11: `recurrence:R19-b` makes `occurs_on` NOT NULL and deletes the
retain branch (a) relied on, so the row records its own date).

- *(a) `occurs_on` NULL* -- recommended: the transfer twin's choice, consistent with `R2e-3` having
  retired the `Once` pattern (a one-off has no rule, so it answers no occurrence). The day the owner
  adds a rule to it, the row is RETAINED (`classify_maintain_work:708`): it is reported by flash
  only (`_recurrence_conflict_chooser.py:600-618` asks nothing for a retained row), it keeps its old
  name if the same edit renamed the definition and reads the series as of its own due date (so a
  restatement dated after that day does not reach it), and **it holds its whole paycheck**
  (`OccurrenceClaims`), so the new rule's occurrence in that paycheck is suppressed for as long as
  the row stands -- the documented cost of REC-516, which ruled that only a rule DROPPING an
  occurrence it once named licenses a deletion.
- *(b) `occurs_on = due_date`* -- rejected on that ruling, with its benefit stated: when the new
  rule NAMES that date the row is adopted as the occurrence and maintained (`work.update`, no
  conflict); when it does not, the same edit HARD-DELETES a record-free Projected one-off
  (`:711-715`). On the 34, one row (2878, Projected) is reachable by either arm; the other 33 are
  immutable and skipped under both.

**F. Scope.** RULED (`R-BAL26`): a sibling step after this family. The ruling is stated for every
plan item; transfers already hold the shape in data (10.3). Whether the ad-hoc transfer door
(`routes/transfers/mutations.py:358`, 0 rows) and the twin's two unowned defects are leaves of this
family or a sibling step is the developer's call; the recommendation is a sibling step ranked after
this family, owning both, so this family's cutover stays one table wide (the twin's delete arm is
`X-bg`'s already). A one-off created in a non-baseline scenario mints a definition that carries no
scenario (definitions never have): one scenario exists and the selector is a stub (`base.html:149`),
so it is noted, not forked.

### 10.7 The decomposition, and where each finding closes

The suite builds a link-less row directly at **268 sites in 69 files** by a TEXTUAL walk (every
`Transaction(...)` in `tests/` naming neither `template_id`, `transfer_id` nor
`credit_payback_for_id`; 50 of them splat `**kwargs` the walk cannot see into, and builder fixtures
are uncounted; regenerate with a parenthesis-matching walk over the constructor calls, not a line
grep), which is why the CHECK cannot tighten first: the parked `X-bv-2` cut measured the analogous
shape -- template-linked rows hand-built undated -- at 1,452 red items (`0341a568`'s commit message,
on `fix/balance-x-bv-2`). Four leaves, each a session, and **the doors come BEFORE the fixtures**
because a test builder that mints a one-off has no producer to call until the doors exist -- the
first draft had them the other way round, which would have hand-built the shape `BAL-480` refused:

1. **`X-bi-7a` -- `recurs`, and every "no cadence" site reads it.** The one accessor on both the row
   and the definition; the eleven sites of 10.5's first class re-keyed (a measurement-day count; the
   leaf re-runs the read); a transaction twin of `propagate_to_unruled_template` so a rule-less
   definition's edit reaches its row; `count_discardable_items`' transaction arm load-then-counts
   (the transfer arm is `X-ci`'s, fork F). No production row changes behaviour (no rule-less
   transaction definition exists), and it is what makes a rule-less definition's row BEHAVE as a
   one-off before any exists. Closes the transaction half of defect 1 in 10.3; the transfer half is
   `X-ci`'s.
2. **`X-bi-7b` -- the doors.** ONE producer for a one-off (mint the rule-less definition, open its
   one-version series, place the row) that every writer in trace 1 calls (bank import's two per fork
   D); the popover's identity, flag and price edits route to the definition (forks A, C); the flag
   fields leave the three row schemas and `_apply_field_updates` stops writing them, so
   **`BAL-484`'s writer -- the crafted PATCH -- is gone** (the model setters stay for the fixtures
   until the fourth leaf drops the columns); the inline create dates the row (fork B); delete keys
   on `recurs` and refuses a standing merchant rule; `offerable_templates` is not filtered and a
   merchant answer mints its definition once and names it thereafter (`R-BAL24`, `X-f6c`).
   **The transitional window, stated**: from here until the fourth leaf, the legacy link-less rows
   still read their own cells with no writer left, so their flags are frozen, and the popover's
   identity edits on such a row keep a `template_id is None` branch the fourth leaf deletes.
3. **`X-bi-7c` -- the suite's one builder for a one-off, and the 268 sites moved onto it.**
   `one_off_row_of(...)` calls 7b's producer, as `X-cf-1`'s `generate_row_of`
   (`tests/_test_helpers.py:4162`) calls the engine. No app change.
4. **`X-bi-7d` -- the cutover.** The migration of trace 8; the model setters and both columns go;
   the accessors' cell-reading arm goes (the link test stays, answering False for a shadow or a
   payback); the CHECK reads `= 1` and both `SET NULL` link keys read `RESTRICT` (a nulled link is a
   zero-link row); every minted row records its date in `occurs_on` (`R-BAL25`); `_stated_amount`'s
   `template is None` refusal (`_definition_cash.py:159`) STAYS -- 7d-2's trace (2026-09-18) found
   it shared with the transfer pricing arm, and `budget.transfers.transfer_template_id` is still
   `SET NULL`, so its reason is restated to name the transfer key while the transaction arm's reach
   to it ends with RESTRICT (the label fallback once named here beside it, `grid_view_service:158`,
   is gone since 7b-2, `e27e5134`: `build_row_keys` has no label fallback). **Closes `BAL-484`** --
   the cell is gone. Its grade is EQUALITY of the grid across the cutover on the production restore
   (`R-BAL34` at 7b-2 separated the six cells the first draft graded as a DIFF), beside every
   migrated figure reproduced to the cent through amount rule 3. **Split 2026-09-18** into
   `X-bi-7d-1` (the suite's bare CHECK builders and the Core / raw-SQL writers 7c's constructor
   census could not see take a pricing link; tests only) and `X-bi-7d-2` (the migration; MOVES
   MONEY), because the fixtures must bind under `= 1` before the CHECK does.
   **`BAL-511` is decided here**: 7d-2's harness counts the grid render's statements across the
   cutover (lane 1's 2026-09-18 figure on the 12:34 restore: 23 before, 23 after, definitions 43 ->
   77, so the "multiplied" claim does not hold on the grid) and the row closes or re-points on that
   number at the tick.

`R-BAL20` as first drafted said the family's FIRST leaf owns `BAL-484`. The finding is
*a dead cell keeps a writer and no step deletes it*; the writer goes at 7b and the cell at 7d, so
the row is owned by 7d and its `also` names 7b -- stated here rather than obeyed silently, because
naming 7a would make rule 2 re-point the row twice for no reason. **RULED so** (developer,
2026-09-12), as was the family's rank: the four leaves take the ranks directly after `X-bv-2`,
blocked by `X-bv-2` and `X-cf`.

### 10.8 The migration's downgrade, exactly

**SUPERSEDED 2026-09-18 by `R-BAL67` (developer): the downgrade is SCHEMA-ONLY and folds nothing.**
It re-adds both flag columns (`false`), restores the CHECK to `<= 1` and both link keys to
`SET NULL`, and every row keeps its definition, its price and its date. The paragraph below was
written 2026-09-12, before `X-bi-7b-1` deployed; since 2026-09-14 the app mints exactly the one-row
rule-less shape for every new one-off (3 on the 2026-09-18 restore), so the fold's signature can no
longer tell this migration's 34 from the owner's and would fold theirs too. The pre-7d app reads and
writes the un-folded shape, so a rollback loses no behaviour and a re-upgrade mints 0. Kept as
written for the reason section 9 gives.

The upgrade's inverse, as first specified, folds a definition back onto its row: the row takes the
definition's two flags into its restored cells and, under fork A, a figure as its own (OWN,
`TEMPLATE` released) by `R-JC`'s two arms -- exact from `settled_amount` on the derived basis where
the row settled that way, else the definition's scalar; the link is cleared; the definition and its
series row are deleted. **Which definitions**: those with no rule and exactly one row --
soft-deleted rows counted, since a soft-deleted one-off is still the definition's (`occurs_on` is
that row's date since fork E was re-ruled, so it is no part of the signature).
**What it does NOT fold, stated**: a rule-less definition with ZERO rows (the "Does not repeat" form
shape, which existed before this migration and is not its work); a rule-less definition holding
several rows, which is a recurrence the owner CLEARED after it generated and a shape that existed
before this migration; and a definition a standing merchant rule names (the rule would cascade away
with it; such a definition is left linked, and the downgrade reports the count).
**Where it is WRONG, stated**: a cleared-recurrence definition whose only surviving row is a
carry-forward override (`_execute.py:665-683`, `occurs_on` NULL) matches the signature and would be
folded as a one-off; and under fork D's from-scratch collapse a rule-less definition holds one row
per paycheck and no one-row signature exists, so that ruling re-specifies this paragraph. The
restore holds none of these shapes (40 of 40 definitions carry a rule), so on the data measured the
downgrade is exact. The 26 dates the upgrade writes are NOT withdrawn on downgrade: a date is not a
copy of anything, `X-bv-2`'s CHECK no longer binds on a link-less row, and withdrawing one would
delete a fact the owner may since have corrected.

### 10.9 What two adversarial passes over this section refuted, 2026-09-12

Recorded rather than silently corrected, for the reason section 9 gives.
**Refuted and corrected above**: the status breakdown of the 34 (13 / 4 / 14 / 4 / 1 summed to 36;
it is 12 / 3 / 14 / 4 / 1); *20 of the 26 undated rows are settled* (8 are; 20 is Paid plus Credit,
a class nothing defines); *each of the 8 dated one-offs sits on its paycheck's start* (5 of 8); fork
A(a)'s *the Recurring page, the grid and the balance read the same figure after the edit*, true only
under a series rule the draft never stated and false under `set_amount` as it stands;
*the before/after grid is an equality* (two live double-displays make it a six-cell diff), and then
the six cells' composition (each definition's paycheck-4 row is Cancelled and displayed nowhere);
*the rollover needs a next canonical* (it falls through to CREATE); `mint_uncategorized` minting a
definition (its rows carry no category), and then
*a real Uncategorized category is the header's fallback made a row* (it is the opposite mechanism);
`X-au-i` as a live owner (withdrawn); the fixture leaf calling a producer the doors leaf had not
built, and then the doors leaf deleting model setters the fixtures still call;
*the twin already has* `recurs`'s delete behaviour (it soft-deletes on the link, N-386); fork E's
costs stated on one side only; a `1,452` quoted without its source and then as *the same shape*; the
merchant-rule CASCADE, picker and rule-flip surfaces missing from fork D, and then their regression
dated to bank_import's ruling rather than to this family's doors; `_context.py:433` / `:439` and
`accounts/crud.py:662-671` missing from the census; and seven line numbers.
**Attacked and NOT broken**: the 34 and every other breakdown in 10.2; the four link-less writers;
the transfer twin's two defects and transfer 409's state; the engine trace (retain / retire), the
route gate, `_stated_amount`'s refusal, the one-time transfer's birth; the three popover branches;
the CHECK and the FK; `X-bv-2`'s sentence; `c8f3a5d2e714`'s shape; fork A(a)'s series rule once
stated (`_version_on` corrects in place, `_states_something_new` is never reached, the scalar
resyncs, a moved due date changes nothing); B(a)'s balance-neutrality; the leaf order 7a before 7b;
10.8's fold rule; and every ruling cited for what it says.

### 10.10 The ruled design, in one place

Every plan item has exactly one definition. A one-off is a rule-less definition plus one row.

1. **Price** (fork A, `R-BAL21`): the definition's series prices the row through amount rule 3, and
   a one-off's series holds ONE version, dated on the row's due date; a restatement corrects the
   version the row's due date reads, in place, and never appends. The row states no figure.
2. **Date** (fork B, `R-BAL22`): a one-off is due on its placed paycheck's start unless the owner
   states a date; the popover keeps the date editable and never clearable; the cutover migration
   writes that date onto every undated link-less row. The developer accepted the cost stated in
   10.6-B.
3. **Home** (fork C, `R-BAL23`): the grid is the one-off's whole lifecycle -- created at the grid,
   its name, category, flags and price edited at the popover and landing on the definition, deleted
   with its definition (refused while a standing merchant rule names it), *make this repeat* opening
   the template edit form. The Recurring list shows definitions with a rule. The transaction
   template form's *Does not repeat* option is removed. The account-delete refusal counts
   definitions with a rule.
4. **Bank import** (fork D, `R-BAL24`, ruled against the recommendation and RE-RULED 2026-09-13 to
   `bank_import:X-f6c`'s shape -- a merchant answer mints one rule-less definition the first time
   and names it thereafter, `X-bi-7b` builds it and `X-f6c` is its alias, so a one-off is a
   definition plus its PLACED ROWS and 10.8 folds one-row definitions only):
   `transaction_templates.category_id` becomes NULLABLE so `mint_uncategorized` can mint a
   definition for a category-less row; `_create_envelope` mints a definition per envelope it
   creates; the merchant-rule picker is not filtered, so a one-off envelope is a pickable answer;
   N-327's cross-period identity is not re-designed here.
   **What that commits the doors leaf to, stated so nothing lands silently**: the nullable-category
   change is a schema-only migration of the doors leaf whose downgrade re-adds `NOT NULL` and
   REFUSES while a category-less definition exists (two shipped revisions already refuse theirs; the
   cutover leaf's downgrade folds such definitions first, so an orderly downgrade never meets the
   refusal); the cross-statement suggestion arm (`_placement.py:355`) is re-keyed from
   `template_id is None` to *a rule-less definition's row of that name and category in this period*,
   or N-327's cross-statement half regresses; and `rule_naming:646` keeps answering NEW-ENVELOPE for
   a destination whose definition has no rule, because today's rule on an ad-hoc envelope files that
   merchant into a fresh envelope in every later paycheck and a TEMPLATE answer naming a one-row
   definition would place nothing there -- the existing answer kinds keyed on the right predicate,
   not a new arm. **A rule the owner states BY PICKING a one-off definition from the unfiltered
   picker does name that one row and resolves UNRESOLVED in every other paycheck**; that is the cost
   the ruled option carries and it is bank_import's to revisit.
5. **Occurrence** (fork E, `R-BAL25`, RE-RULED 2026-09-13): the one-off's row records its own due
   date in `occurs_on`, so `recurrence:R19-b`'s NOT NULL binds on it; a one-off later made to repeat
   is adopted when the rule names its date and otherwise treated as any projected row of a rule that
   changed.
6. **Scope** (fork F, `R-BAL26`): transactions only; a sibling step ranked after this family, `X-ci`
   (minted 2026-09-13), closes the ad-hoc transfer door and owns the twin's two defects (10.3, items
   1 and 2: `BAL-492`, `BAL-493`).
7. **Owner and rank**: `BAL-484` is owned by `X-bi-7d` and by `X-bi-7b`, which deletes its writer
   (the ledger's `also` column holds finding relations, so the owner cell names both; ruled so
   2026-09-13); the four leaves take the ranks directly after `X-bv-2`.

### 10.11 What three reviews of the registry text refuted, 2026-09-13

Recorded for the reason 10.9 gives. The registry rows this section points at were reviewed by three
neutral passes on 2026-09-13 (against this section, against the code on dev `f93ce4df`, and over the
six fork rulings); every finding was fixed in the same commit, and these changed what this section
says. **Refuted and corrected above**: *eight forks* (10.6 holds six; the owner and the rank of 10.7
made eight rulings, never eight forks); the cross-period identity finding is **N-328**, not N-327,
and it was already RULED by bank_import on 2026-08-20 and owned by `bank_import:X-f6c`, a `NOW` step
this audit never saw, so fork D's *bank_import's to revisit* was false and the developer re-ruled D
to that step's shape with `X-bi-7b` as its alias (`R-BAL24`); fork E(a)'s retain branch is the one
`recurrence:R19-b` exists to delete, a step this audit never saw either, and its own spec named the
row's date as the answer, so E was re-ruled (b) (`R-BAL25`); the `= 1` CHECK is violable by
`credit_payback_for_id`'s `ON DELETE SET NULL` as well as `template_id`'s, so the cutover moves both
keys to RESTRICT; the account-delete refusal (trace 6) is not a `template_id IS NULL` site but a
count of definitions, re-keyed to recurring ones; the handoff's
*268 link-less constructions in 69 files* read 263 in 69 on this tree by AST and 264 in 70 on dev,
so the index carries a gate-run marker for the constructor total and no total for the link-less
subset; `X-cf-1` is archived, so the index cites `X-cf`; and
*`R-BAL20`'s text says the FIRST leaf owns `BAL-484`* described a draft the filed row does not
match. **Attacked and not broken**: the 34 and every breakdown in 10.2; the four link-less writers;
the twin's two defects and transfer 409; the engine trace; the three popover branches; the CHECK and
the FK; the six-cell diff's composition; 10.8's fold rule; and every ruling cited for what it says.
