> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# recurrence:R17 leaf 2, as built: a row IS its occurrence (2026-08-28)

**What this is.** The design record for `recurrence:R17`'s second leaf and for the three rulings it
carries out -- **R-R47**, **R-R48** and **R-R49** -- written so those rulings' ARGUMENTS have a home
outside the registry rows that state their RULES. It also records what four adversarial reviews
found, because three of the four defects were in the leaf's own design rather than in its typing.

**It is a DESIGN record, not a change record.** What the step changed is the commit, and that is the
authority: `4e8b40b3`, migration `c8e5a2f31b47`, merged with `origin/dev` at `6b8fa7b0`. Read the
code; this file holds why.

## The rule

A generated row answers ONE occurrence of its template's cadence. The pay period is where that
occurrence's money LANDS -- a derived placement, and one the owner may change by moving the row.

Both the skip predicate and the unique index were keyed on `(template, pay_period, scenario)`, so a
moved row vacated the period its occurrence named and the next whole-schedule pass answered that
occurrence again. Measured on a clone of production, 2026-08-28: **8 rows, `$1,482.93`**, six of them
duplicating a due date a `Paid` row already covered, and 0 after. Every one of the eight prior rows
was `is_override` -- not a coincidence, but the mechanism: `mutations.py` flags any template-linked
row whose `pay_period_id` changes, which puts it outside the partial index and makes the duplicate
storable.

## What is one statement now, and what that cost

`OccurrenceClaims` says what a template's rows already claim, for all three readers. A row claims its
`occurs_on`; a row whose `occurs_on` is NULL claims its whole PAY PERIOD.

**The NULL arm is measured, not chosen.** A row no cadence named cannot be compared against an
occurrence, so the only claim it can make is the pre-R17 one. Letting it claim NOTHING was tested at
the live door that reaches it -- unarchiving a template restores its soft-deleted rows and then
generates. On the archived `Emergency Fund` transfer template, 51 soft-deleted rows, all undated
because the backfill does not walk an archived template:

    today (period-keyed)      11 rows   $5,500
    NULL claims nothing       52 rows   $26,000     <- 41 phantom transfers, $20,500
    NULL claims its period    11 rows   $5,500

`rows_claiming` is period-UNSCOPED, and that is not an optimisation. The row that answers an
occurrence need not sit where the plan places it, so **no period-scoped fetch could have closed D57
however it was keyed.** The generate path consumed it from the start; the maintain path did not until
an adversarial review measured the gap (finding 1 below).

`occurrences_to_write` is the one generate decision, shared by both engines and by
`can_generate_in_period`. Sharing it is what makes the predictor an exact mirror rather than a second
opinion -- the R4b incident, where a hand-written copy said the engine would generate in 32 of 61
periods and the carry-forward executor acted on it. Measured after: **204 YES answers over 2,457
comparisons, zero disagreements**, on a clone with real gaps opened so both answers were exercised.
The first run of that measurement had the predictor saying YES zero times and proved nothing.

## R-R47: the index re-keys here, not at R5

Two partial unique indexes replace one, per table: `(template, scenario, occurs_on)` where the row
answers an occurrence, `(template, scenario, pay_period_id)` where it does not. `occurs_on` is
nullable and PostgreSQL treats NULLs as distinct, so a single index over it would have dropped the
one-row-per-template-per-paycheck rule for undated rows entirely.

The alternative was a guard clause in the predicate refusing a second non-override row in one
paycheck. That is a fence, and it puts back exactly the period-thinking `D57` exists to remove. The
constraint was proven rather than argued:

    ERROR: duplicate key value violates unique constraint
           "idx_transactions_template_period_scenario"
    DETAIL: Key (template_id, pay_period_id, scenario_id)=(20, 16, 1) already exists.

## R-R48: the D19 refusal retires, and carry-forward learns the difference

`refuse_unstorable_repeats` existed only because the paycheck-keyed index could not store a cadence
naming one paycheck twice. Once it can, the refusal guards nothing.

