> **ARCHIVED. Historical record only -- this document governs nothing and may be out of date.** The
> live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as committed is the source of truth for what the app
> does.

# Five shipped recurrence steps, archived out of the plan of record (2026-08-27)

**What this is.** The `- [x]` entries that left `../implementation_plan_recurrence_redesign.md` on
2026-08-27 under conventions.md rule 5, with their `steps.md` rows, in TWO passes that day: three to
buy the room `recurrence:R16-b`'s decomposition and ruling **R-R37** needed, then two more for
`R7d-c`'s. Rule 4 forbids raising a cap when it binds, and it bound twice.
**The COMMIT is the record for every one**; read the code each shipped, not this table.

## The FIRST pass, for `R16-b`

The document stood at
**828 of its 850-line cap against a 20-line headroom floor -- two lines of room**.

**Why these three.** `recurrence_completed_findings_span_as_built_2026-08-19.md` named the next
candidate outright -- "`R7a-1` is the cheapest remaining candidate once its three live citations are
re-pointed" -- and the one reason `R-F1` was held has since lapsed, as its own entry said: it stayed
for a `tools/plan_gate` control that NAMED it as the worked example of the PREFIX trap, and
`pay_calendar:C2-f3e` made that control DERIVE its specimen (`_staging.a_prefix_trap`), closing
**D42**. None of the three blocks anything: no `starts` cell in `steps.md` names any of them, which
for `R7a-2a` is a RE-GREP rather than an inherited claim -- the 2026-08-19 record called it
`recurrence:R-F17`'s blocker, and `R-F17` has since shipped with a `--` `starts` cell.

| step | commit | what it shipped |
|---|---|---|
| `recurrence:R7a-1` | `6fed14af` | The Recurrence cell became ONE function over `(interval, unit)`, replacing eight hand-written template branches keyed on the closed pattern set, so a cadence nothing authors yet already reads correctly. Closed **D17**. |
| `recurrence:R7a-2a` | `003e3657` | The paycheck count became DERIVED per owner (`pay_calendar.PayCadence`) rather than a `Decimal("26")` constant nine files read, so every monthly-equivalent figure stopped being wrong for an owner who is not paid biweekly. Opened **F-16**, **F-17**. |
| `recurrence:R-F1` | `44b25ad3` (migration `c7f3a9d1e864`) | The lagging `ref` identity sequences were put back in step. Closed **F-1**. Its fuller account was already archived to `recurrence_as_built_2026-08-15.md`. |

## Where their live citations now point

**Rule 5's second condition is that no live sentence may depend on an archived one.** Both surviving
citations of `R7a-1` are PROVENANCE -- "this is where that came from" -- which rule 15 permits
against an archived record, and each was re-pointed at this file rather than left naming a row that
no longer exists:

| the citation | what it says |
|---|---|
| The recurrence plan's Half-A / Half-B split ("Half A = R1-R4, R7a-1 through R7c") | Names the span the parallel run covered. Re-pointed here. |
| `ledger.md` row `D29` | Cites `R7a-1` as where `describe`'s coordinate dispatch came from; `R8-c` owns the row and the citation is history, not a dependency. Re-pointed here. |

`conventions.md` rule 13's own prose still names `R-F1`, `R-F10`, `R-F12` and `R-F13` as the worked
example of the prefix trap that made the gate DERIVE its specimen instead of naming one. That
sentence is an argument about how the gate came to be designed and does not depend on `R-F1` being a
live row -- which is the very point it makes.

## The SECOND pass, the same day: `R-F16` and `R-F17`

**`recurrence:R7d-c-1`'s registry pass needed about 22 lines** -- `R7d-c` decomposing into two
leaves, the new step `R17`, and the findings `D57` and `D58` -- against six lines of room, not the
zero this file's last section predicted: the document stood at **824** when the pass began, because
`f7b0ce16` on the same branch had already deleted a six-line paragraph. Measured over the whole
pass: these two entries freed **11** lines, compressing the signpost and the archive note freed
**3**, and dropping a completed migration's expand / backfill / tighten history from section 3 freed
**4**.

