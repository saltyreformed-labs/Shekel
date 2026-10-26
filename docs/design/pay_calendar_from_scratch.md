# The pay calendar from scratch: what `budget.pay_periods` is, and what the arc should be

**STATUS: PROPOSAL, REVISED 2026-09-06 after two neutral adversarial reviews, which broke a good
deal of it.** Section 9 records what they refuted. Nothing here has been built, and no planning
document has been edited. This answers the developer's ruling of 2026-09-06,
*"stop and re-plan the arc first"*, taken on `pay_calendar:C14-f` after that step's own design work
found that the check `R-PC55` specifies is a reconciler for a duplication rather than a fix for one.

Its companion is `from_scratch_architecture.md`, which asked the same question of the balance arc on
2026-09-01. That document already says, of `C4-c`:

> *`C4-c` is nonetheless the same pattern one table over: a pay period's `end_date` stops being
> stored and becomes derived. That is the amount-ownership argument of Section 6.1 applied to the
> calendar, and it is corroboration that the direction is the project's own.*

This document finishes that sentence. The pattern is not one table over. It is the same table, and
`end_date` was one column of it.

**THE ROOT CAUSE BELOW IS NOT A DISCOVERY. The developer ruled it on 2026-09-06, hours before this
document was written**, in `R-PC61`, "on the from-scratch design he asked for":

> The root cause it answers is `budget.pay_periods` holding a RECORD and a PLAN in one table, with
> the projection re-reading its own stored output as its anchor.

A first draft of this document presented that as its own finding and never cited `R-PC61`. An
adversarial review caught it. What is left for this document to add is narrower and is the only
thing it should be read for: **how far that ruled cause reaches, what it prices, and which of the
arc's eleven unshipped steps change because of it.**

`R-PC61` also rules on `nominal_anchor` directly -- **"It is a FACT and not a stored derivation"**,
absorbed by `C17` as one of its five era columns per `R-PC58`. Section 3.1 said "no column" in its
first draft, contradicting both that ruling and this document's own Section 3. Corrected below.

**Section 7 is the only part that asks the developer for anything.** Everything before it is
measurement and argument.

---

## 1. What was measured, on which database, on what day

**Every figure decays. Re-derive before relying on one.**

Measured **2026-09-06** (dates from the database container read as UTC, so `CURRENT_DATE` there was
`2026-09-07`) against **`shekel-prod-db`**, the live production database, read-only, stamped
`b3f7c2a91d4e`. Production has **not** taken `a1c7e5d20f43`, so it holds no `nominal_anchor` column.

Code read at `4a0c48ef` (`wip/pay-calendar-c14e-3-registries`), which is `C14-e-3` at `ed267298`
plus its registry commit. `C14-e-3` is **not yet on `dev`**.

| what | measured |
|---|---|
| `budget.pay_periods` columns | `id`, `user_id`, `start_date`, `created_at` -- four |
| rows, user 1 | **63** |
| ... at or before today | **12** |
| ... in the future | **51** |
| ... exactly reproducible from `min(start_date)` and `cadence_days` | **63 of 63** |
| ... departures the rhythm cannot produce | **0** |
| live transactions filed to a PAST period | 229 |
| live transactions filed to a FUTURE period | **699** |
| stored rhythm | cadence 14, convention `none`. **The opening 2026-03-26 is NOT stored** -- it is `min(start_date)` off the record, and `history_opens_on` is `NULL`. That is the cash-anchored-phase confusion this document is about, so it may not be listed as a stored fact |
| `pay_schedule.rolling_enabled` / `rolling_target_periods` | `true` / **52** |
| current-and-future periods implied by that target | 1 current + **51** future = **52**, which is what is stored |
| foreign keys onto `pay_periods.id` | **4 constraints across 3 tables**, all `ON DELETE CASCADE`: `journal_entries`, `transfers`, and `transactions` TWICE (`transactions_pay_period_id_fkey`, plus the composite `fk_transactions_owner_period` on `(pay_period_id, user_id)`) |
| `pay_period_id` in `app/` | 384 LINES across 96 files (**409 occurrences** -- the two are not the same number and the first draft used them interchangeably) |
| `PayPeriod` the MODEL, `grep -rn 'PayPeriod\b'` | 124 (192 as a bare substring, which also counts `PayPeriodGenerateSchema` and its siblings) |
| `PayCalendar.saved()` CALL SITES -- code reading the MATERIALISED set | **23, across 16 modules.** A raw grep says 32/21, but 9 of those hits are prose in docstrings and comments. The first draft printed the grep under the label "callers", which is the sampler-versus-census error this project has a lesson about |
| `PayPeriod` queried directly through the ORM | 3 |

