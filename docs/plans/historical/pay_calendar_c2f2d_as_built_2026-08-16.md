> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The pay calendar, as built: the C2-f2d span (2026-08-16)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_pay_calendar.md`; this record exists so that document's shipped
entries can be pointers rather than accounts of themselves (`conventions.md` rule 5). Archived to
make room for the three rulings `C2-f2d-3` took, under the same rule that says to shrink the record
of what is DONE and never the specification of what remains.

**`C2-f2d` is a COMPLETED span**: four leaves shipped and the container ticked with the last of
them. What the code does now is stated by `savings_dashboard_service/_types.py`,
`retirement_plan.py` and `paycheck_calculator.py`'s own docstrings.

## The five entries

- **C2-f2d -- `/savings` and `/retirement`.** `c95519dd`. The DECOMPOSED parent, ticked with
  C2-f2d-3. Split into three leaves 2026-08-16 exactly as its own bullet predicted, and into a
  FOURTH when C2-f2d-2 made row **P59** visible. The prediction that it was a multi-session step
  held: four leaves over two days.

- **C2-f2d-1 -- the route opens the render's ONE read pass.** `731f6b3c`. Closed **P43**, ruled
  **P54**, opened **P55**-**P58**. **P43's `/retirement` half was measured FALSE first**: that page
  ran on TWO passes, worth `$4.18` and one paycheck of countdown across a midnight-into-payday
  render. Proof: `verify_retirement_pass_cutover`'s docstring, and `test_one_read_pass_per_render`,
  which fails on `dev` at 2 / 2 / 3.

- **C2-f2d-2 -- the retirement picture has ONE producer.** `9e479ca5`. Closed **P57**; opened
  **P59**, **P60**. `retirement_plan.py` replaced the SECOND implementation of "the picture at a
  candidate plan": a `PlanPoint` says which plan, a `RetirementInputs` is what a render loads once,
  and a memoized `picture_at` derives it, so the lever baseline IS the hero's object.
  Byte-identical -- the rendered HTML matches apart from CSRF nonces; 179 -> 87 queries a render.
  Proof: `verify_retirement_render`'s docstring, and `TestOneLoadPerRender`.

- **C2-f2d-4 -- the levers solve at the assumptions the page SHOWS.** `b84dada4`. Closed **P59**;
  opened **P60**. The what-if sliders moved the hero and not the lever card, so a page reading
  "65.3% funded" at a 3.5% withdrawal rate told the owner `$174.76`/paycheck closes the gap when
  `$273.17` does. Each stepper now carries the OWNER's override alone -- a pre-filled one laundered
  the previous solved default back as an entry -- and `PlanPoint` became RESOLVED, which is what
  stops one plan holding two memo keys. Proof: `TestReadinessFragment`.

- **C2-f2d-3 -- `/savings` AND the paycheck engine read the derived calendar.** `c95519dd`. Closed
  **P58**; carried **P55**'s `/savings` half and **P56**'s first door; opened **P61**, **P62**,
  **P63**. The developer ruled three forks WIDER than the spec (all three are in the live document's
  rulings table), so it also carries the paycheck-engine cutover and `C2-f2e`'s `/accounts/<id>`
  half. Proof: `pylint app/` 10.00/10 and 9,620 passed, plus
  `TestOneCalendarDerivationPerRender`, shown firing at 5 / 4 / 7 on the tree it was written for.

## What a LATER step must still obey

Repeated in the live document's rulings table, so this archive is not load-bearing for any of them:

- **The paycheck engine takes `DerivedPeriod`, and `PeriodInfo.period_id` is never `None`.** That
  is a property of WHERE a caller gets a period (`saved()`, `period_containing()`, `period_by_id()`
  are all MATERIALISED-only), not of a check inside the engine. A step that lets a projected period
  reach it collapses three consumer maps onto one `None` key.
- **A producer below the route takes the read pass and does not build one**, and it does not derive
  what the pass already memoizes either. `TestOneCalendarDerivationPerRender` is the second count
  of that question, and it exists because the read-pass counter could not see a producer that holds
  the pass and derives anyway.
- **`/savings`'s period SET is not a bundle field.** `reported_periods()` is memoized on the
  calendar and the calendar on the pass, so a field is a memo of a memo.

**P55, P56, P60, P61, P62 and P63 remain OPEN** and are carried on `ledger.md`, not here.
