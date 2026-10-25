> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Recurrence redesign, as built: the R4 cluster (2026-08-08)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_recurrence_redesign.md`; this record exists so that document's rule 4
cap measures the specification of what REMAINS rather than the record of what is done (rule 5).

Its predecessor, `recurrence_as_built_2026-08-05.md`, holds R1 through R3 and the rulings taken for
them (R-R4, R-R8, R-R10, R-R11).

**The span: R4a, R4b-1, R4b-2.** Together they replaced the reverse period matcher with the forward
occurrence engine, moved a rule's resolution onto its OWNER's schedule, and gave generation the
`(occurrence, pay period)` pairs. All three are on `dev` and, as of this record's write date, not yet
in a pull request.

| step | commit | what it did | closed |
|---|---|---|---|
| **R4a** | `1836a928` | `match_periods` answers FORWARD: the five `_match_*` helpers deleted and the adapter pointed at `app.services.recurrence`. Baseline re-frozen, +122 / -4 over the 12 predicted shapes. Two adversarial reviews added `Monthly First`'s repeat refusal (it never repeated a period and would have raised `IntegrityError` at a cadence >= 30) and made the day / month domain check bind the AUTHORED value rather than the coerced one | **D3**, **D5** |
| **R4b-1** | `b4538d25` | A rule is resolved against its OWNER's schedule, not its caller's window. `GenerationSchedule` separates the schedule a rule is RESOLVED against from the window a pass WRITES into. Its migration deleted 3 duplicate rows and corrected one wrong stored paycheck ($502.45 low, a third-paycheck deduction the extend could not see). Two adversarial reviews found an N+1, a mis-bounded regenerate sweep, an unenforced dataclass guarantee, an over-broad DELETE, and a "verified red" claim a lossy test port had faked | **D22**, **D25**, **D2** (narrowed to the FIELD, which R7b deletes) |
| **R4b-2** | `75346625` | Generation moved onto the occurrence pairs: `recurrence.rule_occurrences` replaced the `match_periods` adapter for all four readers, and `PlacementOutcome` tells a schedule HOLE from a horizon the schedule has not reached. Three neutral reviews caught the first draft alerting on healthy schedules, measured at 43% of biweekly schedule openings | **D7**, **D19** |

**One finding this span measured rather than closed.** **D10** -- a `Monthly First` anchor is
horizon-dependent -- was proven to reach no generated row: `_first_of_month_anchor`'s fallback
always answers a date strictly after the schedule's last payday, so under
`PERIOD_STARTING_ON_OR_AFTER` no occurrence derived from it can be placed. Proven by argument, by a
3,390,012-pair sweep (1,935,097 of which reached the fallback), and by two baseline shapes plus
`test_recurrence_resolution.TestTheHorizonDependentFirstOfMonthAnchor`. It re-points to R7c, where
the anchor becomes a stored column and stops being recomputed. "Say it on a surface" was ruled
unwarranted on that measurement (2026-08-08): no surface can see it.

**The gate this span left behind.** `tests/oracles/recurrence_baseline.txt` holds 430 shapes over two
schedules (biweekly x 79 periods and 90-day x 12, both contiguous, both crossing a leap day). It is
committed, compared line by line, and carries firing controls that patch the DEFINITION site and
require the gate's own assertion to go red.

## The alternatives that were considered and rejected for the whole arc

Moved here from the live plan's section 6 on 2026-08-08 under rule 5, to buy the room ruling R-R13's
specification needed. These decisions were taken before R1 and none has been re-opened.

**Add `EVERY_N_MONTHS` + `EVERY_N_YEARS` to the enum.** ~1 day, touches the ~12 pattern-aware
surfaces, fills the two named gaps and nothing else. Leaves D1-D4, the sparse table, and every
N-branch switch. The next gap repeats the work.

**RFC 5545 RRULE strings + `python-dateutil`.** Maximum expressiveness, no custom matcher. Rejected
as the storage model: a new dependency; an opaque string cannot be CHECK-constrained or queried
("what is due in March"); RFC 5545 sec 3.3.10 specifies invalid dates are IGNORED, not clamped, so a
monthly "31st" bill would silently lose 5 occurrences a year where Shekel clamps -- a behaviour
change on live data; and RRULE has no notion of pay periods, so the placement layer stays
hand-written either way. The vocabulary (FREQ / INTERVAL) is worth borrowing; the storage is not.

**Materialize occurrence dates into a table.** Rejected: `transactions` / `transfers` already ARE
the materialization. A second one is a second source of truth for the same fact -- the defect class
the balance-architecture arc exists to eliminate. Ruling R-R12 (2026-08-08) is the same principle
applied one level down: a generated row's occurrence becomes a COLUMN on the row that already
exists, never a second table.

## R-R6's measurement (the four `bounds.*` shapes that moved at R4a)

Moved here from the live plan on 2026-08-08 under rule 5. Measured against the R1 baseline's own
schedule and shapes, before R4a shipped, so R4a diffed against a prediction rather than a surprise:

```text
start.midperiod          old= 31 new= 31 SAME
start.on_period_start    old= 31 new= 31 SAME
start.on_period_end      old= 31 new= 30 MOVES  drops idx=011 occurrence=2024-06-15
end.midperiod            old= 18 new= 17 MOVES  drops idx=037 occurrence=2025-06-15
end.on_period_start      old= 18 new= 17 MOVES  drops idx=037 occurrence=2025-06-15
end.on_period_end        old= 18 new= 18 SAME
window.both              old= 13 new= 12 MOVES  drops idx=037 occurrence=2025-06-15
window.inverted          old=  0 new=  0 SAME
```

Every dropped row is a bill dated outside its own rule's window: a monthly-15th rule ending
2025-06-05 generating a row due 2025-06-15, and a monthly-15th rule starting 2024-06-16 generating
one due 2024-06-15. Zero live rules were affected: the only live `end_date` rules are `Every Period`
(whose occurrence IS the period start, so both readings agree) and Monthly rules whose bounds fall
outside the horizon. This amended R1's binding statement, which had said R4 re-freezes the
`long_cadence.*` lines "and no other line may move".

## D3's probe (the defect R4a closed structurally)

Moved here from the live plan on 2026-08-08. `_match_monthly` inspected only the months of a
period's two ENDPOINTS, and `cadence_days` is user-selectable 1..365
(`schemas/validation/pay_periods.py`), so a period spanning more than two months could not match the
interior ones. Probe at a 90-day cadence, monthly bill on the 15th:

```text
monthly day-15 occurrences found: 6      (expected 12)
  fired in period 0 ... (period 0 returned TWICE)
