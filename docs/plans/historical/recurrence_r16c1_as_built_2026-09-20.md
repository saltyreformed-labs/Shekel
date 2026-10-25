> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# recurrence:R16-c-1 as built (2026-09-20)

**A loan's past and future are ONE event stream.** R16-c-1 shipped at `c88ed6ba` (`d217fb29` the leaf half,
`c88ed6ba` the seam half, on dev `44b6f388`; **R-R90** the decomposition, **R-R91** the visible-facts bound;
no migration, no money moved: 3,952 + 919 harness lines, 0 diff, on the 2026-09-19 production clone, the
delinquent door shown to fire). Moved here under conventions.md rule 7 at its tick: the R16-c entry's
STREAM half as it stood when the leaf shipped (the `[ ]` is the entry as it stood before the tick), then the
lane's map of what was built, then the ledger row the leaf closed. The CALENDAR half stayed live in
`implementation_plan_recurrence_redesign.md` under **R16-c-2**.

## The entry's STREAM half, verbatim

- [ ] **R16-c -- the PAST and the FUTURE become ONE event STREAM**

`loan_ledger._walk._replay_events` and `balance_at._plan_fold._split_plan` were two running-balance
implementations of one rule. **That half is done**: `balance:X-au-g-2c-3b-2` (`3b7716f8`) moved the
rule to `loan_ledger._replay.replay_loan_events` and both tiers now call it, which also made the
settled walk charge once per accrual period and CLOSED **D51**.
**This step SHRANK because part of it SHIPPED EARLY, not because scope was dropped.** What is still
owed is the STREAM, not the arithmetic: one event list with `as_of` marking where recorded fact
becomes projection, one seed, and one set of record types, so the two tiers differ in nothing but
their inputs.

**D51 is CLOSED, and it closed OUTSIDE this arc**: at `balance:X-au-g-2c-3b-2` (`3b7716f8`), which
made the settled walk charge once per accrual period. It is recorded here rather than only there
because a reader of THIS arc must be able to find where a finding this arc owned actually went;
`steps.md`'s row for that balance step carries the forward half ("satisfies **recurrence:D51**"),
and this is the backward half.

*Its predicate still greps TRUE and the defect is gone*, which is the trap worth recording: a row
whose WORDS still match while its defect is gone, and a row whose defect remains while its words
stop matching, are the SAME failure, and only re-reading the code separates them.
`_walk._replay_events` does still call `split_one_payment` once per settled shadow -- but
**that call no longer CHARGES anything, and it no longer computes anything**: it copies the four
parts verbatim off the replay's outcome and names them. The charge is derived once per accrual
period from the installments the payments satisfy (`charges_for_due_dates`), the replay accumulates
it, and `apply_payment_cash` clears it at the FIRST payment in that period;
**a second payment in the same `installment_slot` finds nothing standing and pays pure principal.**
Measured on a forced due-month collision: interest `$1,014.06` -> `$0.00`, escrow `$616.99` ->
`$0.00`, principal `$279.90` -> `$1,910.95`.

## What R16-c-1 is, as the lane mapped it at its handoff (the code is the record)

ONE builder, ONE replay, ONE seed, ONE record type:

