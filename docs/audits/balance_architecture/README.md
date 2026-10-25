# The cash balance architecture: the plan of record

**This is the ONLY live document for the balance arc, and it carries the work that REMAINS.**
Amendments are edits HERE, a shipped step gets its checkbox ticked with its commit hash HERE, and no
new planning documents get written for this arc. **The rules are `../../plans/conventions.md`** and
`tools/plan_gate/` grades this file against them.

**What is already done is in `archive/`, indexed by `archive/README.md`. None of it governs
anything, and no live sentence here depends on one** (rule 5) -- an obligation a live step inherits
is stated at that step.

## Where the arc stands

**A signpost, not a log**, and `../../plans/conventions.md` rule 6 is what it is held to: replaced
each session, capped, and carrying no volatile value -- branch state, what production runs and the
migration head are MEASUREMENTS, named by their command rather than copied.

| | | detail |
|---|---|---|
| **just landed** | **X-f3c-2b-2c `2bcf2da5` -- THE ACCOUNT-10 REPAIR, PERFORMED ON PRODUCTION 2026-10-12 THROUGH THE APP'S OWN DOORS, AS REHEARSED** (**R-HJ**, **R-BAL3**), and its code leaf L1 `d6285573`: books and bank are compared on a day only where both records cover it (**R-BAL285**, **R-BAL286**, **R-BAL291**). Earlier landings are Section 5's ticked entries. | Section 5, X-f3c-2b-2c; ../../plans/historical/balance_x_f3c_2b_2c_as_performed_2026-10-12.md |
| **in flight** | **X-bi-6-4d-2**, 6-4d's own release in ONE PR (**R-BAL166**), which holds what was `X-bi-6-4d-3` (**R-BAL361**). **X-f3c-2b-2c** (the account-10 repair) has left this row: performed on production 2026-10-12 through the app's own doors and SHIPPED at `2bcf2da5`, its record `../../plans/historical/balance_x_f3c_2b_2c_as_performed_2026-10-12.md`. **X-f3c-2b-3** was MINTED by X-f3c-3 and sequenced behind THE FLIP: nothing bounds an assertion at its account's `opened_on` (**N-400**), and after X-f3c-5 an assertion stops resetting a PLAIN account, so what the bound should refuse is decided against what an assertion then IS. It legalises nothing -- zero rows sit below their books on either database. Read branch state from `git branch -vv` and the deployed revision from `docker inspect shekel-prod-app`; what to pick up next is `../../plans/steps.md`'s first row | Section 5, X-bi-6-4d / X-f3c-2b-2c / X-f3c-2b-3 |
| **what changed the plan** | **Every plan item has exactly one definition (R-BAL20, 2026-09-12), and `X-bi-7` is the family that builds it**: a one-off becomes a rule-less definition plus one placed row, both flag cells leave `budget.transactions`, and `X-bi-5` was re-pointed at the DEFINITION's `is_envelope` (DISSOLVED 2026-09-20, **R-BAL85**: the kind stays). Ruled when the lane recommended a CHECK on a dead cell and the developer refused the premise. Four leaves (`recurs`, the doors, the fixtures, the cutover) and a transfer sibling `X-ci`, ranked in `../../plans/steps.md`; the argument and the ten traces are `../../design/from_scratch_architecture.md` section 10, the six fork rulings **R-BAL21** to **R-BAL26**. The cutover writes 26 due dates nobody stated; **R-BAL22** rules that it may, the question trace 8 asks of **R-HJ**, cost accepted | Section 5, X-bi-7 / X-ci; Section 4, balance:R-BAL20 to R-BAL26 |
| **blocked on you** | **The first SECU import LANDED 2026-09-15** (on the 2026-09-19 07:21 restore: 1 statement import, 306 bank lines, 163 matches; the observation records are the coordinator's `N-368-observation-2026-09-15.md` / `-18.md`, their rows BAL-497 / BAL-498 (closed at `X-f3c-2b-2c`) / BI-499 / BI-500). That first import was **N-368**'s, and it alone closed THE FLIP's data gate (`56111c3e`); two more CSV imports since, made 2026-09-20 and 2026-09-22 (all three dates EDT), extend Checking's declared windows to cover 2026-01-02 through 2026-09-21 (measured read-only on production 2026-09-24), and none of it waits on a bank feed. What gates this arc now is the developer's file-or-not BATCH the coordinator holds; this document's room is a MEASUREMENT, `wc -l` against the gate's cap and its 20-line headroom, and a tick archives a completed span only when that room runs out (rule 5). Everything else this arc owes is a `developer-decision` / `operator` row in `ledger.md`; what to do next is `../../plans/steps.md`'s first row, never this section | ledger.md BAL-497; ../../plans/steps.md |
| **complementary arcs** | TWO, neither part of this arc and neither pausing it: the recurrence redesign (block 9) and the pay calendar (block 10). **The pay calendar's `C2` IS this arc's `X-l`**, and also recurrence `R-F12` -- one commit under three names, so whoever builds it must satisfy all three specifications | `implementation_plan_recurrence_redesign.md`, `implementation_plan_pay_calendar.md` |
| **the live lesson** | **A test that passes for the wrong reason is indistinguishable from one that works, and only a mutation separates them.** X-f3c-3's `unchecked` control asserted the right verdict for the wrong term -- its one compared day had no row against its bank line, so `disagreeing` was 1 and the mutation deleting `unchecked` SURVIVED. The same pass caught a page assertion that measured nothing (every money string it looked for was published independently by the balance-history card on that same page, so it passed with the new card's whole figure block deleted) and a query that manufactured its own confirmation (summing BOTH legs of the posted ledger and printing `$0.00`, because both ledger accounts carry the same `account_id`). Three instruments, three green answers, none of them measuring its subject | Section 5, X-f3c-3 |
| **resuming cold** | Branch from `dev`; whether it leads `main` is a MEASUREMENT (`git log --oneline origin/main..dev`). Read the repo's migration head from `alembic_version` rather than from prose, and and never rebuild the test template by hand -- since `X-br-4` the wrapper bakes it into an image and re-verifies that image on every invocation. **Nothing has to be passed when another checkout is running the suite**: each run gets its own throwaway cluster, so two suites cannot meet (the `TEST_DB_PREFIX` this row used to require is deleted). Put the venv on `PATH` (`scripts/test.sh` runs `pytest` as a backgrounded CHILD, so it needs one) and keep it ACTIVE for `git commit` (pre-commit hooks are `language: system`). The registries have their own gate: `pytest tools/plan_gate -c /dev/null -q`. Two REFERENCE tags, neither a rebase candidate: `xd-attempt-1-parked-n155` (X-d) and `xx-attempt-1-held-rde` (X-x) | `../../plans/verification.md` |

Section 5 is the work that remains and Section 4 the rulings that govern it; `archive/` is what
already shipped. **Every open defect is a row in `../../plans/ledger.md` whose `arc` reads
`balance`**, and every step is indexed in `../../plans/steps.md` -- both moved out on 2026-08-09
because a finding is not arc-local.

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

Section 5 specifies each step and 5.0 says why its block sits where it does; the EXECUTION ORDER is
`../../plans/steps.md` and who owns each open finding is `../../plans/ledger.md`, with no row
unowned.

## 2. What is already shipped and correct (the foundation this plan builds on)

**This is the FOUNDATION the remaining steps stand on, not the log of how it was built** -- that is
the three as-built records.

**WHAT has shipped and which PR shipped it is `archive/README.md`'s index** -- the table that stood
here was a second copy of it (rule 16), and it carried R-DH (a)'s "an assertion is the closing
balance for its civil day", which ruling **R-FL** has since measured FALSE against the bank. What
belongs here is what no archive holds: the ORACLES a live commit is still graded against.

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
rule 5 forbids a live sentence depending on an archived one.

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
says so.** The steps are section 5; their sequence is `../../plans/steps.md`'s.

## 4. The rulings

**This arc's rulings are in `../../plans/rulings.md`, rows whose `arc` is `balance`.** The key is
`(arc, id)` and no arc document states a ruling; cite one as `balance:R-xx` wherever the bare id
could be another arc's (`../../plans/conventions.md` rules 9 and 10).

**Where a live step needs more than the rule, its Section 5 entry restates it inline** (rule 5).
That is unchanged, and it is why this section is a pointer rather than a deletion: a specification
may depend on a ruling, and the ruling now resolves by key.

## 5. The steps

### 5.0 Why each block sits where it does

**The ORDER is `../../plans/steps.md` and only there.** This section is the arc's RATIONALE: the
fact that forces each block's position, in this arc's own terms. **The block numbers are NAMES, not
ranks** -- four documents cite them -- and this section states no sequence, no member steps and no
readiness. Every copy of those ever kept here drifted: it listed a shipped step as pending, and it
carried an ordering claim three other documents called unsatisfiable. **The phase headings below
group the same work by SUBJECT; this groups it by CAUSE.**

| # | block | the fact that forces its position |
|---|---|---|
| 1 | **the anchor half** | The only remaining work that moves a figure the developer reads, and since 2026-08-13 it ABSORBS block 5: the cutover's residual is meaningless until clearing is a fact and the dates are the bank's, so the importer's first leaf runs inside this block rather than after the card arc |
| 2 | **the pay-calendar door** | Its gate CLEARED 2026-08-05 when the X-f1 cluster reached production (`8d812662`): `accounts.current_anchor_period_id` is GONE rather than going, which is the fact X-ad's trace turned on. The "ONE PR" pairing with X-x ENDED at **R-EY** -- X-x is held behind the writer (**R-DE**) and behind the pay-calendar arc's `C3`, which owns the repair its refusals point at |
| 3 | **the posting restructure** | `X-ai-s` is held until the cutover, which deletes the correction family it would buy attribution for; `X-d` is PARKED on **N-155**, whose fix is X-ai's own placement; `X-bn` closed **N-193**, a reproducible unhandled 500 on a money route split out of `X-ak` 2026-09-03, at `cdc67086` |
| 4 | **the credit-card arc** (own document) | DISCHARGED 2026-09-18 by the per-leaf trace (`credit_card:R-CC13`): `CC1b` dissolved (`R-CC14`) and `CC3b` re-specified on the movement shape (`R-CC15`); what each card leaf waits on is `steps.md`'s cell. Was: `CC1b`'s fold against the reset semantics **R-EB** deletes at the cutover, and `CC3b`'s settle from `paid_at`, which X-f1b deleted |
| 5 | **the bank import** | ABSORBED INTO BLOCK 1 on 2026-08-13. Its 2026-08-03 position -- after the card arc, so one matching rule covers checking and card rows -- rested on the cutover not needing it; measurement refuted that, and the developer's exports carry both accounts anyway, so the one-rule argument survives inside block 1 |
| 6 | **the read-path residue** | Nothing blocks on it and its footprint is disjoint from the write path: tag `xd-attempt-1-parked-n155`'s 30 `app/` files against tag `xx-attempt-1-held-rde`'s 26, **zero overlap**, measured. UNGATED, which is what lets `X-l` run early -- and it must, because it is block 10's `C2` and recurrence `R-F12` as well |
| 7 | **the gate and vocabulary residue** | Shares files with nothing; interleaves anywhere |
| 8 | **E2 and G** | `E2` moves the very modules `G1`'s two allowlists name, so `G1` cuts that boundary once, inside `E2`, rather than re-cutting it after (**R-DQ**, amended by **R-BAL251**); `G2` depends on nothing the move decides (**R-BAL247**) |
| 9 | **the recurrence redesign** (own document) -- **NOT part of this arc** | Overlaps every live block of this arc by ONE file (`_recurrence_common.py`, measured against the `xd-attempt-1-parked-n155` tag), so it pauses nothing here. Its own document holds its argument; how its steps interleave with this arc's is `steps.md`'s answer |
| 10 | **the pay calendar** (own document) -- **NOT part of this arc** | Opened 2026-08-08 out of the recurrence arc's F-10: `budget.pay_periods` stores the payday and DERIVES `end_date` / `period_index`, so a gap, an overlap and an index out of date order all stop being expressible. **Its `C2` IS this arc's `X-l`**, and also recurrence `R-F12` -- one commit under three names, and whoever builds it must satisfy all three specifications, including this arc's N-82 / N-128 / N-79-far |
| 11 | **the amount model** (ruling **R-FI**) | It is the ROOT CAUSE of block 1's last open finding and of three mechanisms nothing else will delete, so it precedes every step that reads a projected figure. Its first two leaves touch no file block 1 does, which is why they run beside the anchor half rather than after it |

**Blocks 9 and 10 are SEPARATE ARCS and blocks 4 and 5 are FEATURES consuming this arc's output.**
None is a correctness fix inside this arc, and **none owns a `ledger.md` row whose `arc` reads
`balance`, nor ever will**. Their documents are `implementation_plan_recurrence_redesign.md`,
`implementation_plan_pay_calendar.md`, `implementation_plan_credit_card.md` and
`implementation_plan_bank_import.md`, all under `docs/plans/`. **The card plan's
2026-07-19 sequencing was DISCHARGED and is now archived to
`../../plans/historical/credit_card_sequencing_2026-07-19.md`**: every balance-arc step it named has
shipped (old `X4` survives here as X-e). The card arc's leaves wait per `steps.md` since 2026-09-18
(block 4, discharged).

**Step ids are append-only, and a DECOMPOSED parent ticks with the last of its leaves** --
`../../plans/conventions.md` rules 2 and 10, stated once there and graded there.

**Shipped steps are one line each in `archive/phase_x_as_built_2026-08-04.md`** (Section 1 through
X-f1b, Section 1a for the X-f1 cluster) and are not repeated here. X-ae, X-af and X-aj1 left this
section on 2026-08-11 as copies of rows the archive already carried; the one live fact among them,
X-aj1 leaving `transfer_service.py` at 987 of 1000, was **N-152**'s, closed 2026-10-11 by that module's package split (`a61a6d19f`).

### Phase X -- the anchor half (ruling R-EB; runs FIRST)

- [ ] **X-f** `feat(transactions): the app records when money moved` -- the DECOMPOSED parent,
  carrying **N-42**, redesigned 2026-08-03 (R-EB) and re-decomposed 2026-08-13 (R-FL..R-FO). **It
  ticks with X-f4**, not the import: a leaf is derived WITHIN an arc (`_classes.py:78`).