**A SECOND fence stood over the same fact**, and finding it is why this ruling covers both:
`carry_forward_service` refused any target paycheck holding more than one open row, citing that
index in its own docstring. Deleting one fence and leaving the other would have installed a latent
defect with two detonators -- a pay cadence of 30+ days, which is a setting the owner can change (2
of the developer's templates repeat a paycheck at 30, 11 at 31), and `R5` making the WEEK unit
authorable. `require_authorable_cadence` is what makes the second unreachable today.

The leftover now tops up the EARLIEST occurrence in the target paycheck -- the soonest obligation --
but only among the rule's OWN rows. **An override row disqualifies the tie-break**, which an
adversarial review is why: a row moved into the paycheck through the PATCH door carries
`is_override = True` and an earlier `occurs_on`, so ranking by occurrence would have selected it over
the paycheck's own canonical and written `estimated_amount = resolve + leftover` over a figure the
owner typed. The ruling was about rows a CADENCE names; it was never about a row the owner owns.

## R-R49: a dropped occurrence retires, it is never re-pointed

Where a rule EDIT moves the occurrence set out from under an existing row, that row is retired if it
carries nothing and held back as a conflict if it carries the owner's records. It is never re-pointed
at whatever occurrence is left in its paycheck.

Re-pointing is a deduction only if every row answers some occurrence, and a NULL `occurs_on` denies
it. It is the same inference an adversarial review cut from `scripts/stamp_occurrences.py`, where it
paired a `$12.34` envelope roll-forward with a car payment nine paychecks away. A wrongly adopted row
SUPPRESSES the real bill -- a payment vanishing with nothing on screen to show it, which is worse
than the duplicate this step exists to stop.

## What four adversarial reviews found

Three of the four defects were in the design, not the typing, and none would have been caught by the
suite as first written.

1. **D57 survived on the REGENERATE path.** `classify_maintain_work` decided CREATE from `existing`,
   which is a period set bounded by the pass's `effective_from`, so a row moved out of that window
   kept its occurrence named and was answered twice. Silent while the row is an override; an
   unhandled `IntegrityError` once the conflict chooser has cleared that flag. Reachable by saving a
   salary profile. Fixed by having the classifier read its own claimants.
2. **A `KeyError` 500 and silent re-pricing.** `derived` stayed keyed by period after selection moved
   to the occurrence, so a row in a period the rule does not name crashed, and where it does name it
   the row was re-derived from the wrong paycheck. Fixed by keying `derived` on the occurrence, which
   also stopped a repeated paycheck collapsing to the last placement's figures.
3. **The carry-forward tie-break above.**
4. **The backfill suppressed a bill.** `_placements_to_offer` collapsed a repeated period to its last
   placement, justified by `occurrence_by_period` -- a function this leaf deleted. It discarded the
   earliest placement before the due-date rule could see it, and the occurrence it dropped left a row
   NULL, which then claims the whole paycheck. Its period matching is deterministic now, ascending
   due date against ascending occurrence; the arbitrary pairing was live on one group of the
   developer's own rows.

**And a fifth finding was about the tests, not the code**: reverting the leaf's core predicate to the
pre-R17 question left **537 tests green**. Every second-pass case in the suite re-ran over an UNMOVED
schedule, where "this period holds a row" and "this occurrence is answered" are the same set. Six
controls were added and each was verified by mutating the production code back and watching it fail.

## What this leaf makes true elsewhere

**One paycheck may legitimately hold more than one generated row of a template.** Three consumers
assumed otherwise; two are fixed here and one was already correct.

- `carry_forward_service` -- fixed, above.
- `integrity_check` DC-06 -- re-keyed, and extended to `budget.transfers`, which it never graded.
- `statement_match.placements_for` -- already correct. It partitions on `len(matches)` and returns
  UNRESOLVED for more than one, saying in its own docstring that which row the owner meant is a
  guess it does not make. What changes is that the branch stops being near-unreachable: a merchant
  rule that resolves cleanly today returns UNRESOLVED for any period now holding two rows of its
  template, and since `bank_import:X-gf-3b-2` an UNRESOLVED placement has `sweep_class is None`, so
  the line also drops out of the one-click sweep. Fewer suggestions, fewer swept lines, more
  hand-picking; no money moves and the line keeps its own destination select.
- `pay_calendar:C4-a-2`'s reconcile panel keys two blocks apart by `(name, pay period)`, which stops
  separating them. Display only -- a tick posts the transaction id -- and recorded on that side.

**Two defects this leaf makes reachable and does not fix**, both reported rather than absorbed:

- **D18's third door.** `compute_due_date` is a pure function of `(rule, period)`, so two occurrences
  inside one paycheck take the same due date -- measured at cadence 31 on `Audible`, `occurs_on`
  2026-04-26 and 2026-05-26 both dated 2026-04-26. `R5` owns it, by giving each row its own `due_on`.
  `$0.00` on a 14-day cadence.
- **`occurs_on` renders nowhere.** No template and no schema exposes it, so for a repeat cadence the
  recurrence conflict chooser shows two rows identical in name, due date, period label and amount,
  and which one the owner picks is a coin flip. Raised for the developer to place.

## The registries, and one thing that emptied

`pay_calendar:C5b` is this commit under the pay-calendar arc's name -- an identity across arcs, filed
the way `C5a` / `R-F10` and `C2` / `X-l` / `R-F12` already are -- and `pay_calendar:C5`, its
container, ticks with its last leaf. `P16` closed with `D57`.

**That emptied the Cross-arc forks table**, since `P16` was the last fork, and twelve plan-gate
controls used the live corpus as their specimen. One of them exists precisely to announce it: *"no
fork at all -- rule 11's second half grades nothing."* It was reporting the truth. On the developer's
ruling the arms now pass on an empty corpus: `_staging.stage_a_fork` builds a well-formed ruled fork
out of REAL rows, and the two controls that still NAMED `P16` derive their specimen instead -- the
third re-anchoring of that class of control, and the reason it is derived rather than re-named.
`test_registry_integrity.py` went over pylint's 1,000-line ceiling in the process, so the fork
controls became `test_forks.py`, this project's ruling on an over-ceiling module being that it splits
rather than being shaved.
