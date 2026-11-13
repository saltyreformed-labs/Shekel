"""Integration: a transfer's settle books ONE figure, whichever door asked.

Plan step **X-f2-c3**, ruling **R-FA** applied to the transfer table.  FOUR
doors could move a transfer into the settled band and exactly ONE of them froze
an auto-derived loan payment's live payment-date cash (the two shadow doors are
the transfer's own, asked from a grid leg, since plan step balance:X-bi-6-1,
ruling **R-BAL87**):

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

**WHICH COLUMN the freeze wrote stayed each shadow's ``actual_amount``, a
developer ruling (2026-08-12)**: this branch BUILT ruling **R-FH**'s move to
the row's OWN amount (finding **N-241**) and withdrew it, as two adversarial
reviews measured it unsafe then -- a manual payment's base was
``estimated_amount``, so a freeze written there compounded the standing extra
across settle/revert cycles (`$1,599.10` -> `$1,699.10` -> `$1,799.10`), and a
reverted settle's leftover ``actual_amount`` would have OUTRANKED it through
``COALESCE``.  The shadows went at plan step balance:X-bi-6-4d-2, and a settled
transfer's figure is the TRANSFER's own since its 4d-4 half (rulings
**R-BAL365**, **R-BAL366**).  So these cases assert what the transfer records
AND what the ledger books, and the second must never change.

**The arithmetic is hand-computed and shown.**  Loan $200,000 / 6% / 360mo:
    P&I    = amortize(200000, 0.06, 360)  = 1,199.10
    escrow = 3,600.00 / 12                =   300.00
    PITI   = 1,199.10 + 300.00            = 1,499.10
The template's dormant price series states a deliberately stale ``$1.00``, so
a settle that books ``$1.00`` is a settle that missed the freeze and a settle
that books ``$1,499.10`` is one that took it.

**On production this changed `$0.00` (measured 2026-08-12)**:
``budget.loan_payment_settings`` held ZERO rows (the loan page's route writes
one), so no live transfer was an auto-derived loan payment and all 17 settled
transfer shadows on Checking carried ``actual_amount = NULL``.
"""
