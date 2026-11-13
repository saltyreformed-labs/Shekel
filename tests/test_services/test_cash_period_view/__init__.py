"""X-c1: the cash PERIOD VIEW -- one row set, two clocks, one named remainder.

Plan step X-c1 (``docs/audits/balance_architecture/README.md``, ruling R-K).
Grades ``app.services.balance_at._cash_periods.period_view_of`` -- the producer
plan step X-c2 points the grid's balance row, its subtotal rows and its two
remainder rows at.  The view is ADDITIVE here: no production surface reads
it yet, so nothing in this package can move a shipped balance.

**The identity is the point, and it is asserted against an INDEPENDENT fold.**
For every period::

    balance(p.end) - balance(p.start - 1 day)
        == net[p] + period_timing[p] + book_vs_bank[p]

**The remainder is TWO figures here, and that is ruling R-DH (f)** (plan step
S1-c).  It was one field, ``reconciliation``, summing two facts with different
causes and different fixes: money landing in a different column from the one it
was budgeted to (``period_timing``), and the gap between what the app had
recorded and what the bank actually held (``book_vs_bank``).  Every assertion
in this package that read the single field now names WHICH half carries the figure and
pins the other at ``$0.00`` -- which is strictly sharper, because the old form
could not tell a timing error from a true-up and each of these fixtures produces
exactly one of the two.

The left side is sampled from ``balances_at`` -- the X-b producer, graded
on its own hand-computed oracle -- and the right side is the view's grouping of
the same rows.  Neither is derived from the other: the view computes its
remainder from the row set (what MOVED in the column minus what was BUDGETED to
it, plus the assertions), never as ``balance_delta - net``, precisely so this
assertion can fail (plan Section 7.2 -- a residual proving itself is the
forbidden shape).

**Every expected figure in this package is HAND-COMPUTED and written out in the test that
asserts it**, per component: the basis change, TIMING (a row settled outside its
own column), the TRUE-UP (a balance assertion), the CLAMP (ruling R-G moving an
overdue plan forward), and the OPENING that must book nothing (ruling R-I).
Where a figure exists in the shipping producer it is asserted too, so the basis
change is shown as a measured difference rather than claimed.
"""