* `loan_ledger._replay`: `LoanEventStream(charges, payments, resets, projections,
  projected_charges, periods)`. A PROJECTED event (a payment that has not happened, or the plan's
  charge against one) is keyed `(max(on_date, boundary), on_date, kind rank)`, `boundary =
  projection_boundary(stream)` = the day after the latest recorded payment or reset, so **a
  projected event is never placed before a recorded fact**, events pushed to the boundary keep
  CONTRACT order among themselves (April's charge, April's catch-up, May's charge, May's catch-up),
  and **no event's `on_date` is rewritten** (a charge's date is the accrual period's identity and
  the loan page renders it). The fact prefix of a merged replay IS the pass's facts replay. The
  what-if `extra_per_period` accrues at PROJECTED charges only. **A RESET clears standing
  interest / escrow / extra** (R-R72 (2); unreachable on today's streams, pinned on a hand-built
  one). `LoanResetEvent.is_opening` names the origination. `PaymentOutcome` is THE record for a
  settled and a projected payment alike (`period`, `is_projected`, and flat read-through
  properties `source`, `due_date`, `visible_on`, `cash`, `interest`, `escrow`, `principal`,
  `excess`, `balance_after`, `charge_date`). `LoanCashEvent.visible_on` is read ONCE by the builder
  (`_events.loan_event_stream`). `_split.py` / `LoanPaymentSplit` / `split_one_payment` DELETED (a
  field-for-field copy of the outcome). `replay_loan_stream(stream, extra)` is the pure walk;
  `load_loan_stream(id, scenario, visible_by=None)` is the load (R-R91's bound lives here: with a
  date, payments through `confirmed_shadows_through`, assertions by their own date, the opening
  always); `walk_loan_ledger` = load + replay, unbounded (the ledger's). `LoanLedgerWalk` carries
  its `stream` and two views `settled_splits` / `projected_splits`; the posting writer reads
  `settled_splits`.
* `balance_at._context.loan_walk` replays `load_loan_stream(..., visible_by=as_of)` -- the pass's
  facts, bounded ONCE at the memo (R-R91). `balance_at._loan_stream` (NEW, seam-private):
  `merged_stream(facts, plan)` appends the plan's payments as projections (sorted `(due,
  effective)`, `visible_on = effective_date`) and the plan's charges as `projected_charges` --
  nothing re-dated, no clamp; `loan_timeline(account, ctx)` memoizes one merged replay per pass on
  the PRIVATE `BalanceContext._timelines` (the loan twin of `_cash_folds`: one named
  `protected-access` crossing, the rationale beside it); `what_if_timeline` replays the same
  stream with an extra.
* `balance_at._positions`: `positions()` = ONE `fold_from_walk` over the timeline for every date,
  gated at origination by `_owed_from_gated` (the retired `_sample_from_steps` rule: a payment can
  settle BEFORE the loan opens -- `test_not_paid_off_even_with_a_confirmed_payment` -- and a loan
  owes `0.00` before it exists). `loan_payoff_date` / `loan_installments` /
  `loan_what_if_owed_at_dates` / `loan_required_extra` read the timeline; `_owed_from` is the one
  entry guard. `_plan_fold` keeps `installments_payoff`, gains `owed_at`, `is_retired` (THE one
  definition, moved from `_loan_figures`, which delegates), `timeline_payoff_date`,
  `required_extra(walk, owed_from, as_of, target, replay_with)` (its doubling bound for an
  unoriginated loan is the opening, by `is_opening`). `_kernel.DebtSchedule` loses
  `projection_seed`. `_loan_interest.loan_interest_in_year` is one sum over the timeline (settled
  by display-tz paid year, projected by `visible_on.year`); `_due_slot` / `exclude_slots` gone.
  `_confirmed_view` drops its per-event `<= as_of` filters (the walk is bounded at the memo).
  Routes read the flat fields (`.split.X` -> `.X`, `.effective_date` -> `.visible_on`).
* `tests/oracles/loan_forward_fold.py`: the retired `(seed, plan)` contract over the timeline (a
  seed becomes an origination reset with a `LoanAnchorFact` source, `is_opening=True`), so the
  three hand-built suites keep every asserted value. `tests/test_services/test_loan_replay_one_stream.py`
  (9): the reset rule, the boundary, contract order among pushed events, the extra rule, the flat
  record. `tests/test_services/test_loan_timeline.py` (NEW, 5): the delinquent shape at the seam
  (two catch-ups behind a settled payment: `197,474.87`, splits, `charge_date` kept; one
  catch-up: `charge_date` kept) and R-R91 (a later true-up not folded; a later-settled payment
  not a fact yet; a loan closing next month projects from its opening). `test_balance_at`'s nine
  `schedule.projection_seed` assertions read `_owed_today()` = the public scalar at `as_of`.

**Byte-identical, measured on the FINAL tree (2026-09-20 05:4x-06:0x EDT):**
`tests/manual/verify_loan_plan_sum.py` base (`wt-base` at `44b6f388`) vs branch on `shekel_r16c`:
**3,952 non-log lines, 0 diff lines** -- the unplanted read, the five R16-b-2 doors, and two doors
this leaf ADDED for the shape its review found the first cut moving: DOOR 6 (the Van, one
installment reverted to projected behind its latest settled) and DOOR 7 (the Mortgage, TWO --
07-01 and 08-01 reverted behind the settled 09-01; the Van cannot hold two: its 06-23 true-up
leaves one installment before its latest settled). **Door 7 was shown to FIRE**: with the
replay's contract-order key removed (the first cut's ordering), it reads 30 differing lines (the
Mortgage payoff `2048-12-01` -> `2049-01-01`, every OWED point from 10-11) and 0 with it. A probe
over `loan_installments` (with and without a `$250` extra), `loan_payoff_date`,
`loan_interest_in_year` (2024-2028), the YTD chips, `loan_required_extra` (3 targets),
`loan_what_if_owed_at_dates` (24 months), `loan_figures`: 919 lines, **0 diff lines**
(`scratchpad/probe_r16c1.py`; outputs `probe_wt-base.txt` / `probe_branch3.txt`). The clone holds
no fact settled after the harness's 09-11 pass, so R-R91's bound is a no-op there by measurement
as well as by argument. Targeted suites: 1,339 + the new modules over 38 loan / balance / route
modules. Full suite on the FIRST cut: 14941 passed / 2 failed (the two R7d-h pass-before-a-true-up
tests R-R91 settles). Full suite on the final tree: **14948 passed / 0 failed** (11:09 wall, 2026-09-20 05:4x-05:5x EDT). `pylint app/` CI gate:
10.00, exit 0.

**The first cut's adversarial review (fresh subagent, 2026-09-19 22:4x) and what it changed.**
HIGH 1: a pass dated before a recorded assertion folded it (the two R7d-h failures) -> R-R91, the
bound at the load. HIGH 2: the boundary CLAMP applied both skipped months' charges before both
catch-ups (`+$2.54`, the first catch-up reading negative principal) -> `projected_charges` keyed
`(walk date, on_date, rank)`; door 7 + `test_loan_timeline` + a leaf pin. HIGH 3: the clamp
rewrote `charge_date`, which `routes/loan/_helpers._period_slot` groups on and `dashboard.py:252`
prints -> nothing is re-dated; pinned. MEDIUM 4 (DISCLOSED, kept as the design): an ad-hoc
projection due after the last settled installment and before the next contractual one now reads
the last FACT charge as its period (`charge_date` = that installment's; ARM rate column and
`extra = principal + interest - period_pi` follow it) where the two-fold design gave it
`charge_date None` and the calendar's period; money identical; this is R-R89's reading (the
payment falls in that installment's interval) and where R16-c-2 lands anyway. MEDIUM 5: prose
stating deleted rules as live at 14 sites -> fixed. LOW 7: divergence (a) was mis-described --
ruling R-EJ REFUSES a future settle day at the doors, so it is unreachable, not merely absent;
moot under R-R91. LOWs 8-10 -> `is_opening` names the opening; the writer reads `settled_splits`;
the seam-level test. Confirmed by the reviewer: the fact-prefix invariant, the oracle adapter's
faithfulness, the restored gates, the interest partition, the memo's privacy, the writer.

**The SECOND adversarial review (fresh subagent, 2026-09-20 06:0x, over `d217fb29` + the staged
seam half): no Critical, no High; the three HIGHs remedied "and the remedies hold under adversarial
reading"; item 2's order proof written out (for projected A, B with dates a < b the keys order a
first; a == b the rank orders charge before payment; a recorded event can never share a walk date
with a projected one; a recorded and a projected charge can never share a month since R-R91 made
`stream.charges` and `seed_slots` one visible set). Its two MEDIUMs and six LOWs, all fixed in the
commit: M1 `_plan._seed_boundaries` re-derived R-R91's bound (`confirmed_shadows_through` + the
anchor test, a second query per loan per pass) while the memo's docstring said no reader does ->
it reads the pass's stream (`{installment_slot(c.on_date) for c in stream.charges}`, `max(reset
.on_date)`; the opening is always present so the default went) -- one spelling, one query fewer;
M2 the past half GAINED the origination gate (`positions()` for a date BEFORE origination on an
ORIGINATED loan with a payment settled before it -- R-EL bounds the settle day by the pay schedule,
not the origination -- read that payment's principal as a NEGATIVE balance under the old past
branch and reads `0.00` now; the posted ledger still books it at its settled day) -> DISCLOSED as
the ONE date the merge answers differently from the two folds (in `positions`' docstring and
here), unreachable on production (the 919-line probe), the door that admits such a settle day the
structural fix (a finding for the coordinator, no id: **a loan payment's settle day is not bounded
below by the loan's origination**); L1/L2/L4 prose in `_replay.py` / `_events.py` (the clamp
sentence, `PaymentOutcome.charge`'s "None only for a stream the two production callers cannot
produce" -- the seam produces it for R-C's early extra -- and its "nothing can intervene" for a
projection behind pushed events); L3 eleven test docstrings and `savings_dashboard_service
._net_worth.py` citing `projection_seed` / `fold_forward` / `plan_payoff_date` / `PlannedInstallment`
as live -> retired; L5 `owed_from <= as_of` spelled twice -> `_plan_fold.is_originated` is the one
home, `_loan_figures._is_originated` delegates; L6 the ad-hoc-projection attribution: agreed as
R-R89's reading, with ONE more rendered field named -- its `period` (the ARM rate column) is the
accrual period's where the two-fold design showed `period_for_date(due)`'s; differs only when a
rate change falls between the last fact charge and the projection's due date; money identical.

**Disclosed divergence (c), kept:** the tax reader no longer drops a projected payment's interest
when an ad-hoc projected extra falls in a settled installment's month (the old `_due_slot` de-dup,
built for a month-keyed synthesis the plan stopped performing at R16-b-2; finding N-180's "can the
two sets still differ" question DISSOLVES -- there are no two sets; `N-180` (balance, owner X-e)
is a row for the coordinator to re-read).

## Ledger row CLOSED at this tick (moved here under the same id)

**D61** CLOSED at R16-c-1 (`c88ed6ba`): the column went at R20 (`b4da8068`, migration `22b23085394d`); the ONE
seed the remedy asked for landed at R16-c-1 (`c88ed6ba`): every loan figure -- past balance, projection, payoff,
tax interest -- is one replay from the origination assertion, and no producer starts from a stored or
separately folded principal. The row as it stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| recurrence | D61 (`balance:N-243`'s census 2026-08-11, dissolved 2026-09-03) | -- | **`loan_params.current_principal` is a stored copy of a figure the loan's own walk derives.** The balance the replay reaches from the origination through every recorded payment is the loan's principal; the column stores it beside that walk with no reconciler | `$0.00` on both live loans today; the exposure is a principal that stops agreeing with the ledger the day a payment is corrected without the column being re-stamped | **OPEN, born with an owner** (re-filed 2026-09-03 from N-243). It goes where the past and the future become one event STREAM with one seed, since the seed is what the column was pretending to be | R16-c |

**balance:N-180** CLOSED at R16-c-1 (`c88ed6ba`): its question -- whether the tax reader's two sets can still differ -- has no referent once `_due_slot` and the second set are gone; `loan_interest_in_year` is one sum over the timeline (settled by display-tz paid year, projected by `visible_on.year`). The row as it stood (owner X-e, whose index row and README entry now say so):

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| balance | N-180 | -- | A de-duplication rationale in `balance_at/_loan_interest.py` was falsified by R-DH, and whether the two sets can still differ for any other reason is UNVERIFIED | `$0.00` -- the code was never wrong, only the reason beside it | OPEN; the paragraph is replaced in place, the question survives | X-e |