| step | commit | what it shipped |
|---|---|---|
| `recurrence:R-F16` | `4258ce28` (migration `f2b7c40d918e`) | ONE producer for "how often am I paid": `salary_profiles.pay_periods_per_year` dropped and the paycheck engine takes a `PayrollBasis` binding a profile to the cadence its paychecks arrive on, so a mismatched pair is unrepresentable -- only 5 of 365 legal cadences could ever have agreed. Closed **F-16**; opened **D43**, **D44**, **D45**. |
| `recurrence:R-F17` | `e2afd21b`, ruling **R-R31** | A month-named forward window resolves in the OWNER's paychecks: `PayCadence.paychecks_within` replaced the hardcoded 6 / 13 / 26 / 52 in five surfaces, one of which announced six months over thirteen weeks. Biweekly answers the same numbers, so nothing on the developer's data moved. Closed **F-17**; opened **F-21**, `pay_calendar:P73`. |

**Why these two and not the four the last section listed.** `R-F10` and `R-F12` are each
IDENTITY-PAIRED with a row in another arc -- `R-F10` with `pay_calendar:C5a` (`ticks it` /
`ticked by it`), `R-F12` with `pay_calendar:C2` and `balance:X-l` -- so removing their `steps.md`
rows would take a member out of a rule-11 identity class and leave two other arcs' `also` cells
naming a row that does not exist. Their entries stay in the live document. `R16-a` was rejected for
a different reason, re-grepped here: `recurrence:R16-b-2`'s `starts` cell NAMES it
(`recurrence:R16-a (shipped)`), so it is a live blocker key.

**Both predicates were re-grepped on 2026-08-27**, over `steps.md`'s `starts` and `also` columns: no
cell in either column names `R-F16` or `R-F17`, and no rule of `conventions.md` and no other arc
document does either.
**51 files under `app/`, `tests/`, `migrations/` and `tools/` cite one of the two** and every one
names the STEP rather than a sentence of its entry, which is PROVENANCE and is what rule 15 permits
against an archived record; the same is true of `ledger.md` rows `D43`, `D44`, `D45`, `F-21` and
`pay_calendar:P73`, so none was re-pointed.
**The one worth naming is `tools/plan_gate/test_order_and_archive.py`** -- a GATE citation, the
category that kept `R-F1` out of the first pass -- and it was checked: that control DERIVES its
specimen from `balance:X-f4`, so archiving these two cannot make it grade nothing.

## What the next session should expect

The document stands at 830 of 850 after BOTH passes, which is
**AT the 20-line headroom floor and not under it** -- `test_the_cap_still_has_headroom` asserts
`<= 830`, so the room is zero again. The next recurrence step to touch it must archive before it
adds a line. **What the remaining candidates cost is recorded above**, and the arc's `## Rulings`
table is the largest single block that is not a step: 46 lines, and `conventions.md` rule 9 keeps a
ruling under its own arc, so moving it needs a ruling of its own rather than a session deciding it
while short of room.

**Each candidate's predicate was RE-GREPPED here rather than carried from the 2026-08-19 record**,
and one of that record's claims had already lapsed:

| candidate | measured 2026-08-27 |
|---|---|
| `R7a-2a` | **No longer blocked-by anything.** The 2026-08-19 record said it was `recurrence:R-F17`'s blocker; `R-F17` is SHIPPED with a `--` `starts` cell, and `R7a-2a` now appears in `steps.md` exactly once, in its own row. It is the cheapest remaining candidate. |
| `R7c` | Still `pay_calendar:C6`'s blocker in `steps.md` (its `starts` names `recurrence:R7c (shipped)`; the RANK beside it has moved since and is not quoted here), so rule 13 still grades that key. |
| `R7c-c` | Still carries a LIVE obligation -- `R5` deletes `compute_due_date` and the WEEK unit `R8-b` frees is where that migration's SQL and its Python twin part -- so rule 5 forbids it. |
| `R-F10` | Identity-paired with `pay_calendar:C5a` (`ticks it` / `ticked by it`); the pairing has to be handled first or rule 11's class loses a member. **Still true after the second pass**, which is why it stayed. |
| `R-F17` | Two live `ledger.md` citations (`F-21`, `P73`) would need the re-pointing this pass gave `R7a-1` -- **re-measured in the second pass below and found to need none**: both name the STEP as provenance. Archived there. |