* [x] **X-f1** `8d812662` a settle carries the day the money moved; absorbs **S2-b**; fourteen leaves in `archive/…2026-08-04.md` 1a. Did NOT close **N-173** (**X-f6a**'s).

* [ ] **X-ax** `fix(carry-forward): a rollover is reconciled against the source it divided` --
  closes **N-249**. **MOVES MONEY.** Carry-forward splits one budget across two periods: it settles
  the source at `sum(entries)` and rolls `estimated - sum(entries)` into the next period's row. The
  split is irreversible and the source is left **Paid, not archived**, so `entry_service` still
  accepts purchases against it and `_resync_settled_envelope` re-derives its actual -- at which point
  the two halves no longer sum to the budget they divided. Live shape: row 2391 settled `$0.00` on
  2026-06-03 with `$157.60` folded into row 2392 (`$257.60 = $100 + $157.60`); one `$30.00` purchase
  against 2391 leaves the projection `$30.00` richer than reality. The other direction covers **8
  rows / `$794.79`** settled at their estimate, any of which a single late `$5` purchase drops to
  `$5`. **The developer ruled the remedy 2026-08-12**: reconcile, do not refuse -- when a settled
  source's spend changes, the target's top-up moves by the same amount. **Its first act is a trace,
  not code**: the top-up is not identified on the target row today (it is folded into
  `estimated_amount` with `is_override = True`), so the step must first rule how a rollover is
  RECORDED before it can be reconciled -- and that recording is a derived quantity, which is
  **R-FI**'s subject, so the design belongs beside the amount model rather than beneath it.
* [ ] **X-f3** `feat(cash): the ledger is sum-of-postings` -- the DECOMPOSED parent of THE CUTOVER,
  split 2026-08-13 (**R-FL**..**R-FO**, Section 3.3) when measurement refuted its one-commit form
  twice over. It ticks with X-f3c; **X-f5 is SUPERSEDED**, its outcome being what the cutover
  produces by construction.
  * [ ] **X-f3a** the DECOMPOSED parent of "clearing is a recorded fact", split 2026-08-14 (**R-FQ**).
    Carries **N-273**.
  * [ ] **X-f3a-2** `feat(reconcile): a walked statement's silence is a fact` -- record that a
    statement was walked LINE BY LINE, so a line it did not show is NOT CLEARED rather than unknown,
    and widen the offer set to every uncleared line. **MOVES MONEY.** Closes **N-273**. **It follows
    X-f3c and R-FQ's theorem is why**: while an assertion RESETS the ledger a line no assertion
    clears reads as `anchor + X` at every later date (**N-285**).
  * [ ] **X-f3c** the DECOMPOSED parent of THE CUTOVER, re-decomposed 2026-08-27 (**R-GW**, with
    **R-GX** and **R-GY**, the two rulings its first draft owed). Carries **N-172**, **N-174**.
    * [ ] **X-f3c-2b** the DECOMPOSED parent of the books boundary (**R-HG**), split 2026-08-28,
      re-cut the same day (**R-HJ**) and again 2026-08-31 (**R-ID**): the invariant is one commit,
      and the DOOR -- with the repair it is the only honest route for -- is its own three.
      * [x] **X-f3c-2b-2** `2bcf2da5` -- the DECOMPOSED parent of the DOOR (**R-ID**): the door, the bank line an import may not offer against it, and the account-10 repair the door exists for; ticked with that last leaf, which closed **N-379** and **N-382**.
        * [x] **X-f3c-2b-2c** `2bcf2da5` -- the account-10 repair, PERFORMED on production 2026-10-12 through the app's own doors (**R-HJ**, **R-BAL3**) from `account_10_repair_runbook.md` as rehearsed that day, its POST block four times AS REHEARSED: record `../../plans/historical/balance_x_f3c_2b_2c_as_performed_2026-10-12.md`, its evidence named there by path only (**R-BAL132**). The runbook this entry used to call SUPERSEDED was rewritten at L2b `f6bcd406` for **R-BAL249**, **R-BAL250**, **R-BAL255** and **R-BAL256**, on L2a's harness `fcfaf7bc`, and given its read-only check at L2c through `f3bbc6c5` (**R-BAL262**); its STATUS line now says it was performed.
          L1 `d6285573` compares books and bank on a day only where BOTH records cover it (**R-BAL285**, re-heard as **R-BAL291**; **R-BAL286**): the books' balance from their opening day and their movement from the day after (`app/utils/books_boundary.py`), the bank inside an imported statement's window (`bank_agreement.covered_pieces`).
          Closed by the act: **BAL-468** (act 7, account_10_repair_runbook.md:357-368), **N-275** (act 4, :314-331; the 2026-03-27 assertion's disagreement with SECU passes, per the runbook's :494-497, to X-bk-2's reconcile), **N-379** (its books' opening by act 3, :303-313; its April transfers' days by acts 5 and 8, :332-345 and :369-398) and **N-382** (act 1 cancels the copy pointing at the archived twin, :247-271), each graded by the rehearsal's check (:485-527) and the POST block AS REHEARSED (:528-567); **BAL-498** by the code: the one governing order is `GOVERNING_ORDER_SQL = "id DESC"` (`app/opening_infrastructure/_base.py:125`), read as `AccountOpening.id.desc()` (`app/services/cash_ledger/_events.py:494-496`), so the row act 4 appended governs and no tie-break decides.
          Closed by L1: **BAL-616**, whose "can never reconcile however the reconcile acts" overclaimed: on the parent the bank's line was shown but never counted or flagged, and a quiet books day was compared only once something was recorded on or before it; and **BAL-650**, never a row, born and closed here: compared = both records cover the day (**R-BAL291**), one predicate, built in L1. **BAL-467** carried to X-bk-2 and **N-397** to X-dc. Filed **BAL-621**, **BAL-651** and **BAL-655** (X-bk-2), **BAL-622**, **BAL-656** and **BAL-657** (X-eq).
      * [ ] **X-f3c-2b-3** `feat(cash): a balance is not declared for a day the books
        did not exist on` -- the ASSERTION side of the books boundary, which is
        the one door **X-f3c-2b-1** left unbounded: `resolve_observation_day`
        floors `observed_on` at `earliest_recordable_day` and at today, never
        at `opened_on`, so a true-up can be back-dated below the books it
        belongs to (**N-400**). Refused at both write doors and by a deferrable
        constraint trigger, the shape X-f3c-2b-1 already gave the movement
        side. **It legalises NOTHING and the bound is why**: STRICTLY before
        `opened_on`, because `account_service.create_account` writes the
        opening and the origination assertion on ONE day from ONE figure -- so
        four production accounts sit exactly ON their opening day and zero sit
        below it (measured 2026-09-01 at X-f3c-3). **Sequenced behind the FLIP**
        (conventions rule 8): after X-f3c-5 an assertion stops resetting a
        PLAIN account, so one dated below the books moves no money there and
        the remedy is decided against what an assertion then IS. Closes
        **N-400**.
    * [ ] **X-f3c-4** `feat(cash): an unexplained difference is a transaction you accept` --
      **MOVES MONEY.** The explicit act **R-FN** requires, under **R-GY**'s three bounds: dated on
      the latest assertion's own day so that assertion clears it and the balance line does not move
      (R-DH (a)); amount DERIVED from the postings OTHER than itself until the owner categorises
      it; offered only where a `statement_imports` row covers the span at a zero residue -- which
      X-f3c-3 now answers as `outstanding_difference.SpanAgreement`, whose `imported` count is bounded by the window an import DECLARES (**R-BAL71**, `bank_import:X-f6b-1`; N-434 closed there). The
      posted ledger, the Book-vs-bank row and the income statement all move. Closes **N-171** --
      a figure this step must RE-DERIVE rather than quote, because the plug is the net of every
      correction and moves with each one: `$1,776.88` when this entry was written and `$2,370.02`
      on the dev clone at 2026-09-01, which `balance_at.cash_outstanding_difference` answers --
      and flips **PLAIN's arm of X-f3d's dispatch** (R-FO).
    * [ ] **X-f3c-5** `feat(cash): an assertion is a check, not a reset` -- **THE FLIP. MOVES MONEY.
      OWN PR, NO BACKLOG.** `balance(T)` becomes `opening equity + SUM(postings <= T)`, PLAIN stops
      booking `account_trueup`, the reconcile reverses the 162 entries that stand, and what a
      clearing link SURVIVES is settled (**N-307**). **Ship-gated on R-DH (c) in both orders
      without a true-up** and on the import **N-368** names, an operator act no `blocked by` cell can hold. Closes **N-172**, **N-174**, **N-285**,
      **N-290**, **N-307**, **N-308**. Integrity check BA-01, `owner_declared_clause`'s raw-SQL second spelling, goes with it (**R-BAL263**).
* [ ] **X-ay** `feat(ledger): a modelled account's own inflows are postings` -- closes **N-277**.
  A modelled account's true-up residual is what it EARNED only where the app models no inflow it
  fails to post, and it fails to post all of them: `ref.posting_sources` holds no accrual and no
  contribution kind, so an employer match and a payroll deduction reach the balance through the
  assertion alone. Measured 2026-08-14: account 6 grew `x1.15448` where both IRAs, modelled at the
  same return, grew `x1.11084` -- **`$1,157.16`** X-f3d must call a change in value, not a gain.
* [ ] **X-f4** `refactor(cash): delete what the cutover orphans` -- `ReconciledThrough`'s COVERAGE
  rule and its cash consumers, `account_posting_service/_anchors.py`, the correction machinery, the
  R-I seed compensator. **Its deletion set is NARROWER than it was**: X-f3a-1 re-pointed the cash
  consumers, so what dies here is the DATE ARM of `StatementCoverage` plus whatever of
  `ReconciledThrough` its two surviving callers -- the modelled contribution feed (R-Z) and the
  posted self-heal's date-keyed cost guard -- do not need. Byte-identical by construction, and it must **name the
  residue arm it deletes** rather than letting it go unnoticed -- that branch has fired in production
  (**N-176**). Closes **N-176**, takes **N-161** with its family, and **N-218 closed** (`7a466d31`);
  **N-169 closed already** at `C2-d` (`3e6cd4ec`). **GATES the recurrence redesign's `R5`**, which
  edits `cash_ledger/_events.py` inside this deletion set -- a column rename in a file this step
  deletes from is avoided by ordering rather than by merge. The account-anchor deploy hook (`scripts/init_database.py`'s `backfill_all_account_anchor_postings_after_migration`, calling `account_posting_service.backfill_all_account_anchor_postings`) is measured here and deleted if it can no longer fire (**R-BAL263**); it also closes **N-208** and carries **N-192**: it deletes the raise's cash caller, `account_posting_service/_anchors.py`, while the loan caller, `loan_posting_service/_anchors.py:234`, remains, so the row is re-measured at its tick (**R-BAL269**).
* [ ] **X-bj** `feat(cash): a level is an observation, never a reset` -- the DECOMPOSED parent of
  the level relation (**R-IS**), split 2026-09-03 by ruling **R-JN**: as written it claimed *a level
  never moves a balance* eight ranks before `X-f3c-5` stops an assertion resetting a plain account.
  It ticks with its second leaf; it subsumed `bank_import:X-gh` and took `X-f6g`'s coverage pass.
  * [x] **X-bj-1** `82b534f6` -- the level relation (**R-BAL55**): 1a `1d1f7ce8` built it (**R-BAL47**..**R-BAL57**; record `archive/x_bj_1a_as_built_2026-09-16.md`), 1b chose within the run; ticked with 1b.
    * [x] **X-bj-1b** `82b534f6` -- the bank walk anchors PER RUN, every other standing bank level a CHECKPOINT (**R-BAL63**..**R-BAL66**); 0 leaves moved on production's shape; closed **N-343**, opened **BAL-512**. Record `archive/x_bj_1b_as_built_2026-09-18.md`.
  * [ ] **X-bj-2** `feat(cash): a level is an observation, never a reset` -- the second half, riding
    **X-f3c-5** (**R-JN**): once the flip stops an assertion resetting a plain account, a level moves no
    balance and yields `discrepancy = observed - computed`, zero being healthy. It closes **N-314**: the
    bank's daily closings are OBSERVATIONS, neither assertions that reset nor restatements.
* [ ] **X-bi** the DECOMPOSED parent of the MOVEMENT UNIFICATION (**R-IT**), split five ways by the
  adversarial review of 2026-09-01 that refuted its first cut. **Every plan item is satisfied by
  zero or more MOVEMENTS**: a bill is the one-movement case, an envelope the many-movement case,
  `budget.transaction_entries` becomes the general movement table and the fold reads movements only.
  The argument, the measured cost and the twelve refuted claims behind it are
  `../../design/from_scratch_architecture.md`. **The root cause it removes** is that one table holds
  a PLAN and a RECORD with a status column pretending one becomes the other -- 161 of 238 fold facts
  on Checking are plan rows whose status was flipped, and the movement record is `$4,270.78` from
  the bank over 155 days.
  * [x] **X-bi-7** `829c2c26` -- the ONE-DEFINITION shape (**R-BAL20**..**R-BAL26**; ticked with 7d-2): every plan item has exactly one definition; argument `../../design/from_scratch_architecture.md` section 10.
    * [x] **X-bi-7b** `321bf2e4` -- ONE producer for a one-off and every link-less writer on it (**R-BAL31**, **R-BAL24** with `bank_import:X-f6c`); ticked with 7b-3.
    * [x] **X-bi-7d** `829c2c26` -- the cutover, 7d-1 `08230752` + 7d-2 `829c2c26` (the migration **MOVED MONEY**: 34 definitions, 26 rows dated, **R-BAL25** / **R-BAL22**). Records, 7c's too: `archive/shipped_steps_archived_2026-09-18b.md`.
  * **X-bi-2 is DISSOLVED** (**R-BAL35**, 2026-09-15): a movement's category, type and scenario are
    its plan row's, read through `transaction_id` and never copied; its fourth column already existed.
  * [x] **X-bi-3** `d1e3f7a6` -- the DECOMPOSED parent of the COVERING MOVEMENT (**R-BAL41**); five leaves, only 3d moved money. Closed **N-447**, **BAL-508**. Record: `archive/x_bi_3_family_2026-09-18.md`.
  * [x] **X-bi-4** `32c65cf1` -- the fold's re-point onto MOVEMENTS (**R-BAL75**..**R-BAL84**; ticked with its last leaf): 4a `babae2e3` (the fold and the ledger read movements only), 4b-1 `21709195` (every reader onto the covering movement), 4b-2 `32c65cf1` (the figure columns deleted by migration `45f10b870c8b`; `$0.00` written). Closed **BAL-500**, **BAL-501**, **BAL-507**, **BAL-516**, **BAL-517**, **BAL-523**. Family record: `archive/x_bi_4_family_2026-09-20.md`.
  * [x] **X-bi-6a** `323400d9` -- every PROJECTED-shadow READER onto the parent's derived legs (**R-BAL13**, **R-BAL38**, **R-BAL42**); `$0.00`. Record: `archive/x_bi_6a_as_built_2026-09-15.md`.
  * [ ] **X-bi-6** the DECOMPOSED parent of the transfer shadow rows' deletion (**R-BAL13**, superseding
    this step's stored-column scope under **R-JA**), split five ways 2026-09-20 (**R-BAL86**; the lane's
    trace: 354 shadows = 298 Projected / 38 Paid / 18 Cancelled, every Paid one under exactly ONE dated
    covering movement); each leaf its own session and review, none moves money; ticks with 6-5.
    * [x] **X-bi-6-1** `45742c05` -- the grid reads LEGS (**R-BAL87**): `TransferLeg` keyed (transfer, account), the cell's doors the transfer routes, `_shadow_mutations.py` and five route guards gone; byte-identical. Closed **N-303**, **N-420**. Record: `archive/x_bi_6_readers_as_built_2026-09-20.md`.
    * [x] **X-bi-6-1b** `6773418b` -- the dashboard's bills / pulse, the calendar and the Spending report read legs through the ONE period-windowed load (`set_transfer_legs_in_periods`); `far_leg_clause` / `paycheck_rows_clause` deleted; byte-identical, ONE declared change (**BAL-528**, 6-4's Fork B). Record: the same file.
    * [x] **X-bi-6-3** `f1abdbbb` -- the ledger takes shape C (**R-BAL45**): the owner-bucket chart row (`is_fallback` -> `is_owner_bucket`, **R-BAL99**) gives each owner a Transfers-in-transit account, and ONE movement writer posts each side's covering movement on its own day against it (**R-BAL101**); the split a date-keyed correction with no row link (**R-BAL102**), reconciled in the anchor corrections' one loop; migration `c7d1e9a4b2f8` renames and re-keys the bucket, seeds transit and unlinks the 25 splits, re-booking nothing -- the deploy's resync re-books every source and re-checks the anchors once (**R-BAL98**, **R-BAL103**), refusing while a legacy one-entry net stands (**R-BAL104**; its cost a manual dump restore, **R-BAL105**, until `X-cv`); DC-12 detects a broken pair.
      Leaves `1c3b05c8` (+ `5320632a`), `c9c63e61`, `6a0b5154`, `e2275ba9` + `f1abdbbb`. Rehearsed 48/48 on the 2026-09-21 10:33 and 2026-09-22 17:06 production dumps: 19 settled transfers (38 movements) re-booked in 57 entries, no anchor correction, a redeploy writing nothing. Record: `archive/x_bi_6_3_as_built_2026-09-22.md`.
    * [ ] **X-bi-6-4** the DECOMPOSED parent of the record half's re-parent onto `budget.transfers` and the mirror's end, its forks ruled 2026-09-20 (**R-BAL88** the movement's two side-keyed links, **R-BAL89** the first evidence pays a transfer and writes both sides, **R-BAL90** a `$0.00` close is no timed bill) and split 2026-09-22 into four leaves, readers first (**R-BAL106**); each leaf its own session and review; ticks with 6-4d. Every reader moves onto a LEG-shaped call whose interval body, the one reach through a shadow, lives in `transfer_legs` (reads) or `transfer_service` (writes), so 6-4d moves each body once. **BAL-538** (a period-lock refusal naming a transfer's shadow, which has no door) is owned here until a leaf takes it; the two acts that do remove such a payment are the hard delete's removal act (`transfer_service/_delete.py:118`) and a `$0.00` re-record's (`status_seam/_covering.py:640`).
      * [x] **X-bi-6-4a** `c72e0286` -- the DECOMPOSED parent of the cash and ledger readers (**R-BAL106**): the cash fold, the posted ledger, the period locks and reset gate, the savings metrics and the ledger oracle onto the legs through ONE producer, in three leaves; ticked with leaf 3.
        * [x] **X-bi-6-4a-1** `4d660606` -- the cash fold, the savings metric and the ledger oracle read a settled transfer's covering movement as a leg of its transfer through one join and loader (`transfer_movement_rows` / `recorded_transfer_legs`), and the timing metric asks a row's and a leg's RECORD alike (**R-BAL90**), the one declared change. Closed **BAL-528**.
        * [x] **X-bi-6-4a-2** `9b4e66c9` -- the posting writer resolves a movement's parent through `transfer_legs.movement_parent` and books a transfer movement under its LEG; the pair door walks `transfer_legs.transfer_family_movements`, moved out of `posting_service`; byte-identical while the mirror holds.
        * [x] **X-bi-6-4a-3** `c72e0286` -- the pay-period lock and the schedule reset read a transfer's settled state off its own STATUS (`pay_period_locks.settled_items`: an ITEM is a row that is no shadow, `Transaction.transfer_id IS NULL`, or a transfer, read off `budget.transfers`), and all six "this transfer holds a payment" checks ask ONE `transfer_legs` join, `_entries_under_shadows` (any entry under any of the transfer's shadows, live or dead), through `transfer_holds_a_movement` or, for the archive aggregate, `held_transfer_entries`: the lock and the reset's second count (`pay_period_locks.items_holding_a_movement`) and `archive_helpers`' transfer archive, recurring-transfer delete and account delete (`legs_of_transfers` deleted), the five of **R-BAL125**, and "Remove earlier paychecks"' `pay_period_gates._reject_held_rows`, the sixth (**R-BAL157**); the reset refusal counts a transfer once (**R-BAL126**, its two sentences the one declared change), and every lock and gate decision was byte-identical on the 2026-09-30 00:11 production dump. Closed **BAL-559**, already fixed by `credit_card:CC-5-4a-4`.
      * [x] **X-bi-6-4b** `78be0ff4` -- the loan family and the contributions read a transfer's to-side LEG, its covering movement attached through the one join; the settled half is a transfer-rooted query, the plan half's exact complement (**R-BAL140**), and a `$0.00` loan payment is dated by the installment it skips (**R-BAL139**); byte-identical on the 2026-09-23 21:17 production dump.
      * [x] **X-bi-6-4c** `dda4011b` -- the DECOMPOSED parent of statement match, the reconcile panel, carry-forward and the side's day reading transfers (**R-BAL106**), in four leaves ruled 2026-09-24 (**R-BAL141**, **R-BAL142**, **R-BAL143**, **R-BAL145**); ticked with leaf 3. No leaf took **BAL-532**, which X-bi-6-4d carries.
        * [x] **X-bi-6-4c-1** `10b321cf` -- statement match offers a still-planned transfer as `RowKind.LEG`, keyed by the transfer id on the screen's account (**R-BAL87**; **R-BAL159**: the account is the screen's, so the KIND discriminates; no `isinstance` router is added; nothing to ask `transfer_legs` for the leg-key test), from `transfer_legs.offerable_transfer_legs` and priced by `transfer_service.leg_settle_amount` in a new module, `statement_match._leg_valuation` (`_valuation` crossed 1,000 lines when the leg joined it, **R-IR**); a paid side is a SETTLEMENT whose parent is its leg, its DATED covering movement only (**R-BAL80**), from `transfer_legs.recorded_transfer_legs`, priced by the movement (`cash_ledger.movement_cash_leg`); both row arms exclude shadows (`transfer_id IS NULL`), `_parent_transfer_stands` is gone, and the cite of the deleted `_transfers.arm` is re-pointed at `offerable_transfer_legs`, which carries its parent clause; claims gain `MatchedSubjects.legs`; `Candidates.unpriceable` is keyed by `(kind, id)` and counts a damaged transfer's still-planned side, which is not offered (**R-BAL158**); the re-price and the accept door's read-back ask the leg (`_valuation.recorded_leg_payment`); the accepted register values every payment member through `transfer_legs.movement_parent` and ONE `status_seam.covered_cash_leg` (a `TransferLeg` answers `covering_movements`), eager-loaded by `transfer_legs.movement_parent_loads`. Declared, and graded on the 2026-09-30 00:11 production dump, identical there but the order: a still-planned leg is its own run after the Projected rows, by transfer id; a leg is labelled from the endpoints' current names (0 of 256 live shadows differ); three drifted states no door writes are decided by the parent (**R-BAL79**, **R-BAL80**); and a stale leg is re-read through its offer scope.
        * [x] **X-bi-6-4c-2** `1c26f05e` -- the reconcile panel offers a transfer's LEG on the statement's account from `transfer_legs.offerable_transfer_legs` (owner, window, no scenario; the parent decides a drifted shadow, **R-JM**), keyed by `transfer_legs.cell_key` (**R-BAL87**) and ticked under `transfer_ids` / `transfer_amount-<id>`, spelled once on `reconcile_service.TickForm` (**R-BAL145**); it prices and clears the leg through `transfer_service.leg_settle_amount` / `record_leg_clearing`, whose interval bodies are `settle_amount` / `record_clearing` over the leg's verified shadow pair (6-4c-1 reuses the price); `reconcile_service._rows.Arm` is `(load, settle, event)` and the writer stays one; a leg whose shadow pair is broken is published in `OutstandingSet.damaged` and printed as a warning, not offered (**R-BAL148**); carry-forward walks the source period's Projected transfers in id order, a transfer plan's `item` its FROM-side leg; `transfer_recurrence._rows_holding_owner_records` asks `transfer_legs.transfers_holding_records`, its query moved unchanged. Declared, and graded on the 2026-09-24 production dump: a transfer block is headed by its leg's label from the endpoints' current names (0 of 354 shadows differ), same-day transfer blocks order by transfer id, carry-forward's transfer plans order by transfer id and take the from side's label (of 51 period pairs, 29 changed only in order and 1 only in one transfer plan's label, of 95 transfer plans), and a drifted shadow's leg is emitted by its parent's state while that side has no dated movement (**R-BAL79**): a soft-deleted shadow's is then warned about, a Cancelled one's offered at `$0.00`. Closed **BAL-546**.
        * [x] **X-bi-6-4c-3** `dda4011b` -- each transfer side keeps its own settle day and basis (**R-BAL142**, amending **R-BAL89**): the verbs take `side_days: tuple[SideDay, ...]` (`transfer_service.SideDay(account_id, day)`, at most one per endpoint; an account on neither side is a `ValueError`) in place of the pair `settle_day` kwarg on `settle_transfer`, `update_transfer` and `TransferSpec`; ONE pure resolver (`transfer_service/_side_days.py`: `resolve_pair_days`, and `borrowed_day`, the only constructor of a borrowed day, pinned by an AST census, `tests/test_arch/test_a_borrowed_day_has_one_constructor.py`) run by `_status`'s two writers, `apply_status_to_all_three(rows, status, *, stated=...)` for every status and day write (settle, correction, figure correction, revert, restore, the matcher `statement_match/_moving.py` and the reconcile tick `reconcile_service/_transfers.py`, each stating ITS side only) and `date_born_settled_pair` for a born-settled create; the fourth basis `SettledDayBasisEnum.BORROWED` (`ref_seeds` and the migration's inline seed); `settle_day.is_evidence` the one statement of the evidence partition; the seam's mirror (`status_seam/_covering._mirror_assertion`) copies a same-day basis change only when it RAISES the movement's, for every row kind, so a movement's own evidence is never lowered (**BAL-582**); `Transfer.settled_on`, `settle_day_columns` and `_income_shadow_settle_pair` DELETED; the popover draws one box per side from ONE load of the legs' records (`_render_helpers.transfer_side_boxes`, **BAL-530**), a borrowed side's box EMPTY with the caption "same as <account>, <day> (a guess)" (**R-BAL164**), and the PATCH grades each box against what THAT box was prefilled from (schema `settled_on_from` / `settled_on_to`); "Remove earlier paychecks"' dated-money arm reads each side's own day off its movement (`pay_period_gates._settled_transfer_side` through `transfer_legs.transfer_movement_rows`, **BAL-568**); a settle over a settled parent honours the day stated for a drifted side (**BAL-578**); a figure correction states no day, so no side moves (**N-304**); migration `d3b8f5a1c7e2` adds `borrowed` and relabels, shadow and movement together, every dated transfer side whose label its writer did not earn, by predicate over the audit log (**R-BAL143**; a pre-step typed day stays `entered` on both sides, **R-BAL165**), refusing, before it relabels any side, one whose day is not its in-band sibling's or whose covering movement does not carry exactly its day and basis (the coordinator, fail-closed). Declared changes, each listed in the leaf's message; graded on the 2026-09-30 00:11 and 14:15 production dumps: 26 and 27 sides relabelled, 0 refused, no day, figure or link moved, each round trip byte-identical. Closed **N-304**, **BAL-530**, **BAL-568**, **BAL-578**.
        * [x] **X-bi-6-4c-4** `82785f73` -- `scripts/integrity_check.py`, which sat at its 1000-line ceiling, is split one module per check family (**R-BAL161**): `_integrity_core` (the two check types, the runner and `TRANSFER_LEG_RECORDS_JOIN`, the sweep's one raw leg join, which DC-11's and BA-06's leg arms share), `_integrity_referential`, `_integrity_orphan`, `_integrity_balance` and `_integrity_consistency`, the script keeping the command line and the orchestration; FK-10 skips a category-less template (BAL-574, fixed here, **R-BAL162**); FK-14..16 grade a transfer's own pay period, scenario and category; OR-03 counts transfers and transfer definitions; BA-06 grades a settled transfer by its legs' movements; `archive_helpers.category_has_usage` asks `TransferTemplate` and `Transfer` by owner (**BAL-545**, declared: a category only a transfer definition names is in use) and `account_has_history` asks a live transfer from or to the account (**BAL-539**); `entry_service`'s update and delete doors read ownership off `entry.owner_id`, `update_entry` refuses a payment record first, and that refusal names the record's parent through `transfer_legs.movement_parent`; the withdrawal event files a transfer's payment under `transfer_ids`; and the removal act takes a movement out of the list `transfer_legs.parent_entries` answers (**R-BAL160**'s four declared changes: the refusal's order, its name, the event's ids and the report). Graded on the 2026-09-30 00:11 production dump: both archive predicates identical (34 categories, 9 accounts), the refusal's new name differing for 0 of 40 transfer payments, and the report's rows identical but FK-10's, 8 -> 0 (FK-14..16 find none). Closed **BAL-539**, **BAL-545**.
      * [ ] **X-bi-6-4d** the DECOMPOSED parent of the record half's re-parent, the mirror's end and the shadow rows' delete, by migrations in its own release after `credit_card:CC-5-4a-4`'s (**R-BAL106**), ruled 2026-09-30 (**R-BAL166** the rows go in this release, not at 6-5; **R-BAL167** the tests re-expressed by class, any dollar, posting-date or day-level balance change and any behaviour a ruling reverses going to the developer one at a time; **R-BAL168** an endpoint move carries a side's record and frees its line; **R-BAL169** a corrected `$0.00` close dated by its due date) and split into three leaves (`balance-2026-09-30/xbi6-4d/design.md` s.6), two since **R-BAL361** joined the last two: the readers onto `transfer_legs`' producers with no schema (4d-1), and the migration, the join, the seam's Transfer arm, the re-plumb, the rest of the upkeep and the rows (4d-2); ticks with 4d-2. What remains: `transaction_entries` GAINS `expense_transfer_id` / `income_transfer_id`, each in a composite key with `account_id` onto its endpoint (**R-BAL88**), `transaction_id` goes NULLABLE under an exactly-one-parent CHECK, no delete cascades to a movement (**R-CC54**), and each transfer account is keyed to its owner (**R-BAL107**); the movements are backfilled, graded by predicate; the ONE join (`transfer_legs/_records.py`) and `transfer_family_movements` MOVE onto the links, and `transfer_legs._is_covering` goes: every movement a side links is covering, or it reaches one covering predicate below both (**BAL-551**); the status seam GAINS a Transfer arm, a NEW module `status_seam/_side.py`, that writes a side's record directly (its figure and source, its description the leg label, its day and basis through `record_settle_day` and 6-4c-3's day function, `purchased_on` following the day, the statement link released on any day move or revert, a same-day write never lowering evidence by `settle_day.is_evidence`, a `$0.00` close taking kept records off through the removal act, a future day refused through `reject_future_settle_day`) and marks the TRANSFER's counter when a side's record OR day nets a change, replacing `_update._versions_of` and `_bump_parent_version_if_a_leg_moved`; a side closed at `$0.00` keeps no statement link (**R-BAL141**); `transfer_service`'s mirror GOES (`_build_shadow`, the five `_restore` clauses, `_validation._get_shadow_transactions`, `_status._resolved_days`' read off the shadows, `_apply_endpoint_move`, and `_amount.apply_amount_ownership`'s leg loop, **R-BAL91**, with the four docstrings that cite `X-au-m` as a future step, `_amount.py:53`, `_update.py:960`, `cash_ledger/_amount_source.py:664` and `tests/test_integration/test_a_transfer_shadow_is_derived.py:693`, re-worded), with the upkeep the trace found unnamed (`_status.date_born_settled_pair`, re-plumbed onto the arm or deleted, **BAL-583**; the pair loop; `_update`'s period, category, due-date and `is_override` mirrors; `_status.drifted_sides_only` with **BAL-578**'s pair repair; `_restore`'s drift repair and `_validation.assert_restorable`; `_delete`'s shadow clauses) and every reader of a twin's status (the shadows' `verify_transition`); the shadow rows are deleted by a migration in this release (**R-BAL166**; its downgrade rebuilds two per transfer from the parent, names and days reproducing on the 2026-09-30 00:11 production dump, 358 of 358 and 40 of 40, its ids, timestamps and **BAL-575**'s 74 stale income categories declared), so the "Transfers: Incoming" category becomes unused (**BAL-575**'s effect); an endpoint move of a side holding a record CARRIES it, the side keys `ON UPDATE CASCADE` (**R-BAL168**, `credit_card:R-CC46`'s endpoint half, amending **R-BAL88**'s refusal): before the transfer's update the door withdraws any match naming the record through `match_withdrawal`, disclosed in the log, releases its statement link and flushes both, and refuses by name a day the new account's books do not reach, and the moved side's day becomes `borrowed` through `borrowed_day` (**BAL-503**), and `_endpoints.py:265-269`'s "the movement adds no failure its parent does not have" (false since `credit_card:CC-5-4a-1`) goes; a DEFERRED constraint trigger with WHEN clauses ties a transfer's status band to its side records (settled: each side one dated record, or none on either side, the `$0.00` close; unsettled: no dated side record), so the drift the readers branch on is unstorable and its pins become refusal tests (**R-BAL167** class 2), the readers' own drift branches going at 6-5; the Transfer-level live and settled filters each caller hands the leg loader are DELETED rather than graded, the trigger arms making the live filter's plant unstorable and the status-band trigger the settled filter's (**BAL-534**); the Transfer arm's refusals answer **BAL-547** and **BAL-550** (a hidden transfer named by its leg label, never the nullable `Transfer.name` nor the row sentence); DC-12 is deleted with the rows, DC-11 arm 1's shadow-day grading is re-keyed, and CLAUDE.md's Transfer Invariants 1 to 5, which all name shadows, are rewritten, with the code-reviewer agent's copy of them, because the shadow mirror's code and rows are deleted and each movement moves onto its transfer link; `opening_infrastructure/_movement.py`'s `SETTLED_MOVEMENTS_SQL` is found correct once the rows go; 6-4c-2's interval code follows the pair it reads: `transfer_service._settle._shadow_of` goes and `leg_settle_amount` / `record_leg_clearing` re-body off the parent and the side's movement, and **R-BAL148**'s damaged path (`reconcile_service.DamagedTransfer`, `OutstandingSet.damaged`, the catch in `_transfers.outstanding_transfers` and the panel's warning block) and `transfer_legs.transfers_holding_records`' `reconciled_by_id` arm go, and with that code go five defects 6-4c-2 reported, each confirmed gone here: the damaged path's catch is by TYPE, any `ValidationError` the leg price raises (`reconcile_service/_transfers.py:307`); the pair loader's refusal names the transfer by its id and says "shadow transactions" (`_validation._get_shadow_transactions`); a Cancelled shadow under a Projected parent is offered at `$0.00` (`settle_amount` over `_shadow_of`) and its tick refused (`_status.apply_status_to_all_three`'s `verify_transition` over the pair loader's shadows, `_status.py:230`), a trigger that goes with the pair loader, while the refusal's status ids are `state_machine._status_labels`' wording (`state_machine.py:315-332`), which stays and is **N-551**'s class; a broken pair logs at ERROR on every panel render (that loader, `_validation.py:366`); and each offered leg costs one pair query (`_shadow_of`); and the carry-forward modal's user-facing "and both linked shadow rows" (`grid/_carry_forward_preview_modal.html:164-166`, reported at 6-4c-2) is re-worded in plain words here at the latest; and what 6-4c-1 left in statement match is removed or found here: its damaged path (`_leg_valuation.leg_price`'s catch by TYPE of `ValidationError`, `Candidates.unpriceable`'s `(LEG, id)` arm, and the pair loader's ERROR log, which since 6-4c-1 fires for every statement scope, each render and each Apply), a damaged transfer's PAID side, which 6-4c-1 still offers priced off its movement (a correction to it refused at Accept by the pair loader's message, a same-day confirm on an `observed` day recorded without refusing), and a Paid or Received transfer whose side holds an UN-DATED kept record, which statement match skips silently rather than counting it unpriceable (both drift no door writes, made unrepresentable here); and what 6-4c-4 left for it: the arrival trigger `budget.refuse_movement_under_deleted_row` reads `budget.transactions` by `NEW.transaction_id` (**BAL-576**; its three changes are in `app/deleted_row_infrastructure.py`'s docstring), and the deleted-row functions gain transfer arms (the arrival arm locks `budget.transfers` FOR NO KEY UPDATE and compares the three links `IS NOT DISTINCT FROM`; the hiding arm reads `budget.transfers`, retiring `credit_card:R-CC92`'s carve-out), `DELETED_ROW_TRIGGERS` counted by `scripts/build_test_db_image.py` at its line ceiling, so a split, never a disable; the sweep's one raw leg join `scripts/_integrity_core.TRANSFER_LEG_RECORDS_JOIN` (DC-11's and BA-06's leg arms) re-points onto the side links, beside DC-11 arm 1's re-key; `transfer_legs.parent_entries` answers a transfer movement's list from its side links (the removal act's contract: the collection the transfer LOADED, never a copy or a union over two; re-measure the act's delete-then-remove on that relationship), and `test_cc5_4a3_movement_removal.TestTheRemovedMovementLeavesItsParentsLoadedList`'s transfer case, which asserts the SHADOW's list, is rewritten onto the side links; and the producers 4d-1 routed its readers through, `transfer_side_leg`, `covering_movements_by_leg`, `movement_parent`, `movement_parent_loads` and `held_transfer_entries` (the hard delete's collection), answer from the side links; and what 6-4c-3 left for it: **BAL-532** (a recurring transfer's occurrence delete leaves a reverted leg's payment and match under hidden shadows), carried from the container `X-bi-6-4c`, which no leaf took, with `credit_card:CC-5-4a-4`'s `transfer_id IS NULL` carve-out in its hidden-row trigger, which retires only when that trigger's arrival arm also refuses a movement under a deleted TRANSFER and its hiding arm reads `budget.transfers`, answered by `credit_card:R-CC75`'s rule for transfers: the delete takes each side's records off through the removal act on BOTH arms before its soft delete, and un-archiving brings the occurrence back empty; each side's current day and basis, which the day function reads off the shadow while the popover's box reads the covering movement (they part on a `$0.00` close, which records no movement), is read off the movement alone (**BAL-580**), and a `$0.00`-closed pair whose figure is later corrected with no typed day is dated, both sides, by its due date, a guess (**R-BAL169**: today when it has none, a due date below the books refused by name), the fallback input to `borrowed_day`; the seam mirror stays raise-only, which this step's design builds on, and whether it should copy any basis onto a plain row's movement was **BAL-582**'s question, ruled by **R-BAL192** (one home, `X-de`, after this step), not this step's; and the three tests and the comment **BAL-581** names, which cannot fail on their subject, are tightened or corrected, the tests under rule 5. Closes **BAL-503**, **BAL-532**, **BAL-534**, **BAL-547**, **BAL-550**, **BAL-551**, **BAL-575**, **BAL-576**, **BAL-580**, **BAL-581**, **BAL-583**.
        * [x] **X-bi-6-4d-1** `da628fd2` -- the readers onto `transfer_legs`' producers, no schema: the transfer service's typed-correction and echo reads (`_settle.settle` / `settle_amount`, `_update`) find a side's record by SIDE through `transfer_legs.transfer_side_leg`, never by an account an endpoint move may have re-pointed; the hard delete hands the removal act `transfer_legs.held_transfer_entries`; the loan probe (`loan_posting_service/_sync.py`) loads through `movement_parent_loads()` (**BAL-579**); the entry doors' settlement-record refusal advises a transfer's own acts; statement match's four readers of a payment member's `transaction_id` (`_acts.named_rows`, `_acts._WHOLE_ACT`, `_candidates._claimed_rows_of_the_owner`, `_accept._reject_parent_and_its_own_purchase`) were found correct with the shadow id and with `None`; the cash walk's same-day tie-break is the movement id (`cash_ledger/_events.settled_cash_facts`, declared: totals unchanged, 0 bank-agreement listings moved on the 2026-09-30 00:11 production dump); and a settled payment with no day is refused by ONE producer, `balance_predicates.require_settled_day` (rule 14), which `settled_day` binds for a row and the loan walk reaches for a settled transfer's loan-side payment, naming the transfer by its name, its two accounts and its due date, the repair it names pinned through the transfer's door (**R-BAL147** applied: after the re-parent the thing the owner fixes through the app is the transfer; re-expressed under **R-BAL167** class 1). Closed **BAL-579**.
        * [ ] **X-bi-6-4d-2** the migration, the Transfer arm, the rest of the twins' upkeep and the shadow rows, HELD OFF DEV, ONE pull request cut as 6-4d's own release and rehearsed on a same-day production dump (design s.6); it holds what was `X-bi-6-4d-3`, joined into it by **R-BAL361**. In three checkpoints (design s.6) and then 4d-3's half: the schema with its backfill graded by predicate, the deleted-row trigger arms, the models and the join onto the side links (**BAL-534**, **BAL-551**, **BAL-576**); then the seam's Transfer arm (**BAL-547**, **BAL-550**), the one writer re-plumbed onto it with `record_leg_clearing`, `leg_settle_amount` and the born-settled create (**BAL-580**, **BAL-583**), the endpoint move (**BAL-503**), the occurrence delete (**BAL-532**) and the status-band trigger, where the twins' status and day upkeep STOPS, with no stopgap, and so does every reader of a twin's status; then the tests this breaks, re-expressed ONLY within **R-BAL167**'s classes (a test failing outside them is a regression to fix), the class-4 shared helpers `transfer_family_journal_filter` and `transfer_side_journal_filter` (`tests/_test_helpers.py`, moved here from 4d-1 by the coordinator 2026-10-03) rewritten as independent SQL over the side links, since a helper that selects money through the producer under test cannot catch that producer's fault, the integrity re-keys (the raw leg join, DC-10, DC-11, BA-06) and a grade on a production dump; then 4d-3's half: the rest of the twins' upkeep goes with the pair loader and both damaged paths, a migration deletes the shadow rows (**R-BAL166**, **BAL-575**), DC-12 goes, DC-11's transfer arm goes as a re-ask of the status-band trigger, CLAUDE.md's Transfer Invariants and the code-reviewer agent's copy are rewritten, the carry-forward modal's copy and the docstrings are re-worded (among them the code comments **R-BAL166** makes false by naming `X-bi-6-5` as the rows' delete: `app/utils/archive_helpers.py` :547, :719-720 and :726, `scripts/_integrity_consistency.py:331`, `cash_ledger/_events.py:721` and `tests/test_routes/test_categories.py:1851`), and the remaining tests are re-expressed under **R-BAL167**; **BAL-581**'s three tests and its comment are tightened or corrected here, since the design's ranges (s.6, s.7) do not settle its leaf and this leaf closes the container, and that test change needs its own rule-5 approval from the developer, which **R-BAL167**'s classes do not obviously cover. After `credit_card:CC-5-4a-5` lands on dev, which the held branch then merges: 4d-2 calls `match_withdrawal`, builds its arm beside `status_seam/_covering.py` and edits the carry-forward modal and the reconcile panel, each of which the card step edits; its branch is pushed but opened as a pull request only once the whole leaf is built, and before it opens, after the last merge from `feat/balance-x-bi-6-4d-c`, ONE mechanical commit re-points every `X-bi-6-4d-3` the branch holds (comments, docstrings, tests, scripts and the migration's four messages; its pushed commit messages cannot change) to `X-bi-6-4d-2`, the one step that ships the work (**R-BAL370**). It also carries **N-148**, **N-150**, **N-159** (re-measured once it lands) and **N-305**, re-homed when `X-ak` was cancelled (**R-BAL269**), and **BAL-640**.
    * **X-bi-6-2 is ABSORBED into X-bi-6-4c** (**R-BAL106**, 2026-09-22): statement match and the reconcile panel offer legs at 6-4c-1 and 6-4c-2 (`statement_match/{_candidates,_offers,_moving,_variance,_destinations}`, `reconcile_service/{_transfers,_rows,_assemble,_transactions}`); its wait on `bank_import:X-f6b-2` lifted, and the held bank-import branch resolves the conflict at its own merge.
    * [ ] **X-bi-6-5** the shadow COLUMN goes (the rows go at 6-4d, **R-BAL166**): the migration drops `transactions.transfer_id`,
      `idx_transactions_transfer`, `uq_transactions_transfer_type_active`, the pricing-link term,
      `AmountOwnershipEnum.PARENT_TRANSFER` and `Transaction.transfer`; the branches (the marker on its `steps.md` row), the exclusions (census 17 code lines `Transaction\.transfer_id\.is_\(None\)` in `app/**/*.py`), the inverted guards (census 5 code lines `transfer_id is None` in `app/**/*.py`), the two Jinja branches (`grid/_transaction_full_edit.html:74`, `:83`), the `is_transfer_shadow` fence and the `EVT_*` shadow events die with them; `journal_entries.transaction_id` and `transfer_id` drop with `idx_journal_entries_transaction` / `idx_journal_entries_transfer`, since after 6-3's resync only zero-net reversed pairs carry either (**R-BAL102**), and the `JournalEntry` link guards in `account_posting_service/_walk` and `ledger_report_service/_attribution` with them; `app/services/_posting_legacy.py` (the legacy one-entry source and the resync's refusal, **R-BAL104**) is deleted whole with its ledger-model fence entry; the readers' drift branches 6-4d's status-band trigger leaves dead go (**R-BAL79**'s per-side emission and **R-BAL147**'s `UndatedSettleError` arm; **R-BAL140**'s `$0.00` close stays legal under the trigger, and `X-db` deletes its arm); the leg twins (eight producers, three routers; **BAL-527**) fold into one valuation seam; CLAUDE.md's Transfer Invariants and the reviewer mirror rewritten. Graded byte-identical on both baselines. Closes **BAL-475**, **BAL-526**, **BAL-527**.
  * **X-bi-5 is DISSOLVED** (**R-BAL85**, 2026-09-20): a plan item's KIND is a constitutive input
    with ONE home (the definition's `is_envelope`) and ONE reader (`tracks_purchases`), which
    carry-forward needs before any purchase exists; the cumulative envelope is `bank_import:X-gg`'s.
* [ ] **X-ci** transfers take the one-definition shape their data holds (X-bi-7's 10.3), split three
  ways 2026-09-20 (**R-BAL95**); ticks with X-ci-3. The rulings **R-BAL92** to **R-BAL97**; the design
  record `HANDOFF-X-ci.md`, `balance-2026-09-20/xci-design/` and `archive/x_ci_1_as_built_2026-09-21.md`.
    * [x] **X-ci-1** `465f91cd` -- `recurs` on the transfer twin; inside `update_transfer` a move re-places the date and a typed figure restates the definition (**R-BAL92**..**R-BAL97**). Closed **BAL-492**, **BAL-493**. Record: `archive/x_ci_1_as_built_2026-09-21.md`.
    * [ ] **X-ci-2** the door goes: `create_ad_hoc`, `TransferCreateSchema`, `_handle_adhoc_integrity`,
      `_adhoc_dedupe_idempotent_response` and their four test modules' cases (re-expressed where they grade
      ownership on another door, deleted where they grade the door), the surviving flush-failure handlers
      capturing `xfer_id` before the `try` (**BAL-529**); the one-time producer moves from
      `routes/transfers/_instances._materialize_one_time_transfer` into a service (the transfer twin of
      `one_off.place_row_of`) so the suite's builder can call it; `tests/_test_helpers.create_transfer` and
      `create_settled_transfer` mint a rule-less definition and place its transfer through it, and the direct
      sites (the service calls and the bare `Transfer(` constructors) move; `uq_transfers_adhoc_dedupe` stays
      until X-ci-3. Tests only beyond the producer and BAL-529's two handler lines. Closes **BAL-529**.
    * [ ] **X-ci-3** the cutover: migration making `transfer_template_id` NOT NULL with `RESTRICT` (the
      template hard-delete's settled-survivor arm, which relied on `SET NULL`, becomes a refusal), dropping
      `uq_transfers_adhoc_dedupe` and `ck_transfers_adhoc_owns_amount`, replacing
      `ck_transfers_template_row_needs_due_date` with `due_date NOT NULL`, backfilling `occurs_on = due_date`
      on the 2 one-time rows (**R-BAL94**) and RE-ATTACHING the one detached before X-ci-1 (transfer 409:
      `is_override` cleared, priced by its definition, `$0.00` because the two figures agree -- the developer's
      placement, 2026-09-21, of the residue X-ci-1's `mutations.py` comment assigns here);
      `TransferSpec.transfer_template_id` required;
      `_definition_cash._stated_amount`'s `template is None` refusal deleted (7d-2 kept it for this key).
      Graded byte-identical on the balance, cells and render harnesses; 0 rows affected by the NOT NULL on the
      13:59 restore. Gives `recurrence:R19-b` the backfill its NOT NULL waits on.
* [ ] **X-cu** `feat(loans): a loan's ledger posts each charge on its own day` -- the from-scratch loan ledger, filed as a FUTURE step when **R-BAL102** made the split a derivation (the developer, 2026-09-21: *"should be documented as a future step"*).
  Each ACCRUAL posts as its own event on its own date, `{loan_interest +I, loan_escrow +E, linked -(I+E)}` keyed `(loan_charge, period, charge date)` -- a date-keyed correction, as the split is today; each payment is a PURE movement `{linked +X, transit -X}` (shape C, nothing else); the `loan_payment` split and its source kind are deleted.
  The payoff overpayment is a FORK (a per-date `loan_refund` correction, or a negative liability presented as a receivable), asked with a worked example per option.
  Since `recurrence:R16-c-2` (`ea946f33`) the one stream charges every contractual installment, and the fold must DATE a charge at its own day in `dated_deltas` in the SAME step: today a payment's whole effect counts at its `visible_on`, and the E1a assert makes the ledger follow whatever the fold dates.
  **MOVES POSTED MONEY** (the balance between an installment and its settle; a skipped month accrues), **OWN PR**. **It also carries `recurrence:R-R116`, the lender's day** (the REC-552 study's design A*, 2026-09-30; after Flip step `X-bi-6-4c-3`'s per-side day and timed with the Flip lane; 5-7 sessions, an estimate): a balance includes the payments the lender had by its date, and where only a guess dates one (a day the checking side's bank shows, one typed, or the day Paid was pressed) and the guess and the due date fall on opposite sides of the balance's date, the app asks, keeps the answers (consistent with each other, changeable on the loan page) until a day from the loan's own records replaces them, and nothing about the loan moves the checking side's day; a charge dated after a balance's date must not sit inside that balance's correction (the study's N2). R27's answer may change this build (the study's design s.3 M2): read R27's state before building those clauses. It owns **BAL-584** (cited in the code as REC-552 only at `loan_anchor_service.py:442` and `:459`, which it re-cites as BAL-584 when it edits those docstrings; the citations of "the REC-552 study" elsewhere name the study and stay), **BAL-585** (a Record balance and the first tracking start on one day, filed as REC-556) and **BAL-586** (a held payment's split against the loan before the start) and **BAL-588** (a Record balance missing one month's interest on its own correction). Its build must still handle a `$0.00`-closed loan payment explicitly: take ruling **R-BAL169** (granted; filed at X-bi-6-4d-1's tick)'s due date as the fallback input, decide whether the design asks about such a payment at all (a `$0.00` close moved no money, and **R-BAL155** refuses new ones at `X-db`), and test the pair across a later write (the study's residual R7).
