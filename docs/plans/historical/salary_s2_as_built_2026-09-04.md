> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:S2 as built (2026-09-04)

**The input is named, it is not the engine, and NO SINGLE CALIBRATION REPRODUCES THE RECORD.**
Finding **N-442** recorded that production's seven March-June 2026 paychecks were generated and
settled at `$2,473.38`, re-derive at `$2,454.10`, and that nothing in the data accounted for the
`-$19.28`; its named candidate was a paycheck-engine change since 2026-03 that no audit trail
records, `balance:X-aw`'s deletion of the biweekly rounding residue (**N-239**). The moved input is
a `salary.calibration_overrides` row DELETED on 2026-08-28, and its deletion is in the audit trail
the row's census read. **The engine is exonerated by measurement.**

**And the owner's twelve settled paychecks span that replacement**, so eleven reproduce under the
deleted calibration and the twelfth reproduces only under the live one. That split is ruling
**R-SAL4** -- a calibration applies forward from its stub's date -- demonstrated on the developer's
own record rather than argued. An earlier draft of this document reported "11 of 11, `$0.00` on
every row" and an adversarial review found the twelfth row missing from the clone it was measured
on; the correction is the most useful thing here.

## What was measured, and against what

Every figure below was read on 2026-09-04 -- from `shekel-prod-db` directly (read-only `SELECT`s),
and from `shekel_s2`, a clone of the dev runtime database migrated to head `e7c3a1f9b482`.

