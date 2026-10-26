> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# 5 shipped balance steps archived under rule 5 on 2026-09-24

The balance README stood at 1,305 of its 1,330 lines after the tick that split `X-bi-6-4` into its
leaves, 5 above the gate's 20-line headroom floor, and its signpost says the next balance tick archives
a completed span before it writes. The OLDEST completed span no live row leans on is the `X-au-g`
family, all five shipped at `3b7716f8` (2026-09-02): none is cited as a wait or an alias by any other
row of `docs/plans/steps.md` (measured on the cut's tree with the gate's own `blocked_keys` /
`alias_keys`) and none owns a ledger row, so under `docs/plans/conventions.md` rule 5 their rows leave
the index and their README entries leave section 5, verbatim below. Carried WITHOUT re-verification.
The one live obligation among them, that a later step must NOT delete
`tests/oracles/loan_monthly_composition.py`, is RESTATED at the parent `X-au`'s README entry. `X-f1`,
older still, keeps its entry: a gate control
(`test_registry_integrity.py::test_a_shipped_leaf_is_never_reported_as_open`) grades its row.

The README entries as they stood:

    * [x] **X-au-g** `3b7716f8` -- the DECOMPOSED parent of the LOAN-PAYMENT cutover (split 2026-08-31),
      ticked 2026-09-23 when its last open leaf was WITHDRAWN (**R-R93**); closed **N-297**, whose
      loan-basis read of the payment history left at `X-au-g-1` (`af61263d`). Its shipped leaves are in
      `archive/shipped_steps_archived_2026-09-16.md` and `archive/x_au_g_2c_3b_2_2026-09-02.md`.
      * [x] **X-au-g-2c** `3b7716f8` -- the CUTOVER's parent (the readers, the declaration, the escrow
        rule); ticked with X-au-g.
        * [x] **X-au-g-2c-3** `3b7716f8` -- the escrow rule's parent: FOUR walks each restated one
          allocation because the rule sat above them in the import graph, so every remedy was a MOVE or
          a DELETION (**R-IZ**, **R-R53**); ticked with X-au-g.
          * [x] **X-au-g-2c-3b** `3b7716f8` -- the charge calendar: `3b-1` `fd3afc59` moved it to the
            leaf both walks reach, `3b-2` below. `3b-3`, the engine feed's floor (**N-409**), was
            WITHDRAWN 2026-09-23 (**R-R93**): nothing has read the feed's amounts since
            `recurrence:R7d-g-3`, and `recurrence:R16-e` deletes the feed with walk 3.
            * [x] **X-au-g-2c-3b-2** `3b7716f8` -- ONE accrual and ONE escrow per INSTALLMENT, both tiers on the ONE replay (rule 14). Rules **R-IX**, files **N-439**. **A later step must NOT delete `tests/oracles/loan_monthly_composition.py`.** `archive/x_au_g_2c_3b_2_2026-09-02.md`.

The index rows:

- **X-au-g** `3b7716f8` -- The DECOMPOSED parent of the LOAN-PAYMENT cutover, split 2026-08-31 into the pricing cycle's deletion, the tier move that unwinds the amount model's reach into the loan service, the ruling that puts a loan's terms on the installment they govern, and the cutover those three unblock. Closed **N-297**, whose loan-basis read of the payment history left at `X-au-g-1`.
- **X-au-g-2c** `3b7716f8` -- The DECOMPOSED parent of the loan-payment CUTOVER, split 2026-09-01: the readers route first and move nothing, the cutover declares both legs derived, and the escrow rule **N-409** needs is its own money-moving act.
- **X-au-g-2c-3** `3b7716f8` -- The DECOMPOSED parent of the escrow rule, split 2026-09-02 when its own trace found the defect was not ONE floor but FOUR statements of one allocation rule and TWO of one charge calendar -- each duplicated because the shared rule sat ABOVE the walks that needed it, so reaching it was an import cycle.
- **X-au-g-2c-3b** `3b7716f8` -- The DECOMPOSED parent of the CHARGE-CALENDAR half, split 2026-09-02: the calendar moved to the leaf both walks reach and the SETTLED walk took it; its third leaf, the ENGINE FEED's floor, was WITHDRAWN 2026-09-23 into `recurrence:R16-e` (**recurrence:R-R93**).
- **X-au-g-2c-3b-2** `3b7716f8` -- Let the SETTLED walk take the charge calendar -- ONE interest accrual and ONE escrow per INSTALLMENT rather than one per PAYMENT -- which DELETES `split_payment_cash`, the composition whose own docstring concedes it is correct only while a loan takes one payment per accrual period. **MOVES POSTED MONEY, OWN PR.** Carries **N-409**'s second half and satisfies **recurrence:D51**.
