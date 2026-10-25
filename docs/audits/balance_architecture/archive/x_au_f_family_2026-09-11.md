> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-f` family as built: the PARENT-transfer cutover (archived 2026-09-11)

**Condensed out of the balance README under `docs/plans/conventions.md` rule 5 on 2026-09-11**
(developer's call under rule 4, when the `X-cf` registry patch bound the README's cap). The three
entries below are carried VERBATIM and WITHOUT re-verification; the two leaves' own as-built
records are `x_au_f_1_as_built_2026-09-09.md` and `x_au_f_2_as_built_2026-09-10.md`. Every id
still resolves in `docs/plans/steps.md`.

* [x] **X-au-f** `cb4239a2` -- the DECOMPOSED parent of the PARENT-transfer cutover, ticked with
  its last leaf. Split into three leaves 2026-09-09 (**R-BAL10**) and RE-CUT into two on
  2026-09-10 (**R-BAL14**), which ABSORBED `X-au-f-3` into `X-au-f-2` rather than withdrawing it:
  the parent's producer and the migration that empties its column are two halves of ONE act, and
  any boundary between them ships a commit where every reader asks the parent while the parent
  still stores its stale snapshot. The two records below carry the measurement.
  * [x] **X-au-f-1** `ce8bf485` -- every parent-transfer render site takes the amount model's answer rather than the column; byte-identical BY CONSTRUCTION. Closed **N-452**; opened **BAL-476**. Record: `archive/x_au_f_1_as_built_2026-09-09.md`.
  * [x] **X-au-f-2** `cb4239a2` -- THE CUTOVER, in one act: the parent's producer answers the
    whole cash on the installment's own due date, amount rule 4 moves onto the TRANSFER
    dispatch (**R-BAL10**), every writer states an `AmountOwnership` (**R-BAL11**), the settle
    freeze event is deleted (**R-BAL12**), and `b7e4c1f38a20` empties `transfers.amount` for
    the 169 non-override generated rows. Closed **N-263**, **N-451**, **BAL-476**, **N-449**
    and **N-352**; opened **BAL-477**. **NOT N-450**, against this commit's own message: what emptied was the INSTANCE column and that row names the TEMPLATE's, so it goes to `X-bp`. Record: `archive/x_au_f_2_as_built_2026-09-10.md`.
