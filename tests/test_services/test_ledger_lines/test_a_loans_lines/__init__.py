"""Tests for a loan's lines on the worked-out ledger, and the walk they are made from.

A loan's confirmed history books on the worked-out ledger
(:func:`app.services.ledger_lines.worked_out_lines`): the per-payment principal /
interest / escrow / refund split, beside the payment's own cash line, and the
once-per-loan OPENING plus every user TRUE-UP as balanced anchor corrections.
Both derive from ONE chronological running-balance walk
(:func:`app.services.loan_ledger.walk_loan_ledger`; its splits are
:func:`app.services.loan_ledger.compute_loan_payment_splits`) that seeds at
origination and RESETS at each anchor.  The split is a date-keyed correction
with no row link (plan step ``balance:X-bi-6-3``, ruling **R-BAL102**), exactly
as the anchors are.  What a loan's own lines SUM to is read through the
suite's dated window (``tests._ledger_reads.posted_loan_balance_at`` /
``posted_loan_balance_map``).

These were the store's writer's unit tests (``loan_posting_service``) until
plan step plan#2914 deleted the writer: each case whose figures were also read
off the worked-out ledger kept them, and lost the writer's call and the stored
entries it read; each whose subject was the writer itself (an entry's count, a
repeat sync, a resync, the writer's reversal of what it had posted, a chart
row's minting, a ledger the store alone could hold cleared) went with it.

The split fixtures are SYNTHETIC with HAND-COMPUTED literals: a $100,000 balance
at 6% gives a clean $500.00 first-month interest (``100000 * 0.06 / 12``), so
every expected interest / principal / escrow / refund is computed by hand and
shown in the docstring's arithmetic.  The loan's user-trueup anchor ($100,000)
deliberately differs from its origination principal ($250,000): the walk seeds
from origination and resets at the trueup, so an asserted $500 (vs the $1,250
the origination balance would give) PROVES the reset, while a payment placed
BEFORE the trueup asserting $1,250 proves origination seeding.

All money is ``Decimal`` from strings.
"""