**The 63-of-63 figure is weaker than it looks and the weakness is the point.** This owner's
convention is `none`, so "reproducible from the rhythm" reduces to "an arithmetic progression from
its own opening", which is the identity case. It does **not** establish that departures never
happen -- `R-PC47` says payroll moves paydays, and that is why the whole `C14` arc exists. It
establishes something else, and narrower than a first draft claimed: **no READER of this table uses
a fact the rhythm does not carry, and there is no column that says *this payday is not where the
rhythm put it*.**

The first draft wrote "the table holds no fact its source does not already carry", and its own
column list four lines above refutes that. **`created_at` is such a fact** -- it holds
**8 distinct materialisation timestamps** across the 63 rows, visibly separating the original batch
from the rolling top-up's later appends, and no rhythm produces it. What makes the weaker claim the
right one is that **nothing reads it**: no reference to a `PayPeriod`'s `created_at` exists in
`app/`, `scripts/` or `tests/`.

---

## 2. The root cause

`budget.pay_schedule` holds a rhythm. `budget.pay_periods` holds the sequence that rhythm produces.
Nothing reconciles them.

That is `CLAUDE.md` rule 14 exactly:

> A value that is derived AND stored has both problems, and the stored copy is a stale cache with no
> reconciler. **The tell is an INVARIANT** -- where a rule says two places must always agree, they
> are one value with two homes and a maintenance contract, and the remedy is to delete a home rather
> than keep them in step.

`P80` is that divergence, observed. `R-PC55`'s two-clause check is the maintenance contract, written
down. Building it is choosing to keep two homes in step.

**The app already derives periods ABOVE the record, and deliberately refuses to below it.** A first
draft of this paragraph claimed both directions and an adversarial review falsified it.

- **Above the record**: `project_period_after`, `covering_projection` and
  `PayCalendar.span_containing` answer for days past the last recorded payday with no row. That path
  is built, exercised and load-bearing.
- **Below the record**: `_rhythm._backdated_paydays`
  **returns `()` unless the owner has stated `history_opens_on`**, and on production that column is
  `NULL` -- so for the only owner there is, it answers nothing below the record today
  (`_rhythm.py:474-476`). That is not an oversight to route around: `balance:R-IA` as amended
  2026-08-31 ruled it, because NULL means *not stated* rather than *always paid this way*, and
  `R-PC14` refused backward projection outright because it "would attribute money to paychecks that
  never happened."
- `derive_periods` is **a function OF the record**, not a generator of it: it takes the payday set
  as `(period_id, date)` pairs and derives spans. Offering it as proof that the calendar is a
  function was the first draft's weakest step, since it shows the payday set is an INPUT.

`DerivedPeriod.period_id` is `int | None` (`_derive.py:222`), and the phrase
*"a period that is not materialised"* is real but lives at `_derive.py:477` and `:995`, not on that
attribute's own docstring.

So why do 51 future rows exist?
**Because a transaction files against a pay period by foreign key, and a foreign key needs a row.**
That is the whole reason, and it is the whole difficulty.

`pay_period_rolling`'s own module docstring states the cache-filler without naming it:

> *nothing asks for it, `/grid` and `/dashboard` call it on every render, and all it may do is
> APPEND paydays the owner's own stored cadence already implies.*

A write, on a read path, that adds only what is already derivable. That is a cache being warmed.

### 2.0 The strongest counter-argument, and why it fails

If the row set encoded an INTENTION -- *how far ahead I want to see* -- it would carry a fact the
rhythm cannot produce, and the cache framing would be wrong.