**The clone is not production and the differences are enumerated, not asserted away.** Its
`budget.transactions` is a snapshot taken before 2026-08-28, so it was missing production's twelfth
settled paycheck entirely -- row 1630 sat Projected with both money columns `NULL` while production
carries it Received at `$2,572.78`. It was reconstructed to production's five settle columns
(`status_id`, `settled_amount`, `settled_on`, `settled_basis_id`, and `settled_day_basis_id`, which
production's schema does not yet carry) before the run below. **That row is the one the whole
correction turns on**, and a census of "the inputs the engine reads" that omits the rows being
GRADED is what let a first draft miss it.

Every engine input was then dumped side by side, row by row, as JSON: `salary_profiles`,
`salary_raises`, `paycheck_deductions`, `fica_configs`, `tax_bracket_sets`, `tax_brackets`,
`state_tax_configs`, `state_child_deductions`, `calibration_overrides`, `budget.pay_schedule`,
`budget.pay_periods` and `auth.users`. Seven of those twelve are byte-identical. The differences,
all of them:

| where | difference | does it move a paycheck |
|---|---|---|
| `salary_profiles` | production still carries `pay_periods_per_year` (`26`); `recurrence:R-F16` dropped it | No -- `365.2425 / 14` quantizes to the same `26` |
| `budget.pay_schedule` | the clone has added `history_opens_on`, `NULL` here | No -- `NULL` is the "not stated" reading `balance:X-bh-2` made the default |
| `budget.pay_periods` | production still carries `end_date` and `period_index`; `pay_calendar:C4-c` dropped them | No -- the engine reads neither |
| `auth.users` | five session and security-event columns | No |
| `calibration_overrides` | production holds the 2026-08-27 row; the clone holds the 2026-03-26 one | **This is the subject** |

## The deleted calibration

`system.audit_log` row **4212** is a `DELETE` of `salary.calibration_overrides` row **2** at
`2026-08-28 11:58:11.966062+00`, by `user_id` 1. Row **4213** is the `INSERT` of row 3 at the same
instant -- one act, two rows. The deleted row's every column is in `old_data`:

| | deleted (id 2) | live (id 3) |
|---|---|---|
| `pay_stub_date` | 2026-03-26 | 2026-08-27 |
| `actual_gross_pay` | `$3,526.00` | `$3,635.84` |
| `effective_federal_rate` | `0.0000000000` | `0.0000000000` |
| `effective_state_rate` | `0.0297972721` | `0.0287420232` |
| `effective_ss_rate` | `0.0551219512` | `0.0535529616` |
| `effective_medicare_rate` | `0.0128899603` | `0.0125225532` |
| `created_at` | 2026-03-27 11:54:30.955037+00 | 2026-08-28 11:58:11.966062+00 |

**It was in force when the first eleven rows were written.** Transaction 865 was created 2026-03-27
`12:18:05.980109+00` and rows 1620-1629 on 2026-04-02 `18:43:24.638731+00`; the deleted calibration
was created 2026-03-27 `11:54:30.955037+00`, twenty-three and a half minutes ahead of the first of
them. **Row 1630 belongs to the other side of the replacement**: its stored plan was rewritten to
`$2,572.78` at `11:58:11.966062+00`, the same instant as the INSERT, and it was settled at that
figure twenty-one minutes later.

## The re-derivation

`tests/manual/measure_settled_paycheck_derivation.py` prices every settled paycheck under each
calibration state and grades it against the figure the row was GENERATED at -- its stored
`estimated_amount` where one survives, else its receipt, which for these rows is the same figure
and is a MEASUREMENT rather than an assumption: production sits below `balance:X-au-e`'s migration
and carries `estimated_amount = settled_amount` on all eight non-override rows.

| payday | generated at | no calibration | 2026-08-27 (live) | 2026-03-26 (deleted) |
|---|---|---|---|---|
| 2026-03-26 .. 06-18 (7 rows) | `$2,473.38` | `$2,454.10` (-`$19.28`) | `$2,483.19` (+`$9.81`) | `$2,473.38` (**`$0.00`**) |
| 07-02, 07-16, 08-13 | `$2,562.67` | `$2,541.49` (-`$21.18`) | `$2,572.78` (+`$10.11`) | `$2,562.67` (**`$0.00`**) |
| 07-30 | `$3,065.12` | `$3,038.93` (-`$26.19`) | `$3,075.75` (+`$10.63`) | `$3,065.12` (**`$0.00`**) |
| **08-27** | `$2,572.78` | `$2,541.49` (-`$31.29`) | `$2,572.78` (**`$0.00`**) | `$2,562.67` (-`$10.11`) |

**Both ledger figures reproduce to the cent.** N-442's `-$19.28` is the no-calibration column
against the record; N-441's `+$29.09` is the distance from the no-calibration column to the live
one on those seven (`+$203.63` over them), and the `+$9.81` beside it is N-442's own COST column --
the fabricated variance on screen -- not N-441's figure.

**The split is the result.** The eleven rows generated before the replacement reproduce under the
calibration in force then, `$0.00` each; the twelfth, generated and settled after it, reproduces
under the calibration in force then, `$0.00`. Neither reproduces the other's. That is exactly what
an undated calibration cannot express and what `R-SAL4` rules.

## Why the engine is excluded, and why a bisect would have found nothing

The reproduction alone is an identity over the composite of engine change AND input drift, so
cancellation is not excluded by it. Two stronger arguments are, and both were available in the
repository:

* **A calibrated paycheck does not read the bracket path at all.** `_calibrated_tax_lines`
  (`app/services/paycheck_calculator.py`) takes all four withholding lines from effective rates and
  touches only the FICA wage-base cap. So the 2026-07-07 tax edit cannot move a calibrated
  paycheck -- which excludes it STRUCTURALLY rather than by N-442's in-session revert, and covers
  the `child_credit_amount` and `filing_status_id` changes that revert never tested.
* **`balance:X-aw` measured its own live-data effect at `078077db`**: five of the owner's 63 saved
  rows moved by one cent, 2027-01-14 to 2027-03-11, and none of the settled rows is among the five.
  The residue rule's whole reachable range on this salary was `{$3,525.96, $3,525.97}`, so X-aw
  could move a paycheck by at most a cent -- and twelve rows read `$0.00` or the calibration's own
  offset. The named candidate was already refuted, by its own step, uncited.

A bisect would have had to run below the migrations that reshaped `budget.pay_schedule`, and it
could only ever have named a commit. Naming the INPUT is what closes the finding.

## Why the row's census missed it

**A census that reads a table's surviving rows plus the audit trail's `INSERT`s and `UPDATE`s is
blind to what a value USED to be.** The fact lives only in a `DELETE`, and only in its `old_data`.
Audit row 4212 sat immediately before the 4213 the census did read. Separately -- and it explains a
different absence -- production's audit coverage begins `2026-05-06 10:44:08.89514+00`, after the
paychecks were generated, so the March calibration's own `INSERT` was never auditable, and a
calibration deleted before that day leaves no trace at all: the sequence stands at 3 and rows 1 and
2 are both gone, with only row 2's deletion recorded.

## The guard, and what it does NOT grade

The harness prices the stored calibration through `income_service.project_profile` and every state
through `paycheck_calculator.project_salary`, and asserts they agree. **That is one producer
invoked twice, not two doors** -- `project_profile` calls `project_salary` -- and a first draft
described it as an independent replay, which the review refuted. What it grades is the four
arguments this harness assembles standing outside a route: shown to fire on an emptied
`configs_by_year`, shown NOT to fire on a single-year collapse. It cannot grade the engine, and the
docstring now says so.

Further controls DO fire and are the run's own. The `none` and `2026-08-27` columns report non-zero
variances on most rows, so a `$0.00` cell is a result rather than a harness measuring nothing.
Pointed at a database with nothing to grade, the run prints `GRADED NOTHING` and exits 1, where a
first draft exited 0 in silence.

**And the VERDICT now distinguishes three outcomes where it distinguished two**, which matters
because the first draft printed this very run's result as *"the residue is what an engine bisect
must explain"* -- naming the wrong subsystem for the finding the run had just made. One state
reproducing every row means the inputs never moved; every row reproduced by SOME state while none
reproduces all means an input VARIED OVER TIME, which is a dated-input finding and never an engine
one; a row reproduced by NO state is the only outcome that leaves the engine something to answer
for. All three branches were exercised: the run above prints the middle one with its partition (11
rows under the deleted calibration, 1 under the live one), and re-run without the live rates the
twelfth row is reproduced by nothing and it prints `UNEXPLAINED: 1 of 12 ... That residue is what
an engine bisect must explain`, exiting 1.

A guard that was WRITTEN and then DELETED belongs here too: `_generated_figure` grew a refusal for
a row storing no plan and declaring no amount source, and the constraint
`ck_transactions_amount_ownership` -- the biconditional `(amount_source_id IS NULL) =
(estimated_amount IS NOT NULL)` -- makes that row unrepresentable. The control could not fire
because PostgreSQL refused to create the state. A fence around what the schema already forbids is
what this project's doctrine says to remove, so it was removed and the constraint cited instead.

## What this hands to S1, and it is a FORK

**`S1` as specified leaves the eleven earlier paychecks permanently wrong.** Its remedy is an
effective date on the calibration, "a migration backfilling the existing row at its stub's date,
and the engine resolving the calibration per period ... applied from its date forward and never
before". The existing row's stub date is 2026-08-27, so every period before that day resolves NO
calibration and re-derives on the bracket path -- the `no calibration` column above. Row 1630, and
only row 1630, comes out right for free.

**Three surfaces move, not one, and a first draft named only the narrowest.** The grid caption and
edit popover read the amount model, where the four override rows carry `amount_source_id IS NULL`
and `amount_rule` answers `OWN` from their stored figure -- so only the seven derived rows move
there, `-$19.28` each, `-$134.96`. But `/salary/<id>/projection` and the salary cockpit both call
`project_profile` over `calendar.saved()` and render `net_pay` for EVERY saved period, past ones
included, with `yearly_net_totals` summing them per calendar year. Against what those pages show
today, S1 as specified moves the eleven past paydays `-$334.32` on the 2026 net total; against the
figures the rows were generated at, `-$224.69`. No balance moves either way -- a settled row is
worth what it recorded.

**The deleted row is recoverable**, from audit row 4212's `old_data`, and restoring it as a second
effective-dated calibration is what makes all twelve settled paychecks re-derive to their own
generated figures. That is a decision about what the app should say a past paycheck was expected to
be, so it is the developer's; the id **N-535** is reserved for it (coordinator, 2026-09-04) and the
row is filed in this step's registry commit.
