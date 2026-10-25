> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `X-au-c`, the amount model's SEAM: three shipped leaves (archived 2026-08-26)

**What this is.** Three of the four `* [x]` leaf entries of `balance:X-au-c` that left `../README.md`
section 5 on 2026-08-26 under Section 9 rule 5, to buy the room `X-i3`'s decomposition needed. The
README had been brought to exactly **980 of its 1,000-line cap with a 20-line headroom floor** by
`five_shipped_steps_2026-08-26.md` earlier the same day -- zero room -- and rule 4 forbids raising a
cap when it binds.

**The COMMIT is the record for every one of them**; read the code each shipped, not this file.

**What was carried FORWARD rather than archived.** Each entry ended in a "what a LATER leaf must
obey" clause, and rule 5's second condition is that no live sentence may depend on an archived one.
Those clauses are restated on the live `X-au-c` entry in `../README.md`, condensed, which is the
same treatment `X-au`'s own parent entry already gave the leaves archived earlier that day.

| leaf | commit | what it shipped |
|---|---|---|
| `X-au-c1` | `2dbdad1c` | THE SCHEMA. Both amount columns became NULLABLE under `(amount_source_id IS NULL) = (<amount> IS NOT NULL)`, and the source names the RELATION rather than the RULE (ruling **R-FK**). Nothing was declared derived yet, so each later cutover stamps its own relation. Opened **N-260**-**N-265** |
| `X-au-c2a` | `d44a4f01` | THE READERS. All 17 `effective_amount` reads routed and BOTH model properties DELETED, with the 104 test reads that were their last callers; `investment_projection` valued at its boundary. Closed **N-262**; opened **N-266**-**N-272** |
| `X-au-c2b` | `a24f0b80` | A row's BUDGET is RESOLVED, and ONE rule says what it DISPLAYS as (`display_amounts_by_id`, where the grid, the fragments and the companion had three). The basis is pinned to `(user_id, scenario_id)`; `priced_ids` deleted, a scenario REFUSAL in its place. Closed **N-268**-**N-271**; opened **N-294**-**N-298** |

**The two constraints a later leaf could still get wrong**, kept here in full because the live
restatement is one sentence:

* `get_payment_history` can never take the resolver (**N-266**: it is a cycle), and nothing
  reachable from `loan_payment_service` may NAME `cash_ledger` (**N-267**) -- which is why the
  producer-free arms live in `row_valuation.py`.
* Rules 2 and 4 read no STATUS; `amounts_by_id` has no gate above the resolve; the basis pins
  `date.today()` and moving it to `ctx.as_of` is `X-i2`'s money move; every door writing an amount
  CLEARS `amount_source_id`, which `X-au-c1`'s CHECK makes an `IntegrityError` rather than a
  convention.

**`X-au-c3` is NOT here.** It stayed in `../README.md`: `balance:X-au-d` and `balance:X-au-e` name
it in their `blocked by` cells, and archiving a step a live blocker cell names is what
`five_shipped_steps_2026-08-26.md` had already had to undo once, for `X-f1`.