**It does not, and the app already stores that intention on the rhythm row.**
`budget.pay_schedule.rolling_target_periods` is **52** for this owner, with `rolling_enabled` true.
`top_up_rolling_window` keeps exactly that many current-and-future periods, counting the period
containing today as one of the N -- its docstring says so, and it appends "exactly the deficit".

Measured: **51 future periods, plus the one containing today, is 52.** The stored target, exactly.

**That is weaker evidence than a first draft made it, and an adversarial review priced the gap.**
The claim was "the forward half is a pure function of `(rhythm, rolling_target_periods, today)`".
Three things falsify it as a general statement:

1. **The top-up never truncates.** It returns early when the count is at or above target, so a count
   ABOVE target is stable and unreproduced.
2. **`extend_pay_periods(user_id, num_periods)` is uncapped.** An owner who presses Extend past
   their target leaves an excess no function reproduces.
3. **`rolling_enabled` can be false**, and for such an owner the row count is entirely a record of
   past user acts.

And **n = 1**: production has exactly one owner. Since the top-up runs on every `/grid` and
`/dashboard` render and fills to exactly the target, measuring afterwards can only FAIL if that
owner had extended past it. So `51 + 1 = 52` establishes
*"this owner has not extended beyond their target"*, not *"the table holds no intention"*.

**What survives is still the point**: the intention has a home on the rhythm row, so the row set is
not its only home. The stronger claim needs an owner with `rolling_enabled = false` or a
hand-extended one, and neither exists to measure.

The backward half is the 12 periods at or before today. That they sit on the grid is a statement
about grid membership, **not** about whether each records a paycheck that happened -- see Section
3.2, which a first draft omitted entirely.

### 2.1 What the duplication has already cost, in rows this arc already owns

These are open findings and shipped steps re-read as one cause.
**A first draft called it nine; it is SIX**, after an adversarial review withdrew `N-492` and
`PC-506` outright, halved `PC-501`, and found `P30` counted twice:

