# The cash balance architecture: the arc's argument

**This is the balance arc's ARGUMENT: the reasoning behind its steps, not the steps.** Each step,
finding and ruling is a card in the tracker, `saltyreformed-labs/shekel-plan`, read and written
through quill (`CLAUDE.md`, Development Status): an open step's specification is its card's body,
the order is the board's, and a shipped step's record is its commit. Sections 4, 5, 6 and 9 held
the rulings' pointer, the steps, the findings' pointer and this document's rules until the tracker
took them; the rest keep their numbers because the code cites them.

**What is already done is in `archive/`, indexed by `archive/README.md`. None of it governs
anything, and no live sentence here depends on one** -- an obligation a live step inherits is
stated in its card.

## 1. The problem, in plain words

The app answered "what is this cash account's balance at time T?" in three places, three ways, and
the ways disagreed. Underneath all three was ONE root cause, the cash form of the loan side's:
**the honest balance function was PARTIAL.** The projection started at the latest anchor and summed
only still-Projected rows forward, so it could not answer a past date, could not see a settled row,
and had to be composed with a seed, a flag or a fallback at every call site -- and every
composition was a new producer that could disagree with the others. Plan step X-c2b2 replaced all
three with ONE total fold and they closed together. The modelled-asset form of the same defect
closed at X-g2b. Both records are in the archive.

**What remains:**
1. **The write side and the read side are still two statements of one rule.** The posted account
   ledger is written by its own walk while the projection folds another; they are proven
   byte-identical, which is a test keeping two implementations in step rather than a structure that
   cannot drift. Plan step **X-d**.
2. **A derived value is read as a source of truth.** **Its named instance is GONE and the SHAPE is
   not** -- the number is kept because `app/` cites it (`cash_ledger/_walk.py`,
   `status_seam/` x3) and renumbering would break those. `Account.current_anchor_*` was a
   denormalized copy of the latest `AccountAnchorHistory` row that `resolve_anchor` detected
   diverging and only LOGGED; X-f1c3a re-pointed every reader at the resolver and X-f1c3c dropped
   both columns (2026-08-04, archived). What survives is the general rule the code cites at the
   posting walk -- **the posted ledger is a projection of the facts, never a second opinion about
   them**, which is root cause 1's other face -- plus the residue **X-e** owns.
3. **The pay calendar is a PARTIAL function -- the same shape, on the other axis.**
   `pay_period_service.get_all_periods` returns the materialized rows and stops, so past the last
   one every consumer improvises. Plan step **X-l**.
4. **The assertion RESETS the ledger instead of reconciling with it**, plugging the difference to
   Equity so real activity is invisible on the income statement. **Re-measured 2026-08-13 at
   `$15,413.71` gross, and the GROSS is the instrument's own path length**: the plug telescopes by
   construction, so the net is a function of the LAST assertion alone. Against the BANK the
   untracked spend is `$2,096.37` over the span. The steps are X-f3a..X-f3c (R-EB, amended
   R-FL..R-FN).
5. **The app can record when money moved and the recorded days are still guesses, and (4) is
   downstream of this.** `paid_at` was `db.func.now()` at the click; X-f1b replaced it with
   `transactions.settled_on` but the backfill was that derivation verbatim, so no stored date
   improved. **Measured against the developer's own bank export 2026-08-13: of 110 movements matched
   to bank lines on exact amount, only 33 (30%) carry the day the bank posted them.** Step **X-f6a**
   replaces the guess with the bank's own record.
6. **A surface still picks which producer answers for its account.** `/dashboard`'s hero, its pulse
   and the analytics calendar read the kind-blind cash view while `/grid` and `/savings` read the
   modelled one, so the same account renders two balances for the same period. Plan step **X-j**.
7. **The read pass pins a clock it does not hand to its loaders.** `BalanceContext` fixes the
   pass's `as_of` and `scenario` and memoizes three loan derivations; every other input is loaded
   ad hoc at the wall clock, which is both a redundancy and an impurity. Plan step **X-i**.

An open step named here is a card whose body specifies it, and a shipped one's record is its
commit and the archive; the order is the board's, and every open finding is a card too.

## 2. What is already shipped and correct (the foundation this plan builds on)

**This is the FOUNDATION the remaining steps stand on, not the log of how it was built** -- that is
the three as-built records.