```

Half the occurrences vanished silently, and the duplicate would have violated
`idx_transactions_template_period_scenario` as an `IntegrityError`, i.e. a 500. Latent at the 14-day
cadence in use. **Its surviving half is plan ledger row D18**: `compute_due_date` still reads the
same endpoint-month scan to date a row, so the defect moved out of period selection rather than
dying, and ruling R-R12 is what finishes it.

## The Rulings table's SHIPPED and SUPERSEDED rows

Moved here from the live plan on 2026-08-08 under rule 5, to buy the room ruling R-R14 needed.
Every row below governs work that has shipped or has been superseded by a later ruling; none
governs a live step.

| fork | ruling | disposition |
|---|---|---|
| Due date model | Subtype table with `due_day` + explicit `due_month_offset` | **SUPERSEDED by R-R12**: the installment is `due_on` on the generated ROW, and no subtype table is created |
| Row columns | `due_date` -> `occurs_on` (rename, no value change) + new `due_on` | **SUPERSEDED by R-R12**: it is a value-SPLITTING migration, not a rename, because the column is polymorphic and for a loan payment is a POSTING INPUT |
| `Once` rules | Retired at R2e, BEFORE the new engine and the cutover | SHIPPED (R-R4 / R-R11) |
| R2 sequencing | R2a (vocabulary) -> R2b (subtypes) -> R2c-1 (the door) -> R2d (stop storing the derivation) | SHIPPED |
| R4 sequencing | THREE leaves: R4a (answer forward) -> R4b-1 (answer against the owner) -> R4b-2 (the pairs), so every money change lands in one reviewable leaf | SHIPPED, in production at PR #85 |
| The wrong stored paycheck | Corrected by the R4b-1 migration to the value a whole-schedule calculation gives, targeted by the defect's SIGNATURE rather than by row id | SHIPPED; the migration reported 3 / 0 / 1 on the clone and again on production |
| Bound semantics | Occurrence-bounded, not period-bounded | SHIPPED at R4a (R-R6) |
| Monthly First anchor | The 1st of the first month whose OWN first paycheck clears the bound | SHIPPED at R3 (R-R6); R7c's backfill must reproduce it |
| Period-unit anchor | The bound DATE itself, not a period boundary | SHIPPED (R-R8) |
| Write-door enforcement | Nothing to enforce: the derived half is not stored | SHIPPED (R-R10) |

## R-F7's proof (two unreachable branches in `_first_of_month_anchor`)

Moved here from the live plan on 2026-08-08 under rule 5. The step R-F7 survives; only its proof
moved, because the argument is finished and the step is a deletion.

Both guards in `app/services/recurrence/_resolution.py` are provably dead, and one carries a comment
describing a case that cannot execute -- worse than the dead code, because it tells the next reader
the function handles something it does not.

*The in-loop `earliest is not None`.* `earliest_start_in_month(y, m)` is called with the year and
month OF A PERIOD ALREADY IN `calendar.periods`, so that period is itself in the minimand and the
result is never `None`.

*The fallback's `if earliest is not None and earliest >= effective`.* The fallback runs only when the
loop returned nothing. If that month's earliest payday were `>= effective`, the period whose start IS
that payday would have passed the loop's own `start_date < effective` guard, and its month's earliest
is the same value -- so the loop would have returned. The branch is therefore unreachable and the
function always falls through to `_next_month_first(effective)`.

Proven both ways: the argument above, plus a brute-force sweep of 243,018 `(schedule shape, effective
date)` pairs across cadences 1-365, gapped and degenerate schedules, and bounds inside and past the
horizon -- 32,006 of which reached the fallback. **Neither guard was ever taken.** An independent
coverage audit re-derived the same conclusion 2026-08-08. Deleting them is provably
behaviour-identical, so the R1 baseline must stay byte-identical and
`tests/test_services/test_recurrence_resolution.py::TestTotality` must stay green unchanged.
