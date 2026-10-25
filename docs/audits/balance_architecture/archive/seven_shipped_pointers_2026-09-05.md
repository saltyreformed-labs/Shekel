> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

Condensed out of `docs/audits/balance_architecture/README.md` on 2026-09-05 to
free 30 lines for the `balance:X-br` mint, under `docs/plans/conventions.md`
rule 5 ("archive a completed span, do not raise the cap"). Seven shipped
entries were still multi-line while 27 of their 34 peers were already
one-line pointers; each is now a pointer, and every sentence removed that was
NOT already carried by an as-built file is reproduced verbatim below.

Cite this for how a decision came to be, never for what is true now.

**What did NOT move.** Every open `N-` finding these entries mention stays in
`docs/plans/ledger.md` and is unaffected: N-238, N-245, N-246, N-266, N-409,
N-416, N-437, N-439, N-440, N-444, N-451, N-452 and F-11. Condensing a
narrative is not closing a row.

---

## X-au-c -- `3d1379d1`

Already recorded in `archive/x_au_c_as_built_2026-08-26.md` and
`archive/eight_shipped_steps_2026-09-01.md`. The clause the live document
carried, reproduced because a LATER step still obeys it:

> **What still binds LATER steps** (rule 5 forbids a live sentence depending
> on an archived one): `get_payment_history` may never take the resolver;
> nothing reachable from `loan_payment_service` may NAME `cash_ledger` (the
> producer-free arms live in `row_valuation.py`); and the amount rules read no
> STATUS -- the `date.today()` basis is `X-i2`'s.

Its nested leaf `X-au-c3` (`3d1379d1`, a settle RECORDS what moved rather than
refreshing an amount) is recorded in `archive/eight_shipped_steps_2026-09-01.md`.

## X-au-k -- `7315ecd9`

Recorded in `archive/x_au_k_as_built_2026-09-02.md`. A row's amount ownership
is ONE mapped attribute over a value-object total across ruling R-FI's two
states (**R-IW**); the `_FIGURE_COLUMNS` registry is gone and no migration was
needed. Closed **N-293**, opened **N-437** and **N-440**.

## X-au-d -- `ed06acf6`

Recorded in `archive/x_au_d_as_built_2026-09-03.md`. A paycheck's amount is
its salary profile's, and it stores none. 59 non-override salary rows declared
(**R-JB**: settled ones too), the read-time repair and its whole seam deleted,
and FOUR dormant defects the stored figure was absorbing closed or filed --
the archive re-price (**N-261**, `-$9,677.24`), a templates-form 500
(**N-253**), `_freshest_amount`'s conjunct, and **N-444**. Two engines, one
pass, one fence fewer.

## X-au-e -- `c000d7f6` `b846386a`

Recorded in `archive/x_au_e_as_built_2026-09-03.md`. A template row reads its
template's series. 525 rows declared, generation's last pricing fork deleted,
and the `$502.45` class dead with it: a generator that prices nothing cannot
mis-price. Closed **N-244**, **N-247**, **N-444**. The correction the live
entry carried, reproduced because it corrects the entry's own first claim:

> **This bullet's own claim that the chooser's keep-vs-use decision dies was
> REFUTED** -- it keeps its offer and loses only its FIGURE (**R-JD**),
> because `_conflicts` is the one door in `app/` that clears `is_override`.

## X-au-g-1 -- `af61263d`

Recorded in `archive/eight_shipped_steps_2026-09-01.md`. A loan's price reads
its TERMS, never its own payment rows. Finding **N-266**(a) is MISDIAGNOSED
rather than falsified: the PATH is dead, the CONCLUSION stands.

## X-au-g-2c-3b-2 -- `3b7716f8`

Recorded in `archive/x_au_g_2c_3b_2_2026-09-02.md`. ONE interest accrual and
ONE escrow per INSTALLMENT, `split_payment_cash` deleted, both tiers folded
onto the ONE replay `loan_ledger._replay.replay_loan_events` (rule 14).
`$0.00` on production, `$1,631.05` on a forced collision. Closes
**recurrence:D51**, carries **N-409**'s second half, rules **R-IX**, files
**N-439**. The constraint a later step still obeys:

> **A later step must NOT delete `tests/oracles/loan_monthly_composition.py`.**

## X-au-h -- `825fd791`

No as-built file; this section is its record. **`is_override` says ONE thing**
(*this row is the OWNER's, not the rule's*) and authorship is a fact the
PAYLOAD carries (**R-JR**); migration `e7c3a1f9b482` dropped the flag from the
OCCURRENCE-keyed unique index, keeping it on the undated one. Closed
**N-248**, **N-436**, **N-448**; opened **N-451**, **N-452**.

> **What a LATER step must still obey**: **N-238** stays OPEN and NARROWED --
> the flag still records a re-price and a period move indistinguishably, and
> deleting it still needs the move represented first; **F-11**, **N-245** and
> **N-246** stay open here, N-246 SHARPENED because `due_date`, `name` and
> `category_id` are derived fields a popover edits without raising the flag.

> The four-fact specification this entry replaced over-counted, and its safety
> argument named `idx_transactions_template_period_scenario`, which R17
> DROPPED -- true of production (35 revisions behind) and false at HEAD, with
> the collision surviving for UNDATED rows only.

**Not re-verified in this condensation.** Every claim above was carried
forward verbatim from the live document; none was re-measured against the code
on 2026-09-05, and the dated measurements inside them decay on their own
terms. Rule 5's third condition is satisfied by saying so rather than by
implying a check nobody ran.
