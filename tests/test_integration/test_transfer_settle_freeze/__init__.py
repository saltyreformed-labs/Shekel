"""Integration: a transfer's settle books ONE figure, whichever door asked.

Plan step **X-f2-c3**, ruling **R-FA** applied to the transfer table.  FOUR
doors can move a transfer into the settled band and exactly ONE of them froze
an auto-derived loan payment's live payment-date cash:

* the grid's shadow "Mark Paid" (``routes/transactions/_shadow_mutations``) --
  it called ``loan_payment_service.live_loan_payment_amount`` itself;
* the transfers page's "Mark Done" (``routes/transfers/mutations.mark_done``);
* the transfer full-edit Status dropdown (``_execute_transfer_update``);
* a transaction PATCH landing on a shadow (``_apply_shadow_update``).

The other three booked the stored estimate -- the creation-time escrow -- so
the same payment recorded a different figure depending on which control the
operator pressed.  That is finding **N-219**'s shape on this table, and a ROUTE
holding a money rule is this arc's own root cause 1.
``transfer_service.settle_transfer`` is the rule's name now and
``update_transfer`` dispatches to it, so these grade the FIGURE at the service
and then prove each door reaches it.

**WHICH COLUMN the freeze writes stays ``actual_amount``, and that is a
developer ruling (2026-08-12) rather than an oversight.**  Ruling **R-FH** says
a derivation belongs in the row's OWN amount and finding **N-241** records that
it does not go there; this branch BUILT that move and withdrew it, because two
adversarial reviews measured it unsafe on today's schema -- a manual payment's
base is ``estimated_amount``, so writing the freeze there compounds the standing
extra across settle/revert cycles (`$1,599.10` -> `$1,699.10` -> `$1,799.10`),
and a leftover ``actual_amount`` from a reverted settle would OUTRANK the frozen
figure through ``COALESCE``.  Plan step **X-au-c**'s ``amount_source`` column is
what makes the write idempotent and authoritative, so the move is that step's.
These cases therefore assert ``actual_amount`` AND ``effective_amount``: the
first is where the freeze lands today, the second is what the ledger books, and
the second is the one that must never change.

**The arithmetic is hand-computed and shown.**  Loan $200,000 / 6% / 360mo:
    P&I    = amortize(200000, 0.06, 360)  = 1,199.10
    escrow = 3,600.00 / 12                =   300.00
    PITI   = 1,199.10 + 300.00            = 1,499.10
The template's dormant price series states a deliberately stale ``$1.00``, so
a settle that books ``$1.00`` is a settle that missed the freeze and a settle
that books ``$1,499.10`` is one that took it.

**On production this changes `$0.00` today**: ``budget.loan_payment_settings``
holds ZERO rows, so no live transfer is an auto-derived loan payment and all 17
settled transfer shadows on Checking carry ``actual_amount = NULL``.  The route
that writes that settings row (``routes/loan/payment_transfer.py``) is live, so
the split opens on the next loan payment transfer created through the loan page.
"""
