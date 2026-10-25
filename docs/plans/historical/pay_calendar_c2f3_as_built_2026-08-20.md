> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The pay calendar, as built: the C2-f3 span (2026-08-20)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_pay_calendar.md`; this record exists so that document's shipped
entries can be condensed to a pointer under `conventions.md` rule 5 without losing what each leaf
proved. Cite it for how something came to be, never for what is true now.

`C2-f3` is the leaf that finished the arc's central sentence -- **one calendar value answers every
"which period" question** -- and its tick is also `C2-f`, `C2`, `balance:X-l` and
`recurrence:R-F12`. At the tick `app/services/pay_period_service.py` holds ONE function,
`earliest_recordable_day`, which asks no "which period" question at all.

**On the leaf count, because two live documents disagreed and neither is now checkable.** This
document said the split was into SIX leaves and `steps.md` said FIVE. What IS checkable is what
shipped: five leaves, `C2-f3a` through `C2-f3e`; and two further leaves promoted OUT to `C11` and
`C12` on 2026-08-19 because neither removed a reader of a column `C4` drops, so leaving them in
gated `C4` behind unrelated work. Five shipped plus two promoted out is SEVEN, which reconciles
with neither stated count. The disagreement is recorded rather than resolved -- picking a side
would be inventing evidence, and a stated count with no reconciler is the defect this whole arc
exists to remove from `budget.pay_periods`.

*A draft of this paragraph said `C2-f3c` split into two on 2026-08-18. It did not: no `C2-f3c-N`
id has ever existed (`git log --all -S"C2-f3c-"` is empty) and `C2-f3c`'s own commits are dated
2026-08-19. The step that split that day was `bank_import:X-f6a-3c`, and the draft reached for it
to make the arithmetic work -- which is exactly what this paragraph exists to refuse. Caught by an
adversarial claim-verification review before the commit.*

## The leaves

- **C2-f3a -- the surfaces that hold no read pass.** `d09761a7`. **`get_current_period` is
  DELETED** with its missing `ORDER BY` and its process clock. Closed **P19**, **P52** and
  **P47**'s duplicate half; opened **P67**-**P69**. Proof:
  `tests/manual/verify_current_period_cutover.py` (1,668 days, 0 disagreements, firing control 12)
  and `TestTheWindowIsAnsweredByTheDerivation`, whose two regression cases were RUN against the
  merge base and FAIL there.

- **C2-f3b -- the destructive doors decide in DERIVED periods.** `91e7f5bf`. No decision in
  `pay_period_admin` or the settings period list reads a column **C4** drops; the lock classifier
  takes a `PayCalendar`, so an unmaterialised period is unconstructible rather than refused; both
  writer doors take retiring IDS; each door reads the OWNER's day once. Closed **P64**'s calendar
  half; opened **P70**, **P71**. Proof: `verify_pay_period_doors_cutover.py` (62 periods, 0
  disagreements, firing control 1).

- **C2-f3c -- the generation seam holds ONE read.** `53488cbf`. `GenerationSchedule` is
  `(calendar, write_period_ids)`; no ORM `PayPeriod` reaches its ten modules, the regenerate row
  select is a period-ID set, and `get_all_periods` and `regeneration_bound` are DELETED. Closed
  **P68** (carry-forward render: 2 derivations -> 1). Proof: on PRODUCTION all 62 periods' derived
  end and ordinal equal the stored ones, zero disagreements; a mutant narrowing the sweep to the
  plan's periods is killed by NINE tests.

- **C2-f3d -- the Spending report's ordinal search.** `1bb99847`. The chart's twelve windows are
  ONE `PayCalendar.window` slice, `_shift_window` is DELETED with `app/`'s last ORDINAL search, and
  neither settled-expense query hydrates `Transaction.pay_period`. Closed **P45**, whose count was
  wrong (**23** statements a render, or 12 where the chosen window's rows masked the pk loads; now
  **1**); opened **P72** and **P74**.

- **C2-f3e -- the grid's create fragments take the ID.** `4f134bf4`. The two create partials take
  `period_id` where they took a whole ORM `PayPeriod` for one integer, and
  `_transaction_empty_cell`'s `account` went the same way; the three routes' twenty-line ownership
  prefix became one `_resolve_grid_cell`, whose PERIOD check is
  `calendar_for(...).period_by_id(...)` and no longer a `row.user_id` comparison, and whose refusal
  LOGS. Closed **P51**; opened **P75**, `balance:N-328`, `balance:N-329`. Proof: the pre-step
  ownership check fails the hydration probe on all three routes, a doubled derivation fails the
  architecture gate on all three, a distinguishable refusal fails the indistinguishability test on
  all three, and dropping a `period_id` context key fails exactly the field tests for that partial.

  It also closed `balance:D42` -- *"a plan-gate control is pinned to a step id, and rule 5 is the
  rule that removes step ids"* -- by building that row's own stated fix. Ticking this container
  turned four of the gate's controls red at once, because their specimen WAS the
  `balance:X-l` / `pay_calendar:C2` / `recurrence:R-F12` identity class; each now DERIVES its
  specimen and stages the shape on real rows, and each was shown firing on the historical per-arc
  blind spot in `_classes.decomposition_leaf_keys`. `recurrence:R-F1` was being held in a
  size-capped index solely for the control that named it, and is now archivable.

## Must not be undone

- **`get_current_period` and `get_all_periods` are DELETED**, with `regeneration_bound` and
  `_shift_window`. `pay_period_service` holds `earliest_recordable_day` alone, and it is not a
  "which period" reader.
- **No ORM `PayPeriod` enters the recurrence generation seam.** `GenerationSchedule` is a calendar
  plus a writable period-ID set, and the regenerate sweep selects on that set rather than on
  `pay_periods.end_date`.
- **The grid's three empty-cell fragments name IDS, not rows**, and prove the submitted period
  against the owner's derived calendar. A reader that starts taking dates off a period object here
  is re-opening **P51**.
- **A statement window is one `PayCalendar.window` slice.** Re-introducing a per-bar ordinal query
  re-opens **P45**, whose own measurement was wrong in the direction that hid it.

## P6's census, re-grepped at the tick

**Row P6 -- "SEVEN implementations of *which pay period contains this date*" (= `recurrence:F-12`)
-- CLOSED with `C2` at `4f134bf4`.** Its last survivor,
`pay_period_service.get_current_period`, was deleted at `C2-f3a`; the predicate was re-run at the
tick rather than carried, because a closed row needs its OWN predicate re-measured and not the
sites the closing step happened to touch.

**Three near-misses the re-grep turned up, none of them P6's**, recorded so the next reader does
not have to derive them again:

- `entry_service/_sums.check_purchase_date_in_period` tests `start <= purchased_on <= end` against
  a period it is HANDED. A containment test is not a containment SEARCH. (It does read
  `transaction.pay_period.end_date`, which is **C4**'s ORM-relationship reader set, not this row's.)
- `recurrence_engine/_plan.py:317` tests containment while iterating the two MONTHS a period
  touches, not the owner's periods -- the endpoint-month scan ledger row **D18** owns.
- `reconcile_service/_rows.py:213` filters `PayPeriod.start_date <= observed_on`, which is "periods
  that have started", not "the period containing".

**The lesson C2 carried and this record keeps:** an AST census keyed on the containment PREDICATE
could not see `savings_dashboard_service._period_id_at`, and one keyed on containment cannot see an
ORDINAL search at all -- `C2-f1` found three more that way and `C2-f3a`'s reviews found a seventh
spelling of a period LABEL nobody had censused. A census of this kind is a floor, not a total.