**WHAT has shipped and which PR shipped it is `archive/README.md`'s index** -- the table that stood
here was a second copy of it (`CLAUDE.md` rule 14), and it carried R-DH (a)'s "an assertion is
the closing balance for its civil day", which ruling **R-FL** has since measured FALSE against the
bank. What belongs here is what no archive holds: the ORACLES a live commit is still graded
against.

**The loan baseline is still a LIVE regression gate for CASH commits.** Mortgage (account 3)
**$177,277.97**, Van Loan (account 8) **$15,663.59**. Re-derive both from the seam before and after
every commit in this phase: a cash change that moves a loan figure is wrong.

**Verified for cash, independently of the producers under test:** the fold reproduces the app's own
persisted double-entry ledger to the cent on both accounts carrying postings (Checking `$2,824.26`
over 177 postings, Money Market `$3,659.51` over 10), and R-K's grid identity holds on 360 of 360
real (account, period) pairs. Do not trust a prose figure older than its write date; pin oracles in
tests.

## 3. The solution

An account's balance is a fold over its event stream. The fold is TOTAL: it cannot return `None`,
cannot raise, and answers any date -- asked about a date before every event it answers the seed,
not an error. That single property deletes the partiality and everything built to manage it.

### 3.1 The cash fold, as shipped

```text
CashEvent = (instant, kind, payload)                    -- cash_ledger._events
kind = ASSERTION  balance := anchor_balance             (AccountAnchorHistory, EVERY row)
     | ACTUAL     balance += settled_cash_leg (signed)  (settled transaction rows)

walk_cash_ledger(account, scenario) = replay(events, seeded 0.00)  -- cash_ledger._walk
dated_deltas(walk) -> [(visible_civil_date, delta)]                -- the ONE clock
```

Three grains read that one fold: a scalar at a date, a per-period map, and a daily series. A
still-Projected item whose date has passed is CLAMPED FORWARD, never absorbed by an assertion
(R-G): its effective instant is `max(its attribution date, as_of + 1 day)`, because a plan cannot
have already happened. Before an account's first assertion the fold BACK-PROJECTS over the records
and holds flat before the earliest one (R-I / R-S).

**What "settled transaction rows" MEANS, and it is structural rather than a fence: a row is settled
if and only if it carries a settle day.** `status_seam.apply_status_change` is the single door that
writes `status_id`, and it writes the day in the same call. A `CHECK` cannot express it -- the
predicate lives in `ref.statuses` and a constraint cannot join -- and a reader finding a settled row
with no day FAILS LOUD, because silently dropping it from the fold is silent money loss. Established
at X-f1 and stated HERE rather than in that step's entry, because X-f2 and X-f3 read against it and
no live sentence depends on an archived one.

### 3.2 The modeled asset, as shipped

One sequential replay over four event kinds -- ASSERTION, ACTUAL, CONTRIBUTION, ACCRUAL -- serves
all five account kinds. An ASSERTION always wins (R-S). ACCRUAL is DAILY, computed at full
precision and credited in whole cents carrying the sub-cent remainder (R-T / R-X), and runs from
the latest assertion's own day forward, including inside the period that holds it (R-L / R-Y). A
CONTRIBUTION lands on its pay period's `start_date` and exists only when that payday is strictly
after the latest assertion (R-Z).

### 3.3 The anchor half, as DESIGNED (ruling R-EB, amended by R-FL..R-FN 2026-08-13)

`balance(T)` becomes `opening equity + SUM(postings <= T)`. An assertion becomes a RECONCILIATION --
a recorded observation the outstanding set is measured against -- rather than a reset that discards
what the records say, and an unexplained difference becomes a recorded, categorizable transaction.

**Four facts carry it, and the second is the one the app does not have** (R-FL):

| # | the fact | today | the design |
|---|---|---|---|
| 1 | when the money moved | `transactions.settled_on` | unchanged |
| 2 | has this line CLEARED the bank | GUESSED: `settled_on <= the latest assertion's day` (`ReconciledThrough.covers`) | RECORDED: the row names the assertion that saw it |
| 3 | a cleared purchase's cash | leaves the book only when its ENVELOPE closes | posts when the purchase clears (R-FM) |
| 4 | an unexplained difference | an automatic plug to `anchor_equity`, invisible | derived, displayed, and RECORDED on an explicit act (R-FN) |

**Fact 2 is why the reset cannot simply be deleted, and ruling R-FL carries the measurement that
says so.** The steps that remain are cards in the tracker, and their order is the board's.

### 3.4 The amount model (ruling R-FI)