* [ ] **X-bk** the ONE-TIME manual reconcile of the imported bank history against the app's own
  rows, through the app's doors and into the post-restructure shape, so the whole history keeps its
  plan-versus-actual comparison. **MOVES MONEY**, and it is an OPERATOR act -- a rehearsed runbook
  and a re-measurement rather than a code change. Reconciling under the PRE-restructure shape was
  rejected: it mutates plan rows to match the bank and destroys the comparison the restructure
  exists to give. **It is what makes THE FLIP safe**, and its completion test is
  `outstanding_difference.SpanAgreement.reconciles` rather than a judgement. Split in two 2026-10-04 (**R-BAL196**, the developer's "Split it in two"); ticks with X-bk-2.
    * [x] **X-bk-1** `cd764e51` -- the disagreement list, as **R-BAL232** ruled (the developer's "Tested report + script"): `app/services/outstanding_difference.py`'s `disagreement_list` cuts the books span into back-to-back stretches (agrees, disagrees, no statement yet and not compared, so they cover every day), each graded by `span_agreement`, the ONE grader the card's verdict reads (the private `_over_span` made public), and names each disagreeing day's bank lines and app rows, the imported days either side of the span graded and set apart; `SpanAgreement.shortfall` states the reconcile conditions once, and `reconciles` and the stretch kinds read it. `tests/test_services/test_disagreement_list.py`'s 14 cases pin it on made-up figures, and `tests/manual/measure_bank_disagreements.py` writes the list from a production copy, its `--out` refusing any path inside a git work tree, so the measured list lives in the handoff folder and never in the repository (**R-BAL132**). No screen changes; `$0.00`. Filed **bank_import:BI-513** (owner `bank_import:X-f6b-2`) and **BAL-616** (owner X-f3c-2b-2c).
    * [ ] **X-bk-2** the reconcile acts, after the restructure (X-bk's wait on X-bi): written from X-bk-1's list, rehearsed on a same-day production copy, then performed through the app's doors; **MOVES MONEY**, an OPERATOR act, done when every span's `SpanAgreement.reconciles` holds. It also waited on the account-10 repair `X-f3c-2b-2c` (**R-BAL203**, the developer's "Repair first", 2026-10-04), shipped at `2bcf2da5`, so Checking opened at its bank's own close before anything was reconciled. It re-runs `tests/manual/measure_bank_disagreements.py` on a same-day copy and is done when the books span RECONCILES. **R-BAL232's filing note** (X-bk-1's lane, 2026-10-08): the ruling's gloss, "done when the whole books span reconciles (every day covered by a statement, none disagreeing)", is looser than `SpanAgreement.reconciles`, which also requires every day COMPARED and a non-empty span; X-bk-1's code documents `reconciles` as the test (the safe direction), and the two coincide once **bank_import:BI-513** closes (**BAL-616** closed at `X-f3c-2b-2c`). If X-bk-2 ever meets the difference, its lane asks the developer. It also owns **BAL-467**, carried from `X-f3c-2b-2c` at its tick: which of the two rows answering one payroll line of Checking's is short is the owner's knowledge, so correcting it is a reconcile act, and this step's done test cannot hold while the residue stands; and **BAL-621**, **BAL-651** and **BAL-655**, filed at that tick. `app/services/statement_match/_reconcile.py` stood at its 1,000-line ceiling and `app/services/bank_agreement.py` at 988 lines on 2026-10-15 (re-measure with `wc -l`), so X-bk-2 splits each on a seam before adding to it.
*THIRTEEN of this span's shipped steps left under rule 5, indexed by `archive/README.md`: X-an, X-f2's four leaves, X-aq, X-as and X-ap (2026-08-13); X-f3a-1 and X-f3d (2026-08-16); X-f1 with five others (2026-08-26); X-f3b and X-i3 (2026-08-27). Sentences above name several of them for how the code came to be, which rule 15 sanctions. The statement importer moved to its own arc 2026-08-13: `../../plans/implementation_plan_bank_import.md`.*

### Phase X -- the amount model (ruling R-FI)

**What this phase is about, in one sentence: the app stores five amounts it also computes, and each
one grew its own private repair mechanism.** Salary income is repaired at READ time by
`income_service.live_projected_net`, which writes nothing back (**N-224**); a loan-payment shadow by
`cash_ledger.LoanPricing.live_cash`, which also read the wall clock until `X-au-g-2b` (**N-40**); a
transfer shadow by a two-line copy in `transfer_service.update_transfer` beside a drift corrector
that logs `"Correcting shadow %d estimated_amount drift"`; an ordinary template row by
`regenerate_for_template`, which DELETES and recreates every non-override row from a date the form
supplies and never stores. `credit_card:CC4b` planned a fifth (re-minted 2026-09-18 as `CC-6`, which prices through the resolver). **R-FI deletes the class**: a derived
amount is not stored, one total resolver answers, and the CHECK makes a stale derived figure
unrepresentable rather than merely unlikely.

**EVERY LEAF HERE MUST OBEY ONE THING `X-au-c3` established**: a figure and a status change are
independent facts that ONE seam call applies. It lived only on that step's entry until the
2026-09-01 archive and is in no registry, so it is restated where it governs all of them.

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
`$0.00`**, re-measured 2026-08-12 by X-au-b's exhaustive oracle -- which
`../../plans/conventions.md` rule 8 says is not resolved, and which the read-time repair is what
hides.

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
the finance charge is a row of a card-owned definition carrying `template_id` from birth (`credit_card:CC-8`; `CC4d` dissolved at the 2026-09-18 re-mint), `X-au-l` DELETES the column. That is the END STATE; this phase's cutovers,
which created the population it must be true across, have all shipped (`X-au-d` .. `X-au-g`).

**The rule is general and its BOUNDARY is part of it** (`R-IY`): *every derivable column is deleted,
because a stored copy is a stale cache -- which is what started this arc.* It does NOT reach a
CONSTITUTIVE fact: opening equity (**R-GX**), an asserted balance and
`pay_schedule.history_opens_on` (**R-IA**) are stored because nothing computes them. Those are
INPUTS; the rule bites on COPIES.

**THE RULE ABOVE IS ONE HALF OF A WIDER ONE** (**R-JA**, the same day): *a value has exactly one
source of truth -- if it is a derivation then one walk, if it is stored then it is stored in one
place.* The stored arm is not hypothetical and this phase is already paying it off: Transfer
Invariant 3 is a maintained DUPLICATE written into `CLAUDE.md` as an invariant, and `X-au-g-2c-2`
made the shadow half structural and `X-au-f` (`cb4239a2`) took the parent half. **The tell is an invariant** --
where a rule says two places must always agree, they are one value with two homes, and the remedy is
to delete a home rather than keep them in step.

**AND EVERY DERIVED VALUE HAS EXACTLY ONE PRODUCER** (**R-IZ**, R-JA's derived half): *one walk, one
answer that can never disagree with itself* -- for ANY derivation, not only one replacing a deleted
column. A second walk is a cache with no column, and
the cost is not the wasted pass but that the two can PART. **Agreement is not the test**:
`X-au-g-2c-3c`'s fifth spelling of one interest expression agreed over 200,000 randomised draws and
was REFUTED at 500,000, `$565.37` against `$565.36`. **Where a layer puts the shared leaf out of
reach, MOVE THE LEAF** -- `X-au-g-2c-3a` deleted three restatements that placement had FORCED, two
walks sitting BELOW the allocation so that reaching it was an import cycle. This phase does not own
the rule, and its instance here has left it: **N-409**'s escrow floor, a SECOND allocation
rule for a question the fold already answers, is `recurrence:R16-e`'s with walk 3 (**R-R93**).

**One ground is REFUTED and must not be re-argued**: that `ondelete="SET NULL"` on the template link
would make a "derived rows have a link" CHECK refuse a definition delete. `budget.transfers` already
ships that constraint, and the alternative is worse -- a persisted row with no link is unpriceable
in SILENCE where a refused DELETE is loud.


* [ ] **X-au** `feat(cash): a row's amount is either its own or derived` -- the DECOMPOSED parent
  (**R-FI**), carrying **N-40**, **N-224**, **N-228**, **N-238**. It ticks with the last of its
  leaves. **It SUPERSEDES X-ar**, whose two stated premises tracing also refuted: its deletion set was
  unreachable (`live_amount_overrides` merges the salary and loan halves at
  `cash_ledger/_amounts.py`, and the loan half feeds BOTH shadow legs, so the loan half being out
  of scope left nothing deletable), and it claimed **N-40**, which `../../plans/ledger.md` owned to
  `X-i2`. **It also WITHDRAWS `X-au-i`** (developer, 2026-08-21), which leaves the order as `X-ar`
  did: the CC payback is a PHANTOM the card arc's locked 2026-07-19 rulings already condemn -- "the
  phantom payback dies", `CC3b` deletes it and `CC3c` deletes `credit_workflow.py` whole while RENAMING `credit_payback_for_id` to `card_charge_for_id` (both re-minted 2026-09-18 as `CC-7`, which deletes the link outright) -- so a `credit_source` relation, a ref
  seed and a 23-row migration would buy structure on a column about to be renamed, for a kind R-FI's
  five never included. What was LIVE in it shipped at X-au-j: **N-252**'s hand-edit refusal and
  **N-323**'s narrowed predicate. N-243's payback bullets and N-311 pass to the card arc. The
  `is_envelope` re-pricing measured while tracing it (`$181.58` to `$500.00` through the popover's
  own Save) is NOT the ground: ruling **R-FK** accepted that class for SALARY at `$13,499.89`
  (**N-261**) and put its remedy at the write DOOR.
  **Two obligations RESTATED from leaves archived 2026-08-26**, binding the leaves that REMAIN: a
  derived row's answer must be INVARIANT under a change to its own amount column, agreement being
  blind to the resolver (X-au-b); and the basis is REQUIRED on both `settle_amount` twins, pinned once in `cash_ledger.baseline_amount_basis` (X-au-j). **One RESTATED from the X-au-g family archived 2026-09-24**: a later step must NOT delete `tests/oracles/loan_monthly_composition.py` (X-au-g-2c-3b-2).
* [x] **X-au-d** `ed06acf6` -- a paycheck's amount is its salary profile's and it stores none; 59 rows declared (**R-JB**), FOUR absorbed defects closed or filed. `archive/x_au_d_as_built_2026-09-03.md`.
* [x] **X-au-e** `c000d7f6` `b846386a` -- a template row reads its template's series; 525 rows declared, the `$502.45` class dead. Closed **N-244**, **N-247**, **N-444**. Its own keep-vs-use claim was REFUTED (**R-JD**) -- see `archive/seven_shipped_pointers_2026-09-05.md` and `archive/x_au_e_as_built_2026-09-03.md`.
* **X-au-m is DISSOLVED** (**R-BAL91**, 2026-09-20, applied 2026-09-24 at the tick that split `X-bi-6-4`): its one remaining object, `transfer_service/_amount.apply_amount_ownership`'s leg loop (a parent that OWNS a typed figure stores it on each shadow as well), is in `X-bi-6-4d`'s mirror list with the three docstrings that cite `X-au-m` as a future step, and `X-bi-6-4d` (leaf 4d-2) deletes the rows (**R-BAL166**); the read half of **R-JM** has been structural since `X-bi-6-1` / `X-bi-6-1b`, every reader drawing a leg off its parent.
* [ ] **X-au-l** `refactor(cash): the LINK is the relation` -- DELETE `amount_source_id`
  (**R-IY**, **R-IZ**). The relation is derivable from which pricing link the row carries and
  `ck_transactions_one_pricing_link` already makes those exclusive, so the column, `ref.amount_sources`,
  `AmountSourceEnum` and `_declared_relation`'s per-dispatch reverse map all go, and `amount_rule`
  reads the LINK -- ONE walk. Both ownership CHECKs become *a figure OR exactly one pricing link*,
  which makes **N-440**'s disagreement state unrepresentable rather than guarded. A migration; moves
  no money. **Its wait on the card arc's `CC4d` DROPPED at the 2026-09-18 re-mint** (developer): the finance charge is a row of a card-owned definition carrying `template_id` from birth (`credit_card:CC-8`). Closes **N-440**.
* [x] **X-au-f** `cb4239a2` -- the DECOMPOSED parent of the PARENT-transfer cutover, ticked with its last leaf; split into three 2026-09-09 (**R-BAL10**) and RE-CUT into two 2026-09-10 (**R-BAL14**), `X-au-f-3` ABSORBED into `X-au-f-2`. Family record: `archive/x_au_f_family_2026-09-11.md`.
* [x] **X-au-f-2** `cb4239a2` -- THE CUTOVER in one act (**R-BAL10**, **R-BAL11**, **R-BAL12**; `b7e4c1f38a20` empties `transfers.amount`). Closed **N-263**, **N-451**, **BAL-476**, **N-449**, **N-352**; opened **BAL-477**; **NOT N-450**, which is `X-bp`'s. Record: `archive/x_au_f_2_as_built_2026-09-10.md`.

* [x] **X-bl-2b** `0e93ec9f` -- a loan payment's three dates are ONE value both records COMPOSE and the replay takes (**R-BAL9**); `ConfirmedPayment` deleted, **101 modules -> 46**, byte-identical. Closed **N-432**; opened **BAL-472**, **BAL-473**, **BAL-474**. Record: `archive/x_bl_2b_as_built_2026-09-09.md`.
  **A LATER step must obey:** a feed reaching the replay carries its schedule SLOT, and `slotted_dates` is the one application.
* [ ] **X-cb** `refactor(loans): the slot has one door and the predicate takes the value` -- closes
  **BAL-472**, **BAL-473**, **BAL-474**, all three found by `X-bl-2b`'s two neutral reviews and
  reported rather than taken there (rule 6). `schedule_dates` stays exported with **zero production
  callers outside `slotted_dates`**, so the collision assignment has a second public door and
  R-BAL7's invariant is held by convention where the import graph could hold it -- and dropping it
  reverses a decision `X-bl-2a` took one step earlier, which is why it is a step and not a footnote.
  `is_confirmed_payment_eligible` still takes `settled_on` and `due_date` apart, the one place a
  caller could still pair two different payments' dates. Seven dead imports across three loan test
  files go in the same sweep; all pre-date `X-bl-2b`, measured byte-identical at `2625963a`.
* [ ] **X-bm** `perf(loaders): every batch caller applies the pricing load` -- closes **N-296**.
  `pricing_load_options` publishes the EIGHT chains a batch pricing needs; THREE loaders apply it
  and SEVEN batch callers do not (companion, pulse, calendar, asset contributions, projection inputs,
  the loan payment context, the render helpers). It bit at `X-au-e`: an unguarded caller pricing 926
  rows went **146 -> 260** statements while a guarded one stayed at **8 and 8**, per DEFINITION, live
  on every render of those surfaces. Pure loader work; before `X-au-f`, which inherits guarded ones.
* [x] **X-bn** `cdc67086` -- every signed-in save, sign-in included, takes the owner's write lock where its database transaction begins (`app/db_transaction.py`'s `after_begin` listener), after the form-token check and the rate limit (**R-CC114**, **R-CC121**, **R-CC122**); the older lock calls and `credit_card:CC-5-4a-4`'s row locks are deleted (**R-CC115**), a save request's teardown rolls back what it left open (**R-CC123**, test fidelity only), and half 2 is `X-da` (**R-CC120**). Closed **N-193**, **N-202**, **BI-492**'s (b) and `pay_calendar:P71`; the race **BAL-565** names stays reachable until `X-dc` (**R-BAL156**).
* [ ] **X-cz** `fix(pages): a page load never writes` -- owns **BAL-553** (**R-CC114**). The grid's
  and dashboard's pay-period top-up and the two-factor setup's pending secret move into real saves,
  so a page load stops waiting behind a save while holding one of two workers (**R-CC122**'s cost), and `write_transaction`, whose three callers (`app/routes/dashboard.py`, `app/routes/grid/page.py`, `app/routes/auth/mfa.py`) are the page loads this step moves, is deleted, so no GET can write and the template hook's verb grep goes (**R-BAL263**).
* [ ] **X-dc** `fix(db): a save is ONE transaction` -- owns **BAL-565** and **BAL-567**
  (**R-BAL156**; ranked by **R-CC124**). A save now spans several transactions (a refusal rolls back
  and re-reads, a committed save renders after its commit), each end releasing the owner's lock. The
  lane's proposed fix, confirmed at its design round: the request boundary owns one transaction per
  save, the lock taken at its start, a refusal rolled back to a SAVEPOINT that keeps it, the answer
  drawn inside, the commit last, routes no longer committing. It amends `X-i5`'s render clause, its
  design round owes a ruling on **R-CC123** (the teardown rollback X-bn added), and it covers
  `recurrence:R16-c-2`'s two loan doors; unmeasured spike diffs wait in the card lane's
  `xbn/spike_partial/`. Its design round decides whether that render, inside the save's transaction
  under the owner's lock, meets **N-358**'s one-snapshot requirement or needs a stronger isolation
  (REPEATABLE READ on the save's transaction, say): whether any lock-free writer touches rows a
  render reads (**BAL-560**'s four scripts are the known lock-free writers of an existing owner's
  rows).
  It also owns **N-397**, re-pointed at `X-f3c-2b-2c`'s tick (the coordinator, 2026-10-15):
  `account_posting_service.self_heal_anchor_corrections` returns early when a movement emitted no
  posting delta, and its design round shows the self-heal path runs under the save's lock, taken at
  the save's start. **N-397 may already be false**: `app/services/account_posting_service/_sync.py:341-359`
  says the owner's lock is taken at the start of every caller's transaction since `X-bn`; X-dc's
  design round re-measures it by a census of the callers and closes it if true.
* [ ] **X-da** `fix(locks): every button sends its page's version` -- X-bn's half 2 (**R-CC120**;
  ranked by **R-CC124**): a click on an out-of-date page answers 'this changed, reload' instead of
  saving over, or deleting, another tab's change. **Its mechanism is its first question for the
  developer**: of about 93 kinds of save, 12 check a version and 49 change rows that carry none,
  some moving money (X-bn's census, 2026-09-25); drafts and a review in the card lane's
  `xbn/layer2_*.md`. It also owns **BAL-591**: a press refused as out of date whose transfer endpoint another tab moved answers a bare 404 htmx drops, where 'this changed, reload' is the answer.
* [ ] **X-db** `fix(payments): a $0.00 payment is refused at every door` -- **balance:R-BAL155**'s step (the developer's "Cash vs debt, no new status", 2026-09-29), after `credit_card:CC-5-4a-5`, whose reconcile panel refuses a `$0.00` payment first (**credit_card:R-CC125**): ONE refusal of a `$0.00` payment in the status seam every door shares -- bills, paychecks, transfers, loan and card payments -- deleting the reconcile panel's own copy of the refusal when it moves it into the seam, and BOTH of the bill popover's `$0.00` captions, the one beside Paid / Received and the one under Estimated (**credit_card:R-CC129**); a cancel of a paid bill, paycheck or transfer takes its cash record off the books through **R-CC54**'s one removal act (`movement_removal.remove_movements`); the grid labels a cancelled loan payment "Missed", DERIVED from the loan, never stored; and broken `$0.00` data (a settled row with no money record) is refused by name. Open at its start, named by the ruling's study: the paid-then-refunded edge, and the derived label's exact rule and where else it shows. The loan's tracking-start and setup doors already count only dated money (`recurrence:R16-c-2`); envelopes closed with nothing spent are unchanged; the card switch-over's `$0.00` settle (**credit_card:R-CC21**) is reopened in `credit_card:CC-7`'s design round. It deletes the `$0.00` halves **R-BAL155** retires or amends, as the `$0.00` study's design lists them for the filing: it RETIRES **R-BAL139**, **recurrence:R-R107**, **R-BAL141** and **R-BAL15**; AMENDS **R-BAL90** (its transfer half), **R-BAL82** (non-envelope items), **R-BAL140** (the reverse drift becomes a refusal, with **R-BAL147**), **recurrence:R-R108** (its `$0.00` example) and **credit_card:R-CC56**, **R-CC59** and **R-CC76** (their `$0.00` halves); REOPENS **credit_card:R-CC21**'s CC-7 detail; and owes, in its own commit, the rewrite of CLAUDE.md Transfer Invariant 5's sentence on the reverse drift and of the `code-reviewer` agent's mirror of it. It also owns **BAL-590**: at a derived `$0.00` transfer price the transfer popover's Paid is refused today as out of date on every try, and this step's refusal is the true answer there.
* [ ] **X-de** `refactor(seam): a row's paid-date basis has one home` -- owns **BAL-582** (**R-BAL192**, the developer's "Store it once", 2026-10-04). A plain row's settle basis, how its paid date is known, is kept twice, on the row and on its covering movement, and the status seam's mirror (`status_seam/_covering._mirror_assertion`) copies the row's onto the movement only when it RAISES it; the one test guarding that rule, `tests/test_services/test_covering_movement.py`'s `test_an_identity_re_submit_keeps_the_movements_observed_day_and_link`, plants a state no door writes. The step first TRACES whether a row's basis derives from its covering movements -- an envelope row has several, so one movement may not answer it -- then deletes one of the two homes, so nothing keeps them in step (rule 14), and the planted test goes with the copy rule. After `X-bi-6-4d`, whose design builds on the raise-only mirror. `$0.00`.
* [ ] **X-bo** `perf(postings): a purchase CREATE re-emits only its own legs` -- closes **N-406**.
  `sync_transaction_postings` reconciles the parent's whole purchase FAMILY on every entry change,
  which **R-FM** needs for a re-category -- but on a CREATE no sibling changed, so filing N purchases
  into one line is quadratic: 51 acts in one envelope, **1,326 calls, 5,919 SQL**, per act
  **0.051s -> 0.195s**, `$0.00` of drift (clone 2026-08-31). The narrower door exists,
  `sync_purchase_postings`; **the step's first act is the ruling** on whether a CREATE may take it for
  the new purchase plus the parent's leg. Not `X-d`'s, which sits behind the cutover.
* [ ] **X-bp** `refactor(templates): a template's default amount is its series` -- closes
  **N-446** and **N-450**; carries **BAL-632**'s template-amount half (**R-BAL263**).
  **WIDENED 2026-09-11 to BOTH `default_amount` columns** (**R-BAL16**). `transaction_templates`
  and `transfer_templates` each carry one, and each is written outside the series --
  `routes/salary/profiles.py` on the transaction side, `loan/payment_transfer.py`, `investment.py`
  and `_transfer_creation_helpers.py` on the transfer side. **It is one design executed twice, so
  it is one step**: splitting it would mean taking the same decision a second time with one column
  live in between.
  **TWO KINDS HAVE NO SERIES TO READ.** `owns_its_amount` refuses one to a SALARY-LINKED
  transaction template and a DERIVE-MODE loan payment: their column is a snapshot of a computation,
  not a copy of a stated price (`transfer_template.py`: "has NONE **and must not**"). For them it
  dies with NO SUCCESSOR -- the paycheck calculator and amount rule 4 answer live, as `X-au-f-2`'s
  instance column did. **The derive-mode payment is N-450's own first kind**, so "delete it, read
  the series" would be wrong for the half that finding is about, and what the DOWNGRADE does for
  those two is the developer's open question (the ledger row carries it).
  **`steps.md`'s `code` census CANNOT SEE A NAME THAT IS A STRING** and seven more sites name the
  column (census 7 lines `["']default_amount` in `app/**/*.py`): two CHECK constraints ON THE
  DROPPED COLUMN, which the migration drops with it or fails, four lines in two route branches
  gating `set_amount` on a dict key (`routes/templates/crud.py:457`/`459`,
  `routes/transfers/templates.py:611`/`613`), which break as hard, and
  `obligations_aggregator.py:322`'s `getattr` of the column, which would read `None` silently.
  Among the readers, `obligations_aggregator.py` SKIPS a row when the column is `None` or `0`;
  the live dependencies are re-measured BY THE STEP, not listed here -- the transaction half's
  three (a downgrade, `archive_profile`, the conflict chooser) plus at least
  `routes/loan/dashboard.py`'s `_payment_drift`, which computes a shortfall a SCREEN SHOWS
  off the stored column; and for a
  SALARY template two routes write it as two quantities, NET and GROSS. No step deleted it and
  `routes/templates/crud.py` claimed `X-au-e` would until PR #210 -- and its TRANSFER twin at
  `routes/transfers/templates.py` still says it, which this step now owns. The columns go (**R-IY**), every
  site reads the series, and each downgrade restores its column from that series' opening version.
*`X-av` (the dated per-paycheck gross) and `X-at` (the tax year) moved to the `salary` arc on
2026-09-03 (**R-SAL1**); their specifications are `../../plans/implementation_plan_salary.md`,
section 4, under their unchanged ids.*

  **The census counts every code line NAMING the column, not attribute access alone**: an
  `\.default_amount` pattern reads 13 and misses the two column declarations, the four
  Marshmallow fields and the five kwarg writes -- including `routes/salary/profiles.py`,
  which is one of the two salary routes this step's own headline is about.
* [ ] **X-bq** `refactor(transactions): the flag's write set matches its meaning` -- the follow-up
  `X-au-h` created by shipping. That step made `is_override` MEAN one thing (*this row is the
  owner's, not the rule's*, **R-JR**) without making its WRITE SET match: `due_date`, `name` and
  `category_id` are `DerivedRowFields` members a popover can edit, and none of them raises the
  flag, so a row the owner has detached from its rule does not say so. The other half is
  **N-238**'s untouched remedy -- the column still records a re-price and a period move
  indistinguishably, and the move needs its own representation before the flag can be deleted.
  Carries **N-238**, **F-11**, **N-245**, **N-246**, and **N-448**'s closed record.
* [x] **X-au-h** `825fd791` -- **`is_override` says ONE thing** and authorship is on the PAYLOAD (**R-JR**); `e7c3a1f9b482` dropped the flag from the occurrence-keyed index. Closed **N-248**, **N-436**, **N-448**; opened **N-451**, **N-452**. **N-238** stays OPEN and NARROWED, with **F-11**, **N-245**, **N-246** -- the clauses a later step obeys are in `archive/seven_shipped_pointers_2026-09-05.md`.

### Phase X -- the posting restructure

* [ ] **X-ai** `refactor(posting): the posted ledger gets one verb and one trigger` -- the END
  STATE R-DM named and **R-DU** ruled, on BOTH ledgers. **The row-level posting writer stops being
  the interface**: you do not tell the ledger "this row settled", you tell it "account A in
  scenario S changed; re-derive it from its facts". **R-DV** then fixed what OWNS an entry: an
  entry is the projection of exactly ONE source event and the event owns it; the account is the
  scope of the loop, never an owner. That answer makes N-162 die by construction (N-161's family is deleted at `X-f4`) and
  removes the write OSCILLATION account-owned entries would have created. Its correction half already books in the period CONTAINING the day it asserts (**R-EA**, shipped
  at X-ai-r). Carries **N-144**,
  **N-153**, **N-155**, **N-157**, **N-158**, **N-160**, **N-162**, **N-163**,
  **N-164**, **N-165**, **N-166**, **N-167**. Requirements its leaves must satisfy, each from a
  review rather than from the design: the verb's source set is the UNION of the walk's facts and
  the ledger's already-posted source links (**N-162**); the verb returns the `(account, scenario)`
  pairs its emitted legs touched and the caller re-enqueues them with a stated termination argument
  (**N-165**); a per-`(account, scenario)` advisory lock is taken inside the verb (**N-166**); and
  the registry-scoped commit-boundary grader is BLIND to bulk `UPDATE` / `DELETE` across 20 call
  sites, which **X-ai-g** classifies rather than forbids (**N-163**).
  * [ ] **X-ai-a** the cash verb, built from R-DV's sentence with N-162's, N-165's and N-166's
    requirements in it from the start.
  * [ ] **X-ai-b** the trigger: the commit-boundary grader, drained from the registry the writers
    populate; the per-door posting field filters and resyncs are measured and deleted if they can no longer fire (**R-BAL263**).
  * [ ] **X-ai-c** the loan side onto the same verb.
  * [ ] **X-ai-g** the bulk-statement census: each of the 20 sites is proven unable to touch a
    posted row, routed through a writer, or named in the one docstring that states what the grader
    cannot see. **The remedy is NOT a checker forbidding bulk statements** -- several are
    legitimate and performance-critical.
  * [ ] **X-ai-s** the `journal_entries` migration -- source identity as an EXCLUSIVE ARC of typed
    FKs with an AT-MOST-ONE check (**R-DY**), plus the reversal linkage **N-167** asks for. **HELD
    pending X-f3**: it buys per-assertion attribution for the correction family X-f3 deletes, and
    running it first would ship a migration and a backfill for something about to be removed. Re-measured before it is built (**R-BAL269**): after `X-bi-6-5` only `transaction_entry_id` may remain a concrete source key (`app/models/journal_entry.py`), so it is possibly superseded; **N-167**'s reversal linkage remains.
* [ ] **X-d** `fix(cash): the posted account ledger is a checked projection` -- E1a's shape for
  cash. The posting writer consumes X-a's walk instead of its own, and the per-visible-date assert
  (`sum(postings) == fold(ACTUAL events)`) makes a stale posting a detectable, repairable cache
  inconsistency. **Its ship gate is ALREADY MEASURED AND CLEAN -- do not re-run it as if it were
  open.** Swept on the 2026-08-01 production clone, in both directions and positive-controlled:
  **0** entries with both concrete FKs NULL and a non-correction source kind, and **0** whose
  `transaction_id` resolves to a missing, soft-deleted or non-contributing row -- against 170
  transaction-linked, 19 transfer-linked and 128 both-NULL entries (every one of the 128 a
  correction) proving the joins bite. **So no F1-class human decision waits on today's data**; that
  is a fact about the data, not the mechanism, and the step must still decide whether the residue
  arm's defence moves to the checked-projection assert or is ceded. **It also inherits
  `ledger_report_service/_attribution.py`'s two duplicate date loaders**, whose `duplicate-code`
  disable named the shipped step 3 as its resolver: X-d deletes their twin rather than extracting a
  third shared home, which is why extracting one now would be scaffolding for a caller X-d removes.
  Both carried from `anchor_settle_partition.md` at its archive move. **PARKED at tag
  `xd-attempt-1-parked-n155`** (six commits, one deliberately RED, off PR #80; the branch
  `feat/xd-checked-projection` was deleted 2026-08-04 once the tag was pushed, because X-f4 deletes
  `account_posting_service/_anchors.py` and the correction machinery that branch conflicted in, so
  the code is a REFERENCE for the re-land and never a rebase candidate) **and nothing there is for
  merge.** The park is
  **N-155**, the step's own assert: it compares an account's WHOLE ledger against its WHOLE
  source-row walk but rides on the PER-ROW write path, so any operation settling N rows one at a
  time grades a half-finished state and REFUSES the write -- three confirmed production defects,
  one reproduced twice independently. The placement is X-ai's, not a patch at each loop. Carries
  **N-135** (wrap both bare fact fields in their own types). Re-lands after X-ai.
* [ ] **X-aj** `refactor(status): one status seam, and the fence is structural` -- rulings
  **R-DN**, **R-DO**, **R-DP**. The DECOMPOSED parent of the one-status-seam work: its merge half
  shipped as X-aj1, and it ticks with its open leaf **X-aj2**.
  * [ ] **X-aj2** the structural write door and the DELETION of W9907 (**R-DP**), carrying
    **N-149**, **N-151**, **N-185** and **N-188**. Its trace decides the three candidates R-DP
    names and must rule what a row may be BORN as: today `create_transfer` runs no legality check,
    so "every row is born Projected and every other status is a verified transition" is available
    and makes the constructor question disappear -- but it would refuse creations the tests
    currently make, one of which (Received) is not in the transfer map at all, so the refusal would
    be correct and the test wrong. That is a behaviour change on a creation path and gets its own
    worked ruling, not a build decision. `_SEAM_OWNED_FIELDS` is measured with it and deleted if it can no longer fire once the field loop iterates the declared writable fields (**R-BAL263**).
    **It carries N-321 too, enumerated here for that same reason**:
    `grid/_transaction_entries.html` withdraws the purchase-date and description inputs,
    the add form and the delete button on `status.is_settled`, where `entry_service`
    refuses only `amount` / `is_credit`, a delete and an UNDATED add -- so **R-FW**'s
    purchase-date correction is reachable from statement review and not from the popover.
    **It also carries N-334, and the five surfaces are enumerated here because a
    ledger row is an index entry** (conventions rule 4): since plan step X-az a row records
    WHICH KIND of settle day it holds -- a bank observation, a balance assertion's UPPER BOUND,
    or the owner's own -- and no screen says so. `grid/_transaction_entries.html` is the one that
    says the opposite (*"Posted <date> -- this money has left your account"*, for a day the bank
    may never have confirmed); `accounts/_statement_review_body.html`,
    `grid/_transaction_full_edit.html`, `transfers/_transfer_full_edit.html` and
    `statement_match._accepted_view` are silent. The telemetry half is smaller and belongs with
    them: `entry_service.create_entry` logs `settled_day_basis` and `update_entry`'s event does
    not, so one door's receipt can tell a bound from an observation and the other's cannot.
* **X-ak is CANCELLED** (**R-BAL269**, 2026-10-11): `X-bi-6-4d` deletes the shadow copy and all three mirror loops it was to unify. **N-152** closed with `transfer_service`'s package split (`a61a6d19f`), and **N-201** because `anchor_service.py` stands at 931 lines on dev (measured 2026-10-11), so the ceiling pressure it records is gone; its other rows were re-homed in `ledger.md`.

### Phase X -- the read-path residue

* [ ] **X-x** `refactor(balance): one pay-calendar precondition, one answer` -- closes **N-116**,
  **N-125**, **N-126**, **N-129**. Rulings **R-CX**..**R-DA**. X-v's sibling one axis over, and its
  own trace inverted the row's premise. **The census replaces the row's count**: an AST pass that
  taints period-valued expressions and reports only ABSENCE tests measures **96 branches in 49
  `app/` files** plus 8 in Jinja, resolving to about 50 distinct answers. They are FIVE questions,
  not one: any periods at all (Q1), which period contains today (Q2), which contains date T (Q3), is
  there a next one (Q4, a normal terminal state), is the requested window non-empty (Q5,
  navigation). **Q1 is unreachable for an owner and Q2 corrupts money**: a 5-day calendar hole moves
  `/savings` net worth by **+$3,228.55**, while `/grid` renders the repair card at the same instant.
  (That measurement ALSO put `Account.current_anchor_balance` on screen as a current balance; **that
  half is now impossible** -- ruling R-EH dropped the column at X-f1c3c, in production since
  `8d812662`. The net-worth figure stands; the substitution does not.)
  **HELD behind X-ad** (ruling R-DE), because N-127 measures the repair its refusals point at as
  non-functional.
  **Its 2026-07-31 build is a REFERENCE, not a rebase candidate.** Tag
  `xx-attempt-1-held-rde` (branch `wip/x-x-held` deleted 2026-08-05 once the tag was pushed, the
  `xd-attempt-1-parked-n155` precedent). Measured at tag time: 39 behind `main`, 3 ahead, 38 files,
  and a test merge conflicts in **7 files across 10 hunks**. Three findings force the re-derive
  rather than a rebase: **one third of it already shipped** (X-x2's anchor-cache arm, below); **its
  reasoning is falsified in at least four places** that assert `accounts.current_anchor_period_id is
  NOT NULL` as a load-bearing guarantee, and that column is gone too; and **one conflict is SEMANTIC**
  -- in `savings_dashboard_service/_projections.py` the branch DELETES the no-current-period fallback
  because `ctx.current_period` is no longer nullable, while `main` (ruling **R-EM**, shipped at
  X-f1c3a) KEEPS the branch and asks the seam for the balance at `ctx.balance_ctx.as_of`. Both cannot
  stand, and choosing is a design decision rather than a merge resolution.
  * [ ] **X-x1 THE ONE ANSWER** (R-CY) -- `PayCalendarGapError`,
    `pay_period_service.require_current_period` / `covers`, the application-level handler and its
    repair page, on the `require_baseline_scenario` / `BaselineMissingError` pattern name for name.
    **It takes the GRID's two pre-checks as its first callers** rather than shipping a door nobody
    walks through: an unreachable handler has no negative control.
  * [ ] **X-x2 THE FABRICATIONS** (R-CY) -- the branches that publish a figure the app did not
    compute take the raising accessor. **Its first arm is DONE and must not be rebuilt**: the
    anchor-cache substitutions died with the column at X-f1c3c (ruling R-EH), and an AST pass over
    `main` finds no live read left -- only prose (census 0 code lines `current_anchor_period_id` in `app/**/*.py`), which is the arm that matters: a file-count of the prose asserts nothing about a live read. What REMAINS, re-measured against
    `8d812662`: the fabricated `$0.00` in four producers, and `build_trend_periods`'
    `current_index = 0` into an empty list, still live at `routes/accounts/detail.py:228` and
    `analytics_view.py:485`.
  * [x] **X-x3** `b7d513ff` -- the pay-period row and its two locks RETIRED (**R-BAL116**,
    superseding R-DA); the banner complete on `has_salary and has_templates`, the second ANY
    template (**BAL-537**); its four facts asked when read (**R-BAL117**). Closed `balance:N-328`.
  * [ ] **X-x4 THE STATES SPLIT** (R-CZ) -- an empty requested window stops answering with the
    absence card, and the card's copy stops naming two states.
  * [ ] **X-x5 THE HARNESS** -- delete `verify_savings_producers.py`'s dict-or-attribute `_get`
    reader and the two-spelling readers its own docstring says X-x deletes. Verified dead by RUNNING
    it against the tree.
* [ ] **X-ad** `feat(periods): the pay calendar a new user can actually enter` -- the DECOMPOSED
  parent, split 2026-08-10 (**R-EZ**). Two write doors, two findings, two commits: the one that
  CREATES a calendar and the one that GROWS it share no function and no test. **It RUNS BEFORE
  X-x** -- R-DD sequenced it after, ruling **R-DE** reversed that, and the later ruling governs --
  but **the pair no longer ships as ONE PR**: **R-EY** moved **N-127** to pay-calendar `C3`, so the
  repair X-x's refusals point at is that step's and X-x waits on both.
  *Its opening line claimed this step "re-anchors accounts". That was expired when written: ruling
  R-EH deleted `accounts.current_anchor_period_id`, so there is nothing left to re-anchor.*
  * [x] **X-ad-a** `2a4eb477` -- registration ASKS for the payday, the cadence and the horizon, and the bootstrap payday is DELETED; closed **N-123** (= pay-calendar `P3`), satisfies `C4`'s `P8`. Record in `archive/eight_shipped_steps_2026-09-01.md`.
  * [ ] **X-ad-b** the rolling top-up stops writing HISTORY (**N-124**, ruling **R-EZ**). With
    rolling enabled, a lapsed schedule's page load appends periods from `last.end_date + 1`
    wherever that is and populates them -- **61 -> 113 -> 132 periods and +991 transactions** over
    two `/grid` loads on a clone whose schedule ended 1,000 days ago, 19 of the new periods
    entirely historical. It self-heals, which is why nothing ever reported it, and what it heals
    with is fabricated history. **An automatic writer may not create a period that has already
    ended**: the top-up creates nothing on a lapsed schedule and the lapse is surfaced instead,
    because the owner's pay may well have changed while they were away and the app must not guess
    that it did not.
* [ ] **X-y** `refactor(balance): the baseline decision that is not the balance seam's` -- closes
  **N-117**. The fifteen surfaces that resolve the baseline DIRECTLY (`get_baseline_scenario`)
  rather than through a `BalanceContext`, and so answer this state without the seam being asked:
  two grid fragments and the carry-forward preview (400), template and transfer generation (a silent
  commit that generates NOTHING), `salary/profiles` (a flash telling the user to register a new
  account -- a competing repair story, wrong since `/grid/create-baseline` shipped),
  `period_population` (`return 0`), `spending_report_service` (`return None`), both posting syncs (a
  silently narrowed scenario set), and `escrow_rates` / `loan/params` (which hand a NULLABLE id to a
  query). **X-v deliberately did not reach these** (R-CC took only the two that fabricate a
  financial statement); the rest are a different question one tier down -- what may a WRITE do
  without a scenario -- and deserve their own answer. Sequenced after X-x.
* [ ] **X-i** `refactor(balance): one read pass, one derivation, one clock` -- closes **FU-3**,
  **N-14**, **N-56**, **N-72**'s second half, **N-89**, **N-91**, **N-92**, **N-93**,
  **N-115**. **N-40 LEFT this set on 2026-08-11** (ruling R-FI): the clock read is a property of a
  DERIVED amount being stored, so it closes at **X-au-g** and no gate reads a prose closes-set. **Nine rows, one root cause:** `BalanceContext` pins the pass's `as_of` and `scenario`
  and memoizes three LOAN derivations, and every OTHER input is loaded ad hoc at the wall clock. The
  arc solved this for loans at D-ctx and stopped there. Two symptom families the ledger filed apart
  are one defect. RECOMPUTED: the calendar loaded three times per modelled grid render, the
  contribution feed at ~9.4 ms per investment account with no cache, the modelled base built 14
  times for 4 accounts on one `/savings` render, `contractual_schedule_from_origination` twice on the
  property page. UNPINNED: the employer-match gross resolving at an implicit `date.today()`,
  and the standing overpayment
  read off the CURRENT template row whatever date the pass is pinned at. (`live_amount_overrides`
  was the third and `X-au-d` DELETED it, so that clause is discharged rather than owed.)
  * [ ] **X-i1 THE MEMO** -- additive, byte-identical on both databases. The context gains the input
    tier the loan derivations already have, through the SAME `_memoize_once` mechanism rather than a
    second one: the per-account contribution feed, the standing extra, the contractual schedule.
    **The OVERRIDE MAP left this list when `X-au-d` deleted it** -- `live_amount_overrides` has
    0 matches in the tree -- so this step carries THREE inputs, not the four it counted until
    2026-09-11. **The CALENDAR left this list 2026-08-13**, taken early by `C2-c`
    (`b8a72f6c`) as a method beside `loan_walk`, which owns its derivation, rather than through
    `_memoize_once`, which stores what the seam fills from above. Every loader keeps its clock, so no
    figure can move and the harness is the proof. **Its tier is WIDENED by N-115** (ruling **R-BU**, which SEQUENCED the residual double load here
    rather than deferring it): the dashboard tracks
    section pays twice per render for three more loaders, and the expensive one is **two full
    `calculate_paycheck` runs (7.2 ms / 7 SQL for the second alone)**. **Its input tier's shape was
    decided at `salary:C12-a` (R-SAL27)**: the basis takes the pass's PRICER, which carries the calendar
    and the per-payday memo.
  * [ ] **X-i2 THE CLOCK** -- the cutover. Each memoized loader takes `ctx.as_of` and `ctx.scenario`,
    and this **MOVES MONEY**: the gross measured at `$3,631.74` today against `$3,722.53` at a 2027
    read and `$0` before the first pay period, and FU-3 changes a loan's whole forward trajectory on
    a historical read. Own trace, own oracle, own every-figure sign-off. **Does NOT close N-56**:
    the grid's two self-refresh endpoints are two HTTP requests, so a per-pass memo cannot reach
    across them -- its fix is the `hx-swap-oob` topology, riding here as its own commit. **Sequenced
    BEFORE X-j** on a measured ground: X-j moves three more surfaces onto the modelled view whose
    contribution load costs `2.7 -> 14.8 ms` per render entry, so shipping X-j first ships that
    regression and then removes it. Forks for its trace: which salary profile a historical read
    picks when several are active, and whether deductions resolve at the read date. It owns **N-191** and **N-197**, re-homed 2026-10-11 from the cancelled `X-ak` (**R-BAL269**), until `pay_calendar:C24`, which routes every civil-day read through one clock, closes them: a `ledger.md` owner cannot name another arc.
  * [x] **X-i3-a THE BOUNDARY** `765daebd`, corrected at `4dddfe73` -- a request is a QUERY or a COMMAND and its transaction says which. Closed **N-353**; opened **N-358**, **N-359**.
  * [x] **X-i3-b THE ACCOMMODATIONS** `1feb0930` -- each accommodation for "the two reads can differ" narrowed to the COMMAND it still describes. Opened **N-364**, **N-365**.
  * [x] **X-i4 THE BINDING** `79a1730c` -- `_memoize_once` takes the `Account` and refuses a foreign one. Ruling **R-GV**. Closed **N-354**; opened **N-360**-**N-363**.
  * [ ] **X-i5 THE COMMAND'S RENDER** -- **N-358**. A mutation route's re-render rides the
    transaction its writes are in, so it reads at READ COMMITTED and is the one render `X-i3-a` does
    not cover; it also keeps `X-i3-b` a NARROWING rather than a deletion. The shape is the one
    `/grid` already has, and the trace owed is WHICH routes render rather than redirect. **X-i4 made
    it load-bearing**: such a render now reads the pre-write FOLD, not merely an older snapshot.
    **Its render clause follows `X-dc`'s design round** (**R-BAL156**). `write_transaction()` rolls
    the query snapshot back first, releasing a lock taken before the block; X-bn locks per COMMAND
    transaction, so a handler's reads that decide its write must sit INSIDE the block. `X-cz` DELETES
    today's `write_transaction` (its only use is a GET's write), so this step waits for it and builds a
    command's write declaration in its place; whether that declaration refuses inside a GET is `X-dc`'s
    design round's to decide (**R-BAL156**; the coordinator's ordering, 2026-10-11).
  * [ ] **X-i6 THE READS A PASS DOES NOT MEMOIZE** -- **N-362**, whose row carries the census. A
    pass binds what it MEMOIZES: seven entries early-out before the funnel, the contribution feed
    scopes its payroll loaders off the ACCOUNT's owner, and `bank_agreement` walks the account
    directly. It owes an ARM as well as a fix -- nothing mechanizes "one door" today. Whether **R-GV**'s owner-scoped `ctx.account(account_id)` makes that arm unnecessary is `X-el`'s question.

* [ ] **X-bc** `fix(accounts): the collateral link's owner is a foreign KEY, not a validator` --
  closes **N-360**, whose row carries the measurement. `collateral_account_id` is a SINGLE-column FK,
  so only `_validate_collateral_link` keeps a secured loan and its asset in one owner; a link written
  by anything else makes `home_equity_service` value another owner's loans through an unscoped
  backref. **The schema already carries the fix** (`uq_accounts_id_user`, which two other tables
  target this way), and production holds one link, same owner, so nothing is backfilled.

* [ ] **X-j** `feat(balance): one account, one answer -- or a row that explains the difference` --
  closes **N-87**, **N-90**, and **N-83**'s DISPLAY half. **This step is the OWN RULING R-AK
  deferred, and its title does not presuppose the outcome.** R-AK ruled the dashboard, the pulse and
  the analytics calendar STAY on the kind-blind cash view inside X-g3 and said the surviving argument
  deserves its own ruling with its own measurement. **Root: the seam offers two families that answer
  "what is this account worth" for the SAME account, and the CALLER picks.** X-g3b proved the
  resolution on the grid -- give the surface the rows that explain the modelled tiers, then let it
  render the modelled balance, and R-K's identity holds for all five kinds. The two candidate end
  states are "one producer, with the difference rendered" and "two producers that legitimately answer
  different questions, with the NAVIGATION that equates them fixed instead"; the trace decides.
  Live on the developer's own default screens.
* [ ] **X-k** `fix(recurring): the recurrence bound is reconciled, not stored and forgotten` --
  closes **N-18**, **N-19**, **N-23**, **N-24**. **Ruling R-AP, taken AGAINST the recommendation**:
  they stay in this ledger and get a step. The ground the recommendation rested on SURVIVES as the
  step's scoping rule -- X-k touches the recurrence engine and the transfer write door and NOT the
  seam, so it must not grow into one. **The "shares no file with any other remaining step" half is
  FALSE, corrected 2026-08-08**: the recurrence redesign's `R5` edits `recurrence_engine.py` and
  `transfer_service.py`, both of them X-k's. Block 9's one-file overlap was measured against the two
  parked TAGS and never against this step. **Sequence X-k AFTER `R5`**, which deletes
  `compute_due_date` and re-keys the generation index; rebasing X-k over that is cheaper than the
  reverse, and X-k's own root is untouched by it. **Root:
  `RecurrenceRule.end_date` is a stored derived value never reconciled against what was actually
  GENERATED**, and the write door's refusals have no consistent batch contract: one refused loan
  payment rolls back an entire carry-forward batch, and three generation call sites have no
  `ValidationError` handler so a refused write 500s on extend and unarchive. The money consequence
  is on a balance screen: a shadow generated past a bound that later moves EARLIER keeps its
  checking-side expense leg.
* [x] **X-l -- the pay calendar answers any date.** `4f134bf4`. ONE step under three names, so rule 11 keeps its row while its record left for `archive/five_shipped_steps_2026-08-26.md`.
* [ ] **X-m** `refactor(growth): the projection engine takes its axis, not its boundaries` -- closes
  **N-86**. **Root: `growth_engine.project_balance` takes a derived boundary as an ARGUMENT its
  caller must compute to match the window the caller also passes**, and nothing checks that they
  agree. That is Section 8's rule verbatim -- an argument a caller can get wrong is a defect, not a
  contract. **Cost when a caller gets it wrong: `$1,000.00` of annual-limit room per period of
  divergence, compounded over the horizon.** Both live callers are correct today and pinned in both
  directions, but the app carries TWO correct YTD boundaries whose difference is invisible in the
  rendered figures, and a THIRD projection surface would have to KNOW the rule. The fix is a
  signature change: trace every caller before touching it.
* [ ] **X-n** `fix(loan): a redistributed payment carries its REAL installment` -- closes **N-36**.
  **Root: `_redistribute_to_distinct_months` OVERWRITES the fact instead of carrying it.** Its own
  docstring is the evidence: it shifts a colliding payment's DUE date to the next month so the
  monthly engine does not sum two into one, and says only the due date shifts because `payment_date`
  is a FACT carried through untouched. **Both are facts** -- the real installment a payment
  satisfies is as much a fact as the period its cash moved in, and the redistribution destroys it.
  The codebase therefore holds one question with two keys ON PURPOSE (archived ruling D5 keys the
  split inputs on the DUE date; `rate_period_engine._replay_from_anchor` stayed on
  `payment.period_start`), stated at the site so it cannot be rediscovered as an accident. Contained
  today because the replay's rows are DISCARDED whenever a `confirmed_view` is supplied -- and
  containment is not a fix.
* [ ] **X-e** `refactor(accounts): current_anchor_balance is a reconciled cache or it is nothing` --
  carries **N-96**, **N-85** and **X5**. **WIDENED 2026-07-27 (R-AO): the one-liner is
  not the step the code describes.** Most of the census is being taken by X-f1c3a..c, which delete
  the columns outright; what remains here is what those leaves do not reach -- the two callerless
  public seam entries N-96 / N-85 name (the de-duplication rationale in `balance_at/_loan_interest.py`, N-180, dissolved at `recurrence:R16-c-1` `c88ed6ba`: there are no two sets to differ), and X5's backdated-assertion option, now largely answered by `observed_on`. **Its census is HISTORICAL and must be re-taken before this step is scoped**: it was
  measured when the columns still existed, and `balance_at/_investment.py` was deleted outright at
  `17c57cde` (its modelled-asset work went to `_asset_fold.py`).
* [ ] **X-p** `fix(analytics): the calendar's chips and its balance line are on one clock` -- closes
  **N-58**. (**N-97** closed 2026-08-14, `7a466d31`; the "same day" guarantee beside its dead
  citation had been false since X-c2b2 -- which is N-58.)
  **Sequenced AFTER X-f by that finding's own ruling**, and the reason is not
  caution: X-f shrinks the date noise at its SOURCE, so ruling before it would decide the question
  against numbers X-f then changes. It is scheduled, not deferred. **Root: one day cell renders two
  facts on two clocks with no row explaining the gap** -- the chips are placed on the BUDGET
  attribution date, the balance line steps on the day the money MOVED. Measured: `paid_at - due_date`
  is median 2 days, p75 6, max 25 across 130 settled Checking rows. The option space: place the chip
  on the cash clock (which changes which MONTH a row appears in), give the calendar R-O's treatment
  (a reconciling figure), or rule the divergence acceptable and label it.
* [ ] **X-ab** `refactor(ledger): one asset-vs-liability rule, read and write` -- closes **N-122**
  (R-CU). `ledger_account_service.ledger_class_id_for_category` asks X-z's question a second time on
  the POSTING path, and the two agree by reading. **It is not a residue fix**: it decides which
  ledger class a real account's paired posting account carries, so it is the write path, and Section
  8's "a DRY refactor of a PREDICATE can move money" is the case rather than the caveat. Its trace
  must answer what a re-class does to accounts that already carry postings. It also decides whether a LINKED ledger account's class is derived from its account's type, which deletes `sync_linked_ledger_class` (today it re-snapshots only an unposted account's class, on a re-type that crosses the asset-liability line), or is a posting-time fact (**R-BAL263**).
* [ ] **X-ac** `refactor(savings): one liquid-savings reduction` -- closes **N-121**. The cockpit
  reduces `_sum_liquid_balances` over one `account_data` TWICE per render and publishes the answer
  under two context keys -- ruling **R-AZ**'s "one fact under two keys" beside a redundant
  computation. Collapsing it changes what the page publishes, so it wants its own commit
  and its own render diff.

### Phase X -- the gate and vocabulary residue

* [ ] **X-ag** `feat(pylint): lax digit acceptance is refused, not remembered` -- closes **N-139**.
  X-ae converted every submitted-id surface it found and the AST then found exactly one digit-
  predicate call site in `app/` and `scripts/` (the implementation of the replacement). Nothing stopped
  the next one: `app/services/statement_import/_secu_csv.py:358`'s item count, `isdigit()` then `int()`, is a second, so the predicate sites read (census 2 code lines `\.is(?:digit|decimal|numeric)\(` in `app/**/*.py`) and (census 0 code lines `\.is(?:digit|decimal|numeric)\(` in `scripts/**/*.py`); and the same file's `_STATED_DAY` (:132) is a spelling no predicate census sees, a `\d` match read through `int()` (:474), which takes Arabic-Indic digits as a day. This step's instrument must fire on both, and the step moves each onto `app/utils/digit_strings.py` (**BAL-605**; the day is **N-139**'s own class). **What this step must NOT do is what its first two drafts specified**, both refuted
  before it was written: matching `isdigit` / `isdecimal` / `isnumeric` by METHOD NAME reports CLEAN
  over the defect it exists for, because a bare `try: int(raw) / except ValueError` passes it -- and
  that is precisely the form the first ruling specified and measurement rejected. The instrument is
  an open question the step's trace decides, and it must be shown FIRING on a planted defect.
* [ ] **X-ah** `fix(routes): a query-string id is parsed like every other id` -- closes **N-142**.
  The one submitted-id surface X-ae did not convert: the query args read as
  `.get(..., type=integer_arg)` (census 31 code lines `request\.args\.get\([^)]*type=integer_arg\b` in `app/**/*.py`),
  and `steps.md`'s marker counts every such coercion, wrapped calls, `grid/page.py`'s local `request_args`
  and `_render_helpers.py`'s `HX-Current-URL` read included. **X-dj** (`da8d8df6`) replaced `type=int` with
  `integer_arg`, which is `int()` held to a PostgreSQL integer's range and NOTHING more, on purpose (its
  docstring leaves the spelling to this step): Werkzeug still catches the `ValueError` (so no crash), and
  `'١٠٦'` is still 106, `' 2026 '` 2026 and `'1_0'` 10. **It needs a per-site ruling, which is why it is a
  step**: the path parameters were all row ids and the schema fields all row ids, so each took one blanket
  rule, but these are MIXED -- `account_id` and `period_id` are row ids while `year`, `month`, `offset`,
  `periods` and `show_all` are not, and `offset=0` / `show_all=0` are meaningful, so a blanket
  `parse_row_id` would silently refuse them. Owes a second small rule in `digit_strings`: ASCII-strict,
  canonical, but admitting zero. **Eight code sites hand their integer to a query** (X-dj's census,
  2026-10-05; graded by 9 requests over the 8 in `tests/test_routes/test_an_integer_no_column_holds.py`):
  `grid/page.py:294`, `grid/partials.py:94`, `analytics.py:180` and `:453`, `transactions/forms.py:545`
  with `:547` (one lookup), `salary/cockpit.py:83`, `_recurrence_preview.py:403` and
  `_render_helpers.py:539`; a ninth is MASKED, `analytics.py:679`, where `_resolve_window_params` reads
  the same `period_id` behind `:453`'s ownership read. Once each site parses by its meaning,
  `tests/test_arch/test_a_query_string_integer_is_bounded.py`, X-dj's GUARD against `type=int`, is
  deleted, as its docstring says. **The schema door holds the same class** (**BAL-602**): eight `fields.Integer` declarations of six fields write an `integer` column with no upper bound, `app/schemas/validation/categories.py:17` (`sort_order`), `loans.py:107-108` and `:154-155` (`arm_first_adjustment_months`, `arm_adjustment_interval_months`) and `settings.py:52`, `:55` and `:58` (`low_balance_threshold`, `large_transaction_threshold` and `anchor_staleness_days`, which `app/routes/settings.py:342-344` stores as loaded), every such field an AST census of `app/schemas/` found on 2026-10-06. Each is held to int4's range, `MIN_INTEGER_COLUMN` to `MAX_INTEGER_COLUMN`, inside any narrower bound it already has (no CHECK forbids a negative `sort_order`, so a negative stays legal), and a value outside it is a validation error, not a 22003 500.
*The `X-br` family -- the fresh container per run, its container and four leaves -- is ARCHIVED to `archive/x_br_family_2026-09-09.md` (2026-09-09, developer's call under rule 4). Every id still resolves in `steps.md`.*
* [ ] **X-cq** `fix(savings): the horizon's asset band reads the fold for plain cash` -- closes
  **BAL-520**: `_horizon._asset_bands` holds a PLAIN asset at today's balance compounded at 0% and folds
  no planned row, while `_liability_band` reads the fold since `credit_card:CC-1`, so a planned card
  payment funded from checking reads as a net-worth GAIN (`$80.00` at every future sample on the
  planted card). One rule for both sides: the asset band reads `balance_at_dates` too; the loan
  asymmetry the horizon always had (loans amortize, checking holds flat) ends with it. Reward tier: a
  figure the developer reads moves to the correct one. Minted 2026-09-18 at CC-1's tick.
* **X-cg is CANCELLED** (**R-BAL269**, 2026-10-11): its 'after a partial restore' premise is false for the plain-dump restore the sweep runs, and on the live database the same constraints refuse the state, so `X-dp` deletes DC-06 as a re-ask; **BAL-481** and **BAL-487** went to `X-dp`.
* [x] **X-cr** `fe53fdc5` -- every test-client request runs on a `flask.g` of its own (**R-BAL189**): `tests/_per_request_globals.py`'s `PerRequestGlobalsClient`, installed on the `Flask` class by `tests/conftest.py`, swaps the current app context's `g` for a fresh one for each request and puts the test's back after it, so a second signed-in client is its own user and nothing a request caches on `g` outlives it; `tests/test_integration/test_each_request_has_its_own_user.py`'s 13 cases fail without it. Built as first written -- a whole fresh app context per request -- it failed 110 tests (measured 2026-10-04: Flask-SQLAlchemy keys `db.session` on the app context, so the test body stops sharing the request's session), so that half and **R-CC123**'s deletion go to **X-di** (**R-BAL200**), and `app/db_transaction.py`'s teardown now says the command arm stays until then. The workarounds the fix made dead are deleted (the `_reset_login_cache` helpers in five adversarial files, an inline `g.pop`, the per-sign-in contexts of two race helpers, a re-login branch and a same-client sign-out), and four tests that move the clock past the idle timeout sign in again after the move (**R-BAL191**). Closed **BAL-521**, with **BAL-495** and **N-550** merged into it.
* [ ] **X-cs** `refactor(schemas): the two shared bounds have one spelling` -- closes **BAL-522**: CC-2
  declared `_RATE_FRACTION_RANGE` and `_DAY_OF_MONTH_RANGE` in `schemas/validation/_helpers.py` and
  re-pointed only its own fields; 19 inline `[0, 1]` and 3 inline `1..31` spellings remain across seven
  modules, and `_pay_rhythm.py:96` already held a `_DAY_OF_MONTH_RANGE` reading the model's constants,
  so the day bound has two same-named constants until this folds `_helpers.py`'s onto it. A schema-only sweep,
  its census re-run first; `$0.00`.
* [ ] **X-ct** `refactor(services): one effective-dated walk` -- closes **BAL-524**: CC-3 extracted
  `app/utils/effective_dated.in_effect_on` for the escrow versions and the card's APR; six more
  spellings stand (`rate_period_engine` x3, `amortization_engine/_projection` x1,
  `template_amount_service` x2; four with a fallback of their own, listed in the row) and fold onto it,
  each fallback stated at its call; three walk `start_date` or dict keys, so the leaf gains a `key=` or
  the caller adapts, not a drop-in; `$0.00`.
* [ ] **X-cj** `fix(audit): a system-authored audit row names its origin` -- closes **BAL-497**.
  A migration's writes and the login door's own write carry no user; the row names its origin
  instead of a NULL author, so an empty `user_id` never reads as unknown. Ruled 2026-09-15.
* [ ] **X-ck** `fix(seam): a movement's name is written once` -- closes **BAL-505**:
  `_record_onto` rewrites the movement's name on every re-record where R-BAL39 says once. Its
  dialog half, **BAL-504**, closed at the tick of `credit_card:CC-5-4a-5`'s first leaf, `ff86849b`.
* [x] **X-cl** `457135d9` -- deleted `tests/manual/measure_entry_reconcile_rules.py`, born dead in
  `b305b7b5` (S1-c), whose migration `d7c1f4a9e603` dropped `is_cleared` and renamed `entry_date`:
  it could never RUN, though pylint E/F passes it (**BAL-533**). Closed **BAL-510**.
* [ ] **X-cm** `fix(cash): the constant-offset signature reads per run` -- closes **BAL-512**:
  `BankAgreement.constant_offset` is account-wide while the walk anchors per run (**R-BAL63**), so
  one run's wrong starting figure goes unreported beside a run whose is right; per `RecordedRun`,
  rendered in its block; the account-wide property then has no reader and goes. **Closes BAL-514 too**: a standing level NO run reaches is listed on the agreement page in a block of its own, so the statements page and the agreement page agree about what the owner asserted (whether such a level is PRICED is that block's design question).
* [ ] **X-cn** `fix(transfers): the transfer door refuses an unknown key` -- closes **BAL-513**:
  `update_transfer` documents silently ignoring an unknown kwarg where `update_entry` refuses one,
  so a renamed key in a caller's bag settled the row at its plan in silence (two fixtures, 3e-1).
* **X-co is CANCELLED** (**R-BAL269**, 2026-10-11): `X-cx`'s card L8 subsumes it, since nothing new is built into `tools/plan_gate` and L8 deletes `STANDING.md` and rewrites the SessionStart hook; **BAL-515** went to `X-cx`.
* [ ] **X-cp** `refactor(tests): dead controls made honest` -- closes **BAL-518**, **BAL-519**:
  `test_grid`'s uncompared `baseline`; `test_race_conditions.py`'s one live request per race. (Its
  other two rows, BAL-516 / BAL-517, closed at `X-bi-4b-2`: `settlement_columns` and its 16 copies
  were DELETED with the columns, not consolidated.)
* [x] **X-cv** `832c30bd` -- entrypoint step 3 is all-or-nothing (**R-BAL105**'s future step; **R-BAL109**..**R-BAL115**, **R-BAL118**..**R-BAL124**, **R-BAL127**, **R-BAL129**): `scripts/init_database.py` runs the schema build or the migrations (through `app/migration_runner.py`, the runner the test template shares), the reference seed, `ref_cache`, the three deploy hooks (existing databases), the tax seed and the audit-trigger check in ONE transaction it opens and commits once, the session joined to it with autobegin off; `deploy/shekel-deploy.sh` stops a target-image container before it re-reads the stamp `FOR SHARE`, saves the failed container's log beside the dump, and re-pins or refuses by what that stamp shows. Closed **BAL-535**, **BAL-536**; filed **BAL-540**.
  Leaves 1 `a8480560`, 1b `5f83439c`, 2 `be1bdb35`, 2b `c6efbbe7` + `832c30bd`; no migration, no money moved; rehearsed on `c6efbbe7`'s tree on a throwaway stack (**R-BAL127**; the S0 + S5 re-run on the merged tree is owed before the release). A failure after step 3's commit in a release that adds a migration (a failure in the first-boot user seed, the static-file copy, the app start or the health check) still meets `refuse_to_repin`, which names the no-loss restart when the new image resolves the stamp; one that adds none leaves the stamp where the previous image resolves it, and `shekel-deploy` re-pins (the rehearsal's S6). Record: `archive/x_cv_as_built_2026-09-23.md`.
* [ ] **X-cw** `feat(hooks): a real production value is refused in anything committed` --
  **R-BAL132**'s build step. The repository is PUBLIC, and its documents, code comments, tests and
  commit messages have carried real production figures; the developer kept it public and ruled that
  a real value be refused at commit time instead. **Decided (the ruling):** a local, UNTRACKED list
  of real values -- balances, paycheck amounts, merchant names -- generated from production, which
  the pre-commit hook refuses the way gitleaks refuses a secret; committed text uses made-up
  figures, measured production figures stay in the handoff folder outside the repository, and what
  is already public stays public. **The building lane designs, with the developer:** where the list
  lives and how it stays out of every commit; how it is generated from production READ-ONLY (a
  same-day dump or a clone) and refreshed; what counts as a real value without drowning in false
  positives against synthetic test amounts; which surfaces are read (the staged diff, the commit
  message, both) and by which hooks; how a refused session is told what to write instead; where that
  practice is written; and whether "stays public" means nothing already committed is rewritten. It
  moves no money.
* [ ] **X-cx** `chore(plan): the plan moves to a private tracker and the coordinator retires` -- the developer's **R-BAL170** (2026-10-03, with **R-BAL171**..**R-BAL174** and the build plan he approved as written the same evening, since amended by its piece L2's rulings, among **R-BAL177**..**R-BAL206**, and by **R-BAL212**..**R-BAL217** and **R-BAL219**..**R-BAL223**, **R-BAL227** and **R-BAL228**, each arc's reasoning staying in the repository): the steps, findings, rulings and questions in `docs/plans/` move to a private GitHub Issues tracker with an ordered board, each session files and closes its own items and merges its own work into `dev`, releases run on the developer's word through a `/release` skill, and the coordinator session retires once the move lands. ONE step here: its leaves are sub-issues of its tracker card #1, never rows in `steps.md`, and it never ticks SHIPPED in this file, which the cutover deletes; until that cutover `steps.md` stays the plan of record. **BAL-543** and **BAL-544**, filed against this id's earlier meaning (**R-BAL130**'s build), keep naming it and are re-read by its leaf L6. Closed by its leaves so far: **BAL-608** and **BAL-609**, by the rename step's leaf A (`027573fe0`, card plan#27). Two cards carry O7's clauses (**R-BAL263**): L8 has the code-reviewer agent Read the rule files rather than restate them (**BAL-629**), and one card gives `Tracker` a read-only client so only `Board` writes; **BAL-515** is L8's, re-homed when `X-co` was cancelled (**R-BAL269**).
* [ ] **X-df** `chore(tests): the test image is production's own PostgreSQL` -- owns **BAL-572**, **BAL-573** and **BAL-659** (**R-BAL193**, 2026-10-04; **BAL-659** placed by the coordinator, 2026-10-16). The test image's base is pinned in `scripts/build_test_db_image.py` while `docker-compose.yml` and `docker-compose.dev.yml` pin production's, three homes for one fact kept in step by hand, and the builder's comment names a dev-compose test-db service `X-br-4` deleted: the builder reads the version from `docker-compose.yml` instead. The suite's cluster raises `max_locks_per_transaction` to 256 (`scripts/test.sh`) over PostgreSQL's default 64, which no production compose file changes, so only the image bake, the whole migration chain in one transaction, still runs at production's size, and nothing says so: the lock table the deploy's migration runs under is stated once and the bake is checked against it. The bake step names docker's own failure rather than a port collision it never checked, while still reporting a genuine one (**BAL-659**; **N-459** is `X-bs`'s). Tests and tooling only, `$0.00`.
* [ ] **X-dg** `fix(tests): a fixed date follows the test clock` -- owns **BAL-592** and **BAL-593** (**R-BAL194**, 2026-10-04). `tests/test_routes/test_dashboard.py`'s `test_debt_track_renders_marker_and_debt_free` builds a never-paid fixture loan from 2026-01-01 whose payoff outlook leaves `_PAYOFF_EXTENSION_MONTHS` from about 2030-08-02, and `tests/test_routes/test_templates.py`'s `_loan_payment_template` ends a rule on 2030-01-01 whose start follows the test calendar, refused by `ck_recurrence_rules_valid_window` by 2030-03-04 (computed) in its seven callers: each date becomes relative to the clock the code reads (`#501`'s `db7498a84` is the shape), the weekly calendar sweep gains a far-future date (2031) so the class cannot return unseen, and `tests/test_models/test_recorded_on_downgrade.py`'s silent-case message stops blaming the backfill for a row `seed_user` writes. `tests/test_routes/test_accounts.py`'s `TestCashDetailContext::test_anchor_as_of_is_event_date_not_period_start` (:6939) compares the database-stamped `created_at` with a period start the frozen clock places, so it gains the `server_clock` marker its sibling `test_checking_detail_shows_anchor_date` already carries, **R-BAL198**'s remedy for that class (**BAL-604**). Each test edit goes to the developer under rule 5. Tests only, `$0.00`.
* [ ] **X-dh** `refactor(tests): row-id carriers are one category` -- owns **BAL-594** (**R-BAL195**, 2026-10-04; `~` **N-139**, `X-ag`'s). The fields that carry row ids inside a larger value -- `PurchaseDestination`, `ReviewedRowField`, `ReviewedDifferenceField`, `RuleAnswerField` and `ShownIds` -- sit in `tests/test_schemas/test_row_id_field.py`'s `_NON_INTEGER_FIELD_SPELLINGS`, so the shared strict-id check skips them and each has a hand-written test whose copy of `LAX_SPELLINGS` has drifted (7 of its 8): they get a category of their own, ONE parametrized test runs every canonical spelling against every id each carries, an arm asserts the not-an-id-field list holds only real non-id fields (how it tells them apart is X-dh's design), and the hand copies are deleted. Tests only, `$0.00`.
* [ ] **X-di** `test(conftest): each test request gets its own context and session` -- owns **BAL-595** (**R-BAL200**, 2026-10-04). **X-cr** gave each test-client request a `flask.g` of its own but left it inside the test's app context, so it still shares the test's `db.session`, and **R-CC123**'s end-of-save rollback in `app/db_transaction.py`'s request teardown stays to end the transaction a save leaves open on that session. This step gives each test request an app context, and so a session, of its own, as production does; reworks the tests that then read a row through a session the request no longer shares (110 failed when that design was measured on 2026-10-04: rows a request deleted still in the test body's identity map, stale amounts, the body's uncommitted setup invisible to the request); then deletes the command arm. **X-dc**'s design round owes a ruling on **R-CC123** too and may change what that line does first. Tests and one app line, `$0.00`.
* [x] **X-dj** `da8d8df6` -- one database driver, psycopg 3, under SQLAlchemy 2.1.3 (**R-BAL208**): `requirements.txt` pins `SQLAlchemy==2.1.3` and `psycopg[c]==3.3.6`, psycopg-c built in the image's builder stage against Debian's libpq as psycopg2 was (**R-BAL209**: a grid render's median 109 ms, against 103 ms on psycopg2 and 176 ms on pure-Python psycopg), and the runtime stage sets `PSYCOPG_IMPL=c`, so an image without psycopg-c fails at `create_app` rather than falling back silently. psycopg2 is gone; the URL scheme stays `postgresql://`, so production's `DATABASE_URL` does not change; one `_DRIVER_CONNECT_ARGS` (`connect_timeout` 5, `prepare_threshold` None) serves every configuration, an **R-BAL207** application. psycopg 3 binds an `Integer` with a server-side `::INTEGER` cast, so an integer outside int4 that reaches a query is a 22003 500 where psycopg2 matched no row: `MIN_INTEGER_COLUMN` / `MAX_INTEGER_COLUMN` have one home in `app/utils/digit_strings.py`, read by `parse_row_id` (an id above the ceiling names no row: 404 as before), `RowId`, the recurrence form's two counts and `integer_arg`, which replaces every `type=int` query-arg coercion, an out-of-range value taking the site's default; `tests/test_routes/test_an_integer_no_column_holds.py` grades the 8 sites whose integer reaches a query with 9 requests, and `tests/test_arch/test_a_query_string_integer_is_bounded.py` is a GUARD **X-ah** deletes. A refusal is read by SQLSTATE, not by class (**R-BAL210**): the tests assert the code and the message through `refused_by_database_rule`, `app/utils/db_errors.py` gains `sqlstate_of` and `is_schema_behind_code`, and the two bootstrap tolerances absorb only `SCHEMA_BEHIND_CODE` (42P01, 42703) until **X-dl** deletes them. Three tests' expectations moved with the developer's rule-5 OK (**R-BAL211**). No finding. Its money check is the release rehearsal on a same-day copy of production, zero read differences required, not yet run at this tick (planned for 2026-10-07).
* [x] **X-dk** `aa8a1c53` -- Werkzeug 3.1.9 (Dependabot; advisory GHSA-g6x2-hccm-hh4m is Windows-only, `safe_join`'s NT branch) and the two of its changes this codebase documents, each restated BY SYMBOL; `RowIdConverter` answers the same on both versions. **The stock int converter refuses an oversized digit run** (pallets/werkzeug#3237): `app/url_converters.py`'s module, class and `to_python` docstrings rest the regex-only argument on what the stock `to_python` still admits (`0`, `007` and a forty-digit id no `integer` column holds, **R-BAL211**); `tests/test_routes/test_url_id_converter.py`'s `test_the_stock_converter_refuses_an_oversized_run` pins the stock answer and `test_the_stock_converter_still_admits_every_digit_script` the half Werkzeug left open; and `TestARefusalEndsMatching` grades both converter layers, until then ungraded: a `ValidationError` ENDS matching, answered by the login gate's redirect for an anonymous caller, else a 404, or a 405 where another method's rule on the same path was visited first. **A urlencoded body is bounded only by `MAX_CONTENT_LENGTH`**, which **R-BAL218** keeps at 512 KB as the ONE cap on every request body, amending **bank_import:R-BI2**: `app/config.py`'s note states it, `tests/test_config.py`'s `TestTheUploadCeiling` pins 524,288 and grades it through the app's own request class (a form body at the ceiling parses, one byte over is a 413), and `StatementMatchSchema`'s note, re-measured on 3.1.9 at the crafted tick, `MAX_BATCH_ITEMS`'s note and two test docstrings are restated to it. 3.1.9's other changes were censused against this codebase from the full source diff: #3231's rewritten parsers change nothing here, #3236, #3248, #3255, #3254, #3189 and #3253 are not reached, and the rest is the development server's. Closed **BAL-598**. Its money check is the release rehearsal on a same-day copy of production, not yet run at this tick.
* [ ] **X-dl** `refactor(app): an entry point that migrates builds no seed and no ref cache` -- reserved by the coordinator 2026-10-05 from **X-dj**'s first review (its H1, option (b); no ruling) and cited from the code, `app/utils/db_errors.py`'s note on `SCHEMA_BEHIND_CODE`. An entry point that migrates builds the app BEFORE the migration it exists to run: the development entrypoint runs `scripts/init_database.py`, whose `create_app(init_ref_cache=False)` still runs the development and testing reference seed (`app._seed_ref_tables`), and `flask db upgrade` builds it with the seed and the ref-cache load (`app/ref_cache/_state.py`'s `_load_rows`) both. So do the hosts that build the testing app: `scripts/build_test_template.py:467` calls `create_app("testing")` on the empty database it has just recreated, before `_populate_template` migrates it, so every test-image rebuild leans on both tolerances, and `tests/conftest.py:1338` builds the suite's session app the same way, though on a clone of that template already migrated. The first three can then meet a table or a column a pending migration has not built yet, and each absorbs that by SQLSTATE through `is_schema_behind_code` and `SCHEMA_BEHIND_CODE` = {42P01, 42703}: **X-dj** narrowed both from a whole-`ProgrammingError` catch, which under psycopg 3 also holds a trigger's refusal, and its first review measured 42P01 alone crashing the development bootstrap when a pending migration adds a ref column. This step makes every such entry point build the app with neither the seed nor the cache, its trace first enumerating each host that builds the app before its schema (`_seed_ref_tables`' docstring also names test setup), then deletes both tolerances, `is_schema_behind_code` and `SCHEMA_BEHIND_CODE`, with the code only they feed: `UNDEFINED_TABLE` / `UNDEFINED_COLUMN` (`app/utils/db_errors.py:52-53`), `ref_cache.init`'s `unavailable` list (`app/ref_cache/_state.py:308-313`) and `create_app`'s partial-init branch (`app/__init__.py:236-246`). The tests that pin the tolerances -- `tests/test_utils/test_db_errors.py`'s `TestASchemaBehindTheCodeIsReadOffTheCode` and `TestTheTwoBootstrapTolerancesReadTheCode`, and `tests/test_ref_cache.py`'s `test_init_records_unavailable_table_and_keeps_others_usable` (**R-BAL211**'s third test) -- change only with the developer's rule-5 OK, which this step asks. No finding; `$0.00`.
* [x] **X-dm** `d0be0eef` -- the flaky deploy behaviour test was a bug in the production deploy script (leaves `dcab5248` and `d0be0eef`). **The cause:** `deploy/shekel-deploy.sh`'s `image_resolves_revisions` piped a multi-line listing from `printf` into `grep -q` under `set -euo pipefail`, and a `grep` that matched early and left before `printf`'s next write killed it with SIGPIPE, so a LISTED revision read as absent in all three callers (the pre-flight, `repin_is_safe`, `refuse_to_repin`). It is now a bash `case` over the listing with no pipe, graded by one test per caller on a listing larger than a pipe, and the module's reads that raised before an assertion's message could run (`calls.index` and its kin) are assertions that print the run. **R-BAL252** swept the shape's 15 other sites (the `:latest` digest matched in bash; `scripts/reconcile_prod_to_canonical.sh`, `scripts/deploy.sh`, `scripts/test.sh` and `scripts/hooks/stop-check.sh` read without an early-leaving reader); **R-BAL254** gave the shell scripts ONE running check, `scripts/_container_lib.sh`'s `container_running`, read by `deploy/shekel-deploy.sh` (which loads it as its first act and refuses to start without it), `scripts/_backup_lib.sh` and `scripts/restore.sh`. Closed **BAL-618** and **X-bs**'s **BAL-509**, **N-408** and **N-510**: holding `printf` between its writes reproduced all four on demand on the old script and none on the fixed one, which also refuted their suspected cause, the fake-docker shim's digest-substring match. Filed **BAL-623**, **BAL-625** (owner **X-do**) and **BAL-626** (owner **X-bs**). `$0.00`.
* [ ] **X-do** `refactor(deploy): one digest shape, and the deploy scripts say what they do` -- owns **BAL-623** and **BAL-625**, the residue **X-dm** reported (minted by the coordinator at its tick, 2026-10-09). An image digest's shape is spelled five ways in three deploy scripts, so a change to what counts as a digest is made five times with nothing checking they agree (**BAL-623**): it gets one spelling. The deploy scripts' comments, packaging and refusals are made to say only what they do (**BAL-625**): `scripts/deploy.sh`'s note that `COSIGN_*` can be set in `.env`, the misleading refusal of a `scripts/_container_lib.sh` lacking `container_running`, `.dockerignore` letting that helper into the image, and `ci.yml`'s inert `find | head -1` under pipefail. **BAL-628** rides it: `docker-compose.yml`'s comment (:521-532) says the gunicorn tmpfs lets the control server initialise cleanly, which is false once the same container restarts and the mount comes back `root:root 0755`, so the mount gets a mode that survives a restart or the comment says what a restart does. Since `864d8e5e5` the current pin is checked whole (`deploy/shekel-deploy.sh:807`, `^sha256:[0-9a-f]{64}$`) while a digest argument still matches only the prefix `sha256:*` (:178), so the two inputs that shared one spelling now differ, and with the `:latest` match (:845, `sha256:[a-f0-9]{64}`) that script alone spells the shape three ways (**BAL-623**'s subject). It also owns what **X-cx**'s L9a-2a reported on that commit (granted by the coordinator, 2026-10-13): the app container's name `shekel-prod-app` gets one home (**BAL-644**), where today `docker-compose.yml:315` sets it, the shared nginx upstream dials it and the deploy and backup scripts default it under `SHEKEL_CONTAINER_NAME` or `APP_CONTAINER`; `take_dump`'s refusal of a backup directory it cannot create (`deploy/shekel-deploy.sh:504`) gets a test that drives it (**BAL-645**); and `deploy/verify_prod_deploy.sh` finds the app by its exact name, where `docker ps --filter name=` matches any name containing it (**BAL-646**), and stops passing a log read that returned nothing as clean, since over a window that excludes the app's start (the default `10m`) a healthy app writes no line at the default `INFO` level (**BAL-647**). The tests that pin the name or the exact `docker ps` call (`tests/test_deploy/test_verify_prod_deploy.py`, `test_deploy_configs.py`) change only with the developer's rule-5 OK, which this step asks. Deploy tooling only, `$0.00`.
* [ ] **X-em** `chore(deps): every package locked and kept current, and Valkey for the rate limits` -- dependency upkeep, minted by the coordinator from the developer's answers of 2026-10-14 (**R-BAL294**, **R-BAL295**, **R-BAL296**, **R-BAL298**); closes **BAL-653**. It covers `pyproject.toml` plus `uv.lock` with hashes, installed by CI, Docker and the local venv; gunicorn into the lock as a server-only group; `.python-version` as the one Python home for uv and CI, with the Dockerfile's FROM line checked against it; Dependabot's uv ecosystem proven on a branch (fallback: a weekly `uv lock --upgrade` workflow); GitHub auto-merge for grouped minor and patch updates on all-green (**R-BAL295**); and the weekly dependency release (**R-BAL296**). The test-db postgres pin read from compose is `X-df`'s (**BAL-573**, under **R-BAL193**) and is not this step's work. The compose images pinned by tag alone are pinned by digest (**BAL-653**). Valkey replaces Redis for the rate limits (**R-BAL298**) under a least-privilege ACL granting exactly the commands `limits` uses (strict, fail-closed), with a release rehearsal that hits a rate-limited route against the REAL store, because CI runs `memory://` and cannot catch a missing grant; its image is digest-pinned, and both redis `ignore` entries in `.github/dependabot.yml` are deleted with the switch. It ships as its own release after `X-bi-6-4d`'s. Tooling and deploy, `$0.00`.
* [ ] **X-en** `chore(python): the app moves to Python 3.15` -- the developer's **R-BAL292** and **R-BAL293** (2026-10-14), minted by the coordinator. The Python 3.15 bump, after `X-bi-6-4d`'s release and the GitHub Ubuntu 26.04 runner bump, which lands first because it edits the same workflow files. Three homes move in ONE pull request: the Dockerfile's FROM with a new digest, every workflow `python-version` setting, and the venv, whose Python uv pins apart from Arch's (**R-BAL293**); with `X-em` built, `.python-version` is the one home for uv and CI. Its gates: PyYAML has a cp315 wheel (or a ruled sdist build), psycopg-c compiles in the builder, pylint gives the same results, the suite passes, and the release is rehearsed. Its own release; it is meant to move no money, which the suite and the rehearsal grade.
* [ ] **X-eo** `fix(grid): a recurring row admits only an item that recurs` -- **R-BAL299** items 1 and 2 (2026-10-14), minted by the coordinator; closes **BAL-654**. The recurring arm of `grid_view_service._match_row_in_period` becomes `txn.recurs and txn.template_id == rk.template_id`, so a transfer's side, which never recurs, cannot join a recurring row whatever number it reports. A grid test seeds a transfer definition and a recurring transaction definition with EQUAL ids in one category and asserts the transfer is drawn once, in its own row; it is mutation-graded (TMPL-REL, and the rule's guard deleted). MEASURE first: the full suite and a grid byte-identity on a production clone (reasoned only: a definition's rows agree on `recurs`), and any transaction cell that moves is a STOP to the developer (the coordinator's condition, not part of **R-BAL299**). Its own small pull request off dev and its own release; it waits for `X-bi-6-4d-2` because its grid test lands in `tests/test_routes/test_grid.py`, which the 4d branch edits, so a dev edit before 4d merges collides. Display; `$0.00` by the reasoning above, which the measurement grades.
* [ ] **X-ep** `refactor(grid): a definition's identity carries its kind` -- **R-BAL299** item 3 (2026-10-14), minted by the coordinator; after `X-eo`. Typed definition identity on the grid's items, so 'transaction #5' is never equal to 'transfer #5', after a census of the `.template_id` readers that names which of them receive a transfer's side; it deletes the leg's constant `template_id = None` (`app/services/transfer_legs/_leg.py`) and its pins (`TestALegsDisplayProjections`' link reads). Its relation to `X-ci-3`: that step makes `transfer_template_id` NOT NULL, so every transfer gains a definition number (the premise of **R-BAL299**'s question), and its specification touches neither the leg's `template_id` nor the grid's match. Display, `$0.00`.
* [x] **X-cy** `75a80208` -- the audit trigger's WORK is the required check and its time only a report (**R-BAL144**; **R-BAL149**..**R-BAL154**): `tests/test_integration/test_audit_trigger_work.py`, in the ordinary suite (**R-BAL151**), runs the five workloads of `tests/_audit_trigger_workloads.py` twice each and asserts each run changes exactly its declared rows (25, 25, 52, 260 and 52; **R-BAL150**) with one `system.audit_log` row apiece and none for another table, and pins the sha256 of the trigger's cost surface, read live from the catalogue under `search_path = pg_catalog`: the function, every trigger on `budget.transactions` that calls it (found by `tgfoid`, not by name), that table's columns (**R-BAL154**) and `system.audit_log`'s shape (**R-BAL149**). Closed **recurrence:REC-533**.
  The report, `tests/test_performance/test_trigger_overhead.py`, times the same workloads with the trigger on and inside `audit_triggers_off()` and asserts no time (**R-BAL152**; the suite's 50 s hang timeout still applies, **R-BAL153**), and CI's serial step on shard 0 prints its five blocks. **The fingerprint grades the module's function as the test template installs it, NOT what a migration would leave in production** (**BAL-556**, filed at this step's tick).
* [ ] **X-bw** `fix(migrations): the downgrade restores a paycheck's OWN figure` -- owns **BAL-464**.
  `_RESTORE_FROM_DEFINITION_SQL` restores the template's `default_amount` rather than the row's own,
  so the 38-step downgrade runs clean (exit 0, stamp back to `a4c6f1d92b73`) while flattening 43
  future paychecks and destroying the raise and bonus schedule: **-$9,677.29** of projected income,
  measured by diffing a downgraded clone against a pristine one, 1028 rows compared and 43 not
  round-tripping. **The cost is GATE INTEGRITY, not rollback**: `shekel-deploy` never invokes
  `flask db downgrade` -- it re-pins and REFUSES rather than making a second dead container, and a
  genuine rollback is the automatic pre-deploy dump. What the broken arm actually breaks is
  `CLAUDE.md` item 7, *migrations tested in both upgrade and downgrade directions*, which passes
  over a round trip that loses data. It also corrects the downgrade comment of `migrations/versions/b2d8f3a6c541_purge_pre_origination_loan_payments.py` (:222-225), which says each snapshot round-trips through `json.dumps` because psycopg2 decodes it to a dict, where the restore it sits on is one SQL `jsonb_populate_record` that never leaves the database (**BAL-603**).
* [ ] **X-bs** `fix(test): a failure must fail, and the last two ports go` -- owns **N-460** and
  **N-459**, two defects in the same files that HIDE EACH OTHER. Seven `pytest.skip` calls in
  `test_proxy_trust_and_headers.py` fire on something that RAN AND FAILED rather than on something
  missing, and the predicate is **did the capability EXIST? skip. Did it exist and FAIL? FAIL** --
  `:929` leads the row (a container started, then `docker port` unreadable), and `:470` proves it is
  a PRINCIPLE and not a docker rule, since `shutil.which("openssl")` passed nineteen lines earlier.
  Two published ports remain, `:911`'s `-p 0:443` and the builder's BAKE container, which needs a
  host port because the entrypoint's bootstrap `psql` hardcodes `/var/run/postgresql`.
  **THE ORDER IS LOAD-BEARING -- unmask FIRST, then the ports**: the three tests now skipping ARE
  the collision and the only witnesses that the laundering arms are wrong, so fixing the ports first
  leaves the laundering untested with its witnesses gone, which is strictly worse than today.
  **Also owns BAL-626**, the deploy behaviour harness that cannot yet render a whole failed run.
* [ ] **X-bt** `refactor(test): one producer answers whether a daemon is safe to spawn on` --
  `scripts/test.sh` ASKS the daemon (`docker info`) while `tests/test_deploy/conftest.py` matches a
  PATH ALLOWLIST, and the conftest cannot read the wrapper's answer because in every case it exists
  for there IS no wrapper. **The drift already happened**: both gate `CI` by PRESENCE, `CI=false` is
  truthy and sanctions the production daemon, and **X-br-3** fixed that in one home while
  `conftest.py:69` still carries it. Build ONE predicate both reach and DELETE the allowlist; it must
  separate UNREACHABLE from NOT-ISOLATED (**X-br-3**'s M6) and read `CI` through a vocabulary.
  **Its rank risk**: on GitHub `docker info` says NOT ROOTLESS, throwing 28 tests onto the `CI` arm.
  **N-461 OCCURRED 2026-09-05, during `X-br-4`**, which is this row's strongest evidence because
  it is not hypothetical drift: `build_test_db_image.py --force` run BY HAND built and committed
  its image on the SYSTEM daemon, the script reading no `DOCKER_HOST` while the wrapper sets one.
  Six `shekel-test-db` images, 489 MB each, oldest 17 h, so it has recurred since `X-br-1`; the
  bake CONTAINERS were cleaned up and the IMAGES were not, and nothing prunes them. **The session
  that did it had read the finding an hour earlier.**
* [ ] **X-by** `fix(test): a worktree's suite runs its own interpreter` -- owns **BAL-469**, the
  half `X-br-4` did not reach: the per-run cluster made a DATABASE unshareable and the INTERPRETER
  is still shared. Four of the seven worktrees hold no `.venv`, so `scripts/test.sh:114` falls
  through to `command -v python3` and their pytest resolves to the main checkout's -- measured with
  three full suites live at once, each correctly in its own cluster. **The comment is already
  wrong**: `:259` states every worktree shares one venv, and `shekel-reg2` and `shekel-r7dd` do not,
  so the step owes that line a correction as well as a fix. **The fence this deletes** is the
  convention that `requirements.txt` is pinned; peer detection does not depend on the sharing, since
  it reads `/proc/<pid>/cwd` precisely because argv would name the main checkout. `.claude/rules/testing.md:54` repeats the claim that every worktree shares one venv, and is corrected with it (**BAL-636**).
* [ ] **X-cc** `test(anchor): a fixture assertion is stamped at its own instant` -- owns
  **N-351**, and the CHECK is the SECOND half. `ck_anchor_history_typed_on_or_after_observed` was
  written, proven to refuse a planted row, and WITHDRAWN on 2026-08-25 because it collides with a
  pervasive fixture idiom rather than a stray test: the cash-walk suites freeze today, stamp every
  assertion at that instant and set `observed_on` across a projection horizon, so 10 row shapes in
  7 modules assert a balance for a day AFTER the instant they carry. Those fixtures are already
  unfaithful to production and the CHECK exposes that rather than causing it, which is why the
  stamping comes first and the constraint second. Production holds the invariant by CONVENTION --
  `anchor_service.resolve_observation_day` refuses a future `observed_on` and `recorded_on`
  defaults to the same day -- and the convention failed within a day of the column shipping, in
  `_test_helpers._restamp_assertion` across 54 call sites, where neither 418 green tests nor pylint
  could see it. `$0.00` and 0 of 82 dev rows violating; what it buys is a fence that cannot be
  held by habit.
* [ ] **X-ce** `refactor(app): a census in the code is dated or it is a command` -- owns
  **BAL-479**, and it is `conventions.md` rule 6 turned on the CODE. That rule makes a planning row
  RE-RUN its census; `app/` states hundreds in docstrings and comments and nothing re-runs any.
  **Not all of them are defects, and the classification IS the step.** A DATED record of a past act
  stands as written -- `loan_payment_service/_context.py`'s *102 modules against the 46 the replay
  needs (2026-09-09)* is a measurement with a date and a pointer, which is what a record should
  look like. A PRESENT-TENSE claim with no date and no command is a stored derivation beside no
  reconciler, in the code rather than beside it.
  **The specimen is three spellings of one measurement**: `url_converters.py` says *34 `type=int`
  sites*, `utils/digit_strings.py` says *42*, and `balance:X-ah`'s re-run says 34. Two of the three
  are docstrings nothing grades, and they are what made the FIRST TWO markers written under rule 6
  both wrong -- the census counted the sentences about the census as uses of the thing.
  `$0.00`, and what it costs is the next session's SCOPE. **The route tree's census is one, and it reaches past `app/`** (**BAL-606**): four files state 123 `<int:>` path parameters where the routes hold more, and the census that each is a serial primary key, the whole case for overriding Werkzeug's `int` converter, is re-run over the parameters added since, not restated.
* [ ] **X-cd** `fix(transfers): a transfer's settled amount refuses $0.00` -- owns **N-301**. **RE-RULED by R-BAL155 (2026-09-29): a `$0.00` payment is refused**, which retires **R-BAL15** (2026-09-11: "the bank moved `$0.00`" is a legal observation, under which this row's contradiction had DISSOLVED and the step was the seam rather than a bound change). So the contradiction RESOLVES BY REFUSING, as the `$0.00` study's filing list states it: the transfer update schema's `settled_amount` takes the exclusive minimum every other transfer money field has through the shared validator `cards._POSITIVE_MONETARY` (`app/schemas/validation/cards.py:45`: above zero, under `_NON_NEGATIVE_MONETARY`'s ceiling, read from it), in place of `_NON_NEGATIVE_MONETARY`'s inclusive minimum, so no second Range is spelled and the ceiling that field's comment calls load-bearing (a figure at or above `10 ** 10` dies at the database as a 500) is kept. Nothing moves on today's data.
* [ ] **X-bg** `feat(transfers): an occurrence that did not happen is not an archive` --
  closes **N-386**, whose row carries the measurement. **The door derives its own
  destructiveness from a link rather than from what the owner said**:
  `routes/transfers/mutations.py:378` is `soft = bool(xfer.transfer_template_id)`, so a
  template-linked instance can only ever be soft-deleted, and no second door reaches a
  SETTLED one -- the template hard-delete takes its archive fallback whenever any sibling
  is Paid and its bulk delete is narrowed to unsettled rows. The money does leave (the
  fold excludes `is_deleted`; `transfer_service._delete:68` reverses the posted effect
  before the rows go), so this is not a balance defect; the exposure is that a
  soft-deleted row still CLAIMS its occurrence and
  `transfer_recurrence.resolve_conflicts(action="update")` restores exactly that shape.
  **The remedy is NOT a wider delete**, which would only move the ambiguity: *this
  occurrence did not happen* and *archive this row* are two owner statements one flag is
  answering for both, and the recurrence engine already reads that flag as an owner HOLD
  (`_recurrence_common.owner_hold_on` -> `BLOCK_DELETED`). What the step owes is the
  second statement, and the arm that keeps a restore from resurrecting the first. Met
  live by `balance:X-f3c-2b-2c`'s account-10 repair, which did not wait for this: under
  **R-BAL255** its act 1 CANCELLED the duplicate transfer rather than soft-deleting it, so
  the soft delete's restore exposure was not incurred.
* [ ] **X-eq** `fix(grid): the cell card and the grid's navigation say and do what they show` -- owns **BAL-622**, **BAL-656** and **BAL-657**, minted by the coordinator at `X-f3c-2b-2c`'s tick (2026-10-15) from what that step's lanes met on the grid. The transfer card's two buttons reading "Cancel" become one that only closes the card and a visibly named cancel of the transfer, and a set-back transfer's one-click re-settle is shown as what it does (**BAL-622**); a refused save on a cell card tells the owner why instead of closing as a good save does (**BAL-656**); and the grid opens on any account and keeps it across its period arrows (**BAL-657**). It also owns **BAL-648** (placed by the coordinator, 2026-10-16): once a standing extra is set on a derived loan payment, a PAID payment's grid cell shows the NEW plan figure struck through beside what it paid. What a PAID derived payment's planned figure shows once the plan changes is a question for the developer, who rules it before this step builds that part.
* [ ] **X-bf** `test(harness): a template says which revisions it was built from` -- closes
  **N-385**, whose row carries the measurement. **`alembic_version` records which revision is
  HEAD, never which revisions RAN, and the `down_revision` graph is mutable** -- so a template
  built before a re-parent, a rebase or any chain edit carries the identical stamp and a
  different schema, and neither `scripts/build_test_template.py` nor `tests/conftest.py`
  compares the two. Measured on this arc's own branch: `d3b6f1c8a274` was re-parented onto
  `recurrence:R17`'s `c8e5a2f31b47` after the template was built, so the built chain FORKED past
  R17 and the template kept the unique index R17 replaces -- **16 failures across five modules
  in three arcs**, every one a `UniqueViolation` on the deleted index, reading exactly like a
  cross-arc regression while the stamp said head. **The remedy is a STATE comparison and the
  stamp is what failed, so asserting it harder cannot work**: record at build time the ORDERED
  revision list the builder actually applied and refuse a bootstrap whose repo chain no longer
  produces it, which a fork changes and a rebase changes. Two remedies are refused: stamp
  equality, measured GREEN on the broken template, and a `pg_indexes` spot check, which names
  one object where the next instance will be a different one -- the same shape as an allowlist.
* [ ] **X-be** `refactor(services): three modules are at the line ceiling, not near it` -- closes
  **N-365**, whose row carries the census and both instances. **Root: in a corpus whose docstrings
  ARE the design record, a per-module LINE cap binds what gets WRITTEN DOWN.** The fork, per module:
  SPLIT, or a ceiling with a stated reason for its number -- raising one to fit is what
  `conventions.md` rule 4 refuses about plan caps, and that argument does not change for code. A
  split is a design decision, so the deliverable is three proposals rather than an edit.
  **A FOURTH firing, 2026-08-30, and it is COUNTER-evidence to N-365's framing** (measured by the
  `bank_import:X-gj-1b` session): a wire-format helper added as a property on `MatchProposal` took
  `services/statement_match/_offers.py` to **1,014** lines and **no prose was cut**. The ceiling was
  right and the HOME was wrong -- the helper is a WIRE transform, `jinja_filters.py` exists for
  exactly that, and moving it there left `_offers.py` at **984**. So a ceiling can find a
  MISPLACEMENT rather than cost a design record, which argues the SPLIT arm; and `_offers.py` is a
  fourth module in a different tree from the three N-365 names. It belongs in this specification and
  not in N-365's row because rule 4 makes a ledger row an INDEX entry, and the row could only have
  absorbed it by trimming its own prose -- which is the harm N-365 IS.
* [ ] **X-al** `fix(pylint): a duplicate-code disable that suppresses nothing is a finding` --
  closes **N-154**. `useless-suppression` is enabled precisely so a stale disable is reported, and
  it is BLIND to a `duplicate-code` one -- measured both directions: removing a disable left
  `pylint app/` at 10.00/10, and planting one back left it at 10.00/10 with no `I0021`. **The
  blindness is upstream, not a misconfiguration** (R0801 is a close-time checker over a similarity
  graph, so suppression accounting has no line to credit). The likely shape is a pre-commit arm that
  strips each disable in turn and fails if the tree stays clean without it; the instrument is not
  ruled here and must be shown FIRING on a planted stale disable. **SIXTEEN live `duplicate-code`
  disables remain in `app/` and not one has been re-measured** -- 5 in `models/`, 4 in
  `routes/`, 7 in `services/`, a breakdown that summed to FIFTEEN beside this sixteen until
  2026-09-11. The step's first deliverable is the census.
* [ ] **E2-0** `the membership trace` -- NO code. Answer from the code: which modules are members,
  what the public re-export surface is, whether `account_projection` is in or out, and whether
  `ledger_report_service` is (**N-35**). Re-run the fence scan and check the arrow risk: whether any
  member imports a NON-member that would then have to move too. **A FIFTH question, N-33's 13
  private-NAME crossings**: routes import 13 private names from `app.utils.account_validation`, and
  the names LIE about their visibility -- routes are their consumers, so they are cross-package API.
  The honest fix is a rename to public, after which extending W9910 to private NAMES is a
  zero-exception tightening. Expect this step to DECOMPOSE from what it finds.
* [ ] **E2-n** the move itself, and the registry deletion. Decide the decomposition from E2-0, not
  here. **The deletion of `_FENCED_MODULE_RULINGS` is the LAST commit, never the first**: prove the
  boundary holds before removing the gate that compensates for its absence (the C3b3
  prove-the-successor-first precedent, applied eight times in this arc). The context's recurrence memos are composed as a field, not a mixin, so stock E1101 covers them (**R-BAL263**).
* [ ] **G1** `refactor(gates): the ledger-model and balance-seam fences stop carrying name lists` --
  closes **N-147**. **Its first action is a trace and no code is written before it**, because the two
  remaining allowlists are not the same problem wearing two hats. `_LEDGER_MODEL_ALLOWLIST` names the
  modules permitted to import the three ledger models; the balance seam checker carries roughly a
  dozen module sets plus a per-module EXPORT map, which encodes what each producer may publish rather
  than merely who may import it. **What the trace must establish, per allowlist**: whether the entry
  exists because a boundary is genuinely absent (the fix is the boundary, as W9910 was), because a
  legitimate member is spelled as an outsider (the fix is the spelling), or because the rule is a
  value-level invariant a TYPE could carry (`ReconciledThrough`'s shape). **Ruling all three the same
  way is the error this step exists to avoid.** Each checker's deletion is the LAST commit of its own
  arm, and the oracle-independence test that parses `_LEDGER_MODEL_ALLOWLIST` is rewritten with it (**R-BAL263**).
* [ ] **G2** `refactor(money): the value types that retire three checkers` -- retires **W9901**,
  **W9904** and **W9902**, the three with no structural replacement scheduled anywhere. **Its first
  action is a trace**, because the two halves are different sizes and only one is obviously worth it.
  **The money half:** `Money`, a value type over `Decimal` that cannot be constructed from a `float`
  and whose rounding is a method carrying the app's rule, retiring W9901 and W9904 together.
  **RE-RUN, not remembered** (it read 47 and 36, measured 2026-08-25): (census 51 code lines `Numeric\(12, ?2\)` in `app/**/*.py`) columns and (census 31 code lines `\.quantize\(` in `app/**/*.py`) sites,
  (census 14 code lines `\.quantize\((?![^)]*rounding=)` in `app/**/*.py`) bare.** Its trace must decide whether it lands at the ORM boundary (a `TypeDecorator`, so the
  blast radius is the type rather than the call sites) or as a hand conversion -- the
  `TypeDecorator` route is the one that makes the checkers redundant BY CONSTRUCTION.
  **The SCHEMA layer is the third surface and the only live money today** -- the corrected census
  **N-212** cites (census 92 code lines `fields\.Decimal` in `app/schemas/**/*.py`), every one of which carries
  `places=` and NOT ONE passes `rounding=`**, so every one quantizes against `ROUND_HALF_EVEN` and
  disagrees with `round_money` at every half-cent boundary (marshmallow 4.3.0: `0.005 -> 0.00`,
  `4.345 -> 4.34`). It was **104 of 104** at `afbf3b3e`, the tree N-212 was written against, so its
  five were a line-based grep rather than a stale count. **The same type answers N-256**: 47 carry
  no upper bound (salary 14, loans 11, savings 6, transfers 4, transactions 3, 9 elsewhere) and
  `accounts.py:62` / `:82`, both halves of a balance assertion, carry no validator at all, so
  `10 ** 10` passes the schema and 500s at flush. One `Money` field type stating the mode and the
  `numeric(12,2)` domain ONCE closes both, where 150 hand-added arguments would be the same rule
  maintained by remembering.
  **The label half is small and should not wait for it:** `DisplayLabel`, a type returned by a ref
  table's `.name` whose `__eq__` against a `str` RAISES, retiring W9902; it reaches the templates, so
  a ref-name comparison in Jinja raises wherever it runs and the template hook's ref-name check and its test twin go, and `Money.__bool__` raises, so a zero amount read as absent raises wherever it runs (**R-BAL263**).

* [ ] **X-ba** `refactor(ref): one lookup, not twenty-six copies of it` -- collapse
  `app/ref_cache/_accessors.py`'s twenty-six near-identical functions into one
  `enum_id(member)` reading `type(member)`, and migrate the call sites. Finding **N-341**.
  Each is `require_init(); return cache().enum_ids[Enum][member]` under a docstring running 1 to 35
  lines, and the repetition is what took the module past pylint's 1,000-line ceiling with four
  lines of headroom -- `bank_import:X-f6e-1` SPLIT it into a package to buy room without touching a
  public name, which is a floor rather than the fix.
  **The census is RE-RUN, not remembered** -- it read "26 canonical accessors plus five, measured
  2026-08-23 and not to be re-taken" until 2026-09-11, by which point it was 27 plus seven and one
  of its claims was false. Canonical:
  (census 28 lines `^def [a-z_]*_id\(` in `app/ref_cache/_accessors.py`).
  **`acct_type_icon` and `acct_type_max_term` have no PRODUCTION
  caller** (census 0 code lines `acct_type_(icon\|max_term)` in `app/routes/**/*.py`) and are
  candidates for DELETION rather than folding -- with them the `_cache.acct_type_meta` map `init()`
  populates to feed them. They are NOT unreferenced, which this paragraph used to say: two tests
  assert each one exists, and those go with the deletion. `from app import ref_cache` reaches no
  private name and nothing uses the `from app.ref_cache import <name>` form.
  **What the migration owes**: the per-accessor rationale several docstrings carry is not noise --
  `raise_type_id` names the producer it serves and the ruling behind it -- so the census decides
  per function whether its prose moves to the ENUM, where the meaning belongs. It rewrites a
  surface 98 modules import, so it takes its own PR.

*X-az left 2026-08-26 (`archive/five_shipped_steps_2026-08-26.md`); its obligation is RESTATED because
a later leaf must obey it -- the settle-day pairing is a BICONDITIONAL, a day and its basis share ONE
lifetime, and a RESUBMITTED day is an ECHO that may not restate its basis (`$4,173.07`, 59 of 66).*

### Phase X -- the fences made unnecessary (outcome O7, **R-BAL263**)

* [ ] **X-dp** `refactor(scripts): the integrity sweep stops re-asking what PostgreSQL enforces` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-481** and **BAL-487**. FK-01, FK-02, FK-04..FK-16, DC-02 and DC-06 each look for a row a foreign key, a CHECK or a unique index already refuses, and the sweep runs on a plain-dump restore (`scripts/verify_backup.sh`'s `restore_to_temp`, one transaction under `ON_ERROR_STOP`) that re-validates every key, check and unique index as it loads, and on the live database, which the weekly `integrity_check.py` also sweeps, the same constraints refuse the state, so none can report anything: they are deleted with their tests. That is also why **BAL-481**'s 'after a partial restore' does not arise for the restore the sweep runs, and why **BAL-487**'s restated index DDL goes with DC-06's case. It waits for `X-bi-6-4d-2`, whose held branch rewrites `_integrity_consistency.py` and `_integrity_core.py`; DC-11's transfer arm, a re-ask from 4d-2 on, goes there. The one thing left open: whether an operator wants a detector for a load run with `session_replication_role` set, which nothing runs today. Script and tests, `$0.00`.
* [ ] **X-dq** `test: the tests that guard nothing are deleted` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). Two kinds, one act. Their subject already gone: the seeded-calendar cadence class whose answer `C4-b`'s foreign key gives, the `generate_schedule` and `calculate_summary` locks, the archive helper's dead `Settled` asserts, the grid's reads of dropped columns and the first `salary_gross_biweekly` guard. A second home of a gate that already enforces: the source scans re-asking W9902, W9904, SHK01 and the services-no-flask test. Each deletion is a test edit and goes to the developer under rule 5. Tests only, `$0.00`.
* [ ] **X-dr** `refactor(money): a float cannot build a Decimal anywhere` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). Setting the `decimal.FloatOperation` trap in the app's and the suite's contexts makes `Decimal(0.1)` and `Decimal(float(x))` raise wherever they run, money or rate, literal or variable (measured 2026-10-11: `==` against a float does not raise, and `Decimal.from_float` stays an explicit exit), so W9901 and its `tests/` arm are deleted. Its trace first lists every float entry point (JSON bodies, the chart boundary). The developer rules before anything is built: a static checker is traded for coverage of the code that runs, and the trap must be set before the workers fork. `G2`'s W9901 clause becomes redundant with it. Reward tier: a float-built rate or amount becomes unrepresentable at run time. `$0.00`.
* [ ] **X-ds** `refactor(templates): the presentation layer cannot compute a figure` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). Jinja's `float` filter is unregistered in the app's environment, and templates and scripts receive formatted display values rather than money they can do arithmetic on, so the template hook's `float` check and the template and script money-arithmetic scans are deleted. It waits for `G2`'s display type. The developer rules first whether the chart boundary's `float()` stays a sanctioned exit. Reward tier. `$0.00`.
* [ ] **X-dt** `refactor(templates): an absent name raises on render` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). Templates render under Jinja's `StrictUndefined`, after a census of the templates that read an absent name as false, so a global, a key or a dropped column nothing passes raises instead of taking the wrong branch; the `REC_*` global census, the grid macro's key lock and the recurrence macro's column lock are deleted (that macro test's import half stays as a regression test). The developer rules first between `StrictUndefined` and `ChainableUndefined` for the optional reads. Upkeep, reward if the census finds a live wrong branch. `$0.00`.
* [ ] **X-du** `refactor(writers): an anchor row, an opening and a pay period have one writer by construction` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-632**'s anchor half. `AccountOpening`, each `AccountAnchorHistory` row kind and `PayPeriod` with its bulk delete become constructible only through their one writer, so the three tests that census their writers are deleted, and the anchor docstrings **BAL-632** names are reworded to name one writer per row kind (the owner's true-up in `anchor_service`, the bank level in `statement_import/_record.py`). It waits for `X-f4`, which deletes `_anchors.py` and the correction machinery that read the anchor rows, so the writers are fenced on the tree it leaves. The developer rules first between a capability the writer's private module mints and an INSERT grant only the writer's database function holds. Upkeep, reward on the grant route. `$0.00`.
* [ ] **X-dv** `refactor(privacy): a second caller is refused, not scanned for` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-632**'s settle-day half. Each leaf a test now guards by scanning for a second caller moves behind package privacy, so W9910 refuses one: the borrowed settle-day basis, the owner write lock, transfer-row mutation, the grid's balance ingredients, the raw `recurrence_rule` relation, `project_salary` with the month-ordinal reader, and the card's purchase-account members, a leaf per arc. It waits for `X-bi-6-4d-2` (`transfer_service`, `_side_days`), for `X-dc` and `X-i5` (the lock) and for `E2-n`, which may take the grid's ingredients; the transfer-row lock's premise after 4d-2 is untraced. The developer rules first how the lock's every-owner deploy form gets the second sanctioned door it needs. `$0.00`.
* [ ] **X-dw** `refactor(status): a status id decides nothing outside its predicates` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). A status id becomes opaque outside `balance_predicates`, so every decision reads the status row's flags (`is_settled`, `excludes_from_balance`) through the predicates and an inline per-status comparison raises where it runs (a raising equality) or is refused by the package-privacy checker (private members), the developer's fork; D6-09's file allowlist and the archive helper's settled-pair lock are deleted. It waits for `X-aj2` and `credit_card:CC-7`, which deletes `Credit`. The developer rules first between a type whose equality raises and private enum members. Reward tier: a dropped RECEIVED once moved a figure (CRIT-05). `$0.00`.
* [ ] **X-dx** `refactor(cash): the amount reduction is a pure function` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). The cash amount reduction takes the entries and the day it is handed and nothing else, so neither an unloaded relationship nor a clock read can reach a priced row, and the `'entries' not in __dict__` scan and the `cash_ledger` clock lock are deleted. It waits for `X-bi-6-4d-2` (on its held branch `cash_ledger` reads the side links) and for `pay_calendar:C10`'s clock rule. Byte-identical, `$0.00`.
* [ ] **X-dy** `refactor(front): a name the server shares with the browser has one home` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-637**. Every name the server shares with a script, a stylesheet or a template gets one home in Python, rendered into a `data-` attribute or a generated token, so the net-worth band vocabulary, the retirement rail's region id, the designed-fragment header and the service worker's static prefixes cannot disagree, and their agreement tests are deleted; editing the stylesheets, it settles **BAL-637**'s `!important` rule the linter turns off. The developer rules first between generated CSS tokens and `data-` attributes for the band colours. `$0.00`.
* [ ] **X-dz** `refactor(schemas): an integer field says what it counts` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). Schemas take integer fields only from a project module of meaning-named fields (a row id, a count, a year), with no bare `fields.Integer`, so a lax id field cannot be declared and `_NON_ROW_ID_INTEGERS` and `_NON_INTEGER_FIELD_SPELLINGS` are deleted with the test that holds them. It waits for `X-ah` and for `X-dh`, whose arm grades the second list this deletes. `$0.00`.
* [ ] **X-ea** `chore(lint): the fail-on list is stated once` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). The shekel `--fail-on` list is spelled in CI, pre-commit and both hooks, and a test holds the copies equal; stated once, it cannot drift and that test is deleted. The developer rules first between registering the shekel messages as error-category, so `E,F` covers them everywhere, and one `fail-on=` in `.pylintrc`; the per-edit hook's two tiers bear on the choice. `$0.00`.
* [ ] **X-eb** `chore(tests): each test-infrastructure fact is stated once` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-638**. The template database's name lives in one stdlib-only module its three readers import, the account-type count is read from `ACCT_TYPE_SEEDS`, the socket-path threshold is computed in `test.sh`, and the runner's environment is an explicit pass list, so the tests that compare copies are deleted; `pytest.ini` gains `--strict-markers` (**BAL-638**). It waits for `X-bi-6-4d-2` (its held branch splits the image builder), `X-df` and `X-by`. Kept apart from `X-ec` because it changes no production artifact. Tests and tooling, `$0.00`.
* [ ] **X-ec** `chore(deploy): each deploy fact is stated once` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). One `ARG` holds the base image digest, one variable the state sentinel's path, one included nginx snippet the security headers; the entrypoint asks `app/config.py` for the secret-key rule; and the host's deploy files are links into the checkout; so the tests that hold the copies in agreement are deleted. It waits for `X-do` and `X-bs`. The developer rules first whether Dependabot bumps an `ARG`-held digest (if it does not, the digest copies are a boundary that stays) and links against installed copies on the host. Deploy tooling, `$0.00`.
* [ ] **X-ed** `refactor(ref): a reseed cannot disagree with its enum` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-639**. Each reference table's reseed list is built from its enum's members, the status flags become enum metadata, and `ref` ids become `GENERATED ALWAYS AS IDENTITY`, so a reseed cannot disagree with its enum and a literal-id seed cannot leave a sequence behind; the seed-parity and sequence tests are deleted, and `app/ref_seeds.py`'s unheld invariant (**BAL-639**) with them. It waits for `X-dl`, `X-ba` and `credit_card:CC-7`, which drops a status row. The developer rules first between identity ids and the stable literal ids the frozen migrations insert. A migration, `$0.00`.
* [ ] **X-ee** `fix(transactions): a settled row with no day is unstorable` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). A plain transaction's status is tied to its settle day by the deferred status-band trigger `X-bi-6-4d-2` builds for transfers, reading the day off its covering movement rather than a copy on the row, so `require_settled_day`'s row arm and DC-11's two row arms are deleted. Measured first: a row violating it refuses the migration. It waits for `X-bi-6-4d-2` (the trigger lands in that release) and `X-de`, which decides which home the day keeps. The developer rules first between the trigger over the day's homes and deleting the row's copy (**R-BAL80**); section 3.1's 'a CHECK cannot express it' is stale, since the held trigger joins `ref.statuses`. Reward tier. `$0.00`.
* [ ] **X-ef** `fix(schema): cross-table rules are held by the database` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-633** and **BAL-634**. Deferred constraint triggers make unstorable what a CHECK cannot express: an account with parameters has its params row, a user has settings, a non-companion user has a baseline, an account has an opening, and a ledger account has its ref kind; `RequiredRecordMissing`'s arms, DC-03, DC-07, DC-08 and the opening read's `RuntimeError` are deleted (`ensure_type_params` stays: it writes the params row the trigger requires), DC-03's and DC-08's raw-SQL name comparisons with them (**BAL-633**), and `books_hold` gets one spelling (**BAL-634**). It waits for `X-ab`, `X-y` (what a write may do with no baseline) and `X-f3c-5`, after which an account with no opening is BA-01's real question. The developer rules first whether a baseline is mandatory, which would delete `recurrence:R-R30`'s repair page and the sweep beside it. Reward tier. A migration, `$0.00`.
* [ ] **X-eg** `refactor(scripts): each rule the sweep reports has one home` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). OR-03 reads `category_has_usage`'s one definition; the archive door refuses an account any active template or savings goal names, or that state is ruled legal and DC-05 and OR-06 are deleted; and BA-06 is deleted (**R-BAL275**; owns **BAL-641**, was `pay_calendar:PC-501`) once a production copy shows its lower clause finds nothing, its precondition (**R-PC62**'s bound) having shipped at `pay_calendar:C18`. It waits for `X-bi-6-4d-2`, which edits the same files. The developer rules first whether an active template or goal on an archived account is legal. Upkeep, reward if the archive door's rule is chosen. `$0.00`.
* [ ] **X-eh** `refactor(status): one settled status, labelled by the row's type` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). The two type-specific settled statuses become one settled status whose Paid or Received label derives from the row's type, so a Paid income row or a Received expense cannot be stored and `reject_mismatched_settled_status` is deleted; a migration over `ref.statuses`. It waits for `credit_card:CC-7`, `X-aj2` and `X-dw`. Its blast radius is large and the alternative is to rule the refusal input validation and keep it, so the developer rules first. Reward tier. `$0.00`.
* [ ] **X-ei** `refactor(recurrence): a row reads what its definition states` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census). A row reads its name, and for a rule-less definition its account, category and type, off its definition rather than holding copies the definition edit propagates, so a row cannot disagree with its definition and `apply_fields`' rename sweep and `propagate_to_unruled_rows` are deleted. It waits for `recurrence:R22`, whose forks decide what a row stores. The developer rules first whether a settled row's name or category is a recorded fact (**R-IY**'s boundary) or a copy. **MOVES MONEY** if any rule-less row disagrees with its definition today, measured first. Reward tier.
* [ ] **X-ej** `refactor(db): a fresh database is built by the migrations` -- outcome O7 (**R-BAL263**; the 2026-10-11 fence census); owns **BAL-542** and **BAL-635**. A fresh database is built by running the migration chain, or one squashed baseline revision, rather than `create_all` plus every infrastructure module, so the schema has one home and the infrastructure modules' three-callers-must-agree contract goes, which answers **BAL-542**; `require_audit_triggers` compares the audited set rather than a count (**BAL-635**). It waits for `X-dl`. The developer rules first between the whole chain and a squashed baseline. `$0.00`.
* [ ] **X-ek** `docs(plans): the developer answers whether the posted ledger stays a stored copy` -- a QUESTION step of outcome O7 (**R-BAL263**; the 2026-10-11 fence census), filed by **R-BAL267**; owns **BAL-630**. In plain words: the books' journal entries are stored, every deploy re-syncs them (`scripts/init_database.py`'s `resync_all_cash_postings_after_migration` and `backfill_loan_payment_postings_after_migration`, and in part the account-anchor hook), the E1a assert grades the loan side against its fold (`loan_posting_service/_sync.py`'s `_assert_checked_projection`), integrity check DC-10 reports an undated movement holding a live leg, and the per-door posting field filters re-post an edited row. Those fences stand because rulings put them there (**R-DM**, **R-DU**, **R-DV**, **R-BAL98**, **R-BAL102**, **R-BAL122**), against rule 14's own (**R-JA**, **R-IY**). From scratch the entries are worked out from the recorded movements when read, or written once by the act that records the movement and never re-derived, so no copy, re-sync or grade remains; only the openings, the asserted balances and an entry's paycheck (`pay_calendar:C25`) stay stored. The cost to weigh: the journal is append-only, and the statements, the trial balance and the income statement read it. The lane puts it to the developer with worked numbers and full context before `recurrence:R5-b`, `X-ai-a`, `X-ai-b`, `X-ai-c`, `X-cu` or `X-d` is built, since each builds on those rulings and waits on this step (**R-BAL267**, **R-BAL271**): `R5-b` extends the per-door posting field lists (`_POSTING_RELEVANT_FIELDS`) and relies on the deploy backfill. It ships at the commit that files the answer in `rulings.md` with, on a yes, the deletion as its own step, onto which the held steps are re-pointed (or by which they are cancelled) in the same commit, or, on a no, the fences as kept in O7's record (`../fence_census_2026-10-11.md`).
* [ ] **X-el** `docs(plans): the developer answers whether a balance read is handed only its owner's accounts` -- a QUESTION step of outcome O7 (**R-BAL263**; the 2026-10-11 fence census), filed by **R-BAL267**; owns **BAL-631**. In plain words: a balance read refuses another owner's account at run time (`ForeignAccountError`, raised by `balance_at._memoize._memoize_once`), and `X-i6` plans an ARM mechanizing that one door. **R-GV** rejected the structural form on measured cost: `ctx.account(account_id)` resolving through an owner-scoped query, so a foreign account is unrepresentable, which inverts the route layer and supersedes three resolvers. The doctrine since, that correctness outranks time and effort, is what reopens it. From scratch the read pass hands out the accounts itself, so another owner's account can never reach a balance read, and the refusal and `X-i6`'s arm go. The lane puts it to the developer with full context, and worked numbers wherever a figure moves, before `X-i6` is built, since `X-i6` waits on this step. It ships at the commit that files the answer in `rulings.md` with, on a yes, the deletion as its own step, onto which `X-i6` is re-pointed (or by which it is cancelled) in the same commit, or, on a no, the refusal as kept in O7's record (`../fence_census_2026-10-11.md`).

## 6. The findings ledger

**Moved to `../../plans/ledger.md`**, the one findings table for every arc. This arc's rows are the
ones whose `arc` column reads `balance`; a row's owner names a step in `../../plans/steps.md`, whose
specification is section 5 of this document, and the rules it is graded against are
`../../plans/conventions.md`.

## 7. Verification standard

**Moved to `../../plans/verification.md`**, one copy for every arc. This arc WROTE it and is why
several of its clauses exist -- three of the five defects behind the suite's two clock gates were
fixture-clock bugs created here, and this arc writes more anchors, assertion instants and due dates
than anything else in the codebase.

## 8. Process lessons

**Moved to `../../plans/lessons.md`**, one copy for every arc.

## 9. Rules for this document

**Moved to `../../plans/conventions.md`**, one copy for every arc.

`tools/plan_gate/` grades this document against them through a pre-commit hook
scoped to it and the CI step that runs the custom pylint checkers -- so EDITING
THIS FILE is what runs the gate. This document's own caps live in the gate's
constants beside the other arcs'.
