"""Tests for the genesis loan posting service (Build-Order Step 4 + read switch).

:mod:`app.services.loan_posting_service` posts a loan's confirmed history into the
double-entry ledger: the per-payment principal / interest / escrow / refund split
(layered on the Step-2 cash entry), and the once-per-loan OPENING plus every user
TRUE-UP as balanced anchor corrections.  Both derive from ONE chronological
running-balance walk that seeds at origination and RESETS at each anchor, and
both are reconciled by ONE loop (``sync_loan_postings``, since plan step
``balance:X-bi-6-3``, ruling **R-BAL102**: the split is a date-keyed
correction with no row link, exactly as the anchors are).  The payment split is
wired into the transfer chokepoints (auto-posted on settle via
``transfer_service``); the tests below drive the same one sync directly.  The
two single-half doors this module used to drive (``sync_loan_payment_postings``
/ ``sync_loan_anchor_corrections``) went with the split's row key -- a rule-5
class (b) re-expression Josh confirmed 2026-09-21.  TWO pinned figures moved
with it, both -99000 -> -100000 and both named in their own docstrings
(``test_hard_delete_reverses_the_correction_at_its_own_key``,
``test_resync_reverses_an_unsettled_payment``): each pinned a state only the
deleted direct doors produced -- the split reversed with the cash leg still
posted -- which no production door reaches.  What the posted legs SUM to is
read back through the test
suite's dated posting window (``tests._ledger_reads.posted_loan_balance_at`` /
``posted_loan_balance_map``) -- plan step E1e deleted the production readers that
used to answer that, since nothing in ``app/`` called them; the values pinned
below are the LEDGER's, and the window is only how the test looks at it.  Those
tests freeze ``date.today()`` so the wiring's sync-as-of is deterministic across
CI clocks; the ledger they read is the same genesis ledger the split + anchor
tests above build.

The split fixtures are SYNTHETIC with HAND-COMPUTED literals: a $100,000 balance
at 6% gives a clean $500.00 first-month interest (``100000 * 0.06 / 12``), so
every expected interest / principal / escrow / refund is computed by hand and
shown in the docstring's arithmetic.  The loan's user-trueup anchor ($100,000)
deliberately differs from its origination principal ($250,000): under genesis the
walk seeds from origination and resets at the trueup, so an asserted $500 (vs the
$1,250 the origination balance would give) PROVES the reset, while a payment
placed BEFORE the trueup asserting $1,250 proves origination seeding (built-in
non-vacuity checks).

All money is ``Decimal`` from strings.
"""