**What the amount model is about, in one sentence: the app stores five amounts it also computes,
and each one grew its own private repair mechanism.** Salary income is repaired at READ time by
`income_service.live_projected_net`, which writes nothing back (**N-224**); a loan-payment shadow by
`cash_ledger.LoanPricing.live_cash`, which also read the wall clock until `X-au-g-2b` (**N-40**); a
transfer shadow by a two-line copy in `transfer_service.update_transfer` beside a drift corrector
that logs `"Correcting shadow %d estimated_amount drift"`; an ordinary template row by
`regenerate_for_template`, which DELETES and recreates every non-override row from a date the form
supplies and never stores. `credit_card:CC4b` planned a fifth (re-minted 2026-09-18 as `CC-6`, which prices through the resolver). **R-FI deletes the class**: a derived
amount is not stored, one total resolver answers, and the CHECK makes a stale derived figure
unrepresentable rather than merely unlikely.

**Measured on production 2026-08-11**, so the size of each cutover is known rather than estimated:
**997 rows, and the buckets RECONCILE to it** because a set of measurements that does not is a
census nobody can check: 51 projected non-overridden salary rows (`$139,642.27`) + 511 non-override
template rows + 342 transfer shadows + 49 ad-hoc rows + 37 override rows + 7 already-settled salary
rows = 997. **Two of those cells were re-measured at X-au-b and are stated more honestly here.** The
shadow and override cells depend on which bucket claims the 12 rows that are BOTH: counting them as
overrides gives 330 and 49 instead of 342 and 37, and both readings reconcile to 997 -- which is why
the resolver's dispatch ORDER is a rule rather than a convenience. And the 49 ad-hoc rows do NOT all
"own their amount and always will", as the first draft of this sentence said: **21 of them are CC
paybacks**, whose figure is derived from the purchases they repay and stored with no reconciler.
That is the kind carrying NEITHER link, which is why R-FI's discriminator is declared rather than
read off the columns. **That kind is no longer cut over here** -- X-au-i is withdrawn to the card
arc, which deletes the payback outright. `template_id` and `transfer_id` are mutually
exclusive across every row -- STRUCTURALLY since X-au-c1's `ck_transactions_one_pricing_link`,
which widened the convention to the third link and was measured at 0 violations -- but the five rules
are NOT a partition over those two columns (ruling R-FI). Stored-vs-derived drift TODAY is **0 rows /
`$0.00`**, re-measured 2026-08-12 by X-au-b's exhaustive oracle -- which the planning
conventions' rule 8 said is not resolved (a finding costing `$0.00` on today's data is a defect
waiting for the data to change), and which the read-time repair is what hides.

**AND THE DISCRIMINATOR ITSELF IS RULED OUT, at `X-au-k` on 2026-09-02 (`R-IY`, `R-IZ`).**
`amount_source_id` is a STORED DISCRIMINATOR over an EXCLUSIVE ARC whose legs the row already
carries -- `template_id`, `transfer_id`, `credit_payback_for_id`, which
`ck_transactions_one_pricing_link` already makes exclusive. It is the shape this codebase builds
everywhere else (`statement_match.py`, `recurrence_rule.py`, `template_amount_version.py`, and
`X-ai-s` converting `journal_entries`), and in all of them the discriminator is DERIVED from which
leg is set. Storing it buys a ref table, a reverse map rebuilt per dispatch, and a state where the
two disagree -- **N-440**, which `budget.transfers` forbids and `budget.transactions` does not.

**What made it look necessary is that the ARC IS INCOMPLETE**: `credit_payback_for_id` has no
`AmountSourceEnum` member and **N-264**'s finance charge would carry no link at all. N-264 had two
readings -- a reason to keep the column, or a missing leg -- and **the developer ruled the second**:
the finance charge is a row of a card-owned definition carrying `template_id` from birth (`credit_card:CC-8`; `CC4d` dissolved at the 2026-09-18 re-mint), `X-au-l` DELETES the column. That is the END STATE; the amount model's cutovers,
which created the population it must be true across, have all shipped (`X-au-d` .. `X-au-g`).

## 7. Verification standard

**Moved to `../../plans/verification.md`**, one copy for every arc. This arc WROTE it and is why
several of its clauses exist -- three of the five defects behind the suite's two clock gates were
fixture-clock bugs created here, and this arc writes more anchors, assertion instants and due dates
than anything else in the codebase.

## 8. Process lessons

**Moved to `../../plans/lessons.md`**, one copy for every arc.