| row / step | what it is | the same cause, stated |
|---|---|---|
| **P80** | an irregular recorded payday set nothing detects | the cache can hold what its source cannot produce |
| **C4-c** (shipped) | `end_date`, `period_index` dropped | two columns of the cache deleted |
| **PC-497 / `nominal_anchor`** (shipped, `C14-e-2`) | a stored nominal phase | the writer needs a nominal day to step from **because the rows it writes are cash days** |
| **N-495, PC-502, PC-504** (all `C17`'s) | forward reader, backward reader and writer all anchor on a CASH day | there is no nominal phase in the record, because the record is a cache of one |
| ~~**N-492**~~ | **WITHDRAWN from this table.** Its row is that `budget.pay_schedule` holds ONE `cadence_days` per owner -- a defect in the SOURCE's expressiveness, present with zero materialised rows. `pay_periods` has no rhythm column, so the first draft's gloss was false about the table it named | evidence for Section 3's POSITIVE half (eras), not for the cache framing |
| **PC-501** (upper clause only) | `BA-06`'s SQL horizon restates the end rule | SQL must reason about materialised spans. **Its LOWER clause is withdrawn**: the row says in bold that its two clauses have different subjects and the lower one is a cash-books question in `balance:R-HG`'s family |
| ~~**PC-506**~~ | **WITHDRAWN.** It is a SHIPPED migration's downgrade body, and its own row calls that a historical artifact | deleting the paydays table cannot un-write it |
| **C9** (#47) | the fold walks SAVED periods, so contributions stop at the horizon: **`+$2,501.92`** Empower, **`+$5,427.07`** Property at six months | the horizon is an artifact of how many rows happen to exist |
| **C8** (#46) | recording paydays and setting the forward cadence welded onto one form | the establish job and the continue job share a door |
| **P29** (shipped, `C3-b`) | `cadence_days` deleted from the extend door | the same weld, already cut once. **`P30` is NOT shipped and is NOT a separate row here** -- it is OPEN, owned by `C8`, which is the row above; the first draft counted one finding twice |

**`C9` is the one that prices it.** `+$7,928.99` of contribution, at six months out, on a boundary
that exists only because a row set is finite and a function is not.

### 2.2 The fence the cascade needs

`budget.journal_entries` is the balance arc's append-only, ORM-immutable double-entry ledger. Its
`pay_period_id` is `ON DELETE CASCADE`. Pay-period rows are deleted by truncate, regenerate and
reset -- cache-maintenance doors.

**Two different mechanisms hold it, and a first draft named one of them for all three doors.**
`classify_schedule_locks` has three callers -- `truncate_pay_periods`, `regenerate_pay_periods` and
a settings display. **`reset_pay_periods` does not call it**: that door gates on zero settled
transactions and lets the entries cascade, then RE-DERIVES and re-posts them from their source
facts, which its own docstring states. So at truncate and regenerate the fence is
`_period_ids_with_unbalanced_ledger`, and at reset it is re-derivation.

*That correction cuts toward this document rather than against it: a posting's `pay_period_id` being
reconstructible from its source facts at the reset door is better evidence for Section 3 than the
sentence it replaces. It is stated because it is true, not because it helps.*

At truncate and regenerate, then, `pay_period_locks._period_ids_with_unbalanced_ledger` is the
fence, and it says so:

> *`journal_entries.pay_period_id` is `ON DELETE CASCADE`, so deleting a period disposes its entries
> and legs at the DB tier -- outside the ORM, where the balanced-journal trigger never fires on
> DELETE.*

An immutable ledger needs a runtime lock to survive a cache-maintenance door.
**This is not a bug -- the lock holds.** It is scaffolding around the defect, and `CLAUDE.md`'s
doctrine says the goal state is that it is structurally unnecessary. A period that is never a row is
never deleted.

---

## 3. What it should be

Two facts are non-derivable. Everything else is a function of them.

1. **The rhythm.** Effective-from, cadence, cadence KIND, nominal phase, displacement convention.
   The owner states it; the app cannot infer it.
   **This is `C17`'s era relation, already ruled (`R-PC58`).**
2. **A departure.** A payday that did *not* land where its era's rhythm says. Payroll paid early for
   a holiday the app's set does not model; a corrected run; an off-cycle payment. Irreproducible,
   therefore a record.

A third fact is non-derivable but is **already stored in the right place** and needs no change:
`rolling_target_periods`, how far ahead the owner wants to look. Today it decides how many rows to
materialise; under this design it decides only how far a view runs, which is what it always meant.

So:

- `budget.pay_schedule` becomes `budget.pay_eras`. One row per *how I have been paid since*.
- **`budget.pay_periods` stops being a table of paydays.** What survives under that name is a table
  of **departures** -- one row per payday that left its era's grid, keyed on the era and the grid
  index it departed from, carrying the day it actually landed.
- A pay period is the view `derive_periods` already returns.

**A departure is exactly the fact `R-PC47` says exists and the schema cannot currently express.** It
is also what makes the two clauses of `R-PC55` unnecessary rather than unbuildable: an off-grid
payday is no longer an anomaly to detect, it is a row with a name.

### 3.1 What becomes unrepresentable, so the fence is never built

- **`P80`.** An irregular forward set cannot be written, because the forward set is a function.
  `R-PC55`'s check loses its subject.
- **`N-495`, `PC-502`, `PC-504`.** No cash anchor to inherit: an era carries the nominal phase, and
  a departure is stored *as a departure* rather than silently becoming the next batch's phase.
- **`PC-497`'s DRIFT.** No materialised future, no batch stepping from its own last output, no phase
  to drift. **The column is NOT deleted** -- `R-PC61` rules `nominal_anchor` "a FACT and not a
  stored derivation" and `R-PC58` already makes it one of `C17`'s five era columns, so it MOVES from
  `budget.pay_schedule` onto the era. Section 3 says the same thing four paragraphs up, listing the
  nominal phase as non-derivable. A first draft said "no column" here and contradicted both.
- **`N-492`.** A past payday's cadence is its era's.
- **`PC-501`, `PC-506`.** No SQL reasons about materialised spans, so nothing restates the end rule.
- **`_reject_backward_payday`, `_reject_overlapping_batch`, `uq_pay_periods_user_start`,
  `extend_pay_periods`, `truncate_pay_periods`, `regenerate_pay_periods`, `top_up_rolling_window`,
  `_period_ids_with_unbalanced_ledger`.** A family whose entire job is keeping a cache in step, or
  surviving its maintenance.
- **`C9`'s horizon.** There is no saved-versus-projected boundary to disagree across. *`C9` is
  nonetheless fixable WITHOUT any of this -- `P7`'s projection half already shipped at `C2-e` via
  `PayCalendar.projection_axis` with the rows still in place, and Section 5 says so.*

### 3.2 The hole in this design, found by its own adversarial review

**Section 3 names two non-derivable facts and there is a THIRD it does not store: the EXTENT of the
record -- the claim that this paycheck happened.**

A departures table holds paydays that departed. It holds no bound on where the record begins or
ends. With `history_opens_on` `NULL`, a rhythm that is a function over the integers runs to
`CALENDAR_DATE_MAX` in both directions with nothing separating *paid* from *would have been paid* --
and that is the exact state `balance:R-IA` was amended on 2026-08-31 to forbid:

> R-IA's first form let NULL mean the claim, so an owner who had never seen the question was counted
> as having made it. That is the guessing the ruling set out to stop.
> **Two facts with opposite epistemics cannot share one encoding.**

Today the row set answers this by existing: a period is a row because someone recorded it. Delete
the rows and the answer has to be stated somewhere else. **`history_opens_on` is half of it** (where
counting backward stops); the forward half -- where the record ends and projection begins -- has no
column at all today, because the last row IS the boundary.

**This is a genuine gap in Section 3, not a detail.** Any version of this design must say which
relation answers *"did this paycheck happen"* and show it is not the rhythm. Section 7.5 asks it.

**`R-PC53` is the standing ruling against the destination and belongs here rather than buried**: it
ruled that `journal_entries.pay_period_id` STAYS, on the ratio that *"the CALENDAR can move under a
past entry -- `extend_pay_periods`, `top_up_rolling_window`, `reset_pay_periods` -- so the stored
value is what records where the postings landed."* Section 4 quotes the balance arc against deriving
the filing key; this is the pay-calendar arc's own ruling on the same column, and it is stronger.

---

## 4. What it costs, and why it is not one step

**Four foreign-key constraints across three tables** land on `pay_periods.id`, and `pay_period_id`
appears on **384 lines across 96 files** (409 occurrences).
**699 of 928 live transactions are filed against a future period** -- a row whose only justification
is that it is a projection someone materialised.

**TWO keys are undesigned, not one**, and a first draft named only the first.

**(a) The FILING key.** `from_scratch_architecture.md` Section 4.4 already refuses the easy answer:

> **`pay_period_id`.** A purchase takes its parent's, deliberately. A movement with no plan item (a
> bank line nothing was planned for) has no parent to take it from, and `ReviewScope.period_holding`
> is the resolver that must answer instead.

So `pay_period_id` is **not** simply *the period covering this row's date*. It carries deliberate
attribution that the date alone does not determine. And `R-PC53` -- this arc's own ruling, quoted in
Section 3.2 -- already ruled the column STAYS.

**(b) The IDENTITY key.** `budget.pay_periods.id` is a stable public identity as well as a filing
key: five route registrations bind it (`/period/<int:period_id>`, two salary views, and the two
carry-forward routes), plus 106 template references. The failure mode is already written down in the
module Section 2.2 quotes -- `pay_period_locks.py` warns that an unmaterialised period carries
`period_id = None` and that keying on it would "collapse them onto each other".
**Under Section 3 every period is unmaterialised.**

**Neither key is designed here**, and that is deliberate. That work touches the balance arc's live
cutover and the recurrence arc's generated rows, and it is a multi-arc program.

**Therefore: the destination in Section 3 is not proposed as work to start now.**

---

## 5. What the ranked steps become

Eleven `pay_calendar` steps are unshipped. **Seven are touched by this cause; four are not.**

| step | rank | today | under this reading |
|---|---|---|---|
| **C14-f** | #13 | add `R-PC55`'s two-clause check | **the fork of Section 7.** Its check is the reconciler; its door fix is the root cause at this scale |
| **C17** | #14 | a pay schedule is a SEQUENCE OF ERAS | **unchanged, and promoted in importance.** It is the positive half of Section 3, already ruled, and it is where `N-495`, `PC-502`, `PC-504`, `N-492` and `P78` are owned |
| **C18** | #15 | a payday may be recorded BELOW the earliest; a period below the books generates nothing | **SURVIVES INTACT.** A first draft said its first half "largely dissolves" because `_backdated_paydays` already answers below the record -- it does not, it returns `()` on production (Section 2), and `PC-499` is about a write door refusing a RECORDED payday, which a function cannot record. Both halves stand |
| **C8** | #46 | the forecast cadence gets ONE control | **already owns half of Section 7's door fix**, for `cadence_days`, on generate / regenerate / reset. It does **not** own `start_date`. *A first draft added "ruled a UX step 2026-08-11, which predates `P80`" -- that is real prose in the arc document but it is not the classifying ruling. `R-PC50` classifies `C8`, it is dated **2026-09-03**, and `P80` was found **2026-09-01**, so the classification POSTDATES the finding. The argument was inverted* |
| **C9** | #47 | the fold projects contributions past the horizon | **the priced symptom.** `+$7,928.99` at six months. Fixable independently, and it stays worth fixing independently |
| **C6** | #48 | a payday may be inserted mid-schedule | **re-framed**: inserting a payday mid-schedule *is* recording a departure. Under Section 3 it is an ordinary write to a departures table rather than a surgery on a cache |
| **C16** | #51 | the ledger's two filing rules stop being one column to its reader | **directly the filing-key question of Section 4**, one reader at a time. Its `$61,633.27` / `$10,653.91` measurement is evidence for that section |
| C13-c | #49 | eight ownership keys | untouched |
| C15 | #50 | the retire-later solve | untouched |
| C10 | #132 | the salary package reads the OWNER's day | untouched |
| C11 | #133 | the LAYER predicate | untouched |

---

## 6. The order this argues for

**No re-ranking is proposed.** Nothing here makes a ranked step wrong to do in its ranked place, and
the board holds the order as one sequence.

What it argues is narrower:

1. **`C17` is the step that matters most in this arc**, and it is already ranked next but one. It
   should not be traded away for cheaper steps ahead of it.
2. **`C14-f` should not ship `R-PC55` as ruled.** Section 7.
3. **`C8` holds half of `P80`'s remedy at rank #46 while `P80`'s owner sits at #13.** *A first draft
   argued this from `C8`'s classification predating `P80`, which is backwards -- `R-PC50` postdates
   it by two days. The argument that survives is only the rank split, which is a weaker reason and
   is stated as one.*
4. **`C9` should be priced as a symptom of Section 2**, not only as a fold bug -- it is the evidence
   that the materialisation boundary reaches money. *It remains fixable on its own via
   `PayCalendar.projection_axis`, so this is a framing point, not a re-sequencing one.*

---

## 7. What is open -- the forks for the developer

**7.1 -- `C14-f` itself.** Three live readings, and the step cannot proceed without one:

- **(a) The door fix.** `POST /pay-periods/generate` stops asking `start_date`, `cadence_days` and
  `shift` of an owner who already holds a rhythm; it asks *how many more*, as the extend door
  already does since `P29`. `P80`'s worked example becomes unwritable. This **supersedes `R-PC55`**
  and needs a ruling saying so. It overlaps `C8`, which owns the `cadence_days` third.
- **(b) The check, narrowed.** Ship a warning check only over what a door fix cannot reach --
  paydays already stored, and sets the reset and regenerate doors produce. Still a fence, and the
  predicate question below still has to be answered.
- **(c) Withdraw `C14-f`** and let `C17` absorb it, on the argument that an era plus a departures
  table makes both clauses meaningless.

**7.2 -- if any check is built, the predicate.** `R-PC55`'s literal form does not survive `C14-e-3`.
Measured on this owner's rhythm extended to 1,888 paydays with 59 real displacements, against a
record that is entirely truthful:

**READ THE ZEROS AS VACUOUS, NOT AS EVIDENCE.** Two independent adversarial reviews found the same
defect in this measurement and they are right. The "truthful record" was BUILT by applying
`shift_to_business_day` to an arithmetic grid, and three of the four predicates are then written in
terms of that same function -- an equality whose two sides share one producer, which is a failure
mode this project keeps a lesson about.
**Five of the eight false-alarm cells are determined by the construction and grade nothing.**

| predicate | false alarms | if the OPENING payday was displaced | sees `P80`? |
|---|---|---|---|
| `R-PC55` clause 1 -- grid from the opening | 0, **VACUOUS** (the record's own producer regenerates it; under `R-PC55`'s LITERAL arithmetic form the count is **59**, one per displacement) | **1,822 of 1,888** -- INFORMATIVE, the perturbation is independent of the predicate | **no** |
| `R-PC55` clause 2 -- gap `!=` cadence | **108 of 1,887 pairs** -- the ONE fully informative cell; it reads the raw cadence integer and consults nothing the generator supplies. Histogram 1,779x14, 54x13, 54x15 | 119 | yes |
| clause 1 restated: the day must be one this convention could pay on | 0, **VACUOUS TWICE** | 0, **VACUOUS** (it never reads the opening) | no |
| clause 2 restated: some nominal pair one cadence apart displaces onto this pair | 0, **VACUOUS** (the generator's own seed is always the witness) | 0, **VACUOUS** | yes |

**Two further things the table does not say, and they matter to fork 7.2.** I censused it after the
review: *"a day this convention could pay on"* is **provably identical to `is_business_day`** -- 0
mismatches over all 54,786 day-convention pairs from 2026 to 2101 -- so under production's stored
convention `none` its refusal set is **EMPTY and it can never fire**. A predicate with an empty
refusal set has zero false alarms and zero power, and this table has no column that tells those
apart. And the simulation is **not committed**, so unlike Section 1 this table states no
reproducible basis.

**What survives all of that** is the row that does not depend on the construction: `R-PC55`'s gap
clause raises **108 false alarms in 1,887 pairs** on a truthful displacing record, and its grid
clause is anchored on a cash day so one displaced opening rotates it off **1,822 of 1,888** paydays.
That is enough to say the ruled form does not survive `C14-e-3`.
**It is NOT enough to say the restated pair is better**, and the first draft said so.

The restated pair did catch `P80`'s 196-day hole, a payday typed a day early, a drifting tail, a
spurious extra payday, and a Thanksgiving payday under `prior` including as the first payday -- but
those are five hand-built cases narrated in prose, not a sensitivity measurement.

**7.3 -- whether Section 3 is the destination at all.** It is a multi-arc program with **two**
undesigned keys (Section 4) and an unanswered question about what records that a paycheck happened
(Section 3.2). The question is not whether to start it now -- it is whether the arc's remaining
steps should be *steered toward* it or merely not away from it.
**`R-PC53` is a standing ruling against it** and would have to be revisited, not worked around.

**7.4 -- `C8`.** Not its classification, which `R-PC50` settled on 2026-09-03 with `P80` already
known. The live question is the RANK SPLIT: `C8` holds half of `P80`'s remedy at #46 while `P80`'s
owner `C14-f` sits at #13.

**7.5 -- what records that a paycheck HAPPENED**, if the rows go (Section 3.2). `history_opens_on`
answers the backward bound; nothing answers the forward one today because the last row is the
boundary. **This is a hole in Section 3, not a detail of it**, and `balance:R-IA` forbids the
obvious shortcut of letting an absence mean the claim. Three options:

- **(i) An explicit forward bound on the record**, beside `history_opens_on`. Symmetrical, and it
  makes both ends stated facts rather than one stated and one implied by row count.
- **(ii) MATERIALISE THE PAST, DERIVE THE FUTURE.** A row exists if and only if that paycheck
  happened, and nothing else is a row. The 51 cache rows go; the 12 records stay.
- **(iii) Infer it from settled transactions filing against the period.** Rejected: it makes a real
  paycheck with nothing settled in it indistinguishable from a projection, which is guessing of the
  kind `R-IA` exists to stop.

**Recommendation: (ii).** It is the smallest change that makes the answer STRUCTURAL rather than
conventional -- "is there a row" stops being a question about materialisation policy and becomes a
question about the world -- and it preserves exactly the record semantics `R-IA` protects.
**It is NOT measured and is not proposed as work**; it is here because fork 7.3 cannot be ruled
without an answer to this one, and because a departures table (Section 3) silently assumes an answer
it does not state.

*Note what (ii) does NOT solve: 699 of 928 live transactions file against FUTURE periods, so the
filing key of Section 4a still has to be designed before the future stops being rows. (ii) answers
what a row MEANS, not what a transaction points at.*

---

## 8. What this document does NOT claim

- **Not that departures are rare.** 0 of 63 was measured on an owner whose convention is `none`,
  which is the identity case. It measures the table's *contents*, not the world.
- **Not that `pay_period_id` is derivable from a row's date.** Section 4 quotes the balance arc
  refuting exactly that.
- **Not that anything here is a live defect.** At truncate and regenerate the cascade is fenced by
  the lock; at reset it is compensated by re-derivation (Section 2.2 -- a first draft named the lock
  for all three doors). `C9`'s `+$7,928.99` is an existing open finding with an existing owner,
  quoted as evidence, not rediscovered.
- **Not that `R-PC55` was wrongly ruled.** It was ruled 2026-09-04. `C14-e-2` and `C14-e-3` shipped
  on 2026-09-05 and 2026-09-06 and changed what a recorded payday *is*. The ruling predates its own
  subject.
- **Not a re-ranking.** Section 6 proposes none.
- **Not that the root cause is this document's finding.** `R-PC61` ruled it on 2026-09-06, before
  this was written. See the header.
- **Not that Section 3 is ready to rule on.** Sections 3.2, 4 and 7.5 name three things it does not
  answer, and `R-PC53` is a standing ruling against it.

---

## 9. What the adversarial reviews refuted

Two neutral agents reviewed this document on 2026-09-06 -- one grading the argument, one re-deriving
every number independently. **Every database and code figure in Section 1 reproduced exactly**, and
the Section 7.2 simulation reproduced cell for cell. What broke was the argument around them.
Recorded here rather than quietly fixed, because a document that hides its own corrections is the
thing this project has a lesson about:

1. **The root cause was already ruled**, in `R-PC61`, hours earlier and uncited. The first draft
   presented it as a discovery.
2. **`_backdated_paydays` does not answer below the record** -- it returns `()` unless
   `history_opens_on` is stated, and production's is `NULL`. Two load-bearing sentences rested on
   it, including the demotion of `C18`, which is withdrawn.
3. **`Section 3.1` said `nominal_anchor` becomes "no column"**, contradicting `R-PC61` and this
   document's own Section 3. It moves to the era; it is not deleted.
4. **The `C8` argument was inverted.** `R-PC50` classifies `C8` on 2026-09-03, two days AFTER `P80`
   was found -- so the classifier had `P80` in hand. Fork 7.4 is re-stated as a rank question.
5. **Five of eight cells in the 7.2 table are vacuous**, because the truthful record and three of
   the predicates share one producer. Only the gap clause's 108 and the displaced-opening 1,822 are
   evidence.
6. **Section 2.1 counted nine causes and has six.** `N-492` and `PC-506` withdrawn, `PC-501` halved,
   `P30` counted twice.
7. **`created_at` IS a fact the rhythm cannot produce** -- 8 distinct materialisation timestamps.
   The claim narrows to: nothing reads it.
8. **Section 2.0's "pure function" claim is not general** -- the top-up never truncates, extend is
   uncapped, `rolling_enabled` can be false, and n = 1.
9. **The identity key was missed** (Section 4b), and **what records that a paycheck happened** was
   missed entirely (Section 3.2 / 7.5). The second is a hole in the design, not a detail.
10. **`R-PC53`, `R-IA` and `R-PC14`** were all uncited and all bear on the destination.

*One reviewer noted the first draft chose the CHARITABLE reading of `R-PC55`'s clause 1 -- the
shift-aware one, scoring 0 -- where the literal ruled form scores 59. That cut against this
document's own thesis, and it was still the wrong reading to print without saying which one it was.*
