> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

The as-built account of recurrence plan step **R8-a** and of the four rulings taken with it on
2026-08-16. Cite it for how those decisions came to be, never as a plan of record.

# R8-a: the offer set stops being gated on a derivation that was deleted

## What R8 said it was, and what measurement said

`steps.md` ranked **R8** at #20: "add the four ruled add-ons -- the WEEK unit,
`recurrence_weekday_anchors`, the business-day shift and the count-bounded end" (the last had already
left at R7b-3). Four premises were measured on `feat/r8` before any of it was built, and four did not
survive.

### 1. The gate refusing the WEEK unit was a fossil

`_frequency.anchor_family` refused `(WEEK, *)` because "the WEEK unit anchors on an authored date this
vocabulary does not yet collect", and `(YEAR, PERIOD_STARTING_ON_OR_AFTER)` because
`_resolution._first_of_month_anchor` "would fire in whichever month the schedule happened to open in".
**Both name derivations plan step R7c-b deleted** when ruling **R-R16** made the first occurrence
AUTHORED. `_first_occurrence` branches on one thing from that step: whether the unit's occurrences
are paydays.

Measured by lifting the refusal alone, on a 14-day calendar of 80 paydays:

```text
(1, year,   deferred)  -> canonical (1, YEAR)  'Yearly (Apr 15, first paycheck)'   3 occurrences
(2, year,   deferred)  -> canonical (2, YEAR)  'Every 2 years (Apr 15, first paycheck)'
(12, month, deferred)  -> canonical (1, YEAR)  'Yearly (Apr 15, first paycheck)'
(1, week,   containing)-> canonical (1, WEEK)  'Weekly (Wednesdays)'    36 occurrences, 36 seated
(2, week,   deferred)  -> canonical (2, WEEK)  'Every 2 weeks (Wednesdays, first paycheck)'
```

Every one resolves, walks, places, canonicalises and words itself correctly with no other change.

### 2. The three families carried no information

Over all eight `(unit, placement)` pairs, the router's one live projection --
`fires_on_day_of_month`, i.e. `family == FAMILY_CALENDAR` -- agreed with
`has_day_of_month_coordinate(unit) and placement is CONTAINING_DATE` on every pair it answered for,
and disagreed only on the three it refused. `_first_occurrence` had stopped dispatching on the family
at R7c-b; `canonical_cadence` compared two families only to work around the YEAR refusal.

### 3. Three of R8's four add-ons cannot deliver before R5

`recurrence_engine.compute_due_date(rule, period)` takes the rule and the PERIOD. The occurrence is
never passed -- plan ledger row **D26** -- so a generated row's date comes from the rule's scheduling
day of the month, or from `period.start_date`, and from nothing else:

| add-on | what a generated row would carry |
|---|---|
| WEEK unit | every weekly row dated on the funding PAYDAY; the authored weekday discarded |
| nth-weekday | dated from `starts_on.day`, the anchor's incidental day (D29 in the DATE, not the label) |
| business-day shift | NO stored row's date moves at all; only which paycheck the occurrence places into |

A second measurement bounds the WEEK unit further. Maximum occurrences landing in ONE paycheck, which
`idx_transactions_template_period_scenario` holds one row for:

```text
 cadence |  1 WEEK |  2 WEEK |  4 WEEK | 1 MONTH | 1 YEAR
       7 |       1 |       1 |       1 |       1 |      1
      14 |       2 |       1 |       1 |       1 |      1     <- the developer's cadence
      30 |       5 |       3 |       2 |       2 |      1
      90 |      13 |       7 |       4 |       3 |      1
     365 |      53 |      27 |      14 |      12 |      1
```

`(1, WEEK)` is unstorable at biweekly pay: `refuse_unstorable_repeats` refuses, the handler rolls
back, and the template is never created. `(1, YEAR)` never repeats a paycheck at ANY cadence in
`pay_schedule.cadence_days`' whole 1-365 domain, which is why the year-scale widening adds no
exposure.

### 4. Ledger row D20 was misdiagnosed

D20: "the placement axis has no 'the LAST paycheck on or before the occurrence', so a bill funded IN
ADVANCE is inexpressible ... both members fund on or AFTER".

```text
funded AFTER the occurrence, over five pay cadences:
  containing_date              0 of 305 seated occurrences   (worst lag: none)
  period_starting_on_or_after  290 of 302                    (worst lag: 89 days)

period_starting_on_or_before vs period_containing:
  0 disagreements over 8,460 days of tiling calendar
  past the horizon: containing = None, on_or_before = the LAST saved paycheck
```

`CONTAINING_DATE` funds from the paycheck whose span COVERS the occurrence, so its payday is on or
before that date by construction. The named remedy is a no-op inside the covered span and past the
horizon would seat every future occurrence of every rule in one paycheck. **D20 closed**; what
survived is a LEAD -- funding from a paycheck EARLIER than the containing one -- which opened as
**D40** with plan step **R11** as its owner.

## The four rulings (developer, 2026-08-16)

