> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-d` as built: a projected paycheck is not stored (2026-09-03)

**What shipped** (`ed06acf6`, migration `d7b2e6c1a483`). A salary row DECLARES
the definition that prices it and stores no figure, so a ROW's amount has one
producer -- `income_service.SalaryPricing`, reached through amount rule 2 --
where it had two: that producer at READ time and
`recurrence_engine._get_transaction_amount` at GENERATION time, with nothing
reconciling the stored copy (finding **N-224**). Rulings **R-FI**, **R-IY**,
**R-IZ**, **R-JA**, **R-JB**, **R-JC**.

## The population, and why it is not the projected rows alone

**59 non-override salary rows on production, settled ones included** (ruling
**R-JB**, developer 2026-09-02). The figure a settled row was PLANNED at when
its money moved is already stored once -- in `settled_amount` on the `derived`
settlement basis, which is what that basis means -- and on all eight of
production's it equals `estimated_amount` to the cent. So the cutover deletes a
copy rather than a fact.

**The rejected alternative is recorded with the defect it would have
reintroduced.** Declaring only the PROJECTED rows needs a writer at the settle
to freeze the plan AND a second at the revert to release it; without the second,
a paycheck reverted in order to be edited keeps a frozen figure nothing ever
recomputes -- N-224 reborn on the revert path, with the status back inside a
pricing rule that finding **N-262** took it out of.

The 4 override rows keep their figures: `is_override` is the only record the
migration can read of a human having touched a row, and taking the conservative
side of it can only leave a row storing a figure that was already true.

## What it moves: `$0.00`, proven rather than argued

`origin/dev`'s code on a pre-migration production clone, and this branch's code
on the post-migration one, produce baselines that differ in **nothing but the
deleted `amount_overrides` field** -- 9 accounts, 441 grid cells, 6,174 daily
points, and **zero added lines** in the diff
(`tests/manual/verify_balance_baseline.py`, both sides folding their own tree).

`tests/manual/verify_amount_resolver.py` over all 1,028 rows of the
post-migration clone: 0 refusals, 0 mismatches, **409 derived rows unchanged**
under a `$1,000` nudge to the column they replace -- which is STRUCTURAL, not
measured: that column is NULL on every derived row, so the nudge touched none
of them (finding **N-445**, corrected 2026-09-03) -- and **619 OWN rows moving
by exactly the nudge**, which is real. Rule census: 619 own, 59 salary, 350 transfer.

What DOES change is what a screen calls the estimate on the 8 settled rows: the
grid cell's `(est: ...)` caption goes from 4 rows to 11, `+$68.67`. Measured
before and after through the published maps rather than reasoned.

## The four dormant defects the stored figure was absorbing

**This is the shape of the step and it is worth stating once.** A cutover does
not only change what is computed; it removes the stored figure that was silently
making four other paths safe. Every one was `$0.00` before and live after.

1. **`-$9,677.24` on archiving the salary profile** (finding **N-261**, whose
   own text said *"it becomes live the moment the salary cutover NULLs them"*).
   `delete_profile` opens the template's price series at its `default_amount`,
   which `is_salary_linked_template` documents as vestigial for exactly this
   kind of template -- so 50 of 59 rows re-priced from a `$2,483.19`-`$3,328.41`
   range to `$2,572.78`. Ruled 2026-09-02: **freeze**.
   `salary_profile_service.archive_profile` records what each row was last worth
   BEFORE the profile is deactivated -- the act a settle already performs one
   tier up -- and the ordering is the fix, because
   `is_salary_linked_template` reads the identity-mapped collection and sees a
   pending `is_active = False` immediately. Re-measured: `$0.00`, 59 frozen.
2. **A 500 on the grid from the templates form** (finding **N-253**'s
   `is_income` axis). `transaction_type_id` is allowlisted and the Type select
   renders for any template; flipping a salary template Income -> Expense makes
   the maintain splat write EXPENSE and declare the rows,
   `_rule_within_definition` still classifies by the DEFINITION, `salary_net_for`
   takes income only and refuses, and `routes/grid/page` prices every row with
   no status gate and no `try` -- with no `AmountUnresolvable` handler
   registered. Refused at the DOOR rather than handled, because no rule CAN
   price such a row: the profile states an income figure and the template's own
   series is dormant while it is salary-linked. The migration predicate gained
   `transaction_type_id = Income` for the same reason.
3. **`_freshest_amount`'s third conjunct**, shipped in advance at `X-au-k`: this
   step is the condition that fix exists for.
4. **A hard template delete stranding a declaration the DOWNGRADE cannot
   restore either** -- filed as **N-444**, not fixed. The remedy widens a
   refusal guarding hard deletion for every template kind (rules 6 and 8).

## Two engines, one pass, one fence fewer

Deleting generation's salary thread left the transaction and transfer recurrence
engines' regenerate paths byte-identical but for three strings and a logger, so
`recurrence_engine/_pass.py` holds the ONE pass both delegate to and
`transfer_recurrence`'s documented `# pylint: disable=duplicate-code` is
**deleted rather than widened**. The earlier attempt recorded in that disable's
rationale failed because it tried to share the LOGGING; `PassReporting` carries
what actually differs. `_recurrence_common` went from 1,034 back to 870 by the
same move (ruling **R-IR**: the session that breaks a module splits it), and
`routes/templates/crud.py` past 1,000 gave its refusals `_validation.py`.

## What a LATER step must obey

* **A row's ownership follows its DEFINITION, never its status or its flags.**
  `_generated_amount_ownership` asks `template_amount_service.owns_its_amount`
  -- the app's one eligibility test for a stated price -- so generation, the
  conflict chooser and the maintain splat cannot come to disagree. `X-au-e`
  extends the derived class to every template row through that same function.
* **A per-kind cutover's downgrade restores from the RECORD, else from the
  definition's scalar** (**R-JC**), and the second arm is value-lossy and
  behaviour-lossless. State which, and say what each side was measured through.
* **Re-run `row_valuation.owned_contribution`'s caller census at every cutover
  that widens the derived class.** X-au-d re-ran it (all seven sites
  settled-only or guarded); an adversarial review caught the paragraph edited
  without the census being re-run, which would have left the next widening
  reading a date that did not cover it.
* **`income_service.SalaryPricing` is the only producer of a ROW's amount and
  NOT the only spelling of the projection.** `routes/salary/views` and
  `routes/salary/cockpit` each build the same `project_salary` call for their
  own breakdowns -- finding **N-443**, opened when an adversarial review
  falsified the wider claim this step had written in five places.

## What two neutral adversarial reviews found

Both were briefed never to run the suite. Between them: the templates-form 500
above; a migration with **no test in either direction** while its own docstring
said one could drive it (Definition of Done item 7 unmet); the "only producer"
claim, false in five places; an ordering control that graded the FIXTURE rather
than the route; a settled-row case asserting the plan and never the record; two
fixtures that made a template's series price and its `default_amount` the same
number, so `_stated_amount`'s no-fallback refusal was invisible to them; a
sensitivity assertion satisfied by any monotone dependence on salary; a dozen
stale citations and two wrong module names.

**Three of the new controls were MUTATED to prove they fire**: reversing the
migration's two restore statements brings a settled row back at `$11.11` instead
of `$2,473.38`; moving the freeze below the flag fails five cases; swapping the
two engines' `EVT_*` constants fails the new reporting control.

`pylint app/` EXIT 0, unpiped. Suite **12,714 passed / 0 failed**.
