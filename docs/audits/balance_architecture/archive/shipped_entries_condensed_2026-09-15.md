> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shipped entries condensed under rule 5 on 2026-09-15

The balance README stood at 1,310 lines, the exact ceiling its headroom arm allows, so the three
fattest SHIPPED entries were condensed in place (`docs/plans/conventions.md` rule 5: shrink the
record of what is DONE, never the specification of what remains). Each entry's full as-built text
as it stood at dev `65c60f06` follows, verbatim and carried WITHOUT re-verification; the condensed
line in the README is the live pointer and the code as committed is the record.

## X-bi-7a

```text
    * [x] **X-bi-7a** `eecef63d` -- `recurs` on the definition and the row (`DerivedFlag`); the seven
      "no cadence" sites and the account-delete refusal read it; the transaction twin
      `propagate_to_unruled_definition`. Two rulings at the cut: a row-less rule-less definition is
      DISPOSED of with its account and a definition's live row is the account's history wherever it
      sits (**R-BAL27**); the `is_override` flip's MOVE half keys on `recurs`, its typed-figure
      half waits for 7b's restate (**R-BAL28**). `_leftover_due_date`'s rule-less arm deleted.
```

## X-bi-7b-1

```text
      * [x] **X-bi-7b-1** `7a2fe751` -- `one_off.place_one_off(spec, period, *, scenario_id,
        due_date=None)`, `spec` an `OneOffToPlace` of what the definition says (**R-BAL30**); both
        grid create doors and `mint_uncategorized` on it, `category_id` nullable (migration
        `9c1e4b7a2d3f`); a definition goes with its last row unless a merchant rule names it;
        *Does not repeat* gone (**R-BAL32**).
```

## X-au-g-2a

```text
  * [x] **X-au-g-2a -- rule 4's producer moves below the amount model.** `b16908f7`. The
    tier move `row_valuation.py` has always said this arc owes: `_basis` / `_pricing` become
    `cash_ledger._loan_installment` / `._loan_pricing`, so the arrow runs one way.
    **A LATER step must obey:** the loan READING tier may now import the amount model, which
    is what `X-au-g-2c` needs.  Byte-identical, AST-verified; opened **N-416**.
```