| id | ruling |
|---|---|
| **R-R23** | What GATES the offer set is what the app can HONOUR, derived from two live facts. `anchor_family` and the three `FAMILY_*` constants are deleted |
| **R-R24** | A year-scale cadence MAY defer onto a later paycheck; `(12, MONTH, deferred)` canonicalises to `(1, YEAR, deferred)` again |
| **R-R25** | The nth-weekday coordinate goes on `budget.recurrence_rules` as an exclusive arc; `budget.recurrence_weekday_anchors` is dropped unwritten (R8-c) |
| **R-R26** | "Non-business day" is weekends plus the eleven US federal holidays DERIVED as rules, not seeded as rows (R8-d) |

R-R25 was ruled against a plan sentence that is not buildable: the arc document said the
day-of-month-XOR-nth-weekday invariant becomes "a CHECK against `recurrence_rules.nominal_day`", and
a PostgreSQL CHECK cannot reference another table.

## What shipped

* `_frequency.anchor_family`, `FAMILY_PERIOD`, `FAMILY_CALENDAR`, `FAMILY_FIRST_OF_MONTH`: DELETED.
* `has_day_of_month_coordinate`: MOVED from `_resolution` to `_frequency` (a consumer's module may
  not import it), taking `_resolution._DAY_OF_MONTH_UNITS` with it. The public name is unchanged.
* `has_row_date_coordinate` and `require_row_date_coordinate`: NEW, and both die with
  `compute_due_date` at R5.
* `fires_on_day_of_month`: stated directly over the two facts.
* `authorable_cadences`: derived from `has_row_date_coordinate` and `emits_period_starts`.
* `canonical_cadence`: loses its `placement` parameter and the guard that skipped
  `(12, MONTH, deferred)`; the property that guard rested on -- MONTH and YEAR authorable on the same
  placements -- is now DERIVED and is asserted rather than checked at runtime.
* `CadenceWire.anchors_day_of_month` -> `schedules_on_day_of_month`, through the picker, the script
  and the browser harness.

## The browser pass (2026-08-17)

The arc's standing mandate ran and PASSED: `tests/manual/verify_recurrence_form.py`, against the
containerized dev app bind-mounted to this worktree, 0 console errors, 0 failures. Three of its
checks are this step's:

```text
PASS  transfer G: funding help says a choice        (was "says FIXED" -- the YEAR unit)
PASS  transfer H: a yearly cadence CAN be funded from a later paycheck
PASS  the WEEK unit is refused under the DEFERRING placement too
```

Nothing was persisted: 0 `ZZVERIFY%` templates afterwards, and the 46-rule cadence census is
byte-identical to the pre-run one.

**The harness itself was a defect this step had to fix first, and an adversarial review is what
found it.** Its refusal list still POSTed `(1, YEAR, deferred)` expecting "cannot be saved yet" --
the exact reading R-R24 admits. Those payloads carry a whole create form and `POST /templates`
GENERATES on create, so the run would have written a template, a recurrence rule and its projected
rows into a dev database cloned from production, and then failed the pass's own two persistence
checks -- which is how a real regression comes to be read as "the known year failure". The case was
replaced by the WEEK unit's other placement, and case G's expectation flipped with the fact it
asserts.

Two operational notes for the next run, both cost time here: the harness needs the project venv's
Python (`playwright` is not in the system one), and the dev app will not start in a fresh worktree
until `state/.seed-complete` exists -- see the `SEED_USER_LAST_PAYDAY` failure the dev compose
guarantees.

## The defect this step introduced, and what caught it

Stating `fires_on_day_of_month` directly deleted a refusal `_reading.scheduling_day_of_month`
INHERITED: `anchor_family` RAISED for the WEEK unit, and the direct predicate answers `False`, which
`compute_due_date` reads as "date this row from its paycheck". Every weekly row would have been dated
on the funding payday, silently.

`test_closed_pattern_set_dies_migration.TestTheSqlDerivationIsThePythonOne
.test_the_equality_is_claimed_for_AUTHORABLE_cadences_only` -- written at R7c-c for a different
purpose -- caught it. `require_row_date_coordinate` is the restated refusal, and
`test_recurrence_frequency.TestTheOfferSetIsWhatTheAppCanHonour.test_the_row_date_rule_has_a_RAISING_twin`
sweeps the predicate and its twin over the whole enum so the two cannot come to disagree.

## What the step also paid for

Six SHIPPED index rows left `steps.md` under `conventions.md` rule 5 -- `R7b-1`, `R7b-2`, `R7b-3`,
`R7b-4`, `R7c-a`, `R7c-b`. Their accounts are in `recurrence_as_built_2026-08-14.md` (all four R7b
leaves) and `recurrence_findings_as_built_2026-08-15.md` (R7c-a, R7c-b).

**Which constraint forced it, stated precisely, because a first draft of this paragraph named the
wrong one and an adversarial review caught it.** `steps.md`'s line cap is 260 and it stood at 240,
so the CAP did not bind and would not have at 245. What binds is the gate's own headroom arm
(`test_the_cap_still_has_headroom`): `actual <= cap - 20`, i.e. 240. R8's rows take it to 245, which
fails that arm and the commit with it. The archive is what rule 5 prescribes for exactly that, and
the alternative -- raising the cap -- is what rule 4 refuses.

It cost one thing worth recording. `tools/plan_gate/test_duplication.py`'s shortest-chain control
planted the literal ids `R7b-1` and `R7c`; `R7b-1` stopped resolving, so the control stopped firing
and the gate reported it. Its own docstring had predicted that failure mode in so many words. The
ids are DERIVED from the live table now, which is what that docstring asked for and what its sibling
control already did.
