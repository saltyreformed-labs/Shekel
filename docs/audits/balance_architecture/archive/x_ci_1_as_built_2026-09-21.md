> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# X-ci-1 as built: the transfer twin takes `recurs`, and a one-time transfer's three doors stop reading its link as generated (2026-09-21)

Archived at the tick of X-ci-1 (`465f91cd`), the first of the three leaves **R-BAL95** cut `X-ci` into. The commit
is the record; the build's harnesses, the 13-mutation battery and the adversarial review are
`~/projects/shekel-handoffs/HANDOFF-X-ci.md` and `balance-2026-09-21/xci-1/`. No schema, no money moved.
Below: the X-ci entry as it stood before the split (verbatim), the `steps.md` row as it stood, the two ledger
rows the leaf closed, and the developer's answers to the four design questions (2026-09-20) and the four
build-time forks (2026-09-21) VERBATIM -- the `rulings.md` rows R-BAL92..R-BAL97 condense them.

## The X-ci entry as it stood before the split, verbatim

* [ ] **X-ci** transfers take the one-definition shape their data holds (X-bi-7's 10.3): the ad-hoc
  door closes, the discardable count and detaching move are fixed. Closes **BAL-492**, **BAL-493**.

## The `steps.md` row as it stood, verbatim

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| balance | X-ci | -- | Close the ad-hoc transfer door (`create_ad_hoc`, `POST /transfers/ad-hoc`; 0 rows on the 2026-09-12 restore) so a transfer too has exactly one definition, and fix the twin's two defects: the discardable count treating a one-time transfer as regenerable, and a move or typed figure detaching a one-time transfer from its definition for good. Closes **BAL-492**, **BAL-493**. | #36 | -- | NOW / balance:X-bi-7b (shipped) |

## Ledger rows CLOSED at this tick (moved here under the same id)

**BAL-492** and **BAL-493** CLOSED at `balance:X-ci-1` `465f91cd`: the discardable count's transfer arm loads
and asks `recurs`; a typed figure restates the definition's price inside `update_transfer` and re-attaches the
row (R-BAL92, R-BAL96 as amended); a paycheck move re-places the due date and `occurs_on` follows (R-BAL93,
R-BAL94). The rows as they stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| balance | BAL-492 (`balance:X-bi-7`'s spec audit 2026-09-12) | -- | **`pay_period_gates.count_discardable_items` COUNTS A ONE-TIME TRANSFER AS REGENERABLE.** Its transfer arm is `transfer_template_id IS NULL OR is_override OR not Projected`, so a rule-less `TransferTemplate`'s materialised Transfer -- template-linked, Projected, not overridden -- is uncounted, and truncate / regenerate's confirmation promises back a row no rule will write; the transaction arm (`Transaction.template_id.is_(None)`) had the same shape and `X-bi-7a` (`eecef63d`) re-keyed it: that arm loads and asks each row `recurs` now, so only the TRANSFER arm stays open here. Both one-time transfers on the 2026-09-12 restore are Paid, so the count is right today by accident | `$0.00` today; a Projected one-time transfer would be destroyed by a period truncate with the dialog naming nothing | **OPEN, born with an owner** (developer 2026-09-12, **R-BAL26**: a sibling step after `X-bi-7`) | X-ci |
| balance | BAL-493 (`balance:X-bi-7`'s spec audit 2026-09-12) | -- | **A MOVE OR A TYPED FIGURE DETACHES A ONE-TIME TRANSFER FROM ITS DEFINITION FOR GOOD.** `routes/transfers/mutations.py:317` flips `is_override` on a period move or an authored figure and `:717` makes the figure OWN; `_instances.non_repeating_live_transfers:246` then excludes the row, so `propagate_to_unruled_template` never speaks to it again and the definition's price and name go dormant on the Recurring page with no live reader. Measured 2026-09-12: transfer 409 (Rogue Equipment, moved to an earlier paycheck) is `is_override = TRUE`, OWN `$2,000.00` beside a definition stating `$2,000.00` -- agreement, not one home (**R-IZ**); a one-off has ONE occurrence, so detaching it leaves a definition with nothing to define. On the TRANSACTION table the move half is closed at `X-bi-7a` (`eecef63d`, the flip's move half keys on `recurs`); the typed-figure half there waits on `X-bi-7b`'s restate door | `$0.00` today (both prices agree); a rename or re-price of the definition reaches nothing, and the Recurring page states a figure the grid no longer uses | **OPEN, born with an owner** (developer 2026-09-12, **R-BAL26**) | X-ci |

## The design round's four answers, verbatim (2026-09-20, asked by the balance lane)

## Ruling 1 (a typed figure on a one-time transfer). PICKED: "Restate the definition's price in place"

Question (verbatim): X-ci, question 1 -- a TYPED FIGURE on a one-time transfer. Background: a
transfer that does not repeat is already a rule-less definition (a Recurring-page entry with no
cadence) plus its one placed transfer, priced by the definition. Today, typing a figure on that
transfer's popover makes the row OWN the figure and flags it overridden, after which the definition
never speaks to it again (its rename or re-price reaches nothing) -- finding BAL-493. Measured:
transfer 409 'Rogue Equipment' is flagged, OWN $2,000.00 beside a definition stating $2,000.00.
Worked: the owner types $2,100.00 on it at the grid. What should the figure do?

RULING (verbatim): The transfer twin of R-BAL29: the definition's one price version (as of the
row's due date) becomes $2,100.00, the row stays priced by the definition with no flag, the
Recurring page and the grid both read $2,100.00, and a later rename still reaches the row. Uses the
existing template_amount_service.restate_in_effect. A rule-less definition that owns no price series
(a derive-mode loan payment; 0 exist) keeps ruling R-JM's arm: the pair's OWN figure at the parent,
still with no flag so the definition's other fields reach it.

Rejected: the row owns the figure and the definition still reaches it (drop the overridden-row
exclusion: grid $2,100.00, Recurring page $2,000.00 -- two prices, rule 14's two homes); refuse
the figure at the popover (one door fewer on the grid, the screen the owner budgets from).

## Ruling 2 (a period move on a one-time transfer). PICKED: "Re-place it, as a one-off row is"

Question (verbatim): X-ci, question 2 -- a PERIOD MOVE on a one-time transfer. Today moving it to
another paycheck flags it overridden (the same detachment as question 1) and leaves its due date
where it was: transfer 409 was born in the paycheck starting 07-30 (due 07-30, the paycheck's start,
which is the default a one-time transfer is born with) and moved to the paycheck 07-16..07-29, so it
is now due AFTER its own paycheck ends. Worked: that move. What should a move do to the due date?
(No flag in any option: a one-time transfer is never 'overridden' against a cadence it does not
have.)

RULING (verbatim): R-BAL33's twin: the due date follows the placement -- the target paycheck's
start (07-16) unless the owner had stated a day of their own, read by position (a due date that was
not the old paycheck's start stays). The same rule the transaction side ships. For a one-time
payment INTO A LOAN the due date is the installment it satisfies, so a move across a month boundary
changes the installment exactly as re-dating it by hand would; the owner can state the day on the
form (the date input is offered on a one-time transfer after this step).

Rejected: never re-date on a move (a default derived from the old placement stays behind, 409's
state); re-place except into a loan (a branch on the destination's kind at the move door; one date
meaning two things by account).

## Ruling 3 (what a one-time transfer records as its occurrence). PICKED: "Its own due date, following every date move"

Question (verbatim): X-ci, question 3 -- what a one-time transfer records as its OCCURRENCE. Every
generated transfer records the day its cadence named (occurs_on); a one-time transfer records
nothing there today, and recurrence:R19-b (make occurs_on NOT NULL, delete the undated index)
explicitly waits 'once the one-time transfer branch has a rule for what its row answers'. On the
transaction side the same question was re-ruled (R-BAL25, 2026-09-13): the placed row records its
own due date. Measured: 2 such transfers (154 due 04-23, 409 due 07-30), both Paid. Which rule?

RULING (verbatim): occurs_on = due_date, written at birth and by every writer that moves the date
(question 2's re-placing included), the transaction twin's state_due_date. Gives R19-b the rule it
waits on; the 2 rows are backfilled at X-ci's cutover (no money moves). Consequence, as on the
transaction side: a cadence later added to the definition that NAMES that day adopts the row as its
occurrence; one that does not retains a row holding records (154 and 409 are Paid, so retained) and
would hard-delete a record-free Projected one (REC-516's stated cost).

Rejected: stays NULL (R19-b stays blocked on the transfer table; the undated index stays; a cadence
added later always retains the row and suppresses the new rule's occurrence in that paycheck).

## Ruling 4 (the cut). PICKED: "Three leaves, each a session"

Question (verbatim): X-ci, question 4 -- how to cut the work. The ad-hoc transfer door (POST
/transfers/ad-hoc) is already dead in the UI (nothing posts to it; 0 ad-hoc rows on production),
but the TEST SUITE builds template-less transfers at about 330 sites: two helpers (create_transfer
106 sites, create_settled_transfer 135) plus 69 direct service calls and 20 bare constructors. 'A
transfer has exactly one definition' means, structurally: transfer_template_id NOT NULL with
RESTRICT, the ad-hoc dedupe index and ck_transfers_adhoc_owns_amount dropped, due_date NOT NULL --
a migration that moves no money (0 rows affected). This is X-bi-7's shape again: the doors, then the
suite's one builder, then the cutover. How many leaves?

RULING (verbatim): X-ci-1: TransferTemplate.recurs / Transfer.recurs, the discardable count's
transfer arm loads-and-asks (BAL-492), the move flip and carry-forward's transfer move keyed on
recurs, the typed figure restating (BAL-493), the due-date input and occurs_on writers per questions
1-3; no schema. X-ci-2: the door and its two integrity handlers deleted, the one-time producer moved
from the route into a service the suite's builder can call, the two helpers re-pointed and the 89
direct sites moved (tests only beyond the producer). X-ci-3: the cutover migration (NOT NULL,
RESTRICT, the two ad-hoc constraints dropped, due_date NOT NULL, the 2-row occurs_on backfill,
_stated_amount's now-dead refusal deleted) graded byte-identical.

Rejected: four leaves (the door on its own, X-bi-7's exact shape; one more session for a small
leaf); one step (~330 sites, the re-key and a migration in one session).


## The build's four forks, verbatim (2026-09-21, asked by the balance lane)

## Fork 1 (where a one-time transfer's ruled acts run). PICKED: "Service dates, door figure" (recommended)

Question (verbatim): X-ci-1, fork 1: WHERE do a one-time transfer's ruled acts run? Every transfer write goes through the one service door `transfer_service.update_transfer`; two doors (the popover PATCH and Carry Forward) call it to move a transfer by paycheck. R-BAL93 says a paycheck move re-places the due date; R-BAL94 says `occurs_on = due_date` follows every date writer; R-BAL92 says a typed figure restates the definition's price at the row's due date AFTER the move. Which shape?

RULING (verbatim): Inside `update_transfer`: a paycheck move on a one-time transfer re-places its due date (`one_off.due_date_after_move`, the target paycheck's start unless the owner had stated a day), and every due-date write on one sets `occurs_on = due_date` on the parent -- so no door, present or future, can move such a transfer without re-placing it or part the two columns. The target paycheck is resolved ONCE off the owner's calendar and threaded to the loan-installment guard, deleting the documented redundant read. The popover alone knows a human typed the figure, so it restates the definition (`template_amount_service.restate_in_effect`) at the transfer's final due date once the door has moved it, and tells the door the row is definition-priced (`derived_ownership(TEMPLATE)`); a definition with no price series (derive-mode loan; 0 exist) keeps OWN at the parent, no flag. Carry Forward's move needs only its flag keyed on `recurs`.

Rejected: "Doors re-place, service follows" (the handoff's stated shape, the transaction twin's: each door computes the re-placed date and sends `due_date`; two doors carry the rule, a third must remember it; the popover derives a calendar the service repeats twice); "Doors send occurs_on explicitly" (`update_transfer` gains an `occurs_on` kwarg; a door can send one column without the other); "Everything in the service" (the service reinterprets `own(figure)` on a one-time transfer; `amount_ownership` would mean two things by row shape, undoing R-BAL11).

## Fork 2 (the two `PaycheckLine`-reaching census sites). PICKED: "Add PaycheckLine.recurs" (recommended)

Question (verbatim): X-ci-1, fork 2: two of the twelve `recurrence_rule is None` census sites read a SALARY deduction line (`PaycheckLine`: `routes/salary/_helpers.py:328`, and `clear_recurrence_rule` in `_recurrence_form_helpers.py:741`, which is generic over all three definition kinds). `PaycheckLine` has a cadence but no `recurs` accessor. What does X-ci-1 do with them?

RULING (verbatim): Give `PaycheckLine` the same `DerivedFlag` accessor the two templates carry (one body per definition kind, R-BAL20's 'stated on the definition because that is where the fact lives'); all ten non-accessor sites then read `recurs`, and the census reads 3 after the leaf (three accessor bodies). A ~15-line addition to a salary-arc model file no live lane holds.

Rejected: "Leave the two salary sites" (census reads 4 after the leaf; the step sentence's 'all but the accessor bodies' re-worded at the tick).

## Fork 3 (opened by X-ci-1's adversarial review; reproduced through the route). PICKED: "Service performs the restate as one act" (recommended) -- AMENDS R-BAL96 (answered 2026-09-21 between ~06:40 and 07:06 EDT, the exact minute not read; recorded at 07:06 EDT 2026-09-21 by `date`)

Question (verbatim): X-ci-1, fork 3 (opened by the adversarial review, reproduced): a typed figure saved TOGETHER WITH Status = Paid on a one-time transfer books the OLD price on both legs, because R-BAL96 places the popover's restate of the definition AFTER `transfer_service.update_transfer`, and the settle that books the legs runs INSIDE that door and reads the definition's price before the restate. The restate must run after the door's re-placing of the date and BEFORE its settle. Which shape?

Worked (in the question text): "Homeschool Reimbursement" planned `$1,500.00`, arrives at `$1,450.00`; the owner types `1450.00`, sets Paid, saves. Leaf as first built: the Recurring page and the grid plan read `$1,450.00`, the two legs book `$1,500.00` (`$50.00` wrong on each account). Correct under every option: the legs book `$1,450.00`.

RULING (verbatim): R-BAL96 amended: the popover hands the typed plan figure to `update_transfer` as an EXPLICIT new kwarg (`definition_price=`), distinct from `amount_ownership` so R-BAL11 stands (no reinterpretation of `own(figure)`, the reason 'everything in the service' was rejected). Inside the door the act runs after the re-placing and before the settle dispatch: restate the definition's price at the final due date (`restate_in_effect`), declare all three rows definition-priced, clear the flag (R-BAL37's heal). One call, one flush, one audit row, and the settle books the typed figure. The derive-mode arm (a definition with no price series: OWN at the parent, no flag) is unchanged and already ordered before the settle.

Rejected: "Two service acts at the popover" (keep R-BAL96's letter; the popover calls the door twice when a save both re-prices and settles -- two flushes, two lock bumps, two audit rows, the 'two doors, one act' shape); "Refuse the combination" (a designed 400; a regression against both twins).

**For the coordinator's row:** R-BAL96's row above is to carry "AMENDED 2026-09-21 (developer)" in its date column and the sentence: *AMENDED the same morning after the leaf's adversarial review reproduced a typed figure saved beside Status = Paid booking the pre-restate price on both legs (the settle runs inside the door the restate followed): the popover sends the typed plan figure as the explicit kwarg `definition_price=` and the door performs the restate itself, after the re-placing and before the settle dispatch (`_placed.restate_definition_price`); `amount_ownership` keeps its one meaning.* (The R-SAL29 precedent for an amended row.)

## Fork 4 (the owner of BAL-529, asked in ONE line as the coordinator directed). PICKED: "X-ci-2" (recommended); answered and recorded at 07:06 EDT 2026-09-21 by `date`

Question (verbatim): One line, for the ledger row the coordinator just granted (BAL-529): `routes/transfers/mutations._execute_transfer_update`'s `except IntegrityError` / `except StaleDataError` handlers read `xfer.id` after a failed flush, when SQLAlchemy has expired the row, so they raise `PendingRollbackError` and answer 500 where the docstring promises a designed 400 / 409 (pre-existing since 2026-06/07, found by X-ci-1's review, reachable today only by a crafted request; the fix is capturing `xfer_id` before the `try`). Which step owns the fix?

RULING (verbatim): The next leaf already rewrites this module (it deletes the ad-hoc door and its two integrity handlers in `mutations.py`), so the two-line handler fix rides with it and gets its control (an IntegrityError on the door answers the designed 400, not a 500) beside the handlers X-ci-2 deletes. No new leaf, no new id.

Rejected: "A new leaf of its own" (a separate step, next free X-cu, for the route-tier handler fix and its control; X-ci-2 stays tests-only beyond the producer as R-BAL95 worded it).


## R-BAL96's row as the lane drafted it (2,000+ characters with its amendment; `rulings.md` holds the condensed row; its `_placed.re_place_on_move` is the name the leaf's M1 rework renamed to `re_place_and_grade_the_day` before committing, kept here as drafted)

| arc | id | also | date | what was ruled |
|---|---|---|---|---|
| balance | R-BAL96 | -- | 2026-09-21 (developer) | **A ONE-TIME TRANSFER'S DATE RULES RUN INSIDE THE SERVICE DOOR; ITS FIGURE RULE AT THE POPOVER.** `X-ci-1`'s first build-time fork: where **R-BAL92** to **R-BAL94** run. Picked *"Service dates, door figure"*: inside `transfer_service.update_transfer` -- the door every mover passes through (Transfer Invariant 4) -- a paycheck move on a placed transfer (`Transfer.is_placed`, a rule-less definition's) re-places its due date through the existing `one_off.due_date_after_move` (`_placed.re_place_on_move`), and the one due-date arm sets `occurs_on = due_date` on the parent, so no door, present or future, can move such a transfer without re-placing it or part the two columns; a day a sibling of the definition already answers is a designed refusal (`_placed.reject_a_day_another_row_answers`, the occurrence index's rule). The target paycheck is resolved ONCE off the owner's calendar (`period_after`) and threaded to `_reject_installment_move_before_loan`, deleting the second walk `pay_calendar:C13-b` measured and left, and that resolution IS the period's ownership check. The popover alone knows a human typed the figure, so it restates the definition (`template_amount_service.restate_in_effect`) at the transfer's final due date AFTER the door has moved it, and sends `derived_ownership(TEMPLATE)` with `is_override=False` (R-BAL37's heal, the twin of `one_off.restate_price`); a definition owning no price series keeps OWN at the parent, no flag. Carry-forward's move needs only its flag keyed on `recurs`. REJECTED: *"Doors re-place, service follows"* (the handoff's shape, the transaction twin's; two doors carry the rule, a third must remember it, and the popover derives a calendar the service repeats); *"Doors send occurs_on explicitly"* (a door can send one column without the other); *"Everything in the service"* (`amount_ownership` would mean two things by row shape, undoing **R-BAL11**) |

## The README entry as it stood before the 2026-09-22 condensing (`balance:X-bi-6-3`'s tick), verbatim

Condensed to one line under `docs/plans/conventions.md` rule 5, the README standing at 1,310 of its 1,330 lines with `X-cu` and `X-cv` to specify. Carried WITHOUT re-verification.

    * [x] **X-ci-1** `465f91cd` -- `recurs` on the twin (`TransferTemplate` / `Transfer` / `PaycheckLine`,
      **R-BAL97**), every no-cadence site re-keyed; the discardable count loads and asks (**BAL-492**);
      inside `update_transfer` a paycheck move re-places the date, `occurs_on` follows a date that moves, and
      a typed figure restates the definition's price before the settle (**R-BAL92**..**R-BAL94**, **R-BAL96**
      as amended; **BAL-493**); the date input offered on a one-time transfer. No schema; byte-identical on
      three harnesses; 15,003/0. Record: `archive/x_ci_1_as_built_2026-09-21.md`.
