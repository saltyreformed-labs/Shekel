> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Six shipped X-au-g-2c leaves, archived out of the plan of record (2026-09-02)

**Why these six and no others.** The balance README stood at its 20-line headroom floor and
`balance:X-au-k`'s ruling minted three steps that each owe a specification (rule 12). Rule 4
forbids raising a cap when it binds, so rule 5 archiving is the only route. Only SHIPPED entries
were eligible: bank_import is holding a decomposition that renumbers nearly every ranked row, and
archiving a RANKED entry would produce a delete/modify conflict against it. These six carry no
rank and sit outside that span.

**Every entry here is reproduced VERBATIM and carried WITHOUT re-verification** (rule 5's third
condition). The COMMIT each names is the record; read the code it shipped, not this file.

## Where each entry's obligations went, checked one at a time

Rule 5's second condition is that no live sentence may depend on an archived one. Five of the six
already had registry homes, so archiving relocated nothing:

| entry | its obligation now lives in |
|---|---|
| `X-au-g-2b` | **N-409**, a live ledger row |
| `X-au-g-2c-1` | **N-432**, a live ledger row |
| `X-au-g-2c-2` | ruling **R-IO**, which already states that a leg takes ownership only when the figure MOVED |
| `X-au-g-2c-3c` | `recurrence:D52`, whose convention half is **R16-d**'s |
| `X-au-g-2c-3b-1` | **D53** and **D55**, live ledger rows |

**The sixth moved, and it moved somewhere better than a stub.** `X-au-g-2c-3a` carried *the leaf
reaches NOTHING in `app.services`, which is what makes it callable from every walk, so moving the
rule back up re-forces the duplication.* That existed in no registry. It now sits in ruling
**R-IZ** -- one walk, one answer -- so it stops being an aside on a shipped step and becomes part
of the rule it is evidence for.

**And its citation was verified before being carried, because an archived entry's claims are
exactly what nobody re-checks.** The entry cites `test_loan_allocation_is_one_rule`.
`grep "def test_loan_allocation_is_one_rule"` returns NOTHING -- it is a test MODULE,
`tests/test_services/test_loan_allocation_is_one_rule.py`, holding seven cases including
`test_the_replay_row_allocates_through_the_one_rule` and a sweep that checks it reaches every
branch it claims. The citation is REAL; the shape it is cited in is not what a reader would grep
for, and a future reader meeting that miss would reasonably conclude the test had been deleted.

## The six entries, reproduced

### X-au-g-2b

```text
  * [x] **X-au-g-2b** `6cd0ad44` -- a loan payment resolves on its own DUE date (**R-IJ**) at
    three sites plus a fourth nobody had filed, so `LoanPricing` takes no `as_of` and
    **`cash_ledger` makes no clock call at all** (AST census, thirteen modules, pinned by
    `test_amount_source.TestTheAmountModelReadsNoClock`). Closed **N-40** and **N-410**.
    **A LATER step must obey:** the escrow-threshold site was BUILT, measured a REGRESSION and
    reverted -- **N-409** stands with its remedy withdrawn, re-owned to `X-au-g-2c`.
```

### X-au-g-2c-1

```text
    * [x] **X-au-g-2c-1** `cdc2c7d9` -- BOTH readers of a projected loan-side shadow take the
      amount model. **N-266 said ONE unrouted reader; the census says TWO of NINE, seven
      settled-only.** The second is `_plan._planned_from_shadows`, and `loan_payment_settings` is
      EMPTY on production, so 47 of 58 projected shadows take its fallback -- `2c-2` would have
      shipped an `AmountUnresolvable` on `/savings`. Closed **N-266**; opened **N-432**. The ledger
      fence is a VALUE control now, with a static check reading W9908's own allowlist.
```

### X-au-g-2c-2

```text
    * [x] **X-au-g-2c-2** `1f2b98a4` -- EVERY transfer shadow is DERIVED (**R-IN**), so Transfer
      Invariant 3 is STRUCTURAL and both repairs that maintained it go with `live_cash`,
      `_manual_shadow_amount` and `frozen_amount`. **Absorbs X-au-f's SHADOW half.** 350 rows,
      `$0.00`, downgrade byte-identical on a production copy. Closed **N-401**. Record in
      `archive/x_au_g_2c_2_as_built_2026-09-01.md`. **A LATER step must obey:** **R-IO** takes
      ownership only when the figure MOVED, so X-au-h inherits that conflation, not a defect.
```

### X-au-g-2c-3a

```text
      * [x] **X-au-g-2c-3a** `becf76f8` -- the ONE allocation moves to `app/utils/money.py`, beside
        the accrual, and the three restatements it forced are DELETED. `$0.00`; 0 differences over
        200,000 trials against `HEAD`. **A LATER step must obey:**
        `test_loan_allocation_is_one_rule` pins that the leaf reaches NOTHING in `app.services` --
        the property that makes it callable from every walk, so moving the rule back up re-forces
        the duplication.
```

### X-au-g-2c-3c

```text
      * [x] **X-au-g-2c-3c** `cb6469b2` -- `debt_strategy_service._accrue_interest` routes through
        the ONE accrual primitive, deleting a FIFTH spelling that associated `(b*r)/12` against
        `b*(r/12)`. **`recurrence:D52`'s "agree on 200,000 randomised draws" is REFUTED**: at
        500,000 they part, `$565.37` against `$565.36`. `$0.00` on both live loans.
        **`D52`'s duplicate half dies here; its CONVENTION half stays `recurrence:R16-d`'s.**
```

### X-au-g-2c-3b-1

```text
        * [x] **X-au-g-2c-3b-1** `fd3afc59` -- the CHARGE calendar moves to `loan_ledger._charges`,
          the same inversion one tier up: it sat in `balance_at._plan`, which REACHES the settled
          walk, so that walk restated it. `$0.00`; 0 mismatches over 20,000 trials.
          **This is what makes `3b-2` a TAKING rather than a rebuild. A LATER step must obey:**
          `D53` and `D55` are deliberately NOT taken here and the docstrings say so.
```

