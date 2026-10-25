> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `pay_calendar:C14` as built

**This is the archived specification span for `C14` and its eight leaves, moved out
of `docs/plans/implementation_plan_pay_calendar.md` on 2026-09-11 under
`conventions.md` rule 5** when `C17` was decomposed into four leaves (**R-PC69**)
and the document, at 472 of an effective 500 lines, had no room to specify them.
Cite it for HOW a decision came to be, never for what is true now: the code as
committed is the source of truth, and every open finding these steps left is in
`ledger.md`.

**What stayed behind rather than being archived**, because rule 5 forbids a live
sentence depending on an archived one: each step's id, its commit and what it
closed remain in the arc document as a one-line entry, so the live prose that
cites `C14` and `C14-c` still resolves; `C14-a`'s contract for its
callers (the displacement may answer outside the calendar bounds, and refuses a
non-member convention) stays on its one line; and the ONE obligation still
addressed to a LATER step -- `C14-c`'s, that the probe window's estimate and its
candidates share ONE anchor -- is restated in `C17-b-2`'s live specification,
the leaf that restores it. The obligations `C14-c`, `C14-d` and `C14-e-1`
addressed to `C14-e` were discharged when `C14-e-3` shipped, and `C14-e-2`'s
(the anchor names the batch that wrote it) was superseded at `C17-a`, where
`nominal_anchor` was absorbed into the era's `effective_from` (**R-PC66**). The
findings these steps opened or narrowed that remain open (**N-492**, **N-493**,
**N-494**, **N-495**, **N-496**, **P80**) are `ledger.md`'s, which is rule 5's
first condition; **PC-497** closed at `C14-e-3` and **PC-498** at `C17-b-1`.

**Carried WITHOUT re-verification**, which rule 5's third condition requires be
said: every hash, count and dollar figure below is reproduced exactly as the arc
document held it on 2026-09-11. Nothing here was re-measured at archive time.

---

- [x] **C14 -- the pay schedule carries its shift convention** `5d14e4d4` -- the container ticked
      with its last leaf `C14-f`; all eight leaves have shipped (rulings **R-PC47**, **R-PC57**,
      **R-PC61**, **R-PC63**).
- [x] **C14-a -- the shared business-day module.** `088339f5`. `app/utils/business_days.py`: the
      weekend rule, the computed federal holiday set of `5 U.S.C. 6103(a)` under `6103(b)` and E.O.
      11582, and ONE `shift_to_business_day` displacement, pure and reusing the
      `BusinessDayShiftEnum` seeded at `recurrence:R2`. **What a later step must obey**: the
      displacement may answer OUTSIDE `CALENDAR_DATE_MIN`..`MAX` and bounding it is the CALLER's,
      and it REFUSES a non-member convention rather than defaulting one forward.
- [x] **C14-b -- the convention column.** `229f0e23` -- gave the schedule its payday convention,
      defaulting to `none` and asked on the four cadence templates (**R-PC56**), with NO CHECK
      constraint (**R-PC59**): the floor is derived from a holiday set that MOVES and a CHECK
      expression must be immutable, so the pair is refused at the column's one write door. Opened
      **N-493** and **N-494**.
- [x] **C14-c** `659260c0` -- ONE end rule: a projected period ends the day before the NEXT payday,
      and `project_period_after` keeps its O(1) jump by probing the estimate's two NEIGHBOURS
      (**R-PC57**). Opened **N-495**, **N-496**. **A LATER STEP MUST OBEY**: `projected_payday` is
      the SINGLE body `C14-e` displaces, and the probe window's second premise is that the estimate
      and its candidates share ONE anchor.
- [x] **C14-d** `c1ce08b5` -- the floor asks `projected_payday` rather than restating
      `latest + cadence` (**58** of 1,951 future paydays refused before, 0 after) and
      `extend_pay_periods` hands the NOMINAL grid day; the grid became `_grid.py` (**R-PC60**).
      Opened **PC-497**. **TWO OBLIGATIONS FOR `C14-e`**: the floor must read the STORED convention
      and not the incoming `Rhythm.shift`, or a convention-changing batch closes the calendar under
      the other; and the WRITER must record each element DISPLACED, or the extend is refused.
- [x] **C14-e-1 -- the rhythm is ONE value.** `f32c9d7a`. `Rhythm` moved out of the session-holding
      `pay_schedule_service` to the pure leaf `app/services/pay_rhythm.py`: pylint **R0401** refused
      the package itself (`pay_calendar` -> `._loader` -> `pay_schedule_service`), and a second
      identical pair inside it is rule 14's defect. Every producer takes the pair; `resolve_shift`
      and its per-request duplicate query are DELETED. **A LATER STEP MUST OBEY**: the floor reads
      the STORED convention, and nothing grades that until `C14-e-3`.
- [x] **C14-e-2 -- the grid carries its own phase.** `ab5b26bc`, migration `a1c7e5d20f43`
      (**R-PC61**). `budget.pay_schedule.nominal_anchor`, three of `C17`'s five era columns; extend
      steps from it, not from its own last output. Closes **PC-497** fault 2 -- 178 of 301 wrong
      under `prior` at a batch of ONE before, 0 after. **A LATER STEP MUST OBEY**: the anchor names
      the BATCH THAT WROTE IT, not a piecewise owner's surviving grid (**N-492**), so
      `nominal_payday_after` is asked against the paycheck's END.
- [x] **C14-e-3 -- the shift goes live** `ed267298` -- every projected and backdated payday became
      the nominal day displaced onto a business day, and the WRITER records the displaced day
      (**PC-497** fault 1). **MOVED MONEY.** Closes **N-398**; **N-495**, **N-496**, **PC-497** and
      **PC-498** did not close with it and re-point at `C17`.
- [x] **C14-f -- the generate door asks one job's questions** `5d14e4d4` -- an owner who already
      holds a rhythm is asked only how many more paychecks, a rebuild that skips a whole paycheck
      asks first, and the gates moved to `pay_period_gates` (**R-PC63**, superseding **R-PC55**).
      **P80 does NOT close**: `regenerate` still writes a 140-day gap at HTTP 200, so it re-points
      at `C17` as an era question. **N-493** and **N-494** are NARROWED, not closed.
